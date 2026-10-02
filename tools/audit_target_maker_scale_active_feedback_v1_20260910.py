"""Fixed144-market observation of Maker scale and Active portfolio feedback.
Uses prior archived fills only. No native/policy/model imports, SQL, or HFT.
Geometry labels are executed effects, not private grants or submit decisions.
"""
from pathlib import Path
from collections import Counter,defaultdict
import gzip,json,hashlib,math,statistics,time

BASE=Path('data/research/r4_v0/p0_provenance_v1')
SOURCE=BASE/'target_intermediate_size_performance_v1_20260910/SCORE.json'
SOURCE_SHA='a045bd1861fa7f61248e513b4576f07098b26e8c8636f647a2d4619a6268ba71'
OUT=BASE/'target_maker_scale_active_feedback_v1_20260910'
EPS=1e-8


def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()

def mean(x):return statistics.mean(x) if x else None

def median(x):return statistics.median(x) if x else None

def write(p,d):
 with Path(p).open('x',encoding='utf-8') as f:json.dump(d,f,ensure_ascii=False,indent=2,allow_nan=False)

def sgn(x):return 1 if x>EPS else -1 if x<-EPS else 0


def state(q,c):
 mu,md=q['MAKER'];tu,td=q['TAKER'];up,down=mu+tu,md+td
 mc=sum(c['MAKER']);cost=mc+sum(c['TAKER']);mg=mu+md;g=up+down
 pair=min(mu,md);unit=1-c['MAKER'][0]/mu-c['MAKER'][1]/md if min(mu,md)>EPS else None
 pm=pair*unit if unit is not None else 0.
 residual=(mu-md)*c['MAKER'][0]/mu if mu>md and mu>EPS else (md-mu)*c['MAKER'][1]/md if md>EPS else 0.
 assert abs((min(mu,md)-mc)-(pm-residual))<1e-6
 return dict(maker_up=mu,maker_down=md,maker_net=mu-md,maker_gross=mg,maker_cost=mc,
    maker_relative_imbalance=abs(mu-md)/mg if mg>EPS else None,
    maker_paired_qty=pair,maker_pair_unit_margin=unit,maker_pair_margin=pm,
    maker_pair_margin_to_cost=pm/mc if mc>EPS else None,maker_only_floor=min(mu,md)-mc,
    up=up,down=down,net=up-down,gross=g,cost=cost,
    relative_imbalance=abs(up-down)/g if g>EPS else None,
    floor=min(up,down)-cost,best=max(up,down)-cost,
    floor_to_cost=(min(up,down)-cost)/cost if cost>EPS else None)


def effect(st,es):
 du=math.fsum(e['shares'] for e in es if e['side']=='UP');dd=math.fsum(e['shares'] for e in es if e['side']=='DOWN')
 pay=math.fsum(e['shares']*e['price'] for e in es)
 f=min(st['up']+du,st['down']+dd)-st['cost']-pay-st['floor']
 b=max(st['up']+du,st['down']+dd)-st['cost']-pay-st['best']
 label='SERVICE_EFFECT' if f>EPS else 'EXPANSION_EFFECT' if f<-EPS and b>EPS else 'OTHER_GEOMETRY'
 side='UP' if du>EPS and dd<=EPS else 'DOWN' if dd>EPS and du<=EPS else 'BOTH'
 weak='DOWN' if st['net']>EPS else 'UP' if st['net']<-EPS else None
 makerweak='DOWN' if st['maker_net']>EPS else 'UP' if st['maker_net']<-EPS else None
 qty=du+dd;gapfrac=qty/abs(st['net']) if side==weak and abs(st['net'])>EPS else None
 return dict(geometry=label,qty=qty,cash=pay,price=pay/qty if qty>EPS else None,side=side,
   deltaFloor=f,deltaBest=b,weak_total=weak,weak_maker=makerweak,
   total_weak_buy=side==weak,maker_weak_buy=side==makerweak,
   maker_total_weak_disagree=bool(weak and makerweak and weak!=makerweak),
   qty_to_total_gap=gapfrac,qty_to_maker_gap=qty/abs(st['maker_net']) if side==makerweak else None,
   crossed_total_net=bool(gapfrac is not None and gapfrac>1+EPS),
   precise_neutral=bool(gapfrac is not None and abs(qty-abs(st['net']))<=EPS))


