from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np
from sklearn.ensemble import RandomForestClassifier
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1';sys.path.insert(0,str(ROOT));import tools.evaluate_r4_management_simulator_v61_formal20_v1 as e
DEV=e.load(e.DEV);VR=json.loads(e.VAL.read_text(encoding='utf-8'));VAL=VR['rows'];X=np.vstack([e.vec(r) for r in DEV]);XV=np.vstack([e.vec(r) for r in VAL])
CTX=['activeObjectiveCount','sameSideActiveObjectives','oppositeSideActiveObjectives','recentObjectiveOpens5s','recentObjectiveOpens15s','recentParallelObjectiveOpens15s','currentObjectiveAgeS','timeSinceLastObjectiveOpenS','timeSinceLastParallelObjectiveOpenS','timeSinceLastResponsibilityCompletionS','ownerCount','weakResponsibilityCount','dominantResponsibilityCount','unresolvedQty','progressRatio']
def meanfeat(ix):
 if not len(ix):return {'n':0}
 return {'n':len(ix),**{f:float(np.mean([float(VAL[i].get(f) or 0) for i in ix])) for f in CTX}}
out={}
for h in e.H:
 yd=np.array([e.labels(r,h) for r in DEV]);yv=np.array([e.labels(r,h) for r in VAL]);ho={}
 for k,name in enumerate(['WEAK','DOM']):
  clf=RandomForestClassifier(n_estimators=260,max_depth=5,min_samples_leaf=4,class_weight='balanced_subsample',random_state=6000+h*10+k,n_jobs=1).fit(X,yd[:,k]);p=clf.predict(XV);pr=clf.predict_proba(XV)[:,1]
  tp=np.where((yv[:,k]==1)&(p==1))[0];fn=np.where((yv[:,k]==1)&(p==0))[0];fp=np.where((yv[:,k]==0)&(p==1))[0];tn=np.where((yv[:,k]==0)&(p==0))[0]
  ho[name]={'actualPositive':int(yv[:,k].sum()),'predPositive':int(p.sum()),'meanProbActualPositive':float(pr[yv[:,k]==1].mean()) if yv[:,k].sum() else None,'meanProbActualNegative':float(pr[yv[:,k]==0].mean()) if (yv[:,k]==0).sum() else None,'TP':meanfeat(tp),'FN':meanfeat(fn),'FP':meanfeat(fp),'TN':meanfeat(tn)}
 out[str(h)]=ho
rep={'version':'R4_MANAGEMENT_V6_1_STREAM_ERROR_CONTEXT_DIAGNOSTIC_V1','researchOnly':True,'promotionEvidence':False,'validationMarkets':20,'validationRoots':len(VAL),'horizons':out,'guard':'Formal20 is now consumed; this diagnostic may guide representation only and cannot retune V6.1 into pass.'};(P/'r4_management_v61_stream_error_context_diagnostic_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
