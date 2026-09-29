from __future__ import annotations

import glob
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "data" / "research" / "r4_v0" / "p0_provenance_v1"
OUT = P / "r4_repair_counterfactual_value_critic_v1_score.json"
DATASET_OUT = P / "r4_r3_repair_counterfactual_teacher_v1_consolidated_20260830.json"

VALID_CLASSES = {"PARETO_BENEFICIAL", "PARETO_HARMFUL", "TRADEOFF", "NO_EFFECT"}
ALPHAS = [0.1, 1.0, 10.0, 100.0]
FEATURES = [
    "riskAgeS",
    "activeMakerOrders",
    "bookAgeS",
    "makerAbsNet",
    "makerCoverage",
    "preFloor",
    "preUpside",
    "makerPairEdge",
    "pRepairMaker",
    "pDominantMaker",
    "pResidualWake",
    "secondsLeft",
    "directionInteraction",
    "predictPairAskEdge",
]


def candidate_files() -> list[Path]:
    vals = set()
    for pat in [
        str(P / "*repair*counterfactual*json"),
        str(P / "*repair*cf*json"),
        str(ROOT / "data/research/lan_worker_returns/r4-repair-cf-formal20-exact-*/teacher.json"),
    ]:
        vals.update(Path(x) for x in glob.glob(pat))
    return sorted(vals)


def rows_from(obj):
    if isinstance(obj, dict) and isinstance(obj.get("rows"), list):
        return obj["rows"]
    if isinstance(obj, dict) and obj.get("marketId") is not None and obj.get("branchClass"):
        return [obj]
    return []


def quality(r: dict) -> int:
    q = 0
    if r.get("exactBranchApplied") is True:
        q += 100
    for key, base in [("candidate", 20), ("baseline", 10), ("counterfactual", 10), ("delta", 30)]:
        v = r.get(key)
        if isinstance(v, dict):
            q += base + sum(x is not None for x in v.values())
    return q


def consolidate() -> list[dict]:
    by = {}
    for f in candidate_files():
        try:
            obj = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for r in rows_from(obj):
            if not isinstance(r, dict) or r.get("branchClass") not in VALID_CLASSES:
                continue
            try:
                mid = int(r["marketId"])
            except Exception:
                continue
            item = (quality(r), str(f.relative_to(ROOT)), r)
            if mid not in by or item[0] > by[mid][0]:
                by[mid] = item
    out = []
    for mid, (q, src, r) in sorted(by.items()):
        rr = dict(r)
        rr["selectedSource"] = src
        rr["selectionQuality"] = q
        out.append(rr)
    DATASET_OUT.write_text(json.dumps({
        "version": "R4_R3_REPAIR_COUNTERFACTUAL_TEACHER_V1_CONSOLIDATED_20260830",
        "researchOnly": True,
        "actionAuthority": False,
        "promotionEvidence": False,
        "selectionPolicy": "one row per market; exact/full candidate+baseline+counterfactual+delta completeness preferred over mtime",
        "marketCount": len(out),
        "classCounts": dict(Counter(r["branchClass"] for r in out)),
        "rows": out,
    }, indent=2, allow_nan=True), encoding="utf-8")
    return out


