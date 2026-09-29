from __future__ import annotations

import bisect, importlib.util, json, math, os, sqlite3, statistics, sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import execution_realism_guard_v1 as execution_guard

import joblib
import numpy as np
import pandas as pd
from predict_bot.core import taker_fee
import subprocess

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
BOOK_DB=ROOT/'data'/'wallet_maker_book_inference.db'
TARGET_DB=ROOT/'data'/'target_wallet_official_v1.db'
PASSIVE_ART=OUT/'post_excursion_passive_repair_1s_ebm_v1.joblib'
MAKER_UP_ART=OUT/'target_general_maker_up_core_book_v1.joblib'
MAKER_DN_ART=OUT/'target_general_maker_down_core_book_v1.joblib'
TAKER1_ART=OUT/'frozen_hazard_1s_full.joblib'
TAKER3_ART=OUT/'frozen_hazard_3s_full.joblib'
SIDE_ART=OUT/'frozen_side_full.joblib'
EFFECT_ART=OUT/'frozen_effect_full.joblib'
BURST_UP_ART=OUT/'target_maker_up_burst_multi_v1.joblib'
BURST_DN_ART=OUT/'target_maker_down_burst_multi_v1.joblib'
CORR_UP_ART=OUT/'target_maker_student_state_corrective_up_v1.joblib'
CORR_DN_ART=OUT/'target_maker_student_state_corrective_down_v1.joblib'
QUOTE_AGG_ART=OUT/'target_maker_quote_aggressive_v1.joblib'
QUOTE_DEEP_ART=OUT/'target_maker_quote_deep_conditional_v1.joblib'
TIMING_ART=OUT/'target_maker_execution_timing_v4_runtime_safe.joblib'
RESIDUAL_PASSIVE_ART=OUT/'residual_passive_repair_1s_hgb_fast_v0.joblib'
RESIDUAL_WAKE_CAL_ART=OUT/'student_state_residual_wake_p3_platt_hft_v1.joblib'
TRAIN_CUTOFF=1787111400000
SHARES=18.0
FEE_BPS=200
EPS=1e-9
RNG_SEED=20260820
START=int(os.environ.get('CTRL_START','0') or 0)
COUNT=int(os.environ.get('CTRL_COUNT','0') or 0)
MODE=os.environ.get('CTRL_MODE','PROMOTED').upper()
SUFFIX=os.environ.get('CTRL_SUFFIX','')
FILL_PROXY='HFTBACKTEST_EXECUTION_TAPE_V1'
HFT_ENTRY_LATENCY_MS=int(os.environ.get('HFT_ENTRY_LATENCY_MS','1092') or 1092)
HFT_RESPONSE_LATENCY_MS=int(os.environ.get('HFT_RESPONSE_LATENCY_MS','273') or 273)
HFT_QUEUE_MODEL=os.environ.get('HFT_QUEUE_MODEL','risk').lower()
HFT_TRADE_OFFSET=os.environ.get('HFT_TRADE_OFFSET','mid').lower()
_HFT=None
BURST_MODE=os.environ.get('BURST_MODE','EBM').upper()
CORRECTIVE_MODE=os.environ.get('CORRECTIVE_MODE','EBM').upper()
QUOTE_MODE=os.environ.get('QUOTE_MODE','EBM').upper()
RESIDUAL_MODE=os.environ.get('RESIDUAL_MODE','OFF').upper()
RESIDUAL_WAKE_MODE=os.environ.get('RESIDUAL_WAKE_MODE','CALIBRATED').upper()
RESIDUAL_MIN_HOLD_MS=int(os.environ.get('RESIDUAL_MIN_HOLD_MS','3000') or 3000)
PREFIX=OUT/f'target_blind_promoted_controller_closed_loop_v7_hft_candidate_v1{SUFFIX}'
REPORT=Path(str(PREFIX)+'_report.json')
MARKETS=Path(str(PREFIX)+'_markets.csv')
ACTIONS=Path(str(PREFIX)+'_actions.csv')
STATES=Path(str(PREFIX)+'_states.csv')
PLACEMENTS=Path(str(PREFIX)+'_placements.csv')

P=ROOT/'tools'/'bridge_target_reentry_hazard_to_our_open_counterfactual_v0.py'
spec=importlib.util.spec_from_file_location('closed_loop_bridge',P); br=importlib.util.module_from_spec(spec); assert spec and spec.loader
sys.modules[spec.name]=br; spec.loader.exec_module(br)
base=br.base; mod=br.mod; coord=br.coord


def ro(p:Path):
 c=sqlite3.connect(f'file:{p.resolve().as_posix()}?mode=ro',uri=True,timeout=30); c.row_factory=sqlite3.Row; c.execute('pragma query_only=on'); return c

def q(xs,p):
 ys=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not ys:return None
 if len(ys)==1:return ys[0]
 z=(len(ys)-1)*p; lo=int(math.floor(z)); hi=int(math.ceil(z)); w=z-lo
 return ys[lo]*(1-w)+ys[hi]*w

def stats(xs):
 ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 return {'n':len(ys),'mean':statistics.mean(ys) if ys else None,'median':statistics.median(ys) if ys else None,'p25':q(ys,.25),'p75':q(ys,.75),'p90':q(ys,.9),'sum':sum(ys) if ys else 0.0}

def fast_prob(artifact):
 model=artifact['model']; features=list(artifact['features']); kb,fm=model._bin_mapper.make_known_categories_bitsets(); trees=[it[0] for it in model._predictors]; b0=float(model._baseline_prediction[0,0])
 def pred(raw):
  x=np.asarray([[float(raw.get(f,math.nan)) if raw.get(f) is not None else math.nan for f in features]],dtype=float); z=b0
  for tree in trees:z+=float(tree.predict(x,known_cat_bitsets=kb,f_idx_map=fm,n_threads=1)[0])
  if z>=0:return 1/(1+math.exp(-z))
  ez=math.exp(z); return ez/(1+ez)
 return pred

def _numeric_array(artifact,raw):
 vals=[]
 for f in artifact['features']:
  v=raw.get(f,math.nan)
  try: vals.append(float(v) if v is not None else math.nan)
  except Exception: vals.append(math.nan)
 return np.asarray([vals],dtype=float)
def model_prob(artifact,raw):
 return float(artifact['model'].predict_proba(_numeric_array(artifact,raw))[0,1])
