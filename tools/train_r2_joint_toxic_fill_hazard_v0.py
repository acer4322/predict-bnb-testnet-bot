from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np,pandas as pd,joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,precision_score,recall_score,balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
OPEN=D/'open_order_fill_lifecycle_v0_dataset.csv';FILL=D/'r2_fill_quality_explicit_v1_dataset.csv';ART=D/'r2_joint_toxic_fill_hazard_v0.joblib';REP=D/'r2_joint_toxic_fill_hazard_v0_report.json';OUT=D/'r2_joint_toxic_fill_hazard_v0_dataset.csv'
FEATURES=['side_is_up','order_age_ms','quote_price','status_none','status_new','status_partial','cum_exec_qty','remaining_qty','remaining_ratio','partial_fill_ratio','active_same_count','active_opp_count','quote_offset_ticks','current_bid','current_ask','current_spread_ticks','initial_depth','public_cum_depletion','public_depletion_ratio','public_any_depletion','maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage','taker_gross','taker_net','taker_abs_net','taker_paired_coverage','combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','last_maker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','maker_fills_1s','maker_fills_5s','maker_fills_10s','maker_shares_5s','maker_shares_10s']
def metrics(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float); pred=(p>=.5).astype(int)
 return {'n':len(y),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum() else None,'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1])),'precisionAt0p5':float(precision_score(y,pred,zero_division=0)),'recallAt0p5':float(recall_score(y,pred,zero_division=0)),'balancedAccuracyAt0p5':float(balanced_accuracy_score(y,pred))}
def main():
 o=pd.read_csv(OPEN,low_memory=False); f=pd.read_csv(FILL,low_memory=False); o=o[pd.to_numeric(o.censored_1s,errors='coerce').fillna(1).astype(int)==0].copy(); f=f.sort_values(['market_id','order_id','fill_ms'])
 by={}
 for (m,oid),g in f.groupby(['market_id','order_id']): by[(int(m),str(oid))]=[(int(r.fill_ms),float(r.label_markout1s_ticks)) for _,r in g.iterrows() if pd.notna(r.label_markout1s_ticks)]
 labels=[]; matched=0; dropped=0
 for _,r in o.iterrows():
  fill=int(r.label_fill_1s); cp=int(r.checkpoint_ms); key=(int(r.market_id),str(r.order_id))
  if not fill: labels.append(0); continue
  z=[(t,m) for t,m in by.get(key,[]) if cp<t<=cp+1000]
  if not z: labels.append(np.nan);dropped+=1;continue
  matched+=1;labels.append(int(z[0][1]<0))
 o['label_toxic_fill_1s']=labels;o=o[o.label_toxic_fill_1s.notna()].copy();o.to_csv(OUT,index=False)
 mids=sorted(o.groupby('market_id').checkpoint_ms.max().to_dict(),key=lambda m:o[o.market_id==m].checkpoint_ms.max());a=int(len(mids)*.70);b=int(len(mids)*.85);sets={'train':set(mids[:a]),'validation':set(mids[a:b]),'test':set(mids[b:])}
 tr=o[o.market_id.isin(sets['train'])];model=Pipeline([('imp',SimpleImputer(strategy='median')),('clf',HistGradientBoostingClassifier(max_depth=3,learning_rate=.045,max_iter=180,l2_regularization=3.0,min_samples_leaf=20,random_state=20260822))]);model.fit(tr[FEATURES],tr.label_toxic_fill_1s.astype(int));mets={}
 for k,s in sets.items():
  x=o[o.market_id.isin(s)];mets[k]=metrics(x.label_toxic_fill_1s.astype(int),model.predict_proba(x[FEATURES])[:,1])
 joblib.dump({'version':'R2_JOINT_TOXIC_FILL_HAZARD_V0','model':model,'features':FEATURES,'trainingMarkets':sorted(sets['train']),'runtimeTargetAllowed':False,'winnerPnlAllowed':False,'semantics':'P(next 1s actual HFT fill AND +1s post-fill markout <0 | strict-past open-order state)'},ART)
 rep={'version':'R2_JOINT_TOXIC_FILL_HAZARD_V0','researchOnly':True,'rows':len(o),'markets':o.market_id.nunique(),'positive':int(o.label_toxic_fill_1s.sum()),'matchedPositiveFillRows':matched,'unmatchedFillRowsDropped':dropped,'splitMarkets':{k:len(v) for k,v in sets.items()},'metrics':mets,'guardrails':['Actual HftBacktest fill labels only','Future markout is label only','No Target/winner/PnL runtime input','Chronological market split','Natural 0.5 action boundary only; no sweep']};REP.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False))
if __name__=='__main__':main()
