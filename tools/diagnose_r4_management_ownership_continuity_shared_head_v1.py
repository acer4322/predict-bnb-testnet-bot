from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
DEV=[ROOT/'data/research/lan_worker_returns/r4-objv5-dev-a/dev_a.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-b/dev_b.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-c/dev_c.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-d/dev_d.json']
VALS={'ONESHOT20':P/'r4_management_v6_market_disjoint20_objective_memory_v1.json','FORMAL20_CONSUMED':P/'r4_management_v61_formal_cohort20_objective_memory_v1.json'};H=[5,15,30]
ECON={'PARTIAL_FILL','FULL_FILL'}
FN=['secondsLeft','floor','absNet','coverage','streamIsDominant','responsibilityCount','activeObjectives','residualQty','reservedQty','confirmedQty','oldestObjectiveAgeS','timeSinceLastFillS','fillQty5s','fillQty15s','objectiveStateEvents5s','objectiveStateEvents15s']
def load(ps):
 out=[]
 for p in ps if isinstance(ps,list) else [ps]:out+=json.loads(p.read_text(encoding='utf-8'))['rows']
 return out
def stream_row(r,dom=False):
 if not dom:
  return [float(r.get('secondsLeft') or 0),float(r.get('floor') or 0),float(r.get('absNet') or 0),float(r.get('coverage') or 0),0.,float(r.get('weakResponsibilityCount') or 0),float(r.get('sameSideActiveObjectives') or 0),float(r.get('sameSideResidualQty') or 0),float(r.get('sameSideReservedQty') or 0),float(r.get('sameSideConfirmedQty') or 0),float(r.get('oldestSameSideObjectiveAgeS') or 0),float(r.get('timeSinceLastWeakFillS') or 999),float(r.get('weakFillQty5s') or 0),float(r.get('weakFillQty15s') or 0),float(r.get('objectiveStateEvents5s') or 0),float(r.get('objectiveStateEvents15s') or 0)]
 return [float(r.get('secondsLeft') or 0),float(r.get('floor') or 0),float(r.get('absNet') or 0),float(r.get('coverage') or 0),1.,float(r.get('dominantResponsibilityCount') or 0),float(r.get('oppositeSideActiveObjectives') or 0),float(r.get('oppositeSideResidualQty') or 0),float(r.get('oppositeSideReservedQty') or 0),float(r.get('oppositeSideConfirmedQty') or 0),float(r.get('oldestOppositeSideObjectiveAgeS') or 0),float(r.get('timeSinceLastDominantFillS') or 999),float(r.get('dominantFillQty5s') or 0),float(r.get('dominantFillQty15s') or 0),float(r.get('objectiveStateEvents5s') or 0),float(r.get('objectiveStateEvents15s') or 0)]
def label(r,h,dom=False):
 rel='DOMINANT' if dom else 'WEAK';es=[e for e in r['portfolioEvents30s'] if int(e['dtMs'])<=h*1000];opens={str(e.get('responsibilityId')):int(e['dtMs']) for e in es if e['eventType']=='RESPONSIBILITY_OPENED' and e.get('responsibilityId')}
 for e in es:
  if e['eventType'] not in ECON or e.get('sideRelation')!=rel:continue
  rid=e.get('responsibilityId');op=opens.get(str(rid)) if rid else None
  if op is None or op>=int(e['dtMs']):return 1
 return 0
def bal(y,p):
 vals=[]
 for c in (0,1):
  ix=np.where(y==c)[0]
  if len(ix):vals.append(float(np.mean(p[ix]==c)))
 return float(np.mean(vals)) if vals else float('nan')
def main():
 dev=load(DEV);rep={'version':'R4_MANAGEMENT_OWNERSHIP_CONTINUITY_SHARED_HEAD_V1_DIAGNOSTIC','researchOnly':True,'promotionEvidence':False,'features':FN,'trainingRoots':len(dev),'cohorts':{}}
 for h in H:
  X=np.asarray([stream_row(r,d) for r in dev for d in (False,True)],float);y=np.asarray([label(r,h,d) for r in dev for d in (False,True)],int)
  models={'LOGIT':make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),LogisticRegression(C=.5,class_weight='balanced',solver='liblinear',random_state=8100+h)),'RF':RandomForestClassifier(n_estimators=260,max_depth=5,min_samples_leaf=4,class_weight='balanced_subsample',random_state=8200+h,n_jobs=1)}
  for m in models.values():m.fit(X,y)
  for cname,pth in VALS.items():
   val=load(pth);c=rep['cohorts'].setdefault(cname,{'roots':len(val),'horizons':{}});out={}
   for mn,m in models.items():
    XV=np.asarray([stream_row(r,d) for r in val for d in (False,True)],float);yv=np.asarray([label(r,h,d) for r in val for d in (False,True)],int);pv=m.predict(XV);yw=yv[0::2];yd=yv[1::2];pw=pv[0::2];pd=pv[1::2]
    out[mn]={'sharedBA':bal(yv,pv),'weakBA':bal(yw,pw),'domBA':bal(yd,pd),'positiveRate':float(yv.mean()),'weakPositiveRate':float(yw.mean()),'domPositiveRate':float(yd.mean()),'predPositiveRate':float(pv.mean())}
   c['horizons'][str(h)]=out
 for cname,c in rep['cohorts'].items():
  c['summary']={mn:float(np.mean([c['horizons'][str(h)][mn]['sharedBA'] for h in H])) for mn in ('LOGIT','RF')}
 rep['guard']='Consumed cohorts only. Shared stream-relative head tests ownership-continuity representation; no promotion or threshold tuning.';(P/'r4_management_ownership_continuity_shared_head_v1_diagnostic.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
