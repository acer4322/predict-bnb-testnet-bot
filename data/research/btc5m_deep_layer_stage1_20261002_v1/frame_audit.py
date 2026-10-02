"""Source frames and existing terminal transport closure are distinct."""
import gzip,json
from pathlib import Path
P=Path(__file__).resolve().parent

def read(path):
    b=path.read_bytes();return json.loads(gzip.decompress(b) if path.suffix=='.gz' else b)

def verify(clock,trace,source):
    books=source['books'];end=source['market']['window_end_ms'];n=len(books)
    assert clock['source_updates']==n and clock['frames']==clock['producer_calls'],'FRAME_COUNTER'
    plans=trace['plans'];roles=trace['direction_rows'];states=trace['states']
    assert len(plans)==len(roles)==clock['frames'],'PLAN_COUNT'
    assert [r['index'] for r in roles]==list(range(len(roles))),'PLAN_INDEX'
    assert [p['t'] for p in plans]==[r['t'] for r in roles],'PLAN_ROLE_TIME'
    calls=clock['calls'];src=[c for c in calls if c['stage']=='source']
    assert len(calls)==n+2 and calls[0]['stage']=='initial' and calls[-1]['stage']=='end2','GUARD_COUNT'
    assert [c['index'] for c in src]==list(range(n)),'SOURCE_INDEX'
    times=[b['received_ms'] for b in books]
    assert [c['target_ns'] for c in src]==[t*1000000 for t in times],'SOURCE_CLOCK'
    assert all(c['reached'] and c['after_ns']>=c['target_ns'] for c in calls),'UNREACHED_SOURCE'
    assert [p['t'] for p in plans[:n]]==times,'SOURCE_PLAN_PREFIX'
    drains=clock['terminal_drain'];observed=clock['drains']
    assert len(drains)==len(observed),'DRAIN_COUNT'
    assert len(states)==n+1+len(drains),'PROCESS_COUNT'
    assert [s['t'] for s in states[:n]]==times and states[n]['t']==times[-1],'SOURCE_STATE_PREFIX'
    assert [s['t'] for s in states[n+1:]]==[d['observed_ms'] for d in drains],'DRAIN_STATE_TIME'
    extra=[];previous=calls[-1]['after_ns'];owners=None
    for i,(d,o) in enumerate(zip(drains,observed)):
        assert d['step']==i and o['before_ns']==previous,'DRAIN_CONTINUITY'
        actual=o['actual_ns'];rc=o['rc'];t=o['observed_ms']
        assert rc in (0,1,2,3) and previous<=actual<=o['query_upper_bound_ns'],'DRAIN_CLOCK'
        assert t==actual//1000000 and d['native_clock_ns']==actual and d['observed_ms']==t,'DRAIN_OBSERVATION'
        assert d['native_return']==rc and d['query_upper_bound_ns']==o['query_upper_bound_ns'],'DRAIN_RECEIPT_CLOCK'
        assert d['new_market_events_appended']==0 and not d.get('right_censored_reason'),'NO_ADDED_SOURCE'
        assert d['before_count']>0 and 0<=d['after_count']<=d['before_count'],'OWNER_COUNT'
        assert owners is None or d['before_count']==owners,'OWNER_CONTINUITY'
        if d['after_count'] and rc!=1 and actual>=end*1000000:extra.append(t)
        if rc==1:assert d['after_count']==0,'EOF_PENDING'
        previous=actual;owners=d['after_count']
    assert clock['native_clock_ns']==previous,'FINAL_CLOCK'
    assert not drains or drains[-1]['after_count']==0,'FINAL_OWNER'
    assert clock['frames']==n+len(extra),'UNEXPLAINED_FRAME'
    assert [p['t'] for p in plans[n:]]==extra,'CLOSURE_PLAN_TIME'
    for p in plans[n:]:
        assert p['t']>=end,'PREEXPIRY_CLOSURE'
        assert all(o['kind'] in ('CANCEL','KEEP') for o in p['operations']),'CLOSURE_NEW'
        assert all(o['key'] in clock['carriers'] for o in p['operations']),'CLOSURE_OWNER'
    return dict(source_frames=n,terminal_drain_steps=len(drains),terminal_closure_frames=len(extra),extra_plan_times=extra,
                source_complete=True,no_postexpiry_acquisition=True)

def audit(arm,clock,trace):
    market=read(arm/'result.json')['market_id']
    source=read(P.parent/'btc5m_cg1at_fresh100a_20260930/base/inputs'/f'public_{market}.json.gz')  # CG1AT fresh-100a
    return verify(clock,trace,source)
