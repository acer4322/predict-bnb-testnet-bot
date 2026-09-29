"""Fixed six-hour-block Target observation: no policy, HFT or model imports.

Select identities first; collect each block once via indexed read-only queries.
Analyze executed-order size distributions, full cashflow and realized behavior.
Original requested sizes/private adaptation intent remain unknown.
"""
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime,timezone,timedelta
import argparse,gzip,hashlib,json,math,re,sqlite3,statistics,time

BASE=Path('data/research/r4_v0/p0_provenance_v1')
OUT=BASE/'target_intermediate_size_performance_v1_20260910'
DB=Path('data/target_wallet_official_v1.db')
WALLET='0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03'
TZ=timezone(timedelta(hours=8));EPS=1e-8
BLOCKS=[('W1','2026-09-07T16:30:00+08:00','15'),('W2','2026-09-07T23:30:00+08:00','20/55'),
 ('W3','2026-09-08T11:30:00+08:00','35/70'),('W4','2026-09-08T23:30:00+08:00','35/70'),
 ('W5','2026-09-09T23:30:00+08:00','30/50'),('W6','2026-09-10T17:30:00+08:00','30/55')]
MARKET_Q='''SELECT r.market_id,r.asset,r.title,r.resolved_at_ms,m.window_end_ms
 FROM target_market_results r INDEXED BY idx_target_results_asset_resolved
 JOIN target_markets m ON m.market_id=r.market_id
 WHERE r.asset=? AND r.resolved_at_ms>? AND r.resolved_at_ms<=?
 ORDER BY r.resolved_at_ms LIMIT 100'''
EVENT_Q='''SELECT leg_id,event_ms,observed_at_ms,role,side,quote_type,order_hash,price,shares
 FROM wallet_shadow_target_events INDEXED BY idx_target_events_asset_market_time
 WHERE asset=? AND market_id=? AND wallet=? ORDER BY event_ms,id LIMIT 10001'''
RESULT_Q='''SELECT market_id,asset,winner,resolved_at_ms,fill_count,parent_count,buy_notional_usdt,
 sell_proceeds_usdt,payout_usdt,net_pnl_usdt,net_roi,up_position_shares,down_position_shares,accounting_version
 FROM target_market_results WHERE market_id=?'''


def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()


def write(p,obj):
 with Path(p).open('x',encoding='utf-8') as f:json.dump(obj,f,ensure_ascii=False,indent=2,allow_nan=False)


def read(p,cap=2000000):
 p=Path(p);assert p.stat().st_size<=cap
 return json.loads(p.read_text(encoding='utf-8-sig'))


def ms(s):return int(datetime.fromisoformat(s).timestamp()*1000)

def local(t):return datetime.fromtimestamp(t/1000,TZ).isoformat()

def avg(x):return statistics.mean(x) if x else None

def med(x):return statistics.median(x) if x else None

def is5(title):
 m=re.search(r'(\d{1,2})(?::(\d{2}))?(AM|PM)-(\d{1,2})(?::(\d{2}))?(AM|PM)',title or '')
 if not m:return False
 a,b,c,d,e,f=m.groups();start=(int(a)%12+(12 if c=='PM' else 0))*60+int(b or 0)
 end=(int(d)%12+(12 if f=='PM' else 0))*60+int(e or 0)
 return (end-start)%1440==5


def connect(seconds):
 c=sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)
 c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('PRAGMA cache_size=-4096')
 deadline=time.monotonic()+seconds;c.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
 c.execute('BEGIN');return c


