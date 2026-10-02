from __future__ import annotations
import argparse,sqlite3,json,joblib
from collections import defaultdict,deque,Counter
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import log_loss,accuracy_score,balanced_accuracy_score,f1_score,roc_auc_score,average_precision_score
from sklearn.preprocessing import label_binarize
EPS=1e-9
LABELS=['PASSIVE_REPAIR','ACTIVE_REPAIR','EXPAND_BEFORE_REPAIR']
GEOM=['floorBestRatio','debtGrossRatio','absNetGrossRatio','repairProgressFrac','latestGenerationDebtGross','latestGenerationRemainingGross','latestGenerationRepairProgress']
MEM=GEOM+[
 'repairMakerQty5Gross','repairMakerQty15Gross','repairMakerQty30Gross','repairTakerQty5Gross','repairTakerQty15Gross','repairTakerQty30Gross',
 'expandMakerQty5Gross','expandMakerQty15Gross','expandMakerQty30Gross','expandTakerQty5Gross','expandTakerQty15Gross','expandTakerQty30Gross',
 'repairMakerQty30Debt','repairTakerQty30Debt','expandMakerQty30Debt','expandTakerQty30Debt','lastRepairWasTaker','lastExpandWasTaker'
]
GROUPS={'GEOMETRY_GENERATION':GEOM,'PARALLEL_ROUTE_NORM':MEM}

