from __future__ import annotations
import argparse,json,math
from pathlib import Path
import numpy as np
from sklearn.ensemble import RandomForestClassifier,RandomForestRegressor
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,mean_absolute_error

H=[3,5]
ACTION_WINDOW_MS=500
STATE=['secondsLeft','floor','absNet','coverage','floorPerGross','unresolvedQty','progressRatio','ownerCount','weakResponsibilityCount','dominantResponsibilityCount','events15s','transitions15s','oldestOwnerAge','pendingCancelCount','requestedPx']
ACTION=['openWeak500','openDom500','cancel500','intentWeak500','intentDom500']

def f(r,k):
 try:
  x=float(r.get(k) or 0.0);return x if math.isfinite(x) else 0.0
 except:return 0.0

def avec(r):
 z={k:0.0 for k in ACTION}
 for e in r.get('portfolioEvents30s',[]):
  if int(e.get('dtMs') or 0)>ACTION_WINDOW_MS:continue
  typ=str(e.get('eventType') or ''); rel=str(e.get('sideRelation') or '')
  if typ=='RESPONSIBILITY_OPENED':
   if rel=='WEAK':z['openWeak500']+=1
   elif rel=='DOMINANT':z['openDom500']+=1
  elif typ=='CARRIER_INTENT_CREATED':
   if rel=='WEAK':z['intentWeak500']+=1
   elif rel=='DOMINANT':z['intentDom500']+=1
  elif typ=='CANCEL_REQUESTED':z['cancel500']+=1
 return [z[k] for k in ACTION]

def targ(r,h):
 wq=dq=0.0;comp=0
 for e in r.get('portfolioEvents30s',[]):
  dt=int(e.get('dtMs') or 0)
  if not (ACTION_WINDOW_MS<dt<=h*1000):continue
  typ=str(e.get('eventType') or '');rel=str(e.get('sideRelation') or '')
  if typ in {'PARTIAL_FILL','FULL_FILL'}:
   q=max(0.0,f(e,'qty'))
   if rel=='WEAK':wq+=q
   elif rel=='DOMINANT':dq+=q
  if typ=='RESPONSIBILITY_COMPLETED':comp=1
 return wq,dq,comp

def load(ps):
 out=[]
 for p in ps:out+=json.loads(Path(p).read_text(encoding='utf-8'))['rows']
 return out

def cls(y,p):
 y=np.asarray(y,int);z=(np.asarray(p)>=.5).astype(int)
 return {'n':int(len(y)),'positiveSupport':int(y.sum()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ba':float(balanced_accuracy_score(y,z)) if len(np.unique(y))>1 else None}

def model_qty(X,V,y,seed):
 y=np.asarray(y,float); binary=(y>0).astype(int)
 if len(np.unique(binary))<2:return None
 c=RandomForestClassifier(n_estimators=600,max_depth=7,min_samples_leaf=3,class_weight='balanced_subsample',random_state=seed,n_jobs=4).fit(X,binary)
 pp=c.predict_proba(V)[:,1]
 pos=np.where(y>0)[0]
 if len(pos)>=8:
  r=RandomForestRegressor(n_estimators=600,max_depth=7,min_samples_leaf=2,random_state=seed+500,n_jobs=4).fit(X[pos],y[pos])
  cq=np.maximum(0.,r.predict(V))
 else:cq=np.full(len(V),float(np.mean(y[pos])) if len(pos) else 0.)
 return pp,cq,pp*cq

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--dev',nargs='+',required=True);ap.add_argument('--val',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
 dev=load(a.dev);val=load([a.val]);Xs=np.asarray([[f(r,k) for k in STATE] for r in dev]);Xa=np.asarray([avec(r) for r in dev]);Vs=np.asarray([[f(r,k) for k in STATE] for r in val]);Va=np.asarray([avec(r) for r in val]);X=np.c_[Xs,Xa];V=np.c_[Vs,Va]
 rep={'version':'R4_ADAPTIVE_EXECUTION_WORLD_V10_1_RESULT','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,'devRows':len(dev),'valRows':len(val),'principle':'learn stochastic execution primitives only; portfolio economics remain deterministic ledger','horizons':{}}
 for h in H:
  yd=np.asarray([targ(r,h) for r in dev],float);yv=np.asarray([targ(r,h) for r in val],float);q={}
  for j,nm in enumerate(['weakMakerFillQty','domMakerFillQty']):
   m0=model_qty(Xs,Vs,yd[:,j],1000+h*10+j);m1=model_qty(X,V,yd[:,j],2000+h*10+j)
   if m0 is None or m1 is None:q[nm]={'insufficientTrainClasses':True};continue
   p0,c0,e0=m0;p1,c1,e1=m1; yy=yv[:,j];yb=(yy>0).astype(int)
   q[nm]={'eventStateOnly':cls(yb,p0),'eventStateAction':cls(yb,p1),'eventAucDelta':None if len(np.unique(yb))<2 else float(roc_auc_score(yb,p1)-roc_auc_score(yb,p0)),'eventBADelta':None if len(np.unique(yb))<2 else float(balanced_accuracy_score(yb,(p1>=.5).astype(int))-balanced_accuracy_score(yb,(p0>=.5).astype(int))),'expectedQtyStateOnlyMAE':float(mean_absolute_error(yy,e0)),'expectedQtyStateActionMAE':float(mean_absolute_error(yy,e1)),'expectedQtyMaeImprovement':float(mean_absolute_error(yy,e0)-mean_absolute_error(yy,e1)),'actualPositiveQtyMean':float(np.mean(yy[yy>0])) if np.any(yy>0) else None}
  yc=yd[:,2].astype(int);yy=yv[:,2].astype(int)
  if len(np.unique(yc))>1:
   c0=RandomForestClassifier(n_estimators=600,max_depth=7,min_samples_leaf=3,class_weight='balanced_subsample',random_state=3100+h,n_jobs=4).fit(Xs,yc);c1=RandomForestClassifier(n_estimators=600,max_depth=7,min_samples_leaf=3,class_weight='balanced_subsample',random_state=4100+h,n_jobs=4).fit(X,yc);p0=c0.predict_proba(Vs)[:,1];p1=c1.predict_proba(V)[:,1];q['completion']={'stateOnly':cls(yy,p0),'stateAction':cls(yy,p1),'aucDelta':None if len(np.unique(yy))<2 else float(roc_auc_score(yy,p1)-roc_auc_score(yy,p0)),'baDelta':None if len(np.unique(yy))<2 else float(balanced_accuracy_score(yy,(p1>=.5).astype(int))-balanced_accuracy_score(yy,(p0>=.5).astype(int)))}
  rep['horizons'][str(h)]=q
 Path(a.out).parent.mkdir(parents=True,exist_ok=True);Path(a.out).write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
