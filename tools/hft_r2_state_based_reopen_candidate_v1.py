from __future__ import annotations
import argparse,copy,json,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke
from tools.hft_r2_fault_state_refresh_exam_v1 import SequenceFault
from tools.hft_r2_maker_fault_state_refresh_exam_v1 import SequenceMakerFault
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0';CHUNK=18.0

def f(v,d=0.0):
 try:
  x=float(v);return x if math.isfinite(x) else d
 except Exception:return d

def side_shares(state):
 p=state.get('actualPortfolio') or {};g=f(p.get('combined_gross'));n=f(p.get('combined_net'))
 return {'UP':max(0.0,(g+n)/2),'DOWN':max(0.0,(g-n)/2),'NET':n}

class StateBasedReopenPolicy:
 """Research-only whole-behavior candidate.
 - Pre-fault: soft manifold cap on the risk-increasing desired side when |net|>=54.
 - First fault: containment mode; cap risk-increasing desired side, preserve at most one repair chunk.
 - >=2 consecutive failures: rebase desired to confirmed actual and globally freeze new economic intents.
 - Reopen automatically only after actual |net|<=18 and there is no unresolved remainder owner / pending replace.
 The freeze is therefore state-based and reversible, never permanent by rule.
 """
 def __init__(self):
  self.calls=[];self.containment=False;self.reopened=False;self.freeze_started=False
 def __call__(self,x):
  s=copy.deepcopy(x['executionState']);m=copy.deepcopy(s.get('behaviorMemory') or {});a=side_shares(s);net=a['NET'];absnet=abs(net)
  faults=int(m.get('consecutiveReject') or 0)+int(m.get('consecutiveNoFill') or 0)+int(m.get('consecutivePartial') or 0)
  unresolved=any(v is not None for v in (s.get('remainderOwner') or {}).values()) or bool(s.get('pendingReplace'))
  risk='UP' if net>0 else 'DOWN' if net<0 else None;repair='DOWN' if net>0 else 'UP' if net<0 else None
  rec={'atMs':int(x['atMs']),'trigger':x.get('trigger'),'net':net,'faults':faults,'unresolved':unresolved,'containment':self.containment,'actual':{'UP':a['UP'],'DOWN':a['DOWN']},'desired':copy.deepcopy(x.get('desiredPortfolio') or {})};self.calls.append(rec)
  # Exit containment only from an actually safe, reconciled state.
  if self.containment and absnet<=CHUNK+1e-9 and not unresolved:
   self.containment=False;self.reopened=True
   return {'freezeNewEconomicIntents':False,'freezeFrozenR2TakerIntents':False,'freezeMakerExecutionChildren':False}
  if faults>=2:
   self.containment=True;self.freeze_started=True
   return {'desiredUP':a['UP'],'desiredDOWN':a['DOWN'],'executionMode':'WAIT','freezeNewEconomicIntents':True,'freezeFrozenR2TakerIntents':True,'freezeMakerExecutionChildren':False}
  if faults==1:
   self.containment=True
   p={'executionMode':'KEEP_PASSIVE','freezeNewEconomicIntents':False,'freezeFrozenR2TakerIntents':True}
   if risk:p['desired'+risk]=a[risk]
   if repair:
    desired=copy.deepcopy(x.get('desiredPortfolio') or {})
    p['desired'+repair]=max(a[repair],min(f(desired.get(repair),a[repair]+CHUNK),a[repair]+CHUNK))
   return p
  if risk and absnet>=3*CHUNK-1e-9:
   desired=copy.deepcopy(x.get('desiredPortfolio') or {})
   return {'desired'+risk:a[risk],'desired'+repair:max(a[repair],min(f(desired.get(repair),a[repair]+CHUNK),a[repair]+CHUNK)) if repair else None,'executionMode':'KEEP_PASSIVE'}
  return None

def run_case(mid,role,plan):
 pol=StateBasedReopenPolicy();inj=SequenceMakerFault(plan) if role=='MAKER' else SequenceFault(plan)
 kw={'maker_submit_fault_override':inj} if role=='MAKER' else {'taker_submit_fault_override':inj,'allowed_executor_taker_kinds':{'FROZEN_R2','PAIR_COMPLETION_REPLACE'}}
 r=run_smoke(mid,passive_mode='wait',passive_program={'PASSIVE_MAINTAIN':'offset0','PASSIVE_REPAIR':'offset0'},own_state_poll_ms=250,fault_reentry_enabled=True,behavior_policy_override=pol,behavior_ownstate_reentry=True,trace_execution_states=True,**kw)
 fault_calls=[c for c in pol.calls if c['faults']>0]
 return {'marketId':mid,'role':role,'faultPlan':plan,'faultsInjected':list(inj.used),'floor':r['actualExecution']['finalPortfolio']['worst_case_floor'],'pnlAuditOnly':r['actualExecution']['realizedPnl'],'absTracking':r['actualExecution']['finalAbsTrackingError'],'makerFilled':r['actualExecution']['makerFilledShares'],'takerFilled':r['actualExecution']['takerFilledShares'],'violations':r['cycleInvariantViolationCount'],'faultStateSeen':bool(fault_calls),'freezeStarted':pol.freeze_started,'reopened':pol.reopened,'policyCalls':len(pol.calls),'postFaultPolicyCalls':len(fault_calls)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--market-ids',default='1576991,1579313,1579674');ap.add_argument('--output',default='hft_r2_state_based_reopen_candidate_v1_report.json');a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 cases=[('MAKER',['SUBMIT_REJECT','SUBMIT_REJECT']),('MAKER',['NO_FILL_STALL','NO_FILL_STALL']),('TAKER',['SUBMIT_REJECT','SUBMIT_REJECT']),('TAKER',['NO_FILL_STALL','NO_FILL_STALL'])]
 rows=[]
 for mid in mids:
  for role,plan in cases:
   row=run_case(mid,role,plan);rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
 app=[r for r in rows if r['faultsInjected']]
 summary={'markets':len(mids),'runs':len(rows),'applicable':len(app),'zeroViolations':all(r['violations']==0 for r in app),'faultStateSeenRate':sum(r['faultStateSeen'] for r in app)/len(app) if app else None,'minFloor':min([r['floor'] for r in app] or [0]),'meanFloor':sum(r['floor'] for r in app)/len(app) if app else None,'freezeStartedCases':sum(r['freezeStarted'] for r in app),'reopenedCases':sum(r['reopened'] for r in app),'continuedTradingCases':sum((r['makerFilled']+r['takerFilled'])>0 for r in app)}
 (OUT/a.output).write_text(json.dumps({'version':'HFT_R2_STATE_BASED_REOPEN_CANDIDATE_V1','researchOnly':True,'rows':rows,'summary':summary},ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
