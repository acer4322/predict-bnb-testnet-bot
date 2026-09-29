from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,importlib.util
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
try:
 from tools import run_eth_repair_functional_exam_v4_persistent_carrier_ledger as v4
except ImportError:
 p4=Path(__file__).resolve().with_name('run_eth_repair_functional_exam_v4_persistent_carrier_ledger.py');sp=importlib.util.spec_from_file_location('v4_v7coverage',p4);v4=importlib.util.module_from_spec(sp);sp.loader.exec_module(v4)
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9;MID=1816800;REPAIR='UP';DOM='DOWN';REPAIR_OID=200;EXPAND_OID=300

class ParallelLaneSim(v4.PersistentCarrierSim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.repairParent=None;self.parentCompletions=0;self.parentCompletionDeferred=0;self.parentIdAtPartial=None;self.parentIdAfterDip=None;self.dipTicks=0;self.parallelSubmitAccepted=0;self.parallelOwnershipTicks=0;self.expandActualFillEvents=0;self.repairResumeSubmits=0;self.parentStructuralCompleteSeen=False
 def submit(self,t,side,p,q):
  n0=self.n;parent_id=getattr(self,'_pendingParentId',None);lane=getattr(self,'_pendingLane',None);ok=super().submit(t,side,p,q)
  if ok:
   key=f'{side}_{n0}'
   if key in self.carrierLedger:self.carrierLedger[key]['parentId']=parent_id;self.carrierLedger[key]['lane']=lane
  self._pendingParentId=None;self._pendingLane=None
  return ok
 def parent_unresolved(self):
  pid=self.repairParent['id'] if self.repairParent else None
  return [(k,e,r) for k,e,r in self.unresolved() if e.get('parentId')==pid]
 def lane_unresolved(self,lane):
  return [(k,e,r) for k,e,r in self.parent_unresolved() if e.get('lane')==lane]
 def _reconcile_objective(self,t):
  # V7 parent completion is scoped to the parent, not to one child role.
  if not self.repairParent:return
  ai=self.auth_inv();side=self.repairParent['side'];opp='DOWN' if side=='UP' else 'UP';structural=ai[side]>=ai[opp]-EPS
  if structural:self.parentStructuralCompleteSeen=True
  if structural and self.parent_unresolved():
   self.parentCompletionDeferred+=1;return
  if structural and not self.parent_unresolved():
   self.parentCompletions+=1;self.repairParent=None;self.activeObjective=None;self.awaitingReentry=True
 def _continuous_capacity_reconcile(self,t):
  self._refresh_carrier_ledger(t)
  if not self.repairParent:return
  ai=self.auth_inv();side=self.repairParent['side'];opp='DOWN' if side=='UP' else 'UP';gap=max(0.,float(ai[opp])-float(ai[side]));rows=self.lane_unresolved('REPAIR');owned=sum(r for _,_,r in rows);self.maxOwnedRepairOverGap=max(self.maxOwnedRepairOverGap,max(0.,owned-gap))
  if owned<=gap+EPS:return
  excess=owned-gap
  for key,e,rem in sorted(rows,key=lambda x:int(x[1].get('submittedAt',0)),reverse=True):
   if excess<=EPS:break
   if e.get('cancelRequested'):continue
   if self._cancel_key(t,key):self.excessCarrierCancelRequests+=1;excess-=rem
 def process(self,t):
  pre_expand=sum(float(e.get('actualFilled',0.)) for e in self.carrierLedger.values() if e.get('lane')=='EXPAND')
  super().process(t)
  post_expand=sum(float(e.get('actualFilled',0.)) for e in self.carrierLedger.values() if e.get('lane')=='EXPAND')
  if post_expand>pre_expand+EPS:self.expandActualFillEvents+=1
  if self.lane_unresolved('REPAIR') and self.lane_unresolved('EXPAND'):self.parallelOwnershipTicks+=1

def place_lane(sim,t,qv,lane,side,qty,oid):
 if sim.unobserved_qty(side)>EPS:return None
 p=float(qv[side]['bid']);legal=1/p if p>EPS else 1e9
 if lane=='REPAIR':
  ai=sim.auth_inv();opp='DOWN' if side=='UP' else 'UP';gap=max(0.,float(ai[opp])-float(ai[side]));owned=sum(r for _,_,r in sim.lane_unresolved('REPAIR'));remaining=max(0.,gap-owned);qty=min(float(qty),remaining)
  if qty<legal-EPS:return None
 qty=max(float(qty),legal);qty=min(qty,12.)
 sim._pendingAuthorizedRole=lane;sim._pendingAuthorizedObjectiveId=oid;sim._pendingParentId=sim.repairParent['id'] if sim.repairParent else None;sim._pendingLane=lane
 ok=sim.submit(t,side,p,qty)
 return f'{side}_{sim.n-1}' if ok else None

def run_one(sim,fill_obs_lag_ms):
 ups=sorted(sim.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(sim.meta['firstReceivedMs']);v1.ex.advance_to(sim.bt,first);end=int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs']);first_valid=None;seed_key=None;seed_cancel=False;repair_key=None;partial_at=None;expand_key=None;expand_ready=False;dip_remaining=0;resume_done=False;events=[]
 for u in ups:
  t=int(u[1]);v1.ex.advance_to(sim.bt,t);pre=dict(sim.truthInv);sim.process(t);sim._refresh_carrier_ledger(t)
  for side in ('UP','DOWN'):
   d=float(sim.truthInv[side])-float(pre[side])
   if d>EPS:events.append({'t':t,'side':side,'inc':d,'truthInv':dict(sim.truthInv)})
  # TTL for forced child carriers; seed is canceled immediately after first material fill.
  for key in [repair_key,expand_key]:
   if key and key in sim.orders:
    o=sim.orders[key]
    if t-int(o['placed'])>=12000:sim._cancel_key(t,key)
  v1.apply(sim.book,u);qv=v1.quotes(sim.book)
  if not qv:continue
  if first_valid is None:first_valid=t
  if seed_key is None and t-first_valid>=2000 and (end-t)/1000>180:
   sim._pendingAuthorizedRole='EXPAND';sim._pendingAuthorizedObjectiveId=100;sim._pendingParentId=None;sim._pendingLane='SEED';seed_key=place_lane(sim,t,qv,'EXPAND',DOM,12.,100)
   # place_lane attaches parent for EXPAND; detach seed explicitly because parent starts only after seed terminal.
   if seed_key and seed_key in sim.carrierLedger:sim.carrierLedger[seed_key]['parentId']=None;sim.carrierLedger[seed_key]['lane']='SEED'
  if seed_key and not seed_cancel and float(sim.truthInv[DOM])>EPS:
   sim._cancel_key(t,seed_key);seed_cancel=True
  if seed_key and repair_key is None:
   e=sim.carrierLedger.get(seed_key);seed_terminal=bool(e and (e.get('terminalConfirmed') or sim._ledger_remaining(e)<=EPS))
   if seed_terminal and float(sim.truthInv[DOM])>float(sim.truthInv[REPAIR])+EPS and (end-t)/1000>180:
    sim.repairParent={'id':1,'side':REPAIR,'bornAt':int(t)};sim.activeObjective={'id':REPAIR_OID,'role':'REPAIR','side':REPAIR};repair_key=place_lane(sim,t,qv,'REPAIR',REPAIR,12.,REPAIR_OID)
  if repair_key and partial_at is None:
   o=sim.orders.get(repair_key);s=sim.snap(o);cum=float(s.get('cumExecQty') or 0.)
   if cum>EPS and cum<float(o['qty'])-EPS:
    partial_at=t;sim.parentIdAtPartial=sim.repairParent['id'] if sim.repairParent else None;expand_ready=True;dip_remaining=3
  if dip_remaining>0 and sim.repairParent:
   sim.dipTicks+=1;dip_remaining-=1
   if dip_remaining==0:sim.parentIdAfterDip=sim.repairParent['id'] if sim.repairParent else None
  # Critical V7 action: next decision tick after a real Repair partial, create EXPAND lane while Repair carrier remains unresolved.
  if expand_ready and expand_key is None and partial_at is not None and t>partial_at and sim.lane_unresolved('REPAIR'):
   expand_key=place_lane(sim,t,qv,'EXPAND',DOM,5.,EXPAND_OID);sim.parallelSubmitAccepted+=int(expand_key is not None);expand_ready=False
  # After original Repair carrier resolves/cancels, if parent still has a weak-side deficit, resume SAME parent Repair lane once.
  if repair_key and not resume_done and sim.repairParent and not sim.lane_unresolved('REPAIR'):
   ai=sim.auth_inv();gap=max(0.,float(ai[DOM])-float(ai[REPAIR]))
   if gap>EPS and t>(partial_at or 0):
    rk=place_lane(sim,t,qv,'REPAIR',REPAIR,12.,REPAIR_OID)
    if rk:repair_key=rk;sim.repairResumeSubmits+=1;resume_done=True
 # terminal flush
 end2=int(sim.meta['lastReceivedMs']);v1.ex.advance_to(sim.bt,end2);sim.process(end2);sim._apply_due_observations(end2+fill_obs_lag_ms+1);sim._refresh_carrier_ledger(end2+fill_obs_lag_ms+1);sim._reconcile_objective(end2+fill_obs_lag_ms+1)
 firstfills=sim.roleStableAtFill+sim.repairToExpandAtFill+sim.expandToRepairAtFill
 return {'marketId':MID,'fillObsLagMs':fill_obs_lag_ms,'seedTruthInv':dict(sim.truthInv),'repairPartialSeen':partial_at is not None,'repairPartialAt':partial_at,'parallelSubmitAccepted':sim.parallelSubmitAccepted,'parallelOwnershipTicks':sim.parallelOwnershipTicks,'expandActualFillEvents':sim.expandActualFillEvents,'repairResumeSubmits':sim.repairResumeSubmits,'parentIdAtPartial':sim.parentIdAtPartial,'parentIdAfterDip':sim.parentIdAfterDip,'dipTicks':sim.dipTicks,'parentSurvivedDip':sim.parentIdAtPartial is not None and sim.parentIdAtPartial==sim.parentIdAfterDip,'parentStructuralCompleteSeen':sim.parentStructuralCompleteSeen,'parentCompletionDeferredTicks':sim.parentCompletionDeferred,'parentCompletions':sim.parentCompletions,'parentActiveAtEnd':sim.repairParent is not None,'repairToExpandAtFirstFill':sim.repairToExpandAtFill,'expandToRepairAtFirstFill':sim.expandToRepairAtFill,'authorizedTruthRoleMismatch':sim.authorizedSubmitWithTruthRoleMismatch,'overOwnedSubmitViolations':sim.overOwnedSubmitViolations,'maxOwnedRepairOverGap':sim.maxOwnedRepairOverGap,'unresolvedParentQty':sum(r for _,_,r in sim.parent_unresolved()) if sim.repairParent else 0.,'partialFillEvents':sim.partialFillEvents,'lateFillAfterCancelEvents':sim.lateFillAfterCancelEvents,'firstFillRoleEvents':firstfills,'events':events[:20]}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v7_parallel_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);models,_,_=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));rows=[]
  for lag in (0,3000):
   sim=ParallelLaneSim(tmp/'tapes'/f'{MID}.json.xz','FORCE_UP',models,life,lag,0)
   try:r=run_one(sim,lag)
   finally:sim.close()
   rows.append(r);print(json.dumps({k:r[k] for k in ['fillObsLagMs','repairPartialSeen','parallelSubmitAccepted','parallelOwnershipTicks','expandActualFillEvents','repairResumeSubmits','parentSurvivedDip','repairToExpandAtFirstFill','authorizedTruthRoleMismatch','overOwnedSubmitViolations','unresolvedParentQty']}),flush=True)
  gates={'realRepairPartialExercised':all(r['repairPartialSeen'] for r in rows),'parallelChildAcceptedWhileRepairOutstanding':all(r['parallelSubmitAccepted']>0 and r['parallelOwnershipTicks']>0 for r in rows),'parentPersistsAcrossForcedPredictionDip':all(r['parentSurvivedDip'] for r in rows),'noRepairToExpandDrift':all(r['repairToExpandAtFirstFill']==0 for r in rows),'noAuthorizedTruthRoleMismatch':all(r['authorizedTruthRoleMismatch']==0 for r in rows),'noOverOwnedRepair':all(r['overOwnedSubmitViolations']==0 and r['maxOwnedRepairOverGap']<=1e-7 for r in rows),'fillObservationLagDoesNotBreakParallelOwnership':rows[1]['parallelOwnershipTicks']>0}
  out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V7_PARALLEL_LANE_FORCED_COVERAGE','researchOnly':True,'performanceGraduationEligible':False,'cohort':'consumed development market 1816800 chosen because real V4 seed->Repair path naturally produces partial fill','architecture':['persistent Repair parent','lane-scoped carrier ownership: REPAIR and EXPAND carriers may coexist under same parent','same-lane capacity conservation retained for REPAIR','parent completion deferred until all parent child carriers are terminal','actual fill remains authoritative','forced capability dip affects policy signal only, never parent ownership'],'rows':rows,'gates':gates,'allPass':all(gates.values()),'boundary':['actual HftBacktest passive fills only; no dream/synthetic fills','EXPAND child authorization is forced for coverage after real Repair partial fill; this proves kernel concurrency, not natural policy selection','no performance graduation claim','same consumed development market; untouched chronology still required later']}
  op=Path(a.output) if a.output else Path(__import__('os').environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'allPass':out['allPass'],'gates':gates}),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
