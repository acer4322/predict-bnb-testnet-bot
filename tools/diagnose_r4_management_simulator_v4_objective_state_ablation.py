from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from sklearn.ensemble import RandomForestClassifier
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
DEV=[ROOT/'data/research/lan_worker_returns/r4-objv4-dev-a/dev_a.json',ROOT/'data/research/lan_worker_returns/r4-objv4-dev-b/dev_b.json',ROOT/'data/research/lan_worker_returns/r4-objv4-dev-c/dev_c.json'];VAL=[ROOT/'data/research/lan_worker_returns/r4-objv4-val-a/val_a.json',ROOT/'data/research/lan_worker_returns/r4-objv4-val-b/val_b.json']
BASE=['secondsLeft','floor','absNet','coverage','absnetRatio','floorPerGross','unresolvedQty','progressRatio','ownerCount','weakResponsibilityCount','dominantResponsibilityCount','events15s','transitions15s','oldestOwnerAge','pendingCancelCount'];OBJ=['activeObjectiveCount','sameSideActiveObjectives','oppositeSideActiveObjectives','sameSideResidualQty','oppositeSideResidualQty','sameSideReservedQty','oppositeSideReservedQty','sameSideConfirmedQty','oppositeSideConfirmedQty','currentObjectiveResidualQty','currentObjectiveReservedQty','currentObjectiveConfirmedQty','currentObjectiveResidualRatio','currentObjectiveResponsibilityCount','recentObjectiveOpens5s','recentObjectiveOpens15s','recentParallelObjectiveOpens15s'];M=['NONE','WEAK_ECON_ONLY','DOM_ECON_ONLY','BOTH_ECON']
def load(ps):return [r for p in ps for r in json.loads(p.read_text())['rows']]
def cls(r,h):
 es=[e for e in r['portfolioEvents30s'] if int(e['dtMs'])<=h*1000];w=any(e['eventType'] in {'PARTIAL_FILL','FULL_FILL','TAKER_EXECUTION'} and e.get('sideRelation')=='WEAK' for e in es);d=any(e['eventType'] in {'PARTIAL_FILL','FULL_FILL','TAKER_EXECUTION'} and e.get('sideRelation')=='DOMINANT' for e in es);return M.index('BOTH_ECON' if w and d else 'WEAK_ECON_ONLY' if w else 'DOM_ECON_ONLY' if d else 'NONE')
def macro(y,p):
 rr=[]
 for c in range(4):
  ix=np.where(y==c)[0];rr.append(float(np.mean(p[ix]==c)) if len(ix) else 0.)
 return float(np.mean(rr)),rr
def main():
 d=load(DEV);v=load(VAL);out={}
 for h in [5,15,30]:
  y=np.array([cls(r,h) for r in d]);yv=np.array([cls(r,h) for r in v]);z={}
  for name,fs in [('base',BASE),('objective_only',OBJ),('base_plus_objective',BASE+OBJ)]:
   X=np.array([[float(r.get(f) or 0) for f in fs] for r in d]);XV=np.array([[float(r.get(f) or 0) for f in fs] for r in v]);m=RandomForestClassifier(n_estimators=300,max_depth=5,min_samples_leaf=4,class_weight='balanced_subsample',random_state=5500+h,n_jobs=1).fit(X,y);p=m.predict(XV);ma,rec=macro(yv,p);imp=sorted(zip(fs,m.feature_importances_),key=lambda x:x[1],reverse=True)[:8];z[name]={'macroRecall':ma,'perClassRecall':dict(zip(M,rec)),'topImportance':[{'feature':a,'importance':float(b)} for a,b in imp]}
  out[str(h)]=z
 rep={'version':'R4_MANAGEMENT_SIMULATOR_V4_OBJECTIVE_STATE_ABLATION','researchOnly':True,'developmentRoots':len(d),'validationRoots':len(v),'horizons':out,'interpretation':'Post-gate localization only; validation is now development-exposed. Determines whether explicit objective occupancy adds signal before choosing the next preregistered state extension.'};(P/'r4_management_simulator_v4_objective_state_ablation.json').write_text(json.dumps(rep,indent=2));print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
