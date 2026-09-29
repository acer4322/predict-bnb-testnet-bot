from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research_fast_path_v1 import ResearchFastPath

PRICE_BOOK = [
    "micro_native_price",
    "micro_same_best_price",
    "micro_opp_best_price",
    "micro_same_best_depth",
    "micro_opp_best_depth",
    "micro_same_top3_depth",
    "micro_opp_top3_depth",
    "micro_same_top5_depth",
    "micro_opp_top5_depth",
    "micro_native_spread_ticks",
    "micro_book_levels_bid",
    "micro_book_levels_ask",
    "micro_current_order_count",
    "micro_current_update_age_ms",
]
FLOW = [
    "micro_u8_same_abs_delta",
    "micro_u8_same_negative_delta",
    "micro_u8_same_positive_delta",
    "micro_u8_opp_abs_delta",
    "micro_u8_order_count_delta",
    "micro_u8_span_ms",
    "micro_u32_same_abs_delta",
    "micro_u32_same_negative_delta",
    "micro_u32_same_positive_delta",
    "micro_u32_opp_abs_delta",
    "micro_u32_order_count_delta",
    "micro_u32_span_ms",
    "micro_t8_trade_count",
    "micro_t8_trade_qty",
    "micro_t8_fill_aggressor_qty",
    "micro_t8_fill_aggressor_share",
    "micro_t8_last_trade_age_ms",
    "micro_t8_last_fill_aggressor_age_ms",
    "micro_t32_trade_count",
    "micro_t32_trade_qty",
    "micro_t32_fill_aggressor_qty",
    "micro_t32_fill_aggressor_share",
    "micro_t32_last_trade_age_ms",
    "micro_t32_last_fill_aggressor_age_ms",
]
GROUPS = {
    "PRICE_BOOK": PRICE_BOOK,
    "FLOW": FLOW,
    "JOINT_MICRO": PRICE_BOOK + FLOW,
}


def auc_or_none(y, p):
    if len(set(int(v) for v in y)) < 2:
        return None
    return float(roc_auc_score(y, p))


def fit_predict(train: pd.DataFrame, ev: pd.DataFrame, features: list[str]) -> np.ndarray:
    imp = SimpleImputer(strategy="median")
    xtr0 = imp.fit_transform(train[features])
    xev0 = imp.transform(ev[features])
    scaler = StandardScaler()
    xtr = scaler.fit_transform(xtr0)
    xev = scaler.transform(xev0)
    model = LogisticRegression(C=1.0, max_iter=2000, random_state=20260909)
    model.fit(xtr, train.y.to_numpy(dtype=int))
    return model.predict_proba(xev)[:, 1]


