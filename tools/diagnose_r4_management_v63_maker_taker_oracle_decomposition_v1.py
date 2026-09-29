from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from sklearn.ensemble import RandomForestClassifier
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
DEV=[ROOT/'data/research/lan_worker_returns/r4-objv5-dev-a/dev_a.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-b/dev_b.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-c/dev_c.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-d/dev_d.json']
VAL=P/'r4_management_v61_formal_cohort20_objective_memory_v1.json';H=[5,15,30]
FEATURES=['secondsLeft','floor','absNet','coverage','absnetRatio','floorPerGross','unresolvedQty','progressRatio','ownerCount','weakResponsibilityCount','dominantResponsibilityCount','events15s','transitions15s','oldestOwnerAge','pendingCancelCount','activeObjectiveCount','sameSideActiveObjectives','oppositeSideActiveObjectives','sameSideResidualQty','oppositeSideResidualQty','sameSideReservedQty','oppositeSideReservedQty','sameSideConfirmedQty','oppositeSideConfirmedQty','currentObjectiveResidualQty','currentObjectiveReservedQty','currentObjectiveConfirmedQty','currentObjectiveResidualRatio','currentObjectiveResponsibilityCount','recentObjectiveOpens5s','recentObjectiveOpens15s','recentParallelObjectiveOpens15s','currentObjectiveAgeS','oldestSameSideObjectiveAgeS','oldestOppositeSideObjectiveAgeS','timeSinceLastObjectiveOpenS','timeSinceLastParallelObjectiveOpenS','timeSinceLastResponsibilityCompletionS','timeSinceLastWeakFillS','timeSinceLastDominantFillS','weakFillQty5s','dominantFillQty5s','weakFillQty15s','dominantFillQty15s','responsibilityCompletions5s','responsibilityCompletions15s','objectiveStateEvents5s','objectiveStateEvents15s','unknownParallelObjectiveCount']
NAMES=['WEAK_EXISTING_MAKER','WEAK_NEW_MAKER','WEAK_TAKER','DOM_EXISTING_MAKER','DOM_NEW_MAKER','DOM_TAKER'];ECON={'PARTIAL_FILL','FULL_FILL','TAKER_EXECUTION'}
def load(ps):
 out=[]
 for p in ps if isinstance(ps,list) else [ps]:out+=json.loads(p.read_text(encoding='utf-8'))['rows']
 return out
def vec(r):return np.array([float(r.get(f) or 0) for f in FEATURES],float)
def evs(r,h):return [e for e in r['portfolioEvents30s'] if int(e['dtMs'])<=h*1000]
def lab6(r,h):
 es=evs(r,h);opens={str(e.get('responsibilityId')):int(e['dtMs']) for e in es if e['eventType']=='RESPONSIBILITY_OPENED' and e.get('responsibilityId')};z=[0]*6
 for e in es:
  if e['eventType'] not in ECON:continue
  rel=e.get('sideRelation');base=0 if rel=='WEAK' else 3 if rel=='DOMINANT' else None
  if base is None:continue
  if e['eventType']=='TAKER_EXECUTION':z[base+2]=1;continue
  rid=e.get('responsibilityId');op=opens.get(str(rid)) if rid else None;z[base+(1 if op is not None and op<int(e['dtMs']) else 0)]=1
 return np.array(z,int)
def apply(r,es,h):
 floor=float(r['floor']);gap=float(r['absNet']);cov=float(r['coverage']);weak=str(r['weakSide']);m=(cov*gap)/(2*max(1e-9,1-cov)) if cov<.999999 else max(0.,float(r.get('unresolvedQty') or 0));m=max(0.,m);M=m+gap;up=m if weak=='UP' else M;dn=m if weak=='DOWN' else M;cost=m-floor
 for e in es:
  if int(e['dtMs'])>h*1000 or e['eventType'] not in ECON:continue
  q=float(e.get('qty') or 0);px=float(e.get('price') or 0);side=str(e.get('side') or '')
  if q<=0 or side not in {'UP','DOWN'}:continue
  if side=='UP':up+=q
  else:dn+=q
  cost+=q*px*(1.02 if e['eventType']=='TAKER_EXECUTION' else 1.0)
 return {'floor':min(up,dn)-cost,'absNet':abs(up-dn)}
