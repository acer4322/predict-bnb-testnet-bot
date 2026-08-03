from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, ROUND_DOWN
from typing import Any, Sequence

from . import xpair_canary_autopilot_server as base
from . import xpair_canary_autopilot_server_v4 as v4
from .core import ApiHttpError, ApiTransportError, BinancePredictionTradingClient
from .xpair_exit_guard_common import (
    EXIT,
    MAX_SHARE_MISMATCH,
    MIN_ONE_WIN_HOLD_PNL_USDT,
    MIN_ORDER_USDT,
    MIN_QUOTE_EXPIRY_MS,
    MIN_SELL_QUOTE_CAPACITY,
    RECOVERY_POSITION_SYNC_ATTEMPTS,
    RECOVERY_POSITION_SYNC_INTERVAL,
    RECOVERY_RECONCILE_SECONDS,
    TERMINAL_NO_FILL,
    _actual_or_estimated_fee,
    _decimal,
    _filled_cash,
    _filled_shares,
    _first_positive,
    _from_wei,
    _merge_recovery_details,
    _to_wei,
    evaluate_one_win_profitability,
)


def _wallet(client: BinancePredictionTradingClient) -> tuple[str, str]:
    wallets = client.wallets().get("wallets") or []
    if len(wallets) != 1:
        raise RuntimeError(f"expected one Prediction wallet, got {len(wallets)}")
    address = str(wallets[0].get("walletAddress") or "")
    wallet_id = str(wallets[0].get("walletId") or "")
    if not address or not wallet_id:
        raise RuntimeError("Prediction wallet metadata is incomplete")
    return address, wallet_id


