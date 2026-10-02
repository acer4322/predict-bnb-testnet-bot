from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,importlib.util,os,math
from pathlib import Path
import numpy as np
import torch

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v10_continuous_repair_authority as v10
except ImportError:v10=sib('v10deadline','run_eth_repair_functional_exam_v10_continuous_repair_authority.py')
try:
 from tools import run_eth_repair_functional_exam_v9_conjunctive_parallel_router as v9
except ImportError:v9=sib('v9deadline','run_eth_repair_functional_exam_v9_conjunctive_parallel_router.py')
try:
 from tools import train_eth_persistent_repair_specialist_v4_selective_parallel_experts as v4t
except ImportError:v4t=sib('v4timingexpert','train_eth_persistent_repair_specialist_v4_selective_parallel_experts.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_repair_functional_exam_v1 as ex1
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9

class RepairDeadlineRuntime:
 def __init__(self,path,device='cpu'):
  ck=torch.load(path,map_location='cpu',weights_only=False);self.device=torch.device(device if device=='cuda' and torch.cuda.is_available() else 'cpu')
  self.mu,self.sd,self.gmu,self.gsd,self.tmu,self.tsd=[np.asarray(x,np.float32) for x in ck['stats']]
  self.model=v4t.SequenceExpert(v4t.MODES[v4t.REG]);self.model.load_state_dict(ck['models'][v4t.REG]);self.model.to(self.device).eval()
 def predict_eta_ms(self,s):
  c=((s['cur']-self.mu)/self.sd).astype(np.float32)[None,:];g=((s['graph']-self.gmu)/self.gsd).astype(np.float32)[None,:]
  def zs(x,m):return (((x-self.tmu)/self.tsd).astype(np.float32)*m[:,None])[None,:,:]
  A=zs(s['allseq'],s['allmask']);R=zs(s['repseq'],s['repmask']);E=zs(s['expseq'],s['expmask'])
  with torch.no_grad():
   z=self.model(torch.from_numpy(c).to(self.device),torch.from_numpy(g).to(self.device),torch.from_numpy(A).to(self.device),torch.from_numpy(R).to(self.device),torch.from_numpy(E).to(self.device))
   y=float(torch.sigmoid(z).item())
  eta=float(np.expm1(np.clip(y,0.,1.)*np.log1p(120000.0)))
  return eta,y

class DeadlineRepairSchedulerSim(v10.ContinuousRepairAuthoritySim):
 def __init__(self,*a,deadline=None,**kw):
  super().__init__(*a,**kw);self.deadline=deadline;self.deadlineDueMs=None;self.deadlineParentId=None;self.deadlineEventN=None;self.deadlineFired=False
  self.deadlineSchedules=0;self.deadlineReschedules=0;self.deadlineWaitTicks=0;self.deadlineNowTicks=0;self.deadlineFires=0;self.deadlineRepairSubmits=0;self.deadlineWaitToNowTransitions=0;self.deadlineEtaMs=[];self.deadlineNorm=[];self.deadlineParentPersistsDuringWaitTicks=0
 def _clear_deadline(self):
  self.deadlineDueMs=None;self.deadlineParentId=None;self.deadlineEventN=None;self.deadlineFired=False
 def _ensure_deadline(self,t,end,cs):
  if self.repairParent is None:return None
  pid=int(self.repairParent['id']);ev=int(self.capState.maker_n+self.capState.taker_n)
  need_new=self.deadlineDueMs is None or self.deadlineParentId!=pid
  need_reschedule=(not need_new and self.deadlineEventN is not None and ev!=self.deadlineEventN and not self.deadlineFired)
  if need_new or need_reschedule:
   eta,norm=self.deadline.predict_eta_ms(cs);self.deadlineDueMs=int(t+max(0.,eta));self.deadlineParentId=pid;self.deadlineEventN=ev;self.deadlineFired=False;self.deadlineEtaMs.append(float(eta));self.deadlineNorm.append(float(norm))
   if need_new:self.deadlineSchedules+=1
   else:self.deadlineReschedules+=1
  return self.deadlineDueMs
 def run_exam_v12(self,models,winner):
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
   if self.repairParent is None or weak is None:
    self._clear_deadline()
   roles=set();submitted=0
   if weak is not None and self.repairParent is not None:
    # A live Repair carrier already owns the work; scheduling is only for the next child when no Repair carrier is unresolved.
    if self.lane_unresolved('REPAIR'):
     self._clear_deadline()
    else:
     cs=self.capState.snapshot(t,end);due=self._ensure_deadline(t,end,cs)
     if due is not None and t<due:
      self.deadlineWaitTicks+=1;self.deadlineParentPersistsDuringWaitTicks+=int(self.repairParent is not None)
     else:
      self.deadlineNowTicks+=1
      if not self.deadlineFired:
       self.deadlineFired=True;self.deadlineFires+=1;self.deadlineWaitToNowTransitions+=int(self.deadlineWaitTicks>0)
      self.repairProposalTicks+=1
      if not global_active:self.repairProposalBelowActionGateTicks+=1
      p=float(qv[weak]['bid']);q=max(qty,1/p if p>EPS else 1e9);z=self.choose_authorized(t,end,weak,min(q,12.));ok=self._submit_authorized(t,qv,z,roles);submitted+=int(ok)
      if ok:
       self.deadlineRepairSubmits+=1
       if not global_active:self.repairSubmitsBelowActionGate+=1
       self._clear_deadline()
   if global_active and dom is not None:
    self.expandProposalTicks+=1;p=float(qv[dom]['bid']);q=max(qty,1/p if p>EPS else 1e9);z=self.choose_authorized(t,end,dom,min(q,12.));submitted+=int(self._submit_authorized(t,qv,z,roles))
   if weak is not None and dom is not None and global_active:self.dualProposalTicks+=1
   if submitted>=2:self.sameTickDualSubmit+=1
  end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2);self._apply_due_observations(end2+self.fillObsLagMs+self.ackReleaseLagMs+1);self._refresh_carrier_ledger(end2+self.fillObsLagMs+self.ackReleaseLagMs+1);self._reconcile_objective(end2+self.fillObsLagMs+self.ackReleaseLagMs+1)
  gross=sum(self.inv.values());ff=self.roleStableAtFill+self.repairToExpandAtFill+self.expandToRepairAtFill
  return {'pnlDiagnosticOnly':self.inv.get(str(winner).upper(),0.)-self.cost,'buyNotional':self.cost,'pairCoverage':2*min(self.inv.values())/gross if gross>EPS else 0.,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'actualFillEvents':self.actualFillEvents,'partialFillEvents':self.partialFillEvents,'lateFillAfterCancelEvents':self.lateFillAfterCancelEvents,'repairToExpandAtFirstFill':self.repairToExpandAtFill,'expandToRepairAtFirstFill':self.expandToRepairAtFill,'firstFillRoleEvents':ff,'acceptedSubmitWhileSameSideUnobservedFill':self.acceptedSubmitWhileSameSideUnobservedFill,'acceptedSubmitWithObservedTruthRoleMismatch':self.acceptedSubmitWithObservedTruthRoleMismatch,'maxUnobservedFillQty':self.maxUnobservedFillQty,'objectiveSwitches':self.objectiveSwitches,'objectiveCompletions':self.objectiveCompletions,'objectiveInvalidations':self.objectiveInvalidations,'authorizedSubmitWithTruthRoleMismatch':self.authorizedSubmitWithTruthRoleMismatch,'observedSubmitWithTruthRoleMismatchDiagnostic':self.acceptedSubmitWithObservedTruthRoleMismatch,'reauthBlocks':self.reauthBlocks,'globalOwnershipBlocks':self.globalOwnershipBlocks,'remainingCapBlocks':self.remainingCapBlocks,'carrierLedgerEntries':len(self.carrierLedger),'unresolvedCarrierCount':len(self.unresolved()),'unresolvedCarrierQty':self.outstanding_total(),'ambiguousOwnershipBlocks':self.ambiguousOwnershipBlocks,'incompatibleOwnershipBlocks':self.incompatibleOwnershipBlocks,'excessCarrierCancelRequests':self.excessCarrierCancelRequests,'maxOwnedRepairOverGap':self.maxOwnedRepairOverGap,'overOwnedSubmitViolations':self.overOwnedSubmitViolations,'repairParentBirths':self.repairParentBirths,'repairParentCompletions':self.repairParentCompletions,'repairParentInvalidations':self.repairParentInvalidations,'repairParentActiveAtEnd':int(self.repairParent is not None),'repairParentTicks':self.repairParentTicks,'repairParentPredBelowHalfTicks':self.repairParentPredBelowHalfTicks,'repairChildCommits':self.repairChildCommits,'expandChildCommitsUnderRepairParent':self.expandChildCommitsUnderRepairParent,'repairResumeAfterExpandCommits':self.repairResumeAfterExpandCommits,'parallelCapabilityTicks':sum(1 for p in self.capPred if p['repair_obligation_30s']>=.5 and p['expand_opportunity_30s']>=.5),'capabilityNoActionBlocks':self.capabilityNoActionBlocks,'parentCompletionDeferred':self.parentCompletionDeferred,'parallelChildCommitsWithRepairOutstanding':self.parallelChildCommitsWithRepairOutstanding,'laneOwnershipBlocks':self.laneOwnershipBlocks,'externalOwnershipBlocks':self.externalOwnershipBlocks,'parallelBudgetBlocks':self.parallelBudgetBlocks,'dualProposalTicks':self.dualProposalTicks,'repairProposalTicks':self.repairProposalTicks,'expandProposalTicks':self.expandProposalTicks,'sameTickDualSubmit':self.sameTickDualSubmit,'repairSupervisorTicks':self.repairSupervisorTicks,'repairParentBirthsBelowActionGate':self.repairParentBirthsBelowActionGate,'repairSubmitsBelowActionGate':self.repairSubmitsBelowActionGate,'repairProposalBelowActionGateTicks':self.repairProposalBelowActionGateTicks,'deadlineSchedules':self.deadlineSchedules,'deadlineReschedules':self.deadlineReschedules,'deadlineWaitTicks':self.deadlineWaitTicks,'deadlineNowTicks':self.deadlineNowTicks,'deadlineFires':self.deadlineFires,'deadlineRepairSubmits':self.deadlineRepairSubmits,'deadlineWaitToNowTransitions':self.deadlineWaitToNowTransitions,'deadlineParentPersistsDuringWaitTicks':self.deadlineParentPersistsDuringWaitTicks,'deadlineEtaMeanMs':float(np.mean(self.deadlineEtaMs)) if self.deadlineEtaMs else 0.,'deadlineEtaMinMs':min(self.deadlineEtaMs,default=0.),'deadlineEtaMaxMs':max(self.deadlineEtaMs,default=0.),'deadlineNormMean':float(np.mean(self.deadlineNorm)) if self.deadlineNorm else 0.}

