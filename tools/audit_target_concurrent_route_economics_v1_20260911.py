"""Fixed144-market pre-state/side route evidence. Never a trading controller.
Each side has separate Maker and Taker observations; no exclusive purpose labels.
Commands: smoke, block W1..W6, finalize. Full-size processing remains bounded ETL.
"""
from pathlib import Path
from collections import defaultdict,Counter
import argparse,gzip,json,hashlib,math,statistics,time,bisect
from tools.build_target_cross_size_public_join_v1_20260910 import raw_book,decorate

BASE=Path('data/research/r4_v0/p0_provenance_v1')
SRC=BASE/'target_intermediate_size_performance_v1_20260910/SCORE.json'
SRC_SHA='a045bd1861fa7f61248e513b4576f07098b26e8c8636f647a2d4619a6268ba71'
OUT=BASE/'target_concurrent_route_economics_v1_20260911'
OLD_META=BASE/'TARGET_SIZE_REGIME_LIFECYCLE_OBSERVATION_V1_20260910_1855.json'
CACHE=Path('data/research/market_capsule_v1/benchmark_50_v1/book_updates.parquet')
BOOK_HELPER=Path('tools/build_target_cross_size_public_join_v1_20260910.py')
BOOK_SHA='d70ee933954d7c087c6cfad22106756733029b3dfd645f247ee4c7cd2455cc2d'
BLOCKS=['W1','W2','W3','W4','W5','W6'];EPS=1e-8
COLS=['market_id','source_ms','received_ms','order_count','is_checkpoint','best_bid','best_ask','spread',
 'bid_depth_total','ask_depth_total','top5_bids_json','top5_asks_json']


def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()

def write(p,d):
 with Path(p).open('x',encoding='utf-8') as f:json.dump(d,f,ensure_ascii=False,indent=2,allow_nan=False)

def read(p,cap=2*1024**2):
 p=Path(p);assert p.stat().st_size<cap
 return json.loads(p.read_text(encoding='utf-8-sig'))

def gzread(p,cap=40*1024**2):
 total=0;out=[]
 with gzip.open(p,'rt',encoding='utf-8') as f:
  for line in f:
   total+=len(line.encode());assert total<cap;out.append(json.loads(line))
 return out

def mean(x):return statistics.mean(x) if x else None

def median(x):return statistics.median(x) if x else None

def oid(e):return (e['role'],e['side'],e['quote_type'],e['order_hash'])

def pricebin(p):return 0 if p<.1-EPS else 1 if p<.3-EPS else 2 if p<.7-EPS else 3

def spreadbin(p):return 0 if p<=.01+EPS else 1 if p<=.03+EPS else 2

def balancebin(x):return 0 if x<.25 else 1 if x<.5 else 2


def rate(rows,key):
 by=defaultdict(list)
 for r in rows:by[r['market_id']].append(float(r[key]))
 return dict(sideSeconds=len(rows),markets=len(by),events=sum(r[key] for r in rows),
    pooled=mean([float(r[key]) for r in rows]),marketEqual=mean([mean(v) for v in by.values()]))


def tests():
 assert pricebin(.09)==0 and pricebin(.10)==1 and pricebin(.30)==2 and pricebin(.70)==3
 assert spreadbin(.01)==0 and spreadbin(.03)==1
 for bid,ask in [(.3,.31),(.02,.08),(.5,.6),(.90,.95)]:
  assert abs(ask+(1-bid)-(1+ask-bid))<1e-12
  assert abs(bid+(1-ask)-(1-ask+bid))<1e-12
  assert abs(ask+(1-ask)-1)<1e-12
  assert abs(bid+(1-bid)-1)<1e-12
  # For equalqty at samebook bothTaker spendspread; never pretend bothMaker fills.
  q=7;assert abs(q-q*(ask+1-bid)+q*(ask-bid))<1e-12
 es=[dict(role='MAKER',side='UP',quote_type='BID',order_hash='A'),dict(role='TAKER',side='UP',quote_type='BID',order_hash='B')]
 assert len({oid(e) for e in es})==2
 return 8


