from __future__ import annotations

import bisect
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score, roc_auc_score, average_precision_score, log_loss

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hftbacktest_r2_execution_school_v0 import STRATEGY_DB, VERSION
from src.predict_bot import unified_controller_paper_v2 as mod

OUT = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
BOOK_DB = ROOT / "data/wallet_maker_book_inference.db"
SPLIT = OUT / "r2_pending_management_fresh_split_v1.json"
DATA = OUT / "passive_queue_selection_v0_dataset.csv"
ART = OUT / "passive_queue_selection_v0.joblib"
REPORT = OUT / "passive_queue_selection_v0_report.json"

CLASSES = ["AHEAD", "AT_BID", "ONE_BEHIND", "TWO_THREE_BEHIND", "FOUR_PLUS_BEHIND"]
SNAP = [
    "seconds_left", "chosen_bid", "chosen_ask", "chosen_spread_ticks",
    "chosen_bid_depth", "chosen_ask_depth", "chosen_top3_bid_depth",
    "opp_bid", "opp_ask", "opp_bid_depth", "pair_bid_edge", "side_is_up",
    "direction_align", "direction_score_side", "vol_watch", "vol_high",
    "spot_return_1s_side", "spot_return_3s_side", "spot_return_5s_side",
    "futures_return_1s_side", "futures_return_3s_side", "futures_return_5s_side",
    "spot_queue_imbalance_side", "futures_queue_imbalance_side",
    "spot_taker_1s_side", "futures_taker_1s_side",
    "spot_minus_strike_side", "chainlink_minus_strike_side",
    "predict_receipt_age_ms", "predict_source_age_ms",
]
PLACE = [
    "last_place_age_ms", "last_same_place_age_ms", "last_opp_place_age_ms",
    "placements_1s", "placements_5s", "placements_10s",
    "same_placements_5s", "opp_placements_5s", "same_placements_10s", "opp_placements_10s",
    "placement_side_balance_5s", "placement_side_balance_10s", "placement_side_streak",
]
FEATURES = SNAP + PLACE


def cls(offset: int) -> int:
    if offset <= -1:
        return 0
    if offset == 0:
        return 1
    if offset == 1:
        return 2
    if offset <= 3:
        return 3
    return 4


def finite(v: Any) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else math.nan
    except Exception:
        return math.nan


def placement_features(history: list[dict[str, Any]], cp: int, side: str) -> dict[str, float]:
    xs = [x for x in history if int(x["at_ms"]) < cp]
    opp = "DOWN" if side == "UP" else "UP"
    if not xs:
        return {
            "last_place_age_ms": math.nan, "last_same_place_age_ms": math.nan, "last_opp_place_age_ms": math.nan,
            "placements_1s": 0.0, "placements_5s": 0.0, "placements_10s": 0.0,
            "same_placements_5s": 0.0, "opp_placements_5s": 0.0,
            "same_placements_10s": 0.0, "opp_placements_10s": 0.0,
            "placement_side_balance_5s": 0.0, "placement_side_balance_10s": 0.0,
            "placement_side_streak": 0.0,
        }
    last = xs[-1]
    same = [x for x in xs if x["side"] == side]
    other = [x for x in xs if x["side"] == opp]
    streak = 0
    for x in reversed(xs):
        if x["side"] == last["side"]:
            streak += 1
        else:
            break

    def count(window: int) -> tuple[int, int, int]:
        z = [x for x in xs if int(x["at_ms"]) > cp - window]
        s = sum(x["side"] == side for x in z)
        return len(z), s, len(z) - s

    n1, _, _ = count(1000)
    n5, s5, o5 = count(5000)
    n10, s10, o10 = count(10000)
    balance = lambda s, o: (s - o) / (s + o) if s + o else 0.0
    return {
        "last_place_age_ms": float(cp - int(last["at_ms"])),
        "last_same_place_age_ms": float(cp - int(same[-1]["at_ms"])) if same else math.nan,
        "last_opp_place_age_ms": float(cp - int(other[-1]["at_ms"])) if other else math.nan,
        "placements_1s": float(n1), "placements_5s": float(n5), "placements_10s": float(n10),
        "same_placements_5s": float(s5), "opp_placements_5s": float(o5),
        "same_placements_10s": float(s10), "opp_placements_10s": float(o10),
        "placement_side_balance_5s": balance(s5, o5),
        "placement_side_balance_10s": balance(s10, o10),
        "placement_side_streak": float(streak),
    }


