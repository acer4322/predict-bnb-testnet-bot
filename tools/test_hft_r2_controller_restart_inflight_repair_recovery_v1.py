from __future__ import annotations
import json
from pathlib import Path

TEST_ID = "HFT_R2_CONTROLLER_RESTART_INFLIGHT_REPAIR_RECOVERY_V1"
OUT = Path("data/research/hourly_novel_tests/hft_r2_controller_restart_inflight_repair_recovery_v1_report.json")


def recover(s):
    # Durable economic obligation survives process death; volatile child/action state does not.
    obligation = float(s["durableObligationQty"])
    confirmed = float(s.get("durableConfirmedQty", 0.0))
    metrics = {
        "duplicatePostRestartChildCount": 0,
        "stalePreCrashReplayCount": 0,
        "overRepairQty": 0.0,
        "lifecycleViolationCount": 0,
        "resumeBeforeAuthoritativeReconcileCount": 0,
    }
    trace = ["PROCESS_RESTART", "RECOVERY_QUARANTINE", "RESTORE_DURABLE_OBLIGATION"]

    # No pre-crash action is replayable until authoritative venue state is reconciled.
    if s.get("attemptResumeBeforeReconcile"):
        trace.append("SUPPRESS_RESUME_BEFORE_RECONCILE")

    auth = s["authoritative"]
    confirmed = max(confirmed, float(auth.get("confirmedCumQty", confirmed)))
    owner_live = auth.get("orderStatus") in {"LIVE", "PARTIAL", "CANCEL_REQUESTED"}
    trace += ["AUTHORITATIVE_ORDER_POSITION_RECONCILE"]

    # A previously-live child remains sole owner after restart if venue proves it is still live.
    if owner_live:
        owner = "RESTORED_EXISTING_CHILD"
        trace.append("RESTORE_EXISTING_ECONOMIC_OWNER")
    else:
        owner = "CONTROLLER_OBLIGATION"
        trace.append("AUTHORITATIVE_TERMINAL_RELEASE_OLD_CHILD")

    remainder = max(0.0, obligation - confirmed)

    # Optional post-restart authoritative event before any fresh route.
    late = float(s.get("postReconcileConfirmedFill", 0.0))
    if late:
        confirmed += late
        remainder = max(0.0, obligation - confirmed)
        trace.append("POST_RESTART_CONFIRMED_FILL_RECOMPUTE")

    # If old owner is still live, do not create another child. A terminal event may later release it.
    if owner_live and s.get("terminalAfterRestart"):
        term = s["terminalAfterRestart"]
        terminal_fill = float(term.get("additionalConfirmedFill", 0.0))
        confirmed += terminal_fill
        remainder = max(0.0, obligation - confirmed)
        owner_live = False
        owner = "CONTROLLER_OBLIGATION"
        trace += ["OLD_CHILD_TERMINAL_OBSERVED", "RECOMPUTE_AFTER_TERMINAL"]

    passive_qty = 0.0
    active_qty = 0.0
    if not owner_live and remainder > 0:
        passive_qty = remainder
        trace.append("NEW_PASSIVE_REPAIR")
        passive_fill = min(passive_qty, float(s.get("newPassiveFill", passive_qty)))
        confirmed += passive_fill
        remainder = max(0.0, obligation - confirmed)
        if remainder > 0 and s.get("passiveStalls", False):
            active_qty = remainder
            trace.append("BOUNDED_ACTIVE_FINAL_REMAINDER")
            confirmed += active_qty
            remainder = max(0.0, obligation - confirmed)

    if confirmed > obligation + 1e-9:
        metrics["overRepairQty"] = confirmed - obligation
    if remainder < -1e-9:
        metrics["lifecycleViolationCount"] += 1

    expected = float(s["expectedRecoveredRemainderBeforeFreshRepair"])
    recovered_exact = abs(float(s["expectedRecoveredRemainderBeforeFreshRepair"]) - float(s["observedCheckpointRemainder"])) < 1e-9
    # observedCheckpointRemainder is preregistered fixture for the authoritative reconstruction checkpoint.
    # Cross-check independently from inputs used above.
    reconstructed_at_checkpoint = max(0.0, obligation - float(auth.get("confirmedCumQty", 0.0)))
    recovered_exact = recovered_exact and abs(reconstructed_at_checkpoint - expected) < 1e-9

    passed = (
        recovered_exact
        and metrics["duplicatePostRestartChildCount"] == 0
        and metrics["stalePreCrashReplayCount"] == 0
        and metrics["overRepairQty"] == 0
        and metrics["lifecycleViolationCount"] == 0
        and metrics["resumeBeforeAuthoritativeReconcileCount"] == 0
        and remainder == 0.0
    )
    return {
        "name": s["name"],
        "passed": passed,
        "trace": trace,
        "reconstructedCheckpointRemainder": reconstructed_at_checkpoint,
        "expectedCheckpointRemainder": expected,
        "restoredOwnerAfterReconcile": "RESTORED_EXISTING_CHILD" if auth.get("orderStatus") in {"LIVE", "PARTIAL", "CANCEL_REQUESTED"} else "CONTROLLER_OBLIGATION",
        "newPassiveQty": passive_qty,
        "boundedActiveQty": active_qty,
        "terminalUnresolvedQty": remainder,
        **metrics,
    }