def initialize():
 if (OUT/'MANIFEST.json').exists():return read(OUT/'MANIFEST.json')
 assert sha(SRC)==SRC_SHA and sha(BOOK_HELPER)==BOOK_SHA
 d=read(SRC);cacheids={r['marketId'] for r in read(OLD_META)['markets'] if r['asset']=='BTC' and r['regime']=='HISTORICAL'}
 markets=[]
 for r in d['rows']:
  p=CACHE if r['market_id'] in cacheids else Path('data/execution_tape_v1/markets')/(str(r['market_id'])+'.json.xz')
  assert p.exists() and (p==CACHE or p.stat().st_size<2*1024**2)
  markets.append(dict(asset=r['asset'],block=r['block'],market_id=r['market_id'],start_ms=r['start_ms'],end_ms=r['end_ms'],
   source=p.as_posix(),source_bytes=p.stat().st_size,source_sha256=sha(p),is_capsule=p==CACHE))
 assert len(markets)==144
 smoke=[]
 for asset in ['BTC','ETH']:
  for block in ['W1','W6']:smoke.append(min((r for r in markets if r['asset']==asset and r['block']==block),key=lambda r:r['start_ms'])['market_id'])
 OUT.mkdir(exist_ok=True)
 man=dict(version='TARGET_CONCURRENT_ROUTE_ECONOMICS_V1',createdDate='2026-09-11',markets=markets,smoke_market_ids=smoke,
   sourceScoreSha256=SRC_SHA,inputs=d['inputs'],sourceCode={str(p):sha(p) for p in [Path(__file__),BOOK_HELPER,Path('tools/build_market_capsule_v1.py')]},
   original144CohortUnchanged=True,allExposureSeconds=43200,sideRows=86400,noNewHFT=True,noPolicyFit=True)
 write(OUT/'MANIFEST.json',man);return man


def load_book(m,access):
 p=Path(m['source']);assert p.stat().st_size==m['source_bytes'] and sha(p)==m['source_sha256']
 if m['is_capsule']:
  rel=access.query('book_updates',where='market_id='+str(m['market_id']),columns=COLS,order_by='source_ms,received_ms',limit=25001)
  rows=[dict(zip(COLS,r)) for r in rel.fetchall()];assert len(rows)<=25000
  book,info=decorate(rows);info['source_kind']='EXISTING_CAPSULE'
 else:
  book,info,rows=raw_book(p,m['market_id']);info['source_kind']='EXACT_RETAINED_TAPE'
 for b,r in zip(book,rows):
  bids=json.loads(r['top5_bids_json']);asks=json.loads(r['top5_asks_json'])
  bb=b['best_bid'];ba=b['best_ask']
  b['up_ask_band01']=sum(q for px,q in asks if ba is not None and px<=ba+.01+EPS)
  b['down_ask_band01']=sum(q for px,q in bids if bb is not None and px>=bb-.01-EPS)
 return book,info


