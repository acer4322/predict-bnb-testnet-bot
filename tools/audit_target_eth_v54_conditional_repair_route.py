from __future__ import annotations
import argparse,sqlite3,json,joblib,importlib.util,sys
from collections import defaultdict,deque
from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,accuracy_score
EPS=1e-9

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--model',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 pack=joblib.load(a.model);mm=pack['models']['PARALLEL_ROUTE_NORM'];model=mm['model'];cols=mm['features'];labels=mm['labels'];ai=labels.index('ACTIVE_REPAIR');pi=labels.index('PASSIVE_REPAIR')
 con=sqlite3.connect(a.db);ev=defaultdict(list)
 for m,role,side,t,p,q in con.execute("select market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset='ETH' and average_price is not null and shares>0 order by market_id,first_event_ms,parent_id"):ev[int(m)].append((int(t),str(role).upper(),str(side).upper(),float(p),float(q)))
 con.close();rows=[]
 for mid,xs in ev.items():
  U=D=C=debt=repaired=0.;lgd=lgp=0.;hist=deque();lr=le=None;states=[];economic=[]
  for t,role,side,p,q in xs:
   while hist and t-hist[0][0]>30000:hist.popleft()
   pre=abs(U-D);pd=debt
   if side=='UP':U+=q
   else:D+=q
   C+=p*q;post=abs(U-D);d=post-pre
   if d>EPS:kind='EXPAND';debt=max(0.,pd)+d;lgd=d;lgp=0.;le=role
   elif d<-EPS:kind='REPAIR';pay=min(max(0.,pd),-d);debt=max(0.,pd-pay);repaired+=pay;lgp=min(lgd,lgp+pay);lr=role
   else:kind='FLAT'
   if kind in ('EXPAND','REPAIR'):hist.append((t,kind,q,role));economic.append((t,kind,role))
   if debt<=EPS:repaired=0.;lgd=lgp=0.
   if kind not in ('EXPAND','REPAIR') or debt<=EPS:continue
   gross=U+D;floor=min(U,D)-C;best=max(U,D)-C;gp=gross+1.;dp=debt+1.;rem=max(0.,lgd-lgp)
   def qs(k,r,w):return sum(h[2] for h in hist if h[1]==k and h[3]==r and t-h[0]<=w)
   z={'marketId':mid,'t':t,'epochStartKind':kind,'floorBestRatio':floor/(abs(best)+1.),'debtGrossRatio':debt/gp,'absNetGrossRatio':post/gp,'repairProgressFrac':repaired/(repaired+debt+EPS),'latestGenerationDebtGross':lgd/gp,'latestGenerationRemainingGross':rem/gp,'latestGenerationRepairProgress':min(1.,lgp/(lgd+EPS)) if lgd>EPS else 1.,'lastRepairWasTaker':1. if lr=='TAKER' else 0.,'lastExpandWasTaker':1. if le=='TAKER' else 0.}
   for kk,rr,pfx in [('REPAIR','MAKER','repairMaker'),('REPAIR','TAKER','repairTaker'),('EXPAND','MAKER','expandMaker'),('EXPAND','TAKER','expandTaker')]:
    for w in (5000,15000,30000):z[f'{pfx}Qty{w//1000}Gross']=qs(kk,rr,w)/gp
    z[f'{pfx}Qty30Debt']=qs(kk,rr,30000)/dp
   states.append(z)
  j=0
  for st in states:
   while j<len(economic) and economic[j][0]<=st['t']:j+=1
   if j>=len(economic):continue
   _,nk,nr=economic[j]
   if nk!='REPAIR':continue
   st['y']=1 if nr=='TAKER' else 0;rows.append(st)
 mids=sorted(set(r['marketId'] for r in rows));n=len(mids);testset=set(m for i,m in enumerate(mids) if (i+1)/n>.8);rr=[r for r in rows if r['marketId'] in testset];X=np.asarray([[r[c] for c in cols] for r in rr],np.float32);y=np.asarray([r['y'] for r in rr],np.int8);p=model.predict_proba(X);cond=p[:,ai]/np.maximum(EPS,p[:,ai]+p[:,pi]);pred=(cond>=.5).astype(np.int8)
 def metrics(mask):
  yy=y[mask];pp=cond[mask];pr=pred[mask];return {'n':int(len(yy)),'active':int(yy.sum()),'activeRate':float(yy.mean()),'predActive':int(pr.sum()),'predActiveRate':float(pr.mean()),'auc':float(roc_auc_score(yy,pp)),'ap':float(average_precision_score(yy,pp)),'logLoss':float(log_loss(yy,pp,labels=[0,1])),'accuracy':float(accuracy_score(yy,pr))}
 mask_all=np.ones(len(rr),dtype=bool);mask_exp=np.asarray([r['epochStartKind']=='EXPAND' for r in rr],bool);mask_rep=~mask_exp
 out={'version':'TARGET_ETH_V54_CONDITIONAL_REPAIR_ROUTE_AUDIT','teacher':'frozen PARALLEL_ROUTE_NORM; no refit','decision':'conditional pActive/(pActive+pPassive) >= 0.5 natural pairwise majority','TEST20_REPAIR_NEXT':metrics(mask_all),'TEST20_POST_EXPAND_REPAIR_NEXT':metrics(mask_exp),'TEST20_POST_REPAIR_REPAIR_NEXT':metrics(mask_rep),'guards':['same strict-past features','no threshold sweep','next-Repair-only evaluation slice']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
