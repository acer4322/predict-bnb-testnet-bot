from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1]
FILES={'FRESH24':ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_hft_shadow_fresh24_v1_rows.csv','UNSEEN24':ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_hft_shadow_unseen24_v1_rows.csv','REPLICATION3':ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_hft_shadow_replication3_v1_rows.csv'}
OUT=ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_phase_map_v2.json'
BINS=['LOW','MID','HIGH'];GOOD={('MID','LOW'),('MID','MID')};PHASES=[('LATE_0_60',0,60),('MID_60_180',60,180),('EARLY_180_300',180,301)]
RATE=['futureWeakMakerFill5s','floorImproved5s','absNetReduced5s']
def qbin(s):
 r=s.rank(method='first',pct=True);return pd.cut(r,[0,1/3,2/3,1.000001],labels=BINS,include_lowest=True).astype(str)
def mb(z):
 per=[]
 for mid,g in z.groupby('marketId'):
  x={'marketId':int(mid)}
  for c in RATE:x[c]=float(g[c].mean())
  x['meanFloorDelta5s']=float(g.floorDelta5s.mean());x['p10FloorDelta5s']=float(g.floorDelta5s.quantile(.1));x['meanAbsNetDelta5s']=float(g.absNetDelta5s.mean());x['damageRate']=float((g.floorDelta5s<0).mean());per.append(x)
 p=pd.DataFrame(per)
 if p.empty:return {'markets':0,'ticks':int(len(z))}
 o={'markets':int(len(p)),'ticks':int(len(z))}
 for c in RATE:o[c]=float(p[c].mean())
 o['meanFloorDelta5s']=float(p.meanFloorDelta5s.mean());o['p10FloorDelta5s']=float(p.p10FloorDelta5s.mean());o['meanAbsNetDelta5s']=float(p.meanAbsNetDelta5s.mean());o['damageRate']=float(p.damageRate.mean());return o
def delta(a,b):
 return {'weakFill':a['futureWeakMakerFill5s']-b['futureWeakMakerFill5s'],'floorImproveRate':a['floorImproved5s']-b['floorImproved5s'],'absNetReduceRate':a['absNetReduced5s']-b['absNetReduced5s'],'meanFloorDelta':a['meanFloorDelta5s']-b['meanFloorDelta5s'],'p10FloorDelta':a['p10FloorDelta5s']-b['p10FloorDelta5s'],'absNetDeltaImprovement':b['meanAbsNetDelta5s']-a['meanAbsNetDelta5s'],'damageRateImprovement':b['damageRate']-a['damageRate']}
def main():
 cohorts={};keys=[]
 for name,path in FILES.items():
  d=pd.read_csv(path);d['b']=qbin(d.p_build);d['p']=qbin(d.p_prepare_role_routed);cells=[]
  for ph,lo,hi in PHASES:
   phase=d[(d.seconds_left>=lo)&(d.seconds_left<hi)].copy();base=mb(phase)
   good=phase[phase[['b','p']].apply(tuple,axis=1).isin(GOOD)];gm=mb(good);dd=delta(gm,base) if gm.get('markets',0)>0 else None
   cells.append({'phase':ph,'phaseBaseline':base,'goodRegime':gm,'deltaGoodVsPhase':dd});keys.append((ph,name))
  cohorts[name]={'rows':int(len(d)),'markets':int(d.marketId.nunique()),'phases':cells}
 cross=[]
 for ph,_,_ in PHASES:
  per={n:next(x for x in cohorts[n]['phases'] if x['phase']==ph)['deltaGoodVsPhase'] for n in FILES}
  fav={k:all(per[n][k]>0 for n in FILES) for k in ['weakFill','floorImproveRate','absNetReduceRate','meanFloorDelta','p10FloorDelta','absNetDeltaImprovement','damageRateImprovement']}
  adv={k:all(per[n][k]<0 for n in FILES) for k in fav}
  cross.append({'phase':ph,'perCohortDelta':per,'stableFavorable':fav,'favorableCount':sum(fav.values()),'stableAdverse':adv,'adverseCount':sum(adv.values()),'coverage':{n:next(x for x in cohorts[n]['phases'] if x['phase']==ph)['goodRegime']['markets'] for n in FILES}})
 art={'version':'R4_PARALLEL_BELIEF_PHASE_MAP_V2','researchOnly':True,'runtimePromotionAllowed':False,'actionChanges':False,'purpose':'Condition the previously replicated favorable belief cells BUILD=MID/PREPARE=LOW-MID on pre-existing lifecycle phases 0-60,60-180,180-300s, and ask whether mean/tail improvements replicate without tuning new thresholds.','goodBeliefCells':[list(x) for x in sorted(GOOD)],'phases':[x[0] for x in PHASES],'cohorts':cohorts,'crossCohortReplication':cross,'guards':['Time phases are inherited from prior Target lifecycle research; not outcome-mined here.','Belief tertiles remain outcome-blind descriptive relative states.','No action changes.','Primary goal includes P10 floor and damage-rate stability, not only mean outcomes.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'cross':cross,'cohorts':cohorts},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
