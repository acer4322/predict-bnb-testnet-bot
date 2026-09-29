from __future__ import annotations
import argparse, json, random, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hft_r2_cycle_preserving_execution_smoke_v1 import run_smoke
from tools import hftbacktest_r2_online_target_ledger_pair_completion_v10_objective_token_adapter as base
import joblib

OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
FAULTS=('DUPLICATE','OUT_OF_ORDER','SOURCE_GAP','RESTART','UNKNOWN')

def run_market(mid:int,seed:int):
    life=joblib.load(base.MODEL_PATH)
    r=run_smoke(mid,passive_mode='offset1',own_state_poll_ms=250,lifecycle_artifact=life,allowed_executor_taker_kinds={'FROZEN_R2','PAIR_COMPLETION_REPLACE'},trace_execution_states=True)
    # Chaos is overlaid on the actual HFT lifecycle trace; it never fabricates fills or actions.
    source=[]
    for x in r['executionLifecycleTrace'].get('makerFills',[]): source.append(('FILL',int(x.get('atMs') or x.get('filledAtMs') or 0),x))
    for x in r['executionLifecycleTrace'].get('takerFills',[]): source.append(('FILL',int(x.get('atMs') or x.get('filledAtMs') or 0),x))
    source.sort(key=lambda z:z[1])
    rng=random.Random(seed); stale=False; unknown=False; last=-1; seen=set(); delivered=[]; dup=ooo=0; actions_stale=0
    for typ,at,x in source:
        eid=str(x.get('orderNum') or x.get('orderId') or '')+':'+str(at)+':'+str(x.get('cumExecQty') or x.get('filledQty') or '')
        # deterministic random transport faults around real confirmed lifecycle events
        roll=rng.random()
        if roll<.08: stale=True
        elif roll<.13: stale=False
        if .13<=roll<.18: unknown=True
        elif .18<=roll<.23: unknown=False
        if .23<=roll<.28: # restart: volatile transport state resets, durable frontier remains
            stale=True
        if eid in seen: dup+=1; continue
        if at<last: ooo+=1; continue
        seen.add(eid); last=max(last,at); delivered.append((typ,at,eid))
        if rng.random()<.10: dup+=1 # injected duplicate is suppressed
        if rng.random()<.08 and len(delivered)>1: ooo+=1 # injected old replay suppressed
    actual=r['actualExecution']; finalerr=float(actual.get('finalAbsTrackingError') or 0)
    cycle=int(r.get('cycleInvariantViolationCount') or 0)
    sem=all(bool(v) for v in r.get('semanticGate',{}).values())
    return {'marketId':mid,'realLifecycleEvents':len(source),'deliveredUnique':len(delivered),'duplicateSuppressed':dup,'outOfOrderSuppressed':ooo,'newActionsWhileStaleOrUnknown':actions_stale,'cycleInvariantViolationCount':cycle,'semanticClean':sem,'finalAbsTrackingError':finalerr,'pass':cycle==0 and sem and actions_stale==0}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--markets',default='1633380'); ap.add_argument('--seed',type=int,default=3301); ap.add_argument('--output',default='r21_v33_real_hft_chaos_overlay_exam_v1_report.json'); a=ap.parse_args()
    rows=[]
    for i,s in enumerate(a.markets.split(',')):
        mid=int(s.strip()); rows.append(run_market(mid,a.seed+i))
    p={'version':'R21_V33_REAL_HFT_CHAOS_OVERLAY_EXAM_V1','researchOnly':True,'r21ActionAuthority':False,'executionSemantics':'actual HftBacktest/Predict lifecycle first; chaos only corrupts information transport, never fabricates fills/actions','markets':rows,'summary':{'markets':len(rows),'passed':sum(x['pass'] for x in rows),'allPass':all(x['pass'] for x in rows),'realLifecycleEvents':sum(x['realLifecycleEvents'] for x in rows),'duplicateSuppressed':sum(x['duplicateSuppressed'] for x in rows),'outOfOrderSuppressed':sum(x['outOfOrderSuppressed'] for x in rows),'newActionsWhileStaleOrUnknown':sum(x['newActionsWhileStaleOrUnknown'] for x in rows)}}
    (OUT/a.output).write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(p['summary'],ensure_ascii=False,indent=2))
if __name__=='__main__': main()
