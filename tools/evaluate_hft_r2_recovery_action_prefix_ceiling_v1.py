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


VERSION = "HFT_R2_RECOVERY_ACTION_PREFIX_CEILING_V1"
WAIT = "WAIT_PRESERVE"
EPS = 1e-8


def finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    return result


def program_grid() -> list[tuple[str, str]]:
    return list(itertools.product(ACTIONS, repeat=2))


def program_id(prefix: tuple[str, str]) -> str:
    return f"{prefix[0]}__THEN__{prefix[1]}__THEN_WAIT"


class PrefixPolicy:
    def __init__(self, prefix: tuple[str, str]) -> None:
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
            self.current_action = (
                self.prefix[self.episode_index] if self.episode_index < len(self.prefix) else WAIT
            )
            execution_state = payload.get("executionState")
            if not isinstance(execution_state, dict):
                raise RuntimeError("prefix callback is missing strict-past execution state")
            observation = observation_vector(execution_state)
            self.option_decisions.append(
                {
                    "optionIndex": self.episode_index,
                    "atMs": int(payload["atMs"]),
                    "side": str(payload["side"]),
                    "pointStateHash": state_hash(
                        int(payload["atMs"]), str(payload["side"]), features
                    ),
                    "selectedAction": self.current_action,
                    "previousRecoveryAction": previous_action,
                    "observation": observation if self.episode_index < 2 else None,
                    "observationLength": len(observation),
                }
            )
        elif self.current_action != WAIT:
            raise RuntimeError("non-WAIT option unexpectedly reached another checkpoint")
        return str(ACTIONS[self.current_action])


def source_contexts(report: dict[str, Any]) -> dict[tuple[int, str], dict[str, Any]]:
    grouped: dict[tuple[int, str], dict[str, dict[str, Any]]] = {}
    for row in report["rows"]:
        key = (int(row["marketId"]), str(row["faultId"]))
        grouped.setdefault(key, {})[str(row["actionId"])] = row
    contexts = {}
    for key, by_action in grouped.items():
        if set(by_action) != set(ACTIONS):
            continue
        floors = {
            action: finite(row["terminalWorstCaseFloor"]) for action, row in by_action.items()
        }
        fixed_oracle_action = max(
            ACTIONS, key=lambda action: (floors[action], action == WAIT)
        )
        contexts[key] = {
            "firstPointStateHash": str(by_action[WAIT]["pointStateHash"]),
            "fixedActionFloors": floors,
            "fixedActionOracleAction": fixed_oracle_action,
            "fixedActionOracleFloor": floors[fixed_oracle_action],
        }
    return contexts


def run_one(
    market_id: int,
    fault_id: str,
    prefix: tuple[str, str],
    source: dict[str, Any],
) -> dict[str, Any]:
    policy = PrefixPolicy(prefix)
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
    if not policy.option_decisions:
        raise RuntimeError(f"no prefix option decision for {market_id}/{fault_id}")
    terminal_floor = finite(
        report["actualExecution"]["finalPortfolio"].get("worst_case_floor")
    )
    counts = Counter(row["selectedAction"] for row in policy.option_decisions)
    return {
        "marketId": int(market_id),
        "faultId": str(fault_id),
        "programId": program_id(prefix),
        "prefix": list(prefix),
        "firstPointStateHash": policy.option_decisions[0]["pointStateHash"],
        "expectedFirstPointStateHash": source["firstPointStateHash"],
        "firstPointStateMatches": policy.option_decisions[0]["pointStateHash"]
        == source["firstPointStateHash"],
        "optionDecisions": policy.option_decisions,
        "secondActionApplied": len(policy.option_decisions) >= 2,
        "optionActionCounts": {action: counts.get(action, 0) for action in ACTIONS},
        "terminalWorstCaseFloor": terminal_floor,
        "fixedWaitWorstCaseFloor": source["fixedActionFloors"][WAIT],
        "fixedActionOracleWorstCaseFloor": source["fixedActionOracleFloor"],
        "cycleInvariantViolationCount": int(report["cycleInvariantViolationCount"]),
        "runtimeSeconds": time.perf_counter() - started,
    }


