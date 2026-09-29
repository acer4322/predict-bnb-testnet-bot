from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,importlib.util
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v9_conjunctive_parallel_router as v9
except ImportError:v9=sib('v9_cont','run_eth_repair_functional_exam_v9_conjunctive_parallel_router.py')
try:
 from tools import run_eth_repair_functional_exam_v8_dual_lane_proposals as v8
except ImportError:v8=sib('v8_cont','run_eth_repair_functional_exam_v8_dual_lane_proposals.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_repair_functional_exam_v1 as ex1
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9

class ContinuousRepairAuthoritySim(v8.DualLaneProposalSim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.repairSupervisorTicks=0;self.repairParentBirthsBelowActionGate=0;self.repairSubmitsBelowActionGate=0;self.repairProposalBelowActionGateTicks=0
 def run_exam_v10(self,models,winner):
  ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(self.meta['firstReceivedMs']);v1.ex.advance_to(self.bt,first);end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
  for u in ups:
   t=int(u[1]);v1.ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);ca=v1.apply(self.book,u);qv=v1.quotes(self.book)
   if not qv:continue
   if self.firstValid is None:self.firstValid=t
   if (end-t)/1000.<=180:continue
   self.seed_if_needed(t,qv)
   if not self.seeded:continue
   x=self.features(t,qv,ca,end).reshape(1,-1);pa=float(models['action'].predict_proba(x)[0,1]);global_active=pa>=models['actionTh'];qty=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))));qty=min(qty,12.)
   # Continuous deterministic repair supervision is no longer behind the old global action gate.
   self.repairSupervisorTicks+=1;self._refresh_carrier_ledger(t);self._reconcile_objective(t);self._continuous_capacity_reconcile(t)
   ai=self.auth_inv();weak='UP' if ai['UP']<ai['DOWN']-EPS else 'DOWN' if ai['DOWN']<ai['UP']-EPS else None;dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
   if weak is not None and self.repairParent is None:
    cs=self.capState.snapshot(t,end);precap=self.capability.predict(cs);b0=self.repairParentBirths;self._maybe_birth_parent(t,weak,precap)
    if self.repairParentBirths>b0 and not global_active:self.repairParentBirthsBelowActionGate+=1
   roles=set();submitted=0
   # Required Repair lane runs whenever a Repair parent exists, independent of global activity probability.
   if weak is not None and self.repairParent is not None:
    self.repairProposalTicks+=1
    if not global_active:self.repairProposalBelowActionGateTicks+=1
    p=float(qv[weak]['bid']);q=max(qty,1/p if p>EPS else 1e9);z=self.choose_authorized(t,end,weak,min(q,12.));ok=self._submit_authorized(t,qv,z,roles);submitted+=int(ok)
    if ok and not global_active:self.repairSubmitsBelowActionGate+=1
   # Optional Expand remains behind the legacy/global action-intent gate.
   if global_active and dom is not None:
    self.expandProposalTicks+=1;p=float(qv[dom]['bid']);q=max(qty,1/p if p>EPS else 1e9);z=self.choose_authorized(t,end,dom,min(q,12.));submitted+=int(self._submit_authorized(t,qv,z,roles))
   if weak is not None and dom is not None and global_active:self.dualProposalTicks+=1
   if submitted>=2:self.sameTickDualSubmit+=1
  end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2);self._apply_due_observations(end2+self.fillObsLagMs+self.ackReleaseLagMs+1);self._refresh_carrier_ledger(end2+self.fillObsLagMs+self.ackReleaseLagMs+1);self._reconcile_objective(end2+self.fillObsLagMs+self.ackReleaseLagMs+1)
  gross=sum(self.inv.values());ff=self.roleStableAtFill+self.repairToExpandAtFill+self.expandToRepairAtFill
  return {'pnlDiagnosticOnly':self.inv.get(str(winner).upper(),0.)-self.cost,'buyNotional':self.cost,'pairCoverage':2*min(self.inv.values())/gross if gross>EPS else 0.,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'actualFillEvents':self.actualFillEvents,'partialFillEvents':self.partialFillEvents,'lateFillAfterCancelEvents':self.lateFillAfterCancelEvents,'repairToExpandAtFirstFill':self.repairToExpandAtFill,'expandToRepairAtFirstFill':self.expandToRepairAtFill,'firstFillRoleEvents':ff,'acceptedSubmitWhileSameSideUnobservedFill':self.acceptedSubmitWhileSameSideUnobservedFill,'authorizedSubmitWithTruthRoleMismatch':self.authorizedSubmitWithTruthRoleMismatch,'maxUnobservedFillQty':self.maxUnobservedFillQty,'objectiveCompletions':self.objectiveCompletions,'reauthBlocks':self.reauthBlocks,'remainingCapBlocks':self.remainingCapBlocks,'carrierLedgerEntries':len(self.carrierLedger),'unresolvedCarrierCount':len(self.unresolved()),'unresolvedCarrierQty':self.outstanding_total(),'maxOwnedRepairOverGap':self.maxOwnedRepairOverGap,'overOwnedSubmitViolations':self.overOwnedSubmitViolations,'repairParentBirths':self.repairParentBirths,'repairParentCompletions':self.repairParentCompletions,'repairParentActiveAtEnd':int(self.repairParent is not None),'repairParentTicks':self.repairParentTicks,'repairChildCommits':self.repairChildCommits,'expandChildCommitsUnderRepairParent':self.expandChildCommitsUnderRepairParent,'repairResumeAfterExpandCommits':self.repairResumeAfterExpandCommits,'parallelCapabilityTicks':sum(1 for p in self.capPred if p['repair_obligation_30s']>=.5 and p['expand_opportunity_30s']>=.5),'capabilityNoActionBlocks':self.capabilityNoActionBlocks,'parentCompletionDeferred':self.parentCompletionDeferred,'parallelChildCommitsWithRepairOutstanding':self.parallelChildCommitsWithRepairOutstanding,'laneOwnershipBlocks':self.laneOwnershipBlocks,'externalOwnershipBlocks':self.externalOwnershipBlocks,'parallelBudgetBlocks':self.parallelBudgetBlocks,'dualProposalTicks':self.dualProposalTicks,'repairProposalTicks':self.repairProposalTicks,'expandProposalTicks':self.expandProposalTicks,'sameTickDualSubmit':self.sameTickDualSubmit,'repairSupervisorTicks':self.repairSupervisorTicks,'repairParentBirthsBelowActionGate':self.repairParentBirthsBelowActionGate,'repairSubmitsBelowActionGate':self.repairSubmitsBelowActionGate,'repairProposalBelowActionGateTicks':self.repairProposalBelowActionGateTicks}

