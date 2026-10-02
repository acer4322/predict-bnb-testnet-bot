from __future__ import annotations
import json, math, sys
from pathlib import Path
from collections import Counter
import numpy as np
from sklearn.metrics import roc_auc_score, balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.evaluate_r4_repair_exact_pathstate_ab_v1 import load_teacher,load_path,enriched_values,impute,model_factory,BASE_FEATURES,PATH_FEATURES
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
OUT=P/'r4_repair_exact_pathstate_signal_anatomy_v1.json'


def build():
    t=load_teacher();p=load_path();rows=[]
    for mid in sorted(set(t)&set(p)):
        c=str(t[mid].get('branchClass'))
        if c not in {'PARETO_BENEFICIAL','PARETO_HARMFUL'}:continue
        vals=enriched_values(t[mid],p[mid],'EXACT_STATE_PATH')
        rows.append({'marketId':mid,'class':c,'v':vals})
    return rows

def loo(rows,features):
    X=np.asarray([[r['v'].get(f,math.nan) for f in features] for r in rows],float)
    y=np.asarray([1 if r['class']=='PARETO_BENEFICIAL' else 0 for r in rows],int)
    pr=np.zeros(len(rows))
    for i in range(len(rows)):
        mask=np.arange(len(rows))!=i;tr,te=impute(X[mask],X[i:i+1]);m=model_factory('LOGISTIC_BALANCED');m.fit(tr,y[mask]);pr[i]=m.predict_proba(te)[0,1]
    return y,pr

def met(y,p):
    pred=(p>=.5).astype(int)
    return {'auc':float(roc_auc_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,pred))}

def main():
    rows=build();sets={
      'EXACT_STATE':BASE_FEATURES,
      'PATH_ONLY_COMPACT':['activeMakerOrders']+PATH_FEATURES,
      'EXACT_STATE_PLUS_AVAIL':BASE_FEATURES+PATH_FEATURES[:4],
      'EXACT_STATE_PLUS_AGE':BASE_FEATURES+['ageSkewS'],
      'EXACT_STATE_PLUS_QUOTE':BASE_FEATURES+['quoteOffsetGapTicks'],
      'EXACT_STATE_PLUS_DEPLETION':BASE_FEATURES+['logDepletionGap'],
      'EXACT_STATE_PATH':BASE_FEATURES+PATH_FEATURES,
    }
    preds={};res={}
    for k,f in sets.items():
        y,p=loo(rows,f);preds[k]=p;res[k]={'features':f,**met(y,p)}
    # leave-one-feature-out from the full compact path representation
    lof={}
    for drop in PATH_FEATURES:
        f=BASE_FEATURES+[x for x in PATH_FEATURES if x!=drop]
        _,p=loo(rows,f);lof[drop]=met(y,p)
    # paired stratified bootstrap of fixed LOO predictions: uncertainty of observed score delta, not a promotion test.
    rng=np.random.default_rng(20260830);benef=np.where(y==1)[0];harm=np.where(y==0)[0];deltas=[]
    pb=preds['EXACT_STATE'];pp=preds['EXACT_STATE_PATH']
    for _ in range(5000):
        idx=np.r_[rng.choice(benef,len(benef),replace=True),rng.choice(harm,len(harm),replace=True)]
        try:deltas.append(float(roc_auc_score(y[idx],pp[idx])-roc_auc_score(y[idx],pb[idx])))
        except Exception:pass
    arr=np.asarray(deltas,float)
    # Stronger influence check: remove each market, retrain all LOO predictions on the reduced development set.
    jack=[]
    for drop_i,r in enumerate(rows):
        sub=[x for j,x in enumerate(rows) if j!=drop_i]
        yy,ps=loo(sub,BASE_FEATURES);_,ppath=loo(sub,BASE_FEATURES+PATH_FEATURES)
        jack.append({'droppedMarketId':r['marketId'],'deltaAuc':float(roc_auc_score(yy,ppath)-roc_auc_score(yy,ps))})
    j=np.asarray([x['deltaAuc'] for x in jack],float)
    rep={'version':'R4_REPAIR_EXACT_PATHSTATE_SIGNAL_ANATOMY_V1','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,
         'marketCount':len(rows),'classCounts':dict(Counter(r['class'] for r in rows)),'representations':res,'leaveOnePathFeatureOut':lof,
         'pairedBootstrapAucDelta':{'deltaObserved':res['EXACT_STATE_PATH']['auc']-res['EXACT_STATE']['auc'],'samples':len(arr),'pDeltaPositive':float(np.mean(arr>0)),'p025':float(np.quantile(arr,.025)),'median':float(np.quantile(arr,.5)),'p975':float(np.quantile(arr,.975))},
         'dropOneMarketRefitAucDelta':{'positiveCount':int(np.sum(j>0)),'n':len(j),'min':float(np.min(j)),'median':float(np.median(j)),'max':float(np.max(j)),'rows':jack},
         'interpretationBoundary':'Development-only representation audit. Bootstrap conditions on frozen LOO predictions; drop-one-market refit is the stronger sensitivity check. No thresholds/features are promoted from this result.'}
    OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8')
    print(json.dumps({k:rep[k] for k in ['marketCount','classCounts','representations','leaveOnePathFeatureOut','pairedBootstrapAucDelta','dropOneMarketRefitAucDelta']},indent=2))
if __name__=='__main__':main()
