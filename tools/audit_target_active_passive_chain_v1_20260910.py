"""Observed passive/Active feedback chains, fixed144market archive.
No native/policy import, HFT/model/SQL. Same-second mixed routes remain ambiguous.
Reuses frozen accounting helpers, checks all final states and prior pure labels.
"""
from pathlib import Path
from collections import Counter,defaultdict
import gzip,json,hashlib,statistics,math,time
from tools.audit_target_maker_scale_active_feedback_v1_20260910 import state,effect,EPS

BASE=Path('data/research/r4_v0/p0_provenance_v1')
SRC=BASE/'target_intermediate_size_performance_v1_20260910/SCORE.json'
SRC_SHA='a045bd1861fa7f61248e513b4576f07098b26e8c8636f647a2d4619a6268ba71'
PRIOR=BASE/'target_maker_scale_active_feedback_v1_20260910/SCORE.json'
PRIOR_SHA='4ff1c89d1b3dd1e3e431f8f4721457ebb986ae50c69e84dffb32d192ac82c7f0'
OUT=BASE/'target_active_passive_chain_v1_20260910'
HORIZONS=(1,3,5)


def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()

def write(p,d):
 with Path(p).open('x',encoding='utf-8') as f:json.dump(d,f,ensure_ascii=False,indent=2,allow_nan=False)

def mean(xs):return statistics.mean(xs) if xs else None

def median(xs):return statistics.median(xs) if xs else None

def sign(x):return 1 if x>EPS else -1 if x<-EPS else 0

def weak(x):return 'DOWN' if x>EPS else 'UP' if x<-EPS else None

def oid(e):return (e['role'],e['side'],e['quote_type'],e['order_hash'])

def compact_state(s):
 return {k:s[k] for k in ['up','down','net','gross','cost','floor','best','relative_imbalance','floor_to_cost',
  'maker_up','maker_down','maker_net','maker_relative_imbalance','maker_pair_margin']}

def category(x):return 'IMPROVED' if x>EPS else 'WORSENED' if x<-EPS else 'FLAT'


def progress_since(post,pre,n):
 if post is None:return 'NO_PREVIOUS_ACTIVE',None
 if not n:return 'NO_PASSIVE_FILLS',0.
 delta=pre['floor']-post['floor'];return category(delta),delta


def window_summary(nodes):
 return dict(events=len(nodes),deltaFloor=sum(n['post']['floor']-n['pre']['floor'] for n in nodes),
 deltaBest=sum(n['post']['best']-n['pre']['best'] for n in nodes),cash=sum(n['post']['cost']-n['pre']['cost'] for n in nodes),
 upQty=sum(n['post']['up']-n['pre']['up'] for n in nodes),downQty=sum(n['post']['down']-n['pre']['down'] for n in nodes),
 lastTime=nodes[-1]['t'],firstTime=nodes[0]['t'])


