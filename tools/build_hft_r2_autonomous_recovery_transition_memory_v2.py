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


VERSION = "HFT_R2_AUTONOMOUS_RECOVERY_TRANSITION_MEMORY_V2"
WAIT = "WAIT_PRESERVE"
EPS = 1e-8


def finite(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise RuntimeError("non-finite transition-memory value")
    return result


def memory_vector(previous: dict[str, Any], current: dict[str, Any], previous_action: str) -> list[float]:
    before = [finite(value) for value in previous["observation"]]
    after = [finite(value) for value in current["observation"]]
    if len(before) != len(OBSERVATION_FEATURES) or len(after) != len(OBSERVATION_FEATURES):
        raise RuntimeError("base observation length mismatch")
    elapsed = int(current["atMs"]) - int(previous["atMs"])
    if elapsed < 0:
        raise RuntimeError("negative recovery transition duration")
    return [
        *after,
        *[right - left for left, right in zip(before, after)],
        float(elapsed),
        *[float(previous_action == action) for action in ACTIONS],
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source", default="hft_r2_autonomous_recovery_branch_train23_v1_report.json"
    )
    parser.add_argument(
        "--output", default="hft_r2_autonomous_recovery_transition_memory_v2.json"
    )
    args = parser.parse_args()
    report = json.loads((BASE / args.source).read_text(encoding="utf-8"))
    groups: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
    for row in report["rows"]:
        key = (int(row["marketId"]), str(row["faultId"]), str(row["prefix"][0]))
        groups.setdefault(key, []).append(row)
    contexts = []
    for (market_id, fault_id, previous_action), rows in sorted(groups.items()):
        applied = [row for row in rows if bool(row["secondActionApplied"])]
        if not applied:
            continue
        if len(applied) != 3:
            raise RuntimeError("partial second-action branch")
        first_hashes = {str(row["optionDecisions"][0]["pointStateHash"]) for row in applied}
        second_hashes = {str(row["optionDecisions"][1]["pointStateHash"]) for row in applied}
        if len(first_hashes) != 1 or len(second_hashes) != 1:
            raise RuntimeError("unmatched recovery transition state")
        exemplar = applied[0]
        first = exemplar["optionDecisions"][0]
        second = exemplar["optionDecisions"][1]
        floors = {
            str(row["prefix"][1]): finite(row["terminalWorstCaseFloor"]) for row in applied
        }
        wait_floor = floors[WAIT]
        advantages = {action: floors[action] - wait_floor for action in ACTIONS}
        oracle = max(ACTIONS, key=lambda action: (advantages[action], action == WAIT))
        contexts.append(
            {
                "marketId": market_id,
                "faultIdAuditOnly": fault_id,
                "previousRecoveryAction": previous_action,
                "previousStateHash": next(iter(first_hashes)),
                "stateHash": next(iter(second_hashes)),
                "elapsedMs": int(second["atMs"]) - int(first["atMs"]),
                "observation": memory_vector(first, second, previous_action),
                "actionWorstCaseFloors": floors,
                "actionAdvantagesVsWait": advantages,
                "oracleAction": oracle,
                "oracleAdvantageVsWait": advantages[oracle],
            }
        )
    counts = Counter(row["oracleAction"] for row in contexts)
    output = {
        "version": VERSION,
        "researchOnly": True,
        "preregistration": "hft_r2_autonomous_recovery_transition_memory_v2_preregistered.json",
        "source": args.source,
        "objectiveBoundary": "Second R2 recovery decision after mandatory WAIT observation; not a new strategy.",
        "features": [
            *[f"current_{name}" for name in OBSERVATION_FEATURES],
            *[f"delta_{name}" for name in OBSERVATION_FEATURES],
            "recovery_transition_elapsed_ms",
            *[f"previous_recovery_action_{action}" for action in ACTIONS],
        ],
        "faultIdAsFeature": False,
        "winnerSettlementOrPnlInput": False,
        "contexts": contexts,
        "summary": {
            "markets": len({row["marketId"] for row in contexts}),
            "contexts": len(contexts),
            "features": len(contexts[0]["observation"]),
            "previousWaitContexts": sum(row["previousRecoveryAction"] == WAIT for row in contexts),
            "oracleActionCounts": {action: counts.get(action, 0) for action in ACTIONS},
            "oracleAdvantageVsWait": sum(row["oracleAdvantageVsWait"] for row in contexts),
        },
    }
    (BASE / args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(BASE / args.output), **output["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
