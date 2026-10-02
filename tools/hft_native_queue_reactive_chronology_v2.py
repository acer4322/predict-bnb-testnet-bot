from __future__ import annotations

import argparse
import json
import math
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.predict_bot.execution_tape_archive_v1 import load_archive  # noqa: E402
from tools import hft_native_queue_regime_value_v1 as v1  # noqa: E402
from tools import hftbacktest_execution_shift_audit_v0 as ex  # noqa: E402


BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
TAPES = ROOT / "data" / "execution_tape_v1" / "markets"
QTY = v1.QTY
GRID = v1.GRID
EPS = v1.EPS

FLOW_FEATURES = [
    "action_add_qty_250ms",
    "action_remove_qty_250ms",
    "action_add_qty_1s",
    "action_remove_qty_1s",
    "action_add_qty_3s",
    "action_remove_qty_3s",
    "action_events_1s",
    "action_events_3s",
    "same_near_add_qty_1s",
    "same_near_remove_qty_1s",
    "same_near_add_qty_3s",
    "same_near_remove_qty_3s",
    "opposite_near_add_qty_1s",
    "opposite_near_remove_qty_1s",
    "opposite_near_add_qty_3s",
    "opposite_near_remove_qty_3s",
    "near_max_abs_delta_3s",
    "near_large_delta_events_3s",
    "action_net_flow_chunks_3s",
    "same_near_net_flow_chunks_3s",
    "opposite_near_net_flow_chunks_3s",
    "queue_depletion_pressure_3s",
    "queue_clearance_proxy_s",
]
FLOW_REGIME_FEATURES = [
    "flow_regime_QUIET",
    "flow_regime_DEPLETING",
    "flow_regime_BUILDING",
]
FEATURES = [*v1.FEATURES, *FLOW_FEATURES, *FLOW_REGIME_FEATURES]


@lru_cache(maxsize=4)
def raw_updates(market_id: int) -> list[Any]:
    tape = load_archive(TAPES / f"{int(market_id)}.json.xz")
    return list(tape.get("updates") or [])


def flow_features(
    market_id: int,
    checkpoint_ms: int,
    execution_state: dict[str, Any],
    side: str,
    action_price: float,
    queue_ahead: float,
) -> tuple[dict[str, float], str]:
    native_side, native_price = ex.native_order(side, float(action_price))
    native_price = round(float(native_price), 12)
    bids = execution_state["bids"]
    asks = execution_state["asks"]
    opposite_best = min(asks) if native_side == "BUY" and asks else max(bids) if native_side == "SELL" and bids else np.nan
    result = {name: 0.0 for name in FLOW_FEATURES}

    def same_near(price: float) -> bool:
        if native_side == "BUY":
            return native_price - EPS <= price <= native_price + 3.0 * GRID + EPS
        return native_price - 3.0 * GRID - EPS <= price <= native_price + EPS

    def opposite_near(price: float) -> bool:
        if not math.isfinite(float(opposite_best)):
            return False
        if native_side == "BUY":
            return float(opposite_best) - EPS <= price <= float(opposite_best) + 3.0 * GRID + EPS
        return float(opposite_best) - 3.0 * GRID - EPS <= price <= float(opposite_best) + EPS

    for update in raw_updates(int(market_id)):
        received_ms = int(update[1])
        age_ms = int(checkpoint_ms) - received_ms
        if age_ms < 0 or age_ms > 3000:
            continue
        changes = update[6] or {}
        for book_side in ("bids", "asks"):
            is_same = (native_side == "BUY" and book_side == "bids") or (native_side == "SELL" and book_side == "asks")
            for item in changes.get(book_side, []) or []:
                price, _before, _after, delta = map(float, item)
                add = max(0.0, delta)
                remove = max(0.0, -delta)
                exact = is_same and abs(price - native_price) <= 1e-8
                near_same = is_same and same_near(price)
                near_opposite = (not is_same) and opposite_near(price)
                if exact:
                    result["action_add_qty_3s"] += add
                    result["action_remove_qty_3s"] += remove
                    result["action_events_3s"] += 1.0
                    if age_ms <= 1000:
                        result["action_add_qty_1s"] += add
                        result["action_remove_qty_1s"] += remove
                        result["action_events_1s"] += 1.0
                    if age_ms <= 250:
                        result["action_add_qty_250ms"] += add
                        result["action_remove_qty_250ms"] += remove
                if near_same:
                    result["same_near_add_qty_3s"] += add
                    result["same_near_remove_qty_3s"] += remove
                    result["near_max_abs_delta_3s"] = max(result["near_max_abs_delta_3s"], abs(delta))
                    if abs(delta) >= QTY:
                        result["near_large_delta_events_3s"] += 1.0
                    if age_ms <= 1000:
                        result["same_near_add_qty_1s"] += add
                        result["same_near_remove_qty_1s"] += remove
                if near_opposite:
                    result["opposite_near_add_qty_3s"] += add
                    result["opposite_near_remove_qty_3s"] += remove
                    result["near_max_abs_delta_3s"] = max(result["near_max_abs_delta_3s"], abs(delta))
                    if abs(delta) >= QTY:
                        result["near_large_delta_events_3s"] += 1.0
                    if age_ms <= 1000:
                        result["opposite_near_add_qty_1s"] += add
                        result["opposite_near_remove_qty_1s"] += remove

    action_net = result["action_add_qty_3s"] - result["action_remove_qty_3s"]
    same_net = result["same_near_add_qty_3s"] - result["same_near_remove_qty_3s"]
    opposite_net = result["opposite_near_add_qty_3s"] - result["opposite_near_remove_qty_3s"]
    result["action_net_flow_chunks_3s"] = action_net / QTY
    result["same_near_net_flow_chunks_3s"] = same_net / QTY
    result["opposite_near_net_flow_chunks_3s"] = opposite_net / QTY
    result["queue_depletion_pressure_3s"] = (-action_net) / (float(queue_ahead) + QTY)
    remove_rate = result["action_remove_qty_3s"] / 3.0
    result["queue_clearance_proxy_s"] = float(queue_ahead) / remove_rate if remove_rate > EPS else np.nan

    if result["action_events_3s"] <= EPS:
        regime = "QUIET"
    elif result["action_remove_qty_3s"] > result["action_add_qty_3s"]:
        regime = "DEPLETING"
    else:
        regime = "BUILDING"
    for name in FLOW_REGIME_FEATURES:
        result[name] = float(name == f"flow_regime_{regime}")
    return result, regime


