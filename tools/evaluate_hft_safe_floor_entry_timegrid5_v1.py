from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_safe_floor_contingent_pair_smoke_v1 import EPS, run_offset
from tools.hftbacktest_r2_execution_school_v0 import load_public_snapshots


OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT_DIR / "hft_safe_floor_entry_timegrid5_v1_preregistered.json"
REPORT = OUT_DIR / "hft_safe_floor_entry_timegrid5_v1_report.json"


def checkpoints(market_id: int, targets: list[int]) -> list[dict[str, Any]]:
    eligible = [
        dict(row)
        for row in load_public_snapshots(market_id)
        if row.get("secondsLeft") is not None
        and row.get("sampledAtMs") is not None
        and row.get("predictUpBid") is not None
        and row.get("predictDownBid") is not None
    ]
    if not eligible:
        raise RuntimeError(f"no eligible strict-past snapshots for {market_id}")
    chosen: list[dict[str, Any]] = []
    seen: set[int] = set()
    for target in targets:
        available = [row for row in eligible if int(row["sampledAtMs"]) not in seen]
        if not available:
            raise RuntimeError(f"not enough unique snapshots for {market_id}")
        row = min(available, key=lambda item: (abs(float(item["secondsLeft"]) - target), int(item["sampledAtMs"])))
        row["targetSecondsLeft"] = target
        chosen.append(row)
        seen.add(int(row["sampledAtMs"]))
    return chosen


def main() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    markets = [int(value) for value in prereg["cohort"]["markets"]]
    targets = [int(value) for value in prereg["actionSpace"]["targetSecondsLeft"]]
    baseline = {
        market_id: float(floor)
        for market_id, floor in zip(markets, prereg["cohort"]["knownFixed240SafeOnlyFloors"], strict=True)
    }
    rows: list[dict[str, Any]] = []
    total = len(markets) * len(targets)
    progress = 0
    for market_id in markets:
        for checkpoint in checkpoints(market_id, targets):
            result = run_offset(market_id, 1, checkpoint_override=checkpoint, winner_audit=False)
            actual = result["actualExecution"]
            progress += 1
            row = {
                "marketId": market_id,
                "targetSecondsLeft": int(checkpoint["targetSecondsLeft"]),
                "actualSecondsLeft": float(checkpoint["secondsLeft"]),
                "checkpointMs": int(checkpoint["sampledAtMs"]),
                "strictPastEntryState": result["strictPastEntryState"],
                "terminalWorstCaseFloor": float(actual["worstCaseFloor"]),
                "terminalState": actual["terminalState"],
                "makerFilledShares": float(actual["makerUp"] + actual["makerDown"]),
                "takerFilledShares": float(actual["takerUp"] + actual["takerDown"]),
                "cycleInvariantViolations": result["cycleInvariantViolations"],
                "fills": result["fills"],
                "lifecycle": result["lifecycle"],
            }
            rows.append(row)
            print(json.dumps({"progress": f"{progress}/{total}", **{key: row[key] for key in ("marketId", "targetSecondsLeft", "actualSecondsLeft", "terminalWorstCaseFloor", "terminalState")}}, ensure_ascii=False), flush=True)

    by_market: list[dict[str, Any]] = []
    new_violations: list[dict[str, Any]] = []
    for market_id in markets:
        selected = [row for row in rows if row["marketId"] == market_id]
        best = max(selected, key=lambda row: row["terminalWorstCaseFloor"])
        oracle_floor = max(0.0, float(best["terminalWorstCaseFloor"]))
        oracle_action = "WAIT" if oracle_floor <= EPS else f"ACT_TARGET_{best['targetSecondsLeft']}S"
        by_market.append(
            {
                "marketId": market_id,
                "fixed240Floor": baseline[market_id],
                "fixed240WaitOrActValue": max(0.0, baseline[market_id]),
                "timegridOracleAction": oracle_action,
                "timegridOracleTargetSecondsLeft": None if oracle_action == "WAIT" else best["targetSecondsLeft"],
                "timegridOracleActualSecondsLeft": None if oracle_action == "WAIT" else best["actualSecondsLeft"],
                "timegridOracleValue": oracle_floor,
                "incrementalOracleValue": oracle_floor - max(0.0, baseline[market_id]),
                "positiveActionCount": sum(row["terminalWorstCaseFloor"] > EPS for row in selected),
                "negativeActionCount": sum(row["terminalWorstCaseFloor"] < -EPS for row in selected),
                "waitEquivalentActionCount": sum(abs(row["terminalWorstCaseFloor"]) <= EPS for row in selected),
            }
        )
        new_violations.extend(
            {"marketId": market_id, "targetSecondsLeft": row["targetSecondsLeft"], "violations": row["cycleInvariantViolations"]}
            for row in selected
            if row["cycleInvariantViolations"]
        )

    fixed_oracle = sum(row["fixed240WaitOrActValue"] for row in by_market)
    timegrid_oracle = sum(row["timegridOracleValue"] for row in by_market)
    positive_markets = sum(row["positiveActionCount"] > 0 for row in by_market)
    non240_choices = sum(
        row["timegridOracleTargetSecondsLeft"] is not None and row["timegridOracleTargetSecondsLeft"] != 240
        for row in by_market
    )
    keep = bool(
        timegrid_oracle - fixed_oracle >= 5.0 - EPS
        and positive_markets >= 4
        and non240_choices >= 2
        and not new_violations
    )
    report = {
        "reportVersion": "HFT_SAFE_FLOOR_ENTRY_TIMEGRID5_V1",
        "researchOnly": True,
        "preregisteredContract": PREREG.name,
        "cohort": prereg["cohort"],
        "actionSpace": prereg["actionSpace"],
        "executionSemantics": prereg["executionSemantics"],
        "runtimeForbidden": prereg["runtimeForbidden"],
        "waitActRate": {"formalWaitValue": 0.0, "timegridOracleActRate": sum(row["timegridOracleValue"] > EPS for row in by_market) / len(by_market)},
        "oracleValueCeiling": {
            "fixed240WaitOrAct": fixed_oracle,
            "waitOrBestTimegridAction": timegrid_oracle,
            "incrementalValue": timegrid_oracle - fixed_oracle,
            "marketsWithAnyPositiveAction": positive_markets,
            "non240OracleActChoices": non240_choices,
        },
        "learnedPolicyRealizedValue": None,
        "newLifecycleViolations": new_violations,
        "byMarket": by_market,
        "rows": rows,
        "decision": "KEEP_FULL_LIFECYCLE_ENTRY_TIMING_ACTION_SPACE" if keep else "REJECT_COARSE_FULL_LIFECYCLE_ENTRY_TIMEGRID",
        "decisionReason": (
            "Sparse entry timing materially expands HftBacktest action value beyond fixed 240s while preserving formal WAIT and lifecycle invariants."
            if keep
            else "The preregistered five-market action-value ceiling gate failed; do not tune this grid locally."
        ),
        "nextExperiment": (
            "Build a chronological multi-checkpoint counterfactual dataset with time-to-entry and execution-regime state, then rank WAIT versus the full protected-cycle action."
            if keep
            else "Return to time-varying competing-risk state collection or a different economic action family."
        ),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("waitActRate", "oracleValueCeiling", "byMarket", "newLifecycleViolations", "decision", "decisionReason", "nextExperiment")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
