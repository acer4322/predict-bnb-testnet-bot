"""B: frozen producer through the actual V49 mixed whole-plan/receipt kernel.

The frozen FAV/UNDER producer replaces CG1AT alpha decisions. This comparison
does not silently add CG1AT repair, passive conversion or learned progression.
Frozen runtime sources are copied, hashed and loaded without editing them.
"""
import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
import gzip
import hashlib
import json
import lzma
import math
import os
from pathlib import Path
import platform
import sys
import time
import traceback

P=Path(__file__).resolve().parent
from comparison_policy import ComparisonPolicy, self_test

def save(path,value):
    path.write_text(json.dumps(value,indent=2,allow_nan=False),encoding='utf-8')

class Trace:
    def __init__(self):
        self.now=0;self.actions=[];self.states=[];self.plans=[];self.terminal_logged=set()
    def emit(self,kind,value):pass
    def action(self,value):self.actions.append(deepcopy(value))
    def process(self,sim,t):
        self.now=t
        self.states.append({'t':t,'inv':dict(sim.inv),'cost':sim.cost})
    def plan(self,value):
        self.plans.append({'t':value['t'],'operations':deepcopy(value['operations'])})

class Producer(ComparisonPolicy):
    def __init__(self,strategy,rv_5m=None):
        super().__init__(strategy,rv_5m);self.calls=0;self.blocked=[]
    def produce(self,frame):
        from tools.minimal_student_training_world_v2 import envelope
        self.calls+=1
        live=[c for c in frame['ledger'].carriers.values() if c.state!='TERMINAL']
        pending={s:sum(max(0.,c.qty-c.filled) for c in live if frame['ledger'].grants[c.parent_id].side==s) for s in ('UP','DOWN')}
        q=frame['quotes']
        wanted=None if q is None else self.intent(t=frame['t'],start=frame['start'],end=frame['end'],bid=q['UP']['bid'],ask=q['UP']['ask'],inventory=frame['own_view']['inv'],pending=pending)
        operations=[]
        if wanted is not None:
            side=wanted['side'];key=f"{side}_{frame['own_view']['n']}";pid=1 if side=='UP' else 2
            op={'kind':'NEW','key':key,'parent_id':pid,'side':side,'price':wanted['price'],'qty':15.,'route':'ACTIVE','role':'COMPARISON_FROZEN_TAKER'}
            # Use the unchanged V49 authority, ownership and self-cross check.
            draft=deepcopy(frame['ledger'])
            try:
                draft.reserve(key,pid,'ACTIVE',15.,op['price'],0.,now_ms=frame['t'],market_end_ms=frame['end'])
            except ValueError as exc:
                self.blocked.append({'t':frame['t'],'intent':op,'component':'V49_OPEN_FUNDING_OWNER_RESERVATION','reason':str(exc)})
                self.counts['V49_RESERVATION_BLOCK']+=1
            else:operations.append(op)
        return envelope(frame,self,operations)

def load_kernel():
    assert platform.node().upper()=='DESKTOP-JIERAGF'
    candidate=json.loads((P/'CANDIDATE.json').read_text())
    binary=Path(candidate['backend'])/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
    assert hashlib.sha256(binary.read_bytes()).hexdigest()==candidate['sha256']
    sys.path.insert(0,str(Path(candidate['backend'])))
    sys.path.insert(0,str(P/'runtime'));sys.path.insert(0,str(P/'runtime/src'))
    import hftbacktest
    from hftbacktest.order import IOC
    assert IOC==3, "IOC ABI must match the existing Rust TimeInForce enum"
    assert Path(hftbacktest.__file__).resolve()==binary.parent/'__init__.py'
    from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
    from tools.minimal_student_quantity_seam_v1 import make_student_class
    from tools.open_funding_native_active_adapter_v1 import make_mixed_training_class
    from tools.hft244_minimal_pair_accounting_v1 import install
    from tools.open_funding_recovery_runtime_v3 import FastOpenFundingLedger
    from tools.minimal_student_open_funding_v1 import OpenFundingProfile
    from tools.pair_core_economic_grant_ledger_v1 import Grant
    from tools.hft244_receipt_adapter_v1 import Receipt
    import ctypes
    assert ctypes.sizeof(Receipt)==104
    lib=ctypes.CDLL(str(binary));lib.hashmapbt_receipt_size_v1.restype=ctypes.c_size_t
    assert lib.hashmapbt_receipt_size_v1()==104
    assert self_test()['status']=='PASS'
    rv=json.loads((P/'RV5.json').read_text())
    for row in json.loads((P/'LABELS.json').read_text())['records']:
        mid=row['market_id'];r=rv[str(mid)];meta=json.loads((P/'META'/f'{mid}.json').read_text())
        assert r['window_start_s']*1000==meta['window_start_ms']
        assert math.isfinite(r['rv_5m']) and r['rv_5m']>=0
    return locals()

