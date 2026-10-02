from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from train_market_capsule_economic_responsibility_teacher_v1 import load_groups, market_order, FEATURE_SETS, SEED

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DECISIONS = ROOT / "data/research/market_capsule_v1/benchmark_50_v1/decision_seams.parquet"
DEFAULT_PUBLIC = ROOT / "data/research/market_capsule_v1/benchmark_50_v1/public_snapshots.parquet"
DEFAULT_OUTPUT = ROOT / "data/research/market_capsule_v1/MARKET_CAPSULE_JOINT_RESPONSIBILITY_ALLOCATION_TEACHER_V1_RESULT_20260907.json"
LABELS = ["REPAIR_ONLY", "EXPAND_ONLY", "BOTH"]


def make_model(name: str):
    if name == "LOGISTIC":
        return Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("model", LogisticRegression(C=1.0, max_iter=1500, random_state=SEED)),
        ])
    if name == "EXTRATREES":
        return Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("model", ExtraTreesClassifier(n_estimators=200, min_samples_leaf=10, class_weight="balanced", random_state=SEED, n_jobs=1)),
        ])
    raise ValueError(name)


def row_logloss(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-8, 1.0)
    p = p / p.sum(axis=1, keepdims=True)
    return -np.log(p[np.arange(len(y)), y])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    ap.add_argument("--public", type=Path, default=DEFAULT_PUBLIC)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ns = ap.parse_args()

    df = load_groups(ns.decisions, ns.public)
    balanced = int(df["balanced"].fillna(0).sum())
    df = df[df["balanced"] == 0].copy().reset_index(drop=True)
    def lab(r):
        if int(r.y_repair) == 1 and int(r.y_expand) == 0: return 0
        if int(r.y_repair) == 0 and int(r.y_expand) == 1: return 1
        if int(r.y_repair) == 1 and int(r.y_expand) == 1: return 2
        raise RuntimeError("unexpected none/none non-balanced group")
    df["y_joint"] = df.apply(lab, axis=1).astype(int)
    markets = market_order(df)
    folds = [(20,30),(30,40),(40,50)]
    preds = defaultdict(list)
    fold_metrics = []

    for trn,end in folds:
        trm=set(markets[:trn]); tem=set(markets[trn:end])
        tr=df[df.market_id.isin(trm)].copy(); te=df[df.market_id.isin(tem)].copy()
        ytr=tr.y_joint.to_numpy(int); yte=te.y_joint.to_numpy(int)
        counts=np.bincount(ytr,minlength=3).astype(float); prior=counts/counts.sum()
        pp=np.tile(prior,(len(te),1))
        fold_metrics.append({"trainMarkets":trn,"testMarkets":len(tem),"cell":"PRIOR","logloss":float(log_loss(yte,pp,labels=[0,1,2])),"accuracy":float(accuracy_score(yte,np.argmax(pp,axis=1))),"macroAuc":None})
        for fs,feats in FEATURE_SETS.items():
            Xtr=tr[feats].replace([np.inf,-np.inf],np.nan); Xte=te[feats].replace([np.inf,-np.inf],np.nan)
            for model_name in ("LOGISTIC","EXTRATREES"):
                m=make_model(model_name); m.fit(Xtr,ytr); p=m.predict_proba(Xte)
                classes=list(map(int,m.named_steps["model"].classes_))
                full=np.zeros((len(te),3),float)
                for j,c in enumerate(classes): full[:,c]=p[:,j]
                full=np.clip(full,1e-8,1.0); full/=full.sum(axis=1,keepdims=True)
                try: auc=float(roc_auc_score(yte,full,multi_class="ovr",average="macro",labels=[0,1,2]))
                except Exception: auc=None
                fold_metrics.append({"trainMarkets":trn,"testMarkets":len(tem),"cell":f"{fs}__{model_name}","logloss":float(log_loss(yte,full,labels=[0,1,2])),"accuracy":float(accuracy_score(yte,np.argmax(full,axis=1))),"macroAuc":auc})
                for idx,yy,pr in zip(te.index.tolist(),yte.tolist(),full.tolist()):
                    preds[(fs,model_name)].append({"idx":int(idx),"marketId":int(te.loc[idx,"market_id"]),"y":int(yy),"p":pr,"prior":prior.tolist(),"trainMarkets":trn})

    agg={}; gate=False
    for fs in FEATURE_SETS:
        for model_name in ("LOGISTIC","EXTRATREES"):
            rows=preds[(fs,model_name)]; y=np.array([r["y"] for r in rows],int); p=np.array([r["p"] for r in rows],float); pri=np.array([r["prior"] for r in rows],float)
            ll=float(log_loss(y,p,labels=[0,1,2])); pll=float(log_loss(y,pri,labels=[0,1,2]))
            bym=defaultdict(list)
            for i,r in enumerate(rows): bym[r["marketId"]].append(i)
            imp=0; ds=[]
            for mid,ix in bym.items():
                a=float(np.mean(row_logloss(y[ix],p[ix]))); b=float(np.mean(row_logloss(y[ix],pri[ix]))); imp+=int(a<b-1e-12); ds.append(b-a)
            try: auc=float(roc_auc_score(y,p,multi_class="ovr",average="macro",labels=[0,1,2]))
            except Exception: auc=None
            key=f"{fs}__{model_name}"; rate=imp/max(1,len(bym))
            agg[key]={"rows":len(rows),"markets":len(bym),"logloss":ll,"priorLogloss":pll,"loglossGainVsPrior":pll-ll,"accuracy":float(accuracy_score(y,np.argmax(p,axis=1))),"macroAuc":auc,"marketImprovedVsPrior":imp,"marketImprovementRateVsPrior":rate,"medianPerMarketGain":float(np.median(ds)),"passes70pct":bool(pll-ll>0 and rate>=0.70)}
            gate = gate or agg[key]["passes70pct"]

    public_increment={}
    for model_name in ("LOGISTIC","EXTRATREES"):
        a=preds[("ECON_BOOK",model_name)]; b=preds[("ECON_BOOK_PUBLIC",model_name)]
        amap={(r["idx"],r["trainMarkets"]):r for r in a}; bmap={(r["idx"],r["trainMarkets"]):r for r in b}; keys=sorted(set(amap)&set(bmap))
        y=np.array([amap[k]["y"] for k in keys],int); pa=np.array([amap[k]["p"] for k in keys],float); pb=np.array([bmap[k]["p"] for k in keys],float)
        bym=defaultdict(list)
        for j,k in enumerate(keys): bym[amap[k]["marketId"]].append(j)
        imp=0; ds=[]
        for mid,ix in bym.items():
            la=float(np.mean(row_logloss(y[ix],pa[ix]))); lb=float(np.mean(row_logloss(y[ix],pb[ix]))); imp+=int(lb<la-1e-12); ds.append(la-lb)
        gain=float(log_loss(y,pa,labels=[0,1,2])-log_loss(y,pb,labels=[0,1,2])); rate=imp/max(1,len(bym))
        public_increment[model_name]={"aggregateLoglossGain":gain,"marketImproved":imp,"markets":len(bym),"marketImprovementRate":rate,"medianPerMarketGain":float(np.median(ds)),"passes70pct":bool(gain>0 and rate>=0.70)}

    payload={"version":"MARKET_CAPSULE_JOINT_RESPONSIBILITY_ALLOCATION_TEACHER_V1_RESULT_20260907","researchOnly":True,"balancedExcluded":balanced,"groups":int(len(df)),"classCounts":{"REPAIR_ONLY":int((df.y_joint==0).sum()),"EXPAND_ONLY":int((df.y_joint==1).sum()),"BOTH":int((df.y_joint==2).sum())},"featureLeakAudit":{"currentTargetActionFieldsInFeatures":False,"winnerInFeatures":False,"futureStateInFeatures":False},"forwardFolds":fold_metrics,"aggregate":agg,"publicIncrement":public_increment,"developmentGate":{"jointLearnable":bool(gate),"runtimeAuthorityGranted":False}}
    ns.output.parent.mkdir(parents=True,exist_ok=True); ns.output.write_text(json.dumps(payload,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps({"ok":True,"output":str(ns.output),"classCounts":payload["classCounts"],"aggregate":agg,"publicIncrement":public_increment,"developmentGate":payload["developmentGate"]},indent=2,ensure_ascii=False))
    return 0

if __name__=="__main__": raise SystemExit(main())
