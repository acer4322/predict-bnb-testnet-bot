from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from sklearn.ensemble import RandomForestClassifier
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
DEV=[ROOT/'data/research/lan_worker_returns/r4-objv5-dev-a/dev_a.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-b/dev_b.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-c/dev_c.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-d/dev_d.json']
VAL=P/'r4_management_v61_formal_cohort20_objective_memory_v1.json';CONTRACT=P/'r4_management_simulator_v61_formal_validation_contract.json';H=[5,15,30]
FEATURES=['secondsLeft','floor','absNet','coverage','absnetRatio','floorPerGross','unresolvedQty','progressRatio','ownerCount','weakResponsibilityCount','dominantResponsibilityCount','events15s','transitions15s','oldestOwnerAge','pendingCancelCount','activeObjectiveCount','sameSideActiveObjectives','oppositeSideActiveObjectives','sameSideResidualQty','oppositeSideResidualQty','sameSideReservedQty','oppositeSideReservedQty','sameSideConfirmedQty','oppositeSideConfirmedQty','currentObjectiveResidualQty','currentObjectiveReservedQty','currentObjectiveConfirmedQty','currentObjectiveResidualRatio','currentObjectiveResponsibilityCount','recentObjectiveOpens5s','recentObjectiveOpens15s','recentParallelObjectiveOpens15s','currentObjectiveAgeS','oldestSameSideObjectiveAgeS','oldestOppositeSideObjectiveAgeS','timeSinceLastObjectiveOpenS','timeSinceLastParallelObjectiveOpenS','timeSinceLastResponsibilityCompletionS','timeSinceLastWeakFillS','timeSinceLastDominantFillS','weakFillQty5s','dominantFillQty5s','weakFillQty15s','dominantFillQty15s','responsibilityCompletions5s','responsibilityCompletions15s','objectiveStateEvents5s','objectiveStateEvents15s','unknownParallelObjectiveCount']
def load(ps):return [r for p in ps for r in json.loads(p.read_text(encoding='utf-8'))['rows']]
def vec(r):return np.array([float(r.get(f) or 0) for f in FEATURES],float)
def evs(r,h):return [e for e in r['portfolioEvents30s'] if int(e['dtMs'])<=h*1000]
def labels(r,h):
 es=evs(r,h); econ={'PARTIAL_FILL','FULL_FILL','TAKER_EXECUTION'}
 return int(any(e['eventType'] in econ and e.get('sideRelation')=='WEAK' for e in es)),int(any(e['eventType'] in econ and e.get('sideRelation')=='DOMINANT' for e in es))
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
def bal(y,p):
 vals=[]
 for c in (0,1):
  ix=np.where(y==c)[0]; vals.append(float(np.mean(p[ix]==c)) if len(ix) else 0.)
 return float(np.mean(vals))
def main():
 contract=json.loads(CONTRACT.read_text(encoding='utf-8'));dev=load(DEV);vr=json.loads(VAL.read_text(encoding='utf-8'));val=vr['rows'];X=np.vstack([vec(r) for r in dev]);XV=np.vstack([vec(r) for r in val]);q10=np.quantile(X,.1,axis=0);q90=np.quantile(X,.9,axis=0);sc=np.maximum(1e-6,q90-q10);XN=X/sc;XVN=XV/sc;out={};bas=[];fds=[];ads=[];nmas=[]
 for h in H:
  yd=np.array([labels(r,h) for r in dev]);yv=np.array([labels(r,h) for r in val]);pred=[]
  for k in range(2):
   clf=RandomForestClassifier(n_estimators=260,max_depth=5,min_samples_leaf=4,class_weight='balanced_subsample',random_state=6000+h*10+k,n_jobs=1).fit(X,yd[:,k]);pred.append(clf.predict(XV))
  pred=np.column_stack(pred); wb=bal(yv[:,0],pred[:,0]);db=bal(yv[:,1],pred[:,1]);bas += [wb,db];per=[]
  for j,r in enumerate(val):
   cand=[i for i,z in enumerate(yd) if np.array_equal(z,pred[j])] or list(range(len(dev)));best=min(cand,key=lambda i:float(np.sum((XVN[j]-XN[i])**2)));des=[]
   for e in evs(dev[best],h):
    ee=dict(e);rel=ee.get('sideRelation')
    if rel=='WEAK':ee['side']=r['weakSide']
    elif rel=='DOMINANT':ee['side']='DOWN' if r['weakSide']=='UP' else 'UP'
    des.append(ee)
   per.append((apply(r,des,h),apply(r,evs(r,h),h),{'floor':float(r['floor']),'absNet':float(r['absNet']),'coverage':float(r['coverage'])}))
  fd=float(np.mean([sign(p['floor']-b['floor'])==sign(a['floor']-b['floor']) for p,a,b in per]));ad=float(np.mean([sign(p['absNet']-b['absNet'])==sign(a['absNet']-b['absNet']) for p,a,b in per]));fds.append(fd);ads.append(ad)
  fn=float(np.mean([abs(p['floor']-a['floor'])/max(1.,abs(a['floor']-b['floor']),abs(b['floor'])) for p,a,b in per]));an=float(np.mean([abs(p['absNet']-a['absNet'])/max(1.,abs(a['absNet']-b['absNet']),abs(b['absNet'])) for p,a,b in per]));cn=float(np.mean([abs(p['coverage']-a['coverage'])/max(.05,abs(a['coverage']-b['coverage']),abs(b['coverage'])) for p,a,b in per]));nmas += [fn,an,cn]
  out[str(h)]={'weakBalancedAccuracy':wb,'domBalancedAccuracy':db,'weakActualRate':float(yv[:,0].mean()),'domActualRate':float(yv[:,1].mean()),'floorDirectionAgreement':fd,'absNetDirectionAgreement':ad,'floorNMAE':fn,'absNetNMAE':an,'coverageNMAE':cn}
 s={'meanBinaryBalancedAccuracy':float(np.mean(bas)),'meanFloorDirectionAgreement':float(np.mean(fds)),'meanAbsNetDirectionAgreement':float(np.mean(ads)),'meanPortfolioNMAE':float(np.mean(nmas))};g=contract['primaryGates'];checks={'meanBinaryBalancedAccuracy':s['meanBinaryBalancedAccuracy']>=g['meanBinaryBalancedAccuracy_min'],'meanFloorDirectionAgreement':s['meanFloorDirectionAgreement']>=g['meanFloorDirectionAgreement_min'],'meanAbsNetDirectionAgreement':s['meanAbsNetDirectionAgreement']>=g['meanAbsNetDirectionAgreement_min'],'meanPortfolioNMAE':s['meanPortfolioNMAE']<=g['meanPortfolioNMAE_max'],'executionCoreExact':all(bool(x.get('executionCoreExact')) for x in vr['markets']),'duplicateExecutionCredit':int(vr.get('duplicateExecutionCredit') or 0)<=g['duplicateExecutionCredit_max']};rep={'version':'R4_MANAGEMENT_SIMULATOR_V6_1_FORMAL20_SCORE_V1','researchOnly':True,'actionAuthority':False,'contractStatusBeforeScoring':contract['status'],'validationMarkets':len(vr['markets']),'validationRoots':len(val),'horizons':out,'summary':s,'checks':checks,'formalGatePassed':all(checks.values()),'guards':contract['guards']};(P/'r4_management_simulator_v61_formal20_score_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
