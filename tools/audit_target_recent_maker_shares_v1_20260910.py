"""Read-only, indexed Maker filled-order size audit; no policy/venue claims.

Frozen at the latest common completed5M window seen in the first read:
2026-09-10 18:55 +08. Recent2h and preceding2h; fixed windows regardless of fills.
Original order quantity is NOT inferred from expected_parent_shares or min rules.
"""
from pathlib import Path
from collections import Counter, defaultdict
import sqlite3, hashlib, json, statistics, datetime, math, re, time

BASE=Path('data/research/r4_v0/p0_provenance_v1')
DB=Path('data/target_wallet_official_v1.db')
WALLET='0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03'
END=1789037700000
TWO_HOURS=7200000
EPS=1e-7


def iso(ms):
    return datetime.datetime.fromtimestamp(ms/1000,datetime.timezone(datetime.timedelta(hours=8))).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def modes(values,n=8):
    counts=Counter(round(v,6) for v in values)
    return [dict(shares=k,orders=v,fraction=v/len(values)) for k,v in counts.most_common(n)] if values else []


def quantile(values,p):
    if not values:return None
    a=sorted(values);x=(len(a)-1)*p;lo=math.floor(x);hi=math.ceil(x)
    return a[lo]+(a[hi]-a[lo])*(x-lo)


def stats(rows,market_ids=None):
    q=[r['shares'] for r in rows]
    markets=sorted(set(market_ids if market_ids is not None else [r['market_id'] for r in rows]))
    by=defaultdict(list)
    for r in rows:by[r['market_id']].append(r['shares'])
    return dict(markets=len(markets),marketsWithObservedMaker=len(by),filledOrderCount=len(rows),
      cumulativeFilledShares=sum(q),meanFilledSharesPerOrder=statistics.mean(q) if q else None,
      medianFilledSharesPerOrder=statistics.median(q) if q else None,
      q10=quantile(q,.1),q90=quantile(q,.9),q95=quantile(q,.95),
      minFilledShares=min(q) if q else None,maxFilledShares=max(q) if q else None,
      orderSizeModes=modes(q),
      equalMarketMeanOfOrderMeans=statistics.mean(statistics.mean(v) for v in by.values()) if by else None,
      multiFillOrders=sum(r.get('fill_legs',1)>1 for r in rows),
      buyOrders=sum(r.get('quote_type')=='BID' for r in rows),
      sellOrders=sum(r.get('quote_type')=='ASK' for r in rows),
      totalObservedFillLegs=sum(r.get('fill_legs',1) for r in rows),
      matchedCompleteRequestedQuantityCount=0,
      quantityDefinition='sum of observed fills of one MAKER wallet/market/order_hash/side/quote identity; not submitted size or proven full fill')


def is_five_minute(title):
    m=re.search(r'(\d{1,2}):(\d{2})(AM|PM)-(\d{1,2}):(\d{2})(AM|PM)',title or '')
    if not m:return False
    a,b,c,d,e,f=m.groups()
    t1=(int(a)%12+(12 if c=='PM' else 0))*60+int(b)
    t2=(int(d)%12+(12 if f=='PM' else 0))*60+int(e)
    return (t2-t1)%1440==5


