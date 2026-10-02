from __future__ import annotations
import argparse, copy, json, math, sys, warnings
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
EPS=1e-8

class SequenceFault:
    def __init__(self, modes:list[str]):
        self.modes=list(modes); self.used=[]; self.calls=[]
    def __call__(self,s:dict[str,Any]):
        self.calls.append(copy.deepcopy(s))
        if str(s.get('kind')) not in {'PAIR_COMPLETION_REPLACE','FROZEN_R2'}: return None
        if len(self.used)>=len(self.modes): return None
        mode=self.modes[len(self.used)]; self.used.append(mode); return mode

class RefreshProbePolicy:
    """Research probe only: forces first ACTIVE, then records every post-fault/reentry state.
    It intentionally does not optimize PnL. After a fault it alternates WAIT/ACTIVE by retry count
    only to prove that refreshed state can change the next behavior decision.
    """
    def __init__(self): self.calls=[]
    def __call__(self,x:dict[str,Any]):
        s=copy.deepcopy(x['executionState']); mem=copy.deepcopy(s.get('behaviorMemory') or {})
        rec={'atMs':int(x['atMs']),'trigger':x.get('trigger'),'actual':copy.deepcopy(s.get('actualPortfolio')),
             'desired':copy.deepcopy(x.get('desiredPortfolio')),'tracking':s.get('trackingError'),'memory':mem}
        self.calls.append(rec)
        # First meaningful asymmetric state: attempt active recovery.
        if len(self.calls)==1: return {'executionMode':'ACTIVE_REPAIR'}
        faults=int(mem.get('consecutiveReject') or 0)+int(mem.get('consecutiveNoFill') or 0)+int(mem.get('consecutivePartial') or 0)
        if faults<=0: return None
        # Probe that policy can react to refreshed memory; not a candidate strategy.
        if faults==1: return {'executionMode':'WAIT'}
        return {'desiredUP':_actual_side(s,'UP'),'desiredDOWN':_actual_side(s,'DOWN'),'executionMode':'WAIT'}

def _actual_side(s:dict[str,Any],side:str)->float:
 p=s.get('actualPortfolio') or {}; g=_f(p.get('combined_gross')); n=_f(p.get('combined_net'))
 return max(0.0,(g+n)/2 if side=='UP' else (g-n)/2)
def _f(v,d=0.0):
 try:
  x=float(v); return x if math.isfinite(x) else d
 except Exception:return d

def run_case(mid:int,name:str,modes:list[str])->dict[str,Any]:
 inj=SequenceFault(modes); pol=RefreshProbePolicy()
 r=run_smoke(mid,passive_mode='wait',passive_program={'PASSIVE_MAINTAIN':'offset0','PASSIVE_REPAIR':'offset0'},own_state_poll_ms=250,
             taker_submit_fault_override=inj,fault_reentry_enabled=True,behavior_policy_override=pol,behavior_ownstate_reentry=True,
             allowed_executor_taker_kinds={'FROZEN_R2','PAIR_COMPLETION_REPLACE'},trace_execution_states=True)
 # Only states after a fault is visible in memory.
 post=[]
 for c in pol.calls:
  m=c['memory']; faults=int(m.get('consecutiveReject') or 0)+int(m.get('consecutiveNoFill') or 0)+int(m.get('consecutivePartial') or 0)
  if faults>0: post.append(c)
 refreshed=bool(post)
 max_rej=max([int((c['memory'] or {}).get('consecutiveReject') or 0) for c in pol.calls] or [0])
 max_nf=max([int((c['memory'] or {}).get('consecutiveNoFill') or 0) for c in pol.calls] or [0])
 state_has_inventory=all(isinstance(c.get('actual'),dict) and 'combined_gross' in c['actual'] for c in post) if post else False
 state_has_memory=all((c.get('memory') or {}).get('lastOutcome') is not None for c in post) if post else False
 desired_present=all(isinstance(c.get('desired'),dict) and 'UP' in c['desired'] and 'DOWN' in c['desired'] for c in post) if post else False
 return {'marketId':mid,'case':name,'faultPlan':modes,'faultsInjected':inj.used,'faultInjectionCount':len(inj.used),
         'policyCalls':len(pol.calls),'postFaultDecisionStates':len(post),'stateRefreshedAfterFault':refreshed,
         'postFaultStateHasActualInventory':state_has_inventory,'postFaultStateHasDesiredPortfolio':desired_present,
         'postFaultStateHasFailureMemory':state_has_memory,'maxConsecutiveReject':max_rej,'maxConsecutiveNoFill':max_nf,
         'unresolvedReturns':r['lifecycleAudit']['unresolvedTakerReturns'],'ownershipEvents':r['lifecycleAudit']['ownershipEventCounts'],
         'cycleInvariantViolationCount':r['cycleInvariantViolationCount'],'postFaultStates':post[:8],
         'terminalPnlAuditOnly':r['actualExecution']['realizedPnl'],'terminalFloorAuditOnly':r['actualExecution']['finalPortfolio']['worst_case_floor']}

def main():
 warnings.filterwarnings('ignore')
 ap=argparse.ArgumentParser();ap.add_argument('--market-id',type=int,default=1579674);ap.add_argument('--output',default='hft_r2_fault_state_refresh_exam_v1_report.json');a=ap.parse_args()
 cases=[('SINGLE_REJECT',['SUBMIT_REJECT']),('DOUBLE_REJECT',['SUBMIT_REJECT','SUBMIT_REJECT']),('TRIPLE_REJECT',['SUBMIT_REJECT']*3),
        ('SINGLE_NO_FILL',['NO_FILL_STALL']),('DOUBLE_NO_FILL',['NO_FILL_STALL']*2),('MIXED_REJECT_NO_FILL',['SUBMIT_REJECT','NO_FILL_STALL'])]
 rows=[]
 for name,modes in cases:
  row=run_case(a.market_id,name,modes);rows.append(row);print(json.dumps({k:row[k] for k in ['case','faultsInjected','postFaultDecisionStates','maxConsecutiveReject','maxConsecutiveNoFill','cycleInvariantViolationCount']},ensure_ascii=False),flush=True)
 summary={'cases':len(rows),'allStateRefresh':all(r['stateRefreshedAfterFault'] for r in rows),'allActualInventoryVisible':all(r['postFaultStateHasActualInventory'] for r in rows),'allDesiredVisible':all(r['postFaultStateHasDesiredPortfolio'] for r in rows),'allFailureMemoryVisible':all(r['postFaultStateHasFailureMemory'] for r in rows),'zeroInvariantViolations':all(r['cycleInvariantViolationCount']==0 for r in rows),'casesWithMultipleFaultsActuallyInjected':sum(r['faultInjectionCount']>=2 for r in rows),'note':'PnL/floor are audit-only and are not pass criteria.'}
 out={'version':'HFT_R2_FAULT_STATE_REFRESH_EXAM_V1','researchOnly':True,'objective':'Verify autonomous-controller prerequisites under manually injected execution faults: state refresh, actual holdings, desired portfolio, failure memory and safe re-entry. PnL is not a graduation target.','rows':rows,'summary':summary}
 p=OUT/a.output;p.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
