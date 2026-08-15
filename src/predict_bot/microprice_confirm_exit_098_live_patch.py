from __future__ import annotations

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, ROUND_DOWN
from functools import wraps
from typing import Any, Iterable

from . import m_realtime as _realtime
from . import microprice_variants as _variants
from .core import ApiHttpError, ApiTransportError
from .microprice_confirm_optimization_shadows import (
    EXIT_098_STRATEGY,
    EXIT_TARGET_PRICE,
    OPTIMIZATION_VERSION,
    SOURCE_STRATEGY,
)


EXIT_098_LIVE_PATCH_VERSION = "MICROPRICE_CONFIRM_EXIT_098_LIVE_V1"
_EXIT_WORKER = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="microprice-exit-098",
)
_EXIT_GUARD = threading.RLock()
_EXIT_IN_FLIGHT: set[int] = set()
_EXIT_RETRY_AFTER: dict[int, float] = {}
_EXIT_RUNTIME: dict[str, Any] = {
    "scheduled": 0,
    "submitted": 0,
    "rejected": 0,
    "lastDecision": None,
}


def _append_unique(values: Iterable[str], item: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*tuple(values), item)))


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _register_live_strategy() -> None:
    from . import live_trading as live
    from . import research_forward as research

    strategy = EXIT_098_STRATEGY
    live.LIVE_RESEARCH_STRATEGIES = _append_unique(
        live.LIVE_RESEARCH_STRATEGIES,
        strategy,
    )
    live.LIVE_SUPPORTED_STRATEGIES = _append_unique(
        live.LIVE_SUPPORTED_STRATEGIES,
        strategy,
    )
    live.LIVE_RESEARCH_REPRICE_GAPS = {
        **live.LIVE_RESEARCH_REPRICE_GAPS,
        strategy: Decimal("0.05"),
    }

    _realtime.LIVE_RESEARCH_STRATEGIES = _append_unique(
        _realtime.LIVE_RESEARCH_STRATEGIES,
        strategy,
    )
    _realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES = {
        *_realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES,
        strategy,
    }

    # Keep confirmation-add mode available for this explicit live strategy.
    all_live_sources = tuple(live.LIVE_SUPPORTED_STRATEGIES)
    research.CONFIRMATION_ADD_SOURCE_STRATEGIES = all_live_sources
    live.CONFIRMATION_ADD_SOURCE_STRATEGIES = all_live_sources


def _live_engine_from_tracker(tracker: Any) -> Any | None:
    sink = getattr(getattr(tracker, "engine", None), "live_signal_sink", None)
    owner = getattr(sink, "__self__", None)
    return owner if owner is not None else None


def _live_exit_candidates(
    live_engine: Any,
    market_id: int,
) -> list[dict[str, Any]]:
    ledger = getattr(live_engine, "ledger", None)
    if ledger is None:
        return []
    lock = getattr(ledger, "lock", None)
    if lock is None:
        return []
    with lock:
        try:
            rows = ledger.db.execute(
                """SELECT o.id, o.strategy, o.market_id, o.side,
                          o.status, o.filled_share_qty,
                          x.status AS exit_status
                     FROM live_orders AS o
                     LEFT JOIN live_strategy_settlements AS s
                       ON s.order_local_id=o.id
                     LEFT JOIN live_manual_exits AS x
                       ON x.order_local_id=o.id
                    WHERE o.strategy=? AND o.market_id=?
                      AND o.status='FILLED'
                      AND COALESCE(o.filled_share_qty, 0)>0
                      AND s.order_local_id IS NULL
                      AND (
                          x.order_local_id IS NULL
                          OR UPPER(x.status) IN (
                              'REJECTED','FAILED','CANCELED','CANCELLED'
                          )
                      )
                    ORDER BY o.id ASC""",
                (EXIT_098_STRATEGY, int(market_id)),
            ).fetchall()
        except Exception:
            return []
    return [dict(row) for row in rows]


