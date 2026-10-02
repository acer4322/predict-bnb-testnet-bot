from __future__ import annotations

import json
import warnings
import sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import balanced_accuracy_score, confusion_matrix, log_loss, roc_auc_score, average_precision_score

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DATA_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
DATA = DATA_DIR / "open_order_fill_lifecycle_v0_dataset.csv"
FILL_ART = DATA_DIR / "open_order_fill_lifecycle_v0.joblib"
ART = DATA_DIR / "execution_aware_pending_management_v0.joblib"
REPORT = DATA_DIR / "execution_aware_pending_management_v0_report.json"
SEED = 20260821

from tools.train_open_order_fill_lifecycle_v0 import FEATURES

KEEP_LABELS = {"KEEP_RESTING_STRONG", "KEEP_RESTING_TARGET_SUPPORTED"}
RELEASE_LABELS = {"RELEASE_FOR_REDECISION_PROXY"}


def metric(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    y = np.asarray(y, dtype=int)
    p = np.clip(np.asarray(p, dtype=float), 1e-7, 1 - 1e-7)
    pred = (p >= 0.5).astype(int)
    cm = confusion_matrix(y, pred, labels=[0, 1])
    tn, fp, fn, tp = [int(x) for x in cm.ravel()]
    return {
        "n": int(len(y)), "continuePending": int(y.sum()), "continueRate": float(y.mean()),
        "predMean": float(p.mean()),
        "auc": float(roc_auc_score(y, p)) if len(set(y.tolist())) > 1 else None,
        "ap": float(average_precision_score(y, p)) if y.sum() else None,
        "logLoss": float(log_loss(y, p, labels=[0, 1])),
        "balancedAccuracyAtNatural0p5": float(balanced_accuracy_score(y, pred)),
        "confusionAtNatural0p5": {"tnRelease": tn, "fpKeep": fp, "fnRelease": fn, "tpKeep": tp},
    }


def top_terms(model: ExplainableBoostingClassifier, n: int = 20) -> list[dict[str, Any]]:
    imp = list(model.term_importances())
    names = list(model.term_names_)
    ix = sorted(range(len(imp)), key=lambda i: float(imp[i]), reverse=True)[:n]
    return [{"term": str(names[i]), "importance": float(imp[i])} for i in ix]


def main() -> int:
    warnings.filterwarnings("ignore", message="X does not have valid feature names")
    d = pd.read_csv(DATA, low_memory=False)
    d = d[d.teacher_execution_label.isin(KEEP_LABELS | RELEASE_LABELS)].copy()
    d["label_continue_pending"] = d.teacher_execution_label.isin(KEEP_LABELS).astype(int)
    d = d.sort_values(["checkpoint_ms", "market_id", "order_id"]).reset_index(drop=True)

    markets = d.groupby("market_id", as_index=False)["checkpoint_ms"].max().sort_values(["checkpoint_ms", "market_id"])
    ids = markets.market_id.astype(int).tolist()
    a = int(len(ids) * 0.70)
    b = int(len(ids) * 0.85)
    splits = {"train": set(ids[:a]), "validation": set(ids[a:b]), "test": set(ids[b:])}
    parts = {k: d[d.market_id.astype(int).isin(v)].copy() for k, v in splits.items()}

    tr = parts["train"]
    ytr = tr.label_continue_pending.astype(int).to_numpy()
    n0 = max(1, int((ytr == 0).sum()))
    n1 = max(1, int((ytr == 1).sum()))
    # Equal total class mass prevents the rare CONTINUE teacher from being ignored. This is training
    # weighting only; validation/test metrics below remain unweighted on their natural chronology.
    w = np.where(ytr == 1, len(ytr) / (2.0 * n1), len(ytr) / (2.0 * n0))
    model = ExplainableBoostingClassifier(
        feature_names=FEATURES,
        max_bins=64,
        max_interaction_bins=16,
        interactions=4,
        outer_bags=4,
        learning_rate=0.03,
        max_rounds=900,
        early_stopping_rounds=60,
        min_samples_leaf=12,
        n_jobs=-2,
        random_state=SEED,
    )
    model.fit(tr[FEATURES].apply(pd.to_numeric, errors="coerce"), ytr, sample_weight=w)

    metrics = {}
    for name, x in parts.items():
        y = x.label_continue_pending.astype(int).to_numpy()
        p = model.predict_proba(x[FEATURES].apply(pd.to_numeric, errors="coerce"))[:, 1]
        metrics[name] = metric(y, p)

    artifact = {
        "version": "EXECUTION_AWARE_PENDING_MANAGEMENT_V0",
        "features": FEATURES,
        "model": model,
        "classSemantics": {"1": "CONTINUE_PENDING_INTENT", "0": "RELEASE_FOR_REDECISION"},
        "trainingMarkets": sorted(splits["train"]),
        "validationMarkets": sorted(splits["validation"]),
        "testMarkets": sorted(splits["test"]),
        "dreamFillAllowed": False,
        "runtimeTargetDataAllowed": False,
        "teacherSemantics": {
            "continue": "HFT own order fills within <=5s AND high-confidence Target lifecycle independently supports KEEP/no-new-parent proxy",
            "release": "HFT own order does not fill within 5s AND Target active same-side lifecycle creates a different-price high-confidence parent within 1s",
        },
    }
    joblib.dump(artifact, ART)

    report = {
        "reportVersion": "EXECUTION_AWARE_PENDING_MANAGEMENT_V0",
        "researchOnly": True,
        "liveTradingChanges": False,
        "studentScale": "PRE_CAP100_ORIGINAL_R2",
        "dataset": {
            "source": str(DATA), "rows": int(len(d)), "markets": int(d.market_id.nunique()),
            "continueRows": int(d.label_continue_pending.sum()), "releaseRows": int((1 - d.label_continue_pending).sum()),
            "continueTeacherLabels": sorted(KEEP_LABELS), "releaseTeacherLabels": sorted(RELEASE_LABELS),
        },
        "splitMarkets": {k: len(v) for k, v in splits.items()},
        "metrics": metrics,
        "topTerms": top_terms(model),
        "artifact": str(ART),
        "runtimeContract": {
            "allowed": "same strict-past Student own open-order/public execution features as OPEN_ORDER_FILL_LIFECYCLE_V0",
            "forbidden": "Target fields, future HFT fill fields, teacher label, winner, settlement PnL",
            "output": "CONTINUE_PENDING_INTENT vs RELEASE_FOR_REDECISION; RELEASE does not itself mean cancel/reprice/taker",
        },
        "guards": [
            "Target is used only to form post-episode teacher labels, never runtime features.",
            "RELEASE_FOR_REDECISION deliberately returns control to the frozen Strategy Brain; it is not a new alpha/action policy.",
            "Natural 0.5 classification is reported only as a diagnostic; no threshold sweep or PnL optimization was performed.",
            "No CAP100 constraint is present in this curriculum.",
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(json.dumps({"ok": True, "artifact": str(ART), "report": str(REPORT), "dataset": report["dataset"], "splitMarkets": report["splitMarkets"], "metrics": metrics, "topTerms": report["topTerms"][:12]}, ensure_ascii=False, allow_nan=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
