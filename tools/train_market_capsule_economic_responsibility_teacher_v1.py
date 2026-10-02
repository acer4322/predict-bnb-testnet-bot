from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DECISIONS = ROOT / "data/research/market_capsule_v1/benchmark_50_v1/decision_seams.parquet"
DEFAULT_PUBLIC = ROOT / "data/research/market_capsule_v1/benchmark_50_v1/public_snapshots.parquet"
DEFAULT_OUTPUT = ROOT / "data/research/market_capsule_v1/MARKET_CAPSULE_ECONOMIC_RESPONSIBILITY_TEACHER_V1_RESULT_20260907.json"
EPS = 1e-9
SEED = 20260907

ECON_BOOK = [
    "seconds_left", "pre_floor", "pre_upside", "pre_surplus", "pre_abs_share_gap",
    "gap_fraction_of_total_shares", "floor_to_abs_upside", "target_prior_fill_legs",
    "target_prior_parent_count", "receipt_strict_spread", "expand_side_bid", "repair_side_bid",
    "expand_side_bid_depth", "repair_side_bid_depth", "book_imbalance_for_expand",
    "receipt_strict_order_count", "receipt_strict_book_received_age_ms", "public_age_ms",
]
PUBLIC_EXTRA = [
    "direction_score_for_expand", "spot_queue_imbalance_for_expand", "futures_queue_imbalance_for_expand",
    "spot_taker_imbalance_250ms_for_expand", "spot_taker_imbalance_1s_for_expand",
    "futures_taker_imbalance_250ms_for_expand", "futures_taker_imbalance_1s_for_expand",
    "spot_return_250ms_for_expand", "spot_return_1s_for_expand", "spot_return_3s_for_expand",
    "spot_return_5s_for_expand", "futures_return_250ms_for_expand", "futures_return_1s_for_expand",
    "futures_return_3s_for_expand", "futures_return_5s_for_expand", "spot_minus_strike_for_expand",
    "chainlink_minus_strike_for_expand", "spot_minus_chainlink_for_expand", "perp_spot_basis_for_expand",
    "volatility_alert_flag",
]
FEATURE_SETS = {"ECON_BOOK": ECON_BOOK, "ECON_BOOK_PUBLIC": ECON_BOOK + PUBLIC_EXTRA}


def finite(v, default=np.nan):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def public_num(d: dict, key: str):
    return finite(d.get(key))


