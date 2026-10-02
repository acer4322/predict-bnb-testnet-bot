"""0-BE outcome-only scoring with narrow historical provenance and cost bounds."""
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from collections import defaultdict

ROOT=Path(__file__).resolve().parents[1]
MIDS=[2022527,2022538,2022602]
RETURN='data/research/lan_worker_returns/root-native-composite-handback3-20260910-v1/COMPACT.json'
SOURCE='data/research/market_capsule_v1/source_bundle_50_v1/market_results.jsonl'
OUT='data/research/r4_v0/p0_provenance_v1/ROOT_HANDBACK_OUTCOME_COST_CLOSURE_COMPACT_V1_20260910.json'


def classify_fills(row):
    """Prove fee route, not arbitrary owner identity; exact-price groups only."""
    filled={k:o for k,o in row['orders'].items() if o['cum']>1e-9}
    records=[]; observed=defaultdict(float); expected=defaultdict(float)
    def route(key): return 'TAKER_ASSUMED_ACTIVE' if key in row['activeMeta'] else 'MAKER'
    for k,o in filled.items(): expected[(o['side'],round(o['price'],8),route(k))]+=o['cum']
    for t,side,qty,p in row['fillHistory']:
        candidates=[k for k,o in filled.items() if o['side']==side and
                    o['placed']<=t and abs(o['price']-p)<=1e-8]
        routes={route(k) for k in candidates}
        if len(routes)!=1:
            raise ValueError('Ambiguous or price-improved fill needs saved native owner receipt')
        fee_route=next(iter(routes)); observed[(side,round(p,8),fee_route)]+=qty
        records.append(dict(t=t,side=side,qty=qty,price=p,
                            owner=candidates[0] if len(candidates)==1 else None,
                            ownerIdentityUnique=len(candidates)==1,route=fee_route))
    assert observed.keys()==expected.keys()
    assert all(abs(observed[k]-expected[k])<=1e-8 for k in expected)
    assert abs(sum(x['qty']*x['price'] for x in records)-row['cost'])<=1e-8
    return records


def cost_interval(gross,fills,rate):
    fee=sum(rate*min(f['price'],1-f['price'])*f['qty'] for f in fills if f['route']=='TAKER_ASSUMED_ACTIVE')
    maker_qty=sum(f['qty'] for f in fills if f['route']=='MAKER')
    rebate_upper=.25*rate*maker_qty
    return dict(gross=gross,undiscountedTakerFee=fee,makerShares=maker_qty,
        makerRebateUpper=rebate_upper,lower=gross-fee,upper=gross-.9*fee+rebate_upper,
        liquidityUnresolvedEnvelope=[gross-fee,gross+.25*rate*sum(f['qty'] for f in fills)],
        liquidityCaveat='Active is GTC intent, not proof each fill was taker; envelope allows zero Active fees and maker rebate on those shares',
        assumptions='all Active fills charged taker; referral multiplier in [0.9,1]; historical rebate bound; excludes deployment costs')


def distribution(values):
    cumulative=peak=dd=0.
    for x in values:
        cumulative+=x; peak=max(peak,cumulative); dd=max(dd,peak-cumulative)
    pos=sum(max(x,0) for x in values); neg=-sum(min(x,0) for x in values)
    return dict(aggregate=sum(values),best=max(values),worst=min(values),
        positiveMarkets=sum(x>0 for x in values),markets=len(values),
        profitFactor=pos/neg if neg else None,leaveOneBestOut=sum(values)-max(values),
        chronologicalDrawdown=dd)


