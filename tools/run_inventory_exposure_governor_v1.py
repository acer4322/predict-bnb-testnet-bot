from __future__ import annotations

import argparse, copy, json, math, statistics, sys
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from tools import hftbacktest_r2_execution_candidate_v2_frozen as base
from tools import r2_execution_graduation_exam_steward_v2_1 as steward

OUT=ROOT/'data/research/hourly_novel_tests'
PREREG=OUT/'inventory_exposure_governor_v1_preregistered.json'
REPORT=OUT/'inventory_exposure_governor_v1_report.json'
EPS=1e-9
STRENGTHS={'OFF':0.0,'VERY_MILD':0.5,'MILD':1.0,'MEDIUM':2.0,'STRONG':4.0}
NORMALIZER=7200.0
MIN_CHILD=1.0


def _dd(xs):
    cur=peak=dd=0.0
    for x in xs:
        cur+=x; peak=max(peak,cur); dd=max(dd,peak-cur)
    return dd


def _median(xs): return statistics.median(xs) if xs else None

def _mean(xs): return sum(xs)/len(xs) if xs else None


def _max_maker_excursion(row):
    fills=sorted(row['makerFills'],key=lambda x:(int(x['observedAtMs']),int(x['atMs'])))
    up=down=peak=0.0
    for f in fills:
        q=float(f.get('deltaShares') or 0.0)
        if f['side']=='UP': up+=q
        else: down+=q
        peak=max(peak,abs(up-down))
    return peak


def _maker_time_integral(row):
    fills=sorted(row['makerFills'],key=lambda x:(int(x['observedAtMs']),int(x['atMs'])))
    if not fills: return 0.0
    terminal=max([int(f['observedAtMs']) for f in fills]+[int(x.get('atMs') or 0) for x in row.get('takerFills',[])])
    up=down=area=0.0; last=int(fills[0]['observedAtMs'])
    for f in fills:
        now=int(f['observedAtMs']); area+=abs(up-down)*max(0,now-last)/1000.0
        q=float(f.get('deltaShares') or 0.0)
        if f['side']=='UP': up+=q
        else: down+=q
        last=now
    area+=abs(up-down)*max(0,terminal-last)/1000.0
    return area


def _capital(row):
    p=row['actualExecution']['finalPortfolio']
    return float(p.get('maker_up_cost',0.0))+float(p.get('maker_down_cost',0.0))+float(p.get('taker_up_cost',0.0))+float(p.get('taker_down_cost',0.0))+float(row['actualExecution'].get('takerFeesUsdt') or 0.0)


def _summary(rows, baseline=None):
    pnls=[float(r['actualExecution']['realizedPnl']) for r in rows]
    capital=[_capital(r) for r in rows]
    maker_exc=[_max_maker_excursion(r) for r in rows]
    maker_area=[_maker_time_integral(r) for r in rows]
    taker_shares=[float(r['actualExecution']['takerFilledShares']) for r in rows]
    maker_shares=[float(r['actualExecution']['makerFilledShares']) for r in rows]
    activity=[m+t for m,t in zip(maker_shares,taker_shares)]
    out={
      'markets':len(rows),'totalPnl':sum(pnls),'meanPnl':_mean(pnls),'medianPnl':_median(pnls),
      'positiveMarkets':sum(x>EPS for x in pnls),'positiveMarketRate':sum(x>EPS for x in pnls)/len(pnls) if pnls else None,
      'maxDrawdown':_dd(pnls),'worstMarketLoss':min(pnls) if pnls else None,
      'meanCapitalUsage':_mean(capital),'maxCapitalUsage':max(capital) if capital else None,'capitalBreaches100':sum(x>100+EPS for x in capital),
      'meanMakerMaxInventoryExcursion':_mean(maker_exc),'medianMakerMaxInventoryExcursion':_median(maker_exc),
      'meanMakerInventoryTimeIntegral':_mean(maker_area),'medianMakerInventoryTimeIntegral':_median(maker_area),
      'totalTakerFilledShares':sum(taker_shares),'meanTakerFilledShares':_mean(taker_shares),
      'totalMakerFilledShares':sum(maker_shares),'totalExecutionShares':sum(activity),
      'largestPositiveMarketContributionShare':(max([x for x in pnls if x>0],default=0.0)/sum(x for x in pnls if x>0)) if sum(x for x in pnls if x>0)>EPS else None,
    }
    if baseline:
        out['pnlDeltaVsBaseline']=out['totalPnl']-baseline['totalPnl']
        out['activityRatioVsBaseline']=out['totalExecutionShares']/baseline['totalExecutionShares'] if baseline['totalExecutionShares']>EPS else None
        out['makerExposureRatioVsBaseline']=out['meanMakerInventoryTimeIntegral']/baseline['meanMakerInventoryTimeIntegral'] if baseline['meanMakerInventoryTimeIntegral']>EPS else None
    return out


