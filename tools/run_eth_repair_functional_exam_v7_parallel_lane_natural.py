from __future__ import annotations
import argparse,json,statistics,tempfile,zipfile,shutil,sys,joblib,importlib.util
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v6_unified_parent as v6
except ImportError:v6=sib('v6_natural','run_eth_repair_functional_exam_v6_unified_parent.py')
try:
 from tools import run_eth_repair_functional_exam_v4_persistent_carrier_ledger as v4
except ImportError:v4=sib('v4_natural','run_eth_repair_functional_exam_v4_persistent_carrier_ledger.py')
try:
 from tools import eth_persistent_repair_online_capability_runtime as caprt
except ImportError:caprt=sib('caprt_natural','eth_persistent_repair_online_capability_runtime.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_repair_functional_exam_v1 as ex1
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9

class NaturalParallelLaneSim(v6.UnifiedRepairParentSim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.repairLaneObjective=None;self.expandLaneObjective=None;self.standaloneObjective=None;self.parentCompletionDeferred=0;self.parallelChildCommitsWithRepairOutstanding=0;self.laneOwnershipBlocks=0;self.externalOwnershipBlocks=0;self.parallelBudgetBlocks=0
 def submit(self,t,side,p,q):
  n0=self.n;pid=getattr(self,'_pendingParentId',None);lane=getattr(self,'_pendingLane',None);ok=super().submit(t,side,p,q)
  if ok:
   key=f'{side}_{n0}'
   if key in self.carrierLedger:self.carrierLedger[key]['parentId']=pid;self.carrierLedger[key]['lane']=lane or self.carrierLedger[key].get('objectiveRole')
  self._pendingParentId=None;self._pendingLane=None
  return ok
 def parent_unresolved(self):
  if not self.repairParent:return []
  pid=self.repairParent['id'];return [(k,e,r) for k,e,r in self.unresolved() if e.get('parentId')==pid]
 def lane_unresolved(self,lane):return [(k,e,r) for k,e,r in self.parent_unresolved() if e.get('lane')==lane]
 def external_unresolved(self):
  pid=self.repairParent['id'] if self.repairParent else None;return [(k,e,r) for k,e,r in self.unresolved() if e.get('parentId')!=pid]
 def _maybe_birth_parent(self,t,weak,cap):
  if self.repairParent is None and weak is not None and float(cap['repair_obligation_30s'])>=0.5:
   self.repairParent={'id':self.nextRepairParentId,'side':weak,'bornAt':int(t)};self.nextRepairParentId+=1;self.repairParentBirths+=1;self.repairLaneObjective=self._new_objective('REPAIR',weak);self.expandLaneObjective=None;self.standaloneObjective=None
 def _complete_parent_if_structural(self,t,ai):
  p=self.repairParent
  if not p:return
  side=p['side'];opp='DOWN' if side=='UP' else 'UP';structural=side is None or float(ai[side])>=float(ai[opp])-EPS
  if not structural:return
  if self.parent_unresolved():self.parentCompletionDeferred+=1;return
  if side is not None and float(ai[side])>float(ai[opp])+EPS:self.repairParentInvalidations+=1
  self.repairParentCompletions+=1;self.repairParent=None;self.repairLaneObjective=None;self.expandLaneObjective=None;self.activeObjective=None;self.awaitingReentry=True
 def _reconcile_objective(self,t):
  if self.repairParent:self._complete_parent_if_structural(t,self.auth_inv())
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
 def choose_authorized(self,t,end,proposed_side,proposed_qty):
  cur,seq,mask,debt,lag=self.lifecycle_features(t,end);oldp=self.lifecycle.predict(cur,seq,mask,debt,lag);self.lifecyclePredictions.append(oldp);self._refresh_carrier_ledger(t);self._reconcile_objective(t);self._continuous_capacity_reconcile(t)
  ai=self.auth_inv();weak='UP' if ai['UP']<ai['DOWN']-EPS else 'DOWN' if ai['DOWN']<ai['UP']-EPS else None;dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None;cs=self.capState.snapshot(t,end);cap=self.capability.predict(cs);self.capPred.append(cap);self._maybe_birth_parent(t,weak,cap);parent=self.repairParent
  if parent:
   self.repairParentTicks+=1
   if float(cap['repair_obligation_30s'])<0.5:self.repairParentPredBelowHalfTicks+=1
   self._complete_parent_if_structural(t,ai);parent=self.repairParent
  chosen_role=None;chosen_side=None;oid=None;qty=float(proposed_qty)
  if parent:
   if self.external_unresolved():self.externalOwnershipBlocks+=1;return None
   repair_available=weak is not None and parent['side']==weak
   expand_available=dom is not None and float(cap['expand_opportunity_30s'])>=0.5
   both=repair_available and expand_available and float(cap['both_responsibilities_30s'])>=0.5
   if both and proposed_side==dom:
    chosen_role='EXPAND';chosen_side=dom
    if self.expandLaneObjective is None:self.expandLaneObjective=self._new_objective('EXPAND',dom)
    oid=self.expandLaneObjective['id']
    if self.lane_unresolved('EXPAND'):self.laneOwnershipBlocks+=1;return None
    gap=max(0.,abs(float(ai['UP'])-float(ai['DOWN'])));qty=min(qty,gap)
    if qty<=EPS:self.parallelBudgetBlocks+=1;return None
   elif repair_available:
    chosen_role='REPAIR';chosen_side=weak;oid=self.repairLaneObjective['id']
    if self.lane_unresolved('REPAIR'):self.laneOwnershipBlocks+=1;return None
    gap=max(0.,abs(float(ai['UP'])-float(ai['DOWN'])));owned=0.;remaining=max(0.,gap-owned);qty=min(qty,remaining)
    if qty<=EPS:self.remainingCapBlocks+=1;return None
   else:self.capabilityNoActionBlocks+=1;return None
  else:
   if self.outstanding_total()>EPS:self.externalOwnershipBlocks+=1;return None
   if dom is not None and float(cap['expand_opportunity_30s'])>=0.5 and float(cap['activity_urgency_30s'])>=0.5:
    if self.awaitingReentry and oldp['reentry']<0.5:self.reauthBlocks+=1;return None
    self.awaitingReentry=False;chosen_role='EXPAND';chosen_side=dom
    if self.standaloneObjective is None:self.standaloneObjective=self._new_objective('EXPAND',dom)
    oid=self.standaloneObjective['id']
   else:self.capabilityNoActionBlocks+=1;return None
  if chosen_side is None or qty<=EPS:return None
  if chosen_role=='REPAIR':
   self.repairChildCommits+=1
   if self.lastCommittedChildRole=='EXPAND':self.repairResumeAfterExpandCommits+=1
  elif parent:
   self.expandChildCommitsUnderRepairParent+=1
   if self.lane_unresolved('REPAIR'):self.parallelChildCommitsWithRepairOutstanding+=1
  self.lastCommittedChildRole=chosen_role
  return chosen_side,qty,oldp,chosen_role,oid
 def run_exam_v7(self,models,winner):
  ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(self.meta['firstReceivedMs']);v1.ex.advance_to(self.bt,first);end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
  for u in ups:
   t=int(u[1]);v1.ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);ca=v1.apply(self.book,u);qv=v1.quotes(self.book)
   if not qv:continue
   if self.firstValid is None:self.firstValid=t
   if (end-t)/1000.<=180:continue
   self.seed_if_needed(t,qv)
   if not self.seeded:continue
   x=self.features(t,qv,ca,end).reshape(1,-1);pa=float(models['action'].predict_proba(x)[0,1])
   if pa<models['actionTh']:continue
   ps=float(models['side'].predict_proba(x)[0,1]);prop='UP' if ps>=models['sideTh'] else 'DOWN';qty=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))));p0=float(qv[prop]['bid']);qty=max(qty,1/p0);qty=min(qty,12.)
   z=self.choose_authorized(t,end,prop,qty)
   if z is None:continue
   side,qty,_,role,oid=z;p=float(qv[side]['bid']);legal=1/p if p>EPS else 1e9
   # Respect lane capacity. Never round a lane above its authorized budget merely to satisfy venue minimum.
   if qty<legal-EPS:
    if role=='REPAIR':self.remainingCapBlocks+=1
    else:self.parallelBudgetBlocks+=1
    continue
   qty=min(max(qty,legal),12.);self._pendingAuthorizedRole=role;self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=self.repairParent['id'] if self.repairParent else None;self._pendingLane=role;self.submit(t,side,p,qty)
  end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2);self._apply_due_observations(end2+self.fillObsLagMs+self.ackReleaseLagMs+1);self._refresh_carrier_ledger(end2+self.fillObsLagMs+self.ackReleaseLagMs+1);self._reconcile_objective(end2+self.fillObsLagMs+self.ackReleaseLagMs+1)
  gross=sum(self.inv.values());ff=self.roleStableAtFill+self.repairToExpandAtFill+self.expandToRepairAtFill
  return {'pnlDiagnosticOnly':self.inv.get(str(winner).upper(),0.)-self.cost,'buyNotional':self.cost,'pairCoverage':2*min(self.inv.values())/gross if gross>EPS else 0.,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'fillsObserved':self.fills,'actualFillEvents':self.actualFillEvents,'partialFillEvents':self.partialFillEvents,'lateFillAfterCancelEvents':self.lateFillAfterCancelEvents,'repairToExpandAtFirstFill':self.repairToExpandAtFill,'expandToRepairAtFirstFill':self.expandToRepairAtFill,'roleStableAtFirstFill':self.roleStableAtFill,'firstFillRoleEvents':ff,'repairToExpandRate':self.repairToExpandAtFill/ff if ff else 0.,'acceptedSubmitWhileSameSideUnobservedFill':self.acceptedSubmitWhileSameSideUnobservedFill,'acceptedSubmitWithObservedTruthRoleMismatch':self.acceptedSubmitWithObservedTruthRoleMismatch,'maxUnobservedFillQty':self.maxUnobservedFillQty,'obsDebtClears':self.obsDebtClears,'duplicateBlocked':self.duplicateBlocked,'localPendingBlocked':self.localPendingBlocked,'objectiveSwitches':self.objectiveSwitches,'objectiveCompletions':self.objectiveCompletions,'objectiveInvalidations':self.objectiveInvalidations,'authorizedSubmitWithTruthRoleMismatch':self.authorizedSubmitWithTruthRoleMismatch,'observedSubmitWithTruthRoleMismatchDiagnostic':self.acceptedSubmitWithObservedTruthRoleMismatch,'reauthBlocks':self.reauthBlocks,'globalOwnershipBlocks':self.globalOwnershipBlocks,'remainingCapBlocks':self.remainingCapBlocks,'carrierLedgerEntries':len(self.carrierLedger),'unresolvedCarrierCount':len(self.unresolved()),'unresolvedCarrierQty':self.outstanding_total(),'ambiguousOwnershipBlocks':self.ambiguousOwnershipBlocks,'incompatibleOwnershipBlocks':self.incompatibleOwnershipBlocks,'excessCarrierCancelRequests':self.excessCarrierCancelRequests,'maxOwnedRepairOverGap':self.maxOwnedRepairOverGap,'overOwnedSubmitViolations':self.overOwnedSubmitViolations,'repairParentBirths':self.repairParentBirths,'repairParentCompletions':self.repairParentCompletions,'repairParentInvalidations':self.repairParentInvalidations,'repairParentActiveAtEnd':int(self.repairParent is not None),'repairParentTicks':self.repairParentTicks,'repairParentPredBelowHalfTicks':self.repairParentPredBelowHalfTicks,'repairChildCommits':self.repairChildCommits,'expandChildCommitsUnderRepairParent':self.expandChildCommitsUnderRepairParent,'repairResumeAfterExpandCommits':self.repairResumeAfterExpandCommits,'parallelCapabilityTicks':sum(1 for p in self.capPred if p['repair_obligation_30s']>=.5 and p['expand_opportunity_30s']>=.5),'capabilityNoActionBlocks':self.capabilityNoActionBlocks,'parentCompletionDeferred':self.parentCompletionDeferred,'parallelChildCommitsWithRepairOutstanding':self.parallelChildCommitsWithRepairOutstanding,'laneOwnershipBlocks':self.laneOwnershipBlocks,'externalOwnershipBlocks':self.externalOwnershipBlocks,'parallelBudgetBlocks':self.parallelBudgetBlocks}

