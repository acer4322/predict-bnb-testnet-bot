from __future__ import annotations

"""Falsification test for factorizing manager value into economics + execution.

Uses only consumed/current-V3B research data.

1) Train a causal Execution Head on clean PRE_SUBMIT observational OUR rows.
2) Generate strictly-forward execution predictions for the two Phase-B exact-fork
   candidates (Repair and Re-Expand) at the same prefix.
3) Test whether those predictions improve forward paired action-value prediction
   above frozen candidate-economic geometry alone.
4) Separately add future branch resolution labels as ORACLE execution features to
   estimate an upper bound.  ORACLE is diagnostic only and never runtime-eligible.

No threshold sweep, no winner/Target/future input to causal models, no policy authority.
"""

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

EPS = 1e-9
BLOCKS = ((20, 40), (40, 60), (60, 80), (80, 100))
OUTER = ((20, 40, 40, 60), (20, 60, 60, 80), (20, 80, 80, 100))
TARGETS = ("dFloor", "dBest", "dFavoredPayoff", "dWeakPayoff")
ROLES = ("PROBE_CORE", "ECONOMIC_CORE", "SATELLITE_REPAIR", "SATELLITE_EXPAND")
ROUTES = ("PASSIVE", "ACTIVE")

EXEC_NUMERIC = (
    "state_seconds_left", "state_floor", "state_best", "state_abs_net", "state_coverage", "state_gross",
    "state_debt_up", "state_debt_down", "state_total_debt", "state_oldest_repair_progress",
    "state_oldest_repair_age_ms", "state_responsibility_count", "state_live_slots",
    "state_repair_family_live_slots", "state_satellite_expand_live_slots", "state_pending_cancel_count",
    "state_book_imbalance", "state_spread", "state_dominant_mid", "state_q_ladder_live",
    "state_q_pending_active", "context_target_debt_for_action_side", "context_side_bid", "context_side_ask",
    "context_side_mid", "context_side_is_dominant", "context_side_is_weak", "action_price", "action_qty",
    "action_price_to_bid", "action_ask_to_price", "action_pair_legal",
)


def safe(x: Any, default: float = 0.0) -> float:
    try:
        z = float(x)
        return z if math.isfinite(z) else default
    except Exception:
        return default


def load_parquet(path: Path) -> list[dict[str, Any]]:
    con = duckdb.connect(database=":memory:")
    try:
        rel = con.execute(f"SELECT * FROM read_parquet('{path.as_posix().replace(chr(39), chr(39)*2)}')")
        cols = [x[0] for x in rel.description]
        return [dict(zip(cols, row)) for row in rel.fetchall()]
    finally:
        con.close()


def role_flags(row: dict[str, Any]) -> list[float]:
    role = str(row.get("action_role") or "")
    route = str(row.get("action_route") or "")
    side = str(row.get("action_side") or "")
    repair = row.get("action_is_repair_role")
    expand = row.get("action_is_expand_role")
    if repair is None:
        repair = 1.0 if str(row.get("action_class") or "") == "REPAIR" else 0.0
    if expand is None:
        expand = 1.0 if str(row.get("action_class") or "") == "EXPAND" else 0.0
    return (
        [1.0 if role == r else 0.0 for r in ROLES]
        + [1.0 if route == r else 0.0 for r in ROUTES]
        + [1.0 if side == "UP" else 0.0, safe(repair), safe(expand)]
    )


def exec_x(rows: list[dict[str, Any]]) -> np.ndarray:
    return np.asarray([[safe(r.get(k)) for k in EXEC_NUMERIC] + role_flags(r) for r in rows], dtype=np.float64)


def fit_exec_models(train: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    x = exec_x(train)
    models: dict[str, Any] = {}
    for label, off in (("label_any_fill_5s", 1), ("label_terminal_5s", 2)):
        y = np.asarray([int(r[label]) for r in train], dtype=int)
        m = HistGradientBoostingClassifier(
            max_iter=180, learning_rate=.06, max_leaf_nodes=23, min_samples_leaf=20,
            l2_regularization=2.0, random_state=seed + off,
        ).fit(x, y)
        models[label] = m
    for label, off in (("label_fill_qty_5s", 3), ("label_repair_pay_qty_5s", 4)):
        y = np.asarray([safe(r[label]) for r in train], dtype=float)
        m = HistGradientBoostingRegressor(
            max_iter=180, learning_rate=.06, max_leaf_nodes=23, min_samples_leaf=20,
            l2_regularization=2.0, random_state=seed + off,
        ).fit(x, y)
        models[label] = m
    return models


def exec_predict(models: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, float]]:
    x = exec_x(rows)
    pfill = models["label_any_fill_5s"].predict_proba(x)[:, 1]
    pterm = models["label_terminal_5s"].predict_proba(x)[:, 1]
    qfill = np.maximum(0.0, models["label_fill_qty_5s"].predict(x))
    qrepair = np.maximum(0.0, models["label_repair_pay_qty_5s"].predict(x))
    return [
        {"pFill5": float(pfill[i]), "pTerminal5": float(pterm[i]),
         "predFillQty5": float(qfill[i]), "predRepairPayQty5": float(qrepair[i])}
        for i in range(len(rows))
    ]


