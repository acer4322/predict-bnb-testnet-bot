from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.train_eth_passive_lifecycle_memory_feasibility_v2 as v2

def ledger_features(r):
    s=r['seq']; m=r['mask']>0; z=s[m]
    if len(z)==0:return np.zeros(12,np.float32)
    rel=z[:,0]; dab=z[:,1]; dpc=z[:,2]; dfl=z[:,3]; elapsed=z[:,4]; qgr=z[:,5]
    # recent objective identity and structured lifecycle memory
    last=float(rel[-1]); streak=0
    for x in rel[::-1]:
        if x==last:streak+=1
        else:break
    repair=(rel==1); expand=(rel==-1)
    repair_progress=float(np.maximum(0,-dab[repair]).sum()) if repair.any() else 0.
    repair_pair_gain=float(np.maximum(0,dpc[repair]).sum()) if repair.any() else 0.
    repair_floor_gain=float(np.maximum(0,dfl[repair]).sum()) if repair.any() else 0.
    expand_pressure=float(np.maximum(0,-dpc[expand]).sum()) if expand.any() else 0.
    switches=float(np.sum(rel[1:]!=rel[:-1])) if len(rel)>1 else 0.
    return np.asarray([last,streak/8.,float(repair.mean()),float(expand.mean()),repair_progress,repair_pair_gain,repair_floor_gain,expand_pressure,switches/7.,float(elapsed[-1]),float(np.mean(elapsed)),float(np.mean(qgr))],np.float32)

def met(y,p):
    y=np.asarray(y,int);pred=(p>=.5).astype(int)
    return {'n':len(y),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,pred))}

def fit_eval(tr,te,task,use_ledger):
    a=[r for r in tr if r[task] is not None];b=[r for r in te if r[task] is not None]
    def X(rows):
        base=np.stack([r['cur'] for r in rows])
        if not use_ledger:return base
        led=np.stack([ledger_features(r) for r in rows])
        return np.concatenate([base,led],axis=1)
    Xtr,Xte=X(a),X(b); ytr=np.asarray([r[task] for r in a],int);yte=np.asarray([r[task] for r in b],int)
    # deterministic thinning for quick local feasibility
    if len(Xtr)>50000:
        ii=np.linspace(0,len(Xtr)-1,50000).astype(int);Xtr=Xtr[ii];ytr=ytr[ii]
    m=HistGradientBoostingClassifier(max_iter=140,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=35,l2_regularization=4.,class_weight='balanced',random_state=31)
    m.fit(Xtr,ytr);p=m.predict_proba(Xte)[:,1]
    return met(yte,p)

def main():
    db=ROOT/'data/research/r4_v0/p0_provenance_v1/target_eth_btc_strategy_compare_snapshot_v1.db'
    rows,cut,nwin=v2.build(str(db));tr=[r for r in rows if r['end']<cut];te=[r for r in rows if r['end']>=cut]
    out={'version':'ETH_PASSIVE_OBJECTIVE_LEDGER_FEASIBILITY_V3_LOCAL','researchOnly':True,'chronologyCutoff':cut,'windows':nwin,'tasks':{}}
    for t in v2.TASKS:
        s=fit_eval(tr,te,t,False);l=fit_eval(tr,te,t,True);out['tasks'][t]={'static':s,'staticPlusLedger':l,'aucDelta':l['auc']-s['auc']}
    out['interpretationBoundary']=['ETH actual-filled Target Maker chronology only','No BTC labels, no winner/future PnL, no 18-unit inference','Ledger features are structured summaries of already-materialized ETH Maker lifecycle history','Feasibility only; not HFT profitability evidence']
    p=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_passive_objective_ledger_feasibility_v3_local.json';p.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False))
if __name__=='__main__':main()
