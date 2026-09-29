from __future__ import annotations
import json, math
from pathlib import Path
from collections import Counter
import numpy as np
from sklearn.metrics import roc_auc_score, balanced_accuracy_score
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.model_selection import LeaveOneOut

FILES=[
 Path('data/research/r4_v0/p0_provenance_v1/MS4_R244_STAGEA16_BATCH_A_RESULT_20260906.json'),
 Path('data/research/r4_v0/p0_provenance_v1/MS4_R244_STAGEA16_BATCH_B_RESULT_20260906.json'),
 Path('data/research/r4_v0/p0_provenance_v1/MS4_R244_FULL24_REMAINDER8_RESULT_20260906.json'),
]
OUT=Path('data/research/r4_v0/p0_provenance_v1/MS4_R245_ENRICHED_CORE_LIVENESS_CAUSAL_STATE_RESULT_20260906.json')
EPS=1e-9

def last_event(hist,t,event,pred=lambda e:True):
 xs=[e for e in hist if int(e.get('t') or 0)<=t and e.get('event')==event and pred(e)]
 return max(xs,key=lambda e:int(e.get('t') or 0)) if xs else None

def derive(row, cmp):
 i=cmp.get('coreActiveIntervention') or {};t=int(i['decisionT']);gen=int(i.get('generation') or 0);hist=row.get('slotHistory') or []
 scope_birth=last_event(hist,t,'RESPONSIBILITY_SCOPE_BIRTH',lambda e:int(e.get('generation') or -1)==gen)
 sb=int(scope_birth['t']) if scope_birth else min([int(e.get('t') or t) for e in hist] or [t])
 source_key=str(i.get('sourceKey'))
 source_submit=last_event(hist,t,'ROLE_SLOT_SUBMIT',lambda e:str(e.get('key'))==source_key)
 source_release=last_event(hist,t,'SLOT_RELEASE',lambda e:str(e.get('key'))==source_key)
 evid=last_event(hist,t,'R244_CORE_EVIDENCE_INJECTED',lambda e:str(e.get('sourceKey'))==source_key)
 pre=[e for e in hist if sb<=int(e.get('t') or 0)<=t]
 submits=[e for e in pre if e.get('event')=='ROLE_SLOT_SUBMIT']
 core_sub=[e for e in submits if e.get('role')=='ECONOMIC_CORE']
 cancel=[e for e in pre if e.get('event')=='SLOT_CANCEL_REQUEST']
 releases=[e for e in pre if e.get('event')=='SLOT_RELEASE']
 core_genuine=[];core_own=[]
 submap={str(e.get('key')):e for e in core_sub}
 for e in releases:
  k=str(e.get('key'))
  if k not in submap or float(e.get('cum') or 0)>EPS:continue
  if bool(e.get('cancelRequested')):core_own.append(e)
  else:core_genuine.append(e)
 fills=[e for e in pre if e.get('event')=='ROLE_FILL_SPLIT']
 repair_f=[e for e in fills if float(e.get('repairAllocated') or 0)>EPS]
 expand_f=[e for e in fills if e.get('role')=='SATELLITE_EXPAND' and float(e.get('overflowRealized') or 0)>EPS]
 expand_sub=[e for e in submits if e.get('role')=='SATELLITE_EXPAND']
 prior_active=[e for e in pre if e.get('event')=='MS4_R2_ACTIVE_REPAIR_SUBMIT' and int(e.get('t') or 0)<t]
 def age(events):
  if not events:return 1e9
  return float(t-max(int(e.get('t') or 0) for e in events))
 live=i.get('liveRoles') or []
 f={
  'scopeAgeMs':float(t-sb),'sourceOrderAgeMs':float((int(source_release['t']) if source_release else t)-(int(source_submit['t']) if source_submit else t)),
  'coreEvidenceWaitMs':float(t-(int(evid['t']) if evid else t)),'coreSubmitsSinceScopeBirth':float(len(core_sub)),
  'coreGenuineZeroFillBeforeDecision':float(len(core_genuine)),'coreOwnCancelBeforeDecision':float(len(core_own)),
  'coreInvalidatedBeforeDecision':float(sum(1 for e in cancel if e.get('reason')=='CORE_INVALIDATED')),
  'expandSubmitsSinceScopeBirth':float(len(expand_sub)),'expandFillsSinceScopeBirth':float(len(expand_f)),
  'repairFillsSinceScopeBirth':float(len(repair_f)),'repairFillQtySinceScopeBirth':float(sum(float(e.get('repairAllocated') or 0) for e in repair_f)),
  'priorActiveSubmitsSinceScopeBirth':float(len(prior_active)),'totalSlotSubmitsSinceScopeBirth':float(len(submits)),
  'totalCancelRequestsSinceScopeBirth':float(len(cancel)),'lastAnyFillAgeMs':age(fills),'lastRepairFillAgeMs':age(repair_f),'lastExpandFillAgeMs':age(expand_f),
  'liveRoleCountAtDecision':float(len(live)),'liveExpandCountAtDecision':float(sum(1 for e in live if e.get('role')=='SATELLITE_EXPAND')),
  'liveRepairCountAtDecision':float(sum(1 for e in live if e.get('role') in ('SATELLITE_REPAIR','ECONOMIC_CORE'))),
 }
 local={
  'scopeUp':1.0 if i.get('scopeSide')=='UP' else 0.0,'progress':float(i.get('repairProgressClock') or 0),'debt':float(i.get('scopeDebtQty') or 0),
  'best':float(i.get('physicalBest') or 0),'reserved':float(i.get('riskCreditReserved') or 0),'imb':float(i.get('bookImbalance') or 0),
  'upMid':float(i.get('upMid') or 0),'src':float(i.get('sourcePrice') or 0),'act':float(i.get('activePrice') or 0),
  'prem':float(i.get('activePrice') or 0)-float(i.get('sourcePrice') or 0),'floorGain':float(i.get('candidateFloor') or 0)-float(i.get('physicalFloor') or 0),
 }
 f.update(local)
 dP=float(cmp['pnlDelta']);dF=float(cmp['floorDelta'])
 cls='SAFE_GAIN' if dP>EPS and dF>=-EPS else 'RISKY_GAIN' if dP>EPS else 'HARM' if dP<-EPS else 'NEUTRAL'
 return {'marketId':int(row['marketId']),'deltaPnl':dP,'deltaFloor':dF,'deltaBest':float(cmp['bestDelta']),'class':cls,'features':f}