def main():
    out=BASE/'TARGET_RECENT_MAKER_SHARES_SCORE_V1_20260910_1855.json'
    snapshot=BASE/'TARGET_RECENT_MAKER_SHARES_SNAPSHOT_V1_20260910_1855.json'
    if out.exists() or snapshot.exists():raise FileExistsError('immutable output exists')
    start=time.monotonic();allparents=[];allmarkets=[];audits=[];plans=[]
    c=sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
    c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('PRAGMA cache_size=-4096')
    deadline=time.monotonic()+12;c.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
    c.execute('BEGIN')
    market_sql='''SELECT r.market_id,r.asset,r.title,r.resolved_at_ms,m.window_end_ms
      FROM target_market_results r INDEXED BY idx_target_results_asset_resolved
      JOIN target_markets m ON m.market_id=r.market_id
      WHERE r.asset=? AND r.resolved_at_ms>? AND r.resolved_at_ms<=?
      ORDER BY r.resolved_at_ms DESC LIMIT 100'''
    parent_sql='''SELECT parent_id,wallet,asset,market_id,role,side,quote_type,order_hash,
      first_event_ms,last_event_ms,average_price,shares,fill_legs,updated_at_ms
      FROM target_parent_orders INDEXED BY idx_target_parents_asset_market_time
      WHERE asset=? AND market_id=? AND role='MAKER' ORDER BY last_event_ms,parent_id'''
    event_sql='''SELECT wallet,order_hash,side,quote_type,COUNT(*) AS fill_legs,
      SUM(shares) AS shares,SUM(shares*price) AS cash,MIN(event_ms) AS first_event_ms,
      MAX(event_ms) AS last_event_ms,MIN(price) AS min_price,MAX(price) AS max_price
      FROM wallet_shadow_target_events INDEXED BY idx_target_events_asset_market_time
      WHERE asset=? AND market_id=? AND role='MAKER'
      GROUP BY wallet,order_hash,side,quote_type'''
    for a in ['BTC','ETH']:
        candidates=[dict(r) for r in c.execute(market_sql,(a,END-2*TWO_HOURS-300000,END+120000))]
        ms=sorted([r for r in candidates if END-2*TWO_HOURS<r['window_end_ms']<=END and is_five_minute(r['title'])],key=lambda x:x['window_end_ms'])
        for r in ms:
            r['window_start_ms']=r['window_end_ms']-300000
            r['period']='RECENT_2H' if r['window_end_ms']>END-TWO_HOURS else 'PRECEDING_2H'
        allmarkets.extend(ms)
        if ms:
            for sql,params in [(market_sql,(a,END-2*TWO_HOURS-300000,END+120000)),(parent_sql,(a,ms[0]['market_id'])),(event_sql,(a,ms[0]['market_id']))]:
                qp=[tuple(x) for x in c.execute('EXPLAIN QUERY PLAN '+sql,params)]
                if any('SCAN wallet_shadow' in str(x) or 'SCAN target_parent' in str(x) for x in qp):raise RuntimeError('unindexed query')
                plans.append(dict(asset=a,query=sql,exampleParameters=params,plan=qp))
        for m in ms:
            mid=m['market_id'];parents=[dict(r) for r in c.execute(parent_sql,(a,mid))]
            events=[dict(r) for r in c.execute(event_sql,(a,mid))]
            parents=[r for r in parents if r['wallet'].lower()==WALLET]
            events=[r for r in events if r['wallet'].lower()==WALLET]
            key=lambda r:(r['wallet'].lower(),r['order_hash'],r['side'],r['quote_type'])
            grouped={key(r):r for r in events}
            assert len(grouped)==len(events)==len(parents)
            maxerr=0
            for r in parents:
                assert r['order_hash'] and r['shares']>0 and math.isfinite(r['shares'])
                e=grouped[key(r)];err=max(abs(r['shares']-e['shares']),abs(r['shares']*r['average_price']-e['cash']))
                maxerr=max(maxerr,err)
                assert err<1e-6 and r['fill_legs']==e['fill_legs']
                assert r['first_event_ms']==e['first_event_ms'] and r['last_event_ms']==e['last_event_ms']
                assert m['window_start_ms']<=r['first_event_ms']<=r['last_event_ms']<m['window_end_ms']
                r.update(period=m['period'],window_start_ms=m['window_start_ms'],window_end_ms=m['window_end_ms'],
                         min_fill_price=e['min_price'],max_fill_price=e['max_price'])
                allparents.append(r)
            audits.append(dict(asset=a,marketId=mid,parentCount=len(parents),eventGroupCount=len(events),maxCashQtyError=maxerr))
    c.rollback();c.close()
    queryElapsed=time.monotonic()-start
    expected_ends=list(range(END-2*TWO_HOURS+300000,END+1,300000))
    missing={a:[iso(t) for t in expected_ends if t not in {m['window_end_ms'] for m in allmarkets if m['asset']==a}] for a in ['BTC','ETH']}
    historical_source=BASE/'PAIR_CORE_TARGET_POST180_EVENTS_COMPACT_V1_20260910.jsonl'
    assert historical_source.stat().st_size<20*1024**2
    h=hashlib.sha256();hist={}
    with historical_source.open('rb') as f:
        for raw in f:
            h.update(raw);r=json.loads(raw)
            if r['role']!='MAKER':continue
            key=(r['asset'],r['marketId'],r['order'],r['side'],r['quote'])
            x=hist.setdefault(key,dict(asset=r['asset'],market_id=r['marketId'],order_hash=r['order'],side=r['side'],
                quote_type=r['quote'],shares=0.,cash=0.,fill_legs=0,first_event_ms=r['t'],last_event_ms=r['t']))
            x['shares']+=r['q'];x['cash']+=r['q']*r['p'];x['fill_legs']+=1
            x['first_event_ms']=min(x['first_event_ms'],r['t']);x['last_event_ms']=max(x['last_event_ms'],r['t'])
    assert h.hexdigest()=='a105a6fa22274f24132c7fa2ef8552fdd035694d0ce7b6cedafea0e61d2d4090'
    summaries={};trends={};price_groups={}
    for a in ['BTC','ETH']:
        ap=[r for r in allparents if r['asset']==a];am=[r for r in allmarkets if r['asset']==a]
        summaries[a]={}
        for label in ['PRECEDING_2H','RECENT_2H']:
            rows=[r for r in ap if r['period']==label];ids=[m['market_id'] for m in am if m['period']==label]
            summaries[a][label]=stats(rows,ids)
        hs=[r for r in hist.values() if r['asset']==a];s=stats(hs)
        s.update(firstEvent=iso(min(r['first_event_ms'] for r in hs)),lastEvent=iso(max(r['last_event_ms'] for r in hs)))
        summaries[a]['HISTORICAL_REFERENCE']=s
        trends[a]=[]
        for k in range(8):
            lo=END-2*TWO_HOURS+k*1800000;hi=lo+1800000
            ms=[m for m in am if lo<m['window_end_ms']<=hi];ids=[m['market_id'] for m in ms]
            s=stats([r for r in ap if r['market_id'] in ids],ids)
            s.update(start=iso(lo),end=iso(hi));trends[a].append(s)
        price_groups[a]=[]
        edges=[0,.1,.3,.5,.7,.9,1]
        for lo,hi in zip(edges,edges[1:]):
            rows=[r for r in ap if r['period']=='RECENT_2H' and lo<=r['average_price']<hi]
            s=stats(rows);s.update(priceBand=[lo,hi]);price_groups[a].append(s)
    snap=dict(classification='READ_ONLY_RECENT_TARGET_EXECUTED_ORDER_QUANTITY_OBSERVATION_NOT_HOLDOUT_POLICY_SCORE',
        capturedHostTime=datetime.datetime.now().astimezone().isoformat(),frozenEndMs=END,
        wallet=WALLET,marketSelection='fixed completed5M windows irrespective of outcome/size/fill success, sources may miss markets',
        markets=allmarkets,makerParents=allparents,audits=audits,queryPlans=plans,missingScheduledMarkets=missing)
    snapshot.write_text(json.dumps(snap,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    permarket=[]
    for m in allmarkets:
        s=stats([r for r in allparents if r['market_id']==m['market_id']],[m['market_id']]);s.update(m);permarket.append(s)
    result=dict(version='TARGET_RECENT_MAKER_SHARES_V1_20260910_1855',asOfLocal=iso(END),
        recentInterval=[iso(END-TWO_HOURS),iso(END)],precedingInterval=[iso(END-2*TWO_HOURS),iso(END-TWO_HOURS)],
        summaries=summaries,halfHourTrends=trends,recentPriceBands=price_groups,perMarket=permarket,
        missingScheduledMarkets=missing,totalMakerOrders=len(allparents),marketCount=len(allmarkets),
        source=dict(db=DB.as_posix(),readOnly=True,rawJsonReadForStatistics=False,
                    snapshot=snapshot.as_posix(),snapshotBytes=snapshot.stat().st_size,snapshotSha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                    historical=historical_source.as_posix(),historicalSha256=h.hexdigest(),databaseGloballyHashed=False),
        maxIndependentAggregationError=max(x['maxCashQtyError'] for x in audits),queryElapsedSeconds=queryElapsed,
        expectedParentSharesRejected='inference expectedParentRule hardcodes18 if observed cumulative<=18.05; not original order evidence',
        originalRequestedQuantityStatus='UNAVAILABLE_IN_THESE_FILL_RECORDS',
        limitInterpretation='no inference of a minimum or maximum from these observed sizes; shares-first descriptive comparison only',
        newHFT=0,newTraining=0,policyChanges=0,
        limitations=['maker order means a hash with observed fills, excluding wholly unfilled orders',
         'market ended does not prove every maker order filled in full; observed cumulative is a lower bound on original requested shares',
         'mean is weighted by observed filled orders; market-equally weighted means and medians are separate',
         'historical and current periods differ in prices, regimes and potentially collection; descriptive change not identified causal parameter change',
         'recent read authorized for Target descriptive analysis only, not unseen policy promotion or training labels'])
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(dict(output=out.as_posix(),bytes=out.stat().st_size,sha256=hashlib.sha256(out.read_bytes()).hexdigest(),
      recentInterval=result['recentInterval'],precedingInterval=result['precedingInterval'],
      summaries=summaries,missing=missing,totalMakerOrders=len(allparents),marketCount=len(allmarkets),
      maxIndependentAggregationError=result['maxIndependentAggregationError'],queryElapsedSeconds=queryElapsed,
      trends={a:[dict(start=x['start'],end=x['end'],n=x['filledOrderCount'],mean=x['meanFilledSharesPerOrder'],median=x['medianFilledSharesPerOrder'],modes=x['orderSizeModes'][:3]) for x in trends[a]] for a in trends}),ensure_ascii=False))


if __name__=='__main__':main()
