"""Bounded consumed-only Target cadence audit. No HFT, model, or policy writes.

Event-time fills and first-fill order identities are observable execution
statistics, not unobserved Target placement/cancel decisions.
"""
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
import sqlite3
import statistics as st
import time

BASE=Path('data/research/r4_v0/p0_provenance_v1')
BUNDLE=Path('data/research/market_capsule_v1/source_bundle_50_v1')
OUT=BASE/'PAIR_CORE_TARGET_POST180_CADENCE_SCORE_V1_20260910.json'
EXTRACT=BASE/'PAIR_CORE_TARGET_POST180_EVENTS_COMPACT_V1_20260910.jsonl'
EPS=1e-7

def hashfile(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(262144),b''):h.update(b)
    return h.hexdigest()

def digest(v):
    return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def readsmall(p,cap=1024*1024):
    p=Path(p)
    if p.stat().st_size>cap:raise RuntimeError('bounded input cap '+str(p))
    return json.loads(p.read_text(encoding='utf-8-sig'))

def percentile(xs,p):
    if not xs:return None
    ys=sorted(xs);k=(len(ys)-1)*p;i=int(k);j=min(i+1,len(ys)-1)
    return ys[i]*(1-k+i)+ys[j]*(k-i)

def vector(rows,key,scale=1):return [sum(r[key][i] for r in rows)*scale for i in range(len(rows[0][key]))]
def iso(ms):return datetime.fromtimestamp(ms/1000,tz=timezone(timedelta(hours=8))).isoformat()

