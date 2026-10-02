"""Fixed198-market, scale-normalized Target control relation audit.

Descriptive only: fills are not submitted orders or private semantic objectives.
No HFT, model fit, policy import, DB/network reads, or live changes. Common support
is based on observed trade prices and portfolio shape, not public-book matching.
"""
from pathlib import Path
from collections import defaultdict, Counter
import hashlib, json, math, statistics, random, time, gzip

BASE=Path('data/research/r4_v0/p0_provenance_v1')
PREFIX='TARGET_CROSS_SIZE_CONTROL_V1_20260910'
META='TARGET_SIZE_REGIME_LIFECYCLE_OBSERVATION_V1_20260910_1855.json'
META_SHA='cb078598e3cfb80fbc5ab66afaf8b870494aeeed99fa0991a32fa14ea5043cbd'
INPUTS=[('HISTORICAL','PAIR_CORE_TARGET_POST180_EVENTS_COMPACT_V1_20260910.jsonl','a105a6fa22274f24132c7fa2ef8552fdd035694d0ce7b6cedafea0e61d2d4090'),
        ('RECENT','TARGET_SIZE_REGIME_LIFECYCLE_OBSERVATION_V1_20260910_1855_RECENT_EVENTS.jsonl','dd2adf892fbd4c46047212d5e935ae8aa7ff1c5311d72725e895dcdc52546e27')]
EPS=1e-8
LABELS=('nextHasTaker','nextNetContraction','nextFloorImprovement','nextSurplusFlip')


def mean(xs):return statistics.mean(xs) if xs else None

def med(xs):return statistics.median(xs) if xs else None

def quantile(xs,p):
    if not xs:return None
    a=sorted(xs);i=(len(a)-1)*p;l=math.floor(i);h=math.ceil(i)
    return a[l]+(a[h]-a[l])*(i-l)


def sign(x):return 1 if x>EPS else -1 if x<-EPS else 0

