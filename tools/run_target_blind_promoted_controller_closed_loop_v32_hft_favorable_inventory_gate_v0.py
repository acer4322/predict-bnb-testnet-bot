from __future__ import annotations
import importlib.util,sys,math,json,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; TOOLS=ROOT/'tools'
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
if str(TOOLS) not in sys.path: sys.path.insert(0,str(TOOLS))
P=TOOLS/'run_target_blind_promoted_controller_closed_loop_v2_hft_early_side_redistribute_v1.py'
spec=importlib.util.spec_from_file_location('hftbase_favgate',P); hft=importlib.util.module_from_spec(spec); sys.modules[spec.name]=hft; assert spec and spec.loader; spec.loader.exec_module(hft)
if os.environ.get('CTRL_OUR_DB'): hft.legacy.mod.DEFAULT_OUR_DB=Path(os.environ['CTRL_OUR_DB'])
MODE_GATE=os.environ.get('FAVORABLE_INVENTORY_GATE','ON').upper()
base_add=hft.legacy.add_order
stats={'evaluated':0,'blockedUnfavorable':0,'allowedFavorable':0,'neutralOrMissing':0,'notDominantGrowth':0,'mode':MODE_GATE}

def sv(s,*ks):
    for k in ks:
        v=s.get(k)
        if v is not None:
            try:return float(v)
            except Exception:pass
    return math.nan

def side_mid(snapshot,side):
    if side=='UP':
        bid=sv(snapshot,'predictUpBid','predict_up_bid'); ask=sv(snapshot,'predictUpAsk','predict_up_ask')
    else:
        bid=sv(snapshot,'predictDownBid','predict_down_bid'); ask=sv(snapshot,'predictDownAsk','predict_down_ask')
    if math.isfinite(bid) and math.isfinite(ask): return (bid+ask)/2.0
    if math.isfinite(bid): return bid
    if math.isfinite(ask): return ask
    return math.nan

def gated(sim,snapshot,book_state,side,ns,now,places,meta,market_id,p,reason,allow_stack=True,bypass_guard=False):
    u,d,g,net,pc=hft.legacy.maker_totals(sim); dom='UP' if net>1e-9 else 'DOWN' if net<-1e-9 else None
    if MODE_GATE=='ON' and (not bypass_guard) and str(reason)=='MAKER_HAZARD' and abs(net)>=18-1e-9 and dom==str(side):
        stats['evaluated']+=1
        mid=side_mid(snapshot,dom)
        if math.isfinite(mid):
            if mid < 0.5-1e-12:
                stats['blockedUnfavorable']+=1
                return False
            if mid > 0.5+1e-12:
                stats['allowedFavorable']+=1
            else:
                stats['neutralOrMissing']+=1
        else:
            stats['neutralOrMissing']+=1
    else:
        stats['notDominantGrowth']+=1
    return base_add(sim,snapshot,book_state,side,ns,now,places,meta,market_id,p,reason,allow_stack,bypass_guard)

hft.legacy.add_order=gated
suf=os.environ.get('CTRL_SUFFIX',''); hft.legacy.PREFIX=hft.legacy.OUT/f'target_blind_promoted_controller_closed_loop_v32_hft_favorable_inventory_gate_v0{suf}'; hft.legacy.REPORT=Path(str(hft.legacy.PREFIX)+'_report.json');hft.legacy.MARKETS=Path(str(hft.legacy.PREFIX)+'_markets.csv');hft.legacy.ACTIONS=Path(str(hft.legacy.PREFIX)+'_actions.csv');hft.legacy.STATES=Path(str(hft.legacy.PREFIX)+'_states.csv')
if __name__=='__main__':
    rc=1
    try:
        rc=hft.legacy.main()
        if hft.legacy.REPORT.exists():
            rep=json.loads(hft.legacy.REPORT.read_text()); rep['reportVersion']='HFT_FAVORABLE_INVENTORY_GATE_V0'; rep['favorableInventoryGate']=stats; rep['hftConfig']={'dreamFillAllowed':False,'entryLatencyMs':hft.ENTRY_LATENCY_MS,'responseLatencyMs':hft.RESPONSE_LATENCY_MS,'queueModel':hft.QUEUE_MODEL}; hft.legacy.REPORT.write_text(json.dumps(rep,indent=2,allow_nan=True))
    finally:
        for v in hft.venues.values(): v.close()
    raise SystemExit(rc)
