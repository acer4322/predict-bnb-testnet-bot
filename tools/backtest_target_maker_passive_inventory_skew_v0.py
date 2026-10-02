from __future__ import annotations

import importlib.util, json, statistics, sys
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
BASE_PATH=ROOT/'tools'/'backtest_target_maker_time_only_no_inventory_v0.py'
spec=importlib.util.spec_from_file_location('time_noinv',BASE_PATH)
base=importlib.util.module_from_spec(spec); assert spec and spec.loader; sys.modules['time_noinv']=base; spec.loader.exec_module(base)
from predict_bot import predict_wallet_maker_ebm_strategy_v1 as maker_ebm

OUT=ROOT/'data'/'research'/'target_maker_passive_inventory_skew_v0_report.json'
VERSION='TARGET_MAKER_PASSIVE_INVENTORY_SKEW_V0'

class SkewSimulator(base.Simulator):
    def __init__(self): super().__init__(use_inventory=False)
    def decide(self,snapshot:dict[str,Any])->dict[str,Any]:
        seconds_left=base.snapshot_value(snapshot,'seconds_left','secondsLeft')
        base_offset=base.time_offset(seconds_left)
        if base_offset is None:
            return {'decision':'IDLE','reason':'OUTSIDE_ACTIVE_WINDOW','orders':[]}
        inv=self.inventory(); net=float(inv.get('netShares') or 0.0)
        offsets={'UP':base_offset,'DOWN':base_offset}
        relation='BALANCED'
        if net>=maker_ebm.SHARES_PER_ORDER-1e-9:
            # UP heavy: encourage DOWN passive completion, discourage more UP.
            offsets['DOWN']=max(0,base_offset-1); offsets['UP']=base_offset+1; relation='UP_HEAVY'
        elif net<=-maker_ebm.SHARES_PER_ORDER+1e-9:
            offsets['UP']=max(0,base_offset-1); offsets['DOWN']=base_offset+1; relation='DOWN_HEAVY'
        orders=[]
        for side in ('UP','DOWN'):
            tick=maker_ebm._quote_tick(snapshot,side,int(offsets[side]))
            if tick is None: continue
            orders.append({'side':side,'priceTick':tick,'price':round(tick*maker_ebm.GRID,2),'shares':maker_ebm.SHARES_PER_ORDER,
                           'offsetTicks':int(offsets[side]),'origin':VERSION})
        if len(orders)==2:
            while sum(float(x['price']) for x in orders)>maker_ebm.MAX_PAIR_PRICE_SUM+1e-9:
                expensive=max(orders,key=lambda x:float(x['price'])); nxt=int(expensive['priceTick'])-1
                if nxt<int(round(maker_ebm.MIN_PRICE/maker_ebm.GRID)):
                    return {'decision':'IDLE','reason':'PAIR_PRICE_CAP_UNSATISFIABLE','orders':[]}
                expensive['priceTick']=nxt; expensive['price']=round(nxt*maker_ebm.GRID,2); expensive['offsetTicks']=int(expensive['offsetTicks'])+1
        return {'decision':'QUOTE' if orders else 'IDLE','reason':'PASSIVE_INVENTORY_DEPTH_SKEW','orders':orders,
                'secondsLeft':seconds_left,'baseOffsetTicks':base_offset,'inventoryRelation':relation,'netShares':net}

def run_skew(snapshots,seed_rows):
    sim=SkewSimulator(); seed_idx=0
    for item in snapshots:
        now_ms=int(item['decision_ms']); snap=dict(item['snapshot'])
        snapshot_ns=int(base.num(snap.get('timestampNs')) or base.num(snap.get('timestamp_ns')) or now_ms*1_000_000)
        maker_filled=sim.fill_existing(snap,snapshot_ns,now_ms)
        decision=sim.decide(snap)
        seed_filled=False
        while seed_idx<len(seed_rows) and int(seed_rows[seed_idx]['filled_at_ms'])<=now_ms:
            sim.apply_seed(seed_rows[seed_idx]); seed_idx+=1; seed_filled=True
        sim.apply_plan(decision,snapshot_ns,now_ms,allow_new=(maker_filled==0 and not seed_filled))
    while seed_idx<len(seed_rows): sim.apply_seed(seed_rows[seed_idx]); seed_idx+=1
    return sim

def pack_sim(sim,winner):
    maker=[dict(x) for x in sim.maker_fills]
    all_fills=maker+[{'side':s['side'],'price':s['price'],'shares':s['shares'],'at_ms':s['filled_at_ms']} for s in sim.seed_fills]
    return {'pnlUsdt':base.pnl_from_fills(all_fills,winner),'placements':sim.placements,'cancels':sim.cancels,'makerFills':len(maker),
            'pair':base.fifo_pair(maker),'makerImbalance':base.imbalance_metrics(maker),'portfolio':base.portfolio_metrics(all_fills)}

