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
VERSION = "HFT_R2_CYCLE_OPTION_PROGRAM_DATASET_V1"
ACTION_TO_ID = {"wait": 0, "offset0": 1, "offset1": 2, "offset2": 3}


OBSERVATION_FEATURES = [
    "terminal",
    "seconds_left",
    "mode_maintain",
    "mode_repair",
    "phase_open",
    "phase_manage",
    "phase_tail",
    "direction_up",
    "direction_strength",
    "direction_score",
    "spot_return_1s_bps",
    "spot_return_3s_bps",
    "spot_queue_imbalance",
    "spot_taker_imbalance_1s",
    "futures_return_1s_bps",
    "futures_return_3s_bps",
    "futures_queue_imbalance",
    "futures_taker_imbalance_1s",
    "book_age_ms",
    "up_bid",
    "up_ask",
    "down_bid",
    "down_ask",
    "up_spread",
    "down_spread",
    "maker_gross",
    "maker_net",
    "maker_abs_net",
    "maker_paired_coverage",
    "taker_gross",
    "taker_net",
    "taker_abs_net",
    "taker_paired_coverage",
    "combined_gross",
    "combined_net",
    "combined_abs_net",
    "combined_paired_coverage",
    "worst_case_floor",
    "best_case_pnl",
    "abs_payoff_gap",
    "maker_fills_1s",
    "maker_fills_5s",
    "maker_fills_10s",
    "maker_shares_5s",
    "maker_shares_10s",
    "desired_up",
    "desired_down",
    "target_net",
    "actual_net",
    "tracking_error",
    "maker_up_exists",
    "maker_up_age_ms",
    "maker_up_price",
    "maker_up_requested_qty",
    "maker_up_cum_exec_qty",
    "maker_up_leaves_qty",
    "maker_up_partial",
    "maker_up_cancel_pending",
    "maker_down_exists",
    "maker_down_age_ms",
    "maker_down_price",
    "maker_down_requested_qty",
    "maker_down_cum_exec_qty",
    "maker_down_leaves_qty",
    "maker_down_partial",
    "maker_down_cancel_pending",
    "open_taker_children",
    "open_taker_filled_qty",
    "open_taker_remaining_qty",
    "open_taker_cancel_requested",
    "route_block_up",
    "route_block_down",
    "pending_replace_count",
    "remainder_owner_up",
    "remainder_owner_down",
    "responsibility_token_count",
    "responsibility_remaining_shares",
    "recent_actual_fill_count_10s",
    "recent_actual_fill_shares_10s",
]


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def nested(mapping: dict[str, Any] | None, *keys: str, default: Any = None) -> Any:
    value: Any = mapping or {}
    for key in keys:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return default if value is None else value


def program_grid() -> list[dict[str, str]]:
    programs = [{"programId": "WAIT_ALL", "maintain": "wait", "repair": "wait"}]
    for maintain in ("offset0", "offset1", "offset2"):
        for repair in ("wait", "offset0", "offset1", "offset2"):
            programs.append(
                {
                    "programId": f"MAKE_{maintain.upper()}__REPAIR_{repair.upper()}",
                    "maintain": maintain,
                    "repair": repair,
                }
            )
    return programs


def action_for_state(state: dict[str, Any], program: dict[str, str]) -> str:
    desired_action = str(nested(state, "decision", "desiredPortfolioAction", default="NONE"))
    if desired_action == "PASSIVE_MAINTAIN":
        return program["maintain"]
    if desired_action == "PASSIVE_REPAIR":
        return program["repair"]
    return "wait"


