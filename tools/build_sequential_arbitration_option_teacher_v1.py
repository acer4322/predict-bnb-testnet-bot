from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / 'data/research/execution_aware_fill_lifecycle_v0'
SRC = D / 'pair_completion_sequential_curriculum_v1.jsonl'
OUT = D / 'sequential_arbitration_option_teacher_v1.jsonl'
REPORT = D / 'sequential_arbitration_option_teacher_v1_report.json'
DOM = {'REPLACE_DOMINATES', 'KEEP_DOMINATES'}

CURRENT_FEATURES = [
    'workingRecoveryExists','workingRecoveryAgeMs','workingRecoveryOffsetTicks','workingRecoveryRemainingQty',
    'absTrackingError','trackingError','actualMakerNet','actualCombinedGross','secondsLeft',
    'recoveryBid','recoveryAsk','recoverySpreadTicks','pairAskSum','pairBidSum',
    'marginalSurplusChunkAvgCost','lockedPairEdgePerShare','lastMakerFillAgeMs','lastMakerFillSideIsRecovery',
    'directionTowardRecovery','spotReturn1sTowardRecovery','spotReturn3sTowardRecovery',
    'spotQueueTowardRecovery','spotTaker1sTowardRecovery','futuresReturn1sTowardRecovery',
    'futuresReturn3sTowardRecovery','futuresQueueTowardRecovery','futuresTaker1sTowardRecovery',
]
DELTA_BASES = [
    'absTrackingError','trackingError','actualMakerNet','actualCombinedGross','recoveryBid','recoveryAsk',
    'recoverySpreadTicks','pairAskSum','pairBidSum','workingRecoveryAgeMs','workingRecoveryOffsetTicks',
    'workingRecoveryRemainingQty','lockedPairEdgePerShare','lastMakerFillAgeMs','directionTowardRecovery',
    'spotReturn1sTowardRecovery','spotReturn3sTowardRecovery','spotQueueTowardRecovery','spotTaker1sTowardRecovery',
    'futuresReturn1sTowardRecovery','futuresReturn3sTowardRecovery','futuresQueueTowardRecovery','futuresTaker1sTowardRecovery',
]


