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
except ImportError:v10=sib('v10timing','run_eth_repair_functional_exam_v10_continuous_repair_authority.py')
try:
 from tools import run_eth_repair_functional_exam_v9_conjunctive_parallel_router as v9
except ImportError:v9=sib('v9timing','run_eth_repair_functional_exam_v9_conjunctive_parallel_router.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_repair_functional_exam_v1 as ex1
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9

class TimingRuntime:
 def __init__(self,path):
  ck=joblib.load(path);self.model=ck['model'];self.threshold=float(ck['threshold']);self.features=list(ck['features'])
 def predict(self,cur):return float(self.model.predict_proba(np.asarray(cur,np.float32).reshape(1,-1))[0,1])

class TargetTimingSchedulerSim(v10.ContinuousRepairAuthoritySim):
 def __init__(self,*a,timing=None,**kw):
  super().__init__(*a,**kw);self.timing=timing;self.timingWaitTicks=0;self.timingRepairNowTicks=0;self.timingWaitWithoutRepairCarrierTicks=0;self.timingScores=[];self.timingRepairSubmits=0
 def run_exam_v11(self,models,winner):
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
   roles=set();submitted=0
   if weak is not None and self.repairParent is not None:
    score=self.timing.predict(cs['cur']);self.timingScores.append(score)
    wait=score>=self.timing.threshold
    if wait:
     self.timingWaitTicks+=1
     if not self.lane_unresolved('REPAIR'):self.timingWaitWithoutRepairCarrierTicks+=1
    else:
     self.timingRepairNowTicks+=1;self.repairProposalTicks+=1
     if not global_active:self.repairProposalBelowActionGateTicks+=1
     p=float(qv[weak]['bid']);q=max(qty,1/p if p>EPS else 1e9);z=self.choose_authorized(t,end,weak,min(q,12.));ok=self._submit_authorized(t,qv,z,roles);submitted+=int(ok)
     if ok:self.timingRepairSubmits+=1
     if ok and not global_active:self.repairSubmitsBelowActionGate+=1
   if global_active and dom is not None:
    self.expandProposalTicks+=1;p=float(qv[dom]['bid']);q=max(qty,1/p if p>EPS else 1e9);z=self.choose_authorized(t,end,dom,min(q,12.));submitted+=int(self._submit_authorized(t,qv,z,roles))
   if weak is not None and dom is not None and global_active:self.dualProposalTicks+=1
   if submitted>=2:self.sameTickDualSubmit+=1
  end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2);self._apply_due_observations(end2+self.fillObsLagMs+self.ackReleaseLagMs+1);self._refresh_carrier_ledger(end2+self.fillObsLagMs+self.ackReleaseLagMs+1);self._reconcile_objective(end2+self.fillObsLagMs+self.ackReleaseLagMs+1)
  gross=sum(self.inv.values());ff=self.roleStableAtFill+self.repairToExpandAtFill+self.expandToRepairAtFill
  return {'pnlDiagnosticOnly':self.inv.get(str(winner).upper(),0.)-self.cost,'buyNotional':self.cost,'pairCoverage':2*min(self.inv.values())/gross if gross>EPS else 0.,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'actualFillEvents':self.actualFillEvents,'partialFillEvents':self.partialFillEvents,'lateFillAfterCancelEvents':self.lateFillAfterCancelEvents,'repairToExpandAtFirstFill':self.repairToExpandAtFill,'expandToRepairAtFirstFill':self.expandToRepairAtFill,'firstFillRoleEvents':ff,'acceptedSubmitWhileSameSideUnobservedFill':self.acceptedSubmitWhileSameSideUnobservedFill,'authorizedSubmitWithTruthRoleMismatch':self.authorizedSubmitWithTruthRoleMismatch,'maxUnobservedFillQty':self.maxUnobservedFillQty,'objectiveCompletions':self.objectiveCompletions,'reauthBlocks':self.reauthBlocks,'remainingCapBlocks':self.remainingCapBlocks,'carrierLedgerEntries':len(self.carrierLedger),'unresolvedCarrierCount':len(self.unresolved()),'unresolvedCarrierQty':self.outstanding_total(),'maxOwnedRepairOverGap':self.maxOwnedRepairOverGap,'overOwnedSubmitViolations':self.overOwnedSubmitViolations,'repairParentBirths':self.repairParentBirths,'repairParentCompletions':self.repairParentCompletions,'repairParentActiveAtEnd':int(self.repairParent is not None),'repairParentTicks':self.repairParentTicks,'repairChildCommits':self.repairChildCommits,'expandChildCommitsUnderRepairParent':self.expandChildCommitsUnderRepairParent,'repairResumeAfterExpandCommits':self.repairResumeAfterExpandCommits,'parallelCapabilityTicks':sum(1 for p in self.capPred if p['repair_obligation_30s']>=.5 and p['expand_opportunity_30s']>=.5),'capabilityNoActionBlocks':self.capabilityNoActionBlocks,'parentCompletionDeferred':self.parentCompletionDeferred,'parallelChildCommitsWithRepairOutstanding':self.parallelChildCommitsWithRepairOutstanding,'laneOwnershipBlocks':self.laneOwnershipBlocks,'externalOwnershipBlocks':self.externalOwnershipBlocks,'parallelBudgetBlocks':self.parallelBudgetBlocks,'dualProposalTicks':self.dualProposalTicks,'repairProposalTicks':self.repairProposalTicks,'expandProposalTicks':self.expandProposalTicks,'sameTickDualSubmit':self.sameTickDualSubmit,'repairSupervisorTicks':self.repairSupervisorTicks,'repairParentBirthsBelowActionGate':self.repairParentBirthsBelowActionGate,'repairSubmitsBelowActionGate':self.repairSubmitsBelowActionGate,'repairProposalBelowActionGateTicks':self.repairProposalBelowActionGateTicks,'timingWaitTicks':self.timingWaitTicks,'timingRepairNowTicks':self.timingRepairNowTicks,'timingWaitWithoutRepairCarrierTicks':self.timingWaitWithoutRepairCarrierTicks,'timingRepairSubmits':self.timingRepairSubmits,'timingScoreMean':float(np.mean(self.timingScores)) if self.timingScores else 0.,'timingScoreMax':max(self.timingScores,default=0.)}

