from __future__ import annotations
import json,lzma,sys
from pathlib import Path
from collections import Counter
import numpy as np
from sklearn.ensemble import RandomForestClassifier
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_pending_submit_reservation_simulator_v1 as sim
from tools import test_r4_p0b_objective_ledger_runtime_materialization_v1 as led
from tools.build_r4_management_simulator_objective_memory_episodes_v5 import objctx,histctx
P=ROOT/'data/research/r4_v0/p0_provenance_v1';SRC=ROOT/'data/hft_forward_paper_v1/markets'
PH=P/'r4_p0b_phase_routed_shadow_controller_v1.json';OUT=P/'r4_management_testbed_ownership_continuity_v1_integrated_shadow_v1.json'
DEV=[ROOT/'data/research/lan_worker_returns/r4-objv5-dev-a/dev_a.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-b/dev_b.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-c/dev_c.json',ROOT/'data/research/lan_worker_returns/r4-objv5-dev-d/dev_d.json']
ECON={'PARTIAL_FILL','FULL_FILL'};EPS=1e-9

def load_rows(paths):
 out=[]
 for p in paths:out+=json.loads(p.read_text(encoding='utf-8'))['rows']
 return out

def sr(r,dom):
 if not dom:return [float(r.get('secondsLeft') or r.get('seconds_left') or 0),float(r.get('floor') or 0),float(r.get('absNet') or 0),float(r.get('coverage') or 0),0.,float(r.get('weakResponsibilityCount') or 0),float(r.get('sameSideActiveObjectives') or 0),float(r.get('sameSideResidualQty') or 0),float(r.get('sameSideReservedQty') or 0),float(r.get('sameSideConfirmedQty') or 0),float(r.get('oldestSameSideObjectiveAgeS') or 0),float(r.get('timeSinceLastWeakFillS') or 999),float(r.get('weakFillQty5s') or 0),float(r.get('weakFillQty15s') or 0),float(r.get('objectiveStateEvents5s') or 0),float(r.get('objectiveStateEvents15s') or 0)]
 return [float(r.get('secondsLeft') or r.get('seconds_left') or 0),float(r.get('floor') or 0),float(r.get('absNet') or 0),float(r.get('coverage') or 0),1.,float(r.get('dominantResponsibilityCount') or 0),float(r.get('oppositeSideActiveObjectives') or 0),float(r.get('oppositeSideResidualQty') or 0),float(r.get('oppositeSideReservedQty') or 0),float(r.get('oppositeSideConfirmedQty') or 0),float(r.get('oldestOppositeSideObjectiveAgeS') or 0),float(r.get('timeSinceLastDominantFillS') or 999),float(r.get('dominantFillQty5s') or 0),float(r.get('dominantFillQty15s') or 0),float(r.get('objectiveStateEvents5s') or 0),float(r.get('objectiveStateEvents15s') or 0)]

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

