from __future__ import annotations
import argparse,copy,hashlib,json,math,sys,warnings
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke
from tools.hft_r2_fault_state_refresh_exam_v1 import SequenceFault
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
BRANCHES=('WAIT','REBASE','ACTIVE','RETIRE')

def f(v,d=0.0):
 try:
  x=float(v);return x if math.isfinite(x) else d
 except:return d

def actual_side(s,side):
 p=s.get('actualPortfolio') or {};g=f(p.get('combined_gross'));n=f(p.get('combined_net'));return max(0.0,(g+n)/2 if side=='UP' else (g-n)/2)
def shash(s):
 p=s.get('actualPortfolio') or {};m=s.get('behaviorMemory') or {};d=s.get('desiredMakerShares') or {}
 z={'atMs':s.get('atMs'),'gross':f(p.get('combined_gross')),'net':f(p.get('combined_net')),'floor':f(p.get('worst_case_floor')),'desired':d,'tracking':f(s.get('trackingError')),'outcome':m.get('lastOutcome'),'fault':m.get('lastFault'),'rej':m.get('consecutiveReject'),'nf':m.get('consecutiveNoFill')};return hashlib.sha256(json.dumps(z,sort_keys=True,default=str).encode()).hexdigest()

class PostFaultBranch:
 def __init__(self,branch,required_faults):self.branch=branch;self.required=required_faults;self.calls=[];self.used=False;self.state=None;self.hash=None
 def __call__(self,x:dict[str,Any]):
  s=copy.deepcopy(x['executionState']);m=s.get('behaviorMemory') or {};faults=int(m.get('consecutiveReject') or 0)+int(m.get('consecutiveNoFill') or 0)+int(m.get('consecutivePartial') or 0);self.calls.append((x['atMs'],faults,m.get('lastOutcome')))
  if len(self.calls)==1:return {'executionMode':'ACTIVE_REPAIR'}
  if faults<self.required:return {'executionMode':'ACTIVE_REPAIR'} if faults>0 else None
  if self.used:return {'executionMode':'WAIT'}
  self.used=True;self.state=copy.deepcopy(s);self.hash=shash(s)
  if self.branch=='WAIT':return {'executionMode':'WAIT','freezeNewEconomicIntents':True}
  if self.branch=='REBASE':return {'desiredUP':actual_side(s,'UP'),'desiredDOWN':actual_side(s,'DOWN'),'executionMode':'WAIT','freezeNewEconomicIntents':True}
  if self.branch=='ACTIVE':return {'executionMode':'ACTIVE_REPAIR','freezeNewEconomicIntents':True}
  if self.branch=='RETIRE':return {'desiredUP':actual_side(s,'UP'),'desiredDOWN':actual_side(s,'DOWN'),'executionMode':'RETURN_OR_RETIRE','freezeNewEconomicIntents':True}
  raise ValueError(self.branch)

def run(mid,faults,branch):
 inj=SequenceFault(faults);pol=PostFaultBranch(branch,len(faults));r=run_smoke(mid,passive_mode='wait',passive_program={'PASSIVE_MAINTAIN':'offset0','PASSIVE_REPAIR':'offset0'},own_state_poll_ms=250,taker_submit_fault_override=inj,fault_reentry_enabled=True,behavior_policy_override=pol,behavior_ownstate_reentry=True,fault_containment_freeze_before_redecision=True,allowed_executor_taker_kinds={'FROZEN_R2','PAIR_COMPLETION_REPLACE'},trace_execution_states=True)
 return {'marketId':mid,'faultPlan':faults,'branch':branch,'faultsInjected':inj.used,'branchUsed':pol.used,'stateHash':pol.hash,'state':pol.state,'floor':r['actualExecution']['finalPortfolio']['worst_case_floor'],'pnl':r['actualExecution']['realizedPnl'],'tracking':r['actualExecution']['finalAbsTrackingError'],'takerFilled':r['actualExecution']['takerFilledShares'],'violations':r['cycleInvariantViolationCount']}
def main():
 warnings.filterwarnings('ignore');ap=argparse.ArgumentParser();ap.add_argument('--market-id',type=int,default=1576991);ap.add_argument('--fault-plan',default='SUBMIT_REJECT');ap.add_argument('--output',default='hft_r2_post_fault_joint_behavior_counterfactual_v1_report.json');a=ap.parse_args();faults=[x for x in a.fault_plan.split(',') if x];rows=[]
 for b in BRANCHES:
  r=run(a.market_id,faults,b);rows.append(r);print(json.dumps({k:r[k] for k in ['branch','faultsInjected','branchUsed','stateHash','floor','pnl','tracking','takerFilled','violations']},ensure_ascii=False),flush=True)
 used=[r for r in rows if r['branchUsed'] and len(r['faultsInjected'])==len(faults)];matched=len(used)==len(BRANCHES) and len({r['stateHash'] for r in used})==1;oracle=max(used,key=lambda r:(f(r['floor'],-1e9),f(r['pnl'],-1e9),r['branch']=='WAIT')) if used else None
 s={'marketId':a.market_id,'faultPlan':faults,'matchedPostFaultState':matched,'oracle':None if oracle is None else oracle['branch'],'oracleFloor':None if oracle is None else oracle['floor'],'branchFloors':{r['branch']:r['floor'] for r in used},'zeroViolations':all(r['violations']==0 for r in rows)};(OUT/a.output).write_text(json.dumps({'version':'HFT_R2_POST_FAULT_JOINT_BEHAVIOR_COUNTERFACTUAL_V1','rows':rows,'summary':s},ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps(s,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
