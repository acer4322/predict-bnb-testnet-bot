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
TARGET=ROOT/'data/research/r4_v0/hourly/r4_management_responsibility_progress_full300_v1_rows.csv'
HFTS=[
 ('REPLICATION5',ROOT/'data/research/r4_v0/hourly/r4_management_progress_hft_replication5_v1_rows.csv'),
 ('REPLICATION6',ROOT/'data/research/r4_v0/hourly/r4_management_progress_hft_replication6_v1_rows.csv'),
 ('REPLICATION7_96',ROOT/'data/research/r4_v0/hourly/r4_management_progress_hft_replication7_96_v1_rows.csv'),
]
PREREG=ROOT/'data/research/r4_v0/hourly/r4_management_realized_progress_phase_routing_v1_preregistered.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_realized_progress_phase_routing_v1.json'
TZ=ZoneInfo('Asia/Taipei')
TEST_ID='R4_MANAGEMENT_REALIZED_PROGRESS_PHASE_ROUTING_V1_20260828_0134'
BASE=['seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross','weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s','current_mode_age_s','events_5s','events_15s','transitions_15s']
PROG=['weak_unresolved_shares','dominant_unresolved_shares','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s']
STALL=['weak_stalled_5s','dominant_stalled_5s','unresolved_side_share','progress_ratio_imbalance']
REALIZED=PROG+STALL
TARGET_LABELS=['floor_improves_5s','absnet_reduces_5s','joint_quality_improves_5s']
HFT_LABELS={'floor_improves_5s':'floorImproved5s','absnet_reduces_5s':'absNetReduced5s','joint_quality_improves_5s':'jointQualityImproves5s'}
PHASES={
 'EARLY_MANAGEMENT':lambda d:(d.seconds_left>120)&(d.seconds_left<=180),
 'LATE_MANAGEMENT':lambda d:(d.seconds_left>=60)&(d.seconds_left<=120),
}

def enrich(d, market_col):
    d=d.sort_values([market_col,'t']).copy()
    d['weak_stalled_5s']=((d['weak_unresolved_shares']>1e-9)&(d['weak_fill_shares_5s']<=1e-9)).astype(float)
    d['dominant_stalled_5s']=((d['dominant_unresolved_shares']>1e-9)&(d['dominant_fill_shares_5s']<=1e-9)).astype(float)
    tot=(d['weak_unresolved_shares']+d['dominant_unresolved_shares']).clip(lower=1e-9)
    d['unresolved_side_share']=d['weak_unresolved_shares']/tot
    d['progress_ratio_imbalance']=d['weak_progress_ratio']-d['dominant_progress_ratio']
    return d

def add_target_labels(d):
    out=[]
    for mid,x in d.groupby('market_id',sort=False):
        x=x.sort_values('t').copy(); ts=x.t.to_numpy(); n=len(x)
        ff=np.full(n,np.nan); aa=np.full(n,np.nan)
        floor=x.floor_per_gross.to_numpy(float); an=x.absnet_ratio.to_numpy(float)
        for i,t in enumerate(ts):
            j=np.searchsorted(ts,t+4000,side='left')
            if j<n and ts[j]<=t+6500:
                ff[i]=floor[j]; aa[i]=an[j]
        x['future_floor_5s']=ff; x['future_absnet_5s']=aa
        x['floor_improves_5s']=(x.future_floor_5s>x.floor_per_gross+1e-9).astype(float)
        x['absnet_reduces_5s']=(x.future_absnet_5s<x.absnet_ratio-1e-9).astype(float)
        x['joint_quality_improves_5s']=((x.floor_improves_5s==1)&(x.absnet_reduces_5s==1)).astype(float)
        x.loc[x.future_floor_5s.isna()|x.future_absnet_5s.isna(),TARGET_LABELS]=np.nan
        out.append(x)
    return pd.concat(out,ignore_index=True)

def fit(X,y):
    return HistGradientBoostingClassifier(max_iter=140,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=30,l2_regularization=3.0,random_state=20260827).fit(X,y)

def met(y,p):
    y=np.asarray(y,int); p=np.clip(np.asarray(p,float),1e-6,1-1e-6)
    return {'n':int(len(y)),'positives':int(y.sum()),'negatives':int(len(y)-y.sum()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p))}

