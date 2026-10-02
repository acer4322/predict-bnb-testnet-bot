from __future__ import annotations
import argparse,sqlite3,json,joblib
from collections import defaultdict,deque
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
EPS=1e-9
COLS=['floor','best','absNet','gross','debt','floorBestRatio','debtGrossRatio','repairProgressFrac','repairQty5','repairQty15','repairQty30','expandQty5','expandQty15','expandQty30','repairCount5','repairCount15','repairCount30','expandCount5','expandCount15','expandCount30','secSinceRepair','secSinceExpand','sameRoleStreak','lastWasRepair']
def met(y,p):return {'n':int(len(y)),'positive':int(y.sum()),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);ap.add_argument('--model-out',required=True);a=ap.parse_args();con=sqlite3.connect(a.db);evs=defaultdict(list)
 for asset,m,role,side,t,p,q,pid in con.execute("select asset,market_id,role,side,first_event_ms,average_price,shares,parent_id from target_parent_orders where average_price is not null and shares>0 and asset='ETH' order by market_id,first_event_ms,parent_id"):
  if str(role).upper()=='MAKER':evs[int(m)].append((int(t),str(side).upper(),float(p),float(q)))
 con.close();rows=[]
 for mid,xs in evs.items():
  U=D=C=0.;debt=0.;repaired=0.;hist=deque();lastKind=None;streak=0;lastR=None;lastE=None
  for t,side,p,q in xs:
   while hist and t-hist[0][0]>30000:hist.popleft()
   preAbs=abs(U-D);gross=U+D;floor=min(U,D)-C;best=max(U,D)-C;preDebt=debt;vals=None
   if preDebt>EPS and lastKind=='REPAIR':
    def agg(k,w):
     z=[h for h in hist if h[1]==k and t-h[0]<=w];return sum(h[2] for h in z),len(z)
    rq5,rc5=agg('REPAIR',5000);rq15,rc15=agg('REPAIR',15000);rq30,rc30=agg('REPAIR',30000);eq5,ec5=agg('EXPAND',5000);eq15,ec15=agg('EXPAND',15000);eq30,ec30=agg('EXPAND',30000)
    vals={'floor':floor,'best':best,'absNet':preAbs,'gross':gross,'debt':preDebt,'floorBestRatio':floor/(abs(best)+1.),'debtGrossRatio':preDebt/(gross+1.),'repairProgressFrac':repaired/(repaired+preDebt+EPS),'repairQty5':rq5,'repairQty15':rq15,'repairQty30':rq30,'expandQty5':eq5,'expandQty15':eq15,'expandQty30':eq30,'repairCount5':rc5,'repairCount15':rc15,'repairCount30':rc30,'expandCount5':ec5,'expandCount15':ec15,'expandCount30':ec30,'secSinceRepair':120. if lastR is None else min(120.,(t-lastR)/1000.),'secSinceExpand':120. if lastE is None else min(120.,(t-lastE)/1000.),'sameRoleStreak':streak,'lastWasRepair':1.}
   if side=='UP':U+=q
   else:D+=q
   C+=p*q;postAbs=abs(U-D);delta=postAbs-preAbs
   if delta>EPS:kind='EXPAND';debt=max(0.,preDebt)+delta;lastE=t
   elif delta<-EPS:kind='REPAIR';pay=min(max(0.,preDebt),-delta);debt=max(0.,preDebt-pay);repaired+=pay;lastR=t
   else:kind='FLAT'
   if vals is not None and kind in ('EXPAND','REPAIR'):vals.update({'marketId':mid,'labelExpand':1 if kind=='EXPAND' else 0});rows.append(vals)
   if kind in ('EXPAND','REPAIR'):streak=streak+1 if kind==lastKind else 1;lastKind=kind;hist.append((t,kind,q))
   if debt<=EPS:repaired=0.
 mids=sorted(set(r['marketId'] for r in rows));n=len(mids);split={m:('TRAIN60' if (i+1)/n<=.6 else ('VALID20' if (i+1)/n<=.8 else 'TEST20')) for i,m in enumerate(mids)}
 groups={s:[r for r in rows if split[r['marketId']]==s] for s in ['TRAIN60','VALID20','TEST20']};tr=groups['TRAIN60'];X=np.asarray([[r[c] for c in COLS] for r in tr],np.float32);y=np.asarray([r['labelExpand'] for r in tr],np.int8);model=HistGradientBoostingClassifier(max_depth=5,learning_rate=.06,max_iter=180,l2_regularization=1.,random_state=43).fit(X,y);metrics={}
 for s,rr in groups.items():
  xx=np.asarray([[r[c] for c in COLS] for r in rr],np.float32);yy=np.asarray([r['labelExpand'] for r in rr],np.int8);pp=model.predict_proba(xx)[:,1];metrics[s]=met(yy,pp)
 out={'version':'TARGET_ETH_REPAIR_NEXT_RESPONSIBILITY_SPECIALIST_V42C','researchOnly':True,'actionAuthority':False,'scope':'ETH Maker actual-fill states, outstanding local expansion debt, immediately prior actual responsibility=REPAIR','features':COLS,'rows':len(rows),'markets':len(mids),'metrics':metrics,'guards':['strict-past only','no winner','no PnL','no future-action feature beyond current training label','chronological market split']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');joblib.dump({'version':out['version'],'model':model,'features':COLS},a.model_out);print(json.dumps(out,indent=2))
if __name__=='__main__':main()
