from __future__ import annotations
import argparse,copy,hashlib,json,math,sys,warnings
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke
BASE=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
EPS=1e-8
SECOND_ACTIONS=('PRESERVE_WAIT','REBASE_ACTUAL_WAIT','PRESERVE_ACTIVE')

def finite(v,d=0.0):
 try:
  x=float(v);return x if math.isfinite(x) else float(d)
 except Exception:return float(d)

def shares_from_portfolio(p):
 g=finite(p.get('combined_gross'));n=finite(p.get('combined_net'));return (g+n)/2,(g-n)/2

def shash(s):
 p=s['actualPortfolio'];pub=s.get('publicState') or {};des=s.get('desiredMakerShares') or {};mem=s.get('behaviorMemory') or {}
 payload={'atMs':int(s['atMs']),'gross':round(finite(p.get('combined_gross')),8),'net':round(finite(p.get('combined_net')),8),'floor':round(finite(p.get('worst_case_floor')),8),'coverage':round(finite(p.get('combined_paired_coverage')),8),'desiredUP':round(finite(des.get('UP')),8),'desiredDOWN':round(finite(des.get('DOWN')),8),'tracking':round(finite(s.get('trackingError')),8),'secondsLeft':round(finite(pub.get('secondsLeft')),6),'lastAction':str(mem.get('lastAction')),'lastOutcome':str(mem.get('lastOutcome')),'lastFault':str(mem.get('lastFault')),'retry':int(mem.get('sameRouteRetryCount') or 0),'noFill':int(mem.get('consecutiveNoFill') or 0),'reject':int(mem.get('consecutiveReject') or 0),'partial':int(mem.get('consecutivePartial') or 0)}
 return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()

class TwoStepJoint:
 def __init__(self,first_action,second_action):
  self.first_action=first_action;self.second_action=second_action;self.first_used=False;self.second_used=False;self.first_state=None;self.second_state=None;self.first_hash=None;self.second_hash=None;self.first_at=None;self.first_actual=None;self.first_desired=None;self.first_tracking=None;self.first_mem_len=0
 def apply(self,action,x):
  s=x['executionState'];p=s['actualPortfolio'];up,dn=shares_from_portfolio(p)
  if action=='PRESERVE_WAIT': return {'executionMode':'WAIT'}
  if action=='REBASE_ACTUAL_WAIT': return {'desiredUP':up,'desiredDOWN':dn,'executionMode':'WAIT'}
  if action=='PRESERVE_ACTIVE': return {'executionMode':'ACTIVE_REPAIR'}
  raise ValueError(action)
 def __call__(self,x):
  s=x['executionState'];p=s['actualPortfolio'];g=finite(p.get('combined_gross'));err=abs(finite(s.get('trackingError')));mem=s.get('behaviorMemory') or {}
  if not self.first_used:
   if g<=EPS or err<18-EPS:return None
   self.first_used=True;self.first_state=copy.deepcopy(s);self.first_hash=shash(s);self.first_at=int(s['atMs']);self.first_actual=(round(finite(p.get('combined_gross')),8),round(finite(p.get('combined_net')),8));des=x['desiredPortfolio'];self.first_desired=(round(finite(des.get('UP')),8),round(finite(des.get('DOWN')),8));self.first_tracking=round(finite(s.get('trackingError')),8);self.first_mem_len=len(mem.get('history') or [])
   return self.apply(self.first_action,x)
  if self.second_used:return None
  now=int(s['atMs']);actual=(round(finite(p.get('combined_gross')),8),round(finite(p.get('combined_net')),8));histlen=len(mem.get('history') or []);des=x['desiredPortfolio'];desired_now=(round(finite(des.get('UP')),8),round(finite(des.get('DOWN')),8));tracking_now=round(finite(s.get('trackingError')),8)
  outcome=str(mem.get('lastOutcome') or '')
  material=(actual!=self.first_actual) or (histlen>self.first_mem_len and outcome not in {'','SUBMITTED'})
  if not material:return None
  self.second_used=True;self.second_state=copy.deepcopy(s);self.second_hash=shash(s)
  return self.apply(self.second_action,x)

def run(mid,first,second):
 pol=TwoStepJoint(first,second)
 r=run_smoke(mid,passive_mode='wait',passive_program={'PASSIVE_MAINTAIN':'offset0','PASSIVE_REPAIR':'offset0'},own_state_poll_ms=250,behavior_policy_override=pol,behavior_ownstate_reentry=True,allowed_executor_taker_kinds={'FROZEN_R2','PAIR_COMPLETION_REPLACE'},trace_execution_states=True)
 return {'marketId':mid,'firstAction':first,'secondAction':second,'firstUsed':pol.first_used,'secondUsed':pol.second_used,'firstHash':pol.first_hash,'secondHash':pol.second_hash,'firstState':pol.first_state,'secondState':pol.second_state,'floor':r['actualExecution']['finalPortfolio']['worst_case_floor'],'pnl':r['actualExecution']['realizedPnl'],'tracking':r['actualExecution']['finalAbsTrackingError'],'takerFilled':r['actualExecution']['takerFilledShares'],'violations':r['cycleInvariantViolationCount']}

def summarize(rows):
 out=[]
 for mid in sorted(set(r['marketId'] for r in rows)):
  g=[r for r in rows if r['marketId']==mid];used=[r for r in g if r['secondUsed']];matched=len(used)==len(SECOND_ACTIONS) and len(set(r['secondHash'] for r in used))==1
  oracle=max(used,key=lambda r:(finite(r['floor'],-1e9),finite(r['pnl'],-1e9),r['secondAction']=='PRESERVE_WAIT')) if used else None
  out.append({'marketId':mid,'firstAction':g[0]['firstAction'] if g else None,'matchedSecondState':matched,'secondOracle':None if oracle is None else oracle['secondAction'],'oracleFloor':None if oracle is None else oracle['floor'],'values':{r['secondAction']:{'floor':r['floor'],'pnl':r['pnl'],'tracking':r['tracking'],'takerFilled':r['takerFilled']} for r in used},'semanticPass':all(r['violations']==0 for r in g)})
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--spec',required=True,help='mid:first,mid:first');ap.add_argument('--output',default='hft_r2_recurrent_joint_behavior_counterfactual_v1_report.json');args=ap.parse_args();spec=[]
 for item in args.spec.split(','):
  mid,first=item.split(':',1);spec.append((int(mid),first))
 warnings.filterwarnings('ignore');rows=[]
 for mid,first in spec:
  for second in SECOND_ACTIONS:
   row=run(mid,first,second);rows.append(row);print(json.dumps({'marketId':mid,'first':first,'second':second,'secondUsed':row['secondUsed'],'floor':row['floor'],'pnl':row['pnl'],'violations':row['violations']},ensure_ascii=False),flush=True)
 contexts=summarize(rows);summary={'markets':len(spec),'matchedSecondContexts':sum(c['matchedSecondState'] for c in contexts),'semanticPassContexts':sum(c['semanticPass'] for c in contexts),'secondOracleCounts':{a:sum(c['secondOracle']==a for c in contexts) for a in SECOND_ACTIONS}}
 out={'version':'HFT_R2_RECURRENT_JOINT_BEHAVIOR_COUNTERFACTUAL_V1','researchOnly':True,'secondActions':list(SECOND_ACTIONS),'rows':rows,'contexts':contexts,'summary':summary};p=BASE/args.output;p.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'output':str(p),'summary':summary},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
