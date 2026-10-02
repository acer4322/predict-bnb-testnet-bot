from __future__ import annotations

import argparse, json
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in __import__('sys').path: __import__('sys').path.insert(0,str(ROOT))
from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke

OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
VERSION='HFT_R21_LONG_RESTING_ACTION_PILOT_V1'
EPS=1e-9

class PatiencePolicy:
    def __init__(self, threshold_ms: int|None):
        self.threshold_ms=threshold_ms
        self.retire_applied=False
        self.passive_return_applied=False
        self.anchor=None
        self.calls=0
    def __call__(self,state:dict[str,Any])->str:
        self.calls+=1
        default=str(state.get('defaultAction') or 'WAIT_FOR_CLARITY')
        if self.threshold_ms is None: return default
        ex=state.get('executionState') or {}
        side=str(state.get('side') or '')
        active=((ex.get('activeMakerChildren') or {}).get(side) or {})
        owner=((ex.get('remainderOwner') or {}).get(side) or {})
        pending=((ex.get('pendingReplace') or {}).get(side) or {})
        if not self.retire_applied:
            age=int(active.get('ageMs') or 0)
            status=str(active.get('status') or '')
            leaves=float(active.get('leavesQty') or 0.0)
            if active and age>=self.threshold_ms and leaves>EPS and status in {'NEW','PARTIALLY_FILLED','NONE'}:
                self.anchor={'atMs':int(state.get('atMs') or 0),'side':side,'ageMs':age,'status':status,'leavesQty':leaves,'trackingError':float(state.get('trackingError') or 0.0),'secondsLeft':float((state.get('features') or {}).get('secondsLeft') or 0.0)}
                self.retire_applied=True
                return 'RETIRE_OBLIGATION'
            return default
        if self.retire_applied and not self.passive_return_applied:
            returned=str(owner.get('state') or '')=='RETURNED_UNRESOLVED'
            no_live=not active
            no_pending=not pending
            if returned and no_live and no_pending:
                self.passive_return_applied=True
                return 'RETURN_TO_PASSIVE_REPAIR'
        return 'WAIT_FOR_CLARITY'

def run_branch(mid:int,th:int|None)->dict[str,Any]:
    p=PatiencePolicy(th)
    r=run_smoke(mid,passive_mode='wait',passive_program={'PASSIVE_MAINTAIN':'offset0','PASSIVE_REPAIR':'offset0'},own_state_poll_ms=250,lifecycle_action_override=p,allowed_executor_taker_kinds={'FROZEN_R2','PAIR_COMPLETION_REPLACE'},trace_execution_states=True)
    a=r['actualExecution']; port=a['finalPortfolio']; lc=r['lifecycleAudit']
    return {'marketId':mid,'thresholdSec':None if th is None else th/1000,'policyCalls':p.calls,'retireApplied':p.retire_applied,'passiveReturnApplied':p.passive_return_applied,'anchor':p.anchor,
      'terminal':{'trackingErrorArea':float(a['targetErrorAreaShareSeconds']),'finalAbsTrackingError':float(a['finalAbsTrackingError']),'makerFilledShares':float(a['makerFilledShares']),'takerFilledShares':float(a['takerFilledShares']),'pairedCoverage':float(port.get('combined_paired_coverage') or 0.0),'worstCaseFloorAudit':float(port.get('worst_case_floor') or 0.0),'realizedPnlAudit':a.get('realizedPnl')},
      'mechanism':{'actionCounts':lc['actionCounts'],'ownershipEventCounts':lc['ownershipEventCounts'],'cancelAckEvidenceCounts':lc['cancelAckEvidenceCounts'],'passiveReturnBlocked':sum(x.get('action')=='PASSIVE_RETURN_BLOCKED_LIVE_CHILD' for x in lc['decisions'])},
      'violations':int(r['cycleInvariantViolationCount']),'semanticGate':r['semanticGate']}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--markets',default='1663753,1569361'); ap.add_argument('--thresholds',default='60,90,120'); ap.add_argument('--output',default='hft_r21_long_resting_action_pilot_v1_report.json'); args=ap.parse_args()
    mids=[int(x) for x in args.markets.split(',') if x.strip()]; ths=[int(x)*1000 for x in args.thresholds.split(',') if x.strip()]
    rows=[]
    for m in mids:
      for th in [None,*ths]:
        x=run_branch(m,th); rows.append(x); print(json.dumps({'market':m,'th':x['thresholdSec'],'retire':x['retireApplied'],'return':x['passiveReturnApplied'],'area':x['terminal']['trackingErrorArea'],'resid':x['terminal']['finalAbsTrackingError'],'maker':x['terminal']['makerFilledShares'],'viol':x['violations']},ensure_ascii=False),flush=True)
    summary=[]
    for m in mids:
      g=[x for x in rows if x['marketId']==m]; base=next(x for x in g if x['thresholdSec'] is None)
      for x in g:
        if x is base: continue
        ba=base['terminal']['trackingErrorArea']; br=base['terminal']['finalAbsTrackingError']
        summary.append({'marketId':m,'thresholdSec':x['thresholdSec'],'retireApplied':x['retireApplied'],'passiveReturnApplied':x['passiveReturnApplied'],'areaReduction':None if ba<=EPS else (ba-x['terminal']['trackingErrorArea'])/ba,'residualReduction':None if br<=EPS else (br-x['terminal']['finalAbsTrackingError'])/br,'makerFillDelta':x['terminal']['makerFilledShares']-base['terminal']['makerFilledShares'],'pnlAuditDelta':None if x['terminal']['realizedPnlAudit'] is None or base['terminal']['realizedPnlAudit'] is None else x['terminal']['realizedPnlAudit']-base['terminal']['realizedPnlAudit'],'violations':x['violations'],'passiveReturnBlocked':x['mechanism']['passiveReturnBlocked']})
    payload={'version':VERSION,'researchOnly':True,'graduationEligible':False,'liveTradingChanges':False,'markets':mids,'thresholdsSec':[x/1000 for x in ths],'branches':'native WAIT vs safe RETIRE(cancel+terminal ACK)+RETURN_TO_PASSIVE_REPAIR','rows':rows,'comparisons':summary,'decision':'PILOT_COMPLETE_REVIEW_RESULTS'}
    (OUT/args.output).write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'ok':True,'output':str(OUT/args.output),'comparisons':summary},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
