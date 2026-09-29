from __future__ import annotations
import argparse,sqlite3,json,joblib
from collections import defaultdict,deque
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
EPS=1e-9
BASE=['floorBestRatio','debtGrossRatio','absNetGrossRatio','repairProgressFrac','repairQty5Gross','repairQty15Gross','repairQty30Gross','expandQty5Gross','expandQty15Gross','expandQty30Gross','repairQty30Debt','expandQty30Debt','lastRepairWasTaker']
GEN=BASE+['genRepairFloorGainToExpandCost','genRepairQtyToExpandQty','genDebtPaidFrac','genRepairCostToExpandCost','lastExpandPrice','lastRepairPrice','genRepairCount']
def met(y,p):return {'n':int(len(y)),'positive':int(y.sum()),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def train_asset(asset,evs):
 rows=[]
 for mid,xs in evs.items():
  U=D=C=0.;debt=0.;repaired=0.;hist=deque();lastKind=None;lastR=None;lastE=None;lastRepairWasTaker=0.;lastRepairPrice=0.;lastExpandPrice=0.;lastExpandQty=0.;lastExpandCost=0.;debtAfterExpand=0.;genGain=0.;genRQ=0.;genRCost=0.;genRCount=0
  for t,execrole,side,p,q in xs:
   while hist and t-hist[0][0]>30000:hist.popleft()
   preAbs=abs(U-D);gross=U+D;floor=min(U,D)-C;best=max(U,D)-C;preDebt=debt;vals=None
   if preDebt>EPS and lastKind=='REPAIR' and lastExpandCost>EPS and lastExpandQty>EPS:
    def agg(k,w):
     z=[h for h in hist if h[1]==k and t-h[0]<=w];return sum(h[2] for h in z)
    rq5=agg('REPAIR',5000);rq15=agg('REPAIR',15000);rq30=agg('REPAIR',30000);eq5=agg('EXPAND',5000);eq15=agg('EXPAND',15000);eq30=agg('EXPAND',30000);gp=gross+1.;dp=preDebt+1.
    vals={'floorBestRatio':floor/(abs(best)+1.),'debtGrossRatio':preDebt/gp,'absNetGrossRatio':preAbs/gp,'repairProgressFrac':repaired/(repaired+preDebt+EPS),'repairQty5Gross':rq5/gp,'repairQty15Gross':rq15/gp,'repairQty30Gross':rq30/gp,'expandQty5Gross':eq5/gp,'expandQty15Gross':eq15/gp,'expandQty30Gross':eq30/gp,'repairQty30Debt':rq30/dp,'expandQty30Debt':eq30/dp,'lastRepairWasTaker':lastRepairWasTaker,'genRepairFloorGainToExpandCost':genGain/(lastExpandCost+EPS),'genRepairQtyToExpandQty':genRQ/(lastExpandQty+EPS),'genDebtPaidFrac':max(0.,min(2.,(debtAfterExpand-preDebt)/(debtAfterExpand+EPS))) if debtAfterExpand>EPS else 0.,'genRepairCostToExpandCost':genRCost/(lastExpandCost+EPS),'lastExpandPrice':lastExpandPrice,'lastRepairPrice':lastRepairPrice,'genRepairCount':min(genRCount,12)}
   if side=='UP':U+=q
   else:D+=q
   C+=p*q;postAbs=abs(U-D);delta=postAbs-preAbs
   if delta>EPS:
    kind='EXPAND';debt=max(0.,preDebt)+delta;lastE=t;lastExpandPrice=p;lastExpandQty=delta;lastExpandCost=p*delta;debtAfterExpand=debt;genGain=0.;genRQ=0.;genRCost=0.;genRCount=0
   elif delta<-EPS:
    kind='REPAIR';pay=min(max(0.,preDebt),-delta);debt=max(0.,preDebt-pay);repaired+=pay;lastR=t;lastRepairWasTaker=1. if execrole=='TAKER' else 0.;lastRepairPrice=p;genGain+=pay*max(0.,1.-p);genRQ+=pay;genRCost+=pay*p;genRCount+=1
   else:kind='FLAT'
   if vals is not None and kind in ('EXPAND','REPAIR'):vals.update({'marketId':mid,'labelExpand':1 if kind=='EXPAND' else 0});rows.append(vals)
   if kind in ('EXPAND','REPAIR'):hist.append((t,kind,q));lastKind=kind
   if debt<=EPS:repaired=0.
 mids=sorted(set(r['marketId'] for r in rows));n=len(mids);split={m:('TRAIN60' if (i+1)/n<=.6 else ('VALID20' if (i+1)/n<=.8 else 'TEST20')) for i,m in enumerate(mids)};groups={s:[r for r in rows if split[r['marketId']]==s] for s in ['TRAIN60','VALID20','TEST20']};mods={};metrics={}
 for name,cols in [('ROLE_AWARE_EVENT_NORM',BASE),('GEN_ECON_NORM',GEN)]:
  tr=groups['TRAIN60'];X=np.asarray([[r[c] for c in cols] for r in tr],np.float32);y=np.asarray([r['labelExpand'] for r in tr],np.int8);model=HistGradientBoostingClassifier(max_depth=5,learning_rate=.06,max_iter=180,l2_regularization=1.,random_state=48).fit(X,y);mods[name]={'model':model,'features':cols};metrics[name]={}
  for s,rr in groups.items():
   xx=np.asarray([[r[c] for c in cols] for r in rr],np.float32);yy=np.asarray([r['labelExpand'] for r in rr],np.int8);pp=model.predict_proba(xx)[:,1];metrics[name][s]=met(yy,pp)
 test=groups['TEST20'];bins={}
 for lo,hi,nm in [(0,.25,'LT025'),(.25,.5,'025_050'),(.5,.75,'050_075'),(.75,1.,'075_100'),(1.,99.,'GE1')]:
  rr=[r for r in test if lo<=r['genRepairFloorGainToExpandCost']<hi];bins[nm]={'n':len(rr),'expandRate':sum(r['labelExpand'] for r in rr)/len(rr) if rr else None,'takerLastRepairRate':sum(r['lastRepairWasTaker'] for r in rr)/len(rr) if rr else None}
 return {'rows':len(rows),'markets':len(mids),'metrics':metrics,'testGenerationValueBins':bins},mods
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);ap.add_argument('--model-out',required=True);a=ap.parse_args();con=sqlite3.connect(a.db);allmods={};report={}
 for asset in ('BTC','ETH'):
  evs=defaultdict(list)
  for m,role,side,t,p,q,pid in con.execute("select market_id,role,side,first_event_ms,average_price,shares,parent_id from target_parent_orders where average_price is not null and shares>0 and asset=? order by market_id,first_event_ms,parent_id",(asset,)):
   evs[int(m)].append((int(t),str(role).upper(),str(side).upper(),float(p),float(q)))
  rep,mods=train_asset(asset,evs);report[asset]=rep
  for k,v in mods.items():allmods[(asset,k)]=v
 con.close();out={'version':'TARGET_BTC_ETH_GENERATION_ECONOMIC_COORDINATOR_V48','researchOnly':True,'actionAuthority':False,'featureGroups':{'ROLE_AWARE_EVENT_NORM':BASE,'GEN_ECON_NORM':GEN},'assets':report,'guards':['strict-past actual Target fills','chronological market split','no winner/PnL','generation economics computed only from already-realized fills','BTC used as architecture cross-check; no BTC threshold transfer']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');joblib.dump({'version':out['version'],'models':allmods},a.model_out);print(json.dumps(out,indent=2))
if __name__=='__main__':main()
