from __future__ import annotations
import json, math
from pathlib import Path
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
TRAIN=[f'recent_execution_placement_p0{i}.json' for i in (1,2,3,4)]
HOLDS={'freshA':'hft_native_freshA10_collector_v0.json','freshB':'hft_native_freshB10_collector_v0.json','freshC':'hft_native_freshC5_collector_v0.json'}
FEATURES=['maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage','combined_net','combined_abs_net','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','current_bid','current_ask','current_spread_ticks','quote_offset_ticks','public_depletion_ratio','order_age_ms','active_opp_count','maker_fills_1s','maker_fills_5s','maker_shares_5s']
def build(name):
 d=json.loads((OUT/name).read_text(encoding='utf-8')); a=[]
 for mid in d.get('markets',[]):
  rr=sorted([z for z in d.get('stateRows',[]) if int(z['market_id'])==int(mid)],key=lambda z:int(z['checkpoint_ms']))
  by={}
  for z in rr:
   t=int(z['checkpoint_ms']); off=abs(float(z.get('quote_offset_ticks') or 999)); old=by.get(t); oldoff=abs(float(old.get('quote_offset_ticks') or 999)) if old else 1e9
   if old is None or off<oldoff: by[t]=z
  seq=[by[t] for t in sorted(by)]
  for i,z in enumerate(seq):
   cur=float(z.get('maker_abs_net') or 0)
   if cur<18-1e-9: continue
   t=int(z['checkpoint_ms']); out={f:z.get(f,math.nan) for f in FEATURES}; out.update({'market_id':int(mid),'checkpoint_ms':t})
   for h in (5000,15000):
    fut=[q for q in seq[i+1:] if int(q['checkpoint_ms'])<=t+h]; mn=min([float(q.get('maker_abs_net') or 0) for q in fut],default=cur); out[f'label_repair{h//1000}s']=int(mn<=cur-18+1e-9)
   a.append(out)
 return pd.DataFrame(a)
def met(m,df,ycol):
 y=df[ycol].astype(int).to_numpy(); p=m.predict_proba(df[FEATURES])[:,1]; return {'n':len(df),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None}
def main():
 tr=pd.concat([build(x) for x in TRAIN],ignore_index=True); rep={'version':'HFT_NATIVE_RESIDUAL_REPAIRABILITY_V0','researchOnly':True,'dreamFillAllowed':False,'features':FEATURES,'trainRows':len(tr),'train':{},'holdouts':{}}; mods={}
 for sec in (5,15):
  y=f'label_repair{sec}s'; m=HistGradientBoostingClassifier(max_iter=180,max_leaf_nodes=16,min_samples_leaf=30,l2_regularization=3,random_state=100+sec).fit(tr[FEATURES],tr[y]); mods[sec]=m; rep['train'][str(sec)]=met(m,tr,y)
 for n,f in HOLDS.items():
  df=build(f); rep['holdouts'][n]={str(sec):met(mods[sec],df,f'label_repair{sec}s') for sec in (5,15)}
 (OUT/'hft_native_residual_repairability_v0_report.json').write_text(json.dumps(rep,indent=2,allow_nan=True),encoding='utf-8'); print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