def aggregate(rows):
 b=v6.aggregate(rows)
 for k in ['parentCompletionDeferred','parallelChildCommitsWithRepairOutstanding','laneOwnershipBlocks','externalOwnershipBlocks','parallelBudgetBlocks']:b[k]=int(sum(int(r.get(k) or 0) for r in rows))
 return b

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--output');ap.add_argument('--markets',type=int,default=24);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v7_natural_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];models,off1,off2=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=caprt.UnifiedCapabilityRuntime(a.capability_model,'cpu');test=[r for r in cohort if r['split']!='TRAIN40'];n=min(a.markets,len(test));idx=sorted(set(int(x) for x in np.linspace(0,len(test)-1,n)));selected=[test[i] for i in idx];summaries={};allrows=[]
  for scenario,cfg in ex1.SCENARIOS.items():
   rows=[]
   for i,cr in enumerate(selected,1):
    sim=NaturalParallelLaneSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,life,cfg['fillObsLagMs'],cfg['ackReleaseLagMs'],capability=cap)
    try:r=sim.run_exam_v7(models,cr['winner'])
    finally:sim.close()
    r.update({'scenario':scenario,'marketId':int(cr['marketId']),'chronologyIndex':idx[i-1]});rows.append(r);allrows.append(r)
    if i%8==0:print(json.dumps({'scenario':scenario,'progress':i,'repairToExpand':sum(x['repairToExpandAtFirstFill'] for x in rows),'overOwned':sum(x['overOwnedSubmitViolations'] for x in rows),'parallelChild':sum(x['parallelChildCommitsWithRepairOutstanding'] for x in rows),'expandUnderParent':sum(x['expandChildCommitsUnderRepairParent'] for x in rows),'repairResumes':sum(x['repairResumeAfterExpandCommits'] for x in rows)}),flush=True)
   s=aggregate(rows);s['pnl']=v6.pnl_summary(rows);summaries[scenario]=s
  control=summaries['CONTROL'];gates={'allScenariosNoRepairToExpand':all(s['repairToExpandAtFirstFill']==0 for s in summaries.values()),'allScenariosNoAuthorizedTruthRoleMismatch':all(s['authorizedSubmitWithTruthRoleMismatch']==0 for s in summaries.values()),'allScenariosNoOverOwnedRepairSubmit':all(s['overOwnedSubmitViolations']==0 for s in summaries.values()),'allScenariosNoUnresolvedCarrierQty':all(abs(float(s['unresolvedCarrierQty']))<=1e-9 for s in summaries.values()),'repairParentCanComplete':control['repairParentCompletions']>0,'ackLagDoesNotCollapseActivity':summaries['ACK_RELEASE_3000']['meanSubmits']>=0.5*max(control['meanSubmits'],EPS),'compoundDoesNotCollapseActivity':summaries['COMPOUND_3000']['meanSubmits']>=0.5*max(control['meanSubmits'],EPS)}
  out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V7_PARALLEL_LANE_NATURAL','researchOnly':True,'performanceGraduationEligible':False,'cohort':'consumed Fresh101 24-market closed-loop development cohort','architecture':['V4 persistent per-carrier ownership retained','unified Repair parent retained','REPAIR and EXPAND are lane-scoped child objectives under same parent','both_responsibilities capability authorizes coexistence directly; no categorical switch required inside same parent','Repair lane capacity remains bounded by authoritative gap','EXPAND-under-parent budget capped by current authoritative gap','parent completion waits for all parent carriers terminal'],'round1Offline':off1,'round2Offline':off2,'scenarios':summaries,'gates':gates,'allPass':all(gates.values()),'naturalCoverage':{'controlParallelChildCommitsWithRepairOutstanding':control['parallelChildCommitsWithRepairOutstanding'],'controlExpandChildCommitsUnderRepairParent':control['expandChildCommitsUnderRepairParent'],'controlRepairResumes':control['repairResumeAfterExpandCommits'],'controlParallelCapabilityTicks':control['parallelCapabilityTicks']},'rows':allrows,'boundary':['No dream fills','No forced child authorization in this natural-policy exam','Same consumed development cohort; not graduation','Forced V7 coverage pass is separate evidence of kernel concurrency','Untouched chronology later than 1823545 required before promotion']}
  op=Path(a.output) if a.output else Path(__import__('os').environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'allPass':out['allPass'],'gates':gates,'naturalCoverage':out['naturalCoverage'],'controlPnl':control['pnl']}),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
