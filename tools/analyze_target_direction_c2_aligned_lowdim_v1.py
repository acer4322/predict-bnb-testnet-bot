from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

BASE=Path('data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907')
SRC=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907/C2_ALIGNED_RENEWED_RISK_FULL120_V4.csv')
OUT=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907/C2_ALIGNED_LOWDIM_ROBUSTNESS_V1.json')
SEED=20260907

def prep(a,b):
 a=np.asarray(a,float);b=np.asarray(b,float); keep=np.isfinite(a).any(0);a=a[:,keep];b=b[:,keep];am=~np.isfinite(a);bm=~np.isfinite(b);med=np.nanmedian(np.where(am,np.nan,a),axis=0);a=np.where(am,med,a);b=np.where(bm,med,b);mu=a.mean(0);sd=a.std(0);sd[sd<1e-8]=1;return np.c_[np.ones(len(a)),(a-mu)/sd],np.c_[np.ones(len(b)),(b-mu)/sd]
def fit(x,y,pen=10.):
 y=np.asarray(y,float);w=np.zeros(x.shape[1]);p=np.clip(y.mean(),1e-5,1-1e-5);w[0]=np.log(p/(1-p));reg=np.full(x.shape[1],pen);reg[0]=0
 for _ in range(40):
  pr=1/(1+np.exp(-np.clip(x@w,-35,35)));g=x.T@(pr-y)+reg*w;h=x.T@(x*(pr*(1-pr))[:,None])+np.diag(reg+1e-9);step=np.linalg.solve(h,g);w-=step
  if np.linalg.norm(step)<1e-7:break
 return w
def metric(y,p):
 y=np.asarray(y,int);p=np.clip(np.asarray(p,float),1e-7,1-1e-7);pos=y.sum();neg=len(y)-pos;r=pd.Series(p).rank(method='average').to_numpy();auc=(r[y==1].sum()-pos*(pos+1)/2)/(pos*neg) if pos and neg else None;return {'rows':len(y),'rate':float(y.mean()),'auc':None if auc is None else float(auc),'logLoss':float(-(y*np.log(p)+(1-y)*np.log(1-p)).mean()),'brier':float(np.mean((p-y)**2))}
