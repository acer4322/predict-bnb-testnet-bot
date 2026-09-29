"""Fixed144market bounded augmentation: last same-side Active inventory reference
and point-in-time public underlying snapshots. No model, HFT, native or runtime.
Full-market desired quantities, pending status and private fairvalue remain unknown.
"""
from pathlib import Path
from collections import defaultdict,Counter
import argparse,sqlite3,bisect,gzip,json,hashlib,math,time
import duckdb

BASE=Path('data/research/r4_v0/p0_provenance_v1')
SRC=BASE/'target_intermediate_size_performance_v1_20260910/SCORE.json'
SRC_SHA='a045bd1861fa7f61248e513b4576f07098b26e8c8636f647a2d4619a6268ba71'
TABLE=BASE/'target_concurrent_route_economics_v1_20260911/side_states.parquet'
TABLE_SHA='2e97491cf365b9c3ba423b5a81fa0955a7d51bf6c298eb4ac36b017e20477147'
OUT=BASE/'target_route_mechanism_discrimination_v1_20260911'
PUB_KEYS=['spotPrice','strikePrice','spotMinusStrikeBps','spotReturn1sBps','spotReturn5sBps',
 'futuresPrice','futuresReturn5sBps','chainlinkMinusStrikeBps','chainlinkSourceAgeMs','chainlinkReceiptAgeMs']
EPS=1e-8


def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()

def read(p,cap=2000000):
 assert Path(p).stat().st_size<cap
 return json.loads(Path(p).read_text(encoding='utf-8-sig'))

def write(p,d):
 with Path(p).open('x',encoding='utf-8') as f:json.dump(d,f,ensure_ascii=False,indent=2,allow_nan=False)

def gzread(p):
 total=0
 with gzip.open(p,'rt',encoding='utf-8') as f:
  for line in f:
   total+=len(line.encode());assert total<32*1024**2;yield json.loads(line)

def conn(path,seconds=5):
 c=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=1);c.row_factory=sqlite3.Row
 c.execute('PRAGMA query_only=ON');c.execute('PRAGMA cache_size=-4096');end=time.monotonic()+seconds
 c.set_progress_handler(lambda:int(time.monotonic()>end),1000);return c

def finite(x):return x if isinstance(x,(int,float)) and math.isfinite(x) else None

