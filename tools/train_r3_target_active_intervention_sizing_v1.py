from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
import joblib
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/strategy_target_cross_compare_v3_unified_v0_effects.csv'
OUT=ROOT/'data/research/r3_v0'; OUT.mkdir(parents=True,exist_ok=True)
FEATURES=['seconds_left','target_taker_price','target_prior_up_shares','target_prior_down_shares','target_prior_net_shares','before_worst_floor_usdt','our_direction_strength','our_net_shares','our_risk_deficit_usdt','our_worst_case_pnl_usdt','our_repair_ask','our_maker_hazard_probability']
df=pd.read_csv(SRC)
df=df[df.effect_class.isin(['REPAIR_EFFECT','ADD_EFFECT'])].copy()
df['prior_abs']=df.target_prior_net_shares.abs()
df=df[(df.prior_abs>1e-6)&(df.target_taker_shares>0)].copy()
df['fraction']=(df.target_taker_shares/df.prior_abs).clip(0,2.0)
markets=sorted(df.market_id.unique())
n=len(markets); a=max(1,int(n*.65)); b=max(a+1,int(n*.82))
splits={'train':set(markets[:a]),'valid':set(markets[a:b]),'test':set(markets[b:])}
report={'version':'R3_TARGET_ACTIVE_INTERVENTION_SIZING_V1','researchOnly':True,'fixed18Forbidden':True,'fullGapPolicyForbidden':True,'source':str(SRC.relative_to(ROOT)),'features':FEATURES,'marketSplit':{k:[int(x) for x in sorted(v)] for k,v in splits.items()},'effects':{}}
for effect in ['REPAIR_EFFECT','ADD_EFFECT']:
 d=df[df.effect_class==effect].copy()
 tr=d[d.market_id.isin(splits['train'])]; va=d[d.market_id.isin(splits['valid'])]; te=d[d.market_id.isin(splits['test'])]
 med=float(tr.fraction.median())
 Xtr=tr[FEATURES].replace([np.inf,-np.inf],np.nan).fillna(0.0); ytr=tr.fraction
 model=HistGradientBoostingRegressor(max_iter=250,learning_rate=.05,max_leaf_nodes=15,l2_regularization=1.0,random_state=20260825).fit(Xtr,ytr)
 def ev(x):
  if len(x)==0:return {'n':0}
  X=x[FEATURES].replace([np.inf,-np.inf],np.nan).fillna(0.0); y=x.fraction.to_numpy(); pr=np.clip(model.predict(X),0,2)
  raw=np.abs(pr-y); base=np.abs(med-y)
  return {'n':len(x),'markets':int(x.market_id.nunique()),'maeFraction':float(raw.mean()),'medianAeFraction':float(np.median(raw)),'baselineMedianFraction':med,'baselineMaeFraction':float(base.mean()),'maeLiftVsMedian':float(base.mean()-raw.mean()),'predMedian':float(np.median(pr)),'labelMedian':float(np.median(y)),'labelP90':float(np.quantile(y,.9))}
 report['effects'][effect]={'train':ev(tr),'valid':ev(va),'test':ev(te),'targetShares':{'median':float(d.target_taker_shares.median()),'p90':float(d.target_taker_shares.quantile(.9))},'fraction':{'median':float(d.fraction.median()),'p75':float(d.fraction.quantile(.75)),'p90':float(d.fraction.quantile(.9))}}
 joblib.dump({'version':report['version'],'effect':effect,'features':FEATURES,'model':model,'clip':[0.0,2.0],'researchOnly':True},OUT/f'r3_target_active_{effect.lower()}_fraction_hgb_v1.joblib')
(OUT/'r3_target_active_intervention_sizing_v1_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
