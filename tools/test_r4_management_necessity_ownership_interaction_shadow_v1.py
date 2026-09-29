from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score,balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1];H=ROOT/'data/research/r4_v0/hourly';P=ROOT/'data/research/r4_v0/p0_provenance_v1'
FILES={'FRESH24':H/'r4_management_hft_shadow_fresh24_v1_rows.csv','UNSEEN24':H/'r4_management_hft_shadow_unseen24_v1_rows.csv','REPLICATION3':H/'r4_management_hft_shadow_replication3_v1_rows.csv'}
BASE=['weak_oldest_age_s','weak_age_over_mode','event_decay_15','event_accel_5v15','weak_active_owners']
INT=BASE+['age_x_owner','age_x_no_owner','decay_x_owner','owner_presence']
def prep(p):
 d=pd.read_csv(p);d=d[(d.seconds_left<=180)&(d.seconds_left>60)&(d.build_now==1)].copy();labs=np.array(['CONTINUE','HANDOFF','OBSERVE']);d['decision']=labs[np.argmax(d[['p_continue_full','p_handoff_full','p_observe_full']].to_numpy(float),axis=1)];d['teacher']=d.management_label_5s.map({'CONTINUE_WEAK':'CONTINUE','HANDOFF_ALLOW':'HANDOFF','OBSERVE_NO_EVENT':'OBSERVE'}).fillna('UNKNOWN');mode=np.maximum(d.current_mode_age_s.to_numpy(float),1.0);d['weak_age_over_mode']=d.weak_oldest_age_s.to_numpy(float)/mode;d['event_decay_15']=-d.events_15s.to_numpy(float);d['event_accel_5v15']=d.events_5s.to_numpy(float)-d.events_15s.to_numpy(float)/3.0;own=(d.weak_active_owners.to_numpy(float)>0).astype(float);d['owner_presence']=own;d['age_x_owner']=d.weak_oldest_age_s.to_numpy(float)*own;d['age_x_no_owner']=d.weak_oldest_age_s.to_numpy(float)*(1-own);d['decay_x_owner']=d['event_decay_15'].to_numpy(float)*own;return d
def fit(d,feats):
 c=d[(d.decision=='CONTINUE')&d.teacher.isin(['CONTINUE','OBSERVE'])];X=c[feats].fillna(0).to_numpy(float);y=(c.teacher=='OBSERVE').astype(int).to_numpy();sc=StandardScaler().fit(X);m=LogisticRegression(C=.5,class_weight='balanced',solver='liblinear',random_state=4402).fit(sc.transform(X),y);return sc,m
def ev(d,feats,sc,m):
 c=d[(d.decision=='CONTINUE')&d.teacher.isin(['CONTINUE','OBSERVE'])].copy();y=(c.teacher=='OBSERVE').astype(int).to_numpy();p=m.predict_proba(sc.transform(c[feats].fillna(0).to_numpy(float)))[:,1];v=p>=.5;prog=((c.floorImproved5s>0)|(c.absNetReduced5s>0)).to_numpy();return {'rows':len(c),'markets':int(c.marketId.nunique()),'auc':float(roc_auc_score(y,p)),'ba':float(balanced_accuracy_score(y,v.astype(int))),'errors':int(y.sum()),'errorsRemoved':int((v&(y==1)).sum()),'correct':int((y==0).sum()),'correctVetoed':int((v&(y==0)).sum()),'vetoNoEconomicProgress':int((v&(~prog)).sum()),'vetoWithEconomicProgress':int((v&prog).sum())}
def main():
 D={k:prep(v) for k,v in FILES.items()};out={'version':'R4_MANAGEMENT_NECESSITY_OWNERSHIP_INTERACTION_SHADOW_V1','researchOnly':True,'actionAuthority':False,'strictPast':True,'realisticHFT':True,'consumedDevelopmentCohorts':True,'models':{}}
 for name,feats in [('STALENESS_BASE',BASE),('OWNERSHIP_INTERACTION',INT)]:
  sc,m=fit(D['FRESH24'],feats);out['models'][name]={k:ev(v,feats,sc,m) for k,v in D.items()};h=[out['models'][name]['UNSEEN24'],out['models'][name]['REPLICATION3']];out['models'][name]['holdout']={'meanAuc':float(np.mean([x['auc'] for x in h])),'meanBA':float(np.mean([x['ba'] for x in h])),'errorsRemoved':sum(x['errorsRemoved'] for x in h),'errors':sum(x['errors'] for x in h),'correctVetoed':sum(x['correctVetoed'] for x in h),'correct':sum(x['correct'] for x in h),'vetoNoEconomicProgress':sum(x['vetoNoEconomicProgress'] for x in h),'vetoWithEconomicProgress':sum(x['vetoWithEconomicProgress'] for x in h)}
 b=out['models']['STALENESS_BASE']['holdout'];i=out['models']['OWNERSHIP_INTERACTION']['holdout'];out['deltaInteractionVsBase']={'meanAuc':i['meanAuc']-b['meanAuc'],'meanBA':i['meanBA']-b['meanBA'],'correctVetoed':i['correctVetoed']-b['correctVetoed'],'errorsRemoved':i['errorsRemoved']-b['errorsRemoved'],'vetoWithEconomicProgress':i['vetoWithEconomicProgress']-b['vetoWithEconomicProgress']}
 out['interpretation']='Discovery-motivated interaction test only. If interaction does not improve realistic-HFT holdouts and trajectory collateral damage, reject synthetic-world implication and record simulator gap.';(P/'r4_management_necessity_ownership_interaction_shadow_v1.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
