from __future__ import annotations

import argparse
import json
import math
import sys
from bisect import bisect_right
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.predict_bot import unified_controller_paper_v2 as controller  # noqa: E402
from tools import hftbacktest_execution_shift_audit_v0 as ex  # noqa: E402
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1  # noqa: E402
from tools.hftbacktest_r2_execution_school_v0 import load_public_snapshots  # noqa: E402


BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
QTY = float(controller.SHARES)
GRID = float(controller.GRID)
EPS = 1e-9

TRAIN = [
    ("recent_execution_placement_p01.json", "hft_native_action_sweep_p01_bothsides_v1.json"),
    ("recent_execution_placement_p02.json", "hft_native_action_sweep_p02_bothsides_v1.json"),
    ("recent_execution_placement_p03.json", "hft_native_action_sweep_p03a_bothsides_v1.json"),
    ("recent_execution_placement_p03b.json", "hft_native_action_sweep_p03b_bothsides_v1.json"),
    ("recent_execution_placement_p04.json", "hft_native_action_sweep_p04_bothsides_v1.json"),
]
EVAL = [
    ("openedP05", "recent_execution_placement_p05.json", "hft_native_action_sweep_p05_bothsides_v1.json", False),
    ("openedFreshA", "hft_native_freshA10_collector_v0.json", "hft_native_action_sweep_freshA10_bothsides_v1.json", False),
    ("openedFreshB", "hft_native_freshB10_collector_v0.json", "hft_native_action_sweep_freshB10_bothsides_v1.json", False),
    ("openedFreshC", "hft_native_freshC5_collector_v0.json", "hft_native_action_sweep_freshC5_bothsides_v1.json", False),
    ("untouchedNew6", "hft_native_new6_collector_20260823_v0.json", "hft_native_action_sweep_new6_bothsides_v1.json", True),
]

PORTFOLIO_FEATURES = [
    "maker_gross",
    "maker_net",
    "maker_abs_net",
    "maker_imbalance_ratio",
    "maker_paired_coverage",
    "taker_gross",
    "taker_net",
    "taker_abs_net",
    "taker_paired_coverage",
    "combined_gross",
    "combined_net",
    "combined_abs_net",
    "combined_imbalance_ratio",
    "combined_paired_coverage",
    "worst_case_floor",
    "best_case_pnl",
    "abs_payoff_gap",
    "last_maker_age_ms",
    "last_maker_up_age_ms",
    "last_maker_down_age_ms",
    "maker_fills_1s",
    "maker_fills_5s",
    "maker_fills_10s",
    "maker_shares_5s",
    "maker_shares_10s",
]

PUBLIC_FEATURES = [
    "seconds_left",
    "direction_toward_side",
    "bias_toward_side",
    "spot_strike_toward_side",
    "chainlink_strike_toward_side",
    "spot_ret1_toward_side",
    "spot_ret3_toward_side",
    "futures_ret1_toward_side",
    "futures_ret3_toward_side",
    "spot_queue_toward_side",
    "futures_queue_toward_side",
    "spot_taker1_toward_side",
    "futures_taker1_toward_side",
    "perp_spot_basis_signed",
]

QUEUE_FEATURES = [
    "native_price",
    "queue_ahead_shares",
    "queue_ahead_chunks",
    "distance_from_same_best_ticks",
    "same_top_shares",
    "opposite_top_shares",
    "same_depth_3ticks_shares",
    "same_book_levels",
    "top_imbalance_toward_action",
    "contra_trade_qty_1s",
    "contra_trade_qty_3s",
    "contra_trade_through_qty_1s",
    "contra_trade_through_qty_3s",
    "depth_events_1s",
    "depth_events_3s",
    "queue_x_spread",
    "queue_x_contra_flow_chunks_3s",
    "queue_x_inventory_chunks",
    "queue_x_recent_fill_chunks",
]

REGIME_FEATURES = [
    f"regime_{spread}_{queue}_{flow}"
    for spread in ("TIGHT", "WIDE")
    for queue in ("EMPTY", "ONE_CHUNK", "DEEP")
    for flow in ("QUIET", "ACTIVE")
]

