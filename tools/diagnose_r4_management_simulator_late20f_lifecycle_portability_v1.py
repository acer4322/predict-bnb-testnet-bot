from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1';OBS=ROOT/'data/research/lan_worker_returns/r4-e2e-late20f-observed-v3/late20f_end_to_end_observed_v1.json';H=[5,15,30]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_management_simulator_v0_semimarkov_v1 as sm

def main():
 obs=json.loads(OBS.read_text());life=obs['lifecycleRows'];exe=obs['executionRows'];first={}
 for r in life:
  k=(int(r['marketId']),str(r['checkpointResponsibilityId']))
  if k not in first or int(r['t'])<int(first[k]['t']):first[k]=r
 em={(int(x['marketId']),str(x['responsibilityId'])):x for x in exe};dev,_=sm.load_rows();lib=sm.episode_library(sm.roots(dev));A=np.vstack([x['v'] for x in lib]);q10=np.quantile(A,.1,axis=0);q90=np.quantile(A,.9,axis=0);sc=np.maximum(1e-6,q90-q10);AN=A/sc
 out={}
 for h in H:
  actual=[];pred=[]
  for k,r0 in first.items():
   e=em.get(k)
   if e is None:continue
   actual.append(1.0 if e.get('completedDtMs') is not None and int(e['completedDtMs'])<=h*1000 else 0.0)
   v=sm.vec(r0);dist=np.sum((AN-v/sc)**2,axis=1);kk=min(sm.K,len(lib));inds=np.argpartition(dist,kk-1)[:kk];pred.append(sum(lib[int(i)]['states'][h]['completed'] for i in inds)/len(inds))
 aa=sum(actual)/len(actual);pp=sum(pred)/len(pred);out[str(h)]={'roots':len(actual),'actualCompletionRate':aa,'predictedCompletionRate':pp,'absError':abs(pp-aa),'brier':sum((p-a)**2 for p,a in zip(pred,actual))/len(actual)}
 rep={'version':'R4_MANAGEMENT_SIMULATOR_LATE20F_LIFECYCLE_PORTABILITY_DIAGNOSTIC_V1','researchOnly':True,'horizons':out,'meanCompletionRateAbsError':sum(x['absError'] for x in out.values())/len(out),'interpretation':'Corrects the end-to-end diagnostic lifecycle label: root disappearance is not completion; explicit RESPONSIBILITY_COMPLETED timing is used. No fitting.'};(P/'r4_management_simulator_late20f_lifecycle_portability_diagnostic_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