def load_groups(decisions: Path, public: Path) -> pd.DataFrame:
    dpath = decisions.resolve().as_posix().replace("'", "''")
    ppath = public.resolve().as_posix().replace("'", "''")
    con = duckdb.connect(database=":memory:")
    try:
        df = con.execute(f"""
            SELECT d.market_id, d.action_event_ms, d.seconds_left,
                   d.target_prior_fill_legs, d.target_prior_parent_count,
                   d.pre_up_shares, d.pre_down_shares, d.pre_floor, d.pre_upside,
                   d.pre_surplus, d.pre_abs_share_gap, d.action_side,
                   d.receipt_strict_best_bid, d.receipt_strict_best_ask,
                   d.receipt_strict_spread, d.receipt_strict_bid_depth_total,
                   d.receipt_strict_ask_depth_total, d.receipt_strict_order_count,
                   d.receipt_strict_book_received_age_ms, d.public_age_ms,
                   d.public_sample_id, p.snapshot_json
            FROM read_parquet('{dpath}') d
            LEFT JOIN read_parquet('{ppath}') p ON p.id = d.public_sample_id
            ORDER BY d.action_event_ms, d.market_id
        """).fetchdf()
    finally:
        con.close()

    out = []
    for (mid, t), g in df.groupby(["market_id", "action_event_ms"], sort=False):
        r = g.iloc[0]
        up = finite(r.pre_up_shares, 0.0)
        dn = finite(r.pre_down_shares, 0.0)
        if abs(up - dn) <= EPS:
            # No structurally defined weak/strong side; preserve for audit but exclude primary fit.
            out.append({"market_id": int(mid), "action_event_ms": int(t), "balanced": 1})
            continue
        weak = "UP" if up < dn else "DOWN"
        strong = "DOWN" if weak == "UP" else "UP"
        sides = {str(x) for x in g.action_side.tolist() if str(x) in {"UP", "DOWN"}}
        y_repair = int(weak in sides)
        y_expand = int(strong in sides)
        bid = finite(r.receipt_strict_best_bid)
        ask = finite(r.receipt_strict_best_ask)
        bid_depth = finite(r.receipt_strict_bid_depth_total)
        ask_depth = finite(r.receipt_strict_ask_depth_total)
        if strong == "UP":
            expand_bid, repair_bid = bid, (1.0 - ask if math.isfinite(ask) else np.nan)
            expand_depth, repair_depth = bid_depth, ask_depth
            sign = 1.0
        else:
            expand_bid, repair_bid = (1.0 - ask if math.isfinite(ask) else np.nan), bid
            expand_depth, repair_depth = ask_depth, bid_depth
            sign = -1.0
        denom_depth = expand_depth + repair_depth if math.isfinite(expand_depth) and math.isfinite(repair_depth) else np.nan
        book_imb = (expand_depth - repair_depth) / denom_depth if math.isfinite(denom_depth) and denom_depth > EPS else np.nan
        total_sh = max(up + dn, EPS)
        upside = finite(r.pre_upside)
        pub = {}
        raw = r.snapshot_json
        if isinstance(raw, str) and raw:
            try:
                pub = json.loads(raw)
            except Exception:
                pub = {}
        rec = {
            "market_id": int(mid), "action_event_ms": int(t), "balanced": 0,
            "weak_side": weak, "expand_side": strong,
            "y_repair": y_repair, "y_expand": y_expand,
            "seconds_left": finite(r.seconds_left), "pre_floor": finite(r.pre_floor),
            "pre_upside": upside, "pre_surplus": finite(r.pre_surplus),
            "pre_abs_share_gap": abs(up - dn), "gap_fraction_of_total_shares": abs(up - dn) / total_sh,
            "floor_to_abs_upside": finite(r.pre_floor) / max(abs(upside), 1.0),
            "target_prior_fill_legs": finite(r.target_prior_fill_legs),
            "target_prior_parent_count": finite(r.target_prior_parent_count),
            "receipt_strict_spread": finite(r.receipt_strict_spread),
            "expand_side_bid": expand_bid, "repair_side_bid": repair_bid,
            "expand_side_bid_depth": expand_depth, "repair_side_bid_depth": repair_depth,
            "book_imbalance_for_expand": book_imb,
            "receipt_strict_order_count": finite(r.receipt_strict_order_count),
            "receipt_strict_book_received_age_ms": finite(r.receipt_strict_book_received_age_ms),
            "public_age_ms": finite(r.public_age_ms),
            "direction_score_for_expand": sign * public_num(pub, "directionScore"),
            "spot_queue_imbalance_for_expand": sign * public_num(pub, "spotQueueImbalance"),
            "futures_queue_imbalance_for_expand": sign * public_num(pub, "futuresQueueImbalance"),
            "spot_taker_imbalance_250ms_for_expand": sign * public_num(pub, "spotTakerImbalance250ms"),
            "spot_taker_imbalance_1s_for_expand": sign * public_num(pub, "spotTakerImbalance1s"),
            "futures_taker_imbalance_250ms_for_expand": sign * public_num(pub, "futuresTakerImbalance250ms"),
            "futures_taker_imbalance_1s_for_expand": sign * public_num(pub, "futuresTakerImbalance1s"),
            "spot_return_250ms_for_expand": sign * public_num(pub, "spotReturn250msBps"),
            "spot_return_1s_for_expand": sign * public_num(pub, "spotReturn1sBps"),
            "spot_return_3s_for_expand": sign * public_num(pub, "spotReturn3sBps"),
            "spot_return_5s_for_expand": sign * public_num(pub, "spotReturn5sBps"),
            "futures_return_250ms_for_expand": sign * public_num(pub, "futuresReturn250msBps"),
            "futures_return_1s_for_expand": sign * public_num(pub, "futuresReturn1sBps"),
            "futures_return_3s_for_expand": sign * public_num(pub, "futuresReturn3sBps"),
            "futures_return_5s_for_expand": sign * public_num(pub, "futuresReturn5sBps"),
            "spot_minus_strike_for_expand": sign * public_num(pub, "spotMinusStrikeBps"),
            "chainlink_minus_strike_for_expand": sign * public_num(pub, "chainlinkMinusStrikeBps"),
            "spot_minus_chainlink_for_expand": sign * public_num(pub, "spotMinusChainlinkBps"),
            "perp_spot_basis_for_expand": sign * public_num(pub, "perpSpotBasisBps"),
            "volatility_alert_flag": 0.0 if str(pub.get("volatilityAlert", "NORMAL")) == "NORMAL" else 1.0,
        }
        out.append(rec)
    return pd.DataFrame(out)