def load_rows(collector_name: str, sweep_name: str) -> pd.DataFrame:
    collector = json.loads((BASE / collector_name).read_text(encoding="utf-8"))
    sweep = json.loads((BASE / sweep_name).read_text(encoding="utf-8"))
    collector_by_key: dict[tuple[int, int], dict[str, Any]] = {}
    for row in collector.get("placementRows") or []:
        collector_by_key.setdefault((int(row["market_id"]), int(row["checkpoint_ms"])), row)

    public_by_market: dict[int, Any] = {}
    checkpoint_cache: dict[tuple[int, int], dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for sweep_row in sweep.get("rows") or []:
        market_id = int(sweep_row["marketId"])
        checkpoint_ms = int(sweep_row["checkpointMs"])
        state = collector_by_key.get((market_id, checkpoint_ms))
        if state is None:
            continue
        if market_id not in public_by_market:
            public_by_market[market_id] = v1.public_lookup(market_id)
        if (market_id, checkpoint_ms) not in checkpoint_cache:
            checkpoint_cache[(market_id, checkpoint_ms)] = v1.checkpoint_execution_state(market_id, checkpoint_ms)
        public = public_by_market[market_id](checkpoint_ms)
        execution = checkpoint_cache[(market_id, checkpoint_ms)]
        for action in sweep_row.get("actions") or []:
            if action.get("invalid"):
                continue
            side = str(action.get("side") or state.get("side") or "").upper()
            if side not in {"UP", "DOWN"}:
                continue
            bid = v1.finite(sweep_row.get("upBid") if side == "UP" else sweep_row.get("downBid"))
            ask = v1.finite(sweep_row.get("upAsk") if side == "UP" else sweep_row.get("downAsk"))
            spread_ticks = (ask - bid) / GRID if math.isfinite(bid) and math.isfinite(ask) else np.nan
            row = {name: v1.finite(state.get(name)) for name in v1.PORTFOLIO_FEATURES}
            row.update(
                {
                    "market_id": market_id,
                    "checkpoint_ms": checkpoint_ms,
                    "side": side,
                    "side_is_up": float(side == "UP"),
                    "action_offset": float(action["offset"]),
                    "action_price": float(action["price"]),
                    "current_bid": bid,
                    "current_ask": ask,
                    "current_spread_ticks": spread_ticks,
                }
            )
            row.update(v1.public_features(public, side))
            qfeatures, static_regime = v1.queue_features(execution, side, float(action["price"]), spread_ticks)
            inventory_chunks = v1.finite(state.get("combined_abs_net")) / QTY
            recent_fill_chunks = v1.finite(state.get("maker_shares_5s")) / QTY
            qfeatures["queue_x_inventory_chunks"] = qfeatures["queue_ahead_chunks"] * inventory_chunks
            qfeatures["queue_x_recent_fill_chunks"] = qfeatures["queue_ahead_chunks"] * recent_fill_chunks
            row.update(qfeatures)
            flow, flow_regime = flow_features(
                market_id,
                checkpoint_ms,
                execution,
                side,
                float(action["price"]),
                float(qfeatures["queue_ahead_shares"]),
            )
            row.update(flow)
            row["regime"] = f"{static_regime}_{flow_regime}"

            filled_shares = float(action.get("filledShares5s") or 0.0)
            fill_price = v1.finite(action.get("fillPrice"))
            execution_price = float(fill_price) if math.isfinite(fill_price) else float(action["price"])
            mtm = float(action.get("mtm1sUsdt") or 0.0)
            floor_realized = v1.post_floor_delta(state, side, execution_price, filled_shares) if filled_shares > EPS else 0.0
            row.update(
                {
                    "filled": float(filled_shares > EPS),
                    "filled_shares": filled_shares,
                    "mtm": mtm,
                    "markout_per_share": mtm / filled_shares if filled_shares > EPS else np.nan,
                    "delta_floor_realized": floor_realized,
                    "portfolio_reward": mtm + floor_realized,
                }
            )
            rows.append(row)
    return pd.DataFrame(rows)


def matrix(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[FEATURES].replace([np.inf, -np.inf], np.nan)


def span(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "markets": int(frame.market_id.nunique()),
        "marketIds": sorted(int(value) for value in frame.market_id.unique()),
        "checkpoints": int(frame[["market_id", "checkpoint_ms"]].drop_duplicates().shape[0]),
        "minCheckpointMs": int(frame.checkpoint_ms.min()),
        "maxCheckpointMs": int(frame.checkpoint_ms.max()),
    }


def regime_summary(frame: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, group in frame.groupby("regime"):
        result[str(name)] = {
            "actionRows": int(len(group)),
            "filledRows": int(group.filled.sum()),
            "fillRate": float(group.filled.mean()),
            "positiveRewardRows": int((group.portfolio_reward > EPS).sum()),
            "negativeRewardRows": int((group.portfolio_reward < -EPS).sum()),
            "reward": float(group.portfolio_reward.sum()),
        }
    return result


def evaluate(name: str, frame: pd.DataFrame, fill_model: Any, size_model: Any, markout_model: Any) -> dict[str, Any]:
    scored = frame.copy()
    xx = matrix(scored)
    scored["pfill"] = fill_model.predict_proba(xx)[:, 1]
    scored["pred_filled_shares_if_fill"] = np.clip(size_model.predict(xx), 0.0, QTY)
    scored["pred_markout_per_share_if_fill"] = markout_model.predict(xx)
    scored["pred_floor_delta_if_fill"] = [
        v1.post_floor_delta(row, str(row.side), float(row.action_price), float(row.pred_filled_shares_if_fill))
        for _, row in scored.iterrows()
    ]
    scored["score"] = scored.pfill * (
        scored.pred_filled_shares_if_fill * scored.pred_markout_per_share_if_fill + scored.pred_floor_delta_if_fill
    )
    result: dict[str, Any] = {
        "name": name,
        "actionRows": int(len(scored)),
        "fillRate": float(scored.filled.mean()),
        "span": span(scored),
        "regimes": regime_summary(scored),
    }
    try:
        result["fillAuc"] = float(roc_auc_score(scored.filled, scored.pfill))
    except Exception:
        result["fillAuc"] = None
    filled = scored[scored.filled > 0]
    if not filled.empty:
        result["filledSharesMaeConditional"] = float(
            mean_absolute_error(filled.filled_shares, filled.pred_filled_shares_if_fill)
        )
        result["markoutPerShareMaeConditional"] = float(
            mean_absolute_error(filled.markout_per_share, filled.pred_markout_per_share_if_fill)
        )

    choices: list[dict[str, Any]] = []
    for (market_id, checkpoint_ms), group in scored.groupby(["market_id", "checkpoint_ms"], sort=True):
        oracle_idx = group.portfolio_reward.idxmax()
        oracle_reward = max(0.0, float(group.loc[oracle_idx, "portfolio_reward"]))
        best_idx = group.score.idxmax()
        predicted_value = float(group.loc[best_idx, "score"])
        if predicted_value > 0.0:
            chosen = group.loc[best_idx]
            action = f"{chosen.side}_{int(chosen.action_offset)}"
            reward = float(chosen.portfolio_reward)
            regime = str(chosen.regime)
        else:
            action = "WAIT"
            reward = 0.0
            regime = None
        choices.append(
            {
                "marketId": int(market_id),
                "checkpointMs": int(checkpoint_ms),
                "oracleReward": oracle_reward,
                "oracleAction": (
                    f"{group.loc[oracle_idx, 'side']}_{int(group.loc[oracle_idx, 'action_offset'])}"
                    if oracle_reward > 0.0
                    else "WAIT"
                ),
                "action": action,
                "predictedValue": predicted_value,
                "reward": reward,
                "regime": regime,
            }
        )
    acts = [row for row in choices if row["action"] != "WAIT"]
    oracle = float(sum(row["oracleReward"] for row in choices))
    policy_reward = float(sum(row["reward"] for row in choices))
    result.update(
        {
            "checkpoints": len(choices),
            "oracleReward": oracle,
            "oracleActs": sum(row["oracleAction"] != "WAIT" for row in choices),
            "oracleActRate": sum(row["oracleAction"] != "WAIT" for row in choices) / max(1, len(choices)),
            "policyReward": policy_reward,
            "policyActs": len(acts),
            "policyActRate": len(acts) / max(1, len(choices)),
            "waits": len(choices) - len(acts),
            "positiveActs": sum(row["reward"] > EPS for row in acts),
            "negativeActs": sum(row["reward"] < -EPS for row in acts),
            "zeroActs": sum(abs(row["reward"]) <= EPS for row in acts),
            "oracleCapture": policy_reward / oracle if oracle > EPS else None,
            "rows": choices,
        }
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", default="hft_native_unused_chronology_v2_preregistered.json")
    parser.add_argument("--collector", default="hft_native_unused120_collector_v2.json")
    parser.add_argument("--sweep", default="hft_native_unused120_bothsides_v2.json")
    parser.add_argument("--output", default="hft_native_queue_reactive_chronology_v2_report.json")
    args = parser.parse_args()

    contract = json.loads((BASE / args.contract).read_text(encoding="utf-8"))
    all_rows = load_rows(args.collector, args.sweep)
    ids = {
        split: {int(row["marketId"]) for row in contract[split]}
        for split in ("train", "validation", "holdout")
    }
    frames = {
        split: all_rows[all_rows.market_id.isin(market_ids)].copy()
        for split, market_ids in ids.items()
    }
    training = frames["train"]
    fill_model = v1.classifier()
    fill_model.fit(matrix(training), training.filled)
    filled_training = training[training.filled > 0].copy()
    size_model = v1.regressor()
    size_model.fit(matrix(filled_training), filled_training.filled_shares)
    markout_model = v1.regressor()
    markout_model.fit(
        matrix(filled_training),
        filled_training.markout_per_share,
        sample_weight=filled_training.filled_shares,
    )
    evaluations = {
        split: evaluate(split, frame, fill_model, size_model, markout_model)
        for split, frame in frames.items()
    }
    holdout = evaluations["holdout"]
    if holdout["oracleReward"] <= EPS:
        decision = "NEED_MORE_DATA"
    elif holdout["policyReward"] > EPS:
        decision = "KEEP"
    else:
        decision = "REJECT"
    report = {
        "version": "HFT_NATIVE_QUEUE_REACTIVE_CHRONOLOGY_V2",
        "researchOnly": True,
        "dreamFillAllowed": False,
        "winnerSettlementPnlRuntimeInput": False,
        "targetFutureActionRuntimeInput": False,
        "contract": args.contract,
        "execution": {
            "engine": "HftBacktest",
            "marketData": "PREDICT_EXECUTION_TAPE_V1",
            "queueModel": "risk",
            "entryLatencyMs": 1092,
            "responseLatencyMs": 273,
            "partialFill": True,
        },
        "representation": {
            "type": "MDQR-lite strict-past queue-reactive flow state",
            "features": FEATURES,
            "flowFeatures": FLOW_FEATURES,
            "neuralLobEncoder": False,
            "reason": "120-market sample is used for interpretable point-process-style state; Predict.fun order flow can later pretrain a larger encoder.",
        },
        "model": {
            "components": [
                "P(any fill within 5s)",
                "E(filled shares within 5s | fill)",
                "E(1s markout per filled share | fill)",
            ],
            "score": "P(fill) * (E(shares|fill) * E(markout/share|fill) + deterministic floor delta at E(shares|fill))",
            "waitValue": 0.0,
            "thresholdSweep": False,
            "rewardWeightSweep": False,
        },
        "training": {
            **span(training),
            "actionRows": int(len(training)),
            "filledRows": int(training.filled.sum()),
            "fillRate": float(training.filled.mean()),
            "regimes": regime_summary(training),
        },
        "evaluations": evaluations,
        "primaryHoldout": "holdout",
        "decision": decision,
    }
    output = BASE / args.output
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    compact = {
        split: {
            key: value
            for key, value in metrics.items()
            if key
            in {
                "checkpoints",
                "oracleReward",
                "oracleActs",
                "oracleActRate",
                "policyReward",
                "policyActs",
                "policyActRate",
                "waits",
                "positiveActs",
                "negativeActs",
                "zeroActs",
                "fillAuc",
            }
        }
        for split, metrics in evaluations.items()
    }
    print(json.dumps({"ok": True, "report": str(output), "decision": decision, "evaluations": compact}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
