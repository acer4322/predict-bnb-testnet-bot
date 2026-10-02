from __future__ import annotations

import importlib.util, json, sys
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'backtest_unified_maker_stable_minus1_open_mid_v0.py'
spec=importlib.util.spec_from_file_location('stable_dir_base',P); mod=importlib.util.module_from_spec(spec); assert spec and spec.loader; sys.modules[spec.name]=mod; spec.loader.exec_module(mod)
REPORT=ROOT/'data'/'research'/'stable_directional_tolerance_v0_report.json'
VERSION='STABLE_DIRECTIONAL_TOLERANCE_V0'


def simple3(snapshot:dict[str,Any])->str|None:
    pu=mod.snapshot_value(snapshot,'predict_up_mid','predictUpMid')
    ss=mod.snapshot_value(snapshot,'spot_minus_strike_bps','spotMinusStrikeBps')
    cs=mod.snapshot_value(snapshot,'chainlink_minus_strike_bps','chainlinkMinusStrikeBps')
    if pu is None or ss is None or cs is None:return None
    up=(1 if pu>0.5 else 0)+(1 if ss>0 else 0)+(1 if cs>0 else 0)
    return 'UP' if up>=2 else 'DOWN'

def both_minus1(snapshot):
    sec=mod.snapshot_value(snapshot,'seconds_left','secondsLeft')
    if sec is None or sec<=mod.OPEN_MID_MIN_SECONDS_LEFT:return {'decision':'DEFER_TO_BASE','orders':[]}
    rows=[]
    for side in ('UP','DOWN'):
        tick=mod.maker_ebm._quote_tick(snapshot,side,1)
        if tick is None:continue
        rows.append({'side':side,'priceTick':tick,'price':round(tick*mod.maker_ebm.GRID,2),'shares':mod.maker_ebm.SHARES_PER_ORDER,'offsetTicks':1,'origin':VERSION})
    if len(rows)==2:
        while sum(float(x['price']) for x in rows)>mod.maker_ebm.MAX_PAIR_PRICE_SUM+1e-9:
            e=max(rows,key=lambda x:float(x['price'])); nxt=int(e['priceTick'])-1
            if nxt<int(round(mod.maker_ebm.MIN_PRICE/mod.maker_ebm.GRID)):return {'decision':'IDLE','orders':[]}
            e['priceTick']=nxt;e['price']=round(nxt*mod.maker_ebm.GRID,2);e['offsetTicks']=int(e['offsetTicks'])+1
    return {'decision':'QUOTE' if rows else 'IDLE','orders':rows,'reason':'STABLE_BOTH_MINUS1'}

class DirectionalSim(mod.Simulator):
    def __init__(self,models):
        super().__init__(models,'CUSTOM'); self.headwind_blocks=0; self.tailwind_allows=0
    def maker_dom(self):
        up=sum(float(f['shares']) for f in self.maker_fills if f['side']=='UP'); down=sum(float(f['shares']) for f in self.maker_fills if f['side']=='DOWN')
        if abs(up-down)<=1e-9:return None
        return 'UP' if up>down else 'DOWN'
    def decide(self,snapshot,market_id,now_ms):
        sec=mod.snapshot_value(snapshot,'seconds_left','secondsLeft')
        if sec is not None and sec>mod.OPEN_MID_MIN_SECONDS_LEFT:
            d=both_minus1(snapshot); dom=self.maker_dom(); dr=simple3(snapshot)
            if d.get('decision')=='QUOTE' and dom and dr:
                if dom!=dr:
                    rows=[r for r in d['orders'] if str(r['side'])!=dom]
                    self.headwind_blocks+=1; d=dict(d);d['orders']=rows;d['decision']='QUOTE' if rows else 'IDLE';d['reason']='HEADWIND_DOMINANT_BLOCK'
                else:self.tailwind_allows+=1
            return d
        return mod.maker_ebm.decide(snapshot,self.models,self.inventory(),cohort=mod.maker_ebm.COMBINED_COHORT,expected_market_id=market_id,now_ms=now_ms)

class NoInvSim(mod.Simulator):
    def __init__(self,models):super().__init__(models,'CUSTOM')
    def decide(self,snapshot,market_id,now_ms):
        sec=mod.snapshot_value(snapshot,'seconds_left','secondsLeft')
        if sec is not None and sec>mod.OPEN_MID_MIN_SECONDS_LEFT:return both_minus1(snapshot)
        return mod.maker_ebm.decide(snapshot,self.models,self.inventory(),cohort=mod.maker_ebm.COMBINED_COHORT,expected_market_id=market_id,now_ms=now_ms)

