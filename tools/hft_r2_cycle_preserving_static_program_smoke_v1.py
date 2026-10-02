from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke  # noqa: E402


OUT = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
MARKET_ID = 1572594
PROGRAMS = ["wait", "offset2", "offset1", "offset0"]


def terminal_key(row: dict[str, Any]) -> tuple[float, int]:
    pnl = row["actualExecution"].get("realizedPnl")
    value = -math.inf if pnl is None or not math.isfinite(float(pnl)) else float(pnl)
    return value, -PROGRAMS.index(row["policy"]["passiveMode"])


def main() -> None:
    rows = []
    for mode in PROGRAMS:
        row = run_smoke(MARKET_ID, passive_mode=mode)
        rows.append(row)
        print(
            json.dumps(
                {
                    "marketId": MARKET_ID,
                    "passiveMode": mode,
                    "runtimeSeconds": row["runtimeSeconds"],
                    "decision": row["decision"],
                    "realizedPnl": row["actualExecution"]["realizedPnl"],
                    "worstCaseFloor": row["actualExecution"]["finalPortfolio"]["worst_case_floor"],
                    "finalAbsTrackingError": row["actualExecution"]["finalAbsTrackingError"],
                    "makerFills": row["r2ObjectiveExecution"]["makerFills"],
                    "takerFills": row["r2ObjectiveExecution"]["takerFills"],
                    "violationCount": row["cycleInvariantViolationCount"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    oracle = max(rows, key=terminal_key)
    all_programs_cycle_valid = all(
        row["cycleInvariantViolationCount"] == 0
        and row["semanticGate"]["actualInventoryEqualsHftFillLedger"]
        and row["semanticGate"]["noFreeExecutorTakerSideOrMode"]
        for row in rows
    )
    oracle_pnl = float(oracle["actualExecution"]["realizedPnl"])
    any_actual_fill = any(
        row["r2ObjectiveExecution"]["makerFills"] + row["r2ObjectiveExecution"]["takerFills"] > 0
        for row in rows
    )
    decision = (
        "KEEP_FOR_THREE_MARKET_PILOT"
        if all_programs_cycle_valid and oracle_pnl > 0.0 and any_actual_fill
        else "REJECT_STATIC_OFFSET_FAMILY"
    )
    summaries = [
        {
            "passiveMode": row["policy"]["passiveMode"],
            "runtimeSeconds": row["runtimeSeconds"],
            "semanticDecision": row["decision"],
            "realizedPnl": row["actualExecution"]["realizedPnl"],
            "worstCaseFloor": row["actualExecution"]["finalPortfolio"]["worst_case_floor"],
            "finalAbsTrackingError": row["actualExecution"]["finalAbsTrackingError"],
            "combinedFinalAbsNet": row["actualExecution"]["combinedFinalAbsNet"],
            "makerFilledShares": row["actualExecution"]["makerFilledShares"],
            "takerFilledShares": row["actualExecution"]["takerFilledShares"],
            "r2DesiredPortfolioActionCounts": row["controller"]["desiredPortfolioActionCounts"],
            "r2ExecutionChoiceCounts": row["controller"]["executionChoiceCounts"],
            "r2OptionTransitions": row["controller"]["optionTransitions"],
            "cycleInvariantViolationCount": row["cycleInvariantViolationCount"],
        }
        for row in rows
    ]
    report = {
        "version": "HFT_R2_CYCLE_PRESERVING_STATIC_PROGRAM_SMOKE_V1_REPORT",
        "researchOnly": True,
        "preregistration": "hft_r2_cycle_preserving_static_program_smoke_v1_preregistered.json",
        "marketId": MARKET_ID,
        "executionSemantics": rows[0]["executionSemantics"],
        "programs": summaries,
        "fixedOffset1RealizedPnl": next(row["realizedPnl"] for row in summaries if row["passiveMode"] == "offset1"),
        "waitRealizedPnl": next(row["realizedPnl"] for row in summaries if row["passiveMode"] == "wait"),
        "oracle": {
            "passiveMode": oracle["policy"]["passiveMode"],
            "realizedPnl": oracle_pnl,
            "worstCaseFloor": oracle["actualExecution"]["finalPortfolio"]["worst_case_floor"],
            "finalAbsTrackingError": oracle["actualExecution"]["finalAbsTrackingError"],
        },
        "allProgramsCycleValid": all_programs_cycle_valid,
        "decision": decision,
        "interpretation": "This is a one-market action-family ceiling, not OOS policy evidence. Static offsets are expanded only if the constrained oracle is positive without violating the R2 cycle.",
        "next": (
            "Run the locked three-market static-program pilot; do not select an offset from this market."
            if decision == "KEEP_FOR_THREE_MARKET_PILOT"
            else "Do not tune more offsets. Model option duration, repair responsibility and route completion within the R2-authorized objective."
        ),
    }
    output = OUT / "hft_r2_cycle_preserving_static_program_smoke_v1_report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(output), "oracle": report["oracle"], "decision": decision}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