def process(m,raw,access):
 ev=raw['events'];ref=raw['result'];assert raw['identity']['market_id']==m['market_id'] and ref['sell_proceeds_usdt']==0
 assert len(ev)==len({r['leg_id'] for r in ev})==ref['fill_count']
 by=defaultdict(list)
 for e in ev:
  assert m['start_ms']<=e['event_ms']<m['end_ms'] and e['event_ms']%1000==0 and e['quote_type']=='BID'
  assert e['side'] in ('UP','DOWN') and e['role'] in ('MAKER','TAKER') and e['shares']>0 and 0<e['price']<1
  by[e['event_ms']].append(e)
 book,info=load_book(m,access);available=sorted(book,key=lambda b:(b['available'],b['seq']));j=0;latest=None
 q={'UP':0.,'DOWN':0.};cost=0.;mq={'UP':0.,'DOWN':0.};mc={'UP':0.,'DOWN':0.};mcount={'UP':0,'DOWN':0}
 lastT={s:None for s in q};lastMQ={s:0. for s in q};lastMN={s:0 for s in q};seen=set();out=[];joint=[];epoch_errors=0
 for t in range(m['start_ms'],m['end_ms'],1000):
  while j<len(available) and available[j]['available']<t:
   b=available[j]
   if latest is None or b['seq']>latest['seq']:latest=b
   j+=1
  es=by.get(t,[]);net=q['UP']-q['DOWN'];gross=sum(q.values());floor=min(q.values())-cost
  weak='DOWN' if net>EPS else 'UP' if net<-EPS else None
  lbl_qty={s:math.fsum(e['shares'] for e in es if e['side']==s) for s in q}
  robust=bool(weak and lbl_qty[weak]<abs(net)-EPS)
  bookstatus=latest['status'] if latest else 'NO_PRIOR_CHECKPOINT'
  age=None
  if latest:
   assert max(latest['source_ms'],latest['received_ms'],latest['chain_source_max'],latest['chain_received_max'])<t
   age=max(t-latest['source_ms'],t-latest['received_ms'])
  for side in ['UP','DOWN']:
   ts=[e for e in es if e['side']==side and e['role']=='TAKER'];ms=[e for e in es if e['side']==side and e['role']=='MAKER']
   tq=math.fsum(e['shares'] for e in ts);tc=math.fsum(e['shares']*e['price'] for e in ts)
   mkq=math.fsum(e['shares'] for e in ms);mkc=math.fsum(e['shares']*e['price'] for e in ms)
   progress=mq[side]-lastMQ[side] if lastT[side] is not None else None
   assert progress is None or progress>=-EPS
   r=dict(asset=m['asset'],block=m['block'],market_id=m['market_id'],t=t,side=side,
     f_phase=(t-m['start_ms'])//100000,f_seconds_left=(m['end_ms']-t)/1000,
     f_role=('WEAK' if side==weak else 'STRONG') if weak else 'NEUTRAL',
     f_up=q['UP'],f_down=q['DOWN'],f_net=net,f_gross=gross,f_cost=cost,f_floor=floor,
     f_relative_imbalance=abs(net)/gross if gross>EPS else None,f_floor_to_cost=floor/cost if cost>EPS else None,
     f_maker_side_qty=mq[side],f_maker_other_qty=mq['DOWN' if side=='UP' else 'UP'],
     f_had_same_side_taker=lastT[side] is not None,
     f_seconds_since_same_taker=(t-lastT[side])/1000 if lastT[side] is not None else None,
     f_maker_qty_since_same_taker=progress,
     f_maker_events_since_same_taker=mcount[side]-lastMN[side] if lastT[side] is not None else None,
     f_has_side_maker_progress=(progress>EPS) if progress is not None else None,
     book_status=bookstatus,book_source_ms=latest['source_ms'] if latest else None,
     book_received_ms=latest['received_ms'] if latest else None,
     book_chain_source_max=latest['chain_source_max'] if latest else None,
     book_chain_received_max=latest['chain_received_max'] if latest else None,
     f_book_age=age,
     fresh1=bool(bookstatus=='OK' and age<=1000),fresh3=bool(bookstatus=='OK' and age<=3000),fresh10=bool(bookstatus=='OK' and age<=10000),
     f_bid=None,f_ask=None,f_spread=None,f_best_ask_qty=None,f_ask_band01=None,
     f_gap_to_best_ask_qty=None,f_gap_to_ask_band01=None,f_protection_per_cash=None,
     y_hasT=bool(ts),y_hasM=bool(ms),y_both=bool(ts and ms),y_Tqty=tq,y_Tcash=tc,y_Mqty=mkq,y_Mcash=mkc,
     y_Tvwap=tc/tq if tq>EPS else None,y_Mvwap=mkc/mkq if mkq>EPS else None,
     y_first_observed_T=any(oid(e) not in seen for e in ts),
     y_all_T_first_observed=all(oid(e) not in seen for e in ts) if ts else None,
     y_robust_whole_batch_sign=robust,y_T_over_gap=bool(weak==side and tq>abs(net)+EPS),
     y_Tminus_prior_ask=None,y_Mminus_prior_bid=None)
   if bookstatus=='OK':
    b=latest;ask=b['best_ask'] if side=='UP' else 1-b['best_bid'];bid=b['best_bid'] if side=='UP' else 1-b['best_ask']
    aq=b['ask_qty'] if side=='UP' else b['bid_qty'];band=b['up_ask_band01'] if side=='UP' else b['down_ask_band01']
    assert abs(ask-bid-b['spread'])<1e-10
    r.update(f_bid=bid,f_ask=ask,f_spread=ask-bid,f_best_ask_qty=aq,f_ask_band01=band,
      f_gap_to_best_ask_qty=abs(net)/aq if aq and aq>0 else None,
      f_gap_to_ask_band01=abs(net)/band if band>0 else None,
      f_protection_per_cash=(1-ask)/ask if side==weak else None,
      y_Tminus_prior_ask=(r['y_Tvwap']-ask) if ts else None,
      y_Mminus_prior_bid=(r['y_Mvwap']-bid) if ms else None)
   out.append(r)
  if bookstatus=='OK':
   a=latest['best_ask'];b=latest['best_bid'];s=a-b
   assert abs((a+1-b)-(1+s))<1e-12 and abs((b+1-a)-(1-s))<1e-12
   if es and any(e['role']=='TAKER' for e in es):
    joint.append(dict(asset=m['asset'],block=m['block'],market_id=m['market_id'],t=t,
      spread=s,prior_two_taker_unitcost=1+s,prior_two_maker_unitcost=1-s,prior_mixed_unitcost=1.,
      observed_Tboth=all(any(e['role']=='TAKER' and e['side']==side for e in es) for side in q),fresh3=age<=3000))
  for e in es:
   side=e['side'];amount=e['shares'];value=e['price']*amount;q[side]+=amount;cost+=value
   if e['role']=='MAKER':mq[side]+=amount;mc[side]+=value
  for side in q:
   if any(e['role']=='MAKER' and e['side']==side for e in es):mcount[side]+=1
   if any(e['role']=='TAKER' and e['side']==side for e in es):lastT[side]=t;lastMQ[side]=mq[side];lastMN[side]=mcount[side]
  seen.update(oid(e) for e in es)
 errors=[abs(q['UP']-ref['up_position_shares']),abs(q['DOWN']-ref['down_position_shares']),abs(cost-ref['buy_notional_usdt']),
         abs(cost-math.fsum(e['shares']*e['price'] for e in ev))]
 assert max(errors)<1e-6 and len(seen)==ref['parent_count']
 assert len(out)==600
 info.update(asset=m['asset'],block=m['block'],market_id=m['market_id'],maxCashQtyError=max(errors),
    nonzeroSeconds=sum(r['f_role']!='NEUTRAL' for r in out)//2,
    TakerSideEvents=sum(r['y_hasT'] for r in out),MakerSideEvents=sum(r['y_hasM'] for r in out),
    fresh3SideRows=sum(r['fresh3'] for r in out))
 return out,info,joint


