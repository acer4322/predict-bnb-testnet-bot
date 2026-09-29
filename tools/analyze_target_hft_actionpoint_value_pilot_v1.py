from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
BOOK_DB = ROOT / "data" / "wallet_maker_book_inference.db"
DEFAULT_DATASET = BASE / "target_hft_actionpoint_value_smoke3_v1.json"
DEFAULT_OUTPUT = BASE / "target_hft_actionpoint_value_pilot_v1_report.json"
PRIMARY_HORIZON_MS = 2000
DIAGNOSTIC_HORIZON_MS = 1000
MIN_CONFIDENCE = 0.60
EPS = 1e-9


def finite(value: Any) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else 0.0
    except Exception:
        return 0.0


def load_placements(market_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    result = {market_id: [] for market_id in market_ids}
    placeholders = ",".join("?" for _ in market_ids)
    connection = sqlite3.connect(
        f"file:{BOOK_DB.resolve().as_posix()}?mode=ro",
        uri=True,
        timeout=20,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=20000")
    try:
        rows = connection.execute(
            f"""SELECT market_id,target_side,placement_first_ms,target_filled_shares,
                       placement_coverage,confidence
                  FROM maker_book_inference_v21_parent_lifecycles
                 WHERE market_id IN ({placeholders})
                   AND placement_first_ms IS NOT NULL
                   AND placement_coverage>=1.0
                   AND confidence>=?
                 ORDER BY market_id,placement_first_ms,parent_id""",
            [*market_ids, MIN_CONFIDENCE],
        )
        for row in rows:
            side = str(row["target_side"] or "").upper()
            if side not in {"UP", "DOWN"}:
                continue
            result[int(row["market_id"])].append(
                {
                    "side": side,
                    "placementMs": int(row["placement_first_ms"]),
                    "shares": finite(row["target_filled_shares"]),
                    "confidence": finite(row["confidence"]),
                }
            )
    finally:
        connection.close()
    return result


def best_with_wait(actions: list[dict[str, Any]]) -> dict[str, Any]:
    filled = [action for action in actions if finite(action.get("filledShares5s")) > EPS]
    positive = [action for action in actions if finite(action.get("mtm1sUsdt")) > EPS]
    best = max(actions, key=lambda action: finite(action.get("mtm1sUsdt"))) if actions else None
    if best is None or finite(best.get("mtm1sUsdt")) <= EPS:
        return {
            "action": "WAIT",
            "reward": 0.0,
            "filledShares": 0.0,
            "filledActionRows": len(filled),
            "positiveActionRows": len(positive),
        }
    return {
        "action": f"{best['side']}_{int(best['offset'])}",
        "reward": finite(best.get("mtm1sUsdt")),
        "filledShares": finite(best.get("filledShares5s")),
        "filledActionRows": len(filled),
        "positiveActionRows": len(positive),
    }


def summarize_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    checkpoints = len(rows)
    aggregate = sum(finite(row["freeOracle"]["reward"]) for row in rows)
    return {
        "checkpoints": checkpoints,
        "markets": len({int(row["marketId"]) for row in rows}),
        "freeOracleRewardMtm1sUsdt": aggregate,
        "freeOracleRewardPerCheckpoint": aggregate / checkpoints if checkpoints else None,
        "freeOracleActs": sum(row["freeOracle"]["action"] != "WAIT" for row in rows),
        "filledActionRows": sum(int(row["freeOracle"]["filledActionRows"]) for row in rows),
        "positiveActionRows": sum(int(row["freeOracle"]["positiveActionRows"]) for row in rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    market_ids = [int(value) for value in dataset.get("markets") or []]
    placements = load_placements(market_ids)
    audit_rows: list[dict[str, Any]] = []

    for raw_row in dataset.get("rows") or []:
        market_id = int(raw_row["marketId"])
        checkpoint_ms = int(raw_row["checkpointMs"])
        actions: list[dict[str, Any]] = []
        for raw_action in raw_row.get("actions") or []:
            side = str(raw_action.get("side") or "").upper()
            if side not in {"UP", "DOWN"}:
                continue
            actions.append(
                {
                    **raw_action,
                    "side": side,
                    "offset": int(raw_action["offset"]),
                }
            )

        future_1s = [
            row
            for row in placements.get(market_id, [])
            if checkpoint_ms < int(row["placementMs"]) <= checkpoint_ms + DIAGNOSTIC_HORIZON_MS
        ]
        future_2s = [
            row
            for row in placements.get(market_id, [])
            if checkpoint_ms < int(row["placementMs"]) <= checkpoint_ms + PRIMARY_HORIZON_MS
        ]
        shares_by_side = {
            side: sum(finite(row["shares"]) for row in future_2s if row["side"] == side)
            for side in ("UP", "DOWN")
        }
        if shares_by_side["UP"] > shares_by_side["DOWN"] + EPS:
            dominant_side = "UP"
        elif shares_by_side["DOWN"] > shares_by_side["UP"] + EPS:
            dominant_side = "DOWN"
        else:
            dominant_side = None

        up_actions = [action for action in actions if action["side"] == "UP"]
        down_actions = [action for action in actions if action["side"] == "DOWN"]
        side_oracles = {
            "UP": best_with_wait(up_actions),
            "DOWN": best_with_wait(down_actions),
        }
        fixed_by_side = {
            side: {
                str(offset): next(
                    (finite(action.get("mtm1sUsdt")) for action in actions if action["side"] == side and action["offset"] == offset),
                    0.0,
                )
                for offset in (0, 1, 2)
            }
            for side in ("UP", "DOWN")
        }
        audit_rows.append(
            {
                "marketId": market_id,
                "checkpointMs": checkpoint_ms,
                "secondsLeft": (raw_row.get("features") or {}).get("secondsLeft"),
                "targetPlacementsNext1s": len(future_1s),
                "targetPlacementsNext2s": len(future_2s),
                "targetSharesNext2s": shares_by_side,
                "targetDominantSideNext2s": dominant_side,
                "freeOracle": best_with_wait(actions),
                "sideOracles": side_oracles,
                "fixedRewardsBySide": fixed_by_side,
            }
        )

    target_rows = [row for row in audit_rows if int(row["targetPlacementsNext2s"]) > 0]
    control_rows = [row for row in audit_rows if int(row["targetPlacementsNext2s"]) == 0]
    dominant_rows = [row for row in target_rows if row["targetDominantSideNext2s"] in {"UP", "DOWN"}]
    target_oracle_reward = 0.0
    opposite_oracle_reward = 0.0
    fixed_rewards = {str(offset): 0.0 for offset in (0, 1, 2)}
    target_filled_action_rows = 0
    target_positive_action_rows = 0
    comparison = Counter()

    for row in dominant_rows:
        target_side = str(row["targetDominantSideNext2s"])
        opposite_side = "DOWN" if target_side == "UP" else "UP"
        target_reward = finite(row["sideOracles"][target_side]["reward"])
        opposite_reward = finite(row["sideOracles"][opposite_side]["reward"])
        target_oracle_reward += target_reward
        opposite_oracle_reward += opposite_reward
        target_filled_action_rows += int(row["sideOracles"][target_side]["filledActionRows"])
        target_positive_action_rows += int(row["sideOracles"][target_side]["positiveActionRows"])
        for offset in (0, 1, 2):
            fixed_rewards[str(offset)] += finite(row["fixedRewardsBySide"][target_side][str(offset)])
        if target_reward > opposite_reward + EPS:
            comparison["targetWins"] += 1
        elif opposite_reward > target_reward + EPS:
            comparison["oppositeWins"] += 1
        else:
            comparison["ties"] += 1

    target_summary = summarize_group(target_rows)
    control_summary = summarize_group(control_rows)
    best_fixed_offset, best_fixed_reward = max(fixed_rewards.items(), key=lambda item: item[1])
    sufficient_teacher = len(target_rows) >= 4
    sufficient_fills = target_filled_action_rows >= 2
    conditions = {
        "sufficientTargetCheckpoints": sufficient_teacher,
        "sufficientTargetSideFilledActionRows": sufficient_fills,
        "bestFixedTargetSideOffsetPositive": best_fixed_reward > EPS,
        "targetSideOracleBeatsOpposite": target_oracle_reward > opposite_oracle_reward + EPS,
        "targetFreeOraclePerCheckpointBeatsControl": (
            target_summary["freeOracleRewardPerCheckpoint"] is not None
            and control_summary["freeOracleRewardPerCheckpoint"] is not None
            and target_summary["freeOracleRewardPerCheckpoint"]
            > control_summary["freeOracleRewardPerCheckpoint"] + EPS
        ),
    }
    if not sufficient_teacher or not sufficient_fills:
        decision = "NEED_MORE_DATA"
    elif all(conditions.values()):
        decision = "KEEP_EXPAND_SMALL"
    else:
        decision = "REJECT"

    report = {
        "version": "TARGET_HFT_ACTIONPOINT_VALUE_PILOT_V1",
        "researchOnly": True,
        "dataset": str(args.dataset.resolve()),
        "preregistration": str((BASE / "target_hft_actionpoint_value_pilot_v1_preregistered.json").resolve()),
        "execution": {
            "engine": "HftBacktest",
            "marketData": "PREDICT_EXECUTION_TAPE_V1",
            "queueModel": "risk",
            "entryLatencyMs": int(dataset["entryLatencyMs"]),
            "responseLatencyMs": int(dataset["responseLatencyMs"]),
            "partialFill": True,
            "fillHorizonMs": 5000,
            "markoutHorizonAfterFillMs": 1000,
        },
        "targetTeacher": {
            "source": "maker_book_inference_v21_parent_lifecycles.placement_first_ms",
            "primaryHorizonMs": PRIMARY_HORIZON_MS,
            "diagnosticHorizonMs": DIAGNOSTIC_HORIZON_MS,
            "minimumConfidence": MIN_CONFIDENCE,
            "minimumPlacementCoverage": 1.0,
            "futureTargetActionRuntimeInput": False,
            "primaryImitationTarget": False,
        },
        "cohort": {
            "markets": market_ids,
            "marketCount": len(market_ids),
            "checkpoints": len(audit_rows),
            "targetLifecycleRows": {str(market_id): len(placements[market_id]) for market_id in market_ids},
        },
        "targetNext2s": target_summary,
        "noTargetNext2s": control_summary,
        "dominantSideAnalysis": {
            "checkpoints": len(dominant_rows),
            "targetSideOracleRewardMtm1sUsdt": target_oracle_reward,
            "oppositeSideOracleRewardMtm1sUsdt": opposite_oracle_reward,
            "targetSideFilledActionRows": target_filled_action_rows,
            "targetSidePositiveActionRows": target_positive_action_rows,
            "fixedTargetSideOffsetRewardsMtm1sUsdt": fixed_rewards,
            "bestFixedTargetSideOffset": int(best_fixed_offset),
            "bestFixedTargetSideRewardMtm1sUsdt": best_fixed_reward,
            **comparison,
        },
        "gateConditions": conditions,
        "decision": decision,
        "guards": {
            "dreamFillAllowed": False,
            "winnerSettlementPnlInput": False,
            "targetFutureActionRuntimeInput": False,
            "officialHftForwardTuning": False,
            "liveOrdersAffected": False,
        },
        "rows": audit_rows,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output.resolve()),
                "decision": decision,
                "targetNext2s": target_summary,
                "noTargetNext2s": control_summary,
                "dominantSideAnalysis": report["dominantSideAnalysis"],
                "gateConditions": conditions,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
