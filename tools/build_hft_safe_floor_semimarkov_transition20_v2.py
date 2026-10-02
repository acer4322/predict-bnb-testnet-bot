from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_hft_safe_floor_semimarkov_phase20_v1 import MARKETS, lifecycle_has, phase_by_name
from tools.hft_safe_floor_contingent_pair_smoke_v1 import EPS, run_offset


OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT_DIR / "hft_safe_floor_semimarkov_transition20_v2_preregistered.json"
V1_REPORT = OUT_DIR / "hft_safe_floor_semimarkov_phase20_v1_report.json"
REPORT = OUT_DIR / "hft_safe_floor_semimarkov_transition20_v2_report.json"


def compact_row(index: int, result: dict[str, Any]) -> dict[str, Any]:
    first = phase_by_name(result, "FIRST_MAKER_FILL_CONFIRMED")
    entered = phase_by_name(result, "SAFE_TAKER_FEASIBILITY_ENTERED")
    ack = phase_by_name(result, "MAKER_CANCEL_ACKS_CONFIRMED")
    taker_fill = phase_by_name(result, "TAKER_FILL_CONFIRMED")
    actual = result["actualExecution"]
    passive = float(actual["makerUp"]) >= 18.0 - EPS and float(actual["makerDown"]) >= 18.0 - EPS
    taker_submitted = lifecycle_has(result, "SAFE_TAKER_SUBMITTED")
    taker_filled = float(actual["takerUp"] + actual["takerDown"]) > EPS
    floor = float(actual["worstCaseFloor"])
    return {
        "marketId": int(result["marketId"]),
        "chronologicalIndex": index,
        "entryState": result["strictPastEntryState"],
        "firstMakerFillPhase": first,
        "safeTakerFeasibilityEnteredPhase": entered,
        "makerCancelAcksConfirmedPhase": ack,
        "takerFillConfirmedPhase": taker_fill,
        "transitionLabelsOfflineOnly": {
            "completionOpportunityArrived": int(entered is not None),
            "timeSubmitToOpportunityMs": int(entered["ageFromPairSubmitMs"]) if entered else None,
            "timeFirstFillToOpportunityMs": int(entered["observedAtMs"] - first["observedAtMs"]) if entered and first else None,
            "passivePairCompleted": int(passive),
            "takerSubmitted": int(taker_submitted),
            "takerActuallyFilled": int(taker_filled),
            "terminalTail": int(floor < -EPS),
            "terminalWorstCaseFloor": floor,
        },
        "lifecycle": result["lifecycle"],
        "cycleInvariantViolations": result["cycleInvariantViolations"],
    }


