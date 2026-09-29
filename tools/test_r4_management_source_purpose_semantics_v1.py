from __future__ import annotations
import gzip,json,lzma,bisect
from pathlib import Path
from collections import Counter,defaultdict
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1';SRC=ROOT/'data/hft_forward_paper_v1/markets'
BASE=['secondsLeft','floor','absNet','coverage','absnetRatio','floorPerGross','unresolvedQty','progressRatio','ownerCount','weakResponsibilityCount','dominantResponsibilityCount','events15s','transitions15s','oldestOwnerAge','pendingCancelCount','activeObjectiveCount','sameSideActiveObjectives','oppositeSideActiveObjectives','sameSideResidualQty','oppositeSideResidualQty','sameSideReservedQty','oppositeSideReservedQty','sameSideConfirmedQty','oppositeSideConfirmedQty','currentObjectiveResidualQty','currentObjectiveReservedQty','currentObjectiveConfirmedQty','currentObjectiveResidualRatio','currentObjectiveResponsibilityCount','recentObjectiveOpens5s','recentObjectiveOpens15s','recentParallelObjectiveOpens15s','currentObjectiveAgeS','sameSideOldestObjectiveAgeS','oppositeSideOldestObjectiveAgeS','timeSinceObjectiveProgressS','timeSinceHandoffLikeS']
SEM=['sem_PASSIVE_REPAIR','sem_PASSIVE_MAINTAIN','sem_ACTIVE_INTERVENTION','sem_PROMOTED_SEQUENTIAL_REPAIR','sem_PROMOTED_MAKER_PRESSURE','sem_PROMOTED_TAKER_ESCALATION','sem_WAIT','sem_MAKER','sem_TAKER']
PERSIST='PERSIST_NO_PROGRESS';PAR={'OPEN_PARALLEL_WEAK','OPEN_PARALLEL_DOMINANT'};CUR={'PROGRESS_CURRENT_OBJECTIVE','CURRENT_OBJECTIVE_COMPLETES'}
def load_rows():
 with gzip.open(P/'r4_objv6_dev_rows_compact.json.gz','rt',encoding='utf-8') as f:return json.load(f)['rows']
def add_sem(rows):
 by=defaultdict(list)
 for i,r in enumerate(rows):by[int(r['marketId'])].append((i,r))
 for mid,items in by.items():
  d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8')); dec=sorted(d.get('decisionRows') or [],key=lambda z:int(z.get('decisionMs') or 0)); ts=[int(z.get('decisionMs') or 0) for z in dec]
  for i,r in items:
   j=bisect.bisect_right(ts,int(r['t']))-1; z=dec[j] if j>=0 else {}; a=str(z.get('desiredPortfolioAction') or '');p=str(z.get('primaryReason') or '');e=str(z.get('executionChoice') or '')
   for k in SEM:r[k]=0.0
   if 'sem_'+a in r:r['sem_'+a]=1.0
   if 'sem_'+p in r:r['sem_'+p]=1.0
   if 'sem_'+e in r:r['sem_'+e]=1.0
 return rows
def X(rows,cols):return np.asarray([[float(r.get(f) or 0) for f in cols] for r in rows],np.float32)
def split(rows):
 mids=sorted(set(int(r['marketId']) for r in rows));cut=mids[int(len(mids)*.75)];return [r for r in rows if int(r['marketId'])<cut],[r for r in rows if int(r['marketId'])>=cut],cut
def rf(seed,leaf=8):return RandomForestClassifier(n_estimators=260,max_depth=10,min_samples_leaf=leaf,class_weight='balanced_subsample',random_state=seed,n_jobs=6)
def eval_variant(tr,va,cols,seed):
 xt,xv=X(tr,cols),X(va,cols);yt=np.asarray([r['nextObjectiveTransition'] for r in tr]);yv=np.asarray([r['nextObjectiveTransition'] for r in va]);occ=rf(seed,12).fit(xt,(yt!=PERSIST).astype(int));op=occ.predict(xv);ti=np.where(yt!=PERSIST)[0];bt=np.asarray(['PARALLEL' if y in PAR else 'CURRENT' for y in yt[ti]]);branch=rf(seed+1,8).fit(xt[ti],bt);pit=np.where(np.isin(yt,list(PAR)))[0];cit=np.where(np.isin(yt,list(CUR)))[0];phead=rf(seed+2,6).fit(xt[pit],yt[pit]);chead=rf(seed+3,6).fit(xt[cit],yt[cit]);pred=np.full(len(va),PERSIST,dtype=object);ev=np.where(op==1)[0]
 if len(ev):
  br=branch.predict(xv[ev]);pa=ev[br=='PARALLEL'];cu=ev[br=='CURRENT'];
  if len(pa):pred[pa]=phead.predict(xv[pa])
  if len(cu):pred[cu]=chead.predict(xv[cu])
 per={}
 for c in sorted(set(yv)):
  ii=np.where(yv==c)[0];per[c]={'n':int(len(ii)),'recall':float(np.mean(pred[ii]==c))}
 return {'balancedAccuracy':float(balanced_accuracy_score(yv,pred)),'perClass':per,'predictedCounts':dict(Counter(pred)),'actualCounts':dict(Counter(yv))}
def main():
 rows=add_sem(load_rows());tr,va,cut=split(rows);base=eval_variant(tr,va,BASE,810);sem=eval_variant(tr,va,BASE+SEM,810);delta=sem['balancedAccuracy']-base['balancedAccuracy'];improved=[]
 for c,v in sem['perClass'].items():
  if c!=PERSIST and v['recall']>base['perClass'][c]['recall']+1e-12:improved.append(c)
 persist_drop=sem['perClass'][PERSIST]['recall']-base['perClass'][PERSIST]['recall'];checks={'deltaAtLeast05':delta>=.05,'twoNonPersistImprove':len(improved)>=2,'persistDropOK':persist_drop>=-.10};rep={'version':'R4_MANAGEMENT_SOURCE_PURPOSE_SEMANTICS_V1','researchOnly':True,'splitMarketCut':cut,'trainRows':len(tr),'validationRows':len(va),'baseline':base,'sourceSemantics':sem,'deltaBalancedAccuracy':delta,'improvedNonPersistClasses':improved,'persistRecallDelta':persist_drop,'checks':checks,'gatePass':all(checks.values()),'interpretation':'Development-only strict-past source-purpose restoration audit on identical V6 hierarchical model/split.'};(P/'r4_management_source_purpose_semantics_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