def get_public(mid,start,end,asset):
 options=[('SOURCE_V2','data/public_source_snapshot_archive_v2.db'),('FLAT_BTC','data/public_research_archive_v1.db'),('PRIVATE_BTC','data/wallet_taker_private_archive.db')] if asset=='BTC' else [('PRIVATE_ETH','data/wallet_eth_taker_private_archive.db')]
 tried=[]
 for kind,path in options:
  if not Path(path).exists():tried.append({'source':kind,'status':'MISSING_DB'});continue
  c=conn(path)
  if kind=='SOURCE_V2':
   sql='SELECT sampled_at_ms,archived_at_ms,snapshot_json FROM (SELECT sampled_at_ms,archived_at_ms,snapshot_json,ROW_NUMBER() OVER (PARTITION BY (sampled_at_ms/1000) ORDER BY sampled_at_ms,id) AS sample_rank FROM public_source_snapshots_v2 INDEXED BY idx_public_source_snapshots_v2_market_time WHERE market_id=? AND sampled_at_ms>=? AND sampled_at_ms<?) WHERE sample_rank=1 ORDER BY sampled_at_ms LIMIT 1001'
  elif kind.startswith('PRIVATE'):
   sql='SELECT sampled_at_ms,window_end_ms,snapshot_json FROM wallet_taker_private_signal_archive WHERE market_id=? AND sample_second_ms>=? AND sample_second_ms<? ORDER BY sample_second_ms LIMIT 1001'
  else:
   sql='SELECT sampled_at_ms,spot_price,strike_price,spot_minus_strike_bps,spot_return_1s_bps,spot_return_5s_bps,futures_price,futures_return_5s_bps,chainlink_minus_strike_bps,chainlink_source_age_ms,chainlink_receipt_age_ms FROM (SELECT sampled_at_ms,spot_price,strike_price,spot_minus_strike_bps,spot_return_1s_bps,spot_return_5s_bps,futures_price,futures_return_5s_bps,chainlink_minus_strike_bps,chainlink_source_age_ms,chainlink_receipt_age_ms,ROW_NUMBER() OVER(PARTITION BY (sampled_at_ms/1000) ORDER BY sampled_at_ms,id) AS sample_rank FROM wallet_taker_signal_snapshots INDEXED BY idx_public_research_market_time WHERE market_id=? AND sampled_at_ms>=? AND sampled_at_ms<?) WHERE sample_rank=1 ORDER BY sampled_at_ms LIMIT 1001'
  plans=[tuple(r) for r in c.execute('EXPLAIN QUERY PLAN '+sql,(mid,start,end))]
  assert any(('SEARCH public_source_snapshots_v2' in str(p) or 'SEARCH wallet_taker' in str(p)) and ('INDEX' in str(p) or 'PRIMARY KEY' in str(p)) for p in plans),plans
  raw=[dict(r) for r in c.execute(sql,(mid,start,end))];c.close();assert len(raw)<1001
  tried.append({'source':kind,'rows':len(raw)})
  if not raw:continue
  rows=[]
  for r in raw:
   if 'snapshot_json' in r:
    assert len(r['snapshot_json'].encode())<16000;d=json.loads(r['snapshot_json'])
    assert int(d.get('marketId',mid))==mid
    assert int(d.get('sampledAtMs',r['sampled_at_ms']))==r['sampled_at_ms']
    vals={k:finite(d.get(k)) for k in PUB_KEYS}
   else:
    names=['spot_price','strike_price','spot_minus_strike_bps','spot_return_1s_bps','spot_return_5s_bps','futures_price','futures_return_5s_bps','chainlink_minus_strike_bps','chainlink_source_age_ms','chainlink_receipt_age_ms']
    vals={k:finite(r.get(n)) for k,n in zip(PUB_KEYS,names)}
   t=int(r['sampled_at_ms']);arch=int(r.get('archived_at_ms',t));assert start<=t<end
   # Excludes stale foreign-market/resolved caches; value check is identity-consistency only.
   if vals['spotPrice'] is not None:assert vals['spotPrice']>0
   if vals['spotPrice'] and vals['strikePrice'] and vals['spotMinusStrikeBps'] is not None:
    err=abs(10000*(vals['spotPrice']/vals['strikePrice']-1)-vals['spotMinusStrikeBps']);assert err<.001
   rows.append(dict(market_id=mid,asset=asset,sampled=t,archived=arch,available=max(t,arch),kind=kind,
       archive_time_observed=('archived_at_ms' in r),**vals))
  return rows,dict(source=kind,path=path,rows=len(rows),plans=plans,tried=tried)
 return [],dict(source=None,rows=0,tried=tried)

def point_public(rows,t):
 # Read histories without taking a future sample even if insertion timestamps invert.
 candidates=[r for r in rows if r['available']<t]
 return max(candidates,key=lambda r:r['sampled']) if candidates else None

def fill_id(e):return (e['role'],e['side'],e['quote_type'],e['order_hash'])

