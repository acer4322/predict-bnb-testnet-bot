from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
BASE=Path('data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907')
SRC=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907/C2_ALIGNED_RENEWED_RISK_FULL120_V4.csv')
OUT=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907/C2_ALIGNED_CROSS_SIGNAL_V1.json')
def prep(a,b):
 a=np.asarray(a,float);b=np.asarray(b,float);keep=np.isfinite(a).any(0);a=a[:,keep];b=b[:,keep];am=~np.isfinite(a);bm=~np.isfinite(b);med=np.nanmedian(np.where(am,np.nan,a),axis=0);a=np.where(am,med,a);b=np.where(bm,med,b);mu=a.mean(0);sd=a.std(0);sd[sd<1e-8]=1;return np.c_[np.ones(len(a)),np.clip((a-mu)/sd,-8,8)],np.c_[np.ones(len(b)),np.clip((b-mu)/sd,-8,8)]
def fit(x,y,pen=10.):
 y=np.asarray(y,float);w=np.zeros(x.shape[1]);r=np.clip(y.mean(),1e-5,1-1e-5);w[0]=np.log(r/(1-r));reg=np.full(x.shape[1],pen);reg[0]=0
 for _ in range(40):
  p=1/(1+np.exp(-np.clip(x@w,-35,35)));g=x.T@(p-y)+reg*w;h=x.T@(x*(p*(1-p))[:,None])+np.diag(reg+1e-9);step=np.linalg.solve(h,g);w-=step
  if np.linalg.norm(step)<1e-7:break
 return w
def metric(y,p):
 y=np.asarray(y,int);p=np.clip(np.asarray(p,float),1e-7,1-1e-7);pos=y.sum();neg=len(y)-pos;r=pd.Series(p).rank(method='average').to_numpy();auc=(r[y==1].sum()-pos*(pos+1)/2)/(pos*neg) if pos and neg else None;return {'rows':len(y),'rate':float(y.mean()),'auc':None if auc is None else float(auc),'logLoss':float(-(y*np.log(p)+(1-y)*np.log(1-p)).mean()),'brier':float(np.mean((p-y)**2))}
def main():
 d=pd.read_csv(SRC,low_memory=False);d=d[d.net_aligned_prior==True].copy();a=pd.read_csv(BASE/'22_TARGET_BTC_DIRECTION_CONFIDENCE_ACTION_CLOCKS_AUDIT_V1.csv',usecols=['market_id','event_ms']);q=pd.read_csv(BASE/'23_TARGET_BTC_DIRECTION_CONFIDENCE_CHECKPOINT_PANEL_V1.csv',usecols=['market_id','checkpoint_ms']);starts=pd.concat([a.groupby('market_id').event_ms.min(),q.groupby('market_id').checkpoint_ms.min()],axis=1).min(axis=1).sort_values(kind='stable');m=starts.index.tolist();mp={x:'TRAIN' for x in m[:72]};mp.update({x:'VALIDATION' for x in m[72:96]});mp.update({x:'TEST' for x in m[96:]});d['split']=d.market_id.map(mp);s=d.anchor.map({'UP':1.,'DOWN':-1.});d['phase']=d.seconds_left/300;d['opp_predict']=-d.predict_support;d['opp_strike']=-d.strike_support
 raw=['spot_queue_imbalance','futures_queue_imbalance','spot_taker_imbalance_1s','futures_taker_imbalance_1s','spot_return_1s_bps','futures_return_1s_bps']
 oriented=[]
 for c in raw:
  n='o_'+c;d[n]=s*d[c];oriented.append(n)
 d['support_votes']=sum((d[c]>0).astype(int) for c in oriented);d['available_votes']=sum(d[c].notna().astype(int) for c in oriented);d['support_vote_fraction']=d.support_votes/d.available_votes.replace(0,np.nan)
 d['log_debt']=np.log1p(d.pre_outstanding_qty);d['floor_slog']=np.sign(d.pre_floor)*np.log1p(abs(d.pre_floor));d['last_age']=np.log1p(d.last_action_age_ms)
 groups={'C0_PREDICT_STRIKE':['phase','opp_predict','opp_strike'],'C1_PLUS_QUEUE':['phase','opp_predict','opp_strike','o_spot_queue_imbalance','o_futures_queue_imbalance'],'C2_PLUS_FLOW_RETURN':['phase','opp_predict','opp_strike']+oriented,'C3_PLUS_PORT_SERVICE':['phase','opp_predict','opp_strike']+oriented+['log_debt','floor_slog','last_age']}
 y=d.renewed_clean_lb_positive.astype(int).to_numpy();tr=d.split.eq('TRAIN').to_numpy();res={}
 for name,cols in groups.items():
  res[name]={}
  for sp in ['VALIDATION','TEST']:
   te=d.split.eq(sp).to_numpy();x,z=prep(d.loc[tr,cols],d.loc[te,cols]);w=fit(x,y[tr]);p=1/(1+np.exp(-np.clip(z@w,-35,35)));res[name][sp]=metric(y[te],p)
 votes={}
 for sp in ['TRAIN','VALIDATION','TEST']:
  z=d[d.split==sp];rows=[]
  for k,g in z.groupby('support_votes'):
   rows.append({'votes':int(k),'rows':len(g),'markets':g.market_id.nunique(),'rate':float(g.renewed_clean_lb_positive.mean()),'meanVoteFraction':float(g.support_vote_fraction.mean())})
  votes[sp]=rows
 med={}
 for sp in ['VALIDATION','TEST']:
  z=d[d.split==sp];med[sp]={c:{'positiveMedian':float(z[z.renewed_clean_lb_positive==1][c].median()),'negativeMedian':float(z[z.renewed_clean_lb_positive==0][c].median())} for c in oriented+['support_vote_fraction']}
 out={'version':'OUR_C2_ALIGNED_CROSS_SIGNAL_V1','rows':len(d),'markets':d.market_id.nunique(),'models':res,'groups':groups,'supportVoteRates':votes,'signalMedians':med,'guards':['C2 already requires Predict and spot-strike oppose anchor; these votes test whether other current public microstructure still supports anchor.','Vote count is descriptive only; sources are correlated and not independent votes.','Original frozen 72/24/24 market split; no threshold tuning; no runtime authority.']};OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
