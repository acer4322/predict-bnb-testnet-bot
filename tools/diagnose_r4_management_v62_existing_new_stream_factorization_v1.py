from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
from sklearn.ensemble import RandomForestClassifier
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
DEV=[ROOT/'data/research/lan_worker_returns/r4-objv5-dev-a/dev_a.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-b/dev_b.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-c/dev_c.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-d/dev_d.json']
VALS={
 'ONESHOT20':P/'r4_management_v6_market_disjoint20_objective_memory_v1.json',
 'FORMAL20_CONSUMED':P/'r4_management_v61_formal_cohort20_objective_memory_v1.json',
}
H=[5,15,30]
FEATURES=['secondsLeft','floor','absNet','coverage','absnetRatio','floorPerGross','unresolvedQty','progressRatio','ownerCount','weakResponsibilityCount','dominantResponsibilityCount','events15s','transitions15s','oldestOwnerAge','pendingCancelCount','activeObjectiveCount','sameSideActiveObjectives','oppositeSideActiveObjectives','sameSideResidualQty','oppositeSideResidualQty','sameSideReservedQty','oppositeSideReservedQty','sameSideConfirmedQty','oppositeSideConfirmedQty','currentObjectiveResidualQty','currentObjectiveReservedQty','currentObjectiveConfirmedQty','currentObjectiveResidualRatio','currentObjectiveResponsibilityCount','recentObjectiveOpens5s','recentObjectiveOpens15s','recentParallelObjectiveOpens15s','currentObjectiveAgeS','oldestSameSideObjectiveAgeS','oldestOppositeSideObjectiveAgeS','timeSinceLastObjectiveOpenS','timeSinceLastParallelObjectiveOpenS','timeSinceLastResponsibilityCompletionS','timeSinceLastWeakFillS','timeSinceLastDominantFillS','weakFillQty5s','dominantFillQty5s','weakFillQty15s','dominantFillQty15s','responsibilityCompletions5s','responsibilityCompletions15s','objectiveStateEvents5s','objectiveStateEvents15s','unknownParallelObjectiveCount']
ECON={'PARTIAL_FILL','FULL_FILL','TAKER_EXECUTION'}
def load(ps):
 rows=[]
 for p in ps if isinstance(ps,list) else [ps]: rows.extend(json.loads(p.read_text(encoding='utf-8'))['rows'])
 return rows
def vec(r): return np.array([float(r.get(f) or 0) for f in FEATURES],float)
def evs(r,h): return [e for e in r['portfolioEvents30s'] if int(e['dtMs'])<=h*1000]
def four_labels(r,h):
 es=evs(r,h)
 opens={str(e.get('responsibilityId')):int(e['dtMs']) for e in es if e['eventType']=='RESPONSIBILITY_OPENED' and e.get('responsibilityId')}
 out=[]
 for rel in ('WEAK','DOMINANT'):
  existing=new=0
  for e in es:
   if e['eventType'] not in ECON or e.get('sideRelation')!=rel: continue
   rid=e.get('responsibilityId')
   # Taker has no responsibility lineage; treat as NEW/EXOGENOUS stream event.
   if e['eventType']=='TAKER_EXECUTION' or not rid: new=1; continue
   op=opens.get(str(rid))
   if op is not None and op < int(e['dtMs']): new=1
   else: existing=1
  out += [existing,new]
 return np.array(out,int)
def collapsed(z): return np.array([int(z[0] or z[1]),int(z[2] or z[3])],int)
def balacc(y,p):
 vals=[]
 for c in [0,1]:
  ix=np.where(y==c)[0]
  if len(ix): vals.append(float(np.mean(p[ix]==c)))
 return float(np.mean(vals)) if vals else float('nan')
def apply(r,es,h):
 floor=float(r['floor']);gap=float(r['absNet']);cov=float(r['coverage']);weak=str(r['weakSide']);m=(cov*gap)/(2*max(1e-9,1-cov)) if cov<.999999 else max(0.,float(r.get('unresolvedQty') or 0));m=max(0.,m);M=m+gap;up=m if weak=='UP' else M;dn=m if weak=='DOWN' else M;cost=m-floor
 for e in es:
  if int(e['dtMs'])>h*1000 or e['eventType'] not in ECON: continue
  q=float(e.get('qty') or 0); px=float(e.get('price') or 0); side=str(e.get('side') or '')
  if q<=0 or side not in {'UP','DOWN'}: continue
  if side=='UP': up+=q
  else: dn+=q
  cost+=q*px*(1.02 if e['eventType']=='TAKER_EXECUTION' else 1.0)
 return {'floor':min(up,dn)-cost,'absNet':abs(up-dn),'coverage':2*min(up,dn)/max(1e-9,up+dn)}