def auc_safe(y: np.ndarray, p: np.ndarray) -> float | None:
    return float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else None


def exec_eval(models: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    pred = exec_predict(models, rows)
    yfill = np.asarray([int(r["label_any_fill_5s"]) for r in rows], int)
    yterm = np.asarray([int(r["label_terminal_5s"]) for r in rows], int)
    qfill = np.asarray([safe(r["label_fill_qty_5s"]) for r in rows], float)
    qrepair = np.asarray([safe(r["label_repair_pay_qty_5s"]) for r in rows], float)
    return {
        "rows": len(rows),
        "anyFill5Auc": auc_safe(yfill, np.asarray([x["pFill5"] for x in pred])),
        "terminal5Auc": auc_safe(yterm, np.asarray([x["pTerminal5"] for x in pred])),
        "fillQty5Mae": float(mean_absolute_error(qfill, [x["predFillQty5"] for x in pred])),
        "repairPayQty5Mae": float(mean_absolute_error(qrepair, [x["predRepairPayQty5"] for x in pred])),
    }


def current_payoffs(r: dict[str, Any]) -> tuple[float, float]:
    u, d, c = safe(r["state_up_qty"]), safe(r["state_down_qty"]), safe(r["state_cost"])
    return u - c, d - c


def immediate(r: dict[str, Any]) -> dict[str, float]:
    u, d, c = safe(r["state_up_qty"]), safe(r["state_down_qty"]), safe(r["state_cost"])
    side, p, q = str(r["action_side"]), safe(r["action_price"]), safe(r["action_qty"])
    if side == "UP":
        u += q
    else:
        d += q
    c += p * q
    pu, pd = u - c, d - c
    dom = str(r.get("state_dominant_side") or "")
    fav = pu if dom == "UP" else pd if dom == "DOWN" else max(pu, pd)
    weak = pd if dom == "UP" else pu if dom == "DOWN" else min(pu, pd)
    curu, curd = current_payoffs(r)
    curfav = curu if dom == "UP" else curd if dom == "DOWN" else max(curu, curd)
    curweak = curd if dom == "UP" else curu if dom == "DOWN" else min(curu, curd)
    floor, best = min(pu, pd), max(pu, pd)
    return {
        "postFloor": floor, "postBest": best, "postGap": best - floor,
        "deltaFloor": floor - safe(r["state_floor"]), "deltaBest": best - safe(r["state_best"]),
        "deltaGap": (best - floor) - (safe(r["state_best"]) - safe(r["state_floor"])),
        "deltaFavored": fav - curfav, "deltaWeak": weak - curweak,
    }


def pair_rows(phaseb: list[dict[str, Any]], exec_pred: dict[tuple[int, int, str], dict[str, float]], target_mode: str = "terminal") -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for r in phaseb:
        grouped.setdefault(str(r["pair_id"]), {})[str(r["action_class"])] = r
    out = []
    for pair_id, g in grouped.items():
        if set(g) != {"REPAIR", "EXPAND"}:
            continue
        rr, er = g["REPAIR"], g["EXPAND"]
        ri, ei = immediate(rr), immediate(er)
        def ep(row: dict[str, Any]) -> dict[str, float]:
            k = (int(row["market_id"]), int(row["decision_ms"]), str(row["action_class"]))
            return exec_pred[k]
        rp, xp = ep(rr), ep(er)
        geom = [
            safe(rr["action_price"]), safe(rr["action_qty"]), safe(er["action_price"]), safe(er["action_qty"]),
            safe(rr["action_price_to_bid"]), safe(er["action_price_to_bid"]),
            ri["deltaFloor"], ei["deltaFloor"], ei["deltaFloor"] - ri["deltaFloor"],
            ri["deltaBest"], ei["deltaBest"], ei["deltaBest"] - ri["deltaBest"],
            ri["deltaFavored"], ei["deltaFavored"], ei["deltaFavored"] - ri["deltaFavored"],
            ri["deltaWeak"], ei["deltaWeak"], ei["deltaWeak"] - ri["deltaWeak"],
        ]
        ex = [
            rp["pFill5"], xp["pFill5"], xp["pFill5"] - rp["pFill5"],
            rp["pTerminal5"], xp["pTerminal5"], xp["pTerminal5"] - rp["pTerminal5"],
            rp["predFillQty5"], xp["predFillQty5"], xp["predFillQty5"] - rp["predFillQty5"],
            rp["predRepairPayQty5"], xp["predRepairPayQty5"], xp["predRepairPayQty5"] - rp["predRepairPayQty5"],
        ]
        def oracle(row: dict[str, Any]) -> list[float]:
            lag = row.get("label_resolution_lag_ms")
            return [safe(row.get("label_structural_fill")), 1.0 if lag is not None else 0.0,
                    math.log1p(max(0.0, safe(lag))) if lag is not None else 0.0]
        ro, xo = oracle(rr), oracle(er)
        oc = ro + xo + [xo[i] - ro[i] for i in range(len(ro))]
        if target_mode == "resolution":
            targets = {
                "dFloor": safe(er["label_resolution_delta_floor"]) - safe(rr["label_resolution_delta_floor"]),
                "dBest": safe(er["label_resolution_delta_best"]) - safe(rr["label_resolution_delta_best"]),
                "dFavoredPayoff": safe(er["label_resolution_delta_favored_payoff"]) - safe(rr["label_resolution_delta_favored_payoff"]),
                "dWeakPayoff": safe(er["label_resolution_delta_weak_payoff"]) - safe(rr["label_resolution_delta_weak_payoff"]),
            }
        else:
            targets = {
                "dFloor": safe(er["label_terminal_floor"]) - safe(rr["label_terminal_floor"]),
                "dBest": safe(er["label_terminal_best"]) - safe(rr["label_terminal_best"]),
                "dFavoredPayoff": safe(er["label_terminal_favored_payoff"]) - safe(rr["label_terminal_favored_payoff"]),
                "dWeakPayoff": safe(er["label_terminal_weak_payoff"]) - safe(rr["label_terminal_weak_payoff"]),
            }
        mech = {
            "dFloor": ei["deltaFloor"] - ri["deltaFloor"],
            "dBest": ei["deltaBest"] - ri["deltaBest"],
            "dFavoredPayoff": ei["deltaFavored"] - ri["deltaFavored"],
            "dWeakPayoff": ei["deltaWeak"] - ri["deltaWeak"],
        }
        out.append({"pairId": pair_id, "marketId": int(rr["market_id"]), "t": int(rr["decision_ms"]),
                    "geom": geom, "exec": ex, "oracle": oc, "targets": targets, "mechanical": mech})
    return sorted(out, key=lambda z: z["marketId"])


def fit_value(kind: str, xtr: np.ndarray, ytr: np.ndarray, xte: np.ndarray, seed: int) -> np.ndarray:
    if kind == "EXTRATREES":
        m = ExtraTreesRegressor(n_estimators=300, min_samples_leaf=3, max_features=.75, random_state=seed, n_jobs=1)
    elif kind == "RIDGE":
        m = make_pipeline(StandardScaler(), Ridge(alpha=10.0))
    else:
        raise ValueError(kind)
    return m.fit(xtr, ytr).predict(xte)


def quantile(a: np.ndarray, p: float) -> float:
    return float(np.quantile(np.asarray(a, float), p))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--our", required=True, type=Path)
    ap.add_argument("--phaseb", required=True, type=Path)
    ap.add_argument("--output", required=True)
    ap.add_argument("--target-mode", choices=["terminal", "resolution"], default="terminal")
    a = ap.parse_args()

    our = load_parquet(a.our)
    phaseb = load_parquet(a.phaseb)
    mids = sorted({int(r["market_id"]) for r in phaseb}, key=lambda m: min(int(x["window_end_ms"]) for x in phaseb if int(x["market_id"]) == m))
    if len(mids) != 100:
        raise RuntimeError(f"expected 100 Phase-B markets, got {len(mids)}")
    idx = {m: i for i, m in enumerate(mids)}
    if set(mids) - {int(r["market_id"]) for r in our}:
        raise RuntimeError("Phase-B markets missing from OUR execution corpus")

    exec_pred: dict[tuple[int, int, str], dict[str, float]] = {}
    exec_reports = []
    for bi, (start, end) in enumerate(BLOCKS, 1):
        train_set = set(mids[:start])
        pred_set = set(mids[start:end])
        otr = [r for r in our if int(r["market_id"]) in train_set]
        ote = [r for r in our if int(r["market_id"]) in pred_set]
        models = fit_exec_models(otr, 260900 + bi * 10)
        report = exec_eval(models, ote)
        report.update({"block": bi, "trainMarkets": start, "predictionMarkets": end - start, "trainRows": len(otr)})
        exec_reports.append(report)
        rows = [r for r in phaseb if int(r["market_id"]) in pred_set]
        preds = exec_predict(models, rows)
        for r, p in zip(rows, preds):
            exec_pred[(int(r["market_id"]), int(r["decision_ms"]), str(r["action_class"]))] = p
        print(json.dumps({"executionBlock": bi, **report}, ensure_ascii=False), flush=True)

    pairs = pair_rows([r for r in phaseb if idx[int(r["market_id"])] >= 20], exec_pred, a.target_mode)
    if len(pairs) != 80:
        raise RuntimeError(f"expected 80 forward-covered pairs, got {len(pairs)}")

    oos = []
    fold_reports = []
    for fi, (tr0, tr1, te0, te1) in enumerate(OUTER, 1):
        trm = set(mids[tr0:tr1]); tem = set(mids[te0:te1])
        tr = [r for r in pairs if r["marketId"] in trm]
        te = [r for r in pairs if r["marketId"] in tem]
        if len(tr) != tr1 - tr0 or len(te) != te1 - te0:
            raise RuntimeError("pair fold size mismatch")
        xgtr = np.asarray([r["geom"] for r in tr], float); xgte = np.asarray([r["geom"] for r in te], float)
        xetr = np.asarray([r["geom"] + r["exec"] for r in tr], float); xete = np.asarray([r["geom"] + r["exec"] for r in te], float)
        xotr = np.asarray([r["geom"] + r["oracle"] for r in tr], float); xote = np.asarray([r["geom"] + r["oracle"] for r in te], float)
        pred: dict[str, dict[str, np.ndarray]] = {t: {} for t in TARGETS}; scales = {}
        for ti, target in enumerate(TARGETS):
            ytr = np.asarray([r["targets"][target] for r in tr], float); yte = np.asarray([r["targets"][target] for r in te], float)
            scales[target] = max(float(np.std(ytr)), .25)
            pred[target]["true"] = yte
            pred[target]["MECHANICAL"] = np.asarray([r["mechanical"][target] for r in te], float)
            for kind in ("EXTRATREES", "RIDGE"):
                pred[target][f"{kind}_GEOM"] = fit_value(kind, xgtr, ytr, xgte, 260907 + fi * 100 + ti)
                pred[target][f"{kind}_EXEC"] = fit_value(kind, xetr, ytr, xete, 261907 + fi * 100 + ti)
                pred[target][f"{kind}_ORACLE"] = fit_value(kind, xotr, ytr, xote, 262907 + fi * 100 + ti)
        fr = {"fold": fi, "trainMarkets": len(trm), "testMarkets": len(tem), "targets": {}}
        for target in TARGETS:
            fr["targets"][target] = {}
            for model, pp in pred[target].items():
                if model == "true": continue
                fr["targets"][target][model] = {"mae": float(mean_absolute_error(pred[target]["true"], pp))}
        fold_reports.append(fr)
        for j, r in enumerate(te):
            rec = {"fold": fi, "marketId": r["marketId"], "t": r["t"], "true": {}, "pred": {}, "vectorError": {}}
            for target in TARGETS:
                rec["true"][target] = float(pred[target]["true"][j])
                rec["pred"][target] = {k: float(v[j]) for k, v in pred[target].items() if k != "true"}
            for model in ("MECHANICAL", "EXTRATREES_GEOM", "EXTRATREES_EXEC", "EXTRATREES_ORACLE", "RIDGE_GEOM", "RIDGE_EXEC", "RIDGE_ORACLE"):
                rec["vectorError"][model] = float(np.mean([
                    abs(rec["pred"][t][model] - rec["true"][t]) / scales[t] for t in TARGETS
                ]))
            oos.append(rec)

    summary: dict[str, Any] = {"oosMarkets": len(oos), "models": {}}
    models = ("MECHANICAL", "EXTRATREES_GEOM", "EXTRATREES_EXEC", "EXTRATREES_ORACLE", "RIDGE_GEOM", "RIDGE_EXEC", "RIDGE_ORACLE")
    for model in models:
        ve = np.asarray([r["vectorError"][model] for r in oos], float)
        tm = {}
        for t in TARGETS:
            yy = np.asarray([r["true"][t] for r in oos], float); pp = np.asarray([r["pred"][t][model] for r in oos], float)
            tm[t] = {"mae": float(mean_absolute_error(yy, pp))}
        summary["models"][model] = {"targets": tm, "vectorError": {"mean": float(np.mean(ve)), "median": float(np.median(ve)), "p90": quantile(ve, .9), "max": float(np.max(ve))}}

    def gate(new: str, base: str) -> dict[str, Any]:
        ne = np.asarray([r["vectorError"][new] for r in oos], float); ba = np.asarray([r["vectorError"][base] for r in oos], float)
        d = ba - ne
        improve = int(np.sum(d > EPS)); worse = int(np.sum(d < -EPS)); tie = len(d) - improve - worse
        return {
            "comparison": f"{new}_vs_{base}", "improvedMarkets": improve, "worsenedMarkets": worse, "tiedMarkets": tie,
            "improvedMarketRate": improve / len(d), "meanVectorErrorImprovement": float(np.mean(d)),
            "p10PerMarketImprovement": quantile(d, .1), "aggregateErrorImproves": bool(np.mean(d) > 0),
            "seventyPercentGate": bool(improve / len(d) >= .70),
            "tailGuard": bool(quantile(ne, .9) <= 1.10 * quantile(ba, .9) + EPS),
        }

    primary = gate("EXTRATREES_EXEC", "EXTRATREES_GEOM")
    primary["developmentGatePass"] = bool(primary["seventyPercentGate"] and primary["aggregateErrorImproves"] and primary["tailGuard"])
    oracle = gate("EXTRATREES_ORACLE", "EXTRATREES_GEOM")
    ridge = gate("RIDGE_EXEC", "RIDGE_GEOM")

    verdict = (
        "PREDICTED_EXECUTION_VALUE_ESTABLISHED" if primary["developmentGatePass"] else
        "EXECUTION_RELEVANT_BUT_PREDICTED_HEAD_INSUFFICIENT" if oracle["aggregateErrorImproves"] and oracle["improvedMarketRate"] >= .60 else
        ("EXECUTION_FACTOR_NOT_ESTABLISHED_FOR_LOCAL_RESOLUTION_VALUE" if a.target_mode == "resolution" else "EXECUTION_FACTOR_NOT_ESTABLISHED_FOR_TERMINAL_VALUE")
    )
    report = {
        "version": "MANAGEMENT_V1_EXECUTION_VALUE_FACTORIZATION_V1", "date": "2026-09-07", "researchOnly": True,
        "targetMode": a.target_mode,
        "runtimeAuthority": False, "ourInput": str(a.our), "phasebInput": str(a.phaseb),
        "executionHead": {"features": list(EXEC_NUMERIC) + ["roleOneHot", "routeOneHot", "sideUp", "repairFlag", "expandFlag"], "forwardBlocks": exec_reports},
        "valueFoldDefinition": "strict forward: exec predictions 20->20 blocks; value folds train 20:40 test 40:60, train 20:60 test 60:80, train 20:80 test 80:100",
        "pairCountWithStrictForwardExecutionPredictions": len(pairs), "foldReports": fold_reports, "summary": summary,
        "primaryPredictedExecutionGate": primary, "oracleExecutionDiagnostic": oracle, "ridgeRobustness": ridge,
        "verdict": verdict,
        "guards": [
            "clean PRE_SUBMIT current-V3B state only", "execution head trained only on earlier markets than prediction block",
            "paired action-value model trained only on earlier Phase-B markets than test block", "same-prefix Repair/Re-Expand exact forks",
            "geometry baseline and +execution model use identical train/test markets", "oracle future resolution features diagnostic only and never runtime eligible",
            "winner/settlement/Target future absent from causal features", "fixed model families/hyperparameters; no threshold or hyperparameter sweep",
            "70/30 development analogue used only for incremental predicted-execution gate", "no policy authority/no NEW24-B/no dream fill/no 8781",
        ],
    }
    out = Path(os.environ["BTC5M_LAN_RESULT_DIR"]) / "result.json" if a.output.upper() == "AUTO" and os.environ.get("BTC5M_LAN_RESULT_DIR") else Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"ok": True, "verdict": verdict, "primary": primary, "oracle": oracle, "ridge": ridge, "executionBlocks": exec_reports}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
