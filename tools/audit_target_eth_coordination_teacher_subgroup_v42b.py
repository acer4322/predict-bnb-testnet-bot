from __future__ import annotations
import argparse,sqlite3,json,math,joblib
from collections import defaultdict,deque
from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
EPS=1e-9

def qtile(a,p):
 a=sorted(float(x) for x in a)
 if not a:return None
 x=(len(a)-1)*p;lo=int(math.floor(x));hi=int(math.ceil(x));w=x-lo
 return a[lo] if lo==hi else a[lo]*(1-w)+a[hi]*w

def stat(a):
 a=[float(x) for x in a];return {'n':len(a),'median':qtile(a,.5),'p05':qtile(a,.05),'p25':qtile(a,.25),'p75':qtile(a,.75),'p95':qtile(a,.95)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--model',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 cm=joblib.load(a.model)[('ETH','COORDINATION')];cols=cm['features'];model=cm['model'];con=sqlite3.connect(a.db);evs=defaultdict(list)
 for asset,m,role,side,t,p,q,pid in con.execute('''select asset,market_id,role,side,first_event_ms,average_price,shares,parent_id from target_parent_orders where average_price is not null and shares>0 and asset='ETH' order by market_id,first_event_ms,parent_id'''):
  if str(role).upper()=='MAKER':evs[int(m)].append((int(t),str(side).upper(),float(p),float(q)))
 con.close();mids=sorted(evs);n=len(mids);testset=set(mids[int(.8*n):]);rows=[]
 for mid,xs in evs.items():
  if mid not in testset:continue
  U=D=C=0.0;debt=0.0;repaired=0.0;hist=deque();lastKind=None;streak=0;lastR=None;lastE=None
  for t,side,p,q in xs:
   while hist and t-hist[0][0]>30000:hist.popleft()
   preAbs=abs(U-D);gross=U+D;floor=min(U,D)-C;best=max(U,D)-C;preDebt=debt
   vals=None
   if preDebt>EPS:
    def agg(k,w):
     z=[h for h in hist if h[1]==k and t-h[0]<=w];return sum(h[2] for h in z),len(z)
    rq5,rc5=agg('REPAIR',5000);rq15,rc15=agg('REPAIR',15000);rq30,rc30=agg('REPAIR',30000);eq5,ec5=agg('EXPAND',5000);eq15,ec15=agg('EXPAND',15000);eq30,ec30=agg('EXPAND',30000)
    vals={'floor':floor,'best':best,'absNet':preAbs,'gross':gross,'debt':preDebt,'floorBestRatio':floor/(abs(best)+1.0),'debtGrossRatio':preDebt/(gross+1.0),'repairProgressFrac':repaired/(repaired+preDebt+EPS),'repairQty5':rq5,'repairQty15':rq15,'repairQty30':rq30,'expandQty5':eq5,'expandQty15':eq15,'expandQty30':eq30,'repairCount5':rc5,'repairCount15':rc15,'repairCount30':rc30,'expandCount5':ec5,'expandCount15':ec15,'expandCount30':ec30,'secSinceRepair':120.0 if lastR is None else min(120.0,(t-lastR)/1000),'secSinceExpand':120.0 if lastE is None else min(120.0,(t-lastE)/1000),'sameRoleStreak':streak,'lastWasRepair':1.0 if lastKind=='REPAIR' else 0.0}
   if side=='UP':U+=q
   else:D+=q
   C+=p*q;postAbs=abs(U-D);delta=postAbs-preAbs
   if delta>EPS:kind='EXPAND';debt=max(0.0,preDebt)+delta;lastE=t
   elif delta< -EPS:kind='REPAIR';pay=min(max(0.0,preDebt),-delta);debt=max(0.0,preDebt-pay);repaired+=pay;lastR=t
   else:kind='FLAT'
   if vals is not None and kind in ('EXPAND','REPAIR'):
    x=np.asarray([[float(vals[c]) for c in cols]],np.float32);pp=float(model.predict_proba(x)[0,1]);rows.append({'marketId':mid,'labelExpand':1 if kind=='EXPAND' else 0,'pExpand':pp,**vals})
   if kind in ('EXPAND','REPAIR'):
    streak=streak+1 if kind==lastKind else 1;lastKind=kind;hist.append((t,kind,q))
   if debt<=EPS:repaired=0.0
 sub=[r for r in rows if r['lastWasRepair']>=.5];y=np.asarray([r['labelExpand'] for r in sub],int);p=np.asarray([r['pExpand'] for r in sub],float)
 bins={'P0_25':(0,.25),'P25_50':(.25,.5),'P50_75':(.5,.75),'P75_100':(.75,1.000001)};b={}
 for name,(lo,hi) in bins.items():
  z=[r for r in sub if lo<=r['repairProgressFrac']<hi];b[name]={'n':len(z),'actualExpandRate':sum(r['labelExpand'] for r in z)/len(z) if z else None,'teacherMajorityExpandRate':sum(r['pExpand']>=.5 for r in z)/len(z) if z else None,'pExpand':stat([r['pExpand'] for r in z]) if z else {'n':0}}
 out={'version':'TARGET_ETH_COORDINATION_TEACHER_SUBGROUP_V42B','researchOnly':True,'actionAuthority':False,'scope':'ETH TEST20 states with outstanding debt and immediately previous actual Maker responsibility=REPAIR','n':len(sub),'markets':len(set(r['marketId'] for r in sub)),'actualExpandRate':float(y.mean()) if len(y) else None,'teacherMajorityExpandRate':float((p>=.5).mean()) if len(p) else None,'teacherMetrics':{'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)) if len(set(y))>1 else None,'logLoss':float(log_loss(y,p,labels=[0,1])) if len(y) else None},'pExpand':stat(p),'featureRanges':{k:stat([r[k] for r in sub]) for k in ['floor','best','debt','repairProgressFrac','repairQty30','expandQty30','secSinceRepair','secSinceExpand','sameRoleStreak']},'byRepairProgress':b}
 Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
