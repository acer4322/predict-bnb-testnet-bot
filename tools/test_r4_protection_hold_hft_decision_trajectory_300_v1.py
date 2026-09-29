from __future__ import annotations
import json,lzma,joblib
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/hft_forward_paper_v1/markets'
PRE=ROOT/'data/research/r4_v0/hourly/r4_protection_hold_hft_decision_trajectory_300_preregistered.json'
MODEL=ROOT/'data/research/r4_v0/hourly/r4_protection_manager_hold_ordinary_v2.joblib'
TRAINROWS=ROOT/'data/research/r4_v0/hourly/r4_protection_manager_hold_ordinary_v2_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_protection_hold_hft_decision_trajectory_300_v1.json'
ROWS=ROOT/'data/research/r4_v0/hourly/r4_protection_hold_hft_decision_trajectory_300_v1_rows.csv'

def train_floor():
 d=pd.read_csv(TRAINROWS).sort_values(['event_ms','market_id']);mids=d.groupby('market_id').event_ms.min().sort_values().index.tolist();c=max(1,int(len(mids)*.8));tr=d[d.market_id.isin(set(mids[:c]))]
 return HistGradientBoostingClassifier(max_depth=3,max_iter=180,learning_rate=.05,min_samples_leaf=20,l2_regularization=1.0,random_state=20260829).fit(tr[['floor']],tr.floor_relapse_5s)
def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float)
 return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def main():
 ids=list(map(int,json.loads(PRE.read_text(encoding='utf-8'))['marketIds']));full=joblib.load(MODEL)['model'];floor=train_floor();rows=[];errs=[]
 for mid in ids:
  p=SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz'
  try:d=json.load(lzma.open(p,'rt',encoding='utf-8'))
  except Exception as e:errs.append({'marketId':mid,'error':str(e)});continue
  last=int((d.get('feed') or {}).get('lastReceivedMs') or 0);dr=sorted(d.get('decisionRows') or [],key=lambda r:int(r.get('decisionMs') or 0));times=[int(r.get('decisionMs') or 0) for r in dr]
  for i,r in enumerate(dr):
   t=times[i];sec=(last-t)/1000.;po=r.get('portfolio') or {};fl=float(po.get('worst_case_floor') or 0.);gap=float(po.get('abs_payoff_gap') or po.get('combined_abs_net') or 0.);gross=float(po.get('combined_gross') or 0.)
   if not (0<sec<=60 and fl>=0 and gross>0):continue
   fut=[]
   j=i+1
   while j<len(dr) and times[j]<=t+5000:
    fpo=dr[j].get('portfolio') or {};fut.append(float(fpo.get('worst_case_floor') or 0.));j+=1
   rows.append({'marketId':mid,'decisionMs':t,'seconds_left':sec,'floor':fl,'abs_gap':gap,'gross':gross,'floorRelapse5s':int(any(x<0 for x in fut))})
 df=pd.DataFrame(rows);df.to_csv(ROWS,index=False)
 if df.empty or df.floorRelapse5s.nunique()<2:raise SystemExit('insufficient rows/classes')
 pf=floor.predict_proba(df[['floor']])[:,1];pg=full.predict_proba(df[['seconds_left','floor','abs_gap']])[:,1];a=met(df.floorRelapse5s,pf);b=met(df.floorRelapse5s,pg);delta={'auc':b['auc']-a['auc'],'ap':b['ap']-a['ap'],'logLossImprovement':a['logLoss']-b['logLoss']}
 # market-level chronological thirds as no-tuning stability readout
 mids=sorted(df.marketId.unique().tolist());blocks=np.array_split(mids,3);st=[]
 for bi,bb in enumerate(blocks):
  z=df[df.marketId.isin(set(map(int,bb.tolist())))];
  if z.floorRelapse5s.nunique()<2:st.append({'block':bi,'markets':int(z.marketId.nunique()),'n':len(z),'rate':float(z.floorRelapse5s.mean())});continue
  af=met(z.floorRelapse5s,floor.predict_proba(z[['floor']])[:,1]);bg=met(z.floorRelapse5s,full.predict_proba(z[['seconds_left','floor','abs_gap']])[:,1]);st.append({'block':bi,'markets':int(z.marketId.nunique()),'FLOOR_ONLY':af,'FULL':bg,'delta':{'auc':bg['auc']-af['auc'],'ap':bg['ap']-af['ap'],'logLossImprovement':af['logLoss']-bg['logLoss']}})
 fixed=bool(df.marketId.nunique()>=50 and b['auc']>.5 and delta['auc']>=0 and delta['ap']>=0 and delta['logLossImprovement']>=0)
 art={'version':'R4_PROTECTION_HOLD_HFT_DECISION_TRAJECTORY_300_V1','status':'TESTED_KEEP_SIGNAL' if fixed else 'TESTED_REJECTED','researchOnly':True,'actionAuthority':False,'preRegistration':str(PRE.relative_to(ROOT)).replace('\\','/'),'coverage':{'requestedMarkets':len(ids),'eligibleMarkets':int(df.marketId.nunique()),'rows':int(len(df)),'positiveRate':float(df.floorRelapse5s.mean())},'FLOOR_ONLY':a,'FULL':b,'deltaFullVsFloor':delta,'chronologicalThirds':st,'fixedRulePassed':fixed,'errors':errs,'guards':['Read-only existing realistic HFT decision trajectory.','combined_gross>0 prevents empty floor=0 from counting as safe base.','Future decision rows used only as 5s relapse labels.','No HFT refit or threshold tuning.']}
 OUT.write_text(json.dumps(art,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':art['status'],'coverage':art['coverage'],'floorOnly':a,'full':b,'delta':delta,'thirds':st,'errors':len(errs)},indent=2))
if __name__=='__main__':main()