def model_class_probs(artifact,raw):
 p=artifact['model'].predict_proba(_numeric_array(artifact,raw))[0]
 return list(map(str,artifact['model'].classes_)),np.asarray(p,float)

def endmap(book):
 out={}
 for r in book.execute('select market_id,window_end_ms from maker_book_inference_markets where window_end_ms is not null order by market_id'):out.setdefault(int(r['window_end_ms']),int(r['market_id']))
 return out

def winners_by_end(target):
 return {int(r['window_end_ms']):str(r['winner']) for r in target.execute("select window_end_ms,winner from target_markets where asset='BTC' and winner in ('UP','DOWN') and window_end_ms is not null")}

def placement_features(places,cp):
 if not places:return {'last_place_age_ms':math.nan,'last_up_place_age_ms':math.nan,'last_down_place_age_ms':math.nan,'placements_1s':0.,'placements_5s':0.,'placements_10s':0.,'up_placements_5s':0.,'down_placements_5s':0.,'up_placements_10s':0.,'down_placements_10s':0.,'placement_side_balance_5s':0.,'placement_side_balance_10s':0.,'placement_side_streak':0.}
 xs=[x for x in places if int(x['at_ms'])<=cp]
 if not xs:return placement_features([],cp)
 times=[int(x['at_ms']) for x in xs]; last=xs[-1]; lu=ld=None; streak=0; ls=str(last['side'])
 for x in reversed(xs):
  if x['side']=='UP' and lu is None:lu=int(x['at_ms'])
  if x['side']=='DOWN' and ld is None:ld=int(x['at_ms'])
  if x['side']==ls:streak+=1
  elif streak:break
 def cnt(w):
  z=[x for x in xs if int(x['at_ms'])>cp-w]; u=sum(x['side']=='UP' for x in z); d=len(z)-u; return len(z),u,d
 n1,u1,d1=cnt(1000);n5,u5,d5=cnt(5000);n10,u10,d10=cnt(10000)
 bal=lambda u,d:(u-d)/(u+d) if u+d else 0.
 return {'last_place_age_ms':float(cp-int(last['at_ms'])),'last_up_place_age_ms':float(cp-lu) if lu is not None else math.nan,'last_down_place_age_ms':float(cp-ld) if ld is not None else math.nan,'placements_1s':float(n1),'placements_5s':float(n5),'placements_10s':float(n10),'up_placements_5s':float(u5),'down_placements_5s':float(d5),'up_placements_10s':float(u10),'down_placements_10s':float(d10),'placement_side_balance_5s':bal(u5,d5),'placement_side_balance_10s':bal(u10,d10),'placement_side_streak':float(streak)}

def quote(snapshot,side,opp_orders,offset_ticks=1):
 tick=mod.maker_ebm._quote_tick(snapshot,side,int(offset_ticks))
 if tick is None:return None
 tick=int(tick); price=round(tick*mod.maker_ebm.GRID,2)
 if opp_orders:
  mx=max(float(o.price) for o in opp_orders)
  while price+mx>mod.maker_ebm.MAX_PAIR_PRICE_SUM+1e-9:
   tick-=1
   if tick<int(round(mod.maker_ebm.MIN_PRICE/mod.maker_ebm.GRID)):return None
   price=round(tick*mod.maker_ebm.GRID,2)
 return tick,price

def maker_totals(sim):
 u=sum(float(f['shares']) for f in sim.maker_fills if f['side']=='UP'); d=sum(float(f['shares']) for f in sim.maker_fills if f['side']=='DOWN'); g=u+d; n=u-d
 return u,d,g,n,(2*min(u,d)/g if g>EPS else 1.0)
def active_geom(sim,cp,side,bf):
 opp='DOWN' if side=='UP' else 'UP'; same=[o for o in sim.orders.values() if o.side==side]; other=[o for o in sim.orders.values() if o.side==opp]
 def one(xs,s):
  if not xs:return 0,math.nan,math.nan
  o=max(xs,key=lambda z:int(z.placed_at_ms)); bid=bf['up_bid'] if s=='UP' else bf['down_bid']; off=(float(bid)-float(o.price))/mod.maker_ebm.GRID
  return len(xs),float(cp-int(o.placed_at_ms)),float(off)
 a,aa,ao=one(same,side); b,ba,bo=one(other,opp); return a,b,aa,ba,ao,bo

def align_num(snapshot,side):
 b=str(snapshot.get('directionBias') or snapshot.get('direction_bias') or 'NEUTRAL').upper()
 if b not in ('UP','DOWN'):return 0.0
 return 1.0 if b==side else -1.0
def vol_level(snapshot):
 return {'NORMAL':0.,'WATCH':1.,'HIGH':2.}.get(str(snapshot.get('volatilityAlert') or snapshot.get('volatility_alert') or 'UNKNOWN').upper(),-1.)

def quote_zone_raw(raw,pf,side):
 up=side=='UP'
 z={**raw,**pf}
 z.update({'side_is_up':float(up),'side_maker_net':float(raw.get('maker_net',0.0))*(1 if up else -1),'side_combined_net':float(raw.get('combined_net',0.0))*(1 if up else -1),'chosen_bid':float(raw.get('up_bid') if up else raw.get('down_bid')),'chosen_ask':float(raw.get('up_ask') if up else raw.get('down_ask')),'chosen_spread_ticks':float(raw.get('up_spread_ticks') if up else raw.get('down_spread_ticks')),'opposite_bid_side':float(raw.get('down_bid') if up else raw.get('up_bid')),'last_same_maker_age_ms':raw.get('last_maker_up_age_ms') if up else raw.get('last_maker_down_age_ms'),'last_opp_maker_age_ms':raw.get('last_maker_down_age_ms') if up else raw.get('last_maker_up_age_ms'),'same_placements_5s':pf.get('up_placements_5s') if up else pf.get('down_placements_5s'),'opp_placements_5s':pf.get('down_placements_5s') if up else pf.get('up_placements_5s'),'same_placements_10s':pf.get('up_placements_10s') if up else pf.get('down_placements_10s'),'opp_placements_10s':pf.get('down_placements_10s') if up else pf.get('up_placements_10s')})
 return z

def choose_quote_zone(raw,pf,side,rng,agg_art,deep_art):
 if QUOTE_MODE!='EBM':return 1,math.nan,math.nan
 z=quote_zone_raw(raw,pf,side); pa=model_prob(agg_art,z)
 if rng.random()<pa:return 0,pa,math.nan
 pd=model_prob(deep_art,z)
 return (2 if rng.random()<pd else 1),pa,pd