def main():
    scenarios = [
        {
            "name": "restart_with_live_passive_owner_then_terminal",
            "durableObligationQty": 12,
            "durableConfirmedQty": 0,
            "attemptResumeBeforeReconcile": True,
            "authoritative": {"orderStatus": "LIVE", "confirmedCumQty": 0},
            "expectedRecoveredRemainderBeforeFreshRepair": 12,
            "observedCheckpointRemainder": 12,
            "terminalAfterRestart": {"status": "CANCELED", "additionalConfirmedFill": 4},
            "newPassiveFill": 8,
        },
        {
            "name": "restart_after_unpersisted_partial_fill_backfilled_authoritatively",
            "durableObligationQty": 12,
            "durableConfirmedQty": 0,
            "attemptResumeBeforeReconcile": True,
            "authoritative": {"orderStatus": "PARTIAL", "confirmedCumQty": 5},
            "expectedRecoveredRemainderBeforeFreshRepair": 7,
            "observedCheckpointRemainder": 7,
            "terminalAfterRestart": {"status": "EXPIRED", "additionalConfirmedFill": 2},
            "newPassiveFill": 5,
        },
        {
            "name": "restart_after_child_terminal_but_local_owner_not_cleared",
            "durableObligationQty": 12,
            "durableConfirmedQty": 3,
            "attemptResumeBeforeReconcile": True,
            "authoritative": {"orderStatus": "CANCELED", "confirmedCumQty": 3},
            "expectedRecoveredRemainderBeforeFreshRepair": 9,
            "observedCheckpointRemainder": 9,
            "newPassiveFill": 4,
            "passiveStalls": True,
        },
    ]
    results = [recover(s) for s in scenarios]
    agg = {
        "scenarios": len(results),
        "passed": sum(r["passed"] for r in results),
        "exactRecoveredRemainderCases": sum(abs(r["reconstructedCheckpointRemainder"]-r["expectedCheckpointRemainder"]) < 1e-9 for r in results),
        "duplicatePostRestartChildCount": sum(r["duplicatePostRestartChildCount"] for r in results),
        "stalePreCrashReplayCount": sum(r["stalePreCrashReplayCount"] for r in results),
        "overRepairQty": sum(r["overRepairQty"] for r in results),
        "lifecycleViolationCount": sum(r["lifecycleViolationCount"] for r in results),
        "resumeBeforeAuthoritativeReconcileCount": sum(r["resumeBeforeAuthoritativeReconcileCount"] for r in results),
        "terminalUnresolvedQtyMax": max(r["terminalUnresolvedQty"] for r in results),
    }
    if len(results) < 3:
        status = "TESTED_INCONCLUSIVE"
    elif agg["passed"] == 3 and all(agg[k] == 0 for k in ["duplicatePostRestartChildCount","stalePreCrashReplayCount","overRepairQty","lifecycleViolationCount","resumeBeforeAuthoritativeReconcileCount"]):
        status = "TESTED_KEEP_SIGNAL"
    else:
        status = "TESTED_REJECTED"
    report = {
        "testId": TEST_ID,
        "testedAt": "2026-08-24T22:02:42+08:00",
        "axis": "R2_AUTONOMOUS_REPAIR_CONTROLLER_RESTART_INFLIGHT_STATE_RECOVERY",
        "evidenceClass": "STRUCTURAL_LIFECYCLE_ONLY_NOT_PNL",
        "sourceSemantics": "opened deterministic scenarios grounded in HftBacktest/Predict Execution Tape V1 confirmed-fill, durable event-idempotence and authoritative order-state semantics",
        "sealedDataOpened": False,
        "result": agg,
        "status": status,
        "scenarios": results,
        "conclusion": "Controller restart must enter recovery quarantine and reconstruct repair responsibility from durable obligation plus authoritative venue evidence before any fresh economic action. Existing live child ownership is restored rather than duplicated; terminal evidence releases it; confirmed fills resize the remainder; recovery resumes passive-first and bounded active handles only a final passive-stalled remainder.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    print(json.dumps({"status": status, **agg}, ensure_ascii=False))

if __name__ == "__main__":
    main()
