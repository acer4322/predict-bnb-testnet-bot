from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from collections import Counter
from pathlib import Path
from typing import Any

import joblib
import numpy as np


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
from tools.train_hft_r2_recovery_semimdp_fqi_v1 import predict_action  # noqa: E402


VERSION = "HFT_R2_RECOVERY_SEMIMDP_FQI_CLOSED_LOOP_V1"
WAIT = "WAIT_PRESERVE"
EPS = 1e-8


def finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    return result if np.isfinite(result) else float(default)


class FrozenEpisodePolicy:
    def __init__(self, artifact: dict[str, Any]) -> None:
        if list(artifact["observationFeatures"]) != list(OBSERVATION_FEATURES):
            raise RuntimeError("frozen model observation contract mismatch")
        if dict(artifact["actions"]) != dict(ACTIONS):
            raise RuntimeError("frozen model action contract mismatch")
        self.models = artifact["models"]
        self.action_map = artifact["actions"]
        self.current_action = WAIT
        self.option_decisions: list[dict[str, Any]] = []
        self.checkpoint_calls: list[dict[str, Any]] = []

    def __call__(self, payload: dict[str, Any]) -> str:
        features = payload["features"]
        first_checkpoint = finite(features.get("hasPriorObservation")) < 0.5
        if first_checkpoint:
            execution_state = payload.get("executionState")
            if not isinstance(execution_state, dict):
                raise RuntimeError("closed-loop callback is missing strict-past execution state")
            vector = np.asarray(observation_vector(execution_state), dtype=float)
            if vector.shape != (len(OBSERVATION_FEATURES),) or not np.all(np.isfinite(vector)):
                raise RuntimeError("invalid closed-loop observation")
            prediction = predict_action(self.models, vector)
            self.current_action = str(prediction["selectedAction"])
            self.option_decisions.append(
                {
                    "atMs": int(payload["atMs"]),
                    "side": str(payload["side"]),
                    "targetRevision": int(payload["targetRevision"]),
                    "pointStateHash": state_hash(
                        int(payload["atMs"]), str(payload["side"]), features
                    ),
                    "selectedAction": self.current_action,
                    "predictions": prediction["predictions"],
                    "observationLength": int(vector.size),
                    "observationFinite": bool(np.all(np.isfinite(vector))),
                }
            )
        elif self.current_action != WAIT:
            raise RuntimeError("non-WAIT option unexpectedly reached another checkpoint")
        self.checkpoint_calls.append(
            {
                "atMs": int(payload["atMs"]),
                "checkpointDelayMs": int(payload["checkpointDelayMs"]),
                "firstCheckpoint": bool(first_checkpoint),
                "committedAction": self.current_action,
            }
        )
        return str(self.action_map[self.current_action])


def expected_contexts(fqi_report: dict[str, Any]) -> dict[tuple[int, str], dict[str, Any]]:
    rows = list(fqi_report["train"]["decisions"]) + list(fqi_report["validation"]["decisions"])
    return {(int(row["marketId"]), str(row["faultId"])): row for row in rows}