def _order_map(
    client: BinancePredictionTradingClient,
    wallet_address: str,
    order_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    wanted = {str(item) for item in order_ids if str(item)}
    orders = client.order_history(wallet_address, limit=100).get("orders") or []
    return {
        str(item.get("orderId") or ""): dict(item)
        for item in orders
        if str(item.get("orderId") or "") in wanted
    }


def _position_shares(payload: dict[str, Any]) -> Decimal:
    value = _first_positive(
        payload,
        (
            "availableShareQty",
            "availableShares",
            "shareQty",
            "shares",
            "positionQty",
            "quantity",
            "amount",
            "balance",
        ),
    )
    return value or Decimal("0")


def _confirmed_position_shares(
    client: BinancePredictionTradingClient,
    *,
    wallet_address: str,
    token_id: str,
    expected_max: Decimal,
) -> Decimal:
    last = Decimal("0")
    for _ in range(RECOVERY_POSITION_SYNC_ATTEMPTS):
        payload = client.position_by_token(wallet_address, token_id)
        last = _position_shares(payload)
        if last > 0:
            return min(last, expected_max)
        time.sleep(RECOVERY_POSITION_SYNC_INTERVAL)
    raise RuntimeError(
        f"wallet position for token {token_id[:12]}… did not expose sellable shares"
    )


def _validate_market_quote(
    quote: dict[str, Any],
    *,
    side: str,
    token_id: str,
    requested_input: Decimal,
    client: BinancePredictionTradingClient,
) -> tuple[Decimal, Decimal, Decimal]:
    if not quote.get("quoteId"):
        raise ValueError(f"{side} quote has no quoteId")
    if str(quote.get("side") or side).upper() != side:
        raise ValueError(f"quote side changed from {side}")
    if str(quote.get("orderType") or "MARKET").upper() != "MARKET":
        raise ValueError("recovery quote changed order type")
    if quote.get("tokenId") is not None and str(quote.get("tokenId")) != token_id:
        raise ValueError("recovery quote token mismatch")
    amount_in = _from_wei(quote.get("amountIn"))
    amount_out = _from_wei(quote.get("amountOut"))
    average = _decimal(quote.get("averagePrice"))
    if amount_in is None or amount_in <= 0:
        raise ValueError("recovery quote amountIn is invalid")
    if amount_out is None or amount_out <= 0:
        raise ValueError("recovery quote amountOut is invalid")
    if amount_in > requested_input + Decimal("1e-8"):
        raise ValueError("recovery quote exceeds requested input")
    capacity = amount_in / requested_input
    if capacity < MIN_SELL_QUOTE_CAPACITY:
        raise ValueError(
            f"recovery quote input capacity {capacity:.1%} is below 95%"
        )
    expiry = int(quote.get("expireAt") or 0)
    if expiry and expiry < client.server_timestamp_ms() + MIN_QUOTE_EXPIRY_MS:
        raise ValueError("recovery quote expires too soon")
    if average is None or average <= 0:
        average = (
            amount_in / amount_out if side == "BUY" else amount_out / amount_in
        )
    if not Decimal("0") < average < Decimal("1"):
        raise ValueError(f"recovery quote average price {average} is outside (0, 1)")
    return amount_in, amount_out, average


def _request_sell_quote(
    client: BinancePredictionTradingClient,
    *,
    wallet_address: str,
    token_id: str,
    shares: Decimal,
    fee_bps: int,
    slippage_bps: int,
) -> dict[str, Any]:
    quote = client.get_quote(
        wallet_address=wallet_address,
        token_id=token_id,
        amount_in_wei=_to_wei(shares),
        price_limit=None,
        slippage_bps=slippage_bps,
        fee_rate_bps=fee_bps,
        funding_source="MPC",
        side="SELL",
        order_type="MARKET",
    )
    _validate_market_quote(
        quote,
        side="SELL",
        token_id=token_id,
        requested_input=shares,
        client=client,
    )
    return quote


def _request_recovery_buy_quote(
    client: BinancePredictionTradingClient,
    *,
    wallet_address: str,
    token_id: str,
    fee_bps: int,
    slippage_bps: int,
    target_shares: Decimal,
    maximum_input: Decimal,
) -> tuple[dict[str, Any], dict[str, Decimal]]:
    if maximum_input < MIN_ORDER_USDT:
        raise ValueError(
            f"recovery buy budget {maximum_input:.6f} is below the 1 USDT minimum"
        )
    initial = client.get_quote(
        wallet_address=wallet_address,
        token_id=token_id,
        amount_in_wei=_to_wei(maximum_input),
        price_limit=None,
        slippage_bps=slippage_bps,
        fee_rate_bps=fee_bps,
        funding_source="MPC",
        side="BUY",
        order_type="MARKET",
    )
    amount_in, amount_out, _ = _validate_market_quote(
        initial,
        side="BUY",
        token_id=token_id,
        requested_input=maximum_input,
        client=client,
    )
    if amount_out <= 0:
        raise ValueError("recovery buy quote produced no shares")
    revised_input = (amount_in * target_shares / amount_out).quantize(
        Decimal("0.000000000000000001"), rounding=ROUND_DOWN
    )
    revised_input = min(maximum_input, revised_input)
    if revised_input < MIN_ORDER_USDT:
        raise ValueError("matched recovery buy would be below the 1 USDT minimum")
    final = client.get_quote(
        wallet_address=wallet_address,
        token_id=token_id,
        amount_in_wei=_to_wei(revised_input),
        price_limit=None,
        slippage_bps=slippage_bps,
        fee_rate_bps=fee_bps,
        funding_source="MPC",
        side="BUY",
        order_type="MARKET",
    )
    final_in, final_out, average = _validate_market_quote(
        final,
        side="BUY",
        token_id=token_id,
        requested_input=revised_input,
        client=client,
    )
    mismatch = abs(final_out - target_shares) / target_shares
    if mismatch > MAX_SHARE_MISMATCH:
        raise ValueError(
            f"recovery buy share mismatch {mismatch:.3%} exceeds 0.25%"
        )
    fee = (
        final_out
        * min(average, Decimal("1") - average)
        * Decimal(fee_bps)
        / Decimal(10_000)
    )
    return final, {
        "input": final_in,
        "shares": final_out,
        "average": average,
        "fee": fee,
        "total": final_in + fee,
    }


def _place_market(
    client: BinancePredictionTradingClient,
    *,
    wallet_address: str,
    wallet_id: str,
    quote: dict[str, Any],
    account_type: str,
    slippage_bps: int,
) -> dict[str, Any]:
    try:
        placed = client.place_market_order(
            wallet_address=wallet_address,
            wallet_id=wallet_id,
            quote_id=str(quote["quoteId"]),
            slippage_bps=slippage_bps,
            account_type=account_type,
            funding_source="MPC",
        )
        order_id = str(placed.get("orderId") or "")
        if not order_id:
            raise ApiTransportError("market placement response did not contain orderId")
        return {"ok": True, "orderId": order_id, "response": placed}
    except Exception as exc:
        ambiguous = isinstance(exc, ApiTransportError) or (
            isinstance(exc, ApiHttpError) and exc.status_code >= 500
        )
        return {
            "ok": False,
            "ambiguous": ambiguous,
            "error": str(exc)[:500],
        }


def _wait_terminal_orders(
    client: BinancePredictionTradingClient,
    *,
    wallet_address: str,
    order_ids: Sequence[str],
    timeout: float = RECOVERY_RECONCILE_SECONDS,
) -> dict[str, dict[str, Any]]:
    wanted = {str(item) for item in order_ids if str(item)}
    deadline = time.monotonic() + max(0.0, timeout)
    found: dict[str, dict[str, Any]] = {}
    while time.monotonic() <= deadline:
        found.update(_order_map(client, wallet_address, list(wanted)))
        if wanted.issubset(found) and all(
            _status(found[item])
            in {"FILLED", *TERMINAL_NO_FILL, "PARTIAL", "PARTIALLY_FILLED"}
            for item in wanted
        ):
            break
        time.sleep(0.5)
    return found


def _incident(
    *,
    run_id: int,
    market_key: str,
    status: str,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    EXIT.update(
        run_id,
        status="EXIT_GUARD_INCIDENT",
        reason=f"{status}: {reason}",
        details=details,
        resolved=True,
    )
    snapshot = v4.SAFETY.snapshot()
    v4.SAFETY.activate_incident(
        status=status,
        reason=reason,
        market_key=market_key,
        run_id=run_id,
        btc_order_id=snapshot.get("btcOrderId"),
        eth_order_id=snapshot.get("ethOrderId"),
        btc_status=snapshot.get("btcStatus"),
        eth_status=snapshot.get("ethStatus"),
        market_end_ms=snapshot.get("marketEndMs"),
    )
    _merge_recovery_details(
        run_id,
        status=status,
        message=reason,
        recovery=EXIT.snapshot() or {},
    )
    base.STATE.disarm("xpair_exit_guard_incident")
    base.STATE.set_phase("XPAIR_INCIDENT_LOCKED")
    base.STATE.log(f"XPAIR_EXIT_GUARD_INCIDENT {status}: {reason}", "ERROR")


def _resolve_hold(
    *,
    run_id: int,
    evaluation: dict[str, Any],
) -> None:
    pnl = evaluation["oneWinPnl"]
    reason = (
        f"both fills retained: minimum one-win PnL {pnl:.6f} USDT is at least "
        f"{MIN_ONE_WIN_HOLD_PNL_USDT:.6f} USDT"
    )
    EXIT.update(
        run_id,
        status="HOLD_PROFITABLE_PAIR",
        reason=reason,
        one_win_pnl=pnl,
        total_cost=evaluation["totalCost"],
        minimum_payout=evaluation["minimumPayout"],
        details=evaluation,
        resolved=True,
    )
    v4.SAFETY.resolve_automatically("HOLD_PROFITABLE_PAIR", reason)
    _merge_recovery_details(
        run_id,
        status="HOLD_PROFITABLE_PAIR",
        message=reason,
        recovery=EXIT.snapshot() or {},
    )
    base.STATE.set_phase("HOLD_PROFITABLE_PAIR")
    base.STATE.log(f"HOLD_PROFITABLE_PAIR {reason}", "WARN")


def _unwind_positions(
    client: BinancePredictionTradingClient,
    *,
    run_id: int,
    market_key: str,
    wallet_address: str,
    wallet_id: str,
    context: dict[str, Any],
    orders: dict[str, dict[str, Any]],
    symbols: Sequence[str],
    final_status: str,
    trigger_reason: str,
    evaluation: dict[str, Any] | None = None,
) -> None:
    EXIT.update(
        run_id,
        status="UNWIND_QUOTING",
        reason=trigger_reason,
        one_win_pnl=(evaluation or {}).get("oneWinPnl"),
        total_cost=(evaluation or {}).get("totalCost"),
        minimum_payout=(evaluation or {}).get("minimumPayout"),
        details={"evaluation": evaluation or {}, "symbols": list(symbols)},
    )
    quotes: dict[str, dict[str, Any]] = {}
    sell_shares: dict[str, Decimal] = {}
    try:
        for symbol in symbols:
            leg = context["legs"][symbol]
            buy_quote = context["quotes"][symbol]
            filled = _filled_shares(orders.get(symbol), buy_quote)
            if filled <= 0:
                raise RuntimeError(f"{symbol} has no confirmed filled shares to unwind")
            sellable = _confirmed_position_shares(
                client,
                wallet_address=wallet_address,
                token_id=str(leg["token_id"]),
                expected_max=filled,
            )
            if sellable <= 0:
                raise RuntimeError(f"{symbol} position has no sellable shares")
            sell_shares[symbol] = sellable
        with ThreadPoolExecutor(max_workers=len(symbols)) as pool:
            futures = {
                symbol: pool.submit(
                    _request_sell_quote,
                    client,
                    wallet_address=wallet_address,
                    token_id=str(context["legs"][symbol]["token_id"]),
                    shares=sell_shares[symbol],
                    fee_bps=int(context["legs"][symbol].get("fee_bps") or 0),
                    slippage_bps=base.STATE.config.slippage_bps,
                )
                for symbol in symbols
            }
            quotes = {symbol: future.result() for symbol, future in futures.items()}
    except Exception as exc:
        _incident(
            run_id=run_id,
            market_key=market_key,
            status="UNWIND_QUOTE_FAILED_LOCK",
            reason=str(exc)[:800],
            details={"symbols": list(symbols), "sellShares": sell_shares},
        )
        return

    EXIT.update(
        run_id,
        status="UNWIND_PLACEMENT_IN_FLIGHT",
        reason="sell quotes accepted; market/FOK placements starting",
        details={
            "symbols": list(symbols),
            "sellShares": sell_shares,
            "sellQuotes": quotes,
        },
    )
    with ThreadPoolExecutor(max_workers=len(symbols)) as pool:
        futures = {
            symbol: pool.submit(
                _place_market,
                client,
                wallet_address=wallet_address,
                wallet_id=wallet_id,
                quote=quotes[symbol],
                account_type=base.STATE.config.account_type,
                slippage_bps=base.STATE.config.slippage_bps,
            )
            for symbol in symbols
        }
        placements = {symbol: future.result() for symbol, future in futures.items()}

    failed = {symbol: result for symbol, result in placements.items() if not result.get("ok")}
    if failed:
        ambiguous = any(bool(result.get("ambiguous")) for result in failed.values())
        _incident(
            run_id=run_id,
            market_key=market_key,
            status=(
                "UNWIND_PLACEMENT_AMBIGUOUS_LOCK"
                if ambiguous
                else "UNWIND_PLACEMENT_REJECTED_LOCK"
            ),
            reason=f"sell placement incomplete: {failed}",
            details={"placements": placements, "sellQuotes": quotes},
        )
        return

    sell_ids = {symbol: str(result["orderId"]) for symbol, result in placements.items()}
    EXIT.update(
        run_id,
        status="UNWIND_TRACKING",
        reason="sell market/FOK orders submitted; reconciling terminal status",
        btc_sell_order_id=sell_ids.get("BTC"),
        eth_sell_order_id=sell_ids.get("ETH"),
        details={"placements": placements, "sellQuotes": quotes},
    )
    found = _wait_terminal_orders(
        client,
        wallet_address=wallet_address,
        order_ids=list(sell_ids.values()),
    )
    statuses = {
        symbol: _status(found.get(order_id))
        for symbol, order_id in sell_ids.items()
    }
    if statuses and all(status == "FILLED" for status in statuses.values()):
        reason = f"automatic exit completed ({statuses}); {trigger_reason}"
        EXIT.update(
            run_id,
            status=final_status,
            reason=reason,
            btc_sell_order_id=sell_ids.get("BTC"),
            eth_sell_order_id=sell_ids.get("ETH"),
            details={
                "placements": placements,
                "sellQuotes": quotes,
                "sellOrders": found,
                "evaluation": evaluation or {},
            },
            resolved=True,
        )
        v4.SAFETY.resolve_automatically(final_status, reason)
        _merge_recovery_details(
            run_id,
            status=final_status,
            message=reason,
            recovery=EXIT.snapshot() or {},
        )
        base.STATE.set_phase(final_status)
        base.STATE.log(f"{final_status}: {reason}", "WARN")
        return

    _incident(
        run_id=run_id,
        market_key=market_key,
        status="UNWIND_NOT_FULLY_FILLED_LOCK",
        reason=f"sell orders did not both fill within timeout: {statuses}",
        details={"sellOrders": found, "placements": placements},
    )


def _attempt_recovery_buy_or_unwind(
    client: BinancePredictionTradingClient,
    *,
    run_id: int,
    market_key: str,
    wallet_address: str,
    wallet_id: str,
    context: dict[str, Any],
    orders: dict[str, dict[str, Any]],
    filled_symbol: str,
    missing_symbol: str,
) -> None:
    existing_quote = context["quotes"][filled_symbol]
    existing_leg = context["legs"][filled_symbol]
    missing_leg = context["legs"][missing_symbol]
    existing_shares = _filled_shares(orders[filled_symbol], existing_quote)
    existing_cash = _filled_cash(orders[filled_symbol], existing_quote)
    existing_fee = _actual_or_estimated_fee(
        orders[filled_symbol],
        shares=existing_shares,
        cash=existing_cash,
        fee_bps=int(existing_leg.get("fee_bps") or 0),
        quote=existing_quote,
    )
    existing_total = existing_cash + existing_fee
    pair_budget = _decimal(context["row"].get("pair_budget_usdt")) or Decimal("0")
    fee_reserve = (
        existing_shares
        * Decimal("0.5")
        * Decimal(int(missing_leg.get("fee_bps") or 0))
        / Decimal(10_000)
    )
    positive_limit = (
        existing_shares
        - existing_total
        - MIN_ONE_WIN_HOLD_PNL_USDT
        - fee_reserve
    )
    budget_limit = pair_budget - existing_total
    maximum_input = min(positive_limit, budget_limit)

    recovery_quote: dict[str, Any] | None = None
    recovery_projection: dict[str, Decimal] | None = None
    try:
        EXIT.update(
            run_id,
            status="RECOVERY_BUY_QUOTING",
            reason=(
                f"{filled_symbol} filled while {missing_symbol} failed; "
                "checking one bounded market/FOK recovery buy"
            ),
            recovery_symbol=missing_symbol,
            details={
                "existingShares": existing_shares,
                "existingTotalCost": existing_total,
                "maximumRecoveryInput": maximum_input,
            },
        )
        recovery_quote, recovery_projection = _request_recovery_buy_quote(
            client,
            wallet_address=wallet_address,
            token_id=str(missing_leg["token_id"]),
            fee_bps=int(missing_leg.get("fee_bps") or 0),
            slippage_bps=base.STATE.config.slippage_bps,
            target_shares=existing_shares,
            maximum_input=maximum_input,
        )
        projected_pnl = (
            min(existing_shares, recovery_projection["shares"])
            - existing_total
            - recovery_projection["total"]
        )
        if projected_pnl < MIN_ONE_WIN_HOLD_PNL_USDT:
            raise ValueError(
                f"recovery buy would leave one-win PnL {projected_pnl:.6f} USDT"
            )
    except Exception as exc:
        _unwind_positions(
            client,
            run_id=run_id,
            market_key=market_key,
            wallet_address=wallet_address,
            wallet_id=wallet_id,
            context=context,
            orders=orders,
            symbols=[filled_symbol],
            final_status="UNWOUND_ONE_SIDED_FILL",
            trigger_reason=(
                f"{missing_symbol} could not be recovered safely: {str(exc)[:500]}"
            ),
        )
        return

    assert recovery_quote is not None and recovery_projection is not None
    EXIT.update(
        run_id,
        status="RECOVERY_BUY_PLACEMENT_IN_FLIGHT",
        reason="recovery quote passed positive one-win gate; placement starting",
        recovery_symbol=missing_symbol,
        details={"quote": recovery_quote, "projection": recovery_projection},
    )
    placement = _place_market(
        client,
        wallet_address=wallet_address,
        wallet_id=wallet_id,
        quote=recovery_quote,
        account_type=base.STATE.config.account_type,
        slippage_bps=base.STATE.config.slippage_bps,
    )
    if not placement.get("ok"):
        if placement.get("ambiguous"):
            _incident(
                run_id=run_id,
                market_key=market_key,
                status="RECOVERY_BUY_AMBIGUOUS_LOCK",
                reason=str(placement.get("error") or "ambiguous recovery placement"),
                details={"placement": placement, "quote": recovery_quote},
            )
        else:
            _unwind_positions(
                client,
                run_id=run_id,
                market_key=market_key,
                wallet_address=wallet_address,
                wallet_id=wallet_id,
                context=context,
                orders=orders,
                symbols=[filled_symbol],
                final_status="UNWOUND_ONE_SIDED_FILL",
                trigger_reason=(
                    f"{missing_symbol} recovery placement was rejected: "
                    f"{placement.get('error')}"
                ),
            )
        return

    recovery_order_id = str(placement["orderId"])
    EXIT.update(
        run_id,
        status="RECOVERY_BUY_TRACKING",
        reason="recovery market/FOK order submitted; reconciling",
        recovery_symbol=missing_symbol,
        recovery_order_id=recovery_order_id,
        details={"placement": placement, "quote": recovery_quote},
    )
    found = _wait_terminal_orders(
        client,
        wallet_address=wallet_address,
        order_ids=[recovery_order_id],
    )
    recovery_order = found.get(recovery_order_id)
    recovery_status = _status(recovery_order)
    if recovery_status != "FILLED":
        if recovery_status in TERMINAL_NO_FILL:
            _unwind_positions(
                client,
                run_id=run_id,
                market_key=market_key,
                wallet_address=wallet_address,
                wallet_id=wallet_id,
                context=context,
                orders=orders,
                symbols=[filled_symbol],
                final_status="UNWOUND_ONE_SIDED_FILL",
                trigger_reason=(
                    f"{missing_symbol} recovery order ended {recovery_status}"
                ),
            )
        else:
            _incident(
                run_id=run_id,
                market_key=market_key,
                status="RECOVERY_BUY_UNRESOLVED_LOCK",
                reason=f"recovery order status is {recovery_status}",
                details={"recoveryOrder": recovery_order or {}},
            )
        return

    completed_orders = dict(orders)
    completed_orders[missing_symbol] = recovery_order
    completed_context = {
        **context,
        "quotes": {**context["quotes"], missing_symbol: recovery_quote},
    }
    evaluation = evaluate_one_win_profitability(
        orders=completed_orders,
        context=completed_context,
    )
    if evaluation["hold"]:
        _resolve_hold(run_id=run_id, evaluation=evaluation)
        return
    _unwind_positions(
        client,
        run_id=run_id,
        market_key=market_key,
        wallet_address=wallet_address,
        wallet_id=wallet_id,
        context=completed_context,
        orders=completed_orders,
        symbols=["BTC", "ETH"],
        final_status="UNWOUND_UNPROFITABLE_PAIR",
        trigger_reason=(
            f"recovered pair one-win PnL {evaluation['oneWinPnl']:.6f} USDT "
            "is not safely positive"
        ),
        evaluation=evaluation,
    )
