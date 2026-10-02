from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research_fast_path_v1 import ResearchFastPath

PUBLIC = [
    "state_seconds_left",
    "state_book_imbalance",
    "state_spread",
]
OUR_RAW = [
    "state_up_qty",
    "state_down_qty",
    "state_abs_net",
    "state_coverage",
    "state_gross",
    "state_debt_up",
    "state_debt_down",
    "state_total_debt",
    "state_oldest_repair_progress",
    "state_oldest_repair_age_ms",
    "state_responsibility_count",
    "state_live_slots",
    "state_repair_family_live_slots",
    "state_satellite_expand_live_slots",
    "state_pending_cancel_count",
    "state_q_ladder_live",
    "state_q_pending_active",
    "state_dominant_side",
    "state_dominant_mid",
]
OUR_ENGINEERED = [
    "inventory_net_up_minus_down",
    "state_abs_net",
    "state_coverage",
    "state_gross",
    "debt_net_up_minus_down",
    "state_total_debt",
    "state_oldest_repair_progress",
    "state_oldest_repair_age_ms",
    "state_responsibility_count",
    "state_live_slots",
    "state_repair_family_live_slots",
    "state_satellite_expand_live_slots",
    "state_pending_cancel_count",
    "state_q_ladder_live",
    "state_q_pending_active",
    "dominant_side_signed",
    "state_dominant_mid",
]
GROUPS = {
    "PUBLIC_MARKET_ONLY": PUBLIC,
    "OUR_HISTORY_ONLY_ENGINEERED": OUR_ENGINEERED,
    "JOINT": PUBLIC + OUR_ENGINEERED,
}
TIE_PRIORITY = {"JOINT": 3, "PUBLIC_MARKET_ONLY": 2, "OUR_HISTORY_ONLY_ENGINEERED": 1}


def finite_or_none(x):
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def auc_or_none(y, p, sample_weight=None):
    if len(set(int(v) for v in y)) < 2:
        return None
    return float(roc_auc_score(y, p, sample_weight=sample_weight))