def run(sim,snaps,seeds,m):
    i=0
    for item in snaps:
        now=int(item['decision_ms']);snap=dict(item['snapshot']);ns=int(mod.num(snap.get('timestampNs')) or mod.num(snap.get('timestamp_ns')) or now*1_000_000)
        f=sim.fill_existing(snap,ns,now);d=sim.decide(snap,m,now);seed=False
        while i<len(seeds) and int(seeds[i]['filled_at_ms'])<=now:sim.apply_seed(seeds[i]);i+=1;seed=True
        sim.apply_plan(d,ns,now,allow_new=(f==0 and not seed))
    while i<len(seeds):sim.apply_seed(seeds[i]);i+=1
    return sim

def pack(sim,w):
    maker=[dict(x) for x in sim.maker_fills];allf=maker+[{'side':s['side'],'price':s['price'],'shares':s['shares'],'at_ms':s['filled_at_ms']} for s in sim.seed_fills];pair=mod.fifo_pair(maker);up=sum(f['shares'] for f in maker if f['side']=='UP');down=sum(f['shares'] for f in maker if f['side']=='DOWN')
    return {'pnlUsdt':mod.pnl_from_fills(allf,w),'placements':sim.placements,'cancels':sim.cancels,'makerFills':len(maker),'finalAbsNet':abs(up-down),'pair':pair,'headwindBlocks':getattr(sim,'headwind_blocks',0),'tailwindAllows':getattr(sim,'tailwind_allows',0)}

def main():
    our=mod.ro(mod.DEFAULT_OUR_DB);target=mod.ro(mod.DEFAULT_TARGET_DB)
    try:
        snaps=mod.load_snapshots(our);seeds=mod.load_seeds(our);winners=mod.load_winners(target);models=mod.maker_ebm.load_models();markets=sorted(set(snaps)&set(winners));rows=[]
        for m in markets:
            ss=list(seeds.get(m,[]));hard=run(mod.Simulator(models,'PERMISSIVE'),snaps[m],ss,m);no=run(NoInvSim(models),snaps[m],ss,m);dire=run(DirectionalSim(models),snaps[m],ss,m)
            rows.append({'marketId':m,'winner':winners[m],'stableHardInventory':pack(hard,winners[m]),'stableNoInventory':pack(no,winners[m]),'directionalTolerance':pack(dire,winners[m])})
        def st(w,k):return mod.stats([float(r[w][k]) for r in rows])
        rep={'reportVersion':VERSION,'researchOnly':True,'policy':{'OPEN_MID':'stable best-bid-1 resting quotes','directionalTolerance':'if Maker-only dominant side disagrees with strict-past SIMPLE3, suppress dominant side; if aligned, keep both sides; flat/unknown keeps both','TAIL':'existing base EBM','noSweep':True},'graduationReference':0.35,'coverage':{'markets':len(rows)},'summary':{},'comparisons':{},'rows':rows}
        for w in ('stableHardInventory','stableNoInventory','directionalTolerance'):
            pn=[float(r[w]['pnlUsdt']) for r in rows];ps=sum(float(r[w]['pair']['pairedShares']) for r in rows);ed=sum(float(r[w]['pair']['lockedEdgeUsdt']) for r in rows)
            rep['summary'][w]={'pnlUsdt':sum(pn),'positiveMarkets':sum(x>0 for x in pn),'positiveMarketRate':sum(x>0 for x in pn)/len(pn),'pnlPerMarket':mod.stats(pn),'placementsPerMarket':st(w,'placements'),'cancelsPerMarket':st(w,'cancels'),'makerFillsPerMarket':st(w,'makerFills'),'finalAbsNet':st(w,'finalAbsNet'),'pairedShares':ps,'lockedEdgeUsdt':ed,'edgePerPairedShare':ed/ps if ps else None,'pairedCoverage':mod.stats([float(r[w]['pair']['pairedCoverage']) for r in rows]),'headwindBlocks':sum(int(r[w]['headwindBlocks']) for r in rows),'tailwindAllows':sum(int(r[w]['tailwindAllows']) for r in rows)}
        for b,name in [('stableNoInventory','dirMinusNoInv'),('stableHardInventory','dirMinusHard')]:
            ds=[float(r['directionalTolerance']['pnlUsdt'])-float(r[b]['pnlUsdt']) for r in rows];rep['comparisons'][name]={'sum':sum(ds),'better':sum(x>1e-9 for x in ds),'worse':sum(x<-1e-9 for x in ds),'tie':sum(abs(x)<=1e-9 for x in ds),'stats':mod.stats(ds)}
        REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
    finally:our.close();target.close()
if __name__=='__main__':main()
