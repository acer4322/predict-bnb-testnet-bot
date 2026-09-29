from __future__ import annotations
import copy,json,math,sys,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke
from tools.hft_r2_maker_fault_state_refresh_exam_v1 import SequenceMakerFault
from tools.hft_r2_state_based_reopen_candidate_v1 import StateBasedReopenPolicy, side_shares, f, CHUNK
OUT=ROOT/'data/research/hourly_novel_tests'
MIDS=[1576991,1579313,1579674]
PLANS=[['SUBMIT_REJECT','NO_FILL_STALL','SUBMIT_REJECT'],['NO_FILL_STALL','SUBMIT_REJECT','NO_FILL_STALL']]

class FaultBudgetEscalationPolicy:
    def __init__(self):
        self.calls=[]; self.containment=False; self.reopened=False; self.freeze_started=False; self.stage2_calls=0; self.stage3_calls=0
    def __call__(self,x):
        s=copy.deepcopy(x['executionState']); m=copy.deepcopy(s.get('behaviorMemory') or {}); a=side_shares(s)
        net=a['NET']; absnet=abs(net)
        faults=int(m.get('consecutiveReject') or 0)+int(m.get('consecutiveNoFill') or 0)+int(m.get('consecutivePartial') or 0)
        unresolved=any(v is not None for v in (s.get('remainderOwner') or {}).values()) or bool(s.get('pendingReplace'))
        risk='UP' if net>0 else 'DOWN' if net<0 else None; repair='DOWN' if net>0 else 'UP' if net<0 else None
        self.calls.append({'atMs':int(x['atMs']),'faults':faults,'net':net,'unresolved':unresolved,'containment':self.containment})
        if self.containment and absnet<=CHUNK+1e-9 and not unresolved:
            self.containment=False; self.reopened=True
            return {'freezeNewEconomicIntents':False,'freezeFrozenR2TakerIntents':False,'freezeMakerExecutionChildren':False}
        if faults>=3:
            self.containment=True; self.freeze_started=True; self.stage3_calls+=1
            return {'desiredUP':a['UP'],'desiredDOWN':a['DOWN'],'executionMode':'WAIT','freezeNewEconomicIntents':True,'freezeFrozenR2TakerIntents':True,'freezeMakerExecutionChildren':False}
        if faults>=1:
            # Stage 1/2: preserve exactly one passive corrective chunk, but never increase the current risk side.
            if faults==2: self.stage2_calls+=1
            p={'executionMode':'KEEP_PASSIVE','freezeNewEconomicIntents':False,'freezeFrozenR2TakerIntents':True,'freezeMakerExecutionChildren':False}
            if risk: p['desired'+risk]=a[risk]
            if repair:
                desired=copy.deepcopy(x.get('desiredPortfolio') or {})
                p['desired'+repair]=max(a[repair],min(f(desired.get(repair),a[repair]+CHUNK),a[repair]+CHUNK))
            return p
        # Same pre-fault soft risk cap as current state-based candidate.
        if risk and absnet>=3*CHUNK-1e-9:
            desired=copy.deepcopy(x.get('desiredPortfolio') or {})
            return {'desired'+risk:a[risk],'desired'+repair:max(a[repair],min(f(desired.get(repair),a[repair]+CHUNK),a[repair]+CHUNK)) if repair else None,'executionMode':'KEEP_PASSIVE'}
        return None

