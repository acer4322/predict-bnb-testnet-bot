"""Bounded, offline Target geometry audit. No policy, HFT or model imports."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import statistics
import time

MANIFEST_SHA = 'f55b618bd9eaae077fb66682fd3e776808b53b3c4a9bd1eb6a85a8e9b45a6dd5'
IDS = [2022527,2023478,2024133,2026085,2026429,2026817,2027678,2028352,2028561,2029246]


def geometry(rows):
    groups = defaultdict(list)
    for r in rows:
        assert r['side'] in ('UP','DOWN') and r['quote_type'] in ('BID','ASK')
        assert r['role'] in ('MAKER','TAKER')
        assert math.isfinite(r['shares']) and r['shares'] > 0
        assert math.isfinite(r['price']) and 0 <= r['price'] <= 1
        groups[r['event_ms'],r['side'],r['order_hash'] or 'anonymous:'+str(r['id'])].append(r)
    batches = defaultdict(list)
    for (t,s,_), legs in groups.items():
        routes={x['role'] for x in legs}; quotes={x['quote_type'] for x in legs}
        assert len(routes)==len(quotes)==1, 'ambiguous physical parent semantics'
        batches[t].append(dict(side=s,route=next(iter(routes)),quote=next(iter(quotes)),
            qty=sum(x['shares'] for x in legs),notional=sum(x['shares']*x['price'] for x in legs)))
    inv={'UP':0.,'DOWN':0.}; buy=sell=0.; route=defaultdict(lambda:dict(buy=0.,sell=0.,UP=0.,DOWN=0.,parents=0))
    counts=Counter(); trace=[]; peak_net=peak_gross=0.; min_floor=0.
    for t, parents in sorted(batches.items()):
        before={s:inv[s]-buy+sell for s in inv}; floor=min(before.values())
        kinds=set(); ambiguity=len(parents)>1
        if ambiguity:counts['simultaneousBatches']+=1
        for p in parents:
            sign=1 if p['quote']=='BID' else -1
            delta={s:sign*((p['qty'] if s==p['side'] else 0)-p['notional']) for s in inv}
            df=min(before[s]+delta[s] for s in inv)-floor
            k='floorImproving' if df>1e-9 else 'floorWorsening' if df< -1e-9 else 'floorFlat'
            kinds.add(k); counts[p['route']+'_'+k]+=1
            if floor< -1e-9 and k=='floorWorsening':counts['floorWorseningWhileUnderwater']+=1
            inv[p['side']]+=sign*p['qty']
            if sign>0:buy+=p['notional'];route[p['route']]['buy']+=p['notional']
            else:sell+=p['notional'];route[p['route']]['sell']+=p['notional']
            for s in inv:route[p['route']][s]+=delta[s]
            route[p['route']]['parents']+=1
        if {'floorImproving','floorWorsening'} <= kinds:counts['mixedGeometryBatches']+=1
        after={s:inv[s]-buy+sell for s in inv}
        peak_net=max(peak_net,abs(inv['UP']-inv['DOWN']));peak_gross=max(peak_gross,sum(inv.values()))
        min_floor=min(min_floor,min(after.values()))
        trace.append(dict(eventMs=t,UP=after['UP'],DOWN=after['DOWN'],buyNotional=buy,sellProceeds=sell,
                          inventoryUP=inv['UP'],inventoryDOWN=inv['DOWN'],parents=len(parents),
                          routes=sorted({p['route'] for p in parents}),geometryKinds=sorted(kinds)))
    endpoints={s:inv[s]-buy+sell for s in inv}
    for s in inv:assert abs(sum(r[s] for r in route.values())-endpoints[s])<1e-7
    delay=sorted(r['observed_at_ms']-r['event_ms'] for r in rows)
    return dict(fillLegs=len(rows),parentEvents=len(groups),timestampBatches=len(batches),
        buyNotional=buy,sellProceeds=sell,inventory=inv,endpoints=endpoints,
        normalizedEndpoints={s:endpoints[s]/buy if buy else None for s in inv},
        route=dict(route),counts=dict(counts),peakAbsNet=peak_net,peakGross=peak_gross,minFloor=min_floor,
        observationDelayMs=dict(min=min(delay) if delay else None,median=statistics.median(delay) if delay else None,
                                max=max(delay) if delay else None),trace=trace)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);a=ap.parse_args()
    out=Path(a.output);assert not out.exists(),'immutable output already exists'
    source=Path('data/research/market_capsule_v1/source_bundle_50_v1/manifest.json')
    raw=source.read_bytes();assert hashlib.sha256(raw).hexdigest()==MANIFEST_SHA
    m=json.loads(raw);ordered=sorted(m['markets'],key=lambda r:(r['window_start_ms'],r['market_id']))
    selected=[ordered[i] for i in range(0,50,5)];assert [r['market_id'] for r in selected]==IDS
    db=Path('data/target_wallet_official_v1.db').resolve()
    c=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=.5);c.row_factory=sqlite3.Row
    c.execute('PRAGMA query_only=ON');c.execute('PRAGMA cache_size=-1024')
    report=dict(version='TARGET_GEOMETRY10_OBSERVATION_V1',manifestSha256=MANIFEST_SHA,rows=[],
        boundary='Offline confirmed-fill geometry; no hidden intent/queue identity; no independent settlement certification',
        HFT=0,training=False,freshUsed=0,liveChanges=False)
    query='SELECT id,role,side,quote_type,order_hash,event_ms,observed_at_ms,price,shares FROM wallet_shadow_target_events WHERE asset=? AND market_id=? AND event_ms>=? AND event_ms<=? ORDER BY event_ms,id LIMIT 5001'
    try:
        for market in selected:
            deadline=time.monotonic()+2;c.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
            mid=market['market_id'];args=('BTC',mid,market['window_start_ms'],market['window_end_ms'])
            plan=[r['detail'] for r in c.execute('EXPLAIN QUERY PLAN '+query,args)]
            assert any('USING INDEX idx_target_events_asset_market_time' in p for p in plan)
            rows=[dict(r) for r in c.execute(query,args)];assert 0<len(rows)<=5000,'missing/capped cohort'
            result=c.execute('SELECT asset,winner,fill_count,buy_notional_usdt,sell_proceeds_usdt,up_position_shares,down_position_shares,net_pnl_usdt,accounting_version FROM target_market_results WHERE market_id=?',(mid,)).fetchone()
            assert result and result['asset']=='BTC','missing result'
            g=geometry(rows);r=dict(result)
            checks=dict(fillCount=g['fillLegs']==r['fill_count'],
                buy=abs(g['buyNotional']-r['buy_notional_usdt'])<1e-6,
                sell=abs(g['sellProceeds']-r['sell_proceeds_usdt'])<1e-6,
                up=abs(g['inventory']['UP']-r['up_position_shares'])<1e-6,
                down=abs(g['inventory']['DOWN']-r['down_position_shares'])<1e-6,
                pnl=r['winner'] in ('UP','DOWN') and abs(g['endpoints'][r['winner']]-r['net_pnl_usdt'])<1e-6)
            assert all(checks.values()),f'{mid}: reconciliation {checks}'
            report['rows'].append(dict(marketId=mid,start=market['window_start_ms'],end=market['window_end_ms'],
                sourceActionCount=market['target_action_count'],queryPlan=plan,sourceRowHash=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest(),
                recordedWinner=r['winner'],recordedWinnerGross=g['endpoints'][r['winner']],reconciliation=checks,**g))
    finally:c.close()
    report['verdict']='OBSERVATION_COMPLETE_NOT_POLICY_VALIDATION'
    payload=json.dumps(report,separators=(',',':'),allow_nan=False).encode();assert len(payload)<2*1024**2
    out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(payload)
    print(json.dumps(dict(verdict=report['verdict'],bytes=len(payload),sha256=hashlib.sha256(payload).hexdigest(),
        rows=[{k:r[k] for k in ('marketId','fillLegs','parentEvents','buyNotional','endpoints','normalizedEndpoints','recordedWinner','recordedWinnerGross','counts','observationDelayMs')} for r in report['rows']]),allow_nan=False))


if __name__=='__main__':main()