def main():
    result_path=ROOT/RETURN; archive_path=ROOT/SOURCE
    assert result_path.stat().st_size<=512*1024 and archive_path.stat().st_size<=64*1024
    sha=hashlib.sha256(result_path.read_bytes()).hexdigest()
    assert sha=='f2e28782d271c92c44681e24c494e5ff4ae31c91eb2f336beb9743ae88b6d197'
    data=json.loads(result_path.read_text())
    archive=[json.loads(line) for line in archive_path.read_text().splitlines()]
    selected=[r for r in archive if r['market_id'] in MIDS]
    assert len(selected)==3 and len({r['market_id'] for r in selected})==3
    cached={r['market_id']:r for r in selected}
    db=ROOT/'data/target_wallet_official_v1.db'
    con=sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)
    con.execute('PRAGMA query_only=ON'); con.execute('BEGIN')
    plans=[]; sources=[]
    try:
        for mid in MIDS:
            sql='SELECT market_id,winner,resolved_at_ms FROM target_market_results WHERE market_id=? LIMIT 1'
            plan=con.execute('EXPLAIN QUERY PLAN '+sql,(mid,)).fetchall()
            assert all('SCAN ' not in r[3].upper() for r in plan)
            plans.append(plan); r=con.execute(sql,(mid,)).fetchone()
            assert r and r[1] in ['UP','DOWN'] and r[1]==cached[mid]['winner'] and r[2]==cached[mid]['resolved_at_ms']
            sql2=('SELECT leg_id,event_ms,observed_at_ms,length(raw_json),'
                  'CASE WHEN length(raw_json)<=65536 THEN raw_json END '
                  'FROM wallet_shadow_target_events WHERE asset=? AND market_id=? ORDER BY event_ms LIMIT 1')
            plan2=con.execute('EXPLAIN QUERY PLAN '+sql2,('BTC',mid)).fetchall()
            assert all('SCAN ' not in r[3].upper() for r in plan2)
            plans.append(plan2); event=con.execute(sql2,('BTC',mid)).fetchone()
            assert event and event[4]
            raw=json.loads(event[4]); market=raw['market']; bps=market['feeRateBps']
            assert market['id']==mid and isinstance(bps,(int,float)) and 0<=bps<=1000
            sources.append(dict(marketId=mid,winner=r[1],resolvedAtMs=r[2],
                archiveCurrentDbParity=True,feeRateBps=bps,
                feeSampleEventMs=event[1],feeSampleObservedMs=event[2],feeSampleBytes=event[3],
                feeSampleSha256=hashlib.sha256(event[4].encode()).hexdigest(),
                provenance='one existing official match market-metadata sample; no Target amounts/prices/features used',
                limitation='not a per-OUR-fill fee receipt or proof fee schedule never changed'))
    finally:
        con.rollback(); con.close()
    bymid={r['marketId']:r for r in sources}; rows=[]
    for row in data['rows']:
        mid=row['marketId']; source=bymid[mid]; winner=source['winner']
        fills=classify_fills(row)
        assert source['resolvedAtMs']>max(f['t'] for f in fills)
        interval=cost_interval(row[winner],fills,source['feeRateBps']/10000)
        rows.append(dict(marketId=mid,branch=row['branch'],winnerScoringOnly=winner,
            gross=row[winner],costInterval=interval,fills=fills,
            floor=row['floor'],best=row['best'],submits=row['submits'],
            scopeCompletions=row['scopeCompletions']))
    summary={}
    for branch in ['S','P']:
        rs=[r for r in rows if r['branch']==branch]
        summary[branch]=dict(grossDistribution=distribution([r['gross'] for r in rs]),
            conditionalNetAggregateInterval=[sum(r['costInterval'][k] for r in rs) for k in ['lower','upper']],
            liquidityUnresolvedAggregateEnvelope=[sum(r['costInterval']['liquidityUnresolvedEnvelope'][i] for r in rs) for i in [0,1]],
            fills=sum(len(r['fills']) for r in rs),submits=sum(r['submits'] for r in rs))
    s=summary['S']; p=summary['P']
    result=dict(version='ROOT_HANDBACK_OUTCOME_COST_CLOSURE_V1',source=RETURN,sourceSha256=sha,
        outcomeArchive=SOURCE,outcomeArchiveSha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        metadataProvenance=sources,queryPlans=plans,rows=rows,summary=summary,
        realizedGrossDelta=p['grossDistribution']['aggregate']-s['grossDistribution']['aggregate'],
        costBoundsAreConditional=True,exactNetIdentified=False,
        verdict='NO_REALIZED_GROSS_GAIN_NO_STABLE_NET_PROFIT_EVIDENCE',
        sourceParity='cache and current source agree; not independent on-chain resolution verification',
        additionalBE=0,modelsTrained=0,freshUsed=0,trainingReady=False,promotion=False)
    (ROOT/OUT).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ['rows','queryPlans']}))


if __name__=='__main__': main()
