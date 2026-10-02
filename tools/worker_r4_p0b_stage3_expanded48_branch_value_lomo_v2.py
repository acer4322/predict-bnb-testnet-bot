from __future__ import annotations
import json,os
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import Ridge
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error,mean_squared_error
from scipy.stats import spearmanr
ROOT=Path.cwd(); ST=ROOT/'.lan_worker_v1/staging'; OUT=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
d=pd.read_csv(ST/'r4_p0b_stage3_expanded48_dataset_v2.csv').replace([np.inf,-np.inf],np.nan)
d['candidatePxMinusSideMid']=d.candidatePx-np.where(d.candidateSide.astype(str).eq('UP'),d.predictUpMidPublic,d.predictDownMidPublic)
ECON=['seconds_left','abs_gap','risk_deficit','coverage','floor_per_gross','candidateUpside','candidatePx','candidatePxMinusSideMid','spotMinusStrikeBpsPublic']
EXEC=['candidateReservedQty','candidateReservedRootCount']
GROUP=['same_side_active_objectives','opposite_side_active_objectives','same_side_total_residual','opposite_side_total_residual','recent_objective_opens_5s','recent_objective_opens_15s','parent_objective_age_s','same_side_oldest_objective_age_s']
DYN=['current_mode_age_s','events_5s','events_15s','transitions_15s','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s']
SETS={'LOCAL_ECON_EXEC':ECON+EXEC,'GROUP_VALUE':ECON+EXEC+GROUP,'FULL_STRICT':ECON+EXEC+GROUP+DYN}
TARGETS=['target_add_floor_gain','target_add_absnet_gain','target_credit_floor_gain','target_credit_absnet_gain']; EPS=1e-6
def model(kind,seed):
 if kind=='RIDGE':return Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler()),('reg',Ridge(alpha=10.0))])
 return Pipeline([('imp',SimpleImputer(strategy='median')),('reg',ExtraTreesRegressor(n_estimators=500,max_depth=3,min_samples_leaf=2,max_features=.75,n_jobs=4,random_state=seed))])
def dom(a,b):return a[0]>=b[0]-EPS and a[1]>=b[1]-EPS and (a[0]>b[0]+EPS or a[1]>b[1]+EPS)
def role(v):
 r=(0.,0.);a=(float(v[0]),float(v[1]));c=(float(v[2]),float(v[3]));w=[]
 if dom(r,a) and dom(r,c):w.append('REJECT_NO_ACTION')
 if dom(c,r) and dom(c,a):w.append('PREPOSITION_REPAIR_SUBSTITUTE')
 if dom(a,r) and dom(a,c):w.append('PARALLEL_STATE_SHAPING')
 return w[0] if len(w)==1 else 'AMBIGUOUS_TRADEOFF'
def eval_one(kind,fs):
 y=d[TARGETS].to_numpy(float);pred=np.zeros_like(y); mids=d.marketId.astype(int).to_numpy(); uniq=list(dict.fromkeys(mids.tolist()))
 for j,mid in enumerate(uniq):
  te=np.where(mids==mid)[0];tr=np.where(mids!=mid)[0];m=model(kind,41000+j);m.fit(d.loc[tr,fs],y[tr]);pred[te]=m.predict(d.loc[te,fs])
 truth=d.knownRole.astype(str).to_numpy(); pr=np.array([role(x) for x in pred]);labs=['REJECT_NO_ACTION','PREPOSITION_REPAIR_SUBSTITUTE','PARALLEL_STATE_SHAPING'];rec={z:float(np.mean(pr[truth==z]==z)) for z in labs};covered=pr!='AMBIGUOUS_TRADEOFF'
 tm={}
 for j,t in enumerate(TARGETS):
  rho=spearmanr(y[:,j],pred[:,j]).statistic;tm[t]={'mae':float(mean_absolute_error(y[:,j],pred[:,j])),'rmse':float(mean_squared_error(y[:,j],pred[:,j])**.5),'spearman':None if np.isnan(rho) else float(rho),'targetStd':float(np.std(y[:,j]))}
 return {'targetMetrics':tm,'paretoRole':{'exactAccuracy':float(np.mean(pr==truth)),'macroRecall':float(np.mean(list(rec.values()))),'classRecall':rec,'coverage':float(np.mean(covered)),'conditionalAccuracy':float(np.mean(pr[covered]==truth[covered])) if covered.any() else None,'ambiguousPredictions':int(np.sum(~covered))},'predictedRoleCounts':pd.Series(pr).value_counts().to_dict()}
def main():
 out={'version':'R4_P0B_STAGE3_EXPANDED48_BRANCH_VALUE_LOMO_V2','researchOnly':True,'actionAuthority':False,'rows':int(len(d)),'markets':int(d.marketId.nunique()),'roleCounts':d.knownRole.value_counts().to_dict(),'evaluation':'True leave-one-market-out; frozen branch economics targets only; same Pareto rule; no threshold/utility sweep.','results':{}}
 for k in ['RIDGE','EXTRATREES']:
  out['results'][k]={}
  for n,fs in SETS.items():out['results'][k][n]=eval_one(k,fs)
 (OUT/'stage3_expanded48_branch_value_lomo_v2.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
