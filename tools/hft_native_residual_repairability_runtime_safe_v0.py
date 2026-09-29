from __future__ import annotations
import json,math,joblib
from pathlib import Path
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
TRAIN=[f'recent_execution_placement_p0{i}.json' for i in (1,2,3,4)]
HOLDS={'freshA':'hft_native_freshA10_collector_v0.json','freshB':'hft_native_freshB10_collector_v0.json','freshC':'hft_native_freshC5_collector_v0.json'}
FEATURES=['maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage','combined_net','combined_abs_net','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','current_bid','current_ask','current_spread_ticks','seconds_left','direction_score']
def build(name):
 d=json.loads((OUT/name).read_text()); a=[]
 for mid in d.get('markets',[]):
  rr=sorted([z for z in d.get('stateRows',[]) if int(z['market_id'])==int(mid)],key=lambda z:int(z['checkpoint_ms'])); by={}
  for z in rr:
   t=int(z['checkpoint_ms']); off=abs(float(z.get('quote_offset_ticks') or 999)); old=by.get(t); oo=abs(float(old.get('quote_offset_ticks') or 999)) if old else 1e9
   if old is None or off<oo: by[t]=z
  seq=[by[t] for t in sorted(by)]
  for i,z in enumerate(seq):
   cur=float(z.get('maker_abs_net') or 0)
   if cur<18-1e-9: continue
   t=int(z['checkpoint_ms']); row={f:z.get(f,math.nan) for f in FEATURES}; row.update({'market_id':mid,'checkpoint_ms':t})
   fut=[q for q in seq[i+1:] if int(q['checkpoint_ms'])<=t+15000]; mn=min([float(q.get('maker_abs_net') or 0) for q in fut],default=cur); row['y']=int(mn<=cur-18+1e-9); a.append(row)
 return pd.DataFrame(a)
def met(m,d):
 y=d.y.astype(int).to_numpy(); p=m.predict_proba(d[FEATURES])[:,1]; return {'n':len(d),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p))}
def main():
 tr=pd.concat([build(x) for x in TRAIN],ignore_index=True);
 global FEATURES
 FEATURES=[f for f in FEATURES if pd.to_numeric(tr[f],errors='coerce').nunique(dropna=True)>1];
 m=HistGradientBoostingClassifier(max_iter=180,max_leaf_nodes=16,min_samples_leaf=30,l2_regularization=3,random_state=115).fit(tr[FEATURES],tr.y); probs=m.predict_proba(tr[FEATURES])[:,1]; thr=float(pd.Series(probs).quantile(.60)); rep={'version':'HFT_NATIVE_RESIDUAL_REPAIRABILITY_RUNTIME_SAFE_V0','features':FEATURES,'thresholdTop40':thr,'train':met(m,tr),'holdouts':{n:met(m,build(f)) for n,f in HOLDS.items()}}; joblib.dump({'model':m,'features':FEATURES,'thresholdTop40':thr},OUT/'hft_native_residual_repairability_runtime_safe_v0.joblib'); (OUT/'hft_native_residual_repairability_runtime_safe_v0_report.json').write_text(json.dumps(rep,indent=2)); print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