def aggregate(rows):
 b=v8.agg(rows)
 for k in ['repairSupervisorTicks','repairParentBirthsBelowActionGate','repairSubmitsBelowActionGate','repairProposalBelowActionGateTicks']:b[k]=int(sum(int(r.get(k) or 0) for r in rows))
 return b

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--markets',type=int,default=24);ap.add_argument('--scenarios',default='CONTROL');ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v10_cont_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];models,off1,off2=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');test=[r for r in cohort if r['split']!='TRAIN40'];n=min(a.markets,len(test));idx=sorted(set(int(x) for x in np.linspace(0,len(test)-1,n)));selected=[test[i] for i in idx];names=[x.strip() for x in a.scenarios.split(',') if x.strip()];summaries={};allrows=[]
  for scenario in names:
   cfg=ex1.SCENARIOS[scenario];rows=[]
   for i,cr in enumerate(selected,1):
    sim=ContinuousRepairAuthoritySim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,life,cfg['fillObsLagMs'],cfg['ackReleaseLagMs'],capability=cap)
    try:r=sim.run_exam_v10(models,cr['winner'])
    finally:sim.close()
    r.update({'scenario':scenario,'marketId':int(cr['marketId']),'chronologyIndex':idx[i-1]});rows.append(r);allrows.append(r)
    if i%8==0:print(json.dumps({'scenario':scenario,'progress':i,'repairToExpand':sum(x['repairToExpandAtFirstFill'] for x in rows),'overOwned':sum(x['overOwnedSubmitViolations'] for x in rows),'birthsBelowGate':sum(x['repairParentBirthsBelowActionGate'] for x in rows),'repairSubmitsBelowGate':sum(x['repairSubmitsBelowActionGate'] for x in rows),'expandUnderParent':sum(x['expandChildCommitsUnderRepairParent'] for x in rows)}),flush=True)
   s=aggregate(rows);s['pnl']=v8.v7.v6.pnl_summary(rows);summaries[scenario]=s
  control=summaries.get('CONTROL');gates={'allScenariosNoRepairToExpand':all(s['repairToExpandAtFirstFill']==0 for s in summaries.values()),'allScenariosNoAuthorizedTruthRoleMismatch':all(s['authorizedSubmitWithTruthRoleMismatch']==0 for s in summaries.values()),'allScenariosNoOverOwnedRepairSubmit':all(s['overOwnedSubmitViolations']==0 for s in summaries.values()),'allScenariosNoUnresolvedCarrierQty':all(abs(float(s['unresolvedCarrierQty']))<=1e-9 for s in summaries.values()),'repairParentCanComplete':bool(control and control['repairParentCompletions']>0),'continuousRepairActuallyBypassesOldGate':bool(control and (control['repairParentBirthsBelowActionGate']>0 or control['repairSubmitsBelowActionGate']>0))}
  out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V10_CONTINUOUS_REPAIR_AUTHORITY','researchOnly':True,'performanceGraduationEligible':False,'cohort':'consumed development evidence only','architecture':['V9 conjunctive Repair/Expand authority retained','Repair supervision and Repair proposal removed from legacy global action gate','optional Expand remains behind global action gate','V4/V7 persistent carrier and lane ownership unchanged'],'scenarios':summaries,'gates':gates,'allPass':all(gates.values()),'rows':allrows,'boundary':['No dream fills','No new model gradients','No threshold lowering','Winner/PnL scoring only','Consumed cohort only; any later promotion requires newly collected chronology']};op=Path(a.output) if a.output else Path(__import__('os').environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'allPass':out['allPass'],'gates':gates,'control':control},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
