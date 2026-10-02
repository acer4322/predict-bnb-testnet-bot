from __future__ import annotations
import argparse,gzip,json
from pathlib import Path
from collections import Counter
import numpy as np
from sklearn.ensemble import RandomForestClassifier,RandomForestRegressor
from sklearn.metrics import balanced_accuracy_score
FEATURES=['secondsLeft','floor','absNet','coverage','absnetRatio','floorPerGross','unresolvedQty','progressRatio','ownerCount','weakResponsibilityCount','dominantResponsibilityCount','events15s','transitions15s','oldestOwnerAge','pendingCancelCount','activeObjectiveCount','sameSideActiveObjectives','oppositeSideActiveObjectives','sameSideResidualQty','oppositeSideResidualQty','sameSideReservedQty','oppositeSideReservedQty','sameSideConfirmedQty','oppositeSideConfirmedQty','currentObjectiveResidualQty','currentObjectiveReservedQty','currentObjectiveConfirmedQty','currentObjectiveResidualRatio','currentObjectiveResponsibilityCount','recentObjectiveOpens5s','recentObjectiveOpens15s','recentParallelObjectiveOpens15s','currentObjectiveAgeS','sameSideOldestObjectiveAgeS','oppositeSideOldestObjectiveAgeS','timeSinceObjectiveProgressS','timeSinceHandoffLikeS']
PERSIST='PERSIST_NO_PROGRESS'; PAR={'OPEN_PARALLEL_WEAK','OPEN_PARALLEL_DOMINANT'}; CUR={'PROGRESS_CURRENT_OBJECTIVE','CURRENT_OBJECTIVE_COMPLETES'}
def load(p):
 op=gzip.open if str(p).endswith('.gz') else open
 with op(p,'rt',encoding='utf-8') as f:return json.load(f)['rows']
def X(rows):return np.asarray([[float(r.get(f) or 0) for f in FEATURES] for r in rows],dtype=np.float32)
def split(rows):
 mids=sorted(set(int(r['marketId']) for r in rows));cut=mids[int(len(mids)*.75)];return [r for r in rows if int(r['marketId'])<cut],[r for r in rows if int(r['marketId'])>=cut],cut
def rf(seed,leaf=8):return RandomForestClassifier(n_estimators=260,max_depth=10,min_samples_leaf=leaf,class_weight='balanced_subsample',random_state=seed,n_jobs=6)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();rows=load(a.input);tr,va,cut=split(rows);xt,xv=X(tr),X(va);yt=np.asarray([r['nextObjectiveTransition'] for r in tr]);yv=np.asarray([r['nextObjectiveTransition'] for r in va])
 # occurrence
 occ=rf(631,12).fit(xt,(yt!=PERSIST).astype(int));op=occ.predict(xv);occ_b=float(balanced_accuracy_score((yv!=PERSIST).astype(int),op))
 # event branch PARALLEL vs CURRENT_PATH
 ti=np.where(yt!=PERSIST)[0]; vi=np.where(yv!=PERSIST)[0]; bt=np.asarray(['PARALLEL' if y in PAR else 'CURRENT' for y in yt[ti]]); bv=np.asarray(['PARALLEL' if y in PAR else 'CURRENT' for y in yv[vi]])
 branch=rf(632,8).fit(xt[ti],bt); bp=branch.predict(xv[vi]); branch_b=float(balanced_accuracy_score(bv,bp))
 # subheads
 pit=np.where(np.isin(yt,list(PAR)))[0]; piv=np.where(np.isin(yv,list(PAR)))[0]; cit=np.where(np.isin(yt,list(CUR)))[0]; civ=np.where(np.isin(yv,list(CUR)))[0]
 phead=rf(633,6).fit(xt[pit],yt[pit]); chead=rf(634,6).fit(xt[cit],yt[cit]); psub=float(balanced_accuracy_score(yv[piv],phead.predict(xv[piv]))) if len(piv) else None; csub=float(balanced_accuracy_score(yv[civ],chead.predict(xv[civ]))) if len(civ) else None
 # compose hierarchy
 pred=np.full(len(va),PERSIST,dtype=object); evix=np.where(op==1)[0]
 if len(evix):
  br=branch.predict(xv[evix]); pa=evix[br=='PARALLEL']; cu=evix[br=='CURRENT']
  if len(pa):pred[pa]=phead.predict(xv[pa])
  if len(cu):pred[cu]=chead.predict(xv[cu])
 comb=float(balanced_accuracy_score(yv,pred))
 # family-specific duration; score both composed family and oracle true family
 regs={}
 for c in sorted(set(yt)-{PERSIST}):
  ii=np.where(yt==c)[0]
  if len(ii)>=20:regs[c]=RandomForestRegressor(n_estimators=220,max_depth=10,min_samples_leaf=8,random_state=740+len(regs),n_jobs=6).fit(xt[ii],np.asarray([float(tr[j]['nextObjectiveTransitionDtS']) for j in ii]))
 q=np.where(yv!=PERSIST)[0]; oracle_rel=[];oracle_ae=[];comp_rel=[];comp_ae=[]
 for i in q:
  actual=float(va[i]['nextObjectiveTransitionDtS']); true=yv[i]; pc=pred[i]
  if true in regs:
   d=float(np.clip(regs[true].predict(xv[i:i+1])[0],1,30));oracle_ae.append(abs(d-actual));oracle_rel.append(abs(d-actual)/max(1.,actual))
  if pc in regs:
   d=float(np.clip(regs[pc].predict(xv[i:i+1])[0],1,30));comp_ae.append(abs(d-actual));comp_rel.append(abs(d-actual)/max(1.,actual))
  else:
   comp_ae.append(abs(30-actual));comp_rel.append(abs(30-actual)/max(1.,actual))
 per={}
 for c in sorted(set(yv)):
  ii=np.where(yv==c)[0];per[c]={'n':int(len(ii)),'recall':float(np.mean(pred[ii]==c))}
 rep={'version':'R4_MANAGEMENT_SIMULATOR_OBJECTIVE_SEMIMARKOV_HIERARCHICAL_V6_DEV_DIAGNOSTIC','researchOnly':True,'splitMarketCut':cut,'trainRows':len(tr),'validationRows':len(va),'occurrenceBalancedAccuracy':occ_b,'parallelVsCurrentBalancedAccuracy':branch_b,'parallelSubtypeBalancedAccuracy':psub,'currentPathSubtypeBalancedAccuracy':csub,'combinedTransitionBalancedAccuracy':comb,'oracleFamilyDurationMedianRelativeError':float(np.median(oracle_rel)),'oracleFamilyDurationMedianAbsoluteErrorS':float(np.median(oracle_ae)),'composedDurationMedianRelativeError':float(np.median(comp_rel)),'composedDurationMedianAbsoluteErrorS':float(np.median(comp_ae)),'perClass':per,'actualCounts':dict(Counter(yv)),'predictedCounts':dict(Counter(pred)),'decision':{'familyGate45':comb>=.45,'oracleDurationGate35':float(np.median(oracle_rel))<=.35},'interpretation':'Final preregistered structural diagnostic before simulator stop/go decision. Hierarchical occurrence -> branch -> subtype with oracle-family duration upper bound.'};o=Path(a.out);o.parent.mkdir(parents=True,exist_ok=True);o.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
