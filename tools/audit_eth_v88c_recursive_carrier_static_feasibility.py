from __future__ import annotations
import json,math,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
V88B=ROOT/'data/research/lan_worker_returns/eth-v88b-recursive-shadow-1912961-20260903-v1/result.json'
V88A=ROOT/'data/research/lan_worker_returns/eth-v88a-recursive-passive-composite-1912961-20260903-v1/result.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V88C_RECURSIVE_CARRIER_STATIC_FEASIBILITY_20260903.json'
EPS=1e-9

def inv_from_fills():
 d=json.load(open(V88A,encoding='utf-8'))['fullCandidate'];u=dn=cost=0.0
 for x in d.get('v53FillEvents',[]):
  q=float(x.get('qty') or 0);p=float(x.get('price') or 0);s=str(x.get('side'))
  if s=='UP':u+=q
  elif s=='DOWN':dn+=q
  cost+=q*p
 return u,dn,cost,d.get('v53FillEvents',[])

def simulate(u,d,cost,p_up,p_down,baseline,max_steps=40):
 path=[];recovered=None;nonneg=None;best=min(u,d)-cost
 for i in range(1,max_steps+1):
  if abs(u-d)<=EPS:
   # tie-break to cheaper legal carrier; this is diagnostic only.
   side='UP' if p_up<=p_down else 'DOWN'
  else:side='UP' if u<d else 'DOWN'
  p=p_up if side=='UP' else p_down
  if not(EPS<p<1-EPS):break
  q=1.0/p;gap=abs(u-d);rep=min(q,gap);ov=max(0.0,q-gap)
  if side=='UP':u+=q
  else:d+=q
  cost+=q*p
  floor=min(u,d)-cost;best=max(best,floor)
  path.append({'step':i,'side':side,'price':p,'physicalQty':q,'repairAllocation':rep,'overflowAllocation':ov,'floor':floor,'gapAfter':abs(u-d)})
  if recovered is None and floor>=baseline-EPS:recovered=i
  if nonneg is None and floor>=-EPS:nonneg=i
 return {'recoveredBaselineAtStep':recovered,'nonnegativeAtStep':nonneg,'bestFloor':best,'terminalFloor':path[-1]['floor'] if path else min(u,d)-cost,'path':path}

def main():
 u0,d0,c0,fills=inv_from_fills();b=json.load(open(V88B,encoding='utf-8'));rows=[]
 for x in b.get('rows',[]):
  side=x['side'];p_side=float(x['price']);p_opp=float(x['futureRepairBid']) if x.get('futureRepairBid') is not None else None
  if p_opp is None:continue
  p_up=p_side if side=='UP' else p_opp;p_down=p_side if side=='DOWN' else p_opp
  s=simulate(u0,d0,c0,p_up,p_down,float(x['floorBefore']))
  rows.append({'t':x['t'],'pUP':p_up,'pDOWN':p_down,'bidSum':p_up+p_down,'oneStepRecoverable':bool(x.get('recoverable')),'oneStepReason':x.get('reason'),'oneStepFloorDelta':x.get('immediateFloorDelta'),'recursiveRecoveredBaselineAtStep':s['recoveredBaselineAtStep'],'recursiveNonnegativeAtStep':s['nonnegativeAtStep'],'recursiveBestFloor':s['bestFloor'],'recursiveTerminalFloor40':s['terminalFloor'],'first12':s['path'][:12]})
 rec=[r for r in rows if r['recursiveRecoveredBaselineAtStep'] is not None];non=[r for r in rows if r['recursiveNonnegativeAtStep'] is not None]
 bysum={'lt1':[r for r in rows if r['bidSum']<1-EPS],'ge1':[r for r in rows if r['bidSum']>=1-EPS]}
 def g(xs):
  rr=[r for r in xs if r['recursiveRecoveredBaselineAtStep'] is not None];nn=[r for r in xs if r['recursiveNonnegativeAtStep'] is not None]
  return {'n':len(xs),'baselineRecoverShare':len(rr)/len(xs) if xs else None,'nonnegativeShare':len(nn)/len(xs) if xs else None,'medianStepsToBaseline':statistics.median([r['recursiveRecoveredBaselineAtStep'] for r in rr]) if rr else None,'medianBidSum':statistics.median([r['bidSum'] for r in xs]) if xs else None}
 out={'version':'ETH_REPAIR_V88C_RECURSIVE_CARRIER_STATIC_FEASIBILITY','date':'2026-09-03','researchOnly':True,'sourceMarket':1912961,'initialState':{'upShares':u0,'downShares':d0,'cost':c0,'floor':min(u0,d0)-c0,'fills':fills},'summary':{'rows':len(rows),'oneStepRecoverable':sum(r['oneStepRecoverable'] for r in rows),'recursiveBaselineRecoverable':len(rec),'recursiveBaselineRecoverShare':len(rec)/len(rows) if rows else None,'recursiveNonnegative':len(non),'recursiveNonnegativeShare':len(non)/len(rows) if rows else None,'medianStepsToBaseline':statistics.median([r['recursiveRecoveredBaselineAtStep'] for r in rec]) if rec else None,'byBidSum':{k:g(v) for k,v in bysum.items()}},'decision':'ONE_STEP_RECOVERABILITY_STRUCTURALLY_UNDERESTIMATES_RECURSIVE_CARRIER' if rec and not any(r['oneStepRecoverable'] for r in rows) else 'NO_CLEAR_RECURSIVE_ADVANTAGE','rows':rows,'boundary':['strict-past same-snapshot bid prices only','diagnostic frozen-price recursion, not runtime execution forecast','minimum legal $1 carrier each step','no Target future prices/actions/winner','max40 steps descriptive only, not runtime threshold','no controller change']}
 OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'summary':out['summary'],'sample':rows[:3]},ensure_ascii=False))
if __name__=='__main__':main()
