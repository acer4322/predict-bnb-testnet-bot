from __future__ import annotations
import json
from pathlib import Path
import numpy as np, pandas as pd, joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
OPEN=D/'open_order_fill_lifecycle_v0_dataset.csv'; FILL=D/'r2_fill_quality_explicit_v1_dataset.csv'
ART=D/'r2_queue_option_value_v0.joblib'; REP=D/'r2_queue_option_value_v0_report.json'; OUT=D/'r2_queue_option_value_v0_dataset.csv'
FEATURES=['side_is_up','order_age_ms','quote_price','status_none','status_new','status_partial','cum_exec_qty','remaining_qty','remaining_ratio','partial_fill_ratio','active_same_count','active_opp_count','quote_offset_ticks','current_bid','current_ask','current_spread_ticks','initial_depth','public_cum_depletion','public_depletion_ratio','public_any_depletion','maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage','taker_gross','taker_net','taker_abs_net','taker_paired_coverage','combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','last_maker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','maker_fills_1s','maker_fills_5s','maker_fills_10s','maker_shares_5s','maker_shares_10s']
def met(y,p):
 y=np.asarray(y,int); p=np.clip(np.asarray(p,float),1e-7,1-1e-7)
 return {'n':len(y),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum() else None,'logLoss':float(log_loss(y,p,labels=[0,1]))}
def main():
 o=pd.read_csv(OPEN,low_memory=False); f=pd.read_csv(FILL,low_memory=False)
 o=o[pd.to_numeric(o.censored_5s,errors='coerce').fillna(1).astype(int)==0].copy()
 by={}
 for (m,oid),g in f.sort_values(['market_id','order_id','fill_ms']).groupby(['market_id','order_id']):
  by[(int(m),str(oid))]=[{'t':int(r.fill_ms),'paired':int(r.label_paired_improves),'m1':float(r.label_markout1s_ticks) if pd.notna(r.label_markout1s_ticks) else None} for _,r in g.iterrows()]
 useful=[]; toxic=[]; matched=0; unresolved=0
 for _,r in o.iterrows():
  cp=int(r.checkpoint_ms); key=(int(r.market_id),str(r.order_id)); fill=int(r.label_fill_5s)
  if not fill: useful.append(0); toxic.append(0); continue
  z=[x for x in by.get(key,[]) if cp < x['t'] <= cp+5000]
  if not z: useful.append(np.nan); toxic.append(np.nan); unresolved+=1; continue
  x=z[0]; matched+=1
  # Natural no-tuning semantics: useful iff it improves paired inventory and 1s markout is non-adverse.
  # Toxic iff 1s markout is adverse. Future data is label-only.
  if x['m1'] is None:
   useful.append(np.nan); toxic.append(np.nan); unresolved+=1; continue
  useful.append(int(x['paired']==1 and x['m1']>=0.0)); toxic.append(int(x['m1']<0.0))
 o['label_useful_queue_fill_5s']=useful; o['label_toxic_queue_fill_5s']=toxic; o=o[o.label_useful_queue_fill_5s.notna()].copy(); o.to_csv(OUT,index=False)
 mids=sorted(o.market_id.unique(), key=lambda m:int(o[o.market_id==m].checkpoint_ms.min())); a=int(len(mids)*.70); b=int(len(mids)*.85); sets={'train':set(mids[:a]),'validation':set(mids[a:b]),'test':set(mids[b:])}
 tr=o[o.market_id.isin(sets['train'])]
 def fit(label):
  m=Pipeline([('imp',SimpleImputer(strategy='median')),('clf',HistGradientBoostingClassifier(max_depth=3,learning_rate=.045,max_iter=180,l2_regularization=3.0,min_samples_leaf=20,random_state=20260822))]); m.fit(tr[FEATURES],tr[label].astype(int)); return m
 mu=fit('label_useful_queue_fill_5s'); mt=fit('label_toxic_queue_fill_5s'); metrics={'useful':{},'toxic':{}}
 for k,s in sets.items():
  x=o[o.market_id.isin(s)]
  metrics['useful'][k]=met(x.label_useful_queue_fill_5s.astype(int),mu.predict_proba(x[FEATURES])[:,1]); metrics['toxic'][k]=met(x.label_toxic_queue_fill_5s.astype(int),mt.predict_proba(x[FEATURES])[:,1])
 joblib.dump({'version':'R2_QUEUE_OPTION_VALUE_V0','features':FEATURES,'usefulModel':mu,'toxicModel':mt,'trainingMarkets':sorted(sets['train']),'semantics':{'useful':'P(actual fill within 5s AND paired inventory improves AND +1s markout >= 0 | strict-past working-order state)','toxic':'P(actual fill within 5s AND +1s markout < 0 | strict-past working-order state)'},'runtimeTargetAllowed':False,'winnerPnlAllowed':False},ART)
 rep={'version':'R2_QUEUE_OPTION_VALUE_V0','researchOnly':True,'runtimeAuthority':False,'rows':len(o),'markets':o.market_id.nunique(),'matchedPositiveFillRows':matched,'unresolvedDropped':unresolved,'splitMarkets':{k:len(v) for k,v in sets.items()},'metrics':metrics,'interpretation':'Two-head queue optionality estimator. Do not collapse to a weighted scalar yet; first test whether useful-vs-toxic predictions explain KEEP value in R2 repair contexts.','guardrails':['Actual HftBacktest fills only','Future paired/markout labels never runtime inputs','No Target/winner/settlement/PnL runtime input','Chronological market split','No score-weight or threshold sweep']}; REP.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False))
if __name__=='__main__': main()
