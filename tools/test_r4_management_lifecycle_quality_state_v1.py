from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_responsibility_progress_full300_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_lifecycle_quality_state_v1.json'
TZ=ZoneInfo('Asia/Taipei')
TEST_ID='R4_MANAGEMENT_LIFECYCLE_QUALITY_STATE_V1_20260827_2233'
BASE=['seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross','weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s','current_mode_age_s','events_5s','events_15s','transitions_15s']
PROG=['weak_unresolved_shares','dominant_unresolved_shares','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s']
STALL=['weak_stalled_5s','dominant_stalled_5s','unresolved_side_share','progress_ratio_imbalance']
TRAJ=['abs_gap_delta_prev','risk_deficit_delta_prev','coverage_delta_prev','absnet_ratio_delta_prev','floor_per_gross_delta_prev','event_interval_s']
TARGETS=['floor_improves_5s','absnet_reduces_5s','joint_quality_improves_5s']

def add_features(d):
    d=d.sort_values(['market_id','t']).copy()
    g=d.groupby('market_id',sort=False)
    for c in ['abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross']:
        d[c+'_delta_prev']=g[c].diff().fillna(0.0)
    d['event_interval_s']=(g['t'].diff().fillna(0.0)/1000.0).clip(0,60)
    d['weak_stalled_5s']=((d['weak_unresolved_shares']>1e-9)&(d['weak_fill_shares_5s']<=1e-9)).astype(float)
    d['dominant_stalled_5s']=((d['dominant_unresolved_shares']>1e-9)&(d['dominant_fill_shares_5s']<=1e-9)).astype(float)
    tot=(d['weak_unresolved_shares']+d['dominant_unresolved_shares']).clip(lower=1e-9)
    d['unresolved_side_share']=d['weak_unresolved_shares']/tot
    d['progress_ratio_imbalance']=d['weak_progress_ratio']-d['dominant_progress_ratio']
    return d

def add_labels(d):
    out=[]
    for mid,x in d.groupby('market_id',sort=False):
        x=x.sort_values('t').copy(); ts=x['t'].to_numpy(); n=len(x)
        ff=np.full(n,np.nan); aa=np.full(n,np.nan)
        floor=x['floor_per_gross'].to_numpy(float); an=x['absnet_ratio'].to_numpy(float)
        for i,t in enumerate(ts):
            j=np.searchsorted(ts,t+4000,side='left')
            if j<n and ts[j]<=t+6500:
                ff[i]=floor[j]; aa[i]=an[j]
        x['future_floor_5s']=ff; x['future_absnet_5s']=aa
        x['floor_improves_5s']=(x['future_floor_5s']>x['floor_per_gross']+1e-9).astype(float)
        x['absnet_reduces_5s']=(x['future_absnet_5s']<x['absnet_ratio']-1e-9).astype(float)
        x['joint_quality_improves_5s']=((x['floor_improves_5s']==1)&(x['absnet_reduces_5s']==1)).astype(float)
        x.loc[x['future_floor_5s'].isna()|x['future_absnet_5s'].isna(),TARGETS]=np.nan
        out.append(x)
    return pd.concat(out,ignore_index=True)

def fit_model(X,y):
    return HistGradientBoostingClassifier(max_iter=140,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=30,l2_regularization=3.0,random_state=20260827).fit(X,y)

def metric(y,p):
    y=np.asarray(y,int); p=np.clip(np.asarray(p,float),1e-6,1-1e-6)
    return {'n':int(len(y)),'positives':int(y.sum()),'negatives':int(len(y)-y.sum()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p))}

