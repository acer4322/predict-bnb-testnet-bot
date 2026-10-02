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

def shares(s):
 p=s.get('actualPortfolio') or {};g=f(p.get('combined_gross'));n=f(p.get('combined_net'))
 return {'UP':max(0.0,(g+n)/2),'DOWN':max(0.0,(g-n)/2),'NET':n}

class SoftPolicy:
 """Research-only whole-behavior candidate. Never permanently freezes all intents.
 Pre-fault: cap only the risk-increasing desired side to current actual when |net|>=54.
 Fault memory: after any execution failure, keep the risk-increasing side capped; allow the opposite side up to actual+1 chunk.
 After >=2 consecutive failures, rebase only the risk-increasing side to actual and WAIT for one decision epoch; no permanent freeze.
 """
 def __init__(self):self.calls=[];self.last_fault_count=0;self.cooldown_until=None
 def __call__(self,x):
  s=copy.deepcopy(x['executionState']);m=copy.deepcopy(s.get('behaviorMemory') or {});a=shares(s);net=a['NET'];absnet=abs(net)
  faults=int(m.get('consecutiveReject') or 0)+int(m.get('consecutiveNoFill') or 0)+int(m.get('consecutivePartial') or 0)
  d=copy.deepcopy(x.get('desiredPortfolio') or {})
  rec={'atMs':int(x['atMs']),'trigger':x.get('trigger'),'net':net,'faults':faults,'lastOutcome':m.get('lastOutcome'),'desired':d,'actual':{'UP':a['UP'],'DOWN':a['DOWN']}}
  self.calls.append(rec)
  risk_side='UP' if net>0 else 'DOWN' if net<0 else None
  repair_side='DOWN' if net>0 else 'UP' if net<0 else None
  proposal={}
  # one-epoch cooldown after second consecutive failure; controller remains able to create future intents.
  if faults>=2 and faults>self.last_fault_count:
   proposal['executionMode']='WAIT'
   if risk_side: proposal['desired'+risk_side]=a[risk_side]
   if repair_side: proposal['desired'+repair_side]=max(a[repair_side],min(f(d.get(repair_side),a[repair_side]),a[repair_side]+CHUNK))
   self.last_fault_count=faults
   return proposal
  self.last_fault_count=max(self.last_fault_count,faults)
  if risk_side and (absnet>=3*CHUNK-1e-9 or faults>0):
   proposal['desired'+risk_side]=a[risk_side]
   if repair_side:
    proposal['desired'+repair_side]=max(a[repair_side],min(f(d.get(repair_side),a[repair_side]+CHUNK),a[repair_side]+CHUNK))
   # preserve passive execution/recovery; do not globally freeze.
   proposal['executionMode']='KEEP_PASSIVE'
   return proposal
  return None

def run_case(mid,role,plan):
 pol=SoftPolicy(); inj=SequenceMakerFault(plan) if role=='MAKER' else SequenceFault(plan)
 kw={'maker_submit_fault_override':inj} if role=='MAKER' else {'taker_submit_fault_override':inj,'allowed_executor_taker_kinds':{'FROZEN_R2','PAIR_COMPLETION_REPLACE'}}
 r=run_smoke(mid,passive_mode='wait',passive_program={'PASSIVE_MAINTAIN':'offset0','PASSIVE_REPAIR':'offset0'},own_state_poll_ms=250,fault_reentry_enabled=True,behavior_policy_override=pol,behavior_ownstate_reentry=True,trace_execution_states=True,**kw)
 fault_events=[x for x in pol.calls if x['faults']>0]
 return {'marketId':mid,'role':role,'faultPlan':plan,'faultsInjected':list(inj.used),'floor':r['actualExecution']['finalPortfolio']['worst_case_floor'],'pnlAuditOnly':r['actualExecution']['realizedPnl'],'absTracking':r['actualExecution']['finalAbsTrackingError'],'takerFilled':r['actualExecution']['takerFilledShares'],'makerFilled':r['actualExecution']['makerFilledShares'],'violations':r['cycleInvariantViolationCount'],'faultStateSeen':bool(fault_events),'postFaultPolicyCalls':len(fault_events),'permanentFreezeUsed':any(bool((e.get('proposal') or {}).get('freezeNewEconomicIntents')) for e in r['r2ObjectiveExecution'].get('behaviorOverrideEvents',[])),'policyCalls':len(pol.calls)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--market-ids',default='1576991,1579313,1579674');ap.add_argument('--output',default='hft_r2_soft_containment_fault_candidate_v1_report.json');a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 cases=[('MAKER',['SUBMIT_REJECT']),('MAKER',['SUBMIT_REJECT','SUBMIT_REJECT']),('MAKER',['NO_FILL_STALL']),('MAKER',['NO_FILL_STALL','NO_FILL_STALL']),('TAKER',['SUBMIT_REJECT']),('TAKER',['SUBMIT_REJECT','SUBMIT_REJECT']),('TAKER',['NO_FILL_STALL']),('TAKER',['NO_FILL_STALL','NO_FILL_STALL'])]
 rows=[]
 for mid in mids:
  for role,plan in cases:
   r=run_case(mid,role,plan);rows.append(r);print(json.dumps(r,ensure_ascii=False),flush=True)
 applicable=[r for r in rows if r['faultsInjected']]
 summary={'markets':len(mids),'runs':len(rows),'applicable':len(applicable),'allApplicableZeroViolations':all(r['violations']==0 for r in applicable),'applicableFaultStateSeenRate':sum(r['faultStateSeen'] for r in applicable)/len(applicable) if applicable else None,'permanentFreezeUsed':any(r['permanentFreezeUsed'] for r in rows),'minApplicableFloor':min([r['floor'] for r in applicable] or [0]),'meanApplicableFloor':sum(r['floor'] for r in applicable)/len(applicable) if applicable else None,'casesWithPostFaultTrading':sum((r['makerFilled']+r['takerFilled'])>0 for r in applicable)}
 (OUT/a.output).write_text(json.dumps({'version':'HFT_R2_SOFT_CONTAINMENT_FAULT_CANDIDATE_V1','researchOnly':True,'rows':rows,'summary':summary},ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
