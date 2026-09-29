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
PREREG=ROOT/'data/research/r4_v0/hourly/r4_management_lifecycle_quality_hft_attribution_v1_preregistered.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_lifecycle_quality_hft_attribution_v1.json'
TZ=ZoneInfo('Asia/Taipei')
TEST_ID='R4_MANAGEMENT_LIFECYCLE_QUALITY_HFT_ATTRIBUTION_V1_20260827_2335'
BASE=['seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross','weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s','current_mode_age_s','events_5s','events_15s','transitions_15s']
PROG=['weak_unresolved_shares','dominant_unresolved_shares','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s']
STALL=['weak_stalled_5s','dominant_stalled_5s','unresolved_side_share','progress_ratio_imbalance']
TRAJ=['abs_gap_delta_prev','risk_deficit_delta_prev','coverage_delta_prev','absnet_ratio_delta_prev','floor_per_gross_delta_prev','event_interval_s']
TARGET_LABELS=['floor_improves_5s','absnet_reduces_5s','joint_quality_improves_5s']
HFT_LABELS={'floor_improves_5s':'floorImproved5s','absnet_reduces_5s':'absNetReduced5s','joint_quality_improves_5s':'jointQualityImproves5s'}
FEATURE_SETS={
 'BASE':BASE,
 'REALIZED':BASE+PROG+STALL,
 'TRAJECTORY':BASE+TRAJ,
 'ALL':BASE+PROG+STALL+TRAJ,
}

def enrich(d, market_col):
    d=d.sort_values([market_col,'t']).copy()
    g=d.groupby(market_col,sort=False)
    for c in ['abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross']:
        d[c+'_delta_prev']=g[c].diff().fillna(0.0)
    d['event_interval_s']=(g['t'].diff().fillna(0.0)/1000.0).clip(0,60)
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
    train_support={}
    for lab in TARGET_LABELS:
        tr=td[td.market_id.isin(train_ids)&td[lab].notna()].copy(); y=tr[lab].astype(int).to_numpy()
        train_support[lab]={'rows':int(len(tr)),'positives':int(y.sum()),'negatives':int(len(y)-y.sum())}
        models[lab]={name:fit(tr[fs].fillna(0).to_numpy(float),y) for name,fs in FEATURE_SETS.items()}
    result={'version':'R4_MANAGEMENT_LIFECYCLE_QUALITY_HFT_ATTRIBUTION_V1','testId':TEST_ID,'createdAt':datetime.now(TZ).isoformat(),'preRegistration':str(PREREG.relative_to(ROOT)).replace('\\','/'),'researchOnly':True,'actionAuthority':False,'targetTrainSupport':train_support,'featureSets':FEATURE_SETS,'cohorts':{},'guards':pre['guards']}
    all_d=[]; comp={'REALIZED':[],'TRAJECTORY':[],'ALL':[]}; support_ok=True
    for cname,path in HFTS:
        d=enrich(pd.read_csv(path),'marketId')
        d['jointQualityImproves5s']=((d.floorImproved5s==1)&(d.absNetReduced5s==1)).astype(int)
        cres={'rows':int(len(d)),'markets':int(d.marketId.nunique()),'targets':{}}
        for lab in TARGET_LABELS:
            hlabel=HFT_LABELS[lab]; y=d[hlabel].astype(int).to_numpy(); pos=int(y.sum()); neg=int(len(y)-pos)
            if pos<10 or neg<10 or len(np.unique(y))<2:
                cres['targets'][lab]={'eligible':False,'n':int(len(y)),'positives':pos,'negatives':neg}; support_ok=False; continue
            ms={}
            for name,fs in FEATURE_SETS.items():
                p=models[lab][name].predict_proba(d[fs].fillna(0).to_numpy(float))[:,1]; ms[name]=met(y,p)
            deltas={}
            for name in ['REALIZED','TRAJECTORY','ALL']:
                deltas[name]={'auc':ms[name]['auc']-ms['BASE']['auc'],'ap':ms[name]['ap']-ms['BASE']['ap'],'logLossImprovement':ms['BASE']['logLoss']-ms[name]['logLoss']}
                comp[name].append(deltas[name]);
            cres['targets'][lab]={'eligible':True,'metrics':ms,'deltaVsBase':deltas}; all_d.append(deltas['ALL'])
        result['cohorts'][cname]=cres
    if not support_ok or len(all_d)!=9:
        decision='TESTED_INCONCLUSIVE'; reason='fixed support rule failed'
    else:
        summary={}
        for name in ['REALIZED','TRAJECTORY','ALL']:
            xs=comp[name]; summary[name]={'meanDeltaAuc':float(np.mean([x['auc'] for x in xs])),'meanDeltaAp':float(np.mean([x['ap'] for x in xs])),'meanLogLossImprovement':float(np.mean([x['logLossImprovement'] for x in xs])),'worstDeltaAuc':float(np.min([x['auc'] for x in xs])),'nonNegativeAucComparisons':int(sum(x['auc']>=0 for x in xs))}
        all_s=summary['ALL']; keep=all_s['meanDeltaAuc']>=.01 and all_s['meanDeltaAp']>0 and all_s['meanLogLossImprovement']>0 and all_s['worstDeltaAuc']>=-.03 and all_s['nonNegativeAucComparisons']>=7
        rd=summary['REALIZED']; tr=summary['TRAJECTORY']; attr='MIXED_NO_DOMINANT_COMPONENT'
        if rd['meanDeltaAuc']-tr['meanDeltaAuc']>=.01 and rd['nonNegativeAucComparisons']>=6: attr='REALIZED_PROGRESS_STALL_DOMINANT'
        elif tr['meanDeltaAuc']-rd['meanDeltaAuc']>=.01 and tr['nonNegativeAucComparisons']>=6: attr='GEOMETRY_TRAJECTORY_DOMINANT'
        result['summary']=summary; result['attribution']=attr
        decision='TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED'; reason='fixed preregistered HFT transfer gate '+('passed' if keep else 'failed')
    result['decision']=decision; result['reason']=reason; result['authority']='MANAGEMENT_LIFECYCLE_QUALITY_CONTEXT_ONLY_NOT_ACTION_AUTHORITY'
    OUT.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'decision':decision,'reason':reason,'summary':result.get('summary'),'attribution':result.get('attribution'),'coverage':{k:{'rows':v['rows'],'markets':v['markets']} for k,v in result['cohorts'].items()}},ensure_ascii=False))
if __name__=='__main__':main()
