from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,importlib.util,os
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v10_continuous_repair_authority as v10
except ImportError:v10=sib('v10wait10','run_eth_repair_functional_exam_v10_continuous_repair_authority.py')
try:
 from tools import run_eth_repair_functional_exam_v9_conjunctive_parallel_router as v9
except ImportError:v9=sib('v9wait10','run_eth_repair_functional_exam_v9_conjunctive_parallel_router.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_repair_functional_exam_v1 as ex1
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9

class Wait10Runtime:
 def __init__(self,path):
  ck=joblib.load(path);self.model=ck['model'];self.threshold=float(ck['threshold']);self.features=list(ck['features'])
 def predict(self,cur):return float(self.model.predict_proba(np.asarray(cur,np.float32).reshape(1,-1))[0,1])

class AnchoredWait10Sim(v10.ContinuousRepairAuthoritySim):
 def __init__(self,*a,timing=None,**kw):
  super().__init__(*a,**kw);self.timing=timing;self.anchorDueMs=None;self.anchorParentId=None;self.anchorEventN=None;self.anchorWasWait=False
  self.timingAnchorSchedules=0;self.timingWait10Schedules=0;self.timingNowSchedules=0;self.timingAnchorReschedules=0;self.timingWaitTicks=0;self.timingDeadlineFires=0;self.timingRepairSubmits=0;self.timingWaitToNowTransitions=0;self.timingParentPersistsDuringWaitTicks=0;self.timingScores=[]
 def _clear_anchor(self):
  self.anchorDueMs=None;self.anchorParentId=None;self.anchorEventN=None;self.anchorWasWait=False
 def _ensure_anchor(self,t,cs):
  if self.repairParent is None:return None
  pid=int(self.repairParent['id']);ev=int(self.capState.maker_n+self.capState.taker_n)
  new=self.anchorDueMs is None or self.anchorParentId!=pid
  changed=(not new and self.anchorEventN is not None and ev!=self.anchorEventN)
  if new or changed:
   score=self.timing.predict(cs['cur']);wait=score>=self.timing.threshold;self.timingScores.append(score);self.timingAnchorSchedules+=1
   if changed:self.timingAnchorReschedules+=1
   if wait:self.timingWait10Schedules+=1;due=int(t+10000)
   else:self.timingNowSchedules+=1;due=int(t)
   self.anchorDueMs=due;self.anchorParentId=pid;self.anchorEventN=ev;self.anchorWasWait=wait
  return self.anchorDueMs
 def run_exam_v13(self,models,winner):
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
   ai=self.auth_inv();weak='UP' if ai['UP']<ai['DOWN']-EPS else 'DOWN' if ai['DOWN']<ai['UP']-EPS else None;dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
   cs=self.capState.snapshot(t,end)
   if weak is not None and self.repairParent is None:
    precap=self.capability.predict(cs);b0=self.repairParentBirths;self._maybe_birth_parent(t,weak,precap)
    if self.repairParentBirths>b0 and not global_active:self.repairParentBirthsBelowActionGate+=1
   if self.repairParent is None or weak is None:self._clear_anchor()
   roles=set();submitted=0
   if weak is not None and self.repairParent is not None:
    if self.lane_unresolved('REPAIR'):
     self._clear_anchor()
    else:
     cs=self.capState.snapshot(t,end);due=self._ensure_anchor(t,cs)
     if due is not None and t<due:
      self.timingWaitTicks+=1;self.timingParentPersistsDuringWaitTicks+=int(self.repairParent is not None)
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
   if weak is not None and dom is not None and global_active:self.dualProposalTicks+=1
   if submitted>=2:self.sameTickDualSubmit+=1
  end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2);self._apply_due_observations(end2+self.fillObsLagMs+self.ackReleaseLagMs+1);self._refresh_carrier_ledger(end2+self.fillObsLagMs+self.ackReleaseLagMs+1);self._reconcile_objective(end2+self.fillObsLagMs+self.ackReleaseLagMs+1)
  gross=sum(self.inv.values());ff=self.roleStableAtFill+self.repairToExpandAtFill+self.expandToRepairAtFill
  return {'pnlDiagnosticOnly':self.inv.get(str(winner).upper(),0.)-self.cost,'buyNotional':self.cost,'pairCoverage':2*min(self.inv.values())/gross if gross>EPS else 0.,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'actualFillEvents':self.actualFillEvents,'partialFillEvents':self.partialFillEvents,'lateFillAfterCancelEvents':self.lateFillAfterCancelEvents,'repairToExpandAtFirstFill':self.repairToExpandAtFill,'expandToRepairAtFirstFill':self.expandToRepairAtFill,'firstFillRoleEvents':ff,'acceptedSubmitWhileSameSideUnobservedFill':self.acceptedSubmitWhileSameSideUnobservedFill,'acceptedSubmitWithObservedTruthRoleMismatch':self.acceptedSubmitWithObservedTruthRoleMismatch,'maxUnobservedFillQty':self.maxUnobservedFillQty,'objectiveSwitches':self.objectiveSwitches,'objectiveCompletions':self.objectiveCompletions,'objectiveInvalidations':self.objectiveInvalidations,'authorizedSubmitWithTruthRoleMismatch':self.authorizedSubmitWithTruthRoleMismatch,'observedSubmitWithTruthRoleMismatchDiagnostic':self.acceptedSubmitWithObservedTruthRoleMismatch,'reauthBlocks':self.reauthBlocks,'globalOwnershipBlocks':self.globalOwnershipBlocks,'remainingCapBlocks':self.remainingCapBlocks,'carrierLedgerEntries':len(self.carrierLedger),'unresolvedCarrierCount':len(self.unresolved()),'unresolvedCarrierQty':self.outstanding_total(),'ambiguousOwnershipBlocks':self.ambiguousOwnershipBlocks,'incompatibleOwnershipBlocks':self.incompatibleOwnershipBlocks,'excessCarrierCancelRequests':self.excessCarrierCancelRequests,'maxOwnedRepairOverGap':self.maxOwnedRepairOverGap,'overOwnedSubmitViolations':self.overOwnedSubmitViolations,'repairParentBirths':self.repairParentBirths,'repairParentCompletions':self.repairParentCompletions,'repairParentInvalidations':self.repairParentInvalidations,'repairParentActiveAtEnd':int(self.repairParent is not None),'repairParentTicks':self.repairParentTicks,'repairParentPredBelowHalfTicks':self.repairParentPredBelowHalfTicks,'repairChildCommits':self.repairChildCommits,'expandChildCommitsUnderRepairParent':self.expandChildCommitsUnderRepairParent,'repairResumeAfterExpandCommits':self.repairResumeAfterExpandCommits,'parallelCapabilityTicks':sum(1 for p in self.capPred if p['repair_obligation_30s']>=.5 and p['expand_opportunity_30s']>=.5),'capabilityNoActionBlocks':self.capabilityNoActionBlocks,'parentCompletionDeferred':self.parentCompletionDeferred,'parallelChildCommitsWithRepairOutstanding':self.parallelChildCommitsWithRepairOutstanding,'laneOwnershipBlocks':self.laneOwnershipBlocks,'externalOwnershipBlocks':self.externalOwnershipBlocks,'parallelBudgetBlocks':self.parallelBudgetBlocks,'dualProposalTicks':self.dualProposalTicks,'repairProposalTicks':self.repairProposalTicks,'expandProposalTicks':self.expandProposalTicks,'sameTickDualSubmit':self.sameTickDualSubmit,'repairSupervisorTicks':self.repairSupervisorTicks,'repairParentBirthsBelowActionGate':self.repairParentBirthsBelowActionGate,'repairSubmitsBelowActionGate':self.repairSubmitsBelowActionGate,'repairProposalBelowActionGateTicks':self.repairProposalBelowActionGateTicks,'timingAnchorSchedules':self.timingAnchorSchedules,'timingWait10Schedules':self.timingWait10Schedules,'timingNowSchedules':self.timingNowSchedules,'timingAnchorReschedules':self.timingAnchorReschedules,'timingWaitTicks':self.timingWaitTicks,'timingDeadlineFires':self.timingDeadlineFires,'timingRepairSubmits':self.timingRepairSubmits,'timingWaitToNowTransitions':self.timingWaitToNowTransitions,'timingParentPersistsDuringWaitTicks':self.timingParentPersistsDuringWaitTicks,'timingScoreMean':float(np.mean(self.timingScores)) if self.timingScores else 0.,'timingScoreMin':min(self.timingScores,default=0.),'timingScoreMax':max(self.timingScores,default=0.)}

