"""Fixed-archive Maker size / minimum notional price-domain audit.

Observed filled order amounts do NOT identify requested order quantity. No HFT,
quote submission, private-intent labels, or outcome selection. Does not infer an
upper bound from a modal size. Input cohorts and tiers were already observed.
"""
from pathlib import Path
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_CEILING
import hashlib, json, math, statistics, time

BASE=Path('data/research/r4_v0/p0_provenance_v1')
FILES=[
 ('HISTORICAL','PAIR_CORE_TARGET_POST180_EVENTS_COMPACT_V1_20260910.jsonl','a105a6fa22274f24132c7fa2ef8552fdd035694d0ce7b6cedafea0e61d2d4090'),
 ('RECENT','TARGET_SIZE_REGIME_LIFECYCLE_OBSERVATION_V1_20260910_1855_RECENT_EVENTS.jsonl','dd2adf892fbd4c46047212d5e935ae8aa7ff1c5311d72725e895dcdc52546e27')]
TIERS={'BTC':[15,18,30,55],'ETH':[5,10,12]}
EXPECTED={('BTC','HISTORICAL'):(50,14099),('ETH','HISTORICAL'):(100,1971),('BTC','RECENT'):(24,4292),('ETH','RECENT'):(24,435)}
EPS=1e-7


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def min_price(q,minimum=1,tick='.01'):
    a=Decimal(str(minimum))/Decimal(str(q));step=Decimal(str(tick))
    return float((a/step).to_integral_value(rounding=ROUND_CEILING)*step)


def percentile(values,fraction):
    if not values:return None
    a=sorted(values);v=(len(a)-1)*fraction;lo=math.floor(v);hi=math.ceil(v)
    return a[lo]+(a[hi]-a[lo])*(v-lo)


def describe(rows):
    ps=[r['price'] for r in rows]
    return dict(orders=len(rows),markets=len({r['marketId'] for r in rows}),
        minPrice=min(ps) if ps else None,p01=percentile(ps,.01),p05=percentile(ps,.05),
        medianPrice=statistics.median(ps) if ps else None,maxPrice=max(ps) if ps else None,
        sumFilledQty=sum(r['qty'] for r in rows),filledCash=sum(r['cash'] for r in rows),
        executedNotionalBelow1=sum(r['cash']<1-EPS for r in rows),
        multiplePriceOrders=sum(r['maxPrice']-r['minPrice']>EPS for r in rows))


def small_witness(r):
    return {k:r[k] for k in ('marketId','orderHash','side','firstMs','qty','price','minPrice','maxPrice','cash','legs')}


