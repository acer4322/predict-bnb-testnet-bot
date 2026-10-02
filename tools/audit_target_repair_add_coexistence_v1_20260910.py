"""Observed coexistence of payoff effects and interleaved execution carriers.
Not Target private objective reconstruction. Pure stdlib, fixed144market archive.
No HFT/model/policy imports, no database queries, no runtime changes.
"""
from pathlib import Path
from collections import Counter,defaultdict
import bisect,gzip,hashlib,json,math,statistics,time

BASE=Path('data/research/r4_v0/p0_provenance_v1')
SRC=BASE/'target_intermediate_size_performance_v1_20260910/SCORE.json'
SRC_SHA='a045bd1861fa7f61248e513b4576f07098b26e8c8636f647a2d4619a6268ba71'
OUT=BASE/'target_repair_add_coexistence_v1_20260910'
EPS=1e-8
PATTERNS=['TAKER_BOTH_EFFECTS','MAKER_BOTH_EFFECTS','PASSIVE_SERVICE_ACTIVE_EXPANSION',
          'ACTIVE_SERVICE_PASSIVE_EXPANSION','ALL_FOUR_CELLS']


def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()

def write(p,d):
 with Path(p).open('x',encoding='utf-8') as f:json.dump(d,f,ensure_ascii=False,indent=2,allow_nan=False)

def oid(e):return (e['role'],e['side'],e['quote_type'],e['order_hash'])

def sign(x):return 1 if x>EPS else -1 if x<-EPS else 0


def batch_effect(up,down,cost,events):
 totals=defaultdict(lambda:dict(qty=0.,cash=0.,orders=set()))
 for e in events:
  k=(e['role'],e['side']);v=totals[k];v['qty']+=e['shares'];v['cash']+=e['shares']*e['price'];v['orders'].add(oid(e))
 qu=math.fsum(e['shares'] for e in events if e['side']=='UP');qd=math.fsum(e['shares'] for e in events if e['side']=='DOWN')
 cash=math.fsum(e['shares']*e['price'] for e in events);net=up-down
 # Even if ALL weak-side fills arrive before any strong-side fills, no sign change.
 robust=1 if net-qd>EPS else -1 if net+qu<-EPS else 0
 weak='DOWN' if robust==1 else 'UP' if robust==-1 else None
 cells={};patterns={k:False for k in PATTERNS}
 if robust:
  for (route,side),v in totals.items():
   eff='SERVICE' if side==weak else 'EXPANSION';k=route+'_'+eff
   du=(v['qty'] if side=='UP' else 0.)-v['cash'];dd=(v['qty'] if side=='DOWN' else 0.)-v['cash']
   cells[k]=dict(side=side,qty=v['qty'],cash=v['cash'],deltaUP=du,deltaDOWN=dd,
       deltaFloor=dd if robust==1 else du,deltaBest=du if robust==1 else dd,orders=len(v['orders']))
   assert (cells[k]['deltaFloor']>0) if eff=='SERVICE' else (cells[k]['deltaFloor']<0 and cells[k]['deltaBest']>0)
  s=set(cells);patterns={
   'TAKER_BOTH_EFFECTS':{'TAKER_SERVICE','TAKER_EXPANSION'}<=s,
   'MAKER_BOTH_EFFECTS':{'MAKER_SERVICE','MAKER_EXPANSION'}<=s,
   'PASSIVE_SERVICE_ACTIVE_EXPANSION':{'MAKER_SERVICE','TAKER_EXPANSION'}<=s,
   'ACTIVE_SERVICE_PASSIVE_EXPANSION':{'TAKER_SERVICE','MAKER_EXPANSION'}<=s,
   'ALL_FOUR_CELLS':len(s)==4}
 postUP=up+qu;postDOWN=down+qd;postCost=cost+cash
 dF=min(postUP,postDOWN)-postCost-(min(up,down)-cost)
 dB=max(postUP,postDOWN)-postCost-(max(up,down)-cost)
 err=max(abs(sum(x['deltaFloor'] for x in cells.values())-dF),abs(sum(x['deltaBest'] for x in cells.values())-dB)) if robust else 0.
 assert err<1e-6
 return dict(preUP=up,preDOWN=down,preNet=net,preCost=cost,postUP=postUP,postDOWN=postDOWN,postCost=postCost,
    qu=qu,qd=qd,batchCash=cash,robustSign=robust,weakSide=weak,cells=cells,patterns=patterns,
    deltaFloor=dF,deltaBest=dB,cellReconciliationError=err,
    takerBothSides=all(('TAKER',s) in totals for s in ('UP','DOWN')),
    hasTaker=any(k[0]=='TAKER' for k in totals),hasMaker=any(k[0]=='MAKER' for k in totals))


