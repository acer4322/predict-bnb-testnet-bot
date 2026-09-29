from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_hft_safe_floor_semimarkov_phase20_v1 import MARKETS
from tools.hft_safe_floor_contingent_pair_smoke_v1 import EPS, run_offset


OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT_DIR / "hft_first_imbalance_taker_expansion20_v1_preregistered.json"
BASELINE = OUT_DIR / "hft_safe_floor_semimarkov_transition20_v2_report.json"
REPORT = OUT_DIR / "hft_first_imbalance_taker_expansion20_v1_report.json"


def main() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    baseline_report = json.loads(BASELINE.read_text(encoding="utf-8"))
    if list(MARKETS) != prereg["cohort"]["markets"]:
        raise RuntimeError("first-imbalance Taker market list differs from preregistration")
    baseline = {
        int(row["marketId"]): float(row["transitionLabelsOfflineOnly"]["terminalWorstCaseFloor"])
        for row in baseline_report["rows"]
    }

    rows: list[dict[str, Any]] = []
    for index, market_id in enumerate(MARKETS):
        result = run_offset(market_id, 1, completion_policy="FIRST_IMBALANCE_TAKER")
        actual = result["actualExecution"]
        new_floor = float(actual["worstCaseFloor"])
        safe_floor = baseline[market_id]
        row = {
            "marketId": market_id,
            "chronologicalIndex": index,
            "safeOnlyFloor": safe_floor,
            "firstImbalanceTakerFloor": new_floor,
            "deltaVsSafeOnly": new_floor - safe_floor,
            "safeOnlyTail": int(safe_floor < -EPS),
            "newTail": int(new_floor < -EPS),
            "newActualExecution": actual,
            "newFills": result["fills"],
            "newLifecycle": result["lifecycle"],
            "newCycleInvariantViolations": result["cycleInvariantViolations"],
        }
        rows.append(row)
        print(json.dumps({"progress": f"{index + 1}/{len(MARKETS)}", **{key: row[key] for key in ("marketId", "safeOnlyFloor", "firstImbalanceTakerFloor", "deltaVsSafeOnly", "safeOnlyTail", "newTail")}}, ensure_ascii=False), flush=True)

    safe_value = sum(row["safeOnlyFloor"] for row in rows)
    new_value = sum(row["firstImbalanceTakerFloor"] for row in rows)
    old_oracle = sum(max(0.0, row["safeOnlyFloor"]) for row in rows)
    expanded_oracle = sum(max(0.0, row["safeOnlyFloor"], row["firstImbalanceTakerFloor"]) for row in rows)
    known_tails = [row for row in rows if row["safeOnlyTail"]]
    improved_known_tails = sum(row["firstImbalanceTakerFloor"] > row["safeOnlyFloor"] + EPS for row in known_tails)
    violations = sum(len(row["newCycleInvariantViolations"]) for row in rows)
    prior_violation_counts = {
        int(row["marketId"]): len(row["cycleInvariantViolations"])
        for row in baseline_report["rows"]
    }
    new_violation_markets = [
        row["marketId"]
        for row in rows
        if len(row["newCycleInvariantViolations"]) > prior_violation_counts[row["marketId"]]
    ]
    fixed_keep = new_value > EPS and not new_violation_markets
    contextual_keep = (
        not fixed_keep
        and expanded_oracle - old_oracle >= 5.0 - EPS
        and improved_known_tails >= 4
        and not new_violation_markets
    )
    decision = (
        "KEEP_FIRST_IMBALANCE_TAKER_FIXED_PROGRAM"
        if fixed_keep
        else "KEEP_FIRST_IMBALANCE_TAKER_ONLY_AS_CONTEXTUAL_ACTION"
        if contextual_keep
        else "REJECT_FIRST_IMBALANCE_TAKER_ACTION"
    )
    report = {
        "reportVersion": "HFT_FIRST_IMBALANCE_TAKER_EXPANSION20_V1",
        "researchOnly": True,
        "preregisteredContract": PREREG.name,
        "cohort": prereg["cohort"],
        "actionSpace": prereg["actionSpace"],
        "executionSemantics": prereg["executionSemantics"],
        "runtimeForbidden": prereg["runtimeForbidden"],
        "waitActRate": {"newFixedProgramActRate": 1.0, "formalWaitValue": 0.0},
        "comparators": {
            "safeOnlyFixedValue": safe_value,
            "firstImbalanceTakerFixedValue": new_value,
            "deltaFixedValue": new_value - safe_value,
            "safeOnlyTails": len(known_tails),
            "newActionTails": sum(row["newTail"] for row in rows),
            "knownSafeOnlyTailsImprovedByNewAction": improved_known_tails,
            "newHistoricalViolationCount": violations,
            "newViolationMarketsVsBaseline": new_violation_markets,
        },
        "oracleValueCeiling": {
            "waitOrSafeOnly": old_oracle,
            "waitOrBestOfSafeOnlyAndFirstImbalanceTaker": expanded_oracle,
            "incrementalCeiling": expanded_oracle - old_oracle,
            "expandedOracleActRate": sum(max(row["safeOnlyFloor"], row["firstImbalanceTakerFloor"]) > EPS for row in rows) / len(rows),
            "expandedOracleNewActionRate": sum(row["firstImbalanceTakerFloor"] > max(0.0, row["safeOnlyFloor"]) + EPS for row in rows) / len(rows),
        },
        "learnedPolicyRealizedValue": None,
        "rows": rows,
        "decision": decision,
        "decisionReason": (
            "The complete maker-to-Taker switch has positive fixed realized execution value with no new lifecycle violations."
            if fixed_keep
            else "The action is not safe as a fixed program, but it adds preregistered material counterfactual value on known tail states and remains eligible only for a causal contextual selector."
            if contextual_keep
            else "Immediate maker-to-Taker switching neither achieved positive fixed value nor added enough preregistered contextual oracle ceiling; no local timing or price tuning is allowed."
        ),
        "nextExperiment": (
            "Freeze this complete lifecycle program and run the next chronological development block before any learned selector."
            if fixed_keep
            else "Build a causal multi-state selector only if the action-space ceiling passed; otherwise return to time-varying competing-risk state collection."
            if contextual_keep
            else "Reject this action and collect time-varying at-risk states for a different semi-Markov intervention."
        ),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("comparators", "oracleValueCeiling", "decision", "decisionReason", "nextExperiment")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
