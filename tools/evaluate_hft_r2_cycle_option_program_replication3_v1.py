from __future__ import annotations

import argparse
import json
import math
import sys
import time
import warnings
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke  # noqa: E402


BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
MARKETS = [1573447, 1573662, 1573848]
POLICIES = [
    {"policyId": "WAIT_ALL", "maintain": "wait", "repair": "wait"},
    {"policyId": "MAKE_ONLY_OFFSET0", "maintain": "offset0", "repair": "wait"},
    {"policyId": "FULL_CYCLE_OFFSET0", "maintain": "offset0", "repair": "offset0"},
]


def finite(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def is_act_transition(state: dict[str, Any], policy: dict[str, str]) -> bool:
    desired = str(((state.get("decision") or {}).get("desiredPortfolioAction") or "NONE"))
    if desired == "PASSIVE_MAINTAIN":
        return policy["maintain"] != "wait"
    if desired == "PASSIVE_REPAIR":
        return policy["repair"] != "wait"
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="hft_r2_cycle_option_program_replication3_v1_report.json")
    args = parser.parse_args()
    warnings.filterwarnings("ignore", message="X does not have valid feature names")
    rows: list[dict[str, Any]] = []
    for market_id in MARKETS:
        for policy in POLICIES:
            started = time.perf_counter()
            report = run_smoke(
                market_id,
                passive_mode="wait",
                passive_program={
                    "PASSIVE_MAINTAIN": policy["maintain"],
                    "PASSIVE_REPAIR": policy["repair"],
                },
            )
            actual = report["actualExecution"]
            transitions = report["strictPastOptionTransitions"]
            act_steps = sum(is_act_transition(state, policy) for state in transitions)
            lifecycle = report["lifecycleAudit"]
            row = {
                "marketId": market_id,
                **policy,
                "runtimeSeconds": time.perf_counter() - started,
                "realizedPnl": actual["realizedPnl"],
                "makerFilledShares": actual["makerFilledShares"],
                "takerFilledShares": actual["takerFilledShares"],
                "takerFeesUsdt": actual["takerFeesUsdt"],
                "finalAbsNet": actual["combinedFinalAbsNet"],
                "finalAbsTrackingError": actual["finalAbsTrackingError"],
                "targetErrorAreaShareSeconds": actual["targetErrorAreaShareSeconds"],
                "combinedExposureAreaShareSeconds": actual["combinedExposureAreaShareSeconds"],
                "finalWorstCaseFloor": finite(actual["finalPortfolio"].get("worst_case_floor")),
                "finalPairedCoverage": finite(actual["finalPortfolio"].get("combined_paired_coverage")),
                "optionSteps": len(transitions),
                "actSteps": act_steps,
                "actRate": act_steps / len(transitions) if transitions else 0.0,
                "cycleInvariantViolationCount": report["cycleInvariantViolationCount"],
                "unresolvedTakerReturns": lifecycle["unresolvedTakerReturns"],
                "cancelPendingAtDataEnd": lifecycle["cancelPendingAtDataEnd"],
                "actualInventoryEqualsHftFillLedger": report["semanticGate"]["actualInventoryEqualsHftFillLedger"],
            }
            rows.append(row)
            print(
                json.dumps(
                    {
                        "marketId": market_id,
                        "policy": policy["policyId"],
                        "pnl": row["realizedPnl"],
                        "floor": row["finalWorstCaseFloor"],
                        "makerFilled": row["makerFilledShares"],
                        "violations": row["cycleInvariantViolationCount"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    by_market = {}
    for market_id in MARKETS:
        market_rows = [row for row in rows if row["marketId"] == market_id]
        wait = next(row for row in market_rows if row["policyId"] == "WAIT_ALL")
        for row in market_rows:
            row["executionValueVsWait"] = finite(row["realizedPnl"]) - finite(wait["realizedPnl"])
        oracle = max(
            market_rows,
            key=lambda row: (
                finite(row["executionValueVsWait"]),
                -finite(row["finalAbsTrackingError"]),
                -finite(row["finalAbsNet"]),
            ),
        )
        by_market[str(market_id)] = {
            "waitPnl": wait["realizedPnl"],
            "oraclePolicy": oracle["policyId"],
            "oracleExecutionValueVsWait": max(0.0, finite(oracle["executionValueVsWait"])),
        }

    aggregates = {}
    for policy in POLICIES:
        policy_rows = [row for row in rows if row["policyId"] == policy["policyId"]]
        aggregates[policy["policyId"]] = {
            "totalRealizedPnl": sum(finite(row["realizedPnl"]) for row in policy_rows),
            "totalExecutionValueVsWait": sum(finite(row["executionValueVsWait"]) for row in policy_rows),
            "positiveExecutionValueMarkets": sum(finite(row["executionValueVsWait"]) > 1e-9 for row in policy_rows),
            "negativeExecutionValueMarkets": sum(finite(row["executionValueVsWait"]) < -1e-9 for row in policy_rows),
            "marketsWithActualFills": sum(
                finite(row["makerFilledShares"]) + finite(row["takerFilledShares"]) > 1e-9 for row in policy_rows
            ),
            "makerFilledShares": sum(finite(row["makerFilledShares"]) for row in policy_rows),
            "takerFilledShares": sum(finite(row["takerFilledShares"]) for row in policy_rows),
            "totalWorstCaseFloor": sum(finite(row["finalWorstCaseFloor"]) for row in policy_rows),
            "meanPairedCoverage": sum(finite(row["finalPairedCoverage"]) for row in policy_rows) / len(policy_rows),
            "waitSteps": sum(int(row["optionSteps"] - row["actSteps"]) for row in policy_rows),
            "actSteps": sum(int(row["actSteps"]) for row in policy_rows),
            "actRate": sum(int(row["actSteps"]) for row in policy_rows)
            / max(1, sum(int(row["optionSteps"]) for row in policy_rows)),
            "cycleInvariantViolations": sum(int(row["cycleInvariantViolationCount"]) for row in policy_rows),
            "unresolvedTakerReturns": sum(int(row["unresolvedTakerReturns"]) for row in policy_rows),
            "cancelPendingAtDataEnd": sum(int(row["cancelPendingAtDataEnd"]) for row in policy_rows),
        }

    candidate = aggregates["FULL_CYCLE_OFFSET0"]
    make_only = aggregates["MAKE_ONLY_OFFSET0"]
    wait = aggregates["WAIT_ALL"]
    all_valid = all(
        int(row["cycleInvariantViolationCount"]) == 0 and bool(row["actualInventoryEqualsHftFillLedger"])
        for row in rows
    )
    enough_coverage = candidate["marketsWithActualFills"] >= 2
    keep = (
        all_valid
        and enough_coverage
        and candidate["totalExecutionValueVsWait"] > 0.0
        and candidate["totalExecutionValueVsWait"] > make_only["totalExecutionValueVsWait"]
        and candidate["positiveExecutionValueMarkets"] >= 2
        and candidate["unresolvedTakerReturns"] <= wait["unresolvedTakerReturns"]
        and candidate["cancelPendingAtDataEnd"] <= wait["cancelPendingAtDataEnd"]
    )
    if not all_valid:
        decision = "BLOCKED"
    elif not enough_coverage:
        decision = "NEED_MORE_DATA"
    elif keep:
        decision = "KEEP_FOR_SMALL_DATASET_EXPANSION"
    else:
        decision = "REJECT_FIXED_PROGRAM"
    report = {
        "version": "HFT_R2_CYCLE_OPTION_PROGRAM_REPLICATION3_V1",
        "researchOnly": True,
        "preregistration": "hft_r2_cycle_option_program_replication3_v1_preregistered.json",
        "markets": MARKETS,
        "cohort": "NEXT_THREE_CHRONOLOGICAL_OPENED_DEVELOPMENT_MARKETS_AFTER_PILOT_NEW_TO_THIS_CONTRACT",
        "executionSemantics": {
            "engine": "HftBacktest + Predict Execution Tape V1",
            "queueModel": "risk",
            "entryLatencyMs": 1092,
            "responseLatencyMs": 273,
            "partialFills": True,
            "inventoryMutation": "CONFIRMED_HFTBACKTEST_FILLS_ONLY",
            "logicAuthority": "FROZEN_R2",
        },
        "rows": rows,
        "byMarket": by_market,
        "aggregates": aggregates,
        "summary": {
            "fixedCandidate": "FULL_CYCLE_OFFSET0",
            "fixedCandidateRealizedValueVsWait": candidate["totalExecutionValueVsWait"],
            "makeOnlyRealizedValueVsWait": make_only["totalExecutionValueVsWait"],
            "restrictedPolicyOracleCeilingVsWait": sum(
                finite(value["oracleExecutionValueVsWait"]) for value in by_market.values()
            ),
            "candidateWaitSteps": candidate["waitSteps"],
            "candidateActSteps": candidate["actSteps"],
            "candidateActRate": candidate["actRate"],
            "positiveMarkets": candidate["positiveExecutionValueMarkets"],
            "negativeMarkets": candidate["negativeExecutionValueMarkets"],
            "marketsWithActualFills": candidate["marketsWithActualFills"],
            "chronologicalUnseenUnderThisContract": True,
            "officialOrSealedUsed": False,
            "decision": decision,
        },
        "next": (
            "If kept, build a separately preregistered small chronological multi-market Minari dataset and only then add conservative offline-RL baselines. "
            "Do not tune offsets or open official/sealed cohorts."
        ),
    }
    output = BASE / args.output
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(output), **report["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