def num(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def feature_row(cur: dict[str, Any], prev: dict[str, Any] | None) -> dict[str, Any]:
    f = cur.get('features') or {}
    p = (prev.get('features') or {}) if prev else {}
    out = {k: num(f.get(k)) for k in CURRENT_FEATURES}
    out['asymmetryAgeMs'] = num(cur.get('candidateAsymmetryAgeMs'))
    out['observationDelayMs'] = float(cur.get('delayMs') or 0)
    out['hasPriorObservation'] = float(prev is not None)
    out['elapsedSincePriorMs'] = float(int(cur.get('candidateAtMs') or 0) - int(prev.get('candidateAtMs') or 0)) if prev else 0.0
    for k in DELTA_BASES:
        a = num(f.get(k)); b = num(p.get(k)) if prev else None
        out['delta_'+k] = (a-b) if a is not None and b is not None else None
    # Discrete lifecycle transitions are important evidence, not future labels.
    cur_exists = bool(num(f.get('workingRecoveryExists')) or 0.0)
    prev_exists = bool(num(p.get('workingRecoveryExists')) or 0.0) if prev else cur_exists
    out['recoveryChildAppearedSincePrior'] = float(cur_exists and not prev_exists)
    out['recoveryChildDisappearedSincePrior'] = float(prev_exists and not cur_exists)
    cur_status = str(f.get('workingRecoveryStatus') or 'NONE')
    prev_status = str(p.get('workingRecoveryStatus') or 'NONE') if prev else cur_status
    out['recoveryStatusChanged'] = float(cur_status != prev_status)
    out['recoveryStatusNew'] = float(cur_status == 'NEW')
    out['recoveryStatusPartial'] = float(cur_status == 'PARTIALLY_FILLED')
    return out


def main() -> int:
    rows = [json.loads(x) for x in SRC.read_text(encoding='utf-8').splitlines() if x.strip()]
    by: dict[int,list[dict[str,Any]]] = {}
    for r in rows:
        by.setdefault(int(r['marketId']), []).append(r)
    teacher: list[dict[str,Any]] = []
    market_summary: list[dict[str,Any]] = []
    for mid,z in sorted(by.items(), key=lambda kv: min(int(x['candidateAtMs']) for x in kv[1])):
        z = sorted(z, key=lambda r:int(r['delayMs']))
        first_dom = None
        for i,r in enumerate(z):
            if str(r.get('paretoLabel')) in DOM:
                first_dom = i; break
        if first_dom is None:
            # If execution-only clarity never emerges by the fixed 8s observation horizon,
            # the reachable policy returns authority immediately; later no-action states are censored.
            r = z[0]
            teacher.append({
                'version':'SEQUENTIAL_ARBITRATION_OPTION_TEACHER_V1','marketId':mid,'sequenceStep':0,
                'delayMs':int(r['delayMs']),'candidateAtMs':int(r['candidateAtMs']),'teacherAction':'RETURN_TO_CONTROLLER',
                'teacherActNow':0,'teacherWait':0,'teacherReplaceIfAct':None,'currentParetoLabel':r['paretoLabel'],
                'firstFutureDominance':None,'firstFutureDominanceDelayMs':None,'features':feature_row(r,None),
            })
            market_summary.append({'marketId':mid,'path':'RETURN_TO_CONTROLLER','firstDominanceDelayMs':None})
            continue
        # Reachable states only: WAIT until the first true local Pareto dominance, then ACT and censor the rest.
        for j in range(first_dom+1):
            r = z[j]; prev = z[j-1] if j>0 else None
            lab = str(r.get('paretoLabel'))
            if j < first_dom:
                action = 'WAIT_FOR_CLARITY'; act=0; wait=1; rep=None
            else:
                action = 'REPLACE_ROUTE' if lab=='REPLACE_DOMINATES' else 'KEEP_EXECUTING'
                act=1; wait=0; rep=int(lab=='REPLACE_DOMINATES')
            teacher.append({
                'version':'SEQUENTIAL_ARBITRATION_OPTION_TEACHER_V1','marketId':mid,'sequenceStep':j,
                'delayMs':int(r['delayMs']),'candidateAtMs':int(r['candidateAtMs']),'teacherAction':action,
                'teacherActNow':act,'teacherWait':wait,'teacherReplaceIfAct':rep,'currentParetoLabel':lab,
                'firstFutureDominance':str(z[first_dom]['paretoLabel']),
                'firstFutureDominanceDelayMs':int(z[first_dom]['delayMs']),
                'features':feature_row(r,prev),
            })
        market_summary.append({'marketId':mid,'path':'DELAYED_ACT' if first_dom>0 else 'IMMEDIATE_ACT','firstDominance':z[first_dom]['paretoLabel'],'firstDominanceDelayMs':int(z[first_dom]['delayMs'])})
    OUT.write_text('\n'.join(json.dumps(r,ensure_ascii=False,allow_nan=True) for r in teacher)+'\n',encoding='utf-8')
    from collections import Counter
    action_counts = Counter(str(r['teacherAction']) for r in teacher)
    path_counts = Counter(str(r['path']) for r in market_summary)
    delay_counts = Counter(str(r.get('firstDominanceDelayMs')) for r in market_summary if r.get('firstDominanceDelayMs') is not None)
    report = {
        'version':'SEQUENTIAL_ARBITRATION_OPTION_TEACHER_V1_REPORT','researchOnly':True,'liveTradingChanges':False,
        'source':str(SRC),'markets':len(by),'rows':len(teacher),'actionCounts':dict(action_counts),'marketPathCounts':dict(path_counts),
        'firstDominanceDelayCounts':dict(delay_counts),
        'runtimeBoundary':'Features are current/strict-past execution state plus deltas from the previous observation. Pareto labels and future-dominance fields are teacher-only.',
        'teacherSemantics':[
            'If current checkpoint is the first local KEEP/REPLACE Pareto dominance, act now.',
            'If current checkpoint is non-dominant but a later checkpoint reaches the first dominance by 8s, WAIT_FOR_CLARITY.',
            'If no dominance appears anywhere through 8s, RETURN_TO_CONTROLLER at the first checkpoint and censor later no-action states.',
            'After an ACT teacher state, later counterfactual no-action states are censored because they are unreachable under the teacher policy.',
        ],
        'forbiddenRuntime':['paretoLabel','deltaTargetErrorArea future outcomes','deltaCompletionCost future outcomes','firstFutureDominance','winner','PnL','Target runtime data'],
    }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'output':str(OUT),'report':str(REPORT),**{k:report[k] for k in ['markets','rows','actionCounts','marketPathCounts','firstDominanceDelayCounts']}},ensure_ascii=False))
    return 0

if __name__=='__main__': raise SystemExit(main())