FEATURES = [
    "side_is_up",
    "action_offset",
    "action_price",
    "current_bid",
    "current_ask",
    "current_spread_ticks",
    *PORTFOLIO_FEATURES,
    *PUBLIC_FEATURES,
    *QUEUE_FEATURES,
    *REGIME_FEATURES,
]


def finite(value: Any) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else np.nan
    except Exception:
        return np.nan


def post_floor_delta(state: dict[str, Any] | pd.Series, side: str, price: float, shares: float) -> float:
    q = max(0.0, float(shares))
    p = float(price)
    net = float(state.get("combined_net") or 0.0)
    floor = float(state.get("worst_case_floor") or 0.0)
    best = float(state.get("best_case_pnl") or floor)
    pup, pdn = (best, floor) if net >= 0 else (floor, best)
    if side == "UP":
        pup += q * (1.0 - p)
        pdn -= q * p
    else:
        pdn += q * (1.0 - p)
        pup -= q * p
    return min(pup, pdn) - floor


def public_lookup(market_id: int):
    snapshots = sorted(load_public_snapshots(int(market_id)), key=lambda row: int(row.get("sampledAtMs") or 0))
    timestamps = [int(row.get("sampledAtMs") or 0) for row in snapshots]

    def get(checkpoint_ms: int) -> dict[str, Any]:
        idx = bisect_right(timestamps, int(checkpoint_ms)) - 1
        return snapshots[idx] if idx >= 0 else {}

    return get


def public_features(snapshot: dict[str, Any], side: str) -> dict[str, float]:
    sign = 1.0 if side == "UP" else -1.0
    bias = str(snapshot.get("directionBias") or "NEUTRAL").upper()
    bias_toward_side = 1.0 if bias == side else -1.0 if bias in {"UP", "DOWN"} else 0.0
    return {
        "seconds_left": finite(snapshot.get("secondsLeft")),
        "direction_toward_side": finite(snapshot.get("directionScore")) * sign,
        "bias_toward_side": bias_toward_side,
        "spot_strike_toward_side": finite(snapshot.get("spotMinusStrikeBps")) * sign,
        "chainlink_strike_toward_side": finite(snapshot.get("chainlinkMinusStrikeBps")) * sign,
        "spot_ret1_toward_side": finite(snapshot.get("spotReturn1sBps")) * sign,
        "spot_ret3_toward_side": finite(snapshot.get("spotReturn3sBps")) * sign,
        "futures_ret1_toward_side": finite(snapshot.get("futuresReturn1sBps")) * sign,
        "futures_ret3_toward_side": finite(snapshot.get("futuresReturn3sBps")) * sign,
        "spot_queue_toward_side": finite(snapshot.get("spotQueueImbalance")) * sign,
        "futures_queue_toward_side": finite(snapshot.get("futuresQueueImbalance")) * sign,
        "spot_taker1_toward_side": finite(snapshot.get("spotTakerImbalance1s")) * sign,
        "futures_taker1_toward_side": finite(snapshot.get("futuresTakerImbalance1s")) * sign,
        "perp_spot_basis_signed": finite(snapshot.get("perpSpotBasisBps")) * sign,
    }


def checkpoint_execution_state(market_id: int, checkpoint_ms: int) -> dict[str, Any]:
    events, _, meta = tape_v1.build_archive_events(int(market_id), trade_offset="mid")
    bt = ex.new_bt(events, entry_latency_ms=1092, response_latency_ms=273, queue_model="risk")
    ex.initialize_bt(bt)
    try:
        ex.advance_to(bt, int(checkpoint_ms))
        depth = bt.depth(0)
        snapshot = depth.snapshot()
        try:
            bids: dict[float, float] = {}
            asks: dict[float, float] = {}
            for item in snapshot:
                event = int(item["ev"])
                price = round(float(item["px"]), 12)
                qty = float(item["qty"])
                if qty <= EPS:
                    continue
                if event & int(ex.BUY_EVENT):
                    bids[price] = qty
                elif event & int(ex.SELL_EVENT):
                    asks[price] = qty
        finally:
            depth.snapshot_free(snapshot)
    finally:
        bt.close()

    checkpoint_ns = int(checkpoint_ms) * 1_000_000
    recent: list[dict[str, float | int]] = []
    for item in events:
        local_ns = int(item["local_ts"])
        if local_ns > checkpoint_ns or local_ns <= checkpoint_ns - 3_000_000_000:
            continue
        recent.append(
            {
                "event": int(item["ev"]),
                "price": float(item["px"]),
                "qty": float(item["qty"]),
                "age_ms": (checkpoint_ns - local_ns) / 1_000_000.0,
            }
        )
    return {"bids": bids, "asks": asks, "recent": recent, "feed": meta}