def overlap_kind(a,b):
 if max(a['first'],b['first'])>=min(a['last'],b['last']):return None
 if a['first']<b['first']<a['last']<b['last'] or b['first']<a['first']<b['last']<a['last']:return 'STRICT_ABAB'
 if a['first']<b['first']<b['last']<a['last'] or b['first']<a['first']<a['last']<b['last']:return 'STRICT_NESTED'
 return 'OTHER_STRICT_SPAN_OVERLAP'


def witness_order(k,o):
 return dict(route=k[0],side=k[1],quote=k[2],order_hash=k[3],first_ms=o['first'],last_ms=o['last'],
   distinct_fill_times=len(o['times']),observed_qty=o['qty'],prices=sorted(o['prices']),
   original_qty=None,continuous_live_interval=None)


def process_market(raw):
 m=raw['identity'];ev=raw['events'];ref=raw['result'];mid=m['market_id'];asset=m['asset'];block=m['block']
 assert ref['sell_proceeds_usdt']==0 and len(ev)==len({e['leg_id'] for e in ev})==ref['fill_count']
 start=m['window_start_ms'];end=m['window_end_ms'];by=defaultdict(list);orders={}
 for e in ev:
  assert start<=e['event_ms']<end and e['event_ms']%1000==0 and e['quote_type']=='BID'
  assert e['role'] in ('MAKER','TAKER') and e['side'] in ('UP','DOWN') and e['shares']>0 and 0<e['price']<1
  k=oid(e);o=orders.setdefault(k,dict(first=e['event_ms'],last=e['event_ms'],times=set(),qty=0.,prices=set()))
  o['first']=min(o['first'],e['event_ms']);o['last']=max(o['last'],e['event_ms']);o['times'].add(e['event_ms']);o['qty']+=e['shares'];o['prices'].add(e['price'])
  by[e['event_ms']].append(e)
 assert len(orders)==ref['parent_count']
 up=down=cost=0.;seen=set();nodes=[];counts=Counter();sample={};maxcell=0.
 for t,es in sorted(by.items()):
  x=batch_effect(up,down,cost,es);up=x['postUP'];down=x['postDOWN'];cost=x['postCost'];maxcell=max(maxcell,x['cellReconciliationError'])
  counts['eventBatches']+=1;counts['takerBatches']+=x['hasTaker'];counts['makerBatches']+=x['hasMaker']
  counts['mixedRouteBatches']+=x['hasTaker'] and x['hasMaker'];counts['allTakerBothSidesSeconds']+=x['takerBothSides']
  counts['nonzeroPreNet']+=abs(x['preNet'])>EPS;counts['robustSignBatches']+=bool(x['robustSign'])
  counts['nonrobustOrSeedBatches']+=not bool(x['robustSign'])
  new={oid(e) for e in es}-seen;first_two=all(any(k[0]=='TAKER' and k[1]==s for k in new) for s in ('UP','DOWN'))
  if x['robustSign']:
   for name,flag in x['patterns'].items():
    counts[name]+=flag
    if flag and name not in sample:
     sample[name]=dict(asset=asset,block=block,market_id=mid,t=t,**x,
       atLeastOneFirstObservedTakerEachSide=first_two,
       cellOrderHashes={r+'_'+s:sorted({e['order_hash'] for e in es if e['role']==r and e['side']==s}) for r in ('MAKER','TAKER') for s in ('UP','DOWN')},
       semantics='simultaneous at recorded1s resolution; robust effect signs for all intrasecond orders; not private purposes')
   counts['firstObservedTakerBothEffects']+=x['patterns']['TAKER_BOTH_EFFECTS'] and first_two
  seen.update(oid(e) for e in es);nodes.append(dict(t=t,robustSign=x['robustSign'],preNet=x['preNet']))
 totalerr=max(abs(up-ref['up_position_shares']),abs(down-ref['down_position_shares']),abs(cost-ref['buy_notional_usdt']))
 totalerr=max(totalerr,abs(up-math.fsum(e['shares'] for e in ev if e['side']=='UP')),
              abs(down-math.fsum(e['shares'] for e in ev if e['side']=='DOWN')),
              abs(cost-math.fsum(e['shares']*e['price'] for e in ev)))
 assert totalerr<1e-6
 # No invented order occupancy: spans are first/last OBSERVED executions only.
 times=[n['t'] for n in nodes];prefix={s:[0] for s in (-1,1)}
 for n in nodes:
  for s in prefix:prefix[s].append(prefix[s][-1]+int(n['robustSign']!=s))
 def stable(lo,hi):
  a=bisect.bisect_left(times,lo);b=bisect.bisect_right(times,hi)
  if b<=a:return 0
  for s in (-1,1):
   if prefix[s][b]==prefix[s][a]:return s
  return 0
 tk={k:v for k,v in orders.items() if k[0]=='TAKER'};mk={k:v for k,v in orders.items() if k[0]=='MAKER'}
 multi={k:v for k,v in tk.items() if len(v['times'])>=2};uu=[(k,v) for k,v in multi.items() if k[1]=='UP'];dd=[(k,v) for k,v in multi.items() if k[1]=='DOWN']
 counts['takerOrders']=len(tk);counts['makerOrders']=len(mk);counts['takerMultiTimestampOrders']=len(multi)
 counts['makerMultiTimestampOrders']=sum(len(v['times'])>=2 for v in mk.values())
 pairs=[];involved=set()
 for ka,a in uu:
  for kb,b in dd:
   typ=overlap_kind(a,b)
   if typ is None:continue
   lo=min(a['first'],b['first']);hi=max(a['last'],b['last']);rs=stable(lo,hi)
   counts['oppositeTakerSpanPairs']+=1;counts['span_'+typ]+=1
   if rs:
    counts['robustOppositeTakerSpanPairs']+=1;counts['robust_span_'+typ]+=1;involved.update((ka,kb))
   pairs.append(dict(asset=asset,block=block,market_id=mid,kind=typ,union_start_ms=lo,union_end_ms=hi,
       overlap_start_ms=max(a['first'],b['first']),overlap_end_ms=min(a['last'],b['last']),robustNetSign=rs,
       upOrder=witness_order(ka,a),downOrder=witness_order(kb,b)))
 counts['robustSpanInvolvedOrders']=len(involved)
 sandwiches=[]
 for ka,a in multi.items():
  opposite=[e for e in ev if e['role']=='TAKER' and e['side']!=ka[1] and a['first']<e['event_ms']<a['last']]
  if not opposite:continue
  counts['takerReturningAroundOppositeOrders']+=1;rs=stable(a['first'],a['last'])
  if rs:counts['robustReturningAroundOppositeOrders']+=1
  e=min(opposite,key=lambda e:e['event_ms'])
  sandwiches.append(dict(asset=asset,block=block,market_id=mid,robustNetSign=rs,carrier=witness_order(ka,a),
      middleOppositeOrder=e['order_hash'],middle_ms=e['event_ms'],middleSide=e['side'],middleQty=e['shares'],
      otherObservedOppositeIdentities=len({oid(x) for x in opposite}),
      semantics='same hash filled before and after opposite-side execution; no claim continuousLIVE or sharedobjective'))
 for name,rows in [('ROBUST_SPAN_OVERLAP',[p for p in pairs if p['robustNetSign']]),
                   ('ROBUST_STRICT_ABAB',[p for p in pairs if p['robustNetSign'] and p['kind']=='STRICT_ABAB']),
                   ('ROBUST_ORDER_RETURN',[p for p in sandwiches if p['robustNetSign']])]:
  if rows:sample[name]=min(rows,key=lambda r:r.get('union_start_ms',r.get('carrier',{}).get('first_ms',2**63)))
 return dict(asset=asset,block=block,market_id=mid,counts=dict(counts),fillLegs=len(ev),
      maxFinalCashQtyError=totalerr,maxCellAdditivityError=maxcell),sample,pairs,sandwiches