def fnum(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else math.nan
    except Exception:
        return math.nan


def row_features(r: dict) -> tuple[list[float], dict] | None:
    c = r.get("candidate")
    if not isinstance(c, dict):
        return None
    p = c.get("portfolio")
    m = c.get("models")
    u = c.get("public")
    if not all(isinstance(x, dict) for x in [p, m, u]):
        return None
    if c.get("activeMakerOrders") is None:
        return None
    maker_net = fnum(p.get("maker_net"))
    p_up = fnum(m.get("pMakerUp"))
    p_dn = fnum(m.get("pMakerDown"))
    if math.isfinite(maker_net) and maker_net > 0:
        p_repair, p_dom = p_dn, p_up
    elif math.isfinite(maker_net) and maker_net < 0:
        p_repair, p_dom = p_up, p_dn
    else:
        p_repair = max(p_up, p_dn) if math.isfinite(p_up) and math.isfinite(p_dn) else math.nan
        p_dom = min(p_up, p_dn) if math.isfinite(p_up) and math.isfinite(p_dn) else math.nan
    direction = fnum(u.get("directionScore"))
    up_ask = fnum(u.get("predictUpAsk")); dn_ask = fnum(u.get("predictDownAsk"))
    pair_ask_edge = 1.0 - up_ask - dn_ask if math.isfinite(up_ask) and math.isfinite(dn_ask) else math.nan
    vals = {
        "riskAgeS": fnum(c.get("riskAgeMs")) / 1000.0,
        "activeMakerOrders": fnum(c.get("activeMakerOrders")),
        "bookAgeS": fnum(c.get("bookAgeMs")) / 1000.0,
        "makerAbsNet": fnum(p.get("maker_abs_net")),
        "makerCoverage": fnum(p.get("maker_paired_coverage")),
        "preFloor": fnum(p.get("worst_case_floor")),
        "preUpside": fnum(p.get("best_case_pnl")),
        "makerPairEdge": fnum(p.get("maker_avg_pair_edge")),
        "pRepairMaker": p_repair,
        "pDominantMaker": p_dom,
        "pResidualWake": fnum(m.get("pResidualWake")),
        "secondsLeft": fnum(u.get("secondsLeft")),
        "directionInteraction": maker_net * direction / 18.0 if math.isfinite(maker_net) and math.isfinite(direction) else math.nan,
        "predictPairAskEdge": pair_ask_edge,
    }
    return [vals[k] for k in FEATURES], vals


def fit_transform(train_x: np.ndarray, test_x: np.ndarray):
    med = np.nanmedian(train_x, axis=0)
    med = np.where(np.isfinite(med), med, 0.0)
    tr = np.where(np.isfinite(train_x), train_x, med)
    te = np.where(np.isfinite(test_x), test_x, med)
    mu = tr.mean(axis=0)
    sd = tr.std(axis=0)
    sd = np.where(sd > 1e-9, sd, 1.0)
    return (tr - mu) / sd, (te - mu) / sd, med, mu, sd


def fit_predict(train_x, train_y, test_x, alpha):
    tr, te, *_ = fit_transform(train_x, test_x)
    model = Ridge(alpha=float(alpha))
    model.fit(tr, train_y)
    return np.asarray(model.predict(te), float)


def inner_alpha(x: np.ndarray, y: np.ndarray) -> float:
    # Nested market-disjoint LOO. Target columns are normalized inside the score
    # so floor and abs-net benefit receive comparable weight.
    if len(x) <= 4:
        return 10.0
    scales = np.nanstd(y, axis=0)
    scales = np.where(scales > 1e-9, scales, 1.0)
    best = None
    for alpha in ALPHAS:
        errs = []
        for i in range(len(x)):
            mask = np.arange(len(x)) != i
            pred = fit_predict(x[mask], y[mask], x[i:i+1], alpha)[0]
            errs.append(np.mean(((pred - y[i]) / scales) ** 2))
        score = float(np.mean(errs))
        if best is None or score < best[0] - 1e-12 or (abs(score-best[0]) <= 1e-12 and alpha > best[1]):
            best = (score, alpha)
    return float(best[1])


def classify(df: float, abs_benefit: float, eps=1e-9):
    if df >= -eps and abs_benefit >= -eps and (df > eps or abs_benefit > eps):
        return "PARETO_BENEFICIAL"
    if df <= eps and abs_benefit <= eps and (df < -eps or abs_benefit < -eps):
        return "PARETO_HARMFUL"
    if abs(df) <= eps and abs(abs_benefit) <= eps:
        return "NO_EFFECT"
    return "TRADEOFF"


def safe_spearman(a, b):
    if len(a) < 3 or np.nanstd(a) < 1e-12 or np.nanstd(b) < 1e-12:
        return math.nan
    return float(spearmanr(a, b).statistic)


def main():
    all_rows = consolidate()
    usable = []
    for r in all_rows:
        rf = row_features(r)
        d = r.get("delta")
        if rf is None or not isinstance(d, dict):
            continue
        df = fnum(d.get("finalFloor")); da = fnum(d.get("finalAbsNet"))
        if not math.isfinite(df) or not math.isfinite(da):
            continue
        x, raw = rf
        usable.append({"marketId": int(r["marketId"]), "class": r["branchClass"], "x": x, "rawFeatures": raw, "deltaFloor": df, "absNetBenefit": -da, "source": r.get("selectedSource")})

    X = np.asarray([r["x"] for r in usable], float)
    Y = np.asarray([[r["deltaFloor"], r["absNetBenefit"]] for r in usable], float)
    preds = np.zeros_like(Y)
    chosen = []
    for i in range(len(usable)):
        mask = np.arange(len(usable)) != i
        alpha = inner_alpha(X[mask], Y[mask])
        chosen.append(alpha)
        preds[i] = fit_predict(X[mask], Y[mask], X[i:i+1], alpha)[0]

    actual_cls = [classify(y[0], y[1]) for y in Y]
    pred_cls = [classify(y[0], y[1]) for y in preds]
    triage = ["APPROVE" if c == "PARETO_BENEFICIAL" else "VETO" if c == "PARETO_HARMFUL" else "ABSTAIN" for c in pred_cls]

    beneficial = np.asarray([c == "PARETO_BENEFICIAL" for c in actual_cls])
    harmful = np.asarray([c == "PARETO_HARMFUL" for c in actual_cls])
    approve = np.asarray([z == "APPROVE" for z in triage])
    veto = np.asarray([z == "VETO" for z in triage])
    abstain = ~(approve | veto)

    def rate(num, den):
        return float(num / den) if den else math.nan

    metrics = {
        "floorSpearman": safe_spearman(Y[:,0], preds[:,0]),
        "absNetBenefitSpearman": safe_spearman(Y[:,1], preds[:,1]),
        "floorMae": float(np.mean(np.abs(preds[:,0]-Y[:,0]))),
        "absNetBenefitMae": float(np.mean(np.abs(preds[:,1]-Y[:,1]))),
        "floorSignAccuracy": float(np.mean(np.sign(preds[:,0]) == np.sign(Y[:,0]))),
        "absNetBenefitSignAccuracy": float(np.mean(np.sign(preds[:,1]) == np.sign(Y[:,1]))),
        "decisionCoverage": float(np.mean(~abstain)),
        "abstainRate": float(np.mean(abstain)),
        "approveCount": int(approve.sum()),
        "vetoCount": int(veto.sum()),
        "approvePrecisionBeneficial": rate(int(np.sum(approve & beneficial)), int(approve.sum())),
        "approveHarmfulRate": rate(int(np.sum(approve & harmful)), int(approve.sum())),
        "beneficialOpportunityRetention": rate(int(np.sum(approve & beneficial)), int(beneficial.sum())),
        "harmfulVetoRecall": rate(int(np.sum(veto & harmful)), int(harmful.sum())),
        "vetoPrecisionHarmful": rate(int(np.sum(veto & harmful)), int(veto.sum())),
        "vetoBeneficialRate": rate(int(np.sum(veto & beneficial)), int(veto.sum())),
        "beneficialFalseVetoCount": int(np.sum(veto & beneficial)),
        "harmfulFalseApproveCount": int(np.sum(approve & harmful)),
    }

    # Development-only heuristic audit: the former zero-active-order abstention idea.
    active0 = np.asarray([float(r["rawFeatures"]["activeMakerOrders"]) == 0.0 for r in usable])
    active0_counts = Counter(actual_cls[i] for i in range(len(usable)) if active0[i])

    # Fit final interpretable ridge only for coefficient inspection; it has NO action authority.
    alpha_final = inner_alpha(X, Y)
    tr, _, med, mu, sd = fit_transform(X, X[:1])
    final_model = Ridge(alpha=alpha_final).fit(tr, Y)
    coef = {
        target: {FEATURES[j]: float(final_model.coef_[ti, j]) for j in range(len(FEATURES))}
        for ti, target in enumerate(["deltaFloor", "absNetBenefit"])
    }

    rows_out = []
    for i, r in enumerate(usable):
        rows_out.append({
            "marketId": r["marketId"], "actualClass": actual_cls[i], "predictedClass": pred_cls[i], "triage": triage[i],
            "actualDeltaFloor": float(Y[i,0]), "predDeltaFloor": float(preds[i,0]),
            "actualAbsNetBenefit": float(Y[i,1]), "predAbsNetBenefit": float(preds[i,1]),
            "outerChosenAlpha": chosen[i], "features": r["rawFeatures"], "source": r["source"],
        })

    rep = {
        "version": "R4_REPAIR_COUNTERFACTUAL_VALUE_CRITIC_V1_SCORE",
        "researchOnly": True,
        "actionAuthority": False,
        "promotionEvidence": False,
        "teacherMarketsTotal": len(all_rows),
        "strictFeatureReadyMarkets": len(usable),
        "strictFeatureClassCounts": dict(Counter(actual_cls)),
        "featureNames": FEATURES,
        "targets": {"deltaFloor": "counterfactual floor - wait floor; positive better", "absNetBenefit": "wait absNet - counterfactual absNet; positive better"},
        "validation": "nested leave-one-market-out; one exact candidate row per market; alpha chosen only inside each training fold",
        "alphaGrid": ALPHAS,
        "outerChosenAlphaCounts": dict(Counter(str(x) for x in chosen)),
        "metrics": metrics,
        "formerActiveMakerOrdersZeroAudit": {"n": int(active0.sum()), "classCounts": dict(active0_counts), "decision": "REJECT_AS_STANDALONE_VETO_OR_ABSTENTION_GATE"},
        "finalDevelopmentFit": {"alpha": alpha_final, "standardizedCoefficients": coef, "imputationMedian": {FEATURES[j]: float(med[j]) for j in range(len(FEATURES))}, "standardizationMean": {FEATURES[j]: float(mu[j]) for j in range(len(FEATURES))}, "standardizationStd": {FEATURES[j]: float(sd[j]) for j in range(len(FEATURES))}},
        "rows": rows_out,
        "interpretationBoundary": "V1 is a development critic diagnostic only. APPROVE/VETO/ABSTAIN are offline counterfactual triage labels, not runtime authority. Anti-conservatism must be checked via beneficialOpportunityRetention and later HFT participation/opportunity-retention gates.",
        "dataset": str(DATASET_OUT.relative_to(ROOT)),
    }
    OUT.write_text(json.dumps(rep, indent=2, allow_nan=True), encoding="utf-8")
    print(json.dumps({"teacherMarketsTotal": len(all_rows), "strictFeatureReadyMarkets": len(usable), "classCounts": rep["strictFeatureClassCounts"], "metrics": metrics, "active0Audit": rep["formerActiveMakerOrdersZeroAudit"], "alphaFinal": alpha_final}, indent=2, allow_nan=True))


if __name__ == "__main__":
    main()
