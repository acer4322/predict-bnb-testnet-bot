from __future__ import annotations
import json,math,sys,joblib
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,balanced_accuracy_score,recall_score
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
SRC=P/'r4_p0b_target_objective_topology_future_rows_v2.csv'
CON=P/'r4_state_shaping_authorization_v1_dev_contract.json'
OUT=P/'r4_state_shaping_authorization_v1_dev_score.json'
MODEL=P/'r4_state_shaping_authorization_v1_shadow.joblib'
RAW=['seconds_left','abs_gap','risk_deficit','floor','upside','absNet','coverage','floor_per_gross','current_commitment','seconds_since_prev_parent','events_5s','events_15s','transitions_15s','mode_age_s','recent_pair_balance_events_15s','recent_state_shaping_events_15s','distinct_objective_keys_15s','weak_active_roots','dominant_active_roots','weak_unresolved_shares','dominant_unresolved_shares','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s']
NORM=['seconds_left_norm','risk_deficit_gap_ratio','floor_gap_ratio','upside_gap_ratio','coverage','floor_per_gross','commitment_gap_ratio','seconds_since_prev_parent_log','events_5s_rate','events_15s_rate','transitions_15s_rate','mode_age_log','recent_pair_balance_rate15','recent_state_shaping_rate15','distinct_objective_keys_15s','weak_active_roots','dominant_active_roots','weak_unresolved_gap_ratio','dominant_unresolved_gap_ratio','weak_progress_ratio','dominant_progress_ratio','weak_fill5_gap_ratio','dominant_fill5_gap_ratio']
LABEL='future_different_objective_5s'
def model(seed=48100):
 return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,l2_regularization=1.0,max_iter=220,random_state=seed)
def norm(d):
 x=d.copy();gap=np.maximum(np.maximum(x.abs_gap.to_numpy(float),x.current_commitment.to_numpy(float)),18.0)
 x['seconds_left_norm']=x.seconds_left/300.0;x['risk_deficit_gap_ratio']=x.risk_deficit/gap;x['floor_gap_ratio']=x.floor/gap;x['upside_gap_ratio']=x.upside/gap;x['commitment_gap_ratio']=x.current_commitment/gap
 x['seconds_since_prev_parent_log']=np.log1p(np.maximum(x.seconds_since_prev_parent,0));x['events_5s_rate']=x.events_5s/5.0;x['events_15s_rate']=x.events_15s/15.0;x['transitions_15s_rate']=x.transitions_15s/15.0;x['mode_age_log']=np.log1p(np.maximum(x.mode_age_s,0));x['recent_pair_balance_rate15']=x.recent_pair_balance_events_15s/15.0;x['recent_state_shaping_rate15']=x.recent_state_shaping_events_15s/15.0
 x['weak_unresolved_gap_ratio']=x.weak_unresolved_shares/gap;x['dominant_unresolved_gap_ratio']=x.dominant_unresolved_shares/gap;x['weak_fill5_gap_ratio']=x.weak_fill_shares_5s/gap;x['dominant_fill5_gap_ratio']=x.dominant_fill_shares_5s/gap
 return x
def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int);u=np.unique(y)
 return {'n':int(len(y)),'positiveRate':float(y.mean()) if len(y) else None,'balancedAccuracy':float(balanced_accuracy_score(y,pred)) if len(u)>1 else None,'auc':float(roc_auc_score(y,p)) if len(u)>1 else None,'ap':float(average_precision_score(y,p)) if len(u)>1 else None,'logLoss':float(log_loss(y,np.clip(p,1e-6,1-1e-6),labels=[0,1])) if len(y) else None,'positiveRecall':float(recall_score(y,pred,pos_label=1,zero_division=0)) if len(y) else None,'negativeRecall':float(recall_score(y,pred,pos_label=0,zero_division=0)) if len(y) else None}
def band(z):
 return {'ALL':z,'FORMATION_180_300':z[(z.seconds_left>180)&(z.seconds_left<=300)],'MANAGEMENT_60_180':z[(z.seconds_left>=60)&(z.seconds_left<=180)]}
