from __future__ import annotations
import importlib.util,json
from pathlib import Path
import numpy as np,pandas as pd
BASE=Path('data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907')
D=Path('data/research/r4_v0/OUR_C2_ALIGNED_RENEWED_RISK_EPISODES_V2_20260907.csv')
A=BASE/'22_TARGET_BTC_DIRECTION_CONFIDENCE_ACTION_CLOCKS_AUDIT_V1.csv';Q=BASE/'23_TARGET_BTC_DIRECTION_CONFIDENCE_CHECKPOINT_PANEL_V1.csv'
spec=importlib.util.spec_from_file_location('v2','tools/run_target_direction_confidence_c2_aligned_new_risk_v2.py');v2=importlib.util.module_from_spec(spec);spec.loader.exec_module(v2)
spec1=importlib.util.spec_from_file_location('v1','tools/run_target_direction_confidence_c2_aligned_new_risk_v1.py');v1=importlib.util.module_from_spec(spec1);spec1.loader.exec_module(v1)
d=pd.read_csv(D,low_memory=False); actions=pd.read_csv(A,low_memory=False);cp=pd.read_csv(Q,low_memory=False);smap=v2.frozen_split_map(actions,cp);d['split']=d.market_id.map(smap)
s=d.anchor.map({'UP':1.,'DOWN':-1.});f=pd.DataFrame(index=d.index)
f['phase']=d.seconds_left/300;f['opp_predict']=-d.predict_support;f['opp_strike']=-d.strike_support
f['spot_q']=s*d.spot_queue_imbalance;f['fut_q']=s*d.futures_queue_imbalance;f['spot_taker']=s*d.spot_taker_imbalance_1s;f['fut_taker']=s*d.futures_taker_imbalance_1s;f['spot_ret']=s*d.spot_return_1s_bps;f['fut_ret']=s*d.futures_return_1s_bps
market=list(f)
f['net_abs']=np.log1p(d.pre_net_shares.abs());f['debt']=np.log1p(d.pre_outstanding_qty.clip(lower=0));f['floor']=v1.safe_log1p(d.pre_floor);f['best']=v1.safe_log1p(d.pre_best);f['cost_gross']=d.pre_cost/d.pre_gross_shares.replace(0,np.nan)
static=['net_abs','debt','floor','best','cost_gross']
f['action_age']=np.log1p(d.last_action_age_ms)
for role in ['CLEAN_AGGREGATE_EXPAND','REPAIR_ONLY','COMPOSITE_CROSSING']:f['last_'+role]=d.last_economic_role.eq(role).astype(float)
service=['action_age','last_CLEAN_AGGREGATE_EXPAND','last_REPAIR_ONLY','last_COMPOSITE_CROSSING']
f['prior_ask']=np.where(s==1,d.predict_up_ask,d.predict_down_ask);f['repair_ask']=np.where(s==1,d.predict_down_ask,d.predict_up_ask);f['pair_surplus']=1-d.predict_up_ask-d.predict_down_ask;f['repair_x_debt']=f.repair_ask*np.log1p(d.pre_outstanding_qty.clip(lower=0))
econ=['prior_ask','repair_ask','pair_surplus','repair_x_debt']
f['clean_age']=np.log1p(d.clean_age_ms);f['clean_run']=np.log1p(d.clean_run_length);hist=['clean_age','clean_run']
groups={'M0_MARKET':market,'S_STATIC':market+static,'R_SERVICE':market+service,'E_ECON':market+econ,'H_HISTORY':market+hist,'SR_STATIC_SERVICE':market+static+service,'RE_SERVICE_ECON':market+service+econ,'ALL_NO_HISTORY':market+static+service+econ,'ALL_HISTORY':market+static+service+econ+hist}
y=d.renewed_clean_lb_positive.to_numpy(int);tr=d.split.eq('TRAIN').to_numpy();res={};pred={}
for name,cols in groups.items():
    res[name]={};pred[name]={}
    for sp in ['VALIDATION','TEST']:
        te=d.split.eq(sp).to_numpy();x,w=v1.prepare(f.loc[tr,cols],f.loc[te,cols]);b=v1.fit_logit(x,y[tr]);p=1/(1+np.exp(-np.clip(w@b,-35,35)));pred[name][sp]=p;res[name][sp]=v1.metrics(y[te],p)
inc={}
for name in groups:
    if name=='M0_MARKET':continue
    inc[name+'_minus_M0']={}
    for sp in ['VALIDATION','TEST']:
        z=d[d.split.eq(sp)].copy();inc[name+'_minus_M0'][sp]=v2.market_cluster_delta(z,pred['M0_MARKET'][sp],pred[name][sp])
# service quartile, fixed train edges
qs=np.unique(np.nanquantile(d[d.split.eq('TRAIN')].last_action_age_ms,[0,.25,.5,.75,1]));bins={}
for sp in ['VALIDATION','TEST']:
    z=d[d.split.eq(sp)].copy();z['bin']=pd.cut(z.last_action_age_ms,qs,include_lowest=True,duplicates='drop');bins[sp]=[{'bin':str(k),'n':int(len(g)),'markets':int(g.market_id.nunique()),'rate':float(g.renewed_clean_lb_positive.mean()),'medianAgeMs':float(g.last_action_age_ms.median())} for k,g in z.groupby('bin',observed=True)]
out={'version':'OUR_C2_ALIGNED_RENEWED_RISK_COMPONENT_ABLATION_V1','status':'RESEARCH_ONLY','rows':len(d),'markets':int(d.market_id.nunique()),'splitRows':d.split.value_counts().to_dict(),'models':res,'incrementsVsMarket':inc,'serviceAgeBins':{'trainEdgesMs':[float(x) for x in qs],**bins},'guards':['Same V2 C2+aligned+full5s conservative renewed-risk cohort.','All additions compared independently over the same market-only baseline.','Predictive association only; no runtime authority.']}
Path('data/research/r4_v0/OUR_C2_ALIGNED_RENEWED_RISK_COMPONENT_ABLATION_V1_20260907.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))
