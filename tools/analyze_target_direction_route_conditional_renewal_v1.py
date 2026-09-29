from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
BASE=Path('data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907')
DIR=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907')
C2=DIR/'C2_ALIGNED_RENEWED_RISK_FULL120_V4.csv'; PATHS=DIR/'C2_ALIGNED_RENEWAL_PATH_V1.csv'; OUT=DIR/'C2_ALIGNED_ROUTE_CONDITIONAL_RENEWAL_V1.json'

def prep(a,b):
 a=np.asarray(a,float);b=np.asarray(b,float);keep=np.isfinite(a).any(0);a=a[:,keep];b=b[:,keep];am=~np.isfinite(a);bm=~np.isfinite(b);med=np.nanmedian(np.where(am,np.nan,a),axis=0);a=np.where(am,med,a);b=np.where(bm,med,b);mu=a.mean(0);sd=a.std(0);sd[sd<1e-8]=1;m=am.any(0);return np.c_[np.ones(len(a)),np.clip((a-mu)/sd,-8,8),am[:,m]],np.c_[np.ones(len(b)),np.clip((b-mu)/sd,-8,8),bm[:,m]]
def fit(x,y,pen=10.):
 y=np.asarray(y,float);w=np.zeros(x.shape[1]);r=np.clip(y.mean(),1e-5,1-1e-5);w[0]=np.log(r/(1-r));reg=np.full(x.shape[1],pen);reg[0]=0
 for _ in range(40):
  p=1/(1+np.exp(-np.clip(x@w,-35,35)));g=x.T@(p-y)+reg*w;h=x.T@(x*(p*(1-p))[:,None])+np.diag(reg+1e-9);step=np.linalg.solve(h,g);w-=step
  if np.linalg.norm(step)<1e-7:break
 return w
def metric(y,p):
 y=np.asarray(y,int);p=np.clip(np.asarray(p,float),1e-7,1-1e-7);pos=y.sum();neg=len(y)-pos;r=pd.Series(p).rank(method='average').to_numpy();auc=(r[y==1].sum()-pos*(pos+1)/2)/(pos*neg) if pos and neg else None;return {'rows':len(y),'positives':int(pos),'rate':float(y.mean()),'auc':None if auc is None else float(auc),'logLoss':float(-(y*np.log(p)+(1-y)*np.log(1-p)).mean()),'brier':float(np.mean((p-y)**2))}
