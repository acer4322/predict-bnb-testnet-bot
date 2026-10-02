from pathlib import Path
import json, numpy as np, pandas as pd
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,average_precision_score
ROOT=Path.cwd(); H=ROOT/'data/research/r4_v0/hourly'; OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_management_testbed_v01_necessity_factorized_v1.json'
coh={'FRESH24':'r4_management_hft_shadow_fresh24_v1_rows.csv','UNSEEN24':'r4_management_hft_shadow_unseen24_v1_rows.csv','REPLICATION3':'r4_management_hft_shadow_replication3_v1_rows.csv'}
def prep(d):
 d=d[(d.seconds_left<=180)&(d.seconds_left>60)&(d.build_now==1)].copy()
 ps=d[['p_continue_full','p_handoff_full','p_observe_full']].to_numpy(float); labs=np.array(['CONTINUE','HANDOFF','OBSERVE']); d['decision']=labs[np.argmax(ps,axis=1)]
 d['teacher']=d.management_label_5s.map({'CONTINUE_WEAK':'CONTINUE','HANDOFF_ALLOW':'HANDOFF','OBSERVE_NO_EVENT':'OBSERVE'}).fillna('UNKNOWN')
 d=d[(d.decision=='CONTINUE') & d.teacher.isin(['OBSERVE','CONTINUE'])].copy();d['y']=(d.teacher=='OBSERVE').astype(int)
 eps=1e-6
 d['weak_owner_present']=(d.weak_active_owners>0).astype(float)
 d['staleness_age']=np.log1p(d.weak_oldest_age_s.clip(lower=0))
 d['event_accel']=d.events_5s/(d.events_15s+eps)
 d['event_decay']=1-d['event_accel'].clip(0,1)
 d['transition_density']=d.transitions_15s/(d.events_15s+eps)
 d['pipeline_occupancy']=d.weak_active_owners + 0.5*(d.transitions_15s>0).astype(float)
 d['staleness_x_decay']=d['staleness_age']*d['event_decay']
 d['staleness_x_occupancy']=d['staleness_age']*d['pipeline_occupancy']
 return d
D={k:prep(pd.read_csv(H/v)) for k,v in coh.items()}
sets={
'LIFECYCLE_STALENESS':['staleness_age','event_decay','transition_density'],
'PIPELINE_OCCUPANCY':['weak_active_owners','weak_owner_present','pipeline_occupancy'],
'FACTORIZED':['staleness_age','event_decay','transition_density','weak_active_owners','weak_owner_present','pipeline_occupancy','staleness_x_decay','staleness_x_occupancy'],
'STATIC_GEOMETRY_CONTROL':['abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross']}
res={}
for test_name in coh:
 train=pd.concat([D[k] for k in coh if k!=test_name],ignore_index=True); test=D[test_name]
 rr={}
 for name,fs in sets.items():
  m=make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),LogisticRegression(C=.5,class_weight='balanced',solver='liblinear',max_iter=2000))
  m.fit(train[fs],train.y); p=m.predict_proba(test[fs])[:,1]; q=(p>=.5).astype(int)
  rr[name]={'n':int(len(test)),'positive':int(test.y.sum()),'ba':float(balanced_accuracy_score(test.y,q)),'auc':float(roc_auc_score(test.y,p)),'ap':float(average_precision_score(test.y,p)),'posRecall':float(np.mean(q[test.y.to_numpy()==1]==1)),'negRecall':float(np.mean(q[test.y.to_numpy()==0]==0))}
 res[test_name]=rr
summary={}
for s in sets:
 vals=[res[c][s] for c in coh]
 summary[s]={'meanBA':float(np.mean([v['ba'] for v in vals])),'meanAUC':float(np.mean([v['auc'] for v in vals])),'minAUC':float(min(v['auc'] for v in vals)),'aucs':{c:res[c][s]['auc'] for c in coh},'bas':{c:res[c][s]['ba'] for c in coh}}
rep={'version':'R4_MANAGEMENT_TESTBED_V0_1_NECESSITY_FACTORIZED_V1','researchOnly':True,'strictPast':True,'crossCohortLeaveOneCohortOut':True,'noThresholdTuning':True,'cohortsConsumedForRepresentationSelection':True,'target':'existing Stage2 CONTINUE that teacher says OBSERVE vs CONTINUE','featureSets':sets,'results':res,'summary':summary,'interpretation':'Development diagnostic. Tests factorized lifecycle-staleness and repair-pipeline occupancy against static geometry control; not promotion evidence.'}
OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(summary,indent=2))
