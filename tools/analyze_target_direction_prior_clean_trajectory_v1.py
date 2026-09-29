from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np,pandas as pd
BASE=Path('data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907')
SRC=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907/C2_ALIGNED_RENEWED_RISK_FULL120_V4.csv')
OUTDIR=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907')
SEED=20260907

def ss(x):return 1. if x=='UP' else (-1. if x=='DOWN' else np.nan)
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
 ap=argparse.ArgumentParser();ap.add_argument('--markets',nargs='*',type=int);ap.add_argument('--tag',default='FULL120');args=ap.parse_args()
 e=pd.read_csv(SRC,low_memory=False);e=e[e.net_aligned_prior==True].copy();a=pd.read_csv(BASE/'22_TARGET_BTC_DIRECTION_CONFIDENCE_ACTION_CLOCKS_AUDIT_V1.csv',low_memory=False);q=pd.read_csv(BASE/'23_TARGET_BTC_DIRECTION_CONFIDENCE_CHECKPOINT_PANEL_V1.csv',low_memory=False)
 if args.markets:
  m=set(args.markets);e=e[e.market_id.isin(m)];a=a[a.market_id.isin(m)];q=q[q.market_id.isin(m)]
 # frozen original 72/24/24 split from full cohort chronology
 af=pd.read_csv(BASE/'22_TARGET_BTC_DIRECTION_CONFIDENCE_ACTION_CLOCKS_AUDIT_V1.csv',usecols=['market_id','event_ms']);qf=pd.read_csv(BASE/'23_TARGET_BTC_DIRECTION_CONFIDENCE_CHECKPOINT_PANEL_V1.csv',usecols=['market_id','checkpoint_ms']);starts=pd.concat([af.groupby('market_id').event_ms.min(),qf.groupby('market_id').checkpoint_ms.min()],axis=1).min(axis=1).sort_values(kind='stable');mids=starts.index.tolist();smap={x:'TRAIN' for x in mids[:72]};smap.update({x:'VALIDATION' for x in mids[72:96]});smap.update({x:'TEST' for x in mids[96:]})
 rows=[]
 for r in e.itertuples():
  sign=ss(r.anchor);hist=a[(a.market_id==r.market_id)&(a.event_ms<r.entry_ms)&(a.economic_role=='CLEAN_AGGREGATE_EXPAND')].sort_values('event_ms')
  if not len(hist):continue
  pc=hist.iloc[-1]
  if pc.birth_side!=r.anchor:continue
  pc_pred=sign*(pc.predict_up_mid-pc.predict_down_mid)/2;pc_strike=sign*pc.spot_minus_strike_bps if pd.notna(pc.spot_minus_strike_bps) else np.nan;pc_ask=pc.predict_up_ask if r.anchor=='UP' else pc.predict_down_ask;cur_ask=r.predict_up_ask if r.anchor=='UP' else r.predict_down_ask
  z=q[(q.market_id==r.market_id)&(q.checkpoint_ms<r.entry_ms)&(q.prior_clean_expand_side==r.anchor)&(q.checkpoint_ms>=r.entry_ms-20000)].copy();z['ps']=sign*(z.predict_up_mid-z.predict_down_mid)/2;z['ssup']=sign*z.spot_minus_strike_bps
  # Predict-conflict age: walk backward over the dense panel while current prior-clean anchor is unchanged.
  c1_age=0.;prev=r.entry_ms
  for x in z.sort_values('checkpoint_ms',ascending=False).itertuples():
   if x.ps<0:c1_age=r.entry_ms-x.checkpoint_ms;prev=x.checkpoint_ms
   else:break
  rec={'market_id':int(r.market_id),'entry_ms':int(r.entry_ms),'anchor':r.anchor,'split':smap.get(r.market_id),'label':int(r.renewed_clean_lb_positive),'cur_predict_support':float(r.predict_support),'cur_strike_support':float(r.strike_support),'prior_clean_event_ms':int(pc.event_ms),'prior_clean_age_ms':float(r.entry_ms-pc.event_ms),'prior_clean_predict_support':float(pc_pred),'prior_clean_strike_support':float(pc_strike) if np.isfinite(pc_strike) else np.nan,'delta_predict_support':float(r.predict_support-pc_pred),'delta_strike_support':float(r.strike_support-pc_strike) if np.isfinite(pc_strike) else np.nan,'prior_side_ask_at_clean':float(pc_ask) if pd.notna(pc_ask) else np.nan,'current_prior_side_ask':float(cur_ask) if pd.notna(cur_ask) else np.nan,'prior_side_ask_change':float(cur_ask-pc_ask) if pd.notna(cur_ask) and pd.notna(pc_ask) else np.nan,'predict_conflict_age_ms':float(c1_age),'pre_debt':float(r.pre_outstanding_qty),'pre_floor':float(r.pre_floor),'last_action_age_ms':float(r.last_action_age_ms)}
  for w in [1000,3000,5000,10000,20000]:
   zz=z[z.checkpoint_ms>=r.entry_ms-w]
   rec[f'pred_mean_{w}']=float(zz.ps.mean()) if len(zz) else np.nan;rec[f'pred_min_{w}']=float(zz.ps.min()) if len(zz) else np.nan;rec[f'pred_max_{w}']=float(zz.ps.max()) if len(zz) else np.nan;rec[f'pred_negative_frac_{w}']=float((zz.ps<0).mean()) if len(zz) else np.nan;rec[f'strike_negative_frac_{w}']=float((zz.ssup<0).mean()) if len(zz) else np.nan
   if len(zz)>=2:
    x=(zz.checkpoint_ms.to_numpy(float)-zz.checkpoint_ms.iloc[0])/1000;y=zz.ps.to_numpy(float);rec[f'pred_slope_{w}']=float(np.polyfit(x,y,1)[0]) if np.ptp(x)>0 else 0.
   else:rec[f'pred_slope_{w}']=np.nan
  rows.append(rec)
 d=pd.DataFrame(rows);csv=OUTDIR/f'PRIOR_CLEAN_TRAJECTORY_{args.tag}_V1.csv';d.to_csv(csv,index=False)
 base=['cur_predict_support','cur_strike_support'];prior=['prior_clean_predict_support','prior_clean_strike_support','delta_predict_support','delta_strike_support','prior_side_ask_change'];traj=['predict_conflict_age_ms','pred_mean_5000','pred_negative_frac_5000','strike_negative_frac_5000','pred_slope_5000','pred_mean_10000','pred_negative_frac_10000','strike_negative_frac_10000','pred_slope_10000'];port=['pre_debt','pre_floor','last_action_age_ms'];groups={'T0_CURRENT':base,'T1_PLUS_PRIOR_CLEAN':base+prior,'T2_PLUS_TRAJECTORY':base+prior+traj,'T3_PLUS_PORT_SERVICE':base+prior+traj+port}
 res={};y=d.label.to_numpy(int);tr=d.split.eq('TRAIN').to_numpy()
 if tr.sum()>=20:
  for name,cols in groups.items():
   res[name]={}
   for sp in ['VALIDATION','TEST']:
    te=d.split.eq(sp).to_numpy()
    if te.sum()<5:res[name][sp]={'rows':int(te.sum())};continue
    x,z=prep(d.loc[tr,cols],d.loc[te,cols]);w=fit(x,y[tr]);p=1/(1+np.exp(-np.clip(z@w,-35,35)));res[name][sp]=metric(y[te],p)
 # fixed descriptive contrasts by split
 fields=['prior_clean_predict_support','prior_clean_strike_support','delta_predict_support','prior_side_ask_change','predict_conflict_age_ms','pred_mean_5000','pred_negative_frac_5000','pred_slope_5000']
 comp={}
 for sp in ['TRAIN','VALIDATION','TEST']:
  zz=d[d.split==sp];comp[sp]={}
  for f in fields:
   comp[sp][f]={'positiveMedian':float(zz[zz.label==1][f].median()) if len(zz[zz.label==1]) else None,'negativeMedian':float(zz[zz.label==0][f].median()) if len(zz[zz.label==0]) else None}
 out={'version':'OUR_PRIOR_CLEAN_TRAJECTORY_FALSIFICATION_V1','tag':args.tag,'rows':len(d),'markets':d.market_id.nunique(),'splitCounts':d.groupby('split').agg(rows=('market_id','size'),markets=('market_id','nunique'),rate=('label','mean')).to_dict('index') if len(d) else {},'models':res,'groups':groups,'contrasts':comp,'guards':['All prior-clean and trajectory features are strict-past relative to C2 entry.','Prior-clean action market fields use its supplied strict-past public signal snapshot.','Trajectory uses only checkpoints before entry with the same prior-clean anchor.','This tests public temporal-memory explanations; no private thesis claim and no runtime authority.']}
 outp=OUTDIR/f'PRIOR_CLEAN_TRAJECTORY_{args.tag}_V1.json';outp.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
