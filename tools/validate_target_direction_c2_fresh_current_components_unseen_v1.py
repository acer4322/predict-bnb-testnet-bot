from __future__ import annotations
import json,importlib.util
from pathlib import Path
import numpy as np,pandas as pd
LANE=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907')
TR=LANE/'C2_FRESH_PROGRAM_SOURCE_FEATURES_V1.csv';TE=LANE/'UNSEEN_OLDER173_SERVICE_REPLICATION_V1.csv';OUT=LANE/'UNSEEN_OLDER173_CURRENT_COMPONENTS_V1.json'
s=importlib.util.spec_from_file_location('v1','tools/run_target_direction_confidence_c2_aligned_new_risk_v1.py');v1=importlib.util.module_from_spec(s);s.loader.exec_module(v1)
s2=importlib.util.spec_from_file_location('v2','tools/run_target_direction_confidence_c2_aligned_new_risk_v2.py');v2=importlib.util.module_from_spec(s2);s2.loader.exec_module(v2)

def fit_transform(train,test,cols):
 a=np.asarray(train[cols],float);b=np.asarray(test[cols],float);keep=np.isfinite(a).any(0);a=a[:,keep];b=b[:,keep];am=~np.isfinite(a);bm=~np.isfinite(b);med=np.nanmedian(np.where(am,np.nan,a),axis=0);a=np.where(am,med,a);b=np.where(bm,med,b);mu=a.mean(0);sd=a.std(0);sd[sd<1e-8]=1;m=am.any(0);return np.c_[np.ones(len(a)),np.clip((a-mu)/sd,-8,8),am[:,m]],np.c_[np.ones(len(b)),np.clip((b-mu)/sd,-8,8),bm[:,m]]
def fitpred(tr,te,cols):
 x,z=fit_transform(tr,te,cols);b=v1.fit_logit(x,tr.fresh_program_admission.to_numpy(int));return 1/(1+np.exp(-np.clip(z@b,-35,35)))
def qdiag(tr,te,col):
 edges=np.unique(np.nanquantile(tr[col].dropna(),[0,.25,.5,.75,1]));
 if len(edges)<3:return None
 z=te.copy();z['bin']=pd.cut(z[col],edges,include_lowest=True,duplicates='drop');return {'canonicalEdges':[float(x) for x in edges],'external':[{'bin':str(k),'n':int(len(g)),'markets':int(g.market_id.nunique()),'rate':float(g.fresh_program_admission.mean()),'median':float(g[col].median())} for k,g in z.groupby('bin',observed=True)]}
def main():
 tr=pd.read_csv(TR,low_memory=False);te=pd.read_csv(TE,low_memory=False);tr['phase']=tr.seconds_left/300;te['phase']=te.seconds_left/300;tr['opp_predict']=-tr.cur_predict_support;te['opp_predict']=-te.predict_support;tr['opp_strike']=-tr.cur_strike_support;te['opp_strike']=-te.strike_support
 groups={'B0_INTERCEPT':[],'B1_PHASE':['phase'],'B2_PREDICT':['phase','opp_predict'],'B3_STRIKE':['phase','opp_strike'],'B4_PREDICT_STRIKE':['phase','opp_predict','opp_strike']};y=te.fresh_program_admission.to_numpy(int);pred={};met={}
 # intercept from canonical prevalence
 r=float(tr.fresh_program_admission.mean());pred['B0_INTERCEPT']=np.full(len(te),r);met['B0_INTERCEPT']=v1.metrics(y,pred['B0_INTERCEPT'])
 for n,c in groups.items():
  if n=='B0_INTERCEPT':continue
  pred[n]=fitpred(tr,te,c);met[n]=v1.metrics(y,pred[n])
 inc={}
 for n in ['B1_PHASE','B2_PREDICT','B3_STRIKE','B4_PREDICT_STRIKE']:
  inc[n+'_minus_INTERCEPT']=v2.market_cluster_delta(te,pred['B0_INTERCEPT'],pred[n],label='fresh_program_admission',resamples=5000)
 for n in ['B2_PREDICT','B3_STRIKE','B4_PREDICT_STRIKE']:
  inc[n+'_minus_PHASE']=v2.market_cluster_delta(te,pred['B1_PHASE'],pred[n],label='fresh_program_admission',resamples=5000)
 out={'version':'OUR_C2_FRESH_CURRENT_COMPONENTS_INDEPENDENT_V1','status':'RESEARCH_ONLY','externalRows':len(te),'externalMarkets':int(te.market_id.nunique()),'fresh':int(y.sum()),'groups':groups,'metrics':met,'increments':inc,'quartiles':{c:qdiag(tr,te,c) for c in ['phase','opp_predict','opp_strike']},'guards':['Model forms fixed before reading component results; canonical417 trains, older173 external cohort tests.','Opposition magnitudes are positive when current signal opposes prior clean anchor; C2 guarantees Predict and strike are already opposing.','This isolates association within C2, not causal intent or profitability.']};OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
