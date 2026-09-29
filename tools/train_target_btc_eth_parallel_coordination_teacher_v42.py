from __future__ import annotations
import sqlite3,json,math
from collections import defaultdict,deque
from pathlib import Path
import argparse
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/research/r4_v0/p0_provenance_v1/target_eth_btc_strategy_compare_snapshot_v1.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_BTC_ETH_PARALLEL_COORDINATION_TEACHER_V42.json'
MODEL=ROOT/'data/research/r4_v0/p0_provenance_v1/target_btc_eth_parallel_coordination_teacher_v42_models.joblib'
EPS=1e-9
BASE=['floor','best','absNet','gross','debt','floorBestRatio','debtGrossRatio']
PROG=BASE+['repairProgressFrac']
COORD=PROG+['repairQty5','repairQty15','repairQty30','expandQty5','expandQty15','expandQty30','repairCount5','repairCount15','repairCount30','expandCount5','expandCount15','expandCount30','secSinceRepair','secSinceExpand','sameRoleStreak','lastWasRepair']

def metrics(y,p):
 return {'n':int(len(y)),'positive':int(y.sum()),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db');ap.add_argument('--output');ap.add_argument('--model-out');a=ap.parse_args();db=Path(a.db) if a.db else DB;outp=Path(a.output) if a.output else OUT;modelp=Path(a.model_out) if a.model_out else MODEL
 con=sqlite3.connect(db); evs=defaultdict(list)
 q='''select asset,market_id,role,side,first_event_ms,average_price,shares,parent_id from target_parent_orders where average_price is not null and shares>0 order by asset,market_id,first_event_ms,parent_id'''
 for a,m,role,side,t,p,qty,pid in con.execute(q):
  if str(role).upper()!='MAKER':continue
  evs[(str(a).upper(),int(m))].append((int(t),str(side).upper(),float(p),float(qty),pid))
 con.close()
 rows=[]
 for (asset,mid),xs in evs.items():
  U=D=C=0.0; debt=0.0; repairedCum=0.0; hist=deque(); lastKind=None; streak=0; lastRepairT=None; lastExpandT=None
  for t,side,p,q,pid in xs:
   while hist and t-hist[0][0]>30000:hist.popleft()
   preAbs=abs(U-D); gross=U+D; floor=min(U,D)-C; best=max(U,D)-C; preDebt=debt
   # Strict-past feature state before this actual fill.
   if preDebt>EPS:
    def agg(kind,win):
     z=[h for h in hist if h[1]==kind and t-h[0]<=win]
     return sum(h[2] for h in z),len(z)
    rq5,rc5=agg('REPAIR',5000);rq15,rc15=agg('REPAIR',15000);rq30,rc30=agg('REPAIR',30000)
    eq5,ec5=agg('EXPAND',5000);eq15,ec15=agg('EXPAND',15000);eq30,ec30=agg('EXPAND',30000)
    vals={'floor':floor,'best':best,'absNet':preAbs,'gross':gross,'debt':preDebt,'floorBestRatio':floor/(abs(best)+1.0),'debtGrossRatio':preDebt/(gross+1.0),'repairProgressFrac':repairedCum/(repairedCum+preDebt+EPS),'repairQty5':rq5,'repairQty15':rq15,'repairQty30':rq30,'expandQty5':eq5,'expandQty15':eq15,'expandQty30':eq30,'repairCount5':rc5,'repairCount15':rc15,'repairCount30':rc30,'expandCount5':ec5,'expandCount15':ec15,'expandCount30':ec30,'secSinceRepair':120.0 if lastRepairT is None else min(120.0,(t-lastRepairT)/1000.0),'secSinceExpand':120.0 if lastExpandT is None else min(120.0,(t-lastExpandT)/1000.0),'sameRoleStreak':streak,'lastWasRepair':1.0 if lastKind=='REPAIR' else 0.0}
   if side=='UP':U+=q
   else:D+=q
   C+=p*q; postAbs=abs(U-D); delta=postAbs-preAbs
   if delta>EPS:kind='EXPAND'; debt=max(0.0,preDebt)+delta; lastExpandT=t
   elif delta< -EPS:kind='REPAIR'; pay=min(max(0.0,preDebt),-delta); debt=max(0.0,preDebt-pay); repairedCum+=pay; lastRepairT=t
   else:kind='FLAT'
   if preDebt>EPS and kind in ('EXPAND','REPAIR'):
    vals.update({'asset':asset,'marketId':mid,'t':t,'labelExpand':1 if kind=='EXPAND' else 0});rows.append(vals)
   if kind in ('EXPAND','REPAIR'):
    streak=streak+1 if kind==lastKind else 1;lastKind=kind;hist.append((t,kind,q))
   if debt<=EPS: repairedCum=0.0
 mids=defaultdict(list)
 for r in rows:mids[r['asset']].append(r['marketId'])
 split={}
 for a,ms in mids.items():
  xs=sorted(set(ms));n=len(xs)
  for i,m in enumerate(xs):split[(a,m)]='TRAIN60' if (i+1)/n<=.6 else ('VALID20' if (i+1)/n<=.8 else 'TEST20')
 out={'version':'TARGET_BTC_ETH_PARALLEL_COORDINATION_TEACHER_V42','researchOnly':True,'actionAuthority':False,'label':'next actual Target Maker responsibility while local expansion debt>0: EXPAND=1 vs REPAIR=0','strictPast':True,'winnerFeature':False,'pnlFeature':False,'futureActionFeature':False,'features':{'GEOMETRY':BASE,'PROGRESS':PROG,'COORDINATION':COORD},'assets':{},'coverage':{'rows':len(rows)}};models={}
 for asset in ('BTC','ETH'):
  ar=[r for r in rows if r['asset']==asset]; out['assets'][asset]={'rows':len(ar),'markets':len(set(r['marketId'] for r in ar)),'models':{}}
  for name,cols in [('GEOMETRY',BASE),('PROGRESS',PROG),('COORDINATION',COORD)]:
   tr=[r for r in ar if split[(asset,r['marketId'])]=='TRAIN60']; va=[r for r in ar if split[(asset,r['marketId'])]=='VALID20']; te=[r for r in ar if split[(asset,r['marketId'])]=='TEST20']
   Xtr=np.asarray([[float(r[c]) for c in cols] for r in tr],np.float32);ytr=np.asarray([r['labelExpand'] for r in tr],np.int8)
   model=HistGradientBoostingClassifier(max_depth=5,learning_rate=.06,max_iter=180,l2_regularization=1.0,random_state=42).fit(Xtr,ytr)
   z={}
   for sn,rr in [('train',tr),('validation',va),('test',te)]:
    X=np.asarray([[float(r[c]) for c in cols] for r in rr],np.float32);y=np.asarray([r['labelExpand'] for r in rr],np.int8);p=model.predict_proba(X)[:,1];z[sn]=metrics(y,p)
   out['assets'][asset]['models'][name]=z;models[(asset,name)]={'model':model,'features':cols}
  g=out['assets'][asset]['models']['GEOMETRY']['test'];p=out['assets'][asset]['models']['PROGRESS']['test'];c=out['assets'][asset]['models']['COORDINATION']['test']
  out['assets'][asset]['testDeltas']={'progressMinusGeometryAuc':p['auc']-g['auc'],'coordinationMinusGeometryAuc':c['auc']-g['auc'],'coordinationMinusProgressAuc':c['auc']-p['auc'],'coordinationMinusGeometryLogLoss':c['logLoss']-g['logLoss']}
 outp.parent.mkdir(parents=True,exist_ok=True);modelp.parent.mkdir(parents=True,exist_ok=True);outp.write_text(json.dumps(out,indent=2),encoding='utf-8');joblib.dump(models,modelp)
 print(json.dumps({'out':str(outp),'model':str(modelp),'coverage':out['coverage'],'brief':{a:{'geometry':out['assets'][a]['models']['GEOMETRY']['test'],'progress':out['assets'][a]['models']['PROGRESS']['test'],'coordination':out['assets'][a]['models']['COORDINATION']['test'],'deltas':out['assets'][a]['testDeltas']} for a in ('BTC','ETH')}},indent=2))
if __name__=='__main__':main()
