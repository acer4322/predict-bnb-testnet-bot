from __future__ import annotations
import argparse,sqlite3,json,joblib
from collections import defaultdict,deque
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
EPS=1e-9
BASE=['floorBestRatio','debtGrossRatio','absNetGrossRatio','repairProgressFrac','repairQty5Gross','repairQty15Gross','repairQty30Gross','expandQty5Gross','expandQty15Gross','expandQty30Gross','repairQty30Debt','expandQty30Debt','lastRepairWasTaker']
LATEST=BASE+['latestGenerationDebtGross','latestGenerationRemainingGross','latestGenerationRepairProgress']
STACK=['openGenerationCount','totalGenerationRemainingGross','inheritedRemainingGross','oldestGenerationProgress','newestGenerationProgress','minimumOpenGenerationProgress']
GROUPS={'LATEST_ONLY':LATEST,'STACK_LIFO':LATEST+[f'lifo_{x}' for x in STACK],'STACK_FIFO':LATEST+[f'fifo_{x}' for x in STACK]}

def met(y,p):return {'n':int(len(y)),'positive':int(y.sum()),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def pay_stack(stack,pay,newest_first):
 rem=float(pay);idx=range(len(stack)-1,-1,-1) if newest_first else range(len(stack))
 for i in idx:
  g=stack[i];owed=max(0.,g['debt']-g['paid'])
  if owed<=EPS:continue
  x=min(owed,rem);g['paid']+=x;rem-=x
  if rem<=EPS:break
 return rem
def stack_feat(stack,gross,prefix):
 opens=[g for g in stack if g['debt']-g['paid']>EPS];gp=gross+1.
 if not opens:return {prefix+'_'+k:v for k,v in zip(STACK,[0.,0.,0.,1.,1.,1.])}
 total=sum(max(0.,g['debt']-g['paid']) for g in opens);new=opens[-1];old=opens[0];inherited=max(0.,total-max(0.,new['debt']-new['paid']))
 prog=lambda g:min(1.,g['paid']/(g['debt']+EPS)) if g['debt']>EPS else 1.
 vals=[float(len(opens)),total/gp,inherited/gp,prog(old),prog(new),min(prog(g) for g in opens)]
 return {prefix+'_'+k:v for k,v in zip(STACK,vals)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);ap.add_argument('--model-out',required=True);a=ap.parse_args();con=sqlite3.connect(a.db);ev=defaultdict(list)
 q="select market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset='ETH' and average_price is not null and shares>0 order by market_id,first_event_ms,parent_id"
 for m,role,side,t,p,qty in con.execute(q):ev[int(m)].append((int(t),str(role).upper(),str(side).upper(),float(p),float(qty)))
 con.close();rows=[]
 for mid,xs in ev.items():
  U=D=C=debt=repaired=0.;hist=deque();lastKind=None;lastRole=None;latestDebt=latestPaid=0.;lifo=[];fifo=[]
  for t,role,side,p,qty in xs:
   while hist and t-hist[0][0]>30000:hist.popleft()
   preAbs=abs(U-D);gross=U+D;floor=min(U,D)-C;best=max(U,D)-C;preDebt=debt;vals=None
   if preDebt>EPS and lastKind=='REPAIR' and lastRole in ('MAKER','TAKER'):
    def qsum(k,w):return sum(h[2] for h in hist if h[1]==k and t-h[0]<=w)
    rq5,rq15,rq30=qsum('REPAIR',5000),qsum('REPAIR',15000),qsum('REPAIR',30000);eq5,eq15,eq30=qsum('EXPAND',5000),qsum('EXPAND',15000),qsum('EXPAND',30000);gp=gross+1.;dp=preDebt+1.;lrem=max(0.,latestDebt-latestPaid)
    vals={'floorBestRatio':floor/(abs(best)+1.),'debtGrossRatio':preDebt/gp,'absNetGrossRatio':preAbs/gp,'repairProgressFrac':repaired/(repaired+preDebt+EPS),'repairQty5Gross':rq5/gp,'repairQty15Gross':rq15/gp,'repairQty30Gross':rq30/gp,'expandQty5Gross':eq5/gp,'expandQty15Gross':eq15/gp,'expandQty30Gross':eq30/gp,'repairQty30Debt':rq30/dp,'expandQty30Debt':eq30/dp,'lastRepairWasTaker':1. if lastRole=='TAKER' else 0.,'latestGenerationDebtGross':latestDebt/gp,'latestGenerationRemainingGross':lrem/gp,'latestGenerationRepairProgress':min(1.,latestPaid/(latestDebt+EPS)) if latestDebt>EPS else 1.,'lastRepairRole':lastRole}
    vals.update(stack_feat(lifo,gross,'lifo'));vals.update(stack_feat(fifo,gross,'fifo'))
   if side=='UP':U+=qty
   else:D+=qty
   C+=p*qty;postAbs=abs(U-D);delta=postAbs-preAbs
   if delta>EPS:
    kind='EXPAND';debt=max(0.,preDebt)+delta;latestDebt=delta;latestPaid=0.;lifo.append({'debt':delta,'paid':0.});fifo.append({'debt':delta,'paid':0.})
   elif delta<-EPS:
    kind='REPAIR';pay=min(max(0.,preDebt),-delta);debt=max(0.,preDebt-pay);repaired+=pay;latestPaid=min(latestDebt,latestPaid+pay);pay_stack(lifo,pay,True);pay_stack(fifo,pay,False)
   else:kind='FLAT'
   if vals is not None and kind in ('EXPAND','REPAIR'):vals.update({'marketId':mid,'labelExpand':int(kind=='EXPAND')});rows.append(vals)
   if kind in ('EXPAND','REPAIR'):lastKind=kind;lastRole=role;hist.append((t,kind,qty,role))
   if debt<=EPS:repaired=0.;latestDebt=latestPaid=0.;lifo=[];fifo=[]
 mids=sorted(set(r['marketId'] for r in rows));n=len(mids);sp={m:('TRAIN60' if (i+1)/n<=.6 else ('VALID20' if (i+1)/n<=.8 else 'TEST20')) for i,m in enumerate(mids)};parts={s:[r for r in rows if sp[r['marketId']]==s] for s in ['TRAIN60','VALID20','TEST20']};models={};metrics={}
 for name,cols in GROUPS.items():
  tr=parts['TRAIN60'];X=np.asarray([[r[c] for c in cols] for r in tr],np.float32);y=np.asarray([r['labelExpand'] for r in tr],np.int8);model=HistGradientBoostingClassifier(max_depth=5,learning_rate=.06,max_iter=180,l2_regularization=1.,random_state=50).fit(X,y);models[name]={'model':model,'features':cols};metrics[name]={}
  for s,rr in parts.items():
   xx=np.asarray([[r[c] for c in cols] for r in rr],np.float32);yy=np.asarray([r['labelExpand'] for r in rr],np.int8);pp=model.predict_proba(xx)[:,1];metrics[name][s]=met(yy,pp)
 out={'version':'TARGET_ETH_MULTIGENERATION_RESPONSIBILITY_STACK_V50','researchOnly':True,'actionAuthority':False,'featureGroups':GROUPS,'rows':len(rows),'markets':len(mids),'metrics':metrics,'guards':['strict-past actual Target ETH fills','chronological market split','same fixed HGB family/hyperparameters','no winner/PnL','no threshold fitting'],'semantics':{'LATEST_ONLY':'V47-style latest generation only','STACK_LIFO':'Repair attribution newest-generation-first','STACK_FIFO':'Repair attribution oldest-generation-first'}};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');joblib.dump({'version':out['version'],'models':models},a.model_out);print(json.dumps(out,indent=2))
if __name__=='__main__':main()