def execute(mid,plan,policy_cls):
    inj=SequenceMakerFault(plan); pol=policy_cls()
    r=run_smoke(mid,passive_mode='wait',passive_program={'PASSIVE_MAINTAIN':'offset0','PASSIVE_REPAIR':'offset0'},own_state_poll_ms=250,maker_submit_fault_override=inj,fault_reentry_enabled=True,behavior_policy_override=pol,behavior_ownstate_reentry=True,trace_execution_states=True)
    ex=r['actualExecution']; fp=ex['finalPortfolio']
    calls=[c for c in pol.calls if c.get('faults',0)>0]
    return {
        'marketId':mid,'faultPlan':plan,'faultsInjected':list(inj.used),'faultInjectionCount':len(inj.used),
        'targetErrorArea':float(ex['targetErrorAreaShareSeconds']),'finalAbsTrackingError':float(ex['finalAbsTrackingError']),
        'floorAuditOnly':float(fp['worst_case_floor']),'pnlAuditOnly':float(ex['realizedPnl']),
        'cycleInvariantViolations':int(r['cycleInvariantViolationCount']),'postFaultPolicyCalls':len(calls),
        'freezeStarted':bool(getattr(pol,'freeze_started',False)),'reopened':bool(getattr(pol,'reopened',False)),
        'stage2Calls':int(getattr(pol,'stage2_calls',0)),'stage3Calls':int(getattr(pol,'stage3_calls',0)),
        'makerFilledShares':float(ex['makerFilledShares']),'takerFilledShares':float(ex['takerFilledShares'])
    }

def med(xs): return statistics.median(xs) if xs else None

def main():
    rows=[]
    for mid in MIDS:
        for plan in PLANS:
            base=execute(mid,plan,StateBasedReopenPolicy)
            cand=execute(mid,plan,FaultBudgetEscalationPolicy)
            row={'marketId':mid,'faultPlan':plan,'baseline':base,'candidate':cand,
                 'candidateMinusBaselineTargetErrorArea':cand['targetErrorArea']-base['targetErrorArea'],
                 'candidateAreaRatio':cand['targetErrorArea']/base['targetErrorArea'] if base['targetErrorArea']>1e-9 else None,
                 'candidateMinusBaselineAbsTracking':cand['finalAbsTrackingError']-base['finalAbsTrackingError']}
            rows.append(row); print(json.dumps(row,ensure_ascii=False),flush=True)
    applicable=[r for r in rows if r['candidate']['faultInjectionCount']>0]
    triple=[r for r in applicable if r['candidate']['faultInjectionCount']==3]
    ratios=[r['candidateAreaRatio'] for r in triple if r['candidateAreaRatio'] is not None and math.isfinite(r['candidateAreaRatio'])]
    zero=all(r['candidate']['cycleInvariantViolations']==0 for r in applicable)
    triple_rate=len(triple)/len(applicable) if applicable else 0.0
    hard_all=all(r['candidate']['freezeStarted'] and r['candidate']['stage3Calls']>0 for r in triple) if triple else False
    median_ratio=med(ratios)
    keep=(len(triple)>=4 and zero and triple_rate>=0.75 and hard_all and median_ratio is not None and median_ratio<=1.20)
    if keep: status='TESTED_KEEP_SIGNAL'
    elif (not zero) or (triple and (not hard_all or (median_ratio is not None and median_ratio>1.20))): status='TESTED_REJECTED'
    else: status='TESTED_INCONCLUSIVE'
    summary={'runs':len(rows),'applicable':len(applicable),'tripleFaultCases':len(triple),'tripleFaultRate':triple_rate,'zeroInvariantViolations':zero,'hardContainmentAfterThirdAllTriple':hard_all,'reopenedTripleCases':sum(r['candidate']['reopened'] for r in triple),'medianCandidateAreaRatioVsBaseline':median_ratio,'medianCandidateMinusBaselineAbsTracking':med([r['candidateMinusBaselineAbsTracking'] for r in triple]),'status':status}
    rep={'testId':'HFT_R2_FAULT_BUDGET_ESCALATION_V1','axis':'R2_AUTONOMOUS_REPAIR_FAULT_BUDGET_ESCALATION','researchOnly':True,'liveTradingChanges':False,'summary':summary,'rows':rows,'interpretation':('Three-stage fault-budget repair visibly exercises a third mixed execution failure, then hard-containment/rebase and safe-state reopen without lifecycle violations.' if keep else 'The three-stage method did not satisfy the preregistered autonomous-recovery gate; do not promote it.')}
    p=OUT/'hft_r2_fault_budget_escalation_v1_report.json'; p.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