def process_market(raw):
 m=raw['identity'];ev=raw['events'];res=raw['result'];start=m['window_start_ms'];end=m['window_end_ms']
 assert end-start==300000 and len(ev)==len({e['leg_id'] for e in ev}) and res['sell_proceeds_usdt']==0
 by=defaultdict(list);first={}
 for e in ev:
  assert start<=e['event_ms']<end and e['event_ms']%1000==0 and e['quote_type']=='BID'
  assert e['role'] in ('MAKER','TAKER') and e['shares']>0 and 0<e['price']<1
  by[e['event_ms']].append(e);first[oid(e)]=min(first.get(oid(e),e['event_ms']),e['event_ms'])
 q={r:[0.,0.] for r in ('MAKER','TAKER')};c={r:[0.,0.] for r in q}
 nodes=[];anchors=[];riskrows=[];last_active=None;maker_epoch=[]
 for t in range(start,end,1000):
  pre=state(q,c);es=by.get(t,[]);ms=[e for e in es if e['role']=='MAKER'];ts=[e for e in es if e['role']=='TAKER']
  progress,delta=progress_since(last_active['post'] if last_active else None,pre,len(maker_epoch))
  pure=bool(ts and not ms);eff=effect(pre,ts) if pure else None
  rr=dict(asset=m['asset'],block=m['block'],market_id=m['market_id'],t=t,phase=(t-start)//100000,
    preRelativeImbalance=pre['relative_imbalance'],preFloorToCost=pre['floor_to_cost'],preGross=pre['gross'],
    passiveEpochEvents=len(maker_epoch),passiveProgress=progress,passiveDeltaFloor=delta,
    anyTakerFill=bool(ts),pureService=bool(pure and eff['geometry']=='SERVICE_EFFECT'),
    pureExpansion=bool(pure and pre['gross']>EPS and eff['geometry']=='EXPANSION_EFFECT'))
  riskrows.append(rr)
  for e in es:
   i=0 if e['side']=='UP' else 1;q[e['role']][i]+=e['shares'];c[e['role']][i]+=e['shares']*e['price']
  post=state(q,c)
  if not es:continue
  route='MIXED' if ts and ms else 'TAKER' if ts else 'MAKER'
  n=dict(t=t,pre=compact_state(pre),post=compact_state(post),events=es,route=route,
     effect=effect(pre,es),allFirstObserved=all(first[oid(e)]==t for e in es))
  if pure:
   a=dict(asset=m['asset'],block=m['block'],market_id=m['market_id'],t=t,seed=pre['gross']<=EPS,
      allFirstObservedTaker=n['allFirstObserved'],active=eff,pre=compact_state(pre),post=compact_state(post),
      passiveProgress=progress,passiveDeltaFloor=delta,passiveEpochEvents=len(maker_epoch),
      passiveEpochCash=pre['cost']-last_active['post']['cost'] if last_active else None,
      secondsSincePreviousActive=(t-last_active['t'])/1000 if last_active else None,
      beforeWindows={},afterWindows={},nextEvent='NONE',cleanPostPassiveEvents=0)
   for k in HORIZONS:
    if len(maker_epoch)>=k:a['beforeWindows'][str(k)]=window_summary(maker_epoch[-k:])
   if maker_epoch:
    prev=maker_epoch[-1];a['lastPassive']=dict(t=prev['t'],geometry=prev['effect']['geometry'],side=prev['effect']['side'],
       qty=prev['effect']['qty'],deltaFloor=prev['effect']['deltaFloor'],allFirstObservedOrders=prev['allFirstObserved'])
   else:a['lastPassive']=None
   a['_nodeIndex']=len(nodes);anchors.append(a)
  nodes.append(n)
  if ts:last_active=n;maker_epoch=[]
  else:maker_epoch.append(n)
 for a in anchors:
  i=a.pop('_nodeIndex');a['nextEvent']=nodes[i+1]['route'] if i+1<len(nodes) else 'NONE'
  after=[]
  for n in nodes[i+1:]:
   if n['route']!='MAKER':break
   after.append(n)
  a['cleanPostPassiveEvents']=len(after)
  for k in HORIZONS:
   if len(after)>=k:a['afterWindows'][str(k)]=window_summary(after[:k])
  if after:
   n=after[0];f=n['effect'];ps=a['post'];os=a['pre'];qty=f['qty']
   oldqty=sum(e['shares'] for e in n['events'] if first[oid(e)]<a['t'])
   alt=effect(os,n['events'])
   a['firstPassive']=dict(t=n['t'],elapsedSeconds=(n['t']-a['t'])/1000,geometry=f['geometry'],side=f['side'],qty=qty,
       cash=f['cash'],deltaFloor=f['deltaFloor'],deltaBest=f['deltaBest'],
       knownOldOrderQty=oldqty,knownOldOrderFraction=oldqty/qty,hasKnownOldOrder=oldqty>EPS,
       allFirstObservedAfter=oldqty<=EPS,
       matchesPreWeak=f['side']==weak(os['net']),matchesPostWeak=f['side']==weak(ps['net']),
       netSignChangedByActive=bool(sign(os['net'])*sign(ps['net'])==-1),
       sameExecutedSideAsActive=f['side']!='BOTH' and f['side']==a['active']['side'],
       previousMakerSide=a['lastPassive']['side'] if a['lastPassive'] else None,
       differsFromPreviousMakerSide=bool(a['lastPassive'] and f['side']!='BOTH' and a['lastPassive']['side']!='BOTH' and f['side']!=a['lastPassive']['side']),
       oldStateSameFillGeometry=alt['geometry'],oldStateSameFillDeltaFloor=alt['deltaFloor'],
       accountingGeometryChanges=alt['geometry']!=f['geometry'],
       diagnostic='old-state same-fill is accounting sensitivity only, not a feasible no-Active counterfactual')
  else:a['firstPassive']=None
 final=state(q,c);errs=[abs(final['up']-res['up_position_shares']),abs(final['down']-res['down_position_shares']),abs(final['cost']-res['buy_notional_usdt'])]
 assert max(errs)<1e-6
 # Independent summation ordering guards bookkeeping without reading target outcome as a predictor.
 ef=math.fsum(e['shares']*e['price'] for e in ev)
 assert abs(ef-final['cost'])<1e-6
 return anchors,riskrows,dict(asset=m['asset'],block=m['block'],market_id=m['market_id'],events=len(ev),
    batches=len(nodes),pureActive=len(anchors),mixedActive=sum(n['route']=='MIXED' for n in nodes),maxReconciliationError=max(errs),
    finalCashFsumError=abs(ef-final['cost']))


