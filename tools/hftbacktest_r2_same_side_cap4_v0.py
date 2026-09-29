from __future__ import annotations

import argparse, json, math, types, sys
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from tools import hftbacktest_r2_execution_school_v0 as base
from src.predict_bot import unified_controller_paper_v2 as mod

EPS=1e-9
ORIG_ADD=mod.UnifiedControllerPaperV2._add_order


def cap4_add(self, side:str, now:int, snapshot_ns:int, decision_id:str, reason:str, p:float, snapshot:dict[str,Any], allow_stack:bool=True, bypass_guard:bool=False)->bool:
    same=[o for o in self.orders.values() if o.side==side]; occupied=bool(same)
    if same:
        if not allow_stack or len(same)>=4: return False
        latest=max(same,key=lambda o:o.placed_at_ms); sec=mod.number(snapshot.get('secondsLeft')); vol=str(snapshot.get('volatilityAlert') or 'NORMAL').upper()
        context=(now-latest.placed_at_ms)<1500 or (sec is not None and 15<float(sec)<=60) or vol in {'WATCH','HIGH'}
        if not context and not bypass_guard:return False
        if not bypass_guard:
            _,_,_,net,pc=self._maker_totals();dom='UP' if net>EPS else 'DOWN' if net<-EPS else None
            if dom==side and pc<0.80:
                bias=str(snapshot.get('directionBias') or 'NEUTRAL').upper()
                if not (bias in {'UP','DOWN'} and bias!=side):return False
    qr=self._quote(side,1)
    if qr is None:return False
    tick,price=qr;min_tick=int(round(mod.MIN_PRICE/mod.GRID))
    while (side,tick) in self.orders:
        tick-=1
        if tick<min_tick:return False
        price=round(tick*mod.GRID,2)
    opp='DOWN' if side=='UP' else 'UP';opp_orders=[o for o in self.orders.values() if o.side==opp]
    if opp_orders:
        mx=max(o.price for o in opp_orders)
        while price+mx>mod.MAX_PAIR_PRICE_SUM+EPS:
            tick-=1
            if tick<min_tick:return False
            price=round(tick*mod.GRID,2)
    key=(side,tick)
    if now-int(self.last_closed.get(key,0))<mod.REFILL_COOLDOWN_MS:return False
    native_side='bids' if side=='UP' else 'asks';native_price=round(price if side=='UP' else 1.0-price,10);initial=float(self.book.book.get(native_side,{}).get(native_price,0.0))
    self.sequence+=1;oid=f'{mod.VERSION}:{self.current_market_id}:MAKER:{side}:{tick}:{now}:{self.sequence}'
    order=mod.PaperOrder(oid,side,tick,price,mod.SHARES,now,snapshot_ns,initial,native_side,native_price,occupied);self.orders[key]=order
    self.placements.append({'at_ms':now,'side':side,'price':price,'reason':reason,'p':p,'occupied_before':int(occupied)})
    self.current_metrics['makerPlacements']+=1;self.run_metrics['makerPlacements']+=1
    self.recorder.record_order_placement(order_id=oid,strategy_version=mod.VERSION,market_id=int(self.current_market_id),placement_decision_id=decision_id,channel='MAKER',side=side,quote_type='BID',price=price,shares=mod.SHARES,placed_at_ms=now,placement_state={'version':mod.VERSION,'decisionId':decision_id,'reason':reason,'pMaker':p,'offsetTicks':1,'occupiedBefore':occupied,'initialDepth':initial,'nativeSide':native_side,'nativePrice':native_price,'portfolio':self.inventory.features(now),'publicBookSourceMs':self.book.last_source_ms,'paperOnly':True,'targetDataUsed':False})
    return True


def run_market(mid:int):
    mod.UnifiedControllerPaperV2._add_order=cap4_add
    try:
        r=base.run_market(mid)
        r['capacityExperiment']={'sameSideLiveCap':4,'baselineCap':2,'modelsAndAllOtherR2LogicUnchanged':True}
        return r
    finally:
        mod.UnifiedControllerPaperV2._add_order=ORIG_ADD


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--market-id',type=int,action='append',required=True);ap.add_argument('--output',default='r2_same_side_cap4_v0.json');args=ap.parse_args()
    from tools.evaluate_r2_pending_management_closed_loop_v0 import winners
    ws=winners(args.market_id);rows=[]
    for mid in args.market_id:
        r=run_market(mid);s=r['studentRollout'];p=s['finalPortfolio'];g=float(p['combined_gross']);n=float(p['combined_net']);up=(g+n)/2;dn=(g-n)/2;pay=up if ws[mid]=='UP' else dn;pnl=pay-float(s['makerCostUsdt'])-float(s['takerCostUsdt'])-float(s['takerFeesUsdt']);row={'marketId':mid,'winner':ws[mid],'pnl':pnl,'makerPlacements':s['makerPlacements'],'makerFilledShares':s['makerFilledShares'],'takerFilledShares':s['takerFilledShares'],'finalAbs':p['combined_abs_net'],'pairCoverage':p['combined_paired_coverage'],'runMetrics':s['runMetrics']};rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
    agg={'markets':len(rows),'pnl':sum(x['pnl'] for x in rows),'positive':sum(x['pnl']>0 for x in rows),'makerFilledShares':sum(x['makerFilledShares'] for x in rows)};out=Path('data/research/execution_aware_fill_lifecycle_v0')/args.output;out.write_text(json.dumps({'version':'R2_SAME_SIDE_CAP4_V0','researchOnly':True,'aggregate':agg,'rows':rows},ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'aggregate':agg,'path':str(out)},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