def book_at(bt):
    d=bt.depth(0)
    bb,ba=round(float(d.best_bid),2),round(float(d.best_ask),2)
    if not (math.isfinite(bb) and math.isfinite(ba) and 0<bb<ba<1):return {'bids':{},'asks':{}}
    return {'bids':{bb:float(d.bid_qty_at_tick(round(bb/.01)))},'asks':{ba:float(d.ask_qty_at_tick(round(ba/.01)))}}

def run_market(k,fixtures,mid,strategy,winner,out,rv_5m=None):
    import numpy as np
    from eof_runtime import advance_to,valid_clock
    base=k['minimal'].v2.base
    fx=fixtures/str(mid)
    meta=json.loads((P/'META'/f'{mid}.json').read_text())
    start,end=meta['window_start_ms'],meta['window_end_ms']
    first=json.loads((fx/'FIXTURE.json').read_text())['conversion_info']['firstReceivedMs']
    with np.load(fx/'events.npz') as z:events=z['data'].copy();times=z['local_times_ms'].tolist()
    t_end=int(events['exch_ts'].max())//1000000
    producer=Producer(strategy,rv_5m);trace=Trace();sim=None
    # The constructor receives the exact NPZ array, bypassing reconversion only.
    # Its tape stub contains no Target action, winner, settlement or future book.
    payload={'marketId':mid,'market':{'window_start_ms':start,'window_end_ms':end},'updates':[]}
    tape=out/'PUBLIC_WINDOW.json.xz';tape.write_bytes(lzma.compress(json.dumps(payload).encode()))
    original_feed=base.feed.build_archive_events
    base.feed.build_archive_events=lambda market_id,**kw:(events,times,{'firstReceivedMs':first,'lastReceivedMs':int(events['local_ts'].max())//1000000})
    try:
        profile=k['OpenFundingProfile'](max_live_owners=4096)
        ledger=k['FastOpenFundingLedger'](profile)
        for pid,side in ((1,'UP'),(2,'DOWN')):
            ledger.issue(k['Grant'](pid,'COMPARISON_FROZEN',side,0.,0.,0.,'NATIVE_ENGINE_COMPARISON_SPEC_20261002'))
        sim=k['Student'](tape,profile.max_live_owners,False,producer=producer,ledger=ledger,trace=trace,verified_window=[start,end],window_source_sha256=hashlib.sha256((P/'META'/f'{mid}.json').read_bytes()).hexdigest())
        assert base.ex.advance_to is advance_to
        assert advance_to(sim.bt,first)
        # 1 Hz observation, exactly 12+2k seconds for NEW. No future quote use.
        t=start+max(0,math.ceil((first-start)/1000))*1000
        while t<min(end,t_end):
            if not advance_to(sim.bt,t):break
            sim.process(t)
            book=book_at(sim.bt);q=base.quotes(book)
            frame=sim.current_frame(t,end,book,q,sim.frame_count)
            sim.consume(frame,producer.produce(deepcopy(frame)))
            t+=1000
        # Process response journal at actual native time, including bounded EOF.
        advance_to(sim.bt,t_end+1000)
        now=int(sim.bt.current_timestamp);assert valid_clock(now)
        sim.process(now//1000000)
        sim.gateway.ledger.invariants();sim._receipt_ledger.reconcile(sim.bt.state_values(0))
        inv=dict(sim.inv);cost=float(sim.cost)
        pnl=inv[winner]-cost
        receipts=list(sim._receipt_ledger.seen.values())
        fills=[{'order_sequence':r['order_id'],'exchange_ms':r['exchange_ts']/1000000,'received_ms':r['receive_ts']/1000000,'side':'UP' if r['side']==1 else 'DOWN','price':r['price'] if r['side']==1 else 1-r['price'],'qty':r['qty'],'maker':bool(r['maker']),'fee':r['fee']} for r in receipts]
        orders=[{'order_sequence':o['n'],'place_ms':o['placed'],'seconds':(o['placed']-start)/1000,'side':o['side'],'price':o['price'],'qty':o['qty'],'status':o['status'],'filled_qty':o['cum']} for o in sim.orders.values()]
        unresolved=sum(c.state!='TERMINAL' for c in sim.gateway.ledger.carriers.values())
        result={'market':mid,'strategy':strategy,'pnl':pnl,'cost':cost,'up':inv['UP'],'dn':inv['DOWN'],'win':winner,'inferred':False,'pnl_fee_1pct':pnl-.01*cost,'pnl_fee_2pct':pnl-.02*cost,'submits':len(orders),'fills':len(fills),'average_fill_price':cost/sum(inv.values()) if sum(inv.values()) else None,'average_order_seconds':sum(o['seconds'] for o in orders)/len(orders) if orders else None,'unresolved_owners_at_eof':unresolved,'owner_status':'RIGHT_CENSORED_EOF' if unresolved else 'ALL_TERMINAL','policy_validity':'INVALID_MISSING_12S_INITIAL_F' if producer.initial_context_invalid else 'VALID_OBSERVED_CONTEXT','effective_strategy':producer.effective,'time_in_force':'IOC','stats':dict(producer.counts),'blocked':producer.blocked,'native_clock_ms':now/1000000,'accounting_reconciled':True,'fits':0}
        save(out/'result.json',result)
        with gzip.GzipFile(filename=str(out/'orders_and_fills.json.gz'),mode='wb',mtime=0) as f:f.write(json.dumps({'market_id':mid,'strategy':strategy,'orders':orders,'fills':fills,'decisions':producer.decisions,'plans':trace.plans},separators=(',',':'),allow_nan=False).encode())
        save(out/'AUDIT.json',{'status':'PASS_OBSERVED_ACCOUNTING','receipt_native_reconciled':True,'inventory_and_cost_reconciled':True,'unresolved_owners':unresolved,'EOF_terminal_gate':'UNKNOWN' if unresolved else 'PASS','legacy_active_matches_opportunity':'NOT_APPLICABLE_POLICY_REPLACEMENT_NOT_RETESTED','all_old_gates_passed':False})
        return result
    except Exception as exc:
        captured={'status':'PATH_ERROR','market':mid,'strategy':strategy,'error':repr(exc),'traceback':traceback.format_exc(limit=12),'decisions':producer.decisions,'plans':trace.plans,'actions':trace.actions}
        if sim is not None:
            captured.update(orders=sim.orders,inventory=sim.inv,cost=sim.cost,receipt_prefix=list(sim._receipt_ledger.seen.values()),owners={key:asdict(c) for key,c in sim.gateway.ledger.carriers.items()},native_clock_ns=int(sim.bt.current_timestamp))
        save(out/'FAILURE.json',captured)
        raise
    finally:
        base.feed.build_archive_events=original_feed
        if sim is not None:sim.close()

def main(check):
    manifest=json.loads((P/'MANIFEST.json').read_text())
    for name,sha in manifest['files'].items():assert hashlib.sha256((P/name).read_bytes()).hexdigest()==sha,name
    k=load_kernel()
    if check:
        print(json.dumps({'status':'LOAD_ONLY_PASS','source_files':len(manifest['files']),'native_executed':0,'fits':0,'receipt_abi_bytes':104}));return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR')
    k['install'](k['minimal'].v2.base,k['binary'])
    k['Student']=k['make_mixed_training_class'](k['minimal'].MinimalPairRoleSim,k['make_student_class'](k['minimal'].MinimalPairRoleSim),k['minimal'].v2.base)
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
    labels=json.loads((P/'LABELS.json').read_text())['records']
    assert len(labels)==185
    fixtures=Path(manifest['fixtures_remote'])
    rows={s:[] for s in ('FAV_TAKER','UNDER_TAKER','V1_SWITCH','V2_THROTTLE')};began=time.monotonic()
    rv=json.loads((P/'RV5.json').read_text())
    for strategy in rows:
        for r in labels:
            arm=out/'arms'/f"{strategy}_{r['market_id']}";arm.mkdir(parents=True,exist_ok=False)
            rows[strategy].append(run_market(k,fixtures,r['market_id'],strategy,r['winner'],arm,rv[str(r['market_id'])]['rv_5m']))
            if len(rows[strategy])%5==0:
                save(out/'PARTIAL.json',rows)
                print(json.dumps({'strategy':strategy,'completed':len(rows[strategy]),'expected':185}),flush=True)
    save(out/'stack_local.json',rows)
    save(out/'RESULT.json',{'status':'COMPLETE_STACK_STRICT_FOUR_ARM185','job_id':os.environ['BTC5M_LAN_WORKER_JOB_ID'],'markets':185,'rows':740,'elapsed_seconds':time.monotonic()-began,'V1_SWITCH':'COMPLETE','V2_THROTTLE':'COMPLETE','rv_threshold':3.1834e-05,'fits':0,'live_changes':0,'policy_scope':'FROZEN_SPEC_PRODUCER_ON_V49_MIXED_WHOLE_PLAN_OWNER_RECEIPT_KERNEL','original_CG1AT_alpha_replayed':False})
    print('COMPLETE_STACK_STRICT_FOUR_ARM185',flush=True)

if __name__=='__main__':
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','NUMBA_NUM_THREADS','RAYON_NUM_THREADS'):os.environ[key]='1'
    ap=argparse.ArgumentParser();ap.add_argument('--check-only',action='store_true');main(ap.parse_args().check_only)