def main():
    pre=json.loads(PREREG.read_text(encoding='utf-8'))
    td=add_target_labels(enrich(pd.read_csv(TARGET),'market_id'))
    order=td.groupby('market_id').t.min().sort_values().index.tolist(); train_ids=set(order[:int(.60*len(order))])
    models={}
    for lab in TARGET_LABELS:
        tr=td[td.market_id.isin(train_ids)&td[lab].notna()].copy(); y=tr[lab].astype(int).to_numpy()
        models[lab]={'BASE':fit(tr[BASE].fillna(0).to_numpy(float),y),'REALIZED':fit(tr[BASE+REALIZED].fillna(0).to_numpy(float),y)}
    result={'version':'R4_MANAGEMENT_REALIZED_PROGRESS_PHASE_ROUTING_V1','testId':TEST_ID,'createdAt':datetime.now(TZ).isoformat(),'preRegistration':str(PREREG.relative_to(ROOT)).replace('\\','/'),'researchOnly':True,'actionAuthority':False,'phases':{},'guards':pre['guards']}
    hfts=[]
    for cname,path in HFTS:
        d=enrich(pd.read_csv(path),'marketId'); d['jointQualityImproves5s']=((d.floorImproved5s==1)&(d.absNetReduced5s==1)).astype(int); hfts.append((cname,d))
    passed=[]; unsupported=[]
    for pname,maskfn in PHASES.items():
        comps=[]; represented=set(); detail={}
        for cname,d0 in hfts:
            d=d0[maskfn(d0)].copy(); cd={'rows':int(len(d)),'markets':int(d.marketId.nunique()),'targets':{}}
            for lab in TARGET_LABELS:
                y=d[HFT_LABELS[lab]].astype(int).to_numpy() if len(d) else np.asarray([],int); pos=int(y.sum()) if len(y) else 0; neg=int(len(y)-pos)
                if pos<8 or neg<8 or len(np.unique(y))<2:
                    cd['targets'][lab]={'eligible':False,'n':int(len(y)),'positives':pos,'negatives':neg}; continue
                pb=models[lab]['BASE'].predict_proba(d[BASE].fillna(0).to_numpy(float))[:,1]
                pr=models[lab]['REALIZED'].predict_proba(d[BASE+REALIZED].fillna(0).to_numpy(float))[:,1]
                mb,mr=met(y,pb),met(y,pr); delta={'auc':mr['auc']-mb['auc'],'ap':mr['ap']-mb['ap'],'logLossImprovement':mb['logLoss']-mr['logLoss']}
                cd['targets'][lab]={'eligible':True,'BASE':mb,'REALIZED':mr,'delta':delta}; comps.append(delta); represented.add(cname)
            detail[cname]=cd
        support=(len(represented)>=2 and len(comps)>=4)
        if support:
            s={'eligibleComparisons':len(comps),'representedCohorts':sorted(represented),'meanDeltaAuc':float(np.mean([x['auc'] for x in comps])),'meanDeltaAp':float(np.mean([x['ap'] for x in comps])),'meanLogLossImprovement':float(np.mean([x['logLossImprovement'] for x in comps])),'worstDeltaAuc':float(np.min([x['auc'] for x in comps])),'nonNegativeAucFraction':float(np.mean([x['auc']>=0 for x in comps]))}
            keep=s['meanDeltaAuc']>=.02 and s['meanDeltaAp']>0 and s['meanLogLossImprovement']>0 and s['worstDeltaAuc']>=-.02 and s['nonNegativeAucFraction']>=.75
            s['keep']=bool(keep)
            if keep: passed.append(pname)
        else:
            s={'eligibleComparisons':len(comps),'representedCohorts':sorted(represented),'keep':False}; unsupported.append(pname)
        result['phases'][pname]={'supportPass':support,'summary':s,'cohorts':detail}
    if passed:
        decision='TESTED_KEEP_SIGNAL'; reason='phase-specific fixed routing gate passed for '+','.join(passed)
    elif unsupported:
        decision='TESTED_INCONCLUSIVE'; reason='no phase passed and at least one phase lacked fixed support'
    else:
        decision='TESTED_REJECTED'; reason='both predeclared phases had sufficient support and failed fixed routing gate'
    result['decision']=decision; result['reason']=reason; result['keptPhases']=passed; result['authority']='MANAGEMENT_LIFECYCLE_QUALITY_PHASE_ROUTING_CONTEXT_ONLY_NOT_ACTION_AUTHORITY'
    OUT.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'decision':decision,'reason':reason,'keptPhases':passed,'summaries':{k:v['summary'] for k,v in result['phases'].items()}},ensure_ascii=False))
if __name__=='__main__': main()
