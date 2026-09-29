"""Bounded read-only Target fill audit; no HFT, policy or future-value labels.

Route bundles at the same timestamp share one pre-state. A route-only projection
is an accounting attribution holding the other fills out, NOT a no-Active replay.
"""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from tools.pair_core_target_geometry_audit_v1 import geometry, IDS

EPS = 1e-8
TARGET_SHA = '0e0e82235cb9dfb5ab013bd0535da241b71b9e756af0cdbd1d55e65d03d8fb67'


def floor(p):return min(p['UP'],p['DOWN'])
def net(p):return p['UP']-p['DOWN']
def add(p,d):return {s:p[s]+d[s] for s in ('UP','DOWN')}
def sign_kind(x):return 'IMPROVE' if x>EPS else 'WORSEN' if x<-EPS else 'FLAT'


def analyze(rows, start, end):
    groups=defaultdict(list)
    for r in rows:
        assert r['side'] in ('UP','DOWN') and r['quote_type'] in ('BID','ASK')
        assert r['role'] in ('MAKER','TAKER')
        assert r['shares']>0 and 0<=r['price']<=1
        groups[r['event_ms'],r['side'],r['order_hash'] or ('anonymous:'+str(r['id']))].append(r)
    batches=defaultdict(list)
    for (t,side,_),legs in groups.items():
        assert len({r['role'] for r in legs})==len({r['quote_type'] for r in legs})==1
        batches[t].append(dict(side=side,route=legs[0]['role'],quote=legs[0]['quote_type'],
                              qty=sum(r['shares'] for r in legs),cost=sum(r['shares']*r['price'] for r in legs)))
    p={'UP':0.,'DOWN':0.}; counts=Counter(); witnesses={}; active_rows=[]
    interval_start=dict(p); interval_start_t=start; maker_only_count=0; maker_only_buy=0.
    pending_maker_growth=[]; later_maker_growth=[]; first_active=None; buy_by_kind=Counter()
    for t,parents in sorted(batches.items()):
        before=dict(p); routes={r['route'] for r in parents}
        deltas={r:{'UP':0.,'DOWN':0.} for r in routes}; route_buy=Counter(); qty=defaultdict(float)
        for r in parents:
            sg=1 if r['quote']=='BID' else -1
            for s in ('UP','DOWN'):
                deltas[r['route']][s]+=sg*((r['qty'] if s==r['side'] else 0)-r['cost'])
            if sg>0:route_buy[r['route']]+=r['cost'];qty[r['route'],r['side']]+=r['qty']
        for d in deltas.values():p=add(p,d)
        if routes=={'MAKER'}:
            maker_only_count+=1;maker_only_buy+=route_buy['MAKER']
            if abs(net(p))>abs(net(before))+EPS and floor(p)<floor(before)-EPS:
                counts['makerOnlyRiskIncreasingBatches']+=1
                pending_maker_growth.append(t);later_maker_growth.append(t)
            continue
        assert 'TAKER' in routes
        counts['activeBatches']+=1
        if first_active is None:first_active=(t-start)/(end-start)
        projected=add(before,deltas['TAKER']);df=floor(projected)-floor(before)
        kind=sign_kind(df);counts['activeFloor'+kind]+=1
        buy_by_kind[kind]+=route_buy['TAKER']
        weak='UP' if before['UP']<before['DOWN']-EPS else 'DOWN' if before['DOWN']<before['UP']-EPS else None
        underwater=floor(before)<-EPS
        if underwater:counts['underwaterActiveBatches']+=1;counts['underwaterActiveFloor'+kind]+=1
        phase='late180' if end-t<=180000 else 'early120'
        counts[phase+'Active']+=1;counts[phase+'ActiveFloor'+kind]+=1
        if len(routes)>1:counts['mixedRouteActiveBatches']+=1
        accumulated=(maker_only_count>0 and maker_only_buy>EPS and
                     abs(net(before))>abs(net(interval_start))+EPS and
                     floor(before)<floor(interval_start)-EPS)
        if accumulated:
            counts['activeAfterMakerAccumulation']+=1
            counts['afterAccumulationFloor'+kind]+=1
            if abs(net(projected))<abs(net(before))-EPS:counts['afterAccumulationAbsNetReduced']+=1
            if floor(p)>floor(before)+EPS:counts['afterAccumulationActualJointFloorImproved']+=1
        if kind=='IMPROVE':
            if abs(net(projected))>EPS:counts['activeImprovementLeavesDirectionalResidual']+=1
            if floor(projected)<-EPS:counts['activeImprovementLeavesNegativeFloor']+=1
        counts['makerGrowthBatchesWithSubsequentActive']+=len(pending_maker_growth)
        counts['makerGrowthBatchesNextActiveFloor'+kind]+=len(pending_maker_growth)
        pending_maker_growth=[]
        entry=dict(t=t,phase=(t-start)/(end-start),remainingSeconds=(end-t)/1000,
                   before=before,activeDelta=deltas['TAKER'],activeProjected=projected,
                   afterJoint=dict(p),activeFloorChange=df,activeAbsNetChange=abs(net(projected))-abs(net(before)),
                   weakSide=weak,weakEndpointChange=deltas['TAKER'].get(weak) if weak else None,
                   activeBuy=route_buy['TAKER'],activeBuyQty={s:qty['TAKER',s] for s in ('UP','DOWN')},
                   mixedRoutes=len(routes)>1,underwater=underwater,afterMakerAccumulation=accumulated,
                   priorMakerOnlyBatches=maker_only_count,priorMakerBuy=maker_only_buy,
                   intervalStart=interval_start,intervalStartMs=interval_start_t)
        active_rows.append(entry)
        if accumulated and kind=='IMPROVE':witnesses.setdefault('firstRepairAfterMakerAccumulation',entry)
        if underwater and kind=='WORSEN':witnesses.setdefault('firstActiveRiskIncreaseUnderwater',entry)
        interval_start=dict(p);interval_start_t=t;maker_only_count=0;maker_only_buy=0.
    counts['makerGrowthBatchesWithoutSubsequentActive']=len(pending_maker_growth)
    for e in active_rows:
        if e['activeFloorChange']>EPS and any(t>e['t'] for t in later_maker_growth):
            counts['activeImprovementFollowedByLaterMakerRiskIncrease']+=1
    return dict(counts=dict(counts),firstActivePhase=first_active,endpoints=p,
                terminalAbsNet=abs(net(p)),terminalFloor=floor(p),
                activeBuyByFloorKind=dict(buy_by_kind),witnesses=witnesses,
                activeBatches=active_rows)


