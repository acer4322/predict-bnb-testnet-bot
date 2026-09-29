from __future__ import annotations
import argparse,json,sys,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.hft_native_action_sweep_v0 import run_action
from tools.hftbacktest_r2_execution_school_v0 import load_public_snapshots
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
PUB=['secondsLeft','directionScore','spotReturn250msBps','spotReturn1sBps','spotReturn3sBps','spotReturn5sBps','spotQueueImbalance','spotTakerImbalance250ms','spotTakerImbalance1s','futuresReturn250msBps','futuresReturn1sBps','futuresReturn3sBps','futuresReturn5sBps','futuresQueueImbalance','futuresTakerImbalance250ms','futuresTakerImbalance1s','spotMinusStrikeBps','chainlinkMinusStrikeBps','perpSpotBasisBps','predictReceiptAgeMs','predictSourceAgeMs','chainlinkReceiptAgeMs','chainlinkSourceAgeMs','predictUpMid','predictDownMid']
def fv(x):
 try:
  z=float(x); return z if math.isfinite(z) else None
 except Exception:return None
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--market-ids',required=True);ap.add_argument('--checkpoints-per-market',type=int,default=3);ap.add_argument('--output',required=True);ap.add_argument('--entry-latency-ms',type=int,default=1092);ap.add_argument('--response-latency-ms',type=int,default=273);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
 for i,mid in enumerate(mids,1):
  snaps=sorted(load_public_snapshots(mid),key=lambda z:int(z['sampledAtMs']))
  usable=[z for z in snaps if all(z.get(k) is not None for k in ['predictUpBid','predictDownBid','predictUpAsk','predictDownAsk'])]
  if len(usable)>4:
   lo=max(0,int(len(usable)*.1));hi=min(len(usable)-1,int(len(usable)*.9));span=usable[lo:hi+1]
  else:span=usable
  if not span:continue
  if len(span)<=a.checkpoints_per_market:chosen=span
  else:
   idx=[round(j*(len(span)-1)/(a.checkpoints_per_market-1)) for j in range(a.checkpoints_per_market)] if a.checkpoints_per_market>1 else [len(span)//2];chosen=[span[int(j)] for j in idx]
  for s in chosen:
   t=int(s['sampledAtMs']);acts=[]
   for side,bid in [('UP',float(s['predictUpBid'])),('DOWN',float(s['predictDownBid']))]:
    for off in (0,1,2):
     z=run_action(mid,t,side,bid,off,entry_latency_ms=a.entry_latency_ms,response_latency_ms=a.response_latency_ms);z['side']=side;acts.append(z)
   feats={k:fv(s.get(k)) for k in PUB};feats.update({'upBid':fv(s.get('predictUpBid')),'upAsk':fv(s.get('predictUpAsk')),'downBid':fv(s.get('predictDownBid')),'downAsk':fv(s.get('predictDownAsk'))})
   rows.append({'marketId':mid,'checkpointMs':t,'features':feats,'actions':acts})
  print(json.dumps({'progress':i,'marketId':mid,'checkpoints':len(chosen)}),flush=True)
 rep={'version':'HFT_NATIVE_TIMEGRID_DATASET_V1','entryLatencyMs':a.entry_latency_ms,'responseLatencyMs':a.response_latency_ms,'markets':mids,'rows':rows,'featureNames':PUB+['upBid','upAsk','downBid','downAsk']};(OUT/a.output).write_text(json.dumps(rep,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'output':str(OUT/a.output),'markets':len(mids),'rows':len(rows)}))
if __name__=='__main__':main()