def aggregate(rows):
 b=v10.aggregate(rows)
 for k in ['timingAnchorSchedules','timingWait10Schedules','timingNowSchedules','timingAnchorReschedules','timingWaitTicks','timingDeadlineFires','timingRepairSubmits','timingWaitToNowTransitions','timingParentPersistsDuringWaitTicks']:
  b[k]=int(sum(int(r.get(k) or 0) for r in rows))
 vals=[]
 for r in rows:
  n=int(r.get('timingAnchorSchedules') or 0)
  if n:vals.extend([float(r.get('timingScoreMean') or 0.)]*n)
 b['timingScoreMean']=float(np.mean(vals)) if vals else 0.;b['timingScoreMin']=min((float(r.get('timingScoreMin') or 0.) for r in rows if r.get('timingAnchorSchedules',0)>0),default=0.);b['timingScoreMax']=max((float(r.get('timingScoreMax') or 0.) for r in rows),default=0.)
 return b

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--markets',type=int,default=3);ap.add_argument('--market-ids',default='');ap.add_argument('--scenarios',default='CONTROL');ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v13_wait10_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];models,off1,off2=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');timing=Wait10Runtime(a.timing_model);test=[r for r in cohort if r['split']!='TRAIN40']
  ids=[int(x) for x in a.market_ids.split(',') if x.strip()]
  if ids:
   by={int(r['marketId']):r for r in test};selected=[by[x] for x in ids if x in by]
   if len(selected)!=len(ids):raise RuntimeError(f'missing requested market ids: requested={ids} found={[int(r["marketId"]) for r in selected]}')
  else:
   n=min(a.markets,len(test));idx=sorted(set(int(x) for x in np.linspace(0,len(test)-1,n)));selected=[test[i] for i in idx]
  names=[x.strip() for x in a.scenarios.split(',') if x.strip()];summaries={};allrows=[]
  for scenario in names:
   cfg=ex1.SCENARIOS[scenario];rows=[]
   for i,cr in enumerate(selected,1):
    sim=AnchoredWait10Sim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,life,cfg['fillObsLagMs'],cfg['ackReleaseLagMs'],capability=cap,timing=timing)
    try:r=sim.run_exam_v13(models,cr['winner'])
    finally:sim.close()
    r.update({'scenario':scenario,'marketId':int(cr['marketId'])});rows.append(r);allrows.append(r)
    print(json.dumps({'scenario':scenario,'progress':i,'marketId':int(cr['marketId']),'anchors':r['timingAnchorSchedules'],'wait10':r['timingWait10Schedules'],'now':r['timingNowSchedules'],'fires':r['timingDeadlineFires'],'repairSubmits':r['timingRepairSubmits'],'pnl':r['pnlDiagnosticOnly'],'drift':r['repairToExpandAtFirstFill'],'overOwned':r['overOwnedSubmitViolations']}),flush=True)
   s=aggregate(rows);s['pnl']=v10.v8.v7.v6.pnl_summary(rows);summaries[scenario]=s
  control=summaries.get('CONTROL');g={'timingAnchorSchedulesNonzero':bool(control and control['timingAnchorSchedules']>0),'timingWait10SchedulesNonzero':bool(control and control['timingWait10Schedules']>0),'timingDeadlineFiresNonzero':bool(control and control['timingDeadlineFires']>0),'timingRepairSubmitsNonzero':bool(control and control['timingRepairSubmits']>0),'parentPersistsDuringWait':bool(control and control['timingParentPersistsDuringWaitTicks']>0),'noRepairToExpand':bool(control and control['repairToExpandAtFirstFill']==0),'noAuthorizedTruthMismatch':bool(control and control['authorizedSubmitWithTruthRoleMismatch']==0),'noOverOwnedRepair':bool(control and control['overOwnedSubmitViolations']==0),'noUnresolvedCarrierQty':bool(control and abs(float(control['unresolvedCarrierQty']))<=1e-9)};verified=all(g.values())
  out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V13_ANCHORED_WAIT10_SCHEDULER','researchOnly':True,'performanceGraduationEligible':False,'cohort':'consumed development only','selectedMarketIds':[int(r['marketId']) for r in selected],'timingThreshold':timing.threshold,'architecture':['V10 continuous Repair parent retained','frozen delay>10s expert evaluated only on material anchor','predicted long => one fixed 10s WAIT deadline','deadline expiry forces NOW','new material event re-anchors','Repair parent/ownership never gated by timing'],'scenarios':summaries,'smokeGates':g,'smokeVerified':verified,'rows':allrows,'boundary':['no model gradients','no threshold tuning','no dream fills','winner/PnL scoring only','smoke-before-scale']};op=Path(a.output) if a.output else Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'smokeVerified':verified,'smokeGates':g,'control':control},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
