from __future__ import annotations
import argparse,json,math
from pathlib import Path
from collections import Counter
import numpy as np
from sklearn.ensemble import RandomForestClassifier,RandomForestRegressor
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,mean_absolute_error

H=[1,3,5]
ACTION_WINDOW_MS=500
STATE=['secondsLeft','floor','absNet','coverage','floorPerGross','unresolvedQty','progressRatio','ownerCount','weakResponsibilityCount','dominantResponsibilityCount','events15s','transitions15s','oldestOwnerAge','pendingCancelCount','requestedPx']
ACTION=['openWeak500','openDom500','cancel500','intentWeak500','intentDom500']
ECON={'PARTIAL_FILL','FULL_FILL','TAKER_EXECUTION'}

def f(r,k):
 try:
  x=float(r.get(k) or 0.0);return x if math.isfinite(x) else 0.0
 except:return 0.0

def action_vec(r):
 z={k:0.0 for k in ACTION}
 for e in r.get('portfolioEvents30s',[]):
  if int(e.get('dtMs') or 0)>ACTION_WINDOW_MS: continue
  rel=str(e.get('sideRelation') or '')
  typ=str(e.get('eventType') or '')
  if typ=='RESPONSIBILITY_OPENED':
   if rel=='WEAK':z['openWeak500']+=1
   elif rel=='DOMINANT':z['openDom500']+=1
  elif typ=='CARRIER_INTENT_CREATED':
   if rel=='WEAK':z['intentWeak500']+=1
   elif rel=='DOMINANT':z['intentDom500']+=1
  elif typ=='CANCEL_REQUESTED': z['cancel500']+=1
 return [z[k] for k in ACTION]

def reconstruct(r):
 gap=max(0.0,f(r,'absNet'));cov=np.clip(f(r,'coverage'),0,0.999999)
 m=(cov*gap)/(2*max(1e-9,1-cov)) if gap>0 else max(0.,f(r,'unresolvedQty'))
 M=m+gap; weak=str(r.get('weakSide') or '')
 up=m if weak=='UP' else M;dn=m if weak=='DOWN' else M;cost=m-f(r,'floor')
 return up,dn,cost

def target(r,h):
 es=[e for e in r.get('portfolioEvents30s',[]) if ACTION_WINDOW_MS<int(e.get('dtMs') or 0)<=h*1000]
 weakFill=domFill=completion=0;up,dn,cost=reconstruct(r)
 for e in es:
  typ=str(e.get('eventType') or '');rel=str(e.get('sideRelation') or '')
  if typ in {'PARTIAL_FILL','FULL_FILL'}:
   if rel=='WEAK':weakFill=1
   elif rel=='DOMINANT':domFill=1
  if typ=='RESPONSIBILITY_COMPLETED':completion=1
  if typ in ECON:
   q=f(e,'qty');px=f(e,'price');side=str(e.get('side') or '')
   if q<=0 or side not in {'UP','DOWN'}:continue
   if side=='UP':up+=q
   else:dn+=q
   cost+=q*px*(1.02 if typ=='TAKER_EXECUTION' else 1.0)
 floor1=min(up,dn)-cost;gap1=abs(up-dn)
 return weakFill,domFill,completion,floor1-f(r,'floor'),gap1-f(r,'absNet')

def load(paths):
 out=[]
 for p in paths:out+=json.loads(Path(p).read_text(encoding='utf-8'))['rows']
 return out

def met_cls(y,p):
 y=np.asarray(y,int);z=(np.asarray(p)>=.5).astype(int)
 return {'n':len(y),'positiveSupport':int(y.sum()),'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ba':float(balanced_accuracy_score(y,z)) if len(set(y))>1 else None}
def delta(a,b):return None if a is None or b is None else float(b-a)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--dev',nargs='+',required=True);ap.add_argument('--val',required=True);ap.add_argument('--out',required=True);args=ap.parse_args()
 dev=load(args.dev);val=load([args.val]); Xs=np.array([[f(r,k) for k in STATE] for r in dev]);Xa=np.array([action_vec(r) for r in dev]);Vs=np.array([[f(r,k) for k in STATE] for r in val]);Va=np.array([action_vec(r) for r in val]);X=np.c_[Xs,Xa];V=np.c_[Vs,Va]
 rep={'version':'R4_ADAPTIVE_SHORT_BRANCH_WORLD_MODEL_V10_PHASEA_OBSERVED_ACTION_RESULT','researchOnly':True,'promotionEvidence':False,'actionAuthority':False,'devRows':len(dev),'valRows':len(val),'actionWindowMs':ACTION_WINDOW_MS,'stateFeatures':STATE,'actionFeatures':ACTION,'horizons':{}}
 for h in H:
  yd=[target(r,h) for r in dev];yv=[target(r,h) for r in val];hr={}
  for j,nm in enumerate(['weakFill','domFill','completion']):
   y=np.array([q[j] for q in yd],int);yy=np.array([q[j] for q in yv],int)
   if len(set(y))<2: hr[nm]={'trainClassSupport':Counter(map(int,y)),'stateOnly':None,'stateAction':None};continue
   m0=RandomForestClassifier(n_estimators=500,max_depth=7,min_samples_leaf=3,class_weight='balanced_subsample',random_state=1000+h*10+j,n_jobs=4).fit(Xs,y)
   m1=RandomForestClassifier(n_estimators=500,max_depth=7,min_samples_leaf=3,class_weight='balanced_subsample',random_state=2000+h*10+j,n_jobs=4).fit(X,y)
   p0=m0.predict_proba(Vs)[:,1];p1=m1.predict_proba(V)[:,1];a=met_cls(yy,p0);b=met_cls(yy,p1);hr[nm]={'stateOnly':a,'stateAction':b,'aucDeltaAction':delta(a['auc'],b['auc']),'baDeltaAction':delta(a['ba'],b['ba'])}
  for j,nm in [(3,'floorDelta'),(4,'absNetDelta')]:
   y=np.array([q[j] for q in yd],float);yy=np.array([q[j] for q in yv],float)
   m0=RandomForestRegressor(n_estimators=500,max_depth=8,min_samples_leaf=3,random_state=3000+h*10+j,n_jobs=4).fit(Xs,y)
   m1=RandomForestRegressor(n_estimators=500,max_depth=8,min_samples_leaf=3,random_state=4000+h*10+j,n_jobs=4).fit(X,y)
   p0=m0.predict(Vs);p1=m1.predict(V);scale=max(1.0,float(np.mean(np.abs(yy))))
   hr[nm]={'stateOnlyMAE':float(mean_absolute_error(yy,p0)),'stateActionMAE':float(mean_absolute_error(yy,p1)),'normalizedStateOnlyMAE':float(mean_absolute_error(yy,p0)/scale),'normalizedStateActionMAE':float(mean_absolute_error(yy,p1)/scale),'maeImprovementAction':float(mean_absolute_error(yy,p0)-mean_absolute_error(yy,p1))}
  rep['horizons'][str(h)]=hr
 Path(args.out).parent.mkdir(parents=True,exist_ok=True);Path(args.out).write_text(json.dumps(rep,indent=2,default=lambda x:dict(x) if isinstance(x,Counter) else str(x)),encoding='utf-8');print(json.dumps(rep,indent=2,default=lambda x:dict(x) if isinstance(x,Counter) else str(x)))
if __name__=='__main__':main()
