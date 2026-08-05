from __future__ import annotations

import time
from decimal import Decimal, ROUND_DOWN
from typing import Any

from . import microprice_confirm_exit_098_live_patch as _base
from . import microprice_variants as _variants
from .core import ApiHttpError, ApiTransportError
from .microprice_confirm_optimization_shadows import (
    EXIT_098_STRATEGY,
    EXIT_TARGET_PRICE,
)


EXIT_098_LIVE_V2_VERSION = "MICROPRICE_CONFIRM_EXIT_098_LIVE_V2"
SELLABLE_ENTRY_STATUSES = {
    "FILLED",
    "PARTIAL",
    "PARTIALLY_FILLED",
    "OPEN",
    "PENDING",
    "SUBMITTED",
    "PLACED_PENDING_SYNC",
    "OPENING",
    "CANCELED",
    "CANCELLED",
    "EXPIRED",
}
ACTIVE_PARTIAL_ENTRY_STATUSES = {
    "PARTIAL",
    "PARTIALLY_FILLED",
    "OPEN",
    "PENDING",
    "SUBMITTED",
    "PLACED_PENDING_SYNC",
    "OPENING",
}


def _live_exit_candidates_v2(
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
                      AND UPPER(o.status) IN (
                          'FILLED','PARTIAL','PARTIALLY_FILLED',
                          'OPEN','PENDING','SUBMITTED',
                          'PLACED_PENDING_SYNC','OPENING',
                          'CANCELED','CANCELLED','EXPIRED'
                      )
                      AND COALESCE(o.filled_share_qty, 0)>0
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


def _submit_live_target_exit_v2(
    live_engine: Any,
    order_local_id: int,
) -> None:
    """Submit a full-position GTC SELL LIMIT at 0.98.

    A stable filled entry gets its protection order immediately.  The previous
    implementation waited for a transient 0.98 bid and then performed several
    REST calls, which could miss the touch before placement completed.
    """
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
        if entry_status not in SELLABLE_ENTRY_STATUSES:
            raise ValueError(
                f"EXIT_098 entry status {entry_status or 'UNKNOWN'} is not sellable"
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

        position_payload = client.position_by_token(wallet_address, token_id)
        wallet_shares = live_engine._position_shares(
            live_engine._position_record(position_payload)
        )
        if wallet_shares is None or wallet_shares <= 0:
            raise ValueError("Binance reports no available EXIT_098 shares")

        # Use Binance's currently available amount instead of requiring an
        # exact match with the local ledger.  Partial fills, reservation lag,
        # and tiny dust differences must not block the protective sell.
        requested_shares = min(wallet_shares, ledger_shares)
        amount_in_wei = int(
            (requested_shares * Decimal(10**18)).to_integral_value(
                rounding=ROUND_DOWN
            )
        )
        if amount_in_wei <= 0:
            raise ValueError("the EXIT_098 position is too small to sell")
        sell_shares = Decimal(amount_in_wei) / Decimal(10**18)

        target = Decimal(str(EXIT_TARGET_PRICE))
        price_limit_text = format(target.normalize(), "f")

        # Telemetry only.  LIMIT GTC may rest at 0.98, so a lower current bid
        # must not block submission and force the executor to race a later touch.
        best_bid: Decimal | None = None
        try:
            best_bid = live_engine._best_bid(
                client.orderbook(market_id, token_id)
            )
        except Exception:
            best_bid = None

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
        observed_bid_text = (
            format(best_bid.normalize(), "f") if best_bid is not None else "unknown"
        )
        live_engine.ledger.record_event(
            "WARN",
            "EXIT_098_PROTECTION_SUBMITTED",
            (
                f"{EXIT_098_STRATEGY} market {market_id}: GTC SELL LIMIT "
                f"{sell_shares} {order['side']} shares at 0.98; "
                f"observed bid {observed_bid_text}"
            ),
            market_id,
        )
        _base._EXIT_RUNTIME["submitted"] += 1
        _base._EXIT_RUNTIME["lastDecision"] = {
            "orderLocalId": int(order_local_id),
            "marketId": market_id,
            "status": "PROTECTION_SUBMITTED",
            "target": EXIT_TARGET_PRICE,
            "entryStatus": entry_status,
            "ledgerShares": float(ledger_shares),
            "walletShares": float(wallet_shares),
            "sellShares": float(sell_shares),
            "bestBidBeforeQuote": (
                float(best_bid) if best_bid is not None else None
            ),
            "restingAtTarget": bool(best_bid is None or best_bid < target),
            "version": EXIT_098_LIVE_V2_VERSION,
        }
    finally:
        live_engine.manual_exit_lock.release()


def _schedule_live_exit_v2(
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

    live_engine = _base._live_engine_from_tracker(tracker)
    if live_engine is None:
        return

    for order in _live_exit_candidates_v2(live_engine, market_id):
        side = str(order.get("side") or "").upper()
        if side not in {"UP", "DOWN"}:
            continue
        entry_status = str(order.get("status") or "").upper()
        bid = _base._finite(snapshot.get(f"{side.lower()}_bid"))
        bid_size = _base._finite(snapshot.get(f"{side.lower()}_bid_size"))
        shares = _base._finite(order.get("filled_share_qty"))
        if shares is None or shares <= 0:
            continue

        # An entry still accepting fills waits until the target is observed;
        # otherwise later BUY fills could appear after the protection quantity
        # was frozen.  FILLED and terminal partial entries are protected now.
        if entry_status in ACTIVE_PARTIAL_ENTRY_STATUSES and (
            bid is None or bid < EXIT_TARGET_PRICE
        ):
            continue

        local_id = int(order["id"])
        now = time.monotonic()
        with _base._EXIT_GUARD:
            if local_id in _base._EXIT_IN_FLIGHT:
                continue
            if now < _base._EXIT_RETRY_AFTER.get(local_id, 0.0):
                continue
            _base._EXIT_IN_FLIGHT.add(local_id)
        _base._EXIT_RUNTIME["scheduled"] += 1
        _base._EXIT_RUNTIME["lastDecision"] = {
            "orderLocalId": local_id,
            "marketId": market_id,
            "status": "PROTECTION_SCHEDULED",
            "target": EXIT_TARGET_PRICE,
            "entryStatus": entry_status,
            "observedBid": bid,
            "visibleBidSize": bid_size,
            "requiredShares": shares,
            "restingAtTarget": bool(bid is None or bid < EXIT_TARGET_PRICE),
            "version": EXIT_098_LIVE_V2_VERSION,
        }
        _base._EXIT_WORKER.submit(_base._exit_worker, live_engine, local_id)


def install_microprice_confirm_exit_098_live_v2_patch() -> None:
    """Upgrade the already-installed V1 tracker wrapper in place."""
    _base.EXIT_098_LIVE_PATCH_VERSION = EXIT_098_LIVE_V2_VERSION
    _base._live_exit_candidates = _live_exit_candidates_v2
    _base._submit_live_target_exit = _submit_live_target_exit_v2
    _base._schedule_live_exit = _schedule_live_exit_v2