def tests():
 q={'MAKER':[10.,0.],'TAKER':[0.,0.]};c={'MAKER':[4.,0.],'TAKER':[0.,0.]}
 st=state(q,c);x=effect(st,[dict(side='DOWN',shares=2.,price=.3)]);assert x['geometry']=='SERVICE_EFFECT' and abs(x['qty_to_total_gap']-.2)<EPS
 y=effect(st,[dict(side='UP',shares=2.,price=.3)]);assert y['geometry']=='EXPANSION_EFFECT'
 # Identical share balance is not identical economics.
 a=state({'MAKER':[10.,10.],'TAKER':[0.,0.]},{'MAKER':[4.,4.],'TAKER':[0.,0.]})
 b=state({'MAKER':[10.,10.],'TAKER':[0.,0.]},{'MAKER':[6.,6.],'TAKER':[0.,0.]})
 assert a['maker_relative_imbalance']==b['maker_relative_imbalance']==0 and a['floor']==2 and b['floor']==-2
 # Prior active fills can reverse the weakness relative to Maker-only inventory.
 d=state({'MAKER':[20.,10.],'TAKER':[0.,15.]},{'MAKER':[8.,4.],'TAKER':[0.,6.]})
 x=effect(d,[dict(side='UP',shares=2.,price=.4)]);assert x['maker_total_weak_disagree'] and x['total_weak_buy'] and not x['maker_weak_buy'] and x['geometry']=='SERVICE_EFFECT'
 sc=state({k:[v*7 for v in vs] for k,vs in q.items()},{k:[v*7 for v in vs] for k,vs in c.items()})
 assert sc['relative_imbalance']==st['relative_imbalance']
 return 5