def augment_market(raw,states,pubs):
 m=raw['identity'];events=raw['events'];ref=raw['result'];by=defaultdict(list)
 for e in events:by[e['event_ms']].append(e)
 index={(r['t'],r['side']):r for r in states};assert len(index)==600
 ordered=sorted(pubs,key=lambda r:(r['available'],r['sampled']));j=0;latest=None
 q={'UP':0.,'DOWN':0.};cum={(role,side):0. for role in ('MAKER','TAKER') for side in q};cost=0.;last={s:None for s in q};seen=set();out=[];maxprefix=0.
 for t in range(m['window_start_ms'],m['window_end_ms'],1000):
  while j<len(ordered) and ordered[j]['available']<t:
   a=ordered[j]
   if latest is None or a['sampled']>latest['sampled']:latest=a
   j+=1
  es=by.get(t,[]);net=q['UP']-q['DOWN'];gross=sum(q.values())
  for side in ['UP','DOWN']:
   r=dict(index[t,side]);sgn=1 if side=='UP' else -1;other='DOWN' if side=='UP' else 'UP';prev=last[side]
   err=max(abs(r['f_net']-net),abs(r['f_gross']-gross),abs(r['f_cost']-cost));maxprefix=max(maxprefix,err);assert err<1e-6
   a={k:None for k in ['ref_t','ref_net','ref_gross','ref_ask','ref_pre_role','ref_spot_distance','delta_net_side','delta_net_over_refgross','delta_same_M','delta_opp_M','delta_opp_T','ask_minus_ref','public_source','public_sample_ms','public_archive_ms','public_age_ms','side_spot_distance','side_spot_return5','side_distance_change_since_ref']}
   a.update(has_reference=prev is not None,public_fresh3=False,public_direction_known=False,reference_same_dominant=False)
   if latest:
    assert latest['sampled']<t and latest['archived']<t
    age=t-latest['sampled'];a.update(public_source=latest['kind'],public_sample_ms=latest['sampled'],public_archive_ms=latest['archived'],public_age_ms=age,public_fresh3=age<=3000)
    a['side_spot_distance']=sgn*latest['spotMinusStrikeBps'] if latest['spotMinusStrikeBps'] is not None else None
    a['side_spot_return5']=sgn*latest['spotReturn5sBps'] if latest['spotReturn5sBps'] is not None else None
    a['public_direction_known']=bool(age<=3000 and latest['spotPrice'] is not None and a['side_spot_distance'] is not None)
   if prev:
    delta=sgn*(net-prev['net']);sm=cum['MAKER',side]-prev['cum']['MAKER',side];om=cum['MAKER',other]-prev['cum']['MAKER',other];ot=cum['TAKER',other]-prev['cum']['TAKER',other]
    assert abs(cum['TAKER',side]-prev['cum']['TAKER',side])<1e-8
    assert abs(delta-(sm-om-ot))<1e-6
    a.update(ref_t=prev['t'],ref_net=prev['net'],ref_gross=prev['gross'],ref_ask=prev['ask'],ref_pre_role=prev['pre_role'],ref_spot_distance=prev['spot_dist'],
       delta_net_side=delta,delta_net_over_refgross=delta/prev['gross'] if prev['gross']>EPS else None,
       delta_same_M=sm,delta_opp_M=om,delta_opp_T=ot,
       reference_same_dominant=sgn*prev['net']>EPS and sgn*net>EPS,
       ask_minus_ref=r['f_ask']-prev['ask'] if r['fresh3'] and prev['ask'] is not None else None)
    if a['public_direction_known'] and prev['spot_dist'] is not None:a['side_distance_change_since_ref']=a['side_spot_distance']-prev['spot_dist']
   r.update({'z_'+k:v for k,v in a.items()});out.append(r)
   ts=[e for e in es if e['role']=='TAKER' and e['side']==side]
   assert r['y_first_observed_T']==any(fill_id(e) not in seen for e in ts)
  for e in es:
   q[e['side']]+=e['shares'];cum[e['role'],e['side']]+=e['shares'];cost+=e['shares']*e['price'];seen.add(fill_id(e))
  for side in ['UP','DOWN']:
   if any(e['role']=='TAKER' and e['side']==side for e in es):
    r=index[t,side];sgn=1 if side=='UP' else -1
    last[side]=dict(t=t,net=q['UP']-q['DOWN'],gross=sum(q.values()),cum=dict(cum),ask=r['f_ask'] if r['fresh3'] else None,
      pre_role=r['f_role'],spot_dist=sgn*latest['spotMinusStrikeBps'] if latest and t-latest['sampled']<=3000 and latest['spotMinusStrikeBps'] is not None else None)
 err=max(abs(q['UP']-ref['up_position_shares']),abs(q['DOWN']-ref['down_position_shares']),abs(cost-ref['buy_notional_usdt']),abs(cost-math.fsum(e['shares']*e['price'] for e in events)))
 assert err<1e-6
 return out,dict(market_id=m['market_id'],asset=m['asset'],block=m['block'],prefixError=maxprefix,finalError=err,
   publicRows=len(pubs),directionKnown=sum(r['z_public_direction_known'] for r in out),referenceRows=sum(r['z_has_reference'] for r in out))

