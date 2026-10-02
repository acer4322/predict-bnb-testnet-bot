from __future__ import annotations
import json
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss

ROOT=Path(__file__).resolve().parents[1]
H=ROOT/'data/research/r4_v0/hourly'
OUT=H/'r4_management_queue_opportunity_incremental_v1.json'
PREREG=H/'r4_management_queue_opportunity_incremental_v1_preregistered.json'
COHORTS=['fresh24','unseen24','replication3']
TARGETS=['futureWeakMakerFill5s','floorImproved5s','absNetReduced5s']
BASE=['p_m0_full','floor','absNet','abs_gap','risk_deficit','coverage','absnet_ratio','weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s','events_5s','events_15s','seconds_left']
FULL=BASE+['queue_opportunity_score']
KEYS=['marketId','t','weakSide','seconds_left']

def load(name):
    q=pd.read_csv(H/f'r4_queue_opportunity_hft_shadow_v1_{name}_rows.csv')
    m=pd.read_csv(H/f'r4_management_hft_shadow_{name}_v1_rows.csv')
    keep=KEYS+['queue_opportunity_score','queue_book_age_ms','spread_ticks']
    z=m.merge(q[keep].drop_duplicates(KEYS),on=KEYS,how='inner')
    z=z[(z.seconds_left>=60)&(z.seconds_left<180)].copy()
    return z

def metric(y,p):
    y=np.asarray(y,int); p=np.asarray(p,float)
    return {'n':int(len(y)),'positives':int(y.sum()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}

def main():
    pre=json.loads(PREREG.read_text(encoding='utf-8'))
    d={n:load(n) for n in COHORTS}
    tr=d['fresh24']
    report={'version':'R4_MANAGEMENT_QUEUE_OPPORTUNITY_INCREMENTAL_V1','testId':pre['testId'],'researchOnly':True,'actionAuthority':False,'preRegistration':str(PREREG.relative_to(ROOT)).replace('\\','/'),'semanticNovelty':pre['semanticDifference'],'layerAssignment':pre['layerAssignment'],'coverage':{},'targets':{},'guards':pre['guards']}
    for n,z in d.items():
        report['coverage'][n.upper()]={'rows':int(len(z)),'markets':int(z.marketId.nunique()),'medianQueueBookAgeMs':float(z.queue_book_age_ms.median()) if len(z) else None}
    eligible=True
    comps=[]
    target_pass={}
    for target in TARGETS:
        ytr=tr[target].astype(int).values
        if len(set(ytr))<2: eligible=False; continue
        mb=make_pipeline(StandardScaler(),LogisticRegression(C=1.0,max_iter=1000,random_state=20260827)).fit(tr[BASE].fillna(0),ytr)
        mf=make_pipeline(StandardScaler(),LogisticRegression(C=1.0,max_iter=1000,random_state=20260827)).fit(tr[FULL].fillna(0),ytr)
        report['targets'][target]={}
        passes=[]
        for n in ['unseen24','replication3']:
            z=d[n]; y=z[target].astype(int).values
            ok=len(z)>=20 and len(set(y))==2
            if not ok:
                eligible=False; report['targets'][target][n.upper()]={'eligible':False,'n':int(len(z)),'positives':int(y.sum())}; continue
            pb=mb.predict_proba(z[BASE].fillna(0))[:,1]; pf=mf.predict_proba(z[FULL].fillna(0))[:,1]
            a=metric(y,pb); b=metric(y,pf)
            delta={'auc':b['auc']-a['auc'],'ap':b['ap']-a['ap'],'logLossImprovement':a['logLoss']-b['logLoss']}
            report['targets'][target][n.upper()]={'eligible':True,'baseline':a,'withQueue':b,'delta':delta}
            comps.append(delta); passes.append(delta['auc']>=0 and delta['ap']>=0 and delta['logLossImprovement']>=0)
        target_pass[target]=len(passes)==2 and all(passes)
    if not eligible or len(comps)<6:
        status='TESTED_INCONCLUSIVE'; decision={'eligible':False,'reason':'support/classes gate failed'}
    else:
        means={k:float(np.mean([x[k] for x in comps])) for k in ['auc','ap','logLossImprovement']}
        pass_count=sum(target_pass.values()); worst_auc=min(x['auc'] for x in comps)
        keep=pass_count>=2 and all(v>0 for v in means.values()) and worst_auc>=-0.02
        status='TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED'
        decision={'eligible':True,'targetPass':target_pass,'targetPassCount':pass_count,'meanDelta':means,'worstAucDelta':float(worst_auc),'keepRulePass':bool(keep)}
    report['decision']=decision; report['status']=status
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':status,'coverage':report['coverage'],'decision':decision,'targets':report['targets']},ensure_ascii=False))
if __name__=='__main__':main()