def analyze(market):
 m=market['identity'];ev=market['events'];res=market['result'];start=m['window_start_ms'];end=m['window_end_ms']
 assert end-start==300000 and len(ev)==len({e['leg_id'] for e in ev}) and res['sell_proceeds_usdt']==0
 batches=defaultdict(list);orders=defaultdict(float);maker_ids=set()
 for e in ev:
  assert start<=e['event_ms']<end and e['event_ms']%1000==0 and e['quote_type']=='BID'
  assert e['role'] in ('MAKER','TAKER') and e['side'] in ('UP','DOWN') and 0<e['price']<1 and e['shares']>0
  batches[e['event_ms']].append(e);k=(e['role'],e['side'],e['order_hash']);orders[k]+=e['shares']
  if e['role']=='MAKER':maker_ids.add(k)
 q={k:[0.,0.] for k in ('MAKER','TAKER')};c={k:[0.,0.] for k in q}
 frames=[];pure=[];mixed=[];seen=set();last_pair=0.;last_taker_seen=False
 for t in range(start,end,1000):
  st=state(q,c);es=batches.get(t,[]);ms=[e for e in es if e['role']=='MAKER'];ts=[e for e in es if e['role']=='TAKER']
  frame=dict(asset=m['asset'],block=m['block'],market_id=m['market_id'],t=t,phase=(t-start)//100000,
    feature_maker_pair_gain_since_last_taker=st['maker_pair_margin']-last_pair,
    feature_had_prior_taker=last_taker_seen,
    **{'feature_'+k:v for k,v in st.items()},
    label_taker_fill=bool(ts),label_pure_taker=bool(ts and not ms),
    label_pure_service=False,label_pure_expansion=False,label_taker_maker_same_second=bool(ts and ms))
  if ts:
   e=effect(st,ts);kset={('TAKER',x['side'],x['order_hash']) for x in ts};new_ids=kset-seen
   rec=dict(asset=m['asset'],block=m['block'],market_id=m['market_id'],t=t,pre=st,
      makerPairGainSinceLastTaker=st['maker_pair_margin']-last_pair,hadPriorTaker=last_taker_seen,
      firstObservedTakerOrders=len(new_ids),existingObservedTakerOrders=len(kset&seen),
      seed=st['gross']<=EPS,**e)
   if ms:
    qs={k:list(v) for k,v in q.items()};cs={k:list(v) for k,v in c.items()}
    for x in ms:
     i=0 if x['side']=='UP' else 1;qs['MAKER'][i]+=x['shares'];cs['MAKER'][i]+=x['shares']*x['price']
    alt=effect(state(qs,cs),ts)
    rec.update(makerFirstGeometry=alt['geometry'],geometryAgreesUnderTwoOrderingBounds=e['geometry']==alt['geometry'])
    mixed.append(rec)
   else:
    pure.append(rec);frame['label_pure_service']=e['geometry']=='SERVICE_EFFECT';frame['label_pure_expansion']=e['geometry']=='EXPANSION_EFFECT'
  frames.append(frame)
  for x in es:
   i=0 if x['side']=='UP' else 1;q[x['role']][i]+=x['shares'];c[x['role']][i]+=x['shares']*x['price'];seen.add((x['role'],x['side'],x['order_hash']))
  if ts:last_pair=state(q,c)['maker_pair_margin'];last_taker_seen=True
 final=state(q,c);errs=[abs(final['up']-res['up_position_shares']),abs(final['down']-res['down_position_shares']),abs(final['cost']-res['buy_notional_usdt'])]
 assert max(errs)<1e-6 and len(orders)==res['parent_count']
 serv=[r for r in pure if r['geometry']=='SERVICE_EFFECT'];adds=[r for r in pure if r['geometry']=='EXPANSION_EFFECT'];weak=[r for r in pure if r['total_weak_buy']]
 makerqty=[v for k,v in orders.items() if k[0]=='MAKER'];takerqty=[v for k,v in orders.items() if k[0]=='TAKER']
 out=dict(asset=m['asset'],block=m['block'],market_id=m['market_id'],start_ms=start,
   eventLegs=len(ev),makerOrders=len(makerqty),takerOrders=len(takerqty),
   makerMeanQty=mean(makerqty),makerMedianQty=median(makerqty),takerMeanQty=mean(takerqty),takerMedianQty=median(takerqty),
   activeSeconds=sum(f['label_taker_fill'] for f in frames),pureActiveSeconds=len(pure),mixedSeconds=len(mixed),
   pureServiceSeconds=len(serv),pureExpansionSeconds=len(adds),pureSeedSeconds=sum(r['seed'] for r in pure),
   serviceFirstObservedOrders=sum(r['firstObservedTakerOrders'] for r in serv),expansionFirstObservedOrders=sum(r['firstObservedTakerOrders'] for r in adds),
   expansionPer100MakerOrders=100*len(adds)/len(makerqty) if makerqty else None,
   medianServiceQty=median([r['qty'] for r in serv]),medianServicePrice=median([r['price'] for r in serv]),
   medianWeakQtyToTotalGap=median([r['qty_to_total_gap'] for r in weak]),
   terminal=final,maxReconstructionError=max(errs))
 return out,frames,pure,mixed


def rate(rs,label):
 by=defaultdict(list)
 for r in rs:by[r['market_id']].append(float(r[label]))
 return dict(seconds=len(rs),markets=len(by),observed=sum(r[label] for r in rs),
    pooledRate=sum(r[label] for r in rs)/len(rs) if rs else None,equalMarketRate=mean([mean(v) for v in by.values()]))


