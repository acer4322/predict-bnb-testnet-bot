from __future__ import annotations
import argparse,copy,json,sys,warnings
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke
from tools.hft_r2_fault_state_refresh_exam_v1 import SequenceFault
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
CASES=[('SINGLE_REJECT',['SUBMIT_REJECT']),('DOUBLE_REJECT',['SUBMIT_REJECT']*2),('TRIPLE_REJECT',['SUBMIT_REJECT']*3),('SINGLE_NO_FILL',['NO_FILL_STALL']),('DOUBLE_NO_FILL',['NO_FILL_STALL']*2),('MIXED_REJECT_NO_FILL',['SUBMIT_REJECT','NO_FILL_STALL'])]

class LegacyResponsibilitySupervisorAdapter:
    """Ports CAP100_RESPONSIBILITY_SUPERVISOR_CONTRACT_V1 semantics to the current behavior hook.
    It is not a new learned policy: responsibility persists; rejected/no-fill work is not forgotten;
    after a visible failure the controller stops creating new exposure and preserves/reconciles the existing target.
    The old contract had escalation states, but this adapter deliberately does not invent a new escalation rule
    that was not specified quantitatively in the legacy artifact.
    """
    def __init__(self):self.calls=[]
    def __call__(self,x:dict[str,Any]):
        s=copy.deepcopy(x['executionState']);m=copy.deepcopy(s.get('behaviorMemory') or {})
        faults=int(m.get('consecutiveReject') or 0)+int(m.get('consecutiveNoFill') or 0)+int(m.get('consecutivePartial') or 0)
        self.calls.append({'atMs':x['atMs'],'trigger':x.get('trigger'),'actual':s.get('actualPortfolio'),'desired':x.get('desiredPortfolio'),'memory':m,'faultCount':faults})
        if len(self.calls)==1:return {'executionMode':'ACTIVE_REPAIR'}
        if faults>0:
            # Legacy contract semantics: preserve responsibility, freeze/reconcile instead of silently dropping it.
            return {'executionMode':'WAIT'}
        return None

def grade(r):
    if not r['faultsInjected']:return 'NOT_APPLICABLE'
    return 'PASS' if r['stateRefresh'] and r['memoryVisible'] and r['inventoryVisible'] and r['violations']==0 else 'FAIL'

def run_case(mid,name,modes):
    inj=SequenceFault(modes);pol=LegacyResponsibilitySupervisorAdapter()
    r=run_smoke(mid,passive_mode='wait',passive_program={'PASSIVE_MAINTAIN':'offset0','PASSIVE_REPAIR':'offset0'},own_state_poll_ms=250,taker_submit_fault_override=inj,fault_reentry_enabled=True,behavior_policy_override=pol,behavior_ownstate_reentry=True,allowed_executor_taker_kinds={'FROZEN_R2','PAIR_COMPLETION_REPLACE'},trace_execution_states=True)
    post=[c for c in pol.calls if c['faultCount']>0]
    out={'marketId':mid,'case':name,'faultPlan':modes,'faultsInjected':inj.used,'stateRefresh':bool(post),'memoryVisible':all((c['memory'] or {}).get('lastOutcome') is not None for c in post) if post else False,'inventoryVisible':all(isinstance(c.get('actual'),dict) and 'combined_gross' in c['actual'] for c in post) if post else False,'postFaultStates':len(post),'violations':r['cycleInvariantViolationCount'],'floorAuditOnly':r['actualExecution']['finalPortfolio']['worst_case_floor'],'pnlAuditOnly':r['actualExecution']['realizedPnl'],'finalTrackingAuditOnly':r['actualExecution']['finalAbsTrackingError'],'unresolvedReturns':r['lifecycleAudit']['unresolvedTakerReturns']}
    out['grade']=grade(out);return out

def main():
    warnings.filterwarnings('ignore');ap=argparse.ArgumentParser();ap.add_argument('--market-ids',default='1579674,1579313');ap.add_argument('--output',default='hft_r2_legacy_responsibility_supervisor_exam_v1_report.json');a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    for mid in mids:
      for name,modes in CASES:
        r=run_case(mid,name,modes);rows.append(r);print(json.dumps({'marketId':mid,'case':name,'grade':r['grade'],'injected':r['faultsInjected'],'floorAuditOnly':r['floorAuditOnly'],'violations':r['violations']},ensure_ascii=False),flush=True)
    app=[r for r in rows if r['grade']!='NOT_APPLICABLE'];floors=[float(r['floorAuditOnly']) for r in app if r['grade']=='PASS']
    s={'candidate':'LEGACY_CAP100_RESPONSIBILITY_SUPERVISOR_PORT','markets':len(mids),'cases':len(rows),'applicable':len(app),'pass':sum(r['grade']=='PASS' for r in rows),'fail':sum(r['grade']=='FAIL' for r in rows),'notApplicable':sum(r['grade']=='NOT_APPLICABLE' for r in rows),'auditFloorMin':min(floors) if floors else None,'auditFloorMean':sum(floors)/len(floors) if floors else None,'auditFloorMax':max(floors) if floors else None,'interpretation':'Structural legacy replay under the new examiner. PnL/floor are audit-only; this adapter preserves the old responsibility/freeze semantics and does not invent a learned escalation rule.'}
    (OUT/a.output).write_text(json.dumps({'version':'HFT_R2_LEGACY_RESPONSIBILITY_SUPERVISOR_EXAM_V1','sourceContract':'cap100_responsibility_supervisor_contract_v1.json','rows':rows,'summary':s},ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps(s,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
