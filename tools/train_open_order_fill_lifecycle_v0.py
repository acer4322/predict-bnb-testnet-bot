from __future__ import annotations

import json
import math
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
TEACHER_CSV = DATA_DIR / "r2_execution_school_teacher_v0_rows.csv"
ART = DATA_DIR / "open_order_fill_lifecycle_v0.joblib"
REPORT = DATA_DIR / "open_order_fill_lifecycle_v0_report.json"
DATASET = DATA_DIR / "open_order_fill_lifecycle_v0_dataset.csv"
SEED = 20260821

BASE_FEATURES = [
    "side_is_up",
    "order_age_ms",
    "quote_price",
    "status_none",
    "status_new",
    "status_partial",
    "cum_exec_qty",
    "remaining_qty",
    "remaining_ratio",
    "partial_fill_ratio",
    "active_same_count",
    "active_opp_count",
    "quote_offset_ticks",
    "current_bid",
    "current_ask",
    "current_spread_ticks",
    "initial_depth",
    "public_cum_depletion",
    "public_depletion_ratio",
    "public_any_depletion",
]

PORTFOLIO_FEATURES = [
    "maker_gross", "maker_net", "maker_abs_net", "maker_imbalance_ratio", "maker_paired_coverage",
    "taker_gross", "taker_net", "taker_abs_net", "taker_paired_coverage",
    "combined_gross", "combined_net", "combined_abs_net", "combined_imbalance_ratio", "combined_paired_coverage",
    "worst_case_floor", "best_case_pnl", "abs_payoff_gap",
    "last_maker_age_ms", "last_maker_up_age_ms", "last_maker_down_age_ms",
    "maker_fills_1s", "maker_fills_5s", "maker_fills_10s", "maker_shares_5s", "maker_shares_10s",
]
FEATURES = BASE_FEATURES + PORTFOLIO_FEATURES


def finite(v: Any) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else math.nan
    except Exception:
        return math.nan


def metrics(y: np.ndarray, p: np.ndarray, baseline_rate: float) -> dict[str, Any]:
    y = np.asarray(y, dtype=int)
    p = np.clip(np.asarray(p, dtype=float), 1e-7, 1 - 1e-7)
    both = len(set(y.tolist())) > 1
    base = np.full(len(y), np.clip(float(baseline_rate), 1e-7, 1 - 1e-7))
    ll = float(log_loss(y, p, labels=[0, 1]))
    base_ll = float(log_loss(y, base, labels=[0, 1]))
    return {
        "n": int(len(y)), "positives": int(y.sum()), "rate": float(y.mean()) if len(y) else None,
        "predMean": float(p.mean()) if len(p) else None,
        "auc": float(roc_auc_score(y, p)) if both else None,
        "ap": float(average_precision_score(y, p)) if y.sum() else None,
        "logLoss": ll, "baselineLogLoss": base_ll, "logLossLift": base_ll - ll,
        "brier": float(brier_score_loss(y, p)),
    }


def top_terms(model: ExplainableBoostingClassifier, n: int = 20) -> list[dict[str, Any]]:
    imp = list(model.term_importances())
    names = list(model.term_names_)
    order = sorted(range(len(imp)), key=lambda i: float(imp[i]), reverse=True)[:n]
    return [{"term": str(names[i]), "importance": float(imp[i])} for i in order]


