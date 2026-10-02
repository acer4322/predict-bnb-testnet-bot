from __future__ import annotations
import argparse,json,math
from pathlib import Path
import numpy as np,pandas as pd,joblib
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestRegressor,ExtraTreesRegressor,HistGradientBoostingRegressor,RandomForestClassifier,ExtraTreesClassifier,HistGradientBoostingClassifier
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
import sys
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hft_native_constrained_rank_v4 as v4
from tools import hft_native_timegrid_inventory_value_v3 as v3
EPS=1e-9

def prep(contract,dataset):
 ids={s:{int(x['marketId']) for x in contract[s]} for s in ('train','validation','holdout')}; allids=set().union(*ids.values())
 rows=v3.load_rows(dataset,allids); frames={s:rows[rows.market_id.isin(ids[s])].copy() for s in ids}
 ti,ts,tm,tmeta=v4.fit_target_regime(); raw=v4.raw_timegrid_lookup(dataset); c={}
 for s in frames:c[s],_=v4.build_candidates(frames[s],raw,ti,ts,tm)
 return frames,c,tmeta

def matrix_fit(train,features):
 imp=SimpleImputer(strategy='median'); sc=StandardScaler(); x=sc.fit_transform(imp.fit_transform(train[features].replace([np.inf,-np.inf],np.nan))); return imp,sc,x

def transform(df,features,imp,sc):return sc.transform(imp.transform(df[features].replace([np.inf,-np.inf],np.nan)))
def evaluate(cand,score):
 d=cand.copy();d['score']=score; rew=0.;acts=neg=pos=fill=0;oracle=0.;rows=[]
 for (m,t),g in d.groupby(['market_id','checkpoint_ms'],sort=True):
  o=g.sort_values(['reward_mtm','is_wait_action'],ascending=[False,False]).iloc[0]; oracle+=float(o.reward_mtm)
  z=g.sort_values(['score','is_wait_action'],ascending=[False,False]).iloc[0]; r=float(z.reward_mtm); a=str(z.action)
  if a!='WAIT': acts+=1; fill+=int(float(z.filled_shares)>EPS);pos+=int(r>EPS);neg+=int(r<-EPS)
  rew+=r;rows.append({'marketId':int(m),'checkpointMs':int(t),'action':a,'reward':r,'score':float(z.score)})
 return {'reward':rew,'oracle':oracle,'capture':rew/oracle if oracle>EPS else None,'acts':acts,'filledActs':fill,'positiveActs':pos,'negativeActs':neg,'rows':rows}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--contract',default='hft_native_constrained_rank_v4_preregistered.json');ap.add_argument('--dataset',default='hft_native_constrained_rank_unused90_v4.json');ap.add_argument('--output',default='hft_execution_automl_rank_v1_report.json');ap.add_argument('--model-output',default='hft_execution_automl_rank_v1.joblib');a=ap.parse_args()
 contract=json.loads((BASE/a.contract).read_text());frames,c,tmeta=prep(contract,a.dataset);features=[*v4.BASE_RANK_FEATURES,*v4.TARGET_ONEHOT_FEATURES];imp,sc,x=matrix_fit(c['train'],features); y=c['train'].reward_mtm.to_numpy(float)
 candidates=[]
 # Direct reward models: compact AutoML search, validation-only selection.
 specs=[
 ('extra_trees_200_leaf5',ExtraTreesRegressor(n_estimators=200,min_samples_leaf=5,max_features=.7,n_jobs=-1,random_state=20260823)),
 ('extra_trees_300_leaf10',ExtraTreesRegressor(n_estimators=300,min_samples_leaf=10,max_features=1.0,n_jobs=-1,random_state=20260823)),
 ('rf_200_leaf5',RandomForestRegressor(n_estimators=200,min_samples_leaf=5,max_features=.7,n_jobs=-1,random_state=20260823)),
 ('hist_d3_l05',HistGradientBoostingRegressor(max_depth=3,learning_rate=.05,max_iter=180,l2_regularization=5,random_state=20260823)),
 ('hist_d2_l03',HistGradientBoostingRegressor(max_depth=2,learning_rate=.03,max_iter=250,l2_regularization=10,random_state=20260823))]
 for name,model in specs:
  model.fit(x,y); ev={s:evaluate(c[s],model.predict(transform(c[s],features,imp,sc))) for s in ('train','validation')}; candidates.append((name,model,ev))
 # Pairwise logistic family.
 px,py=v4.pairwise_examples(c['train'],x)
 for C in (.05,.1,.25,.5,1.0):
  model=LogisticRegression(C=C,max_iter=2000,fit_intercept=False,random_state=20260823);model.fit(px,py);ev={s:evaluate(c[s],model.decision_function(transform(c[s],features,imp,sc))) for s in ('train','validation')};candidates.append((f'pair_logit_C{C}',model,ev))
 def key(item):
  e=item[2]['validation']; return (e['reward']>0 and e['negativeActs']==0,e['reward'], -e['negativeActs'], e['capture'] or -1)
 candidates.sort(key=key,reverse=True);name,model,ev=candidates[0]; hold=evaluate(c['holdout'],model.decision_function(transform(c['holdout'],features,imp,sc)) if name.startswith('pair_') else model.predict(transform(c['holdout'],features,imp,sc)))
 decision='KEEP' if hold['oracle']>EPS and hold['reward']>EPS and hold['negativeActs']==0 else ('NEED_MORE_DATA' if hold['oracle']<=EPS else 'REJECT')
 report={'version':'HFT_EXECUTION_AUTOML_RANK_V1','researchOnly':True,'dreamFillAllowed':False,'selection':'validation only; holdout opened once after winner freeze','objectivePriority':['validation reward > 0 AND zero negative fills','validation reward','fewer negative fills','oracle capture'],'searchSpace':[x[0] for x in candidates],'winner':name,'validation':ev['validation'],'holdout':hold,'decision':decision,'targetRegime':tmeta}
 (BASE/a.output).write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=True));joblib.dump({'winner':name,'features':features,'imputer':imp,'scaler':sc,'model':model},BASE/a.model_output);print(json.dumps({k:v for k,v in report.items() if k not in ('targetRegime',)},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