def conditional(left,right,kind):
 def cell(r):
  if kind=='MAKER_BALANCE':z=r['feature_maker_relative_imbalance'];return (r['phase'],0 if z<.25 else 1 if z<.5 else 2)
  z=r['feature_relative_imbalance'];f=r['feature_floor_to_cost'];b=0 if f>=0 else 1 if f>=-.05 else 2 if f>=-.2 else 3
  return (r['phase'],0 if z<.25 else 1 if z<.5 else 2,b)
 sets=[];labels=['label_taker_fill','label_pure_service','label_pure_expansion']
 for rows in (left,right):
  rows=[r for r in rows if r['feature_maker_gross']>EPS and r['feature_cost']>EPS]
  ns=Counter(r['market_id'] for r in rows);cells={}
  for r in rows:
   k=cell(r);w=1/ns[r['market_id']]/len(ns);x=cells.setdefault(k,dict(w=0.,markets=set(),sums=Counter()))
   x['w']+=w;x['markets'].add(r['market_id'])
   for l in labels:x['sums'][l]+=w*r[l]
  sets.append(cells)
 a,b=sets;ks=[k for k in a if k in b and len(a[k]['markets'])>=3 and len(b[k]['markets'])>=3]
 ws={k:min(a[k]['w'],b[k]['w']) for k in ks};mass=sum(ws.values())
 values={}
 for label in labels:
  x=sum(ws[k]*a[k]['sums'][label]/a[k]['w'] for k in ks)/mass if mass else None
  y=sum(ws[k]*b[k]['sums'][label]/b[k]['w'] for k in ks)/mass if mass else None
  values[label]=dict(left=x,right=y,diff=y-x if x is not None else None,
   absoluteCellDifference=sum(ws[k]*abs(a[k]['sums'][label]/a[k]['w']-b[k]['sums'][label]/b[k]['w']) for k in ks)/mass if mass else None)
 return dict(conditioning=kind,commonCells=len(ks),commonMass=mass,leftCoverage=sum(a[k]['w'] for k in ks),rightCoverage=sum(b[k]['w'] for k in ks),rates=values)


def summarize(markets,frames,pure,mixed):
 sv=[r for r in pure if r['geometry']=='SERVICE_EFFECT'];ad=[r for r in pure if r['geometry']=='EXPANSION_EFFECT'];wg=[r for r in pure if r['qty_to_total_gap'] is not None]
 disagree=[r for r in pure if r['maker_total_weak_disagree'] and r['side']!='BOTH']
 invested=[r for r in frames if r['feature_gross']>EPS];paired=[r for r in invested if r['feature_maker_paired_qty']>EPS]
 pos=[r for r in paired if r['feature_maker_pair_margin']>EPS];nonpos=[r for r in paired if r['feature_maker_pair_margin']<=EPS]
 md=lambda key:median([r[key] for r in markets if r[key] is not None])
 vals=dict(markets=len(markets),makerMedianPerMarketOrderQty=md('makerMedianQty'),takerMedianPerMarketOrderQty=md('takerMedianQty'),
  pureServiceBatches=len(sv),pureExpansionBatches=len(ad),mixedMakerTakerBatches=len(mixed),
  meanPureExpansionPerMarket=len(ad)/len(markets),medianPureExpansionPerMarket=md('pureExpansionSeconds'),
  meanExpansionFirstObservedOrdersPerMarket=sum(r['expansionFirstObservedOrders'] for r in markets)/len(markets),
  expansionPer100MakerOrders=100*len(ad)/sum(r['makerOrders'] for r in markets),
  pureServiceMedianQty=median([r['qty'] for r in sv]),medianOfMarketServiceQtyMedians=md('medianServiceQty'),
  pureServiceMedianPrice=median([r['price'] for r in sv]),weakMedianQtyToTotalGap=median([r['qty_to_total_gap'] for r in wg]),
  weakMedianOfMarketGapMedians=md('medianWeakQtyToTotalGap'),weakCount=len(wg),weakExactNeutral=sum(r['precise_neutral'] for r in wg),
  singleSideServiceMakerTotalDisagree=sum(r['maker_total_weak_disagree'] for r in sv if r['side']!='BOTH'),
  singleSideServiceCount=sum(r['side']!='BOTH' for r in sv),
  allSingleSideActiveDisagree=len(disagree),disagreeBuysTotalWeak=sum(r['total_weak_buy'] for r in disagree),disagreeBuysMakerWeak=sum(r['maker_weak_buy'] for r in disagree),
  expansionPositiveMakerPair=sum(r['pre']['maker_pair_margin']>EPS for r in ad),
  expansionPositiveMakerPairButTotalFloorNegative=sum(r['pre']['maker_pair_margin']>EPS and r['pre']['floor']<-EPS for r in ad),
  expansionWithPositivePairGainSinceLastTaker=sum(r['makerPairGainSinceLastTaker']>EPS for r in ad),
  expansionWithoutPriorTaker=sum(not r['hadPriorTaker'] for r in ad),
  positivePairExpansionExposure=rate(pos,'label_pure_expansion'),nonpositivePairExpansionExposure=rate(nonpos,'label_pure_expansion'),
  pairStatePositiveSeconds=len(pos),pairStateNonpositiveSeconds=len(nonpos),
  terminalMakerPairedQtyMedian=median([r['terminal']['maker_paired_qty'] for r in markets]),
  terminalMakerPairMarginMedian=median([r['terminal']['maker_pair_margin'] for r in markets]),
  terminalMakerPairMarginToCostMedian=median([r['terminal']['maker_pair_margin_to_cost'] for r in markets if r['terminal']['maker_pair_margin_to_cost'] is not None]),
  terminalWholeFloorToCostMedian=median([r['terminal']['floor_to_cost'] for r in markets]),
  mixedGeometrySameUnderMakerFirst=sum(r['geometryAgreesUnderTwoOrderingBounds'] for r in mixed))
 vals['makerBalanceObservedResponse']=[];vals['totalBalanceObservedResponse']=[]
 for field,name in [('feature_maker_relative_imbalance','makerBalanceObservedResponse'),('feature_relative_imbalance','totalBalanceObservedResponse')]:
  for lo,hi in [(0,.25),(.25,.5),(.5,1.0000001)]:
   rs=[r for r in invested if r[field] is not None and lo<=r[field]<hi]
   vals[name].append(dict(bin=[lo,hi],taker=rate(rs,'label_taker_fill'),pureService=rate(rs,'label_pure_service'),pureExpansion=rate(rs,'label_pure_expansion')))
 return vals


