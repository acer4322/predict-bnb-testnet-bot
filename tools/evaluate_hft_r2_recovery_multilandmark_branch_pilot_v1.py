from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
import warnings
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_r2_cycle_option_program_dataset_v1 import (  # noqa: E402
    OBSERVATION_FEATURES,
    observation_vector,
)
from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke  # noqa: E402
from tools.hft_r2_fault_conditioned_recovery_matrix_v1 import (  # noqa: E402
    ACTIONS,
    BASE,
    DeepFirstPassiveChild,
    state_hash,
)


VERSION = "HFT_R2_RECOVERY_MULTILANDMARK_BRANCH_PILOT_V1"
WAIT = "WAIT_PRESERVE"
EPS = 1e-8


def finite(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def program_grid() -> list[tuple[str, str]]:
    return list(itertools.product(ACTIONS, repeat=2))


def program_id(branch_ordinal: int, prefix: tuple[str, str]) -> str:
    return (
        f"WAIT_UNTIL_{branch_ordinal}__"
        f"{prefix[0]}__THEN__{prefix[1]}__THEN_WAIT"
    )


class LandmarkPrefixPolicy:
    def __init__(self, branch_ordinal: int, prefix: tuple[str, str]) -> None:
        self.branch_ordinal = int(branch_ordinal)
        self.prefix = prefix
        self.episode_index = -1
        self.current_action = WAIT
        self.option_decisions: list[dict[str, Any]] = []

    def __call__(self, payload: dict[str, Any]) -> str:
        features = payload["features"]
        first_checkpoint = finite(features.get("hasPriorObservation")) < 0.5
        if first_checkpoint:
            previous_action = (
                self.option_decisions[-1]["selectedAction"]
                if self.option_decisions
                else "NONE"
            )
            self.episode_index += 1
            relative = self.episode_index - self.branch_ordinal
            self.current_action = self.prefix[relative] if 0 <= relative < 2 else WAIT
            execution_state = payload.get("executionState")
            if not isinstance(execution_state, dict):
                raise RuntimeError("landmark callback is missing strict-past execution state")
            observation = observation_vector(execution_state)
            self.option_decisions.append(
                {
                    "optionIndex": self.episode_index,
                    "relativeBranchIndex": relative,
                    "atMs": int(payload["atMs"]),
                    "side": str(payload["side"]),
                    "pointStateHash": state_hash(
                        int(payload["atMs"]), str(payload["side"]), features
                    ),
                    "selectedAction": self.current_action,
                    "previousRecoveryAction": previous_action,
                    "observation": observation if relative in (0, 1) else None,
                    "observationLength": len(observation),
                }
            )
        elif self.current_action != WAIT:
            raise RuntimeError("non-WAIT landmark option unexpectedly reached another checkpoint")
        return str(ACTIONS[self.current_action])


def run_one(
    market_id: int,
    fault_id: str,
    branch_ordinal: int,
    prefix: tuple[str, str],
) -> dict[str, Any]:
    policy = LandmarkPrefixPolicy(branch_ordinal, prefix)
    price_fault = DeepFirstPassiveChild(fault_id == "DEEP_FIRST_PASSIVE_CHILD")
    started = time.perf_counter()
    report = run_smoke(
        market_id,
        passive_mode="wait",
        passive_program={"PASSIVE_MAINTAIN": "offset0", "PASSIVE_REPAIR": "offset0"},
        own_state_poll_ms=250,
        passive_price_policy=price_fault,
        lifecycle_action_override=policy,
        allowed_executor_taker_kinds={"FROZEN_R2", "PAIR_COMPLETION_REPLACE"},
    )
    branch = next(
        (
            row
            for row in policy.option_decisions
            if int(row["optionIndex"]) == int(branch_ordinal)
        ),
        None,
    )
    second = next(
        (
            row
            for row in policy.option_decisions
            if int(row["optionIndex"]) == int(branch_ordinal) + 1
        ),
        None,
    )
    counts = Counter(
        row["selectedAction"]
        for row in policy.option_decisions
        if int(row["optionIndex"]) >= int(branch_ordinal)
    )
    return {
        "marketId": int(market_id),
        "faultId": str(fault_id),
        "branchOrdinal": int(branch_ordinal),
        "programId": program_id(branch_ordinal, prefix),
        "prefix": list(prefix),
        "branchReached": branch is not None,
        "branchPointStateHash": branch["pointStateHash"] if branch else None,
        "branchObservation": branch["observation"] if branch else None,
        "secondBranchReached": second is not None,
        "secondPointStateHash": second["pointStateHash"] if second else None,
        "secondObservation": second["observation"] if second else None,
        "optionDecisions": policy.option_decisions,
        "postBranchActionCounts": {action: counts.get(action, 0) for action in ACTIONS},
        "terminalWorstCaseFloor": finite(
            report["actualExecution"]["finalPortfolio"].get("worst_case_floor")
        ),
        "cycleInvariantViolationCount": int(report["cycleInvariantViolationCount"]),
        "runtimeSeconds": time.perf_counter() - started,
    }


def summarize(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    expected_programs = len(program_grid())
    for market_id, fault_id, branch_ordinal in sorted(
        {
            (int(row["marketId"]), str(row["faultId"]), int(row["branchOrdinal"]))
            for row in rows
        }
    ):
        group = [
            row
            for row in rows
            if int(row["marketId"]) == market_id
            and str(row["faultId"]) == fault_id
            and int(row["branchOrdinal"]) == branch_ordinal
        ]
        if len(group) != expected_programs:
            continue
        wait_row = next(row for row in group if row["prefix"] == [WAIT, WAIT])
        fixed_rows = [row for row in group if row["prefix"][1] == WAIT]
        fixed_best = max(
            fixed_rows,
            key=lambda row: (
                row["terminalWorstCaseFloor"],
                row["prefix"][0] == WAIT,
            ),
        )
        best = max(
            group,
            key=lambda row: (
                row["terminalWorstCaseFloor"],
                -sum(row["postBranchActionCounts"][a] for a in ACTIONS if a != WAIT),
                row["prefix"] == [WAIT, WAIT],
            ),
        )
        hashes = {
            row["branchPointStateHash"] for row in group if row["branchReached"]
        }
        uses_switch = bool(
            best["secondBranchReached"] and best["prefix"][0] != best["prefix"][1]
        )
        contexts.append(
            {
                "marketId": market_id,
                "faultId": fault_id,
                "branchOrdinal": branch_ordinal,
                "programs": len(group),
                "branchReachedRuns": sum(row["branchReached"] for row in group),
                "allBranchStatesMatched": len(hashes) == 1 and len(group) == expected_programs,
                "branchPointStateHash": next(iter(hashes)) if len(hashes) == 1 else None,
                "secondBranchReachedRuns": sum(row["secondBranchReached"] for row in group),
                "waitWorstCaseFloor": wait_row["terminalWorstCaseFloor"],
                "fixedActionOraclePrefix": fixed_best["prefix"],
                "fixedActionOracleWorstCaseFloor": fixed_best["terminalWorstCaseFloor"],
                "prefixOraclePrefix": best["prefix"],
                "prefixOracleWorstCaseFloor": best["terminalWorstCaseFloor"],
                "prefixOracleUsesAppliedSwitch": uses_switch,
                "prefixOracleAdvantageVsWait": best["terminalWorstCaseFloor"]
                - wait_row["terminalWorstCaseFloor"],
                "incrementalPrefixOracleVsFixedActionOracle": best[
                    "terminalWorstCaseFloor"
                ]
                - fixed_best["terminalWorstCaseFloor"],
            }
        )
    wait_floors = {round(row["waitWorstCaseFloor"], 8) for row in contexts}
    summary = {
        "runs": len(rows),
        "contexts": len(contexts),
        "allBranchStatesMatchedContexts": sum(
            row["allBranchStatesMatched"] for row in contexts
        ),
        "semanticViolationRuns": sum(
            row["cycleInvariantViolationCount"] != 0 for row in rows
        ),
        "waitBaselineStableAcrossLandmarks": len(wait_floors) <= 1,
        "laterLandmarksWithPositiveNonWaitOracle": sum(
            row["prefixOraclePrefix"] != [WAIT, WAIT]
            and row["prefixOracleAdvantageVsWait"] > EPS
            for row in contexts
        ),
        "appliedSwitchOracleContexts": sum(
            row["prefixOracleUsesAppliedSwitch"] for row in contexts
        ),
        "waitWorstCaseFloor": sum(row["waitWorstCaseFloor"] for row in contexts),
        "fixedActionOracleWorstCaseFloor": sum(
            row["fixedActionOracleWorstCaseFloor"] for row in contexts
        ),
        "prefixOracleWorstCaseFloor": sum(
            row["prefixOracleWorstCaseFloor"] for row in contexts
        ),
        "prefixOracleAdvantageVsWait": sum(
            row["prefixOracleAdvantageVsWait"] for row in contexts
        ),
        "incrementalPrefixOracleVsFixedActionOracle": sum(
            row["incrementalPrefixOracleVsFixedActionOracle"] for row in contexts
        ),
    }
    summary["pilotGatePass"] = bool(
        len(contexts) >= 3
        and summary["allBranchStatesMatchedContexts"] == len(contexts)
        and summary["semanticViolationRuns"] == 0
        and summary["waitBaselineStableAcrossLandmarks"]
        and summary["laterLandmarksWithPositiveNonWaitOracle"] >= 2
        and summary["prefixOracleAdvantageVsWait"] >= 20.0 - EPS
        and summary["incrementalPrefixOracleVsFixedActionOracle"] >= 5.0 - EPS
        and summary["appliedSwitchOracleContexts"] >= 1
    )
    return contexts, summary


def save(
    output: Path,
    market_id: int,
    fault_id: str,
    branch_ordinals: list[int],
    rows: list[dict[str, Any]],
    complete: bool,
) -> None:
    contexts, summary = summarize(rows)
    report = {
        "version": VERSION,
        "researchOnly": True,
        "preregistration": "hft_r2_recovery_multilandmark_branch_pilot_v1_preregistered.json",
        "objectiveBoundary": (
            "Matched execution-repair curriculum beneath Frozen R2 only; no new "
            "direction, inventory target, alpha or independent entry authority."
        ),
        "complete": bool(complete),
        "marketId": int(market_id),
        "faultId": str(fault_id),
        "branchOrdinals": branch_ordinals,
        "programsPerLandmark": [list(prefix) for prefix in program_grid()],
        "executionSemantics": {
            "simulator": "HftBacktest + Predict Execution Tape V1",
            "strictPast": True,
            "formalWaitBeforeLandmark": True,
            "oneActionPerRecoveryOption": True,
            "tailAction": WAIT,
            "actualFillOwnStateFeedback": True,
            "cancelAckRequired": True,
            "dreamFill": False,
            "winnerSettlementOrPnlRuntimeInput": False,
            "targetFutureActionRuntimeInput": False,
        },
        "observationFeatures": OBSERVATION_FEATURES,
        "rows": rows,
        "contexts": contexts,
        "summary": summary,
        "decision": (
            "KEEP_EXPAND_MULTI_LANDMARK_R2_RECOVERY_CURRICULUM"
            if complete and summary["pilotGatePass"]
            else ("REJECT_MULTI_LANDMARK_PILOT" if complete else "INCOMPLETE")
        ),
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-id", type=int, required=True)
    parser.add_argument(
        "--fault-id",
        choices=("NATURAL_LIFECYCLE", "DEEP_FIRST_PASSIVE_CHILD"),
        default="NATURAL_LIFECYCLE",
    )
    parser.add_argument("--branch-ordinals", default="1,2,3")
    parser.add_argument(
        "--output", default="hft_r2_recovery_multilandmark_branch_pilot_v1_report.json"
    )
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    ordinals = sorted(
        {int(value) for value in args.branch_ordinals.split(",") if value.strip()}
    )
    if not ordinals or min(ordinals) < 1:
        raise ValueError("pilot branch ordinals must be later than the already-tested ordinal 0")
    output = BASE / args.output
    rows: list[dict[str, Any]] = []
    if args.resume and output.exists():
        prior = json.loads(output.read_text(encoding="utf-8"))
        rows = [
            row
            for row in prior.get("rows", [])
            if int(row["marketId"]) == int(args.market_id)
            and str(row["faultId"]) == str(args.fault_id)
            and int(row["branchOrdinal"]) in ordinals
        ]
    completed = {
        (int(row["branchOrdinal"]), str(row["programId"])) for row in rows
    }
    warnings.filterwarnings("ignore", message="X does not have valid feature names")
    total = len(ordinals) * len(program_grid())
    for ordinal in ordinals:
        for prefix in program_grid():
            pid = program_id(ordinal, prefix)
            if (ordinal, pid) in completed:
                continue
            row = run_one(args.market_id, args.fault_id, ordinal, prefix)
            rows.append(row)
            save(output, args.market_id, args.fault_id, ordinals, rows, complete=False)
            print(
                json.dumps(
                    {
                        "progress": f"{len(rows)}/{total}",
                        "branchOrdinal": ordinal,
                        "programId": pid,
                        "branchReached": row["branchReached"],
                        "secondReached": row["secondBranchReached"],
                        "floor": row["terminalWorstCaseFloor"],
                        "violations": row["cycleInvariantViolationCount"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    save(output, args.market_id, args.fault_id, ordinals, rows, complete=True)
    final = json.loads(output.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {"ok": True, "output": str(output), **final["summary"], "decision": final["decision"]},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