def rate(rows,key):
 by=defaultdict(list)
 for r in rows:by[r['market_id']].append(float(r[key]))
 return dict(seconds=len(rows),markets=len(by),events=sum(r[key] for r in rows),
  pooled=sum(r[key] for r in rows)/len(rows) if rows else None,equalMarket=mean([mean(v) for v in by.values()]))


def progress_rates(rows):
 return {s:{k:rate([r for r in rows if r['passiveProgress']==s and r['preGross']>EPS],k)
   for k in ['anyTakerFill','pureService','pureExpansion']} for s in ['NO_PASSIVE_FILLS','IMPROVED','WORSENED','FLAT']}


def standardized(rows):
 labels=['anyTakerFill','pureService','pureExpansion'];sets=[]
 for cond in ['IMPROVED','WORSENED']:
  rs=[r for r in rows if r['passiveProgress']==cond and r['preGross']>EPS and r['preFloorToCost'] is not None]
  cnt=Counter(r['market_id'] for r in rs);cells={}
  for r in rs:
   x=r['preRelativeImbalance'];f=r['preFloorToCost'];n=r['passiveEpochEvents']
   key=(r['phase'],0 if x<.25 else 1 if x<.5 else 2,0 if f>=0 else 1 if f>=-.05 else 2 if f>=-.2 else 3,0 if n==1 else 1 if n<=3 else 2)
   w=1/len(cnt)/cnt[r['market_id']];z=cells.setdefault(key,dict(w=0.,markets=set(),sums=Counter(),n=0))
   z['w']+=w;z['markets'].add(r['market_id']);z['n']+=1
   for k in labels:z['sums'][k]+=w*r[k]
  sets.append(cells)
 a,b=sets;ks=[k for k in a if k in b and len(a[k]['markets'])>=3 and len(b[k]['markets'])>=3]
 weights={k:min(a[k]['w'],b[k]['w']) for k in ks};mass=sum(weights.values());rates={}
 for l in labels:
  x=sum(weights[k]*a[k]['sums'][l]/a[k]['w'] for k in ks)/mass if mass else None
  y=sum(weights[k]*b[k]['sums'][l]/b[k]['w'] for k in ks)/mass if mass else None
  rates[l]=dict(afterImprovement=x,afterDeterioration=y,difference=y-x if mass else None)
 return dict(commonCells=len(ks),commonMass=mass,improvementCoverage=sum(a[k]['w'] for k in ks),
    deteriorationCoverage=sum(b[k]['w'] for k in ks),rates=rates,
    limitation='observed fill rates, matched coarse current state and passive event count; not causal hazard or a known HOLD control')


