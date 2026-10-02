from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_safe_floor_contingent_pair_smoke_v1 import EPS, run_offset


OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT_DIR / "hft_safe_floor_semimarkov_phase20_v1_preregistered.json"
REPORT = OUT_DIR / "hft_safe_floor_semimarkov_phase20_v1_report.json"
MARKETS = (
    1575819, 1576119, 1576324, 1576518, 1576765,
    1576991, 1577181, 1577392, 1577751, 1577937,
    1578351, 1578546, 1578732, 1578921, 1579116,
    1579313, 1579674, 1579874, 1580153, 1580346,
)


def phase_by_name(result: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((row for row in result["phaseObservations"] if row["phase"] == name), None)


def lifecycle_has(result: dict[str, Any], action: str) -> bool:
    return any(row.get("action") == action for row in result["lifecycle"])


def make_row(index: int, result: dict[str, Any]) -> dict[str, Any]:
    actual = result["actualExecution"]
    first = phase_by_name(result, "FIRST_MAKER_FILL_CONFIRMED")
    cancel_ack = phase_by_name(result, "MAKER_CANCEL_ACKS_CONFIRMED")
    taker_fill_phase = phase_by_name(result, "TAKER_FILL_CONFIRMED")
    maker_fills = [fill for fill in result["fills"] if fill["role"] == "MAKER"]
    taker_fills = [fill for fill in result["fills"] if fill["role"] == "TAKER"]
    first_completion = first["completionEconomics"] if first else None
    maker_up = float(actual["makerUp"])
    maker_down = float(actual["makerDown"])
    floor = float(actual["worstCaseFloor"])
    return {
        "marketId": int(result["marketId"]),
        "chronologicalIndex": index,
        "fixedAction": "CONTINGENT_PAIR_OFFSET1",
        "entryState": result["strictPastEntryState"],
        "firstMakerFill": {
            "observed": first is not None,
            "side": maker_fills[0]["side"] if maker_fills else None,
            "exchangeAtMs": int(maker_fills[0]["atMs"]) if maker_fills else None,
            "confirmedAtMs": int(first["observedAtMs"]) if first else None,
            "confirmedAgeMs": int(first["ageFromPairSubmitMs"]) if first else None,
        },
        "firstFillPhaseState": first,
        "firstFillCompletionEconomics": first_completion,
        "cancelAckPhaseState": cancel_ack,
        "takerFillPhaseState": taker_fill_phase,
        "labelsOfflineOnly": {
            "passivePairCompleted": int(maker_up >= 18.0 - EPS and maker_down >= 18.0 - EPS),
            "takerSubmitted": int(lifecycle_has(result, "SAFE_TAKER_SUBMITTED")),
            "takerActuallyFilled": int(bool(taker_fills)),
            "terminalTail": int(floor < -EPS),
            "terminalPositiveFloor": int(floor > EPS),
            "terminalWorstCaseFloor": floor,
            "terminalState": actual["terminalState"],
        },
        "actualExecution": actual,
        "fills": result["fills"],
        "lifecycle": result["lifecycle"],
        "cycleInvariantViolations": result["cycleInvariantViolations"],
    }


def main() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if list(MARKETS) != prereg["cohort"]["markets"]:
        raise RuntimeError("phase20 market list differs from preregistration")

    rows: list[dict[str, Any]] = []
    for index, market_id in enumerate(MARKETS):
        row = make_row(index, run_offset(market_id, 1))
        rows.append(row)
        labels = row["labelsOfflineOnly"]
        completion = row["firstFillCompletionEconomics"] or {}
        print(
            json.dumps(
                {
                    "progress": f"{index + 1}/{len(MARKETS)}",
                    "marketId": market_id,
                    "firstFillAgeMs": row["firstMakerFill"]["confirmedAgeMs"],
                    "firstFillTakerFeasible": completion.get("safeTakerFeasible"),
                    "passivePairCompleted": labels["passivePairCompleted"],
                    "takerSubmitted": labels["takerSubmitted"],
                    "takerActuallyFilled": labels["takerActuallyFilled"],
                    "floor": labels["terminalWorstCaseFloor"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    maker_fill_rows = [row for row in rows if row["firstMakerFill"]["observed"]]
    first_economics = [row["firstFillCompletionEconomics"] for row in maker_fill_rows]
    taker_submitted_rows = [row for row in rows if row["labelsOfflineOnly"]["takerSubmitted"]]
    taker_filled_rows = [row for row in rows if row["labelsOfflineOnly"]["takerActuallyFilled"]]
    tail_rows = [row for row in rows if row["labelsOfflineOnly"]["terminalTail"]]
    positive_rows = [row for row in rows if row["labelsOfflineOnly"]["terminalPositiveFloor"]]
    violations = sum(len(row["cycleInvariantViolations"]) for row in rows)
    phase_counts = Counter(
        phase["phase"]
        for row in rows
        for phase in (row["firstFillPhaseState"], row["cancelAckPhaseState"], row["takerFillPhaseState"])
        if phase is not None
    )
    feasible_rows = [row for row in maker_fill_rows if bool(row["firstFillCompletionEconomics"]["safeTakerFeasible"])]
    infeasible_rows = [row for row in maker_fill_rows if not bool(row["firstFillCompletionEconomics"]["safeTakerFeasible"])]
    reason_counts = Counter(str(item["reason"]) for item in first_economics)
    tail_mechanisms = {
        "firstFillCompletionEconomicsNonpositive": sum(
            not bool(row["firstFillCompletionEconomics"]["safeTakerFeasible"]) for row in tail_rows
        ),
        "positiveFloorTakerSubmittedButNoFill": sum(
            bool(row["firstFillCompletionEconomics"]["safeTakerFeasible"])
            and bool(row["labelsOfflineOnly"]["takerSubmitted"])
            and not bool(row["labelsOfflineOnly"]["takerActuallyFilled"])
            for row in tail_rows
        ),
    }
    gate_pass = bool(
        len(rows) == 20
        and len(maker_fill_rows) == 20
        and len(taker_submitted_rows) >= 4
        and all(row["cancelAckPhaseState"] is not None for row in taker_submitted_rows)
        and len(feasible_rows) >= 4
        and len(infeasible_rows) >= 4
        and len(taker_filled_rows) >= 4
        and len(tail_rows) >= 4
        and violations == 0
    )
    report = {
        "reportVersion": "HFT_SAFE_FLOOR_SEMIMARKOV_PHASE20_V1",
        "researchOnly": True,
        "preregisteredContract": PREREG.name,
        "cohort": {
            "name": prereg["cohort"]["name"],
            "markets": list(MARKETS),
            "openedDevelopment": True,
            "officialHftForwardUsed": False,
        },
        "fixedAction": "CONTINGENT_PAIR_OFFSET1",
        "formalWaitValue": 0.0,
        "executionSemantics": prereg["executionSemantics"],
        "runtimeInputBoundary": prereg["causality"],
        "support": {
            "markets": len(rows),
            "makerFillPhaseRows": len(maker_fill_rows),
            "phaseCounts": dict(phase_counts),
            "firstFillSafeTakerFeasible": len(feasible_rows),
            "firstFillSafeTakerInfeasible": len(infeasible_rows),
            "firstFillCompletionReasonCounts": dict(reason_counts),
            "passivePairCompleted": sum(row["labelsOfflineOnly"]["passivePairCompleted"] for row in rows),
            "takerSubmitted": len(taker_submitted_rows),
            "takerActuallyFilled": len(taker_filled_rows),
            "terminalPositiveFloor": len(positive_rows),
            "terminalTail": len(tail_rows),
            "cycleInvariantViolations": violations,
        },
        "tailMechanisms": tail_mechanisms,
        "fixedActionRealizedExecutionValue": sum(row["labelsOfflineOnly"]["terminalWorstCaseFloor"] for row in rows),
        "formalWaitRealizedValue": 0.0,
        "postRevealOracleWaitOrActCeiling": sum(max(0.0, row["labelsOfflineOnly"]["terminalWorstCaseFloor"]) for row in rows),
        "oracleActRate": len(positive_rows) / len(rows),
        "learnedPolicyRealizedValue": None,
        "rows": rows,
        "decision": "KEEP_PHASE_DATASET_FOR_CHRONOLOGICAL_EXPANSION" if gate_pass else "NEED_MORE_DATA_OR_REJECT_PHASE_DATASET",
        "decisionReason": (
            "The preregistered small-sample support gate passed: all competing lifecycle paths and both completion failure mechanisms are represented without changing execution outcomes."
            if gate_pass
            else "At least one preregistered lifecycle-path support or invariance condition failed."
        ),
        "nextExperiment": (
            "Replay the already revealed pre-official 130-market chronology into the frozen phase schema, then measure chronological regime drift and only train a structured multi-head model if every rare phase retains support."
            if gate_pass
            else "Inspect the failed support condition before any larger replay or model training."
        ),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("support", "tailMechanisms", "fixedActionRealizedExecutionValue", "postRevealOracleWaitOrActCeiling", "oracleActRate", "decision", "nextExperiment")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
