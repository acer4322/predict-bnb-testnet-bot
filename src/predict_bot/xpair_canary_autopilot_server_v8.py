from __future__ import annotations

import os
import threading
import time
from typing import Any

from . import xpair_canary_autopilot_server as base
from . import xpair_canary_autopilot_server_v2 as v2
from . import xpair_canary_autopilot_server_v4 as v4
from . import xpair_canary_autopilot_server_v5 as v5
from . import xpair_canary_autopilot_server_v7 as v7
from .core import BinancePredictionTradingClient
from .xpair_exit_guard_actions import (
    _attempt_recovery_buy_or_unwind,
    _incident,
    _order_map,
    _wallet,
)
from .xpair_exit_guard_common import (
    ACTIVE_EXIT_STATUSES,
    EXIT,
    TERMINAL_EXIT_STATUSES,
    TERMINAL_NO_FILL,
    _merge_recovery_details,
    _order_has_fill,
    _run_context,
    _status,
)

PLACEMENT_INCOMPLETE_STATUS = "PLACEMENT_INCOMPLETE_MANUAL_RECONCILE"
_V7_STATE_PAYLOAD = v7.state_payload


def classify_explicit_incomplete_placement(
    *,
    safety: dict[str, Any],
    known_order: dict[str, Any] | None,
) -> tuple[str, str, str]:
    """Classify only placement failures that are safe to compensate automatically."""
    btc_id = str(safety.get("btcOrderId") or "")
    eth_id = str(safety.get("ethOrderId") or "")
    if bool(btc_id) == bool(eth_id):
        return "IGNORE", "IDENTIFIER_COUNT_UNSAFE", "expected exactly one known order ID"

    known_symbol = "BTC" if btc_id else "ETH"
    missing_symbol = "ETH" if btc_id else "BTC"
    missing_status = str(
        safety.get("ethStatus" if btc_id else "btcStatus") or "UNKNOWN"
    ).upper()
    if missing_status != "REJECTED":
        return (
            "IGNORE",
            "MISSING_LEG_NOT_DETERMINISTIC",
            f"{missing_symbol} placement status is {missing_status}; automatic compensation is unsafe",
        )

    known_status = _status(known_order)
    if _order_has_fill(known_order):
        return (
            "RECOVER_OR_UNWIND",
            "ONE_SIDED_PLACEMENT_REJECTED",
            f"{known_symbol} has a fill while {missing_symbol} placement was explicitly rejected",
        )
    if known_status in TERMINAL_NO_FILL:
        return (
            "RESOLVE_NO_EXPOSURE",
            "NO_EXPOSURE_PLACEMENT_INCOMPLETE",
            f"{known_symbol} ended {known_status} without a fill and {missing_symbol} was rejected",
        )
    return (
        "WAIT",
        "KNOWN_ORDER_NOT_TERMINAL",
        f"waiting for {known_symbol} order terminal state ({known_status})",
    )


def _resolve_no_exposure(*, run_id: int, reason: str) -> None:
    status = "NO_EXPOSURE_PLACEMENT_INCOMPLETE"
    EXIT.update(
        run_id,
        status=status,
        reason=reason,
        details={"exposure": "NONE_CONFIRMED"},
        resolved=True,
    )
    v4.SAFETY.resolve_automatically(status, reason)
    _merge_recovery_details(
        run_id,
        status=status,
        message=reason,
        recovery=EXIT.snapshot() or {},
    )
    base.STATE.set_phase(status)
    base.STATE.log(f"{status}: {reason}", "WARN")