def run_one(
    market_id: int,
    fault_id: str,
    artifact: dict[str, Any],
    expected: dict[str, Any],
) -> dict[str, Any]:
    policy = FrozenEpisodePolicy(artifact)
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
        trace_execution_states=False,
    )
    if not policy.option_decisions:
        raise RuntimeError(f"no closed-loop option decision for {market_id}/{fault_id}")
    first = policy.option_decisions[0]
    portfolio = report["actualExecution"]["finalPortfolio"]
    terminal_floor = finite(portfolio.get("worst_case_floor"))
    wait_floor = finite(expected["waitWorstCaseFloor"])
    proxy_floor = finite(expected["selectedWorstCaseFloor"])
    counts = Counter(row["selectedAction"] for row in policy.option_decisions)
    return {
        "marketId": int(market_id),
        "faultId": str(fault_id),
        "firstPointStateHash": first["pointStateHash"],
        "expectedFirstPointStateHash": str(expected["pointStateHash"]),
        "firstPointStateMatchesFrozenReport": first["pointStateHash"]
        == str(expected["pointStateHash"]),
        "firstSelectedAction": first["selectedAction"],
        "expectedFirstSelectedAction": str(expected["selectedAction"]),
        "firstActionMatchesFrozenReport": first["selectedAction"]
        == str(expected["selectedAction"]),
        "optionDecisions": policy.option_decisions,
        "optionActionCounts": {action: counts.get(action, 0) for action in ACTIONS},
        "optionActRate": sum(row["selectedAction"] != WAIT for row in policy.option_decisions)
        / len(policy.option_decisions),
        "checkpointCalls": policy.checkpoint_calls,
        "terminalWorstCaseFloor": terminal_floor,
        "fixedWaitWorstCaseFloor": wait_floor,
        "advantageVsFixedWait": terminal_floor - wait_floor,
        "firstStateFixedActionProxyFloor": proxy_floor,
        "deltaVsFirstStateFixedActionProxy": terminal_floor - proxy_floor,
        "fixedActionOracleWorstCaseFloor": finite(expected["oracleWorstCaseFloor"]),
        "cycleInvariantViolationCount": int(report["cycleInvariantViolationCount"]),
        "actualMakerFilledShares": finite(report["actualExecution"]["makerFilledShares"]),
        "actualTakerFilledShares": finite(report["actualExecution"]["takerFilledShares"]),
        "runtimeSeconds": time.perf_counter() - started,
    }


def summarize(rows: list[dict[str, Any]], split: str, gate_enabled: bool) -> dict[str, Any]:
    option_counts = Counter(
        action
        for row in rows
        for action, count in row["optionActionCounts"].items()
        for _ in range(int(count))
    )
    total_options = sum(option_counts.values())
    policy_floor = sum(row["terminalWorstCaseFloor"] for row in rows)
    wait_floor = sum(row["fixedWaitWorstCaseFloor"] for row in rows)
    gate = bool(
        gate_enabled
        and policy_floor > 0.0
        and policy_floor - wait_floor > 0.0
        and sum(option_counts[action] for action in ACTIONS if action != WAIT) > 0
        and all(row["cycleInvariantViolationCount"] == 0 for row in rows)
    )
    return {
        "split": split,
        "contexts": len(rows),
        "firstStateMatches": sum(row["firstPointStateMatchesFrozenReport"] for row in rows),
        "firstActionMatches": sum(row["firstActionMatchesFrozenReport"] for row in rows),
        "finiteObservationDecisions": sum(
            decision["observationFinite"] and decision["observationLength"] == len(OBSERVATION_FEATURES)
            for row in rows
            for decision in row["optionDecisions"]
        ),
        "optionDecisions": total_options,
        "optionActionCounts": {action: option_counts.get(action, 0) for action in ACTIONS},
        "optionActRate": 0.0
        if total_options == 0
        else sum(option_counts[action] for action in ACTIONS if action != WAIT) / total_options,
        "policyWorstCaseFloor": policy_floor,
        "fixedWaitWorstCaseFloor": wait_floor,
        "policyAdvantageVsFixedWait": policy_floor - wait_floor,
        "firstStateFixedActionProxyFloor": sum(
            row["firstStateFixedActionProxyFloor"] for row in rows
        ),
        "deltaVsFirstStateFixedActionProxy": sum(
            row["deltaVsFirstStateFixedActionProxy"] for row in rows
        ),
        "fixedActionOracleWorstCaseFloor": sum(
            row["fixedActionOracleWorstCaseFloor"] for row in rows
        ),
        "cycleInvariantViolationContexts": sum(
            row["cycleInvariantViolationCount"] != 0 for row in rows
        ),
        "gateEnabled": bool(gate_enabled),
        "gatePass": gate,
    }