def _submit_live_target_exit(
    live_engine: Any,
    order_local_id: int,
) -> None:
    from . import live_trading as live

    if not live_engine.manual_exit_lock.acquire(blocking=False):
        raise ValueError("another live sell request is already being processed")
    try:
        with live_engine.lock:
            client = live_engine.client
            wallet_address = live_engine.wallet_address
            wallet_id = live_engine.wallet_id
        if client is None or not wallet_address or not wallet_id:
            raise ValueError("live trading wallet is not ready for exit 0.98")

        order = live_engine.ledger.order_for_manual_exit(int(order_local_id))
        if order is None:
            raise ValueError("active EXIT_098 live position was not found")
        if str(order.get("strategy") or "").upper() != EXIT_098_STRATEGY:
            raise ValueError("the live position is not EXIT_098")
        if str(order.get("status") or "").upper() != "FILLED":
            raise ValueError("EXIT_098 requires a fully filled entry")

        reference = live_engine.current_market() or {}
        try:
            market_id = int(order["market_id"])
            if int(reference["market_id"]) != market_id:
                raise ValueError("the EXIT_098 position is not in the current market")
            if client.server_timestamp_ms() >= int(reference["end_ms"]):
                raise ValueError("the EXIT_098 market has already ended")
            fee_bps = int(reference.get("fee_bps") or 200)
        except KeyError as exc:
            raise ValueError("current market metadata is incomplete") from exc

        token_id = str(order.get("token_id") or "")
        ledger_shares = live._decimal(order.get("filled_share_qty"))
        if not token_id or ledger_shares is None or ledger_shares <= 0:
            raise ValueError("the EXIT_098 entry has no sellable shares")

        position_payload = client.position_by_token(wallet_address, token_id)
        wallet_shares = live_engine._position_shares(
            live_engine._position_record(position_payload)
        )
        if wallet_shares is None or wallet_shares <= 0:
            raise ValueError("Binance reports no available EXIT_098 shares")
        if wallet_shares + Decimal("0.02") < ledger_shares:
            raise ValueError(
                "wallet shares are below the EXIT_098 strategy fill"
            )

        sell_shares = min(wallet_shares, ledger_shares)
        amount_in_wei = int(
            (sell_shares * Decimal(10**18)).to_integral_value(
                rounding=ROUND_DOWN
            )
        )
        if amount_in_wei <= 0:
            raise ValueError("the EXIT_098 position is too small to sell")

        current_book = client.orderbook(market_id, token_id)
        best_bid = live_engine._best_bid(current_book)
        target = Decimal(str(EXIT_TARGET_PRICE))
        if best_bid is None or best_bid < target:
            raise ValueError(
                "the current executable bid is below the 0.98 target"
            )

        price_limit_text = format(target.normalize(), "f")
        exit_row = live_engine.ledger.begin_manual_exit(
            order=order,
            order_type="LIMIT",
            sell_shares=sell_shares,
            price_limit=target,
        )
        exit_id = int(exit_row["id"])

        try:
            quote = client.get_quote(
                wallet_address=wallet_address,
                token_id=token_id,
                amount_in_wei=str(amount_in_wei),
                price_limit=price_limit_text,
                slippage_bps=100,
                fee_rate_bps=fee_bps,
                funding_source="MPC",
                side="SELL",
                order_type="LIMIT",
            )
            safe, reason = live_engine._quote_is_safe(
                quote,
                token_id=token_id,
                price_limit=target,
                amount_in_wei=amount_in_wei,
                side="SELL",
                order_type="LIMIT",
            )
            if not safe:
                raise ValueError(reason)
            quoted_input = live._decimal(quote.get("amountIn"))
            if quoted_input is None or abs(
                quoted_input - Decimal(amount_in_wei)
            ) >= Decimal(1):
                raise ValueError(
                    "EXIT_098 quote does not cover the full position"
                )
            expiry = int(quote.get("expireAt") or 0)
            if expiry and expiry <= client.server_timestamp_ms() + 250:
                raise ValueError("EXIT_098 quote expired before placement")
            live_engine.ledger.update_manual_exit(
                exit_id,
                status="QUOTE_ACCEPTED",
                quote_average_price=live._float(quote.get("averagePrice")),
                quote_amount_in_wei=str(quote.get("amountIn")),
                quote_amount_out_wei=str(quote.get("amountOut")),
                quote_expires_at=expiry or None,
            )
        except Exception as exc:
            detail = str(exc)[:500]
            live_engine.ledger.update_manual_exit(
                exit_id,
                status="REJECTED",
                error_kind="EXIT_098_QUOTE_REJECTED",
                error_message=detail,
            )
            live_engine.ledger.record_event(
                "ERROR",
                "EXIT_098_QUOTE_REJECTED",
                detail,
                market_id,
            )
            raise ValueError(detail) from exc

        live_engine.ledger.update_manual_exit(
            exit_id,
            status="PLACE_ATTEMPTED",
            attempted_at=live.utc_iso(),
        )
        try:
            placed = client.place_limit_order(
                wallet_address=wallet_address,
                wallet_id=wallet_id,
                quote_id=str(quote["quoteId"]),
                price_limit=price_limit_text,
                slippage_bps=100,
                account_type=live_engine.account_type,
                funding_source="MPC",
            )
            exchange_order_id = str(placed.get("orderId") or "")
            if not exchange_order_id:
                raise ApiTransportError(
                    "EXIT_098 placement response did not contain orderId"
                )
        except Exception as exc:
            ambiguous = isinstance(exc, ApiTransportError) or (
                isinstance(exc, ApiHttpError) and exc.status_code >= 500
            )
            detail = str(exc)[:500]
            live_engine.ledger.update_manual_exit(
                exit_id,
                status="AMBIGUOUS" if ambiguous else "REJECTED",
                error_kind=(
                    "EXIT_098_PLACEMENT_AMBIGUOUS"
                    if ambiguous
                    else "EXIT_098_PLACEMENT_REJECTED"
                ),
                error_message=detail,
            )
            live_engine.ledger.record_event(
                "ERROR",
                (
                    "EXIT_098_PLACEMENT_AMBIGUOUS"
                    if ambiguous
                    else "EXIT_098_PLACEMENT_REJECTED"
                ),
                detail,
                market_id,
            )
            raise ValueError(detail) from exc

        live_engine.ledger.update_manual_exit(
            exit_id,
            status="SUBMITTED",
            submitted_at=live.utc_iso(),
            order_id=exchange_order_id,
            response_json=live._safe_payload(placed),
            error_kind=None,
            error_message=None,
        )
        with live_engine.lock:
            live_engine.next_order_sync = 0.0
        live_engine.ledger.record_event(
            "WARN",
            "EXIT_098_SELL_SUBMITTED",
            (
                f"{EXIT_098_STRATEGY} market {market_id}: auto SELL LIMIT "
                f"{sell_shares} {order['side']} shares at 0.98"
            ),
            market_id,
        )
        _EXIT_RUNTIME["submitted"] += 1
        _EXIT_RUNTIME["lastDecision"] = {
            "orderLocalId": int(order_local_id),
            "marketId": market_id,
            "status": "SUBMITTED",
            "target": EXIT_TARGET_PRICE,
            "bestBidBeforeQuote": float(best_bid),
            "version": EXIT_098_LIVE_PATCH_VERSION,
        }
    finally:
        live_engine.manual_exit_lock.release()


