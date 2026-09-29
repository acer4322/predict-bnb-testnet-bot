from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/target_maker_inventory_conditioned_side_v2.csv';OUT=ROOT/'data/research/r4_v0/hourly/target_strike_distance_formation_mode_v1.json'
x=pd.read_csv(SRC,low_memory=False);x=x[x.regime.eq('ORDINARY_PRE_SPECIAL') & x.spot_minus_strike_bps.notna()].copy()
for basis,delta_col in [('MAKER','maker_delta_shares'),('COMBINED','combined_delta_shares')]:
 d=pd.to_numeric(x[delta_col],errors='coerce');valid=d.abs()>1e-9;z=x[valid].copy();d=d[valid]
 dom=np.where(d>0,'UP','DOWN');z['dominantSide']=dom;z['placementRole']=np.where(z.target_side.to_numpy()==dom,'DOMINANT','MINORITY');sgn=np.where(dom=='UP',1.,-1.);z['strikeTowardDominantBps']=pd.to_numeric(z.spot_minus_strike_bps,errors='coerce')*sgn
 z['absNet']=d.abs().to_numpy();z['predictDominantMid']=np.where(dom=='UP',pd.to_numeric(z.predict_up_mid,errors='coerce'),pd.to_numeric(z.predict_down_mid,errors='coerce'))
 bins=[-1e9,-10,-5,-2,0,2,5,10,1e9];labs=['<-10','-10:-5','-5:-2','-2:0','0:2','2:5','5:10','10+'];z['strikeBin']=pd.cut(z.strikeTowardDominantBps,bins,labels=labs,right=False)
 z['timeBin']=pd.cut(pd.to_numeric(z.seconds_left,errors='coerce'),[0,60,120,180,240,301],labels=['0-60','60-120','120-180','180-240','240-300'],right=False)
 z['netBin']=pd.cut(z.absNet,[0,18,36,72,144,1e9],labels=['0-18','18-36','36-72','72-144','144+'],right=False)
 z['predBin']=pd.cut((z.predictDominantMid-.5).abs(),[0,.05,.1,.2,.3,.51],labels=['0-.05','.05-.1','.1-.2','.2-.3','.3-.5'],right=False)
 def agg(g):
  return {'n':int(len(g)),'markets':int(g.market_id.nunique()),'dominantPlacementRate':float((g.placementRole=='DOMINANT').mean()),'minorityPlacementRate':float((g.placementRole=='MINORITY').mean()),'meanTicks':float(pd.to_numeric(g.placement_ticks_from_pre_best_bid,errors='coerce').mean()),'near1':float(pd.to_numeric(g.label_near_best_1tick,errors='coerce').mean()),'meanParentShares':float(pd.to_numeric(g.expected_parent_shares,errors='coerce').mean())}
 out=[]
 for b in labs:
  g=z[z.strikeBin.astype(str)==b]
  if len(g):out.append({'strikeBin':b,**agg(g)})
 by_time=[]
 for (tb,sb),g in z.groupby(['timeBin','strikeBin'],observed=True):
  if len(g)>=50:by_time.append({'timeBin':str(tb),'strikeBin':str(sb),**agg(g)})
 by_net=[]
 for (nb,sb),g in z.groupby(['netBin','strikeBin'],observed=True):
  if len(g)>=50:by_net.append({'netBin':str(nb),'strikeBin':str(sb),**agg(g)})
 by_pred=[]
 for (pb,sb),g in z.groupby(['predBin','strikeBin'],observed=True):
  if len(g)>=50:by_pred.append({'predictDominantEdgeBin':str(pb),'strikeBin':str(sb),**agg(g)})
 globals()[f'{basis.lower()}_payload']={'rows':int(len(z)),'markets':int(z.market_id.nunique()),'overallByStrike':out,'byTime':by_time,'byAbsNet':by_net,'byPredictDominantEdge':by_pred}
rep={'version':'TARGET_STRIKE_DISTANCE_FORMATION_MODE_V1','researchOnly':True,'question':'When spot is farther from strike toward Target strict-past dominant inventory side, does the next inferred Maker placement favor continued dominant-side asymmetry or minority-side base construction?','guards':['ordinary pre-special only','strict-past official inventory from source dataset','no winner/settlement','fixed strike bins; no outcome threshold sweep','inferred placements are later-fill-confirmed parents, not private order truth'],'makerInventoryBasis':maker_payload,'combinedInventoryBasis':combined_payload};OUT.write_text(json.dumps(rep,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'maker':maker_payload['overallByStrike'],'combined':combined_payload['overallByStrike']},ensure_ascii=False,indent=2))