def sign(x): return 1 if x>1e-8 else -1 if x<-1e-8 else 0
def fit_predict(dev,val,h):
 X=np.vstack([vec(r) for r in dev]); XV=np.vstack([vec(r) for r in val]); yd=np.vstack([four_labels(r,h) for r in dev]); yv=np.vstack([four_labels(r,h) for r in val])
 pred=[]; probs=[]; names=['WEAK_EXISTING','WEAK_NEW','DOM_EXISTING','DOM_NEW']
 for k,name in enumerate(names):
  yy=yd[:,k]
  if len(set(yy))<2:
   pp=np.full(len(val),int(yy[0])); pr=np.full(len(val),float(yy[0]))
  else:
   clf=RandomForestClassifier(n_estimators=260,max_depth=5,min_samples_leaf=4,class_weight='balanced_subsample',random_state=7200+h*10+k,n_jobs=1).fit(X,yy)
   pp=clf.predict(XV); pr=clf.predict_proba(XV)[:,1]
  pred.append(pp); probs.append(pr)
 pred=np.column_stack(pred); probs=np.column_stack(probs)
 pc=np.vstack([collapsed(z) for z in pred]); yc=np.vstack([collapsed(z) for z in yv])
 # nearest empirical trajectory conditional on the four predicted substream states
 q10=np.quantile(X,.1,axis=0);q90=np.quantile(X,.9,axis=0);sc=np.maximum(1e-6,q90-q10);XN=X/sc;XVN=XV/sc
 per=[]
 for j,r in enumerate(val):
  cand=[i for i,z in enumerate(yd) if np.array_equal(z,pred[j])] or [i for i,z in enumerate(yd) if np.array_equal(collapsed(z),pc[j])] or list(range(len(dev)))
  best=min(cand,key=lambda i:float(np.sum((XVN[j]-XN[i])**2)))
  des=[]
  for e in evs(dev[best],h):
   ee=dict(e); rel=ee.get('sideRelation')
   if rel=='WEAK': ee['side']=r['weakSide']
   elif rel=='DOMINANT': ee['side']='DOWN' if r['weakSide']=='UP' else 'UP'
   des.append(ee)
  per.append((apply(r,des,h),apply(r,evs(r,h),h),{'floor':float(r['floor']),'absNet':float(r['absNet']),'coverage':float(r['coverage'])}))
 return {
  'substreamBA':{names[k]:balacc(yv[:,k],pred[:,k]) for k in range(4)},
  'collapsedWeakBA':balacc(yc[:,0],pc[:,0]),'collapsedDomBA':balacc(yc[:,1],pc[:,1]),
  'actualRates':{names[k]:float(yv[:,k].mean()) for k in range(4)},
  'predRates':{names[k]:float(pred[:,k].mean()) for k in range(4)},
  'floorDirectionAgreement':float(np.mean([sign(p['floor']-b['floor'])==sign(a['floor']-b['floor']) for p,a,b in per])),
  'absNetDirectionAgreement':float(np.mean([sign(p['absNet']-b['absNet'])==sign(a['absNet']-b['absNet']) for p,a,b in per])),
  'floorNMAE':float(np.mean([abs(p['floor']-a['floor'])/max(1.,abs(a['floor']-b['floor']),abs(b['floor'])) for p,a,b in per])),
  'absNetNMAE':float(np.mean([abs(p['absNet']-a['absNet'])/max(1.,abs(a['absNet']-b['absNet']),abs(b['absNet'])) for p,a,b in per])),
  'coverageNMAE':float(np.mean([abs(p['coverage']-a['coverage'])/max(.05,abs(a['coverage']-b['coverage']),abs(b['coverage'])) for p,a,b in per])),
 }
def main():
 dev=load(DEV); rep={'version':'R4_MANAGEMENT_V6_2_EXISTING_NEW_STREAM_FACTORIZATION_DIAGNOSTIC_V1','researchOnly':True,'promotionEvidence':False,'developmentRoots':len(dev),'architecture':'WEAK/DOM streams factorized again into EXISTING responsibility continuation vs NEW responsibility/open/exogenous event','cohorts':{}}
 for name,p in VALS.items():
  val=load(p); hs={str(h):fit_predict(dev,val,h) for h in H}; rep['cohorts'][name]={'roots':len(val),'horizons':hs,'summary':{'meanCollapsedBA':float(np.mean([np.mean([hs[str(h)]['collapsedWeakBA'],hs[str(h)]['collapsedDomBA']]) for h in H])),'meanFloorDirectionAgreement':float(np.mean([hs[str(h)]['floorDirectionAgreement'] for h in H])),'meanAbsNetDirectionAgreement':float(np.mean([hs[str(h)]['absNetDirectionAgreement'] for h in H])),'meanPortfolioNMAE':float(np.mean([np.mean([hs[str(h)]['floorNMAE'],hs[str(h)]['absNetNMAE'],hs[str(h)]['coverageNMAE']]) for h in H]))}}
 rep['guard']='Both validation cohorts are already consumed. This is representation-development evidence only; no promotion and no threshold retuning.'
 out=P/'r4_management_simulator_v62_existing_new_stream_factorization_diagnostic_v1.json'; out.write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
if __name__=='__main__': main()
