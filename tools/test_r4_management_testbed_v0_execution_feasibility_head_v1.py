from __future__ import annotations
import json, math
from pathlib import Path
from collections import defaultdict
import pandas as pd
from sklearn.metrics import roc_auc_score, average_precision_score
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
PH=json.loads((P/'r4_p0b_phase_routed_shadow_controller_v1.json').read_text(encoding='utf-8'))
DEV=json.loads((P/'r4_management_simulator_execution_primitives_dev7_v1.json').read_text(encoding='utf-8'))['rows']+json.loads((P/'r4_management_simulator_execution_primitives_val8_v1.json').read_text(encoding='utf-8'))['rows']
H=pd.read_csv(ROOT/'data/research/r4_v0/hourly/r4_management_hft_shadow_unseen24_v1_rows.csv')
OUT=P/'r4_management_testbed_v0_execution_feasibility_head_v1.json'
FS=['price','secondsLeft','ownerCount','weakResponsibilityCount','events15s','oldestOwnerAge']
def gv(r,f):
 mp={'price':r.get('price',r.get('requested_px',0)),'secondsLeft':r.get('secondsLeft',r.get('seconds_left',0)),'ownerCount':r.get('ownerCount',r.get('checkpointOwnerCount',0)),'weakResponsibilityCount':r.get('weakResponsibilityCount',0),'events15s':r.get('events15s',r.get('events_15s',0)),'oldestOwnerAge':r.get('oldestOwnerAge',r.get('checkpointOldestOwnerAgeS',0))}
 return float(mp.get(f,r.get(f,0)) or 0)
def target(r,k):
 if k=='anyFill5s':return int(float(r.get('fillQty5s',r.get('rootFillShares5s',0)) or 0)>1e-9)
 return int(r.get('completed5s',r.get('rootCompleted5s',0)) or 0)
def scales():
 s={}
 for f in FS:
  xs=sorted(gv(r,f) for r in DEV);lo=xs[int(.1*(len(xs)-1))];hi=xs[int(.9*(len(xs)-1))];s[f]=max(1e-6,hi-lo)
 return s
SC=scales()
def pred(v,k):
 ds=[]
 for d in DEV:
  dist=sum(((gv(v,f)-gv(d,f))/SC[f])**2 for f in FS);ds.append((dist,d))
 nn=[x[1] for x in sorted(ds,key=lambda x:x[0])[:min(15,len(ds))]]
 return sum(target(x,k) for x in nn)/len(nn)
def main():
 # map richer unseen row by market,t,side,kind; duplicate keys allowed: use first matching exact event
 idx=defaultdict(list)
 for _,r in H.iterrows():idx[(int(r.marketId),int(r.t),str(r.side),str(r.kind))].append(r.to_dict())
 rows=[]
 for r in PH.get('trace',[]):
  if r.get('phase')!='MANAGEMENT_60_180' or int(r.get('build_now') or 0)!=1:continue
  key=(int(r['marketId']),int(r['t']),str(r['side']),str(r['kind']))
  hr=(idx.get(key) or [{}])[0]
  v=dict(hr)
  v['price']=hr.get('requested_px',0);v['secondsLeft']=r.get('seconds_left',hr.get('seconds_left',0));v['ownerCount']=len(r.get('weakResponsibilityIds') or [])+len(r.get('dominantResponsibilityIds') or [])
  v['weakResponsibilityCount']=len(r.get('weakResponsibilityIds') or []);v['events15s']=hr.get('events_15s',0)
  # phase trace lacks root age; use 0 as frozen missing-value convention for this diagnostic
  v['oldestOwnerAge']=0
  paf=pred(v,'anyFill5s');pc=pred(v,'completed5s')
  y=int(float(r.get('futureWeakMakerFill5s') or 0)>0)
  econ=int(float(r.get('floorImproved5s') or 0)>0 and float(r.get('absNetReduced5s') or 0)>0)
  rows.append({'marketId':int(r['marketId']),'t':int(r['t']),'decision':r.get('shadow_decision'),'teacher':r.get('future_management_label_5s'),'actualFill5s':y,'actualEconProgress5s':econ,'pAnyFill5s':paf,'pCompleted5s':pc,'floor':r.get('floor'),'absNet':r.get('absNet')})
 def metr(y,p):
  return {'n':len(y),'rate':sum(y)/len(y),'meanPred':sum(p)/len(p),'auc':roc_auc_score(y,p) if len(set(y))>1 else None,'ap':average_precision_score(y,p) if len(set(y))>1 else None}
 y=[x['actualFill5s'] for x in rows];p=[x['pAnyFill5s'] for x in rows];ye=[x['actualEconProgress5s'] for x in rows];pc=[x['pCompleted5s'] for x in rows]
 # diagnostic thresholds only; no action authority. Show how much current CONTINUE mass lands in low feasibility.
 cont=[x for x in rows if x['decision']=='CONTINUE']
 q={}
 for th in [0.15,0.20,0.25,0.30,0.35,0.40]:
  low=[x for x in cont if x['pAnyFill5s']<th]
  q[str(th)]={'continueRowsLowFeas':len(low),'rate':len(low)/len(cont) if cont else 0,'actualFillRate':sum(x['actualFill5s'] for x in low)/len(low) if low else None,'actualEconProgressRate':sum(x['actualEconProgress5s'] for x in low)/len(low) if low else None}
 out={'version':'R4_MANAGEMENT_TESTBED_V0_EXECUTION_FEASIBILITY_HEAD_V1','researchOnly':True,'actionAuthority':False,'developmentRoots':len(DEV),'rows':len(rows),'fillPrediction':metr(y,p),'completionVsEconProgressDiagnostic':metr(ye,pc),'continueLowFeasibilitySweepDiagnosticOnly':q,'rowsDetail':rows,'interpretation':'Existing strict-past execution primitive kNN reused as a diagnostic feasibility head; thresholds are descriptive only and not action policy.'}
 OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({k:out[k] for k in ['developmentRoots','rows','fillPrediction','completionVsEconProgressDiagnostic','continueLowFeasibilitySweepDiagnosticOnly']},indent=2))
if __name__=='__main__':main()
