from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = ROOT / 'tools' / 'backtest_target_maker_time_only_no_inventory_v0.py'
spec = importlib.util.spec_from_file_location('td_base_dyn', BASE_PATH)
base = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = base
spec.loader.exec_module(base)

REPORT = ROOT / 'data' / 'research' / 'target_maker_dynamic_inventory_skew_v0_report.json'
VERSION = 'TARGET_MAKER_DYNAMIC_INVENTORY_SKEW_V0'


def maker_only_net(sim: base.Simulator) -> float:
    up = sum(float(f['shares']) for f in sim.maker_fills if str(f['side']) == 'UP')
    down = sum(float(f['shares']) for f in sim.maker_fills if str(f['side']) == 'DOWN')
    return up - down


def dynamic_decide(snapshot: dict[str, Any], sim: base.Simulator) -> dict[str, Any]:
    sec = base.snapshot_value(snapshot, 'seconds_left', 'secondsLeft')
    base_offset = base.time_offset(sec)
    if base_offset is None:
        return {'decision':'IDLE','reason':'OUTSIDE_ACTIVE_WINDOW','orders':[]}
    net = maker_only_net(sim)
    steps = int(abs(net) // base.maker_ebm.SHARES_PER_ORDER + 1e-9)
    orders = []
    for side in ('UP','DOWN'):
        if abs(net) <= 1e-9:
            off = base_offset
        else:
            dominant = 'UP' if net > 0 else 'DOWN'
            if side == dominant:
                off = base_offset + steps
            else:
                off = max(0, base_offset - steps)
        tick = base.maker_ebm._quote_tick(snapshot, side, off)
        if tick is None:
            continue
        orders.append({'side':side,'priceTick':tick,'price':round(tick*base.maker_ebm.GRID,2),'shares':base.maker_ebm.SHARES_PER_ORDER,'offsetTicks':off,'origin':VERSION})
    if len(orders) == 2:
        while sum(float(x['price']) for x in orders) > base.maker_ebm.MAX_PAIR_PRICE_SUM + 1e-9:
            expensive=max(orders,key=lambda x:float(x['price']))
            nxt=int(expensive['priceTick'])-1
            if nxt < int(round(base.maker_ebm.MIN_PRICE/base.maker_ebm.GRID)):
                return {'decision':'IDLE','reason':'PAIR_PRICE_CAP_UNSATISFIABLE','orders':[]}
            expensive['priceTick']=nxt
            expensive['price']=round(nxt*base.maker_ebm.GRID,2)
            expensive['offsetTicks']=int(expensive['offsetTicks'])+1
    return {'decision':'QUOTE' if orders else 'IDLE','reason':'DYNAMIC_INVENTORY_SKEW','orders':orders,'makerOnlyNet':net,'skewSteps':steps,'baseOffsetTicks':base_offset}


class DynamicSkewSimulator(base.Simulator):
    def __init__(self) -> None:
        super().__init__(use_inventory=False)
        self.skew_steps_seen=[]
    def decide(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        d=dynamic_decide(snapshot,self)
        self.skew_steps_seen.append(int(d.get('skewSteps') or 0))
        return d


def run_dynamic(snapshots: list[dict[str, Any]], seed_rows: list[dict[str, Any]]) -> DynamicSkewSimulator:
    sim=DynamicSkewSimulator(); seed_idx=0
    for item in snapshots:
        now_ms=int(item['decision_ms']); snap=dict(item['snapshot'])
        snapshot_ns=int(base.num(snap.get('timestampNs')) or base.num(snap.get('timestamp_ns')) or (now_ms*1_000_000))
        maker_filled=sim.fill_existing(snap,snapshot_ns,now_ms)
        decision=sim.decide(snap)
        seed_filled=False
        while seed_idx < len(seed_rows) and int(seed_rows[seed_idx]['filled_at_ms']) <= now_ms:
            sim.apply_seed(seed_rows[seed_idx]); seed_idx+=1; seed_filled=True
        sim.apply_plan(decision,snapshot_ns,now_ms,allow_new=(maker_filled==0 and not seed_filled))
    while seed_idx < len(seed_rows):
        sim.apply_seed(seed_rows[seed_idx]); seed_idx+=1
    return sim


def pack(sim: base.Simulator, winner: str) -> dict[str, Any]:
    maker=[dict(x) for x in sim.maker_fills]
    all_fills=maker+[{'side':s['side'],'price':s['price'],'shares':s['shares'],'at_ms':s['filled_at_ms']} for s in sim.seed_fills]
    out={'pnlUsdt':base.pnl_from_fills(all_fills,winner),'placements':sim.placements,'cancels':sim.cancels,'makerFills':len(maker),'pair':base.fifo_pair(maker),'makerImbalance':base.imbalance_metrics(maker),'portfolio':base.portfolio_metrics(all_fills)}
    if isinstance(sim,DynamicSkewSimulator): out['skewSteps']=base.stats([float(x) for x in sim.skew_steps_seen])
    return out


def main() -> int:
    our=base.ro(base.DEFAULT_OUR_DB); target=base.ro(base.DEFAULT_TARGET_DB)
    try:
        snapshots=base.load_snapshots(our); seeds=base.load_seeds(our); baseline=base.load_baseline(our); winners=base.load_winners(target)
        markets=sorted(set(snapshots)&set(winners)); rows=[]
        for m in markets:
            seed_rows=list(seeds.get(m,[])); winner=winners[m]
            no=base.run_sim(snapshots[m],seed_rows,use_inventory=False)
            hard=base.run_sim(snapshots[m],seed_rows,use_inventory=True)
            dyn=run_dynamic(snapshots[m],seed_rows)
            base_fills=[dict(x) for x in baseline.get(m,{}).get('fills',[])]
            base_maker=[f for f in base_fills if str(f.get('channel'))=='MAKER']
            bpack={'pnlUsdt':base.pnl_from_fills(base_fills,winner),'placements':int(baseline.get(m,{}).get('placements',0)),'cancels':int(baseline.get(m,{}).get('cancels',0)),'makerFills':len(base_maker),'pair':base.fifo_pair(base_maker),'makerImbalance':base.imbalance_metrics(base_maker),'portfolio':base.portfolio_metrics(base_fills)}
            rows.append({'marketId':m,'winner':winner,'baseline':bpack,'timeDepthNoInventory':pack(no,winner),'timeDepthHardInventory':pack(hard,winner),'dynamicInventorySkew':pack(dyn,winner)})
        def vals(w,k): return [float(r[w][k]) for r in rows]
        def nest(w,g,k): return [float(r[w][g][k]) for r in rows]
        report={'reportVersion':VERSION,'researchOnly':True,'liveTradingChanges':False,'hypothesis':'A soft Avellaneda-Stoikov-like inventory skew can regulate inventory without the hard side shutdown: every additional 18 Maker-only net shares pushes dominant quote one tick deeper and minority quote one tick closer, on top of the time-depth base curve.','policy':{'base':'same Layer-1 time-depth curve','inventorySkew':'steps=floor(abs(Maker-only net)/18); dominant offset=base+steps; minority offset=max(0,base-steps); both sides remain eligible','fixedNotSwept':True,'same':['18 shares/order','pair cap <=0.99','ask-touch fill proxy','recorded OPEN_SEED']},'graduationReference':{'positiveMarketRate':0.35},'coverage':{'markets':len(rows)},'summary':{},'comparisons':{},'rows':rows,'guards':['Historical replay, not fresh validation.','This tests one fixed soft-skew shape, not an optimized A-S solution.','No direction or future winner enters the policy.']}
        for w in ('baseline','timeDepthNoInventory','timeDepthHardInventory','dynamicInventorySkew'):
            p=vals(w,'pnlUsdt'); ps=sum(nest(w,'pair','pairedShares')); edge=sum(nest(w,'pair','lockedEdgeUsdt'))
            report['summary'][w]={'pnlUsdt':sum(p),'positiveMarkets':sum(x>0 for x in p),'positiveMarketRate':sum(x>0 for x in p)/len(p) if p else None,'pnlPerMarket':base.stats(p),'placementsPerMarket':base.stats(vals(w,'placements')),'cancelsPerMarket':base.stats(vals(w,'cancels')),'makerFillsPerMarket':base.stats(vals(w,'makerFills')),'pairedCoverage':base.stats(nest(w,'pair','pairedCoverage')),'lockedEdgeUsdt':edge,'pairedShares':ps,'edgePerPairedShare':edge/ps if ps>0 else None,'finalAbsNetShares':base.stats(nest(w,'makerImbalance','finalAbsNetShares')),'peakAbsNetShares':base.stats(nest(w,'makerImbalance','peakAbsNetShares')),'riskDeficitUsdt':base.stats(nest(w,'portfolio','riskDeficitUsdt'))}
        for other in ('timeDepthNoInventory','timeDepthHardInventory','baseline'):
            ds=[float(r['dynamicInventorySkew']['pnlUsdt'])-float(r[other]['pnlUsdt']) for r in rows]
            report['comparisons']['dynamicMinus'+other[0].upper()+other[1:]]={'sumPnlDelta':sum(ds),'better':sum(x>1e-9 for x in ds),'worse':sum(x<-1e-9 for x in ds),'tie':sum(abs(x)<=1e-9 for x in ds),'deltaPerMarket':base.stats(ds)}
        REPORT.parent.mkdir(parents=True,exist_ok=True); REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
        our.close(); target.close()
    return 0

if __name__=='__main__': raise SystemExit(main())
