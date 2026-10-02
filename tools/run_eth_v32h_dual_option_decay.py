from __future__ import annotations
import json, sqlite3, statistics
from collections import defaultdict
from pathlib import Path
import numpy as np
from tools import audit_eth_v32f_joint_parent_reachability_v1 as base

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'data/research/r4_v0/p0_provenance_v1'
SOURCES={
 'UNSEEN20A':ROOT/'data/research/lan_worker_returns/eth-v24-shadow-quality-unseen20-a/result.json',
 'CONFIRM20B':ROOT/'data/research/lan_worker_returns/eth-v24-shadow-quality-confirm20-b/result.json'}
OUT=BASE/'ETH_REPAIR_V32H_DUAL_OPTION_DECAY_RESULT.json'

def med(xs):
 xs=[float(x) for x in xs if x is not None and np.isfinite(float(x))]
 return float(np.median(xs)) if xs else None

def auc(scores,labels):
 p=[float(s) for s,y in zip(scores,labels) if y and s is not None]; n=[float(s) for s,y in zip(scores,labels) if not y and s is not None]
 if not p or not n:return None
 w=t=0
 for a in p:
  for b in n:
   if a>b:w+=1
   elif a==b:t+=1
 return (w+.5*t)/(len(p)*len(n))

def build(path,name,con):
 src=json.loads(path.read_text(encoding='utf-8')); rows=[dict(r) for r in src['rows']]
 by=defaultdict(list)
 for r in rows:by[int(r['marketId'])].append(r)
 enriched=[]
 for mid,rr in sorted(by.items()):
  rr=sorted(rr,key=lambda x:int(x['placed']))
  states,end=base.reconstruct_market(con,mid,[int(x['placed']) for x in rr])
  seg=0
  for r in rr:
   t=int(r['placed']); s=states.get(t); z=dict(r); z['segment']=seg
   if s and end:
    ub,ua=s; bid,ask=base.side_book(str(r['side']),ub,ua); midp=(bid+ask)/2
    z.update(secondsLeft=(end-t)/1000.0,liveBid=bid,liveAsk=ask,marketMid=midp,
             gap=max(0.0,bid-float(r['price'])),connected=bool(float(r['price'])+1e-9>=bid))
   enriched.append(z)
   if bool(r['filled']):seg+=1
 pmap=defaultdict(list)
 for r in enriched:pmap[(int(r['marketId']),int(r['segment']))].append(r)
 out=[]
 for (mid,seg),rr in sorted(pmap.items()):
  rr=sorted(rr,key=lambda x:int(x['placed'])); valid=[x for x in rr if x.get('liveBid') is not None]
  if len(valid)<2:continue
  a,b=valid[0],valid[1]; rec=any(bool(x['filled']) for x in rr)
  z={'cohort':name,'marketId':mid,'segment':seg,'recovered':rec,'failed':not rec,'carrierCount':len(rr),
     'firstPlaced':int(a['placed']),'secondPlaced':int(b['placed']),'elapsedMs':int(b['placed'])-int(a['placed']),
     'secondsLeftSecond':b['secondsLeft'],'ceiling':float(a['price']),'repairSide':a['side'],
     'gap1':float(a['gap']),'gap2':float(b['gap']),'gapDelta':float(b['gap']-a['gap']),
     'firstAsk':float(a['liveAsk']),'secondAsk':float(b['liveAsk']),'repairAskDelta':float(b['liveAsk']-a['liveAsk']),
     'firstMid':float(a['marketMid']),'secondMid':float(b['marketMid']),'midDelta':float(b['marketMid']-a['marketMid'])}
  z['dualOptionDecay']=bool(z['gapDelta']>0 and z['repairAskDelta']>0)
  out.append(z)
 return out

def summary(rows):
 rec=[r for r in rows if r['recovered']]; fail=[r for r in rows if r['failed']]; labels=[r['failed'] for r in rows]
 def m(k):return {'aucFailure':auc([r[k] for r in rows],labels),'recoveredMedian':med([r[k] for r in rec]),'failedMedian':med([r[k] for r in fail])}
 flag=[r for r in rows if r['dualOptionDecay']]
 return {'parents':len(rows),'recovered':len(rec),'failed':len(fail),'metrics':{k:m(k) for k in ['gapDelta','repairAskDelta','midDelta','gap2','elapsedMs','secondsLeftSecond']},
 'dualOptionDecay':{'flagged':len(flag),'recoveredFalsePositives':sum(r['recovered'] for r in flag),'failedCaptured':sum(r['failed'] for r in flag),
 'recoveredFalsePositiveRate':sum(r['recovered'] for r in flag)/len(rec) if rec else None,'failedCoverage':sum(r['failed'] for r in flag)/len(fail) if fail else None}}

def main():
 con=sqlite3.connect(f'file:{base.DB.resolve().as_posix()}?mode=ro',uri=True); con.row_factory=sqlite3.Row
 cohorts={n:build(p,n,con) for n,p in SOURCES.items()}; con.close(); sums={n:summary(r) for n,r in cohorts.items()}
 keep=True
 for s in sums.values():
  a=s['metrics']['repairAskDelta']
  if a['failedMedian'] is None or a['recoveredMedian'] is None or not a['failedMedian']>a['recoveredMedian']:keep=False
  # first-disconnect hard authority is known to be highly nonselective; dual state must not flag every recovered parent.
  if s['dualOptionDecay']['recoveredFalsePositives']>=s['recovered']:keep=False
 out={'version':'ETH_REPAIR_V32H_DUAL_OPTION_DECAY_RESULT','researchOnly':True,'behaviorChange':False,'actionAuthority':False,
 'preregistered':'ETH_REPAIR_V32H_DUAL_OPTION_DECAY_PREREGISTERED.json','cohortSummaries':sums,'keepShadowPrimitive':keep,
 'decision':'KEEP_DUAL_OPTION_DECAY_AS_SHADOW_READINESS_ONLY' if keep else 'REJECT_DUAL_OPTION_DECAY','rows':[x for rs in cohorts.values() for x in rs],
 'invalidPriorRunReplaced':True,'boundary':['strict-past receipt clock','no winner/PnL state','no threshold sweep','consumed cohorts only','no Taker submit authority','no 8781']}
 OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'decision':out['decision'],'summaries':sums,'output':str(OUT)},ensure_ascii=False))
if __name__=='__main__':main()
