from __future__ import annotations
import json
from pathlib import Path
from collections import Counter
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
DEV=[ROOT/f'data/research/lan_worker_returns/r4-objv5-dev104-{s}/{s}.json' for s in 'abcd']
VAL=[ROOT/f'data/research/lan_worker_returns/r4-objv5-duration-{s}/{s}.json' for s in 'abc']
H=[5,15,30]
BASE=['secondsLeft','floor','absNet','coverage','absnetRatio','floorPerGross','unresolvedQty','progressRatio','ownerCount','weakResponsibilityCount','dominantResponsibilityCount','events15s','transitions15s','oldestOwnerAge','pendingCancelCount','activeObjectiveCount','sameSideActiveObjectives','oppositeSideActiveObjectives','sameSideResidualQty','oppositeSideResidualQty','sameSideReservedQty','oppositeSideReservedQty','sameSideConfirmedQty','oppositeSideConfirmedQty','currentObjectiveResidualQty','currentObjectiveReservedQty','currentObjectiveConfirmedQty','currentObjectiveResidualRatio','currentObjectiveResponsibilityCount','recentObjectiveOpens5s','recentObjectiveOpens15s','recentParallelObjectiveOpens15s']
DUR=['currentObjectiveAgeS','sameSideOldestObjectiveAgeS','oppositeSideOldestObjectiveAgeS','timeSinceObjectiveProgressS','timeSinceHandoffLikeS']
REL=['p_pair_balance','p_state_shaping','p_multi_objective','p_same_objective','p_different_objective','p_unknown_relation']
MOTIFS=['NONE','WEAK_ECON_ONLY','DOM_ECON_ONLY','BOTH_ECON'];FLAGS=['NEW_WEAK_ROOT','NEW_DOM_ROOT','CANCEL','COMPLETE']
def load(ps):
 reps=[json.loads(p.read_text(encoding='utf-8')) for p in ps]; return [r for z in reps for r in z['rows']],reps
