from __future__ import annotations
import argparse,gzip,json
from pathlib import Path
from collections import Counter
import numpy as np
from sklearn.ensemble import RandomForestClassifier,RandomForestRegressor
from sklearn.metrics import balanced_accuracy_score
FEATURES=['secondsLeft','floor','absNet','coverage','absnetRatio','floorPerGross','unresolvedQty','progressRatio','ownerCount','weakResponsibilityCount','dominantResponsibilityCount','events15s','transitions15s','oldestOwnerAge','pendingCancelCount','activeObjectiveCount','sameSideActiveObjectives','oppositeSideActiveObjectives','sameSideResidualQty','oppositeSideResidualQty','sameSideReservedQty','oppositeSideReservedQty','sameSideConfirmedQty','oppositeSideConfirmedQty','currentObjectiveResidualQty','currentObjectiveReservedQty','currentObjectiveConfirmedQty','currentObjectiveResidualRatio','currentObjectiveResponsibilityCount','recentObjectiveOpens5s','recentObjectiveOpens15s','recentParallelObjectiveOpens15s','currentObjectiveAgeS','sameSideOldestObjectiveAgeS','oppositeSideOldestObjectiveAgeS','timeSinceObjectiveProgressS','timeSinceHandoffLikeS']
PERSIST='PERSIST_NO_PROGRESS'
def load(p):
 op=gzip.open if str(p).endswith('.gz') else open
 with op(p,'rt',encoding='utf-8') as f:return json.load(f)['rows']
def X(rows):return np.asarray([[float(r.get(f) or 0) for f in FEATURES] for r in rows],dtype=np.float32)
def split(rows):
 mids=sorted(set(int(r['marketId']) for r in rows));cut=mids[int(len(mids)*.75)];return [r for r in rows if int(r['marketId'])<cut],[r for r in rows if int(r['marketId'])>=cut],cut
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();rows=load(a.input);tr,va,cut=split(rows);xt,xv=X(tr),X(va);yt=np.asarray([r['nextObjectiveTransition'] for r in tr]);yv=np.asarray([r['nextObjectiveTransition'] for r in va]);event_t=(yt!=PERSIST).astype(int);event_v=(yv!=PERSIST).astype(int)
 occ=RandomForestClassifier(n_estimators=260,max_depth=10,min_samples_leaf=12,class_weight='balanced_subsample',random_state=621,n_jobs=6).fit(xt,event_t);occ_p=occ.predict(xv);occ_b=float(balanced_accuracy_score(event_v,occ_p))
 tri=np.where(yt!=PERSIST)[0];vai=np.where(yv!=PERSIST)[0];fam=RandomForestClassifier(n_estimators=260,max_depth=10,min_samples_leaf=8,class_weight='balanced_subsample',random_state=622,n_jobs=6).fit(xt[tri],yt[tri]);fam_p=fam.predict(xv[vai]);fam_b=float(balanced_accuracy_score(yv[vai],fam_p))
 regs={};durpred=np.full(len(va),30.0);family_pred=np.full(len(va),PERSIST,dtype=object)
 family_pred[np.where(occ_p==1)[0]]=fam.predict(xv[np.where(occ_p==1)[0]])
 for c in sorted(set(yt[tri])):
  ii=np.where(yt==c)[0]
  if len(ii)<20:continue
  regs[c]=RandomForestRegressor(n_estimators=220,max_depth=10,min_samples_leaf=8,random_state=700+len(regs),n_jobs=6).fit(xt[ii],np.asarray([float(tr[j]['nextObjectiveTransitionDtS']) for j in ii]))
 for i,c in enumerate(family_pred):
  if c in regs:durpred[i]=float(np.clip(regs[c].predict(xv[i:i+1])[0],1,30))
 comb_b=float(balanced_accuracy_score(yv,family_pred));q=np.where(yv!=PERSIST)[0];rel=[abs(durpred[i]-float(va[i]['nextObjectiveTransitionDtS']))/max(1.,float(va[i]['nextObjectiveTransitionDtS'])) for i in q];ae=[abs(durpred[i]-float(va[i]['nextObjectiveTransitionDtS'])) for i in q]
 per={}
 for c in sorted(set(yv)):
  ii=np.where(yv==c)[0];per[c]={'n':len(ii),'recall':float(np.mean(family_pred[ii]==c))}
 rep={'version':'R4_MANAGEMENT_SIMULATOR_OBJECTIVE_SEMIMARKOV_FACTORIZED_V6_DEV_DIAGNOSTIC','researchOnly':True,'splitMarketCut':cut,'trainRows':len(tr),'validationRows':len(va),'occurrenceBalancedAccuracy':occ_b,'conditionalFamilyBalancedAccuracy':fam_b,'combinedTransitionBalancedAccuracy':comb_b,'durationMedianRelativeErrorNonPersist':float(np.median(rel)),'durationMedianAbsoluteErrorSNonPersist':float(np.median(ae)),'perClass':per,'actualCounts':dict(Counter(yv)),'predictedCounts':dict(Counter(family_pred)),'interpretation':'Development-only factorization diagnostic: 30s event occurrence -> conditional transition family -> family-specific duration regression. Used to localize which semi-Markov component is missing; not a promoted runtime model.'};o=Path(a.out);o.parent.mkdir(parents=True,exist_ok=True);o.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
