from __future__ import annotations

import importlib.util,json,math,sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score,log_loss,roc_auc_score

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
HIST=OUT/'post_taker_maker_reentry_hazard_v0.csv';HAND=OUT/'post_taker_handoff_states_v1.csv';TAKER=OUT/'taker_event_states_v1.csv';FRESH=OUT/'post_taker_maker_reentry_hazard_forward_states_v0.csv';FHAND=OUT/'forward_handoff_states_v1.csv';FTAKER=OUT/'forward_taker_states_v1.csv';OUR=OUT/'our_synthetic_repair_add_reentry_hazard_bridge_v0_states.csv'
REPORT=OUT/'scale_normalized_effect_conditioned_reentry_v1_report.json';SAME_ART=OUT/'post_taker_reentry_same_scale_norm_effect_v1.joblib';OPP_ART=OUT/'post_taker_reentry_opp_scale_norm_effect_v1.joblib'
P=ROOT/'tools'/'analyze_post_taker_reentry_by_effect_v0.py';spec=importlib.util.spec_from_file_location('norm_eff',P);eff=importlib.util.module_from_spec(spec);assert spec and spec.loader;sys.modules[spec.name]=eff;spec.loader.exec_module(eff);coord=eff.coord
EFFECTS=['REPAIR_EFFECT','ADD_EFFECT'];EPS=1e-9
BOOK=['up_bid','up_ask','up_spread_ticks','up_bid_depth','up_ask_depth','up_top3_bid_depth','down_bid','down_ask','down_spread_ticks','down_bid_depth','down_ask_depth','down_top3_bid_depth','pair_bid_edge','pair_ask_edge','dominant_bid','dominant_ask','opposite_bid','opposite_ask','dominant_opp_bid_pair_edge']
AGES=['last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','last_taker_up_age_ms','last_taker_down_age_ms']
COUNTS=['maker_fills_1s','maker_fills_5s','maker_fills_10s','taker_fills_1s','taker_fills_5s','taker_fills_10s','maker_side_streak','taker_side_streak']
PRICES=['maker_up_avg_price','maker_down_avg_price','maker_avg_pair_edge','taker_up_avg_price','taker_down_avg_price','taker_avg_pair_edge','combined_up_avg_price','combined_down_avg_price','combined_avg_pair_edge']
MEM=['post_same_placements_so_far','post_opp_placements_so_far','post_both_seen','post_last_place_age_ms','post_last_place_is_same','post_last_same_place_age_ms','post_last_opp_place_age_ms','post_place_side_balance']
BASE=['seconds_left','maker_net_frac','maker_imbalance_ratio','maker_paired_coverage','taker_net_frac','taker_imbalance_ratio','taker_paired_coverage','combined_net_frac','combined_imbalance_ratio','combined_paired_coverage','floor_per_gross','best_per_gross','payoff_gap_frac','maker_taker_net_same_sign','maker_recent_share_frac_5s','maker_recent_share_frac_10s','taker_recent_share_frac_5s','taker_recent_share_frac_10s','maker_absnet_change_frac_10s','combined_absnet_change_frac_10s','time_since_taker_ms','intervention_side_is_up','intervention_frac_combined','intervention_avg_price','effect_is_add']+BOOK+AGES+COUNTS+PRICES+MEM

def div(a,b):
 a=pd.to_numeric(a,errors='coerce');b=pd.to_numeric(b,errors='coerce');return a/b.where(b.abs()>EPS,np.nan)

def transform(d:pd.DataFrame)->pd.DataFrame:
 x=pd.DataFrame(index=d.index)
 for c in ['seconds_left','maker_imbalance_ratio','maker_paired_coverage','taker_imbalance_ratio','taker_paired_coverage','combined_imbalance_ratio','combined_paired_coverage','maker_taker_net_same_sign','time_since_taker_ms','intervention_side_is_up','intervention_avg_price']+BOOK+AGES+COUNTS+PRICES+MEM:
  x[c]=pd.to_numeric(d.get(c),errors='coerce')
 x['maker_net_frac']=div(d['maker_net'],d['maker_gross']);x['taker_net_frac']=div(d['taker_net'],d['taker_gross']);x['combined_net_frac']=div(d['combined_net'],d['combined_gross'])
 x['floor_per_gross']=div(d['worst_case_floor'],d['combined_gross']);x['best_per_gross']=div(d['best_case_pnl'],d['combined_gross']);x['payoff_gap_frac']=div(d['abs_payoff_gap'],d['combined_gross'])
 x['maker_recent_share_frac_5s']=div(d['maker_shares_5s'],d['maker_gross']);x['maker_recent_share_frac_10s']=div(d['maker_shares_10s'],d['maker_gross']);x['taker_recent_share_frac_5s']=div(d['taker_shares_5s'],d['taker_gross']);x['taker_recent_share_frac_10s']=div(d['taker_shares_10s'],d['taker_gross'])
 x['maker_absnet_change_frac_10s']=div(d['maker_absnet_change_10s'],d['maker_gross']);x['combined_absnet_change_frac_10s']=div(d['combined_absnet_change_10s'],d['combined_gross']);x['intervention_frac_combined']=div(d['intervention_shares'],d['combined_gross'])
 x['effect_is_add']=(d['label_effect'].astype(str)=='ADD_EFFECT').astype(float)
 return x[BASE]

