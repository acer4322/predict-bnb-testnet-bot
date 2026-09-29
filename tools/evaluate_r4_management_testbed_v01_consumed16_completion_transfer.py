from __future__ import annotations
import json
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.metrics import balanced_accuracy_score, roc_auc_score, average_precision_score
ROOT=Path(__file__).resolve().parents[1]; P=ROOT/'data/research/r4_v0/p0_provenance_v1'
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_hft_shadow_unseen24_v1_rows.csv'
dev=pd.read_csv(SRC); dev=dev[(dev.seconds_left<=180)&(dev.seconds_left>60)&(dev.build_now==1)].copy(); dev['y']=(dev.futureWeakMakerFill5s>0).astype(int)
score=json.loads((P/'r4_p0b_stage3_additive_admission_untouched16_score_v1.json').read_text())
lookup={}
for fp in sorted(P.glob('r4_p0b_stage3_additive_admission_candidate_discovery_*_v1.json')):
 d=json.loads(fp.read_text())
 for mr in d.get('rows',[]):
  for o in mr.get('opportunities',[]): lookup[(int(o['marketId']),str(o['candidateKey']))]=o
val=[]
for s in score['scored']:
 o=lookup.get((int(s['marketId']),str(s['candidateKey'])))
 if o is None: continue
 z=dict(o); rf=s['reservationFinal']; af=s['additiveFinal']
 z['y_effect']=int(abs(float(af['floor'])-float(rf['floor']))>1e-9 or abs(float(af['absNet'])-float(rf['absNet']))>1e-9)
 z['y_beneficial']=int(s['label']); val.append(z)
val=pd.DataFrame(val)
FS=['weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s','current_mode_age_s','events_5s','events_15s','transitions_15s','requested_px']
model=make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),LogisticRegression(C=.5,class_weight='balanced',solver='liblinear',max_iter=2000,random_state=71001))
model.fit(dev[FS],dev.y); prob=model.predict_proba(val[FS])[:,1]; pred=(prob>=.5).astype(int)
def met(y):
 y=np.asarray(y,int); two=len(set(y))>1
 return {'n':len(y),'positive':int(y.sum()),'ba':float(balanced_accuracy_score(y,pred)) if two else None,'auc':float(roc_auc_score(y,prob)) if two else None,'ap':float(average_precision_score(y,prob)) if two else None,'posRecall':float(np.mean(pred[y==1]==1)) if np.any(y==1) else None,'negRecall':float(np.mean(pred[y==0]==0)) if np.any(y==0) else None,'meanProbPositive':float(np.mean(prob[y==1])) if np.any(y==1) else None,'meanProbNegative':float(np.mean(prob[y==0])) if np.any(y==0) else None}
out={'version':'R4_MANAGEMENT_TESTBED_V0_1_CONSUMED16_COMPLETION_TRANSFER','researchOnly':True,'diagnosticOnly':True,'notPromotion':True,'developmentRows':len(dev),'developmentMarkets':int(dev.marketId.nunique()),'validationRowsMatched':len(val),'targetBranchHasAnyEffect':met(val.y_effect),'targetAdditiveBeneficial':met(val.y_beneficial),'interpretation':'Tests whether responsibility-dynamics completion signal transfers onto already-consumed Stage3 untouched16. Branch effect/benefit are diagnostic proxies, not the original futureWeakMakerFill5s target.'}
(P/'r4_management_testbed_v01_consumed16_completion_transfer.json').write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out,indent=2))
