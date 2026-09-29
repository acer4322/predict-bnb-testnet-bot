from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import train_student_state_act_adapter_v1 as a
import train_student_state_supervisor_v0 as ss

OUT = ROOT / "data" / "research" / "supervisor_options_v0"
CUR = ROOT / "data" / "research" / "supervisor_curriculum_v0"
TARGET = ROOT / "data" / "target_wallet_official_v1.db"
a.TARGET_STATES = OUT / "supervisor_target_act_states_v2.csv"
a.TEACHER_ART = OUT / "supervisor_target_act_large_v2_full699.joblib"

WINDOWS = [20, 30, 40, 48]
EPS = 1e-12


def ll(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    p = np.clip(p, EPS, 1 - EPS)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def safe_auc(y: np.ndarray, p: np.ndarray):
    return float(roc_auc_score(y, p)) if np.unique(y).size > 1 else None


def safe_ap(y: np.ndarray, p: np.ndarray):
    return float(average_precision_score(y, p)) if np.unique(y).size > 1 else None


def main():
    d, mem, _ = ss.build()
    d = d.sort_values(["market_end_ms", "market_id", "checkpoint_ms"]).reset_index(drop=True)
    d["exact_act"] = d.teacher_mode.ne("HOLD").astype(int)
    d, _ = a.align_soft_teacher(d)

    con = sqlite3.connect(f"file:{TARGET.resolve().as_posix()}?mode=ro", uri=True)
    participating = {
        int(r[0])
        for r in con.execute(
            "select distinct market_id from wallet_shadow_target_events where asset='BTC' and quote_type='BID'"
        )
    }
    con.close()

    ms = a.market_order(d)
    pids = [int(x) for x in ms.market_id if int(x) in participating]
    features = ss.CURRENT + mem

    ctx = pd.read_csv(CUR / "student_act_rolling_context_v0_markets.csv")
    ctx = ctx.rename(columns={"marketId": "market_id"})
    catalog = pd.read_csv(CUR / "ordinary_market_curriculum_catalog_v0.csv")
    catalog = catalog.rename(columns={"marketId": "market_id"})
    catalog_keep = [
        "market_id", "primaryLesson", "difficultyTier", "lessonTags", "lessonPurity",
        "skillEntropy", "distinctSkills", "channelSwitches", "skillSwitches",
        "transitionDensity", "stressScore", "switchScore", "difficultyScore",
        "makerRepairRate", "takerRepairRate", "makerActions", "takerActions"
    ]
    catalog = catalog[[c for c in catalog_keep if c in catalog.columns]]

    rows = []
    window_summary = {}
    for n in WINDOWS:
        assert n + 10 <= len(pids)
        train_ids = set(pids[:n])
        test_ids = pids[n:n + 10]
        tr = d[d.market_id.astype(int).isin(train_ids) & d.teacher_soft_act.notna()].copy()
        te = d[d.market_id.astype(int).isin(set(test_ids))].copy()

        hard, hf = a.fit_hard(tr, features, 20260820)
        soft, sf = a.fit_soft_binary(
            tr, features, tr.teacher_soft_act.astype(float).to_numpy(), 20260821
        )
        ph = a.p_act(hard, te, hf)
        ps = a.p_act(soft, te, sf)
        te = te[["market_id", "market_end_ms", "checkpoint_ms", "exact_act"]].copy()
        te["hard_p"] = ph
        te["soft_p"] = ps
        y = te.exact_act.astype(int).to_numpy()
        te["hard_ll"] = ll(y, ph)
        te["soft_ll"] = ll(y, ps)
        te["ll_delta_soft_minus_hard"] = te.soft_ll - te.hard_ll
        te["hard_brier"] = (ph - y) ** 2
        te["soft_brier"] = (ps - y) ** 2
        te["brier_delta_soft_minus_hard"] = te.soft_brier - te.hard_brier

        market_rows = []
        for mid, g in te.groupby("market_id", sort=False):
            yy = g.exact_act.astype(int).to_numpy()
            hp = g.hard_p.to_numpy(float)
            sp = g.soft_p.to_numpy(float)
            market_rows.append({
                "window_train_n": n,
                "market_id": int(mid),
                "rows": int(len(g)),
                "positive_rate": float(np.mean(yy)),
                "hard_mean_p": float(np.mean(hp)),
                "soft_mean_p": float(np.mean(sp)),
                "hard_log_loss": float(np.mean(g.hard_ll)),
                "soft_log_loss": float(np.mean(g.soft_ll)),
                "ll_delta_soft_minus_hard": float(np.mean(g.ll_delta_soft_minus_hard)),
                "hard_brier": float(np.mean(g.hard_brier)),
                "soft_brier": float(np.mean(g.soft_brier)),
                "brier_delta_soft_minus_hard": float(np.mean(g.brier_delta_soft_minus_hard)),
                "hard_auc": safe_auc(yy, hp),
                "soft_auc": safe_auc(yy, sp),
                "hard_ap": safe_ap(yy, hp),
                "soft_ap": safe_ap(yy, sp),
            })
        mdf = pd.DataFrame(market_rows)
        rows.append(mdf)

        hard_metric = a.metric_exact(te.exact_act.astype(int), ph)
        soft_metric = a.metric_exact(te.exact_act.astype(int), ps)
        window_summary[f"n{n}"] = {
            "trainMarkets": n,
            "testMarketIds": [int(x) for x in test_ids],
            "rows": int(len(te)),
            "hard": hard_metric,
            "soft": soft_metric,
            "liftSoftMinusHard": {
                "auc": soft_metric["auc"] - hard_metric["auc"],
                "ap": soft_metric["ap"] - hard_metric["ap"],
                "logLoss": soft_metric["logLoss"] - hard_metric["logLoss"],
            },
        }

    detail = pd.concat(rows, ignore_index=True)
    detail = detail.merge(ctx, on="market_id", how="left", suffixes=("", "_ctx"))
    detail = detail.merge(catalog, on="market_id", how="left")
    detail["soft_ll_better"] = detail.ll_delta_soft_minus_hard < 0
    detail["soft_brier_better"] = detail.brier_delta_soft_minus_hard < 0
    detail_path = CUR / "student_act_participated_curriculum_context_v1_markets.csv"
    detail.to_csv(detail_path, index=False)

    def group_stats(df: pd.DataFrame, col: str):
        out = {}
        for key, g in df.groupby(col, dropna=False):
            k = "MISSING" if pd.isna(key) else str(key)
            out[k] = {
                "evaluationAppearances": int(len(g)),
                "uniqueMarkets": int(g.market_id.nunique()),
                "meanLlDeltaSoftMinusHard": float(g.ll_delta_soft_minus_hard.mean()),
                "medianLlDeltaSoftMinusHard": float(g.ll_delta_soft_minus_hard.median()),
                "softLlBetterFrac": float(g.soft_ll_better.mean()),
                "meanBrierDeltaSoftMinusHard": float(g.brier_delta_soft_minus_hard.mean()),
                "softBrierBetterFrac": float(g.soft_brier_better.mean()),
                "meanPositiveRate": float(g.positive_rate.mean()),
            }
        return out

    failure = detail[detail.window_train_n.isin([30, 40])].copy()
    all_eval = detail.copy()

    report = {
        "reportVersion": "STUDENT_ACT_PARTICIPATED_CURRICULUM_CONTEXT_V1",
        "researchOnly": True,
        "question": "Which curriculum lesson/context is associated with unstable Soft Teacher transfer in participated-only chronological ACT/HOLD rolling exams?",
        "participatingMarketsTotal": int(len(pids)),
        "windows": window_summary,
        "coverage": {
            "evaluationAppearances": int(len(detail)),
            "uniqueEvaluationMarkets": int(detail.market_id.nunique()),
            "catalogMatchedAppearances": int(detail.primaryLesson.notna().sum()) if "primaryLesson" in detail else 0,
            "contextMatchedAppearances": int(detail.ourIndex.notna().sum()) if "ourIndex" in detail else 0,
            "overlapNote": "n48 is a learning-curve/remaining-holdout exam and overlaps n40 on participating indices 48-49; grouped counts retain evaluation appearances and also report unique markets."
        },
        "allWindowsByPrimaryLesson": group_stats(all_eval, "primaryLesson") if "primaryLesson" in all_eval else {},
        "allWindowsByDifficultyTier": group_stats(all_eval, "difficultyTier") if "difficultyTier" in all_eval else {},
        "failureWindowsN30N40ByPrimaryLesson": group_stats(failure, "primaryLesson") if "primaryLesson" in failure else {},
        "failureWindowsN30N40ByDifficultyTier": group_stats(failure, "difficultyTier") if "difficultyTier" in failure else {},
        "worstSoftMinusHardMarkets": detail.sort_values("ll_delta_soft_minus_hard", ascending=False)[
            [c for c in ["window_train_n", "market_id", "ll_delta_soft_minus_hard", "positive_rate", "primaryLesson", "difficultyTier", "lessonPurity", "skillEntropy", "transitionDensity", "takerShareOfAct", "modeSwitchRate", "actClusteredFrac", "readinessRate"] if c in detail.columns]
        ].head(12).replace({np.nan: None}).to_dict(orient="records"),
        "bestSoftMinusHardMarkets": detail.sort_values("ll_delta_soft_minus_hard", ascending=True)[
            [c for c in ["window_train_n", "market_id", "ll_delta_soft_minus_hard", "positive_rate", "primaryLesson", "difficultyTier", "lessonPurity", "skillEntropy", "transitionDensity", "takerShareOfAct", "modeSwitchRate", "actClusteredFrac", "readinessRate"] if c in detail.columns]
        ].head(12).replace({np.nan: None}).to_dict(orient="records"),
        "interpretationGuard": "Descriptive diagnosis only. Do not tune a model/threshold from these rolling labels. Any lesson/context hypothesis must be tested on independent future ordinary data.",
        "guards": [
            "Confirmed Target-participating ordinary markets only for rolling exams.",
            "No winner/PnL features or labels.",
            "2026-08-16 special markets remain sealed.",
            "final75-99 remains sealed.",
            "Fixed model capacity/seeds; no threshold sweep.",
            "No runtime changes; 8784/8786 frozen PAPER and 8781/8782 untouched."
        ],
        "files": {"marketDetail": str(detail_path.relative_to(ROOT)).replace('\\\\', '/')}
    }

    report_path = CUR / "student_act_participated_curriculum_context_v1_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
