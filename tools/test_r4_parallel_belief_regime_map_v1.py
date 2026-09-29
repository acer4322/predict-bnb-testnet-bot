from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
FILES={
 'FRESH24':ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_hft_shadow_fresh24_v1_rows.csv',
 'UNSEEN24':ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_hft_shadow_unseen24_v1_rows.csv',
}
OUT=ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_regime_map_v1.json'
ROWS=ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_regime_map_v1_cells.csv'
BINS=['LOW','MID','HIGH']

RATE_COLS=['futureFrozenWeakNeed5s','futureWeakMakerFill5s','floorImproved5s','absNetReduced5s']
VALUE_COLS=['floorDelta5s','absNetDelta5s']

def qbin(s:pd.Series):
    # Descriptive relative-state bins only. Rank-based tertiles never use outcomes.
    r=s.rank(method='first',pct=True)
    return pd.cut(r,[0,1/3,2/3,1.0000001],labels=BINS,include_lowest=True,right=True)

def market_balanced(z:pd.DataFrame):
    per=[]
    for mid,m in z.groupby('marketId'):
        row={'marketId':int(mid),'nTicks':int(len(m))}
        for c in RATE_COLS: row[c]=float(m[c].mean())
        row['meanFloorDelta5s']=float(m.floorDelta5s.mean())
        row['p10FloorDelta5s']=float(m.floorDelta5s.quantile(.1))
        row['meanAbsNetDelta5s']=float(m.absNetDelta5s.mean())
        per.append(row)
    p=pd.DataFrame(per)
    if p.empty:return {'markets':0}
    out={'markets':int(len(p)),'ticks':int(len(z))}
    for c in RATE_COLS: out[c]=float(p[c].mean())
    out['meanFloorDelta5s']=float(p.meanFloorDelta5s.mean())
    out['p10FloorDelta5s']=float(p.p10FloorDelta5s.mean())
    out['meanAbsNetDelta5s']=float(p.meanAbsNetDelta5s.mean())
    return out

def tick_stats(z:pd.DataFrame):
    out={'ticks':int(len(z)),'markets':int(z.marketId.nunique())}
    for c in RATE_COLS: out[c]=float(z[c].mean())
    out['meanFloorDelta5s']=float(z.floorDelta5s.mean())
    out['p10FloorDelta5s']=float(z.floorDelta5s.quantile(.1))
    out['meanAbsNetDelta5s']=float(z.absNetDelta5s.mean())
    out['meanBuildBelief']=float(z.p_build.mean())
    out['meanPrepareBelief']=float(z.p_prepare_role_routed.mean())
    return out

def delta(cell,base):
    return {
      'futureFrozenWeakNeed5s':cell['futureFrozenWeakNeed5s']-base['futureFrozenWeakNeed5s'],
      'futureWeakMakerFill5s':cell['futureWeakMakerFill5s']-base['futureWeakMakerFill5s'],
      'floorImproved5s':cell['floorImproved5s']-base['floorImproved5s'],
      'absNetReduced5s':cell['absNetReduced5s']-base['absNetReduced5s'],
      'meanFloorDelta5s':cell['meanFloorDelta5s']-base['meanFloorDelta5s'],
      # negative is favorable for absNet delta, so expose a signed improvement too.
      'meanAbsNetDelta5s':cell['meanAbsNetDelta5s']-base['meanAbsNetDelta5s'],
      'absNetDeltaImprovement':base['meanAbsNetDelta5s']-cell['meanAbsNetDelta5s'],
      'p10FloorDelta5s':cell['p10FloorDelta5s']-base['p10FloorDelta5s'],
    }

