from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from statistics import median
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_hft_safe_floor_semimarkov_transition20_v2 import compact_row
from tools.hft_safe_floor_contingent_pair_smoke_v1 import EPS, run_offset


OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT_DIR / "hft_safe_floor_semimarkov_phase130_v1_preregistered.json"
REPORT = OUT_DIR / "hft_safe_floor_semimarkov_phase130_v1_report.json"


def nested(data: dict[str, Any], path: str) -> Any:
    value: Any = data
    for key in path.split("."):
        value = value[key]
    return value


def load_blocks(prereg: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for spec in prereg["cohort"]["sourceBlocks"]:
        source = json.loads((OUT_DIR / spec["source"]).read_text(encoding="utf-8"))
        markets = [int(value) for value in nested(source, spec["jsonPath"])]
        if len(markets) != int(spec["count"]) or markets[0] != int(spec["firstMarket"]) or markets[-1] != int(spec["lastMarket"]):
            raise RuntimeError(f"source block drift: {spec['name']}")
        blocks.append({"name": spec["name"], "markets": markets})
    ordered = [market for block in blocks for market in block["markets"]]
    digest = hashlib.sha256(",".join(map(str, ordered)).encode("utf-8")).hexdigest()
    if len(ordered) != int(prereg["cohort"]["totalMarkets"]) or len(set(ordered)) != len(ordered):
        raise RuntimeError("phase130 market count or uniqueness mismatch")
    if digest != prereg["cohort"]["orderedMarketIdsSha256"]:
        raise RuntimeError("phase130 ordered market digest mismatch")
    if max(ordered) >= int(prereg["cohort"]["officialHftForwardCutoverMarket"]):
        raise RuntimeError("official HFT Forward boundary crossed")
    return blocks


def prior_terminal_floors() -> dict[int, float]:
    out: dict[int, float] = {}
    sources = (
        ("hft_safe_floor_contingent_pair_expansion20_v1_report.json", "full"),
        ("hft_safe_floor_entry_value_dataset60_v1.json", "flat"),
        ("hft_net_qr_safe_floor_blind5_v1_report.json", "counterfactual"),
        ("hft_net_qr_safe_floor_blind20_v1_report.json", "counterfactual"),
        ("hft_safe_floor_late_regime_blind25_v1_report.json", "full"),
    )
    for filename, kind in sources:
        data = json.loads((OUT_DIR / filename).read_text(encoding="utf-8"))
        for row in data["rows"]:
            market_id = int(row["marketId"])
            if kind == "flat":
                floor = float(row["terminalWorstCaseFloor"])
            elif kind == "counterfactual":
                floor = float(row["realActionCounterfactual"]["actualExecution"]["worstCaseFloor"])
            else:
                floor = float(row["actualExecution"]["worstCaseFloor"])
            out[market_id] = floor
    return out


def prior_cycle_violation_counts() -> dict[int, int]:
    out: dict[int, int] = {}
    sources = (
        ("hft_safe_floor_contingent_pair_expansion20_v1_report.json", "full"),
        ("hft_safe_floor_entry_value_dataset60_v1.json", "flat"),
        ("hft_net_qr_safe_floor_blind5_v1_report.json", "counterfactual"),
        ("hft_net_qr_safe_floor_blind20_v1_report.json", "counterfactual"),
        ("hft_safe_floor_late_regime_blind25_v1_report.json", "full"),
    )
    for filename, kind in sources:
        data = json.loads((OUT_DIR / filename).read_text(encoding="utf-8"))
        for row in data["rows"]:
            market_id = int(row["marketId"])
            source = row["realActionCounterfactual"] if kind == "counterfactual" else row
            out[market_id] = int(source.get("cycleInvariantViolationCount") or 0)
    return out


def quantiles(values: list[int]) -> dict[str, int | None]:
    if not values:
        return {"min": None, "p25": None, "median": None, "p75": None, "max": None}
    ordered = sorted(values)
    return {
        "min": ordered[0],
        "p25": ordered[int((len(ordered) - 1) * 0.25)],
        "median": int(median(ordered)),
        "p75": ordered[int((len(ordered) - 1) * 0.75)],
        "max": ordered[-1],
    }


def summarize(selected: list[dict[str, Any]]) -> dict[str, Any]:
    labels = [row["transitionLabelsOfflineOnly"] for row in selected]
    arrived = [row for row in selected if row["transitionLabelsOfflineOnly"]["completionOpportunityArrived"]]
    no_arrival = [row for row in selected if not row["transitionLabelsOfflineOnly"]["completionOpportunityArrived"]]
    ack = [row for row in selected if row["makerCancelAcksConfirmedPhase"] is not None]
    taker_submitted = [row for row in selected if row["transitionLabelsOfflineOnly"]["takerSubmitted"]]
    taker_filled = [row for row in selected if row["transitionLabelsOfflineOnly"]["takerActuallyFilled"]]
    tails = [row for row in selected if row["transitionLabelsOfflineOnly"]["terminalTail"]]
    arrival_times = [int(row["transitionLabelsOfflineOnly"]["timeFirstFillToOpportunityMs"]) for row in arrived]
    ack_positive = sum(
        bool(row["makerCancelAcksConfirmedPhase"]["completionEconomics"]["safeTakerFeasible"])
        for row in ack
    )
    ack_balanced = sum(
        row["makerCancelAcksConfirmedPhase"]["completionEconomics"]["reason"] == "BALANCED_NO_COMPLETION_NEEDED"
        for row in ack
    )
    return {
        "markets": len(selected),
        "fixedActionValue": sum(float(label["terminalWorstCaseFloor"]) for label in labels),
        "postRevealWaitOrActOracleCeiling": sum(max(0.0, float(label["terminalWorstCaseFloor"])) for label in labels),
        "positiveFloor": sum(float(label["terminalWorstCaseFloor"]) > EPS for label in labels),
        "zeroFloor": sum(abs(float(label["terminalWorstCaseFloor"])) <= EPS for label in labels),
        "tails": len(tails),
        "completionOpportunityArrived": len(arrived),
        "completionOpportunityArrivalRate": len(arrived) / len(selected) if selected else None,
        "completionOpportunityNeverArrived": len(no_arrival),
        "arrivalTimeFromFirstFillMs": quantiles(arrival_times),
        "passivePairCompleted": sum(label["passivePairCompleted"] for label in labels),
        "cancelAckPaths": len(ack),
        "cancelAckBalancedBeforeTaker": ack_balanced,
        "cancelAckPositiveFloorForTaker": ack_positive,
        "cancelAckOtherOrLostFeasibility": len(ack) - ack_balanced - ack_positive,
        "takerSubmitted": len(taker_submitted),
        "takerActuallyFilled": len(taker_filled),
        "takerSubmittedWithoutFill": sum(
            row["transitionLabelsOfflineOnly"]["takerSubmitted"] and not row["transitionLabelsOfflineOnly"]["takerActuallyFilled"]
            for row in selected
        ),
        "tailWithOpportunityArrival": sum(row["transitionLabelsOfflineOnly"]["completionOpportunityArrived"] for row in tails),
        "cycleInvariantViolations": sum(len(row["cycleInvariantViolations"]) for row in selected),
    }


def main() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    blocks = load_blocks(prereg)
    prior = prior_terminal_floors()
    prior_violations = prior_cycle_violation_counts()
    rows: list[dict[str, Any]] = []
    index = 0
    total = sum(len(block["markets"]) for block in blocks)
    for block in blocks:
        for market_id in block["markets"]:
            row = compact_row(index, run_offset(market_id, 1))
            row["chronologicalBlock"] = block["name"]
            rows.append(row)
            labels = row["transitionLabelsOfflineOnly"]
            print(
                json.dumps(
                    {
                        "progress": f"{index + 1}/{total}",
                        "block": block["name"],
                        "marketId": market_id,
                        "opportunity": labels["completionOpportunityArrived"],
                        "passive": labels["passivePairCompleted"],
                        "takerSubmit": labels["takerSubmitted"],
                        "takerFill": labels["takerActuallyFilled"],
                        "tail": labels["terminalTail"],
                        "floor": labels["terminalWorstCaseFloor"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            index += 1

    outcome_mismatches = [
        {
            "marketId": row["marketId"],
            "priorFloor": prior.get(row["marketId"]),
            "replayFloor": row["transitionLabelsOfflineOnly"]["terminalWorstCaseFloor"],
        }
        for row in rows
        if row["marketId"] not in prior
        or abs(float(row["transitionLabelsOfflineOnly"]["terminalWorstCaseFloor"]) - float(prior[row["marketId"]])) > EPS
    ]
    violation_mismatches = [
        {
            "marketId": row["marketId"],
            "priorViolationCount": prior_violations.get(row["marketId"]),
            "replayViolationCount": len(row["cycleInvariantViolations"]),
        }
        for row in rows
        if row["marketId"] not in prior_violations
        or len(row["cycleInvariantViolations"]) != int(prior_violations[row["marketId"]])
    ]
    by_block = {block["name"]: summarize([row for row in rows if row["chronologicalBlock"] == block["name"]]) for block in blocks}
    partitions = {
        "developmentTrainSupport80": summarize(rows[:80]),
        "developmentValidationSupport25": summarize(rows[80:105]),
        "lateRegimeDiagnostic25": summarize(rows[105:]),
    }
    aggregate = summarize(rows)
    mixed_blocks = sum(
        summary["completionOpportunityArrived"] > 0 and summary["completionOpportunityNeverArrived"] > 0
        for summary in by_block.values()
    )
    gate_pass = bool(
        len(rows) == 130
        and not outcome_mismatches
        and not violation_mismatches
        and aggregate["tails"] >= 15
        and aggregate["completionOpportunityNeverArrived"] >= 15
        and aggregate["takerActuallyFilled"] >= 15
        and aggregate["takerSubmittedWithoutFill"] >= 1
        and mixed_blocks >= 2
    )
    rare_taker_failure = aggregate["takerSubmittedWithoutFill"] < 5
    if gate_pass and rare_taker_failure:
        decision = "KEEP_PHASE_SCHEMA_NEED_MORE_TAKER_FAILURE_DATA"
    elif gate_pass:
        decision = "KEEP_PHASE_DATASET_FOR_STRUCTURED_MODEL"
    else:
        decision = "REJECT_OR_REVISE_PHASE130_DATASET"
    report = {
        "reportVersion": "HFT_SAFE_FLOOR_SEMIMARKOV_PHASE130_V1",
        "researchOnly": True,
        "preregisteredContract": PREREG.name,
        "orderedMarketIdsSha256": prereg["cohort"]["orderedMarketIdsSha256"],
        "cohort": prereg["cohort"],
        "chronologicalPartitions": prereg["chronologicalPartitions"],
        "fixedAction": prereg["fixedAction"],
        "formalWaitAction": True,
        "formalWaitValue": 0.0,
        "executionSemantics": prereg["executionSemantics"],
        "runtimeForbidden": prereg["runtimeForbidden"],
        "aggregate": aggregate,
        "byChronologicalBlock": by_block,
        "byChronologicalPartition": partitions,
        "regimeDiagnostics": {
            "blocksWithBothOpportunityArrivalAndNonArrival": mixed_blocks,
            "opportunityArrivalRateRange": [
                min(summary["completionOpportunityArrivalRate"] for summary in by_block.values()),
                max(summary["completionOpportunityArrivalRate"] for summary in by_block.values()),
            ],
            "outcomeMismatchesAgainstPriorReports": outcome_mismatches,
            "cycleViolationMismatchesAgainstPriorReports": violation_mismatches,
        },
        "oracleValueCeiling": {
            "postRevealWaitOrAct": aggregate["postRevealWaitOrActOracleCeiling"],
            "oracleActRate": aggregate["positiveFloor"] / aggregate["markets"],
        },
        "learnedPolicyRealizedValue": None,
        "rows": rows,
        "decision": decision,
        "decisionReason": (
            "The frozen phase schema passed all aggregate and chronology support gates, but positive-floor Taker submission failures remain too sparse for an independently learned fill-failure head."
            if gate_pass and rare_taker_failure
            else "The frozen phase schema passed the preregistered support and invariance gates."
            if gate_pass
            else "At least one preregistered support, chronology, or outcome-invariance gate failed."
        ),
        "nextExperiment": (
            "Fit an option-arrival head and a passive-versus-Taker branch head using the first 80 markets only; use the next 25 for one fixed model-selection decision and the late25 only as a diagnostic. Keep Taker no-fill as a conservative hard risk term until more failures exist."
            if gate_pass
            else "Do not train; inspect the failed dataset gate."
        ),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("aggregate", "byChronologicalBlock", "byChronologicalPartition", "regimeDiagnostics", "oracleValueCeiling", "decision", "decisionReason", "nextExperiment")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
