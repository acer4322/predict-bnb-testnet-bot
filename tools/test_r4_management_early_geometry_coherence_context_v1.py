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
PREREG=ROOT/'data/research/r4_v0/hourly/r4_management_early_geometry_coherence_context_v1_preregistered.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_early_geometry_coherence_context_v1.json'
TZ=ZoneInfo('Asia/Taipei')
TEST_ID='R4_MANAGEMENT_EARLY_GEOMETRY_COHERENCE_CONTEXT_V1_20260828_0334'
BASE=['seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross','weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s','current_mode_age_s','events_5s','events_15s','transitions_15s']
PAIR=['abs_gap_delta_prev','coverage_delta_prev','absnet_ratio_delta_prev']
FLOOR=['risk_deficit_delta_prev','floor_per_gross_delta_prev']
RAW=PAIR+FLOOR
COHERENCE=['pair_direction_score','floor_direction_score','cross_component_coherence']
TARGET_LABELS=['floor_improves_5s','absnet_reduces_5s','joint_quality_improves_5s']
HFT_LABELS={'floor_improves_5s':'floorImproved5s','absnet_reduces_5s':'absNetReduced5s','joint_quality_improves_5s':'jointQualityImproves5s'}

def enrich(d, market_col):
    d=d.sort_values([market_col,'t']).copy(); g=d.groupby(market_col,sort=False)
    for c in ['abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross']:
        d[c+'_delta_prev']=g[c].diff().fillna(0.0)
    # Natural zero boundaries only. Positive score means improving economic geometry.
    d['pair_direction_score']=(-np.sign(d['abs_gap_delta_prev']) + np.sign(d['coverage_delta_prev']) - np.sign(d['absnet_ratio_delta_prev']))/3.0
    d['floor_direction_score']=(-np.sign(d['risk_deficit_delta_prev']) + np.sign(d['floor_per_gross_delta_prev']))/2.0
    d['cross_component_coherence']=d['pair_direction_score']*d['floor_direction_score']
    return d

def add_target_labels(d):
    out=[]
    for mid,x in d.groupby('market_id',sort=False):
        x=x.sort_values('t').copy(); ts=x.t.to_numpy(); n=len(x)
        ff=np.full(n,np.nan); aa=np.full(n,np.nan); floor=x.floor_per_gross.to_numpy(float); an=x.absnet_ratio.to_numpy(float)
        for i,t in enumerate(ts):
            j=np.searchsorted(ts,t+4000,side='left')
            if j<n and ts[j]<=t+6500: ff[i]=floor[j]; aa[i]=an[j]
        x['future_floor_5s']=ff; x['future_absnet_5s']=aa
        x['floor_improves_5s']=(x.future_floor_5s>x.floor_per_gross+1e-9).astype(float)
        x['absnet_reduces_5s']=(x.future_absnet_5s<x.absnet_ratio-1e-9).astype(float)
        x['joint_quality_improves_5s']=((x.floor_improves_5s==1)&(x.absnet_reduces_5s==1)).astype(float)
        x.loc[x.future_floor_5s.isna()|x.future_absnet_5s.isna(),TARGET_LABELS]=np.nan; out.append(x)
    return pd.concat(out,ignore_index=True)

def fit(X,y):
    return HistGradientBoostingClassifier(max_iter=140,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=30,l2_regularization=3.0,random_state=20260828).fit(X,y)

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
        models[lab]={
            'RAW':fit(tr[BASE+RAW].fillna(0).to_numpy(float),y),
            'COHERENCE':fit(tr[BASE+RAW+COHERENCE].fillna(0).to_numpy(float),y),
        }
    comps=[]; represented=set(); detail={}
    for cname,path in HFTS:
        d=enrich(pd.read_csv(path),'marketId'); d=d[(d.seconds_left>120)&(d.seconds_left<=180)].copy(); d['jointQualityImproves5s']=((d.floorImproved5s==1)&(d.absNetReduced5s==1)).astype(int)
        cd={'rows':int(len(d)),'markets':int(d.marketId.nunique()),'coherence':{'mean':float(d.cross_component_coherence.mean()) if len(d) else None,'positiveFraction':float((d.cross_component_coherence>0).mean()) if len(d) else None,'negativeFraction':float((d.cross_component_coherence<0).mean()) if len(d) else None},'targets':{}}
        for lab in TARGET_LABELS:
            y=d[HFT_LABELS[lab]].astype(int).to_numpy() if len(d) else np.asarray([],int); pos=int(y.sum()) if len(y) else 0; neg=int(len(y)-pos)
            if pos<8 or neg<8 or len(np.unique(y))<2:
                cd['targets'][lab]={'eligible':False,'n':int(len(y)),'positives':pos,'negatives':neg}; continue
            pr=models[lab]['RAW'].predict_proba(d[BASE+RAW].fillna(0).to_numpy(float))[:,1]
            pc=models[lab]['COHERENCE'].predict_proba(d[BASE+RAW+COHERENCE].fillna(0).to_numpy(float))[:,1]
            mr,mc=met(y,pr),met(y,pc); delta={'auc':mc['auc']-mr['auc'],'ap':mc['ap']-mr['ap'],'logLossImprovement':mr['logLoss']-mc['logLoss']}
            cd['targets'][lab]={'eligible':True,'RAW_COMPONENTS':mr,'PLUS_COHERENCE':mc,'delta':delta}; comps.append(delta); represented.add(cname)
        detail[cname]=cd
    support=(len(represented)>=2 and len(comps)>=4)
    if support:
        s={'eligibleComparisons':len(comps),'representedCohorts':sorted(represented),'meanDeltaAuc':float(np.mean([x['auc'] for x in comps])),'meanDeltaAp':float(np.mean([x['ap'] for x in comps])),'meanLogLossImprovement':float(np.mean([x['logLossImprovement'] for x in comps])),'worstDeltaAuc':float(np.min([x['auc'] for x in comps])),'nonNegativeAucFraction':float(np.mean([x['auc']>=0 for x in comps]))}
        keep=s['meanDeltaAuc']>=.01 and s['meanDeltaAp']>0 and s['meanLogLossImprovement']>0 and s['worstDeltaAuc']>=-.02 and s['nonNegativeAucFraction']>=.75
        s['keep']=bool(keep)
    else:
        s={'eligibleComparisons':len(comps),'representedCohorts':sorted(represented),'keep':False}
    if not support: decision='TESTED_INCONCLUSIVE'; reason='preregistered support gate failed'
    elif s['keep']: decision='TESTED_KEEP_SIGNAL'; reason='cross-component coherence passed all fixed incremental stability/calibration gates'
    else: decision='TESTED_REJECTED'; reason='support passed but cross-component coherence failed one or more fixed incremental gates'
    result={'version':'R4_MANAGEMENT_EARLY_GEOMETRY_COHERENCE_CONTEXT_V1','testId':TEST_ID,'createdAt':datetime.now(TZ).isoformat(),'preRegistration':str(PREREG.relative_to(ROOT)).replace('\\','/'),'researchOnly':True,'actionAuthority':False,'layers':pre['layerAssignment'],'features':{'rawTrajectory':RAW,'coherence':COHERENCE},'summary':s,'cohorts':detail,'decision':decision,'reason':reason,'authority':'EARLY_MANAGEMENT_LIFECYCLE_QUALITY_CONSISTENCY_CONTEXT_ONLY_NOT_ACTION_AUTHORITY','guards':pre['guards']}
    OUT.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'decision':decision,'summary':s},ensure_ascii=False))
if __name__=='__main__': main()
