from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
INPUTS = [
    BASE / "hft_r2_cycle_option_program_ceiling2_v1_market1574038.json",
    BASE / "hft_r2_cycle_option_program_ceiling2_v1_market1574352.json",
]
OUTPUT = BASE / "hft_r2_cycle_option_program_ceiling2_v1_report.json"


def finite(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def main() -> None:
    markets = []
    for path in INPUTS:
        source = json.loads(path.read_text(encoding="utf-8"))
        rows = source["programs"]
        wait = next(row for row in rows if row["programId"] == "WAIT_ALL")
        wait_floor = finite(wait["finalWorstCaseFloor"])
        wait_pnl = finite(wait["realizedPnl"])
        valid = [
            row
            for row in rows
            if int(row["cycleInvariantViolationCount"]) == 0
            and bool(row["semanticGate"].get("actualInventoryEqualsHftFillLedger"))
        ]
        for row in valid:
            row["floorValueVsWait"] = finite(row["finalWorstCaseFloor"]) - wait_floor
            row["pnlValueVsWait"] = finite(row["realizedPnl"]) - wait_pnl
        oracle = max(
            valid,
            key=lambda row: (
                finite(row["floorValueVsWait"]),
                finite(row["pnlValueVsWait"]),
                -finite(row["finalAbsTrackingError"]),
            ),
        )
        invalid = [row for row in rows if row not in valid]
        markets.append(
            {
                "marketId": int(source["marketId"]),
                "programs": len(rows),
                "validPrograms": len(valid),
                "invalidPrograms": len(invalid),
                "invalidProgramIds": [row["programId"] for row in invalid],
                "waitFloor": wait_floor,
                "waitPnl": wait_pnl,
                "oracleProgram": oracle["programId"],
                "oracleFloorValueVsWait": max(0.0, finite(oracle["floorValueVsWait"])),
                "oraclePnlValueVsWait": max(0.0, finite(oracle["pnlValueVsWait"])),
                "oracleMakerFilledShares": oracle["makerFilledShares"],
                "oracleTakerFilledShares": oracle["takerFilledShares"],
                "oracleFinalPairedCoverage": finite(
                    (oracle["terminalExecutionState"].get("actualPortfolio") or {}).get("combined_paired_coverage")
                ),
                "oracleFinalAbsNet": oracle["finalAbsNet"],
                "oracleFinalAbsTrackingError": oracle["finalAbsTrackingError"],
                "oracleCycleInvariantViolations": oracle["cycleInvariantViolationCount"],
            }
        )

    positive = sum(row["oracleFloorValueVsWait"] > 1e-9 for row in markets)
    invalid_programs = sum(row["invalidPrograms"] for row in markets)
    floor_ceiling = sum(row["oracleFloorValueVsWait"] for row in markets)
    pnl_ceiling = sum(row["oraclePnlValueVsWait"] for row in markets)
    if invalid_programs:
        decision = "NEED_MORE_DATA_FEASIBILITY_MASK_BEFORE_CONTEXTUAL_TRAINING"
    elif positive:
        decision = "KEEP_FOR_CONTEXTUAL_DATASET"
    else:
        decision = "REJECT_PHASE_PROGRAM_ACTION_FAMILY"
    report = {
        "version": "HFT_R2_CYCLE_OPTION_PROGRAM_CEILING2_V1",
        "researchOnly": True,
        "preregistration": "hft_r2_cycle_option_program_ceiling2_v1_preregistered.json",
        "cohort": "NEXT_TWO_CHRONOLOGICAL_OPENED_DEVELOPMENT_MARKETS_AFTER_REPLICATION3_NEW_TO_THIS_CONTRACT",
        "executionSemantics": {
            "engine": "HftBacktest + Predict Execution Tape V1",
            "queueModel": "risk",
            "entryLatencyMs": 1092,
            "responseLatencyMs": 273,
            "partialFills": True,
            "inventoryMutation": "CONFIRMED_HFTBACKTEST_FILLS_ONLY",
            "logicAuthority": "FROZEN_R2",
        },
        "primaryValue": "terminal worst-case portfolio floor minus same-market WAIT_ALL floor",
        "winnerRuntimeInput": False,
        "markets": markets,
        "summary": {
            "markets": len(markets),
            "positiveOracleMarkets": positive,
            "waitOracleMarkets": len(markets) - positive,
            "validProgramRows": sum(row["validPrograms"] for row in markets),
            "invalidProgramRows": invalid_programs,
            "oracleFloorValueCeilingVsWait": floor_ceiling,
            "oraclePnlAuditCeilingVsWait": pnl_ceiling,
            "learnedPolicyRealizedValue": None,
            "chronologicalUnseenOos": False,
            "decision": decision,
        },
        "interpretation": (
            "The full-cycle program family has sparse, regime-dependent positive value: one market supports a large positive paired-floor cycle and one market requires WAIT. "
            "Two negative actions also end with a final Hft fill after the last controller decision and therefore fail the mandatory return-to-logic invariant. "
            "They remain infeasible labels; do not weaken the invariant or use their economics."
        ),
        "next": (
            "Add an explicit feasibility head/mask to the offline dataset (cycle-valid versus infeasible) before any value learner. "
            "Then collect a small chronological multi-market program dataset and require the learned policy to choose WAIT on negative regimes and positive valid programs on positive regimes."
        ),
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(OUTPUT), **report["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
