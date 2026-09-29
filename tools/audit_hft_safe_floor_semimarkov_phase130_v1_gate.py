from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_hft_safe_floor_semimarkov_phase130_v1 import prior_cycle_violation_counts


OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
SOURCE = OUT_DIR / "hft_safe_floor_semimarkov_phase130_v1_report.json"
REPORT = OUT_DIR / "hft_safe_floor_semimarkov_phase130_v1_gate_audit.json"


def main() -> None:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    aggregate = source["aggregate"]
    by_block = source["byChronologicalBlock"]
    prior = prior_cycle_violation_counts()
    violation_mismatches = [
        {
            "marketId": row["marketId"],
            "priorViolationCount": prior.get(int(row["marketId"])),
            "replayViolationCount": len(row["cycleInvariantViolations"]),
        }
        for row in source["rows"]
        if int(row["marketId"]) not in prior
        or len(row["cycleInvariantViolations"]) != int(prior[int(row["marketId"])])
    ]
    mixed_blocks = sum(
        block["completionOpportunityArrived"] > 0 and block["completionOpportunityNeverArrived"] > 0
        for block in by_block.values()
    )
    gates = {
        "markets130": aggregate["markets"] == 130,
        "terminalFloorsMatchPriorReports": not source["regimeDiagnostics"]["outcomeMismatchesAgainstPriorReports"],
        "noNewCycleViolation": not violation_mismatches,
        "atLeast15Tails": aggregate["tails"] >= 15,
        "atLeast15OpportunityNonArrivals": aggregate["completionOpportunityNeverArrived"] >= 15,
        "atLeast15TakerFills": aggregate["takerActuallyFilled"] >= 15,
        "atLeastOneTakerNoFill": aggregate["takerSubmittedWithoutFill"] >= 1,
        "atLeastTwoMixedChronologicalBlocks": mixed_blocks >= 2,
    }
    passed = all(gates.values())
    rare_taker_failure = aggregate["takerSubmittedWithoutFill"] < 5
    decision = (
        "KEEP_PHASE_SCHEMA_NEED_MORE_TAKER_FAILURE_DATA"
        if passed and rare_taker_failure
        else "KEEP_PHASE_DATASET_FOR_STRUCTURED_MODEL"
        if passed
        else "REJECT_OR_REVISE_PHASE130_DATASET"
    )
    report = {
        "reportVersion": "HFT_SAFE_FLOOR_SEMIMARKOV_PHASE130_V1_GATE_AUDIT",
        "researchOnly": True,
        "sourceReport": SOURCE.name,
        "whyAuditExists": "The source evaluator implemented the preregistered 'zero new lifecycle violation' condition as 'zero total historical violations'. This audit compares each replay count with its prior report without rerunning or changing any HftBacktest result.",
        "sourceReportedHistoricalCycleViolations": aggregate["cycleInvariantViolations"],
        "historicalViolationMarkets": [
            {"marketId": row["marketId"], "violations": row["cycleInvariantViolations"]}
            for row in source["rows"]
            if row["cycleInvariantViolations"]
        ],
        "cycleViolationMismatchesAgainstPriorReports": violation_mismatches,
        "preregisteredGates": gates,
        "allPreregisteredGatesPass": passed,
        "decision": decision,
        "decisionReason": (
            "All dataset and chronology gates pass, but only two positive-floor Taker submissions failed to fill, so a separate learned Taker-failure head is under-supported."
            if passed and rare_taker_failure
            else "All preregistered dataset gates pass."
            if passed
            else "At least one preregistered dataset gate genuinely fails."
        ),
        "nextExperiment": (
            "Use the first 80 markets for option-arrival and completion-branch heads, the next 25 for one fixed selection, and late25 as post-reveal diagnostic only. Keep Taker failure as a hard conservative state until more failures arrive."
            if passed
            else "Do not train a model from this dataset."
        ),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
