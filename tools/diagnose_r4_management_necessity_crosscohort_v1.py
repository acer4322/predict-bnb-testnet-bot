from pathlib import Path
import json, numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score
ROOT=Path.cwd(); H=ROOT/'data/research/r4_v0/hourly'; OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_management_testbed_v01_management_necessity_crosscohort_v1.json'
coh={'FRESH24':'r4_management_hft_shadow_fresh24_v1_rows.csv','UNSEEN24':'r4_management_hft_shadow_unseen24_v1_rows.csv','REPLICATION3':'r4_management_hft_shadow_replication3_v1_rows.csv'}
features=['absnet_ratio','abs_gap','risk_deficit','coverage','floor_per_gross','floor','weak_active_owners','dominant_active_owners']
res={}
for name,fn in coh.items():
 d=pd.read_csv(H/fn); d=d[(d.seconds_left<=180)&(d.seconds_left>60)&(d.build_now==1)].copy()
 ps=d[['p_continue_full','p_handoff_full','p_observe_full']].to_numpy(float); labs=np.array(['CONTINUE','HANDOFF','OBSERVE']); d['decision']=labs[np.argmax(ps,axis=1)]
 d['teacher']=d.management_label_5s.map({'CONTINUE_WEAK':'CONTINUE','HANDOFF_ALLOW':'HANDOFF','OBSERVE_NO_EVENT':'OBSERVE'}).fillna('UNKNOWN')
 x=d[(d.decision=='CONTINUE') & d.teacher.isin(['OBSERVE','CONTINUE'])].copy(); x['y']=(x.teacher=='OBSERVE').astype(int)
 fs={}
 for f in features:
  z=x[[f,'y']].replace([np.inf,-np.inf],np.nan).dropna()
  auc=float(roc_auc_score(z.y,z[f])) if z.y.nunique()==2 else None
  fs[f]={'auc_error_high':auc,'direction':'HIGHER_ERROR' if auc is not None and auc>0.5 else 'LOWER_ERROR' if auc is not None and auc<0.5 else 'NEUTRAL', 'error_median':float(x.loc[x.y==1,f].median()),'correct_median':float(x.loc[x.y==0,f].median())}
 paired={}
 for f in features:
  dirs=[]
  for m,g in x.groupby('marketId'):
   a=g[g.y==1][f].dropna(); b=g[g.y==0][f].dropna()
   if len(a) and len(b): dirs.append(float(a.median()-b.median()))
  paired[f]={'markets':len(dirs),'errorHigher':sum(v>0 for v in dirs),'errorLower':sum(v<0 for v in dirs),'ties':sum(v==0 for v in dirs)}
 res[name]={'rows':int(len(d)),'markets':int(d.marketId.nunique()),'continueBinaryRows':int(len(x)),'errors':int(x.y.sum()),'correct':int((x.y==0).sum()),'features':fs,'paired':paired}
stable=[]
for f in features:
 aucs=[res[c]['features'][f]['auc_error_high'] for c in coh]
 if all(a is not None for a in aucs):
  same=(all(a>0.5 for a in aucs) or all(a<0.5 for a in aucs))
  stable.append({'feature':f,'aucs':dict(zip(coh,aucs)),'sameDirection':same,'minDistanceFromChance':float(min(abs(a-0.5) for a in aucs)),'meanAuc':float(np.mean(aucs))})
rep={'version':'R4_MANAGEMENT_TESTBED_V0_1_MANAGEMENT_NECESSITY_CROSSCOHORT_V1','researchOnly':True,'strictPastRuntimeFeatures':True,'noThresholdTuning':True,'cohortsConsumedForRepresentationSelection':True,'question':'Do geometry/ownership features that explain CONTINUE->OBSERVE error keep direction across independent realistic-HFT cohorts?','cohorts':res,'stableDirection':stable}
OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
