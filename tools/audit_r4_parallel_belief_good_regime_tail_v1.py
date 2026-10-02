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
OUT=ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_good_regime_tail_audit_v1.json'
BINS=['LOW','MID','HIGH']
GOOD={('MID','LOW'),('MID','MID')}
FEATURES=['seconds_left','floor','absNet','pre_risk_deficit','predict_edge','strike_toward_dominant_bps','p_build','p_prepare_role_routed','placement_readiness_native_5s']
BINARY=['predict_supports_dominant','spot_supports_dominant']

def qbin(s):
 r=s.rank(method='first',pct=True)
 return pd.cut(r,[0,1/3,2/3,1.0000001],labels=BINS,include_lowest=True).astype(str)

def robust_effect(tail,rest,c):
 a=tail[c].dropna().astype(float);b=rest[c].dropna().astype(float)
 if a.empty or b.empty:return None
 iqr=float(b.quantile(.75)-b.quantile(.25));scale=max(abs(iqr),1e-9)
 return {'tailMedian':float(a.median()),'restMedian':float(b.median()),'medianDelta':float(a.median()-b.median()),'robustDeltaIqr':float((a.median()-b.median())/scale),'tailMean':float(a.mean()),'restMean':float(b.mean())}

def main():
 cohorts={}
 for name,path in FILES.items():
  d=pd.read_csv(path).replace([np.inf,-np.inf],np.nan).copy()
  d['buildRegime']=qbin(d.p_build);d['prepareRegime']=qbin(d.p_prepare_role_routed)
  g=d[d[['buildRegime','prepareRegime']].apply(tuple,axis=1).isin(GOOD)].copy()
  cut=float(g.floorDelta5s.quantile(.10));tail=g[g.floorDelta5s<=cut].copy();rest=g[g.floorDelta5s>cut].copy()
  feats={c:robust_effect(tail,rest,c) for c in FEATURES}
  bins={}
  for c in BINARY:
   bins[c]={'tailRate':float(tail[c].mean()),'restRate':float(rest[c].mean()),'delta':float(tail[c].mean()-rest[c].mean())}
  # Natural, predeclared semantic categories only; no outcome-optimized thresholds.
  semantic={
   'negativeFloor':{'tailRate':float((tail.floor<0).mean()),'restRate':float((rest.floor<0).mean())},
   'alreadySafeFloor':{'tailRate':float((tail.floor>=0).mean()),'restRate':float((rest.floor>=0).mean())},
   'late0_60':{'tailRate':float((tail.seconds_left<60).mean()),'restRate':float((rest.seconds_left<60).mean())},
   'mid60_180':{'tailRate':float(((tail.seconds_left>=60)&(tail.seconds_left<180)).mean()),'restRate':float(((rest.seconds_left>=60)&(rest.seconds_left<180)).mean())},
   'early180_300':{'tailRate':float((tail.seconds_left>=180).mean()),'restRate':float((rest.seconds_left>=180).mean())},
   'weakFill5s':{'tailRate':float(tail.futureWeakMakerFill5s.mean()),'restRate':float(rest.futureWeakMakerFill5s.mean())},
   'frozenWeakNeed5s':{'tailRate':float(tail.futureFrozenWeakNeed5s.mean()),'restRate':float(rest.futureFrozenWeakNeed5s.mean())},
   'absNetReduced5s':{'tailRate':float(tail.absNetReduced5s.mean()),'restRate':float(rest.absNetReduced5s.mean())},
  }
  for x in semantic.values():x['delta']=x['tailRate']-x['restRate']
  # Market concentration of tail, to ensure one market isn't dominating.
  mt=tail.groupby('marketId').size().sort_values(ascending=False)
  cohorts[name]={'goodRows':int(len(g)),'goodMarkets':int(g.marketId.nunique()),'tailCutFloorDelta5s':cut,'tailRows':int(len(tail)),'tailMarkets':int(tail.marketId.nunique()),'topTailMarketShare':float(mt.iloc[0]/len(tail)) if len(tail) else None,'features':feats,'binary':bins,'semantic':semantic,'tailOutcome':{'meanFloorDelta5s':float(tail.floorDelta5s.mean()),'meanAbsNetDelta5s':float(tail.absNetDelta5s.mean()),'p10FloorDelta5s':float(tail.floorDelta5s.quantile(.1))},'restOutcome':{'meanFloorDelta5s':float(rest.floorDelta5s.mean()),'meanAbsNetDelta5s':float(rest.absNetDelta5s.mean())}}
 # Cross-cohort stable precursor ordering, ranked descriptively by minimum absolute robust effect.
 stable=[]
 for c in FEATURES:
  vals={n:cohorts[n]['features'][c] for n in FILES}
  if any(v is None for v in vals.values()):continue
  signs=[np.sign(vals[n]['medianDelta']) for n in FILES]
  same=bool(signs[0]==signs[1] and signs[0]!=0)
  stable.append({'feature':c,'sameDirection':same,'direction':'TAIL_HIGHER' if same and signs[0]>0 else 'TAIL_LOWER' if same else 'MIXED','perCohort':vals,'minAbsRobustDeltaIqr':float(min(abs(vals[n]['robustDeltaIqr']) for n in FILES))})
 stable.sort(key=lambda x:(not x['sameDirection'],-x['minAbsRobustDeltaIqr']))
 art={'version':'R4_PARALLEL_BELIEF_GOOD_REGIME_TAIL_AUDIT_V1','researchOnly':True,'runtimePromotionAllowed':False,'actionChanges':False,'question':'Within the two cross-cohort favorable belief cells BUILD=MID/PREPARE=LOW-MID, what strict-past state characteristics are associated with the residual 5s floor-delta downside tail?','goodRegimes':[list(x) for x in sorted(GOOD)],'tailDefinition':'Bottom 10% floorDelta5s within the good regimes separately in each cohort. Outcome-defined tail is retrospective attribution only, never a runtime threshold.','cohorts':cohorts,'crossCohortStablePrecursors':stable,'guards':['No action changes.','Tail label used only for retrospective attribution.','No search over belief thresholds; good regimes came from prior outcome-blind tertile map.','Semantic time/floor categories are fixed natural bins, not tuned.','Market concentration is reported to catch single-market artifacts.']}
 OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'cohorts':cohorts,'stablePrecursors':stable},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
