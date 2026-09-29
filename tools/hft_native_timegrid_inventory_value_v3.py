from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.predict_bot import unified_controller_paper_v2 as mod  # noqa: E402
from tools import collect_recent_execution_value_states_v0 as collector  # noqa: E402
from tools import hft_native_queue_regime_value_v1 as v1  # noqa: E402


BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
QTY = v1.QTY
GRID = v1.GRID
EPS = v1.EPS


def portfolio_at(fill_log: list[dict[str, Any]], checkpoint_ms: int) -> dict[str, float]:
    inventory = mod.Inventory()
    maker_fills: list[dict[str, Any]] = []
    for event in sorted(fill_log, key=lambda row: int(row.get("eventMs") or 0)):
        event_ms = int(event.get("eventMs") or 0)
        if event_ms > int(checkpoint_ms):
            break
        inventory.apply(
            {
                "event_ms": event_ms,
                "role": str(event["role"]),
                "side": str(event["side"]),
                "price": float(event["price"]),
                "shares": float(event["shares"]),
            }
        )
        if str(event.get("role")) == "MAKER":
            maker_fills.append(event)
    features = inventory.features(int(checkpoint_ms))
    features.pop("_combined_net", None)
    if maker_fills:
        features["last_maker_age_ms"] = int(checkpoint_ms) - max(
            int(event["eventMs"]) for event in maker_fills
        )
    else:
        features["last_maker_age_ms"] = math.nan
    for side, name in (("UP", "last_maker_up_age_ms"), ("DOWN", "last_maker_down_age_ms")):
        timestamps = [int(event["eventMs"]) for event in maker_fills if str(event["side"]) == side]
        features[name] = int(checkpoint_ms) - max(timestamps) if timestamps else math.nan
    for seconds in (1, 5, 10):
        recent = [
            event
            for event in maker_fills
            if int(event["eventMs"]) >= int(checkpoint_ms) - seconds * 1000
        ]
        features[f"maker_fills_{seconds}s"] = float(len(recent))
        features[f"maker_shares_{seconds}s"] = float(
            sum(float(event.get("shares") or 0.0) for event in recent)
        )
    return {name: v1.finite(features.get(name)) for name in v1.PORTFOLIO_FEATURES}


