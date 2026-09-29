from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,importlib.util
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v8_dual_lane_proposals as v8
except ImportError:v8=sib('v8_conj','run_eth_repair_functional_exam_v8_dual_lane_proposals.py')
try:
 from tools import eth_persistent_repair_online_capability_runtime as caprt
except ImportError:caprt=sib('caprt_conj','eth_persistent_repair_online_capability_runtime.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_repair_functional_exam_v1 as ex1
EPS=1e-9

class ConjunctiveCapabilityRuntime:
 def __init__(self,model_path,device='cpu'):
  self.base=caprt.UnifiedCapabilityRuntime(model_path,device)
 def predict(self,s):
  p=self.base.predict(s)
  # both_responsibilities teacher was exactly (future Repair AND future Expand).
  # Use the two stronger primitive heads as authority; the weaker redundant Both head no longer vetoes coexistence.
  p['both_responsibilities_30s']=min(float(p['repair_obligation_30s']),float(p['expand_opportunity_30s']))
  return p

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--markets',type=int,default=24);ap.add_argument('--scenarios',default='CONTROL');ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v9_conj_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];models,off1,off2=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=ConjunctiveCapabilityRuntime(a.capability_model,'cpu');test=[r for r in cohort if r['split']!='TRAIN40'];n=min(a.markets,len(test));idx=sorted(set(int(x) for x in np.linspace(0,len(test)-1,n)));selected=[test[i] for i in idx]
  names=[x.strip() for x in a.scenarios.split(',') if x.strip()];summaries={};allrows=[]
  for scenario in names:
   cfg=ex1.SCENARIOS[scenario];rows=[]
   for i,cr in enumerate(selected,1):
    sim=v8.DualLaneProposalSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,life,cfg['fillObsLagMs'],cfg['ackReleaseLagMs'],capability=cap)
    try:r=sim.run_exam_v8(models,cr['winner'])
    finally:sim.close()
    r.update({'scenario':scenario,'marketId':int(cr['marketId']),'chronologyIndex':idx[i-1]});rows.append(r);allrows.append(r)
    if i%8==0:print(json.dumps({'scenario':scenario,'progress':i,'repairToExpand':sum(x['repairToExpandAtFirstFill'] for x in rows),'overOwned':sum(x['overOwnedSubmitViolations'] for x in rows),'parallelChild':sum(x['parallelChildCommitsWithRepairOutstanding'] for x in rows),'expandUnderParent':sum(x['expandChildCommitsUnderRepairParent'] for x in rows),'repairResumes':sum(x['repairResumeAfterExpandCommits'] for x in rows),'dualSubmit':sum(x['sameTickDualSubmit'] for x in rows)}),flush=True)
   s=v8.agg(rows);s['pnl']=v8.v7.v6.pnl_summary(rows);summaries[scenario]=s
  control=summaries.get('CONTROL');gates={'allScenariosNoRepairToExpand':all(s['repairToExpandAtFirstFill']==0 for s in summaries.values()),'allScenariosNoAuthorizedTruthRoleMismatch':all(s['authorizedSubmitWithTruthRoleMismatch']==0 for s in summaries.values()),'allScenariosNoOverOwnedRepairSubmit':all(s['overOwnedSubmitViolations']==0 for s in summaries.values()),'allScenariosNoUnresolvedCarrierQty':all(abs(float(s['unresolvedCarrierQty']))<=1e-9 for s in summaries.values()),'naturalParallelChildOccurs':any(s['parallelChildCommitsWithRepairOutstanding']>0 for s in summaries.values()),'naturalExpandUnderParentOccurs':any(s['expandChildCommitsUnderRepairParent']>0 for s in summaries.values())}
  if control is not None:gates['repairParentCanComplete']=control['repairParentCompletions']>0
  if control is not None and 'ACK_RELEASE_3000' in summaries:gates['ackLagDoesNotCollapseActivity']=summaries['ACK_RELEASE_3000']['meanSubmits']>=.5*max(control['meanSubmits'],EPS)
  if control is not None and 'COMPOUND_3000' in summaries:gates['compoundDoesNotCollapseActivity']=summaries['COMPOUND_3000']['meanSubmits']>=.5*max(control['meanSubmits'],EPS)
  out={'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V9_CONJUNCTIVE_PARALLEL_ROUTER','researchOnly':True,'performanceGraduationEligible':False,'cohort':'consumed Fresh101 closed-loop development cohort','architecture':['V7 lane-scoped ownership','V8 independent Repair/Expand proposals','Repair obligation and Expand opportunity primitive heads independently authorize lanes','coexistence is deterministic conjunction of primitive authorities','weaker redundant Both expert retained only as prior research diagnostic and has no veto'],'scenarios':summaries,'gates':gates,'allPass':all(gates.values()),'rows':allrows,'boundary':['No dream fills','No forced child authorization','No new model gradients','No manual threshold lowering','Both label in V5 was defined as exact conjunction rep and exp','Same consumed development cohort; not graduation','Full five-disturbance pass required before freeze/new-market test']}
  op=Path(a.output) if a.output else Path(__import__('os').environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'allPass':out['allPass'],'gates':gates,'control':control},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
