from __future__ import annotations
import argparse,json,math,statistics
from pathlib import Path

def q(xs,q):
 xs=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not xs:return None
 z=(len(xs)-1)*q;lo=int(z);hi=min(lo+1,len(xs)-1);w=z-lo
 return xs[lo]*(1-w)+xs[hi]*w

def summary(rows):
 if not rows:return {'n':0}
 return {'n':len(rows),'stalled':sum(r['stallShadow'] for r in rows),'stallRate':sum(r['stallShadow'] for r in rows)/len(rows),'maxBehindMedian':statistics.median(r['maxBehindTicks'] for r in rows),'durationMedianMs':statistics.median(r['durationMs'] for r in rows),'depletionMedian':statistics.median(r['publicLevelDepletionFraction'] for r in rows)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--ours',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 src=json.load(open(a.ours,encoding='utf-8'));rows=[]
 for r in src['rows']:
  # Shadow semantic conjunction only. Numeric 5 ticks/3s are inherited diagnostic bins from prereg evidence, not tuned on PnL/winner.
  far=float(r.get('maxBehindTicks') or 0)>=5.0-1e-9
  lease=float(r.get('durationMs') or 0)>=3000
  no_net_progress=float(r.get('publicLevelDepletionFraction') or 0)<=1e-12
  stalled=bool(far and lease and no_net_progress)
  rows.append({'marketId':r['marketId'],'key':r['key'],'filled':bool(r['filled']),'maxBehindTicks':r['maxBehindTicks'],'durationMs':r['durationMs'],'publicLevelDepletionFraction':r['publicLevelDepletionFraction'],'progressEvents':r['progressEvents'],'extensions':r['extensions'],'stallShadow':stalled})
 filled=[r for r in rows if r['filled']];unfilled=[r for r in rows if not r['filled']]
 target=src.get('targetSuccessfulCheapPostfillReference',{})
 # Aggregate Target reference lacks joint rows. Use the preregistered far-behind+rest successful cross-check (22 BTC, zero no-depletion) as external false-positive evidence, not as a fitted threshold.
 out={'version':'ETH_REPAIR_V32_PASSIVE_PIPELINE_STALLED_SHADOW_AUDIT_V1','researchOnly':True,'behaviorChange':False,'source':a.ours,'definition':'frontier escape diagnostic (maxBehind>=5 ticks) AND carrier/path lease >=3s AND zero net same-price public-level depletion; payoff-unresolved condition is implicit because source rows are second-leg carriers before completion','ours':{'all':summary(rows),'filled':summary(filled),'unfilled':summary(unfilled),'falsePositiveFilled':sum(r['stallShadow'] for r in filled),'capturedUnfilled':sum(r['stallShadow'] for r in unfilled)},'targetSuccessfulCrossCheck':{'BTC_farBehindAndRestAtLeast3s_n':22,'noDepletionCount':0,'shadowFalsePositiveCount':0,'note':'from V32 prereg/live-path synthesis; successful Target joint far-behind+long-rest episodes all retained nonzero same-price depletion'},'rows':rows,'decision':None,'boundary':['No winner/PnL used.','No behavior change and no Taker authority.','5 ticks/3s are prereg diagnostic bins, not a sweep or fitted threshold.','Public aggregate depletion is queue-context evidence, not private queue rank.']}
 fp=out['ours']['falsePositiveFilled'];cap=out['ours']['capturedUnfilled'];out['decision']='KEEP_AS_ESCALATION_CANDIDATE' if fp==0 and cap>0 else 'REJECT_OR_REFINE'
 Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'decision':out['decision'],'ours':out['ours'],'target':out['targetSuccessfulCrossCheck']},ensure_ascii=False))
if __name__=='__main__':main()