def table_summary(rows):
 groups={}
 for asset in ['BTC','ETH']:
  aa=[r for r in rows if r['asset']==asset]
  if not aa:continue
  g=dict(markets=len({r['market_id'] for r in aa}),sideRows=len(aa),fresh1=sum(r['fresh1'] for r in aa),
    fresh3=sum(r['fresh3'] for r in aa),fresh10=sum(r['fresh10'] for r in aa),statuses=dict(Counter(r['book_status'] for r in aa)),
    activeRows=sum(r['y_hasT'] for r in aa),roles={})
  for role in ['WEAK','STRONG']:
   ss=[r for r in aa if r['fresh3'] and r['f_role']==role]
   z=dict(eligible=len(ss),marketCount=len({r['market_id'] for r in ss}),
      outcomeCounts=dict(Counter(('BOTH' if r['y_both'] else 'TAKER_ONLY' if r['y_hasT'] else 'MAKER_ONLY' if r['y_hasM'] else 'NO_OBSERVED_FILL') for r in ss)),
      T=rate(ss,'y_hasT'),M=rate(ss,'y_hasM'),priceBins=[])
   for binno in range(4):
    xs=[r for r in ss if pricebin(r['f_ask'])==binno]
    z['priceBins'].append(dict(bin=binno,T=rate(xs,'y_hasT'),M=rate(xs,'y_hasM'),both=rate(xs,'y_both')))
   prices=[r['y_Tminus_prior_ask'] for r in ss if r['y_hasT']]
   z['takerPriceCheck']=dict(events=len(prices),medianDiff=median(prices),moreThanOneCentAway=sum(abs(p)>.01+EPS for p in prices),
       betterThanPriorAsk=sum(p<-.005 for p in prices),sameWithinHalfCent=sum(abs(p)<=.005 for p in prices),worseThanPriorAsk=sum(p>.005 for p in prices))
   g['roles'][role]=z
  groups[asset]=g
 return groups


