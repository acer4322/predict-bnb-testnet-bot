from pathlib import Path
import json,numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score,balanced_accuracy_score
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
ROOT=Path.cwd();H=ROOT/'data/research/r4_v0/hourly';OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_management_testbed_v01_responsibility_load_necessity_bridge_v1.json'
coh={'FRESH24':'r4_management_hft_shadow_fresh24_v1_rows.csv','UNSEEN24':'r4_management_hft_shadow_unseen24_v1_rows.csv','REPLICATION3':'r4_management_hft_shadow_replication3_v1_rows.csv'}
def prep(fn):
 d=pd.read_csv(H/fn);d=d[(d.seconds_left<=180)&(d.seconds_left>60)&(d.build_now==1)].copy()
 ps=d[['p_continue_full','p_handoff_full','p_observe_full']].to_numpy(float);labs=np.array(['CONTINUE','HANDOFF','OBSERVE']);d['decision']=labs[np.argmax(ps,axis=1)]
 d['teacher']=d.management_label_5s.map({'CONTINUE_WEAK':'CONTINUE','HANDOFF_ALLOW':'HANDOFF','OBSERVE_NO_EVENT':'OBSERVE'}).fillna('UNKNOWN')
 d=d[(d.decision=='CONTINUE')&d.teacher.isin(['CONTINUE','OBSERVE'])].copy();d['y_error']=(d.teacher=='OBSERVE').astype(int)
 d['qty_gap_ratio']=d.requested_qty/np.maximum(d.abs_gap,18.0)
 d['weak_owner_gap18']=d.weak_active_owners/np.maximum(d.abs_gap/18.0,1.0)
 d['load_uncovered']=d.qty_gap_ratio/(1.0+d.weak_owner_gap18)
 d['owner_covered_load']=d.qty_gap_ratio-d.weak_owner_gap18
 return d
D={k:prep(v) for k,v in coh.items()}; feats=['qty_gap_ratio','weak_owner_gap18','load_uncovered','owner_covered_load']
res={}
for c,d in D.items():
 fs={}
 for f in feats:
  auc=float(roc_auc_score(d.y_error,d[f])) if d.y_error.nunique()==2 else None
  fs[f]={'aucErrorHigh':auc,'errorMedian':float(d.loc[d.y_error==1,f].median()),'correctMedian':float(d.loc[d.y_error==0,f].median())}
 res[c]={'n':len(d),'errors':int(d.y_error.sum()),'features':fs}
# leave-one-cohort-out model using only load variables; fixed C=.5 threshold=.5
models={}
for test in coh:
 tr=pd.concat([D[c] for c in coh if c!=test],ignore_index=True);te=D[test]
 for name,fs in {'LOAD_ONLY':['qty_gap_ratio'],'LOAD_PLUS_OWNER':['qty_gap_ratio','weak_owner_gap18'],'LOAD_FACTOR':['qty_gap_ratio','weak_owner_gap18','load_uncovered','owner_covered_load']}.items():
  m=make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),LogisticRegression(C=.5,class_weight='balanced',solver='liblinear',max_iter=2000))
  m.fit(tr[fs],tr.y_error);p=m.predict_proba(te[fs])[:,1];q=(p>=.5).astype(int)
  models.setdefault(name,{})[test]={'auc':float(roc_auc_score(te.y_error,p)),'ba':float(balanced_accuracy_score(te.y_error,q)),'posRecall':float(np.mean(q[te.y_error.to_numpy()==1]==1)),'negRecall':float(np.mean(q[te.y_error.to_numpy()==0]==0))}
rep={'version':'R4_MANAGEMENT_TESTBED_V0_1_RESPONSIBILITY_LOAD_NECESSITY_BRIDGE_V1','researchOnly':True,'strictPast':True,'noThresholdTuning':True,'question':'Does the portable Completion Capacity responsibility-load state also explain Stage2 CONTINUE->OBSERVE Management Necessity errors?','cohorts':res,'leaveOneCohortOut':models,'interpretation':'If load features transfer, Completion Capacity and Necessity may share a responsibility-load state but must remain separate decisions. If they fail here, do not collapse the heads.'}
OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(models,indent=2))