def snapshot_features(s: dict[str, Any], bf: dict[str, float], side: str) -> dict[str, float]:
    up = side == "UP"
    sign = 1.0 if up else -1.0
    chosen_bid = bf["up_bid"] if up else bf["down_bid"]
    chosen_ask = bf["up_ask"] if up else bf["down_ask"]
    chosen_bid_depth = bf["up_bid_depth"] if up else bf["down_bid_depth"]
    chosen_ask_depth = bf["up_ask_depth"] if up else bf["down_ask_depth"]
    chosen_top3 = bf["up_top3_bid_depth"] if up else bf["down_top3_bid_depth"]
    opp_bid = bf["down_bid"] if up else bf["up_bid"]
    opp_ask = bf["down_ask"] if up else bf["up_ask"]
    opp_bid_depth = bf["down_bid_depth"] if up else bf["up_bid_depth"]
    bias = str(s.get("directionBias") or "NEUTRAL").upper()
    align = 1.0 if bias == side else -1.0 if bias in {"UP", "DOWN"} else 0.0
    vol = str(s.get("volatilityAlert") or "").upper()
    return {
        "seconds_left": finite(s.get("secondsLeft")),
        "chosen_bid": chosen_bid, "chosen_ask": chosen_ask,
        "chosen_spread_ticks": (chosen_ask - chosen_bid) / mod.GRID,
        "chosen_bid_depth": chosen_bid_depth, "chosen_ask_depth": chosen_ask_depth,
        "chosen_top3_bid_depth": chosen_top3,
        "opp_bid": opp_bid, "opp_ask": opp_ask, "opp_bid_depth": opp_bid_depth,
        "pair_bid_edge": bf["pair_bid_edge"], "side_is_up": float(up),
        "direction_align": align, "direction_score_side": sign * finite(s.get("directionScore")),
        "vol_watch": float(vol == "WATCH"), "vol_high": float(vol == "HIGH"),
        "spot_return_1s_side": sign * finite(s.get("spotReturn1sBps")),
        "spot_return_3s_side": sign * finite(s.get("spotReturn3sBps")),
        "spot_return_5s_side": sign * finite(s.get("spotReturn5sBps")),
        "futures_return_1s_side": sign * finite(s.get("futuresReturn1sBps")),
        "futures_return_3s_side": sign * finite(s.get("futuresReturn3sBps")),
        "futures_return_5s_side": sign * finite(s.get("futuresReturn5sBps")),
        "spot_queue_imbalance_side": sign * finite(s.get("spotQueueImbalance")),
        "futures_queue_imbalance_side": sign * finite(s.get("futuresQueueImbalance")),
        "spot_taker_1s_side": sign * finite(s.get("spotTakerImbalance1s")),
        "futures_taker_1s_side": sign * finite(s.get("futuresTakerImbalance1s")),
        "spot_minus_strike_side": sign * finite(s.get("spotMinusStrikeBps")),
        "chainlink_minus_strike_side": sign * finite(s.get("chainlinkMinusStrikeBps")),
        "predict_receipt_age_ms": finite(s.get("predictReceiptAgeMs")),
        "predict_source_age_ms": finite(s.get("predictSourceAgeMs")),
    }


def build() -> tuple[pd.DataFrame, dict[str, int], dict[str, Any]]:
    split = json.loads(SPLIT.read_text(encoding="utf-8"))
    ids = [int(x) for x in split["developmentMarkets"]]
    strategy = sqlite3.connect(STRATEGY_DB)
    strategy.row_factory = sqlite3.Row
    bookdb = sqlite3.connect(BOOK_DB)
    bookdb.row_factory = sqlite3.Row
    tailer = mod.PublicBookTailer(BOOK_DB)
    rows: list[dict[str, Any]] = []
    drops = {"noPrior": 0, "staleSnapshot": 0, "noBook": 0}
    try:
        for mid in ids:
            decisions: list[tuple[int, dict[str, Any]]] = []
            for r in strategy.execute(
                "select decision_ms,public_state_json from our_decisions where strategy_version=? and market_id=? order by decision_ms",
                (VERSION, mid),
            ):
                try:
                    decisions.append((int(r["decision_ms"]), json.loads(r["public_state_json"])))
                except Exception:
                    pass
            if not decisions:
                continue
            times = [x[0] for x in decisions]
            parents = [dict(r) for r in bookdb.execute(
                "select parent_id,target_side,target_price,placement_first_ms from maker_book_inference_v21_parent_lifecycles where market_id=? and placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75 and placement_first_ms is not null order by placement_first_ms,parent_id",
                (mid,),
            )]
            history: list[dict[str, Any]] = []
            tailer.market_id = None
            for parent in parents:
                t = int(parent["placement_first_ms"])
                j = bisect.bisect_left(times, t) - 1
                if j < 0:
                    drops["noPrior"] += 1
                    continue
                cp, snap = decisions[j]
                age = t - cp
                if age < 0 or age > 1500:
                    drops["staleSnapshot"] += 1
                    continue
                if not tailer.advance(mid, cp):
                    drops["noBook"] += 1
                    continue
                bf = mod.outcome_book(tailer.book, None)
                if not bf:
                    drops["noBook"] += 1
                    continue
                side = str(parent["target_side"]).upper()
                bid = bf["up_bid"] if side == "UP" else bf["down_bid"]
                price = float(parent["target_price"])
                offset = int(round((bid - price) / mod.GRID))
                z: dict[str, Any] = {
                    "market_id": mid, "checkpoint_ms": cp, "placement_ms": t,
                    "checkpoint_age_ms": age, "parent_id": parent["parent_id"],
                    "side": side, "target_price": price,
                    "offset_ticks": offset, "offset_class": cls(offset),
                }
                z.update(snapshot_features(snap, bf, side))
                z.update(placement_features(history, cp, side))
                rows.append(z)
                history.append({"at_ms": t, "side": side})
    finally:
        tailer.close()
        strategy.close()
        bookdb.close()
    d = pd.DataFrame(rows).sort_values(["market_id", "placement_ms"]).reset_index(drop=True)
    d.to_csv(DATA, index=False)
    return d, drops, split