def aggregate(rows):
 counts=Counter()
 for r in rows:counts.update(r['counts'])
 marketCounts={k:sum(r['counts'].get(k,0)>0 for r in rows) for k in counts}
 return dict(markets=len(rows),fillLegs=sum(r['fillLegs'] for r in rows),counts=dict(counts),marketsWith=marketCounts)


def tests():
 def e(side,q,p=.3,r='TAKER',h='x'):
  return dict(side=side,shares=q,price=p,role=r,quote_type='BID',order_hash=h)
 n=0
 # Four effects coexist with a stable dominant side, independent of ordering.
 es=[e('UP',2,r='MAKER',h='1'),e('DOWN',3,r='MAKER',h='2'),e('UP',2,h='3'),e('DOWN',1,h='4')]
 x=batch_effect(20,5,8,es);assert x['robustSign']==1 and x['patterns']['ALL_FOUR_CELLS'];n+=1
 assert x==batch_effect(20,5,8,list(reversed(es)));n+=1
 assert not batch_effect(10,9,4,[e('UP',10),e('DOWN',2)])['robustSign'];n+=1
 assert not batch_effect(10,9,4,[e('DOWN',1)])['robustSign'];n+=1
 assert batch_effect(3,20,8,es)['robustSign']==-1;n+=1
 assert not batch_effect(0,0,0,[e('UP',2),e('DOWN',3)])['robustSign'];n+=1
 assert not batch_effect(20,5,8,[e('UP',2)])['patterns']['TAKER_BOTH_EFFECTS'];n+=1
 assert overlap_kind(dict(first=1,last=4),dict(first=2,last=5))=='STRICT_ABAB';n+=1
 assert overlap_kind(dict(first=1,last=5),dict(first=2,last=4))=='STRICT_NESTED';n+=1
 assert overlap_kind(dict(first=1,last=2),dict(first=2,last=5)) is None;n+=1
 assert overlap_kind(dict(first=1,last=1),dict(first=0,last=3)) is None;n+=1
 return n