def build_dataset() -> pd.DataFrame:
    d = pd.read_csv(TEACHER_CSV, low_memory=False)
    # One state per own order/checkpoint. Prefer POST_DECISION_ACTIVE because BEFORE_ADD rows duplicate the
    # same state around an action boundary and would overweight precisely the mistakes we later want to grade.
    d["_post"] = (d["context"].astype(str) == "POST_DECISION_ACTIVE").astype(int)
    d = d.sort_values(["marketId", "checkpointMs", "orderId", "_post"], ascending=[True, True, True, False])
    d = d.drop_duplicates(["marketId", "checkpointMs", "orderId"], keep="first").copy()

    def portfolio(v: Any) -> dict[str, Any]:
        try:
            x = json.loads(v) if isinstance(v, str) else {}
            return x if isinstance(x, dict) else {}
        except Exception:
            return {}

    ps = d["portfolio_json"].map(portfolio)
    out = pd.DataFrame({
        "market_id": pd.to_numeric(d["marketId"], errors="coerce").astype("Int64"),
        "checkpoint_ms": pd.to_numeric(d["checkpointMs"], errors="coerce"),
        "order_id": d["orderId"].astype(str),
        "teacher_execution_label": d["teacherExecutionLabel"].astype(str),
        "side_is_up": (d["side"].astype(str).str.upper() == "UP").astype(float),
        "order_age_ms": pd.to_numeric(d["orderAgeMs"], errors="coerce"),
        "quote_price": pd.to_numeric(d["price"], errors="coerce"),
        "status_none": (d["hftStatus"].astype(str) == "NONE").astype(float),
        "status_new": (d["hftStatus"].astype(str) == "NEW").astype(float),
        "status_partial": (d["hftStatus"].astype(str) == "PARTIALLY_FILLED").astype(float),
        "cum_exec_qty": pd.to_numeric(d["cumExecQty"], errors="coerce"),
        "remaining_qty": pd.to_numeric(d["remainingQty"], errors="coerce"),
        "partial_fill_ratio": pd.to_numeric(d["partialFillRatio"], errors="coerce"),
        "active_same_count": pd.to_numeric(d["activeSameCount"], errors="coerce"),
        "active_opp_count": pd.to_numeric(d["activeOppCount"], errors="coerce"),
        "quote_offset_ticks": pd.to_numeric(d["quoteOffsetTicks"], errors="coerce"),
        "current_bid": pd.to_numeric(d["currentBid"], errors="coerce"),
        "current_ask": pd.to_numeric(d["currentAsk"], errors="coerce"),
        "current_spread_ticks": pd.to_numeric(d["currentSpreadTicks"], errors="coerce"),
        "initial_depth": pd.to_numeric(d["initialDepth"], errors="coerce"),
        "public_cum_depletion": pd.to_numeric(d["publicCumDepletion"], errors="coerce"),
        "public_any_depletion": d["publicAnyDepletion"].astype(str).str.lower().isin({"true", "1", "yes"}).astype(float),
    })
    qty0 = out["cum_exec_qty"].fillna(0) + out["remaining_qty"].fillna(0)
    out["remaining_ratio"] = np.where(qty0 > 1e-9, out["remaining_qty"].fillna(0) / qty0, np.nan)
    out["public_depletion_ratio"] = np.where(out["initial_depth"] > 1e-9, out["public_cum_depletion"] / out["initial_depth"], np.nan)
    for f in PORTFOLIO_FEATURES:
        out[f] = ps.map(lambda x, k=f: finite(x.get(k)))
    for h in (1, 3, 5):
        out[f"label_fill_{h}s"] = pd.to_numeric(d[f"labelAnyFill{h}s"], errors="coerce").fillna(0).astype(int)
        out[f"future_fill_shares_{h}s"] = pd.to_numeric(d[f"futureFillShares{h}s"], errors="coerce").fillna(0.0)
        out[f"censored_{h}s"] = pd.to_numeric(d[f"censoredBefore{h}s"], errors="coerce").fillna(0).astype(int)
    # Only states with a live remaining quantity are meaningful open-order lifecycle examples.
    out = out[(out["remaining_qty"].fillna(0) > 1e-9)].copy()
    out = out.sort_values(["checkpoint_ms", "market_id", "order_id"]).reset_index(drop=True)
    return out


