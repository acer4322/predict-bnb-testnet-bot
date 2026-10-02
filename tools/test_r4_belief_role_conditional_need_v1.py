from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score,average_precision_score
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'data/research/r4_v0/hourly'
FILES={'FRESH24':BASE/'r4_queue_opportunity_hft_shadow_v1_fresh24_rows.csv','UNSEEN24':BASE/'r4_queue_opportunity_hft_shadow_v1_unseen24_rows.csv','REPLICATION3':BASE/'r4_queue_opportunity_hft_shadow_v1_replication3_rows.csv'}
OUT=BASE/'r4_belief_role_conditional_need_v1.json'
TARGETS=['futureWeakMakerFill5s','floorImproved5s','absNetReduced5s']
def m(y,s):
 y=np.asarray(y,int);s=np.asarray(s,float)
 if len(set(y))<2:return {'n':int(len(y)),'rate':float(y.mean())}
 return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,s)),'ap':float(average_precision_score(y,s))}
def main():
 rep={'version':'R4_BELIEF_ROLE_CONDITIONAL_NEED_V1','researchOnly':True,'runtimePromotionAllowed':False,'actionChanges':False,'question':'Conditional on a frozen weak-side need appearing within 5s, is queue-opportunity belief more informative about execution/path realization than PREPARE belief?','cohorts':{},'cross':{}}
 for name,p in FILES.items():
  d=pd.read_csv(p);d['p_prepare_phase_routed']=np.where(d.seconds_left<60,d.p_prepare_ctx,d.p_prepare_role_routed);z=d[d.futureFrozenWeakNeed5s==1].copy();co={'rows':int(len(z)),'markets':int(z.marketId.nunique())}
  for t in TARGETS:
   co[t]={'PREPARE':m(z[t],z.p_prepare_phase_routed),'QUEUE':m(z[t],z.queue_opportunity_score)}
   if 'auc' in co[t]['QUEUE'] and 'auc' in co[t]['PREPARE']:co[t]['queueMinusPrepare']={'auc':co[t]['QUEUE']['auc']-co[t]['PREPARE']['auc'],'ap':co[t]['QUEUE']['ap']-co[t]['PREPARE']['ap']}
  rep['cohorts'][name]=co
 for t in TARGETS:
  vals={n:rep['cohorts'][n][t].get('queueMinusPrepare',{}) for n in FILES};rep['cross'][t]={'perCohort':vals,'queueWinsAucAll':all(vals[n].get('auc',0)>0 for n in FILES),'queueWinsApAll':all(vals[n].get('ap',0)>0 for n in FILES)}
 OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'cross':rep['cross'],'cohorts':rep['cohorts']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
