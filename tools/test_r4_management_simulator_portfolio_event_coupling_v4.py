from __future__ import annotations
import json
from pathlib import Path
from collections import Counter
import numpy as np
from sklearn.ensemble import RandomForestClassifier
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
DEV=[ROOT/'data/research/lan_worker_returns/r4-objv4-dev-a/dev_a.json',ROOT/'data/research/lan_worker_returns/r4-objv4-dev-b/dev_b.json',ROOT/'data/research/lan_worker_returns/r4-objv4-dev-c/dev_c.json']
VAL=[ROOT/'data/research/lan_worker_returns/r4-objv4-val-fourth20/val.json']
H=[5,15,30];FEATURES=['secondsLeft','floor','absNet','coverage','absnetRatio','floorPerGross','unresolvedQty','progressRatio','ownerCount','weakResponsibilityCount','dominantResponsibilityCount','events15s','transitions15s','oldestOwnerAge','pendingCancelCount','activeObjectiveCount','sameSideActiveObjectives','oppositeSideActiveObjectives','sameSideResidualQty','oppositeSideResidualQty','sameSideReservedQty','oppositeSideReservedQty','sameSideConfirmedQty','oppositeSideConfirmedQty','currentObjectiveResidualQty','currentObjectiveReservedQty','currentObjectiveConfirmedQty','currentObjectiveResidualRatio','currentObjectiveResponsibilityCount','recentObjectiveOpens5s','recentObjectiveOpens15s','recentParallelObjectiveOpens15s'];MOTIFS=['NONE','WEAK_ECON_ONLY','DOM_ECON_ONLY','BOTH_ECON'];FLAGS=['NEW_WEAK_ROOT','NEW_DOM_ROOT','CANCEL','COMPLETE']
def load(ps):
 reps=[json.loads(p.read_text(encoding='utf-8')) for p in ps];return [r for z in reps for r in z['rows']],reps
def vec(r):return np.array([float(r.get(f) or 0) for f in FEATURES],float)
def evs(r,h):return [e for e in r['portfolioEvents30s'] if int(e['dtMs'])<=h*1000]
def classify(r,h):
 es=evs(r,h);w=any(e['eventType'] in {'PARTIAL_FILL','FULL_FILL','TAKER_EXECUTION'} and e.get('sideRelation')=='WEAK' for e in es);d=any(e['eventType'] in {'PARTIAL_FILL','FULL_FILL','TAKER_EXECUTION'} and e.get('sideRelation')=='DOMINANT' for e in es);mot='BOTH_ECON' if w and d else 'WEAK_ECON_ONLY' if w else 'DOM_ECON_ONLY' if d else 'NONE';fl={'NEW_WEAK_ROOT':int(any(e['eventType']=='RESPONSIBILITY_OPENED' and e.get('sideRelation')=='WEAK' for e in es)),'NEW_DOM_ROOT':int(any(e['eventType']=='RESPONSIBILITY_OPENED' and e.get('sideRelation')=='DOMINANT' for e in es)),'CANCEL':int(any(e['eventType']=='CANCEL_REQUESTED' for e in es)),'COMPLETE':int(any(e['eventType']=='RESPONSIBILITY_COMPLETED' for e in es))};return mot,fl
def apply(r,es,h):
 floor=float(r['floor']);gap=float(r['absNet']);cov=float(r['coverage']);weak=str(r['weakSide']);m=(cov*gap)/(2*max(1e-9,1-cov)) if cov<.999999 else max(0.,float(r.get('unresolvedQty') or 0));m=max(0.,m);M=m+gap;up=m if weak=='UP' else M;dn=m if weak=='DOWN' else M;cost=m-floor
 for e in es:
  if int(e['dtMs'])>h*1000 or e['eventType'] not in {'PARTIAL_FILL','FULL_FILL','TAKER_EXECUTION'}:continue
  q=float(e.get('qty') or 0);px=float(e.get('price') or 0);side=str(e.get('side') or '')
  if q<=0 or side not in {'UP','DOWN'}:continue
  if side=='UP':up+=q
  else:dn+=q
  cost+=q*px*(1.02 if e['eventType']=='TAKER_EXECUTION' else 1.0)
 return {'floor':min(up,dn)-cost,'absNet':abs(up-dn),'coverage':2*min(up,dn)/max(1e-9,up+dn)}