def metric(y: pd.Series, p: np.ndarray) -> dict[str, Any]:
    yy = np.asarray(y, dtype=int)
    pred = np.argmax(p, axis=1)
    out = {
        "n": int(len(yy)),
        "accuracy": float(accuracy_score(yy, pred)),
        "balancedAccuracy": float(balanced_accuracy_score(yy, pred)),
        "macroF1": float(f1_score(yy, pred, average="macro", zero_division=0)),
        "truthDistribution": {CLASSES[i]: int((yy == i).sum()) for i in range(5)},
        "predDistribution": {CLASSES[i]: int((pred == i).sum()) for i in range(5)},
    }
    near = (yy <= 2).astype(int)
    p_near = p[:, :3].sum(axis=1)
    out["withinOneTickOrAhead"] = {
        "rate": float(near.mean()),
        "auc": float(roc_auc_score(near, p_near)),
        "ap": float(average_precision_score(near, p_near)),
        "logLoss": float(log_loss(near, np.clip(p_near, 1e-7, 1 - 1e-7), labels=[0, 1])),
    }
    return out


def main() -> None:
    d, drops, split = build()
    ids = [int(x) for x in split["developmentMarkets"]]
    train_ids = set(ids[:20])
    validation_ids = set(ids[20:])
    train = d[d.market_id.astype(int).isin(train_ids)].copy()
    val = d[d.market_id.astype(int).isin(validation_ids)].copy()
    model = ExplainableBoostingClassifier(
        feature_names=FEATURES,
        max_bins=64,
        max_interaction_bins=16,
        interactions=4,
        outer_bags=6,
        learning_rate=0.03,
        max_rounds=1200,
        early_stopping_rounds=60,
        min_samples_leaf=12,
        n_jobs=-2,
        random_state=20260821,
    )
    model.fit(train[FEATURES].apply(pd.to_numeric, errors="coerce"), train.offset_class.astype(int))
    artifact = {
        "version": "PASSIVE_QUEUE_SELECTION_V0",
        "features": FEATURES,
        "classes": CLASSES,
        "model": model,
        "trainingMarkets": sorted(train_ids),
        "validationMarkets": sorted(validation_ids),
        "runtimeTargetDataAllowed": False,
        "dreamFillAllowed": False,
        "semantics": "Given a frozen Strategy Brain Maker intent side, choose a passive queue-offset bucket. Target placement is label only.",
    }
    joblib.dump(artifact, ART)
    report = {
        "reportVersion": "PASSIVE_QUEUE_SELECTION_V0",
        "researchOnly": True,
        "liveTradingChanges": False,
        "dataset": {
            "rows": int(len(d)), "markets": int(d.market_id.nunique()), "drops": drops,
            "distribution": {CLASSES[i]: int((d.offset_class == i).sum()) for i in range(5)},
        },
        "split": {"trainMarkets": len(train_ids), "validationMarkets": len(validation_ids)},
        "metrics": {
            "train": metric(train.offset_class, model.predict_proba(train[FEATURES].apply(pd.to_numeric, errors="coerce"))),
            "validation": metric(val.offset_class, model.predict_proba(val[FEATURES].apply(pd.to_numeric, errors="coerce"))),
        },
        "artifact": str(ART),
        "guards": [
            "Target price/placement is label only.",
            "Runtime inputs are public market state plus own placement-history semantics.",
            "No winner/PnL/future-fill feature.", "No CAP100.", "No threshold or PnL sweep.",
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "artifact": str(ART), "report": str(REPORT), "dataset": report["dataset"], "metrics": report["metrics"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