def observation_vector(state: dict[str, Any]) -> list[float]:
    decision = state.get("decision") or {}
    public = state.get("publicState") or {}
    book = state.get("outcomeBook") or {}
    portfolio = state.get("actualPortfolio") or {}
    desired = state.get("desiredMakerShares") or {}
    maker_children = state.get("activeMakerChildren") or {}
    up_child = maker_children.get("UP") or {}
    down_child = maker_children.get("DOWN") or {}
    taker_children = state.get("openTakerChildren") or []
    phase = str(decision.get("phase") or "").upper()
    desired_action = str(decision.get("desiredPortfolioAction") or "NONE")
    direction = decision.get("direction") or {}
    public_sample_ms = finite(public.get("sampledAtMs"), finite(state.get("atMs")))
    book_received_ms = finite(public.get("bookReceivedAtMs"), public_sample_ms)

    def child_values(child: dict[str, Any]) -> list[float]:
        status = str(child.get("status") or "NONE")
        return [
            float(bool(child)),
            finite(child.get("ageMs")),
            finite(child.get("price")),
            finite(child.get("requestedQty")),
            finite(child.get("cumExecQty")),
            finite(child.get("leavesQty")),
            float(status == "PARTIALLY_FILLED"),
            float(bool(child.get("cancelPending")) or status == "CANCEL_REQUESTED"),
        ]

    recent_fills = state.get("recentActualFills10s") or []
    tokens = state.get("responsibilityTokens") or []
    route_block = state.get("routeBlock") or {}
    remainder_owner = state.get("remainderOwner") or {}
    values = [
        float(bool(state.get("terminal"))),
        finite(public.get("secondsLeft")),
        float(desired_action == "PASSIVE_MAINTAIN"),
        float(desired_action == "PASSIVE_REPAIR"),
        float(phase == "OPEN"),
        float(phase in {"MANAGE", "MID"}),
        float(phase in {"TAIL", "CLOSE"}),
        float(str(direction.get("side") or "") == "UP"),
        finite(direction.get("strength")),
        finite(public.get("directionScore")),
        finite(public.get("spotReturn1sBps")),
        finite(public.get("spotReturn3sBps")),
        finite(public.get("spotQueueImbalance")),
        finite(public.get("spotTakerImbalance1s")),
        finite(public.get("futuresReturn1sBps")),
        finite(public.get("futuresReturn3sBps")),
        finite(public.get("futuresQueueImbalance")),
        finite(public.get("futuresTakerImbalance1s")),
        max(0.0, public_sample_ms - book_received_ms),
        finite(book.get("up_bid")),
        finite(book.get("up_ask")),
        finite(book.get("down_bid")),
        finite(book.get("down_ask")),
        finite(book.get("up_ask")) - finite(book.get("up_bid")),
        finite(book.get("down_ask")) - finite(book.get("down_bid")),
        finite(portfolio.get("maker_gross")),
        finite(portfolio.get("maker_net")),
        finite(portfolio.get("maker_abs_net")),
        finite(portfolio.get("maker_paired_coverage")),
        finite(portfolio.get("taker_gross")),
        finite(portfolio.get("taker_net")),
        finite(portfolio.get("taker_abs_net")),
        finite(portfolio.get("taker_paired_coverage")),
        finite(portfolio.get("combined_gross")),
        finite(portfolio.get("combined_net")),
        finite(portfolio.get("combined_abs_net")),
        finite(portfolio.get("combined_paired_coverage")),
        finite(portfolio.get("worst_case_floor")),
        finite(portfolio.get("best_case_pnl")),
        finite(portfolio.get("abs_payoff_gap")),
        finite(portfolio.get("maker_fills_1s")),
        finite(portfolio.get("maker_fills_5s")),
        finite(portfolio.get("maker_fills_10s")),
        finite(portfolio.get("maker_shares_5s")),
        finite(portfolio.get("maker_shares_10s")),
        finite(desired.get("UP")),
        finite(desired.get("DOWN")),
        finite(state.get("targetNet")),
        finite(state.get("actualNet")),
        finite(state.get("trackingError")),
        *child_values(up_child),
        *child_values(down_child),
        float(len(taker_children)),
        sum(finite(child.get("filledQty")) for child in taker_children),
        sum(finite(child.get("remainingQty")) for child in taker_children),
        float(sum(bool(child.get("cancelRequested")) for child in taker_children)),
        float(bool(route_block.get("UP"))),
        float(bool(route_block.get("DOWN"))),
        float(len(state.get("pendingReplace") or {})),
        float(bool(remainder_owner.get("UP"))),
        float(bool(remainder_owner.get("DOWN"))),
        float(len(tokens)),
        sum(finite(token.get("remainingShares")) for token in tokens),
        float(len(recent_fills)),
        sum(finite(fill.get("shares")) for fill in recent_fills),
    ]
    if len(values) != len(OBSERVATION_FEATURES):
        raise RuntimeError(f"observation size {len(values)} != feature size {len(OBSERVATION_FEATURES)}")
    if not all(math.isfinite(value) for value in values):
        raise RuntimeError("non-finite observation value")
    return values


