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
TRAIN=ROOT/'data/research/r4_v0/hourly/r4_management_m0_phase_routing_replication4_v1_rows.csv'
HOLDOUTS=[
 ('REPLICATION6',ROOT/'data/research/r4_v0/hourly/r4_management_progress_hft_replication6_v1_rows.csv'),
 ('REPLICATION7_96',ROOT/'data/research/r4_v0/hourly/r4_management_progress_hft_replication7_96_v1_rows.csv'),
]
PREREG=ROOT/'data/research/r4_v0/hourly/r4_management_early_execution_kind_context_v1_preregistered.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_early_execution_kind_context_v1.json'
TZ=ZoneInfo('Asia/Taipei')
TEST_ID='R4_MANAGEMENT_EARLY_EXECUTION_KIND_CONTEXT_V1_20260828_0438'
BASE=['seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross','weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s','current_mode_age_s','events_5s','events_15s','transitions_15s','abs_gap_delta_prev','coverage_delta_prev','absnet_ratio_delta_prev','risk_deficit_delta_prev','floor_per_gross_delta_prev']
TARGETS=['floorImproved5s','absNetReduced5s','jointQualityImproves5s']

def enrich(d):
    d=d.sort_values(['marketId','t']).copy(); g=d.groupby('marketId',sort=False)
    for c in ['abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross']:
        d[c+'_delta_prev']=g[c].diff().fillna(0.0)
    d['kind_is_option']=(d['kind'].astype(str).str.upper()=='OPTION').astype(float)
    d['jointQualityImproves5s']=((d.floorImproved5s==1)&(d.absNetReduced5s==1)).astype(int)
    return d[(d.seconds_left>120)&(d.seconds_left<=180)].copy()

def fit(X,y):
    return HistGradientBoostingClassifier(max_iter=140,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=15,l2_regularization=3.0,random_state=20260828).fit(X,y)

def met(y,p):
    y=np.asarray(y,int); p=np.clip(np.asarray(p,float),1e-6,1-1e-6)
    return {'n':int(len(y)),'positives':int(y.sum()),'negatives':int(len(y)-y.sum()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p))}

def main():
    pre=json.loads(PREREG.read_text(encoding='utf-8'))
    tr=enrich(pd.read_csv(TRAIN))
    train_support={}
    models={}
    for lab in TARGETS:
        y=tr[lab].astype(int).to_numpy(); pos=int(y.sum()); neg=int(len(y)-pos)
        ok=pos>=8 and neg>=8 and len(np.unique(y))==2
        train_support[lab]={'n':int(len(y)),'positives':pos,'negatives':neg,'eligible':ok}
        if ok:
            models[lab]={'BASE':fit(tr[BASE].fillna(0).to_numpy(float),y),'KIND':fit(tr[BASE+['kind_is_option']].fillna(0).to_numpy(float),y)}
    comps=[]; detail={}; represented=set()
    for cname,path in HOLDOUTS:
        d=enrich(pd.read_csv(path)); cd={'rows':int(len(d)),'markets':int(d.marketId.nunique()),'kindCounts':{str(k):int(v) for k,v in d.kind.value_counts().items()},'targets':{}}
        for lab in TARGETS:
            y=d[lab].astype(int).to_numpy(); pos=int(y.sum()); neg=int(len(y)-pos); ok=lab in models and pos>=8 and neg>=8 and len(np.unique(y))==2
            if not ok:
                cd['targets'][lab]={'eligible':False,'n':int(len(y)),'positives':pos,'negatives':neg}; continue
            pb=models[lab]['BASE'].predict_proba(d[BASE].fillna(0).to_numpy(float))[:,1]
            pk=models[lab]['KIND'].predict_proba(d[BASE+['kind_is_option']].fillna(0).to_numpy(float))[:,1]
            mb,mk=met(y,pb),met(y,pk); delta={'auc':mk['auc']-mb['auc'],'ap':mk['ap']-mb['ap'],'logLossImprovement':mb['logLoss']-mk['logLoss']}
            cd['targets'][lab]={'eligible':True,'BASE':mb,'PLUS_KIND':mk,'delta':delta}; comps.append(delta); represented.add(cname)
        detail[cname]=cd
    support=len(represented)>=2 and len(comps)>=4 and sum(1 for x in train_support.values() if x['eligible'])>=2
    if support:
        summary={'eligibleComparisons':len(comps),'representedHoldouts':sorted(represented),'meanDeltaAuc':float(np.mean([x['auc'] for x in comps])),'meanDeltaAp':float(np.mean([x['ap'] for x in comps])),'meanLogLossImprovement':float(np.mean([x['logLossImprovement'] for x in comps])),'worstDeltaAuc':float(np.min([x['auc'] for x in comps])),'nonNegativeAucFraction':float(np.mean([x['auc']>=0 for x in comps]))}
        keep=summary['meanDeltaAuc']>=.015 and summary['meanDeltaAp']>0 and summary['meanLogLossImprovement']>0 and summary['worstDeltaAuc']>=-.02 and summary['nonNegativeAucFraction']>=.75
        summary['keep']=bool(keep)
        decision='TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED'
    else:
        summary={'eligibleComparisons':len(comps),'representedHoldouts':sorted(represented),'keep':False}; decision='TESTED_INCONCLUSIVE'
    result={'version':'R4_MANAGEMENT_EARLY_EXECUTION_KIND_CONTEXT_V1','testId':TEST_ID,'createdAt':datetime.now(TZ).isoformat(),'preRegistration':str(PREREG.relative_to(ROOT)).replace('\\','/'),'researchOnly':True,'actionAuthority':False,'layerAssignment':pre['layerAssignment'],'train':{'rows':int(len(tr)),'markets':int(tr.marketId.nunique()),'kindCounts':{str(k):int(v) for k,v in tr.kind.value_counts().items()},'support':train_support},'holdouts':detail,'summary':summary,'decision':decision,'authority':'EARLY_MANAGEMENT_LIFECYCLE_QUALITY_CONTEXT_ONLY_NOT_ACTION_AUTHORITY','guards':pre['guards']}
    OUT.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'decision':decision,'trainRows':len(tr),'summary':summary,'holdoutRows':{k:v['rows'] for k,v in detail.items()}},ensure_ascii=False))
if __name__=='__main__': main()