def main():
 candidates={}; cmps={}
 for p in FILES:
  d=json.load(open(p,encoding='utf-8'))
  for r in d['rows']:
   if r.get('cell')=='MS4_R244_SINGLE_CORE_ACTIVE':candidates.setdefault(int(r['marketId']),r)
  for x in d['comparison']:
   if x.get('coreActiveMaterialized'):cmps.setdefault(int(x['marketId']),x)
 rows=[derive(candidates[m],cmps[m]) for m in sorted(cmps)]
 lifecycle=['scopeAgeMs','sourceOrderAgeMs','coreEvidenceWaitMs','coreSubmitsSinceScopeBirth','coreGenuineZeroFillBeforeDecision','coreOwnCancelBeforeDecision','coreInvalidatedBeforeDecision','expandSubmitsSinceScopeBirth','expandFillsSinceScopeBirth','repairFillsSinceScopeBirth','repairFillQtySinceScopeBirth','priorActiveSubmitsSinceScopeBirth','totalSlotSubmitsSinceScopeBirth','totalCancelRequestsSinceScopeBirth','lastAnyFillAgeMs','lastRepairFillAgeMs','lastExpandFillAgeMs','liveRoleCountAtDecision','liveExpandCountAtDecision','liveRepairCountAtDecision']
 econ=['progress','debt','best','reserved','src','act','prem','floorGain'];book=['imb','upMid'];side=['scopeUp']
 y=np.asarray([1 if r['class']=='SAFE_GAIN' else 0 for r in rows],int)
 univ={}
 for f in lifecycle+econ+book+side:
  vals=np.asarray([r['features'][f] for r in rows],float)
  try:a=float(roc_auc_score(y,vals));univ[f]=max(a,1-a)
  except Exception:univ[f]=None
 groups={'ECON_LOCAL':econ,'BOOK_LOCAL':book,'LIFECYCLE_ONLY':lifecycle,'LIFECYCLE_ECON':lifecycle+econ,'COMBINED':lifecycle+econ+book+side}
 cv={}
 for name,fs in groups.items():
  X=np.asarray([[r['features'][f] for f in fs] for r in rows],float);res=[]
  for C in (0.01,0.05,0.1):
   probs=[];pred=[]
   for tr,te in LeaveOneOut().split(X):
    m=make_pipeline(StandardScaler(),LogisticRegression(C=C,max_iter=3000,class_weight='balanced')).fit(X[tr],y[tr])
    p=float(m.predict_proba(X[te])[0,1]);probs.append(p);pred.append(int(p>=0.5))
   res.append({'C':C,'auc':float(roc_auc_score(y,probs)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),
               'truePositive':int(sum((np.asarray(pred)==1)&(y==1))),'falsePositive':int(sum((np.asarray(pred)==1)&(y==0)))})
  cv[name]=res
 out={'version':'MS4_R2_45_ENRICHED_CORE_LIVENESS_CAUSAL_STATE_V1','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,
      'materializedSamples':len(rows),'classCounts':dict(Counter(r['class'] for r in rows)),'rows':rows,'univariateSafeGainAucAbs':univ,
      'leaveOneMarketOutStrongRegularized':cv,
      'decision':'LIFECYCLE_STATE_SHOWS_PORTABLE_SIGNAL' if max(x['auc'] for x in cv['LIFECYCLE_ONLY'])>=0.7 else 'NO_ROBUST_LOW_CAPACITY_SAFE_GAIN_SIGNAL_STOP_GATE_SEARCH',
      'boundary':['strict-past persisted candidate event history only','same R2.44 causal labels; no replay/behavior change','winner only in post-hoc labels','no threshold fitting','no runtime authority','no fresh data consumed','no 8781']}
 OUT.write_text(json.dumps(out,indent=2),encoding='utf-8')
 print(json.dumps({'ok':True,'n':len(rows),'classes':out['classCounts'],'topLifecycle':sorted([(k,v) for k,v in univ.items() if k in lifecycle and v is not None],key=lambda z:z[1],reverse=True)[:8],'cv':cv,'decision':out['decision']},ensure_ascii=False))
if __name__=='__main__':main()
