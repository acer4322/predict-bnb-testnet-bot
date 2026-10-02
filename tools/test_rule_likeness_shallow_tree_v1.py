from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.tree import DecisionTreeClassifier,export_text
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data/research/r4_v0/hourly/rule_likeness_shallow_tree_v1.json'
TARGET=ROOT/'data/research/target_maker_direct_hazard_v1.csv';R4=ROOT/'data/research/r4_v0/hourly/r4_anticipated_responsibility_teacher_v1_rows.csv'
TN=['seconds_left','predict_up_bid','predict_up_ask','predict_up_mid','predict_down_bid','predict_down_ask','predict_down_mid','predict_up_spread','predict_down_spread','predict_mid_sum','predict_up_mid_edge']
TE=['spot_queue_imbalance','spot_taker_imbalance_250ms','spot_taker_imbalance_1s','spot_return_250ms_bps','spot_return_1s_bps','spot_return_3s_bps','spot_return_5s_bps','futures_queue_imbalance','futures_taker_imbalance_250ms','futures_taker_imbalance_1s','futures_return_250ms_bps','futures_return_1s_bps','futures_return_3s_bps','futures_return_5s_bps','perp_spot_basis_bps','spot_minus_strike_bps','chainlink_minus_strike_bps','spot_minus_chainlink_bps']
RLOCAL=['combined_abs_net','maker_abs_net','combined_paired_coverage','maker_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','maker_fills_1s','maker_fills_5s','maker_shares_5s','maker_shares_10s','taker_fills_1s','taker_fills_5s','taker_shares_5s','taker_shares_10s','p_maker_side','p_maker_opp','p_taker_1s','p_taker_3s','p_residual_wake','active_maker_orders','book_age_ms','predict_toward_side']
def met(y,p):return {'n':len(y),'rate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1]))}
def fit_tree(tr,te,features,label):
 imp=SimpleImputer(strategy='median');X=imp.fit_transform(tr[features]);Xt=imp.transform(te[features]);m=DecisionTreeClassifier(max_depth=3,min_samples_leaf=80,class_weight='balanced',random_state=20260826);m.fit(X,tr[label].astype(int));p=m.predict_proba(Xt)[:,1];return met(te[label].astype(int),p),export_text(m,feature_names=features,max_depth=3)
def split_by_market(d,midcol,timecol):
 mids=d.groupby(midcol)[timecol].min().sort_values().index.astype(int).tolist();a=int(len(mids)*.70);b=int(len(mids)*.85);return set(mids[:a]),set(mids[a:b]),set(mids[b:])
def main():
 t=pd.read_csv(TARGET,low_memory=False).sort_values(['decision_sampled_at_ms','market_id']);trm,vm,tm=split_by_market(t,'market_id','decision_sampled_at_ms');tout={}
 for h in (1,2,5):
  lab=f'label_next_inferred_placement_any_{h}s';tr=t[t.market_id.isin(trm)];te=t[t.market_id.isin(tm)];tout[str(h)]={}
  for name,feats in [('NATIVE',TN),('NATIVE_EXTERNAL',TN+TE)]:tout[str(h)][name]={'test':fit_tree(tr,te,feats,lab)[0],'rules':fit_tree(tr,te,feats,lab)[1]}
 r=pd.read_csv(R4).sort_values(['t','market_id']);rr,rv,rt=split_by_market(r,'market_id','t');mr,rule=fit_tree(r[r.market_id.isin(rr)],r[r.market_id.isin(rt)],RLOCAL,'y');rep={'version':'RULE_LIKENESS_SHALLOW_TREE_V1','method':'Single preregistered depth-3 balanced decision tree; no depth/threshold sweep. This is a descriptive rule-likeness teacher, not action authority.','targetDirectPlacement':tout,'r4AnticipatedResponsibility':{'test':mr,'rules':rule},'guards':['Chronological market split 70/15/15','Future labels only','Tree thresholds are descriptive learned splits and must not be promoted directly']};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False))
if __name__=='__main__':main()
