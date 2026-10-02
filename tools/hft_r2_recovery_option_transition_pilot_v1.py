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
from tools.hft_r2_fault_conditioned_recovery_matrix_v1 import (  # noqa: E402
    ACTIONS,
    BASE,
    FAULTS,
    DeepFirstPassiveChild,
    FirstLifecycleAction,
    first_lifecycle_decision,
    state_hash,
)


VERSION = "HFT_R2_RECOVERY_OPTION_TRANSITION_PILOT_V1"
WAIT = "WAIT_PRESERVE"
CHUNK = 18.0
EPS = 1e-8


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _state_at_or_after(states: list[dict[str, Any]], at_ms: int) -> dict[str, Any] | None:
    return next((state for state in states if int(state["atMs"]) >= int(at_ms)), None)


def option_boundary(report: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    start_ms = int(decision["atMs"])
    side = str(decision["side"])
    states = sorted(report["strictPastExecutionStates"], key=lambda row: int(row["atMs"]))
    start_state = _state_at_or_after(states, start_ms)
    if start_state is None or int(start_state["atMs"]) != start_ms:
        raise RuntimeError("missing exact execution state at first lifecycle checkpoint")

    candidates: list[tuple[int, int, str, dict[str, Any]]] = []
    for episode in report["lifecycleAudit"]["replaceEpisodes"]:
        if str(episode.get("side") or "") != side or int(episode.get("triggerAtMs") or -1) != start_ms:
            continue
        # A cancel ACK is an intermediate state when a Taker/retire route follows it.
        # Select the semantically deepest observed boundary from each episode, rather
        # than the earliest timestamp among all lifecycle fields.
        if episode.get("returnedAtMs") is not None:
            candidates.append(
                (
                    int(episode["returnedAtMs"]),
                    0,
                    "REPLACE_OR_RETIRE_RETURNED_TO_LOGIC",
                    episode,
                )
            )
        elif episode.get("terminalAtMs") is not None:
            candidates.append(
                (int(episode["terminalAtMs"]), 1, "REPLACE_TAKER_TERMINAL", episode)
            )
        elif episode.get("dataEndAtMs") is not None:
            candidates.append(
                (int(episode["dataEndAtMs"]), 5, "REPLACE_DATA_END_CENSORED", episode)
            )
        elif episode.get("cancelTerminalObservedAtMs") is not None:
            candidates.append(
                (
                    int(episode["cancelTerminalObservedAtMs"]),
                    2,
                    "CANCEL_TERMINAL_WITHOUT_DOWNSTREAM_CHILD",
                    episode,
                )
            )
    for event in report["lifecycleAudit"]["decisions"]:
        if int(event.get("atMs") or -1) < start_ms or str(event.get("action") or "") != "RETURN_TO_CONTROLLER":
            continue
        if str(event.get("side") or side) != side:
            continue
        candidates.append((int(event["atMs"]), 3, "EXPLICIT_RETURN_TO_CONTROLLER", event))

    if candidates:
        end_ms, _, reason, evidence = min(candidates, key=lambda row: (row[0], row[1]))
        end_state = _state_at_or_after(states, end_ms)
        censored = "DATA_END" in reason
    else:
        later = [
            state
            for state in states
            if int(state["atMs"]) > start_ms and abs(finite(state.get("trackingError"))) < CHUNK - EPS
        ]
        if later:
            end_state = later[0]
            end_ms = int(end_state["atMs"])
            reason = "ASYMMETRY_RESOLVED_BEFORE_EXPLICIT_RETURN"
            evidence = {"trackingError": end_state.get("trackingError")}
            censored = False
        else:
            end_state = report.get("terminalExecutionState") or states[-1]
            end_ms = int(end_state["atMs"])
            reason = "DATA_END_CENSORED_NO_OPTION_BOUNDARY"
            evidence = {}
            censored = True
    if end_state is None:
        end_state = report.get("terminalExecutionState") or states[-1]
    start_portfolio = start_state["actualPortfolio"]
    end_portfolio = end_state["actualPortfolio"]
    return {
        "startAtMs": start_ms,
        "endAtMs": end_ms,
        "durationMs": end_ms - start_ms,
        "boundaryReason": reason,
        "censored": bool(censored),
        "evidence": evidence,
        "startState": start_state,
        "nextState": end_state,
        "localReward": {
            "deltaWorstCaseFloor": finite(end_portfolio.get("worst_case_floor"))
            - finite(start_portfolio.get("worst_case_floor")),
            "deltaPairedCoverage": finite(end_portfolio.get("combined_paired_coverage"))
            - finite(start_portfolio.get("combined_paired_coverage")),
            "deltaAbsPayoffGap": finite(end_portfolio.get("abs_payoff_gap"))
            - finite(start_portfolio.get("abs_payoff_gap")),
            "deltaCombinedGross": finite(end_portfolio.get("combined_gross"))
            - finite(start_portfolio.get("combined_gross")),
            "deltaAbsTrackingError": abs(finite(end_state.get("trackingError")))
            - abs(finite(start_state.get("trackingError"))),
        },
    }


def all_option_transitions(report: dict[str, Any], action_id: str) -> list[dict[str, Any]]:
    checkpoints = sorted(
        [
            event
            for event in report["lifecycleAudit"]["decisions"]
            if isinstance(event.get("stateFeatures"), dict)
        ],
        key=lambda event: int(event["atMs"]),
    )
    transitions = []
    cursor = -1
    while True:
        decision = next((event for event in checkpoints if int(event["atMs"]) > cursor), None)
        if decision is None:
            break
        transition = option_boundary(report, decision)
        transition.update(
            {
                "actionId": action_id,
                "side": str(decision["side"]),
                "pointStateHash": state_hash(
                    int(decision["atMs"]), str(decision["side"]), decision["stateFeatures"]
                ),
                "checkpointFeatures": decision["stateFeatures"],
            }
        )
        transitions.append(transition)
        cursor = max(int(decision["atMs"]), int(transition["endAtMs"]))
        if transition["censored"]:
            break
    return transitions


def run_one(
    market_id: int,
    fault_id: str,
    action_id: str,
    expected: dict[tuple[int, str, str], dict[str, Any]],
) -> dict[str, Any]:
    action_policy = FirstLifecycleAction(ACTIONS[action_id])
    price_fault = DeepFirstPassiveChild(fault_id == "DEEP_FIRST_PASSIVE_CHILD")
    started = time.perf_counter()
    report = run_smoke(
        market_id,
        passive_mode="wait",
        passive_program={"PASSIVE_MAINTAIN": "offset0", "PASSIVE_REPAIR": "offset0"},
        own_state_poll_ms=250,
        passive_price_policy=price_fault,
        lifecycle_action_override=action_policy,
        allowed_executor_taker_kinds={"FROZEN_R2", "PAIR_COMPLETION_REPLACE"},
        trace_execution_states=True,
    )
    decision = first_lifecycle_decision(report)
    if decision is None:
        raise RuntimeError(f"no recovery checkpoint for {market_id}/{fault_id}/{action_id}")
    point_hash = state_hash(int(decision["atMs"]), str(decision["side"]), decision["stateFeatures"])
    prior = expected[(market_id, fault_id, action_id)]
    terminal_floor = finite(report["actualExecution"]["finalPortfolio"].get("worst_case_floor"))
    transitions = all_option_transitions(report, action_id)
    if not transitions:
        raise RuntimeError("no recovery option transition extracted")
    return {
        "marketId": market_id,
        "faultId": fault_id,
        "actionId": action_id,
        "pointStateHash": point_hash,
        "expectedPointStateHash": prior["stateAtFirstAction"]["stateHash"],
        "pointStateMatchesPriorMatrix": point_hash == prior["stateAtFirstAction"]["stateHash"],
        "terminalWorstCaseFloor": terminal_floor,
        "expectedTerminalWorstCaseFloor": finite(prior["terminal"]["worstCaseFloor"]),
        "terminalFloorMatchesPriorMatrix": abs(
            terminal_floor - finite(prior["terminal"]["worstCaseFloor"])
        )
        <= EPS,
        "optionTransition": transitions[0],
        "optionTransitions": transitions,
        "cycleInvariantViolationCount": int(report["cycleInvariantViolationCount"]),
        "runtimeSeconds": time.perf_counter() - started,
    }


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contexts = []
    for market_id, fault_id in sorted({(row["marketId"], row["faultId"]) for row in rows}):
        group = [row for row in rows if row["marketId"] == market_id and row["faultId"] == fault_id]
        by_action = {row["actionId"]: row for row in group}
        wait_local = by_action[WAIT]["optionTransition"]["localReward"]["deltaWorstCaseFloor"]
        local_advantages = {
            action: row["optionTransition"]["localReward"]["deltaWorstCaseFloor"] - wait_local
            for action, row in by_action.items()
        }
        terminal_wait = by_action[WAIT]["terminalWorstCaseFloor"]
        terminal_advantages = {
            action: row["terminalWorstCaseFloor"] - terminal_wait for action, row in by_action.items()
        }
        local_oracle = max(local_advantages, key=lambda action: (local_advantages[action], action == WAIT))
        terminal_oracle = max(
            terminal_advantages, key=lambda action: (terminal_advantages[action], action == WAIT)
        )
        contexts.append(
            {
                "marketId": market_id,
                "faultId": fault_id,
                "matchedStartState": len({row["pointStateHash"] for row in group}) == 1,
                "localFloorAdvantagesVsWait": local_advantages,
                "localOracleAction": local_oracle,
                "localOracleAdvantageVsWait": local_advantages[local_oracle],
                "terminalFloorAdvantagesVsWait": terminal_advantages,
                "terminalOracleAction": terminal_oracle,
                "terminalOracleAdvantageVsWait": terminal_advantages[terminal_oracle],
                "localTerminalOracleAgree": local_oracle == terminal_oracle,
                "boundaryReasons": {
                    action: row["optionTransition"]["boundaryReason"]
                    for action, row in by_action.items()
                },
                "censoredActions": [
                    action for action, row in by_action.items() if row["optionTransition"]["censored"]
                ],
            }
        )
    return contexts


def save(output: Path, market_ids: list[int], source_matrices: list[str], rows: list[dict[str, Any]], complete: bool) -> None:
    contexts = summarize(rows) if rows and len(rows) % len(ACTIONS) == 0 else []
    nontrivial = [
        row
        for row in contexts
        if row["localOracleAction"] != WAIT and row["localOracleAdvantageVsWait"] > EPS
    ]
    report = {
        "version": VERSION,
        "researchOnly": True,
        "preregistration": "hft_r2_recovery_option_transition_pilot_v1_preregistered.json",
        "sourceMatrix": source_matrices,
        "complete": bool(complete),
        "marketIds": market_ids,
        "rows": rows,
        "contexts": contexts,
        "summary": {
            "runs": len(rows),
            "contexts": len(contexts),
            "matchedStartStateContexts": sum(row["matchedStartState"] for row in contexts),
            "pointStateMatchesPriorMatrixRuns": sum(row["pointStateMatchesPriorMatrix"] for row in rows),
            "terminalFloorReproductions": sum(row["terminalFloorMatchesPriorMatrix"] for row in rows),
            "optionBoundaryRuns": sum(bool(row.get("optionTransition")) for row in rows),
            "totalOptionTransitions": sum(len(row.get("optionTransitions") or []) for row in rows),
            "optionTransitionsByAction": {
                action: sum(
                    len(row.get("optionTransitions") or []) for row in rows if row["actionId"] == action
                )
                for action in ACTIONS
            },
            "censoredOptionRuns": sum(row["optionTransition"]["censored"] for row in rows),
            "semanticViolationRuns": sum(row["cycleInvariantViolationCount"] != 0 for row in rows),
            "positiveNonWaitLocalOracleContexts": len(nontrivial),
            "localTerminalOracleAgreementContexts": sum(row["localTerminalOracleAgree"] for row in contexts),
            "localOracleActionCounts": {
                action: sum(row["localOracleAction"] == action for row in contexts) for action in ACTIONS
            },
            "terminalOracleActionCounts": {
                action: sum(row["terminalOracleAction"] == action for row in contexts) for action in ACTIONS
            },
        },
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-ids", required=True)
    parser.add_argument(
        "--source-matrix", default="hft_r2_fault_conditioned_recovery_contract3_v1_report.json"
    )
    parser.add_argument("--output", default="hft_r2_recovery_option_transition_pilot_v1_report.json")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--seed-report")
    args = parser.parse_args()
    market_ids = [int(value) for value in args.market_ids.split(",") if value.strip()]
    source_matrices = [value for value in args.source_matrix.split(",") if value.strip()]
    expected = {}
    for source_name in source_matrices:
        source = json.loads((BASE / source_name).read_text(encoding="utf-8"))
        expected.update(
            {
                (int(row["marketId"]), str(row["faultId"]), str(row["actionId"])): row
                for row in source["rows"]
                if row.get("actionId") in ACTIONS
            }
        )
    output = BASE / args.output
    rows = []
    if args.resume and output.exists():
        prior = json.loads(output.read_text(encoding="utf-8"))
        rows = [
            row for row in prior.get("rows", []) if int(row.get("marketId") or -1) in set(market_ids)
        ]
    elif args.seed_report:
        prior = json.loads((BASE / args.seed_report).read_text(encoding="utf-8"))
        rows = [
            row for row in prior.get("rows", []) if int(row.get("marketId") or -1) in set(market_ids)
        ]
    completed = {
        (int(row["marketId"]), str(row["faultId"]), str(row["actionId"])) for row in rows
    }
    warnings.filterwarnings("ignore", message="X does not have valid feature names")
    for market_id in market_ids:
        for fault_id in FAULTS:
            for action_id in ACTIONS:
                key = (market_id, fault_id, action_id)
                if key in completed:
                    continue
                if key not in expected:
                    raise RuntimeError(f"source matrices are missing {key}")
                row = run_one(market_id, fault_id, action_id, expected)
                rows.append(row)
                save(output, market_ids, source_matrices, rows, complete=False)
                print(
                    json.dumps(
                        {
                            "progress": f"{len(rows)}/{len(market_ids) * len(FAULTS) * len(ACTIONS)}",
                            "marketId": market_id,
                            "faultId": fault_id,
                            "actionId": action_id,
                            "boundary": row["optionTransition"]["boundaryReason"],
                            "censored": row["optionTransition"]["censored"],
                            "localFloorDelta": row["optionTransition"]["localReward"]["deltaWorstCaseFloor"],
                            "terminalFloor": row["terminalWorstCaseFloor"],
                            "matchesPrior": row["terminalFloorMatchesPriorMatrix"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    save(output, market_ids, source_matrices, rows, complete=True)
    final = json.loads(output.read_text(encoding="utf-8"))
    print(json.dumps({"ok": True, "output": str(output), **final["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
