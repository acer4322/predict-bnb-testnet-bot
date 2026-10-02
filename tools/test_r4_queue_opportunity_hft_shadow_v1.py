from __future__ import annotations
import bisect,json,sys
from pathlib import Path
import numpy as np,pandas as pd,joblib
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as sh
BASE=ROOT/'data/research/r4_v0/hourly'
FILES={
 'FRESH24':BASE/'r4_parallel_belief_hft_shadow_fresh24_v1_rows.csv',
 'UNSEEN24':BASE/'r4_parallel_belief_hft_shadow_unseen24_v1_rows.csv',
 'REPLICATION3':BASE/'r4_parallel_belief_hft_shadow_replication3_v1_rows.csv',
}
MODEL=BASE/'r4_queue_opportunity_belief_v0.joblib'
OUT=BASE/'r4_queue_opportunity_hft_shadow_v1.json'
TARGETS=['futureFrozenWeakNeed5s','futureWeakMakerFill5s','floorImproved5s','absNetReduced5s']
PHASES=[('LATE_0_60',0,60),('MID_60_180',60,180),('EARLY_180_300',180,301)]

def metric(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float)
 if len(y)==0 or len(set(y))<2:return {'n':int(len(y)),'rate':float(y.mean()) if len(y) else None}
 return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}

def enrich(df,mdl,feats):
 out=[]
 for mid,z in df.groupby('marketId',sort=False):
  br=sh.book_rows(int(mid));ts=[int(r[0]) for r in br]
  for r in z.itertuples(index=False):
   t=int(r.t);j=bisect.bisect_right(ts,t)-1
   if j<0:continue
   bt,bb,ba,top5,churn=br[j];age=t-int(bt)
   if age>1500:continue
   row=r._asdict();row.update({'queue_book_age_ms':age,'seconds_left':float(r.seconds_left),'spread_ticks':float((ba-bb)/.01),'top5_total':float(top5),'churn1_total':float(churn)});out.append(row)
 e=pd.DataFrame(out)
 if not e.empty:e['queue_opportunity_score']=mdl.predict_proba(e[feats])[:,1]
 return e

def qstats(z):
 if z.empty:return []
 q=pd.qcut(z.queue_opportunity_score.rank(method='first'),4,labels=['Q1','Q2','Q3','Q4']);out=[]
 for lab in ['Q1','Q2','Q3','Q4']:
  x=z[q==lab];out.append({'q':lab,'n':int(len(x)),'meanScore':float(x.queue_opportunity_score.mean()),**{t:float(x[t].mean()) for t in TARGETS},'meanFloorDelta5s':float(x.floorDelta5s.mean()),'meanAbsNetDelta5s':float(x.absNetDelta5s.mean())})
 return out

def main():
 pack=joblib.load(MODEL);mdl=pack['model'];feats=pack['features']
 rep={'version':'R4_QUEUE_OPPORTUNITY_HFT_SHADOW_V1','researchOnly':True,'runtimePromotionAllowed':False,'actionChanges':False,'clock':'HFT features recomputed strictly from receipt-clock Execution Tape V1. Target expert was source-clock trained; this is explicit domain-shift shadow evidence only.','cohorts':{},'crossPhase':{}}
 for name,p in FILES.items():
  d=pd.read_csv(p);e=enrich(d,mdl,feats);csvp=BASE/f'r4_queue_opportunity_hft_shadow_v1_{name.lower()}_rows.csv';e.to_csv(csvp,index=False)
  co={'rows':int(len(e)),'markets':int(e.marketId.nunique()),'medianBookAgeMs':float(e.queue_book_age_ms.median()),'overall':{},'phases':{},'quartiles':qstats(e)}
  for t in TARGETS:co['overall'][t]=metric(e[t],e.queue_opportunity_score)
  for ph,lo,hi in PHASES:
   z=e[(e.seconds_left>=lo)&(e.seconds_left<hi)];co['phases'][ph]={t:metric(z[t],z.queue_opportunity_score) for t in TARGETS};co['phases'][ph]['rows']=int(len(z));co['phases'][ph]['markets']=int(z.marketId.nunique())
  rep['cohorts'][name]=co
 for ph,_,_ in PHASES:
  rep['crossPhase'][ph]={}
  for t in TARGETS:
   vals={n:rep['cohorts'][n]['phases'][ph][t].get('auc') for n in FILES};rep['crossPhase'][ph][t]={'auc':vals,'allAboveHalf':all(v is not None and v>.5 for v in vals.values())}
 OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
 brief={n:{'rows':rep['cohorts'][n]['rows'],'overallAuc':{t:rep['cohorts'][n]['overall'][t].get('auc') for t in TARGETS}} for n in FILES}
 print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'brief':brief,'crossPhase':rep['crossPhase']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