def evs(r,h): return [e for e in r['portfolioEvents30s'] if int(e['dtMs'])<=h*1000]
def classify(r,h):
 es=evs(r,h);w=any(e['eventType'] in {'PARTIAL_FILL','FULL_FILL','TAKER_EXECUTION'} and e.get('sideRelation')=='WEAK' for e in es);d=any(e['eventType'] in {'PARTIAL_FILL','FULL_FILL','TAKER_EXECUTION'} and e.get('sideRelation')=='DOMINANT' for e in es)
 mot='BOTH_ECON' if w and d else 'WEAK_ECON_ONLY' if w else 'DOM_ECON_ONLY' if d else 'NONE'
 fl={'NEW_WEAK_ROOT':int(any(e['eventType']=='RESPONSIBILITY_OPENED' and e.get('sideRelation')=='WEAK' for e in es)),'NEW_DOM_ROOT':int(any(e['eventType']=='RESPONSIBILITY_OPENED' and e.get('sideRelation')=='DOMINANT' for e in es)),'CANCEL':int(any(e['eventType']=='CANCEL_REQUESTED' for e in es)),'COMPLETE':int(any(e['eventType']=='RESPONSIBILITY_COMPLETED' for e in es))};return mot,fl
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
def add_relation_beliefs(rows):
 # Target role/topology teacher: only strict-past shared geometry/memory inputs.
 q=P/'r4_p0b_target_objective_topology_rows_v2.csv';td=pd.read_csv(q)
 shared_t=['seconds_left','floor','absNet','coverage','floor_per_gross','events_15s','transitions_15s','mode_age_s']
 shared_r=['secondsLeft','floor','absNet','coverage','floorPerGross','events15s','transitions15s','oldestOwnerAge']
 X=td[shared_t].fillna(0).to_numpy(float)
 ypb=(td['objective_family'].astype(str)=='PAIR_BALANCE').astype(int).to_numpy(); ymulti=(td['distinct_objective_keys_15s'].fillna(0).to_numpy(float)>1).astype(int)
 role=RandomForestClassifier(n_estimators=180,max_depth=6,min_samples_leaf=20,class_weight='balanced',random_state=551,n_jobs=1).fit(X,ypb)
 multi=RandomForestClassifier(n_estimators=160,max_depth=6,min_samples_leaf=20,class_weight='balanced',random_state=552,n_jobs=1).fit(X,ymulti)
 # MAIN grouping evidence remains low-support; use it only as soft belief and preserve UNKNOWN.
 gd=json.loads((P/'r4_p0b_objective_context_memory_v1.json').read_text(encoding='utf-8'))['rows'];gd=[x for x in gd if 'groundTruth' in x]
 gf=['same_side_active_objectives','opposite_side_active_objectives','same_side_total_residual','opposite_side_total_residual','recent_objective_opens_5s','recent_objective_opens_15s','recent_parallel_opens_15s','parent_objective_age_s','parent_residual','parent_reserved','parent_confirmed','parent_residual_ratio','same_side_oldest_objective_age_s']
 gr=['sameSideActiveObjectives','oppositeSideActiveObjectives','sameSideResidualQty','oppositeSideResidualQty','recentObjectiveOpens5s','recentObjectiveOpens15s','recentParallelObjectiveOpens15s','currentObjectiveAgeS','currentObjectiveResidualQty','currentObjectiveReservedQty','currentObjectiveConfirmedQty','currentObjectiveResidualRatio','sameSideOldestObjectiveAgeS']
 GX=np.array([[float(x.get(f) or 0) for f in gf] for x in gd],float);GY=np.array([1 if x['groundTruth']=='SAME_OBJECTIVE' else 0 for x in gd])
 grp=make_pipeline(StandardScaler(),LogisticRegression(C=.35,class_weight='balanced',random_state=553,max_iter=2000)).fit(GX,GY)
 RX=np.array([[float(r.get(f) or 0) for f in shared_r] for r in rows],float); pb=role.predict_proba(RX)[:,1];mu=multi.predict_proba(RX)[:,1]
 GG=np.array([[float(r.get(f) or 0) for f in gr] for r in rows],float);ps=grp.predict_proba(GG)[:,1]
 for i,r in enumerate(rows):
  cert=abs(float(ps[i])-.5)*2.0; r['p_pair_balance']=float(pb[i]);r['p_state_shaping']=float(1-pb[i]);r['p_multi_objective']=float(mu[i]);r['p_unknown_relation']=float(1-cert);r['p_same_objective']=float(cert if ps[i]>=.5 else 0);r['p_different_objective']=float(cert if ps[i]<.5 else 0)
