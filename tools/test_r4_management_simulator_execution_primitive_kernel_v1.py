from __future__ import annotations
import json,math,statistics,sys
from pathlib import Path
from collections import defaultdict,Counter
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
DEV=P/'r4_management_simulator_execution_primitives_dev7_v1.json';VAL=P/'r4_management_simulator_execution_primitives_val8_v1.json'
FEATURES=['requestedQty','price','secondsLeft','ownerCount','weakResponsibilityCount','dominantResponsibilityCount','events15s','transitions15s','oldestOwnerAge']
RATE_TARGETS=['anyFill5s','completed5s','cancelRequest5s']
def bucket(r):
 return (round(float(r['requestedQty'])/6),round(float(r['price'])*5),int(float(r['secondsLeft'])//30),min(2,int(r['ownerCount'])),min(2,int(float(r['weakResponsibilityCount']))),min(2,int(float(r['events15s'])//2)))
def backoffs(b):
 yield b
 yield b[:4]+(None,None)
 yield (b[0],b[1],None,b[3],None,None)
 yield (b[0],None,None,b[3],None,None)
 yield tuple([None]*len(b))
def build(rows):
 tab=defaultdict(list)
 for r in rows:
  b=bucket(r)
  for k in backoffs(b):tab[k].append(r)
 return tab
def mean(xs):return sum(xs)/len(xs) if xs else None
def predict(tab,r):
 for k in backoffs(bucket(r)):
  xs=tab.get(k) or []
  if len(xs)>=3 or k==(None,)*6:
   out={t:mean([float(x[t]) for x in xs]) for t in RATE_TARGETS};out['fillQty5s']=mean([float(x['fillQty5s']) for x in xs]);tt=[float(x['timeToFirstFillMs']) for x in xs if x.get('timeToFirstFillMs') is not None];out['timeToFirstFillMs']=statistics.median(tt) if tt else None;out['support']=len(xs);out['bucket']=str(k);return out
 return None
def main():
 dev=json.loads(DEV.read_text()) if DEV.exists() else {};val=json.loads(VAL.read_text()) if VAL.exists() else {};dr=dev.get('rows',[]);vr=val.get('rows',[])
 if len(vr)<20:raise SystemExit(f'INSUFFICIENT_VALIDATION_ROOTS {len(vr)}')
 tab=build(dr);pred=[predict(tab,r) for r in vr]
 rr={}
 for t in RATE_TARGETS:
  actual=mean([float(r[t]) for r in vr]);p=mean([x[t] for x in pred]);rr[t]={'actual':actual,'predicted':p,'absError':abs(p-actual),'pass':abs(p-actual)<=.12}
 actual_qty=[float(r['fillQty5s']) for r in vr];pred_qty=[float(x['fillQty5s']) for x in pred];scale=max(1.,mean([abs(x) for x in actual_qty]));qmae=mean([abs(a-b) for a,b in zip(actual_qty,pred_qty)])/scale
 ats=[float(r['timeToFirstFillMs']) for r in vr if r.get('timeToFirstFillMs') is not None];pts=[float(p['timeToFirstFillMs']) for r,p in zip(vr,pred) if r.get('timeToFirstFillMs') is not None and p.get('timeToFirstFillMs') is not None]
 if ats and pts:
  am=statistics.median(ats);pm=statistics.median(pts);terr=abs(pm-am)/max(1.,am)
 else:am=pm=None;terr=math.inf
 rep={'version':'R4_MANAGEMENT_SIMULATOR_EXECUTION_PRIMITIVE_KERNEL_V1','researchOnly':True,'developmentRoots':len(dr),'validationRoots':len(vr),'developmentMarkets':len(dev.get('markets',[])),'validationMarkets':len(val.get('markets',[])),'rateMetrics':rr,'fillQtyNormalizedMAE':qmae,'timeToFirstFillMedianActualMs':am,'timeToFirstFillMedianPredMs':pm,'timeToFirstFillMedianRelativeError':terr,'fallbackBuckets':dict(Counter(x['bucket'] for x in pred)),'gatePass':all(x['pass'] for x in rr.values()) and qmae<=.35 and terr<=.35,'interpretation':'Low-capacity empirical execution primitive kernel with preregistered strict-past context buckets and hierarchical fallback.'};(P/'r4_management_simulator_execution_primitive_kernel_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
