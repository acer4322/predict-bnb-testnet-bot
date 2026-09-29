from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import numpy as np
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss

ROOT=Path(__file__).resolve().parents[1]
FILES={
 'FRESH24':ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_hft_shadow_fresh24_v1_rows.csv',
 'UNSEEN24':ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_hft_shadow_unseen24_v1_rows.csv',
 'REPLICATION3':ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_hft_shadow_replication3_v1_rows.csv',
}
OUT=ROOT/'data/research/r4_v0/hourly/r4_prepare_readiness_phase_routing_v1.json'
PHASES=[('LATE_0_60',0,60),('MID_60_180',60,180),('EARLY_180_300',180,301)]
TARGETS=['futureFrozenWeakNeed5s','futureWeakMakerFill5s','floorImproved5s','absNetReduced5s']

def metric(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float)
    if len(y)==0 or len(set(y))<2:return {'n':int(len(y)),'rate':float(y.mean()) if len(y) else None}
    return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}

def main():
    rep={'version':'R4_PREPARE_READINESS_PHASE_ROUTING_V1','researchOnly':True,'runtimePromotionAllowed':False,'actionChanges':False,
         'question':'Does the role-routed placement-readiness contribution to PREPARE belief replicate mainly in a particular pre-existing lifecycle phase?','phases':[x[0] for x in PHASES],'cohorts':{},'cross':{}}
    for name,p in FILES.items():
        d=pd.read_csv(p);co={}
        for ph,lo,hi in PHASES:
            z=d[(d.seconds_left>=lo)&(d.seconds_left<hi)].copy();tr={}
            for t in TARGETS:
                a=metric(z[t],z.p_prepare_ctx);b=metric(z[t],z.p_prepare_role_routed)
                dd={}
                if 'auc' in a and 'auc' in b:
                    dd={'deltaAuc':b['auc']-a['auc'],'deltaAp':b['ap']-a['ap'],'logLossImprovement':a['logLoss']-b['logLoss']}
                tr[t]={'ctx':a,'roleRouted':b,'delta':dd}
            co[ph]={'ticks':int(len(z)),'markets':int(z.marketId.nunique()),'targets':tr}
        rep['cohorts'][name]=co
    for ph,_,_ in PHASES:
        x={}
        for t in TARGETS:
            vals={n:rep['cohorts'][n][ph]['targets'][t]['delta'] for n in FILES}
            keys=['deltaAuc','deltaAp','logLossImprovement']
            x[t]={'perCohort':vals,'allThreePositive':{k:all(vals[n].get(k,0)>0 for n in FILES) for k in keys}}
        rep['cross'][ph]=x
    OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    brief={}
    for ph in rep['cross']:
        brief[ph]={t:{'allThreePositive':rep['cross'][ph][t]['allThreePositive'],'dAuc':{n:rep['cross'][ph][t]['perCohort'][n].get('deltaAuc') for n in FILES}} for t in TARGETS}
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'brief':brief},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