def summarize(anchors,market_count):
 aa=[a for a in anchors if not a['seed']];nextcounts=Counter(a['nextEvent'] for a in aa)
 groups={}
 for g in ['SERVICE_EFFECT','EXPANSION_EFFECT','OTHER_GEOMETRY']:
  rs=[a for a in aa if a['active']['geometry']==g];withpass=[a for a in rs if a['passiveEpochEvents']>0 and a['passiveProgress']!='NO_PREVIOUS_ACTIVE']
  aft=[a for a in rs if a['firstPassive'] is not None];ft=[a['firstPassive'] for a in aft];prev=[a for a in rs if a['lastPassive']]
  flips=[a for a in aft if a['firstPassive']['netSignChangedByActive'] and a['firstPassive']['side']!='BOTH']
  groups[g]=dict(anchors=len(rs),markets=len({a['market_id'] for a in rs}),passiveProgress=dict(Counter(a['passiveProgress'] for a in rs)),
    afterKnownActiveAndMaker=len(withpass),passivePreviouslyImproved=sum(a['passiveProgress']=='IMPROVED' for a in withpass),
    previousPassiveObserved=len(prev),previousPassiveGeometry=dict(Counter(a['lastPassive']['geometry'] for a in prev)),
    previousPassiveSameSide=sum(a['active']['side']!='BOTH' and a['active']['side']==a['lastPassive']['side'] for a in prev),
    priorSameSideService=sum(a['lastPassive']['geometry']=='SERVICE_EFFECT' and a['active']['side']!='BOTH' and a['active']['side']==a['lastPassive']['side'] for a in prev),
    nextEvent=dict(Counter(a['nextEvent'] for a in rs)),directPassiveContinuation=len(aft),
    nextPassiveGeometry=dict(Counter(a['geometry'] for a in ft)),
    nextPassiveHasOldOrders=sum(a['hasKnownOldOrder'] for a in ft),
    nextPassiveAllFirstObservedAfter=sum(a['allFirstObservedAfter'] for a in ft),
    nextPassiveOldQtyFraction=math.fsum(a['knownOldOrderQty'] for a in ft)/math.fsum(a['qty'] for a in ft) if ft else None,
    nextPassiveSameActiveSide=sum(a['sameExecutedSideAsActive'] for a in ft),
    nextPassiveSingleSide=sum(a['side']!='BOTH' for a in ft),
    nextPassiveWeakPost=sum(a['matchesPostWeak'] for a in ft),
    nextPassiveSwitchesSide=sum(a['differsFromPreviousMakerSide'] for a in ft),
    nextPassiveGeometryChangesOnlyFromState=sum(a['accountingGeometryChanges'] for a in ft),
    activeFlipsWithSingleSideMaker=len(flips),afterFlipFollowsNewWeak=sum(a['firstPassive']['matchesPostWeak'] for a in flips),
    afterFlipFollowsOldWeak=sum(a['firstPassive']['matchesPreWeak'] for a in flips),
    medianSecondsToFirstPassive=median([a['elapsedSeconds'] for a in ft]),horizons={})
  for k in HORIZONS:
   follow=[a for a in rs if str(k) in a['afterWindows']];paired=[a for a in follow if str(k) in a['beforeWindows']]
   groups[g]['horizons'][str(k)]=dict(eligible=len(follow),censored=len(rs)-len(follow),
       markets=len({a['market_id'] for a in follow}),
       positivePassiveDeltaFloor=sum(a['afterWindows'][str(k)]['deltaFloor']>EPS for a in follow),
       negativePassiveDeltaFloor=sum(a['afterWindows'][str(k)]['deltaFloor']<-EPS for a in follow),
       medianPostPassiveDeltaFloorToAnchorCost=median([a['afterWindows'][str(k)]['deltaFloor']/a['pre']['cost'] for a in follow if a['pre']['cost']>EPS]),
       symmetricPrePostEligible=len(paired),
       symmetricBeforeFloorPositive=sum(a['beforeWindows'][str(k)]['deltaFloor']>EPS for a in paired),
       symmetricAfterFloorPositive=sum(a['afterWindows'][str(k)]['deltaFloor']>EPS for a in paired),
       caveat='next1/3/5Maker batches only before anotherTaker, otherwise censored; not a future-success-selected strategy')
 return dict(markets=market_count,pureIncludingSeeds=len(anchors),nonseedAnchors=len(aa),allFirstObservedAnchors=sum(a['allFirstObservedTaker'] for a in aa),
       nextCompetingEvent=dict(nextcounts),geometry=groups)


def tests():
 post=dict(floor=-5);pre=dict(floor=-3)
 assert progress_since(post,pre,1)==('IMPROVED',2) and progress_since(post,pre,0)==('NO_PASSIVE_FILLS',0.)
 assert progress_since(None,pre,1)==('NO_PREVIOUS_ACTIVE',None)
 # A saved Maker buy can change economic label purely because Active reversed inventory.
 q={'MAKER':[10.,0.],'TAKER':[0.,0.]};c={'MAKER':[3.,0.],'TAKER':[0.,0.]};pre=state(q,c)
 q['TAKER'][1]=12;c['TAKER'][1]=2.4;post=state(q,c)
 es=[dict(side='UP',shares=1.,price=.3)]
 assert effect(pre,es)['geometry']=='EXPANSION_EFFECT' and effect(post,es)['geometry']=='SERVICE_EFFECT'
 assert weak(pre['net'])=='DOWN' and weak(post['net'])=='UP'
 # Balanced bookkeeping with different route totals is still one portfolio.
 assert abs(post['floor']-(10-5.4))<EPS
 return 6