def run(block):
 begin=time.monotonic();assert sha(SRC)==SRC_SHA and sha(TABLE)==TABLE_SHA;OUT.mkdir(exist_ok=True);op=OUT/(block+'.json');rp=OUT/(block+'_ROWS.jsonl.gz');sp=OUT/(block+'_PUBLIC.jsonl.gz')
 if op.exists() or rp.exists() or sp.exists():raise FileExistsError('do not overwrite partial or complete capture')
 source=read(SRC);inp=next(d for d in source['inputs'] if Path(d['path']).stem==block);assert sha(inp['source']['path'])==inp['source']['sha256']
 c=duckdb.connect();c.execute('SET threads=1');c.execute("SET memory_limit='96MB'")
 res=c.execute('SELECT * FROM read_parquet(?) WHERE block=? ORDER BY market_id,t,side',[str(TABLE),block]);cols=[x[0] for x in res.description];states=defaultdict(list)
 for x in res.fetchall():r=dict(zip(cols,x));states[r['market_id']].append(r)
 c.close();rows=[];checks=[];puball=[];sources=[]
 for raw in gzread(inp['source']['path']):
  m=raw['identity'];pubs,ps=get_public(m['market_id'],m['window_start_ms'],m['window_end_ms'],m['asset'])
  out,ck=augment_market(raw,states[m['market_id']],pubs);rows+=out;checks.append(ck);puball+=pubs;sources.append(dict(market_id=m['market_id'],asset=m['asset'],**ps))
  if time.monotonic()-begin>18:raise TimeoutError('bounded stage exceeded; no automatic replay fallback')
 assert len(checks)==24 and len(rows)==14400
 for p,rs in [(rp,rows),(sp,puball)]:
  with gzip.open(p,'xt',encoding='utf-8',compresslevel=3) as f:
   for r in rs:f.write(json.dumps(r,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
 d=dict(block=block,status='FIXED_SOURCE_PREFIX_AUGMENTATION_NOT_PRIVATE_TARGET',rows=dict(path=str(rp),bytes=rp.stat().st_size,sha256=sha(rp)),
  publicSource=dict(path=str(sp),bytes=sp.stat().st_size,sha256=sha(sp)),publicQueries=sources,checks=checks,
  sourceScoreSha256=SRC_SHA,sourceSideTableSha256=TABLE_SHA,seconds=time.monotonic()-begin,newHFT=0,newTraining=0)
 write(op,d)
 print(json.dumps(dict(block=block,seconds=d['seconds'],markets=24,sideRows=len(rows),publicRecords=len(puball),
  coverage={a:dict(markets=sum(r['asset']==a for r in checks),marketsPublic=sum(r['asset']==a and r['publicRows']>0 for r in checks),directionKnownRows=sum(r['directionKnown'] for r in checks if r['asset']==a),sources=dict(Counter(r['source'] for r in sources if r['asset']==a))) for a in ['BTC','ETH']},
  maxPrefixError=max(r['prefixError'] for r in checks),maxFinalError=max(r['finalError'] for r in checks)),ensure_ascii=False))


if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('block',choices=['W1','W2','W3','W4','W5','W6']);run(p.parse_args().block)