def aggregate(rows):
 b=v10.aggregate(rows)
 for k in ['deadlineSchedules','deadlineReschedules','deadlineWaitTicks','deadlineNowTicks','deadlineFires','deadlineRepairSubmits','deadlineWaitToNowTransitions','deadlineParentPersistsDuringWaitTicks']:
  b[k]=int(sum(int(r.get(k) or 0) for r in rows))
 vals=[float(r.get('deadlineEtaMeanMs') or 0.) for r in rows if r.get('deadlineSchedules',0)+r.get('deadlineReschedules',0)>0]
 b['deadlineEtaMeanMs']=float(np.mean(vals)) if vals else 0.;b['deadlineEtaMinMs']=min((float(r.get('deadlineEtaMinMs') or 0.) for r in rows if r.get('deadlineSchedules',0)+r.get('deadlineReschedules',0)>0),default=0.);b['deadlineEtaMaxMs']=max((float(r.get('deadlineEtaMaxMs') or 0.) for r in rows),default=0.)
 return b

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--deadline-model',required=True);ap.add_argument('--markets',type=int,default=3);ap.add_argument('--market-ids',default='');ap.add_argument('--scenarios',default='CONTROL');ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v12_deadline_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];models,off1,off2=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');deadline=RepairDeadlineRuntime(a.deadline_model,'cpu');test=[r for r in cohort if r['split']!='TRAIN40']
  ids=[int(x) for x in a.market_ids.split(',') if x.strip()]
  if ids:
   by={int(r['marketId']):r for r in test};selected=[by[x] for x in ids if x in by]
   if len(selected)!=len(ids):raise RuntimeError(f'missing requested smoke market ids: requested={ids} found={[int(r["marketId"]) for r in selected]}')
  else:
   n=min(a.markets,len(test));idx=sorted(set(int(x) for x in np.linspace(0,len(test)-1,n)));selected=[test[i] for i in idx]
  names=[x.strip() for x in a.scenarios.split(',') if x.strip()];summaries={};allrows=[]
  for scenario in names:
   cfg=ex1.SCENARIOS[scenario];rows=[]
   for i,cr in enumerate(selected,1):
    sim=DeadlineRepairSchedulerSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,life,cfg['fillObsLagMs'],cfg['ackReleaseLagMs'],capability=cap,deadline=deadline)
    try:r=sim.run_exam_v12(models,cr['winner'])
    finally:sim.close()
    r.update({'scenario':scenario,'marketId':int(cr['marketId'])});rows.append(r);allrows.append(r)
    print(json.dumps({'scenario':scenario,'progress':i,'marketId':int(cr['marketId']),'schedules':r['deadlineSchedules'],'wait':r['deadlineWaitTicks'],'fires':r['deadlineFires'],'repairSubmits':r['deadlineRepairSubmits'],'drift':r['repairToExpandAtFirstFill'],'overOwned':r['overOwnedSubmitViolations']}),flush=True)
   s=aggregate(rows);s['pnl']=v10.v8.v7.v6.pnl_summary(rows);summaries[scenario]=s
  control=summaries.get('CONTROL')
  smoke={'processSemantics':'evaluated inside successful process','deadlineSchedulesNonzero':bool(control and control['deadlineSchedules']>0),'deadlineWaitNonzero':bool(control and control['deadlineWaitTicks']>0),'deadlineFiresNonzero':bool(control and control['deadlineFires']>0),'deadlineRepairSubmitsNonzero':bool(control and control['deadlineRepairSubmits']>0),'waitToNowTransitionNonzero':bool(control and control['deadlineWaitToNowTransitions']>0),'noRepairToExpand':bool(control and control['repairToExpandAtFirstFill']==0),'noAuthorizedTruthMismatch':bool(control and control['authorizedSubmitWithTruthRoleMismatch']==0),'noOverOwnedRepair':bool(control and control['overOwnedSubmitViolations']==0),'noUnresolvedCarrierQty':bool(control and abs(float(control['unresolvedCarrierQty']))<=1e-9)}
  verified=all(v for k,v in smoke.items() if k!='processSemantics')
  out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V12_DEADLINE_HAZARD_SCHEDULER','researchOnly':True,'performanceGraduationEligible':False,'cohort':'consumed development smoke only','selectedMarketIds':[int(r['marketId']) for r in selected],'architecture':['V10 continuous Repair parent authority retained','V4 time_to_next_repair expert converted to event-driven deadline ETA','deadline clock guarantees temporal progress from WAIT to NOW','Repair parent identity/ownership never gated by scheduler','Expand remains independent optional lane'],'scenarios':summaries,'smokeGates':smoke,'smokeVerified':verified,'rows':allrows,'boundary':['no dream fills','no model gradients','no HFT-PnL threshold tuning','smoke-before-scale: do not run 24x5 unless smokeVerified']};op=Path(a.output) if a.output else Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'smokeVerified':verified,'smokeGates':smoke,'control':control},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
