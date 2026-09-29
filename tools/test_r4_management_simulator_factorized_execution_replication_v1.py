from __future__ import annotations
import json,math,statistics
from pathlib import Path
from collections import defaultdict,Counter
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
DEV1=ROOT/'data/research/lan_worker_returns/r4-execv2-dev21/dev21.json';DEV2=ROOT/'data/research/lan_worker_returns/r4-execv2-val23/val23.json';REP=ROOT/'data/research/lan_worker_returns/r4-factorized-exec-rep20/rep20.json';OUT=P/'r4_management_simulator_factorized_execution_replication_v1.json'
RATE_TARGETS=['anyFill5s','completed5s','cancelRequest5s']
def mean(x):return sum(x)/len(x) if x else None
def bucket(r):return (round(float(r['requestedQty'])/6),round(float(r['price'])*5),int(float(r['secondsLeft'])//30),min(2,int(r['ownerCount'])),min(2,int(float(r['weakResponsibilityCount']))),min(2,int(float(r['events15s'])//2)))
def backoffs(b):
 yield b;yield b[:4]+(None,None);yield (b[0],b[1],None,b[3],None,None);yield (b[0],None,None,b[3],None,None);yield (None,)*6
def build(rows):
 t=defaultdict(list)
 for r in rows:
  for k in backoffs(bucket(r)):t[k].append(r)
 return t
def pred(tab,r):
 for k in backoffs(bucket(r)):
  xs=tab.get(k) or []
  if len(xs)>=3 or k==(None,)*6:
   pos=[x for x in xs if float(x.get('fillQty5s') or 0)>1e-9 and float(x.get('unresolvedQty') or 0)>1e-9]
   tt=[float(x['timeToFirstFillMs']) for x in pos if x.get('timeToFirstFillMs') is not None]
   return {'bucket':str(k),'support':len(xs),**{z:mean([float(x[z]) for x in xs]) for z in RATE_TARGETS},'positiveFillFraction':mean([min(1.0,float(x['fillQty5s'])/float(x['unresolvedQty'])) for x in pos]) if pos else 1.0,'ttf':statistics.median(tt) if tt else None}
 raise RuntimeError('no fallback')
def main():
 a=json.loads(DEV1.read_text());b=json.loads(DEV2.read_text());r=json.loads(REP.read_text());dev=a['rows']+b['rows'];rep=r['rows'];tab=build(dev);pp=[pred(tab,x) for x in rep]
 rates={}
 for z in RATE_TARGETS:
  aa=mean([float(x[z]) for x in rep]);pr=mean([float(x[z]) for x in pp]);rates[z]={'actual':aa,'predicted':pr,'absError':abs(pr-aa),'pass':abs(pr-aa)<=.12}
 pos=[(x,p) for x,p in zip(rep,pp) if float(x.get('fillQty5s') or 0)>1e-9 and float(x.get('unresolvedQty') or 0)>1e-9]
 cmae=mean([abs(min(1.0,float(x['fillQty5s'])/float(x['unresolvedQty']))-float(p['positiveFillFraction'])) for x,p in pos]) if pos else math.inf
 ats=[float(x['timeToFirstFillMs']) for x,p in pos if x.get('timeToFirstFillMs') is not None and p.get('ttf') is not None];pts=[float(p['ttf']) for x,p in pos if x.get('timeToFirstFillMs') is not None and p.get('ttf') is not None]
 if ats:am=statistics.median(ats);pm=statistics.median(pts);terr=abs(pm-am)/max(1.,am)
 else:am=pm=None;terr=math.inf
 exact=all(bool(x.get('executionCoreExact')) for x in r.get('markets',[]));over=sum(1 for x in rep if float(x.get('overfill5s') or 0)>1e-9);dup=int(r.get('duplicateExecutionCredit') or 0)
 checks={'executionCoreExactAll':exact,'overfillRoots':over==0,'duplicateExecutionCredit':dup==0,'rootSupport':len(rep)>=30,'positiveFillSupport':len(pos)>=8,'anyFillRate':rates['anyFill5s']['pass'],'completedRate':rates['completed5s']['pass'],'cancelRate':rates['cancelRequest5s']['pass'],'conditionalPositiveFillFractionMAE':cmae<=.25,'timeToFirstFillMedianRelativeError':terr<=.35}
 # legacy realized-quantity NMAE retained only as diagnostic
 actual=[float(x['fillQty5s']) for x in rep];expected=[float(p['anyFill5s'])*float(p['positiveFillFraction'])*float(x['unresolvedQty']) for x,p in zip(rep,pp)];scale=max(1.,mean([abs(x) for x in actual]));legacy=mean([abs(x-y) for x,y in zip(actual,expected)])/scale
 repout={'version':'R4_MANAGEMENT_SIMULATOR_FACTORIZED_EXECUTION_REPLICATION_V1','researchOnly':True,'developmentRoots':len(dev),'replicationMarkets':len(r.get('markets',[])),'replicationRoots':len(rep),'positiveFillRoots':len(pos),'partialPositiveRoots':sum(1 for x,p in pos if float(x['fillQty5s'])<float(x['unresolvedQty'])-1e-9),'rateMetrics':rates,'conditionalPositiveFillFractionMAE':cmae,'timeToFirstFillMedianActualMs':am,'timeToFirstFillMedianPredictedMs':pm,'timeToFirstFillMedianRelativeError':terr,'legacyExpectedFillQtyNormalizedMAEDiagnostic':legacy,'structural':{'executionCoreExactAll':exact,'overfillRoots':over,'duplicateExecutionCredit':dup,'pendingSubmitReservationBlocks':int(r.get('pendingSubmitReservationBlocks') or 0)},'fallbackBuckets':dict(Counter(x['bucket'] for x in pp)),'checks':checks,'gatePass':all(checks.values()),'interpretation':'Pre-registered independent replication of factorized stochastic execution heads. Occurrence is evaluated by calibration/rate error; size and timing are conditionally scored on realized positive-fill roots; no replication fitting.'}
 OUT.write_text(json.dumps(repout,indent=2),encoding='utf-8');print(json.dumps(repout,indent=2))
if __name__=='__main__':main()