def add_engineered(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["inventory_net_up_minus_down"] = out["state_up_qty"].astype(float) - out["state_down_qty"].astype(float)
    out["debt_net_up_minus_down"] = out["state_debt_up"].astype(float) - out["state_debt_down"].astype(float)
    out["dominant_side_signed"] = out["state_dominant_side"].map({"UP": 1.0, "DOWN": -1.0, "FLAT": 0.0}).fillna(0.0)
    return out


def market_equal_row_weights(df: pd.DataFrame) -> np.ndarray:
    counts = df.groupby("market_id")["market_id"].transform("count").astype(float)
    return (1.0 / counts).to_numpy()


def market_metrics(df: pd.DataFrame, pred: np.ndarray) -> dict:
    tmp = pd.DataFrame({"market_id": df.market_id.to_numpy(), "y": df.y.to_numpy(), "p": pred})
    mg = tmp.groupby("market_id", as_index=False).agg(y=("y", "first"), p=("p", "mean"))
    y = mg.y.to_numpy(dtype=int)
    p = mg.p.to_numpy(dtype=float)
    return {
        "markets": int(len(mg)),
        "positiveRate": float(y.mean()) if len(y) else None,
        "auc": auc_or_none(y, p),
        "brier": float(brier_score_loss(y, p)) if len(y) else None,
        "accuracy": float(accuracy_score(y, p >= 0.5)) if len(y) else None,
        "probMean": float(np.mean(p)) if len(p) else None,
        "probStd": float(np.std(p)) if len(p) else None,
    }


def row_metrics(df: pd.DataFrame, pred: np.ndarray) -> dict:
    y = df.y.to_numpy(dtype=int)
    w = market_equal_row_weights(df)
    return {
        "rows": int(len(df)),
        "equalMarketWeightedAuc": auc_or_none(y, pred, sample_weight=w),
        "equalMarketWeightedAccuracy": float(np.average((pred >= 0.5) == y, weights=w)),
    }


def timing_band_metrics(df: pd.DataFrame, pred: np.ndarray) -> dict:
    out = {}
    sec = df.state_seconds_left.to_numpy(dtype=float)
    bands = [(240.0, 300.000001, "240-300"), (210.0, 240.0, "210-240"), (180.0, 210.0, "180-210")]
    for lo, hi, name in bands:
        mask = (sec > lo) & (sec <= hi)
        if not np.any(mask):
            out[name] = {"rows": 0, "markets": 0, "auc": None, "accuracy": None}
            continue
        part = df.loc[mask].copy()
        pp = pred[mask]
        mm = market_metrics(part, pp)
        out[name] = {"rows": int(mask.sum()), "markets": mm["markets"], "auc": mm["auc"], "accuracy": mm["accuracy"]}
    return out


def fit_hgb(train: pd.DataFrame, features: list[str]):
    imp = SimpleImputer(strategy="median")
    X = imp.fit_transform(train[features])
    y = train.y.to_numpy(dtype=int)
    w = market_equal_row_weights(train)
    model = HistGradientBoostingClassifier(
        max_iter=150,
        learning_rate=0.05,
        max_leaf_nodes=15,
        l2_regularization=1.0,
        random_state=20260909,
    )
    model.fit(X, y, sample_weight=w)
    return imp, model


def predict_hgb(pair, df: pd.DataFrame, features: list[str]) -> np.ndarray:
    imp, model = pair
    return model.predict_proba(imp.transform(df[features]))[:, 1]


def fit_logistic(train: pd.DataFrame, features: list[str]):
    imp = SimpleImputer(strategy="median")
    X0 = imp.fit_transform(train[features])
    scaler = StandardScaler()
    X = scaler.fit_transform(X0)
    y = train.y.to_numpy(dtype=int)
    w = market_equal_row_weights(train)
    model = LogisticRegression(C=1.0, max_iter=2000, random_state=20260909)
    model.fit(X, y, sample_weight=w)
    return imp, scaler, model


def predict_logistic(triple, df: pd.DataFrame, features: list[str]) -> np.ndarray:
    imp, scaler, model = triple
    X = scaler.transform(imp.transform(df[features]))
    return model.predict_proba(X)[:, 1]


def eval_bundle(df: pd.DataFrame, pred: np.ndarray) -> dict:
    return {
        "market": market_metrics(df, pred),
        "row": row_metrics(df, pred),
        "timingBandsDiagnostic": timing_band_metrics(df, pred),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", required=True)
    ap.add_argument("--output", required=True)
    ns = ap.parse_args()

    label_obj = json.loads(Path(ns.labels).read_text(encoding="utf-8"))
    labels = {int(r["market_id"]): 1 if str(r["winner"]).upper() == "UP" else 0 for r in label_obj["labels"]}

    cols = ["market_id", "decision_ms", "state_seconds_left"] + PUBLIC + OUR_RAW
    cols = list(dict.fromkeys(cols))
    with ResearchFastPath() as fp:
        df = fp.rows(
            "our-features",
            columns=cols,
            where="state_seconds_left > 180",
            include_labels=False,
        ).df()

    df = add_engineered(df)
    df = df[df.market_id.isin(labels)].copy()
    df["y"] = df.market_id.map(labels).astype(int)

    first = df.groupby("market_id", as_index=False).decision_ms.min().sort_values(["decision_ms", "market_id"])
    mids = [int(x) for x in first.market_id.tolist()]
    if len(mids) != 100:
        raise RuntimeError(f"expected 100 labeled eligible markets, got {len(mids)}")
    split_ids = {"TRAIN": mids[:60], "VALIDATION": mids[60:80], "TEST": mids[80:100]}
    split_map = {mid: split for split, ids in split_ids.items() for mid in ids}
    df["split"] = df.market_id.map(split_map)

    train = df[df.split == "TRAIN"].copy()
    val = df[df.split == "VALIDATION"].copy()
    test = df[df.split == "TEST"].copy()

    split_summary = {}
    for name, part in [("TRAIN", train), ("VALIDATION", val), ("TEST", test)]:
        ym = part.groupby("market_id").y.first()
        split_summary[name] = {
            "markets": int(len(ym)),
            "rows": int(len(part)),
            "upMarkets": int(ym.sum()),
            "downMarkets": int(len(ym) - ym.sum()),
            "marketIds": [int(x) for x in split_ids[name]],
        }

    validation = {}
    fitted_hgb = {}
    fitted_log = {}
    for group, features in GROUPS.items():
        hp = fit_hgb(train, features)
        lp = fit_logistic(train, features)
        fitted_hgb[group] = hp
        fitted_log[group] = lp
        pv_h = predict_hgb(hp, val, features)
        pv_l = predict_logistic(lp, val, features)
        validation[group] = {
            "primaryHGB": eval_bundle(val, pv_h),
            "secondaryLogisticSanityOnly": eval_bundle(val, pv_l),
        }

    passed = []
    for group in GROUPS:
        m = validation[group]["primaryHGB"]["market"]
        if m["auc"] is not None and m["auc"] >= 0.60 and m["accuracy"] is not None and m["accuracy"] >= 0.55:
            passed.append(group)

    result = {
        "version": "OUR_STRICT_PAST_DIRECTIONAL_ALPHA_IDENTIFICATION_V1_1",
        "date": "2026-09-09",
        "researchOnly": True,
        "runtimeAuthority": False,
        "prereg": "data/research/r4_v0/p0_provenance_v1/OUR_STRICT_PAST_DIRECTIONAL_ALPHA_IDENTIFICATION_V1_PREREGISTERED_20260909.json",
        "amendment": "data/research/r4_v0/p0_provenance_v1/OUR_STRICT_PAST_DIRECTIONAL_ALPHA_IDENTIFICATION_V1_1_LABEL_SOURCE_AMENDMENT_20260909.json",
        "labelProvenance": str(Path(ns.labels)),
        "eligibility": "state_seconds_left > 180",
        "coverage": {"rows": int(len(df)), "markets": int(df.market_id.nunique())},
        "split": split_summary,
        "featureGroups": GROUPS,
        "validation": validation,
        "validationPassedGroups": passed,
        "testOpened": bool(passed),
    }

    if not passed:
        result["status"] = "VALIDATION_FAIL_STOP_NO_TEST"
        result["verdict"] = "DIRECTIONAL_ALPHA_NOT_IDENTIFIED_ON_VALIDATION"
    else:
        selected = sorted(
            passed,
            key=lambda g: (validation[g]["primaryHGB"]["market"]["auc"], TIE_PRIORITY[g]),
            reverse=True,
        )[0]
        features = GROUPS[selected]
        pt = predict_hgb(fitted_hgb[selected], test, features)
        tm = eval_bundle(test, pt)
        confirm = (
            tm["market"]["auc"] is not None
            and tm["market"]["auc"] >= 0.60
            and tm["market"]["accuracy"] is not None
            and tm["market"]["accuracy"] >= 0.55
        )
        result["selectedGroupFromValidation"] = selected
        result["test"] = {"primaryHGB": tm}
        result["testConfirmPass"] = bool(confirm)
        result["status"] = "TEST_CONFIRM_PASS" if confirm else "TEST_CONFIRM_FAIL"
        result["verdict"] = (
            "STRICT_PAST_DIRECTIONAL_ALPHA_IDENTIFIED"
            if confirm
            else "VALIDATION_SIGNAL_FAILED_CONFIRMATION"
        )

    Path(ns.output).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    console = {
        "coverage": result["coverage"],
        "splitCounts": {k: {x: v[x] for x in ["markets", "rows", "upMarkets", "downMarkets"]} for k, v in split_summary.items()},
        "validation": {g: validation[g]["primaryHGB"]["market"] for g in GROUPS},
        "validationPassedGroups": passed,
        "testOpened": result["testOpened"],
        "selectedGroupFromValidation": result.get("selectedGroupFromValidation"),
        "test": result.get("test", {}).get("primaryHGB", {}).get("market"),
        "status": result["status"],
        "verdict": result["verdict"],
    }
    print(json.dumps(console, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