def main():
 started=time.monotonic();ut=tests();assert sha(SOURCE)==SOURCE_SHA;source=json.loads(SOURCE.read_text(encoding='utf-8-sig'))
 OUT.mkdir(exist_ok=True)
 if (OUT/'SCORE.json').exists():raise FileExistsError('immutable output exists')
 markets=[];frames=[];pure=[];mixed=[];inputs=[]
 for d in source['inputs']:
  p=Path(d['source']['path']);assert sha(p)==d['source']['sha256'];expanded=0
  with gzip.open(p,'rt',encoding='utf-8') as f:
   for line in f:
    expanded+=len(line.encode());assert expanded<32*1024**2
    m=json.loads(line);a,b,c,e=analyze(m);markets.append(a);frames+=b;pure+=c;mixed+=e
  inputs.append(d['source'])
 assert len(markets)==len({(r['asset'],r['market_id']) for r in markets})==144 and len(frames)==43200
 assert sum(r['eventLegs'] for r in markets)==38721
 old={(r['asset'],r['block']):r for r in source['groups']};groups={}
 for asset in ('BTC','ETH'):
  for block in ['W1','W2','W3','W4','W5','W6']:
   match=lambda r:r['asset']==asset and r['block']==block
   ms=[r for r in markets if match(r)];fs=[r for r in frames if match(r)];ps=[r for r in pure if match(r)];xs=[r for r in mixed if match(r)]
   g=summarize(ms,fs,ps,xs);assert g['pureServiceBatches']==old[asset,block]['taker_floor_events']
   assert g['pureExpansionBatches']==old[asset,block]['taker_risk_events'];groups[asset+'_'+block]=g
 comparisons=[]
 for asset in ('BTC','ETH'):
  for l,r in [('W1',x) for x in ['W2','W3','W4','W5','W6']]+[('W3','W4')]:
   left=[f for f in frames if f['asset']==asset and f['block']==l];right=[f for f in frames if f['asset']==asset and f['block']==r]
   comparisons.append(dict(asset=asset,left=l,right=r,makerOnly=conditional(left,right,'MAKER_BALANCE'),
     fullState=conditional(left,right,'TOTAL_BALANCE_COST')))
 framepath=OUT/'PRESTATE_SECONDS.jsonl.gz';purepath=OUT/'PURE_ACTIVE_EVENTS.jsonl.gz';marketpath=OUT/'MARKET_SUMMARIES.json'
 for path,rs in [(framepath,frames),(purepath,pure)]:
  if path.exists():raise FileExistsError(str(path))
  with gzip.open(path,'wt',encoding='utf-8',compresslevel=3) as f:
   for r in rs:f.write(json.dumps(r,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
 write(marketpath,markets)
 d=dict(version='TARGET_MAKER_SCALE_ACTIVE_FEEDBACK_V1',status='EXECUTED_EFFECT_AND_PREFIX_RELATION_AUDIT_NOT_ACTIVE_LAW_IDENTIFIED',
 sourceScoreSha256=SOURCE_SHA,inputs=inputs,markets=144,seconds=43200,fillLegs=38721,pureActiveBatches=len(pure),mixedActiveMakerBatches=len(mixed),
 groups=groups,conditionalComparisons=comparisons,maxReconstructionError=max(r['maxReconstructionError'] for r in markets),tests=ut,
 sourceSchema='strict earlier event_ms Target prestate;1s observed fill response, not private placement/decision or OUR receipt time',
 artifacts=[dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)) for p in (framepath,purepath,marketpath)],
 noFutureOutcomeFeatures=True,modelFits=0,newHFT=0,newTraining=0,policyChanges=0,secondsElapsed=time.monotonic()-started,
 limits=['fixed six calendar blocks have different market conditions and known sizes; already observed development evidence',
 'pure Taker geometry is not hidden Repair/ADD/desired side; mixed Maker/Taker excluded from primary geometric attribution',
 'first observed filled order appearance is not first submission; original requested size and unfilled orders unknown',
 'paired Maker margin uses average-cost assignment; not FIFO/private grant or a causal standalone Maker return',
 'positive matched margin is not spendable budget if residual acquisition costs or other taker claims consume protection',
 'all-second rates concern observed executions, not latent intended actions; no public book/queue/fees/forecast/pending joins',
 'relative state conditioning cannot certify identical policies and has sparse common cells, endogenous selection, no formal equivalence test',
 'size changes can change absolute Active quantities even with fixed state-dependent rules; no proof of only one code/config changed',
 'no counterfactual swap of Maker size, no strategy-performance claim, no new gate or passive minima alteration'])
 write(OUT/'SCORE.json',d)
 brief={k:{x:v[x] for x in ('markets','takerMedianPerMarketOrderQty','pureServiceBatches','pureExpansionBatches','meanExpansionFirstObservedOrdersPerMarket',
 'expansionPer100MakerOrders','medianOfMarketServiceQtyMedians','pureServiceMedianPrice','weakMedianOfMarketGapMedians','weakCount','weakExactNeutral',
 'singleSideServiceMakerTotalDisagree','singleSideServiceCount','allSingleSideActiveDisagree','disagreeBuysTotalWeak','disagreeBuysMakerWeak',
 'expansionPositiveMakerPair','expansionPositiveMakerPairButTotalFloorNegative','expansionWithPositivePairGainSinceLastTaker',
 'terminalMakerPairedQtyMedian','terminalMakerPairMarginMedian','terminalMakerPairMarginToCostMedian','positivePairExpansionExposure','nonpositivePairExpansionExposure')}
 for k,v in groups.items()}
 print(json.dumps(dict(output=str(OUT/'SCORE.json'),bytes=(OUT/'SCORE.json').stat().st_size,sha256=sha(OUT/'SCORE.json'),
  elapsed=d['secondsElapsed'],pure=len(pure),mixed=len(mixed),reconstructionError=d['maxReconstructionError'],groups=brief),ensure_ascii=False))


if __name__=='__main__':main()
