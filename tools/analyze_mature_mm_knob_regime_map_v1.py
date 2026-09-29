from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / 'data' / 'research' / 'mature_mm_parameter_sensitivity_v1_report.json'
V2 = ROOT / 'data' / 'research' / 'mature_mm_event_knob_regimes_v2_report.json'
OUT = ROOT / 'data' / 'research' / 'mature_mm_knob_regime_map_v1_report.json'

FEATURES = [
    'meanAbsPredictFromHalf','predictRange','predictTotalVariation','predictTrendiness',
    'simple3FlipRate','meanAbsSpotStrikeBps','meanAbsChainlinkStrikeBps','meanPredictSpread',
]


def q(xs:list[float], p:float)->float:
    ys=sorted(xs); pos=(len(ys)-1)*p; lo=int(math.floor(pos)); hi=int(math.ceil(pos)); w=pos-lo
    return ys[lo]*(1-w)+ys[hi]*w

def bucket(v:float,a:float,b:float)->str:
    return 'LOW' if v<=a else 'MID' if v<=b else 'HIGH'

def mean(xs:list[float])->float|None:
    return statistics.mean(xs) if xs else None


def main()->int:
    p1=json.loads(V1.read_text(encoding='utf-8'))
    p2=json.loads(V2.read_text(encoding='utf-8'))
    prof={int(r['marketId']):r['early60Profile'] for r in p2['rows']}
    thresholds={}
    for f in FEATURES:
        xs=[float(x[f]) for x in prof.values() if x.get(f) is not None]
        thresholds[f]=[q(xs,1/3),q(xs,2/3)] if len(xs)>=9 else None

    sources=[
        ('STATIC',p1,'REF_OFFSET1_NET18_AGEINF',['OFFSET2','OFFSET3','BLOCK_NET36','BLOCK_NET54','MAX_AGE_2S','MAX_AGE_5S','MAX_AGE_10S']),
        ('EVENT',p2,'REF_DELAY0_REPRICEINF',['FILL_DELAY_500MS','FILL_DELAY_1000MS','FILL_DELAY_2000MS','REPRICE_TOL_1T','REPRICE_TOL_2T','REPRICE_TOL_3T']),
    ]
    variants={}
    for family,p,ref,names in sources:
        rows=[r for r in p['rows'] if int(r['marketId']) in prof]
        for vn in names:
            global_ds=[float(r[vn]['pnlUsdt'])-float(r[ref]['pnlUsdt']) for r in rows]
            reg={}
            cells=[]
            for f in FEATURES:
                th=thresholds.get(f)
                if not th: continue
                a,b=th; by={}
                for bn in ('LOW','MID','HIGH'):
                    sub=[r for r in rows if prof[int(r['marketId'])].get(f) is not None and bucket(float(prof[int(r['marketId'])][f]),a,b)==bn]
                    ds=[float(r[vn]['pnlUsdt'])-float(r[ref]['pnlUsdt']) for r in sub]
                    pr=[float(r[vn]['pnlUsdt']) for r in sub]
                    rr=[float(r[ref]['pnlUsdt']) for r in sub]
                    cell={'n':len(sub),'meanDelta':mean(ds),'medianDelta':statistics.median(ds) if ds else None,'betterRate':sum(x>1e-9 for x in ds)/len(ds) if ds else None,'variantPositiveRate':sum(x>0 for x in pr)/len(pr) if pr else None,'referencePositiveRate':sum(x>0 for x in rr)/len(rr) if rr else None}
                    by[bn]=cell
                    if cell['meanDelta'] is not None:
                        cells.append({'feature':f,'bucket':bn,**cell})
                reg[f]={'thresholds':[a,b],'buckets':by}
            best=max(cells,key=lambda x:x['meanDelta']) if cells else None
            worst=min(cells,key=lambda x:x['meanDelta']) if cells else None
            positive=[x for x in cells if x['meanDelta']>0]
            variants[vn]={
                'family':family,
                'global':{'n':len(rows),'meanDelta':mean(global_ds),'sumDelta':sum(global_ds),'betterRate':sum(x>1e-9 for x in global_ds)/len(global_ds) if global_ds else None},
                'bestEarlyRegimeCell':best,
                'worstEarlyRegimeCell':worst,
                'positiveRegimeCellCount':len(positive),
                'totalRegimeCellCount':len(cells),
                'positiveRegimeCells':sorted(positive,key=lambda x:x['meanDelta'],reverse=True)[:10],
                'regimeMap':reg,
            }
    report={
        'reportVersion':'MATURE_MM_KNOB_REGIME_MAP_V1','researchOnly':True,'liveTradingChanges':False,
        'purpose':'Combine mature-MM knob sensitivities under a common public-only first-minute market profile. Find state-dependent response surfaces, not a global best parameter.',
        'profileBoundary':'seconds_left>240; descriptive state available from T240 onward; no Target/winner inputs.',
        'guards':['Historical response discovery only; do not tune 8785 R1 from this map.','Positive cells are hypotheses for future frozen controller rules, not proven policies.','Market profile is first-minute aggregate; later controller should use strict-past rolling equivalents.'],
        'thresholds':thresholds,'variants':variants,
    }
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    compact={k:{'family':v['family'],'global':v['global'],'best':v['bestEarlyRegimeCell'],'worst':v['worstEarlyRegimeCell'],'positiveCells':v['positiveRegimeCellCount'],'totalCells':v['totalRegimeCellCount']} for k,v in variants.items()}
    print(json.dumps({'variants':compact,'report':str(OUT)},ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__': raise SystemExit(main())