def main():
 ph=json.loads(PH.read_text(encoding='utf-8'));targets=[r for r in ph['trace'] if r.get('phase')=='MANAGEMENT_60_180' and int(r.get('build_now') or 0)==1];mids=sorted(set(int(r['marketId']) for r in targets))
 dev=load_rows(DEV);X=np.asarray([sr(r,d) for r in dev for d in (False,True)],float);y=np.asarray([lab_dev(r,d) for r in dev for d in (False,True)],int);model=RandomForestClassifier(n_estimators=260,max_depth=5,min_samples_leaf=4,class_weight='balanced_subsample',random_state=8205,n_jobs=1).fit(X,y)
 bykey={(int(r['marketId']),int(r['t']),str(r.get('side')),str(r.get('kind'))):r for r in targets}; enriched=[];missing=[]
 for mid in mids:
  d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'));res=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True);journal=res.get('provenanceJournal') or []
  for mr in res.get('managementShadowRows') or []:
   key=(mid,int(mr['t']),str(mr.get('side')),str(mr.get('kind')))
   pr=bykey.get(key)
   if pr is None or int(pr.get('build_now') or 0)!=1:continue
   cursor=int(mr.get('provenanceJournalCursor') or 0);prefix=journal[:cursor];oj,ostate,resp_to_obj=led.materialize(mid,prefix);weak=str(mr.get('weakSide') or '');rid=(mr.get('weakResponsibilityIds') or mr.get('dominantResponsibilityIds') or [mr.get('submittedResponsibilityId') or 'NONE'])[0]
   z=dict(mr);z.update(objctx(mid,str(rid),int(mr['t']),weak,oj,resp_to_obj));z.update(histctx(prefix,oj,resp_to_obj,str(rid),int(mr['t']),weak,weak))
   probs=model.predict_proba(np.asarray([sr(z,False),sr(z,True)],float))[:,1];pw,pd=map(float,probs)
   weak_ids=set(map(str,mr.get('weakResponsibilityIds') or []));dom_ids=set(map(str,mr.get('dominantResponsibilityIds') or []));cut=int(mr['t'])+5000
   fut=[e for e in journal if int(mr['t'])<int(e.get('received_at_ms') or 0)<=cut and e.get('event_type') in ECON]
   aw=int(any(str(e.get('responsibility_id')) in weak_ids for e in fut));ad=int(any(str(e.get('responsibility_id')) in dom_ids for e in fut))
   orig=str(pr.get('shadow_decision') or '');cf=orig;veto=False
   if orig=='CONTINUE' and weak_ids and pw<0.5:cf='OBSERVE';veto=True
   tr=teacher(pr);econ=bool(int(pr.get('floorImproved5s') or 0) or int(pr.get('absNetReduced5s') or 0))
   enriched.append({'marketId':mid,'t':int(mr['t']),'originalDecision':orig,'counterfactualDecision':cf,'teacherDecision':tr,'pExistingWeak5s':pw,'pExistingDom5s':pd,'actualExistingWeakFill5s':aw,'actualExistingDomFill5s':ad,'weakExistingCount':len(weak_ids),'domExistingCount':len(dom_ids),'continuityVeto':veto,'futureEconomicProgress5s':econ,'futureWeakMakerFill5s':int(pr.get('futureWeakMakerFill5s') or 0),'originalMatch':orig==tr,'counterfactualMatch':cf==tr,'phase':pr.get('phase'),'reason':pr.get('reason')})
 matched_keys={(r['marketId'],r['t']) for r in enriched}
 for r in targets:
  if (int(r['marketId']),int(r['t'])) not in matched_keys:missing.append({'marketId':r['marketId'],'t':r['t'],'side':r.get('side'),'kind':r.get('kind')})
 yw=np.asarray([r['actualExistingWeakFill5s'] for r in enriched]);pw=np.asarray([int(r['pExistingWeak5s']>=.5) for r in enriched]);yd=np.asarray([r['actualExistingDomFill5s'] for r in enriched]);pd=np.asarray([int(r['pExistingDom5s']>=.5) for r in enriched])
 orig_match=sum(r['originalMatch'] for r in enriched);cf_match=sum(r['counterfactualMatch'] for r in enriched);v=[r for r in enriched if r['continuityVeto']]
 removals=sum((not r['originalMatch']) and r['counterfactualMatch'] for r in v);introduced=sum(r['originalMatch'] and (not r['counterfactualMatch']) for r in v);correct_continue_veto=sum(r['originalDecision']=='CONTINUE' and r['teacherDecision']=='CONTINUE' for r in v);econ_veto=sum(r['futureEconomicProgress5s'] for r in v)
 out={'version':'R4_MANAGEMENT_TESTBED_OWNERSHIP_CONTINUITY_V1_INTEGRATED_SHADOW_V1','researchOnly':True,'actionAuthority':False,'cohortStatus':'CONSUMED_WHOLEMARKET20_DIAGNOSTIC_ONLY','markets':len(mids),'eligibleRowsExpected':len(targets),'matchedRows':len(enriched),'missingRows':len(missing),'existingProgressHead5s':{'weakBalancedAccuracy':bal(yw,pw),'dominantBalancedAccuracy':bal(yd,pd),'weakPositiveSupport':int(yw.sum()),'dominantPositiveSupport':int(yd.sum()),'weakPredPositive':int(pw.sum()),'dominantPredPositive':int(pd.sum())},'decisionDiagnostic':{'originalMatches':orig_match,'counterfactualMatches':cf_match,'netMatchDelta':cf_match-orig_match,'continuityVetoCount':len(v),'errorRemovals':removals,'errorsIntroduced':introduced,'correctContinueVetoes':correct_continue_veto,'futureEconomicProgressVetoes':econ_veto,'handoffMutationCount':sum(r['originalDecision']=='HANDOFF' and r['counterfactualDecision']!=r['originalDecision'] for r in enriched),'protectionMutationCount':0},'vetoTeacherCounts':dict(Counter(r['teacherDecision'] for r in v)),'vetoRows':v,'missing':missing,'rows':enriched,'interpretationBoundary':'Ownership Continuity is an environment-side annotation. Counterfactual OBSERVE veto is diagnostic only; it is not promoted as Management Necessity or action authority.'}
 OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({k:out[k] for k in ['markets','eligibleRowsExpected','matchedRows','missingRows','existingProgressHead5s','decisionDiagnostic','vetoTeacherCounts']},indent=2,ensure_ascii=False))
if __name__=='__main__':main()