def save(
    output: Path,
    model_name: str,
    fqi_report_name: str,
    market_ids: list[int],
    split: str,
    rows: list[dict[str, Any]],
    gate_enabled: bool,
    complete: bool,
) -> None:
    summary = summarize(rows, split, gate_enabled)
    report = {
        "version": VERSION,
        "researchOnly": True,
        "preregistration": "hft_r2_recovery_semimdp_fqi_closed_loop_v1_preregistered.json",
        "modelArtifact": model_name,
        "sourceFqiReport": fqi_report_name,
        "split": split,
        "complete": bool(complete),
        "marketIds": market_ids,
        "executionSemantics": {
            "simulator": "HftBacktest + Predict Execution Tape V1",
            "strictPast": True,
            "formalWait": True,
            "waitCommittedUntilEpisodeBoundary": True,
            "actualFillOwnStateFeedback": True,
            "cancelAckRequired": True,
            "dreamFill": False,
            "winnerSettlementOrPnlRuntimeInput": False,
            "targetFutureActionRuntimeInput": False,
        },
        "rows": rows,
        "summary": summary,
        "decision": (
            "KEEP_FREEZE_CLOSED_LOOP_V1_FOR_BLIND_HOLDOUT"
            if summary["gatePass"]
            else (
                "REJECT_CLOSED_LOOP_V1_BEFORE_HOLDOUT"
                if gate_enabled and complete
                else "CONTRACT_ONLY_NO_ECONOMIC_DECISION"
            )
        ),
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-ids", required=True)
    parser.add_argument("--split", required=True)
    parser.add_argument("--model", default="hft_r2_recovery_semimdp_fqi_v1.joblib")
    parser.add_argument("--fqi-report", default="hft_r2_recovery_semimdp_fqi_v1_report.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    market_ids = [int(value) for value in args.market_ids.split(",") if value.strip()]
    artifact = joblib.load(BASE / args.model)
    fqi_report = json.loads((BASE / args.fqi_report).read_text(encoding="utf-8"))
    expected = expected_contexts(fqi_report)
    output = BASE / args.output
    rows = []
    if args.resume and output.exists():
        prior = json.loads(output.read_text(encoding="utf-8"))
        rows = [row for row in prior.get("rows", []) if int(row["marketId"]) in market_ids]
    completed = {(int(row["marketId"]), str(row["faultId"])) for row in rows}
    warnings.filterwarnings("ignore", message="X does not have valid feature names")
    for market_id in market_ids:
        for fault_id in ("NATURAL_LIFECYCLE", "DEEP_FIRST_PASSIVE_CHILD"):
            key = (market_id, fault_id)
            if key in completed:
                continue
            if key not in expected:
                raise RuntimeError(f"frozen FQI report is missing context {key}")
            row = run_one(market_id, fault_id, artifact, expected[key])
            rows.append(row)
            save(
                output,
                args.model,
                args.fqi_report,
                market_ids,
                args.split,
                rows,
                args.gate,
                complete=False,
            )
            print(
                json.dumps(
                    {
                        "progress": f"{len(rows)}/{len(market_ids) * 2}",
                        "marketId": market_id,
                        "faultId": fault_id,
                        "firstAction": row["firstSelectedAction"],
                        "optionActionCounts": row["optionActionCounts"],
                        "terminalFloor": row["terminalWorstCaseFloor"],
                        "advantageVsWait": row["advantageVsFixedWait"],
                        "deltaVsProxy": row["deltaVsFirstStateFixedActionProxy"],
                        "violations": row["cycleInvariantViolationCount"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    save(
        output,
        args.model,
        args.fqi_report,
        market_ids,
        args.split,
        rows,
        args.gate,
        complete=True,
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    print(json.dumps({"ok": True, "output": str(output), **report["summary"], "decision": report["decision"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
