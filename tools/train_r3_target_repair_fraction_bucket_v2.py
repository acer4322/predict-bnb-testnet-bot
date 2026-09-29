from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd,joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score,balanced_accuracy_score,classification_report
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'data/research/r3_v0'
SRC=ROOT/'data/research/strategy_target_cross_compare_v3_unified_v0_effects.csv'
F=['seconds_left','target_taker_price','target_prior_up_shares','target_prior_down_shares','target_prior_net_shares','before_worst_floor_usdt','our_direction_strength','our_net_shares','our_risk_deficit_usdt','our_worst_case_pnl_usdt','our_repair_ask','our_maker_hazard_probability']
d=pd.read_csv(SRC); d=d[d.effect_class.eq('REPAIR_EFFECT')].copy(); d['prior_abs']=d.target_prior_net_shares.abs(); d=d[(d.prior_abs>1e-6)&(d.target_taker_shares>0)].copy(); d['fraction']=(d.target_taker_shares/d.prior_abs).clip(0,2)
# preregistered structural bins from target distribution, not tuned on pnl: micro<=2%, small 2-7%, medium 7-15%, large>15%
bins=[-1,0.02,0.07,0.15,10]; labels=['MICRO','SMALL','MEDIUM','LARGE']; d['bucket']=pd.cut(d.fraction,bins=bins,labels=labels).astype(str)
markets=sorted(d.market_id.unique()); a=max(1,int(len(markets)*.65)); b=max(a+1,int(len(markets)*.82)); sp={'train':set(markets[:a]),'valid':set(markets[a:b]),'test':set(markets[b:])}
tr=d[d.market_id.isin(sp['train'])]; X=tr[F].replace([np.inf,-np.inf],np.nan).fillna(0); y=tr.bucket
m=HistGradientBoostingClassifier(max_iter=250,learning_rate=.05,max_leaf_nodes=15,l2_regularization=1,class_weight='balanced',random_state=20260825).fit(X,y)
def ev(x):
 X=x[F].replace([np.inf,-np.inf],np.nan).fillna(0); y=x.bucket; p=m.predict(X); maj=tr.bucket.value_counts().idxmax()
 return {'n':len(x),'markets':int(x.market_id.nunique()),'accuracy':float(accuracy_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,p)),'majorityClass':maj,'majorityAccuracy':float((y==maj).mean()),'actualRates':x.bucket.value_counts(normalize=True).to_dict(),'predRates':pd.Series(p).value_counts(normalize=True).to_dict(),'report':classification_report(y,p,output_dict=True,zero_division=0)}
r={'version':'R3_TARGET_REPAIR_FRACTION_BUCKET_V2','researchOnly':True,'fixed18Forbidden':True,'bins':{'MICRO':'<=2%','SMALL':'2-7%','MEDIUM':'7-15%','LARGE':'>15%'},'features':F,'split':{k:[int(z) for z in sorted(v)] for k,v in sp.items()},'train':ev(tr),'valid':ev(d[d.market_id.isin(sp['valid'])]),'test':ev(d[d.market_id.isin(sp['test'])])}
joblib.dump({'version':r['version'],'features':F,'model':m,'bins':bins,'labels':labels,'researchOnly':True},OUT/'r3_target_repair_fraction_bucket_hgb_v2.joblib'); (OUT/'r3_target_repair_fraction_bucket_v2_report.json').write_text(json.dumps(r,indent=2,ensure_ascii=False),encoding='utf-8'); print(json.dumps(r,ensure_ascii=False))
