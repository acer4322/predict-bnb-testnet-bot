from __future__ import annotations
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / 'data/research/execution_aware_fill_lifecycle_v0'
SEQ = D / 'sequential_arbitration_option_teacher_v1.jsonl'
TRAP = D / 'theory_to_execution_trap_exam_v0.jsonl'
OUT = D / 'theory_execution_handoff_curriculum_v1.jsonl'
REPORT = D / 'theory_execution_handoff_curriculum_v1_report.json'


def load_jsonl(path: Path):
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]


def past(events, t):
    return [e for e in (events or []) if int(e.get('atMs') or -1) <= int(t)]


def last_or_none(xs):
    return max(xs, key=lambda e: int(e.get('atMs') or -1)) if xs else None


def age(t, e):
    return (int(t) - int(e['atMs'])) if e and e.get('atMs') is not None else None


def recent_count(xs, t, window_ms):
    lo = int(t) - int(window_ms)
    return sum(1 for e in xs if int(e.get('atMs') or -1) >= lo)


def build_hist(trap_row, t):
    ev = trap_row.get('events') or {}
    dup = past(ev.get('duplicateAddWhileLiveChild'), t)
    stale = past(ev.get('stalePassiveChild'), t)
    part = past(ev.get('partialFill'), t)
    taker = past(ev.get('takerNotCompleted'), t)
    ld, ls, lp, lt = map(last_or_none, (dup, stale, part, taker))
    reasons = Counter(str(e.get('attemptReason') or 'UNKNOWN') for e in dup)
    return {
        'pastDuplicateCount': float(len(dup)),
        'pastDuplicate1s': float(recent_count(dup, t, 1000)),
        'pastDuplicate3s': float(recent_count(dup, t, 3000)),
        'pastDuplicate5s': float(recent_count(dup, t, 5000)),
        'pastDuplicate10s': float(recent_count(dup, t, 10000)),
        'lastDuplicateAgeMs': age(t, ld),
        'lastDuplicateExistingChildAgeMs': float(ld.get('existingAgeMs')) if ld and ld.get('existingAgeMs') is not None else None,
        'lastDuplicateRemainingQty': float(ld.get('remainingQty')) if ld and ld.get('remainingQty') is not None else None,
        'lastDuplicateQuoteOffsetTicks': float(ld.get('quoteOffsetTicks')) if ld and ld.get('quoteOffsetTicks') is not None else None,
        'maxPastDuplicateExistingChildAgeMs': max((float(e.get('existingAgeMs') or 0.0) for e in dup), default=None),
        'pastDuplicateMakerHazard': float(reasons.get('MAKER_HAZARD', 0)),
        'pastDuplicateMakerBurst': float(reasons.get('MAKER_BURST', 0)),
        'pastDuplicatePassiveRepair': float(reasons.get('PASSIVE_REPAIR_PRIORITY', 0)),
        'pastDuplicateUnresolvedRepair': float(reasons.get('UNRESOLVED_REPAIR_ONLY', 0)),
        'pastStaleCount': float(len(stale)),
        'pastStale1s': float(recent_count(stale, t, 1000)),
        'pastStale3s': float(recent_count(stale, t, 3000)),
        'pastStale5s': float(recent_count(stale, t, 5000)),
        'lastStaleEventAgeMs': age(t, ls),
        'lastStaleChildAgeMs': float(ls.get('ageMs')) if ls and ls.get('ageMs') is not None else None,
        'lastStaleRemainingQty': float(ls.get('remainingQty')) if ls and ls.get('remainingQty') is not None else None,
        'lastStaleQuoteOffsetTicks': float(ls.get('quoteOffsetTicks')) if ls and ls.get('quoteOffsetTicks') is not None else None,
        'pastPartialCount': float(len(part)),
        'pastPartial5s': float(recent_count(part, t, 5000)),
        'lastPartialEventAgeMs': age(t, lp),
        'lastPartialCumExecQty': float(lp.get('cumExecQty')) if lp and lp.get('cumExecQty') is not None else None,
        'lastPartialRemainingQty': float(lp.get('remainingQty')) if lp and lp.get('remainingQty') is not None else None,
        'lastPartialQuoteOffsetTicks': float(lp.get('quoteOffsetTicks')) if lp and lp.get('quoteOffsetTicks') is not None else None,
        'pastTakerNotCompletedCount': float(len(taker)),
        'lastTakerNotCompletedAgeMs': age(t, lt),
        'hasPastDuplicate': float(bool(dup)),
        'hasPastStale': float(bool(stale)),
        'hasPastPartial': float(bool(part)),
        'hasPastTakerNotCompleted': float(bool(taker)),
    }


def main():
    seq = load_jsonl(SEQ)
    traps = {int(r['marketId']): r for r in load_jsonl(TRAP)}
    out=[]
    prev_by_mid={}
    for r in sorted(seq, key=lambda z:(int(z['marketId']), int(z['candidateAtMs']))):
        mid=int(r['marketId']); t=int(r['candidateAtMs'])
        tr=traps[mid]
        h=build_hist(tr,t)
        prev=prev_by_mid.get(mid)
        delta={}
        for k,v in h.items():
            if k.startswith('past') or k.startswith('hasPast'):
                pv=(prev or {}).get(k)
                if isinstance(v,(int,float)) and isinstance(pv,(int,float)):
                    delta['delta_'+k]=float(v)-float(pv)
                elif isinstance(v,(int,float)):
                    delta['delta_'+k]=0.0
        row={**r,
             'theoryTrapHistoryFeatures':h,
             'theoryTrapHistoryDeltaFeatures':delta,
             'theoryTrapDiagnosticOnly':{
                 'trapCategoriesFullMarket':tr.get('trapCategories'),
                 'trapSeverityFullMarket':tr.get('trapSeverityDiagnostic'),
             },
             'antiLeakage':'Only trap events with atMs <= candidateAtMs are runtime features. Full-market trap categories/severity are diagnostic only.'}
        out.append(row); prev_by_mid[mid]=h
    OUT.write_text('\n'.join(json.dumps(x,ensure_ascii=False,allow_nan=True) for x in out)+'\n',encoding='utf-8')
    rep={
        'version':'THEORY_EXECUTION_HANDOFF_CURRICULUM_V1',
        'researchOnly':True,'liveTradingChanges':False,
        'rows':len(out),'markets':len(set(int(x['marketId']) for x in out)),
        'teacherActionCounts':dict(Counter(x['teacherAction'] for x in out)),
        'strictPastTrapFeatureCounts':{
            'rowsWithPastDuplicate':sum(x['theoryTrapHistoryFeatures']['hasPastDuplicate']>0 for x in out),
            'rowsWithPastStale':sum(x['theoryTrapHistoryFeatures']['hasPastStale']>0 for x in out),
            'rowsWithPastPartial':sum(x['theoryTrapHistoryFeatures']['hasPastPartial']>0 for x in out),
            'rowsWithPastTakerNotCompleted':sum(x['theoryTrapHistoryFeatures']['hasPastTakerNotCompleted']>0 for x in out),
        },
        'antiLeakage':'Full-market trap outcome is never a runtime feature; only already-observed event history is joined.'
    }
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'output':str(OUT),'report':str(REPORT),**rep},ensure_ascii=False))

if __name__=='__main__': main()
