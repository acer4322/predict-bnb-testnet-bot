from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss
ROOT=Path(__file__).resolve().parents[1]
HAZ=ROOT/'data/research/target_maker_direct_hazard_v1.csv'
PLC=ROOT/'data/research/target_maker_inventory_conditioned_side_v2.csv'
OUT=ROOT/'data/research/r4_v0/hourly/target_strike_distance_confidence_amplification_v1.json'
BINS=[0,2,5,10,20,40,80,float('inf')]
LABELS=['0-2','2-5','5-10','10-20','20-40','40-80','80+']
BASE=['seconds_left','predict_up_bid','predict_up_ask','predict_up_mid','predict_down_bid','predict_down_ask','predict_down_mid','predict_up_spread','predict_down_spread','predict_mid_sum','predict_up_mid_edge']
MICRO=['spot_queue_imbalance','spot_taker_imbalance_1s','spot_return_1s_bps','spot_return_3s_bps','futures_queue_imbalance','futures_taker_imbalance_1s','futures_return_1s_bps','futures_return_3s_bps','perp_spot_basis_bps']

def bins(s): return pd.cut(s.abs(),BINS,labels=LABELS,right=False,include_lowest=True)
def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float)
 return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1]))}
def model_eval(d, feats, lab, parts):
 tr=d[d.market_id.isin(parts['train'])]
 m=Pipeline([('imp',SimpleImputer(strategy='median')),('clf',HistGradientBoostingClassifier(max_depth=3,learning_rate=.05,max_iter=180,l2_regularization=3.0,min_samples_leaf=40,random_state=20260826))])
 m.fit(tr[feats],tr[lab].astype(int)); out={}
 for k,s in parts.items():
  z=d[d.market_id.isin(s)]; out[k]=met(z[lab].astype(int),m.predict_proba(z[feats])[:,1])
 return out

def summarize(g):
 if len(g)==0:return {'n':0}
 x={
  'n':int(len(g)),
  'markets':int(g.market_id.nunique()),
  'meanAbsStrikeBps':float(g.abs_strike_bps.mean()),
  'strikeAlignedPlacementRate':float(g.strike_aligned.mean()) if g.strike_aligned.notna().any() else None,
  'meanExpectedParentShares':float(g.expected_parent_shares.mean()),
  'medianExpectedParentShares':float(g.expected_parent_shares.median()),
  'parentGe36Rate':float((g.expected_parent_shares>=36-1e-9).mean()),
  'parentGe54Rate':float((g.expected_parent_shares>=54-1e-9).mean()),
  'nearBest1TickRate':float(g.label_near_best_1tick.mean()),
  'nearBest2TicksRate':float(g.label_near_best_2ticks.mean()),
  'meanTicksFromBest':float(g.placement_ticks_from_pre_best_bid.mean()),
  'meanChosenPredictMid':float(g.chosen_predict_mid.mean()),
  'meanMakerAbsDeltaShares':float(g.maker_abs_delta_shares.mean()),
  'meanCombinedAbsDeltaShares':float(g.combined_abs_delta_shares.mean()),
 }
 return x