def market_order(df: pd.DataFrame) -> list[int]:
    x = df.groupby("market_id")["action_event_ms"].min().sort_values()
    return [int(v) for v in x.index.tolist()]


def make_model(name: str):
    if name == "LOGISTIC":
        return Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("model", LogisticRegression(C=1.0, max_iter=1000, random_state=SEED)),
        ])
    if name == "EXTRATREES":
        return Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("model", ExtraTreesClassifier(n_estimators=200, min_samples_leaf=10, class_weight="balanced", random_state=SEED, n_jobs=1)),
        ])
    raise ValueError(name)


def safe_auc(y, p):
    return float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else None


def binary_logloss(y, p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, int)
    return float(np.mean(-(y * np.log(p) + (1-y) * np.log(1-p))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    ap.add_argument("--public", type=Path, default=DEFAULT_PUBLIC)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ns = ap.parse_args()

    df_all = load_groups(ns.decisions, ns.public)
    balanced = int(df_all.get("balanced", pd.Series(dtype=int)).fillna(0).sum())
    df = df_all[df_all["balanced"] == 0].copy().reset_index(drop=True)
    markets = market_order(df)
    folds = [(20, 30), (30, 40), (40, 50)]
    pred_store: dict[tuple[str,str,str], list[dict]] = defaultdict(list)
    fold_rows = []
    for train_n, end_n in folds:
        trm = set(markets[:train_n]); tem = set(markets[train_n:end_n])
        train = df[df.market_id.isin(trm)].copy(); test = df[df.market_id.isin(tem)].copy()
        for head, ycol in [("REPAIR", "y_repair"), ("EXPAND", "y_expand")]:
            prior = float(np.clip(train[ycol].mean(), 1e-6, 1 - 1e-6))
            ytest = test[ycol].to_numpy(int)
            pp = np.full(len(test), prior, float)
            prior_ll = binary_logloss(ytest, pp)
            fold_rows.append({"trainMarkets": train_n, "testMarkets": len(tem), "head": head, "cell": "PRIOR", "logloss": prior_ll, "auc": safe_auc(ytest, pp), "brier": float(brier_score_loss(ytest, pp))})
            for fs_name, feats in FEATURE_SETS.items():
                Xtr = train[feats].replace([np.inf,-np.inf], np.nan)
                Xte = test[feats].replace([np.inf,-np.inf], np.nan)
                for model_name in ("LOGISTIC", "EXTRATREES"):
                    m = make_model(model_name)
                    m.fit(Xtr, train[ycol].to_numpy(int))
                    p = m.predict_proba(Xte)[:,1]
                    fold_rows.append({"trainMarkets": train_n, "testMarkets": len(tem), "head": head, "cell": f"{fs_name}__{model_name}", "logloss": binary_logloss(ytest,p), "auc": safe_auc(ytest,p), "brier": float(brier_score_loss(ytest,p))})
                    for idx, prob, yy in zip(test.index.tolist(), p.tolist(), ytest.tolist()):
                        pred_store[(head,fs_name,model_name)].append({"idx": int(idx), "marketId": int(test.loc[idx,"market_id"]), "y": int(yy), "p": float(prob), "trainMarkets": train_n, "prior": prior})
                    # Store prior aligned to the same test rows for per-market comparisons.
                    for idx, yy in zip(test.index.tolist(), ytest.tolist()):
                        pred_store[(head,"PRIOR","PRIOR")].append({"idx": int(idx), "marketId": int(test.loc[idx,"market_id"]), "y": int(yy), "p": prior, "trainMarkets": train_n, "prior": prior})

    aggregate = {}
    head_pass = {}
    for head in ("REPAIR","EXPAND"):
        prior_rows = pred_store[(head,"PRIOR","PRIOR")]
        # duplicated 4x due insertion inside model loops; dedupe by (idx,trainMarkets)
        prmap = {(r["idx"],r["trainMarkets"]):r for r in prior_rows}
        prior_rows = list(prmap.values())
        yprior=np.array([r["y"] for r in prior_rows],int); pprior=np.array([r["p"] for r in prior_rows],float)
        prior_ll = binary_logloss(yprior,pprior)
        best_pass=False
        for fs_name in FEATURE_SETS:
            for model_name in ("LOGISTIC","EXTRATREES"):
                rows=pred_store[(head,fs_name,model_name)]
                y=np.array([r["y"] for r in rows],int); p=np.array([r["p"] for r in rows],float)
                bym=defaultdict(list); bymp=defaultdict(list)
                prior_lookup={(r["idx"],r["trainMarkets"]):r for r in prior_rows}
                for r in rows:
                    bym[r["marketId"]].append(r)
                    bymp[r["marketId"]].append(prior_lookup[(r["idx"],r["trainMarkets"])])
                improved=0; market_deltas=[]
                for mid in sorted(bym):
                    a=bym[mid]; b=bymp[mid]
                    ll=binary_logloss([x["y"] for x in a],[x["p"] for x in a])
                    pll=binary_logloss([x["y"] for x in b],[x["p"] for x in b])
                    improved += int(ll < pll - 1e-12)
                    market_deltas.append(pll-ll)
                key=f"{fs_name}__{model_name}"
                aggregate[f"{head}__{key}"]={
                    "rows":len(rows), "markets":len(bym), "auc":safe_auc(y,p),
                    "logloss":binary_logloss(y,p), "priorLogloss":prior_ll,
                    "loglossGainVsPrior":prior_ll-binary_logloss(y,p),
                    "brier":float(brier_score_loss(y,p)),
                    "marketImprovedVsPrior":improved,
                    "marketImprovementRateVsPrior":improved/max(1,len(bym)),
                    "medianPerMarketLoglossGain":float(np.median(market_deltas)),
                }
                if aggregate[f"{head}__{key}"]["loglossGainVsPrior"]>0 and aggregate[f"{head}__{key}"]["marketImprovementRateVsPrior"]>=0.70:
                    best_pass=True
        head_pass[head]=best_pass

    # Public increment: compare same-model PUBLIC vs ECON_BOOK, same rows.
    public_increment={}
    for head in ("REPAIR","EXPAND"):
        for model_name in ("LOGISTIC","EXTRATREES"):
            a=pred_store[(head,"ECON_BOOK",model_name)]; b=pred_store[(head,"ECON_BOOK_PUBLIC",model_name)]
            amap={(r["idx"],r["trainMarkets"]):r for r in a}; bmap={(r["idx"],r["trainMarkets"]):r for r in b}
            keys=sorted(set(amap)&set(bmap)); bym=defaultdict(list)
            for k in keys: bym[amap[k]["marketId"]].append(k)
            imp=0; deltas=[]
            for mid,ks in bym.items():
                ll_a=binary_logloss([amap[k]["y"] for k in ks],[amap[k]["p"] for k in ks])
                ll_b=binary_logloss([bmap[k]["y"] for k in ks],[bmap[k]["p"] for k in ks])
                imp += int(ll_b < ll_a - 1e-12); deltas.append(ll_a-ll_b)
            y=np.array([amap[k]["y"] for k in keys]); pa=np.array([amap[k]["p"] for k in keys]); pb=np.array([bmap[k]["p"] for k in keys])
            public_increment[f"{head}__{model_name}"]={
                "aggregateLoglossGain":binary_logloss(y,pa)-binary_logloss(y,pb),
                "marketImproved":imp,"markets":len(bym),"marketImprovementRate":imp/max(1,len(bym)),
                "medianPerMarketGain":float(np.median(deltas)),
                "passes70pct":bool((binary_logloss(y,pa)-binary_logloss(y,pb))>0 and imp/max(1,len(bym))>=0.70),
            }

    # Exact multi-head set accuracy for each cell, fixed threshold 0.5.
    exact_set={}
    for fs_name in FEATURE_SETS:
        for model_name in ("LOGISTIC","EXTRATREES"):
            rr={(x["idx"],x["trainMarkets"]):x for x in pred_store[("REPAIR",fs_name,model_name)]}
            ee={(x["idx"],x["trainMarkets"]):x for x in pred_store[("EXPAND",fs_name,model_name)]}
            keys=sorted(set(rr)&set(ee)); ok=[]
            bym=defaultdict(list)
            for k in keys:
                hit=(int(rr[k]["p"]>=0.5)==rr[k]["y"] and int(ee[k]["p"]>=0.5)==ee[k]["y"])
                ok.append(hit); bym[rr[k]["marketId"]].append(hit)
            exact_set[f"{fs_name}__{model_name}"]={
                "rows":len(ok),"exactSetAccuracy":float(np.mean(ok)),
                "marketMeanExactSetAccuracy":float(np.mean([np.mean(v) for v in bym.values()])),
            }

    labels={
        "groupsPrimary":int(len(df)), "balancedExcluded":balanced, "markets":len(markets),
        "repairPositive":int(df.y_repair.sum()), "expandPositive":int(df.y_expand.sum()),
        "bothPositive":int(((df.y_repair==1)&(df.y_expand==1)).sum()),
        "repairOnly":int(((df.y_repair==1)&(df.y_expand==0)).sum()),
        "expandOnly":int(((df.y_repair==0)&(df.y_expand==1)).sum()),
    }
    payload={
        "version":"MARKET_CAPSULE_ECONOMIC_RESPONSIBILITY_TEACHER_V1_RESULT_20260907",
        "researchOnly":True,
        "featureLeakAudit":{
            "currentTargetActionFieldsInFeatures":False,"winnerInFeatures":False,"futureStateInFeatures":False,
            "publicSnapshotJoin":"strict-past public_sample_id already materialized by Decision Seam V1"
        },
        "labels":labels,"forwardFolds":fold_rows,"aggregate":aggregate,"publicIncrement":public_increment,
        "exactMultiHead":exact_set,
        "developmentGate":{"repairHeadLearnable":bool(head_pass["REPAIR"]),"expandHeadLearnable":bool(head_pass["EXPAND"]),"bothHeadsLearnable":bool(head_pass["REPAIR"] and head_pass["EXPAND"]),"runtimeAuthorityGranted":False},
    }
    ns.output.parent.mkdir(parents=True,exist_ok=True)
    ns.output.write_text(json.dumps(payload,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps({"ok":True,"output":str(ns.output),"labels":labels,"developmentGate":payload["developmentGate"],"aggregate":aggregate,"publicIncrement":public_increment,"exactMultiHead":exact_set},indent=2,ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