def profile(asset,mid,start,end,events,expected):
    seen={};duplicates=0
    for e in events:
        ident=e['leg']
        if ident in seen:
            if seen[ident]!=e:raise RuntimeError('conflicting duplicate leg '+str(mid))
            duplicates+=1;continue
        seen[ident]=e
    events=sorted(seen.values(),key=lambda e:(e['t'],e['leg']))
    inv={'UP':0.,'DOWN':0.};buy=sell=0.
    for e in events:
        if e['side'] not in inv or e['role'] not in ('MAKER','TAKER') or e['quote'] not in ('BID','ASK'):raise RuntimeError('unknown fill semantics')
        if not all(math.isfinite(e[k]) for k in ('p','q')) or e['q']<=0 or not 0<=e['p']<=1:raise RuntimeError('invalid quantity/price')
        s=1 if e['quote']=='BID' else -1;inv[e['side']]+=s*e['q']
        if s==1:buy+=e['p']*e['q']
        else:sell+=e['p']*e['q']
    pnl=inv[expected['winner']]+sell-buy
    errors={'buy':abs(buy-expected['buy']),'pnl':abs(pnl-expected['pnl'])}
    if 'inv' in expected:
        errors.update({s:abs(inv[s]-expected['inv'][s]) for s in inv})
        errors['sell']=abs(sell-expected['sell'])
        errors['fillCount']=abs(len(events)-expected['fills'])
    ok=all(v<=max(EPS,1e-8*max(abs(buy),1)) for v in errors.values())
    r=dict(asset=asset,marketId=mid,startMs=start,endMs=end,sourceLegs=len(events),duplicates=duplicates,
        reconstructionPass=ok,reconstructionErrors=errors,expected=expected,reconstructed=dict(buy=buy,sell=sell,pnl=pnl,inv=inv),
        fill30=[0]*10,maker30=[0]*10,taker30=[0]*10,firstOrder30=[0]*10,makerFirstOrder30=[0]*10,takerFirstOrder30=[0]*10,
        activeSeconds30=[0]*10,buy30=[0.]*10,shares30=[0.]*10,observed30=[0]*10,observedOutsideWindow=0,
        eventOutsideWindow=0,missingOrderHash=0,lateSameOldOrderFills=0,lateFirstFillOrderFills=0,
        timestampExactSeconds=0,collectorBeforeEvent=0)
    seconds=[set() for _ in range(10)];parents={};inside=[];delays=[]
    for e in events:
        if not start<=e['t']<end:r['eventOutsideWindow']+=1;continue
        inside.append(e);i=(e['t']-start)//30000
        r['fill30'][i]+=1;r['maker30' if e['role']=='MAKER' else 'taker30'][i]+=1
        r['shares30'][i]+=e['q']
        if e['quote']=='BID':r['buy30'][i]+=e['p']*e['q']
        seconds[i].add(e['t']//1000);r['timestampExactSeconds']+=e['t']%1000==0
        if e['obs'] is not None:
            delays.append(e['obs']-e['t']);r['collectorBeforeEvent']+=e['obs']<e['t']
            if start<=e['obs']<end:r['observed30'][(e['obs']-start)//30000]+=1
            else:r['observedOutsideWindow']+=1
        if not e['order']:r['missingOrderHash']+=1;continue
        key=(e['role'],e['side'],e['quote'],e['order'])
        if key not in parents:
            parents[key]=e['t'];r['firstOrder30'][i]+=1
            r['makerFirstOrder30' if e['role']=='MAKER' else 'takerFirstOrder30'][i]+=1
        if i>=4:r['lateSameOldOrderFills' if parents[key]<start+120000 else 'lateFirstFillOrderFills']+=1
    r['activeSeconds30']=[len(s) for s in seconds]
    r.update(inWindowLegs=len(inside),distinctFilledOrders=len(parents),lastFillRemainingSeconds=(end-inside[-1]['t'])/1000 if inside else None,
        firstFillElapsedSeconds=(inside[0]['t']-start)/1000 if inside else None,collectorDelayMedianMs=st.median(delays) if delays else None,
        collectorDelayP95Ms=percentile(delays,.95),dataStatus='RECONCILED' if ok and inside else 'MISSING_OR_RECONCILIATION_FAILED')
    return r,events

def aggregate(rows):
    good=[r for r in rows if r['dataStatus']=='RECONCILED'];n=len(good)
    if not n:return dict(markets=0,requested=len(rows),missing=[r['marketId'] for r in rows])
    ret=dict(markets=n,requested=len(rows),missing=[r['marketId'] for r in rows if r not in good],
        start=iso(min(r['startMs'] for r in good)),end=iso(max(r['endMs'] for r in good)),
        totalLegs=sum(r['inWindowLegs'] for r in good),totalOrders=sum(r['distinctFilledOrders'] for r in good),
        totalEventOutsideWindow=sum(r['eventOutsideWindow'] for r in good),totalObservedOutsideWindow=sum(r['observedOutsideWindow'] for r in good),
        duplicateLegs=sum(r['duplicates'] for r in good),missingOrderHashes=sum(r['missingOrderHash'] for r in good),
        exactSecondTimestampFraction=sum(r['timestampExactSeconds'] for r in good)/sum(r['inWindowLegs'] for r in good),
        collectorDelayMedianAcrossMarketsMs=st.median(r['collectorDelayMedianMs'] for r in good if r['collectorDelayMedianMs'] is not None),
        lastFillRemainingMedianSeconds=st.median(r['lastFillRemainingSeconds'] for r in good),
        lastFillRemainingP10=percentile([r['lastFillRemainingSeconds'] for r in good],.1),lastFillRemainingP90=percentile([r['lastFillRemainingSeconds'] for r in good],.9),
        marketsTradingAfter180=sum(sum(r['fill30'][4:])>0 for r in good),marketsTradingFinal60=sum(sum(r['fill30'][8:])>0 for r in good),
        marketsTradingFinal30=sum(r['fill30'][9]>0 for r in good),marketsTakerAfter180=sum(sum(r['taker30'][4:])>0 for r in good),
        marketsMakerAfter180=sum(sum(r['maker30'][4:])>0 for r in good),metrics={})
    for key in ['fill30','maker30','taker30','firstOrder30','makerFirstOrder30','takerFirstOrder30','activeSeconds30','buy30','shares30','observed30']:
        totals=vector(good,key);minute=[totals[2*i]+totals[2*i+1] for i in range(5)]
        per=[minute[i]/n for i in range(5)];early=sum(totals[:4])/(n*2);late=sum(totals[4:])/(n*3)
        pairs=[];norm=[];slopes=[];decrease=increase=tie=monotone=postmonotone=0
        for r in good:
            a=r[key];m=[a[2*i]+a[2*i+1] for i in range(5)];early_r=sum(m[:2])/2;late_r=sum(m[2:])/3
            if early_r>0:pairs.append(late_r/early_r)
            norm.append([v/sum(m) if sum(m) else 0 for v in m])
            if late_r<early_r-1e-9:decrease+=1
            elif late_r>early_r+1e-9:increase+=1
            else:tie+=1
            monotone+=all(m[i+1]<=m[i] for i in range(4))
            postmonotone+=m[2]>=m[3]>=m[4]
            slopes.append(sum((i-2)*m[i] for i in range(5))/10)
        ret['metrics'][key]=dict(total=sum(totals),totals30=totals,perMarketPerMinute=per,
            equalMarketPhaseShares=[st.mean(a[i] for a in norm) for i in range(5)],
            medianMarketPerMinute=[st.median(r[key][2*i]+r[key][2*i+1] for r in good) for i in range(5)],
            earlyPerMarketMinute=early,latePerMarketMinute=late,lateVsEarlyPooledRateRatio=late/early if early else None,
            lateShare=sum(totals[4:])/sum(totals) if sum(totals) else None,lateVsEarlyMedianMarketRatio=st.median(pairs) if pairs else None,
            ratioEligibleMarkets=len(pairs),marketsLateLower=decrease,marketsLateHigher=increase,marketsLateEqual=tie,
            marketsMonotonicNonincreasing5=monotone,marketsPost180MonotonicNonincreasing3=postmonotone,
            finalMinuteVsEarlyRateRatio=per[4]/early if early else None,marketsFinalMinuteBelowEarly=sum(sum(r[key][8:])<sum(r[key][:4])/2 for r in good),
            slopeMedian=st.median(slopes),marketsNegativeSlope=sum(s<0 for s in slopes))
    ret['lateOrderOrigins']=dict(fillsOfOrdersFirstFilledBefore180=sum(r['lateSameOldOrderFills'] for r in good),fillsOfOrdersFirstFilledAfter180=sum(r['lateFirstFillOrderFills'] for r in good),
        warning='First fill is not placement; unfilled orders absent')
    return ret

def main():
    if OUT.exists() or EXTRACT.exists():raise RuntimeError('immutable output exists')
    started=time.monotonic();sources=[];allrows=[];export=[]
    manifest=readsmall(BUNDLE/'manifest.json');btc={int(r['market_id']):r for r in manifest['markets']};assert len(btc)==50
    resultrows={}
    p=BUNDLE/'market_results.jsonl';sources.append(dict(path=str(p),bytes=p.stat().st_size,sha256=hashfile(p)))
    for line in p.open(encoding='utf-8'):
        r=json.loads(line)
        if int(r['market_id']) in btc:resultrows[int(r['market_id'])]=r
    actions=defaultdict(list);p=BUNDLE/'target_actions.jsonl'
    if p.stat().st_size>50*1024**2:raise RuntimeError('use indexed source instead')
    h=hashlib.sha256();streamed=0
    with p.open('rb') as f:
        for line in f:
            h.update(line);streamed+=1;r=json.loads(line);mid=int(r['market_id'])
            if mid not in btc:continue
            actions[mid].append(dict(leg=r['source_leg_id'],t=int(r['event_ms']),obs=r['observed_at_ms'],role=r['role'],side=r['side'],quote=r['quote_type'],order=r['order_hash'],p=float(r['price']),q=float(r['shares'])))
    sources.append(dict(path=str(p),bytes=p.stat().st_size,sha256=h.hexdigest(),streamedRows=streamed))
    for mid,meta in sorted(btc.items(),key=lambda x:(x[1]['window_start_ms'],x[0])):
        x=resultrows[mid];exp=dict(buy=x['buy_notional_usdt'],sell=x['sell_proceeds_usdt'],pnl=x['net_pnl_usdt'],inv={'UP':x['up_position_shares'],'DOWN':x['down_position_shares']},fills=x['fill_count'],winner=x['winner'])
        r,es=profile('BTC',mid,meta['window_start_ms'],meta['window_end_ms'],actions[mid],exp);allrows.append(r)
        export.extend(dict(asset='BTC',marketId=mid,**e) for e in es)
    eth={}
    for i in range(4):
        p=Path(f'data/research/lan_worker_returns/eth-pair-only-large100-s{i}-20260905-v1/result.json');d=readsmall(p);sources.append(dict(path=str(p),bytes=p.stat().st_size,sha256=hashfile(p)))
        for r in d['rows']:
            mid=int(r['marketId']);assert mid not in eth;eth[mid]=dict(buy=r['targetBuyPostHocOnly'],pnl=r['targetPnlPostHocOnly'],winner=r['winnerPostHocOnly'])
    assert len(eth)==100
    p=BASE/'target_eth_fill_legs_v3_snapshot_20260904.db';info=p.stat();con=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=2);con.execute('PRAGMA query_only=ON');deadline=time.monotonic()+20
    con.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
    query='SELECT leg_id,event_ms,observed_at_ms,role,side,order_hash,price,shares FROM eth_events INDEXED BY idx_eth_market_time WHERE market_id=? ORDER BY event_ms,id LIMIT 5001'
    plan=con.execute('EXPLAIN QUERY PLAN '+query,(next(iter(eth)),)).fetchall();assert any('SEARCH' in str(x) and 'idx_eth_market_time' in str(x) for x in plan)
    ethrecords=[]
    for mid,exp in sorted(eth.items()):
        meta=con.execute('SELECT window_end_ms FROM eth_markets WHERE market_id=?',(mid,)).fetchone()
        if meta is None:allrows.append(dict(asset='ETH',marketId=mid,dataStatus='MISSING_MARKET_METADATA'));continue
        rows=con.execute(query,(mid,)).fetchall();assert len(rows)<5001
        es=[dict(leg=a,t=int(b),obs=c,role=d,side=e,order=f,p=float(g),q=float(h),quote='BID') for a,b,c,d,e,f,g,h in rows]
        end=int(meta[0]);r,es=profile('ETH',mid,end-300000,end,es,exp);r['quoteTypeBoundary']='Snapshot lacks quote_type; buy-only interpreted and compared against archived Target cashflow. This does not identify gross sell legs independently.';allrows.append(r)
        ethrecords.extend(dict(asset='ETH',marketId=mid,**e) for e in es)
    con.close();assert p.stat().st_size==info.st_size and p.stat().st_mtime_ns==info.st_mtime_ns,'snapshot changed'
    export.extend(ethrecords);sources.append(dict(path=str(p),bytes=info.st_size,modifiedNs=info.st_mtime_ns,queryPlan=plan,queries=len(eth),extractedRecordSha256=digest(ethrecords),noFullDbHashOrScan=True))
    matched=readsmall(BASE/'PAIR_CORE_CROSSASSET10_COHORT_V1_20260910.json');mids={r['marketId'] for r in matched['rows']}
    summaries={}
    for asset in ('BTC','ETH'):
        rs=[r for r in allrows if r['asset']==asset];summaries[asset+'_ALL_CONSUMED']=aggregate(rs);summaries[asset+'_MATCHED5']=aggregate([r for r in rs if r['marketId'] in mids])
        good=sorted([r for r in rs if r['dataStatus']=='RECONCILED'],key=lambda r:(r['startMs'],r['marketId']))
        for label,part in [('EARLIER_HALF',good[:len(good)//2]),('LATER_HALF',good[len(good)//2:])]:summaries[asset+'_'+label]=aggregate(part)
    own=[]
    for asset in ('BTC','ETH'):
        p=Path(f'data/research/lan_worker_returns/hft244-pair-crossasset10-{asset.lower()}-20260910-v1/COMPACT.json');d=readsmall(p);sources.append(dict(path=str(p),bytes=p.stat().st_size,sha256=hashfile(p)))
        for row in d['rows']:
            meta=next(r for r in matched['rows'] if r['marketId']==row['marketId']);start=meta['windowStartMs'];end=meta['windowEndMs'];recv=[0]*5;ex=[0]*5;active=[set() for _ in range(5)]
            for x in row['receipts']:
                tr=x['receive_ts']//1000000;te=x['exchange_ts']//1000000
                if start<=tr<end:idx=(tr-start)//60000;recv[idx]+=1;active[idx].add(tr//1000)
                if start<=te<end:ex[(te-start)//60000]+=1
            own.append(dict(asset=asset,marketId=row['marketId'],arm=row['arm'],receiveCounts60=recv,exchangeCounts60=ex,activeReceiveSeconds60=[len(s) for s in active],lateReceiveCount=sum(recv[2:]),lateExchangeCount=sum(ex[2:])))
    ownsum={}
    for asset in ('BTC','ETH'):
        for arm in sorted({r['arm'] for r in own}):
            rs=[r for r in own if r['asset']==asset and r['arm']==arm]
            ownsum[asset+'_'+arm]=dict(markets=len(rs),receiveFillsPerMarketMinute=[sum(r['receiveCounts60'][i] for r in rs)/len(rs) for i in range(5)],
                exchangeFillsPerMarketMinute=[sum(r['exchangeCounts60'][i] for r in rs)/len(rs) for i in range(5)],lateFillMarkets=sum(r['lateReceiveCount']>0 for r in rs),lateReceiveCount=sum(r['lateReceiveCount'] for r in rs))
    raw=''.join(json.dumps(r,separators=(',',':'),allow_nan=False)+'\n' for r in export).encode();assert len(raw)<30*1024**2
    EXTRACT.write_bytes(raw)
    output=dict(version='PAIR_CORE_TARGET_POST180_CADENCE_V1',mode='OBSERVATIONAL_FIXED_CONSUMED_SOURCES',newHFT=0,training=0,policyChanged=False,
        noFreshOrLockedOrSealed=True,requestedMarkets=150,sources=sources,summaries=summaries,markets=allrows,ourMatched=own,ourSummary=ownsum,
        extractedEvents=dict(path=str(EXTRACT),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),count=len(export)),
        missingOrInconsistent=[{k:r.get(k) for k in ('asset','marketId','dataStatus','reconstructionErrors')} for r in allrows if r['dataStatus']!='RECONCILED'],
        limitations=['Fills/first fills are not placement/cancel rates.','Observations conditional on legacy consumed cohorts and retained data; total market population not random.','BTC and ETH dates differ; calendar and asset confounded.','Market-level activity taper is not causal effect of elapsed time; market conditions and inventory change.','No protocol permissions or180s fence changed.','Timestamp coarse and collector-lag distributions bound precision.','Target and OUR order grouping differ; equal-market rates and raw legs both shown.','Market result fields called net may omit explicit fees.'],elapsedSeconds=time.monotonic()-started)
    OUT.write_text(json.dumps(output,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    short={k:{a:v[a] for a in ('markets','requested','start','end','totalLegs','totalOrders','marketsTradingAfter180','marketsTradingFinal60','marketsTradingFinal30','marketsTakerAfter180','lastFillRemainingMedianSeconds','totalEventOutsideWindow','totalObservedOutsideWindow')}|{'fills':v['metrics']['fill30'],'firstOrders':v['metrics']['firstOrder30'],'activeSeconds':v['metrics']['activeSeconds30']} for k,v in summaries.items() if 'ALL_CONSUMED' in k or 'MATCHED5' in k}
    print(json.dumps(dict(output=str(OUT),bytes=OUT.stat().st_size,sha256=hashfile(OUT),elapsed=output['elapsedSeconds'],missing=output['missingOrInconsistent'],summaries=short,own=ownsum),ensure_ascii=False))

if __name__=='__main__':main()
