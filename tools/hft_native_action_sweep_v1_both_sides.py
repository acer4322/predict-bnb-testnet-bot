from __future__ import annotations
import argparse,json,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.hft_native_action_sweep_v0 import run_action
from tools.hftbacktest_r2_execution_school_v0 import load_public_snapshots
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'

def latest_snapshot(snaps,t):
 z=[x for x in snaps if int(x['sampledAtMs'])<=int(t)]
 return max(z,key=lambda x:int(x['sampledAtMs'])) if z else None

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--source',required=True);ap.add_argument('--market-count',type=int,default=10);ap.add_argument('--placements-per-market',type=int,default=2);ap.add_argument('--output',required=True);a=ap.parse_args()
 src=json.loads((OUT/a.source).read_text(encoding='utf-8'));placements=src.get('placementRows') or [];mids=[int(x) for x in (src.get('markets') or [])][:a.market_count];rows=[]
 for i,mid in enumerate(mids,1):
  pp=sorted([z for z in placements if int(z['market_id'])==mid],key=lambda z:int(z['checkpoint_ms']))
  if not pp:continue
  if len(pp)<=a.placements_per_market:chosen=pp
  else:
   idx=[round(j*(len(pp)-1)/(a.placements_per_market-1)) for j in range(a.placements_per_market)] if a.placements_per_market>1 else [len(pp)//2];chosen=[pp[int(j)] for j in idx]
  snaps=load_public_snapshots(mid)
  for p in chosen:
   t=int(p['checkpoint_ms']);s=latest_snapshot(snaps,t)
   if s is None:continue
   upb=s.get('predictUpBid');dnb=s.get('predictDownBid');upa=s.get('predictUpAsk');dna=s.get('predictDownAsk')
   if upb is None or dnb is None:continue
   acts=[]
   for side,bid in [('UP',float(upb)),('DOWN',float(dnb))]:
    for off in (0,1,2):
     z=run_action(mid,t,side,bid,off);z['side']=side;acts.append(z)
   valid=[z for z in acts if not z.get('invalid')]
   best=max(valid,key=lambda z:(float(z.get('mtm1sUsdt') or 0),float(z.get('filledShares5s') or 0))) if valid else None
   rows.append({'marketId':mid,'checkpointMs':t,'r2OriginalSide':str(p['side']),'r2OriginalOffset':p.get('quote_offset_ticks'),'upBid':float(upb),'downBid':float(dnb),'upAsk':float(upa) if upa is not None else None,'downAsk':float(dna) if dna is not None else None,'actions':acts,'bestSide':best.get('side') if best else None,'bestOffset':best.get('offset') if best else None,'bestMtm1sUsdt':best.get('mtm1sUsdt') if best else None})
  print(json.dumps({'progress':i,'marketId':mid,'checkpoints':len(chosen)},ensure_ascii=False),flush=True)
 agg={}
 for side in ('UP','DOWN'):
  for off in (0,1,2):
   z=[q for r in rows for q in r['actions'] if q.get('side')==side and q.get('offset')==off and not q.get('invalid')];agg[f'{side}_{off}']={'n':len(z),'filledStates':sum(float(q.get('filledShares5s') or 0)>0 for q in z),'mtm1sUsdt':sum(float(q.get('mtm1sUsdt') or 0) for q in z)}
 oracle=sum(max(0.0,max([float(q.get('mtm1sUsdt') or 0) for q in r['actions'] if not q.get('invalid')] or [0.0])) for r in rows); act=sum(max([float(q.get('mtm1sUsdt') or 0) for q in r['actions'] if not q.get('invalid')] or [0.0])>0 for r in rows);opposite=sum(r.get('bestSide') is not None and r.get('bestSide')!=r.get('r2OriginalSide') and float(r.get('bestMtm1sUsdt') or 0)>0 for r in rows)
 rep={'version':'HFT_NATIVE_ACTION_SWEEP_V1_BOTH_SIDES','researchOnly':True,'winnerOrSettlementUsed':False,'source':a.source,'markets':mids,'checkpoints':len(rows),'actionSpace':['WAIT','UP_0','UP_1','UP_2','DOWN_0','DOWN_1','DOWN_2'],'aggregate':agg,'oracleWaitBestMtm1sUsdt':oracle,'oracleActCheckpoints':act,'positiveBestOppositeR2Side':opposite,'rows':rows};(OUT/a.output).write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'output':str(OUT/a.output),'checkpoints':len(rows),'oracle':oracle,'act':act,'positiveBestOppositeR2Side':opposite,'aggregate':agg},ensure_ascii=False))
if __name__=='__main__':main()
