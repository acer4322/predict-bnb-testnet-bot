from __future__ import annotations

import argparse, hashlib, json, math, sys, warnings
from pathlib import Path
from typing import Any
import joblib

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke
from tools.hft_r2_execution_incident_notification_exam_v1 import ExecutionIncidentNotifier, HftIncidentBridge, IncidentReceiverProbe
from tools.train_hft_r21_lifecycle_belief_v1 import RecordingIncidentBridge
from tools.train_hft_r21_obligation_residual_belief_v2 import R21ObligationResidualInbox, FirstNMakerFaults

OUT = ROOT / 'data/research/execution_aware_fill_lifecycle_v0'
MODEL_PATH = OUT / 'hft_r21_obligation_residual_belief_v2.joblib'
VERSION = 'HFT_R21_COOPERATION_RESPONSE_BELIEF_CURRICULUM_V1'
BRANCHES = {
    'NATIVE_PASSIVE': None,
    'FORMAL_WAIT': 'WAIT_FOR_CLARITY',
    'ACTIVE_ONCE': 'REPLACE_ROUTE',
}
FAULTS = ('SUBMIT_REJECT', 'NO_FILL_STALL')
EPS = 1e-9


def finite(v: Any, default=0.0) -> float:
    try:
        x = float(v)
    except Exception:
        return float(default)
    return x if math.isfinite(x) else float(default)


