from __future__ import annotations
import json
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,average_precision_score
ROOT=Path(__file__).resolve().parents[1]; H=ROOT/'data/research/r4_v0/hourly'; P=ROOT/'data/research/r4_v0/p0_provenance_v1'
NAMES=['fresh24','unseen24','replication3']

def load(n):
 d=pd.read_csv(H/f'r4_management_hft_shadow_{n}_v1_rows.csv')
 d=d[(d.seconds_left<=180)&(d.seconds_left>60)&(d.build_now==1)].copy()
 d['y']=(d.futureWeakMakerFill5s>0).astype(int)
 eps=1e-6
 # Strict-past, dimensionless/rate-like transforms only.
 d['event5_per_mode_s']=d.events_5s/np.maximum(d.current_mode_age_s,1.0)
 d['event15_per_mode_s']=d.events_15s/np.maximum(d.current_mode_age_s,1.0)
 d['transition_share15']=d.transitions_15s/np.maximum(d.events_15s,1.0)
 d['weak_owner_gap18']=d.weak_active_owners/np.maximum(d.abs_gap/18.0,1.0)
 d['dominant_owner_gap18']=d.dominant_active_owners/np.maximum(d.abs_gap/18.0,1.0)
 d['weak_age_mode_ratio']=d.weak_oldest_age_s/np.maximum(d.current_mode_age_s,1.0)
 d['dominant_age_mode_ratio']=d.dominant_oldest_age_s/np.maximum(d.current_mode_age_s,1.0)
 d['owner_balance_norm']=(d.weak_active_owners-d.dominant_active_owners)/np.maximum(d.weak_active_owners+d.dominant_active_owners,1.0)
 d['risk_per_gap']=d.risk_deficit/np.maximum(d.abs_gap,1.0)
 d['floor_per_absnet']=d.floor/np.maximum(d.absNet,18.0)
 d['qty_gap_ratio']=d.requested_qty/np.maximum(d.abs_gap,18.0)
 return d
D={n:load(n) for n in NAMES}
RAW=['weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s','current_mode_age_s','events_5s','events_15s','transitions_15s','requested_px']
NORM=['event5_per_mode_s','event15_per_mode_s','transition_share15','weak_owner_gap18','dominant_owner_gap18','weak_age_mode_ratio','dominant_age_mode_ratio','owner_balance_norm','risk_per_gap','floor_per_absnet','qty_gap_ratio']

def auc_feat(d,f):
 x=np.asarray(d[f],float); y=np.asarray(d.y,int); m=np.isfinite(x)
 x=x[m]; y=y[m]
 if len(set(y))<2 or np.nanstd(x)<1e-12:return None
 a=roc_auc_score(y,x); return float(a)
feat={}
for f in RAW+NORM:
 vals={n:auc_feat(D[n],f) for n in NAMES}; centered=[None if v is None else v-.5 for v in vals.values()]
 finite=[x for x in centered if x is not None and abs(x)>1e-6]
 sign_consistent=(len(finite)==3 and (all(x>0 for x in finite) or all(x<0 for x in finite)))
 feat[f]={'aucByCohort':vals,'signedLiftByCohort':{n:(None if vals[n] is None else vals[n]-.5) for n in NAMES},'signConsistent':sign_consistent,'minAbsLift':None if not finite else float(min(abs(x) for x in finite))}
stable=[f for f in NORM if feat[f]['signConsistent'] and feat[f]['minAbsLift'] is not None and feat[f]['minAbsLift']>=0.03]
# Also report weak consistency >=.015 separately; model uses only preregister-like 0.03 stability screen for diagnostic, not promotion.
weakstable=[f for f in NORM if feat[f]['signConsistent'] and feat[f]['minAbsLift'] is not None and feat[f]['minAbsLift']>=0.015]

def score(train_names,test_name,fs):
 tr=pd.concat([D[n] for n in train_names],ignore_index=True); te=D[test_name]
 if len(fs)==0 or tr.y.nunique()<2 or te.y.nunique()<2:return None
 m=make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),LogisticRegression(C=.5,class_weight='balanced',solver='liblinear',max_iter=2000,random_state=71001))
 m.fit(tr[fs],tr.y); p=m.predict_proba(te[fs])[:,1]; q=(p>=.5).astype(int); y=te.y.to_numpy()
 return {'trainRows':len(tr),'testRows':len(te),'testMarkets':int(te.marketId.nunique()),'positiveRate':float(y.mean()),'ba':float(balanced_accuracy_score(y,q)),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'posRecall':float(np.mean(q[y==1]==1)),'negRecall':float(np.mean(q[y==0]==0))}
transfers={}
for fsname,fs in [('RAW_DYNAMICS',RAW),('NORMALIZED_ALL',NORM),('NORMALIZED_STABLE_003',stable),('NORMALIZED_STABLE_0015',weakstable)]:
 transfers[fsname]={'features':fs,'fresh_to_unseen':score(['fresh24'],'unseen24',fs),'fresh_to_replication3':score(['fresh24'],'replication3',fs),'fresh_unseen_to_replication3':score(['fresh24','unseen24'],'replication3',fs),'unseen_replication3_to_fresh':score(['unseen24','replication3'],'fresh24',fs)}
rep={'version':'R4_MANAGEMENT_COMPLETION_REGIME_NORMALIZED_V1','researchOnly':True,'actionAuthority':False,'strictPastRuntimeFeaturesOnly':True,'cohorts':{n:{'rows':len(D[n]),'markets':int(D[n].marketId.nunique()),'positive':int(D[n].y.sum())} for n in NAMES},'featureAudit':feat,'stable003':stable,'stable0015':weakstable,'crossCohortTransfers':transfers,'guard':'Diagnostic feature-stability decomposition only. No threshold sweep; no promotion; no future outcome used as runtime feature.'}
out=P/'r4_management_completion_regime_normalized_v1.json';out.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'cohorts':rep['cohorts'],'stable003':stable,'stable0015':weakstable,'transfers':transfers},indent=2))
