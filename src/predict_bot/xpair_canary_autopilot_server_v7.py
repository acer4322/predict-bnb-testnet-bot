from __future__ import annotations

import os
import threading
import time
from typing import Any

from . import xpair_canary_autopilot_server as base
from . import xpair_canary_autopilot_server_v2 as v2
from . import xpair_canary_autopilot_server_v4 as v4
from . import xpair_canary_autopilot_server_v5 as v5
from . import xpair_canary_autopilot_server_v6 as v6
from .core import BinancePredictionTradingClient
from .xpair_exit_guard_actions import (
    _attempt_recovery_buy_or_unwind,
    _incident,
    _order_map,
    _resolve_hold,
    _unwind_positions,
    _wallet,
)
from .xpair_exit_guard_common import (
    ACTIVE_EXIT_STATUSES,
    EXIT,
    MAX_SHARE_MISMATCH,
    MIN_ONE_WIN_HOLD_PNL_ENV,
    MIN_ONE_WIN_HOLD_PNL_USDT,
    TERMINAL_EXIT_STATUSES,
    TERMINAL_NO_FILL,
    _order_has_fill,
    _run_context,
    _status,
    evaluate_one_win_profitability,
)

_ORIGINAL_CLASSIFY_PAIR_ORDERS = v4.classify_pair_orders


def classify_pair_orders_with_exit_guard(
    *,
    btc_order: dict[str, Any] | None,
    eth_order: dict[str, Any] | None,
    tracking_age_seconds: float,
    market_ended: bool,
) -> tuple[str, str, str]:
    active = EXIT.latest()
    safety = v4.SAFETY.snapshot()
    active_matches_safety = (
        active is not None
        and int(active.get("run_id") or 0) == int(safety.get("runId") or 0)
    )
    if (
        active_matches_safety
        and str(active.get("status") or "") in ACTIVE_EXIT_STATUSES
    ):
        status = str(active["status"])
        return "TRACK", status, str(active.get("reason") or "exit guard active")

    btc_status = _status(btc_order)
    eth_status = _status(eth_order)
    btc_fill = _order_has_fill(btc_order)
    eth_fill = _order_has_fill(eth_order)

    if btc_status == "FILLED" and eth_status == "FILLED":
        return (
            "TRACK",
            "FILLED_BOTH_POST_FILL_CHECK",
            "both legs filled; verifying actual one-win terminal profitability",
        )

    if btc_fill != eth_fill:
        filled_symbol = "BTC" if btc_fill else "ETH"
        missing_symbol = "ETH" if btc_fill else "BTC"
        missing_status = eth_status if btc_fill else btc_status
        if missing_status in TERMINAL_NO_FILL:
            return (
                "TRACK",
                "ONE_SIDED_RECOVERY_REQUIRED",
                f"{filled_symbol} filled while {missing_symbol} ended {missing_status}",
            )
        if not market_ended and (
            missing_status != "NOT_FOUND"
            or tracking_age_seconds < v4.TRACKING_TIMEOUT_SECONDS
        ):
            return (
                "TRACK",
                "ONE_SIDED_WAITING_OTHER_LEG",
                f"{filled_symbol} filled; waiting for {missing_symbol} terminal status "
                f"({missing_status})",
            )

    return _ORIGINAL_CLASSIFY_PAIR_ORDERS(
        btc_order=btc_order,
        eth_order=eth_order,
        tracking_age_seconds=tracking_age_seconds,
        market_ended=market_ended,
    )