def pack_base(raw,winner):
    fills=[dict(x) for x in raw.get('fills',[])]; maker=[f for f in fills if str(f.get('channel'))=='MAKER']
    return {'pnlUsdt':base.pnl_from_fills(fills,winner),'placements':int(raw.get('placements',0)),'cancels':int(raw.get('cancels',0)),
            'makerFills':len(maker),'pair':base.fifo_pair(maker),'makerImbalance':base.imbalance_metrics(maker),'portfolio':base.portfolio_metrics(fills)}

def summarize(rows,which):
    vals=lambda f:[float(r[which][f]) for r in rows]
    nested=lambda g,f:[float(r[which][g][f]) for r in rows]
    pair_sh=sum(nested('pair','pairedShares')); pair_edge=sum(nested('pair','lockedEdgeUsdt'))
    return {
      'pnlUsdt':sum(vals('pnlUsdt')),'positiveMarkets':sum(float(r[which]['pnlUsdt'])>0 for r in rows),'pnlPerMarket':base.stats(vals('pnlUsdt')),
      'makerActivity':{'placements':base.stats(vals('placements')),'cancels':base.stats(vals('cancels')),'makerFills':base.stats(vals('makerFills')),
                       'fillPerPlacementTotal':sum(vals('makerFills'))/sum(vals('placements')) if sum(vals('placements')) else None},
      'pairing':{'pairedShares':base.stats(nested('pair','pairedShares')),'pairedCoverage':base.stats(nested('pair','pairedCoverage')),
                 'lockedEdgeUsdt':base.stats(nested('pair','lockedEdgeUsdt')),'edgePerPairedShareTotal':pair_edge/pair_sh if pair_sh else None},
      'inventoryRisk':{'finalAbsNetShares':base.stats(nested('makerImbalance','finalAbsNetShares')),'finalImbalanceRatio':base.stats(nested('makerImbalance','finalImbalanceRatio')),
                       'peakAbsNetShares':base.stats(nested('makerImbalance','peakAbsNetShares')),'maxSameSideFillRun':base.stats(nested('makerImbalance','maxSameSideFillRun'))},
      'portfolioRisk':{'worstCasePnlUsdt':base.stats(nested('portfolio','worstCasePnlUsdt')),'riskDeficitUsdt':base.stats(nested('portfolio','riskDeficitUsdt'))},
    }

def main():
    our=base.ro(base.DEFAULT_OUR_DB); target=base.ro(base.DEFAULT_TARGET_DB)
    try:
        snapshots=base.load_snapshots(our); seeds=base.load_seeds(our); baseline=base.load_baseline(our); winners=base.load_winners(target)
        markets=sorted(set(snapshots)&set(winners)); rows=[]
        for m in markets:
            seed_rows=list(seeds.get(m,[])); winner=winners[m]
            sim_hard=base.run_sim(snapshots[m],seed_rows,use_inventory=True)
            sim_skew=run_skew(snapshots[m],seed_rows)
            rows.append({'marketId':m,'winner':winner,'baseline':pack_base(baseline.get(m,{}),winner),
                         'timeDepthHardInventory':pack_sim(sim_hard,winner),'passiveInventorySkew':pack_sim(sim_skew,winner)})
        report={'reportVersion':VERSION,'researchOnly':True,'liveTradingChanges':False,'coverage':{'markets':len(rows),'settledMarkets':len(rows)},
                'policy':{'timeDepth':'same fixed Layer1 time-offset V0 as prior ablation','balanced':'quote both sides at base time offset',
                          'ifAbsNetAtLeast18':'keep both sides; minority side one tick closer than base, dominant side one tick deeper than base',
                          'preserved':['18 shares/order','pair cap <=0.99','same dynamic repricing','same ask-touch fill proxy','same 1s refill cooldown','same OPEN_SEED','no direction input'],
                          'purpose':'architectural probe only; not claimed to match Target observed Layer2 yet'},
                'baseline':summarize(rows,'baseline'),'timeDepthHardInventory':summarize(rows,'timeDepthHardInventory'),
                'passiveInventorySkew':summarize(rows,'passiveInventorySkew'),'directComparison':{},'rows':rows,
                'guards':['No parameter sweep.','Direction/winner not used by policy.','Historical replay only; fresh blind validation required before KEEP.']}
        ds=[float(r['passiveInventorySkew']['pnlUsdt'])-float(r['timeDepthHardInventory']['pnlUsdt']) for r in rows]
        report['directComparison']={'skewMinusHardInventoryPnlUsdt':sum(ds),'deltaPerMarket':base.stats(ds),'skewBetterMarkets':sum(x>1e-9 for x in ds),'skewWorseMarkets':sum(x<-1e-9 for x in ds),'ties':sum(abs(x)<=1e-9 for x in ds)}
        OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'coverage':report['coverage'],'baseline':report['baseline'],'timeDepthHardInventory':report['timeDepthHardInventory'],'passiveInventorySkew':report['passiveInventorySkew'],'directComparison':report['directComparison'],'report':str(OUT)},ensure_ascii=False,indent=2))
    finally: our.close(); target.close()

if __name__=='__main__': main()