def main():
    cohorts={};flat=[]
    for name,path in FILES.items():
        d=pd.read_csv(path).replace([np.inf,-np.inf],np.nan).dropna(subset=['p_build','p_prepare_role_routed']+RATE_COLS+VALUE_COLS).copy()
        d['buildRegime']=qbin(d.p_build).astype(str);d['prepareRegime']=qbin(d.p_prepare_role_routed).astype(str)
        overall_mb=market_balanced(d);overall_tick=tick_stats(d)
        cells=[]
        for b in BINS:
            for p in BINS:
                z=d[(d.buildRegime==b)&(d.prepareRegime==p)]
                if z.empty:continue
                mb=market_balanced(z);ts=tick_stats(z);dd=delta(mb,overall_mb)
                rec={'buildRegime':b,'prepareRegime':p,'marketBalanced':mb,'tickWeighted':ts,'deltaVsCohortMarketBalanced':dd}
                cells.append(rec)
                flat.append({'cohort':name,'buildRegime':b,'prepareRegime':p,**{f'mb_{k}':v for k,v in mb.items()},**{f'delta_{k}':v for k,v in dd.items()}})
        cohorts[name]={
          'rows':int(len(d)),'markets':int(d.marketId.nunique()),
          'buildTertileRawMeans':{x:float(d.loc[d.buildRegime==x,'p_build'].mean()) for x in BINS},
          'prepareTertileRawMeans':{x:float(d.loc[d.prepareRegime==x,'p_prepare_role_routed'].mean()) for x in BINS},
          'overallMarketBalanced':overall_mb,'overallTickWeighted':overall_tick,'cells':cells,
        }
    # Cross-cohort replication by relative 3x3 cell; no outcome-selected thresholds.
    cross=[]
    maps={n:{(c['buildRegime'],c['prepareRegime']):c for c in v['cells']} for n,v in cohorts.items()}
    for b in BINS:
      for p in BINS:
        pair=[maps[n].get((b,p)) for n in FILES]
        if any(x is None for x in pair):continue
        per={n:maps[n][(b,p)]['deltaVsCohortMarketBalanced'] for n in FILES}
        # Stable favorable if same direction in both cohorts. This is descriptive, not a policy gate.
        dims={
          'weakFill':all(per[n]['futureWeakMakerFill5s']>0 for n in FILES),
          'floorImproveRate':all(per[n]['floorImproved5s']>0 for n in FILES),
          'absNetReduceRate':all(per[n]['absNetReduced5s']>0 for n in FILES),
          'meanFloorDelta':all(per[n]['meanFloorDelta5s']>0 for n in FILES),
          'meanAbsNetDelta':all(per[n]['absNetDeltaImprovement']>0 for n in FILES),
        }
        adverse={
          'weakFill':all(per[n]['futureWeakMakerFill5s']<0 for n in FILES),
          'floorImproveRate':all(per[n]['floorImproved5s']<0 for n in FILES),
          'absNetReduceRate':all(per[n]['absNetReduced5s']<0 for n in FILES),
          'meanFloorDelta':all(per[n]['meanFloorDelta5s']<0 for n in FILES),
          'meanAbsNetDelta':all(per[n]['absNetDeltaImprovement']<0 for n in FILES),
        }
        cross.append({'buildRegime':b,'prepareRegime':p,'perCohortDelta':per,'stableFavorableDimensions':dims,'favorableCount':sum(dims.values()),'stableAdverseDimensions':adverse,'adverseCount':sum(adverse.values()),'marketCoverage':{n:maps[n][(b,p)]['marketBalanced']['markets'] for n in FILES},'tickCounts':{n:maps[n][(b,p)]['marketBalanced']['ticks'] for n in FILES}})
    cross.sort(key=lambda x:(-x['favorableCount'],x['adverseCount'],x['buildRegime'],x['prepareRegime']))
    art={'version':'R4_PARALLEL_BELIEF_REGIME_MAP_V1','researchOnly':True,'runtimePromotionAllowed':False,'actionChanges':False,'purpose':'Map non-exclusive P(BUILD) x P(PREPARE) relative regimes across two independent realistic-HFT shadow cohorts; identify cross-cohort stable path-quality associations without tuning action thresholds.','binning':'Within each cohort independently, rank-based tertiles of shadow P(BUILD) and role-routed P(PREPARE). Bins are descriptive relative states, use no outcomes, and are not deployable thresholds.','cohorts':cohorts,'crossCohortReplication':cross,'guards':['No HFT action changed.','No threshold sweep.','Tertiles are outcome-blind descriptive states.','Primary replication readout is direction agreement across both cohorts, not magnitude optimization.','Market-balanced metrics prevent long markets from dominating.','Future weak fill/floor/absNet changes are scoring labels only.']}
    OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
    pd.DataFrame(flat).to_csv(ROWS,index=False)
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'cohortOverall':{n:v['overallMarketBalanced'] for n,v in cohorts.items()},'crossCohortReplication':cross},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