def aggregate(rows):
 b=v10.aggregate(rows)
 for k in ['timingWaitTicks','timingRepairNowTicks','timingWaitWithoutRepairCarrierTicks','timingRepairSubmits']:b[k]=int(sum(int(r.get(k) or 0) for r in rows))
 b['timingScoreMean']=float(np.mean([r.get('timingScoreMean',0.) for r in rows if r.get('timingWaitTicks',0)+r.get('timingRepairNowTicks',0)>0])) if any(r.get('timingWaitTicks',0)+r.get('timingRepairNowTicks',0)>0 for r in rows) else 0.
 b['timingScoreMax']=max((float(r.get('timingScoreMax') or 0.) for r in rows),default=0.)
 return b

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--markets',type=int,default=24);ap.add_argument('--scenarios',default='CONTROL');ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v11_timing_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];models,off1,off2=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');timing=TimingRuntime(a.timing_model);test=[r for r in cohort if r['split']!='TRAIN40'];n=min(a.markets,len(test));idx=sorted(set(int(x) for x in np.linspace(0,len(test)-1,n)));selected=[test[i] for i in idx];names=[x.strip() for x in a.scenarios.split(',') if x.strip()];summaries={};allrows=[]
  for scenario in names:
   cfg=ex1.SCENARIOS[scenario];rows=[]
   for i,cr in enumerate(selected,1):
    sim=TargetTimingSchedulerSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,life,cfg['fillObsLagMs'],cfg['ackReleaseLagMs'],capability=cap,timing=timing)
    try:r=sim.run_exam_v11(models,cr['winner'])
    finally:sim.close()
    r.update({'scenario':scenario,'marketId':int(cr['marketId']),'chronologyIndex':idx[i-1]});rows.append(r);allrows.append(r)
    if i%8==0:print(json.dumps({'scenario':scenario,'progress':i,'drift':sum(x['repairToExpandAtFirstFill'] for x in rows),'overOwned':sum(x['overOwnedSubmitViolations'] for x in rows),'birthsBelowGate':sum(x['repairParentBirthsBelowActionGate'] for x in rows),'waitTicks':sum(x['timingWaitTicks'] for x in rows),'waitNoCarrier':sum(x['timingWaitWithoutRepairCarrierTicks'] for x in rows)}),flush=True)
   s=aggregate(rows);s['pnl']=v10.v8.v7.v6.pnl_summary(rows);summaries[scenario]=s
  control=summaries.get('CONTROL');gates={'allScenariosNoRepairToExpand':all(s['repairToExpandAtFirstFill']==0 for s in summaries.values()),'allScenariosNoAuthorizedTruthRoleMismatch':all(s['authorizedSubmitWithTruthRoleMismatch']==0 for s in summaries.values()),'allScenariosNoOverOwnedRepairSubmit':all(s['overOwnedSubmitViolations']==0 for s in summaries.values()),'allScenariosNoUnresolvedCarrierQty':all(abs(float(s['unresolvedCarrierQty']))<=1e-9 for s in summaries.values()),'repairParentCanComplete':bool(control and control['repairParentCompletions']>0),'continuousRepairParentStillBypassesOldGate':bool(control and control['repairParentBirthsBelowActionGate']>0),'timingWaitActivates':bool(control and control['timingWaitTicks']>0)}
  out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V11_TARGET_TIMING_SCHEDULER','researchOnly':True,'performanceGraduationEligible':False,'cohort':'consumed development evidence only','timingThreshold':timing.threshold,'architecture':['V10 continuous Repair parent/supervision','V9 conjunctive Repair/Expand authority','Target timing expert gates Repair child NOW vs WAIT only','Repair parent/ownership never gated by timing expert'],'scenarios':summaries,'gates':gates,'allPass':all(gates.values()),'rows':allrows,'boundary':['no dream fills','no new model gradients','timing threshold frozen before HFT','winner/PnL scoring only','consumed development cohort only']};op=Path(a.output) if a.output else Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'allPass':out['allPass'],'gates':gates,'control':control},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
