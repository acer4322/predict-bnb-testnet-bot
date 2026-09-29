from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier, HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, roc_auc_score
from sklearn.tree import DecisionTreeClassifier, export_text

ROOT0 = Path(__file__).resolve().parents[1]
if str(ROOT0) not in sys.path:
    sys.path.insert(0, str(ROOT0))

from tools.evaluate_r4_repair_counterfactual_value_critic_v1 import ROOT, P, FEATURES, consolidate, row_features, fnum

OUT = P / 'r4_repair_counterfactual_nonlinear_probe_v1.json'


def data():
    rows=[]
    for r in consolidate():
        rf=row_features(r); d=r.get('delta')
        if rf is None or not isinstance(d,dict): continue
        df=fnum(d.get('finalFloor')); da=fnum(d.get('finalAbsNet'))
        if not math.isfinite(df) or not math.isfinite(da): continue
        x,raw=rf
        rows.append({'marketId':int(r['marketId']),'class':r['branchClass'],'x':x,'raw':raw})
    return rows


def impute(train,test):
    med=np.nanmedian(train,axis=0); med=np.where(np.isfinite(med),med,0.0)
    return np.where(np.isfinite(train),train,med),np.where(np.isfinite(test),test,med),med


def standardize(train,test):
    tr,te,med=impute(train,test); mu=tr.mean(axis=0); sd=tr.std(axis=0);sd=np.where(sd>1e-9,sd,1.0)
    return (tr-mu)/sd,(te-mu)/sd


def models():
    return {
        'LOGISTIC_BALANCED': lambda: LogisticRegression(C=1.0,class_weight='balanced',max_iter=3000,random_state=20260830),
        'TREE_D2_LEAF4': lambda: DecisionTreeClassifier(max_depth=2,min_samples_leaf=4,class_weight='balanced',random_state=20260830),
        'TREE_D3_LEAF4': lambda: DecisionTreeClassifier(max_depth=3,min_samples_leaf=4,class_weight='balanced',random_state=20260830),
        'GB_STUMPS_40': lambda: GradientBoostingClassifier(n_estimators=40,learning_rate=0.05,max_depth=1,min_samples_leaf=4,random_state=20260830),
        'GB_D2_30': lambda: GradientBoostingClassifier(n_estimators=30,learning_rate=0.05,max_depth=2,min_samples_leaf=4,random_state=20260830),
        'HGB_D2': lambda: HistGradientBoostingClassifier(max_depth=2,max_iter=50,learning_rate=0.05,min_samples_leaf=5,l2_regularization=1.0,random_state=20260830),
        'RF_D2': lambda: RandomForestClassifier(n_estimators=200,max_depth=2,min_samples_leaf=3,class_weight='balanced',random_state=20260830,n_jobs=1),
    }


def main():
    rows=data(); X=np.asarray([r['x'] for r in rows],float)
    yclass=[r['class'] for r in rows]
    is_bh=np.asarray([c in {'PARETO_BENEFICIAL','PARETO_HARMFUL'} for c in yclass])
    y=np.asarray([1 if c=='PARETO_BENEFICIAL' else 0 for c in yclass],int)
    results={}
    probs_by={}
    for name,mk in models().items():
        probs=np.full(len(rows),np.nan)
        for i in range(len(rows)):
            train_idx=np.asarray([j for j in range(len(rows)) if j!=i and is_bh[j]],int)
            tr=X[train_idx]; te=X[i:i+1]
            if name.startswith('LOGISTIC'):
                tr2,te2=standardize(tr,te)
            else:
                tr2,te2,_=impute(tr,te)
            m=mk();m.fit(tr2,y[train_idx]);probs[i]=float(m.predict_proba(te2)[0,1])
        probs_by[name]=probs
        pbh=probs[is_bh]; ybh=y[is_bh]
        pred=(pbh>=0.5).astype(int)
        approve=probs>=0.65; veto=probs<=0.35; abstain=~(approve|veto)
        beneficial=np.asarray([c=='PARETO_BENEFICIAL' for c in yclass]);harmful=np.asarray([c=='PARETO_HARMFUL' for c in yclass]);other=~(beneficial|harmful)
        def rate(n,d): return float(n/d) if d else math.nan
        results[name]={
            'bhN':int(is_bh.sum()),'auc':float(roc_auc_score(ybh,pbh)),'balancedAccuracyAt050':float(balanced_accuracy_score(ybh,pred)),
            'beneficialRecallAt050':rate(int(np.sum((pbh>=.5)&(ybh==1))),int(np.sum(ybh==1))),
            'harmfulRecallAt050':rate(int(np.sum((pbh<.5)&(ybh==0))),int(np.sum(ybh==0))),
            'triageThresholds':{'approveMin':.65,'vetoMax':.35},
            'decisionCoverage':float(np.mean(~abstain)),'abstainRate':float(np.mean(abstain)),
            'beneficialOpportunityRetention':rate(int(np.sum(approve&beneficial)),int(beneficial.sum())),
            'harmfulVetoRecall':rate(int(np.sum(veto&harmful)),int(harmful.sum())),
            'approvePrecisionBeneficial':rate(int(np.sum(approve&beneficial)),int(approve.sum())),
            'approveHarmfulRate':rate(int(np.sum(approve&harmful)),int(approve.sum())),
            'vetoPrecisionHarmful':rate(int(np.sum(veto&harmful)),int(veto.sum())),
            'vetoBeneficialRate':rate(int(np.sum(veto&beneficial)),int(veto.sum())),
            'otherExtremeDecisionRate':rate(int(np.sum((approve|veto)&other)),int(other.sum())),
            'harmfulFalseApproveCount':int(np.sum(approve&harmful)),'beneficialFalseVetoCount':int(np.sum(veto&beneficial)),
        }
    # Final-fit interpretability for only the tiny trees/stumps; development inspection only.
    bh_idx=np.where(is_bh)[0]; tr,_,med=impute(X[bh_idx],X[:1])
    interpretations={}
    for name in ['TREE_D2_LEAF4','TREE_D3_LEAF4','GB_STUMPS_40','GB_D2_30','RF_D2']:
        m=models()[name]();m.fit(tr,y[bh_idx])
        if hasattr(m,'feature_importances_'):
            imp=np.asarray(m.feature_importances_,float)
            interpretations[name]={'featureImportance':{FEATURES[i]:float(imp[i]) for i in np.argsort(-imp) if imp[i]>0}}
        if name.startswith('TREE_'):
            interpretations[name]['tree']=export_text(m,feature_names=FEATURES)
    rep={'version':'R4_REPAIR_COUNTERFACTUAL_NONLINEAR_PROBE_V1','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,
         'marketCount':len(rows),'classCounts':dict(Counter(yclass)),'features':FEATURES,
         'validation':'leave-one-market-out; model family/parameters fixed before scoring this probe; beneficial-vs-harmful training only; tradeoff/no-effect used only to audit abstention/extreme predictions',
         'results':results,'interpretations':interpretations,
         'rows':[{'marketId':rows[i]['marketId'],'actualClass':yclass[i],**{name:float(probs_by[name][i]) for name in probs_by}} for i in range(len(rows))],
         'boundary':'Development diagnostic only. Do not select a runtime model from this same dataset without freezing a candidate and validating on new market-disjoint causal/HFT evidence.'}
    OUT.write_text(json.dumps(rep,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'marketCount':len(rows),'classCounts':rep['classCounts'],'results':results},indent=2,allow_nan=True))

if __name__=='__main__': main()
