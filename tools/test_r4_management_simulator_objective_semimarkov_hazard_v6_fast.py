from __future__ import annotations
import argparse,gzip,json
from pathlib import Path
from collections import Counter
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import balanced_accuracy_score
FEATURES=['secondsLeft','floor','absNet','coverage','absnetRatio','floorPerGross','unresolvedQty','progressRatio','ownerCount','weakResponsibilityCount','dominantResponsibilityCount','events15s','transitions15s','oldestOwnerAge','pendingCancelCount','activeObjectiveCount','sameSideActiveObjectives','oppositeSideActiveObjectives','sameSideResidualQty','oppositeSideResidualQty','sameSideReservedQty','oppositeSideReservedQty','sameSideConfirmedQty','oppositeSideConfirmedQty','currentObjectiveResidualQty','currentObjectiveReservedQty','currentObjectiveConfirmedQty','currentObjectiveResidualRatio','currentObjectiveResponsibilityCount','recentObjectiveOpens5s','recentObjectiveOpens15s','recentParallelObjectiveOpens15s','currentObjectiveAgeS','sameSideOldestObjectiveAgeS','oppositeSideOldestObjectiveAgeS','timeSinceObjectiveProgressS','timeSinceHandoffLikeS']
EVENTS=['PROGRESS_CURRENT_OBJECTIVE','OPEN_PARALLEL_WEAK','OPEN_PARALLEL_DOMINANT','CURRENT_OBJECTIVE_COMPLETES','HANDOFF_LIKE_DEPARTURE']
BIN=2

def load(path):
 op=gzip.open if str(path).endswith('.gz') else open
 with op(path,'rt',encoding='utf-8') as f:return json.load(f)['rows']
def base(r):return [float(r.get(f) or 0) for f in FEATURES]
def split(rows):
 mids=sorted(set(int(r['marketId']) for r in rows));cut=mids[int(len(mids)*.75)]
 return [r for r in rows if int(r['marketId'])<cut],[r for r in rows if int(r['marketId'])>=cut],cut
def expand(rows):
 X=[];y=[]
 for r in rows:
  typ=str(r['nextObjectiveTransition']);dt=float(r['nextObjectiveTransitionDtS']);last=15 if typ=='PERSIST_NO_PROGRESS' else min(15,max(1,int(np.ceil(dt/BIN))));b=base(r)
  for k in range(1,last+1):
   sec=k*BIN;X.append(b+[sec,sec/max(1.,float(r.get('currentObjectiveAgeS') or 0)+sec)]);y.append(typ if typ in EVENTS and k==last else 'NONE')
 return np.asarray(X,np.float32),np.asarray(y,object)
def fit(rows):
 X,y=expand(rows);cnt=Counter(y);w=np.asarray([len(y)/(len(cnt)*cnt[z]) for z in y]);clf=HistGradientBoostingClassifier(max_iter=80,learning_rate=.1,max_leaf_nodes=31,min_samples_leaf=120,l2_regularization=1.0,random_state=612).fit(X,y,sample_weight=w);return clf,dict(cnt),len(y)
def pred(clf,r):
 surv=1.;m=[];ix={c:i for i,c in enumerate(clf.classes_)};b=base(r)
 for k in range(1,16):
  sec=k*BIN;pr=clf.predict_proba(np.asarray([b+[sec,sec/max(1.,float(r.get('currentObjectiveAgeS') or 0)+sec)]],np.float32))[0];pn=float(pr[ix['NONE']]) if 'NONE' in ix else 0.
  for ev in EVENTS:
   if ev in ix:m.append((surv*float(pr[ix[ev]]),ev,sec))
  surv*=pn
 m.append((surv,'PERSIST_NO_PROGRESS',30));return max(m,key=lambda z:(z[0],-z[2]))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();rows=load(a.input);tr,va,cut=split(rows);clf,cnt,nexp=fit(tr);pp=[pred(clf,r) for r in va];yp=[z[1] for z in pp];yv=[str(r['nextObjectiveTransition']) for r in va];bac=float(balanced_accuracy_score(yv,yp));per={}
 for c in sorted(set(yv)):
  q=[i for i,x in enumerate(yv) if x==c];per[c]={'n':len(q),'recall':float(np.mean([yp[i]==c for i in q]))}
 q=[i for i,x in enumerate(yv) if x!='PERSIST_NO_PROGRESS'];rel=[abs(pp[i][2]-float(va[i]['nextObjectiveTransitionDtS']))/max(1.,float(va[i]['nextObjectiveTransitionDtS'])) for i in q];ae=[abs(pp[i][2]-float(va[i]['nextObjectiveTransitionDtS'])) for i in q]
 rep={'version':'R4_MANAGEMENT_SIMULATOR_OBJECTIVE_SEMIMARKOV_HAZARD_V6_FAST_2S_DEV','researchOnly':True,'binSeconds':2,'trainRows':len(tr),'validationRows':len(va),'expandedTrainRows':nexp,'expandedClassCounts':cnt,'splitMarketCut':cut,'transitionFamilyBalancedAccuracy':bac,'perClass':per,'durationNonCensoredMedianRelativeError':float(np.median(rel)) if rel else None,'durationNonCensoredMedianAbsoluteErrorS':float(np.median(ae)) if ae else None,'actualCounts':dict(Counter(yv)),'predictedCounts':dict(Counter(yp)),'interpretation':'2-second discrete competing-risk hazard development diagnostic; cumulative event-mass inference; no threshold sweep.'};o=Path(a.out);o.parent.mkdir(parents=True,exist_ok=True);o.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