def sign(x):return 1 if x>1e-8 else -1 if x<-1e-8 else 0
def score(per):return {'floorDirectionAgreement':float(np.mean([sign(p['floor']-b['floor'])==sign(a['floor']-b['floor']) for p,a,b in per])),'absNetDirectionAgreement':float(np.mean([sign(p['absNet']-b['absNet'])==sign(a['absNet']-b['absNet']) for p,a,b in per]))}
def main():
 dev=load(DEV);val=load(VAL);X=np.vstack([vec(r) for r in dev]);XV=np.vstack([vec(r) for r in val]);q10=np.quantile(X,.1,axis=0);q90=np.quantile(X,.9,axis=0);sc=np.maximum(1e-6,q90-q10);XN=X/sc;XVN=XV/sc;rep={'version':'R4_MANAGEMENT_V6_3_MAKER_TAKER_ORACLE_DECOMPOSITION_V1','researchOnly':True,'promotionEvidence':False,'validationRoots':len(val),'horizons':{}}
 for h in H:
  yd=np.vstack([lab6(r,h) for r in dev]);yv=np.vstack([lab6(r,h) for r in val]);pred=[]
  for k in range(6):
   yy=yd[:,k]
   if len(set(yy))<2:pp=np.full(len(val),int(yy[0]))
   else:pp=RandomForestClassifier(n_estimators=260,max_depth=5,min_samples_leaf=4,class_weight='balanced_subsample',random_state=7600+h*10+k,n_jobs=1).fit(X,yy).predict(XV)
   pred.append(pp)
  pred=np.column_stack(pred)
  variants={}
  for vname in ('PREDICTED_ALL','ORACLE_TAKER','ORACLE_MAKER','ORACLE_ALL'):
   per=[]
   for j,r in enumerate(val):
    target=pred[j].copy()
    if vname in {'ORACLE_TAKER','ORACLE_ALL'}:target[[2,5]]=yv[j,[2,5]]
    if vname in {'ORACLE_MAKER','ORACLE_ALL'}:target[[0,1,3,4]]=yv[j,[0,1,3,4]]
    cand=[i for i,z in enumerate(yd) if np.array_equal(z,target)]
    if not cand:
     maker_idx=[0,1,3,4];taker_idx=[2,5]
     if vname=='ORACLE_TAKER':cand=[i for i,z in enumerate(yd) if np.array_equal(z[taker_idx],target[taker_idx])]
     elif vname=='ORACLE_MAKER':cand=[i for i,z in enumerate(yd) if np.array_equal(z[maker_idx],target[maker_idx])]
    if not cand:cand=list(range(len(dev)))
    best=min(cand,key=lambda i:float(np.sum((XVN[j]-XN[i])**2)));des=[]
    for e in evs(dev[best],h):
     if vname in {'ORACLE_TAKER','ORACLE_ALL'} and e['eventType']=='TAKER_EXECUTION':continue
     ee=dict(e);rel=ee.get('sideRelation')
     if rel=='WEAK':ee['side']=r['weakSide']
     elif rel=='DOMINANT':ee['side']='DOWN' if r['weakSide']=='UP' else 'UP'
     des.append(ee)
    if vname in {'ORACLE_TAKER','ORACLE_ALL'}:des += [dict(e) for e in evs(r,h) if e['eventType']=='TAKER_EXECUTION']
    per.append((apply(r,des,h),apply(r,evs(r,h),h),{'floor':float(r['floor']),'absNet':float(r['absNet'])}))
   variants[vname]=score(per)
  rep['horizons'][str(h)]=variants
 rep['interpretation']='ORACLE_TAKER isolates exogenous/Taker-event prediction error; ORACLE_MAKER isolates responsibility-stream prediction error. Neither is runtime-deployable.';(P/'r4_management_v63_maker_taker_oracle_decomposition_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
