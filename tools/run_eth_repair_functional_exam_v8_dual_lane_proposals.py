from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,importlib.util
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v7_parallel_lane_natural as v7
except ImportError:v7=sib('v7_dual','run_eth_repair_functional_exam_v7_parallel_lane_natural.py')
try:
 from tools import eth_persistent_repair_online_capability_runtime as caprt
except ImportError:caprt=sib('caprt_dual','eth_persistent_repair_online_capability_runtime.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_repair_functional_exam_v1 as ex1
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9

class DualLaneProposalSim(v7.NaturalParallelLaneSim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.dualProposalTicks=0;self.repairProposalTicks=0;self.expandProposalTicks=0;self.sameTickDualSubmit=0
 def _submit_authorized(self,t,qv,z,roles_this_tick):
  if z is None:return False
  side,qty,_,role,oid=z
  if role in roles_this_tick:return False
  p=float(qv[side]['bid']);legal=1/p if p>EPS else 1e9
  if qty<legal-EPS:
   if role=='REPAIR':self.remainingCapBlocks+=1
   else:self.parallelBudgetBlocks+=1
   return False
  qty=min(max(qty,legal),12.)
  self._pendingAuthorizedRole=role;self._pendingAuthorizedObjectiveId=oid
  self._pendingParentId=self.repairParent['id'] if self.repairParent else None;self._pendingLane=role
  ok=self.submit(t,side,p,qty)
  if ok:roles_this_tick.add(role)
  return bool(ok)
 def run_exam_v8(self,models,winner):
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
   qty=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))));qty=min(qty,12.)
   ai=self.auth_inv();weak='UP' if ai['UP']<ai['DOWN']-EPS else 'DOWN' if ai['DOWN']<ai['UP']-EPS else None;dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
   roles=set();submitted=0
   # Independent lane proposals from the same frozen market snapshot. Repair first preserves safety priority;
   # EXPAND remains independently eligible in the same tick under both_responsibilities capability.
   if weak is not None:
    self.repairProposalTicks+=1
    p=float(qv[weak]['bid']);q=max(qty,1/p if p>EPS else 1e9);z=self.choose_authorized(t,end,weak,min(q,12.));submitted+=int(self._submit_authorized(t,qv,z,roles))
   if dom is not None:
    self.expandProposalTicks+=1
    p=float(qv[dom]['bid']);q=max(qty,1/p if p>EPS else 1e9);z=self.choose_authorized(t,end,dom,min(q,12.));submitted+=int(self._submit_authorized(t,qv,z,roles))
   if weak is not None and dom is not None:self.dualProposalTicks+=1
   if submitted>=2:self.sameTickDualSubmit+=1
  end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2);self._apply_due_observations(end2+self.fillObsLagMs+self.ackReleaseLagMs+1);self._refresh_carrier_ledger(end2+self.fillObsLagMs+self.ackReleaseLagMs+1);self._reconcile_objective(end2+self.fillObsLagMs+self.ackReleaseLagMs+1)
  gross=sum(self.inv.values());ff=self.roleStableAtFill+self.repairToExpandAtFill+self.expandToRepairAtFill
  return {'pnlDiagnosticOnly':self.inv.get(str(winner).upper(),0.)-self.cost,'buyNotional':self.cost,'pairCoverage':2*min(self.inv.values())/gross if gross>EPS else 0.,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'actualFillEvents':self.actualFillEvents,'partialFillEvents':self.partialFillEvents,'lateFillAfterCancelEvents':self.lateFillAfterCancelEvents,'repairToExpandAtFirstFill':self.repairToExpandAtFill,'expandToRepairAtFirstFill':self.expandToRepairAtFill,'firstFillRoleEvents':ff,'acceptedSubmitWhileSameSideUnobservedFill':self.acceptedSubmitWhileSameSideUnobservedFill,'authorizedSubmitWithTruthRoleMismatch':self.authorizedSubmitWithTruthRoleMismatch,'maxUnobservedFillQty':self.maxUnobservedFillQty,'objectiveCompletions':self.objectiveCompletions,'reauthBlocks':self.reauthBlocks,'remainingCapBlocks':self.remainingCapBlocks,'carrierLedgerEntries':len(self.carrierLedger),'unresolvedCarrierCount':len(self.unresolved()),'unresolvedCarrierQty':self.outstanding_total(),'maxOwnedRepairOverGap':self.maxOwnedRepairOverGap,'overOwnedSubmitViolations':self.overOwnedSubmitViolations,'repairParentBirths':self.repairParentBirths,'repairParentCompletions':self.repairParentCompletions,'repairParentActiveAtEnd':int(self.repairParent is not None),'repairParentTicks':self.repairParentTicks,'repairChildCommits':self.repairChildCommits,'expandChildCommitsUnderRepairParent':self.expandChildCommitsUnderRepairParent,'repairResumeAfterExpandCommits':self.repairResumeAfterExpandCommits,'parallelCapabilityTicks':sum(1 for p in self.capPred if p['repair_obligation_30s']>=.5 and p['expand_opportunity_30s']>=.5),'capabilityNoActionBlocks':self.capabilityNoActionBlocks,'parentCompletionDeferred':self.parentCompletionDeferred,'parallelChildCommitsWithRepairOutstanding':self.parallelChildCommitsWithRepairOutstanding,'laneOwnershipBlocks':self.laneOwnershipBlocks,'externalOwnershipBlocks':self.externalOwnershipBlocks,'parallelBudgetBlocks':self.parallelBudgetBlocks,'dualProposalTicks':self.dualProposalTicks,'repairProposalTicks':self.repairProposalTicks,'expandProposalTicks':self.expandProposalTicks,'sameTickDualSubmit':self.sameTickDualSubmit,'bothScoreMax':max([float(p['both_responsibilities_30s']) for p in self.capPred],default=0.0),'bothScoreQ90':float(np.quantile([float(p['both_responsibilities_30s']) for p in self.capPred],.9)) if self.capPred else 0.0,'bothScoreQ75':float(np.quantile([float(p['both_responsibilities_30s']) for p in self.capPred],.75)) if self.capPred else 0.0,'bothTicksGe03':sum(float(p['both_responsibilities_30s'])>=.3 for p in self.capPred),'bothTicksGe04':sum(float(p['both_responsibilities_30s'])>=.4 for p in self.capPred),'bothTicksGe05':sum(float(p['both_responsibilities_30s'])>=.5 for p in self.capPred)}