def main():
    began=time.monotonic();out=BASE/'TARGET_MAKER_SIZE_PRICE_FLOOR_SCORE_V1_20260910.json'
    parents_path=BASE/'TARGET_MAKER_SIZE_PRICE_FLOOR_PARENTS_V1_20260910.jsonl'
    if out.exists() or parents_path.exists():raise FileExistsError('immutable audit outputs exist')
    # Arithmetic tests include the direction of rounding; never infer q<=12/18.
    for q,p in [(5,.2),(10,.1),(12,.09),(15,.07),(18,.06),(30,.04),(55,.02),(100,.01)]:
        assert abs(min_price(q)-p)<1e-12 and q*p>=1-1e-12
        if p>.01:assert q*(p-.01)<1+1e-12
    sources=[];groups={};skip=Counter();seen=set()
    for regime,name,expected in FILES:
        path=BASE/name;size=path.stat().st_size
        if size>20*1024**2:raise ValueError('source size cap')
        digest=hashlib.sha256();lines=0
        with path.open('rb') as f:
            for raw in f:
                digest.update(raw);e=json.loads(raw);lines+=1
                identity=(regime,e['leg'])
                if identity in seen:raise ValueError('duplicate source leg')
                seen.add(identity)
                if e['role']!='MAKER':skip[regime+'_nonMaker']+=1;continue
                if e['quote']!='BID':skip[regime+'_nonBidMaker']+=1;continue
                q,p=float(e['q']),float(e['p'])
                assert math.isfinite(q) and math.isfinite(p) and q>0 and 0<p<1
                order=e.get('order') or e.get('order_id');assert order
                key=(regime,e['asset'],e['marketId'],order,e['side'],e['quote'])
                r=groups.setdefault(key,dict(regime=regime,asset=e['asset'],marketId=e['marketId'],
                    orderHash=order,side=e['side'],quote=e['quote'],qty=0.,cash=0.,legs=0,
                    firstMs=e['t'],lastMs=e['t'],minPrice=p,maxPrice=p))
                r['qty']+=q;r['cash']+=q*p;r['legs']+=1
                r['firstMs']=min(r['firstMs'],e['t']);r['lastMs']=max(r['lastMs'],e['t'])
                r['minPrice']=min(r['minPrice'],p);r['maxPrice']=max(r['maxPrice'],p)
        if digest.hexdigest()!=expected:raise ValueError('source changed')
        sources.append(dict(path=path.as_posix(),bytes=size,sha256=expected,eventRows=lines))
    rows=list(groups.values());summary={};tiers=[];bands=[];halves=[];bymarket=[]
    for r in rows:r['price']=r['cash']/r['qty']
    for (asset,regime),(nmarkets,norders) in EXPECTED.items():
        rs=[r for r in rows if r['asset']==asset and r['regime']==regime]
        mids=sorted({r['marketId'] for r in rs},key=lambda mid:min(r['firstMs'] for r in rs if r['marketId']==mid))
        assert len(rs)==norders and len(mids)==nmarkets
        s=describe(rs);s.update(asset=asset,regime=regime,
            nonCentPriceOrders=sum(abs(r['price']*100-round(r['price']*100))>1e-6 for r in rs),
            minPriceWitnesses=[small_witness(r) for r in sorted(rs,key=lambda r:(r['price'],r['firstMs']))[:3]])
        for label,bound in [('belowOld15PriceFloor',.07),('below30PriceFloor',.04),('below55PriceFloor',.02),('atOrBelow10c',.10000001)]:
            selected=[r for r in rs if r['price']<bound-EPS] if label!='atOrBelow10c' else [r for r in rs if r['price']<=.1+EPS]
            s[label]=dict(describe(selected),orderFraction=len(selected)/len(rs),
                marketFraction=len({r['marketId'] for r in selected})/len(mids),
                equalMarketOrderFraction=statistics.mean(sum(r['price']<bound-EPS if label!='atOrBelow10c' else r['price']<=.1+EPS for r in rs if r['marketId']==m)/sum(r['marketId']==m for r in rs) for m in mids))
        summary[asset+'_'+regime]=s
        for q in TIERS[asset]:
            matches=[r for r in rs if abs(r['qty']-q)<1e-6]
            single=[r for r in matches if r['maxPrice']-r['minPrice']<=EPS]
            pmin=min_price(q);at=[r for r in single if abs(r['price']-pmin)<EPS]
            below=[r for r in single if r['price']<pmin-EPS]
            tier=dict(describe(matches),asset=asset,regime=regime,observedFilledQtyTier=q,
                theoreticalMinimumPriceAtNotional1=pmin,theoreticalUnroundedMinimum=1/q,
                singlePriceOrders=len(single),singlePriceMin=min((r['price'] for r in single),default=None),
                atBoundaryOrders=len(at),atBoundaryMarkets=len({r['marketId'] for r in at}),
                belowBoundaryOrders=len(below),belowBoundaryMarkets=len({r['marketId'] for r in below}),
                atBoundaryWitnesses=[small_witness(r) for r in at[:3]],
                belowBoundaryWitnesses=[small_witness(r) for r in below[:3]],
                qualifier='conditional on observed filled quantity equaling tier, not proven requested quantity')
            tiers.append(tier)
        for low,high in [(0,.02),(.02,.04),(.04,.07),(.07,.1),(.1,1)]:
            selected=[r for r in rs if r['price']>=low-EPS and r['price']<high-EPS]
            counts=Counter(round(r['qty'],6) for r in selected)
            bands.append(dict(describe(selected),asset=asset,regime=regime,priceRange=[low,high],
                commonFilledSizes=[{'qty':q,'orders':n} for q,n in counts.most_common(8)],
                ordersAt30or55=sum(abs(r['qty']-30)<1e-6 or abs(r['qty']-55)<1e-6 for r in selected)))
        for label,ids in [('FIRST_HALF',mids[:len(mids)//2]),('SECOND_HALF',mids[len(mids)//2:])]:
            subset=[r for r in rs if r['marketId'] in ids];low=[r for r in subset if r['price']<.07-EPS]
            halves.append(dict(asset=asset,regime=regime,half=label,markets=len(ids),orders=len(subset),
                below07Orders=len(low),below07OrderFraction=len(low)/len(subset),below07Markets=len({r['marketId'] for r in low})))
        for mid in mids:
            subset=[r for r in rs if r['marketId']==mid]
            bymarket.append(dict(asset=asset,regime=regime,marketId=mid,orders=len(subset),minMakerFillPrice=min(r['minPrice'] for r in subset),
                low07Orders=sum(r['price']<.07-EPS for r in subset),low04Orders=sum(r['price']<.04-EPS for r in subset)))
    with parents_path.open('x',encoding='utf-8') as f:
        for r in rows:f.write(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n')
    result=dict(version='TARGET_MAKER_SIZE_PRICE_FLOOR_V1_20260910',
        classification='CONDITIONAL_MIN_NOTIONAL_GEOMETRY_AND_OBSERVED_PRICE_DOMAIN__NOT_INTENT_CAUSALITY',
        minimumOrderValue=1,minimumSource='USER_CONFIRMED_MAKER_CONTRACT_AND_CURRENT_PREDICT_OFFICIAL_DOC_INDEX; historical route-specific applicability not independently dated',
        tickForArithmetic='.01',originalRequestQuantityKnown=False,terminalStatusesKnown=False,
        sourceCohorts='fixed previously observed historicalBTC50/ETH100 and recentBTC24/ETH24 20260910 16:55-18:55 Asia/Taipei',
        sources=sources,summary=summary,tiers=tiers,bands=bands,temporalHalves=halves,perMarket=bymarket,
        skipped=dict(skip),parentsSource=dict(path=parents_path.as_posix(),bytes=parents_path.stat().st_size,sha256=sha(parents_path)),
        noPolicyOrSizingChanges=True,newHFT=0,newTraining=0,elapsedSeconds=time.monotonic()-began,
        limits=['An observed tier is cumulative executed shares, not original requested quantity; below-threshold observations may be partial fills.',
                'Quote feasibility does not prove execution, positive expectancy, or private motive for the size change.',
                'Different dates and price opportunity sets confound low-price frequency; no public-book exposure-time denominator computed.',
                'Previously known size/price minima motivated this audit; this is not preregistered unseen statistical confirmation.',
                'All-Maker minimum and modal-tier minimum are different claims; old larger/partial orders may already reach low prices.',
                'User12/18 minima not replaced by empirical10/15/30/55 tiers. No passive sizing floors are transferred toActive.'])
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(dict(output=out.as_posix(),bytes=out.stat().st_size,sha256=sha(out),
        elapsedSeconds=result['elapsedSeconds'],summary=summary,
        tiers=[{k:r[k] for k in ('asset','regime','observedFilledQtyTier','orders','markets','singlePriceMin','theoreticalMinimumPriceAtNotional1','atBoundaryOrders','atBoundaryMarkets','belowBoundaryOrders')} for r in tiers],
        bands=bands,temporalHalves=halves),ensure_ascii=False))


if __name__=='__main__':main()
