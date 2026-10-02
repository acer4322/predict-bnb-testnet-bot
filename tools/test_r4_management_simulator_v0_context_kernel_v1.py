from __future__ import annotations
import json,math
from pathlib import Path
from collections import Counter,defaultdict
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
CAL=[P/f'r4_management_simulator_context_fresh21_chunk{i}_v1.json' for i in range(3)]
VAL=[P/f'r4_management_simulator_context_unseen23_chunk{i}_v1.json' for i in range(3)]
CLASSES=['COMPLETED','LIVE_STALLED','LIVE_PROGRESS','OPEN_UNOWNED']

def rows(paths):
 out=[]
 for p in paths: out.extend(json.loads(p.read_text(encoding='utf-8')).get('firstRows') or [])
 return out

def bint(x,cuts):
 x=float(x or 0)
 for i,c in enumerate(cuts):
  if x<=c:return i
 return len(cuts)

def feat(r):
 return (
  bint(r.get('seconds_left'),[60,120]),
  bint(r.get('checkpointUnresolvedQty'),[18]),
  bint(r.get('checkpointOwnerCount'),[1]),
  int(bool(r.get('pendingCancelResponsibilityIds'))),
  bint(r.get('checkpointProgressRatio'),[0]),
  bint(r.get('weakResponsibilityCount'),[1]),
 )

def dist(c):
 n=sum(c.values());return {k:(c.get(k,0)+0.5)/(n+0.5*len(CLASSES)) for k in CLASSES}

def build(cal):
 tables=[]
 # hierarchical: all 6, drop progress, drop pending, then phase+unresolved+owners, phase+owners, global
 specs=[(0,1,2,3,4,5),(0,1,2,3,5),(0,1,2,5),(0,1,2),(0,2),()]
 for spec in specs:
  t=defaultdict(Counter)
  for r in cal:
   f=feat(r); key=tuple(f[i] for i in spec);t[key][str(r.get('rootLifecycleOutcome5s'))]+=1
  tables.append((spec,t))
 return tables

def predict(r,tables):
 f=feat(r)
 for spec,t in tables:
  key=tuple(f[i] for i in spec); c=t.get(key)
  if c and (sum(c.values())>=3 or not spec): return dist(c),spec,sum(c.values())
 raise RuntimeError('no fallback')

def main():
 cal=rows(CAL);val=rows(VAL);tabs=build(cal)
 pred_sum=Counter();actual=Counter();fallback=Counter();logloss=0.0
 for r in val:
  p,spec,n=predict(r,tabs); fallback[str(spec)]+=1
  y=str(r.get('rootLifecycleOutcome5s'));actual[y]+=1
  for k,v in p.items():pred_sum[k]+=v
  logloss-=math.log(max(1e-12,p.get(y,1e-12)))
 N=len(val); pr={k:pred_sum[k]/N for k in CLASSES}; ar={k:actual[k]/N for k in CLASSES}; err={k:abs(pr[k]-ar[k]) for k in CLASSES}
 # primary classes with enough validation support; rare classes diagnostic only
 supported=[k for k in CLASSES if actual[k]>=3]
 meanerr=sum(err[k] for k in supported)/len(supported) if supported else None
 maxerr=max((err[k] for k in supported),default=None)
 passed=(N>=20 and meanerr is not None and meanerr<=0.10 and maxerr<=0.15)
 rep={'version':'R4_MANAGEMENT_SIMULATOR_V0_CONTEXT_KERNEL_V1','researchOnly':True,'calibrationRoots':len(cal),'validationRoots':N,'actualCounts':dict(actual),'actualRates':ar,'predictedRates':pr,'absoluteErrors':err,'supportedClasses':supported,'meanSupportedClassRateError':meanerr,'maxSupportedClassRateError':maxerr,'meanLogLoss':logloss/N if N else None,'fallbackUsage':dict(fallback),'gatePass':passed,'interpretation':'Low-capacity strict-past context-conditioned empirical lifecycle kernel. No manager policy or role label is used.'}
 out=P/'r4_management_simulator_v0_context_kernel_v1.json';out.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
