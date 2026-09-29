"""Post-primary sensitivity and independent verification on fixed side table.
Risk/depth and1/10s checks are descriptive robustness, not outcome-tuned rules.
No raw Tape rebuild, policy/ML/HFT or new market lookup.
"""
from pathlib import Path
from collections import Counter,defaultdict
import gzip,json,math,statistics,time
from tools.audit_target_concurrent_route_economics_v1_20260911 import OUT,SRC,SRC_SHA,sha,read,write,gzread,pricebin,spreadbin,balancebin,EPS,mean,median

SCORE_SHA='6e8c24fbd651e648ee480150780826d0f4e701cd94aacb76b8ff425b90362d20'


def contrast(rows,level='RISK',fresh='fresh3'):
 labels=['y_hasT','y_first_observed_T','y_hasM'];sides=[]
 for arm in [0,1]:
  rr=[r for r in rows if r[fresh] and (r['f_ask']<.3-EPS if arm==0 else r['f_ask']>=.7-EPS)]
  if level=='RISK_DEPTH':rr=[r for r in rr if r['f_gap_to_ask_band01'] is not None]
  ns=Counter(r['market_id'] for r in rr);cs={}
  for r in rr:
   f=r['f_floor_to_cost'];fb=0 if f>=0 else 1 if f>=-.05 else 2 if f>=-.2 else 3
   k=(r['block'],r['f_phase'],balancebin(r['f_relative_imbalance']),spreadbin(r['f_spread']),fb)
   if level=='RISK_DEPTH':
    d=r['f_gap_to_ask_band01'];k+=(0 if d<=.25 else 1 if d<=1 else 2,)
   w=1/len(ns)/ns[r['market_id']];c=cs.setdefault(k,dict(w=0.,markets=set(),n=0,sums=Counter()))
   c['w']+=w;c['markets'].add(r['market_id']);c['n']+=1
   for l in labels:c['sums'][l]+=w*r[l]
  sides.append(cs)
 a,b=sides;ks=[k for k in a if k in b and len(a[k]['markets'])>=3 and len(b[k]['markets'])>=3]
 ws={k:min(a[k]['w'],b[k]['w']) for k in ks};mass=sum(ws.values());rates={}
 for l in labels:
  p=sum(ws[k]*a[k]['sums'][l]/a[k]['w'] for k in ks)/mass if mass else None
  q=sum(ws[k]*b[k]['sums'][l]/b[k]['w'] for k in ks)/mass if mass else None
  rates[l]=dict(lowAsk=p,highAsk=q,difference=q-p if mass else None)
 return dict(level=level,freshness=fresh,commonCells=len(ks),commonMass=mass,
     lowCoverage=sum(a[k]['w'] for k in ks),highCoverage=sum(b[k]['w'] for k in ks),rates=rates)