def main():
 d=pd.read_csv(SRC,low_memory=False);d=d[d.net_aligned_prior==True].copy()
 a=pd.read_csv(BASE/'22_TARGET_BTC_DIRECTION_CONFIDENCE_ACTION_CLOCKS_AUDIT_V1.csv',usecols=['market_id','event_ms'])
 q=pd.read_csv(BASE/'23_TARGET_BTC_DIRECTION_CONFIDENCE_CHECKPOINT_PANEL_V1.csv',usecols=['market_id','checkpoint_ms'])
 starts=pd.concat([a.groupby('market_id').event_ms.min(),q.groupby('market_id').checkpoint_ms.min()],axis=1).min(axis=1).sort_values(kind='stable');m=starts.index.tolist(); mp={x:'TRAIN' for x in m[:72]};mp.update({x:'VALIDATION' for x in m[72:96]});mp.update({x:'TEST' for x in m[96:]});d['split']=d.market_id.map(mp)
 s=d.anchor.map({'UP':1.,'DOWN':-1.});d['opp_predict']=-d.predict_support;d['opp_strike']=-d.strike_support;d['log_debt']=np.log1p(d.pre_outstanding_qty);d['slog_floor']=np.sign(d.pre_floor)*np.log1p(abs(d.pre_floor));d['slog_best']=np.sign(d.pre_best)*np.log1p(abs(d.pre_best));d['last_age']=np.log1p(d.last_action_age_ms);d['prior_side_ask']=np.where(s==1,d.predict_up_ask,d.predict_down_ask);d['repair_ask']=np.where(s==1,d.predict_down_ask,d.predict_up_ask);d['pair_surplus']=1-d.predict_up_ask-d.predict_down_ask;d['clean_age_log']=np.log1p(d.clean_age_ms);d['clean_run_log']=np.log1p(d.clean_run_length);d['phase']=d.seconds_left/300
 groups={'L0_MARKET':['phase','opp_predict','opp_strike'],'L1_PLUS_PORTFOLIO':['phase','opp_predict','opp_strike','log_debt','slog_floor','slog_best','last_age'],'L2_PLUS_ECONOMICS':['phase','opp_predict','opp_strike','log_debt','slog_floor','slog_best','last_age','prior_side_ask','repair_ask','pair_surplus'],'L3_PLUS_HISTORY':['phase','opp_predict','opp_strike','log_debt','slog_floor','slog_best','last_age','prior_side_ask','repair_ask','pair_surplus','clean_age_log','clean_run_log']}
 y=d.renewed_clean_lb_positive.astype(int).to_numpy();res={};tr=d.split.eq('TRAIN').to_numpy()
 for name,cols in groups.items():
  res[name]={}
  for sp in ['VALIDATION','TEST']:
   te=d.split.eq(sp).to_numpy();x,z=prep(d.loc[tr,cols],d.loc[te,cols]);w=fit(x,y[tr]);p=1/(1+np.exp(-np.clip(z@w,-35,35)));res[name][sp]=metric(y[te],p)
 # fixed, non-optimized bins
 bins={
  'opposition_predict':('opp_predict',[0,.05,.10,.20,np.inf]),
  'opposition_strike':('opp_strike',[0,.25,1,3,np.inf]),
  'debt':('pre_outstanding_qty',[0,18,54,162,np.inf]),
  'floor':('pre_floor',[-np.inf,-40,-20,0,np.inf]),
  'prior_side_ask':('prior_side_ask',[0,.25,.50,.75,1.01]),
  'pair_surplus':('pair_surplus',[-np.inf,-.05,0,.05,np.inf]),
  'last_action_age_ms':('last_action_age_ms',[0,250,1000,5000,np.inf]),
  'clean_age_ms':('clean_age_ms',[0,1000,5000,15000,np.inf])}
 tables={}
 for name,(col,edges) in bins.items():
  cat=pd.cut(d[col],edges,right=False,include_lowest=True); tmp=d.assign(_bin=cat)
  rows=[]
  for sp in ['VALIDATION','TEST']:
   for k,g in tmp[tmp.split==sp].groupby('_bin',observed=False):
    if len(g):rows.append({'split':sp,'bin':str(k),'rows':len(g),'markets':g.market_id.nunique(),'rate':float(g.renewed_clean_lb_positive.mean()),'meanNewQty':float(g.renewed_clean_birth_lower_5s.mean())})
  tables[name]=rows
 # simple medians by outcome and split
 comparisons={}
 for sp in ['VALIDATION','TEST']:
  z=d[d.split==sp]; comparisons[sp]={}
  for col in ['opp_predict','opp_strike','pre_outstanding_qty','pre_floor','pre_best','prior_side_ask','repair_ask','pair_surplus','last_action_age_ms','clean_age_ms','clean_run_length']:
   comparisons[sp][col]={'positiveMedian':float(z[z.renewed_clean_lb_positive==1][col].median()),'negativeMedian':float(z[z.renewed_clean_lb_positive==0][col].median())}
 out={'version':'OUR_C2_ALIGNED_LOWDIM_ROBUSTNESS_V1','rows':len(d),'markets':d.market_id.nunique(),'splitCounts':d.groupby('split').agg(rows=('market_id','size'),markets=('market_id','nunique'),rate=('renewed_clean_lb_positive','mean')).to_dict('index'),'groups':groups,'models':res,'fixedBinRates':tables,'outcomeMedians':comparisons,'guard':'Exploratory robustness using frozen original 72/24/24 market split; fixed bins, no runtime authority, no causal claim.'}
 OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
