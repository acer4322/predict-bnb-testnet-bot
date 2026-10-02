from __future__ import annotations
import json,statistics,math
from pathlib import Path
from collections import defaultdict,Counter
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
FRESH=json.loads((P/'r4_management_simulator_execution_primitives_dev7_v1.json').read_text());UN=json.loads((P/'r4_management_simulator_execution_primitives_val8_v1.json').read_text())
DEV_IDS={int(x['marketId']) for x in UN['markets'][:16]};VAL_IDS={int(x['marketId']) for x in UN['markets'][16:23]}
dev=FRESH['rows']+[r for r in UN['rows'] if int(r['marketId']) in DEV_IDS];val=[r for r in UN['rows'] if int(r['marketId']) in VAL_IDS]
PARTIAL=0.2755555555555556
def bucket(r):return (round(float(r['requestedQty'])/6),round(float(r['price'])*5),int(float(r['secondsLeft'])//30),min(2,int(r['ownerCount'])),min(2,int(float(r['weakResponsibilityCount']))),min(2,int(float(r['events15s'])//2)))
def backoffs(b):
 yield b;yield b[:4]+(None,None);yield (b[0],b[1],None,b[3],None,None);yield (b[0],None,None,b[3],None,None);yield (None,None,None,None,None,None)
tab=defaultdict(list)
for r in dev:
 for k in backoffs(bucket(r)):tab[k].append(r)
def pred(r):
 for k in backoffs(bucket(r)):
  xs=tab[k]
  if len(xs)>=4 or k==(None,None,None,None,None,None):
   pa=sum(float(x['anyFill5s']) for x in xs)/len(xs);pc=sum(float(x['completed5s']) for x in xs)/len(xs);pcan=sum(float(x['cancelRequest5s']) for x in xs)/len(xs);pc=min(pc,pa);req=float(r['requestedQty']);eq=req*(pc+max(0.,pa-pc)*PARTIAL);tt=[float(x['timeToFirstFillMs']) for x in xs if x.get('timeToFirstFillMs') is not None];return {'any':pa,'comp':pc,'cancel':pcan,'qty':eq,'ttf':statistics.median(tt) if tt else None,'bucket':str(k)}
if len(val)<18:raise SystemExit(f'INSUFFICIENT_VALIDATION_ROOTS {len(val)}')
p=[pred(r) for r in val]
def avg(xs):return sum(xs)/len(xs)
rate={}
for key,pk in [('anyFill5s','any'),('completed5s','comp'),('cancelRequest5s','cancel')]:
 a=avg([float(r[key]) for r in val]);q=avg([x[pk] for x in p]);rate[key]={'actual':a,'predicted':q,'absError':abs(a-q),'pass':abs(a-q)<=.12}
scale=max(1.,avg([abs(float(r['fillQty5s'])) for r in val]));qmae=avg([abs(float(r['fillQty5s'])-x['qty']) for r,x in zip(val,p)])/scale
ats=[float(r['timeToFirstFillMs']) for r in val if r.get('timeToFirstFillMs') is not None];pts=[float(x['ttf']) for r,x in zip(val,p) if r.get('timeToFirstFillMs') is not None and x.get('ttf') is not None]
am=statistics.median(ats) if ats else None;pm=statistics.median(pts) if pts else None;terr=abs(pm-am)/max(1.,am) if am is not None and pm is not None else math.inf
rep={'version':'R4_MANAGEMENT_SIMULATOR_EXECUTION_PRIMITIVE_V2_V1','researchOnly':True,'developmentRoots':len(dev),'validationRoots':len(val),'developmentMarkets':7+16,'validationMarkets':7,'rateMetrics':rate,'fillQtyNormalizedMAE':qmae,'timeToFirstFillMedianActualMs':am,'timeToFirstFillMedianPredMs':pm,'timeToFirstFillMedianRelativeError':terr,'partialFractionFallback':PARTIAL,'fallbackBuckets':dict(Counter(x['bucket'] for x in p)),'gatePass':all(x['pass'] for x in rate.values()) and qmae<=.35 and terr<=.35,'interpretation':'V2 separates occurrence/completion from quantity. Full completion quantity deterministic; rare positive-noncomplete branch uses frozen empirical partial fraction.'};(P/'r4_management_simulator_execution_primitive_v2_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