def episode_from_report(
    report: dict[str, Any], program: dict[str, str], execution_value: float, program_index: int
) -> dict[str, Any]:
    transitions = report["strictPastOptionTransitions"]
    terminal_state = report["terminalExecutionState"]
    if not transitions or not isinstance(terminal_state, dict):
        raise RuntimeError(f"program {program['programId']} has no complete option episode")
    observations = [observation_vector(state) for state in transitions]
    observations.append(observation_vector(terminal_state))
    actions = [ACTION_TO_ID[action_for_state(state, program)] for state in transitions]
    rewards = [0.0 for _ in actions]
    rewards[-1] = float(execution_value)
    terminations = [False for _ in actions]
    terminations[-1] = True
    truncations = [False for _ in actions]
    infos = [
        {
            "market_id": int(report["marketId"]),
            "program_index": int(program_index),
            "option_index": int(index),
            "at_ms": int(state["atMs"]),
            "desired_action_code": 1
            if nested(state, "decision", "desiredPortfolioAction") == "PASSIVE_MAINTAIN"
            else 2
            if nested(state, "decision", "desiredPortfolioAction") == "PASSIVE_REPAIR"
            else 0,
        }
        for index, state in enumerate(transitions)
    ]
    infos.append(
        {
            "market_id": int(report["marketId"]),
            "program_index": int(program_index),
            "option_index": int(len(transitions)),
            "at_ms": int(terminal_state["atMs"]),
            "desired_action_code": 0,
        }
    )
    return {
        "observations": observations,
        "actions": actions,
        "rewards": rewards,
        "terminations": terminations,
        "truncations": truncations,
        "infos": infos,
        "return": float(sum(rewards)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-id", type=int, default=1573252)
    parser.add_argument("--output", default="hft_r2_cycle_option_program_dataset_v1_report.json")
    parser.add_argument("--own-state-poll-ms", type=int)
    parser.add_argument("--preregistration", default="hft_r2_cycle_option_program_dataset_v1_preregistered.json")
    parser.add_argument("--cohort", default="OPENED_DEVELOPMENT_ELSEWHERE_NEW_TO_R2_CYCLE_OPTION_PROGRAM")
    args = parser.parse_args()
    programs = program_grid()
    rows: list[dict[str, Any]] = []
    warnings.filterwarnings("ignore", message="X does not have valid feature names")

    for index, program in enumerate(programs):
        started = time.perf_counter()
        report = run_smoke(
            args.market_id,
            passive_mode="wait",
            passive_program={
                "PASSIVE_MAINTAIN": program["maintain"],
                "PASSIVE_REPAIR": program["repair"],
            },
            own_state_poll_ms=args.own_state_poll_ms,
        )
        actual = report["actualExecution"]
        lifecycle = report["lifecycleAudit"]
        row = {
            **program,
            "marketId": int(args.market_id),
            "runtimeSeconds": time.perf_counter() - started,
            "realizedPnl": actual["realizedPnl"],
            "makerFilledShares": actual["makerFilledShares"],
            "takerFilledShares": actual["takerFilledShares"],
            "takerFeesUsdt": actual["takerFeesUsdt"],
            "finalAbsNet": actual["combinedFinalAbsNet"],
            "finalAbsTrackingError": actual["finalAbsTrackingError"],
            "targetErrorAreaShareSeconds": actual["targetErrorAreaShareSeconds"],
            "combinedExposureAreaShareSeconds": actual["combinedExposureAreaShareSeconds"],
            "finalWorstCaseFloor": nested(actual, "finalPortfolio", "worst_case_floor", default=0.0),
            "cycleInvariantViolationCount": report["cycleInvariantViolationCount"],
            "semanticGate": report["semanticGate"],
            "unresolvedTakerReturns": lifecycle["unresolvedTakerReturns"],
            "cancelPendingAtDataEnd": lifecycle["cancelPendingAtDataEnd"],
            "remainderOwnershipAtEnd": lifecycle["remainderOwnershipAtEnd"],
            "controller": {
                key: report["controller"][key]
                for key in (
                    "steps",
                    "highLevelDecisions",
                    "desiredPortfolioActionCounts",
                    "executionChoiceCounts",
                    "optionTransitions",
                )
            },
            "r2ObjectiveExecution": report["r2ObjectiveExecution"],
            "strictPastOptionTransitions": report["strictPastOptionTransitions"],
            "terminalExecutionState": report["terminalExecutionState"],
        }
        rows.append(row)
        print(
            json.dumps(
                {
                    "progress": f"{index + 1}/{len(programs)}",
                    "program": program["programId"],
                    "pnl": row["realizedPnl"],
                    "makerFilled": row["makerFilledShares"],
                    "takerFilled": row["takerFilledShares"],
                    "violations": row["cycleInvariantViolationCount"],
                    "optionTransitions": len(row["strictPastOptionTransitions"]),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    wait_row = next(row for row in rows if row["programId"] == "WAIT_ALL")
    wait_pnl = finite(wait_row["realizedPnl"])
    episodes = []
    for index, (program, row) in enumerate(zip(programs, rows, strict=True)):
        row["executionValueVsWait"] = finite(row["realizedPnl"]) - wait_pnl
        episode = episode_from_report(row, program, row["executionValueVsWait"], index)
        row["episodeSteps"] = len(episode["actions"])
        row["episodeActSteps"] = sum(action != ACTION_TO_ID["wait"] for action in episode["actions"])
        episodes.append({"programId": program["programId"], **episode})

    constrained = [
        row
        for row in rows
        if int(row["cycleInvariantViolationCount"]) == 0
        and int(row["unresolvedTakerReturns"]) <= int(wait_row["unresolvedTakerReturns"])
        and int(row["cancelPendingAtDataEnd"]) <= int(wait_row["cancelPendingAtDataEnd"])
    ]
    oracle = max(
        constrained,
        key=lambda row: (
            finite(row["executionValueVsWait"]),
            -finite(row["finalAbsTrackingError"]),
            -finite(row["finalAbsNet"]),
        ),
    )
    all_valid = all(
        int(row["cycleInvariantViolationCount"]) == 0
        and row["semanticGate"].get("actualInventoryEqualsHftFillLedger")
        and row["episodeSteps"] > 0
        for row in rows
    )
    oracle_value = max(0.0, finite(oracle["executionValueVsWait"]))
    oracle_episode = next(episode for episode in episodes if episode["programId"] == oracle["programId"])
    oracle_act_steps = sum(action != ACTION_TO_ID["wait"] for action in oracle_episode["actions"])
    oracle_act_rate = oracle_act_steps / len(oracle_episode["actions"])
    multiple_actual_fills = finite(oracle["makerFilledShares"]) + finite(oracle["takerFilledShares"]) >= 36.0
    if not all_valid:
        decision = "BLOCKED_DATA_CONTRACT"
    elif oracle_value <= 0.0:
        decision = "REJECT_ACTION_FAMILY_KEEP_DATA_CONTRACT_PENDING_MINARI"
    elif not multiple_actual_fills:
        decision = "NEED_MORE_DATA_KEEP_DATA_CONTRACT_PENDING_MINARI"
    else:
        decision = "KEEP_ACTION_FAMILY_AND_DATA_CONTRACT_PENDING_MINARI"

    report = {
        "version": VERSION,
        "researchOnly": True,
        "preregistration": args.preregistration,
        "marketId": int(args.market_id),
        "cohort": args.cohort,
        "executionSemantics": {
            "engine": "HftBacktest + Predict Execution Tape V1",
            "queueModel": "risk",
            "entryLatencyMs": 1092,
            "responseLatencyMs": 273,
            "partialFills": True,
            "inventoryMutation": "CONFIRMED_HFTBACKTEST_FILLS_ONLY",
            "logicAuthority": "FROZEN_R2",
            "lifecyclePolicy": "FIXED_KEEP_CURRENT_R2_OBJECTIVE",
            "ownStatePollMs": args.own_state_poll_ms,
            "ownStateEventReentry": args.own_state_poll_ms is not None,
        },
        "actionSpace": ACTION_TO_ID,
        "observationFeatures": OBSERVATION_FEATURES,
        "reward": "terminal realized PnL net of fees minus WAIT_ALL terminal PnL; intermediate rewards zero",
        "winnerRuntimeInput": False,
        "targetFutureActionRuntimeInput": False,
        "programs": rows,
        "episodes": episodes,
        "summary": {
            "programs": len(rows),
            "waitProgramPnl": wait_pnl,
            "constrainedOracleProgram": oracle["programId"],
            "constrainedOracleTotalPnl": oracle["realizedPnl"],
            "constrainedOracleExecutionValueVsWait": oracle_value,
            "oracleActSteps": oracle_act_steps,
            "oracleDecisionSteps": len(oracle_episode["actions"]),
            "oracleActRate": oracle_act_rate,
            "oracleMakerFilledShares": oracle["makerFilledShares"],
            "oracleTakerFilledShares": oracle["takerFilledShares"],
            "learnedPolicyRealizedValue": None,
            "chronologicalUnseenOos": False,
            "allCycleInvariantViolationsZero": all_valid,
            "decision": decision,
        },
        "next": "Export and round-trip validate with Minari. Do not install/train d3rlpy until the episode contract passes and a larger chronological dataset is separately preregistered.",
    }
    output = BASE / args.output
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(output), **report["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