def agg(rows):
 b=v7.aggregate(rows)
 for k in ['dualProposalTicks','repairProposalTicks','expandProposalTicks','sameTickDualSubmit','bothTicksGe03','bothTicksGe04','bothTicksGe05']:b[k]=int(sum(int(r.get(k) or 0) for r in rows))
 b['bothScoreMax']=max((float(r.get('bothScoreMax') or 0) for r in rows),default=0.0)
 b['bothScoreQ90MaxMarket']=max((float(r.get('bothScoreQ90') or 0) for r in rows),default=0.0)
 return b

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--markets',type=int,default=24);ap.add_argument('--scenarios',default='CONTROL');ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v8_dual_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];models,off1,off2=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=caprt.UnifiedCapabilityRuntime(a.capability_model,'cpu');test=[r for r in cohort if r['split']!='TRAIN40'];n=min(a.markets,len(test));idx=sorted(set(int(x) for x in np.linspace(0,len(test)-1,n)));selected=[test[i] for i in idx]
  names=[x.strip() for x in a.scenarios.split(',') if x.strip()];summaries={};allrows=[]
  for scenario in names:
   cfg=ex1.SCENARIOS[scenario];rows=[]
   for i,cr in enumerate(selected,1):
    sim=DualLaneProposalSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,life,cfg['fillObsLagMs'],cfg['ackReleaseLagMs'],capability=cap)
    try:r=sim.run_exam_v8(models,cr['winner'])
    finally:sim.close()
    r.update({'scenario':scenario,'marketId':int(cr['marketId']),'chronologyIndex':idx[i-1]});rows.append(r);allrows.append(r)
    if i%8==0:print(json.dumps({'scenario':scenario,'progress':i,'repairToExpand':sum(x['repairToExpandAtFirstFill'] for x in rows),'overOwned':sum(x['overOwnedSubmitViolations'] for x in rows),'parallelChild':sum(x['parallelChildCommitsWithRepairOutstanding'] for x in rows),'expandUnderParent':sum(x['expandChildCommitsUnderRepairParent'] for x in rows),'dualSubmit':sum(x['sameTickDualSubmit'] for x in rows)}),flush=True)
   s=agg(rows);s['pnl']=v7.v6.pnl_summary(rows);summaries[scenario]=s
  control=summaries.get('CONTROL');gates={'allScenariosNoRepairToExpand':all(s['repairToExpandAtFirstFill']==0 for s in summaries.values()),'allScenariosNoAuthorizedTruthRoleMismatch':all(s['authorizedSubmitWithTruthRoleMismatch']==0 for s in summaries.values()),'allScenariosNoOverOwnedRepairSubmit':all(s['overOwnedSubmitViolations']==0 for s in summaries.values()),'allScenariosNoUnresolvedCarrierQty':all(abs(float(s['unresolvedCarrierQty']))<=1e-9 for s in summaries.values()),'naturalParallelChildOccurs':any(s['parallelChildCommitsWithRepairOutstanding']>0 for s in summaries.values()),'naturalExpandUnderParentOccurs':any(s['expandChildCommitsUnderRepairParent']>0 for s in summaries.values())}
  if control is not None:gates['repairParentCanComplete']=control['repairParentCompletions']>0
  out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V8_DUAL_LANE_PROPOSALS','researchOnly':True,'performanceGraduationEligible':False,'cohort':'consumed Fresh101 closed-loop development cohort','architecture':['V7 lane-scoped ownership retained','old single side proposal removed inside parent','shared execution timing/qty proposal followed by independent weak-side REPAIR and dominant-side EXPAND candidates','each lane separately passes capability, legal-min, budget and ownership checks','same tick may commit both lanes'],'scenarios':summaries,'gates':gates,'allPass':all(gates.values()),'rows':allrows,'boundary':['No dream fills','No forced child authorization','No new model gradients','Same consumed development cohort; not graduation','Full five-disturbance regression required after CONTROL coverage pass']}
  op=Path(a.output) if a.output else Path(__import__('os').environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'allPass':out['allPass'],'gates':gates,'control':control},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
