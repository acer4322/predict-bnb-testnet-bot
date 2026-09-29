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
PROGRAMS = [
    ("WAIT_ALL", "wait", "all"),
    ("EXECUTE_MAINTAIN_ONLY", "offset0", "maintain_only"),
    ("EXECUTE_REPAIR_ONLY", "offset0", "repair_only"),
    ("EXECUTE_ALL", "offset0", "all"),
]


def pnl(row: dict[str, Any]) -> float:
    value = row["actualExecution"].get("realizedPnl")
    return -math.inf if value is None or not math.isfinite(float(value)) else float(value)


def main() -> None:
    rows = []
    for name, passive_mode, option_scope in PROGRAMS:
        row = run_smoke(MARKET_ID, passive_mode=passive_mode, option_scope=option_scope)
        row["programName"] = name
        rows.append(row)
        print(
            json.dumps(
                {
                    "program": name,
                    "pnl": pnl(row),
                    "makerFilledShares": row["actualExecution"]["makerFilledShares"],
                    "takerFilledShares": row["actualExecution"]["takerFilledShares"],
                    "finalAbsTrackingError": row["actualExecution"]["finalAbsTrackingError"],
                    "worstCaseFloor": row["actualExecution"]["finalPortfolio"]["worst_case_floor"],
                    "r2ModeCounts": row["controller"]["desiredPortfolioActionCounts"],
                    "cycleViolations": row["cycleInvariantViolationCount"],
                    "runtimeSeconds": row["runtimeSeconds"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    oracle = max(rows, key=lambda row: (pnl(row), -next(i for i, item in enumerate(PROGRAMS) if item[0] == row["programName"])))
    positive_act = [
        row
        for row in rows
        if row["programName"] != "WAIT_ALL"
        and pnl(row) > 0.0
        and row["actualExecution"]["makerFilledShares"] + row["actualExecution"]["takerFilledShares"] > 0.0
        and row["cycleInvariantViolationCount"] == 0
    ]
    decision = "KEEP_OPTION_SCOPE_INTERACTION" if positive_act else "REJECT_STATIC_OPTION_SCOPE"
    summaries = [
        {
            "program": row["programName"],
            "passiveMode": row["policy"]["passiveMode"],
            "optionScope": row["policy"]["optionScope"],
            "realizedPnl": pnl(row),
            "worstCaseFloor": row["actualExecution"]["finalPortfolio"]["worst_case_floor"],
            "makerFilledShares": row["actualExecution"]["makerFilledShares"],
            "takerFilledShares": row["actualExecution"]["takerFilledShares"],
            "combinedFinalAbsNet": row["actualExecution"]["combinedFinalAbsNet"],
            "finalAbsTrackingError": row["actualExecution"]["finalAbsTrackingError"],
            "r2DesiredPortfolioActionCounts": row["controller"]["desiredPortfolioActionCounts"],
            "r2ExecutionChoiceCounts": row["controller"]["executionChoiceCounts"],
            "optionTransitions": row["controller"]["optionTransitions"],
            "makerIntents": row["r2ObjectiveExecution"]["makerIntents"],
            "targetIncrementShares": row["r2ObjectiveExecution"]["targetIncrementShares"],
            "cycleInvariantViolationCount": row["cycleInvariantViolationCount"],
            "runtimeSeconds": row["runtimeSeconds"],
        }
        for row in rows
    ]
    report = {
        "version": "HFT_R2_CYCLE_OPTION_SCOPE_ABLATION_V1_REPORT",
        "researchOnly": True,
        "preregistration": "hft_r2_cycle_option_scope_ablation_v1_preregistered.json",
        "marketId": MARKET_ID,
        "executionSemantics": rows[0]["executionSemantics"],
        "programs": summaries,
        "oracle": {"program": oracle["programName"], "realizedPnl": pnl(oracle)},
        "waitActRate": {"waitPrograms": int(oracle["programName"] == "WAIT_ALL"), "actPrograms": int(oracle["programName"] != "WAIT_ALL")},
        "decision": decision,
        "next": (
            "Preregister a small chronological option-conditioned trajectory dataset; learn value-to-go with WAIT while keeping Frozen R2 mode authority."
            if decision == "KEEP_OPTION_SCOPE_INTERACTION"
            else "Do not tune static scopes. The next unit must be multi-step option termination/value-to-go under actual fill and ownership state."
        ),
    }
    output = OUT / "hft_r2_cycle_option_scope_ablation_v1_report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(output), "oracle": report["oracle"], "decision": decision}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
