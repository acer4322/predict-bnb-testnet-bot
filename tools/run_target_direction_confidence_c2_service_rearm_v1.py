from __future__ import annotations
import importlib.util,json
from pathlib import Path
import numpy as np,pandas as pd
D=Path('data/research/r4_v0/OUR_C2_ALIGNED_RENEWED_RISK_EPISODES_V2_20260907.csv')
BASE=Path('data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907')
A=pd.read_csv(BASE/'22_TARGET_BTC_DIRECTION_CONFIDENCE_ACTION_CLOCKS_AUDIT_V1.csv',low_memory=False);Q=pd.read_csv(BASE/'23_TARGET_BTC_DIRECTION_CONFIDENCE_CHECKPOINT_PANEL_V1.csv',low_memory=False)
spec=importlib.util.spec_from_file_location('v2','tools/run_target_direction_confidence_c2_aligned_new_risk_v2.py');v2=importlib.util.module_from_spec(spec);spec.loader.exec_module(v2)
spec1=importlib.util.spec_from_file_location('v1','tools/run_target_direction_confidence_c2_aligned_new_risk_v1.py');v1=importlib.util.module_from_spec(spec1);spec1.loader.exec_module(v1)
d=pd.read_csv(D,low_memory=False); smap=v2.frozen_split_map(A,Q); d['split']=d.market_id.map(smap); y=d.renewed_clean_lb_positive.to_numpy(int)
q=np.quantile(d[d.split.eq('TRAIN')].last_action_age_ms,[.25,.5,.75]);q1,q2,q3=[float(x) for x in q]
d['age_class']=np.select([d.last_action_age_ms<=q1,d.last_action_age_ms<=q2,d.last_action_age_ms<=q3],['Q1','Q2','Q3'],default='Q4')
# descriptive role x age
role_age=[]
for sp in ['TRAIN','VALIDATION','TEST']:
  z=d[d.split.eq(sp)]
  for (age,role),g in z.groupby(['age_class','last_economic_role'],dropna=False):
    role_age.append({'split':sp,'ageClass':str(age),'lastRole':str(role),'n':len(g),'markets':g.market_id.nunique(),'rate':float(g.renewed_clean_lb_positive.mean())})
# model market baseline with categorical rearm state
s=d.anchor.map({'UP':1.,'DOWN':-1.});f=pd.DataFrame(index=d.index)
f['phase']=d.seconds_left/300;f['opp_predict']=-d.predict_support;f['opp_strike']=-d.strike_support;f['spot_q']=s*d.spot_queue_imbalance;f['fut_q']=s*d.futures_queue_imbalance;f['spot_taker']=s*d.spot_taker_imbalance_1s;f['fut_taker']=s*d.futures_taker_imbalance_1s;f['spot_ret']=s*d.spot_return_1s_bps;f['fut_ret']=s*d.futures_return_1s_bps
market=list(f)
for c in ['Q2','Q3','Q4']:f['age_'+c]=d.age_class.eq(c).astype(float)
for role in ['CLEAN_AGGREGATE_EXPAND','REPAIR_ONLY','COMPOSITE_CROSSING']:f['role_'+role]=d.last_economic_role.eq(role).astype(float)
age=['age_Q2','age_Q3','age_Q4'];roles=['role_CLEAN_AGGREGATE_EXPAND','role_REPAIR_ONLY','role_COMPOSITE_CROSSING']
groups={'M0_MARKET':market,'A_AGE_BINS':market+age,'R_ROLE':market+roles,'AR_AGE_ROLE':market+age+roles}
tr=d.split.eq('TRAIN').to_numpy();res={};pred={}
for name,cols in groups.items():
  res[name]={};pred[name]={}
  for sp in ['VALIDATION','TEST']:
    te=d.split.eq(sp).to_numpy();x,w=v1.prepare(f.loc[tr,cols],f.loc[te,cols]);b=v1.fit_logit(x,y[tr]);p=1/(1+np.exp(-np.clip(w@b,-35,35)));pred[name][sp]=p;res[name][sp]=v1.metrics(y[te],p)
inc={}
for name in ['A_AGE_BINS','R_ROLE','AR_AGE_ROLE']:
  inc[name]={}
  for sp in ['VALIDATION','TEST']:
    z=d[d.split.eq(sp)].copy();inc[name][sp]=v2.market_cluster_delta(z,pred['M0_MARKET'][sp],pred[name][sp])
# old vs recent descriptive cluster bootstrap risk difference within each split, Q4 vs Q1+Q2.
def boot_rd(z,B=5000):
  x=z[z.age_class.isin(["Q1","Q2","Q4"])].copy();x["old"]=x.age_class.eq("Q4").astype(int)
  mids=x.market_id.unique(); rows=[]
  for mid in mids:
    m=x[x.market_id.eq(mid)]; old=m[m.old.eq(1)]; recent=m[m.old.eq(0)]
    rows.append([len(old),old.renewed_clean_lb_positive.sum(),len(recent),recent.renewed_clean_lb_positive.sum()])
  g=np.asarray(rows,float); rng=np.random.default_rng(20260907); ix=rng.integers(0,len(g),size=(B,len(g))); zsum=g[ix].sum(axis=1)
  oldrate=np.divide(zsum[:,1],zsum[:,0],out=np.full(B,np.nan),where=zsum[:,0]>0); recrate=np.divide(zsum[:,3],zsum[:,2],out=np.full(B,np.nan),where=zsum[:,2]>0); vals=oldrate-recrate
  old=x[x.old.eq(1)];recent=x[x.old.eq(0)]; rd=float(old.renewed_clean_lb_positive.mean()-recent.renewed_clean_lb_positive.mean())
  return {"oldN":len(old),"recentN":len(recent),"oldRate":float(old.renewed_clean_lb_positive.mean()),"recentRate":float(recent.renewed_clean_lb_positive.mean()),"riskDifference":rd,"bootstrap95CI":[float(v) for v in np.nanquantile(vals,[.025,.975])],"markets":int(x.market_id.nunique()),"resamples":B}

rd={sp:boot_rd(d[d.split.eq(sp)]) for sp in ['VALIDATION','TEST']}
out={'version':'OUR_C2_ALIGNED_SERVICE_REARM_DIAGNOSTIC_V1','status':'RESEARCH_ONLY','trainAgeQuartilesMs':[q1,q2,q3],'models':res,'incrementsVsMarket':inc,'oldVsRecent':rd,'roleAge':role_age,'guards':['Q4 threshold fixed from TRAIN 75th percentile; not runtime timer.','Primary cohort unchanged: C2 + net aligned + full5s + conservative renewed clean lower bound.','Small Q4 counts; bootstrap is descriptive, not causal.']}
Path('data/research/r4_v0/OUR_C2_ALIGNED_SERVICE_REARM_DIAGNOSTIC_V1_20260907.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