def model(seed):return HistGradientBoostingClassifier(max_iter=350,learning_rate=.06,max_leaf_nodes=31,l2_regularization=1.0,early_stopping=True,validation_fraction=.12,n_iter_no_change=30,random_state=seed)
def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);both=len(set(y.tolist()))==2
 return {'n':len(y),'positives':int(y.sum()),'positiveRate':float(y.mean()),'rocAuc':float(roc_auc_score(y,p)) if both else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None,'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1]))}
def bridge_metric(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);both=len(set(y.tolist()))==2
 return {'n':len(y),'positives':int(y.sum()),'positiveRate':float(y.mean()),'rocAuc':float(roc_auc_score(y,p)) if both else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None}

def main():
 h=eff.attach_effect(pd.read_csv(HIST),pd.read_csv(HAND),pd.read_csv(TAKER));h=h[h.label_effect.isin(EFFECTS)].copy();X=transform(h);sp=coord.split_markets(h);reps={};arts={}
 for label,short,seed in [('label_same_next1s','same',20260821),('label_opp_next1s','opp',20260822)]:
  m=model(seed);tr=h.market_id.astype(int).isin(sp['train']);m.fit(X.loc[tr],h.loc[tr,label].astype(int));art={'model':m,'features':BASE,'transformVersion':'SCALE_NORM_EFFECT_V1','label':label};path=SAME_ART if short=='same' else OPP_ART;joblib.dump(art,path);arts[short]=str(path);reps[short]={'splitMarkets':{k:len(v) for k,v in sp.items()}}
  for k,ms in sp.items():q=h.market_id.astype(int).isin(ms);p=m.predict_proba(X.loc[q])[:,1];reps[short][k]=met(h.loc[q,label].astype(int),p)
 # Fresh Target forward, never used in training.
 fresh=None
 if FRESH.exists() and FHAND.exists() and FTAKER.exists():
  z=eff.attach_effect(pd.read_csv(FRESH),pd.read_csv(FHAND),pd.read_csv(FTAKER));z=z[z.label_effect.isin(EFFECTS)].copy();Z=transform(z);fresh={}
  for short,label,path in [('same','label_same_next1s',SAME_ART),('opp','label_opp_next1s',OPP_ART)]:
   a=joblib.load(path);p=a['model'].predict_proba(Z)[:,1];fresh[short]=met(z[label].astype(int),p)
 # OUR synthetic bridge.
 o=pd.read_csv(OUR);raw=pd.DataFrame(index=o.index)
 for c in set([x[6:] for x in o.columns if x.startswith('state_')]): raw[c]=o['state_'+c]
 raw['label_effect']=o['syntheticEffect'];O=transform(raw);bridge={}
 ps=joblib.load(SAME_ART)['model'].predict_proba(O)[:,1];po=joblib.load(OPP_ART)['model'].predict_proba(O)[:,1]
 o=o.copy();o['pSameV1']=ps;o['pOppV1']=po
 for e in EFFECTS:
  q=o.syntheticEffect==e;r=o[q];sw=(r.oracleMtmBest=='SWITCH_OPPOSITE').astype(int);sa=(r.oracleMtmBest=='CONTINUE_SAME').astype(int)
  bridge[e]={'n':len(r),'meanPOpp':float(r.pOppV1.mean()),'meanPSame':float(r.pSameV1.mean()),'pOppVsSwitchMtmBest':bridge_metric(sw,r.pOppV1),'pSameVsContinueMtmBest':bridge_metric(sa,r.pSameV1),'spearmanPOppVsSwitchMinusSameMtm':float(r.pOppV1.corr(r.switchMinusSameMtm,method='spearman')),'spearmanPOppVsSwitchMinusPauseMtm':float(r.pOppV1.corr(r.switchMinusPauseMtm,method='spearman'))}
 rep={'reportVersion':'SCALE_NORMALIZED_EFFECT_CONDITIONED_REENTRY_V1','researchOnly':True,'runtimeTargetDataAllowed':False,'question':'Can a scale-normalized Target REPAIR/ADD re-entry student retain Target unseen/fresh performance and transfer better to OUR smaller-capital post-intervention states?','features':BASE,'historical':reps,'fresh':fresh,'ourSyntheticBridge':bridge,'artifacts':arts,'guards':['Only REPAIR_EFFECT and ADD_EFFECT are included; BUILD_FROM_FLAT is a separate sparse lifecycle.','Same HGB capacity as prior sequential V0; no hyperparameter sweep.','Scale-sensitive absolute gross/net/share totals are replaced by ratios/per-gross features.','OUR bridge cohort is not used to train or tune this model.','No threshold promotion; compare ranking only.']}
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
