from __future__ import annotations
import argparse, copy, hashlib, json, math, sys, warnings
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke
BASE=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
EPS=1e-8
ACTIONS=(
 'PRESERVE_WAIT',
 'HALF_GAP_WAIT',
 'REBASE_ACTUAL_WAIT',
 'PRESERVE_ACTIVE',
)

def finite(v,d=0.0):
 try:
  x=float(v);return x if math.isfinite(x) else float(d)
 except Exception:return float(d)

def state_hash(state:dict[str,Any])->str:
 p=state['actualPortfolio'];pub=state.get('publicState') or {};des=state.get('desiredMakerShares') or {}
 payload={
  'atMs':int(state['atMs']),
  'gross':round(finite(p.get('combined_gross')),8),'net':round(finite(p.get('combined_net')),8),
  'floor':round(finite(p.get('worst_case_floor')),8),'coverage':round(finite(p.get('combined_paired_coverage')),8),
  'desiredUP':round(finite(des.get('UP')),8),'desiredDOWN':round(finite(des.get('DOWN')),8),
  'tracking':round(finite(state.get('trackingError')),8),'secondsLeft':round(finite(pub.get('secondsLeft')),6)
 }
 return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()

class OneShotJoint:
 def __init__(self,action:str):self.action=action;self.used=False;self.state=None;self.hash=None
 def __call__(self,x):
  s=x['executionState'];p=s['actualPortfolio'];g=finite(p.get('combined_gross'));err=abs(finite(s.get('trackingError')))
  if self.used or g<=EPS or err<18-EPS:return None
  self.used=True;self.state=copy.deepcopy(s);self.hash=state_hash(s)
  n=finite(p.get('combined_net'));up=(g+n)/2;dn=(g-n)/2;des=x['desiredPortfolio'];du=finite(des.get('UP'));dd=finite(des.get('DOWN'))
  if self.action=='PRESERVE_WAIT':return {'executionMode':'WAIT'}
  if self.action=='HALF_GAP_WAIT':return {'desiredUP':up+0.5*max(0.0,du-up),'desiredDOWN':dn+0.5*max(0.0,dd-dn),'executionMode':'WAIT'}
  if self.action=='REBASE_ACTUAL_WAIT':return {'desiredUP':up,'desiredDOWN':dn,'executionMode':'WAIT'}
  if self.action=='PRESERVE_ACTIVE':return {'executionMode':'ACTIVE_REPAIR'}
  raise ValueError(self.action)

def run(mid:int,action:str)->dict[str,Any]:
 pol=OneShotJoint(action)
 r=run_smoke(mid,passive_mode='wait',passive_program={'PASSIVE_MAINTAIN':'offset0','PASSIVE_REPAIR':'offset0'},own_state_poll_ms=250,behavior_policy_override=pol,allowed_executor_taker_kinds={'FROZEN_R2','PAIR_COMPLETION_REPLACE'},trace_execution_states=True)
 return {'marketId':mid,'action':action,'used':pol.used,'stateHash':pol.hash,'state':pol.state,'pnl':r['actualExecution']['realizedPnl'],'floor':r['actualExecution']['finalPortfolio']['worst_case_floor'],'tracking':r['actualExecution']['finalAbsTrackingError'],'makerFilled':r['actualExecution']['makerFilledShares'],'takerFilled':r['actualExecution']['takerFilledShares'],'violations':r['cycleInvariantViolationCount']}

def summarize(rows):
 out=[]
 for mid in sorted(set(r['marketId'] for r in rows)):
  g=[r for r in rows if r['marketId']==mid]
  used=[r for r in g if r['used']]
  matched=len(used)==len(ACTIONS) and len(set(r['stateHash'] for r in used))==1
  oracle=max(used,key=lambda r:(finite(r['floor'],-1e9), finite(r['pnl'],-1e9), r['action']=='PRESERVE_WAIT')) if used else None
  out.append({'marketId':mid,'matchedStartState':matched,'oracleAction':None if oracle is None else oracle['action'],'oracleFloor':None if oracle is None else oracle['floor'],'values':{r['action']:{'floor':r['floor'],'pnl':r['pnl'],'tracking':r['tracking'],'takerFilled':r['takerFilled']} for r in used},'semanticPass':all(r['violations']==0 for r in g)})
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--market-ids',required=True);ap.add_argument('--output',default='hft_r2_joint_behavior_counterfactual_v0_report.json');args=ap.parse_args();mids=[int(x) for x in args.market_ids.split(',') if x.strip()]
 warnings.filterwarnings('ignore');rows=[]
 for mid in mids:
  for a in ACTIONS:
   row=run(mid,a);rows.append(row);print(json.dumps({'marketId':mid,'action':a,'used':row['used'],'floor':row['floor'],'pnl':row['pnl'],'violations':row['violations']},ensure_ascii=False),flush=True)
 contexts=summarize(rows);summary={'markets':len(mids),'matchedContexts':sum(c['matchedStartState'] for c in contexts),'semanticPassContexts':sum(c['semanticPass'] for c in contexts),'oracleCounts':{a:sum(c['oracleAction']==a for c in contexts) for a in ACTIONS}}
 out={'version':'HFT_R2_JOINT_BEHAVIOR_COUNTERFACTUAL_V0','researchOnly':True,'actions':list(ACTIONS),'rows':rows,'contexts':contexts,'summary':summary};p=BASE/args.output;p.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'output':str(p),'summary':summary},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