def run(block=None,smoke=False):
 start=time.monotonic();man=initialize()
 for p,h in man['sourceCode'].items():assert sha(p)==h,'modified analyzer/helper requires new version'
 name='SMOKE' if smoke else block;op=OUT/(name+'.json');rp=OUT/(name+'_SIDES.jsonl.gz')
 if op.exists():print(json.dumps(dict(reused=name,summary=read(op)['summary']),ensure_ascii=False));return
 assert not rp.exists()
 if not smoke:assert (OUT/'SMOKE.json').exists()
 selected=[m for m in man['markets'] if m['market_id'] in man['smoke_market_ids']] if smoke else [m for m in man['markets'] if m['block']==block]
 bymeta={m['market_id']:m for m in selected};smoked={};infos={}
 if not smoke:
  sd=read(OUT/'SMOKE.json');infos={r['market_id']:r for r in sd['marketChecks']}
  for r in gzread(OUT/'SMOKE_SIDES.jsonl.gz'):smoked.setdefault(r['market_id'],[]).append(r)
 from tools.research_data_access_v1 import ResearchData
 rows=[];checks=[];joints=[]
 with ResearchData() as access:
  access.con.execute('SET threads=1');access.con.execute("SET memory_limit='128MB'")
  for inp in man['inputs']:
   groupname=Path(inp['path']).stem
   if not any(m['block']==groupname for m in selected):continue
   p=Path(inp['source']['path']);assert sha(p)==inp['source']['sha256']
   for raw in gzread(p):
    mid=raw['identity']['market_id']
    if mid not in bymeta:continue
    if time.monotonic()-start>12:raise TimeoutError('bounded ETL stage cap: split before retry, not background')
    if mid in smoked:
     rows+=smoked[mid];checks.append(infos[mid]|{'reusedSmoke':True});continue
    a,b,c=process(bymeta[mid],raw,access);rows+=a;checks.append(b);joints+=c
 assert len(checks)==len(selected) and len(rows)==600*len(selected)
 with gzip.open(rp,'wt',encoding='utf-8',compresslevel=3) as f:
  for r in rows:f.write(json.dumps(r,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
 summary=table_summary(rows)
 d=dict(stage=name,manifest_sha256=sha(OUT/'MANIFEST.json'),summary=summary,marketChecks=checks,
     sides=dict(path=str(rp),bytes=rp.stat().st_size,sha256=sha(rp)),tests=tests(),newHFT=0,newTraining=0,
     elapsed=time.monotonic()-start,semantics='prestate strictlybefore event second; side observations not latent purpose or HOLD')
 write(op,d)
 print(json.dumps(dict(stage=name,markets=len(selected),sideRows=len(rows),seconds=d['elapsed'],tests=d['tests'],
   summary={a:{k:v for k,v in s.items() if k!='roles'} for a,s in summary.items()},
   role_counts={a:{role:v['outcomeCounts'] for role,v in s['roles'].items()} for a,s in summary.items()},
   maximumCashError=max(c['maxCashQtyError'] for c in checks)),ensure_ascii=False))


def standardized(rows,contrast,age_sensitivity=False):
 labels=['y_hasT','y_hasM','y_both'];sets=[]
 for arm in [0,1]:
  eligible=[]
  for r in rows:
   if not r['fresh3'] or r['f_role']=='NEUTRAL':continue
   if contrast=='PRICE':
    p=r['f_ask'];g=0 if p<.3-EPS else 1 if p>=.7-EPS else None
   else:
    if not r['f_had_same_side_taker']:continue
    g=int(r['f_has_side_maker_progress'])
   if g==arm:eligible.append(r)
  counts=Counter(r['market_id'] for r in eligible);cells={}
  for r in eligible:
   k=(r['block'],r['f_phase'],balancebin(r['f_relative_imbalance']))
   k+=(spreadbin(r['f_spread']),) if contrast=='PRICE' else (pricebin(r['f_ask']),)
   if age_sensitivity:
    sec=r['f_seconds_since_same_taker'];k+=(0 if sec<=3 else 1 if sec<=15 else 2,)
   w=1/len(counts)/counts[r['market_id']];c=cells.setdefault(k,dict(w=0.,markets=set(),n=0,sums=Counter()))
   c['w']+=w;c['markets'].add(r['market_id']);c['n']+=1
   for l in labels:c['sums'][l]+=w*r[l]
  sets.append(cells)
 a,b=sets;keys=[k for k in a if k in b and len(a[k]['markets'])>=3 and len(b[k]['markets'])>=3]
 weights={k:min(a[k]['w'],b[k]['w']) for k in keys};mass=sum(weights.values());scores={}
 for l in labels:
  x=sum(weights[k]*a[k]['sums'][l]/a[k]['w'] for k in keys)/mass if mass else None
  y=sum(weights[k]*b[k]['sums'][l]/b[k]['w'] for k in keys)/mass if mass else None
  scores[l]=dict(arm0=x,arm1=y,diff=y-x if mass else None)
 return dict(contrast=contrast,arms=['LOW_ASK_BELOW_30C','HIGH_ASK_AT_LEAST_70C'] if contrast=='PRICE' else ['NO_MAKER_PROGRESS','MAKER_PROGRESS'],
  commonCells=len(keys),commonMass=mass,arm0Coverage=sum(a[k]['w'] for k in keys),arm1Coverage=sum(b[k]['w'] for k in keys),
  elapsedSinceTakerMatched=age_sensitivity,scores=scores,
  cells=[dict(key=list(k),weight=weights[k],n0=a[k]['n'],n1=b[k]['n'],markets0=len(a[k]['markets']),markets1=len(b[k]['markets'])) for k in keys])


def finalize():
 op=OUT/'SCORE.json'
 if op.exists():raise FileExistsError(str(op))
 man=read(OUT/'MANIFEST.json');rows=[];checks=[];sources=[];grouped={}
 for block in BLOCKS:
  d=read(OUT/(block+'.json'));assert d['manifest_sha256']==sha(OUT/'MANIFEST.json')
  p=Path(d['sides']['path']);assert sha(p)==d['sides']['sha256'];rr=gzread(p,40*1024**2)
  rows+=rr;checks+=d['marketChecks'];sources.append(d['sides']);grouped[block]=d['summary']
 assert len(rows)==86400 and len({(r['market_id'],r['t'],r['side']) for r in rows})==86400
 assert len({r['market_id'] for r in rows})==144
 summary=table_summary(rows);contrasts={}
 for asset in ['BTC','ETH']:
  for role in ['WEAK','STRONG']:
   rr=[r for r in rows if r['asset']==asset and r['f_role']==role]
   contrasts[asset+'_'+role]=dict(price=standardized(rr,'PRICE'),progress=standardized(rr,'PROGRESS'),
      progressElapsedSensitivity=standardized(rr,'PROGRESS',True),
      perBlock={b:dict(price=standardized([r for r in rr if r['block']==b],'PRICE'),
       progress=standardized([r for r in rr if r['block']==b],'PROGRESS')) for b in BLOCKS})
 # Every paired side row shares exact same prior book and state, never getsexclusive mode.
 index=defaultdict(list)
 for r in rows:index[(r['market_id'],r['t'])].append(r)
 both=[];maxident=0.
 for key,ss in index.items():
  assert len(ss)==2;u=next(r for r in ss if r['side']=='UP');dn=next(r for r in ss if r['side']=='DOWN')
  assert u['f_net']==dn['f_net'] and u['f_cost']==dn['f_cost'] and u['fresh3']==dn['fresh3']
  if u['book_status']=='OK':
   err=max(abs(u['f_ask']+dn['f_ask']-1-u['f_spread']),abs(u['f_bid']+dn['f_bid']-1+u['f_spread']),
       abs(u['f_ask']+dn['f_bid']-1),abs(u['f_bid']+dn['f_ask']-1));maxident=max(maxident,err);assert err<1e-10
  if u['y_hasT'] and dn['y_hasT']:
   both.append(dict(asset=u['asset'],block=u['block'],market_id=key[0],t=key[1],robust=u['y_robust_whole_batch_sign'],
    fresh3=u['fresh3'],prior_offer_sum=u['f_ask']+dn['f_ask'] if u['book_status']=='OK' else None,
    actual_Tsum=u['y_Tvwap']+dn['y_Tvwap']))
 assert sum(r['robust'] for r in both)==87
 dual={a:dict(observedBothTaker=len([r for r in both if r['asset']==a]),robust=sum(r['asset']==a and r['robust'] for r in both),
   robustFresh3=sum(r['asset']==a and r['robust'] and r['fresh3'] for r in both),
   achievedBelow1DespitePriorOffersAbove1=sum(r['asset']==a and r['robust'] and r['fresh3'] and r['actual_Tsum']<1-EPS and r['prior_offer_sum']>1+EPS for r in both)) for a in ['BTC','ETH']}
 d=dict(version='TARGET_CONCURRENT_ROUTE_ECONOMICS_V1',status='SIDE_SPECIFIC_OBSERVED_ROUTE_CONDITIONS_NOT_PRIVATE_WORK_AUTHORITY',
   sourceManifestSha256=sha(OUT/'MANIFEST.json'),markets=144,marketSeconds=43200,sideRows=86400,summary=summary,perBlock=grouped,
   contrasts=contrasts,dualTakerPriceCheck=dual,dualPriceRows=both,unitBookIdentityMaxError=maxident,
   maxCashQtyError=max(c['maxCashQtyError'] for c in checks),sourceClockByBlock={a+'_'+b:dict(medianSourceMinusReceived=median([c['sourceMinusReceivedMedian'] for c in checks if c['asset']==a and c['block']==b]),
       deltaMismatches=sum(c.get('deltaBeforeMismatches',0) for c in checks if c['asset']==a and c['block']==b)) for a in ['BTC','ETH'] for b in BLOCKS},
   sources=sources,tests=8,newHFT=0,newTraining=0,modelFits=0,policyChanges=0,
   limitations=['side status derives from confirmed prestate only; weak/strong effect is not known Repair/ADD purpose or both-authorized permission',
   'all 1s observations are retrospective target event clocks; no observed fill is not a private HOLD',
   'book strict prior dual-clock/source-dependency is verified, actual decision/placement time and public clock synchronization remain unknown',
   'market-equal cell contrasts control only declared coarse variables, not private expectedreturns/pending/queue/targetgrant or full causal alternatives',
   'progress is actual same-side Maker quantity since previous same-sideTaker, not original duty progress or evidence of order failure',
   'samebook Maker prices are hypothetical execution alternatives with no fill guarantee; pair identity is arithmetic, not new empirical alpha',
   'actual both-side Taker prices can reflect different instants, limits or clock ordering; below1 does not establish simultaneous executable arbitrage',
   'all144markets already observed, blocks and size profiles confounded with dates; do not claim unseen transfer or train from outcomes here'])
 write(op,d)
 compactcontr={k:{name:{kk:vv for kk,vv in v[name].items() if kk!='cells'} for name in ['price','progress','progressElapsedSensitivity']} for k,v in contrasts.items()}
 print(json.dumps(dict(output=str(op),bytes=op.stat().st_size,sha256=sha(op),summary=summary,contrasts=compactcontr,
    dual=dual,clock=d['sourceClockByBlock'],newHFT=0,newTraining=0),ensure_ascii=False))


if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('stage',choices=['smoke','block','finalize']);ap.add_argument('--block',choices=BLOCKS);args=ap.parse_args()
 if args.stage=='smoke':run(smoke=True)
 elif args.stage=='block':run(args.block)
 else:finalize()
