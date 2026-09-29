import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

parser=argparse.ArgumentParser(); parser.add_argument('variant',choices=['v1','v2']); variant=parser.parse_args().variant
roots={'v1':Path('C:/BTC5M-worker/.tmp/hft244_worker_repair_20260910_v1'), 'v2':Path('C:/BTC5M-worker/.tmp/hft244_receipt_v2_env1_20260910')}
expected={'v1':'509fcad7f8cc56919337b567b170f1b3806ae75103bfe3ec755126f170aae37b','v2':'06e36073579d94218f7c39673a14b6a13796081331c22284de869cef3b2ba1a9'}
package=roots[variant]/'.tmp/hft244_accounting_build_v1/candidate_python'
assert hashlib.sha256((package/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd').read_bytes()).hexdigest()==expected[variant]
sys.path.insert(0,str(package)); import hftbacktest as h; import numpy as np
spec=importlib.util.spec_from_file_location('frozen_fixture',roots['v2']/'tools/check_hft244_receipt_v2_controls_fix1.py')
fixture=importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
result=dict(variant=variant,marketBE=0,engines=0,trace=[],fixture=dict(scenario='IOC_PARTIAL',side='BUY'),nativeSha256=expected[variant])

class BacktestProxy:
    def __init__(self,assets):
        result['engines']+=1; assert result['engines']==1
        self.bt=h.HashMapMarketDepthBacktest(assets)
    def __getattr__(self,key): return getattr(self.bt,key)
    def capture(self,label):
        depth=self.bt.depth(0); order=self.bt.orders(0).get(1)
        result['trace'].append(dict(label=label,now=int(self.bt.current_timestamp),bestAsk=float(depth.best_ask),bestBid=float(depth.best_bid),
            ask75=float(depth.ask_qty_at_tick(75)),ask76=float(depth.ask_qty_at_tick(76)),ask80=float(depth.ask_qty_at_tick(80)),
            order=None if order is None else {k:float(getattr(order,k)) for k in ['price','qty','leaves_qty','time_in_force','order_type','status','req']}))
    def elapse(self,t):
        rc=self.bt.elapse(t)
        if int(self.bt.current_timestamp) in [1100000000,1599000000,1700000000]: self.capture('elapse')
        return rc
    def submit_buy_order(self,*args):
        result['submittedArgs']=list(args); rc=self.bt.submit_buy_order(*args); self.capture('submit'); return rc

class ModuleProxy:
    HashMapMarketDepthBacktest=BacktestProxy
    def __getattr__(self,key): return getattr(h,key)

try:
    fixture.fixture(ModuleProxy(),np,'IOC_PARTIAL','BUY',result['fixture'])
    result['fixtureOutcome']='PASS'
except Exception as exc: result.update(fixtureOutcome='STOP_REPRODUCED_OR_DIAGNOSTIC_ERROR',error=type(exc).__name__+': '+str(exc))
output=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/(variant+'-ioc-trace.json'); assert not output.exists()
output.write_text(json.dumps(result,indent=2)); print(json.dumps({k:v for k,v in result.items() if k not in ['trace','fixture']}),flush=True)
assert result['engines']==1 and len(result['trace'])>=4, 'diagnostic not exercised'