def load_rows(dataset_name: str, expected_ids: set[int]) -> pd.DataFrame:
    dataset = json.loads((BASE / dataset_name).read_text(encoding="utf-8"))
    dataset_ids = {int(value) for value in dataset.get("markets") or []}
    if dataset_ids != expected_ids:
        raise RuntimeError("time-grid dataset markets differ from preregistration")
    public_by_market: dict[int, Any] = {}
    execution_cache: dict[tuple[int, int], dict[str, Any]] = {}
    portfolio_cache: dict[tuple[int, int], dict[str, float]] = {}
    fill_logs: dict[int, list[dict[str, Any]]] = {}
    rows: list[dict[str, Any]] = []

    for timegrid_row in dataset.get("rows") or []:
        market_id = int(timegrid_row["marketId"])
        checkpoint_ms = int(timegrid_row["checkpointMs"])
        if market_id not in fill_logs:
            replay = collector.run_recovery(
                market_id,
                enable_intervention=False,
                passive_priority=False,
            )
            fill_logs[market_id] = list(replay.get("fillLog") or [])
            public_by_market[market_id] = v1.public_lookup(market_id)
        key = (market_id, checkpoint_ms)
        if key not in execution_cache:
            execution_cache[key] = v1.checkpoint_execution_state(market_id, checkpoint_ms)
            portfolio_cache[key] = portfolio_at(fill_logs[market_id], checkpoint_ms)
        execution = execution_cache[key]
        portfolio = portfolio_cache[key]
        public = public_by_market[market_id](checkpoint_ms)
        timegrid_features = timegrid_row.get("features") or {}

        for action in timegrid_row.get("actions") or []:
            if action.get("invalid"):
                continue
            side = str(action.get("side") or "").upper()
            if side not in {"UP", "DOWN"}:
                continue
            bid = v1.finite(timegrid_features.get("upBid") if side == "UP" else timegrid_features.get("downBid"))
            ask = v1.finite(timegrid_features.get("upAsk") if side == "UP" else timegrid_features.get("downAsk"))
            spread_ticks = (ask - bid) / GRID if math.isfinite(bid) and math.isfinite(ask) else np.nan
            row: dict[str, Any] = dict(portfolio)
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
            queue, regime = v1.queue_features(execution, side, float(action["price"]), spread_ticks)
            inventory_chunks = v1.finite(portfolio.get("combined_abs_net")) / QTY
            recent_fill_chunks = v1.finite(portfolio.get("maker_shares_5s")) / QTY
            queue["queue_x_inventory_chunks"] = queue["queue_ahead_chunks"] * inventory_chunks
            queue["queue_x_recent_fill_chunks"] = queue["queue_ahead_chunks"] * recent_fill_chunks
            row.update(queue)
            row["regime"] = regime

            filled_shares = float(action.get("filledShares5s") or 0.0)
            fill_price = v1.finite(action.get("fillPrice"))
            execution_price = float(fill_price) if math.isfinite(fill_price) else float(action["price"])
            mtm = float(action.get("mtm1sUsdt") or 0.0)
            floor_delta = (
                v1.post_floor_delta(portfolio, side, execution_price, filled_shares)
                if filled_shares > EPS
                else 0.0
            )
            row.update(
                {
                    "filled": float(filled_shares > EPS),
                    "filled_shares": filled_shares,
                    "mtm": mtm,
                    "markout_per_share": mtm / filled_shares if filled_shares > EPS else np.nan,
                    "delta_floor_realized": floor_delta,
                    "portfolio_reward": mtm + floor_delta,
                }
            )
            rows.append(row)
    return pd.DataFrame(rows)


