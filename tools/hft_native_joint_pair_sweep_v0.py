from __future__ import annotations
import argparse,json,math,sys
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_execution_school_v0 import load_public_snapshots
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
from src.predict_bot import unified_controller_paper_v2 as mod
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0';GRID=float(mod.GRID);QTY=float(mod.SHARES);EPS=1e-9

def side_mid(z:dict[str,Any],side:str):
 v=z.get('predictUpMid') if side=='UP' else z.get('predictDownMid')
 if v is not None:
  try:return float(v)
  except:pass
 b=z.get('predictUpBid') if side=='UP' else z.get('predictDownBid');a=z.get('predictUpAsk') if side=='UP' else z.get('predictDownAsk')
 try:return (float(b)+float(a))/2 if b is not None and a is not None else None
 except:return None

def snapshot_at(snaps,t):
 cand=[z for z in snaps if int(z['sampledAtMs'])<=int(t)]
 return max(cand,key=lambda z:int(z['sampledAtMs'])) if cand else None

def future_snapshot(snaps,t,window=2500):
 cand=[z for z in snaps if int(z['sampledAtMs'])>=int(t) and int(z['sampledAtMs'])<=int(t)+window]
 return min(cand,key=lambda z:int(z['sampledAtMs'])) if cand else None

def run_pair(mid,t,off,horizon_ms=5000):
 snaps=load_public_snapshots(mid);z=snapshot_at(snaps,t)
 if not z:return {'offset':off,'invalid':True,'reason':'NO_SNAPSHOT'}
 ub=z.get('predictUpBid');db=z.get('predictDownBid')
 if ub is None or db is None:return {'offset':off,'invalid':True,'reason':'NO_BIDS'}
 up_px=round(float(ub)-off*GRID,2);dn_px=round(float(db)-off*GRID,2)
 if min(up_px,dn_px)<float(mod.MIN_PRICE) or max(up_px,dn_px)>0.99:return {'offset':off,'invalid':True,'upPrice':up_px,'downPrice':dn_px}
 events,_,meta=tape_v1.build_archive_events(mid,trade_offset='mid');bt=ex.new_bt(events,entry_latency_ms=1092,response_latency_ms=273,queue_model='risk');ex.initialize_bt(bt)
 try:
  ex.advance_to(bt,int(t));rcu=ex.submit_native(bt,1,'UP',up_px,QTY);rcd=ex.submit_native(bt,2,'DOWN',dn_px,QTY);end=min(int(meta['lastReceivedMs']),int(t)+horizon_ms);ex.advance_to(bt,end)
  su=ex.order_snapshot(bt,1);sd=ex.order_snapshot(bt,2);qu=float(su.get('cumExecQty') or 0);qd=float(sd.get('cumExecQty') or 0)
  npu=su.get('execPrice');npd=sd.get('execPrice');fpu=up_px if npu is None else float(npu);fpd=dn_px if npd is None else 1.0-float(npd)
  fz=future_snapshot(snaps,end)
  mu=side_mid(fz,'UP') if fz else None;md=side_mid(fz,'DOWN') if fz else None
  # Unified 5s portfolio MTM: paired fills naturally realize complementary economics; one-sided fills carry directional markout.
  value=0.0
  if qu>EPS and mu is not None:value+=qu*(float(mu)-fpu)
  if qd>EPS and md is not None:value+=qd*(float(md)-fpd)
  paired=min(qu,qd);locked=paired*(1.0-fpu-fpd) if paired>EPS else 0.0
  return {'offset':off,'upPrice':up_px,'downPrice':dn_px,'pairQuoteSum':up_px+dn_px,'submitRcUp':int(rcu),'submitRcDown':int(rcd),'upFilled':qu,'downFilled':qd,'bothFilled':bool(qu>EPS and qd>EPS),'oneSidedFill':bool((qu>EPS)^(qd>EPS)),'pairedShares':paired,'upExecPrice':fpu if qu>EPS else None,'downExecPrice':fpd if qd>EPS else None,'futureUpMid5s':mu,'futureDownMid5s':md,'portfolioMtm5s':float(value),'lockedPairEdgeUsdt':float(locked),'upStatus':su.get('status'),'downStatus':sd.get('status')}
 finally:bt.close()

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--source',required=True);ap.add_argument('--market-count',type=int,default=5);ap.add_argument('--checkpoints-per-market',type=int,default=2);ap.add_argument('--output',required=True);a=ap.parse_args();src=json.load(open(OUT/a.source,encoding='utf-8'));mids=[int(x) for x in src['markets'][:a.market_count]];pp=src.get('placementRows') or [];rows=[]
 for i,mid in enumerate(mids,1):
  xs=sorted({int(r['checkpoint_ms']) for r in pp if int(r['market_id'])==mid})
  if len(xs)>a.checkpoints_per_market:
   idx=[round(j*(len(xs)-1)/(a.checkpoints_per_market-1)) for j in range(a.checkpoints_per_market)] if a.checkpoints_per_market>1 else [len(xs)//2];xs=[xs[k] for k in idx]
  for t in xs:
   acts=[run_pair(mid,t,o) for o in (0,1,2)];valid=[x for x in acts if not x.get('invalid')];best=max(valid,key=lambda x:float(x.get('portfolioMtm5s') or 0)) if valid else None;rows.append({'marketId':mid,'checkpointMs':t,'actions':acts,'oracleAction':('WAIT' if best is None or float(best.get('portfolioMtm5s') or 0)<=0 else f"PAIR_{best['offset']}_{best['offset']}"),'oracleReward':max(0.0,float(best.get('portfolioMtm5s') or 0)) if best else 0.0})
  print(json.dumps({'progress':i,'marketId':mid,'checkpoints':len(xs)},ensure_ascii=False),flush=True)
 agg={}
 for o in (0,1,2):
  aa=[x for r in rows for x in r['actions'] if x.get('offset')==o and not x.get('invalid')];agg[str(o)]={'n':len(aa),'mtm5s':sum(float(x.get('portfolioMtm5s') or 0) for x in aa),'positive':sum(float(x.get('portfolioMtm5s') or 0)>EPS for x in aa),'bothFill':sum(bool(x.get('bothFilled')) for x in aa),'oneSidedFill':sum(bool(x.get('oneSidedFill')) for x in aa),'pairedShares':sum(float(x.get('pairedShares') or 0) for x in aa),'lockedPairEdgeUsdt':sum(float(x.get('lockedPairEdgeUsdt') or 0) for x in aa)}
 rep={'version':'HFT_NATIVE_JOINT_PAIR_SWEEP_V0','researchOnly':True,'dreamFillAllowed':False,'winnerSettlementUsed':False,'source':a.source,'markets':mids,'checkpoints':len(rows),'actionSpace':['WAIT','PAIR_0_0','PAIR_1_1','PAIR_2_2'],'horizonMs':5000,'entryLatencyMs':1092,'responseLatencyMs':273,'queueModel':'risk','metric':'5s actual-HFT-fill portfolio MTM; no settlement/winner; joint pair submitted simultaneously','aggregate':agg,'oracleReward':sum(r['oracleReward'] for r in rows),'oracleActs':sum(r['oracleAction']!='WAIT' for r in rows),'rows':rows};(OUT/a.output).write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'output':str(OUT/a.output),'checkpoints':len(rows),'aggregate':agg,'oracleReward':rep['oracleReward'],'oracleActs':rep['oracleActs']},ensure_ascii=False))
if __name__=='__main__':main()