def sign(x):return 1 if x>1e-8 else -1 if x<-1e-8 else 0
def main():
 dev,dreps=load(DEV);val,vreps=load(VAL);X=np.vstack([vec(r) for r in dev]);XV=np.vstack([vec(r) for r in val]);q10=np.quantile(X,.1,axis=0);q90=np.quantile(X,.9,axis=0);sc=np.maximum(1e-6,q90-q10);XN=X/sc;XVN=XV/sc;outH={};mac=[];fd=[];ad=[];nma=[]
 for h in H:
  devcls=[classify(r,h) for r in dev];valcls=[classify(r,h) for r in val];ym=np.array([MOTIFS.index(x[0]) for x in devcls]);yv=np.array([MOTIFS.index(x[0]) for x in valcls]);clf=RandomForestClassifier(n_estimators=220,max_depth=5,min_samples_leaf=4,class_weight='balanced_subsample',random_state=4317+h,n_jobs=1).fit(X,ym);yp=clf.predict(XV);flagpred={}
  for flag in FLAGS:
   yy=np.array([x[1][flag] for x in devcls]);
   if len(set(yy))<2:flagpred[flag]=np.full(len(val),int(yy[0]))
   else:flagpred[flag]=RandomForestClassifier(n_estimators=120,max_depth=4,min_samples_leaf=5,class_weight='balanced',random_state=900+h+FLAGS.index(flag),n_jobs=1).fit(X,yy).predict(XV)
  per=[]
  for j,r in enumerate(val):
   pf={k:int(flagpred[k][j]) for k in FLAGS};cand=[i for i,x in enumerate(devcls) if MOTIFS[int(yp[j])]==x[0]] or list(range(len(dev)));best=min(cand,key=lambda i:float(np.sum((XVN[j]-XN[i])**2))+.20*sum(abs(pf[k]-devcls[i][1][k]) for k in FLAGS));des=[]
   for e in evs(dev[best],h):
    ee=dict(e);rel=ee.get('sideRelation');
    if rel=='WEAK':ee['side']=r['weakSide']
    elif rel=='DOMINANT':ee['side']='DOWN' if r['weakSide']=='UP' else 'UP'
    des.append(ee)
   per.append((apply(r,des,h),apply(r,evs(r,h),h),{'floor':float(r['floor']),'absNet':float(r['absNet']),'coverage':float(r['coverage'])}))
  recalls=[]
  for c in range(len(MOTIFS)):
   ix=np.where(yv==c)[0];recalls.append(float(np.mean(yp[ix]==c)) if len(ix) else 0.0)
  macro=float(np.mean(recalls));mac.append(macro);floor_dir=float(np.mean([sign(p['floor']-b['floor'])==sign(a['floor']-b['floor']) for p,a,b in per]));abs_dir=float(np.mean([sign(p['absNet']-b['absNet'])==sign(a['absNet']-b['absNet']) for p,a,b in per]));fd.append(floor_dir);ad.append(abs_dir);fn=float(np.mean([abs(p['floor']-a['floor'])/max(1.,abs(a['floor']-b['floor']),abs(b['floor'])) for p,a,b in per]));an=float(np.mean([abs(p['absNet']-a['absNet'])/max(1.,abs(a['absNet']-b['absNet']),abs(b['absNet'])) for p,a,b in per]));cn=float(np.mean([abs(p['coverage']-a['coverage'])/max(.05,abs(a['coverage']-b['coverage']),abs(b['coverage'])) for p,a,b in per]));nma += [fn,an,cn];outH[str(h)]={'roots':len(val),'jointMotifMacroAccuracy':macro,'motifActual':dict(Counter(MOTIFS[i] for i in yv)),'motifPredicted':dict(Counter(MOTIFS[i] for i in yp)),'floorDirectionAgreement':floor_dir,'absNetDirectionAgreement':abs_dir,'floorNMAE':fn,'absNetNMAE':an,'coverageNMAE':cn}
 markets={int(r['marketId']) for r in val};struct={'validationMarkets':len(markets),'validationRoots':len(val),'executionCoreExactAll':all(x.get('executionCoreExact') for rep in vreps for x in rep['markets']),'duplicateExecutionCredit':sum(int(rep.get('duplicateExecutionCredit') or 0) for rep in vreps)};mm=float(np.mean(mac));mf=float(np.mean(fd));ma=float(np.mean(ad));mn=float(np.mean(nma));checks={'validationMarkets':len(markets)>=16,'validationRoots':len(val)>=20,'executionCoreExactAll':struct['executionCoreExactAll'],'duplicateExecutionCredit':struct['duplicateExecutionCredit']==0,'jointMotifMacroAccuracy':mm>=.45,'floorDirectionAgreement':mf>=.60,'absNetDirectionAgreement':ma>=.60,'meanPortfolioNMAE':mn<=.45};rep={'version':'R4_MANAGEMENT_SIMULATOR_PORTFOLIO_EVENT_COUPLING_V4','researchOnly':True,'developmentRoots':len(dev),'validationMarkets':len(markets),'validationRoots':len(val),'horizons':outH,'summary':{'meanJointMotifMacroAccuracy':mm,'meanFloorDirectionAgreement':mf,'meanAbsNetDirectionAgreement':ma,'meanPortfolioNMAE':mn,'checks':checks,'gatePass':all(checks.values())},'structural':struct,'interpretation':'Preregistered V4 explicit objective-state joint motif + topology flags + donor primitive template. Objective state is strict-past provenance-derived; deterministic economics; untouched validation.'};(P/'r4_management_simulator_portfolio_event_coupling_v4.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
