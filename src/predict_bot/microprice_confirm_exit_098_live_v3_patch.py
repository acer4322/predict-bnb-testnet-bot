from __future__ import annotations

import math
import threading
import time
from decimal import Decimal, ROUND_DOWN
from functools import wraps
from typing import Any

from . import microprice_confirm_exit_098_live_patch as _base
from .core import ApiHttpError, ApiTransportError
from .microprice_confirm_optimization_shadows import (
    EXIT_098_STRATEGY,
    EXIT_TARGET_PRICE,
)


EXIT_098_LIVE_V3_VERSION = "MICROPRICE_CONFIRM_EXIT_098_LIVE_V3"
EXIT_098_MONITOR_INTERVAL_SECONDS = 0.50
EXIT_098_IDLE_INTERVAL_SECONDS = 1.00
_MONITOR_LOCK = threading.RLock()
_MONITORED_ENGINE_IDS: set[int] = set()


def _finite_decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except Exception:
        return None
    return number if number.is_finite() else None


def _level_price_size(level: Any) -> tuple[Decimal | None, Decimal | None]:
    if isinstance(level, dict):
        price = _finite_decimal(
            level.get("price", level.get("p"))
        )
        size = _finite_decimal(
            level.get(
                "size",
                level.get("quantity", level.get("qty", level.get("q"))),
            )
        )
        return price, size
    if isinstance(level, (list, tuple)) and len(level) >= 2:
        return _finite_decimal(level[0]), _finite_decimal(level[1])
    return None, None


def _book_payload(book: Any) -> dict[str, Any]:
    if not isinstance(book, dict):
        return {}
    for key in ("data", "orderBook", "orderbook"):
        nested = book.get(key)
        if isinstance(nested, dict) and isinstance(nested.get("bids"), list):
            return nested
    return book


def _best_bid_and_capacity(
    book: Any,
    *,
    minimum_price: Decimal | None = None,
) -> tuple[Decimal | None, Decimal]:
    payload = _book_payload(book)
    best: Decimal | None = None
    capacity = Decimal("0")
    for level in payload.get("bids") or []:
        price, size = _level_price_size(level)
        if (
            price is None
            or size is None
            or price <= 0
            or price >= 1
            or size <= 0
        ):
            continue
        if best is None or price > best:
            best = price
        if minimum_price is None or price >= minimum_price:
            capacity += size
    return best, capacity


def _best_bid_any(book: Any) -> Decimal | None:
    return _best_bid_and_capacity(book)[0]