def main():
 began=time.monotonic();score=OUT/'SCORE.json';assert sha(score)==SCORE_SHA and sha(SRC)==SRC_SHA
 d=read(score);source=read(SRC);op=OUT/'SENSITIVITY_AND_INDEPENDENT_CHECK.json'
 if op.exists():raise FileExistsError(str(op))
 rows=[];byblock={};rawblocks={};meta=[];primary_counts=Counter();maxprefix=0.;maxfinal=0.;pricebins=defaultdict(Counter)
 for block in ['W1','W2','W3','W4','W5','W6']:
  saved=read(OUT/(block+'.json'));p=Path(saved['sides']['path']);assert sha(p)==saved['sides']['sha256']
  rr=gzread(p);meta.append(saved['sides']);index={(r['market_id'],r['t'],r['side']):r for r in rr};assert len(index)==len(rr)==14400
  inp=next(x for x in source['inputs'] if Path(x['path']).stem==block);rawpath=Path(inp['source']['path']);assert sha(rawpath)==inp['source']['sha256']
  for raw in gzread(rawpath):
   m=raw['identity'];mid=m['market_id'];es=raw['events'];batches=defaultdict(list)
   for e in es:batches[e['event_ms']].append(e)
   q={'UP':0.,'DOWN':0.};maker={'UP':0.,'DOWN':0.};cost=0.;last={s:None for s in q};atlast={s:0. for s in q};seen=set()
   for t in range(m['window_start_ms'],m['window_end_ms'],1000):
    ev=batches.get(t,[])
    for side in ['UP','DOWN']:
     r=index[mid,t,side];assert r['asset']==m['asset'] and r['block']==block
     err=max(abs(r['f_up']-q['UP']),abs(r['f_down']-q['DOWN']),abs(r['f_cost']-cost));maxprefix=max(maxprefix,err);assert err<1e-6
     ts=[e for e in ev if e['role']=='TAKER' and e['side']==side];ms=[e for e in ev if e['role']=='MAKER' and e['side']==side]
     assert r['y_hasT']==bool(ts) and r['y_hasM']==bool(ms) and r['y_both']==bool(ts and ms)
     assert abs(r['y_Tqty']-math.fsum(e['shares'] for e in ts))<1e-8
     assert abs(r['y_Mcash']-math.fsum(e['shares']*e['price'] for e in ms))<1e-8
     isnew=lambda e:(e['role'],e['side'],e['quote_type'],e['order_hash']) not in seen
     assert r['y_first_observed_T']==any(isnew(e) for e in ts)
     assert r['f_had_same_side_taker']==(last[side] is not None)
     if last[side] is not None:
      assert abs(r['f_maker_qty_since_same_taker']-(maker[side]-atlast[side]))<1e-6
      assert r['f_seconds_since_same_taker']==(t-last[side])/1000
     if r['book_source_ms'] is not None:
      assert max(r['book_source_ms'],r['book_received_ms'],r['book_chain_source_max'],r['book_chain_received_max'])<t
     if r['fresh3']:assert r['book_status']=='OK' and r['f_book_age']<=3000
     if r['y_hasT'] and r['fresh3'] and r['f_role']!='NEUTRAL':
      a=pricebin(r['f_ask']);b=pricebin(r['y_Tvwap']);pricebins[r['asset']+'_'+r['f_role']][a,b]+=1
    for e in ev:
     side=e['side'];q[side]+=e['shares'];cost+=e['price']*e['shares']
     if e['role']=='MAKER':maker[side]+=e['shares']
     seen.add((e['role'],e['side'],e['quote_type'],e['order_hash']))
    for side in q:
     if any(e['role']=='TAKER' and e['side']==side for e in ev):last[side]=t;atlast[side]=maker[side]
   ref=raw['result'];err=max(abs(q['UP']-ref['up_position_shares']),abs(q['DOWN']-ref['down_position_shares']),abs(cost-ref['buy_notional_usdt']))
   assert err<1e-6;maxfinal=max(maxfinal,err)
  rows+=rr;byblock[block]=rr
 assert len(rows)==86400
 sensitivity={};raw_price_checks={};elapsed=[]
 for asset in ['BTC','ETH']:
  for role in ['WEAK','STRONG']:
   rr=[r for r in rows if r['asset']==asset and r['f_role']==role and r['f_cost']>EPS]
   key=asset+'_'+role;sensitivity[key]=dict(
    risk=contrast(rr,'RISK'),riskDepth=contrast(rr,'RISK_DEPTH'),
    oneSecond=contrast(rr,'RISK_DEPTH','fresh1'),tenSecond=contrast(rr,'RISK_DEPTH','fresh10'),
    perBlock=[dict(block=b,result=contrast([r for r in rr if r['block']==b],'RISK_DEPTH')) for b in ['W1','W2','W3','W4','W5','W6']])
   counts=pricebins[key];n=sum(counts.values());raw_price_checks[key]=dict(events=n,
     sameBroadPriceBin=sum(v for (a,b),v in counts.items() if a==b),
     lowToHighOrHighToLow=sum(v for (a,b),v in counts.items() if (a<=1 and b==3) or (a==3 and b<=1)),
     confusion=[dict(priorBin=a,achievedBin=b,events=v) for (a,b),v in sorted(counts.items())])
 # Independent plain sums and newly saved compact table; caller must selectf_ columns explicitly.
 import duckdb
 c=duckdb.connect();c.execute('SET threads=1');c.execute("SET memory_limit='128MB'")
 paths=[str(OUT/(b+'_SIDES.jsonl.gz')).replace('\\','/') for b in ['W1','W2','W3','W4','W5','W6']]
 arr='['+','.join("'"+p+"'" for p in paths)+']';parquet=OUT/'side_states.parquet';assert not parquet.exists()
 c.execute("COPY (SELECT * FROM read_json_auto("+arr+",format='newline_delimited',union_by_name=true)) TO '"+parquet.as_posix()+"' (FORMAT PARQUET,COMPRESSION ZSTD)")
 t=c.execute('SELECT count(*),count(DISTINCT (market_id,t,side)),count(DISTINCT market_id) FROM read_parquet(?)',[str(parquet)]).fetchone();assert t==(86400,86400,144)
 cols=[r[0] for r in c.execute('DESCRIBE SELECT * FROM read_parquet(?)',[str(parquet)]).fetchall()];c.close()
 result=dict(status='PREFIX_LABEL_CASH_CLOCK_PARITY_VERIFIED_FIXED_SENSITIVITIES',primarySha256=SCORE_SHA,
   markets=144,seconds=43200,sideRows=86400,maxIndependentPrefixError=maxprefix,maxIndependentFinalError=maxfinal,
   priceContrasts=sensitivity,priorVersusAchievedPrice=raw_price_checks,
   parquet=dict(path=str(parquet),bytes=parquet.stat().st_size,sha256=sha(parquet),
      features=[x for x in cols if x.startswith('f_')],labels=[x for x in cols if x.startswith('y_')]),
   newHFT=0,newTraining=0,modelFits=0,secondsElapsed=time.monotonic()-began,
   scope='post-primary additionalrisk/depth andclock sensitivity; not a holdout prediction test or causal adjustment guarantee',
   limitations=['NoTaker seconds are confirmed no-observed-execution, not intended HOLD or known pending states.',
     'Maker progress describes same-side confirmed volume, not whether each private duty has succeeded.',
     'Matched state support changes when addingrisk/depth/elapsed controls; sign changes need not be genuine economic effect reversals.',
     'Price state differs from actual execution time; samebroad bin consistency cannot certify private decision-price availability.',
     'No bootstrapped CI or multiple-test inference; quantities/age bins not optimized and are not runtime rules.'])
 write(op,result)
 print(json.dumps(dict(output=str(op),bytes=op.stat().st_size,sha256=sha(op),prefixError=maxprefix,finalError=maxfinal,
    priceContrasts={k:{x:y for x,y in v.items() if x!='perBlock'} for k,v in sensitivity.items()},
    perBlock=[dict(name=k,blocks=[{'block':r['block'],'cells':r['result']['commonCells'],'mass':r['result']['commonMass'],'T':r['result']['rates']['y_hasT']} for r in v['perBlock']]) for k,v in sensitivity.items()],
    priceChecks=raw_price_checks,parquet={k:v for k,v in result['parquet'].items() if k not in ['features','labels']},seconds=result['secondsElapsed']),ensure_ascii=False))


if __name__=='__main__':main()
