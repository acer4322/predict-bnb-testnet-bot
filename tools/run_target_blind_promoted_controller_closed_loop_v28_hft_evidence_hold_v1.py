from __future__ import annotations

import os, sys, math, json, importlib.util, subprocess
from pathlib import Path
from typing import Any
import joblib, numpy as np

os.environ.setdefault('BTC5M_DIAGNOSTIC_OPTIMISTIC_EXECUTION','1')
os.environ.setdefault('FILL_PROXY','QUEUECLEAR_PASS')
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
TOOLS=ROOT/'tools'
if str(TOOLS) not in sys.path: sys.path.insert(0,str(TOOLS))

P=TOOLS/'run_target_blind_promoted_controller_closed_loop_v27_soft_pending_legacy_v1.py'
spec=importlib.util.spec_from_file_location('legacy_v3_hft_base',P)
legacy=importlib.util.module_from_spec(spec); assert spec and spec.loader
sys.modules[spec.name]=legacy; spec.loader.exec_module(legacy)
if os.environ.get('CTRL_OUR_DB'):
    legacy.mod.DEFAULT_OUR_DB=Path(os.environ['CTRL_OUR_DB'])

ENTRY_LATENCY_MS=int(os.environ.get('HFT_ENTRY_LATENCY_MS','1092') or 1092)
RESPONSE_LATENCY_MS=int(os.environ.get('HFT_RESPONSE_LATENCY_MS','273') or 273)
QUEUE_MODEL=os.environ.get('HFT_QUEUE_MODEL','risk')
TRADE_OFFSET=os.environ.get('HFT_TRADE_OFFSET','mid')
EPS=1e-9
TERMINAL={'FILLED','REJECTED','EXPIRED','CANCELED'}
TIMING_ART=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'/'hft_exec_timing_v25_v1.joblib'
TIMING=joblib.load(TIMING_ART); TIMING_MODEL=TIMING['model']; TIMING_FEATURES=list(TIMING['features'])
PENDING_P={}; PENDING_RNG={}; PENDING_STATS={'draws':0,'suppressed':0,'maxP':0.0}
LAST_FILL_SIDE_MS={}; EVIDENCE_HOLD_MS=1000; EVIDENCE_HOLD_STATS={'blocked':0,'checks':0}
PENDING_MODE=os.environ.get('TIMING_PENDING_MODE','OFF').upper()

def timing_prob(row):
    x=np.asarray([[float(row.get(f, math.nan)) if row.get(f) is not None else math.nan for f in TIMING_FEATURES]],dtype=float)
    return float(TIMING_MODEL.predict_proba(x)[0,1])

def pending_rng(mid):
    mid=int(mid)
    if mid not in PENDING_RNG:PENDING_RNG[mid]=np.random.default_rng(2026082600+mid)
    return PENDING_RNG[mid]

