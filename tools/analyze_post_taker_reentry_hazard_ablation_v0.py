from __future__ import annotations
import json
from pathlib import Path
import joblib, numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, log_loss, roc_auc_score
import train_target_maker_taker_coordination_big_v1 as coord
import train_post_taker_maker_reentry_hazard_v0 as rr
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'; DATA=OUT/'post_taker_maker_reentry_hazard_v0.csv'; REPORT=OUT/'post_taker_maker_reentry_hazard_ablation_v0_report.json'
SETS={'CORE':coord.CORE+rr.CTX,'CORE_BOOK':coord.CORE+coord.BOOK+rr.CTX,'CORE_BOOK_LIFE':coord.CORE+coord.BOOK+coord.LIFE+rr.CTX,'FULL':coord.CORE+coord.BOOK+coord.LIFE+coord.ECON+rr.CTX,'FULL_PLUS_MEMORY':coord.CORE+coord.BOOK+coord.LIFE+coord.ECON+rr.CTX+rr.MEM}
def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);return {'n':len(y),'positiveRate':float(y.mean()),'rocAuc':float(roc_auc_score(y,p)),'averagePrecision':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1]))}
def main():
 df=pd.read_csv(DATA);sp=coord.split_markets(df);parts={k:df[df.market_id.astype(int).isin(v)].copy() for k,v in sp.items()};out={'reportVersion':'POST_TAKER_REENTRY_HAZARD_ABLATION_V0','splitMarkets':{k:len(v) for k,v in sp.items()},'sets':{}}
 for side in ('same','opp'):
  label=f'label_{side}_next1s';out['sets'][side]={}
  for j,(name,fs) in enumerate(SETS.items()):
   m=HistGradientBoostingClassifier(max_iter=350,learning_rate=.06,max_leaf_nodes=31,l2_regularization=1.0,early_stopping=True,validation_fraction=.12,n_iter_no_change=30,random_state=20260830+j+(0 if side=='same' else 100));m.fit(coord.numeric(parts['train'],fs),parts['train'][label].astype(int));r={'features':fs}
   for k in ('validation','test'):
    p=m.predict_proba(coord.numeric(parts[k],fs))[:,1];r[k]=met(parts[k][label].astype(int),p)
   out['sets'][side][name]=r
 REPORT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