def metric(y,p):
 pred=np.argmax(p,axis=1);out={'n':int(len(y)),'classCounts':{LABELS[i]:int((y==i).sum()) for i in range(3)},'accuracy':float(accuracy_score(y,pred)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'macroF1':float(f1_score(y,pred,average='macro')),'logLoss':float(log_loss(y,p,labels=[0,1,2])),'predictedClassCounts':{LABELS[i]:int((pred==i).sum()) for i in range(3)}}
 yy=label_binarize(y,classes=[0,1,2])
 for i,l in enumerate(LABELS):
  if yy[:,i].sum()>0 and yy[:,i].sum()<len(y):
   out[l+'_auc']=float(roc_auc_score(yy[:,i],p[:,i]));out[l+'_ap']=float(average_precision_score(yy[:,i],p[:,i]))
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);ap.add_argument('--model-out',required=True);a=ap.parse_args()
 con=sqlite3.connect(a.db);ev=defaultdict(list)
 q="select market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset='ETH' and average_price is not null and shares>0 order by market_id,first_event_ms,parent_id"
 for m,role,side,t,p,qty in con.execute(q):ev[int(m)].append((int(t),str(role).upper(),str(side).upper(),float(p),float(qty)))
 con.close();rows=[]
 for mid,xs in ev.items():
  U=D=C=debt=repaired=0.;latestGenDebt=latestGenPaid=0.;hist=deque();lastRepairRole=None;lastExpandRole=None;states=[];kinds=[]
  for t,role,side,p,qty in xs:
   while hist and t-hist[0][0]>30000:hist.popleft()
   preAbs=abs(U-D);preDebt=debt
   if side=='UP':U+=qty
   else:D+=qty
   C+=p*qty;postAbs=abs(U-D);delta=postAbs-preAbs
   if delta>EPS:
    kind='EXPAND';debt=max(0.,preDebt)+delta;latestGenDebt=delta;latestGenPaid=0.;lastExpandRole=role
   elif delta<-EPS:
    kind='REPAIR';pay=min(max(0.,preDebt),-delta);debt=max(0.,preDebt-pay);repaired+=pay;latestGenPaid=min(latestGenDebt,latestGenPaid+pay);lastRepairRole=role
   else:kind='FLAT'
   if kind in ('EXPAND','REPAIR'):hist.append((t,kind,qty,role))
   if debt<=EPS:
    repaired=0.;latestGenDebt=latestGenPaid=0.
   gross=U+D;floor=min(U,D)-C;best=max(U,D)-C;gp=gross+1.;dp=debt+1.;grem=max(0.,latestGenDebt-latestGenPaid)
   def qs(k,r,w):return sum(h[2] for h in hist if h[1]==k and h[3]==r and t-h[0]<=w)
   if kind in ('EXPAND','REPAIR') and debt>EPS:
    vals={'marketId':mid,'t':t,'epochStartKind':kind,'floorBestRatio':floor/(abs(best)+1.),'debtGrossRatio':debt/gp,'absNetGrossRatio':postAbs/gp,'repairProgressFrac':repaired/(repaired+debt+EPS),'latestGenerationDebtGross':latestGenDebt/gp,'latestGenerationRemainingGross':grem/gp,'latestGenerationRepairProgress':min(1.,latestGenPaid/(latestGenDebt+EPS)) if latestGenDebt>EPS else 1.,'lastRepairWasTaker':1. if lastRepairRole=='TAKER' else 0.,'lastExpandWasTaker':1. if lastExpandRole=='TAKER' else 0.}
    for kk,rr,prefix in [('REPAIR','MAKER','repairMaker'),('REPAIR','TAKER','repairTaker'),('EXPAND','MAKER','expandMaker'),('EXPAND','TAKER','expandTaker')]:
     for w in (5000,15000,30000):vals[f'{prefix}Qty{w//1000}Gross']=qs(kk,rr,w)/gp
     vals[f'{prefix}Qty30Debt']=qs(kk,rr,30000)/dp
    states.append(vals);kinds.append(kind)
  # label each epoch state by the next economic materialization after its timestamp
  economic=[]
  U=D=0.
  for t,role,side,p,qty in xs:
   pre=abs(U-D)
   if side=='UP':U+=qty
   else:D+=qty
   post=abs(U-D);dlt=post-pre
   if dlt>EPS:economic.append((t,'EXPAND',role))
   elif dlt<-EPS:economic.append((t,'REPAIR',role))
  j=0
  for st in states:
   while j<len(economic) and economic[j][0]<=st['t']:j+=1
   if j>=len(economic):continue
   nt,nk,nr=economic[j]
   if nk=='EXPAND':lab='EXPAND_BEFORE_REPAIR'
   else:lab='ACTIVE_REPAIR' if nr=='TAKER' else 'PASSIVE_REPAIR'
   z=dict(st);z['label']=lab;rows.append(z)
 mids=sorted(set(r['marketId'] for r in rows));n=len(mids);split={m:('TRAIN60' if (i+1)/n<=.6 else ('VALID20' if (i+1)/n<=.8 else 'TEST20')) for i,m in enumerate(mids)}
 parts={s:[r for r in rows if split[r['marketId']]==s] for s in ['TRAIN60','VALID20','TEST20']};models={};metrics={};label_idx={l:i for i,l in enumerate(LABELS)}
 for name,cols in GROUPS.items():
  tr=parts['TRAIN60'];X=np.asarray([[r[c] for c in cols] for r in tr],np.float32);y=np.asarray([label_idx[r['label']] for r in tr],np.int8)
  model=HistGradientBoostingClassifier(max_depth=5,learning_rate=.06,max_iter=180,l2_regularization=1.,random_state=54).fit(X,y);models[name]={'model':model,'features':cols,'labels':LABELS};metrics[name]={}
  prior=np.bincount(y,minlength=3).astype(float);prior/=prior.sum()
  for s,rr in parts.items():
   xx=np.asarray([[r[c] for c in cols] for r in rr],np.float32);yy=np.asarray([label_idx[r['label']] for r in rr],np.int8);pp=model.predict_proba(xx);metrics[name][s]=metric(yy,pp);metrics[name][s]['classPriorLogLoss']=float(log_loss(yy,np.tile(prior,(len(yy),1)),labels=[0,1,2]));metrics[name][s]['logLossImprovementVsPrior']=metrics[name][s]['classPriorLogLoss']-metrics[name][s]['logLoss']
  # post-Expand TEST slice is the key portability/architecture domain
  rr=[r for r in parts['TEST20'] if r['epochStartKind']=='EXPAND'];xx=np.asarray([[r[c] for c in cols] for r in rr],np.float32);yy=np.asarray([label_idx[r['label']] for r in rr],np.int8);pp=model.predict_proba(xx);metrics[name]['TEST20_POST_EXPAND']=metric(yy,pp)
 out={'version':'TARGET_ETH_V54_PARALLEL_RESPONSIBILITY_ROUTE_TEACHER','researchOnly':True,'actionAuthority':False,'rows':len(rows),'markets':len(mids),'featureGroups':GROUPS,'labelOrder':LABELS,'splitCounts':{s:len(v) for s,v in parts.items()},'metrics':metrics,'guards':['strict-past features only','future used only as route label','chronological market split','no winner/PnL','no threshold sweep','no wallet-size raw feature']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');joblib.dump({'version':out['version'],'models':models,'labels':LABELS},a.model_out);print(json.dumps(out,indent=2))
if __name__=='__main__':main()