def run_variant(name,features,dev,val,vreps):
 X=np.array([[float(r.get(f) or 0) for f in features] for r in dev],float);XV=np.array([[float(r.get(f) or 0) for f in features] for r in val],float);q10=np.quantile(X,.1,axis=0);q90=np.quantile(X,.9,axis=0);sc=np.maximum(1e-6,q90-q10);XN=X/sc;XVN=XV/sc;outH={};mac=[];fd=[];ad=[];nma=[]
 for h in H:
  dc=[classify(r,h) for r in dev];vc=[classify(r,h) for r in val];ym=np.array([MOTIFS.index(x[0]) for x in dc]);yv=np.array([MOTIFS.index(x[0]) for x in vc]);clf=RandomForestClassifier(n_estimators=220,max_depth=5,min_samples_leaf=4,class_weight='balanced_subsample',random_state=5600+h,n_jobs=1).fit(X,ym);yp=clf.predict(XV);fp={}
  for flag in FLAGS:
   yy=np.array([x[1][flag] for x in dc]);fp[flag]=np.full(len(val),int(yy[0])) if len(set(yy))<2 else RandomForestClassifier(n_estimators=120,max_depth=4,min_samples_leaf=5,class_weight='balanced',random_state=5700+h+FLAGS.index(flag),n_jobs=1).fit(X,yy).predict(XV)
  per=[]
  for j,r in enumerate(val):
   pf={k:int(fp[k][j]) for k in FLAGS};cand=[i for i,x in enumerate(dc) if MOTIFS[int(yp[j])]==x[0]] or list(range(len(dev)));best=min(cand,key=lambda i:float(np.sum((XVN[j]-XN[i])**2))+.20*sum(abs(pf[k]-dc[i][1][k]) for k in FLAGS));des=[]
   for e in evs(dev[best],h):
    ee=dict(e);rel=ee.get('sideRelation');ee['side']=r['weakSide'] if rel=='WEAK' else ('DOWN' if r['weakSide']=='UP' else 'UP') if rel=='DOMINANT' else ee.get('side');des.append(ee)
   per.append((apply(r,des,h),apply(r,evs(r,h),h),{'floor':float(r['floor']),'absNet':float(r['absNet']),'coverage':float(r['coverage'])}))
  recalls=[]
  for c in range(len(MOTIFS)):
   ix=np.where(yv==c)[0];recalls.append(float(np.mean(yp[ix]==c)) if len(ix) else 0.)
  macro=float(np.mean(recalls));floor_dir=float(np.mean([sign(p['floor']-b['floor'])==sign(a['floor']-b['floor']) for p,a,b in per]));abs_dir=float(np.mean([sign(p['absNet']-b['absNet'])==sign(a['absNet']-b['absNet']) for p,a,b in per]));fn=float(np.mean([abs(p['floor']-a['floor'])/max(1.,abs(a['floor']-b['floor']),abs(b['floor'])) for p,a,b in per]));an=float(np.mean([abs(p['absNet']-a['absNet'])/max(1.,abs(a['absNet']-b['absNet']),abs(b['absNet'])) for p,a,b in per]));cn=float(np.mean([abs(p['coverage']-a['coverage'])/max(.05,abs(a['coverage']-b['coverage']),abs(b['coverage'])) for p,a,b in per]));mac.append(macro);fd.append(floor_dir);ad.append(abs_dir);nma += [fn,an,cn];outH[str(h)]={'roots':len(val),'jointMotifMacroAccuracy':macro,'floorDirectionAgreement':floor_dir,'absNetDirectionAgreement':abs_dir,'floorNMAE':fn,'absNetNMAE':an,'coverageNMAE':cn}
 return {'variant':name,'horizons':outH,'meanJointMotifMacroAccuracy':float(np.mean(mac)),'meanFloorDirectionAgreement':float(np.mean(fd)),'meanAbsNetDirectionAgreement':float(np.mean(ad)),'meanDirectionAgreement':float((np.mean(fd)+np.mean(ad))/2),'meanPortfolioNMAE':float(np.mean(nma))}
def main():
 dev,dreps=load(DEV);val,vreps=load(VAL);add_relation_beliefs(dev);add_relation_beliefs(val)
 variants=[run_variant('RELATION_BELIEF',BASE+REL,dev,val,vreps),run_variant('DURATION_MEMORY',BASE+DUR,dev,val,vreps)]
 variants=sorted(variants,key=lambda z:(-z['meanDirectionAgreement'],z['meanPortfolioNMAE']));winner=variants[0]['variant'];rep={'version':'R4_MANAGEMENT_SIMULATOR_OBJECTIVE_SEMANTICS_V5_FIFTH20_COMPARISON','researchOnly':True,'developmentRoots':len(dev),'diagnosticMarkets':len({int(r['marketId']) for r in val}),'diagnosticRoots':len(val),'variants':variants,'selectionRule':'larger mean floor+absNet direction agreement; tie lower portfolio NMAE','winner':winner,'winnerFrozenForSixth20':True,'relationBeliefCaveat':'SAME/DIFFERENT teacher is low-support; uncertainty is explicitly preserved as p_unknown_relation rather than hard grouping.'};(P/'r4_management_simulator_objective_semantics_v5_fifth20_comparison.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