def placement_incident_exit_loop() -> None:
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        base.STATE.log(
            "PLACEMENT_INCIDENT_EXIT_DISABLED missing Binance credentials",
            "ERROR",
        )
        return
    client = BinancePredictionTradingClient(api_key, api_secret)
    wallet_address = wallet_id = ""
    try:
        wallet_address, wallet_id = _wallet(client)
        while True:
            safety = v4.SAFETY.snapshot()
            if (
                not safety.get("locked")
                or safety.get("lockKind") != "INCIDENT"
                or safety.get("status") != PLACEMENT_INCOMPLETE_STATUS
            ):
                time.sleep(0.5)
                continue

            run_id = int(safety.get("runId") or 0)
            market_key = str(safety.get("marketKey") or "")
            if run_id <= 0 or not market_key:
                time.sleep(0.5)
                continue
            existing = EXIT.get(run_id)
            if existing and str(existing.get("status") or "") in (
                ACTIVE_EXIT_STATUSES | TERMINAL_EXIT_STATUSES
            ):
                time.sleep(0.5)
                continue

            btc_id = str(safety.get("btcOrderId") or "")
            eth_id = str(safety.get("ethOrderId") or "")
            known_ids = [item for item in (btc_id, eth_id) if item]
            if len(known_ids) != 1:
                time.sleep(0.5)
                continue
            try:
                found = _order_map(client, wallet_address, known_ids)
            except Exception as exc:
                base.STATE.log(
                    f"PLACEMENT_INCIDENT_ORDER_SYNC_ERROR {str(exc)[:300]}",
                    "ERROR",
                )
                time.sleep(1.0)
                continue

            known_id = known_ids[0]
            known_order = found.get(known_id)
            action, status, reason = classify_explicit_incomplete_placement(
                safety=safety,
                known_order=known_order,
            )
            if action in {"IGNORE", "WAIT"}:
                time.sleep(0.5)
                continue
            if not EXIT.claim(run_id=run_id, market_key=market_key, trigger=status):
                time.sleep(0.5)
                continue

            try:
                if action == "RESOLVE_NO_EXPOSURE":
                    _resolve_no_exposure(run_id=run_id, reason=reason)
                    time.sleep(0.5)
                    continue

                known_symbol = "BTC" if btc_id else "ETH"
                missing_symbol = "ETH" if btc_id else "BTC"
                context = _run_context(run_id)
                orders = {
                    "BTC": known_order if known_symbol == "BTC" else {},
                    "ETH": known_order if known_symbol == "ETH" else {},
                }
                _attempt_recovery_buy_or_unwind(
                    client,
                    run_id=run_id,
                    market_key=market_key,
                    wallet_address=wallet_address,
                    wallet_id=wallet_id,
                    context=context,
                    orders=orders,
                    filled_symbol=known_symbol,
                    missing_symbol=missing_symbol,
                )
            except Exception as exc:
                _incident(
                    run_id=run_id,
                    market_key=market_key,
                    status="PLACEMENT_INCIDENT_EXIT_INTERNAL_ERROR_LOCK",
                    reason=f"{type(exc).__name__}: {str(exc)[:700]}",
                )
            time.sleep(0.5)
    finally:
        client.close()


def state_payload() -> dict[str, Any]:
    payload = _V7_STATE_PAYLOAD()
    policy = payload.setdefault("policy", {})
    policy.update(
        {
            "explicitRejectedPlacementRecovery": True,
            "ambiguousPlacementAutomaticRecovery": False,
            "noExposureIncompletePlacementAutoClear": True,
        }
    )
    return payload


class Handler(v7.Handler):
    server_version = "BTC5MLabXPairAutopilot/8.0"


def install_patches() -> None:
    v7.install_patches()
    base.state_payload = state_payload
    v4.state_payload = state_payload


def main() -> None:
    install_patches()
    threading.Thread(
        target=v2.monitor_loop,
        name="xpair-autopilot-monitor-v8",
        daemon=True,
    ).start()
    threading.Thread(
        target=v5.resilient_order_watch_loop,
        name="xpair-order-watch-v8",
        daemon=True,
    ).start()
    threading.Thread(
        target=v5.incident_order_audit_loop,
        name="xpair-incident-audit-v8",
        daemon=True,
    ).start()
    threading.Thread(
        target=v7.exit_guard_loop,
        name="xpair-exit-guard-v8",
        daemon=True,
    ).start()
    threading.Thread(
        target=placement_incident_exit_loop,
        name="xpair-placement-incident-exit-v8",
        daemon=True,
    ).start()
    server = base.ThreadingHTTPServer((base.API_HOST, base.API_PORT), Handler)
    print(
        "XPAIR autopilot v8 API listening on "
        f"http://{base.API_HOST}:{base.API_PORT}; actual two-leg fills must retain "
        "positive one-win terminal PnL, explicit one-leg placement rejection is "
        "recovered once or unwound, and ambiguous placement remains hard-locked"
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