def metrics(y, p):
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    return {
        "n": int(len(y)),
        "positiveRate": float(y.mean()) if len(y) else None,
        "auc": auc_or_none(y, p),
        "accuracy": float(accuracy_score(y, p >= 0.5)) if len(y) else None,
        "brier": float(brier_score_loss(y, p)) if len(y) else None,
        "probMean": float(p.mean()) if len(p) else None,
        "probStd": float(p.std()) if len(p) else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", required=True)
    ap.add_argument("--v1-result", required=True)
    ap.add_argument("--output", required=True)
    ns = ap.parse_args()

    v1 = json.loads(Path(ns.v1_result).read_text(encoding="utf-8"))
    allowed = [int(x) for x in v1["split"]["TRAIN"]["marketIds"] + v1["split"]["VALIDATION"]["marketIds"]]
    forbidden_test = set(int(x) for x in v1["split"]["TEST"]["marketIds"])
    if len(allowed) != 80 or len(set(allowed)) != 80 or set(allowed) & forbidden_test:
        raise RuntimeError("invalid V1-derived 80-market exploratory boundary")

    label_obj = json.loads(Path(ns.labels).read_text(encoding="utf-8"))
    winners_all = {int(r["market_id"]): str(r["winner"]).upper() for r in label_obj["labels"]}
    winners = {mid: winners_all[mid] for mid in allowed}

    cols = ["market_id", "decision_ms", "action_class", "action_side"] + PRICE_BOOK + FLOW
    where_ids = ",".join(str(x) for x in allowed)
    with ResearchFastPath() as fp:
        micro = fp.rows(
            "phaseb-decision-micro",
            where=f"market_id in ({where_ids}) and action_class = 'EXPAND'",
            columns=cols,
            include_oracles=False,
            allow_stale=False,
        ).df()
        state = fp.rows(
            "our-features",
            where=f"market_id in ({where_ids}) and state_seconds_left > 180",
            columns=["market_id", "decision_ms", "state_seconds_left"],
            include_labels=False,
        ).df()

    if set(int(x) for x in micro.market_id.unique()) != set(allowed):
        raise RuntimeError("phaseb EXPAND coverage does not match allowed80")
    per = micro.groupby("market_id").size()
    if not (per == 1).all():
        raise RuntimeError(f"expected one EXPAND row/market, got {per.value_counts().to_dict()}")

    state_nu = state.groupby(["market_id", "decision_ms"])["state_seconds_left"].nunique(dropna=False)
    if (state_nu > 1).any():
        raise RuntimeError("inconsistent state_seconds_left within identical market_id/decision_ms")
    state_one = state.groupby(["market_id", "decision_ms"], as_index=False)["state_seconds_left"].first()
    df = micro.merge(state_one, on=["market_id", "decision_ms"], how="left", validate="one_to_one")
    if df.state_seconds_left.isna().any() or not (df.state_seconds_left > 180).all():
        raise RuntimeError("strict-past >180 state join failed")

    order = {mid: i for i, mid in enumerate(allowed)}
    df["ordinal"] = df.market_id.map(order)
    df["winner"] = df.market_id.map(winners)
    df["y"] = (df.winner == df.action_side.str.upper()).astype(int)
    df = df.sort_values("ordinal").reset_index(drop=True)

    folds = [
        (1, 0, 40, 40, 50),
        (2, 0, 50, 50, 60),
        (3, 0, 60, 60, 70),
        (4, 0, 70, 70, 80),
    ]

    group_results = {}
    for group, features in GROUPS.items():
        fold_rows = []
        all_y = []
        all_p = []
        all_mid = []
        for fold, tr0, tr1, ev0, ev1 in folds:
            train_ids = allowed[tr0:tr1]
            eval_ids = allowed[ev0:ev1]
            tr = df[df.market_id.isin(train_ids)].copy()
            ev = df[df.market_id.isin(eval_ids)].copy().sort_values("ordinal")
            pred = fit_predict(tr, ev, features)
            fm = metrics(ev.y.to_numpy(), pred)
            fold_rows.append({
                "fold": fold,
                "trainMarkets": len(train_ids),
                "evalMarkets": len(eval_ids),
                "evalOrdinalRange": [ev0, ev1 - 1],
                "metrics": fm,
            })
            all_y.extend(ev.y.astype(int).tolist())
            all_p.extend(float(x) for x in pred)
            all_mid.extend(int(x) for x in ev.market_id.tolist())
        pooled = metrics(all_y, all_p)
        group_results[group] = {
            "folds": fold_rows,
            "pooledOOF": pooled,
            "oofMarketIds": all_mid,
        }

    joint = group_results["JOINT_MICRO"]
    fold_auc = [f["metrics"]["auc"] for f in joint["folds"] if f["metrics"]["auc"] is not None]
    consistency = sum(a > 0.5 for a in fold_auc)
    pm = joint["pooledOOF"]
    gate_pass = (
        pm["auc"] is not None and pm["auc"] >= 0.60
        and pm["accuracy"] is not None and pm["accuracy"] >= 0.575
        and consistency >= 3
    )

    out = {
        "version": "OUR_EARLY_EXPAND_THESIS_MICROSTRUCTURE_OOF_V2",
        "date": "2026-09-09",
        "researchOnly": True,
        "runtimeAuthority": False,
        "freshConfirmation": False,
        "prereg": "data/research/r4_v0/p0_provenance_v1/OUR_EARLY_EXPAND_THESIS_MICROSTRUCTURE_OOF_V2_PREREGISTERED_20260909.json",
        "coverage": {
            "allowedMarkets": 80,
            "expandRows": int(len(df)),
            "allStateJoinsGt180": bool((df.state_seconds_left > 180).all()),
            "secondsLeftMin": float(df.state_seconds_left.min()),
            "secondsLeftMedian": float(df.state_seconds_left.median()),
            "secondsLeftMax": float(df.state_seconds_left.max()),
            "naiveAlwaysTrustExpandAccuracy": float(df.y.mean()),
        },
        "v1Test20Excluded": True,
        "groups": group_results,
        "primaryGate": {
            "jointPooledAuc": pm["auc"],
            "jointPooledAccuracy": pm["accuracy"],
            "foldsWithAucAboveHalf": consistency,
            "foldAucsDefined": len(fold_auc),
            "pass": bool(gate_pass),
        },
        "status": "EXPLORATORY_SIGNAL_WORTH_FRESH_CONFIRMATION" if gate_pass else "EXPLORATORY_SIGNAL_FAIL_STOP_NO_THRESHOLD_TUNING",
        "verdict": "MICROSTRUCTURE_THESIS_QUALITY_SIGNAL_PRESENT_EXPLORATORY_ONLY" if gate_pass else "EARLY_PHASEB_MICROSTRUCTURE_THESIS_SELECTOR_NOT_SUPPORTED",
    }
    Path(ns.output).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "coverage": out["coverage"],
        "groups": {g: r["pooledOOF"] for g, r in group_results.items()},
        "jointFoldAucs": [f["metrics"]["auc"] for f in joint["folds"]],
        "primaryGate": out["primaryGate"],
        "status": out["status"],
        "verdict": out["verdict"],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
