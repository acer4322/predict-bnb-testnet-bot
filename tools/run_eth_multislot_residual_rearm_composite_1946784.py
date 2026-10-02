from __future__ import annotations
import argparse,json,math,shutil,tempfile,threading,time,zipfile,sys
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_multislot_economic_handoff_lease_lock_1946475 as mod
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
import tools.run_eth_dagger60_smoke_v1 as v1
from tools.eth_repair_modular.residual_execution_active_rearm import ResidualExecutionActiveRearmContext,ResidualExecutionActiveRearmPolicyV1
from tools.eth_repair_modular.residual_multipayment import ResidualCompositeMultipaymentContext,ResidualCompositeMultipaymentRouterPolicyV1
from tools.eth_repair_modular.recoverability import RecursiveCompositeCurrentCoordinateRecoverabilityPolicy,RecursiveCompositeRecoverabilityContext
EPS=1e-9;MID=1946784;pe=base.pe

class ResidualRearmCompositeHFT(mod.EconomicHandoffLeaseLockHFT):
 def __init__(self,*a,**kw):
  self.residualRearmPolicy=ResidualExecutionActiveRearmPolicyV1();self.residualRouter=ResidualCompositeMultipaymentRouterPolicyV1();self.recursivePolicy=RecursiveCompositeCurrentCoordinateRecoverabilityPolicy();self.residualRearmEvents=[];self.residualCompositeEvents=[];self.residualCompositeParents=set();self.residualCompositeKeys=[]
  super().__init__(*a,**kw)
 def _active_reconciled_qty(self,key):
  try:return float(getattr(self,'allocationLedgerV2').carrier_seen.get(str(key),0.0))
  except Exception:return 0.0
 def _try_residual_active_rearm(self,t):
  self._refresh_carrier_ledger(int(t));self._sync_parent_occupancy()
  for pid,a in list(getattr(self,'activeByParent',{}).items()):
   ak=str(a.get('key'));e=getattr(self,'carrierLedger',{}).get(ak,{});o=getattr(self,'orders',{}).get(ak,{})
   try:live=bool(o and v1.live(self.snap(o).get('status')))
   except Exception:live=False
   terminal=bool(e.get('terminalConfirmed'));cancel_pending=bool(e.get('cancelRequested')) and not terminal;filled=float(e.get('actualFilled') or 0.0);reconciled=self._active_reconciled_qty(ak);debt=float(self._parent_debt_now(int(pid)))
   occ=self.parentExecutionOccupancy.describe_parent(int(pid),debt);other_live=any(str(x.get('key'))!=ak and float(x.get('unresolvedQty') or 0)>EPS for x in occ.get('carriers',[]));pending=any(str(x.get('key'))!=ak and bool(x.get('cancelPending')) for x in occ.get('carriers',[]))
   d=self.residualRearmPolicy.evaluate(ResidualExecutionActiveRearmContext(int(pid),ak,live,cancel_pending,terminal,filled,reconciled,debt,other_live,pending))
   sig=(int(pid),ak,d.reason,round(debt,9),round(reconciled,9),terminal,other_live,pending)
   if not self.residualRearmEvents or self.residualRearmEvents[-1].get('signature')!=sig:
    self.residualRearmEvents.append({'t':int(t),'event':'RESIDUAL_ACTIVE_REARM_EVALUATION','parentId':int(pid),'activeKey':ak,'activeLive':live,'activeTerminal':terminal,'activeFilled':filled,'activeReconciledQty':reconciled,'residualDebt':debt,'otherLive':other_live,'pendingSibling':pending,'allow':bool(d.allow_rearm),'reason':d.reason,'signature':sig})
   if not d.allow_rearm:continue
   getattr(self,'activeByParent',{}).pop(int(pid),None);getattr(self,'hardConfirmed',set()).discard(int(pid));st=getattr(self,'generationEpochByParent',{}).get(int(pid))
   if st is not None:st.active_owned=False
   self.residualRearmEvents.append({'t':int(t),'event':'RESIDUAL_TERMINAL_ACTIVE_ARCHIVED','parentId':int(pid),'activeKey':ak,'residualDebt':debt})
 def process(self,t):
  super().process(t);self._try_residual_active_rearm(int(t))
 def _residual_context(self,t,qv,z):
  if z is None or str(z[3]).upper()!='REPAIR':return None
  rp=getattr(self,'repairParent',None)
  if not isinstance(rp,dict):return None
  pid=int(rp.get('id'));side=str(z[0]).upper();debt=float(self._parent_debt_now(pid))
  if debt<=EPS or side not in ('UP','DOWN') or not qv or side not in qv:return None
  price=float(qv[side]['bid']);physical=1.0/price if price>EPS else math.inf
  if not math.isfinite(physical) or physical<=debt+EPS or physical>12.0+EPS:return None
  pstate=self.allocationLedgerV2.describe_parent(pid) or {};prior_paid=float(pstate.get('repairPaid') or 0.0)
  if prior_paid<=EPS:return None
  self._sync_parent_occupancy();occ=self.parentExecutionOccupancy.describe_parent(pid,debt);same_live=any(float(x.get('unresolvedQty') or 0)>EPS for x in occ.get('carriers',[]));pending=any(bool(x.get('cancelPending')) for x in occ.get('carriers',[]));active_owned=pid in getattr(self,'activeByParent',{}) or bool(getattr(getattr(self,'generationEpochByParent',{}).get(pid),'active_owned',False))
  repair=min(debt,physical);overflow=max(0.0,physical-debt);floor_before,u,d,cost=self._raw_floor();hu=float(u)+(physical if side=='UP' else 0.0);hd=float(d)+(physical if side=='DOWN' else 0.0);hf=min(hu,hd)-(float(cost)+physical*price);surplus_side='UP' if hu>hd+EPS else 'DOWN' if hd>hu+EPS else side
  rec=self.recursivePolicy.evaluate(RecursiveCompositeRecoverabilityContext(float(floor_before),float(hf),surplus_side,abs(hu-hd),float(qv['UP']['bid']),float(qv['DOWN']['bid']),4,12.0))
  truth=getattr(self,'truthInv',self.auth_inv());opp='DOWN' if side=='UP' else 'UP';truth_role='REPAIR' if float(truth[side])<float(truth[opp])-EPS else 'EXPAND'
  ctx=ResidualCompositeMultipaymentContext(t=int(t),seconds_left=(int(self.capEnd)-int(t))/1000.0,responsibility_id=pid,responsibility_side=side,authorized_role='REPAIR',physical_truth_role=truth_role,prior_confirmed_payments=1,authoritative_residual_debt=debt,candidate_physical_qty=physical,candidate_repair_allocation=repair,candidate_overflow_allocation=overflow,shared_remaining_budget=12.0,ledger_snapshot_current=True,pending_sibling_reconciliation=pending,same_parent_live_carrier=same_live,active_already_owned=active_owned,venue_legal=True,recursive_current_coordinate_recoverable=bool(rec.recoverable));dec=self.residualRouter.evaluate(ctx)
  return {'pid':pid,'side':side,'price':price,'physical':physical,'repair':repair,'overflow':overflow,'debt':debt,'priorPaid':prior_paid,'truthRole':truth_role,'recursive':rec,'decision':dec,'objectiveId':z[4],'sameLive':same_live,'pending':pending,'activeOwned':active_owned,'floorBefore':floor_before,'floorAfterHyp':hf}
 def _submit_authorized(self,t,qv,z,roles_this_tick):
  self._try_residual_active_rearm(int(t));x=self._residual_context(int(t),qv,z)
  if x is None:return super()._submit_authorized(t,qv,z,roles_this_tick)
  ev={'t':int(t),'event':'RESIDUAL_COMPOSITE_ROUTER_EVALUATION','parentId':x['pid'],'side':x['side'],'residualDebt':x['debt'],'price':x['price'],'physicalQty':x['physical'],'repairAllocation':x['repair'],'overflowAllocation':x['overflow'],'priorRepairPaid':x['priorPaid'],'truthRole':x['truthRole'],'sameLive':x['sameLive'],'pendingSibling':x['pending'],'activeOwned':x['activeOwned'],'recursiveRecoverable':bool(x['recursive'].recoverable),'recursiveReason':x['recursive'].reason,'floorBefore':x['floorBefore'],'floorAfterHyp':x['floorAfterHyp'],'allow':bool(x['decision'].allow),'reason':x['decision'].reason}
  if x['pid'] in self.residualCompositeParents:
   ev.update({'allow':False,'reason':'ONE_RESIDUAL_COMPOSITE_ALREADY_SUBMITTED'});self.residualCompositeEvents.append(ev);return super()._submit_authorized(t,qv,z,roles_this_tick)
  if not x['decision'].allow:self.residualCompositeEvents.append(ev);return super()._submit_authorized(t,qv,z,roles_this_tick)
  self._pendingAuthorizedRole='REPAIR';self._pendingAuthorizedObjectiveId=x['objectiveId'];self._pendingParentId=x['pid'];self._pendingLane='MULTISLOT_RESIDUAL_COMPOSITE_MULTIPAYMENT';n0=self.n;ok=self.submit(int(t),x['side'],x['price'],x['physical']);ev['submit']=bool(ok)
  if ok:
   key=f"{x['side']}_{n0}";roles_this_tick.add('REPAIR');self.residualCompositeParents.add(x['pid']);self.residualCompositeKeys.append(key);ev['key']=key;ev['event']='RESIDUAL_COMPOSITE_MULTIPAYMENT_SUBMIT'
  self.residualCompositeEvents.append(ev);return bool(ok)
 def run_candidate(self,models,winner):
  r=self.run_locked(models,winner);r.update({'residualExecutionActiveRearmPolicy':self.residualRearmPolicy.name,'residualCompositeRouter':self.residualRouter.name,'residualRearmEvents':self.residualRearmEvents[:400],'residualCompositeEvents':self.residualCompositeEvents[:400],'residualCompositeKeys':self.residualCompositeKeys});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='multislot_residual_rearm_comp_1946784_'));stop=threading.Event();started=time.time()
 def hb():
  while not stop.wait(15):print(json.dumps({'heartbeat':'MULTISLOT_RESIDUAL_REARM_COMPOSITE_1946784','elapsedSeconds':round(time.time()-started,1)}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'MULTISLOT_RESIDUAL_REARM_COMPOSITE_START','marketId':MID}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz';s=pe.make(ResidualRearmCompositeHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
  try:r=s.run_candidate(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);occ=r.get('occupancyParents') or {};keys=set(r.get('residualCompositeKeys') or []);fills=[]
  finally:s.close()
  for k in keys:
   e=getattr(s,'carrierLedger',{}).get(k,{}) if False else {}
  # All fill/accounting metrics are retained in result/allocation parents even after simulator close.
  m=base.slim(r);ss=pe.safety(r);occok=all(float(x.get('overReservedQty') or 0)<=EPS for x in occ.values()) if occ else True;rearm=sum(x.get('event')=='RESIDUAL_TERMINAL_ACTIVE_ARCHIVED' for x in r.get('residualRearmEvents',[]));subs=[x for x in r.get('residualCompositeEvents',[]) if x.get('event')=='RESIDUAL_COMPOSITE_MULTIPAYMENT_SUBMIT' and x.get('submit')];p1=parents.get('1') or parents.get(1) or {};repair_paid=float(p1.get('repairPaid') or 0.0);remaining=float(p1.get('remainingDebt') or 0.0);overflow=float(p1.get('overflowBorn') or 0.0)
  gates={'activeTerminalRearmExercised':rearm>0,'residualCompositeSubmitExercised':len(subs)>0,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'executionOccupancyBounded':bool(occok),'oldParentRepairPaidExactlyInitial':abs(repair_paid-float(p1.get('initialDebt') or 0.0))<=1e-7,'oldParentResidualCleared':remaining<=EPS,'zeroPreBirthLeak':float(ss.get('preBirthLeak') or 0)<=EPS,'zeroDuplicateDebt':float(ss.get('duplicateDebt') or 0)<=EPS,'truthMismatchZero':float(ss.get('truthMismatch') or 0)<=EPS,'unexplainedOverOwnedZero':float(ss.get('unexplainedOverOwned') or 0)<=EPS,'unexplainedRepairDriftZero':float(ss.get('unexplainedRepairDrift') or 0)<=EPS,'floorNonWorseThanCurrentMultiSlot':m['floor']>=-0.760204081632653-1e-7}
  if not(all(v for k,v in gates.items() if k!='residualCompositeSubmitExercised') and gates['residualCompositeSubmitExercised']):decision='REJECT_OR_DIAGNOSE_RESIDUAL_REARM_COMPOSITE'
  elif overflow>EPS:decision='FUNCTIONAL_PASS_RESIDUAL_CLEARED_WITH_EXPLICIT_OVERFLOW'
  else:decision='FUNCTIONAL_PASS_RESIDUAL_CLEARED'
  out={'version':'ETH_MULTISLOT_RESIDUAL_REARM_COMPOSITE_1946784_V1','date':'2026-09-05','researchOnly':True,'marketId':MID,'winnerPostHocOnly':cr['winner'],'decision':decision,'candidate':m,'gates':gates,'safety':ss,'allocationParents':parents,'occupancyParents':occ,'residualRearmEvents':r.get('residualRearmEvents') or [],'residualCompositeEvents':r.get('residualCompositeEvents') or [],'economicEvents':(r.get('economicHandoffLeaseEvents') or [])[:200],'boundary':['single consumed market 1946784','EconomicHandoffLeaseLock behavior frozen until terminal Active archival','Active rearm only after terminal confirmation + AllocationLedger fill reconciliation + no live/pending sibling + residual debt','same Repair parent/objective/generation retained','ResidualCompositeMultipaymentRouterV1 frozen','Manager must supply REPAIR proposal','AllocationLedger V2 remains Repair-first/overflow-second authority','no threshold/model/price/timing tuning','<=180 fence preserved','winner posthoc only','realistic HFT','no dream fill','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'candidate':m,'gates':gates,'repairPaid':repair_paid,'remainingDebt':remaining,'overflowBorn':overflow,'rearmEvents':out['residualRearmEvents'][-10:],'compositeEvents':out['residualCompositeEvents'][:20]},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