def _exit_worker(live_engine: Any, order_local_id: int) -> None:
    error: str | None = None
    try:
        _submit_live_target_exit(live_engine, int(order_local_id))
    except Exception as exc:
        error = str(exc)[:500]
        _EXIT_RUNTIME["rejected"] += 1
        _EXIT_RUNTIME["lastDecision"] = {
            "orderLocalId": int(order_local_id),
            "status": "RETRYABLE_BLOCK",
            "reason": error,
            "target": EXIT_TARGET_PRICE,
            "version": EXIT_098_LIVE_PATCH_VERSION,
        }
    finally:
        with _EXIT_GUARD:
            _EXIT_IN_FLIGHT.discard(int(order_local_id))
            _EXIT_RETRY_AFTER[int(order_local_id)] = (
                time.monotonic() + (2.0 if error else 60.0)
            )


def _schedule_live_exit(
    tracker: Any,
    snapshot: dict[str, Any],
    context: dict[str, Any],
) -> None:
    if not _variants._direct_context_is_safe(
        getattr(tracker, "engine", None),
        context,
    ):
        return
    try:
        market_id = int(snapshot["market_id"])
    except (KeyError, TypeError, ValueError):
        return

    live_engine = _live_engine_from_tracker(tracker)
    if live_engine is None:
        return

    for order in _live_exit_candidates(live_engine, market_id):
        side = str(order.get("side") or "").upper()
        if side not in {"UP", "DOWN"}:
            continue
        bid = _finite(snapshot.get(f"{side.lower()}_bid"))
        bid_size = _finite(snapshot.get(f"{side.lower()}_bid_size"))
        shares = _finite(order.get("filled_share_qty"))
        if (
            bid is None
            or bid < EXIT_TARGET_PRICE
            or bid_size is None
            or shares is None
            or shares <= 0
            or bid_size + 1e-12 < shares
        ):
            continue

        local_id = int(order["id"])
        now = time.monotonic()
        with _EXIT_GUARD:
            if local_id in _EXIT_IN_FLIGHT:
                continue
            if now < _EXIT_RETRY_AFTER.get(local_id, 0.0):
                continue
            _EXIT_IN_FLIGHT.add(local_id)
        _EXIT_RUNTIME["scheduled"] += 1
        _EXIT_RUNTIME["lastDecision"] = {
            "orderLocalId": local_id,
            "marketId": market_id,
            "status": "SCHEDULED",
            "target": EXIT_TARGET_PRICE,
            "observedBid": bid,
            "visibleBidSize": bid_size,
            "requiredShares": shares,
            "version": EXIT_098_LIVE_PATCH_VERSION,
        }
        _EXIT_WORKER.submit(_exit_worker, live_engine, local_id)


