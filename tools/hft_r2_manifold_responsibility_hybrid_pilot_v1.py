from __future__ import annotations
import argparse,copy,json,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke
from tools.hft_r2_fault_state_refresh_exam_v1 import SequenceFault
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
CHUNK=18.0

def f(v,d=0.0):
 try:
  x=float(v);return x if math.isfinite(x) else d
 except Exception:return d

def actual_side(s,side):
 p=s.get('actualPortfolio') or {};g=f(p.get('combined_gross'));n=f(p.get('combined_net'))
 return max(0.0,(g+n)/2 if side=='UP' else (g-n)/2)

class HybridPolicy:
 """Research-only hybrid pilot, not a candidate strategy.
 Pre-fault: manifold-style containment when abs combined net >= 54 shares.
 Post-fault: responsibility-style freeze/rebuild; after 2 consecutive faults rebase to confirmed actual and WAIT.
 """
 def __init__(self):self.calls=[];self.containment=False
 def __call__(self,x):
  s=copy.deepcopy(x['executionState']);m=copy.deepcopy(s.get('behaviorMemory') or {});p=s.get('actualPortfolio') or {}
  net=abs(f(p.get('combined_net')));faults=int(m.get('consecutiveReject') or 0)+int(m.get('consecutiveNoFill') or 0)+int(m.get('consecutivePartial') or 0)
  rec={'atMs':int(x['atMs']),'trigger':x.get('trigger'),'net':net,'faults':faults,'lastOutcome':m.get('lastOutcome'),'actual':copy.deepcopy(p),'desired':copy.deepcopy(x.get('desiredPortfolio'))};self.calls.append(rec)
  if faults>0:
   self.containment=True
   if faults>=2:
    return {'desiredUP':actual_side(s,'UP'),'desiredDOWN':actual_side(s,'DOWN'),'executionMode':'WAIT','freezeNewEconomicIntents':True}
   return {'executionMode':'WAIT','freezeNewEconomicIntents':True}
  # manifold-style pre-fault containment: stop adding new economic intent once inventory is materially one-sided.
  if net>=3*CHUNK-1e-9:
   self.containment=True
   return {'executionMode':'WAIT','freezeNewEconomicIntents':True}
  if self.containment and net<=CHUNK+1e-9:
   self.containment=False
   return {'freezeNewEconomicIntents':False}
  return None

def run_case(mid,plan):
 pol=HybridPolicy();inj=SequenceFault(plan)
 r=run_smoke(mid,passive_mode='wait',passive_program={'PASSIVE_MAINTAIN':'offset0','PASSIVE_REPAIR':'offset0'},own_state_poll_ms=250,taker_submit_fault_override=inj,fault_reentry_enabled=True,behavior_policy_override=pol,behavior_ownstate_reentry=True,allowed_executor_taker_kinds={'FROZEN_R2','PAIR_COMPLETION_REPLACE'},trace_execution_states=True)
 return {'marketId':mid,'faultPlan':plan,'faultsInjected':inj.used,'floor':r['actualExecution']['finalPortfolio']['worst_case_floor'],'pnlAuditOnly':r['actualExecution']['realizedPnl'],'absTracking':r['actualExecution']['finalAbsTrackingError'],'takerFilled':r['actualExecution']['takerFilledShares'],'violations':r['cycleInvariantViolationCount'],'policyCalls':len(pol.calls),'maxAbsNetSeen':max([x['net'] for x in pol.calls] or [0.0]),'faultStateSeen':any(x['faults']>0 for x in pol.calls)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--market-ids',default='1576991,1579313,1579674');ap.add_argument('--output',default='hft_r2_manifold_responsibility_hybrid_pilot_v1_report.json');a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 plans=[[],['SUBMIT_REJECT'],['SUBMIT_REJECT','SUBMIT_REJECT'],['NO_FILL_STALL','NO_FILL_STALL'],['SUBMIT_REJECT','NO_FILL_STALL']]
 rows=[]
 for mid in mids:
  for plan in plans:
   row=run_case(mid,plan);rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
 base={r['marketId']:r for r in rows if not r['faultPlan']}
 for r in rows:
  b=base[r['marketId']];r['faultAddedDamage']=float(r['floor'])-float(b['floor']) if r['faultPlan'] else 0.0
 summary={'markets':len(mids),'runs':len(rows),'zeroViolations':all(r['violations']==0 for r in rows),'baselineFloors':{str(k):v['floor'] for k,v in base.items()},'faultAddedDamageMin':min([r['faultAddedDamage'] for r in rows if r['faultPlan']] or [0]),'faultAddedDamageMean':sum(r['faultAddedDamage'] for r in rows if r['faultPlan'])/max(1,sum(bool(r['faultPlan']) for r in rows)),'absoluteFaultFloorMin':min([r['floor'] for r in rows if r['faultPlan']] or [0])}
 out={'version':'HFT_R2_MANIFOLD_RESPONSIBILITY_HYBRID_PILOT_V1','researchOnly':True,'priorArtCheck':'No prior formal manifold+fault-responsibility hybrid report found; components existed separately.','rows':rows,'summary':summary};(OUT/a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
