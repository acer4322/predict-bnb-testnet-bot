from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke


OUT = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
OUTPUT = OUT / "hft_r2_cycle_ownstate_event_reentry_v1_report.json"


def finite(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def compact(row: dict[str, Any]) -> dict[str, Any]:
    feedback = row["actualFillFeedbackCycles"]
    reentries = row["lifecycleAudit"]["ownStateEventReentries"]
    return {
        "runtimeSeconds": row["runtimeSeconds"],
        "realizedPnl": row["actualExecution"]["realizedPnl"],
        "worstCaseFloor": finite(row["actualExecution"]["finalPortfolio"].get("worst_case_floor")),
        "makerFilledShares": row["actualExecution"]["makerFilledShares"],
        "takerFilledShares": row["actualExecution"]["takerFilledShares"],
        "controllerSteps": row["controller"]["steps"],
        "ownStateReentries": len(reentries),
        "fillsObservedOnOwnStateReentry": sum(
            cycle["controllerReevaluatedAtMs"] is not None
            and any(int(reentry["atMs"]) == int(cycle["controllerReevaluatedAtMs"]) for reentry in reentries)
            for cycle in feedback
        ),
        "actualFillWithoutControllerReevaluation": len(
            row["cycleInvariantViolations"]["actualFillWithoutControllerReevaluation"]
        ),
        "cycleInvariantViolationCount": row["cycleInvariantViolationCount"],
        "actualInventoryEqualsHftFillLedger": row["semanticGate"]["actualInventoryEqualsHftFillLedger"],
        "unauthorizedExecutorTakerAttempt": len(
            row["cycleInvariantViolations"]["unauthorizedExecutorTakerAttempt"]
        ),
    }


def main() -> None:
    program = {"PASSIVE_MAINTAIN": "offset0", "PASSIVE_REPAIR": "offset1"}
    baseline = run_smoke(1574352, passive_mode="wait", passive_program=program)
    event = run_smoke(
        1574352,
        passive_mode="wait",
        passive_program=program,
        own_state_poll_ms=250,
    )
    baseline_summary = compact(baseline)
    event_summary = compact(event)
    passes = (
        event_summary["cycleInvariantViolationCount"] == 0
        and event_summary["actualInventoryEqualsHftFillLedger"]
        and event_summary["unauthorizedExecutorTakerAttempt"] == 0
        and event_summary["ownStateReentries"] > 0
    )
    report = {
        "version": "HFT_R2_CYCLE_OWNSTATE_EVENT_REENTRY_V1",
        "researchOnly": True,
        "preregistration": "hft_r2_cycle_ownstate_event_reentry_v1_preregistered.json",
        "marketId": 1574352,
        "cohort": "REVEALED_ARCHITECTURE_SMOKE_ONLY",
        "program": program,
        "executionSemantics": {
            "engine": "HftBacktest + Predict Execution Tape V1",
            "queueModel": "risk",
            "entryLatencyMs": 1092,
            "responseLatencyMs": 273,
            "partialFills": True,
            "inventoryMutation": "CONFIRMED_HFTBACKTEST_FILLS_ONLY",
            "logicAuthority": "FROZEN_R2",
            "eventOwnStatePollMs": 250,
            "eventPublicState": "LAST_STRICTLY_OBSERVED_PUBLIC_STATE_ASOF_POLL",
        },
        "baseline": baseline_summary,
        "eventDriven": event_summary,
        "trajectoryChanged": {
            "makerFilledSharesDelta": finite(event_summary["makerFilledShares"])
            - finite(baseline_summary["makerFilledShares"]),
            "takerFilledSharesDelta": finite(event_summary["takerFilledShares"])
            - finite(baseline_summary["takerFilledShares"]),
            "floorDelta": finite(event_summary["worstCaseFloor"])
            - finite(baseline_summary["worstCaseFloor"]),
        },
        "winnerRuntimeInput": False,
        "targetFutureRuntimeInput": False,
        "decision": "KEEP_FOR_SMALL_CEILING" if passes else "REJECT_EVENT_REENTRY",
        "interpretation": (
            "Own-state polling is part of the execution transition, not a profitability threshold. "
            "It may change later R2 actions because confirmed fills enter portfolio state before the next public snapshot."
        ),
        "next": (
            "If kept, run the unchanged 13-program action family on preregistered markets 1574538 and 1574737. "
            "Use portfolio floor as primary value and reject any remaining lifecycle-invalid row."
        ),
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(OUTPUT), **report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
