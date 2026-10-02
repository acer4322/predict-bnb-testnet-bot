from __future__ import annotations

import importlib.util
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
V1=ROOT/'tools'/'backtest_mature_mm_parameter_sensitivity_v1.py'
spec=importlib.util.spec_from_file_location('pair_phase_v1',V1); v1=importlib.util.module_from_spec(spec); assert spec and spec.loader; sys.modules[spec.name]=v1; spec.loader.exec_module(v1)
base=v1.base
V2R=ROOT/'data'/'research'/'mature_mm_event_knob_regimes_v2_report.json'
REPORT=ROOT/'data'/'research'/'binary_mm_paircap_phase_sensitivity_v3_report.json'

@dataclass(frozen=True)
class P:
    name:str
    pair_cap:float=0.99
    open_mid_min_seconds:float=60.0


def run_with_globals(models:dict[str,dict[str,Any]], p:P, snaps:list[dict[str,Any]], seeds:list[dict[str,Any]], market_id:int):
    old_cap=base.maker_ebm.MAX_PAIR_PRICE_SUM
    old_phase=base.OPEN_MID_MIN_SECONDS_LEFT
    try:
        base.maker_ebm.MAX_PAIR_PRICE_SUM=float(p.pair_cap)
        base.OPEN_MID_MIN_SECONDS_LEFT=float(p.open_mid_min_seconds)
        sim=v1.ParamSim(models,v1.Params(p.name,1,18.0,None))
        return v1.run_one(sim,snaps,seeds,market_id)
    finally:
        base.maker_ebm.MAX_PAIR_PRICE_SUM=old_cap
        base.OPEN_MID_MIN_SECONDS_LEFT=old_phase


def summ(rows,name):
    vals=[r[name] for r in rows]; pn=[float(x['pnlUsdt']) for x in vals]; paired=sum(float(x['pair']['pairedShares']) for x in vals); edge=sum(float(x['pair']['lockedEdgeUsdt']) for x in vals)
    return {'positiveRate':sum(x>0 for x in pn)/len(pn),'positiveMarkets':sum(x>0 for x in pn),'pnlUsdt':sum(pn),'meanPnl':statistics.mean(pn),'edgePerPairedShare':edge/paired if paired else None,'pairedCoverageMean':statistics.mean(float(x['pair']['pairedCoverage']) for x in vals),'finalAbsNetMedian':statistics.median(float(x['finalAbsNet']) for x in vals),'finalAbsNetMax':max(float(x['finalAbsNet']) for x in vals)}


def main():
    our=base.ro(base.DEFAULT_OUR_DB); target=base.ro(base.DEFAULT_TARGET_DB)
    try:
        snaps=base.load_snapshots(our); seeds=base.load_seeds(our); winners=base.load_winners(target); models=base.maker_ebm.load_models(); markets=sorted(set(snaps)&set(winners))
        ps=[P('REF_CAP099_TAIL60',.99,60),P('PAIR_CAP_098',.98,60),P('PAIR_CAP_097',.97,60),P('TAIL_SWITCH_30',.99,30),P('TAIL_SWITCH_90',.99,90)]
        rows=[]
        for m in markets:
            row={'marketId':m,'winner':winners[m]}; ss=list(seeds.get(m,[]))
            for p in ps:
                sim=run_with_globals(models,p,snaps[m],ss,m); row[p.name]=v1.pack(sim,winners[m])
            rows.append(row)
        ss={p.name:summ(rows,p.name) for p in ps}; ref=ps[0].name
        sens={}
        for p in ps[1:]:
            ds=[float(r[p.name]['pnlUsdt'])-float(r[ref]['pnlUsdt']) for r in rows]
            sens[p.name]={'params':{'pairCap':p.pair_cap,'openMidMinSeconds':p.open_mid_min_seconds},'meanDelta':statistics.mean(ds),'sumDelta':sum(ds),'better':sum(x>1e-9 for x in ds),'worse':sum(x<-1e-9 for x in ds),'tie':sum(abs(x)<=1e-9 for x in ds)}
        # Reuse public-only early60 profiles from V2 for regime discovery.
        p2=json.loads(V2R.read_text(encoding='utf-8')); prof={int(r['marketId']):r['early60Profile'] for r in p2['rows']}
        features=['meanAbsPredictFromHalf','predictRange','predictTotalVariation','predictTrendiness','simple3FlipRate','meanAbsSpotStrikeBps','meanPredictSpread']
        regime={}
        for f in features:
            xs=sorted(float(prof[int(r['marketId'])][f]) for r in rows if int(r['marketId']) in prof and prof[int(r['marketId'])].get(f) is not None)
            if len(xs)<9: continue
            def qq(fr):
                pos=(len(xs)-1)*fr; lo=int(pos); hi=min(len(xs)-1,lo+1); w=pos-lo; return xs[lo]*(1-w)+xs[hi]*w
            a,b=qq(1/3),qq(2/3); fr={'thresholds':[a,b],'buckets':{}}
            for bn in ('LOW','MID','HIGH'):
                def isb(v): return (v<=a) if bn=='LOW' else (a<v<=b) if bn=='MID' else v>b
                sub=[r for r in rows if int(r['marketId']) in prof and prof[int(r['marketId'])].get(f) is not None and isb(float(prof[int(r['marketId'])][f]))]
                bd={}
                for p in ps[1:]:
                    ds=[float(r[p.name]['pnlUsdt'])-float(r[ref]['pnlUsdt']) for r in sub]
                    bd[p.name]={'n':len(sub),'meanDelta':statistics.mean(ds) if ds else None,'betterRate':sum(x>1e-9 for x in ds)/len(ds) if ds else None}
                fr['buckets'][bn]=bd
            regime[f]=fr
        rep={'reportVersion':'BINARY_MM_PAIRCAP_PHASE_SENSITIVITY_V3','researchOnly':True,'liveTradingChanges':False,'purpose':'Finish first-pass economically meaningful binary-MM knobs: pair-cost cap and OPEN/MID-to-TAIL transition. One factor at a time; response discovery only.','guards':['Do not tune 8785 R1 from reused cohort.','Pair cap changes global simultaneous/resting-opposite pair economics in replay.','Tail-switch variants change when stable-directional OPEN/MID hands control back to existing base Maker EBM.'],'coverage':{'markets':len(rows)},'summary':ss,'sensitivityVsReference':sens,'conditionalEarly60Regime':regime,'rows':rows}
        REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'coverage':rep['coverage'],'summary':ss,'sensitivity':sens,'report':str(REPORT)},ensure_ascii=False,indent=2))
    finally: our.close(); target.close()
    return 0
if __name__=='__main__': raise SystemExit(main())