def select():
 OUT.mkdir(exist_ok=True);p=OUT/'COHORT.json'
 if p.exists():raise FileExistsError(str(p))
 c=connect(6);records=[];windows=[];plans=[]
 try:
  for code,st,anchor in BLOCKS:
   start=ms(st);end=start+3600000
   w=dict(block=code,start_ms=start,end_ms=end,start_local=local(start),end_local=local(end),anchor_observed_tiers=anchor)
   for a in ('BTC','ETH'):
    qp=[tuple(x) for x in c.execute('EXPLAIN QUERY PLAN '+MARKET_Q,(a,start,end+900000))]
    assert not any('SCAN r' in str(x) for x in qp)
    candidates=[dict(x) for x in c.execute(MARKET_Q,(a,start,end+900000))];assert len(candidates)<100
    selected=[r for r in candidates if r['window_end_ms'] is not None and start<=r['window_end_ms']-300000<end and is5(r['title'])]
    assert len(selected)==len({r['window_end_ms'] for r in selected})
    ends=set(r['window_end_ms'] for r in selected);expected=set(range(start+300000,end+1,300000))
    assert ends<=expected
    w[a]=dict(expected=12,available=len(selected),missing_ends=[local(t) for t in sorted(expected-ends)])
    for r in selected:
     records.append(r|dict(block=code,window_start_ms=r['window_end_ms']-300000))
    if not plans:plans=qp
   windows.append(w)
 finally:c.rollback();c.close()
 d=dict(version='TARGET_SIZE_ADAPTATION_FIXED_COHORT_V1',created_at=datetime.now(TZ).isoformat(),
   blocks=windows,markets=sorted(records,key=lambda r:(r['block'],r['asset'],r['window_start_ms'])),
   expected_asset_markets=144,available_asset_markets=len(records),
   outcome_fields_read_for_selection=False,size_based_market_exclusion=False,query_plan=plans,
   limitations='Only identities with recorded resolved results can be enumerated. Missing scheduled windows are unknown, not inactivity.')
 write(p,d);print(json.dumps({k:d[k] for k in ('blocks','expected_asset_markets','available_asset_markets')},ensure_ascii=False))