class Venue:
    def __init__(self,market_id:int):
        self.market_id=int(market_id); self.next_num=1; self.order_num_by_key={}; self.prev_cum={}; self.meta={}; self.exhausted=False
        self.p=subprocess.Popen([sys.executable,str(TOOLS/'hftbacktest_venue_worker_v1.py')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1,cwd=str(ROOT))
        self._send({'marketId':self.market_id,'entryLatencyMs':ENTRY_LATENCY_MS,'responseLatencyMs':RESPONSE_LATENCY_MS,'queueModel':QUEUE_MODEL,'tradeOffset':TRADE_OFFSET},raw=True)
        hello=self._recv()
        if not hello.get('ok'): raise RuntimeError(f'venue init failed {hello}')
        self.meta=hello.get('meta') or {}; self.current_ms=int(hello.get('currentMs') or 0)
    def _send(self,q,raw=False):
        assert self.p.stdin is not None
        self.p.stdin.write(json.dumps(q,separators=(',',':'),allow_nan=True)+'\n'); self.p.stdin.flush()
    def _recv(self):
        assert self.p.stdout is not None
        line=self.p.stdout.readline()
        if not line:
            err=''
            if self.p.stderr is not None: err=self.p.stderr.read()
            raise RuntimeError(f'venue worker ended rc={self.p.poll()} stderr={err[-4000:]}')
        return json.loads(line)
    def rpc(self,q):
        self._send(q); r=self._recv()
        if not r.get('ok'): raise RuntimeError(f'venue rpc failed {q.get("op")}: {r}')
        return r
    def advance(self,now:int):
        now=int(now)
        if now<=self.current_ms:return
        self._send({'op':'advance','ms':now}); r=self._recv(); self.current_ms=int(r.get('currentMs') or self.current_ms); self.exhausted = self.exhausted or (not bool(r.get('ok')))
    def submit(self,key,order)->int:
        self.advance(int(order.placed_at_ms));
        if self.exhausted:return -1
        num=self.next_num; self.next_num+=1
        r=self.rpc({'op':'submit','num':num,'side':str(order.side),'price':float(order.price),'shares':float(order.shares)}); rc=int(r['rc'])
        if rc==0:self.order_num_by_key[key]=num; self.prev_cum[num]=0.0
        return rc
    def snaps(self,keys):
        nums=[self.order_num_by_key[k] for k in keys if k in self.order_num_by_key]
        if not nums:return {}
        return self.rpc({'op':'snap_many','nums':nums}).get('snaps') or {}
    def cancel_key(self,key,now:int):
        num=self.order_num_by_key.get(key)
        if num is None:return
        self.advance(now); self.rpc({'op':'cancel','num':int(num)})
    def close(self):
        try:
            if self.p.poll() is None:self.rpc({'op':'close'})
        except Exception:pass
        try:self.p.terminate()
        except Exception:pass

venues:dict[int,Venue]={}; sim_venue:dict[int,Venue]={}
venue_stats={'submitted':0,'submitRejects':0,'fillEvents':0,'filledShares':0.0,'terminalZeroFill':0,'markets':0}
EXEC_ROWS=[]
orig_add=legacy.add_order

SOFT_STATS={'adjustments':0,'maxExpectedUp':0.0,'maxExpectedDown':0.0,'sumExpectedUp':0.0,'sumExpectedDown':0.0}
SOFT_MODE=os.environ.get('HFT_SOFT_PENDING_MODE','ON').upper()
def soft_pending_adjust(sim, raw, cu, cd):
    if SOFT_MODE!='ON': return cu, cd, raw
    eu=0.0; ed=0.0
    for k,o in sim.orders.items():
        pp=float(PENDING_P.get((id(sim),k),0.0) or 0.0)
        rem=max(0.0,float(getattr(o,'shares',0.0) or 0.0))
        if str(o.side)=='UP': eu += pp*rem
        else: ed += pp*rem
    fu=max(0.0,1.0-eu/legacy.SHARES); fd=max(0.0,1.0-ed/legacy.SHARES)
    SOFT_STATS['adjustments']+=1; SOFT_STATS['maxExpectedUp']=max(SOFT_STATS['maxExpectedUp'],eu); SOFT_STATS['maxExpectedDown']=max(SOFT_STATS['maxExpectedDown'],ed); SOFT_STATS['sumExpectedUp']+=eu; SOFT_STATS['sumExpectedDown']+=ed
    rr=dict(raw); rr['expected_pending_up_shares']=eu; rr['expected_pending_down_shares']=ed; rr['expected_pending_net_shares']=eu-ed
    return float(cu)*fu, float(cd)*fd, rr
legacy.pending_adjust=soft_pending_adjust

def venue_for(mid:int)->Venue:
    mid=int(mid)
    if mid not in venues:
        venues[mid]=Venue(mid); venue_stats['markets']+=1
    return venues[mid]

def add_order_hft(sim,snapshot,book_state,side,ns,now,places,meta,market_id,p,reason,allow_stack=True,bypass_guard=False):
    if not bypass_guard:
        EVIDENCE_HOLD_STATS['checks']+=1
        lf=LAST_FILL_SIDE_MS.get((id(sim),str(side)))
        if lf is not None and int(now)-int(lf)<EVIDENCE_HOLD_MS:
            EVIDENCE_HOLD_STATS['blocked']+=1
            return False
    same=[k for k,o in sim.orders.items() if str(o.side)==str(side)]
    if PENDING_MODE=='ON' and same and allow_stack and not bypass_guard:
        pp=max([float(PENDING_P.get((id(sim),k),0.0)) for k in same] or [0.0]); PENDING_STATS['maxP']=max(PENDING_STATS['maxP'],pp); PENDING_STATS['draws']+=1
        if float(pending_rng(int(market_id)).random())<pp:
            PENDING_STATS['suppressed']+=1; return False
    before=set(sim.orders.keys())
    made=orig_add(sim,snapshot,book_state,side,ns,now,places,meta,market_id,p,reason,allow_stack,bypass_guard)
    if not made:return False
    new=[k for k in sim.orders.keys() if k not in before]
    if not new:return False
    key=new[0]; o=sim.orders[key]; v=venue_for(int(market_id)); sim_venue[id(sim)]=v
    rc=v.submit(key,o); venue_stats['submitted']+=1
    if rc!=0:
        venue_stats['submitRejects']+=1; sim.orders.pop(key,None); sim.last_closed[key]=int(now); meta.pop(key,None); return False
    if key in meta: meta[key]['hft_order_num']=v.order_num_by_key.get(key); meta[key]['hft_submit_rc']=rc
    return True

def fill_proxy_hft(sim,book_state,order_meta,now,mode):
    v=sim_venue.get(id(sim))
    if v is None:return []
    v.advance(int(now)); keys=list(sim.orders.keys()); snaps=v.snaps(keys); rec=[]
    for key,o in list(sim.orders.items()):
        num=v.order_num_by_key.get(key)
        if num is None:continue
        s=snaps.get(str(num)) or {}; cum=float(s.get('cumExecQty') or 0.0); old=float(v.prev_cum.get(num,0.0))
        m=order_meta.get(key) or {}; native_side=str(m.get('native_side') or ('bids' if str(o.side)=='UP' else 'asks')); native_price=float(m.get('native_price') or (float(o.price) if str(o.side)=='UP' else 1.0-float(o.price))); levels=(book_state.get(native_side,{}) if isinstance(book_state,dict) else {}) or {}; cur_depth=float(levels.get(native_price,0.0) or 0.0); bb=max(book_state.get('bids',{})) if isinstance(book_state,dict) and book_state.get('bids') else None; ba=min(book_state.get('asks',{})) if isinstance(book_state,dict) and book_state.get('asks') else None; cur_bid=(float(bb) if str(o.side)=='UP' and bb is not None else (1.0-float(ba) if str(o.side)=='DOWN' and ba is not None else None)); quote_offset=(None if cur_bid is None else (cur_bid-float(o.price))/0.01); pass_through=(False if cur_bid is None else cur_bid<float(o.price)-1e-9); row={'market_id':int(m.get('market_id') or v.market_id),'checkpoint_ms':int(now),'order_id':f"{v.market_id}:{str(o.side)}:{int(o.placed_at_ms)}:{int(getattr(o,'price_tick',0))}",'side':str(o.side),'side_is_up':1 if str(o.side)=='UP' else 0,'order_age_ms':int(now)-int(o.placed_at_ms),'quote_price':float(o.price),'quote_offset_ticks':quote_offset,'initial_depth':float(m.get('initial_depth') or 0.0),'current_depth':cur_depth,'cum_depletion_qty':float(m.get('cum_depletion') or 0.0),'any_depletion':1 if m.get('any_depletion') else 0,'pass_through_now':1 if pass_through else 0,'hft_cum_exec_qty':cum,'hft_leaves_qty':float(s.get('leavesQty') or 0.0),'hft_status':str(s.get('status') or 'NONE')}; row['p_fill_next1s']=timing_prob(row); PENDING_P[(id(sim),key)]=row['p_fill_next1s']; EXEC_ROWS.append(row)
        if cum>old+EPS:
            delta=cum-old; native=s.get('execPrice'); px=float(o.price)
            if native is not None and math.isfinite(float(native)): px=float(native) if str(o.side)=='UP' else 1.0-float(native)
            fill_ms=int((s.get('exchangeTs') or int(now)*1_000_000)//1_000_000)
            pre_u=sum(float(f['shares']) for f in sim.maker_fills if f['side']=='UP'); pre_d=sum(float(f['shares']) for f in sim.maker_fills if f['side']=='DOWN'); pre_g=pre_u+pre_d; pre_net=pre_u-pre_d; pre_pc=(2*min(pre_u,pre_d)/pre_g if pre_g>EPS else 1.0)
            if str(o.side)=='UP': sim.up_shares+=delta; sim.up_cost+=delta*px
            else: sim.down_shares+=delta; sim.down_cost+=delta*px
            sim.maker_fills.append({'side':str(o.side),'price':px,'shares':delta,'at_ms':fill_ms}); v.prev_cum[num]=cum
            venue_stats['fillEvents']+=1; venue_stats['filledShares']+=delta; LAST_FILL_SIDE_MS[(id(sim),str(o.side))]=fill_ms
            post_net=pre_net+(delta if str(o.side)=='UP' else -delta)
            rec.append({'key':key,'meta':order_meta.get(key),'pre_net':pre_net,'pre_pc':pre_pc,'post_net':post_net,'proxy':'HFTBACKTEST_ACTUAL_FILL','pass_through':False,'queue_cleared':False,'any_depletion':False,'deltaShares':delta,'cumShares':cum,'hftStatus':s.get('status')})
            leaves=s.get('leavesQty')
            if leaves is not None and float(leaves)>EPS:o.shares=float(leaves)
        status=str(s.get('status') or 'NONE')
        if status in TERMINAL:
            if cum<=EPS:venue_stats['terminalZeroFill']+=1
            sim.orders.pop(key,None);sim.last_closed[key]=int(now)
    return rec

legacy.add_order=add_order_hft; legacy.fill_proxy=fill_proxy_hft; legacy.FILL_PROXY='HFTBACKTEST_ACTUAL_FILL'
legacy.PREFIX=legacy.OUT/f'target_blind_promoted_controller_closed_loop_v2_hft_early_side_redistribute_v1{legacy.SUFFIX}'
legacy.REPORT=Path(str(legacy.PREFIX)+'_report.json');legacy.MARKETS=Path(str(legacy.PREFIX)+'_markets.csv');legacy.ACTIONS=Path(str(legacy.PREFIX)+'_actions.csv');legacy.STATES=Path(str(legacy.PREFIX)+'_states.csv')

if __name__=='__main__':
    rc=1
    try:
        rc=legacy.main()
        if legacy.REPORT.exists():
            rep=json.loads(legacy.REPORT.read_text(encoding='utf-8')); rep['reportVersion']='TARGET_BLIND_PROMOTED_CONTROLLER_CLOSED_LOOP_V26_HFT_TIMING_PENDING_V1';rep['fillProxy']='HFTBACKTEST_ACTUAL_FILL';rep['hftConfig']={'entryLatencyMs':ENTRY_LATENCY_MS,'responseLatencyMs':RESPONSE_LATENCY_MS,'queueModel':QUEUE_MODEL,'tradeOffset':TRADE_OFFSET,'dreamFillAllowed':False,'venueIsolation':'SUBPROCESS_PER_MARKET'};rep['hftVenueStats']=venue_stats; rep['timingPendingStats']=PENDING_STATS; rep['timingPendingMode']=PENDING_MODE; rep['softPendingStats']=SOFT_STATS; rep['softPendingMode']=SOFT_MODE; rep['reportVersion']='TARGET_BLIND_PROMOTED_CONTROLLER_CLOSED_LOOP_V27_HFT_SOFT_PENDING_V1'; rep['timingArtifact']=str(TIMING_ART)
            rep.setdefault('guards',[]).append('Maker inventory is mutated only by confirmed HftBacktest cumulative executions from Execution Tape V1. Unfilled commitments remain working orders and never become inventory.')
            legacy.REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
        if EXEC_ROWS:
            import pandas as pd
            pd.DataFrame(EXEC_ROWS).to_csv(Path(str(legacy.PREFIX)+'_exec_obs.csv'),index=False)
    finally:
        for v in venues.values():v.close()
    raise SystemExit(rc)
