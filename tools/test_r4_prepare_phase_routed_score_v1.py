from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss

ROOT=Path(__file__).resolve().parents[1]
FILES={
 'FRESH24':ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_hft_shadow_fresh24_v1_rows.csv',
 'UNSEEN24':ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_hft_shadow_unseen24_v1_rows.csv',
 'REPLICATION3':ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_hft_shadow_replication3_v1_rows.csv',
}
OUT=ROOT/'data/research/r4_v0/hourly/r4_prepare_phase_routed_score_v1.json'
TARGETS=['futureFrozenWeakNeed5s','futureWeakMakerFill5s','floorImproved5s','absNetReduced5s']

def metric(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float)
 if len(set(y))<2:return {'n':int(len(y)),'rate':float(y.mean())}
 return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}

def main():
 rep={'version':'R4_PREPARE_PHASE_ROUTED_SCORE_V1','researchOnly':True,'runtimePromotionAllowed':False,'actionChanges':False,
      'routing':'Use role-routed placement-readiness PREPARE belief for 60<=seconds_left<=300; use context-only PREPARE belief for 0<=seconds_left<60. Boundary inherited from prior Target lifecycle phases; no outcome sweep.',
      'cohorts':{},'cross':{}}
 for name,p in FILES.items():
  d=pd.read_csv(p).copy();d['p_prepare_phase_routed']=np.where(d.seconds_left<60,d.p_prepare_ctx,d.p_prepare_role_routed)
  cr={}
  for t in TARGETS:
   base=metric(d[t],d.p_prepare_ctx);always=metric(d[t],d.p_prepare_role_routed);routed=metric(d[t],d.p_prepare_phase_routed)
   cr[t]={'CTX_ONLY':base,'ALWAYS_ROLE_ROUTED':always,'PHASE_ROUTED':routed,
          'phaseVsCtx':{'deltaAuc':routed['auc']-base['auc'],'deltaAp':routed['ap']-base['ap'],'logLossImprovement':base['logLoss']-routed['logLoss']},
          'phaseVsAlways':{'deltaAuc':routed['auc']-always['auc'],'deltaAp':routed['ap']-always['ap'],'logLossImprovement':always['logLoss']-routed['logLoss']}}
  rep['cohorts'][name]=cr
 for t in TARGETS:
  rep['cross'][t]={}
  for cmp in ['phaseVsCtx','phaseVsAlways']:
   vals={n:rep['cohorts'][n][t][cmp] for n in FILES}
   rep['cross'][t][cmp]={'perCohort':vals,'allThreePositive':{k:all(vals[n][k]>0 for n in FILES) for k in ['deltaAuc','deltaAp','logLossImprovement']}}
 OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
 brief={t:{cmp:{'allThreePositive':rep['cross'][t][cmp]['allThreePositive'],'dAuc':{n:rep['cross'][t][cmp]['perCohort'][n]['deltaAuc'] for n in FILES}} for cmp in ['phaseVsCtx','phaseVsAlways']} for t in TARGETS}
 print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'brief':brief},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