def main():
    base=Path('data/research/pair_core_target_geometry10_20260910_v1')
    out=base/'EXPOSURE_SERVICE_AUDIT.json';assert not out.exists(),'immutable output exists'
    raw=(base/'TARGET_COMPACT.json').read_bytes();assert len(raw)<2*1024**2
    assert hashlib.sha256(raw).hexdigest()==TARGET_SHA
    reference=json.loads(raw);assert [r['marketId'] for r in reference['rows']]==IDS
    db=Path('data/target_wallet_official_v1.db').resolve()
    c=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=.5);c.row_factory=sqlite3.Row
    c.execute('PRAGMA query_only=ON');c.execute('PRAGMA cache_size=-1024')
    query='SELECT id,role,side,quote_type,order_hash,event_ms,observed_at_ms,price,shares FROM wallet_shadow_target_events WHERE asset=? AND market_id=? AND event_ms>=? AND event_ms<=? ORDER BY event_ms,id LIMIT 5001'
    results=[]
    try:
        for ref in reference['rows']:
            deadline=time.monotonic()+2;c.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
            args=('BTC',ref['marketId'],ref['start'],ref['end'])
            plan=[r['detail'] for r in c.execute('EXPLAIN QUERY PLAN '+query,args)]
            assert any('USING INDEX idx_target_events_asset_market_time' in p for p in plan)
            rows=[dict(r) for r in c.execute(query,args)];assert 0<len(rows)<=5000
            digest=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
            assert digest==ref['sourceRowHash'],'frozen consumed source changed'
            g=geometry(rows)
            assert g['route']==ref['route'] and g['trace']==ref['trace'],'frozen geometry parity'
            a=analyze(rows,ref['start'],ref['end'])
            for s in ('UP','DOWN'):assert abs(a['endpoints'][s]-ref['endpoints'][s])<1e-6
            assert abs(sum(a['activeBuyByFloorKind'].values())-ref['route']['TAKER']['buy'])<1e-6
            results.append(dict(marketId=ref['marketId'],sourceRowHash=digest,queryPlan=plan,
                                observationDelayMs=ref['observationDelayMs'],**a))
    finally:c.close()
    totals=Counter();buy=Counter()
    for r in results:totals.update(r['counts']);buy.update(r['activeBuyByFloorKind'])
    result=dict(verdict='OBSERVED_ACTIVE_PARTIAL_RISK_SERVICE_NOT_PRIVATE_POLICY_IDENTIFICATION',
                targetSha256=TARGET_SHA,sourceParityMarkets=len(results),HFT=0,training=False,freshUsed=0,
                totalCounts=dict(totals),totalActiveBuyByFloorKind=dict(buy),rows=results)
    payload=json.dumps(result,separators=(',',':'),allow_nan=False).encode();assert len(payload)<2*1024**2
    out.write_bytes(payload)
    print(json.dumps(dict(verdict=result['verdict'],bytes=len(payload),sha256=hashlib.sha256(payload).hexdigest(),
                         totalCounts=result['totalCounts'],totalActiveBuyByFloorKind=dict(buy),
                         markets=[{k:r[k] for k in ('marketId','counts','firstActivePhase','terminalFloor','terminalAbsNet')} for r in results])))


if __name__=='__main__':main()
