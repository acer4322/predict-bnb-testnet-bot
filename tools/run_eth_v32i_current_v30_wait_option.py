from __future__ import annotations
import json, sqlite3
from pathlib import Path
from collections import defaultdict
import numpy as np
from tools import audit_eth_v32f_joint_parent_reachability_v1 as base

ROOT=Path(__file__).resolve().parents[1]
B=ROOT/'data/research/r4_v0/p0_provenance_v1'
FILES=[
 B/'ETH_REPAIR_V30_DIRECTIONAL_THESIS_CYCLE_SMOKE_A_20260902.json',
 B/'ETH_REPAIR_V30_DIRECTIONAL_THESIS_CYCLE_SMOKE_B_20260902.json',
 B/'ETH_REPAIR_V30_CONFIRMATORY_CONSUMED_A_20260902.json',
 B/'ETH_REPAIR_V30_CONFIRMATORY_CONSUMED_B_20260902.json']
OUT=B/'ETH_REPAIR_V32I_CURRENT_V30_PARENT_WAIT_OPTION_VALUE_RESULT.json'

def med(xs):
 xs=[float(x) for x in xs if x is not None and np.isfinite(float(x))]
 return float(np.median(xs)) if xs else None

def auc(scores,labels):
 p=[float(s) for s,y in zip(scores,labels) if y and s is not None]; n=[float(s) for s,y in zip(scores,labels) if (not y) and s is not None]
 if not p or not n:return None
 w=t=0
 for a in p:
  for b in n:
   if a>b:w+=1
   elif a==b:t+=1
 return (w+.5*t)/(len(p)*len(n))

def cycle_boundaries(f):
 ev=sorted(f.get('thesisEvents',[]),key=lambda x:int(x.get('t',0)))
 starts=[e for e in ev if e.get('event') in ('OPEN_DIRECTION_ACTUAL_FILL','REEXPAND_ACTUAL_FILL')]
 recovs=[e for e in ev if e.get('event')=='PAYOFF_RECOVERED']
 out=[]
 for i,s in enumerate(starts):
  st=int(s['t']); nxt=int(starts[i+1]['t']) if i+1<len(starts) else 10**30
  rr=[r for r in recovs if st<=int(r['t'])<nxt]
  out.append({'cycle':i,'startT':st,'endT':nxt,'recovered':bool(rr),'recoveryT':int(rr[0]['t']) if rr else None})
 return out

def main():
 artifacts=[]
 for p in FILES:
  d=json.loads(p.read_text(encoding='utf-8'))
  for row in d['rows']:
   artifacts.append((p.name,int(row['marketId']),row['functional']))
 con=sqlite3.connect(f'file:{base.DB.resolve().as_posix()}?mode=ro',uri=True);con.row_factory=sqlite3.Row
 parents=[];structural=[]
 for src,mid,f in artifacts:
  caps=sorted(f.get('payoffRepairQtyCaps',[]),key=lambda x:int(x['t']))
  thesis=f.get('thesisSide')
  repair='DOWN' if thesis=='UP' else 'UP'
  for cyc in cycle_boundaries(f):
   cc=[x for x in caps if cyc['startT']<=int(x['t'])<cyc['endT']]
   if len(cc)<2:
    structural.append({'source':src,'marketId':mid,'cycle':cyc['cycle'],'recovered':cyc['recovered'],'carrierCount':len(cc),'classification':'NO_SECOND_CARRIER_GRADIENT'})
    continue
   q=[int(cc[0]['t']),int(cc[1]['t'])]; states,end=base.reconstruct_market(con,mid,q)
   if any(t not in states for t in q) or not end:continue
   vals=[]
   for c,t in zip(cc[:2],q):
    ub,ua=states[t];bid,ask=base.side_book(repair,ub,ua);midp=(bid+ask)/2;ce=float(c['price'])
    vals.append({'t':t,'price':ce,'bid':bid,'ask':ask,'mid':midp,'gap':max(0.0,bid-ce),'secondsLeft':(end-t)/1000.0})
   a,b=vals
   parents.append({'source':src,'marketId':mid,'cycle':cyc['cycle'],'recovered':cyc['recovered'],'failed':not cyc['recovered'],'repairSide':repair,'carrierCount':len(cc),
                   'first':a,'second':b,'gapDelta':b['gap']-a['gap'],'repairAskDelta':b['ask']-a['ask'],'midDelta':b['mid']-a['mid'],
                   'elapsedMs':b['t']-a['t'],'secondsLeftSecond':b['secondsLeft']})
 con.close()
 rec=[r for r in parents if r['recovered']];fail=[r for r in parents if r['failed']];labels=[r['failed'] for r in parents]
 def met(k):return {'aucFailure':auc([r[k] for r in parents],labels),'recoveredMedian':med([r[k] for r in rec]),'failedMedian':med([r[k] for r in fail])}
 metrics={k:met(k) for k in ['gapDelta','repairAskDelta','midDelta','elapsedMs','secondsLeftSecond']}
 m0896={r['cycle']:r for r in parents if r['marketId']==1840896}
 within=False
 if 0 in m0896 and 1 in m0896:
  within=(m0896[1]['repairAskDelta']>m0896[0]['repairAskDelta'] and m0896[1]['midDelta']>m0896[0]['midDelta'])
 m2999=[r for r in parents if r['marketId']==1842999 and r['cycle']==0]
 truth2999=bool(m2999 and m2999[0]['recovered'])
 direction=all(metrics[k]['failedMedian'] is not None and metrics[k]['recoveredMedian'] is not None and metrics[k]['failedMedian']>metrics[k]['recoveredMedian'] for k in ['gapDelta','repairAskDelta','midDelta'])
 aucpass=sum((metrics[k]['aucFailure'] or 0)>=0.70 for k in ['gapDelta','repairAskDelta','midDelta'])>=2
 keep=bool(direction and aucpass and within and truth2999)
 out={'version':'ETH_REPAIR_V32I_CURRENT_V30_PARENT_WAIT_OPTION_VALUE_RESULT','researchOnly':True,'behaviorChange':False,'actionAuthority':False,
      'preregistered':'ETH_REPAIR_V32I_CURRENT_V30_PARENT_WAIT_OPTION_VALUE_PREREGISTERED.json','currentParentCount':len(parents),'recovered':len(rec),'failed':len(fail),
      'metrics':metrics,'checks':{'directionPass':direction,'aucPass':aucpass,'withinMarket1840896Pass':within,'market1842999CurrentTruthRecovered':truth2999},
      'decision':'KEEP_WAIT_OPTION_GRADIENT_CURRENT_V30_SHADOW' if keep else 'REJECT_WAIT_OPTION_GRADIENT_CURRENT_V30','parents':parents,'nonGradientCurrentParents':structural,
      'boundary':['current V30 PAYOFF_RECOVERED truth only','strict-past book at first/second V30 carrier submit','no winner/PnL','no threshold sweep','no Taker authority','no 8781']}
 OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({k:out[k] for k in ['currentParentCount','recovered','failed','metrics','checks','decision']},ensure_ascii=False))
if __name__=='__main__':main()