def exit_guard_loop() -> None:
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        base.STATE.log("EXIT_GUARD_DISABLED missing Binance credentials", "ERROR")
        return
    client = BinancePredictionTradingClient(api_key, api_secret)
    wallet_address = wallet_id = ""
    try:
        wallet_address, wallet_id = _wallet(client)
        while True:
            safety = v4.SAFETY.snapshot()
            if not safety.get("locked") or safety.get("lockKind") != "TRACKING":
                time.sleep(0.5)
                continue
            run_id = int(safety.get("runId") or 0)
            market_key = str(safety.get("marketKey") or "")
            if run_id <= 0 or not market_key:
                time.sleep(0.5)
                continue
            existing_exit = EXIT.get(run_id)
            if existing_exit and str(existing_exit.get("status") or "") in (
                ACTIVE_EXIT_STATUSES | TERMINAL_EXIT_STATUSES
            ):
                time.sleep(0.5)
                continue

            btc_id = str(safety.get("btcOrderId") or "")
            eth_id = str(safety.get("ethOrderId") or "")
            if not btc_id or not eth_id:
                time.sleep(0.5)
                continue
            try:
                found = _order_map(client, wallet_address, [btc_id, eth_id])
            except Exception as exc:
                base.STATE.log(f"EXIT_GUARD_ORDER_SYNC_ERROR {str(exc)[:300]}", "ERROR")
                time.sleep(1.0)
                continue
            orders = {"BTC": found.get(btc_id), "ETH": found.get(eth_id)}
            btc_status = _status(orders["BTC"])
            eth_status = _status(orders["ETH"])
            btc_fill = _order_has_fill(orders["BTC"])
            eth_fill = _order_has_fill(orders["ETH"])

            trigger = ""
            if btc_status == "FILLED" and eth_status == "FILLED":
                trigger = "BOTH_FILLED"
            elif btc_fill != eth_fill:
                missing_status = eth_status if btc_fill else btc_status
                if missing_status in TERMINAL_NO_FILL:
                    trigger = "ONE_SIDED_TERMINAL"
            if not trigger:
                time.sleep(0.5)
                continue
            if not EXIT.claim(run_id=run_id, market_key=market_key, trigger=trigger):
                time.sleep(0.5)
                continue

            try:
                context = _run_context(run_id)
                if trigger == "BOTH_FILLED":
                    evaluation = evaluate_one_win_profitability(
                        orders={"BTC": orders["BTC"] or {}, "ETH": orders["ETH"] or {}},
                        context=context,
                    )
                    EXIT.update(
                        run_id,
                        status="EVALUATING",
                        reason="actual fills evaluated against one-win hold rule",
                        one_win_pnl=evaluation["oneWinPnl"],
                        total_cost=evaluation["totalCost"],
                        minimum_payout=evaluation["minimumPayout"],
                        details=evaluation,
                    )
                    if evaluation["hold"]:
                        _resolve_hold(run_id=run_id, evaluation=evaluation)
                    else:
                        _unwind_positions(
                            client,
                            run_id=run_id,
                            market_key=market_key,
                            wallet_address=wallet_address,
                            wallet_id=wallet_id,
                            context=context,
                            orders={
                                "BTC": orders["BTC"] or {},
                                "ETH": orders["ETH"] or {},
                            },
                            symbols=["BTC", "ETH"],
                            final_status="UNWOUND_UNPROFITABLE_PAIR",
                            trigger_reason=(
                                f"actual one-win PnL {evaluation['oneWinPnl']:.6f} USDT "
                                f"is below hold minimum {MIN_ONE_WIN_HOLD_PNL_USDT:.6f} "
                                f"or share mismatch {evaluation['shareMismatch']:.3%} is too large"
                            ),
                            evaluation=evaluation,
                        )
                else:
                    filled_symbol = "BTC" if btc_fill else "ETH"
                    missing_symbol = "ETH" if btc_fill else "BTC"
                    _attempt_recovery_buy_or_unwind(
                        client,
                        run_id=run_id,
                        market_key=market_key,
                        wallet_address=wallet_address,
                        wallet_id=wallet_id,
                        context=context,
                        orders={
                            "BTC": orders["BTC"] or {},
                            "ETH": orders["ETH"] or {},
                        },
                        filled_symbol=filled_symbol,
                        missing_symbol=missing_symbol,
                    )
            except Exception as exc:
                _incident(
                    run_id=run_id,
                    market_key=market_key,
                    status="EXIT_GUARD_INTERNAL_ERROR_LOCK",
                    reason=f"{type(exc).__name__}: {str(exc)[:700]}",
                )
            time.sleep(0.5)
    finally:
        client.close()


_original_state_payload = v6.state_payload


def state_payload() -> dict[str, Any]:
    payload = _original_state_payload()
    payload["exitGuard"] = EXIT.snapshot()
    policy = payload.setdefault("policy", {})
    policy.update(
        {
            "postFillOneWinProfitGuard": True,
            "minimumOneWinHoldPnlUsdt": float(MIN_ONE_WIN_HOLD_PNL_USDT),
            "minimumOneWinHoldPnlEnvironment": MIN_ONE_WIN_HOLD_PNL_ENV,
            "holdRequiresPositiveBothOneWinOutcomes": True,
            "shareMismatchMaximum": float(MAX_SHARE_MISMATCH),
            "oneSidedRecoveryBuyAttempts": 1,
            "recoveryBuyMustRemainOneWinPositive": True,
            "automaticOneSidedUnwind": True,
            "automaticUnprofitablePairUnwind": True,
            "unwindOrderType": "MARKET_FOK",
            "unwindPlacementAttempts": 1,
            "ambiguousExitNeverRetriedBlindly": True,
            "doubleLossRiskStillExists": True,
        }
    )
    return payload