def main() -> int:
    warnings.filterwarnings("ignore", message="X does not have valid feature names")
    d = build_dataset()
    d.to_csv(DATASET, index=False)
    markets = d.groupby("market_id", as_index=False)["checkpoint_ms"].max().sort_values(["checkpoint_ms", "market_id"])
    ids = markets.market_id.astype(int).tolist()
    n = len(ids)
    a = int(n * 0.70)
    b = int(n * 0.85)
    splits = {"train": set(ids[:a]), "validation": set(ids[a:b]), "test": set(ids[b:])}
    parts = {k: d[d.market_id.astype(int).isin(v)].copy() for k, v in splits.items()}

    models: dict[str, ExplainableBoostingClassifier] = {}
    report_metrics: dict[str, Any] = {}
    top: dict[str, Any] = {}
    for h in (1, 3, 5):
        label = f"label_fill_{h}s"
        usable = {k: x[x[f"censored_{h}s"] == 0].copy() for k, x in parts.items()}
        tr = usable["train"]
        model = ExplainableBoostingClassifier(
            feature_names=FEATURES,
            max_bins=64,
            max_interaction_bins=16,
            interactions=6,
            outer_bags=4,
            learning_rate=0.035,
            max_rounds=1000,
            early_stopping_rounds=60,
            min_samples_leaf=16,
            n_jobs=-2,
            random_state=SEED + h,
        )
        xtr = tr[FEATURES].apply(pd.to_numeric, errors="coerce")
        ytr = tr[label].astype(int)
        model.fit(xtr, ytr)
        models[f"fill_{h}s"] = model
        base_rate = float(ytr.mean())
        report_metrics[f"fill{h}s"] = {}
        for name, x in usable.items():
            y = x[label].astype(int).to_numpy()
            p = model.predict_proba(x[FEATURES].apply(pd.to_numeric, errors="coerce"))[:, 1]
            report_metrics[f"fill{h}s"][name] = metrics(y, p, base_rate)
        top[f"fill{h}s"] = top_terms(model)

    # Target is an independent post-hoc examiner, not a model feature. Compare the 3s estimator across
    # teacher bins on validation/test only.
    eval_ids = splits["validation"] | splits["test"]
    ev = d[d.market_id.astype(int).isin(eval_ids) & (d["censored_3s"] == 0)].copy()
    ev["p_fill_3s"] = models["fill_3s"].predict_proba(ev[FEATURES].apply(pd.to_numeric, errors="coerce"))[:, 1]
    teacher_exam = {}
    for label, x in ev.groupby("teacher_execution_label"):
        teacher_exam[str(label)] = {
            "n": int(len(x)),
            "markets": int(x.market_id.nunique()),
            "meanPFill3s": float(x.p_fill_3s.mean()),
            "medianPFill3s": float(x.p_fill_3s.median()),
            "actualFill3sRate": float(x.label_fill_3s.mean()),
        }

    artifact = {
        "version": "OPEN_ORDER_FILL_LIFECYCLE_V0",
        "student": "PRE_CAP100_R2",
        "features": FEATURES,
        "models": models,
        "trainingMarkets": sorted(splits["train"]),
        "validationMarkets": sorted(splits["validation"]),
        "testMarkets": sorted(splits["test"]),
        "trainingMaxCheckpointMs": int(parts["train"].checkpoint_ms.max()),
        "dreamFillAllowed": False,
        "runtimeTargetDataAllowed": False,
        "semantics": "Predict Student own resting-order any-fill within 1s/3s/5s under canonical Execution Tape V1 + HftBacktest physics. Target is post-hoc examiner only.",
    }
    joblib.dump(artifact, ART)

    report = {
        "reportVersion": "OPEN_ORDER_FILL_LIFECYCLE_V0",
        "researchOnly": True,
        "liveTradingChanges": False,
        "student": "UNIFIED_PROMOTED_OWNSTATE_V4_R2_RESIDUAL_FORWARD_PAPER",
        "studentScale": "PRE_CAP100_ORIGINAL_R2",
        "dreamFillAllowed": False,
        "dataset": {
            "source": str(TEACHER_CSV), "derived": str(DATASET), "rows": int(len(d)), "markets": int(d.market_id.nunique()),
            "dedupe": "one Student own-order state per market/checkpoint/order; POST_DECISION_ACTIVE preferred",
            "openOrderFilter": "remaining_qty > 0",
            "censoring": "rows cancelled before each horizon are excluded from that horizon training/evaluation",
        },
        "splitMarkets": {k: len(v) for k, v in splits.items()},
        "metrics": report_metrics,
        "targetPosthocExam3s": teacher_exam,
        "topTerms": top,
        "artifact": str(ART),
        "runtimeContract": {
            "allowed": "Student own open-order state + strict-past public book/depletion + Student own filled portfolio",
            "forbidden": "Target data, Target lifecycle proxy, future HFT fills, winner, settlement PnL, teacher labels",
            "role": "state estimator only; V0 does not directly override Maker/Taker strategy actions",
        },
        "guards": [
            "All fills used as labels are Student HftBacktest fills from Execution Tape V1; no dream/paper/Target fill is injected.",
            "Market-level chronological split; no random row leakage.",
            "No probability threshold sweep and no PnL tuning.",
            "Target teacher fields are used only for independent post-hoc examination.",
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(json.dumps({
        "ok": True, "artifact": str(ART), "report": str(REPORT), "rows": int(len(d)),
        "markets": int(d.market_id.nunique()), "splitMarkets": report["splitMarkets"],
        "metrics": report_metrics, "targetPosthocExam3s": teacher_exam,
    }, ensure_ascii=False, allow_nan=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
