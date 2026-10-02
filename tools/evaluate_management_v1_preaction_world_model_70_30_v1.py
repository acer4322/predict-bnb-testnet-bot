from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import balanced_accuracy_score, mean_absolute_error, roc_auc_score

ROLES = ("PROBE_CORE", "ECONOMIC_CORE", "SATELLITE_REPAIR", "SATELLITE_EXPAND")
ROUTES = ("PASSIVE", "ACTIVE")
H = (3, 5)
STATE = [
    "secondsLeft", "floor", "best", "absNet", "coverage", "gross", "debtUp", "debtDown",
    "totalDebt", "targetDebtForActionSide", "oldestRepairProgress", "oldestRepairAgeMs",
    "responsibilityCount", "liveSlots", "sideLiveSlots", "repairLiveSlots", "expandLiveSlots",
    "pendingCancelCount", "bookImbalance", "spread", "sideBid", "sideAsk", "sideMid",
    "dominantMid", "qLadderLive", "qPendingActive", "sideIsDominant", "sideIsWeak",
]
ACTION = ["price", "qty", "priceToBid", "askToPrice", "pairLegal", "isRepairRole", "isExpandRole"]


def safe(x, d=0.0):
    try:
        z = float(x)
        return z if math.isfinite(z) else d
    except Exception:
        return d


def role_code(r):
    return [1.0 if r["role"] == x else 0.0 for x in ROLES] + [1.0 if r["route"] == x else 0.0 for x in ROUTES] + [1.0 if r["side"] == "UP" else 0.0]


def X(rows, with_action):
    a = np.asarray([[safe(r[k]) for k in STATE] for r in rows], dtype=np.float64)
    if not with_action:
        return a
    b = np.asarray([[safe(r[k]) for k in ACTION] + role_code(r) for r in rows], dtype=np.float64)
    return np.c_[a, b]


def auc(y, p):
    y = np.asarray(y, int)
    return float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else None


def ba(y, p):
    y = np.asarray(y, int)
    return float(balanced_accuracy_score(y, (np.asarray(p) >= 0.5).astype(int))) if len(np.unique(y)) > 1 else None


def fit_event(xtr, xva, ytr, yva, seed):
    if len(set(ytr)) < 2:
        return {"trainClasses": sorted(set(map(int, ytr)))}, None
    m = HistGradientBoostingClassifier(
        max_iter=220, learning_rate=0.05, max_leaf_nodes=23, min_samples_leaf=18,
        l2_regularization=2.0, random_state=seed,
    ).fit(xtr, ytr)
    p = m.predict_proba(xva)[:, 1]
    return {"n": len(yva), "positiveSupport": int(sum(yva)), "auc": auc(yva, p), "ba": ba(yva, p), "baseRate": float(np.mean(yva))}, m


def fit_qty(xtr, xva, ytr, yva, seed):
    m = HistGradientBoostingRegressor(
        max_iter=220, learning_rate=0.05, max_leaf_nodes=23, min_samples_leaf=18,
        l2_regularization=2.0, random_state=seed,
    ).fit(xtr, ytr)
    p = np.maximum(0.0, m.predict(xva))
    sc = max(1.0, float(np.mean(np.abs(yva))))
    return {"mae": float(mean_absolute_error(yva, p)), "nmae": float(mean_absolute_error(yva, p) / sc), "actualMean": float(np.mean(yva)), "predMean": float(np.mean(p))}, m


def load_rows(path: Path):
    out = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True, type=Path)
    ap.add_argument("--output", required=True)
    ap.add_argument("--train-markets", type=int, default=70)
    a = ap.parse_args()

    rows = load_rows(a.rows)
    mids = sorted({int(r["marketId"]) for r in rows}, key=lambda m: min(int(x["windowEndMs"]) for x in rows if int(x["marketId"]) == m))
    cut = min(max(1, int(a.train_markets)), len(mids) - 1)
    trset, vaset = set(mids[:cut]), set(mids[cut:])
    tr = [r for r in rows if int(r["marketId"]) in trset]
    va = [r for r in rows if int(r["marketId"]) in vaset]

    xs0, xv0 = X(tr, False), X(va, False)
    xs1, xv1 = X(tr, True), X(va, True)
    report = {
        "version": "MANAGEMENT_V1_PREACTION_WORLD_MODEL_70_30_V1",
        "date": "2026-09-07",
        "researchOnly": True,
        "actionAuthority": False,
        "stateTiming": "PRE_SUBMIT_DECISION",
        "rows": len(rows),
        "markets": len(mids),
        "trainMarkets": len(trset),
        "validationMarkets": len(vaset),
        "trainRows": len(tr),
        "validationRows": len(va),
        "horizons": {},
        "guards": [
            "same feature/action contract and model hyperparameters as original Phase-A 70/30",
            "pre-submit state only",
            "winner/Target future absent",
            "no policy authority",
            "NEW24-B untouched",
        ],
    }

    for h in H:
        hr = {}
        for label in (f"anyFill{h}s", f"cancelReq{h}s", f"terminal{h}s"):
            yt = [int(r[label]) for r in tr]
            yv = [int(r[label]) for r in va]
            s0, _ = fit_event(xs0, xv0, yt, yv, 100 + h * 10 + len(hr))
            s1, _ = fit_event(xs1, xv1, yt, yv, 200 + h * 10 + len(hr))
            hr[label] = {
                "stateOnly": s0,
                "stateAction": s1,
                "aucDeltaAction": None if s0.get("auc") is None or s1.get("auc") is None else float(s1["auc"] - s0["auc"]),
                "baDeltaAction": None if s0.get("ba") is None or s1.get("ba") is None else float(s1["ba"] - s0["ba"]),
            }
        for label in (f"fillQty{h}s", f"repairPayQty{h}s", f"overflowQty{h}s"):
            yt = np.asarray([safe(r[label]) for r in tr])
            yv = np.asarray([safe(r[label]) for r in va])
            s0, _ = fit_qty(xs0, xv0, yt, yv, 300 + h * 10 + len(hr))
            s1, _ = fit_qty(xs1, xv1, yt, yv, 400 + h * 10 + len(hr))
            hr[label] = {"stateOnly": s0, "stateAction": s1, "maeImprovementAction": float(s0["mae"] - s1["mae"])}
        report["horizons"][str(h)] = hr

    deltas = []
    for h in H:
        for v in report["horizons"][str(h)].values():
            if v.get("aucDeltaAction") is not None:
                deltas.append(v["aucDeltaAction"])
            if "maeImprovementAction" in v:
                deltas.append(v["maeImprovementAction"])
    report["diagnostic"] = {
        "positiveIncrementCount": int(sum(x > 0 for x in deltas)),
        "metricCount": len(deltas),
        "maxPositiveActionIncrement": float(max(deltas)) if deltas else None,
        "decision": "PREACTION_ACTION_CONTEXT_EXERCISED" if any(x > 0 for x in deltas) else "PREACTION_ACTION_CONTEXT_NOT_IDENTIFIED",
    }

    out = (Path(os.environ["BTC5M_LAN_RESULT_DIR"]) / "result.json") if str(a.output).upper() == "AUTO" else Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"ok": True, "trainRows": len(tr), "validationRows": len(va), "diagnostic": report["diagnostic"], "horizons": report["horizons"]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