def _restore_or_lock_exit_guard_after_restart() -> None:
    latest = EXIT.latest()
    if latest is None:
        return
    status = str(latest.get("status") or "")
    run_id = int(latest.get("run_id") or 0)
    market_key = str(latest.get("market_key") or "")
    safety = v4.SAFETY.snapshot()
    safety_matches = (
        bool(safety.get("locked"))
        and int(safety.get("runId") or 0) == run_id
    )

    if status in {
        "HOLD_PROFITABLE_PAIR",
        "UNWOUND_UNPROFITABLE_PAIR",
        "UNWOUND_ONE_SIDED_FILL",
    } and safety_matches:
        reason = str(latest.get("reason") or "completed exit guard restored")
        v4.SAFETY.resolve_automatically(status, reason)
        base.STATE.log(
            f"EXIT_GUARD_COMPLETION_RESTORED {status}: {reason}",
            "WARN",
        )
        return

    if status == "EXIT_GUARD_INCIDENT":
        if safety_matches and safety.get("lockKind") != "INCIDENT":
            v4.SAFETY.activate_incident(
                status="EXIT_GUARD_RESTORED_INCIDENT_LOCK",
                reason=str(latest.get("reason") or "exit guard incident restored"),
                market_key=market_key or safety.get("marketKey"),
                run_id=run_id or safety.get("runId"),
                btc_order_id=safety.get("btcOrderId"),
                eth_order_id=safety.get("ethOrderId"),
                btc_status=safety.get("btcStatus"),
                eth_status=safety.get("ethStatus"),
                market_end_ms=safety.get("marketEndMs"),
            )
        return

    if status not in ACTIVE_EXIT_STATUSES:
        return
    if not safety_matches:
        EXIT.update(
            run_id,
            status="EXIT_GUARD_INCIDENT",
            reason=(
                "ORPHANED_EXIT_GUARD_RECORD: active recovery record did not match "
                "the durable XPAIR safety lock on restart"
            ),
            resolved=True,
        )
        return

    reason = (
        f"process restarted while exit guard was {status}; automatic placement "
        "is not replayed because the prior execution state may be ambiguous"
    )
    EXIT.update(
        run_id,
        status="EXIT_GUARD_INCIDENT",
        reason=f"EXIT_GUARD_RESTART_RECONCILE_LOCK: {reason}",
        resolved=True,
    )
    v4.SAFETY.activate_incident(
        status="EXIT_GUARD_RESTART_RECONCILE_LOCK",
        reason=reason,
        market_key=market_key or safety.get("marketKey"),
        run_id=run_id or safety.get("runId"),
        btc_order_id=safety.get("btcOrderId"),
        eth_order_id=safety.get("ethOrderId"),
        btc_status=safety.get("btcStatus"),
        eth_status=safety.get("ethStatus"),
        market_end_ms=safety.get("marketEndMs"),
    )
    base.STATE.disarm("stale_exit_guard_after_restart")
    base.STATE.log(
        f"EXIT_GUARD_RESTART_RECONCILE_LOCK: {reason}",
        "ERROR",
    )


class Handler(v6.Handler):
    server_version = "BTC5MLabXPairAutopilot/7.0"


def install_patches() -> None:
    v6.install_patches()
    _restore_or_lock_exit_guard_after_restart()
    v4.classify_pair_orders = classify_pair_orders_with_exit_guard
    base.state_payload = state_payload
    v4.state_payload = state_payload
    v6.state_payload = state_payload


def main() -> None:
    install_patches()
    threading.Thread(
        target=v2.monitor_loop,
        name="xpair-autopilot-monitor-v7",
        daemon=True,
    ).start()
    threading.Thread(
        target=v5.resilient_order_watch_loop,
        name="xpair-order-watch-v7",
        daemon=True,
    ).start()
    threading.Thread(
        target=v5.incident_order_audit_loop,
        name="xpair-incident-audit-v7",
        daemon=True,
    ).start()
    threading.Thread(
        target=exit_guard_loop,
        name="xpair-exit-guard-v7",
        daemon=True,
    ).start()
    server = base.ThreadingHTTPServer((base.API_HOST, base.API_PORT), Handler)
    print(
        f"XPAIR autopilot v7 API listening on http://{base.API_HOST}:{base.API_PORT}; "
        f"both fills must retain at least {MIN_ONE_WIN_HOLD_PNL_USDT:.2f} USDT "
        "in the lower one-win outcome, otherwise MARKET/FOK unwind is attempted; "
        "one-sided terminal fills receive one bounded recovery buy before unwind"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        base.STATE.disarm("server_shutdown")
        server.server_close()


if __name__ == "__main__":
    main()