def queue_features(execution_state: dict[str, Any], side: str, action_price: float, spread_ticks: float) -> tuple[dict[str, float], str]:
    native_side, native_price = ex.native_order(side, float(action_price))
    native_price = round(float(native_price), 12)
    bids = execution_state["bids"]
    asks = execution_state["asks"]
    same = bids if native_side == "BUY" else asks
    opposite = asks if native_side == "BUY" else bids
    same_best = max(bids) if native_side == "BUY" and bids else min(asks) if native_side == "SELL" and asks else np.nan
    opposite_best = min(asks) if native_side == "BUY" and asks else max(bids) if native_side == "SELL" and bids else np.nan
    queue_ahead = float(same.get(native_price, 0.0))
    same_top = float(same.get(round(float(same_best), 12), 0.0)) if math.isfinite(float(same_best)) else 0.0
    opposite_top = float(opposite.get(round(float(opposite_best), 12), 0.0)) if math.isfinite(float(opposite_best)) else 0.0
    if native_side == "BUY":
        same_depth_3ticks = sum(q for p, q in bids.items() if native_price - EPS <= p <= native_price + 3.0 * GRID + EPS)
        distance_ticks = (float(same_best) - native_price) / GRID if math.isfinite(float(same_best)) else np.nan
        contra_flag = int(ex.SELL_EVENT)
    else:
        same_depth_3ticks = sum(q for p, q in asks.items() if native_price - 3.0 * GRID - EPS <= p <= native_price + EPS)
        distance_ticks = (native_price - float(same_best)) / GRID if math.isfinite(float(same_best)) else np.nan
        contra_flag = int(ex.BUY_EVENT)

    contra_1s = contra_3s = through_1s = through_3s = 0.0
    depth_events_1s = depth_events_3s = 0
    for item in execution_state["recent"]:
        event = int(item["event"])
        age = float(item["age_ms"])
        if event & int(ex.DEPTH_EVENT):
            depth_events_3s += 1
            if age <= 1000.0:
                depth_events_1s += 1
        if not (event & int(ex.TRADE_EVENT)) or not (event & contra_flag):
            continue
        qty = float(item["qty"])
        trade_price = float(item["price"])
        contra_3s += qty
        at_or_through = trade_price <= native_price + EPS if native_side == "BUY" else trade_price >= native_price - EPS
        if at_or_through:
            through_3s += qty
        if age <= 1000.0:
            contra_1s += qty
            if at_or_through:
                through_1s += qty

    denom = same_top + opposite_top
    top_imbalance = (same_top - opposite_top) / denom if denom > EPS else 0.0
    queue_chunks = queue_ahead / QTY
    spread_name = "TIGHT" if math.isfinite(spread_ticks) and spread_ticks <= 1.5 else "WIDE"
    queue_name = "EMPTY" if queue_ahead <= EPS else "ONE_CHUNK" if queue_ahead <= QTY + EPS else "DEEP"
    flow_name = "ACTIVE" if contra_3s > EPS else "QUIET"
    regime = f"{spread_name}_{queue_name}_{flow_name}"
    result = {
        "native_price": native_price,
        "queue_ahead_shares": queue_ahead,
        "queue_ahead_chunks": queue_chunks,
        "distance_from_same_best_ticks": distance_ticks,
        "same_top_shares": same_top,
        "opposite_top_shares": opposite_top,
        "same_depth_3ticks_shares": float(same_depth_3ticks),
        "same_book_levels": float(len(same)),
        "top_imbalance_toward_action": top_imbalance,
        "contra_trade_qty_1s": contra_1s,
        "contra_trade_qty_3s": contra_3s,
        "contra_trade_through_qty_1s": through_1s,
        "contra_trade_through_qty_3s": through_3s,
        "depth_events_1s": float(depth_events_1s),
        "depth_events_3s": float(depth_events_3s),
        "queue_x_spread": queue_chunks * finite(spread_ticks),
        "queue_x_contra_flow_chunks_3s": queue_chunks * (contra_3s / QTY),
    }
    for name in REGIME_FEATURES:
        result[name] = float(name == f"regime_{regime}")
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
            public_by_market[market_id] = public_lookup(market_id)
        if (market_id, checkpoint_ms) not in checkpoint_cache:
            checkpoint_cache[(market_id, checkpoint_ms)] = checkpoint_execution_state(market_id, checkpoint_ms)
        public = public_by_market[market_id](checkpoint_ms)
        execution = checkpoint_cache[(market_id, checkpoint_ms)]
        for action in sweep_row.get("actions") or []:
            if action.get("invalid"):
                continue
            side = str(action.get("side") or state.get("side") or "").upper()
            if side not in {"UP", "DOWN"}:
                continue
            bid = finite(sweep_row.get("upBid") if side == "UP" else sweep_row.get("downBid"))
            ask = finite(sweep_row.get("upAsk") if side == "UP" else sweep_row.get("downAsk"))
            spread_ticks = (ask - bid) / GRID if math.isfinite(bid) and math.isfinite(ask) else np.nan
            row = {name: finite(state.get(name)) for name in PORTFOLIO_FEATURES}
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
            row.update(public_features(public, side))
            qfeatures, regime = queue_features(execution, side, float(action["price"]), spread_ticks)
            inventory_chunks = finite(state.get("combined_abs_net")) / QTY
            recent_fill_chunks = finite(state.get("maker_shares_5s")) / QTY
            qfeatures["queue_x_inventory_chunks"] = qfeatures["queue_ahead_chunks"] * inventory_chunks
            qfeatures["queue_x_recent_fill_chunks"] = qfeatures["queue_ahead_chunks"] * recent_fill_chunks
            row.update(qfeatures)
            row["regime"] = regime

            filled_shares = float(action.get("filledShares5s") or 0.0)
            fill_price = finite(action.get("fillPrice"))
            execution_price = float(fill_price) if math.isfinite(fill_price) else float(action["price"])
            mtm = float(action.get("mtm1sUsdt") or 0.0)
            floor_realized = post_floor_delta(state, side, execution_price, filled_shares) if filled_shares > EPS else 0.0
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


