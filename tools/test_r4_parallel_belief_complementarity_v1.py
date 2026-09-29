from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'data/research/r4_v0/hourly'
FILES={
 'TRAIN_FRESH24':BASE/'r4_queue_opportunity_hft_shadow_v1_fresh24_rows.csv',
 'TEST_UNSEEN24':BASE/'r4_queue_opportunity_hft_shadow_v1_unseen24_rows.csv',
 'TEST_REPLICATION3':BASE/'r4_queue_opportunity_hft_shadow_v1_replication3_rows.csv',
}
OUT=BASE/'r4_parallel_belief_complementarity_v1.json'
TARGETS=['futureFrozenWeakNeed5s','futureWeakMakerFill5s','floorImproved5s','absNetReduced5s']
SETS={
 'PREP_ONLY':['p_build','p_prepare_phase_routed'],
 'QUEUE_ONLY':['queue_opportunity_score'],
 'PREP_PLUS_QUEUE':['p_build','p_prepare_phase_routed','queue_opportunity_score'],
}

def metric(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float)
 return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def main():
 ds={k:pd.read_csv(p) for k,p in FILES.items()}
 for d in ds.values():d['p_prepare_phase_routed']=np.where(d.seconds_left<60,d.p_prepare_ctx,d.p_prepare_role_routed)
 train=ds['TRAIN_FRESH24'];rep={'version':'R4_PARALLEL_BELIEF_COMPLEMENTARITY_V1','researchOnly':True,'runtimePromotionAllowed':False,'actionChanges':False,'training':'Fixed logistic diagnostic trained only on FRESH24; untouched evaluation on UNSEEN24 and REPLICATION3. No hyperparameter/threshold sweep.','featureSets':SETS,'targets':{}}
 for t in TARGETS:
  tr={}
  for sname,fs in SETS.items():
   m=make_pipeline(StandardScaler(),LogisticRegression(C=1.0,max_iter=1000,random_state=27));m.fit(train[fs],train[t].astype(int));rr={}
   for dname,d in ds.items():rr[dname]=metric(d[t],m.predict_proba(d[fs])[:,1])
   tr[sname]=rr
  tr['incrementQueueOverPrep']={}
  for dname in ['TEST_UNSEEN24','TEST_REPLICATION3']:
   a=tr['PREP_ONLY'][dname];b=tr['PREP_PLUS_QUEUE'][dname];tr['incrementQueueOverPrep'][dname]={'deltaAuc':b['auc']-a['auc'],'deltaAp':b['ap']-a['ap'],'logLossImprovement':a['logLoss']-b['logLoss']}
  tr['incrementQueueOverPrep']['positiveBoth']={k:all(tr['incrementQueueOverPrep'][d][k]>0 for d in ['TEST_UNSEEN24','TEST_REPLICATION3']) for k in ['deltaAuc','deltaAp','logLossImprovement']}
  rep['targets'][t]=tr
 OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
 brief={t:{'increment':rep['targets'][t]['incrementQueueOverPrep'],'testAuc':{s:{d:rep['targets'][t][s][d]['auc'] for d in ['TEST_UNSEEN24','TEST_REPLICATION3']} for s in SETS}} for t in TARGETS}
 print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'brief':brief},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