def main():
    d=pd.read_csv(SRC)
    d=add_labels(add_features(d))
    market_order=(d.groupby('market_id')['t'].min().sort_values().index.tolist())
    n=len(market_order); ntr=int(n*.60); b=max(1,int(n*.10))
    train=set(market_order[:ntr]); blocks=[]
    for k in range(4): blocks.append(set(market_order[ntr+k*b: ntr+(k+1)*b if k<3 else n]))
    aug=BASE+PROG+STALL+TRAJ
    result={'version':'R4_MANAGEMENT_LIFECYCLE_QUALITY_STATE_V1','testId':TEST_ID,'createdAt':datetime.now(TZ).isoformat(),'researchOnly':True,'actionAuthority':False,'source':str(SRC.relative_to(ROOT)).replace('\\','/'),'coverage':{'markets':n,'rows':int(len(d)),'trainMarkets':len(train),'evalBlockMarkets':[len(x) for x in blocks]},'features':{'baseline':BASE,'progress':PROG,'stall':STALL,'trajectory':TRAJ},'targets':{},'guards':{'strictPastFeatures':True,'futureGeometryLabelOnly':True,'sealed20260816':True,'noEchtgeldFit':True,'noActionAuthority':True,'noSweep':True}}
    eligible=[]; support_fail=[]
    for target in TARGETS:
        tr=d[d.market_id.isin(train)&d[target].notna()].copy(); ytr=tr[target].astype(int).to_numpy()
        if len(np.unique(ytr))<2: support_fail.append(target); continue
        mb=fit_model(tr[BASE].fillna(0).to_numpy(float),ytr); ma=fit_model(tr[aug].fillna(0).to_numpy(float),ytr)
        rows=[]
        for i,ids in enumerate(blocks,1):
            te=d[d.market_id.isin(ids)&d[target].notna()].copy(); y=te[target].astype(int).to_numpy()
            pos=int(y.sum()); neg=int(len(y)-pos)
            if pos<25 or neg<25 or len(np.unique(y))<2:
                rows.append({'block':i,'eligible':False,'n':int(len(y)),'positives':pos,'negatives':neg}); continue
            pb=mb.predict_proba(te[BASE].fillna(0).to_numpy(float))[:,1]; pa=ma.predict_proba(te[aug].fillna(0).to_numpy(float))[:,1]
            xb=metric(y,pb); xa=metric(y,pa); delta={'auc':xa['auc']-xb['auc'],'ap':xa['ap']-xb['ap'],'logLossImprovement':xb['logLoss']-xa['logLoss']}
            rows.append({'block':i,'eligible':True,'baseline':xb,'augmented':xa,'delta':delta}); eligible.append(delta|{'augAuc':xa['auc'],'target':target,'block':i})
        result['targets'][target]=rows
        if sum(r.get('eligible',False) for r in rows)<3: support_fail.append(target)
    if support_fail:
        decision='TESTED_INCONCLUSIVE'; reason='support failure for '+','.join(sorted(set(support_fail)))
    else:
        mean_auc=float(np.mean([x['auc'] for x in eligible])); mean_ap=float(np.mean([x['ap'] for x in eligible])); mean_ll=float(np.mean([x['logLossImprovement'] for x in eligible])); worst=float(np.min([x['auc'] for x in eligible])); min_aug=float(np.min([x['augAuc'] for x in eligible]))
        result['summary']={'eligibleComparisons':len(eligible),'meanDeltaAuc':mean_auc,'meanDeltaAp':mean_ap,'meanLogLossImprovement':mean_ll,'worstDeltaAuc':worst,'minAugmentedAuc':min_aug}
        keep=mean_auc>=.01 and mean_ap>0 and mean_ll>0 and worst>=-.01 and min_aug>.55
        decision='TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED'; reason='fixed preregistered gate '+('passed' if keep else 'failed')
    result['decision']=decision; result['reason']=reason; result['authority']='MANAGEMENT_LIFECYCLE_QUALITY_CONTEXT_ONLY_NOT_ACTION_AUTHORITY'
    OUT.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'decision':decision,'reason':reason,'coverage':result['coverage'],'summary':result.get('summary')},ensure_ascii=False))
if __name__=='__main__': main()