def file_sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def build_batches(meta,events):
    by=defaultdict(list);seen=set()
    for e in events:
        assert e['leg'] not in seen;seen.add(e['leg'])
        assert meta['startMs']<=e['t']<meta['endMs']
        assert e['side'] in ('UP','DOWN') and e['role'] in ('MAKER','TAKER') and e['quote'] in ('BID','ASK')
        assert all(math.isfinite(e[k]) for k in ('q','p')) and e['q']>0 and 0<e['p']<1
        by[e['t']].append(e)
    inv={'UP':0.,'DOWN':0.};cost=0.;out=[]
    for t,es in sorted(by.items()):
        pre=dict(inv);pre_net=pre['UP']-pre['DOWN'];pre_gross=sum(pre.values())
        pre_floor=min(pre.values())-cost;pre_best=max(pre.values())-cost
        qs=sum(e['q'] for e in es);routes={e['role'] for e in es};sides={e['side'] for e in es}
        buy_only=all(e['quote']=='BID' for e in es)
        flow={s:sum((1 if e['quote']=='BID' else -1)*e['q'] for e in es if e['side']==s) for s in inv}
        cash=sum((1 if e['quote']=='BID' else -1)*e['q']*e['p'] for e in es)
        for s in inv:inv[s]+=flow[s]
        cost+=cash;net=inv['UP']-inv['DOWN'];gross=sum(inv.values());floor=min(inv.values())-cost;best=max(inv.values())-cost
        r=dict(asset=meta['asset'],regime=meta['regime'],marketId=meta['marketId'],t=t,
          phase=min(4,int((t-meta['startMs'])//60000)),route=next(iter(routes)) if len(routes)==1 else 'MIXED',
          hasTaker='TAKER' in routes,fillLegs=len(es),buyOnly=buy_only,
          preNet=pre_net,preGross=pre_gross,preFloor=pre_floor,postNet=net,postGross=gross,postFloor=floor,
          postBest=best,cashDelta=cash,deltaFloor=floor-pre_floor,deltaBest=best-pre_best,
          netContraction=abs(net)<abs(pre_net)-EPS,netIncrease=abs(net)>abs(pre_net)+EPS,
          floorImprovement=floor>pre_floor+EPS,riskAddition=floor<pre_floor-EPS and best>pre_best+EPS,
          residualAfter=abs(net)>EPS,negativeFloorAfter=floor<-EPS,
          normalizedAbsNet=abs(net)/gross if gross>EPS else None,
          normalizedFloor=floor/gross if gross>EPS else None,
          observedYesPrice=sum(e['q']*(e['p'] if e['side']=='UP' else 1-e['p']) for e in es)/qs,
          commonPriceDomain=all(.1-EPS<=e['p']<=.9+EPS for e in es),
          lowPriceDomain=any(e['p']<.07-EPS for e in es),
          weakSideAcquisition=False,weakGapFraction=None,preciseNeutral=False,weakSideCross=False)
        if buy_only and len(sides)==1 and abs(pre_net)>EPS:
            side=next(iter(sides));weak='DOWN' if pre_net>0 else 'UP'
            if side==weak:
                r.update(weakSideAcquisition=True,weakGapFraction=qs/abs(pre_net),preciseNeutral=abs(net)<=EPS,
                         weakSideCross=sign(net)!=0 and sign(net)!=sign(pre_net))
        out.append(r)
    for i,r in enumerate(out):
        if i+1<len(out):
            nxt=out[i+1]
            r.update(nextHasTaker=nxt['hasTaker'],nextNetContraction=nxt['netContraction'],
                     nextFloorImprovement=nxt['floorImprovement'],
                     nextSurplusFlip=(sign(r['postNet'])*sign(nxt['postNet'])==-1),
                     nextRiskAddition=nxt['riskAddition'],nextRoute=nxt['route'])
        else:
            for label in LABELS:r[label]=None
            r['nextRiskAddition']=None;r['nextRoute']=None
    return out,dict(UP=inv['UP']-cost,DOWN=inv['DOWN']-cost,inventory=inv,cost=cost)


def rate_metrics(rows,condition,label):
    eligible=[r for r in rows if condition(r) and r.get(label) is not None]
    by=defaultdict(list)
    for r in eligible:by[r['marketId']].append(float(r[label]))
    return dict(events=len(eligible),markets=len(by),pooledRate=mean([float(r[label]) for r in eligible]),
                equalMarketRate=mean([mean(x) for x in by.values()]))


def describe(rows,market_ids):
    floor=[r for r in rows if r['floorImprovement']]
    weak=[r for r in rows if r['weakSideAcquisition']]
    def weak_summary(rs):
        by=defaultdict(list)
        for r in rs:by[r['marketId']].append(r['weakGapFraction'])
        return dict(events=len(rs),markets=len(by),medianGapFraction=med([r['weakGapFraction'] for r in rs]),
          medianOfMarketMedians=med([med(x) for x in by.values()]),q10=quantile([r['weakGapFraction'] for r in rs],.1),
          q90=quantile([r['weakGapFraction'] for r in rs],.9),
          partialBelowOne=sum(r['weakGapFraction']<1-1e-8 for r in rs),
          preciseNeutral=sum(r['preciseNeutral'] for r in rs),crossedNetSide=sum(r['weakSideCross'] for r in rs))
    result=dict(markets=len(market_ids),marketsRepresented=len({r['marketId'] for r in rows}),eventBatches=len(rows),
      floorImprovingBatches=len(floor),floorImprovementRetainingResidual=sum(r['residualAfter'] for r in floor),
      floorImprovementStillNegativeFloor=sum(r['negativeFloorAfter'] for r in floor),
      weakSide=weak_summary(weak),weakSideByRoute={route:weak_summary([r for r in weak if r['route']==route]) for route in ['MAKER','TAKER','MIXED']},
      routeFunctions={route:dict(events=sum(r['route']==route for r in rows),floorImproving=sum(r['route']==route and r['floorImprovement'] for r in rows),
          riskAdding=sum(r['route']==route and r['riskAddition'] for r in rows),
          marketsFloorImproving=len({r['marketId'] for r in rows if r['route']==route and r['floorImprovement']}),
          marketsRiskAdding=len({r['marketId'] for r in rows if r['route']==route and r['riskAddition']})) for route in ['MAKER','TAKER','MIXED']},
      nextObserved={
        'afterFloorImprovementThenRiskAddition':rate_metrics(rows,lambda r:r['floorImprovement'],'nextRiskAddition'),
        'afterRiskAdditionThenFloorImprovement':rate_metrics(rows,lambda r:r['riskAddition'],'nextFloorImprovement'),
        'afterMakerRiskAdditionThenTaker':rate_metrics(rows,lambda r:r['route']=='MAKER' and r['riskAddition'],'nextHasTaker'),
        'afterMakerNotRiskAdditionThenTaker':rate_metrics(rows,lambda r:r['route']=='MAKER' and not r['riskAddition'],'nextHasTaker')})
    return result


def overlap_comparison(left,right,name):
    groups=[]
    for rows in (left,right):
        filtered=[r for r in rows if r['commonPriceDomain'] and r['postGross']>EPS and r['nextHasTaker'] is not None]
        by_market=Counter(r['marketId'] for r in filtered);cells={}
        for r in filtered:
            p=r['observedYesPrice'];pb=0 if p<.3 else 1 if p<.5 else 2 if p<.7 else 3
            ib=min(3,int(r['normalizedAbsNet']*4));key=(r['phase'],ib,pb);w=1/by_market[r['marketId']]/len(by_market)
            c=cells.setdefault(key,dict(weight=0.,markets=set(),n=0,sums=Counter()))
            c['weight']+=w;c['markets'].add(r['marketId']);c['n']+=1
            for label in LABELS:c['sums'][label]+=w*float(r[label])
        groups.append(dict(filtered=filtered,cells=cells,markets=len(by_market),fullRows=len(rows)))
    a,b=groups;common=[k for k in a['cells'] if k in b['cells'] and len(a['cells'][k]['markets'])>=3 and len(b['cells'][k]['markets'])>=3]
    weights={k:min(a['cells'][k]['weight'],b['cells'][k]['weight']) for k in common};total=sum(weights.values())
    def standardized(g,label):
        return sum(weights[k]*g['cells'][k]['sums'][label]/g['cells'][k]['weight'] for k in common)/total if total else None
    def raw(g,label):return sum(c['sums'][label] for c in g['cells'].values())
    return dict(comparison=name,cellDefinition='current post-fill phase5 x absNet/gross4 x current executed-UP-equivalent-price4; both cells need>=3markets',
      commonCells=len(common),overlapMass=total,
      left=dict(allEvents=len(left),eligible=len(a['filtered']),markets=a['markets'],coveredMass=sum(a['cells'][k]['weight'] for k in common)),
      right=dict(allEvents=len(right),eligible=len(b['filtered']),markets=b['markets'],coveredMass=sum(b['cells'][k]['weight'] for k in common)),
      outcomes={label:dict(leftUnweightedByState=raw(a,label),rightUnweightedByState=raw(b,label),
          leftOverlapStandardized=standardized(a,label),rightOverlapStandardized=standardized(b,label),
          difference=standardized(b,label)-standardized(a,label) if total else None) for label in LABELS},
      cells=[dict(key=list(k),weight=weights[k],leftMarkets=len(a['cells'][k]['markets']),rightMarkets=len(b['cells'][k]['markets']),
          leftEvents=a['cells'][k]['n'],rightEvents=b['cells'][k]['n'],
          leftRates={l:a['cells'][k]['sums'][l]/a['cells'][k]['weight'] for l in LABELS},
          rightRates={l:b['cells'][k]['sums'][l]/b['cells'][k]['weight'] for l in LABELS}) for k in common])


def motif_count(routes):return sum(routes[i-1:i+2]==['MAKER','TAKER','MAKER'] for i in range(1,len(routes)-1))


def motif_audit(permarket):
    result=[]
    for key,bs in permarket.items():
        routes=[r['route'] for r in bs];n=max(0,len(routes)-2);observed=motif_count(routes);inner_taker=sum(r=='TAKER' for r in routes[1:-1])
        phase_indices=defaultdict(list)
        for i,r in enumerate(bs):phase_indices[r['phase']].append(i)
        sims=[]
        for rep in range(20):
            rng=random.Random(20260910+key[2]*31+rep);perm=list(routes)
            for inds in phase_indices.values():
                vals=[routes[i] for i in inds];rng.shuffle(vals)
                for i,v in zip(inds,vals):perm[i]=v
            sims.append(motif_count(perm))
        result.append(dict(asset=key[0],regime=key[1],marketId=key[2],triples=n,motif=observed,
            pureTakerCenters=inner_taker,conditionalOnPureTaker=observed/inner_taker if inner_taker else None,
            observedRate=observed/n if n else None,phaseShuffleMeanRate=mean(sims)/n if n else None,
            difference=(observed-mean(sims))/n if n else None))
    summaries={}
    for a in ['BTC','ETH']:
        for regime in ['HISTORICAL','RECENT']:
            rs=[r for r in result if r['asset']==a and r['regime']==regime]
            summaries[a+'_'+regime]=dict(markets=len(rs),motifs=sum(r['motif'] for r in rs),
                pureTakerCenters=sum(r['pureTakerCenters'] for r in rs),
                eventWeightedConditionalOnPureTaker=sum(r['motif'] for r in rs)/sum(r['pureTakerCenters'] for r in rs) if sum(r['pureTakerCenters'] for r in rs) else None,
                equalMarketObservedRate=mean([r['observedRate'] for r in rs if r['observedRate'] is not None]),
                equalMarketPhaseShuffleRate=mean([r['phaseShuffleMeanRate'] for r in rs if r['phaseShuffleMeanRate'] is not None]),
                equalMarketDifference=mean([r['difference'] for r in rs if r['difference'] is not None]),
                marketsAboveNull=sum(r['difference'] is not None and r['difference']>0 for r in rs))
    return summaries,result


def tests():
    m=dict(asset='TEST',regime='UNIT',marketId=1,startMs=0,endMs=300000)
    def e(i,t,side,q,p=.3,route='MAKER'):return dict(leg=str(i),t=t,side=side,q=q,p=p,role=route,quote='BID')
    es=[e(1,1000,'UP',10),e(2,2000,'DOWN',3,route='TAKER'),e(3,3000,'UP',2)]
    b,z=build_batches(m,es);assert b[1]['weakSideAcquisition'] and abs(b[1]['weakGapFraction']-.3)<EPS and b[1]['floorImprovement'] and b[1]['residualAfter']
    assert b[1]['nextRiskAddition'] and b[0]['nextHasTaker'] and b[-1]['nextHasTaker'] is None
    scaled,_=build_batches(m,[dict(x,q=x['q']*5) for x in es]);assert abs(scaled[1]['weakGapFraction']-b[1]['weakGapFraction'])<EPS
    assert abs(scaled[1]['normalizedAbsNet']-b[1]['normalizedAbsNet'])<EPS
    same,_=build_batches(m,[dict(x,t=1000) for x in es]);rev,_=build_batches(m,list(reversed([dict(x,t=1000) for x in es])))
    assert len(same)==1 and same[0]['route']=='MIXED' and same[0]['postNet']==rev[0]['postNet']
    bal,_=build_batches(m,[e(1,1000,'UP',10),e(2,2000,'DOWN',10)]);assert bal[1]['preciseNeutral'] and not bal[1]['residualAfter']
    flip,_=build_batches(m,[e(1,1000,'UP',10),e(2,2000,'DOWN',12)]);assert flip[1]['weakSideCross'] and not flip[1]['preciseNeutral']
    assert motif_count(['MAKER','TAKER','MAKER'])==1 and motif_count(['MAKER','MIXED','MAKER'])==0
    return 8


def main():
    started=time.monotonic();counttests=tests();out=BASE/(PREFIX+'_SCORE.json');rowpath=BASE/(PREFIX+'_EVENT_STATES.jsonl.gz')
    if out.exists() or rowpath.exists():raise FileExistsError('preserve previous results')
    metapath=BASE/META;assert metapath.stat().st_size<400000 and file_sha(metapath)==META_SHA
    original=json.loads(metapath.read_text(encoding='utf-8-sig'));metas={(m['asset'],m['regime'],m['marketId']):m for m in original['markets']}
    assert len(metas)==198
    events=defaultdict(list);sources=[]
    for regime,name,wanted in INPUTS:
        path=BASE/name;assert path.stat().st_size<20*1024**2;h=hashlib.sha256();lines=0
        with path.open('rb') as f:
            for raw in f:
                h.update(raw);e=json.loads(raw);key=(e['asset'],regime,e['marketId']);assert key in metas
                events[key].append(e);lines+=1
        assert h.hexdigest()==wanted;sources.append(dict(path=path.as_posix(),bytes=path.stat().st_size,sha256=wanted,rows=lines))
    assert set(events)==set(metas);permarket={};allrows=[];checks=[]
    for key,m in metas.items():
        bs,z=build_batches(m,events[key]);err=max(abs(z['UP']-m['UP']),abs(z['DOWN']-m['DOWN']))
        assert err<1e-6 and len(events[key])==m['fillLegs']
        permarket[key]=bs;allrows.extend(bs);checks.append(dict(asset=key[0],regime=key[1],marketId=key[2],events=len(bs),endpointError=err))
    summary={};bygroup={}
    for a in ['BTC','ETH']:
        for regime in ['HISTORICAL','RECENT']:
            name=a+'_'+regime;rs=[r for r in allrows if r['asset']==a and r['regime']==regime];mids=[k[2] for k in metas if k[:2]==(a,regime)]
            bygroup[name]=rs
            summary[name]=dict(allPrices=describe(rs,mids),commonPrices=describe([r for r in rs if r['commonPriceDomain']],mids),
                lowPriceEvents=sum(r['lowPriceDomain'] for r in rs),allPriceNext={l:rate_metrics(rs,lambda r:True,l) for l in LABELS})
    comparisons=[]
    for a in ['BTC','ETH']:comparisons.append(overlap_comparison(bygroup[a+'_HISTORICAL'],bygroup[a+'_RECENT'],a+'_OLD_TO_NEW'))
    comparisons.append(overlap_comparison(bygroup['BTC_RECENT'],bygroup['ETH_RECENT'],'SAME_CLOCK_RECENT_BTC_TO_ETH'))
    motifs,motifrows=motif_audit(permarket)
    with gzip.open(rowpath,'wt',encoding='utf-8') as f:
        for r in allrows:f.write(json.dumps(r,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
    result=dict(version=PREFIX,verdict='DESCRIPTIVE_RELATION_AUDIT_NOT_POLICY_OR_CAUSAL_EQUIVALENCE',
       sourceDefinition='actual event-time fill batches; current post-fill features and next observed fill-batch outcomes; not original submissions',
       sources=sources+[dict(path=metapath.as_posix(),bytes=metapath.stat().st_size,sha256=META_SHA)],
       markets=198,eventBatches=len(allrows),tests=counttests,maxEndpointReconstructionError=max(c['endpointError'] for c in checks),
       summary=summary,overlapComparisons=comparisons,motifNull=motifs,perMarketChecks=checks,perMarketMotifs=motifrows,
       eventStates=dict(path=rowpath.as_posix(),bytes=rowpath.stat().st_size,sha256=file_sha(rowpath)),
       modelFits=0,newHFT=0,newTraining=0,policyChanges=0,elapsedSeconds=time.monotonic()-started,
       limitations=['All cohorts already observed; exploratory analysis not fresh promotion.',
        'Size change is observational and endogenous; ratios remove units not omitted-state confounding.',
        'Event-time Target inventory is not receipt-time OUR data; private intended debt, original order quantity, unfilled orders and cancels unavailable.',
        'Common-price means realized trade prices, not matched public-book opportunity; no depth/spread/volatility join.',
        'Within-bin standardization is descriptive; coarse cells and incomplete overlap do not prove invariance.',
        'Next-batch analysis is conditional on another observed fill, not action timing/HOLD/decision policy.',
        'Route shuffles are marginal/phase controls, not executable alternatives, p-values or a strategy simulation.',
        'Mechanical scaling identities, floor improvement geometry and partial-acquisition ratios are not by themselves evidence of intelligent Repair.',
        'No net-cost or outcome-conditioned selection, no OUR economic evaluation, no update to passive12/18 minima or Active authority.'])
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    compact={k:dict(markets=v['allPrices']['markets'],events=v['allPrices']['eventBatches'],
      floorEvents=v['allPrices']['floorImprovingBatches'],retained=v['allPrices']['floorImprovementRetainingResidual'],negative=v['allPrices']['floorImprovementStillNegativeFloor'],
      weak=v['allPrices']['weakSide'],weakCommon=v['commonPrices']['weakSide'],routeFunctions=v['allPrices']['routeFunctions'],next=v['allPrices']['nextObserved']) for k,v in summary.items()}
    print(json.dumps(dict(output=out.as_posix(),bytes=out.stat().st_size,sha256=file_sha(out),markets=198,eventBatches=len(allrows),
      elapsedSeconds=result['elapsedSeconds'],maxEndpointError=result['maxEndpointReconstructionError'],summaries=compact,
      overlap=[{k:v for k,v in c.items() if k!='cells'} for c in comparisons],motifNull=motifs),ensure_ascii=False))


if __name__=='__main__':main()
