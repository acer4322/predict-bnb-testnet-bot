from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from predict_bot import unified_controller_paper_v2 as base
from predict_bot import unified_controller_r3s_r31_echtgeld_v1 as live
from predict_bot import echtgeld_engine_v30 as eng

def main():
    c=live.UnifiedControllerR3SR31EchtgeldV1()
    c.current_market_id=777001
    c.r21_bridge.reset_market(777001)
    c._deployment_live_ready=lambda: True
    calls=[]
    c._engine_post=lambda path,payload: (calls.append((path,dict(payload))) or {'ok':True,'accepted':True,'order':{'state':'RESTING'}})
    orig=base.UnifiedControllerPaperV2._add_order
    def fake_add(self,side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack=True,bypass_guard=False):
        tick=int(round(float(p)*100))
        key=(str(side),tick)
        self.orders[key]=base.PaperOrder(
            id=f'{live.VERSION}:777001:MAKER:{side}:{tick}:{now}:TEST',side=str(side),price_tick=tick,
            price=float(p),shares=10.0,placed_at_ms=int(now),placed_snapshot_ns=int(snapshot_ns),
            initial_depth=100.0,native_side='bids',native_price=float(p),occupied_before=False,
        )
        self.current_metrics['makerPlacements']=int(self.current_metrics.get('makerPlacements',0))+1
        self.run_metrics['makerPlacements']=int(self.run_metrics.get('makerPlacements',0))+1
        return True
    base.UnifiedControllerPaperV2._add_order=fake_add
    try:
        snap={'marketId':777001,'bucketStartSec':1700000000,'windowEndMs':1700000300000,'sampledAtMs':1700000001000}
        ok=c._add_order('DOWN',1700000001000,1700000001000000000,'TEST:DECISION','TEST_MAKER',0.49,snap)
    finally:
        base.UnifiedControllerPaperV2._add_order=orig
        try:c.stop()
        except Exception:pass
    payload=calls[0][1] if calls else {}
    checks={
      'returnedTrue':bool(ok),
      'oneEnginePost':len(calls)==1 and calls[0][0]=='/cap100/maker',
      'makerPayloadHasNoWtpVar': 'r3sWtp' not in payload,
      'controllerVersionExact': payload.get('controllerVersion')==live.VERSION,
      'sharesExactly10': abs(float(payload.get('shares') or 0)-10.0)<1e-9,
      'engineDurableStrategyExact': eng.R3S_R31_STRATEGY==live.VERSION,
      'engineSourceMapExact': eng.v23.CAP100_STRATEGY_BY_SOURCE.get(eng.R3S_R31_SOURCE)==live.VERSION,
      'makerAcceptedMetric': int(c.live_metrics.get('makerSubmitAccepted',0))==1,
      'makerUnknownMetricZero': int(c.live_metrics.get('makerUnknown',0))==0,
    }
    out={'version':live.VERSION,'checks':checks,'allPass':all(checks.values()),'payload':payload}
    ap=ROOT/'data/research/r3_v0/r3s_v113_maker_bridge_no_write_regression.json';ap.write_text(json.dumps(out,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'ok':True,'allPass':out['allPass'],'artifact':str(ap),'checks':checks}))
    if not out['allPass']: raise SystemExit(1)
if __name__=='__main__': main()
