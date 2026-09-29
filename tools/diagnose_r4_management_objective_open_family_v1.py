from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import balanced_accuracy_score, roc_auc_score, average_precision_score

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
SRC=P/'r4_p0b_target_objective_topology_rows_v2.csv'
OUT=P/'r4_management_objective_open_family_v1_diagnostic.json'
BASE=['seconds_left','abs_gap','risk_deficit','floor','upside','absNet','coverage','floor_per_gross','current_commitment','seconds_since_prev_parent','events_5s','events_15s','transitions_15s','mode_age_s','distinct_objective_keys_15s']
DELTA=['floor','upside','absNet','coverage','risk_deficit','current_commitment']

def model(seed):
    return HistGradientBoostingClassifier(learning_rate=.06,max_iter=140,max_leaf_nodes=15,min_samples_leaf=35,l2_regularization=1,random_state=seed)

def score(y,p,prob):
    y=np.asarray(y).astype(str);p=np.asarray(p).astype(str);yb=(y=='STATE_SHAPING').astype(int)
    out={'balancedAccuracy':float(balanced_accuracy_score(y,p)),'counts':pd.Series(y).value_counts().to_dict(),'perClass':{c:float(np.mean(p[y==c]==c)) for c in sorted(set(y))}}
    if len(np.unique(yb))==2:
        out['stateShapingAuc']=float(roc_auc_score(yb,prob));out['stateShapingAp']=float(average_precision_score(yb,prob))
    return out

def main():
    d=pd.read_csv(SRC).sort_values(['market_id','t','parent_id']).copy()
    famsets=d.groupby(['market_id','t'],sort=False).objective_family.agg(lambda x:tuple(sorted(set(map(str,x)))))
    prevsets=famsets.groupby(level=0).shift(1);pm=prevsets.to_dict()
    d['prev_family_set']=[pm.get((m,t),np.nan) for m,t in zip(d.market_id,d.t)]
    d=d[d.prev_family_set.map(lambda x:isinstance(x,tuple))].copy()
    d['is_open_or_switch']=[str(f) not in set(s) for f,s in zip(d.objective_family,d.prev_family_set)]
    d['prev_has_pair_balance']=[float('PAIR_BALANCE' in set(s)) for s in d.prev_family_set]
    d['prev_has_state_shaping']=[float('STATE_SHAPING' in set(s)) for s in d.prev_family_set]
    d['prev_parallel_family_set']=[float(len(set(s))>1) for s in d.prev_family_set]
    ts=d.groupby(['market_id','t'],sort=False)[DELTA].first();pts=ts.groupby(level=0).shift(1)
    for c in DELTA:
        mp=pts[c].to_dict();d[f'{c}_delta_prev_event']=pd.to_numeric(d[c],errors='coerce')-[mp.get((m,t),np.nan) for m,t in zip(d.market_id,d.t)]
    fs=BASE+['prev_has_pair_balance','prev_has_state_shaping','prev_parallel_family_set']+[f'{c}_delta_prev_event' for c in DELTA]
    o=d[d.is_open_or_switch].copy();o['target']=o.objective_family.astype(str)
    mids=sorted(d.market_id.unique(),key=lambda m:d.loc[d.market_id==m,'t'].min());folds=[]
    for i,cut in enumerate((.60,.70,.80)):
        k=int(len(mids)*cut);v=int(len(mids)*.10);tr=o[o.market_id.isin(mids[:k])];te=o[o.market_id.isin(mids[k:k+v])]
        ytr=tr.target.to_numpy();yte=te.target.to_numpy();vc=pd.Series(ytr).value_counts();w={c:len(ytr)/(len(vc)*n) for c,n in vc.items()};sw=np.array([w[x] for x in ytr])
        m=model(9400+i).fit(tr[fs],ytr,sample_weight=sw);pred=m.predict(te[fs]);idx=list(m.classes_).index('STATE_SHAPING');prob=m.predict_proba(te[fs])[:,idx]
        maj=pd.Series(ytr).value_counts().idxmax();base=np.repeat(maj,len(te));baseprob=np.repeat(float(maj=='STATE_SHAPING'),len(te))
        folds.append({'trainMarkets':int(tr.market_id.nunique()),'testMarkets':int(te.market_id.nunique()),'trainRows':int(len(tr)),'testRows':int(len(te)),'majorityBaseline':score(yte,base,baseprob),'modelBalancedWeight':score(yte,pred,prob)})
    rep={'version':'R4_MANAGEMENT_OBJECTIVE_OPEN_FAMILY_V1_DIAGNOSTIC','researchOnly':True,'promotionEligible':False,'strictPast':True,'target':'Conditional on SWITCH/OPEN under strict event-time family-set semantics: PAIR_BALANCE vs STATE_SHAPING','features':fs,'coverage':{'rows':int(len(o)),'markets':int(o.market_id.nunique()),'classCounts':o.target.value_counts().to_dict()},'folds':folds,'summary':{'meanBA':float(np.mean([x['modelBalancedWeight']['balancedAccuracy'] for x in folds])),'worstBA':float(np.min([x['modelBalancedWeight']['balancedAccuracy'] for x in folds])),'meanAuc':float(np.mean([x['modelBalancedWeight']['stateShapingAuc'] for x in folds]))},'interpretation':'Second stage of Objective-First hierarchy. No HOLD/MAKER/TAKER actor tuning and no future/settlement features.'}
    OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'coverage':rep['coverage'],'summary':rep['summary'],'folds':folds},indent=2))
if __name__=='__main__':main()
