from __future__ import annotations
import json,lzma,sys
from pathlib import Path
from collections import Counter,defaultdict
import numpy as np
from sklearn.ensemble import RandomForestClassifier
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_management_provenance_bridge_v1 as sim
from tools import test_r4_p0b_objective_ledger_runtime_materialization_v1 as led
from tools.build_r4_management_simulator_objective_memory_episodes_v5 import objctx,histctx
P=ROOT/'data/research/r4_v0/p0_provenance_v1';SRC=ROOT/'data/hft_forward_paper_v1/markets';PH=P/'r4_p0b_phase_routed_shadow_controller_v1.json';OUT=P/'r4_management_testbed_ownership_continuity_v1_integrated_shadow_v2.json'
DEV=[ROOT/'data/research/lan_worker_returns/r4-objv5-dev-a/dev_a.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-b/dev_b.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-c/dev_c.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-d/dev_d.json']
ECON={'PARTIAL_FILL','FULL_FILL'};EPS=1e-9

def load_rows(paths):
 out=[]
 for p in paths:out+=json.loads(p.read_text(encoding='utf-8'))['rows']
 return out

def sr(r,dom):
 sl=float(r.get('secondsLeft') or r.get('seconds_left') or 0);base=[sl,float(r.get('floor') or 0),float(r.get('absNet') or 0),float(r.get('coverage') or 0),1. if dom else 0.]
 if not dom:return base+[float(r.get('weakResponsibilityCount') or r.get('weak_active_owners') or 0),float(r.get('sameSideActiveObjectives') or 0),float(r.get('sameSideResidualQty') or 0),float(r.get('sameSideReservedQty') or 0),float(r.get('sameSideConfirmedQty') or 0),float(r.get('oldestSameSideObjectiveAgeS') or 0),float(r.get('timeSinceLastWeakFillS') or 999),float(r.get('weakFillQty5s') or 0),float(r.get('weakFillQty15s') or 0),float(r.get('objectiveStateEvents5s') or 0),float(r.get('objectiveStateEvents15s') or 0)]
 return base+[float(r.get('dominantResponsibilityCount') or r.get('dominant_active_owners') or 0),float(r.get('oppositeSideActiveObjectives') or 0),float(r.get('oppositeSideResidualQty') or 0),float(r.get('oppositeSideReservedQty') or 0),float(r.get('oppositeSideConfirmedQty') or 0),float(r.get('oldestOppositeSideObjectiveAgeS') or 0),float(r.get('timeSinceLastDominantFillS') or 999),float(r.get('dominantFillQty5s') or 0),float(r.get('dominantFillQty15s') or 0),float(r.get('objectiveStateEvents5s') or 0),float(r.get('objectiveStateEvents15s') or 0)]

def lab_dev(r,dom):
 rel='DOMINANT' if dom else 'WEAK';es=[e for e in r['portfolioEvents30s'] if int(e['dtMs'])<=5000];opens={str(e.get('responsibilityId')):int(e['dtMs']) for e in es if e['eventType']=='RESPONSIBILITY_OPENED' and e.get('responsibilityId')}
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
 return float(np.mean(vals)) if vals else None

def teacher(r):
 fut=str(r.get('future_management_label_5s') or '')
 return 'CONTINUE' if fut=='CONTINUE_WEAK' else 'HANDOFF' if fut=='HANDOFF_ALLOW' else 'OBSERVE' if fut=='OBSERVE_NO_EVENT' else 'UNKNOWN'

def prefix_before_submit(journal,mr):
 rid=str(mr.get('submittedResponsibilityId') or '');iid=str(mr.get('submittedIntentId') or '');t=int(mr['t'])
 for i,e in enumerate(journal):
  if str(e.get('responsibility_id'))==rid and int(e.get('received_at_ms') or 0)==t and e.get('event_type')=='RESPONSIBILITY_OPENED':return journal[:i]
 # fallback is strictly earlier receipt time, conservative and no current-submit leakage
 return [e for e in journal if int(e.get('received_at_ms') or 0)<t]

def open_roots(prefix,weak):
 by=defaultdict(list)
 for e in prefix:by[str(e.get('responsibility_id'))].append(e)
 w=set();d=set()
 for rid,es in by.items():
  op=next((e for e in es if e.get('event_type')=='RESPONSIBILITY_OPENED'),None)
  if op is None:continue
  if any(e.get('event_type') in {'RESPONSIBILITY_COMPLETED','RESPONSIBILITY_TERMINATED'} for e in es):continue
  req=float(op.get('requested_qty') or 0);filled=sum(float((e.get('extras') or {}).get('fillDeltaQty') or 0) for e in es if e.get('event_type') in ECON)
  if req-filled<=EPS:continue
  side=str(op.get('side') or '')
  (w if side==weak else d if side in {'UP','DOWN'} else set()).add(rid)
 return w,d

