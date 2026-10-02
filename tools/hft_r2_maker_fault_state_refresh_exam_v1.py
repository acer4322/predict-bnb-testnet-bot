from __future__ import annotations
import argparse,copy,json,math,sys,warnings
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'

class SequenceMakerFault:
    def __init__(self,modes:list[str]):self.modes=list(modes);self.used=[];self.calls=[]
    def __call__(self,s:dict[str,Any]):
        self.calls.append(copy.deepcopy(s))
        if len(self.used)>=len(self.modes):return None
        mode=self.modes[len(self.used)];self.used.append(mode);return mode

class RefreshPolicy:
    def __init__(self):self.calls=[]
    def __call__(self,x:dict[str,Any]):
        s=copy.deepcopy(x['executionState']);mem=copy.deepcopy(s.get('behaviorMemory') or {})
        self.calls.append({'atMs':int(x['atMs']),'trigger':x.get('trigger'),'actual':copy.deepcopy(s.get('actualSharesBySide')),'desired':copy.deepcopy(x.get('desiredPortfolio')),'tracking':s.get('trackingError'),'memory':mem})
        faults=int(mem.get('consecutiveReject') or 0)+int(mem.get('consecutiveNoFill') or 0)+int(mem.get('consecutivePartial') or 0)
        if faults>0:
            return {'executionMode':'WAIT','freezeFrozenR2TakerIntents':True}
        return None

def run_case(mid:int,name:str,modes:list[str])->dict[str,Any]:
    inj=SequenceMakerFault(modes);pol=RefreshPolicy()
    r=run_smoke(mid,passive_mode='wait',passive_program={'PASSIVE_MAINTAIN':'offset0','PASSIVE_REPAIR':'offset0'},own_state_poll_ms=250,maker_submit_fault_override=inj,fault_reentry_enabled=True,behavior_policy_override=pol,behavior_ownstate_reentry=True,trace_execution_states=True)
    post=[]
    for c in pol.calls:
        m=c['memory'];faults=int(m.get('consecutiveReject') or 0)+int(m.get('consecutiveNoFill') or 0)+int(m.get('consecutivePartial') or 0)
        if faults>0:post.append(c)
    inv=all(isinstance(c.get('actual'),dict) and 'UP' in c['actual'] and 'DOWN' in c['actual'] for c in post) if post else False
    des=all(isinstance(c.get('desired'),dict) and 'UP' in c['desired'] and 'DOWN' in c['desired'] for c in post) if post else False
    mem=all((c.get('memory') or {}).get('lastOutcome') is not None for c in post) if post else False
    return {'marketId':mid,'case':name,'faultPlan':modes,'faultsInjected':inj.used,'faultInjectionCount':len(inj.used),'policyCalls':len(pol.calls),'postFaultDecisionStates':len(post),'stateRefreshedAfterFault':bool(post),'postFaultStateHasActualInventory':inv,'postFaultStateHasDesiredPortfolio':des,'postFaultStateHasFailureMemory':mem,'maxConsecutiveReject':max([int((c['memory'] or {}).get('consecutiveReject') or 0) for c in pol.calls] or [0]),'maxConsecutiveNoFill':max([int((c['memory'] or {}).get('consecutiveNoFill') or 0) for c in pol.calls] or [0]),'cycleInvariantViolationCount':r['cycleInvariantViolationCount'],'terminalFloorAuditOnly':r['actualExecution']['finalPortfolio']['worst_case_floor'],'terminalPnlAuditOnly':r['actualExecution']['realizedPnl'],'postFaultStates':post[:8]}

def grade(r):
    if r['faultInjectionCount']==0:return 'NOT_APPLICABLE'
    structural=r['stateRefreshedAfterFault'] and r['postFaultStateHasActualInventory'] and r['postFaultStateHasDesiredPortfolio'] and r['postFaultStateHasFailureMemory'] and r['cycleInvariantViolationCount']==0
    return 'PASS' if structural else 'FAIL'

def main():
    warnings.filterwarnings('ignore');ap=argparse.ArgumentParser();ap.add_argument('--market-ids',default='1576991,1579313,1579674');ap.add_argument('--output',default='hft_r2_maker_fault_state_refresh_exam_v1_report.json');a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    cases=[('MAKER_SINGLE_REJECT',['SUBMIT_REJECT']),('MAKER_DOUBLE_REJECT',['SUBMIT_REJECT']*2),('MAKER_SINGLE_NO_FILL',['NO_FILL_STALL']),('MAKER_DOUBLE_NO_FILL',['NO_FILL_STALL']*2),('MAKER_MIXED_REJECT_NO_FILL',['SUBMIT_REJECT','NO_FILL_STALL'])]
    rows=[]
    for mid in mids:
        for name,modes in cases:
            r=run_case(mid,name,modes);r['grade']=grade(r);rows.append(r);print(json.dumps({k:r[k] for k in ('marketId','case','grade','faultsInjected','postFaultDecisionStates','maxConsecutiveReject','maxConsecutiveNoFill','cycleInvariantViolationCount','terminalFloorAuditOnly')},ensure_ascii=False),flush=True)
    applicable=[r for r in rows if r['grade']!='NOT_APPLICABLE'];summary={'markets':len(mids),'cases':len(rows),'applicable':len(applicable),'pass':sum(r['grade']=='PASS' for r in rows),'fail':sum(r['grade']=='FAIL' for r in rows),'notApplicable':sum(r['grade']=='NOT_APPLICABLE' for r in rows),'zeroInvariantViolations':all(r['cycleInvariantViolationCount']==0 for r in rows),'role':'Maker-side extension of canonical fault recovery examiner; PnL/floor audit-only.'}
    (OUT/a.output).write_text(json.dumps({'version':'HFT_R2_MAKER_FAULT_STATE_REFRESH_EXAM_V1','rows':rows,'summary':summary},ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