def main():
 h=pd.read_csv(HAZ,low_memory=False).sort_values(['decision_sampled_at_ms','market_id']).copy(); h['abs_strike_bps']=h.spot_minus_strike_bps.abs(); h['strike_bin']=bins(h.spot_minus_strike_bps)
 mids=h.groupby('market_id').decision_sampled_at_ms.min().sort_values().index.astype(int).tolist(); a=int(len(mids)*.70); b=int(len(mids)*.85); parts={'train':set(mids[:a]),'validation':set(mids[a:b]),'test':set(mids[b:])}
 hazard={}
 for sec in (1,2,5):
  lab=f'label_next_inferred_placement_any_{sec}s'
  by=[]
  for q in LABELS:
   z=h[h.strike_bin==q]; by.append({'bin':q,'n':int(len(z)),'markets':int(z.market_id.nunique()),'rate':float(z[lab].mean()) if len(z) else None,'meanPredictUpMid':float(z.predict_up_mid.mean()) if len(z) else None})
  specs={
   'PREDICT_ONLY':BASE,
   'PREDICT_PLUS_SIGNED_STRIKE':BASE+['spot_minus_strike_bps'],
   'PREDICT_PLUS_ABS_STRIKE':BASE+['abs_strike_bps'],
   'PREDICT_PLUS_SIGNED_ABS_STRIKE':BASE+['spot_minus_strike_bps','abs_strike_bps'],
   'PREDICT_PLUS_MICRO_NO_STRIKE':BASE+MICRO,
   'PREDICT_PLUS_MICRO_AND_STRIKE':BASE+MICRO+['spot_minus_strike_bps','abs_strike_bps'],
  }
  ev={k:model_eval(h,v,lab,parts) for k,v in specs.items()}
  base=ev['PREDICT_ONLY']; inc={}
  for k in specs:
   if k=='PREDICT_ONLY':continue
   inc[k]={s:{'dAuc':ev[k][s]['auc']-base[s]['auc'],'dAP':ev[k][s]['ap']-base[s]['ap'],'dLogLossImprovement':base[s]['logLoss']-ev[k][s]['logLoss']} for s in ('validation','test')}
  # conditional descriptive rates inside broad Predict confidence bands to avoid mistaking Predict repricing for strike-distance effect.
  hc=h.copy(); hc['predict_conf_bin']=pd.cut(hc.predict_up_mid,[0,.2,.4,.6,.8,1.000001],labels=['0-.2','.2-.4','.4-.6','.6-.8','.8-1'],include_lowest=True,right=False)
  cond=[]
  for pc in hc.predict_conf_bin.cat.categories:
   for sb in LABELS:
    z=hc[(hc.predict_conf_bin==pc)&(hc.strike_bin==sb)]
    if len(z)>=100: cond.append({'predictBin':str(pc),'strikeBin':sb,'n':int(len(z)),'rate':float(z[lab].mean())})
  hazard[str(sec)]={'byAbsStrikeDistance':by,'models':ev,'incrementalVsPredictOnly':inc,'conditionalRates':cond}
 p=pd.read_csv(PLC,low_memory=False).copy(); p=p[p.spot_minus_strike_bps.notna()].copy(); p['abs_strike_bps']=p.spot_minus_strike_bps.abs(); p['strike_bin']=bins(p.spot_minus_strike_bps)
 # sign >0 means spot above strike => UP is aligned; sign <0 => DOWN aligned; near zero is neutral.
 p['strike_aligned']=np.where(p.spot_minus_strike_bps>0,p.target_side.eq('UP'),np.where(p.spot_minus_strike_bps<0,p.target_side.eq('DOWN'),np.nan)).astype(float)
 # candidate placement relation to strict-past Maker inventory.
 up=p.maker_up_net_shares.fillna(0).astype(float); dn=p.maker_down_net_shares.fillna(0).astype(float)
 p['placement_inventory_role']=np.where(np.isclose(up,dn,atol=1e-9),'FLAT',np.where(((p.target_side=='UP')&(up<dn))|((p.target_side=='DOWN')&(dn<up)),'MINORITY','DOMINANT'))
 p['aligned_with_existing_dominant']=np.where(p.placement_inventory_role=='DOMINANT',p.strike_aligned,np.nan)
 bybin=[]
 for q in LABELS:
  z=p[p.strike_bin==q]; row={'bin':q,**summarize(z)}
  for role in ('FLAT','MINORITY','DOMINANT'):
   r=z[z.placement_inventory_role==role]; row[f'{role.lower()}N']=int(len(r)); row[f'{role.lower()}Share']=float(len(r)/len(z)) if len(z) else None
  dom=z[z.placement_inventory_role=='DOMINANT']; row['dominantStrikeAlignedRate']=float(dom.strike_aligned.mean()) if len(dom) else None
  bybin.append(row)
 # Control for Predict price: within fixed chosen_predict_mid bins, does strike distance still change size/aggression/alignment?
 p['chosen_predict_bin']=pd.cut(p.chosen_predict_mid,[0,.2,.4,.6,.8,1.000001],labels=['0-.2','.2-.4','.4-.6','.6-.8','.8-1'],include_lowest=True,right=False)
 cond=[]
 for pc in p.chosen_predict_bin.cat.categories:
  for sb in LABELS:
   z=p[(p.chosen_predict_bin==pc)&(p.strike_bin==sb)]
   if len(z)>=40: cond.append({'predictBin':str(pc),'strikeBin':sb,**summarize(z)})
 rep={'version':'TARGET_STRIKE_DISTANCE_CONFIDENCE_AMPLIFICATION_V1','researchOnly':True,'question':'Does absolute/signed spot distance from binary strike amplify Target Maker confidence/participation beyond Predict price itself?','fixedBinsBps':LABELS,'hazard':hazard,'placementBehavior':{'rows':int(len(p)),'markets':int(p.market_id.nunique()),'byAbsStrikeDistance':bybin,'conditionalOnChosenPredictMid':cond},'guards':['Fixed strike-distance bins chosen before outcomes; no threshold sweep','Chronological 70/15/15 market split for hazard models','Target labels are outcomes only; no winner/settlement used','Placement behavior is descriptive conditional on observed Target Maker placements, not causal action authority','spot_minus_strike_bps is strict-past public state at signal sample']}
 OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(rep,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8')
 print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'hazardIncremental':{k:v['incrementalVsPredictOnly'] for k,v in hazard.items()},'placementByBin':bybin},ensure_ascii=False))
if __name__=='__main__':main()