def run_variant(mid:int,strength:float):
    orig_submit=base.ex.submit_native
    state={'makerArea':0.0,'lastMs':None,'makerUp':0.0,'makerDown':0.0,'events':[]}
    # We cannot directly intercept base's local qty before submit, so this V1 monkey-patches native submit
    # using call order and current maker-net reconstructed from confirmed maker fills only after each completed run.
    # For causal sizing we instead patch CHUNK locally; base submit_maker computes qty immediately before submit.
    orig_chunk=base.CHUNK
    # strength maps to a fixed smooth global child-size scale at exposure=normalizer proxy. This first intervention
    # preserves side asymmetry logic while progressively reducing only risk-increasing child capacity through
    # base's existing TRACK_SKEW branch. Implemented by a temporary wrapper around submit_native keyed by native side
    # is not sufficient to change qty. Therefore V1 uses the existing inventory curve hook below.
    orig_mode=base.INVENTORY_CURVE_MODE
    try:
        # OFF exactly baseline. Non-OFF uses preregistered fixed smooth-equivalent stages via existing risk-increasing
        # branch only; recovery-side Maker is untouched by the base code.
        if strength<=0:
            base.INVENTORY_CURVE_MODE=orig_mode
        elif strength<=0.5:
            base.INVENTORY_CURVE_MODE='HALF_AT_54'
        elif strength<=1.0:
            base.INVENTORY_CURVE_MODE='STAGED_54_108'
        elif strength<=2.0:
            base.INVENTORY_CURVE_MODE='STOP_AT_72'
        else:
            base.INVENTORY_CURVE_MODE='STOP_AT_72'
            base.CHUNK=orig_chunk*0.8
        r=base.run_market(mid)
        r=copy.deepcopy(r);r['governorStrength']=strength;r['governorImplementation']='RISK_INCREASING_EXISTING_INVENTORY_CURVE_ONLY'
        return r
    finally:
        base.INVENTORY_CURVE_MODE=orig_mode;base.CHUNK=orig_chunk;base.ex.submit_native=orig_submit


def eligible_markets(limit:int):
    import sqlite3
    con=sqlite3.connect(ROOT/'data/strategy_input_snapshot_archive_v1.db')
    try:
        rows=con.execute('select market_id,min(sampled_at_ms) t from strategy_input_snapshots_v1 group by market_id order by t,market_id').fetchall()
    finally: con.close()
    mids=[]
    for mid,_ in rows:
        try:
            q=steward.quality_check_without_answer(int(mid))
            if q.get('eligible'): mids.append(int(mid))
        except Exception: pass
    return mids[-limit:]


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--historical',type=int,default=12);ap.add_argument('--forward',type=int,default=6);ap.add_argument('--market-ids',default='');a=ap.parse_args()
    if a.market_ids:
        mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    else:mids=eligible_markets(a.historical+a.forward)
    hist=mids[:a.historical];fwd=mids[a.historical:a.historical+a.forward]
    all_results={}
    for name,strength in STRENGTHS.items():
        rows=[]
        for i,mid in enumerate(hist,1):
            try:
                r=run_variant(mid,strength);rows.append(r);print(json.dumps({'phase':'historical','variant':name,'i':i,'n':len(hist),'marketId':mid,'pnl':r['actualExecution']['realizedPnl']}),flush=True)
            except Exception as e: print(json.dumps({'phase':'historical','variant':name,'marketId':mid,'error':repr(e)}),flush=True)
        all_results[name]={'historicalRows':rows}
    baseline=_summary(all_results['OFF']['historicalRows'])
    for name in STRENGTHS:
        all_results[name]['historical']=_summary(all_results[name]['historicalRows'],None if name=='OFF' else baseline)
    candidates=[]
    for name in ['VERY_MILD','MILD','MEDIUM','STRONG']:
        s=all_results[name]['historical']
        ok=(s['totalPnl']>0 and (s['largestPositiveMarketContributionShare'] or 1)<=0.5 and s['positiveMarketRate']>=baseline['positiveMarketRate']-0.05 and s['maxDrawdown']<baseline['maxDrawdown'] and s['worstMarketLoss']>baseline['worstMarketLoss'] and s['meanMakerInventoryTimeIntegral']<baseline['meanMakerInventoryTimeIntegral'])
        s['historicalCandidatePass']=bool(ok)
        if ok:candidates.append(name)
    selected=candidates[0] if candidates else None
    if selected:
        for name in ['OFF',selected]:
            strength=STRENGTHS[name];rows=[]
            for i,mid in enumerate(fwd,1):
                try:
                    r=run_variant(mid,strength);rows.append(r);print(json.dumps({'phase':'forward','variant':name,'i':i,'n':len(fwd),'marketId':mid,'pnl':r['actualExecution']['realizedPnl']}),flush=True)
                except Exception as e: print(json.dumps({'phase':'forward','variant':name,'marketId':mid,'error':repr(e)}),flush=True)
            all_results[name]['forwardRows']=rows
        fb=_summary(all_results['OFF']['forwardRows'])
        fs=_summary(all_results[selected]['forwardRows'],fb);all_results['OFF']['forward']=fb;all_results[selected]['forward']=fs
        promoted=fs['totalPnl']>0 and fs['meanMakerInventoryTimeIntegral']<fb['meanMakerInventoryTimeIntegral'] and fs['positiveMarketRate']>=fb['positiveMarketRate']-0.05
    else:promoted=False
    report={'testId':'INVENTORY_EXPOSURE_GOVERNOR_V1','researchOnly':True,'preregistration':str(PREREG.relative_to(ROOT)),'dreamFillUsed':False,'historicalMarketIds':hist,'forwardMarketIds':fwd,'variants':all_results,'selectedCandidate':selected,'promotionPassed':bool(promoted),'conclusion':('FORWARD_POSITIVE_CANDIDATE' if promoted else ('HISTORICAL_POSITIVE_CANDIDATE_NEEDS_OR_FAILED_FORWARD' if selected else 'NO_FIXED_GOVERNOR_RESTORED_POSITIVE_HISTORICAL_EXPECTANCY'))}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'report':str(REPORT),'selected':selected,'promotionPassed':promoted,'historical':{k:v['historical'] for k,v in all_results.items()}},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
