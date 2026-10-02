from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_r2_cycle_option_program_dataset_v1 import OBSERVATION_FEATURES  # noqa: E402
from tools.hft_r2_fault_conditioned_recovery_matrix_v1 import ACTIONS, BASE  # noqa: E402


VERSION = "HFT_R2_AUTONOMOUS_RECOVERY_BRANCH_DATASET_V1"
WAIT = "WAIT_PRESERVE"
PREVIOUS_ACTIONS = ("NONE", *ACTIONS)
EPS = 1e-8


def finite(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise RuntimeError("non-finite value in branch report")
    return result


def augmented_observation(
    observation: list[float], option_index: int, previous_action: str
) -> list[float]:
    if len(observation) != len(OBSERVATION_FEATURES):
        raise RuntimeError("base observation contract mismatch")
    if previous_action not in PREVIOUS_ACTIONS:
        raise RuntimeError(f"unsupported previous action {previous_action}")
    values = [finite(value) for value in observation]
    values.append(float(option_index))
    values.extend(float(previous_action == action) for action in PREVIOUS_ACTIONS)
    return values


def choose_oracle(advantages: dict[str, float]) -> str:
    return max(ACTIONS, key=lambda action: (advantages[action], action == WAIT))


def context_row(
    market_id: int,
    fault_id: str,
    option_index: int,
    previous_action: str,
    state_hash: str,
    observation: list[float],
    floors: dict[str, float],
    source_programs: dict[str, list[str]],
) -> dict[str, Any]:
    wait_floor = floors[WAIT]
    advantages = {action: floors[action] - wait_floor for action in ACTIONS}
    oracle = choose_oracle(advantages)
    return {
        "marketId": int(market_id),
        "faultIdAuditOnly": str(fault_id),
        "optionIndex": int(option_index),
        "previousRecoveryAction": str(previous_action),
        "stateHash": str(state_hash),
        "observation": augmented_observation(observation, option_index, previous_action),
        "actionWorstCaseFloors": floors,
        "actionAdvantagesVsWait": advantages,
        "oracleAction": oracle,
        "oracleWorstCaseFloor": floors[oracle],
        "oracleAdvantageVsWait": advantages[oracle],
        "sourcePrograms": source_programs,
    }


def build(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = report["rows"]
    result = []
    by_context: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for row in rows:
        by_context.setdefault((int(row["marketId"]), str(row["faultId"])), []).append(row)
    for (market_id, fault_id), group in sorted(by_context.items()):
        if len(group) != 9:
            raise RuntimeError(f"incomplete prefix grid at {market_id}/{fault_id}")
        first_hashes = {str(row["optionDecisions"][0]["pointStateHash"]) for row in group}
        first_observations = {
            tuple(float(value) for value in row["optionDecisions"][0]["observation"])
            for row in group
        }
        if len(first_hashes) != 1 or len(first_observations) != 1:
            raise RuntimeError(f"unmatched first state at {market_id}/{fault_id}")
        first_floors = {}
        first_sources = {}
        for first_action in ACTIONS:
            branches = [row for row in group if row["prefix"][0] == first_action]
            best = max(branches, key=lambda row: finite(row["terminalWorstCaseFloor"]))
            first_floors[first_action] = finite(best["terminalWorstCaseFloor"])
            first_sources[first_action] = [str(best["programId"])]
        result.append(
            context_row(
                market_id,
                fault_id,
                0,
                "NONE",
                next(iter(first_hashes)),
                list(next(iter(first_observations))),
                first_floors,
                first_sources,
            )
        )
        for first_action in ACTIONS:
            branches = [row for row in group if row["prefix"][0] == first_action]
            applied = [row for row in branches if bool(row["secondActionApplied"])]
            if not applied:
                continue
            if len(applied) != 3:
                raise RuntimeError(
                    f"partial second branch at {market_id}/{fault_id}/{first_action}"
                )
            second_hashes = {
                str(row["optionDecisions"][1]["pointStateHash"]) for row in applied
            }
            second_observations = {
                tuple(float(value) for value in row["optionDecisions"][1]["observation"])
                for row in applied
            }
            if len(second_hashes) != 1 or len(second_observations) != 1:
                raise RuntimeError(
                    f"unmatched second state at {market_id}/{fault_id}/{first_action}"
                )
            floors = {str(row["prefix"][1]): finite(row["terminalWorstCaseFloor"]) for row in applied}
            sources = {str(row["prefix"][1]): [str(row["programId"])] for row in applied}
            if set(floors) != set(ACTIONS):
                raise RuntimeError("incomplete second-action values")
            result.append(
                context_row(
                    market_id,
                    fault_id,
                    1,
                    first_action,
                    next(iter(second_hashes)),
                    list(next(iter(second_observations))),
                    floors,
                    sources,
                )
            )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source", default="hft_r2_autonomous_recovery_branch_train23_v1_report.json"
    )
    parser.add_argument(
        "--output", default="hft_r2_autonomous_recovery_branch_dataset_v1.json"
    )
    args = parser.parse_args()
    source = json.loads((BASE / args.source).read_text(encoding="utf-8"))
    contexts = build(source)
    action_counts = Counter(row["oracleAction"] for row in contexts)
    by_depth = {}
    for depth in (0, 1):
        depth_rows = [row for row in contexts if row["optionIndex"] == depth]
        counts = Counter(row["oracleAction"] for row in depth_rows)
        by_depth[str(depth)] = {
            "contexts": len(depth_rows),
            "oracleActionCounts": {action: counts.get(action, 0) for action in ACTIONS},
            "positiveNonWaitOracleContexts": sum(
                row["oracleAction"] != WAIT and row["oracleAdvantageVsWait"] > EPS
                for row in depth_rows
            ),
            "waitWorstCaseFloor": sum(row["actionWorstCaseFloors"][WAIT] for row in depth_rows),
            "oracleWorstCaseFloor": sum(row["oracleWorstCaseFloor"] for row in depth_rows),
            "oracleAdvantageVsWait": sum(row["oracleAdvantageVsWait"] for row in depth_rows),
        }
    report = {
        "version": VERSION,
        "researchOnly": True,
        "source": args.source,
        "objectiveBoundary": "Matched action values only inside Frozen R2 recovery obligations; not a new strategy.",
        "baseObservationFeatures": OBSERVATION_FEATURES,
        "augmentedObservationFeatures": [
            *OBSERVATION_FEATURES,
            "recovery_option_index",
            *[f"previous_recovery_action_{action}" for action in PREVIOUS_ACTIONS],
        ],
        "winnerSettlementOrPnlInput": False,
        "faultIdAsFeature": False,
        "contexts": contexts,
        "summary": {
            "markets": len({row["marketId"] for row in contexts}),
            "decisionContexts": len(contexts),
            "uniqueStateHashes": len({row["stateHash"] for row in contexts}),
            "observationFeatures": len(contexts[0]["observation"]),
            "oracleActionCounts": {action: action_counts.get(action, 0) for action in ACTIONS},
            "byOptionIndex": by_depth,
        },
    }
    (BASE / args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"ok": True, "output": str(BASE / args.output), **report["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