def reward_decomposition(metrics: dict[str, Any], frame: pd.DataFrame) -> dict[str, Any]:
    chosen: list[dict[str, Any]] = []
    for choice in metrics["rows"]:
        action = str(choice["action"])
        if action == "WAIT":
            continue
        side, offset_text = action.split("_")
        selected = frame[
            (frame.market_id == int(choice["marketId"]))
            & (frame.checkpoint_ms == int(choice["checkpointMs"]))
            & (frame.side == side)
            & (frame.action_offset == float(offset_text))
        ]
        if len(selected) != 1:
            raise RuntimeError(f"cannot uniquely resolve chosen action: {choice}")
        row = selected.iloc[0]
        chosen.append(
            {
                "marketId": int(row.market_id),
                "checkpointMs": int(row.checkpoint_ms),
                "action": action,
                "filledShares5s": float(row.filled_shares),
                "mtm1sUsdt": float(row.mtm),
                "portfolioFloorDelta": float(row.delta_floor_realized),
                "portfolioReward": float(row.portfolio_reward),
                "predictedValue": float(choice["predictedValue"]),
                "oracleAction": str(choice["oracleAction"]),
                "oracleReward": float(choice["oracleReward"]),
            }
        )
    mtm = float(sum(row["mtm1sUsdt"] for row in chosen))
    floor_delta = float(sum(row["portfolioFloorDelta"] for row in chosen))
    reward = float(sum(row["portfolioReward"] for row in chosen))
    if abs(reward - float(metrics["policyReward"])) > 1e-8:
        raise RuntimeError("chosen reward decomposition differs from evaluation")
    return {
        "acts": len(chosen),
        "filledActs": sum(row["filledShares5s"] > EPS for row in chosen),
        "zeroFillActs": sum(row["filledShares5s"] <= EPS for row in chosen),
        "mtm1sUsdt": mtm,
        "portfolioFloorDelta": floor_delta,
        "portfolioReward": reward,
        "rows": chosen,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", default="hft_native_timegrid_inventory_chronology_v3_preregistered.json")
    parser.add_argument("--dataset", default="hft_native_timegrid_inventory_unused80_v3.json")
    parser.add_argument("--output", default="hft_native_timegrid_inventory_value_v3_report.json")
    args = parser.parse_args()

    contract = json.loads((BASE / args.contract).read_text(encoding="utf-8"))
    ids = {
        split: {int(row["marketId"]) for row in contract[split]}
        for split in ("train", "validation", "holdout")
    }
    all_ids = set().union(*ids.values())
    all_rows = load_rows(args.dataset, all_ids)
    frames = {
        split: all_rows[all_rows.market_id.isin(market_ids)].copy()
        for split, market_ids in ids.items()
    }
    if not (
        int(frames["train"].checkpoint_ms.max())
        < int(frames["validation"].checkpoint_ms.min())
        < int(frames["holdout"].checkpoint_ms.min())
    ):
        raise RuntimeError("chronological split is not strict")
    for split, expected_markets in (("train", 50), ("validation", 10), ("holdout", 20)):
        if frames[split].market_id.nunique() != expected_markets:
            raise RuntimeError(f"{split} market coverage mismatch")
        checkpoints = frames[split][["market_id", "checkpoint_ms"]].drop_duplicates()
        if len(checkpoints) != expected_markets * 3:
            raise RuntimeError(f"{split} checkpoint coverage mismatch")

    training = frames["train"]
    fill_model = v1.classifier()
    fill_model.fit(v1.matrix(training), training.filled)
    filled_training = training[training.filled > 0].copy()
    size_model = v1.regressor()
    size_model.fit(v1.matrix(filled_training), filled_training.filled_shares)
    markout_model = v1.regressor()
    markout_model.fit(
        v1.matrix(filled_training),
        filled_training.markout_per_share,
        sample_weight=filled_training.filled_shares,
    )
    evaluations = {
        split: v1.evaluate(split, frame, fill_model, size_model, markout_model, split == "holdout")
        for split, frame in frames.items()
    }
    decompositions = {
        split: reward_decomposition(metrics, frames[split])
        for split, metrics in evaluations.items()
    }
    holdout = evaluations["holdout"]
    if holdout["oracleReward"] <= EPS:
        decision = "NEED_MORE_DATA"
    elif holdout["policyReward"] > EPS:
        decision = "KEEP"
    else:
        decision = "REJECT"

    report = {
        "version": "HFT_NATIVE_TIMEGRID_INVENTORY_VALUE_V3",
        "researchOnly": True,
        "dreamFillAllowed": False,
        "winnerSettlementPnlRuntimeInput": False,
        "targetFutureActionRuntimeInput": False,
        "targetPlacementCheckpointSelection": False,
        "contract": args.contract,
        "dataset": args.dataset,
        "execution": contract["execution"],
        "checkpointRule": contract["checkpointRule"],
        "ownStateRule": contract["ownStateRule"],
        "representation": {
            "type": "strict-past static portfolio/public/queue state",
            "features": list(v1.FEATURES),
            "queueReactiveRawFlowFeatures": False,
        },
        "model": {
            "components": [
                "P(any fill within 5s)",
                "E(filled shares within 5s | fill)",
                "E(1s markout per filled share | fill)",
            ],
            "score": contract["score"],
            "waitValue": 0.0,
            "thresholdSweep": False,
            "rewardWeightSweep": False,
        },
        "training": {
            **v1.cohort_span(training),
            "actionRows": int(len(training)),
            "filledRows": int(training.filled.sum()),
            "fillRate": float(training.filled.mean()),
            "regimes": v1.regime_summary(training),
        },
        "evaluations": evaluations,
        "chosenRewardDecomposition": decompositions,
        "primaryHoldout": "holdout",
        "decision": decision,
    }
    output = BASE / args.output
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    compact_keys = {
        "fillAuc",
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
    }
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(output),
                "decision": decision,
                "evaluations": {
                    split: {key: value for key, value in metrics.items() if key in compact_keys}
                    for split, metrics in evaluations.items()
                },
                "rewardDecomposition": {
                    split: {key: value for key, value in breakdown.items() if key != "rows"}
                    for split, breakdown in decompositions.items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
