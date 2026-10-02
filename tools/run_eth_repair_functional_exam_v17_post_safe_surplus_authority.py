from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,importlib.util,os,math
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v16_phase_aware_economic_admission as v16
except ImportError:v16=sib('v16surplus','run_eth_repair_functional_exam_v16_phase_aware_economic_admission.py')
try:
 from tools import run_eth_repair_functional_exam_v15_economic_lane_admission as v15
except ImportError:v15=sib('v15surplus','run_eth_repair_functional_exam_v15_economic_lane_admission.py')
try:
 from tools import run_eth_repair_functional_exam_v13_anchored_wait10_scheduler as v13
except ImportError:v13=sib('v13surplus','run_eth_repair_functional_exam_v13_anchored_wait10_scheduler.py')
try:
 from tools import run_eth_repair_functional_exam_v9_conjunctive_parallel_router as v9
except ImportError:v9=sib('v9surplus','run_eth_repair_functional_exam_v9_conjunctive_parallel_router.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_repair_functional_exam_v1 as ex1
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9

class SurplusValueRuntime:
 def __init__(self,path):
  ck=joblib.load(path);self.version=ck.get('version');self.features=list(ck['features']);z=ck['models']['economicFull'];self.model=z['model'];self.cols=list(z['cols']);self.threshold=float(z['threshold'])
 def score(self,vals):
  x=np.asarray([float(vals[k]) for k in self.features],np.float32).reshape(1,-1)
  return float(self.model.predict_proba(x[:,self.cols])[0,1])

class PostSafeSurplusSim(v16.PhaseAwareEconomicAdmissionSim):
 def __init__(self,*a,surplus_value=None,**kw):
  super().__init__(*a,**kw);self.surplusValue=surplus_value;self.surplusObjective=None
  self.surplusCandidateEval=0;self.surplusValueAccept=0;self.surplusValueBlock=0;self.surplusFloorBlock=0;self.surplusSubmit=0;self.surplusFillCarriers=0
  self.balancedDualCandidateTicks=0;self.sameTickDualSurplusSubmit=0;self.acceptedProjectedFloorNegative=0;self.surplusScores=[];self.surplusMargins=[];self.surplusSubmitKeys=[]
 def _raw_floor(self):
  u,d,cu,cd,cost,reserve,debt,un,hist=self._reconstruct_economics();return float(min(u,d)-cost),float(u),float(d),float(cost)
 def _surplus_candidates(self,t,qv,qty):
  floor,u,d,cost=self._raw_floor();gross=u+d;absr=abs(u-d)/gross if gross>EPS else 0.
  if floor<-EPS or absr>.02 or self.repairParent is not None or self.outstanding_total()>EPS:return []
  if abs(u-d)<=EPS:sides=['UP','DOWN'];self.balancedDualCandidateTicks+=1
  else:sides=['UP' if u>d else 'DOWN']
  out=[]
  for side in sides:
   p=float(qv[side]['bid']);legal=1/p if p>EPS else 1e9;maxsafe=floor/p if p>EPS else 0.
   if maxsafe+EPS<legal:self.surplusFloorBlock+=1;continue
   q=min(12.,maxsafe,max(float(qty),legal))
   if q<legal-EPS:self.surplusFloorBlock+=1;continue
   x,vals=self._feature(t,side,q,p);rel=int(round(vals['candidate_relation']))
   if rel not in (-1,0):continue
   self.surplusCandidateEval+=1;sc=self.surplusValue.score(vals);self.surplusScores.append(sc);margin=sc-self.surplusValue.threshold;self.surplusMargins.append(margin)
   pu=u+(q if side=='UP' else 0.);pd=d+(q if side=='DOWN' else 0.);pfloor=min(pu,pd)-(cost+q*p)
   if pfloor<-1e-7:self.surplusFloorBlock+=1;continue
   if sc<self.surplusValue.threshold:self.surplusValueBlock+=1;continue
   self.surplusValueAccept+=1;out.append((margin,sc,side,q,p,pfloor))
  return out
 def _submit_surplus(self,t,cand):
  if cand is None:return False
  margin,sc,side,q,p,pfloor=cand
  if pfloor<-1e-7:self.acceptedProjectedFloorNegative+=1;return False
  if self.surplusObjective is None:self.surplusObjective=self._new_objective('EXPAND',side)
  else:self.surplusObjective['side']=side
  oid=self.surplusObjective['id'];n0=self.n
  self._pendingAuthorizedRole='EXPAND';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=None;self._pendingLane='SURPLUS'
  ok=self.submit(t,side,p,q)
  if ok:
   self.surplusSubmit+=1;self.surplusSubmitKeys.append(f'{side}_{n0}')
  return bool(ok)
 def _finalize_surplus_fills(self,t):
  self._refresh_carrier_ledger(t);self.surplusFillCarriers=sum(1 for k in self.surplusSubmitKeys if float(self.carrierLedger.get(k,{}).get('actualFilled',0.))>EPS)
 def run_exam_v17(self,models,winner):
  ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(self.meta['firstReceivedMs']);v1.ex.advance_to(self.bt,first);end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
  for u in ups:
   t=int(u[1]);v1.ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);ca=v1.apply(self.book,u);qv=v1.quotes(self.book)
   if not qv:continue
   if self.firstValid is None:self.firstValid=t
   if (end-t)/1000.<=180:continue
   self.seed_if_needed(t,qv)
   if not self.seeded:continue
   x=self.features(t,qv,ca,end).reshape(1,-1);pa=float(models['action'].predict_proba(x)[0,1]);global_active=pa>=models['actionTh'];qty=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))));qty=min(qty,12.)
   self.repairSupervisorTicks+=1;self._refresh_carrier_ledger(t);self._reconcile_objective(t);self._continuous_capacity_reconcile(t)
   ai=self.auth_inv();weak='UP' if ai['UP']<ai['DOWN']-EPS else 'DOWN' if ai['DOWN']<ai['UP']-EPS else None;dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None;cs=self.capState.snapshot(t,end)
   if weak is not None and self.repairParent is None:
    precap=self.capability.predict(cs);b0=self.repairParentBirths;self._maybe_birth_parent(t,weak,precap)
    if self.repairParentBirths>b0 and not global_active:self.repairParentBirthsBelowActionGate+=1
   if self.repairParent is None or weak is None:self._clear_anchor()
   roles=set();submitted=0
   if weak is not None and self.repairParent is not None:
    if self.lane_unresolved('REPAIR'):self._clear_anchor()
    else:
     due=self._ensure_anchor(t,self.capState.snapshot(t,end))
     if due is not None and t<due:self.timingWaitTicks+=1;self.timingParentPersistsDuringWaitTicks+=1
     else:
      if self.anchorWasWait:self.timingDeadlineFires+=1;self.timingWaitToNowTransitions+=1;self.anchorWasWait=False
      self.repairProposalTicks+=1
      if not global_active:self.repairProposalBelowActionGateTicks+=1
      p=float(qv[weak]['bid']);q=max(qty,1/p if p>EPS else 1e9);z=self.choose_authorized(t,end,weak,min(q,12.));ok=self._submit_authorized(t,qv,z,roles);submitted+=int(ok)
      if ok:
       self.timingRepairSubmits+=1
       if not global_active:self.repairSubmitsBelowActionGate+=1
       self._clear_anchor()
    if global_active and dom is not None:
     self.expandProposalTicks+=1;p=float(qv[dom]['bid']);q=max(qty,1/p if p>EPS else 1e9);z=self.choose_authorized(t,end,dom,min(q,12.));submitted+=int(self._submit_authorized(t,qv,z,roles))
   elif global_active and self.repairParent is None and self.outstanding_total()<=EPS:
    cands=self._surplus_candidates(t,qv,qty)
    if cands:
     best=max(cands,key=lambda z:(z[0],z[1]));submitted+=int(self._submit_surplus(t,best))
   if submitted>=2:self.sameTickDualSubmit+=1
  end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2);self._apply_due_observations(end2+self.fillObsLagMs+self.ackReleaseLagMs+1);self._refresh_carrier_ledger(end2+self.fillObsLagMs+self.ackReleaseLagMs+1);self._reconcile_objective(end2+self.fillObsLagMs+self.ackReleaseLagMs+1);self._finalize_surplus_fills(end2+self.fillObsLagMs+self.ackReleaseLagMs+1)
  gross=sum(self.inv.values());ff=self.roleStableAtFill+self.repairToExpandAtFill+self.expandToRepairAtFill
  return {'pnlDiagnosticOnly':self.inv.get(str(winner).upper(),0.)-self.cost,'buyNotional':self.cost,'pairCoverage':2*min(self.inv.values())/gross if gross>EPS else 0.,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'actualFillEvents':self.actualFillEvents,'partialFillEvents':self.partialFillEvents,'lateFillAfterCancelEvents':self.lateFillAfterCancelEvents,'repairToExpandAtFirstFill':self.repairToExpandAtFill,'expandToRepairAtFirstFill':self.expandToRepairAtFill,'authorizedSubmitWithTruthRoleMismatch':self.authorizedSubmitWithTruthRoleMismatch,'unresolvedCarrierQty':self.outstanding_total(),'overOwnedSubmitViolations':self.overOwnedSubmitViolations,'repairParentBirths':self.repairParentBirths,'repairParentCompletions':self.repairParentCompletions,'repairParentActiveAtEnd':int(self.repairParent is not None),'timingRepairSubmits':self.timingRepairSubmits,'crossLaneRepairFallbackBlocks':self.crossLaneRepairFallbackBlocks,'crossLaneExpandFallbackBlocks':self.crossLaneExpandFallbackBlocks,'basePriceEval':self.basePriceEval,'basePriceAccept':self.basePriceAccept,'basePriceBlock':self.basePriceBlock,'reservePriceEval':self.reservePriceEval,'reservePriceAccept':self.reservePriceAccept,'reservePriceBlock':self.reservePriceBlock,'surplusCandidateEval':self.surplusCandidateEval,'surplusValueAccept':self.surplusValueAccept,'surplusValueBlock':self.surplusValueBlock,'surplusFloorBlock':self.surplusFloorBlock,'surplusSubmit':self.surplusSubmit,'surplusFillCarriers':self.surplusFillCarriers,'balancedDualCandidateTicks':self.balancedDualCandidateTicks,'sameTickDualSurplusSubmit':self.sameTickDualSurplusSubmit,'acceptedProjectedFloorNegative':self.acceptedProjectedFloorNegative,'surplusScoreMean':float(np.mean(self.surplusScores)) if self.surplusScores else 0.,'surplusScoreMin':min(self.surplusScores,default=0.),'surplusScoreMax':max(self.surplusScores,default=0.),'surplusMarginMean':float(np.mean(self.surplusMargins)) if self.surplusMargins else 0.}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--economic-model',required=True);ap.add_argument('--price-model',required=True);ap.add_argument('--surplus-model',required=True);ap.add_argument('--market-ids',default='1827195,1827414,1829435');ap.add_argument('--scenarios',default='CONTROL');ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v17_surplus_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];models,_,_=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');timing=v13.Wait10Runtime(a.timing_model);economic=v15.EconomicLaneValueRuntime(a.economic_model);price=v16.RepairPriceEnvelopeRuntime(a.price_model);surplus=SurplusValueRuntime(a.surplus_model);by={int(r['marketId']):r for r in cohort};ids=[int(x) for x in a.market_ids.split(',') if x.strip()];names=[x.strip() for x in a.scenarios.split(',') if x.strip()];rows=[]
  for sc in names:
   cfg=ex1.SCENARIOS[sc]
   for mid in ids:
    cr=by[mid];sim=PostSafeSurplusSim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,cfg['fillObsLagMs'],cfg['ackReleaseLagMs'],capability=cap,timing=timing,economic=economic,price_envelope=price,surplus_value=surplus)
    try:r=sim.run_exam_v17(models,cr['winner']);c=sim.causal()
    finally:sim.close()
    rows.append({'scenario':sc,'marketId':mid,'functional':r,'causal':c});print(json.dumps({'marketId':mid,'surplusEval':r['surplusCandidateEval'],'surplusAccept':r['surplusValueAccept'],'surplusBlock':r['surplusValueBlock'],'surplusSubmit':r['surplusSubmit'],'surplusFills':r['surplusFillCarriers'],'dualTicks':r['balancedDualCandidateTicks'],'pnl':r['pnlDiagnosticOnly'],'drift':r['repairToExpandAtFirstFill'],'overOwned':r['overOwnedSubmitViolations']}),flush=True)
  keys=['surplusCandidateEval','surplusValueAccept','surplusValueBlock','surplusFloorBlock','surplusSubmit','surplusFillCarriers','balancedDualCandidateTicks','sameTickDualSurplusSubmit','acceptedProjectedFloorNegative','repairToExpandAtFirstFill','authorizedSubmitWithTruthRoleMismatch','overOwnedSubmitViolations','crossLaneExpandFallbackBlocks','crossLaneRepairFallbackBlocks','timingRepairSubmits','basePriceEval','basePriceAccept','basePriceBlock']
  agg={k:sum(int(x['functional'].get(k) or 0) for x in rows) for k in keys};agg['unresolvedCarrierQty']=sum(float(x['functional']['unresolvedCarrierQty']) for x in rows)
  gates={'surplusCandidatesExercised':agg['surplusCandidateEval']>0,'balancedDualCandidatesExercised':agg['balancedDualCandidateTicks']>0,'surplusValueAcceptExercised':agg['surplusValueAccept']>0,'surplusValueBlockExercised':agg['surplusValueBlock']>0,'surplusSubmitExercised':agg['surplusSubmit']>0,'actualSurplusFillExercised':agg['surplusFillCarriers']>0,'zeroSameTickDualSurplus':agg['sameTickDualSurplusSubmit']==0,'zeroAcceptedNegativeProjectedFloor':agg['acceptedProjectedFloorNegative']==0,'zeroRepairToExpand':agg['repairToExpandAtFirstFill']==0,'zeroTruthMismatch':agg['authorizedSubmitWithTruthRoleMismatch']==0,'zeroOverOwned':agg['overOwnedSubmitViolations']==0,'zeroUnresolved':abs(agg['unresolvedCarrierQty'])<=1e-9,'zeroCrossLaneExpandBypass':agg['crossLaneExpandFallbackBlocks']==0}
  out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V17_POST_SAFE_SURPLUS_AUTHORITY','researchOnly':True,'performanceGraduationEligible':False,'surplusTeacherVersion':surplus.version,'surplusThreshold':surplus.threshold,'selectedMarketIds':ids,'aggregate':agg,'gates':gates,'smokeVerified':all(gates.values()),'rows':rows,'boundary':['consumed HFT only','actual HftBacktest fills','no model gradients','no PnL threshold tuning','Repair priority retained','post-safe Surplus independent of Repair parent','no new entry <=180s']};op=Path(a.output) if a.output else Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'smokeVerified':out['smokeVerified'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