def summarize(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    contexts = []
    wait_program = program_id((WAIT, WAIT))
    for market_id, fault_id in sorted({(row["marketId"], row["faultId"]) for row in rows}):
        group = [
            row for row in rows if row["marketId"] == market_id and row["faultId"] == fault_id
        ]
        if len(group) != len(program_grid()):
            continue
        best = max(
            group,
            key=lambda row: (
                row["terminalWorstCaseFloor"],
                -sum(row["optionActionCounts"][a] for a in ACTIONS if a != WAIT),
                row["programId"] == wait_program,
            ),
        )
        wait_row = next(row for row in group if row["programId"] == wait_program)
        mixed = best["prefix"][0] != best["prefix"][1] and best["secondActionApplied"]
        contexts.append(
            {
                "marketId": market_id,
                "faultId": fault_id,
                "allFirstStatesMatched": all(row["firstPointStateMatches"] for row in group),
                "waitWaitWorstCaseFloor": wait_row["terminalWorstCaseFloor"],
                "fixedWaitWorstCaseFloor": wait_row["fixedWaitWorstCaseFloor"],
                "waitWaitReproducesFixedWait": abs(
                    wait_row["terminalWorstCaseFloor"] - wait_row["fixedWaitWorstCaseFloor"]
                )
                <= EPS,
                "fixedActionOracleWorstCaseFloor": wait_row[
                    "fixedActionOracleWorstCaseFloor"
                ],
                "prefixOracleProgram": best["programId"],
                "prefixOracle": best["prefix"],
                "prefixOracleSecondActionApplied": best["secondActionApplied"],
                "prefixOracleUsesAppliedSwitch": mixed,
                "prefixOracleWorstCaseFloor": best["terminalWorstCaseFloor"],
                "incrementalPrefixOracleVsFixedActionOracle": best["terminalWorstCaseFloor"]
                - wait_row["fixedActionOracleWorstCaseFloor"],
                "prefixOracleOptionActionCounts": best["optionActionCounts"],
            }
        )
    incremental = sum(
        row["incrementalPrefixOracleVsFixedActionOracle"] for row in contexts
    )
    gate = bool(
        contexts
        and all(row["allFirstStatesMatched"] for row in contexts)
        and all(row["waitWaitReproducesFixedWait"] for row in contexts)
        and all(row["cycleInvariantViolationCount"] == 0 for row in rows)
        and incremental >= 5.0 - EPS
        and any(row["prefixOracleUsesAppliedSwitch"] for row in contexts)
    )
    summary = {
        "runs": len(rows),
        "contexts": len(contexts),
        "matchedFirstStateRuns": sum(row["firstPointStateMatches"] for row in rows),
        "semanticViolationRuns": sum(
            row["cycleInvariantViolationCount"] != 0 for row in rows
        ),
        "waitReproductionContexts": sum(
            row["waitWaitReproducesFixedWait"] for row in contexts
        ),
        "fixedActionOracleWorstCaseFloor": sum(
            row["fixedActionOracleWorstCaseFloor"] for row in contexts
        ),
        "prefixOracleWorstCaseFloor": sum(
            row["prefixOracleWorstCaseFloor"] for row in contexts
        ),
        "incrementalPrefixOracleVsFixedActionOracle": incremental,
        "appliedSwitchOracleContexts": sum(
            row["prefixOracleUsesAppliedSwitch"] for row in contexts
        ),
        "expansionGatePass": gate,
    }
    return contexts, summary


def save(
    output: Path,
    market_ids: list[int],
    source_report: str,
    rows: list[dict[str, Any]],
    complete: bool,
) -> None:
    contexts, summary = summarize(rows)
    report = {
        "version": VERSION,
        "researchOnly": True,
        "preregistration": "hft_r2_recovery_action_prefix_ceiling_v1_preregistered.json",
        "sourceFixedActionReport": source_report,
        "complete": bool(complete),
        "marketIds": market_ids,
        "programs": [program_id(prefix) for prefix in program_grid()],
        "executionSemantics": {
            "simulator": "HftBacktest + Predict Execution Tape V1",
            "strictPast": True,
            "formalWait": True,
            "oneActionPerRecoveryOption": True,
            "tailAction": WAIT,
            "actualFillOwnStateFeedback": True,
            "cancelAckRequired": True,
            "dreamFill": False,
            "winnerSettlementOrPnlRuntimeInput": False,
        },
        "observationFeatures": OBSERVATION_FEATURES,
        "rows": rows,
        "contexts": contexts,
        "summary": summary,
        "decision": (
            "KEEP_EXPAND_MATCHED_ACTION_BRANCHING"
            if complete and summary["expansionGatePass"]
            else (
                "REJECT_TWO_OPTION_ACTION_BRANCHING"
                if complete
                else "INCOMPLETE"
            )
        ),
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-ids", required=True)
    parser.add_argument(
        "--source-report", default="hft_r2_recovery_semimdp_train26_v1_report.json"
    )
    parser.add_argument(
        "--output", default="hft_r2_recovery_action_prefix_ceiling_pilot1_v1_report.json"
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--seed-report")
    args = parser.parse_args()
    market_ids = [int(value) for value in args.market_ids.split(",") if value.strip()]
    source_report = json.loads((BASE / args.source_report).read_text(encoding="utf-8"))
    sources = source_contexts(source_report)
    output = BASE / args.output
    rows = []
    if args.resume and output.exists():
        prior = json.loads(output.read_text(encoding="utf-8"))
        rows = [row for row in prior.get("rows", []) if int(row["marketId"]) in market_ids]
    elif args.seed_report:
        prior = json.loads((BASE / args.seed_report).read_text(encoding="utf-8"))
        rows = [row for row in prior.get("rows", []) if int(row["marketId"]) in market_ids]
    completed = {
        (int(row["marketId"]), str(row["faultId"]), str(row["programId"])) for row in rows
    }
    warnings.filterwarnings("ignore", message="X does not have valid feature names")
    total = len(market_ids) * 2 * len(program_grid())
    for market_id in market_ids:
        for fault_id in ("NATURAL_LIFECYCLE", "DEEP_FIRST_PASSIVE_CHILD"):
            key = (market_id, fault_id)
            if key not in sources:
                raise RuntimeError(f"source fixed-action report is missing {key}")
            for prefix in program_grid():
                pid = program_id(prefix)
                if (market_id, fault_id, pid) in completed:
                    continue
                row = run_one(market_id, fault_id, prefix, sources[key])
                rows.append(row)
                save(output, market_ids, args.source_report, rows, complete=False)
                print(
                    json.dumps(
                        {
                            "progress": f"{len(rows)}/{total}",
                            "marketId": market_id,
                            "faultId": fault_id,
                            "programId": pid,
                            "secondApplied": row["secondActionApplied"],
                            "floor": row["terminalWorstCaseFloor"],
                            "violations": row["cycleInvariantViolationCount"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    save(output, market_ids, args.source_report, rows, complete=True)
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
