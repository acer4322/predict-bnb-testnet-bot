from __future__ import annotations

import importlib.util, json, sys
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'backtest_unified_maker_stable_minus1_open_mid_v0.py'
spec=importlib.util.spec_from_file_location('stable_base',P); mod=importlib.util.module_from_spec(spec); assert spec and spec.loader; sys.modules[spec.name]=mod; spec.loader.exec_module(mod)
REPORT=ROOT/'data'/'research'/'stable_resting_toxicity_combo_v0_report.json'
VERSION='STABLE_RESTING_TOXICITY_COMBO_V0'


def both_decide(snapshot:dict[str,Any])->dict[str,Any]:
    sec=mod.snapshot_value(snapshot,'seconds_left','secondsLeft')
    if sec is None or sec<=mod.OPEN_MID_MIN_SECONDS_LEFT: return {'decision':'DEFER_TO_BASE','orders':[]}
    orders=[]
    for side in ('UP','DOWN'):
        tick=mod.maker_ebm._quote_tick(snapshot,side,1)
        if tick is None: continue
        orders.append({'side':side,'priceTick':tick,'price':round(tick*mod.maker_ebm.GRID,2),'shares':mod.maker_ebm.SHARES_PER_ORDER,'offsetTicks':1,'origin':VERSION})
    if len(orders)==2:
        while sum(float(x['price']) for x in orders)>mod.maker_ebm.MAX_PAIR_PRICE_SUM+1e-9:
            e=max(orders,key=lambda x:float(x['price'])); nxt=int(e['priceTick'])-1
            if nxt<int(round(mod.maker_ebm.MIN_PRICE/mod.maker_ebm.GRID)): return {'decision':'IDLE','orders':[]}
            e['priceTick']=nxt; e['price']=round(nxt*mod.maker_ebm.GRID,2); e['offsetTicks']=int(e['offsetTicks'])+1
    return {'decision':'QUOTE' if orders else 'IDLE','reason':'STABLE_BOTH_MINUS1','orders':orders}

class ComboSim(mod.Simulator):
    def __init__(self,models,tox:bool):
        super().__init__(models,'CUSTOM'); self.tox=tox; self.pending=[]; self.pause_until={'UP':0,'DOWN':0}; self.triggers=0
    def maker_dom(self):
        up=sum(float(f['shares']) for f in self.maker_fills if f['side']=='UP'); down=sum(float(f['shares']) for f in self.maker_fills if f['side']=='DOWN')
        if abs(up-down)<=1e-9:return None
        return 'UP' if up>down else 'DOWN'
    def fill_existing(self,snapshot,snapshot_ns,now_ms):
        n0=len(self.maker_fills); n=super().fill_existing(snapshot,snapshot_ns,now_ms)
        for f in self.maker_fills[n0:]:
            mid=mod.snapshot_value(snapshot,'predict_up_mid','predictUpMid') if f['side']=='UP' else mod.snapshot_value(snapshot,'predict_down_mid','predictDownMid')
            if mid is not None:self.pending.append({'side':f['side'],'fill_ms':f['at_ms'],'mid':mid})
        return n
    def update_tox(self,snapshot,now_ms):
        keep=[]
        for x in self.pending:
            if now_ms<x['fill_ms']+1000: keep.append(x); continue
            if now_ms>x['fill_ms']+3000: continue
            mid=mod.snapshot_value(snapshot,'predict_up_mid','predictUpMid') if x['side']=='UP' else mod.snapshot_value(snapshot,'predict_down_mid','predictDownMid')
            if mid is None: keep.append(x); continue
            ticks=(float(mid)-float(x['mid']))/mod.maker_ebm.GRID
            if self.tox and ticks<=-1+1e-9 and self.maker_dom()==x['side']:
                self.pause_until[x['side']]=max(self.pause_until[x['side']],x['fill_ms']+5000); self.triggers+=1
        self.pending=keep
    def decide(self,snapshot,market_id,now_ms):
        sec=mod.snapshot_value(snapshot,'seconds_left','secondsLeft')
        if sec is not None and sec>mod.OPEN_MID_MIN_SECONDS_LEFT:
            d=both_decide(snapshot)
            if d.get('decision')=='QUOTE':
                rows=[r for r in d.get('orders',[]) if now_ms>=self.pause_until.get(str(r['side']),0)]
                d=dict(d); d['orders']=rows; d['decision']='QUOTE' if rows else 'IDLE'
            return d
        return mod.maker_ebm.decide(snapshot,self.models,self.inventory(),cohort=mod.maker_ebm.COMBINED_COHORT,expected_market_id=market_id,now_ms=now_ms)