def main():
 began=time.monotonic();nt=tests();assert sha(SRC)==SRC_SHA
 source=json.loads(SRC.read_text(encoding='utf-8-sig'));OUT.mkdir(exist_ok=True);op=OUT/'SCORE.json'
 if op.exists():raise FileExistsError('immutable output exists')
 markets=[];witnesses={};pairs=[];sandwiches=[]
 for inp in source['inputs']:
  p=Path(inp['source']['path']);assert sha(p)==inp['source']['sha256'];expanded=0
  with gzip.open(p,'rt',encoding='utf-8') as f:
   for line in f:
    expanded+=len(line.encode());assert expanded<32*1024**2
    row,samp,pp,ss=process_market(json.loads(line));markets.append(row);pairs+=pp;sandwiches+=ss
    for k,v in samp.items():
     for scope in [row['asset'],row['asset']+'_'+row['block']]:
      name=scope+'_'+k
      def wt(x):return x.get('t',x.get('union_start_ms',x.get('carrier',{}).get('first_ms',2**63)))
      if name not in witnesses or wt(v)<wt(witnesses[name]):witnesses[name]=v
 assert len(markets)==len({(r['asset'],r['market_id']) for r in markets})==144 and sum(r['fillLegs'] for r in markets)==38721
 assets={a:aggregate([r for r in markets if r['asset']==a]) for a in ['BTC','ETH']}
 groups={a+'_'+b:aggregate([r for r in markets if r['asset']==a and r['block']==b]) for a in ['BTC','ETH'] for b in ['W1','W2','W3','W4','W5','W6']}
 write(OUT/'EARLIEST_WITNESSES.json',witnesses)
 span=OUT/'FILL_SPAN_EVIDENCE.jsonl.gz'
 with gzip.open(span,'wt',encoding='utf-8',compresslevel=3) as f:
  for typ,rs in [('OPPOSITE_TAKER_SPAN_PAIR',pairs),('ORDER_RETURN',sandwiches)]:
   for r in rs:f.write(json.dumps(dict(evidence_type=typ,**r),ensure_ascii=False,separators=(',',':'))+'\n')
 d=dict(version='TARGET_REPAIR_ADD_COEXISTENCE_V1',verdict='COEXECUTED_EFFECTS_AND_FILLSPAN_INTERLEAVING_ONLY_PRIVATE_OBJECTIVE_CONCURRENCY_UNIDENTIFIED',
   sources=source['inputs'],sourceScoreSha256=SRC_SHA,marketCount=144,fillLegs=38721,assets=assets,groups=groups,perMarket=markets,
   robustDefinition='preNet minus ALL opposite-side same-second observed buys keeps sign, so service/expansion effect labels do not depend on intrasecond ordering',
   fullPurposeState='UNKNOWN; four effect×route flags are not exclusive purpose labels',
   maxCashQtyError=max(r['maxFinalCashQtyError'] for r in markets),maxCellAdditivityError=max(r['maxCellAdditivityError'] for r in markets),
   unitTests=nt,newHFT=0,newTraining=0,modelFits=0,policyChanges=0,elapsedSeconds=time.monotonic()-began,
   artifacts=[dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)) for p in [OUT/'EARLIEST_WITNESSES.json',span]],
   limitations=['1s event timestamps cannot prove simultaneous decisions; a fast serialized scheduler or old orders can generate coexecution',
    'robust stable-sign rule proves effect attribution invariance, not private Repair/ADD grant or direction-belief semantics',
    'first-to-last fills delimit observed activity, not a continuously knownLIVE owner or no cancellation; original quantity and terminal statuses unknown',
    'same-order A-B-A or ABAB is execution interleaving, not definitive evidence of concurrent private controllers',
    'all144markets are already-observed selected block descriptions; no significance/null model/invariance claim',
    'no new venue sizing, no virtual repaircredit, no requirement everymarket must exercise fourcells, no policy/HFT/PnL promotion'])
 write(op,d)
 print(json.dumps(dict(output=str(op),bytes=op.stat().st_size,sha256=sha(op),elapsed=d['elapsedSeconds'],unitTests=nt,
   assets=assets,groups={k:{'markets':v['markets'],'counts':{x:v['counts'].get(x,0) for x in PATTERNS+['firstObservedTakerBothEffects','robustOppositeTakerSpanPairs','robustReturningAroundOppositeOrders']},
                    'marketsWith':{x:v['marketsWith'].get(x,0) for x in PATTERNS}} for k,v in groups.items()},
   maxCashQtyError=d['maxCashQtyError'],maxCellAdditivityError=d['maxCellAdditivityError']),ensure_ascii=False))


if __name__=='__main__':main()