def market_metrics(m,ev,res):
 assert res['asset']==m['asset'] and res['winner'] in ('UP','DOWN')
 assert len(ev)==res['fill_count'] and len({r['leg_id'] for r in ev})==len(ev)
 start=m['window_start_ms'];end=m['window_end_ms'];by=defaultdict(list);parents={}
 buy=sell=0.;inv={'UP':0.,'DOWN':0.};maker_buy=taker_buy=late_buy=0.;late_fills=0
 for e in ev:
  assert start<=e['event_ms']<end and e['role'] in ('MAKER','TAKER') and e['side'] in inv and e['quote_type'] in ('BID','ASK')
  assert e['order_hash'] and math.isfinite(e['shares']) and e['shares']>0 and 0<e['price']<1
  by[e['event_ms']].append(e);key=(e['role'],e['side'],e['quote_type'],e['order_hash'])
  p=parents.setdefault(key,dict(q=0.,cash=0.,legs=0,minp=e['price'],maxp=e['price']))
  p['q']+=e['shares'];p['cash']+=e['shares']*e['price'];p['legs']+=1
 assert len(parents)==res['parent_count']
 floor_improve=risk_add=partial=neutral=cross=0;gapratios=[];pure_taker_service=pure_taker_add=0
 worst_floor=0.;cut_inv={'UP':0.,'DOWN':0.};cut_cost=0.;routebatch=Counter();phasecash=[0.]*5
 for t,es in sorted(by.items()):
  oldnet=inv['UP']-inv['DOWN'];oldfloor=min(inv.values())-(buy-sell);oldbest=max(inv.values())-(buy-sell)
  routes={e['role'] for e in es};route=next(iter(routes)) if len(routes)==1 else 'MIXED';routebatch[route]+=1
  buys=all(e['quote_type']=='BID' for e in es);sides={e['side'] for e in es}
  for e in es:
   q=e['shares'];cash=q*e['price'];sgn=1 if e['quote_type']=='BID' else -1
   inv[e['side']]+=sgn*q
   if sgn==1:
    buy+=cash;maker_buy+=cash if e['role']=='MAKER' else 0.;taker_buy+=cash if e['role']=='TAKER' else 0.
    phasecash[min(4,(t-start)//60000)]+=cash
    if t>=start+120000:late_buy+=cash
   else:sell+=cash
   if t>=start+120000:late_fills+=1
  floor=min(inv.values())-(buy-sell);best=max(inv.values())-(buy-sell);newnet=inv['UP']-inv['DOWN']
  worst_floor=min(worst_floor,floor)
  fi=floor>oldfloor+EPS;ra=floor<oldfloor-EPS and best>oldbest+EPS
  floor_improve+=fi;risk_add+=ra;pure_taker_service+=fi and route=='TAKER';pure_taker_add+=ra and route=='TAKER'
  if buys and len(sides)==1 and abs(oldnet)>EPS and next(iter(sides))==('DOWN' if oldnet>0 else 'UP'):
   q=sum(e['shares'] for e in es);ratio=q/abs(oldnet);gapratios.append(ratio)
   partial+=ratio<1-1e-8;neutral+=abs(newnet)<=EPS;cross+=oldnet*newnet<0
  if t<start+120000:cut_inv=dict(inv);cut_cost=buy-sell
 cost=buy-sell;up=inv['UP']-cost;down=inv['DOWN']-cost;payout=inv[res['winner']];pnl=payout-cost
 errs={'buy':abs(buy-res['buy_notional_usdt']),'sell':abs(sell-res['sell_proceeds_usdt']),
 'upQty':abs(inv['UP']-res['up_position_shares']),'downQty':abs(inv['DOWN']-res['down_position_shares']),
 'payout':abs(payout-res['payout_usdt']),'pnl':abs(pnl-res['net_pnl_usdt'])}
 assert max(errs.values())<1e-6,dict(market=m['market_id'],errors=errs)
 mq=[p['q'] for k,p in parents.items() if k[0]=='MAKER' and k[2]=='BID'];hist=Counter(round(q,6) for q in mq)
 modes=sorted(hist.items(),key=lambda x:(-x[1],x[0]));makerlegs=sum(e['role']=='MAKER' for e in ev)
 cut_up=cut_inv['UP']-cut_cost;cut_down=cut_inv['DOWN']-cut_cost
 return dict(market_id=m['market_id'],asset=m['asset'],block=m['block'],start_ms=start,end_ms=end,start_local=local(start),
   winner=res['winner'],accounting_version=res['accounting_version'],traded=bool(ev),gross_pnl=pnl,positive=pnl>EPS,negative=pnl<-EPS,
   buy_turnover=buy,sell_proceeds=sell,turnover_return=pnl/buy if buy>EPS else None,
   up_endpoint=up,down_endpoint=down,adverse_endpoint=min(up,down),favorable_endpoint=max(up,down),
   terminal_floor_to_turnover=min(up,down)/buy if buy>EPS else None,worst_path_floor_to_turnover=worst_floor/buy if buy>EPS else None,
   terminal_abs_net=abs(inv['UP']-inv['DOWN']),terminal_relative_abs_net=abs(inv['UP']-inv['DOWN'])/sum(inv.values()) if sum(inv.values())>EPS else None,
   native_target_events=len(ev),parent_orders=len(parents),maker_filled_orders=len(mq),maker_fill_legs=makerlegs,taker_fill_legs=len(ev)-makerlegs,
   maker_mean_filled_qty=avg(mq),maker_median_filled_qty=med(mq),maker_modes=[{'qty':q,'n':n,'fraction':n/len(mq)} for q,n in modes[:8]],
   maker_qty_histogram=[{'qty':q,'n':n} for q,n in sorted(hist.items())],
   maker_low07_orders=sum(p['cash']/p['q']<.07-EPS for k,p in parents.items() if k[0]=='MAKER' and k[2]=='BID'),
   maker_multifill_orders=sum(p['legs']>1 for k,p in parents.items() if k[0]=='MAKER' and k[2]=='BID'),
   maker_buy_cash=maker_buy,taker_buy_cash=taker_buy,taker_buy_fraction=taker_buy/buy if buy>EPS else None,
   late_buy_cash=late_buy,late_buy_fraction=late_buy/buy if buy>EPS else None,phase_buy_cash=phasecash,
   late_fill_legs=late_fills,last_fill_remaining=(end-max(by))/1000 if by else None,
   event_batches=len(by),route_batches=dict(routebatch),floor_improvement_batches=floor_improve,risk_addition_batches=risk_add,
   floor_improvement_event_fraction=floor_improve/len(by) if by else None,
   pure_taker_floor_improvements=pure_taker_service,pure_taker_risk_additions=pure_taker_add,
   weak_buy_batches=len(gapratios),weak_gap_fraction_median=med(gapratios),partial_weak_buy_batches=partial,neutral_weak_buy_batches=neutral,crossed_weak_buy_batches=cross,
   at120_up_endpoint=cut_up,at120_down_endpoint=cut_down,
   late_fixed_winner_endpoint_delta=pnl-(cut_up if res['winner']=='UP' else cut_down),
   late_fixed_up_delta=up-cut_up,late_fixed_down_delta=down-cut_down,
   max_source_reconciliation_error=max(errs.values()),original_requested_qty_known=False)


def collect(block):
 cohort=read(OUT/'COHORT.json');op=OUT/(block+'.json');rp=OUT/(block+'_SOURCE.jsonl.gz')
 if op.exists() or rp.exists():raise FileExistsError('block already captured; never silently rerun')
 selected=[m for m in cohort['markets'] if m['block']==block];c=connect(10);rows=[];rawrows=[];start=time.monotonic();plans=[]
 try:
  for m in selected:
   mid=m['market_id'];asset=m['asset'];ev=[dict(r) for r in c.execute(EVENT_Q,(asset,mid,WALLET))]
   assert len(ev)<10001,'row cap reached, cannot score truncated market'
   res=dict(c.execute(RESULT_Q,(mid,)).fetchone());row=market_metrics(m,ev,res);rows.append(row)
   rawrows.append(dict(identity=m,events=ev,result=res))
   if not plans:
    plans=[tuple(x) for x in c.execute('EXPLAIN QUERY PLAN '+EVENT_Q,(asset,mid,WALLET))]
    assert not any('SCAN wallet_shadow' in str(x) for x in plans)
 finally:c.rollback();c.close()
 with gzip.open(rp,'wt',encoding='utf-8',compresslevel=3) as f:
  for r in rawrows:f.write(json.dumps(r,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
 d=dict(block=block,cohort_sha256=sha(OUT/'COHORT.json'),rows=rows,source=dict(path=str(rp),bytes=rp.stat().st_size,sha256=sha(rp)),
 query_plan=plans,seconds=time.monotonic()-start,newHFT=0,newTraining=0)
 write(op,d)
 print(json.dumps(dict(block=block,markets=len(rows),seconds=d['seconds'],source=d['source'],
   summaries={a:summary([r for r in rows if r['asset']==a]) for a in ('BTC','ETH')}),ensure_ascii=False))


def wilson(k,n):
 if not n:return None
 z=1.95996398454;ph=k/n;den=1+z*z/n;center=(ph+z*z/(2*n))/den;half=z*math.sqrt(ph*(1-ph)/n+z*z/(4*n*n))/den
 return [max(0,center-half),min(1,center+half)]


def summary(rows):
 pnl=[r['gross_pnl'] for r in rows];positive=[x for x in pnl if x>EPS];negative=[x for x in pnl if x<-EPS]
 hist=Counter();medkey=lambda k:med([r[k] for r in rows if r.get(k) is not None])
 for r in rows:
  for h in r['maker_qty_histogram']:hist[h['qty']]+=h['n']
 modes=sorted(hist.items(),key=lambda x:(-x[1],x[0]))
 buy=sum(r['buy_turnover'] for r in rows);total=sum(pnl);largest=max(pnl,default=0)
 nav=high=dd=0
 for r in sorted(rows,key=lambda r:r['start_ms']):
  nav+=r['gross_pnl'];high=max(high,nav);dd=max(dd,high-nav)
 return dict(markets=len(rows),traded=sum(r['traded'] for r in rows),wins=len(positive),losses=len(negative),zero=len(rows)-len(positive)-len(negative),
 win_rate=len(positive)/len(rows) if rows else None,illustrative_wilson95=wilson(len(positive),len(rows)),
 winners=dict(Counter(r['winner'] for r in rows)),gross_pnl=total,median_pnl=med(pnl),buy_turnover=buy,
 aggregate_turnover_return=total/buy if buy>EPS else None,median_turnover_return=medkey('turnover_return'),
 median_loss_turnover_return=med([r['turnover_return'] for r in rows if r['negative'] and r['turnover_return'] is not None]),
 largest_win=largest,leave_best_out=total-largest,worst_pnl=min(pnl,default=None),within_block_drawdown=dd,
 profit_factor=sum(positive)/-sum(negative) if negative else None,
 maker_orders=sum(r['maker_filled_orders'] for r in rows),top_maker_modes=[{'qty':q,'n':n} for q,n in modes[:6]],
 median_maker_order_count=medkey('maker_filled_orders'),median_maker_qty=medkey('maker_median_filled_qty'),
 median_taker_buy_fraction=medkey('taker_buy_fraction'),aggregate_taker_buy_fraction=sum(r['taker_buy_cash'] for r in rows)/buy if buy>EPS else None,
 median_late_buy_fraction=medkey('late_buy_fraction'),median_last_fill_remaining=medkey('last_fill_remaining'),
 median_terminal_floor_to_turnover=medkey('terminal_floor_to_turnover'),median_worst_path_floor_to_turnover=medkey('worst_path_floor_to_turnover'),
 median_terminal_relative_abs_net=medkey('terminal_relative_abs_net'),median_floor_improvement_event_fraction=medkey('floor_improvement_event_fraction'),
 median_weak_gap_fraction=medkey('weak_gap_fraction_median'),
 weak_batches=sum(r['weak_buy_batches'] for r in rows),weak_exact_neutral=sum(r['neutral_weak_buy_batches'] for r in rows),
 taker_floor_events=sum(r['pure_taker_floor_improvements'] for r in rows),taker_risk_events=sum(r['pure_taker_risk_additions'] for r in rows),
 max_source_error=max([r['max_source_reconciliation_error'] for r in rows],default=0))


def finalize():
 op=OUT/'SCORE.json'
 if op.exists():raise FileExistsError(str(op))
 cohort=read(OUT/'COHORT.json');rows=[];inputs=[];groups=[];halves=[]
 for window in cohort['blocks']:
  code=window['block'];p=OUT/(code+'.json');d=read(p);assert d['cohort_sha256']==sha(OUT/'COHORT.json');assert sha(d['source']['path'])==d['source']['sha256']
  rows+=d['rows'];inputs.append(dict(path=str(p),sha256=sha(p),source=d['source']))
  for asset in ('BTC','ETH'):
   rs=[r for r in d['rows'] if r['asset']==asset];s=summary(rs);s.update(block=code,asset=asset,start=window['start_local'],end=window['end_local'],expected=12,missing_ends=window[asset]['missing_ends']);groups.append(s)
   for h,(lo,hi) in enumerate([(window['start_ms'],window['start_ms']+1800000),(window['start_ms']+1800000,window['end_ms'])]):
    rrs=[r for r in rs if lo<=r['start_ms']<hi];sm=summary(rrs);sm.update(block=code,asset=asset,half=h+1);halves.append(sm)
 assert len(rows)==cohort['available_asset_markets']==len({(r['asset'],r['market_id']) for r in rows})
 # Independent algebra on stored outcomes; no trading cost model invented.
 for r in rows:
  assert abs(r['gross_pnl']-(r['up_endpoint'] if r['winner']=='UP' else r['down_endpoint']))<1e-6
  assert abs(r['buy_turnover']-r['maker_buy_cash']-r['taker_buy_cash'])<1e-6
  assert abs(sum(r['phase_buy_cash'])-r['buy_turnover'])<1e-6
 d=dict(version='TARGET_INTERMEDIATE_SIZE_PERFORMANCE_V1',verdict='DESCRIPTIVE_FIXED_BLOCK_OBSERVATION_NOT_OPTIMAL_SIZE_OR_CAUSAL_ADAPTATION',
  expected_asset_markets=144,observed_asset_markets=len(rows),groups=groups,halves=halves,rows=rows,inputs=inputs,
  cohort_sha256=sha(OUT/'COHORT.json'),accounting_versions=sorted({r['accounting_version'] for r in rows}),
  no_trade_markets=[dict(asset=r['asset'],market_id=r['market_id'],block=r['block']) for r in rows if not r['traded']],
  newHFT=0,newTraining=0,policyChanges=0,
  limitations=['Six calendar blocks are selected around earlier size witnesses, not representative random samples or exact switch points.',
   'Each nominal block has only12 markets; sequential dependence and changed market direction/opportunity confound size effects.',
   'Wilson intervals assume independent binomial trials and are only illustrative; no formal change-point or significance test.',
   'Maker size modes are retrospective cumulative fills per observed order, not original requested qty or proven private parameter.',
   'Observed outcomes lack explicit full fees; gross/buy-turnover is not capital-at-risk ROI.',
   'Geometry improvements are not identified economic Repair, unfilled orders/cancellation details unavailable.',
   'Clock-aligned ETH comparison cannot fully control different asset returns, liquidity, strategy and sizes.',
   'Source archive consistency is not independent verification of completeness or settlement oracle truth.'])
 write(op,d);print(json.dumps(dict(output=str(op),bytes=op.stat().st_size,sha256=sha(op),expected=144,observed=len(rows),groups=groups,accounting=d['accounting_versions']),ensure_ascii=False))


if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('stage',choices=['select','collect','finalize']);a.add_argument('--block',choices=[b[0] for b in BLOCKS]);args=a.parse_args()
 if args.stage=='select':select()
 elif args.stage=='collect':collect(args.block)
 else:finalize()
