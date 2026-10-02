from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from sklearn.ensemble import RandomForestClassifier
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
DEV=[ROOT/'data/research/lan_worker_returns/r4-objv5-dev-a/dev_a.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-b/dev_b.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-c/dev_c.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-d/dev_d.json']
VAL=P/'r4_management_ownership_continuity_v1_untouched20_objective_memory_merged.json';CON=P/'r4_management_ownership_continuity_v1_validation_contract.json';H=[5,15,30];ECON={'PARTIAL_FILL','FULL_FILL'}
def load(ps):
 out=[]
 for p in ps if isinstance(ps,list) else [ps]:out+=json.loads(p.read_text(encoding='utf-8'))['rows']
 return out
def sr(r,dom):
 if not dom:return [float(r.get('secondsLeft') or 0),float(r.get('floor') or 0),float(r.get('absNet') or 0),float(r.get('coverage') or 0),0.,float(r.get('weakResponsibilityCount') or 0),float(r.get('sameSideActiveObjectives') or 0),float(r.get('sameSideResidualQty') or 0),float(r.get('sameSideReservedQty') or 0),float(r.get('sameSideConfirmedQty') or 0),float(r.get('oldestSameSideObjectiveAgeS') or 0),float(r.get('timeSinceLastWeakFillS') or 999),float(r.get('weakFillQty5s') or 0),float(r.get('weakFillQty15s') or 0),float(r.get('objectiveStateEvents5s') or 0),float(r.get('objectiveStateEvents15s') or 0)]
 return [float(r.get('secondsLeft') or 0),float(r.get('floor') or 0),float(r.get('absNet') or 0),float(r.get('coverage') or 0),1.,float(r.get('dominantResponsibilityCount') or 0),float(r.get('oppositeSideActiveObjectives') or 0),float(r.get('oppositeSideResidualQty') or 0),float(r.get('oppositeSideReservedQty') or 0),float(r.get('oppositeSideConfirmedQty') or 0),float(r.get('oldestOppositeSideObjectiveAgeS') or 0),float(r.get('timeSinceLastDominantFillS') or 999),float(r.get('dominantFillQty5s') or 0),float(r.get('dominantFillQty15s') or 0),float(r.get('objectiveStateEvents5s') or 0),float(r.get('objectiveStateEvents15s') or 0)]
def lab(r,h,dom):
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
 contract=json.loads(CON.read_text(encoding='utf-8'));dev=load(DEV);vr=json.loads(VAL.read_text(encoding='utf-8'));val=vr['rows'];hs={};shared=[];weak=[];dom=[];dompos=0
 for h in H:
  X=np.asarray([sr(r,d) for r in dev for d in (False,True)],float);y=np.asarray([lab(r,h,d) for r in dev for d in (False,True)],int);XV=np.asarray([sr(r,d) for r in val for d in (False,True)],float);yv=np.asarray([lab(r,h,d) for r in val for d in (False,True)],int);m=RandomForestClassifier(n_estimators=260,max_depth=5,min_samples_leaf=4,class_weight='balanced_subsample',random_state=8200+h,n_jobs=1).fit(X,y);pv=m.predict(XV);yw=yv[0::2];yd=yv[1::2];pw=pv[0::2];pd=pv[1::2];sb=bal(yv,pv);wb=bal(yw,pw);db=bal(yd,pd);shared.append(sb);weak.append(wb);dom.append(db);dompos+=int(yd.sum());hs[str(h)]={'sharedBalancedAccuracy':sb,'weakBalancedAccuracy':wb,'dominantBalancedAccuracy':db,'weakPositiveSupport':int(yw.sum()),'dominantPositiveSupport':int(yd.sum()),'weakPredPositive':int(pw.sum()),'dominantPredPositive':int(pd.sum())}
 g=contract['primaryGates'];summary={'meanSharedBalancedAccuracy':float(np.mean(shared)),'meanWeakBalancedAccuracy':float(np.mean(weak)),'meanDominantBalancedAccuracy':float(np.mean(dom)),'dominantPositiveSupportAcrossHorizons':dompos};checks={'meanSharedBalancedAccuracy':summary['meanSharedBalancedAccuracy']>=g['meanSharedBalancedAccuracy_min'],'meanWeakBalancedAccuracy':summary['meanWeakBalancedAccuracy']>=g['meanWeakBalancedAccuracy_min'],'meanDominantBalancedAccuracy':summary['meanDominantBalancedAccuracy']>=g['meanDominantBalancedAccuracy_min'] if dompos>0 else False,'dominantSupportConclusive':dompos>0,'executionCoreExact':all(bool(x.get('executionCoreExact')) for x in vr['markets']),'duplicateExecutionCredit':int(vr.get('duplicateExecutionCredit') or 0)<=g['duplicateExecutionCredit_max']};rep={'version':'R4_MANAGEMENT_OWNERSHIP_CONTINUITY_V1_UNTOUCHED20_SCORE','researchOnly':True,'actionAuthority':False,'contractStatusBeforeSelection':contract['status'],'validationMarkets':len(vr['markets']),'validationRoots':len(val),'horizons':hs,'summary':summary,'checks':checks,'formalGatePassed':all(checks.values()),'guards':contract['guards']};(P/'r4_management_ownership_continuity_v1_untouched20_score.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