def classifier() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        max_depth=3,
        learning_rate=0.045,
        max_iter=180,
        l2_regularization=5.0,
        min_samples_leaf=12,
        random_state=20260823,
    )


def regressor() -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        max_depth=3,
        learning_rate=0.045,
        max_iter=180,
        l2_regularization=5.0,
        min_samples_leaf=12,
        random_state=20260823,
    )


def cohort_span(frame: pd.DataFrame) -> dict[str, Any]:
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


def evaluate(
    name: str,
    frame: pd.DataFrame,
    fill_model: HistGradientBoostingClassifier,
    size_model: HistGradientBoostingRegressor,
    markout_model: HistGradientBoostingRegressor,
    untouched: bool,
) -> dict[str, Any]:
    scored = frame.copy()
    xx = matrix(scored)
    scored["pfill"] = fill_model.predict_proba(xx)[:, 1]
    scored["pred_filled_shares_if_fill"] = np.clip(size_model.predict(xx), 0.0, QTY)
    scored["pred_markout_per_share_if_fill"] = markout_model.predict(xx)
    scored["pred_floor_delta_if_fill"] = [
        post_floor_delta(row, str(row.side), float(row.action_price), float(row.pred_filled_shares_if_fill))
        for _, row in scored.iterrows()
    ]
    scored["score"] = scored.pfill * (
        scored.pred_filled_shares_if_fill * scored.pred_markout_per_share_if_fill + scored.pred_floor_delta_if_fill
    )

    metrics: dict[str, Any] = {
        "name": name,
        "untouchedAtPreregistration": bool(untouched),
        "actionRows": int(len(scored)),
        "fillRate": float(scored.filled.mean()),
        "span": cohort_span(scored),
        "regimes": regime_summary(scored),
    }
    try:
        metrics["fillAuc"] = float(roc_auc_score(scored.filled, scored.pfill))
    except Exception:
        metrics["fillAuc"] = None
    filled = scored[scored.filled > 0].copy()
    if not filled.empty:
        metrics["filledSharesMaeConditional"] = float(
            mean_absolute_error(filled.filled_shares, filled.pred_filled_shares_if_fill)
        )
        metrics["markoutPerShareMaeConditional"] = float(
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
    reward = float(sum(row["reward"] for row in choices))
    metrics.update(
        {
            "checkpoints": len(choices),
            "oracleReward": oracle,
            "oracleActs": sum(row["oracleAction"] != "WAIT" for row in choices),
            "oracleActRate": sum(row["oracleAction"] != "WAIT" for row in choices) / max(1, len(choices)),
            "policyReward": reward,
            "policyActs": len(acts),
            "policyActRate": len(acts) / max(1, len(choices)),
            "waits": len(choices) - len(acts),
            "positiveActs": sum(row["reward"] > EPS for row in acts),
            "negativeActs": sum(row["reward"] < -EPS for row in acts),
            "zeroActs": sum(abs(row["reward"]) <= EPS for row in acts),
            "oracleCapture": reward / oracle if oracle > EPS else None,
            "rows": choices,
        }
    )
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="hft_native_queue_regime_value_v1_report.json")
    args = parser.parse_args()

    training = pd.concat([load_rows(collector, sweep) for collector, sweep in TRAIN], ignore_index=True)
    fill_model = classifier()
    fill_model.fit(matrix(training), training.filled)
    filled_training = training[training.filled > 0].copy()
    size_model = regressor()
    size_model.fit(matrix(filled_training), filled_training.filled_shares)
    markout_model = regressor()
    markout_model.fit(
        matrix(filled_training),
        filled_training.markout_per_share,
        sample_weight=filled_training.filled_shares,
    )

    evaluations: dict[str, Any] = {}
    for name, collector, sweep, untouched in EVAL:
        frame = load_rows(collector, sweep)
        evaluations[name] = evaluate(name, frame, fill_model, size_model, markout_model, untouched)

    holdout = evaluations["untouchedNew6"]
    if holdout["oracleReward"] <= EPS:
        decision = "NEED_MORE_DATA"
    elif holdout["policyReward"] > EPS:
        decision = "KEEP"
    else:
        decision = "REJECT"
    report = {
        "version": "HFT_NATIVE_QUEUE_REGIME_VALUE_V1",
        "researchOnly": True,
        "dreamFillAllowed": False,
        "winnerSettlementPnlRuntimeInput": False,
        "targetFutureActionRuntimeInput": False,
        "preregistration": "hft_native_queue_regime_value_v1_preregistered.json",
        "execution": {
            "engine": "HftBacktest",
            "marketData": "PREDICT_EXECUTION_TAPE_V1",
            "queueModel": "risk",
            "entryLatencyMs": 1092,
            "responseLatencyMs": 273,
            "partialFill": True,
            "markout": "1s post-fill public midpoint from actual simulated execution price",
        },
        "training": {
            **cohort_span(training),
            "actionRows": int(len(training)),
            "filledRows": int(training.filled.sum()),
            "fillRate": float(training.filled.mean()),
            "features": FEATURES,
            "regimes": regime_summary(training),
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
        "evaluations": evaluations,
        "primaryHoldout": "untouchedNew6",
        "decision": decision,
    }
    output = BASE / args.output
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    compact = {
        name: {
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
        for name, metrics in evaluations.items()
    }
    print(json.dumps({"ok": True, "report": str(output), "decision": decision, "evaluations": compact}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