def main():
 con=json.loads(CON.read_text());assert con['status']=='FROZEN_BEFORE_DEVELOPMENT_SCORING'
 d=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan);d=d[(d.current_pair_balance==1)&(d.current_state_shaping==0)].copy();d[LABEL]=d[LABEL].astype(int);d=norm(d)
 order=d.groupby('market_id').t.min().sort_values().index.astype(int).tolist();initial=max(40,int(len(order)*.60));initial=min(initial,len(order)-4);rem=len(order)-initial;sizes=[rem//4]*4
 for i in range(rem%4):sizes[i]+=1
 blocks=[];cur=initial
 for bi,sz in enumerate(sizes,1):
  trm=set(order[:cur]);tem=set(order[cur:cur+sz]);cur+=sz;tr=d[d.market_id.isin(trm)];te=d[d.market_id.isin(tem)];b={'block':bi,'trainMarkets':len(trm),'testMarkets':len(tem),'testRows':len(te),'sets':{}}
  for si,(name,feats) in enumerate([('RAW_STRICTPAST',RAW),('SCALE_NORMALIZED',NORM)]):
   a=tr.dropna(subset=feats+[LABEL]);m=model(48100+bi*20+si).fit(a[feats],a[LABEL]);bm={}
   for bn,z in band(te).items():
    q=z.dropna(subset=feats+[LABEL]);bm[bn]=met(q[LABEL],m.predict_proba(q[feats])[:,1]) if len(q) else {'n':0}
   b['sets'][name]=bm
  blocks.append(b)
 summary={}
 for name in ['RAW_STRICTPAST','SCALE_NORMALIZED']:
  summary[name]={}
  for bn in ['ALL','FORMATION_180_300','MANAGEMENT_60_180']:
   q=[b['sets'][name][bn] for b in blocks if b['sets'][name][bn].get('auc') is not None]
   summary[name][bn]={'blocks':len(q),'meanAuc':float(np.mean([x['auc'] for x in q])) if q else None,'worstAuc':float(np.min([x['auc'] for x in q])) if q else None,'meanBA':float(np.mean([x['balancedAccuracy'] for x in q])) if q else None,'meanAP':float(np.mean([x['ap'] for x in q])) if q else None,'meanPositiveRecall':float(np.mean([x['positiveRecall'] for x in q])) if q else None,'meanNegativeRecall':float(np.mean([x['negativeRecall'] for x in q])) if q else None}
 sn=summary['SCALE_NORMALIZED']['ALL'];raw=summary['RAW_STRICTPAST']['ALL'];keep=bool(sn['meanAuc'] is not None and sn['meanAuc']>=.68 and sn['worstAuc']>=.62 and sn['meanAuc']>=raw['meanAuc']-.03)
 art={'version':'R4_STATE_SHAPING_AUTHORIZATION_V1_DEV_SCORE','researchOnly':True,'actionAuthority':False,'contract':CON.name,'rows':int(len(d)),'markets':int(d.market_id.nunique()),'positiveRate':float(d[LABEL].mean()),'blocks':blocks,'summary':summary,'developmentKeep':keep,'interpretation':'Development-only Target strict-past PAIR_BALANCE->DIFFERENT_OBJECTIVE/STATE_SHAPING authorization. <=180s remains shadow-only and cannot authorize NEW entry.'}
 OUT.write_text(json.dumps(art,indent=2,allow_nan=True),encoding='utf-8')
 if keep:
  q=d.dropna(subset=NORM+[LABEL]);m=model(48999).fit(q[NORM],q[LABEL]);joblib.dump({'version':'R4_STATE_SHAPING_AUTHORIZATION_V1_SHADOW','researchOnly':True,'actionAuthority':False,'features':NORM,'model':m,'rowFilter':'current_pair_balance==1 and current_state_shaping==0','label':LABEL,'normalization':con['normalization'],'guards':con['guards']},MODEL)
 print(json.dumps({'rows':len(d),'markets':d.market_id.nunique(),'positiveRate':d[LABEL].mean(),'summary':summary,'developmentKeep':keep,'modelSaved':bool(keep)},indent=2))
if __name__=='__main__':main()