def _live_exit_candidates_v3(
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
                          o.status, o.token_id, o.filled_share_qty,
                          x.status AS exit_status
                     FROM live_orders AS o
                     LEFT JOIN live_strategy_settlements AS s
                       ON s.order_local_id=o.id
                     LEFT JOIN live_manual_exits AS x
                       ON x.order_local_id=o.id
                    WHERE o.strategy=? AND o.market_id=?
                      AND UPPER(o.status) IN (
                          'FILLED','CANCELED','CANCELLED','EXPIRED',
                          'REJECTED','FAILED'
                      )
                      AND COALESCE(o.filled_share_qty, 0)>0
                      AND COALESCE(o.token_id, '')<>''
                      AND s.order_local_id IS NULL
                      AND (
                          x.order_local_id IS NULL
                          OR UPPER(x.status) IN (
                              'REJECTED','FAILED','CANCELED','CANCELLED','EXPIRED'
                          )
                      )
                    ORDER BY o.id ASC""",
                (EXIT_098_STRATEGY, int(market_id)),
            ).fetchall()
        except Exception:
            return []
    return [dict(row) for row in rows]


def _schedule_candidate(
    live_engine: Any,
    order: dict[str, Any],
    *,
    best_bid: Decimal,
    capacity_at_target: Decimal | None,
    source: str,
) -> bool:
    local_id = int(order["id"])
    now = time.monotonic()
    with _base._EXIT_GUARD:
        if local_id in _base._EXIT_IN_FLIGHT:
            return False
        if now < _base._EXIT_RETRY_AFTER.get(local_id, 0.0):
            return False
        _base._EXIT_IN_FLIGHT.add(local_id)
    _base._EXIT_RUNTIME["scheduled"] += 1
    _base._EXIT_RUNTIME["lastDecision"] = {
        "orderLocalId": local_id,
        "marketId": int(order["market_id"]),
        "status": "TARGET_OBSERVED_SCHEDULED",
        "target": EXIT_TARGET_PRICE,
        "observedBid": float(best_bid),
        "visibleCapacityAtOrAboveTarget": (
            float(capacity_at_target)
            if capacity_at_target is not None
            else None
        ),
        "requiredShares": float(order.get("filled_share_qty") or 0.0),
        "triggerSource": source,
        "version": EXIT_098_LIVE_V3_VERSION,
    }
    _base._EXIT_WORKER.submit(
        _base._exit_worker,
        live_engine,
        local_id,
    )
    return True


def _submit_live_target_exit_v3(
    live_engine: Any,
    order_local_id: int,
) -> None:
    """Sell a stable EXIT_098 position only after a real 0.98 bid exists."""
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
        entry_status = str(order.get("status") or "").upper()
        if entry_status not in {
            "FILLED", "CANCELED", "CANCELLED", "EXPIRED", "REJECTED", "FAILED"
        }:
            raise ValueError(
                f"EXIT_098 entry status {entry_status or 'UNKNOWN'} is not stable"
            )

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

        target = Decimal(str(EXIT_TARGET_PRICE))
        current_book = client.orderbook(market_id, token_id)
        best_bid, capacity_at_target = _best_bid_and_capacity(
            current_book,
            minimum_price=target,
        )
        if best_bid is None or best_bid < target:
            raise ValueError(
                "the current executable bid is below the 0.98 target"
            )

        position_payload = client.position_by_token(wallet_address, token_id)
        wallet_shares = live_engine._position_shares(
            live_engine._position_record(position_payload)
        )
        if wallet_shares is None or wallet_shares <= 0:
            raise ValueError("Binance reports no available EXIT_098 shares")

        requested_shares = min(wallet_shares, ledger_shares)
        amount_in_wei = int(
            (requested_shares * Decimal(10**18)).to_integral_value(
                rounding=ROUND_DOWN
            )
        )
        if amount_in_wei <= 0:
            raise ValueError("the EXIT_098 position is too small to sell")
        sell_shares = Decimal(amount_in_wei) / Decimal(10**18)
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
                    "EXIT_098 quote does not cover the complete sell request"
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
                f"{EXIT_098_STRATEGY} market {market_id}: target observed at "
                f"bid {best_bid}; submitted SELL LIMIT {sell_shares} "
                f"{order['side']} shares at 0.98"
            ),
            market_id,
        )
        _base._EXIT_RUNTIME["submitted"] += 1
        _base._EXIT_RUNTIME["lastDecision"] = {
            "orderLocalId": int(order_local_id),
            "marketId": market_id,
            "status": "SUBMITTED",
            "target": EXIT_TARGET_PRICE,
            "bestBidBeforeQuote": float(best_bid),
            "visibleCapacityAtOrAboveTarget": float(capacity_at_target),
            "ledgerShares": float(ledger_shares),
            "walletShares": float(wallet_shares),
            "sellShares": float(sell_shares),
            "version": EXIT_098_LIVE_V3_VERSION,
        }
    finally:
        live_engine.manual_exit_lock.release()


def _monitor_once(live_engine: Any) -> bool:
    with live_engine.lock:
        client = live_engine.client
        wallet_address = live_engine.wallet_address
    if client is None or not wallet_address:
        return False

    reference = live_engine.current_market() or {}
    try:
        market_id = int(reference["market_id"])
        if client.server_timestamp_ms() >= int(reference["end_ms"]):
            return False
    except (KeyError, TypeError, ValueError):
        return False

    candidates = _live_exit_candidates_v3(live_engine, market_id)
    if not candidates:
        return False

    target = Decimal(str(EXIT_TARGET_PRICE))
    observed_any = False
    for order in candidates:
        token_id = str(order.get("token_id") or "")
        if not token_id:
            continue
        try:
            book = client.orderbook(market_id, token_id)
            best_bid, capacity = _best_bid_and_capacity(
                book,
                minimum_price=target,
            )
        except Exception as exc:
            _base._EXIT_RUNTIME["lastDecision"] = {
                "orderLocalId": int(order["id"]),
                "marketId": market_id,
                "status": "BOOK_READ_FAILED",
                "reason": str(exc)[:300],
                "version": EXIT_098_LIVE_V3_VERSION,
            }
            continue
        observed_any = True
        if best_bid is None or best_bid < target:
            _base._EXIT_RUNTIME["lastDecision"] = {
                "orderLocalId": int(order["id"]),
                "marketId": market_id,
                "status": "WATCHING_TARGET",
                "target": EXIT_TARGET_PRICE,
                "observedBid": float(best_bid) if best_bid is not None else None,
                "version": EXIT_098_LIVE_V3_VERSION,
            }
            continue
        _schedule_candidate(
            live_engine,
            order,
            best_bid=best_bid,
            capacity_at_target=capacity,
            source="DEDICATED_REST_MONITOR",
        )
    return observed_any


def _monitor_loop(live_engine: Any) -> None:
    stop_event = getattr(live_engine, "stop_event", None)
    while stop_event is not None and not stop_event.is_set():
        active = False
        try:
            active = _monitor_once(live_engine)
        except Exception as exc:
            _base._EXIT_RUNTIME["lastDecision"] = {
                "status": "MONITOR_ERROR",
                "reason": str(exc)[:300],
                "version": EXIT_098_LIVE_V3_VERSION,
            }
        delay = (
            EXIT_098_MONITOR_INTERVAL_SECONDS
            if active
            else EXIT_098_IDLE_INTERVAL_SECONDS
        )
        stop_event.wait(delay)


def _start_monitor(live_engine: Any) -> None:
    engine_id = id(live_engine)
    with _MONITOR_LOCK:
        if engine_id in _MONITORED_ENGINE_IDS:
            return
        _MONITORED_ENGINE_IDS.add(engine_id)
    thread = threading.Thread(
        target=_monitor_loop,
        args=(live_engine,),
        name="microprice-exit-098-monitor",
        daemon=True,
    )
    live_engine.exit_098_monitor_thread = thread
    thread.start()


def _patch_live_engine() -> None:
    from . import live_trading as live

    engine_class = live.LiveM0WEngine
    engine_class._best_bid = staticmethod(_best_bid_any)

    original_init = engine_class.__init__
    if getattr(original_init, "_microprice_exit_098_live_v3", False):
        return

    @wraps(original_init)
    def init_with_exit_098_monitor(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        original_init(self, *args, **kwargs)
        _start_monitor(self)

    init_with_exit_098_monitor._microprice_exit_098_live_v3 = True  # type: ignore[attr-defined]
    engine_class.__init__ = init_with_exit_098_monitor


def _schedule_live_exit_v3(
    tracker: Any,
    snapshot: dict[str, Any],
    context: dict[str, Any],
) -> None:
    """Fast path from a verified tracker snapshot; REST monitor is authoritative."""
    live_engine = _base._live_engine_from_tracker(tracker)
    if live_engine is None:
        return
    try:
        market_id = int(snapshot["market_id"])
    except (KeyError, TypeError, ValueError):
        return
    target = Decimal(str(EXIT_TARGET_PRICE))
    for order in _live_exit_candidates_v3(live_engine, market_id):
        side = str(order.get("side") or "").upper()
        if side not in {"UP", "DOWN"}:
            continue
        bid = _finite_decimal(snapshot.get(f"{side.lower()}_bid"))
        if bid is None or bid < target:
            continue
        _schedule_candidate(
            live_engine,
            order,
            best_bid=bid,
            capacity_at_target=None,
            source="VERIFIED_TRACKER_SNAPSHOT",
        )


def install_microprice_confirm_exit_098_live_v3_patch() -> None:
    _base.EXIT_098_LIVE_PATCH_VERSION = EXIT_098_LIVE_V3_VERSION
    _base._live_exit_candidates = _live_exit_candidates_v3
    _base._submit_live_target_exit = _submit_live_target_exit_v3
    _base._schedule_live_exit = _schedule_live_exit_v3
    _patch_live_engine()