def stable_hash(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def compact_belief(inbox: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(inbox, dict):
        return None
    pack = inbox.get('obligationResidualBeliefs') or {}
    obs = pack.get('obligations') or []
    if not obs:
        return None
    # Oldest still-live obligation is the cooperation anchor; fixed before branch divergence.
    live = [o for o in obs if finite(o.get('residualQty')) > EPS]
    if not live:
        return None
    o = sorted(live, key=lambda r: (int(r.get('faultAtMs') or 0), str(r.get('obligationId') or '')))[0]
    return {
        'obligationId': str(o.get('obligationId') or ''),
        'faultType': str(o.get('faultType') or ''),
        'faultAtMs': int(o.get('faultAtMs') or 0),
        'side': str(o.get('side') or ''),
        'originalQty': finite(o.get('originalQty')),
        'progressQty': finite(o.get('progressQty')),
        'residualQty': finite(o.get('residualQty')),
        'probability': finite(o.get('probability'), finite(o.get('modelProbability'))),
        'informationOnly': bool(pack.get('informationOnly', True)),
        'actionAuthority': bool(pack.get('actionAuthority', False)),
    }


class CooperationPolicy:
    def __init__(self, branch: str, provider) -> None:
        self.branch = branch
        self.provider = provider
        self.applied = False
        self.anchor = None
        self.calls = 0

    def __call__(self, state: dict[str, Any]) -> str:
        self.calls += 1
        default_action = str(state.get('defaultAction') or 'WAIT_FOR_CLARITY')
        at_ms = int(state.get('atMs') or 0)
        candidates = [r for r in self.provider.rows if int(r.get('atMs') or 0) <= at_ms and finite(r.get('residualQty')) > EPS]
        if not candidates:
            return default_action
        r = sorted(candidates, key=lambda x: (int(x.get('atMs') or 0), int(x.get('faultAtMs') or 0), str(x.get('obligationId') or '')))[-1]
        belief = {
            'obligationId': str(r.get('obligationId') or ''), 'faultType': str(r.get('faultType') or ''),
            'faultAtMs': int(r.get('faultAtMs') or 0), 'side': str(r.get('side') or ''),
            'originalQty': finite((r.get('features') or {}).get('obligation_original_qty')),
            'progressQty': finite((r.get('features') or {}).get('obligation_progress_qty')),
            'residualQty': finite(r.get('residualQty')),
            'probability': finite(r.get('onlineModelProbability')),
            'informationOnly': True, 'actionAuthority': False, 'beliefAsOfMs': int(r.get('atMs') or 0),
        }
        if self.anchor is None:
            features = state.get('features') or {}
            self.anchor = {
                'atMs': int(state.get('atMs') or 0),
                'side': str(state.get('side') or ''),
                'trackingError': finite(state.get('trackingError')),
                'checkpointDelayMs': int(state.get('checkpointDelayMs') or 0),
                'features': features,
                'belief': belief,
                'stateHash': stable_hash({
                    'atMs': int(state.get('atMs') or 0),
                    'side': str(state.get('side') or ''),
                    'trackingError': finite(state.get('trackingError')),
                    'features': features,
                    'belief': belief,
                }),
            }
        if self.applied:
            return 'WAIT_FOR_CLARITY'
        self.applied = True
        if self.branch == 'NATIVE_PASSIVE':
            return default_action
        return str(BRANCHES[self.branch])


def run_branch(market_id: int, fault: str, branch: str) -> dict[str, Any]:
    artifact = joblib.load(MODEL_PATH)
    model = artifact['model'] if isinstance(artifact, dict) else artifact
    receiver = IncidentReceiverProbe()
    notifier = ExecutionIncidentNotifier(receiver)
    bridge = RecordingIncidentBridge(HftIncidentBridge(notifier))
    provider = R21ObligationResidualInbox(f'R21_COOP_{fault}_{branch}', receiver, bridge, model=model)
    faults = FirstNMakerFaults(fault, count=3)
    policy = CooperationPolicy(branch, provider)
    report = run_smoke(
        market_id,
        passive_mode='wait',
        passive_program={'PASSIVE_MAINTAIN': 'offset0', 'PASSIVE_REPAIR': 'offset0'},
        own_state_poll_ms=250,
        maker_submit_fault_override=faults,
        fault_reentry_enabled=True,
        behavior_policy_override=bridge,
        behavior_ownstate_reentry=False,
        lifecycle_action_override=policy,
        allowed_executor_taker_kinds={'FROZEN_R2', 'PAIR_COMPLETION_REPLACE'},
        trace_execution_states=True,
        fault_no_fill_stall_ms=15_000 if fault == 'NO_FILL_STALL' else None,
        r21_incident_inbox_provider=provider,
    )
    actual = report['actualExecution']
    portfolio = actual['finalPortfolio']
    return {
        'marketId': market_id,
        'fault': fault,
        'branch': branch,
        'makerFaultsUsed': faults.used,
        'policyCalls': policy.calls,
        'responseApplied': policy.applied,
        'anchor': policy.anchor,
        'terminal': {
            'finalAbsTrackingError': finite(actual.get('finalAbsTrackingError')),
            'trackingErrorAreaShareSeconds': finite(actual.get('targetErrorAreaShareSeconds')),
            'pairedCoverage': finite(portfolio.get('combined_paired_coverage')),
            'absPayoffGap': finite(portfolio.get('abs_payoff_gap')),
            'worstCaseFloor': finite(portfolio.get('worst_case_floor')),
        },
        'cycleInvariantViolationCount': int(report.get('cycleInvariantViolationCount') or 0),
        'semanticGate': report.get('semanticGate') or {},
        'r21': {
            'modelOutputCount': provider.model_output_count,
            'strictPastViolations': provider.strict_past_violations,
        },
    }


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out=[]
    for market_id, fault in sorted({(r['marketId'], r['fault']) for r in rows}):
        g=[r for r in rows if r['marketId']==market_id and r['fault']==fault]
        by={r['branch']:r for r in g}
        if set(by)!=set(BRANCHES):
            continue
        hashes={r['anchor']['stateHash'] for r in g if r.get('anchor')}
        beliefs={json.dumps(r['anchor']['belief'],sort_keys=True) for r in g if r.get('anchor')}
        usable=(len(hashes)==1 and len(beliefs)==1 and all(r['responseApplied'] for r in g)
                and all(r['cycleInvariantViolationCount']==0 for r in g)
                and all(not r['r21']['strictPastViolations'] for r in g)
                and all(r['anchor']['belief']['actionAuthority'] is False for r in g if r.get('anchor')))
        tracking={b:by[b]['terminal']['finalAbsTrackingError'] for b in BRANCHES}
        area={b:by[b]['terminal']['trackingErrorAreaShareSeconds'] for b in BRANCHES}
        # Recovery teacher: lexicographic terminal residual first, then error area, then preserve native/passive on tie.
        oracle=min(BRANCHES, key=lambda b:(tracking[b], area[b], b!='NATIVE_PASSIVE')) if usable else None
        out.append({
            'marketId':market_id,'fault':fault,'usable':usable,
            'matchedAnchor':len(hashes)==1,'matchedFrozenBelief':len(beliefs)==1,
            'anchor':by['NATIVE_PASSIVE'].get('anchor'),
            'trackingByBranch':tracking,'areaByBranch':area,
            'floorAuditByBranch':{b:by[b]['terminal']['worstCaseFloor'] for b in BRANCHES},
            'oracleRecoveryAction':oracle,
        })
    return out


def main():
    warnings.filterwarnings('ignore')
    ap=argparse.ArgumentParser()
    ap.add_argument('--market-ids',required=True)
    ap.add_argument('--output',default='hft_r21_cooperation_response_belief_curriculum_v1_report.json')
    args=ap.parse_args()
    ids=[int(x) for x in args.market_ids.split(',') if x.strip()]
    rows=[]
    for m in ids:
        for f in FAULTS:
            for b in BRANCHES:
                r=run_branch(m,f,b); rows.append(r)
                print(json.dumps({'marketId':m,'fault':f,'branch':b,'anchor':bool(r['anchor']),'tracking':r['terminal']['finalAbsTrackingError'],'area':r['terminal']['trackingErrorAreaShareSeconds'],'belief':None if not r['anchor'] else r['anchor']['belief']['probability'],'violations':r['cycleInvariantViolationCount']},ensure_ascii=False),flush=True)
    contexts=summarize(rows)
    usable=[c for c in contexts if c['usable']]
    payload={
        'version':VERSION,'researchOnly':True,'graduationEligible':False,
        'frozenBeliefModel':str(MODEL_PATH.relative_to(ROOT)),
        'r21ExecutionAuthority':False,
        'marketIds':ids,'faults':list(FAULTS),'branches':BRANCHES,
        'rows':rows,'contexts':contexts,
        'summary':{
            'runs':len(rows),'contexts':len(contexts),'usableContexts':len(usable),
            'oracleCounts':{b:sum(c['oracleRecoveryAction']==b for c in usable) for b in BRANCHES},
            'strictPastViolationRuns':sum(bool(r['r21']['strictPastViolations']) for r in rows),
            'semanticViolationRuns':sum(r['cycleInvariantViolationCount']!=0 for r in rows),
        },
        'teacher':'matched HFT recovery quality: minimize terminal absolute tracking residual, then tracking-error area; PnL/floor audit only',
    }
    (OUT/args.output).write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'ok':True,'output':str(OUT/args.output),**payload['summary']},ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