def main() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    prior = json.loads(V1_REPORT.read_text(encoding="utf-8"))
    if list(MARKETS) != prereg["cohort"]["markets"]:
        raise RuntimeError("transition20 V2 market list differs from preregistration")
    prior_floors = {int(row["marketId"]): float(row["labelsOfflineOnly"]["terminalWorstCaseFloor"]) for row in prior["rows"]}

    rows: list[dict[str, Any]] = []
    for index, market_id in enumerate(MARKETS):
        row = compact_row(index, run_offset(market_id, 1))
        rows.append(row)
        labels = row["transitionLabelsOfflineOnly"]
        print(json.dumps({"progress": f"{index + 1}/{len(MARKETS)}", "marketId": market_id, **labels}, ensure_ascii=False), flush=True)

    opportunity = [row for row in rows if row["transitionLabelsOfflineOnly"]["completionOpportunityArrived"]]
    no_opportunity = [row for row in rows if not row["transitionLabelsOfflineOnly"]["completionOpportunityArrived"]]
    ack_rows = [row for row in rows if row["makerCancelAcksConfirmedPhase"] is not None]
    passive_rows = [row for row in opportunity if row["transitionLabelsOfflineOnly"]["passivePairCompleted"]]
    taker_fill_rows = [row for row in opportunity if row["transitionLabelsOfflineOnly"]["takerActuallyFilled"]]
    tails = [row for row in rows if row["transitionLabelsOfflineOnly"]["terminalTail"]]
    exact_outcome_match = all(abs(row["transitionLabelsOfflineOnly"]["terminalWorstCaseFloor"] - prior_floors[row["marketId"]]) <= EPS for row in rows)
    violations = sum(len(row["cycleInvariantViolations"]) for row in rows)
    all_ack_covered = all(
        row["safeTakerFeasibilityEnteredPhase"] is not None
        and int(row["safeTakerFeasibilityEnteredPhase"]["observedAtMs"]) <= int(row["makerCancelAcksConfirmedPhase"]["observedAtMs"])
        for row in ack_rows
    )
    false_entry_without_ack = [row["marketId"] for row in opportunity if row["makerCancelAcksConfirmedPhase"] is None]
    no_opportunity_non_tail = [row["marketId"] for row in no_opportunity if not row["transitionLabelsOfflineOnly"]["terminalTail"]]
    gate_pass = bool(
        len(ack_rows) == 15
        and len(opportunity) == 15
        and all_ack_covered
        and not false_entry_without_ack
        and not no_opportunity_non_tail
        and len(passive_rows) >= 4
        and len(taker_fill_rows) >= 4
        and exact_outcome_match
        and violations == 0
    )
    arrival_times = [row["transitionLabelsOfflineOnly"]["timeFirstFillToOpportunityMs"] for row in opportunity]
    report = {
        "reportVersion": "HFT_SAFE_FLOOR_SEMIMARKOV_TRANSITION20_V2",
        "researchOnly": True,
        "preregisteredContract": PREREG.name,
        "cohort": prereg["cohort"],
        "fixedAction": prereg["fixedAction"],
        "formalWaitValue": 0.0,
        "executionSemantics": prereg["executionSemantics"],
        "support": {
            "markets": len(rows),
            "completionOpportunityArrived": len(opportunity),
            "completionOpportunityNeverArrived": len(no_opportunity),
            "cancelAckPaths": len(ack_rows),
            "allCancelAckPathsCoveredByPriorOrSamePollOpportunity": all_ack_covered,
            "opportunityWithoutCancelAckMarkets": false_entry_without_ack,
            "noOpportunityButNonTailMarkets": no_opportunity_non_tail,
            "opportunityThenPassivePairCompletion": len(passive_rows),
            "opportunityThenTakerFill": len(taker_fill_rows),
            "terminalTails": len(tails),
            "tailWithOpportunityArrival": sum(row["transitionLabelsOfflineOnly"]["completionOpportunityArrived"] for row in tails),
            "timeFirstFillToOpportunityMs": {
                "min": min(arrival_times),
                "median": sorted(arrival_times)[len(arrival_times) // 2],
                "max": max(arrival_times),
            },
            "cycleInvariantViolations": violations,
            "terminalOutcomesExactlyMatchV1": exact_outcome_match,
        },
        "fixedActionRealizedExecutionValue": sum(row["transitionLabelsOfflineOnly"]["terminalWorstCaseFloor"] for row in rows),
        "postRevealOracleWaitOrActCeiling": sum(max(0.0, row["transitionLabelsOfflineOnly"]["terminalWorstCaseFloor"]) for row in rows),
        "oracleActRate": sum(row["transitionLabelsOfflineOnly"]["terminalWorstCaseFloor"] > EPS for row in rows) / len(rows),
        "learnedPolicyRealizedValue": None,
        "rows": rows,
        "decision": "KEEP_SEMIMARKOV_OPTION_ARRIVAL_SCHEMA" if gate_pass else "REJECT_OR_REVISE_SEMIMARKOV_OPTION_ARRIVAL_SCHEMA",
        "interpretation": (
            "The entry action creates an initially unsafe option. Positive lifecycle value depends on whether a fee-inclusive completion opportunity later arrives; after arrival this pilot has two successful branches, passive completion and Taker completion. The opportunity-arrival event is a target/state transition, not a runtime lookahead feature."
            if gate_pass
            else "The proposed first positive-feasibility transition did not causally cover the known completion paths."
        ),
        "nextExperiment": (
            "Expand only the frozen phase schema across the already revealed 130-market pre-official chronology. Measure regime-conditioned option-arrival, passive-completion, cancel-ACK survival, and Taker-fill support before fitting separate heads; reserve a later chronological block for realized-value evaluation."
            if gate_pass
            else "Stop expansion and inspect the failed causal-coverage condition."
        ),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("support", "fixedActionRealizedExecutionValue", "postRevealOracleWaitOrActCeiling", "oracleActRate", "decision", "interpretation", "nextExperiment")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