def main():
 ph=json.loads(PH.read_text(encoding='utf-8'));targets=[r for r in ph['trace'] if r.get('phase')=='MANAGEMENT_60_180' and int(r.get('build_now') or 0)==1];mids=sorted(set(int(r['marketId']) for r in targets));bykey={(int(r['marketId']),int(r['t']),str(r.get('side')),str(r.get('kind'))):r for r in targets}
 dev=load_rows(DEV);X=np.asarray([sr(r,d) for r in dev for d in (False,True)],float);y=np.asarray([lab_dev(r,d) for r in dev for d in (False,True)],int);model=RandomForestClassifier(n_estimators=260,max_depth=5,min_samples_leaf=4,class_weight='balanced_subsample',random_state=8205,n_jobs=1).fit(X,y)
 rows=[];missing=[]
 for mid in mids:
  d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'));res=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True);journal=res.get('provenanceJournal') or []
  for mr in res.get('managementShadowRows') or []:
   key=(mid,int(mr['t']),str(mr.get('side')),str(mr.get('kind')));pr=bykey.get(key)
   if pr is None:continue
   pref=prefix_before_submit(journal,mr);weak=str(mr.get('weakSide') or '');wids,dids=open_roots(pref,weak);oj,ostate,resp_to_obj=led.materialize(mid,pref);rid=next(iter(wids or dids),str(mr.get('submittedResponsibilityId') or 'NONE'))
   z=dict(mr);z['weakResponsibilityCount']=float(mr.get('weak_active_owners') or 0);z['dominantResponsibilityCount']=float(mr.get('dominant_active_owners') or 0);z.update(objctx(mid,str(rid),int(mr['t']),weak,oj,resp_to_obj));z.update(histctx(pref,oj,resp_to_obj,str(rid),int(mr['t']),weak,weak))
   pw,pd=map(float,model.predict_proba(np.asarray([sr(z,False),sr(z,True)],float))[:,1]);cut=int(mr['t'])+5000;fut=[e for e in journal if int(mr['t'])<int(e.get('received_at_ms') or 0)<=cut and e.get('event_type') in ECON];aw=int(any(str(e.get('responsibility_id')) in wids for e in fut));ad=int(any(str(e.get('responsibility_id')) in dids for e in fut))
   orig=str(pr.get('shadow_decision') or '');cf=orig;veto=False
   if orig=='CONTINUE' and float(mr.get('weak_active_owners') or 0)>0 and pw<0.5:cf='OBSERVE';veto=True
   tr=teacher(pr);econ=bool(int(pr.get('floorImproved5s') or 0) or int(pr.get('absNetReduced5s') or 0));rows.append({'marketId':mid,'t':int(mr['t']),'originalDecision':orig,'counterfactualDecision':cf,'teacherDecision':tr,'pExistingWeak5s':pw,'pExistingDom5s':pd,'actualExistingWeakFill5s':aw,'actualExistingDomFill5s':ad,'weakOpenResponsibilityCount':len(wids),'domOpenResponsibilityCount':len(dids),'weakLiveOwnerCount':int(mr.get('weak_active_owners') or 0),'domLiveOwnerCount':int(mr.get('dominant_active_owners') or 0),'continuityVeto':veto,'futureEconomicProgress5s':econ,'futureWeakMakerFill5s':int(pr.get('futureWeakMakerFill5s') or 0),'originalMatch':orig==tr,'counterfactualMatch':cf==tr})
 keys={(r['marketId'],r['t'],) for r in rows}
 for r in targets:
  if (int(r['marketId']),int(r['t'])) not in keys:missing.append({'marketId':r['marketId'],'t':r['t'],'side':r.get('side'),'kind':r.get('kind')})
 yw=np.asarray([r['actualExistingWeakFill5s'] for r in rows]);pw=np.asarray([int(r['pExistingWeak5s']>=.5) for r in rows]);yd=np.asarray([r['actualExistingDomFill5s'] for r in rows]);pd=np.asarray([int(r['pExistingDom5s']>=.5) for r in rows]);v=[r for r in rows if r['continuityVeto']];orig=sum(r['originalMatch'] for r in rows);cf=sum(r['counterfactualMatch'] for r in rows)
 diag={'originalMatches':orig,'counterfactualMatches':cf,'netMatchDelta':cf-orig,'continuityVetoCount':len(v),'errorRemovals':sum((not r['originalMatch']) and r['counterfactualMatch'] for r in v),'errorsIntroduced':sum(r['originalMatch'] and not r['counterfactualMatch'] for r in v),'correctContinueVetoes':sum(r['originalDecision']=='CONTINUE' and r['teacherDecision']=='CONTINUE' for r in v),'futureEconomicProgressVetoes':sum(r['futureEconomicProgress5s'] for r in v),'handoffMutationCount':sum(r['originalDecision']=='HANDOFF' and r['counterfactualDecision']!=r['originalDecision'] for r in rows),'protectionMutationCount':0}
 out={'version':'R4_MANAGEMENT_TESTBED_OWNERSHIP_CONTINUITY_V1_INTEGRATED_SHADOW_V2','researchOnly':True,'actionAuthority':False,'sourceSimulator':'test_r4_management_provenance_bridge_v1 (the validated source family for phase-routed testbed)','cohortStatus':'CONSUMED_WHOLEMARKET20_DIAGNOSTIC_ONLY','markets':len(mids),'eligibleRowsExpected':len(targets),'matchedRows':len(rows),'missingRows':len(missing),'existingProgressHead5s':{'weakBalancedAccuracy':bal(yw,pw),'dominantBalancedAccuracy':bal(yd,pd),'weakPositiveSupport':int(yw.sum()),'dominantPositiveSupport':int(yd.sum()),'weakPredPositive':int(pw.sum()),'dominantPredPositive':int(pd.sum())},'decisionDiagnostic':diag,'vetoTeacherCounts':dict(Counter(r['teacherDecision'] for r in v)),'vetoRows':v,'missing':missing,'rows':rows,'interpretationBoundary':'Ownership Continuity is an environment-side annotation. Counterfactual OBSERVE veto is diagnostic only; NEW Maker/Taker remain policy-side; no promotion from consumed cohort.'}
 OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({k:out[k] for k in ['markets','eligibleRowsExpected','matchedRows','missingRows','existingProgressHead5s','decisionDiagnostic','vetoTeacherCounts']},indent=2,ensure_ascii=False))
if __name__=='__main__':main()
