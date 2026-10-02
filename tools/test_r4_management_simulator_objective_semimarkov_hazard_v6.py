from __future__ import annotations
import json,glob,sys
from pathlib import Path
from collections import Counter
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
FILES=glob.glob(str(ROOT/'data/research/lan_worker_returns/r4-objv6-dev-*/*.json'))
FEATURES=['secondsLeft','floor','absNet','coverage','absnetRatio','floorPerGross','unresolvedQty','progressRatio','ownerCount','weakResponsibilityCount','dominantResponsibilityCount','events15s','transitions15s','oldestOwnerAge','pendingCancelCount','activeObjectiveCount','sameSideActiveObjectives','oppositeSideActiveObjectives','sameSideResidualQty','oppositeSideResidualQty','sameSideReservedQty','oppositeSideReservedQty','sameSideConfirmedQty','oppositeSideConfirmedQty','currentObjectiveResidualQty','currentObjectiveReservedQty','currentObjectiveConfirmedQty','currentObjectiveResidualRatio','currentObjectiveResponsibilityCount','recentObjectiveOpens5s','recentObjectiveOpens15s','recentParallelObjectiveOpens15s','currentObjectiveAgeS','sameSideOldestObjectiveAgeS','oppositeSideOldestObjectiveAgeS','timeSinceObjectiveProgressS','timeSinceHandoffLikeS']
EVENTS=['PROGRESS_CURRENT_OBJECTIVE','OPEN_PARALLEL_WEAK','OPEN_PARALLEL_DOMINANT','CURRENT_OBJECTIVE_COMPLETES','HANDOFF_LIKE_DEPARTURE']
CLASSES=['NONE']+EVENTS

def load():
 rows=[]
 for f in FILES: rows.extend(json.load(open(f,encoding='utf-8'))['rows'])
 return rows

def xbase(r): return [float(r.get(f) or 0) for f in FEATURES]
def split(rows):
 mids=sorted(set(int(r['marketId']) for r in rows)); cut=mids[int(len(mids)*.75)]
 return [r for r in rows if int(r['marketId'])<cut],[r for r in rows if int(r['marketId'])>=cut],cut

def expand(rows):
 X=[];y=[]
 for r in rows:
  typ=str(r['nextObjectiveTransition']); dt=float(r['nextObjectiveTransitionDtS']); horizon=30 if typ=='PERSIST_NO_PROGRESS' else min(30,max(1,int(np.ceil(dt))))
  b=xbase(r)
  for sec in range(1,horizon+1):
   X.append(b+[float(sec),float(sec)/max(1.,float(r.get('currentObjectiveAgeS') or 0)+sec)])
   y.append(typ if typ in EVENTS and sec==horizon else 'NONE')
 return np.asarray(X,dtype=np.float32),np.asarray(y,dtype=object)

def fit(rows):
 X,y=expand(rows); cnt=Counter(y); w=np.asarray([len(y)/(len(cnt)*cnt[z]) for z in y],dtype=float)
 clf=HistGradientBoostingClassifier(max_iter=180,learning_rate=.08,max_leaf_nodes=31,min_samples_leaf=80,l2_regularization=1.0,random_state=611)
 clf.fit(X,y,sample_weight=w)
 return clf,{'expandedRows':len(y),'classCounts':dict(cnt)}

def predict_one(clf,r):
 surv=1.0; masses=[]; b=xbase(r); class_to_idx={c:i for i,c in enumerate(clf.classes_)}
 for sec in range(1,31):
  xx=np.asarray([b+[float(sec),float(sec)/max(1.,float(r.get('currentObjectiveAgeS') or 0)+sec)]],dtype=np.float32)
  pr=clf.predict_proba(xx)[0]; pnone=float(pr[class_to_idx.get('NONE',0)]) if 'NONE' in class_to_idx else 0.0
  for ev in EVENTS:
   if ev in class_to_idx:
    masses.append((surv*float(pr[class_to_idx[ev]]),ev,sec))
  surv*=pnone
 masses.append((surv,'PERSIST_NO_PROGRESS',30))
 return max(masses,key=lambda z:(z[0],-z[2]))

def main():
 rows=load();tr,va,cut=split(rows);clf,train=fit(tr);pred=[predict_one(clf,r) for r in va];yp=[z[1] for z in pred];yv=[str(r['nextObjectiveTransition']) for r in va]
 bac=float(balanced_accuracy_score(yv,yp)); per={}
 for c in sorted(set(yv)):
  ix=[i for i,x in enumerate(yv) if x==c];per[c]={'n':len(ix),'recall':float(np.mean([yp[i]==c for i in ix]))}
 dur_ix=[i for i,r in enumerate(va) if str(r['nextObjectiveTransition'])!='PERSIST_NO_PROGRESS']; rel=[];ae=[]
 for i in dur_ix:
  a=float(va[i]['nextObjectiveTransitionDtS']);p=float(pred[i][2]);ae.append(abs(p-a));rel.append(abs(p-a)/max(1.,a))
 rep={'version':'R4_MANAGEMENT_SIMULATOR_OBJECTIVE_SEMIMARKOV_HAZARD_V6_DEV_DIAGNOSTIC','researchOnly':True,'splitMarketCut':cut,'trainMarkets':len(set(r['marketId'] for r in tr)),'validationMarkets':len(set(r['marketId'] for r in va)),'trainRows':len(tr),'validationRows':len(va),'training':train,'transitionFamilyBalancedAccuracy':bac,'perClass':per,'durationNonCensoredMedianRelativeError':float(np.median(rel)) if rel else None,'durationNonCensoredMedianAbsoluteErrorS':float(np.median(ae)) if ae else None,'predictedCounts':dict(Counter(yp)),'actualCounts':dict(Counter(yv)),'interpretation':'Discrete-time competing-risk hazard diagnostic on development chronology only. Inference chooses the largest cumulative event mass across 1..30s, with residual survival as PERSIST; no threshold sweep.'}
 (P/'r4_management_simulator_objective_semimarkov_hazard_v6_dev_diagnostic.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
