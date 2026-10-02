from __future__ import annotations
import json,joblib,sys
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score,recall_score,log_loss
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1';SRC=P/'r4_p0b_target_objective_topology_future_rows_v2.csv';CON=P/'r4_state_shaping_authorization_v11_portable_contract.json';PARENT=P/'r4_state_shaping_authorization_v1_dev_score.json';OUT=P/'r4_state_shaping_authorization_v11_portable_score.json';MOD=P/'r4_state_shaping_authorization_v11_portable_shadow.joblib';Y='future_different_objective_5s'
F=['seconds_left_norm','risk_deficit_gap_ratio','floor_gap_ratio','upside_gap_ratio','coverage','floor_per_gross','events_5s_rate','events_15s_rate','transitions_15s_rate','mode_age_log','weak_active_roots','dominant_active_roots','weak_unresolved_gap_ratio','dominant_unresolved_gap_ratio','weak_progress_ratio','dominant_progress_ratio','weak_fill5_gap_ratio','dominant_fill5_gap_ratio']
def mdl(seed):return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,l2_regularization=1,max_iter=220,random_state=seed)
def prep(d):
 x=d.copy();g=np.maximum(x.abs_gap.to_numpy(float),18.);x['seconds_left_norm']=x.seconds_left/300.;x['risk_deficit_gap_ratio']=x.risk_deficit/g;x['floor_gap_ratio']=x.floor/g;x['upside_gap_ratio']=x.upside/g;x['events_5s_rate']=x.events_5s/5.;x['events_15s_rate']=x.events_15s/15.;x['transitions_15s_rate']=x.transitions_15s/15.;x['mode_age_log']=np.log1p(np.maximum(x.mode_age_s,0));x['weak_unresolved_gap_ratio']=x.weak_unresolved_shares/g;x['dominant_unresolved_gap_ratio']=x.dominant_unresolved_shares/g;x['weak_fill5_gap_ratio']=x.weak_fill_shares_5s/g;x['dominant_fill5_gap_ratio']=x.dominant_fill_shares_5s/g;return x
def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);z=(p>=.5).astype(int);return {'n':len(y),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'ba':float(balanced_accuracy_score(y,z)),'posRecall':float(recall_score(y,z,pos_label=1,zero_division=0)),'negRecall':float(recall_score(y,z,pos_label=0,zero_division=0)),'logLoss':float(log_loss(y,np.clip(p,1e-6,1-1e-6),labels=[0,1]))}
def main():
 con=json.loads(CON.read_text());assert con['status']=='FROZEN_BEFORE_PORTABLE_SCORING';par=json.loads(PARENT.read_text());d=prep(pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan));d=d[(d.current_pair_balance==1)&(d.current_state_shaping==0)].dropna(subset=F+[Y]).copy();d[Y]=d[Y].astype(int);order=d.groupby('market_id').t.min().sort_values().index.astype(int).tolist();initial=max(40,int(len(order)*.6));rem=len(order)-initial;sizes=[rem//4]*4
 for i in range(rem%4):sizes[i]+=1
 blocks=[];cur=initial
 for bi,sz in enumerate(sizes,1):
  trm=set(order[:cur]);tem=set(order[cur:cur+sz]);cur+=sz;tr=d[d.market_id.isin(trm)];te=d[d.market_id.isin(tem)];m=mdl(49100+bi).fit(tr[F],tr[Y]);b={'block':bi,'trainMarkets':len(trm),'testMarkets':len(tem),'bands':{}}
  for name,q in [('ALL',te),('FORMATION_180_300',te[(te.seconds_left>180)&(te.seconds_left<=300)]),('MANAGEMENT_60_180',te[(te.seconds_left>=60)&(te.seconds_left<=180)])]:
   b['bands'][name]=met(q[Y],m.predict_proba(q[F])[:,1]) if len(q) and q[Y].nunique()>1 else {'n':len(q),'auc':None}
  blocks.append(b)
 summary={}
 for name in ['ALL','FORMATION_180_300','MANAGEMENT_60_180']:
  q=[b['bands'][name] for b in blocks if b['bands'][name].get('auc') is not None];summary[name]={'meanAuc':float(np.mean([x['auc'] for x in q])),'worstAuc':float(np.min([x['auc'] for x in q])),'meanBA':float(np.mean([x['ba'] for x in q])),'meanAP':float(np.mean([x['ap'] for x in q])),'meanPosRecall':float(np.mean([x['posRecall'] for x in q])),'meanNegRecall':float(np.mean([x['negRecall'] for x in q]))}
 fullAuc=float(par['summary']['SCALE_NORMALIZED']['ALL']['meanAuc']);keep=summary['ALL']['meanAuc']>=.68 and summary['ALL']['worstAuc']>=.62 and fullAuc-summary['ALL']['meanAuc']<=.08
 out={'version':'R4_STATE_SHAPING_AUTHORIZATION_V1_1_PORTABLE_SCORE','researchOnly':True,'actionAuthority':False,'contract':CON.name,'rows':len(d),'markets':d.market_id.nunique(),'positiveRate':float(d[Y].mean()),'features':F,'blocks':blocks,'summary':summary,'parentFullNormalizedMeanAuc':fullAuc,'meanAucDropVsParent':fullAuc-summary['ALL']['meanAuc'],'developmentKeep':bool(keep)};OUT.write_text(json.dumps(out,indent=2),encoding='utf-8')
 if keep:
  m=mdl(49999).fit(d[F],d[Y]);joblib.dump({'version':'R4_STATE_SHAPING_AUTHORIZATION_V1_1_PORTABLE_SHADOW','researchOnly':True,'actionAuthority':False,'features':F,'model':m,'gapScaleRule':'max(abs_gap,parent_lot)','sourceParentLot':18.0,'guards':con['guards']},MOD)
 print(json.dumps({'rows':len(d),'markets':d.market_id.nunique(),'summary':summary,'parentMeanAuc':fullAuc,'drop':fullAuc-summary['ALL']['meanAuc'],'developmentKeep':keep},indent=2))
if __name__=='__main__':main()