def main():
 begin=time.monotonic();ut=tests();assert sha(SRC)==SRC_SHA and sha(PRIOR)==PRIOR_SHA
 source=json.loads(SRC.read_text(encoding='utf-8-sig'));old=json.loads(PRIOR.read_text(encoding='utf-8-sig'))
 OUT.mkdir(exist_ok=True);op=OUT/'SCORE.json'
 if op.exists():raise FileExistsError('immutable outputs')
 anchors=[];risk=[];checks=[]
 for d in source['inputs']:
  p=Path(d['source']['path']);assert sha(p)==d['source']['sha256'];expanded=0
  with gzip.open(p,'rt',encoding='utf-8') as f:
   for line in f:
    expanded+=len(line.encode());assert expanded<32*1024**2
    a,r,c=process_market(json.loads(line));anchors+=a;risk+=r;checks.append(c)
 assert len(checks)==144 and len(risk)==43200 and len(anchors)==old['pureActiveBatches']
 assert sum(r['events'] for r in checks)==38721 and sum(r['mixedActive'] for r in checks)==old['mixedActiveMakerBatches']
 groups={};conditionals={};aggregate={};firstonly={}
 for asset in ['BTC','ETH']:
  asa=[a for a in anchors if a['asset']==asset];aggregate[asset]=summarize(asa,72)
  firstonly[asset]=summarize([a for a in asa if a['allFirstObservedTaker']],72)
  for block in ['W1','W2','W3','W4','W5','W6']:
   match=lambda r:r['asset']==asset and r['block']==block
   a=[x for x in anchors if match(x)];r=[x for x in risk if match(x)];name=asset+'_'+block;groups[name]=summarize(a,12)
   conditionals[name]=dict(raw=progress_rates(r),standardized=standardized(r))
   for g,label in [('SERVICE_EFFECT','pureServiceBatches'),('EXPANSION_EFFECT','pureExpansionBatches')]:
    assert sum(x['active']['geometry']==g for x in a)==old['groups'][name][label]
 ap=OUT/'ANCHOR_CHAINS.jsonl.gz';rp=OUT/'PRE_ACTIVE_RISK_SECONDS.jsonl.gz'
 for p,rs in [(ap,anchors),(rp,risk)]:
  if p.exists():raise FileExistsError(str(p))
  with gzip.open(p,'wt',encoding='utf-8',compresslevel=3) as f:
   for r in rs:f.write(json.dumps(r,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
 d=dict(version='TARGET_ACTIVE_PASSIVE_CHAIN_V1',verdict='OBSERVED_FEEDBACK_CHAIN_ONLY_PRIVATE_DECISION_CAUSALITY_UNKNOWN',
   sourceSha256=SRC_SHA,priorSha256=PRIOR_SHA,markets=144,fillLegs=38721,exposureSeconds=43200,
   pureActiveAnchors=len(anchors),mixedActiveSeconds=sum(x['mixedActive'] for x in checks),
   groups=groups,aggregate=aggregate,firstObservedTakerOnly=firstonly,passiveProgressConditionals=conditionals,
   sources=[x['source'] for x in source['inputs']],checks=checks,tests=ut,maxReconciliationError=max(x['maxReconciliationError'] for x in checks),
   artifacts=[dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)) for p in [ap,rp]],
   newHFT=0,newTraining=0,modelFits=0,policyChanges=0,elapsed=time.monotonic()-begin,
   limitations=['effect-based labels cannot identify why Target acted; no-Taker is not proven HOLD',
    'pure seconds exclude many concurrent Maker/Taker seconds; cohort is selected past description, not independent transfer',
    'post passive run censors at any nextTaker; all missing competing events retained',
    'same-fill old-state comparison is accounting only, never a feasible policy counterfactual',
    'known old order means previously filled hash; first observed after may already have been resting before intervention',
    'no placement/cancel/order-pending/public-quote/queue/fees/state-grants introduced or privately inferred',
    'conditional exposure tables remain observational with possibly sparse overlap; no formal causality or profitability claim'])
 write(op,d)
 print(json.dumps(dict(output=str(op),bytes=op.stat().st_size,sha256=sha(op),elapsed=d['elapsed'],tests=ut,
    markets=144,pure=len(anchors),mixed=d['mixedActiveSeconds'],maxError=d['maxReconciliationError'],aggregate=aggregate,
    perBlock=[dict(name=k,nonseed=v['nonseedAnchors'],service=v['geometry']['SERVICE_EFFECT']['anchors'],
      priorImproved=v['geometry']['SERVICE_EFFECT']['passivePreviouslyImproved'],priorKnown=v['geometry']['SERVICE_EFFECT']['afterKnownActiveAndMaker'],
      nextServiceAfterExpansion=v['geometry']['EXPANSION_EFFECT']['nextPassiveGeometry'],
      conditional=conditionals[k]['standardized']) for k,v in groups.items()]),ensure_ascii=False))


if __name__=='__main__':main()