def run(sim,snaps,seeds,market_id):
    i=0
    for item in snaps:
        now=int(item['decision_ms']); snap=dict(item['snapshot']); ns=int(mod.num(snap.get('timestampNs')) or mod.num(snap.get('timestamp_ns')) or now*1_000_000)
        filled=sim.fill_existing(snap,ns,now)
        if isinstance(sim,ComboSim): sim.update_tox(snap,now)
        d=sim.decide(snap,market_id,now); seed=False
        while i<len(seeds) and int(seeds[i]['filled_at_ms'])<=now: sim.apply_seed(seeds[i]); i+=1; seed=True
        sim.apply_plan(d,ns,now,allow_new=(filled==0 and not seed))
    while i<len(seeds): sim.apply_seed(seeds[i]); i+=1
    return sim

def pack(sim,winner):
    maker=[dict(x) for x in sim.maker_fills]; allf=maker+[{'side':s['side'],'price':s['price'],'shares':s['shares'],'at_ms':s['filled_at_ms']} for s in sim.seed_fills]
    pair=mod.fifo_pair(maker); up=sum(f['shares'] for f in maker if f['side']=='UP'); down=sum(f['shares'] for f in maker if f['side']=='DOWN')
    return {'pnlUsdt':mod.pnl_from_fills(allf,winner),'placements':sim.placements,'cancels':sim.cancels,'makerFills':len(maker),'pair':pair,'finalAbsNet':abs(up-down),'triggers':getattr(sim,'triggers',0)}

def main():
    our=mod.ro(mod.DEFAULT_OUR_DB); target=mod.ro(mod.DEFAULT_TARGET_DB)
    try:
        snaps=mod.load_snapshots(our); seeds=mod.load_seeds(our); winners=mod.load_winners(target); models=mod.maker_ebm.load_models(); markets=sorted(set(snaps)&set(winners)); rows=[]
        for m in markets:
            ss=list(seeds.get(m,[])); hard=run(mod.Simulator(models,'PERMISSIVE'),snaps[m],ss,m); no=run(ComboSim(models,False),snaps[m],ss,m); tox=run(ComboSim(models,True),snaps[m],ss,m)
            rows.append({'marketId':m,'winner':winners[m],'stableHardInventory':pack(hard,winners[m]),'stableNoInventory':pack(no,winners[m]),'stableToxicity':pack(tox,winners[m])})
        def st(w,k): return mod.stats([float(r[w][k]) for r in rows])
        rep={'reportVersion':VERSION,'researchOnly':True,'policy':{'OPEN_MID':'new quote best bid -1 tick; existing quote rests while side remains desired','stableHardInventory':'prior V0 desired_sides hard inventory rule','stableNoInventory':'both sides remain desired','stableToxicity':'same as stableNoInventory, but after >=1s markout <=-1 tick on Maker-only dominant side, suppress that side until fill+5s','noSweep':True},'graduationReference':0.35,'coverage':{'markets':len(rows)},'summary':{},'comparisons':{},'rows':rows}
        for w in ('stableHardInventory','stableNoInventory','stableToxicity'):
            pn=[float(r[w]['pnlUsdt']) for r in rows]; ps=sum(float(r[w]['pair']['pairedShares']) for r in rows); ed=sum(float(r[w]['pair']['lockedEdgeUsdt']) for r in rows)
            rep['summary'][w]={'pnlUsdt':sum(pn),'positiveMarkets':sum(x>0 for x in pn),'positiveMarketRate':sum(x>0 for x in pn)/len(pn),'pnlPerMarket':mod.stats(pn),'placementsPerMarket':st(w,'placements'),'cancelsPerMarket':st(w,'cancels'),'makerFillsPerMarket':st(w,'makerFills'),'finalAbsNet':st(w,'finalAbsNet'),'pairedShares':ps,'lockedEdgeUsdt':ed,'edgePerPairedShare':ed/ps if ps else None,'pairedCoverage':mod.stats([float(r[w]['pair']['pairedCoverage']) for r in rows]),'triggers':sum(int(r[w]['triggers']) for r in rows)}
        for a,b,name in [('stableToxicity','stableNoInventory','toxMinusNoInv'),('stableToxicity','stableHardInventory','toxMinusHard')]:
            ds=[float(r[a]['pnlUsdt'])-float(r[b]['pnlUsdt']) for r in rows]; rep['comparisons'][name]={'sum':sum(ds),'better':sum(x>1e-9 for x in ds),'worse':sum(x<-1e-9 for x in ds),'tie':sum(abs(x)<=1e-9 for x in ds),'stats':mod.stats(ds)}
        REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False,indent=2))
    finally: our.close(); target.close()
if __name__=='__main__': main()