def _append_exit_candidate(
    tracker: Any,
    opened: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result = list(opened)
    source_candidates = [
        candidate
        for candidate in opened
        if str(candidate.get("strategy") or "").upper()
        == SOURCE_STRATEGY
    ]
    for source in source_candidates:
        try:
            market_id = int(source["market_id"])
            row = tracker.store.db.execute(
                """SELECT target_price FROM trades
                   WHERE strategy=? AND market_id=?
                   ORDER BY id DESC LIMIT 1""",
                (EXIT_098_STRATEGY, market_id),
            ).fetchone()
        except Exception:
            row = None
        if row is None:
            continue
        result.append(
            {
                **source,
                "strategy": EXIT_098_STRATEGY,
                "target_price": row["target_price"],
                "paper_only": True,
                "live_orders_affected": False,
                "live_forwardable_when_selected": True,
                "live_exit_target_price": (
                    float(row["target_price"])
                    if row["target_price"] is not None
                    else None
                ),
                "variant_mode": "FOLLOW_V2_EXIT_AT_098",
                "source_strategy": SOURCE_STRATEGY,
                "strategy_version": OPTIMIZATION_VERSION,
            }
        )
    return result


def _patch_tracker_process() -> None:
    tracker_class = _variants.MicropriceVariantTracker
    original = tracker_class.process
    if getattr(original, "_microprice_exit_098_live_v1", False):
        return

    @wraps(original)
    def process_with_exit_098_live(
        self: Any,
        snapshot: dict[str, Any],
        fee_bps: int,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        normalized_context = dict(context or {})
        opened = list(
            original(
                self,
                snapshot,
                int(fee_bps),
                normalized_context,
            )
            or []
        )
        _schedule_live_exit(
            self,
            snapshot,
            normalized_context,
        )
        return _append_exit_candidate(self, opened)

    process_with_exit_098_live._microprice_exit_098_live_v1 = True  # type: ignore[attr-defined]
    tracker_class.process = process_with_exit_098_live


def install_microprice_confirm_exit_098_live_patch() -> None:
    _register_live_strategy()
    _patch_tracker_process()