def main():
 d=pd.read_csv(C2,low_memory=False);d=d[d.net_aligned_prior==True].copy();p=pd.read_csv(PATHS,low_memory=False);m=p[['market_id','entry_ms','renew_route','hq_postonly','taker_or_mixed','path','delay_ms','renew_new_birth_lower']].copy();d=d.merge(m,on=['market_id','entry_ms'],how='left');d['hq_post_maker_first']=((d.renew_route=='MAKER_ONLY')&(d.hq_postonly==True)).astype(int);d['active_first']=d.taker_or_mixed.fillna(False).astype(bool).astype(int);d['any_renewal']=d.renewed_clean_lb_positive.astype(int)
 # Original frozen 72/24/24 market split.
 a=pd.read_csv(BASE/'22_TARGET_BTC_DIRECTION_CONFIDENCE_ACTION_CLOCKS_AUDIT_V1.csv',usecols=['market_id','event_ms']);q=pd.read_csv(BASE/'23_TARGET_BTC_DIRECTION_CONFIDENCE_CHECKPOINT_PANEL_V1.csv',usecols=['market_id','checkpoint_ms']);starts=pd.concat([a.groupby('market_id').event_ms.min(),q.groupby('market_id').checkpoint_ms.min()],axis=1).min(axis=1).sort_values(kind='stable');mids=starts.index.tolist();mp={x:'TRAIN' for x in mids[:72]};mp.update({x:'VALIDATION' for x in mids[72:96]});mp.update({x:'TEST' for x in mids[96:]});d['split']=d.market_id.map(mp);s=d.anchor.map({'UP':1.,'DOWN':-1.});d['phase']=d.seconds_left/300;d['opp_predict']=-d.predict_support;d['opp_strike']=-d.strike_support
 for c in ['spot_queue_imbalance','futures_queue_imbalance','spot_taker_imbalance_1s','futures_taker_imbalance_1s','spot_return_1s_bps','futures_return_1s_bps']:d['o_'+c]=s*d[c]
 d['log_debt']=np.log1p(d.pre_outstanding_qty);d['slog_floor']=np.sign(d.pre_floor)*np.log1p(abs(d.pre_floor));d['slog_best']=np.sign(d.pre_best)*np.log1p(abs(d.pre_best));d['last_age']=np.log1p(d.last_action_age_ms);d['prior_side_ask']=np.where(s==1,d.predict_up_ask,d.predict_down_ask);d['repair_ask']=np.where(s==1,d.predict_down_ask,d.predict_up_ask);d['pair_surplus']=1-d.predict_up_ask-d.predict_down_ask;d['cost_per_gross']=d.pre_cost/d.pre_gross_shares.replace(0,np.nan);d['clean_age']=np.log1p(d.clean_age_ms);d['clean_run']=np.log1p(d.clean_run_length)
 current=['phase','opp_predict','opp_strike'];queue=['o_spot_queue_imbalance','o_futures_queue_imbalance'];flow=['o_spot_taker_imbalance_1s','o_futures_taker_imbalance_1s','o_spot_return_1s_bps','o_futures_return_1s_bps'];econ=['prior_side_ask','repair_ask','pair_surplus','cost_per_gross'];port=['log_debt','slog_floor','slog_best','last_age'];hist=['clean_age','clean_run']
 groups={'R0_CURRENT':current,'R1_PLUS_QUEUE':current+queue,'R2_PLUS_FLOW':current+queue+flow,'R3_PLUS_ECON':current+queue+flow+econ,'R4_PLUS_PORT_SERVICE':current+queue+flow+econ+port,'R5_PLUS_HISTORY':current+queue+flow+econ+port+hist};tr=d.split.eq('TRAIN').to_numpy();models={}
 for label in ['hq_post_maker_first','active_first','any_renewal']:
  y=d[label].to_numpy(int);models[label]={}
  for name,cols in groups.items():
   models[label][name]={}
   for sp in ['VALIDATION','TEST']:
    te=d.split.eq(sp).to_numpy();x,z=prep(d.loc[tr,cols],d.loc[te,cols]);w=fit(x,y[tr]);pr=1/(1+np.exp(-np.clip(z@w,-35,35)));models[label][name][sp]=metric(y[te],pr)
 # descriptive contrasts among mutually interpretable groups
 desc={}
 fields=['opp_predict','opp_strike','o_spot_queue_imbalance','o_futures_queue_imbalance','o_spot_taker_imbalance_1s','o_futures_taker_imbalance_1s','prior_side_ask','repair_ask','pair_surplus','pre_outstanding_qty','pre_floor','last_action_age_ms','clean_age_ms']
 for sp in ['TRAIN','VALIDATION','TEST']:
  z=d[d.split==sp];desc[sp]={}
  for label in ['hq_post_maker_first','active_first']:
   desc[sp][label]={}
   for f in fields:
    pos=z[z[label]==1][f];neg=z[z[label]==0][f];desc[sp][label][f]={'positiveMedian':float(pos.median()) if len(pos) else None,'negativeMedian':float(neg.median()) if len(neg) else None}
 # route composition and direct-path counts
 pos=d[d.any_renewal==1];route_summary={'renewalEpisodes':len(pos),'hqPostMakerFirst':int(d.hq_post_maker_first.sum()),'activeFirst':int(d.active_first.sum()),'otherOrUnresolvedFirst':int(len(pos)-d.hq_post_maker_first.sum()-d.active_first.sum()),'directNoPriorAction':int((d.path=='DIRECT_NO_PRIOR_ACTION').sum()),'directHQPostMaker':int(((d.path=='DIRECT_NO_PRIOR_ACTION')&(d.hq_post_maker_first==1)).sum()),'directActive':int(((d.path=='DIRECT_NO_PRIOR_ACTION')&(d.active_first==1)).sum())}
 out={'version':'OUR_C2_ALIGNED_ROUTE_CONDITIONAL_RENEWAL_V1','rows':len(d),'markets':d.market_id.nunique(),'splitCounts':d.groupby('split').agg(rows=('market_id','size'),markets=('market_id','nunique'),renewalRate=('any_renewal','mean'),makerRate=('hq_post_maker_first','mean'),activeRate=('active_first','mean')).to_dict('index'),'routeSummary':route_summary,'featureGroups':groups,'models':models,'contrasts':desc,'guards':['Cohort and C2 entry semantics are canonical frozen BTC120 V4 parity.','HQ POST Maker label requires first conservative renewal to be Maker-only with high-quality post-conflict-only parent timing.','Active label requires first conservative renewal route Taker-only or Mixed; it is not inferred from stale Maker fill.','Models are predictive diagnostics only; no route hard gate or runtime authority.']};OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'rows':out['rows'],'splitCounts':out['splitCounts'],'routeSummary':route_summary,'models':models},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
