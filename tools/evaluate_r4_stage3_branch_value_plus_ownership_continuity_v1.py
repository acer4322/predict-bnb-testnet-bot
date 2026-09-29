from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import Ridge
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error,mean_squared_error
from scipy.stats import spearmanr
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
DATA=P/'r4_p0b_stage3_expanded48_dataset_v2.csv'
PRE=P/'r4_stage3_branch_value_plus_ownership_continuity_v1_preregistered.json'
CHUNKS=[
 ROOT/'data/research/lan_worker_returns/r4-stage3-continuity-a4-v1/r4_stage3_candidate_ownership_continuity_chunk_0_16_v1.json',
 ROOT/'data/research/lan_worker_returns/r4-stage3-continuity-b4-v1/r4_stage3_candidate_ownership_continuity_chunk_16_1_v1.json',
 ROOT/'data/research/lan_worker_returns/r4-stage3-continuity-b2-v1/r4_stage3_candidate_ownership_continuity_chunk_16_16_v1.json',
 ROOT/'data/research/lan_worker_returns/r4-stage3-continuity-c2-v1/r4_stage3_candidate_ownership_continuity_chunk_32_16_v1.json',
]
OUT=P/'r4_stage3_branch_value_plus_ownership_continuity_v1_diagnostic.json'
EPS=1e-6
ECON=['seconds_left','abs_gap','risk_deficit','coverage','floor_per_gross','candidateUpside','candidatePx','candidatePxMinusSideMid','spotMinusStrikeBpsPublic']
EXEC=['candidateReservedQty','candidateReservedRootCount']
GROUP=['same_side_active_objectives','opposite_side_active_objectives','same_side_total_residual','opposite_side_total_residual','recent_objective_opens_5s','recent_objective_opens_15s','parent_objective_age_s','same_side_oldest_objective_age_s']
DYN=['current_mode_age_s','events_5s','events_15s','transitions_15s','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s']
BASE=ECON+EXEC+GROUP+DYN
CONT=['pExistingCandidate5s','pExistingCandidate15s','pExistingCandidate30s','existingCandidateResponsibilityCount']
TARGETS=['target_add_floor_gain','target_add_absnet_gain','target_credit_floor_gain','target_credit_absnet_gain']
ROLES=['REJECT_NO_ACTION','PREPOSITION_REPAIR_SUBSTITUTE','PARALLEL_STATE_SHAPING']
def model(kind,seed):
 if kind=='RIDGE':return Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler()),('reg',Ridge(alpha=10.0))])
 return Pipeline([('imp',SimpleImputer(strategy='median')),('reg',ExtraTreesRegressor(n_estimators=500,max_depth=3,min_samples_leaf=2,max_features=.75,n_jobs=1,random_state=seed))])
def dom(a,b):return a[0]>=b[0]-EPS and a[1]>=b[1]-EPS and (a[0]>b[0]+EPS or a[1]>b[1]+EPS)
def role(v):
 r=(0.,0.);a=(float(v[0]),float(v[1]));c=(float(v[2]),float(v[3]));w=[]
 if dom(r,a) and dom(r,c):w.append('REJECT_NO_ACTION')
 if dom(c,r) and dom(c,a):w.append('PREPOSITION_REPAIR_SUBSTITUTE')
 if dom(a,r) and dom(a,c):w.append('PARALLEL_STATE_SHAPING')
 return w[0] if len(w)==1 else 'AMBIGUOUS_TRADEOFF'
def predict_lomo(d,kind,augmented):
 mids=d.marketId.astype(int).to_numpy();uniq=list(dict.fromkeys(mids.tolist()));y=d[TARGETS].to_numpy(float);pred=np.zeros_like(y)
 for i,mid in enumerate(uniq):
  te=np.where(mids==mid)[0];tr=np.where(mids!=mid)[0]
  for j,t in enumerate(TARGETS):
   fs=BASE+(CONT if augmented and t.startswith('target_credit_') else [])
   m=model(kind,51000+i*17+j);m.fit(d.iloc[tr][fs],y[tr,j]);pred[te,j]=m.predict(d.iloc[te][fs])
 return y,pred
def metrics(y,pred,truth):
 tm={}
 for j,t in enumerate(TARGETS):
  rho=spearmanr(y[:,j],pred[:,j]).statistic
  tm[t]={'mae':float(mean_absolute_error(y[:,j],pred[:,j])),'rmse':float(mean_squared_error(y[:,j],pred[:,j])**.5),'spearman':None if np.isnan(rho) else float(rho)}
 pr=np.array([role(v) for v in pred]);rec={r:float(np.mean(pr[truth==r]==r)) for r in ROLES};covered=pr!='AMBIGUOUS_TRADEOFF'
 return {'targetMetrics':tm,'paretoRole':{'exactAccuracy':float(np.mean(pr==truth)),'macroRecall':float(np.mean(list(rec.values()))),'classRecall':rec,'coverage':float(np.mean(covered)),'conditionalAccuracy':float(np.mean(pr[covered]==truth[covered])) if covered.any() else None,'ambiguousPredictions':int(np.sum(~covered))},'predictedRoleCounts':pd.Series(pr).value_counts().to_dict()}
def main():
 pre=json.loads(PRE.read_text())
 d=pd.read_csv(DATA).replace([np.inf,-np.inf],np.nan);d['candidatePxMinusSideMid']=d.candidatePx-np.where(d.candidateSide.astype(str).eq('UP'),d.predictUpMidPublic,d.predictDownMidPublic)
 rows=[]
 for f in CHUNKS:
  x=json.loads(f.read_text());rows+=x['rows']
 c=pd.DataFrame([r for r in rows if 'error' not in r])
 d=d.merge(c[['marketId','candidateKey']+CONT],on=['marketId','candidateKey'],how='left',validate='one_to_one')
 if d[CONT].isna().any().any():raise RuntimeError(f'missing continuity rows: {d[d[CONT].isna().any(axis=1)][["marketId","candidateKey"]].to_dict("records")}')
 truth=d.knownRole.astype(str).to_numpy();res={}
 for kind in ('RIDGE','EXTRATREES'):
  y,b=predict_lomo(d,kind,False);_,a=predict_lomo(d,kind,True);bm=metrics(y,b,truth);am=metrics(y,a,truth)
  delta={t:{'spearmanDelta':(am['targetMetrics'][t]['spearman'] or 0)-(bm['targetMetrics'][t]['spearman'] or 0),'maeDelta':am['targetMetrics'][t]['mae']-bm['targetMetrics'][t]['mae']} for t in TARGETS}
  res[kind]={'BASELINE_FULL_STRICT':bm,'CREDIT_PLUS_OWNERSHIP_CONTINUITY':am,'delta':delta}
 out={'version':'R4_STAGE3_BRANCH_VALUE_PLUS_OWNERSHIP_CONTINUITY_V1_DIAGNOSTIC','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,'contractStatus':pre['status'],'rows':int(len(d)),'markets':int(d.marketId.nunique()),'continuityCoverage':int(len(c)),'results':res,'interpretationBoundary':'Ownership Continuity augments CREDIT target regressors only; ADDITIVE target regressors remain exact FULL_STRICT baseline. Consumed development cohort only; no promotion.','guards':pre['guards']}
 OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
