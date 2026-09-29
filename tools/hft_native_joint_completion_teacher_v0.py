from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
TRAIN_SWEEPS=[f'hft_native_joint_pair_sweep_p0{i}_train_v0.json' for i in (1,2,3,4)]
TRAIN_SOURCES=[f'recent_execution_placement_p0{i}.json' for i in (1,2,3,4)]
HOLDS={
 'freshA':('hft_native_joint_pair_sweep_freshA10_v0.json','hft_native_freshA10_collector_v0.json'),
 'freshB':('hft_native_joint_pair_sweep_freshB10_v0.json','hft_native_freshB10_collector_v0.json'),
 'freshC':('hft_native_joint_pair_sweep_freshC5_v0.json','hft_native_freshC5_collector_v0.json'),
}
BASE=['maker_net','maker_abs_net','maker_paired_coverage','combined_net','combined_abs_net','combined_paired_coverage','worst_case_floor','best_case_pnl','current_spread_ticks','current_bid','current_ask']
FEATURES=BASE+['offset','pair_quote_sum','pair_edge_if_both','up_price','down_price']

def index_source(name):
 d=json.loads((OUT/name).read_text(encoding='utf-8')); idx={}
 for r in d.get('placementRows',[]): idx[(int(r['market_id']),int(r['checkpoint_ms']))]=r
 return idx

def rows(sweep_name, source_name):
 sw=json.loads((OUT/sweep_name).read_text(encoding='utf-8')); idx=index_source(source_name); out=[]
 for r in sw['rows']:
  k=(int(r['marketId']),int(r['checkpointMs'])); st=idx.get(k,{})
  for a in r['actions']:
   if a.get('invalid'): continue
   uf=float(a.get('upFilled') or 0)>1e-9; df=float(a.get('downFilled') or 0)>1e-9
   z={f:st.get(f,math.nan) for f in BASE}; z.update({'market_id':k[0],'checkpoint_ms':k[1],'offset':float(a['offset']),'pair_quote_sum':float(a['pairQuoteSum']),'pair_edge_if_both':1.0-float(a['pairQuoteSum']),'up_price':float(a['upPrice']),'down_price':float(a['downPrice']),'label_both':int(uf and df),'label_one':int(uf ^ df),'reward':float(a.get('portfolioMtm5s') or 0.0)}); out.append(z)
 return pd.DataFrame(out)

def metrics(model,df,label):
 y=df[label].astype(int).to_numpy(); p=model.predict_proba(df[FEATURES])[:,1]
 return {'n':len(df),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None}

def main():
 tr=pd.concat([rows(a,b) for a,b in zip(TRAIN_SWEEPS,TRAIN_SOURCES)],ignore_index=True)
 mb=HistGradientBoostingClassifier(max_iter=180,max_leaf_nodes=12,min_samples_leaf=12,l2_regularization=2.0,random_state=17).fit(tr[FEATURES],tr['label_both'])
 mo=HistGradientBoostingClassifier(max_iter=180,max_leaf_nodes=12,min_samples_leaf=12,l2_regularization=2.0,random_state=19).fit(tr[FEATURES],tr['label_one'])
 rep={'version':'HFT_NATIVE_JOINT_COMPLETION_TEACHER_V0','researchOnly':True,'dreamFillAllowed':False,'features':FEATURES,'train':{'both':metrics(mb,tr,'label_both'),'one':metrics(mo,tr,'label_one')},'holdouts':{}}
 for n,(a,b) in HOLDS.items():
  df=rows(a,b); rep['holdouts'][n]={'both':metrics(mb,df,'label_both'),'one':metrics(mo,df,'label_one')}
 (OUT/'hft_native_joint_completion_teacher_v0_report.json').write_text(json.dumps(rep,indent=2,allow_nan=True),encoding='utf-8')
 print(json.dumps(rep,indent=2))
if __name__=='__main__': main()