class Venue:
 def __init__(self,market_id):
  self.market_id=int(market_id);self.next_num=1;self.key_to_num={};self.prev_cum={};self.pending_takers={}
  self.p=subprocess.Popen([sys.executable,str(ROOT/'tools'/'hftbacktest_venue_worker_v1.py')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1,cwd=str(ROOT))
  self._send({'marketId':self.market_id,'entryLatencyMs':HFT_ENTRY_LATENCY_MS,'responseLatencyMs':HFT_RESPONSE_LATENCY_MS,'queueModel':HFT_QUEUE_MODEL,'tradeOffset':HFT_TRADE_OFFSET});hello=self._recv()
  if not hello.get('ok'):raise RuntimeError(f'venue init failed {hello}')
  self.meta=hello.get('meta') or {};self.current_ms=int(hello.get('currentMs') or 0)
 def _send(self,q):
  self.p.stdin.write(json.dumps(q,separators=(',',':'),allow_nan=True)+'\n');self.p.stdin.flush()
 def _recv(self):
  line=self.p.stdout.readline()
  if not line:
   err=self.p.stderr.read() if self.p.stderr else '';raise RuntimeError(f'venue worker ended rc={self.p.poll()} stderr={err[-2000:]}')
  return json.loads(line)
 def rpc(self,q):
  self._send(q);r=self._recv()
  if not r.get('ok'):raise RuntimeError(f'venue rpc failed {q}: {r}')
  return r
 def advance(self,ms):
  ms=int(ms)
  if ms<=self.current_ms:return
  self._send({'op':'advance','ms':ms});r=self._recv();self.current_ms=int(r.get('currentMs') or self.current_ms)
  if (not r.get('ok')) and self.current_ms < ms:return
  if not r.get('ok'):raise RuntimeError(f'venue advance failed: {r}')
 def submit(self,key,order):
  self.advance(order.placed_at_ms);n=self.next_num;self.next_num+=1;r=self.rpc({'op':'submit','num':n,'side':order.side,'price':float(order.price),'shares':float(order.shares)});rc=int(r['rc'])
  if rc==0:self.key_to_num[key]=n;self.prev_cum[n]=0.0
  return rc,n
 def submit_taker(self,side,max_price,shares,now):
  self.advance(now);n=self.next_num;self.next_num+=1;r=self.rpc({'op':'submit_taker','num':n,'side':side,'maxPrice':float(max_price),'shares':float(shares)});rc=int(r['rc'])
  if rc==0:self.pending_takers[n]={'side':side,'ask':float(max_price),'prev':0.0}
  return rc,n
 def snaps(self,nums):return self.rpc({'op':'snap_many','nums':[int(x) for x in nums]}).get('snaps') or {}
 def cancel(self,key,now):
  n=self.key_to_num.get(key)
  if n is None:return
  self.advance(now);self.rpc({'op':'cancel','num':int(n)})
 def close(self):
  try:
   if self.p.poll() is None:self.rpc({'op':'close'})
  except Exception:pass
  try:self.p.terminate()
  except Exception:pass

def _hft_open(market_id):return Venue(market_id)
def _hft_close():
 global _HFT
 if _HFT is not None:_HFT.close()
 _HFT=None
def _hft_cancel(key):
 if _HFT is not None:_HFT.cancel(key,_HFT.current_ms)

def add_order(sim,snapshot,book_state,side,ns,now,places,meta,market_id,p,reason,offset_ticks=1,p_aggressive=None,p_deep=None,allow_stack=True,bypass_guard=False):
 global _HFT
 same=[o for o in sim.orders.values() if o.side==side];opp='DOWN' if side=='UP' else 'UP';opp_orders=[o for o in sim.orders.values() if o.side==opp];occupied=bool(same)
 if same:
  if not allow_stack or len(same)>=2:return False
  latest=max(same,key=lambda o:int(o.placed_at_ms));age=now-int(latest.placed_at_ms);sec=mod.snapshot_value(snapshot,'seconds_left','secondsLeft');vol=str(snapshot.get('volatilityAlert') or 'NORMAL').upper();context=age<1500 or (sec is not None and 15<float(sec)<=60) or vol in ('WATCH','HIGH')
  if not context and not bypass_guard:return False
  if not bypass_guard:
   _,_,_,net,pc=maker_totals(sim);dom='UP' if net>EPS else 'DOWN' if net<-EPS else None
   if dom==side and pc<.80:
    bias=str(snapshot.get('directionBias') or snapshot.get('direction_bias') or 'NEUTRAL').upper()
    if not (bias in ('UP','DOWN') and bias!=side):return False
 qr=quote(snapshot,side,opp_orders,offset_ticks)
 if qr is None:return False
 tick,price=qr
 while (side,tick) in sim.orders:
  tick-=1
  if tick<int(round(mod.maker_ebm.MIN_PRICE/mod.maker_ebm.GRID)):return False
  price=round(tick*mod.maker_ebm.GRID,2)
 if opp_orders:
  mx=max(float(o.price) for o in opp_orders)
  while price+mx>mod.maker_ebm.MAX_PAIR_PRICE_SUM+1e-9:
   tick-=1
   if tick<int(round(mod.maker_ebm.MIN_PRICE/mod.maker_ebm.GRID)):return False
   price=round(tick*mod.maker_ebm.GRID,2)
 key=(side,tick)
 if now-int(sim.last_closed.get(key,0))<mod.maker_ebm.REFILL_COOLDOWN_MS:return False
 o=mod.SimOrder(key=key,side=side,price_tick=tick,price=price,shares=SHARES,placed_at_ms=now,placed_snapshot_ns=ns);rc,num=_HFT.submit(key,o)
 if rc!=0:return False
 sim.orders[key]=o;sim.placements+=1
 m={'market_id':market_id,'at_ms':now,'side':side,'price':price,'shares':SHARES,'pMaker':p,'reason':reason,'chosenOffsetTicks':int(offset_ticks),'pQuoteAggressive':p_aggressive,'pQuoteDeep':p_deep,'occupied_before':int(occupied),'pre_maker_net':maker_totals(sim)[3],'pre_maker_pc':maker_totals(sim)[4],'hft_order_num':num};meta[key]=m;places.append(dict(m));return True

def update_depletion_meta(order_meta,changes,source_ms):return

def harvest_hft(sim,order_meta,now):
 global _HFT
 rec=[]
 if _HFT is None:return rec
 _HFT.advance(now);keys=list(sim.orders.keys());nums=[_HFT.key_to_num[k] for k in keys if k in _HFT.key_to_num];snaps=_HFT.snaps(nums) if nums else {}
 for key,o in list(sim.orders.items()):
  num=_HFT.key_to_num.get(key)
  if num is None:continue
  snap=snaps.get(str(num)) or {};cum=float(snap.get('cumExecQty') or 0.);old=float(_HFT.prev_cum.get(num,0.))
  if cum>old+EPS:
   delta=cum-old;native=snap.get('execPrice');px=float(o.price)
   if native is not None and math.isfinite(float(native)):px=float(native) if o.side=='UP' else 1.-float(native)
   fm=int((snap.get('exchangeTs') or int(now)*1_000_000)//1_000_000);_,_,_,pre_net,pre_pc=maker_totals(sim)
   if o.side=='UP':sim.up_shares+=delta;sim.up_cost+=delta*px
   else:sim.down_shares+=delta;sim.down_cost+=delta*px
   sim.maker_fills.append({'side':o.side,'price':px,'shares':delta,'at_ms':fm});_HFT.prev_cum[num]=cum;rec.append({'key':key,'meta':order_meta.get(key),'pre_net':pre_net,'pre_pc':pre_pc,'post_net':maker_totals(sim)[3],'proxy':'HFT_ACTUAL_FILL'})
   leaves=snap.get('leavesQty')
   if leaves is not None and float(leaves)>EPS:o.shares=float(leaves)
  if str(snap.get('status') or 'NONE') in {'FILLED','REJECTED','EXPIRED','CANCELED'}:
   sim.orders.pop(key,None);sim.last_closed[key]=int(now)
 return rec

def submit_taker_hft(side,ask,now):
 global _HFT
 rc,num=_HFT.submit_taker(side,min(.99,float(ask)+.02),SHARES,now);return num if rc==0 else None

def harvest_takers(inv,allfills,now):
 global _HFT
 fees=0.;count=0;last=None
 if _HFT is None or not _HFT.pending_takers:return fees,count,last
 _HFT.advance(now);nums=list(_HFT.pending_takers);snaps=_HFT.snaps(nums)
 for num,m in list(_HFT.pending_takers.items()):
  snap=snaps.get(str(num)) or {};cum=float(snap.get('cumExecQty') or 0.);old=float(m['prev'])
  if cum>old+EPS:
   q=cum-old;native=snap.get('execPrice');px=float(m['ask'])
   if native is not None and math.isfinite(float(native)):px=float(native) if m['side']=='UP' else 1.-float(native)
   fm=int((snap.get('exchangeTs') or int(now)*1_000_000)//1_000_000);e={'event_ms':fm,'at_ms':fm,'role':'TAKER','side':m['side'],'price':px,'shares':q};inv.apply(e);allfills.append(e);fees+=taker_fee(q,px,FEE_BPS);count+=1;last=fm;m['prev']=cum
  if str(snap.get('status') or 'NONE') in {'FILLED','REJECTED','EXPIRED','CANCELED'}:_HFT.pending_takers.pop(num,None)
 return fees,count,last

def pnl(fills,winner,with_fee=True):
 sh={'UP':0.,'DOWN':0.}; cost=fees=0.
 for f in fills:
  s=str(f['side']); z=float(f['shares']); px=float(f['price']); sh[s]+=z; cost+=z*px
  if with_fee and str(f.get('role','MAKER'))=='TAKER':fees+=taker_fee(z,px,FEE_BPS)
 return sh[winner]-cost-fees,fees

def majority_hit(fills,winner):
 u=sum(float(f['shares']) for f in fills if f['side']=='UP'); d=sum(float(f['shares']) for f in fills if f['side']=='DOWN')
 if abs(u-d)<=EPS:return None
 return ('UP' if u>d else 'DOWN')==winner

def residual_wake_prob(cal_art,p3):
 p=min(max(float(p3),1e-6),1-1e-6); z=np.array([[math.log(p/(1-p))]],dtype=float)
 return float(cal_art['calibrator'].predict_proba(z)[0,1])

def residual_raw(raw):
 z=dict(raw)
 mg=float(z.get('maker_gross',0.) or 0.); cg=float(z.get('combined_gross',0.) or 0.)
 z['maker_coverage_gap']=1.-float(z.get('maker_paired_coverage',0.) or 0.)
 z['combined_coverage_gap']=1.-float(z.get('combined_paired_coverage',0.) or 0.)
 z['floor_per_maker_gross']=float(z.get('worst_case_floor',0.) or 0.)/mg if abs(mg)>EPS else math.nan
 z['floor_per_combined_gross']=float(z.get('worst_case_floor',0.) or 0.)/cg if abs(cg)>EPS else math.nan
 z['absnet_per_maker_gross']=float(z.get('maker_abs_net',0.) or 0.)/mg if abs(mg)>EPS else math.nan
 z['maker_absnet_velocity_10s_per_gross']=float(z.get('maker_absnet_change_10s',0.) or 0.)/mg if abs(mg)>EPS else math.nan
 z['recent_maker_share_rate_10s']=float(z.get('maker_shares_10s',0.) or 0.)/10.
 return z

def main():
 maker_up=joblib.load(MAKER_UP_ART); maker_dn=joblib.load(MAKER_DN_ART); t1=joblib.load(TAKER1_ART); t3=joblib.load(TAKER3_ART); side_art=joblib.load(SIDE_ART); effect_art=joblib.load(EFFECT_ART); passive=joblib.load(PASSIVE_ART); burst_up=joblib.load(BURST_UP_ART); burst_dn=joblib.load(BURST_DN_ART); corr_up=joblib.load(CORR_UP_ART); corr_dn=joblib.load(CORR_DN_ART); quote_agg=joblib.load(QUOTE_AGG_ART); quote_deep=joblib.load(QUOTE_DEEP_ART); timing_art=joblib.load(TIMING_ART); residual_passive=joblib.load(RESIDUAL_PASSIVE_ART); residual_wake_cal=joblib.load(RESIDUAL_WAKE_CAL_ART)
 p_up=fast_prob(maker_up); p_dn=fast_prob(maker_dn); p_t1=fast_prob(t1); p_t3=fast_prob(t3)
 our=mod.ro(mod.DEFAULT_OUR_DB); book=ro(BOOK_DB); target=ro(TARGET_DB)
 try:
  snaps=mod.load_snapshots(our); emap=endmap(book); wins=winners_by_end(target); models=mod.maker_ebm.load_models(); eligible=[]
  for om,items in snaps.items():
   if not items:continue
   we=int(mod.snapshot_value(dict(items[0]['snapshot']),'window_end_ms','windowEndMs') or 0)
   if we>TRAIN_CUTOFF and we in emap and we in wins:eligible.append((we,int(om)))
  eligible=sorted(eligible)
  forced=os.environ.get('CTRL_MARKET_IDS','').strip()
  if forced:
   wanted=[int(x) for x in forced.split(',') if x.strip()];by_id={int(om):(we,int(om)) for we,om in eligible};chosen=[by_id[mid] for mid in wanted if mid in by_id]
  else:chosen=eligible[START:(START+COUNT if COUNT>0 else None)]
  market_rows=[]; actions=[]; state_rows=[]; placement_rows=[]
  for mi,(we,om) in enumerate(chosen,1):
   global _HFT
   _HFT=_hft_open(om)
   rng=np.random.default_rng((RNG_SEED*1000003 + int(om)) % (2**63-1)); exec_rng=np.random.default_rng((RNG_SEED*2000003 + int(om) + 99173) % (2**63-1)); residual_rng=np.random.default_rng((RNG_SEED*3000017 + int(om) + 7717) % (2**63-1))
   tm=emap[we]; ups=list(book.execute('select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(tm,))); ui=0; state={'bids':{},'asks':{}}; last=None
   sim=mod.Simulator(models,'CUSTOM'); inv=coord.Inventory(); places=[]; order_meta={}; allfills=[]; episode=None; readiness=False; passive_priority_count=taker_count=0; repair_maker_fills=0; excursion_count=0; unresolved_guard_entries=0; residual_wakes=0; residual_recoveries=0; burst_draws=0; burst_placements=0; taker_fee_total=0.; last_taker_ms=-10**18
   for item in snaps[om]:
    now=int(item['decision_ms']); snap=dict(item['snapshot']); ns=int(mod.num(snap.get('timestampNs')) or mod.num(snap.get('timestamp_ns')) or now*1_000_000)
    while ui<len(ups) and int(ups[ui]['source_timestamp_ms'])<=now:
     u=ups[ui]
     if int(u['is_checkpoint']):state={'bids':{float(k):float(v) for k,v in (coord.dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (coord.dec(u['native_asks_z']) or {}).items()}}
     else:
      ch=coord.dec(u['changes_z']) or {}; update_depletion_meta(order_meta,ch,int(u['source_timestamp_ms'])); coord.apply_changes(state,ch)
     last=int(u['source_timestamp_ms']); ui+=1
    before_orders=dict(sim.orders); pre_u,pre_d,pre_g,pre_net,pre_pc=maker_totals(sim); before_fill_n=len(sim.maker_fills)
    prefill_feat=inv.features(now); prefill_cn=float(prefill_feat.pop('_combined_net')); prefill_dom='UP' if prefill_cn>EPS else 'DOWN' if prefill_cn<-EPS else None; prefill_bf=coord.outcome_book(state,prefill_dom); prefill_raw={'seconds_left':(we-now)/1000.,**prefill_feat,**prefill_bf} if prefill_bf is not None else None
    proxy_records=harvest_hft(sim,order_meta,now)
    filled_keys=[k for k in before_orders if k not in sim.orders]
    newmf=sim.maker_fills[before_fill_n:]
    for f in newmf:
     e={'event_ms':int(f['at_ms']),'role':'MAKER','side':str(f['side']),'price':float(f['price']),'shares':float(f['shares'])}; inv.apply(e); allfills.append({**e,'at_ms':e['event_ms']})
    tf,tc,tlast=harvest_takers(inv,allfills,now);taker_fee_total+=tf;taker_count+=tc;last_taker_ms=max(last_taker_ms,tlast if tlast is not None else last_taker_ms)
    # Detect realized OUR excursion from a filled overlapping second-layer order.
    proxy_by_key={r['key']:r for r in proxy_records}
    for k in filled_keys:
     m=order_meta.pop(k,None); rr=proxy_by_key.get(k); local_pre_net=float(rr['pre_net']) if rr else pre_net; local_pre_pc=float(rr['pre_pc']) if rr else pre_pc; local_post_net=float(rr['post_net']) if rr else maker_totals(sim)[3]
     if not m or not int(m.get('occupied_before',0)):continue
     side=str(m['side']); predom='UP' if local_pre_net>EPS else 'DOWN' if local_pre_net<-EPS else None
     if predom==side and abs(local_pre_net)>=SHARES-EPS and abs(local_post_net)>abs(local_pre_net)+1:
      if episode is None or str(episode.get('side'))!=side:
       episode={'kind':'OVERLAP','side':side,'risk_start_ms':now,'risk_pre_abs':abs(local_pre_net),'start_ms':now,'pre_abs':abs(local_pre_net),'start_abs':abs(local_post_net),'expansion':abs(local_post_net)-abs(local_pre_net),'pre_pc':local_pre_pc,'unresolved':False}; readiness=False
      else:
       episode.update({'kind':'OVERLAP','risk_start_ms':now,'risk_pre_abs':abs(local_pre_net),'start_ms':now,'pre_abs':abs(local_pre_net),'start_abs':abs(local_post_net),'expansion':abs(local_post_net)-abs(local_pre_net),'pre_pc':local_pre_pc,'unresolved':False}); readiness=False
      excursion_count+=1; actions.append({'marketId':om,'windowEndMs':we,'atMs':now,'action':'EXCURSION_START','side':side,'p':None,'detail':json.dumps(episode)})
    if last is None or not (0<=now-last<=2000):continue
    feat=inv.features(now); cn=float(feat.pop('_combined_net')); dom='UP' if cn>EPS else 'DOWN' if cn<-EPS else None; bf=coord.outcome_book(state,dom)
    if bf is None:continue
    raw={'seconds_left':(we-now)/1000.,**feat,**bf}; pf=placement_features(places,now); pu_base=float(p_up(raw)); pdn_base=float(p_dn(raw)); pt1=float(p_t1(raw)); pt3=float(p_t3(raw)); corr_raw={**raw,**pf,'pMakerUp':pu_base,'pMakerDown':pdn_base,'pTaker1s':pt1,'pTaker3s':pt3}; pu=model_prob(corr_up,corr_raw) if CORRECTIVE_MODE=='EBM' else pu_base; pdn=model_prob(corr_dn,corr_raw) if CORRECTIVE_MODE=='EBM' else pdn_base; ppass=math.nan; passive_draw=False; episode_active=False
    p_residual_wake=residual_wake_prob(residual_wake_cal,pt3) if RESIDUAL_WAKE_MODE=='CALIBRATED' else pt3
    if RESIDUAL_MODE=='ON' and episode is None:
     mnet=float(raw.get('maker_net',0.) or 0.); mabs=abs(mnet)
     if mabs>1.0 and residual_rng.random()<p_residual_wake:
      es='UP' if mnet>0 else 'DOWN'; mpc=float(raw.get('maker_paired_coverage',0.) or 0.)
      episode={'kind':'RESIDUAL','side':es,'risk_start_ms':now,'risk_pre_abs':mabs,'start_ms':now,'pre_abs':mabs,'start_abs':mabs,'expansion':0.0,'pre_pc':mpc,'unresolved':False}; readiness=True; residual_wakes+=1
      actions.append({'marketId':om,'windowEndMs':we,'atMs':now,'action':'RESIDUAL_ARBITRATION_WAKE','side':es,'p':p_residual_wake,'detail':json.dumps({'rawP3':pt3,'wakeMode':RESIDUAL_WAKE_MODE,'directTaker':False})})
    # Episode ends after convergence or 15s.
    if episode is not None:
     cur_abs=abs(maker_totals(sim)[3])
     kind=str(episode.get('kind','OVERLAP'))
     recovered=((now-int(episode['risk_start_ms'])>=RESIDUAL_MIN_HOLD_MS) and cur_abs<float(episode['start_abs'])-1.0) if kind=='RESIDUAL' else (cur_abs<=float(episode.get('risk_pre_abs',episode['pre_abs']))+1)
     if recovered:
      if kind=='RESIDUAL': residual_recoveries+=1
      actions.append({'marketId':om,'windowEndMs':we,'atMs':now,'action':'RESIDUAL_RECOVERED' if kind=='RESIDUAL' else 'EXCURSION_END','side':episode['side'],'p':None,'detail':'RECOVERED'}); episode=None; readiness=False
     elif now-int(episode.get('risk_start_ms',episode['start_ms']))>15000 and MODE in ('PROMOTED_GUARD','PROMOTED_FULL') and not bool(episode.get('unresolved')):
      episode['unresolved']=True; readiness=True; unresolved_guard_entries+=1
      # High-risk dead-zone guard: cancel dominant-side resting risk and stop further same-side expansion.
      es=str(episode['side'])
      for key,o in list(sim.orders.items()):
       if str(o.side)==es:
        _hft_cancel(key); sim.orders.pop(key,None); sim.last_closed[key]=now; sim.cancels+=1; order_meta.pop(key,None)
      actions.append({'marketId':om,'windowEndMs':we,'atMs':now,'action':'UNRESOLVED_GUARD_ENTER','side':es,'p':None,'detail':'15S_NO_RECOVERY_CANCEL_DOMINANT_AND_LATCH_TAKER'})
     elif now-int(episode.get('risk_start_ms',episode['start_ms']))>15000 and MODE not in ('PROMOTED_GUARD','PROMOTED_FULL'):
      actions.append({'marketId':om,'windowEndMs':we,'atMs':now,'action':'EXCURSION_END','side':episode['side'],'p':None,'detail':'TIMEOUT'}); episode=None; readiness=False
    if MODE in ('PROMOTED','PROMOTED_GUARD','PROMOTED_FULL') and episode is not None:
     episode_active=True; es=str(episode['side']); ac,oc,aa,oa,aoff,ooff=active_geom(sim,now,es,bf)
     prow={**raw,'excursion_side_is_up':float(es=='UP'),'excursion_age_ms':float(now-int(episode['start_ms'])),'excursion_pre_maker_abs_net':float(episode['pre_abs']),'excursion_start_maker_abs_net':float(episode['start_abs']),'excursion_expansion_shares':float(episode['expansion']),'excursion_pre_maker_paired_coverage':float(episode['pre_pc']),'active_same_count':float(ac),'active_opp_count':float(oc),'active_same_age_ms':aa,'active_opp_age_ms':oa,'active_same_offset_ticks':aoff,'active_opp_offset_ticks':ooff,'direction_alignment':align_num(snap,es),'direction_score':float(snap.get('directionScore') or snap.get('direction_score') or 0.),'volatility_level':vol_level(snap),'maker_pressure_same':pu_base if es=='UP' else pdn_base,'maker_pressure_opp':pdn_base if es=='UP' else pu_base,'taker_pressure_1s':pt1,'maker_pressure_gap':(pdn_base-pu_base) if es=='UP' else (pu_base-pdn_base)}
     if str(episode.get('kind','OVERLAP'))=='RESIDUAL':
      ppass=model_prob(residual_passive,residual_raw(raw)); passive_draw=bool(residual_rng.random()<ppass)
     else:
      ppass=model_prob(passive,prow); passive_draw=bool(rng.random()<ppass)
     if not readiness and rng.random()<pt3:readiness=True; actions.append({'marketId':om,'windowEndMs':we,'atMs':now,'action':'TAKER_READINESS_LATCH','side':es,'p':pt3,'detail':''})
     if passive_draw:
      passive_priority_count+=1; opp='DOWN' if es=='UP' else 'UP'; existed=any(o.side==opp for o in sim.orders.values())
      if not existed:
       qo,qpa,qpd=choose_quote_zone(raw,pf,opp,rng,quote_agg,quote_deep); add_order(sim,snap,state,opp,ns,now,places,order_meta,om,pdn if opp=='DOWN' else pu,'PASSIVE_REPAIR_PRIORITY',qo,qpa,qpd,allow_stack=False,bypass_guard=True)
      actions.append({'marketId':om,'windowEndMs':we,'atMs':now,'action':'PASSIVE_REPAIR_PRIORITY','side':opp,'p':ppass,'detail':'KEEP' if existed else 'CREATE_IF_POSSIBLE'})
     if bool(episode.get('unresolved')):
      opp='DOWN' if es=='UP' else 'UP'; existed=any(o.side==opp for o in sim.orders.values())
      if not existed:
       qo,qpa,qpd=choose_quote_zone(raw,pf,opp,rng,quote_agg,quote_deep); add_order(sim,snap,state,opp,ns,now,places,order_meta,om,pdn if opp=='DOWN' else pu,'UNRESOLVED_REPAIR_ONLY',qo,qpa,qpd,allow_stack=False,bypass_guard=True)
    # Active intervention normally waits for passive draw to fail; unresolved guard allows both repair channels concurrently.
    if readiness and (not passive_draw or bool(episode and episode.get('unresolved'))) and rng.random()<pt1 and now-last_taker_ms>=1000:
     classes,probs=model_class_probs(side_art,raw); probs=probs/probs.sum(); chosen_side=str(rng.choice(classes,p=probs)); ecls,eprobs=model_class_probs(effect_art,raw); pred_eff=str(ecls[int(np.argmax(eprobs))]); ask=bf['up_ask'] if chosen_side=='UP' else bf['down_ask']
     if ask is not None and math.isfinite(float(ask)):
      submit_taker_hft(chosen_side,float(ask),now); last_taker_ms=now
      realized=coord.effect_label(cn,chosen_side,SHARES); actions.append({'marketId':om,'windowEndMs':we,'atMs':now,'action':'TAKER_INTERVENE','side':chosen_side,'p':pt1,'detail':json.dumps({'predEffect':pred_eff,'realizedEffect':realized,'p3':pt3,'pPassive':ppass})}); episode=None; readiness=False
    elif MODE in ('GLOBAL_TAKER','PROMOTED_FULL') and episode is None and rng.random()<pt1 and now-last_taker_ms>=1000:
     # STACK_PLUS_GLOBAL_TAKER baseline: mature Taker hazard is allowed globally, no passive arbitration/readiness latch.
     classes,probs=model_class_probs(side_art,raw); probs=probs/probs.sum(); chosen_side=str(rng.choice(classes,p=probs)); ask=bf['up_ask'] if chosen_side=='UP' else bf['down_ask']
     if ask is not None and math.isfinite(float(ask)):
      submit_taker_hft(chosen_side,float(ask),now); last_taker_ms=now
    # Regular Maker hazard actions; passive-priority blocks adding more excursion-side inventory this checkpoint.
    trig_up=bool(rng.random()<pu); trig_dn=bool(rng.random()<pdn)
    blocked_side=str(episode['side']) if (episode is not None and (passive_draw or bool(episode.get('unresolved')))) else None
    if trig_up and blocked_side!='UP':
     qo,qpa,qpd=choose_quote_zone(raw,pf,'UP',rng,quote_agg,quote_deep); made=add_order(sim,snap,state,'UP',ns,now,places,order_meta,om,pu,'MAKER_HAZARD',qo,qpa,qpd)
     if made and BURST_MODE=='EBM':
      braw={**raw,**pf,'hazard_p':pu}; pb=model_prob(burst_up,braw)
      if rng.random()<pb:
       burst_draws+=1
       qo2,qpa2,qpd2=choose_quote_zone(raw,pf,'UP',rng,quote_agg,quote_deep)
       if add_order(sim,snap,state,'UP',ns,now,places,order_meta,om,pb,'MAKER_BURST',qo2,qpa2,qpd2):burst_placements+=1
    if trig_dn and blocked_side!='DOWN':
     qo,qpa,qpd=choose_quote_zone(raw,pf,'DOWN',rng,quote_agg,quote_deep); made=add_order(sim,snap,state,'DOWN',ns,now,places,order_meta,om,pdn,'MAKER_HAZARD',qo,qpa,qpd)
     if made and BURST_MODE=='EBM':
      braw={**raw,**pf,'hazard_p':pdn}; pb=model_prob(burst_dn,braw)
      if rng.random()<pb:
       burst_draws+=1
       qo2,qpa2,qpd2=choose_quote_zone(raw,pf,'DOWN',rng,quote_agg,quote_deep)
       if add_order(sim,snap,state,'DOWN',ns,now,places,order_meta,om,pb,'MAKER_BURST',qo2,qpa2,qpd2):burst_placements+=1
    state_rows.append({'marketId':om,'windowEndMs':we,'atMs':now,'pMakerUpBase':pu_base,'pMakerDownBase':pdn_base,'pMakerUp':pu,'pMakerDown':pdn,'pTaker1s':pt1,'pTaker3s':pt3,'pResidualWake':p_residual_wake,'pPassiveRepair':ppass,'episodeActive':int(episode_active),'episodeKind':str(episode.get('kind')) if episode else None,'readiness':int(readiness),**raw,**pf})
   terminal=int(_HFT.meta['lastReceivedMs']);harvest_hft(sim,order_meta,terminal);tf,tc,tlast=harvest_takers(inv,allfills,terminal);taker_fee_total+=tf;taker_count+=tc;_hft_close()
   for x in places: placement_rows.append({'ourMarketId':om,'targetMarketId':tm,'windowEndMs':we,**x})
   winner=wins[we]; rawp,fees=pnl(allfills,winner,False); netp,_=pnl(allfills,winner,True); pair=mod.fifo_pair([f for f in allfills if f['role']=='MAKER']); mu,md,mg,mn,mpc=maker_totals(sim); hit=majority_hit(allfills,winner)
   market_rows.append({'ourMarketId':om,'targetMarketId':tm,'windowEndMs':we,'winner':winner,'mode':MODE,'pnlBeforeTakerFees':rawp,'takerFees':taker_fee_total,'pnlAfterTakerFees':netp,'positive':int(netp>0),'makerPlacements':sim.placements,'makerFills':len(sim.maker_fills),'takerFills':taker_count,'excursions':excursion_count,'passivePrioritySteps':passive_priority_count,'unresolvedGuardEntries':unresolved_guard_entries,'residualWakes':residual_wakes,'residualRecoveries':residual_recoveries,'burstDraws':burst_draws,'burstPlacements':burst_placements,'makerPlacementsFirst60':sum(int(x['at_ms'])<=we-240000 for x in places),'makerFillsFirst60':sum(int(f['at_ms'])<=we-240000 for f in sim.maker_fills),'finalMakerAbsNet':abs(mn),'makerPairedCoverage':mpc,'pairedShares':pair['pairedShares'],'lockedEdgeUsdt':pair['lockedEdgeUsdt'],'majoritySideHit':None if hit is None else int(hit),'totalGrossShares':sum(float(f['shares']) for f in allfills)})
   if mi%10==0:print(json.dumps({'progress':mi,'markets':len(chosen),'pnl':sum(r['pnlAfterTakerFees'] for r in market_rows),'positiveRate':sum(r['positive'] for r in market_rows)/len(market_rows),'takers':sum(r['takerFills'] for r in market_rows),'excursions':sum(r['excursions'] for r in market_rows)}),flush=True)
  mdf=pd.DataFrame(market_rows); pd.DataFrame(actions).to_csv(ACTIONS,index=False); pd.DataFrame(state_rows).to_csv(STATES,index=False); pd.DataFrame(placement_rows).to_csv(PLACEMENTS,index=False); mdf.to_csv(MARKETS,index=False)
  traded=mdf[mdf.totalGrossShares>0] if len(mdf) else mdf; hitvals=pd.to_numeric(mdf.majoritySideHit,errors='coerce').dropna() if len(mdf) else pd.Series(dtype=float)
  rep={'reportVersion':'TARGET_BLIND_PROMOTED_CONTROLLER_CLOSED_LOOP_V7_HFT_CANDIDATE_V1','researchOnly':True,'liveTradingChanges':False,'runtimeTargetDataAllowed':False,'mode':MODE,'fillProxy':FILL_PROXY,'burstMode':BURST_MODE,'correctiveMode':CORRECTIVE_MODE,'quoteMode':QUOTE_MODE,'residualMode':RESIDUAL_MODE,'residualWakeMode':RESIDUAL_WAKE_MODE,'residualMinHoldMs':RESIDUAL_MIN_HOLD_MS,'trainingCutoffMarketEndMs':TRAIN_CUTOFF,'allMarketsStrictlyPostTraining':bool(len(mdf) and int(mdf.windowEndMs.min())>TRAIN_CUTOFF),'chunk':{'eligibleMarkets':len(eligible),'start':START,'countRequested':COUNT,'chosenMarkets':len(chosen),'suffix':SUFFIX},'policy':{'maker':'student-state corrective EBM calibrates primary pressure; quote-zone EBM maps each new Maker quote to 0/1/2 ticks behind; stable max2 CONTEXT+PAIR80_HEADWIND; conditional Burst EBM may add one second layer','passiveRepair':'overlap episode uses promoted Passive EBM; residual episode uses residual passive-repair HGB and stays open at least 3s before recovery may close it; both keep/create minority quote and suppress dominant stacking on repair-priority draws','taker':'Residual R2: outside episode calibrated frozen-3s repair-wake probability may wake arbitration only (never direct Taker); inside residual/overlap episode frozen 1s remains the only active Taker trigger after readiness, with passive-repair arbitration first','side':'sample frozen SIDE probabilities','effect':'frozen EFFECT scored/logged at intervention, never forces side','sizeShares':SHARES,'execution':f'Maker public-book execution mode={FILL_PROXY}; TIMING_V4_EVER = ever-depletion/pass opportunity gate then independent-RNG conditional timing hazard; Taker current public ask','fees':'200bps Predict taker fee helper; Maker fee/rebate assumed 0','rngSeed':'per-market deterministic policy RNG plus separate execution and residual-arbitration RNG streams','originalOpenSeeds':'NONE; fully self-generated own-state loop'},'outcome':{'markets':len(mdf),'tradedMarkets':len(traded),'pnlBeforeTakerFees':float(mdf.pnlBeforeTakerFees.sum()) if len(mdf) else 0.,'takerFees':float(mdf.takerFees.sum()) if len(mdf) else 0.,'pnlAfterTakerFees':float(mdf.pnlAfterTakerFees.sum()) if len(mdf) else 0.,'pnlPerMarket':stats(mdf.pnlAfterTakerFees.tolist()) if len(mdf) else stats([]),'positiveMarkets':int(mdf.positive.sum()) if len(mdf) else 0,'positiveMarketRate':float(mdf.positive.mean()) if len(mdf) else None,'majoritySideHitRate':float(hitvals.mean()) if len(hitvals) else None},'activity':{'makerPlacements':stats(mdf.makerPlacements.tolist()),'makerFills':stats(mdf.makerFills.tolist()),'takerFills':stats(mdf.takerFills.tolist()),'excursions':stats(mdf.excursions.tolist()),'passivePrioritySteps':stats(mdf.passivePrioritySteps.tolist()),'unresolvedGuardEntries':stats(mdf.unresolvedGuardEntries.tolist()),'residualWakes':stats(mdf.residualWakes.tolist()),'residualRecoveries':stats(mdf.residualRecoveries.tolist()),'burstDraws':stats(mdf.burstDraws.tolist()),'burstPlacements':stats(mdf.burstPlacements.tolist()),'makerPlacementsFirst60':stats(mdf.makerPlacementsFirst60.tolist()),'makerFillsFirst60':stats(mdf.makerFillsFirst60.tolist()),'finalMakerAbsNet':stats(mdf.finalMakerAbsNet.tolist()),'makerPairedCoverage':stats(mdf.makerPairedCoverage.tolist()),'pairedShares':stats(mdf.pairedShares.tolist()),'lockedEdgeUsdt':stats(mdf.lockedEdgeUsdt.tolist()),'totalGrossShares':stats(mdf.totalGrossShares.tolist())},'guards':['No Target action/inventory/future value is used at runtime; Target DB winner is read only after replay for settlement.','All evaluated markets are after Passive EBM training cutoff.','No threshold sweep or profit tuning. Corrective primary probabilities, Taker hazards and conditional burst probabilities are sampled directly.','Fixed 18-share size and public-book execution evidence remain proxies. Residual wake is train-only Platt calibration of frozen p3; 3s minimum episode duration comes from Target residual-repair trajectory plateau, not PnL tuning.','This V1 tests controller self-generation from zero inventory; it intentionally does not replay recorded OPEN_SEED.','Positive-market rate is a downstream research metric, not a teacher-training target.']}
  REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False,indent=2)); return 0
 finally:our.close();book.close();target.close()

if __name__=='__main__':raise SystemExit(main())
