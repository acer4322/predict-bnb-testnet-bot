from __future__ import annotations
import json
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
HAZ=ROOT/'data/research/target_maker_direct_hazard_v1.csv'; PLC=ROOT/'data/research/target_maker_inventory_conditioned_side_v2.csv'; OUT=ROOT/'data/research/r4_v0/hourly/target_strike_direction_agreement_v1.json'
BASE=['seconds_left','predict_up_bid','predict_up_ask','predict_up_mid','predict_down_bid','predict_down_ask','predict_down_mid','predict_up_spread','predict_down_spread','predict_mid_sum','predict_up_mid_edge']
DBINS=[-1e9,-10,-5,-2,0,2,5,10,1e9]; DLAB=['<-10','-10:-5','-5:-2','-2:0','0:2','2:5','5:10','10+']
CBINS=[0,.05,.1,.2,.3,.500001]; CLAB=['0-.05','.05-.1','.1-.2','.2-.3','.3-.5']
def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);return {'n':len(y),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1]))}
def evalm(d,feats,lab,parts):
 tr=d[d.market_id.isin(parts['train'])];m=Pipeline([('i',SimpleImputer(strategy='median')),('m',HistGradientBoostingClassifier(max_depth=3,learning_rate=.05,max_iter=180,l2_regularization=3,min_samples_leaf=40,random_state=20260826))]);m.fit(tr[feats],tr[lab].astype(int));return {k:met(z[lab],m.predict_proba(z[feats])[:,1]) for k,s in parts.items() for z in [d[d.market_id.isin(s)]]}
def summ(z):
 return {'n':int(len(z)),'markets':int(z.market_id.nunique()),'meanParentShares':float(z.expected_parent_shares.mean()),'ge36':float((z.expected_parent_shares>=36-1e-9).mean()),'near1':float(z.label_near_best_1tick.mean()),'near2':float(z.label_near_best_2ticks.mean()),'meanTicks':float(z.placement_ticks_from_pre_best_bid.mean()),'meanChosenPredictMid':float(z.chosen_predict_mid.mean())}
def main():
 h=pd.read_csv(HAZ,low_memory=False).sort_values(['decision_sampled_at_ms','market_id']).copy(); pe=h.predict_up_mid.astype(float)-.5;sg=np.where(pe>=0,1.,-1.);h['predict_edge']=pe.abs();h['strike_toward_predict_bps']=h.spot_minus_strike_bps.astype(float)*sg;h['agree_bin']=pd.cut(h.strike_toward_predict_bps,DBINS,labels=DLAB,right=False);h['confidence_bin']=pd.cut(h.predict_edge,CBINS,labels=CLAB,right=False,include_lowest=True)
 mids=h.groupby('market_id').decision_sampled_at_ms.min().sort_values().index.astype(int).tolist();a=int(len(mids)*.70);b=int(len(mids)*.85);parts={'train':set(mids[:a]),'validation':set(mids[a:b]),'test':set(mids[b:])}
 hz={}
 for sec in (1,2,5):
  lab=f'label_next_inferred_placement_any_{sec}s';base=evalm(h,BASE,lab,parts);add=evalm(h,BASE+['strike_toward_predict_bps'],lab,parts);grid=[]
  for cb in CLAB:
   for db in DLAB:
    z=h[(h.confidence_bin==cb)&(h.agree_bin==db)]
    if len(z)>=100:grid.append({'predictEdgeBin':cb,'strikeTowardPredictBin':db,'n':len(z),'rate':float(z[lab].mean())})
  by=[{'bin':db,'n':int(len(z)),'rate':float(z[lab].mean()) if len(z) else None,'meanPredictEdge':float(z.predict_edge.mean()) if len(z) else None} for db in DLAB for z in [h[h.agree_bin==db]]]
  hz[str(sec)]={'predictOnly':base,'plusStrikeTowardPredict':add,'delta':{s:{'dAuc':add[s]['auc']-base[s]['auc'],'dAP':add[s]['ap']-base[s]['ap'],'dLogLossImprovement':base[s]['logLoss']-add[s]['logLoss']} for s in ('validation','test')},'byAgreementDistance':by,'conditionalGrid':grid}
 p=pd.read_csv(PLC,low_memory=False);p=p[p.spot_minus_strike_bps.notna()].copy();side_sign=np.where(p.target_side.eq('UP'),1.,-1.);p['strike_toward_placement_bps']=p.spot_minus_strike_bps.astype(float)*side_sign;p['agree_bin']=pd.cut(p.strike_toward_placement_bps,DBINS,labels=DLAB,right=False);p['chosen_conf_bin']=pd.cut((p.chosen_predict_mid.astype(float)-.5).abs(),CBINS,labels=CLAB,right=False,include_lowest=True)
 pb=[{'bin':db,**summ(z)} for db in DLAB for z in [p[p.agree_bin==db]] if len(z)]
 grid=[]
 for cb in CLAB:
  for db in DLAB:
   z=p[(p.chosen_conf_bin==cb)&(p.agree_bin==db)]
   if len(z)>=40:grid.append({'predictEdgeBin':cb,'strikeTowardPlacementBin':db,**summ(z)})
 rep={'version':'TARGET_STRIKE_DIRECTION_AGREEMENT_V1','researchOnly':True,'semantics':{'positiveStrikeTowardPredict':'spot is on the same side of strike as Predict favored binary outcome','positiveStrikeTowardPlacement':'spot is on the same side of strike as the observed Target Maker placement side'},'hazard':hz,'placementBehavior':{'byAgreementDistance':pb,'conditionalGrid':grid},'guards':['Fixed bins, no threshold sweep','Chronological 70/15/15 hazard split','No winner/settlement','Observed Target placement side is used only for descriptive placement-conditioned behavior, never as runtime input']};OUT.write_text(json.dumps(rep,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'hazardDelta':{k:v['delta'] for k,v in hz.items()},'placementByAgreement':pb},ensure_ascii=False))
if __name__=='__main__':main()
