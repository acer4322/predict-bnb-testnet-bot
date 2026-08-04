from __future__ import annotations

import threading
import time
from typing import Any, Callable

from . import xpair_canary_autopilot_server as base
from . import xpair_canary_autopilot_server_v2 as v2
from . import xpair_canary_autopilot_server_v4 as v4
from . import xpair_canary_autopilot_server_v5 as v5
from . import xpair_canary_autopilot_server_v7 as v7
from . import xpair_canary_autopilot_server_v8 as v8
from . import xpair_canary_autopilot_server_v10 as v10
from .xpair_dashboard_paper_sim import paper_simulation_loop

PLACEMENT_INCOMPLETE_STATUS = "PLACEMENT_INCOMPLETE_MANUAL_RECONCILE"
_RESTART_DELAY_SECONDS = 2.0
_DIAGNOSTIC_INTERVAL_SECONDS = 0.5


def incident_automation_snapshot(
    safety: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = dict(safety or v4.SAFETY.snapshot())
    locked = bool(snapshot.get("locked"))
    lock_kind = str(snapshot.get("lockKind") or "CLEAR")
    status = str(snapshot.get("status") or "CLEAR")
    btc_id = str(snapshot.get("btcOrderId") or "")
    eth_id = str(snapshot.get("ethOrderId") or "")
    btc_status = str(snapshot.get("btcStatus") or "UNKNOWN").upper()
    eth_status = str(snapshot.get("ethStatus") or "UNKNOWN").upper()
    known_ids = [item for item in (btc_id, eth_id) if item]

    result: dict[str, Any] = {
        "locked": locked,
        "lockKind": lock_kind,
        "incidentStatus": status,
        "btcOrderIdPresent": bool(btc_id),
        "ethOrderIdPresent": bool(eth_id),
        "btcStatus": btc_status,
        "ethStatus": eth_status,
        "automaticRecoveryEnabledForExplicitReject": True,
        "automaticRecoveryEnabledForAmbiguousPlacement": False,
    }
    if not locked:
        result.update(
            state="IDLE",
            automaticActionAllowed=False,
            reason="no persistent XPAIR safety lock is active",
        )
        return result
    if lock_kind != "INCIDENT":
        result.update(
            state="ORDER_TRACKING",
            automaticActionAllowed=True,
            reason="submitted order pair is still under normal reconciliation",
        )
        return result
    if status != PLACEMENT_INCOMPLETE_STATUS:
        result.update(
            state="OTHER_INCIDENT",
            automaticActionAllowed=False,
            reason=f"incident {status} is handled by the post-fill exit guard or requires reconciliation",
        )
        return result
    if len(known_ids) == 0:
        result.update(
            state="BLOCKED_NO_ORDER_IDS",
            automaticActionAllowed=False,
            reason=(
                "neither placement returned an order ID; order history and positions "
                "must be reconciled before any compensating trade"
            ),
        )
        return result
    if len(known_ids) == 2:
        result.update(
            state="BLOCKED_TWO_ORDER_IDS_IN_INCOMPLETE_STATE",
            automaticActionAllowed=False,
            reason=(
                "both order IDs exist even though placement was marked incomplete; "
                "normal order reconciliation must identify their true terminal states"
            ),
        )
        return result

    known_symbol = "BTC" if btc_id else "ETH"
    missing_symbol = "ETH" if btc_id else "BTC"
    missing_status = eth_status if btc_id else btc_status
    result["knownOrderSymbol"] = known_symbol
    result["missingOrderSymbol"] = missing_symbol
    result["missingPlacementStatus"] = missing_status
    if missing_status == "REJECTED":
        result.update(
            state="AUTO_RECOVERY_WAITING_KNOWN_ORDER",
            automaticActionAllowed=True,
            reason=(
                f"{missing_symbol} placement was explicitly rejected; the worker will "
                f"inspect the known {known_symbol} order, then recover or unwind only "
                "after its actual fill state is known"
            ),
        )
        return result

    result.update(
        state="BLOCKED_NONDETERMINISTIC_MISSING_LEG",
        automaticActionAllowed=False,
        reason=(
            f"{missing_symbol} placement status is {missing_status}; it may have reached "
            "the exchange despite the missing response, so an automatic buy or sell "
            "could create a new one-sided position"
        ),
    )
    return result


def resilient_worker(
    worker: Callable[[], None],
    *,
    label: str,
    restart_delay_seconds: float = _RESTART_DELAY_SECONDS,
) -> None:
    while True:
        try:
            worker()
            base.STATE.log(
                f"{label}_STOPPED unexpectedly; restarting",
                "ERROR",
            )
        except Exception as exc:
            base.STATE.log(
                f"{label}_RESTARTING {type(exc).__name__}: {str(exc)[:500]}",
                "ERROR",
            )
        time.sleep(max(0.25, float(restart_delay_seconds)))


def incident_diagnostic_loop() -> None:
    last_signature = ""
    while True:
        diagnostic = incident_automation_snapshot()
        signature = "|".join(
            str(diagnostic.get(key) or "")
            for key in (
                "state",
                "incidentStatus",
                "btcStatus",
                "ethStatus",
                "btcOrderIdPresent",
                "ethOrderIdPresent",
            )
        )
        if signature != last_signature and diagnostic.get("locked"):
            level = (
                "WARN"
                if diagnostic.get("automaticActionAllowed")
                else "ERROR"
            )
            base.STATE.log(
                "INCIDENT_AUTOMATION "
                f"state={diagnostic.get('state')} "
                f"BTC={diagnostic.get('btcStatus')} "
                f"ETH={diagnostic.get('ethStatus')} "
                f"reason={str(diagnostic.get('reason') or '')[:500]}",
                level,
            )
        last_signature = signature
        time.sleep(_DIAGNOSTIC_INTERVAL_SECONDS)


def state_payload() -> dict[str, Any]:
    payload = v10.state_payload()
    payload["incidentAutomation"] = incident_automation_snapshot(
        payload.get("safety") if isinstance(payload.get("safety"), dict) else None
    )
    policy = payload.setdefault("policy", {})
    policy.update(
        {
            "resilientExitGuardWorker": True,
            "resilientPlacementIncidentWorker": True,
            "incidentAutomationDiagnostics": True,
            "ambiguousPlacementRemainsHardLocked": True,
        }
    )
    return payload


class Handler(v10.Handler):
    server_version = "BTC5MLabXPairAutopilot/11.0"


def install_patches() -> None:
    v10.install_patches()
    base.state_payload = state_payload
    v4.state_payload = state_payload


def main() -> None:
    install_patches()
    threading.Thread(
        target=v2.monitor_loop,
        name="xpair-autopilot-monitor-v11",
        daemon=True,
    ).start()
    threading.Thread(
        target=v5.resilient_order_watch_loop,
        name="xpair-order-watch-v11",
        daemon=True,
    ).start()
    threading.Thread(
        target=v5.incident_order_audit_loop,
        name="xpair-incident-audit-v11",
        daemon=True,
    ).start()
    threading.Thread(
        target=resilient_worker,
        kwargs={"worker": v7.exit_guard_loop, "label": "EXIT_GUARD"},
        name="xpair-exit-guard-v11",
        daemon=True,
    ).start()
    threading.Thread(
        target=resilient_worker,
        kwargs={
            "worker": v8.placement_incident_exit_loop,
            "label": "PLACEMENT_INCIDENT_EXIT",
        },
        name="xpair-placement-incident-exit-v11",
        daemon=True,
    ).start()
    threading.Thread(
        target=incident_diagnostic_loop,
        name="xpair-incident-diagnostics-v11",
        daemon=True,
    ).start()
    threading.Thread(
        target=paper_simulation_loop,
        name="xpair-dashboard-paper-v11",
        daemon=True,
    ).start()
    server = base.ThreadingHTTPServer((base.API_HOST, base.API_PORT), Handler)
    print(
        "XPAIR autopilot v11 API listening on "
        f"http://{base.API_HOST}:{base.API_PORT}; exit and placement-incident workers "
        "restart after transient failures, incident automation diagnostics are exposed, "
        "and ambiguous placements remain persistently hard-locked"
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
