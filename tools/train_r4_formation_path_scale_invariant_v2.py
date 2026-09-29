from __future__ import annotations
import json,joblib
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
try:
 from interpret.glassbox import ExplainableBoostingClassifier
except Exception:
 ExplainableBoostingClassifier=None
ROOT=Path(__file__).resolve().parents[1];SRC=ROOT/'data/research/r4_v0/hourly/r4_formation_path_value_teacher_v1_rows.csv';OUT=ROOT/'data/research/r4_v0/hourly/r4_formation_path_scale_invariant_v2.json';MOD=ROOT/'data/research/r4_v0/hourly/r4_formation_path_scale_invariant_v2.joblib'
d=pd.read_csv(SRC);eps=1e-9;gross=2*d.pre_base+d.pre_surplus
# Scale-invariant semantic state: economic ratios + realized/parent-flow fractions + event cadence + phase.
d['floor_per_gross']=d.pre_floor/(gross+eps);d['upside_per_gross']=d.pre_upside/(gross+eps)
for c in ['weak_maker_shares_5s','weak_maker_shares_15s','weak_maker_shares_30s','surplus_maker_shares_15s','surplus_maker_shares_30s','weak_taker_shares_15s','surplus_taker_shares_15s','floor_change_5s','floor_change_15s','upside_change_5s','upside_change_15s','surplus_change_5s','surplus_change_15s']:
 d[c+'_per_gross']=d[c]/(gross+eps)
features=['seconds_left','pre_surplus_ratio','pre_floor_per_base','pre_upside_per_surplus','floor_per_gross','upside_per_gross','last_price','last_role_taker','events_5s','events_15s','events_30s','maker_events_15s','taker_events_15s','parent_weak_dominance_15s','parent_weak_dominance_30s','seconds_from_first_event']+[c+'_per_gross' for c in ['weak_maker_shares_5s','weak_maker_shares_15s','weak_maker_shares_30s','surplus_maker_shares_15s','surplus_maker_shares_30s','weak_taker_shares_15s','surplus_taker_shares_15s','floor_change_5s','floor_change_15s','upside_change_5s','upside_change_15s','surplus_change_5s','surplus_change_15s']]
markets=d[['market_id','resolved_at_ms']].drop_duplicates().sort_values('resolved_at_ms').reset_index(drop=True);a=int(len(markets)*.6);b=int(len(markets)*.8);tr=set(markets.market_id.iloc[:a]);va=set(markets.market_id.iloc[a:b]);te=set(markets.market_id.iloc[b:]);X=d[features].replace([np.inf,-np.inf],np.nan).fillna(0);y=d.y.astype(int);idxtr=d.market_id.isin(tr);idxv=d.market_id.isin(va);idxt=d.market_id.isin(te)
def auc(y,p):return float(roc_auc_score(y,p)) if len(set(y))>1 else None
models={};results={}
def fit(name,m):
 m.fit(X.loc[idxtr],y.loc[idxtr]);models[name]=m;res={}
 for s,idx in [('val',idxv),('test',idxt)]:
  p=m.predict_proba(X.loc[idx])[:,1];yy=y.loc[idx];z=pd.DataFrame({'m':d.loc[idx,'market_id'].values,'y':yy.values,'p':p}).groupby('m').agg(y=('y','first'),p=('p','mean')).reset_index();res[s]={'rows':int(idx.sum()),'markets':int(z.shape[0]),'rate':float(yy.mean()),'auc':auc(yy,p),'ap':float(average_precision_score(yy,p)),'logLoss':float(log_loss(yy,p,labels=[0,1])),'marketMeanAuc':auc(z.y,z.p),'marketMeanAP':float(average_precision_score(z.y,z.p))}
 results[name]=res
fit('HGB',HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=220,l2_regularization=3.,random_state=92,class_weight='balanced'))
if ExplainableBoostingClassifier:fit('EBM',ExplainableBoostingClassifier(interactions=4,max_bins=64,max_rounds=700,learning_rate=.03,min_samples_leaf=8,random_state=92))
joblib.dump({'version':'R4_FORMATION_PATH_SCALE_INVARIANT_V2','features':features,'models':models,'actionAuthority':False},MOD)
out={'version':'R4_FORMATION_PATH_SCALE_INVARIANT_V2','source':'R4_FORMATION_PATH_VALUE_TEACHER_V1 rows/labels; feature transform only','definition':'Remove absolute position/notional scale from Target formation-path teacher. Use economic ratios, flow-per-gross, change-per-gross, cadence, price and phase so Target intent can transfer across OUR smaller HFT notional. Same chronological market split; no threshold sweep.','features':features,'splitMarkets':{'train':len(tr),'val':len(va),'test':len(te)},'results':results,'researchOnly':True,'actionAuthority':False};OUT.write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
