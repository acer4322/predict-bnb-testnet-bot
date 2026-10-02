from __future__ import annotations
import argparse,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.hft_native_action_sweep_v0 import run_action
from tools.hftbacktest_r2_execution_school_v0 import load_public_snapshots
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--market-ids',required=True);ap.add_argument('--checkpoints-per-market',type=int,default=4);ap.add_argument('--output',required=True);ap.add_argument('--entry-latency-ms',type=int,default=1092);ap.add_argument('--response-latency-ms',type=int,default=273);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
 for i,mid in enumerate(mids,1):
  snaps=sorted(load_public_snapshots(mid),key=lambda z:int(z['sampledAtMs']))
  usable=[z for z in snaps if z.get('predictUpBid') is not None and z.get('predictDownBid') is not None and z.get('predictUpAsk') is not None and z.get('predictDownAsk') is not None]
  if len(usable)>4:
   lo=max(0,int(len(usable)*0.1)); hi=min(len(usable)-1,int(len(usable)*0.9)); span=usable[lo:hi+1]
  else: span=usable
  if not span:continue
  if len(span)<=a.checkpoints_per_market:chosen=span
  else:
   idx=[round(j*(len(span)-1)/(a.checkpoints_per_market-1)) for j in range(a.checkpoints_per_market)] if a.checkpoints_per_market>1 else [len(span)//2];chosen=[span[int(j)] for j in idx]
  for s in chosen:
   t=int(s['sampledAtMs']);acts=[]
   for side,bid in [('UP',float(s['predictUpBid'])),('DOWN',float(s['predictDownBid']))]:
    for off in (0,1,2):
     z=run_action(mid,t,side,bid,off,entry_latency_ms=a.entry_latency_ms,response_latency_ms=a.response_latency_ms);z['side']=side;acts.append(z)
   valid=[z for z in acts if not z.get('invalid')];best=max(valid,key=lambda z:(float(z.get('mtm1sUsdt') or 0),float(z.get('filledShares5s') or 0))) if valid else None
   rows.append({'marketId':mid,'checkpointMs':t,'upBid':float(s['predictUpBid']),'downBid':float(s['predictDownBid']),'upAsk':float(s['predictUpAsk']),'downAsk':float(s['predictDownAsk']),'actions':acts,'bestSide':best.get('side') if best else None,'bestOffset':best.get('offset') if best else None,'bestMtm1sUsdt':best.get('mtm1sUsdt') if best else None})
  print(json.dumps({'progress':i,'marketId':mid,'checkpoints':len(chosen)},ensure_ascii=False),flush=True)
 vals=[]
 for r in rows:
  a0=[float(z.get('mtm1sUsdt') or 0) for z in r['actions'] if not z.get('invalid')];vals.append(max([0.0]+a0))
 agg={}
 for side in ('UP','DOWN'):
  for off in (0,1,2):
   z=[q for r in rows for q in r['actions'] if q.get('side')==side and q.get('offset')==off and not q.get('invalid')];agg[f'{side}_{off}']={'n':len(z),'filledStates':sum(float(q.get('filledShares5s') or 0)>0 for q in z),'mtm1sUsdt':sum(float(q.get('mtm1sUsdt') or 0) for q in z)}
 rep={'version':'HFT_NATIVE_TIMEGRID_SWEEP_V0','researchOnly':True,'winnerOrSettlementUsed':False,'entryLatencyMs':a.entry_latency_ms,'responseLatencyMs':a.response_latency_ms,'markets':mids,'checkpoints':len(rows),'checkpointPolicy':'evenly spaced over middle 80% of public snapshots; no R2 action timing','actionSpace':['WAIT','UP_0','UP_1','UP_2','DOWN_0','DOWN_1','DOWN_2'],'oracleActCheckpoints':sum(v>0 for v in vals),'oracleActRate':sum(v>0 for v in vals)/len(vals) if vals else None,'oracleMtm1sUsdt':sum(vals),'aggregate':agg,'rows':rows};(OUT/a.output).write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'output':str(OUT/a.output),'checkpoints':len(rows),'oracleActRate':rep['oracleActRate'],'oracleMtm1sUsdt':rep['oracleMtm1sUsdt'],'aggregate':agg},ensure_ascii=False))
if __name__=='__main__':main()
