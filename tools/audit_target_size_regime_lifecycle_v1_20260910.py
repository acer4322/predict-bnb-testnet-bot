"""Bounded observational lifecycle proxies across pre-existing size regimes.
No policy, HFT, training, raw order-size inference, or semantic repair labels.
Same exchange-event timestamp is one batch; no invented intrasecond ordering.
Recent cohort is exactly prior frozen BTC24/ETH24 ending20260910 18:55 +08.
"""
from pathlib import Path
from collections import Counter, defaultdict
import datetime, hashlib, json, math, sqlite3, statistics, time

BASE=Path('data/research/r4_v0/p0_provenance_v1')
WALLET='0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03'
PREFIX='TARGET_SIZE_REGIME_LIFECYCLE_OBSERVATION_V1_20260910_1855'
EPS=1e-8


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path,cap):
    p=Path(path)
    if p.stat().st_size>cap:raise ValueError('bounded input exceeded: '+str(p))
    return json.loads(p.read_text(encoding='utf-8-sig'))


def analyze(meta,events):
    start,end=meta['startMs'],meta['endMs']
    if end-start!=300000:raise ValueError('not5M')
    batches=defaultdict(list);seen=set();routes=Counter();sides=Counter()
    for e in events:
        if e['leg'] in seen:raise ValueError('duplicate leg')
        seen.add(e['leg'])
        if not start<=e['t']<end:raise ValueError('event outside market')
        if e['side'] not in ('UP','DOWN') or e['role'] not in ('MAKER','TAKER'):raise ValueError('unknown side/route')
        if not (math.isfinite(e['p']) and math.isfinite(e['q']) and 0<e['p']<1 and e['q']>0):raise ValueError('invalid fill')
        if e['quote'] not in ('BID','ASK'):raise ValueError('unknown buy/sell')
        routes[e['role']]+=1;sides[e['side']]+=1;batches[e['t']].append(e)
    inv={'UP':0.,'DOWN':0.};buy=sell=0.;previous=None;updown_turns=0
    increases=decreases=flats=flips=0;byphase=[0]*5;bothroutes=0
    for t,es in sorted(batches.items()):
        pre_net=inv['UP']-inv['DOWN'];before=abs(pre_net)
        if len({e['role'] for e in es})==2:bothroutes+=1
        byphase[min(4,int((t-start)//60000))]+=1
        for e in es:
            sign=1 if e['quote']=='BID' else -1
            inv[e['side']]+=sign*e['q']
            if sign>0:buy+=e['p']*e['q']
            else:sell+=e['p']*e['q']
        post_net=inv['UP']-inv['DOWN'];change=abs(post_net)-before
        state='INCREASE' if change>EPS else 'DECREASE' if change<-EPS else 'FLAT'
        if state=='INCREASE':increases+=1
        elif state=='DECREASE':decreases+=1
        else:flats+=1
        if state!='FLAT':
            if previous=='INCREASE' and state=='DECREASE':updown_turns+=1
            previous=state
        if pre_net*post_net<-EPS:flips+=1
    cost=buy-sell
    return dict(asset=meta['asset'],marketId=meta['marketId'],regime=meta['regime'],startMs=start,endMs=end,
        fillLegs=len(events),eventBatches=len(batches),makerLegs=routes['MAKER'],takerLegs=routes['TAKER'],
        bothExecutionRoutes=bool(routes['MAKER'] and routes['TAKER']),twoSided=bool(sides['UP'] and sides['DOWN']),
        lateFills=sum(e['t']>=start+120000 for e in events),lateTakerLegs=sum(e['t']>=start+120000 and e['role']=='TAKER' for e in events),
        phaseActiveEventBatches=byphase,mixedRouteBatches=bothroutes,absNetIncreaseBatches=increases,
        absNetDecreaseBatches=decreases,absNetFlatBatches=flats,netSideFlips=flips,
        increaseToDecreaseTurns=updown_turns,turnsPer100EventBatches=100*updown_turns/len(batches) if batches else None,
        repeatedInventoryContractionProxy=updown_turns>=2,semanticCycles=None,
        lastFillRemainingSeconds=(end-max(batches))/1000 if batches else None,
        buy=buy,sell=sell,inventory=inv,UP=inv['UP']-cost,DOWN=inv['DOWN']-cost,
        currentOriginalRequestedQtyKnown=False)


def selftest():
    meta=dict(asset='TEST',marketId=0,regime='fixture',startMs=0,endMs=300000)
    def e(n,t,side,q,route):return dict(leg=str(n),t=t,side=side,q=q,p=.3,role=route,quote='BID')
    rows=[e(1,1000,'UP',10,'MAKER'),e(2,2000,'DOWN',5,'TAKER'),e(3,3000,'UP',5,'MAKER'),e(4,4000,'DOWN',4,'MAKER')]
    x=analyze(meta,rows);assert x['increaseToDecreaseTurns']==2 and x['semanticCycles'] is None
    # This is an algebraic invariance of the descriptor, not empirical scale transfer.
    y=analyze(meta,[dict(r,q=r['q']*7) for r in rows]);assert x['increaseToDecreaseTurns']==y['increaseToDecreaseTurns']
    z=analyze(meta,[dict(r,t=1000) for r in rows]);assert z['eventBatches']==1 and z['increaseToDecreaseTurns']==0
    rev=analyze(meta,list(reversed([dict(r,t=1000) for r in rows])));assert abs(z['UP']-rev['UP'])<EPS
    return 4


def main():
    started=time.monotonic();tests=selftest();out=BASE/(PREFIX+'.json');recent_path=BASE/(PREFIX+'_RECENT_EVENTS.jsonl')
    if out.exists() or recent_path.exists():raise FileExistsError('immutable output exists')
    size_path=BASE/'TARGET_RECENT_MAKER_SHARES_SCORE_V2_20260910_1855.json'
    size=load(size_path,400000);assert sha(size_path)=='a777d6c3dad31fe936a957190addd24106cbf1ae087fd0456006fd92a236575f'
    cadence_path=BASE/'PAIR_CORE_TARGET_POST180_CADENCE_SCORE_V1_20260910.json';old=load(cadence_path,800000)
    metas={(m['asset'],m['marketId']):dict(asset=m['asset'],marketId=m['marketId'],regime='HISTORICAL',startMs=m['startMs'],endMs=m['endMs'],expectedLegs=m['sourceLegs']) for m in old['markets']}
    for m in size['perMarket']:
        if m['period']=='RECENT_2H':
            key=(m['asset'],m['market_id']);assert key not in metas
            metas[key]=dict(asset=m['asset'],marketId=m['market_id'],regime='RECENT',startMs=m['window_start_ms'],endMs=m['window_end_ms'])
    recent=[m for m in metas.values() if m['regime']=='RECENT'];assert len(recent)==48
    sets={a:{m['startMs'] for m in recent if m['asset']==a} for a in ('BTC','ETH')};assert sets['BTC']==sets['ETH'] and len(sets['BTC'])==24
    events=defaultdict(list);hist_path=BASE/'PAIR_CORE_TARGET_POST180_EVENTS_COMPACT_V1_20260910.jsonl'
    assert hist_path.stat().st_size<20*1024**2;h=hashlib.sha256()
    with hist_path.open('rb') as f:
        for raw in f:
            h.update(raw);e=json.loads(raw);events[(e['asset'],e['marketId'])].append(e)
    assert h.hexdigest()=='a105a6fa22274f24132c7fa2ef8552fdd035694d0ce7b6cedafea0e61d2d4090'
    db=Path('data/target_wallet_official_v1.db');c=sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
    c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('PRAGMA cache_size=-8192')
    deadline=time.monotonic()+10;c.set_progress_handler(lambda:int(time.monotonic()>deadline),10000);c.execute('BEGIN')
    sql="""SELECT leg_id AS leg,event_ms AS t,observed_at_ms AS obs,role,side,quote_type AS quote,
        order_hash AS order_id,price AS p,shares AS q FROM wallet_shadow_target_events
        INDEXED BY idx_target_events_asset_market_time WHERE asset=? AND market_id=? AND wallet=?
        ORDER BY event_ms,id LIMIT 10001"""
    qp=[tuple(x) for x in c.execute('EXPLAIN QUERY PLAN '+sql,('BTC',recent[0]['marketId'],WALLET))]
    assert not any('SCAN wallet_shadow' in str(x) for x in qp)
    expected={};recent_rows=[]
    for m in recent:
        key=(m['asset'],m['marketId']);rows=[dict(r) for r in c.execute(sql,(*key,WALLET))]
        assert len(rows)<10001 and sum(len(v) for v in events.values())+len(rows)<100000
        for r in rows:r.update(asset=key[0],marketId=key[1])
        events[key]=rows;recent_rows.extend(rows)
        res=c.execute('SELECT asset,fill_count,buy_notional_usdt,sell_proceeds_usdt,up_position_shares,down_position_shares,accounting_version FROM target_market_results WHERE market_id=?',(key[1],)).fetchone()
        if res is None:raise ValueError('missing result reference')
        expected[key]=dict(res)
    c.rollback();c.close();results=[];maxerr=0.
    for key,m in metas.items():
        rows=events[key];r=analyze(m,rows)
        if m['regime']=='HISTORICAL':assert len(rows)==m['expectedLegs']
        else:
            ex=expected[key];assert ex['asset']==key[0] and ex['fill_count']==len(rows)
            errors=[abs(r['buy']-ex['buy_notional_usdt']),abs(r['sell']-ex['sell_proceeds_usdt']),abs(r['inventory']['UP']-ex['up_position_shares']),abs(r['inventory']['DOWN']-ex['down_position_shares'])]
            r['sourceReconstructionError']=max(errors);maxerr=max(maxerr,max(errors));assert max(errors)<1e-6
        results.append(r)
    groups={}
    for a in ('BTC','ETH'):
        for regime in ('HISTORICAL','RECENT'):
            rs=[r for r in results if r['asset']==a and r['regime']==regime]
            med=lambda k:statistics.median(r[k] for r in rs if r[k] is not None)
            groups[a+'_'+regime]=dict(markets=len(rs),bothSidesMarkets=sum(r['twoSided'] for r in rs),
                bothRoutesMarkets=sum(r['bothExecutionRoutes'] for r in rs),lateTradingMarkets=sum(r['lateFills']>0 for r in rs),
                repeatedContractionProxyMarkets=sum(r['repeatedInventoryContractionProxy'] for r in rs),
                medianIncreaseToDecreaseTurns=med('increaseToDecreaseTurns'),medianTurnsPer100EventBatches=med('turnsPer100EventBatches'),
                medianEventBatches=med('eventBatches'),medianLastFillRemaining=med('lastFillRemainingSeconds'),
                medianTakerFillLegShare=statistics.median(r['takerLegs']/r['fillLegs'] for r in rs if r['fillLegs']),
                medianMixedRouteBatchShare=statistics.median(r['mixedRouteBatches']/r['eventBatches'] for r in rs if r['eventBatches']),
                semanticCycleCount=None)
    with recent_path.open('x',encoding='utf-8') as f:
        for r in recent_rows:f.write(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n')
    output=dict(version=PREFIX,classification='DESCRIPTIVE_SCALE_REGIME_LIFECYCLE_PROXIES_NOT_INVARIANT_POLICY_PROOF',
        evidenceStrength='Scale distribution shift confirmed separately; repeated realized inventory contractions checked here; semantic cycle equivalence not tested.',
        groups=groups,markets=results,newHFT=0,newTraining=0,policyChanges=0,tests=tests,recentMarketCount=48,
        recentBTCETHMatchedClockPairs=24,recentExtractRows=len(recent_rows),maxRecentReconstructionError=maxerr,
        sources=[{'path':p.as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in (size_path,cadence_path,hist_path,recent_path)],
        indexedQueryPlan=qp,elapsedSeconds=time.monotonic()-started,
        limitations=['Filled quantity is not original order quantity; unfilled orders and private economic intent unavailable.',
          'Same-second events are batched. Intra-batch order and Maker/Taker causal roles not identified.',
          'Repeated increase-to-decrease in abs(UP-DOWN) is an inventory geometry proxy, not semantic Repair-ADD completion, profitability, direction skill or Target authority.',
          'The sign/turn statistic is mathematically invariant to rescaling the same fills; agreement of that statistic alone is not evidence of adaptive sizing.',
          'Old/new time periods differ and larger filled size is endogenous to policy/liquidity. This is not a randomized size intervention.',
          'Current simultaneousBTC/ETH windows remove calendar mismatch for that comparison only; olderBTC/ETH periods differ.',
          'No price/depth matching, conditional policy transfer, fee model, settlement win rate, normalized payoff-value transfer, or OUR HFT was run.',
          'Recent48 Target markets used for descriptive architecture research are not untouched policy promotion labels.'])
    out.write_text(json.dumps(output,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({k:output[k] for k in ('classification','groups','recentBTCETHMatchedClockPairs','recentExtractRows','maxRecentReconstructionError','elapsedSeconds','newHFT','newTraining')},ensure_ascii=False))
    print('ARTIFACT',out.as_posix(),out.stat().st_size,sha(out))


if __name__=='__main__':main()
