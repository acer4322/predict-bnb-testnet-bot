from __future__ import annotations
import argparse,json,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools.hftbacktest_r2_execution_school_v0 import load_public_snapshots
from src.predict_bot import unified_controller_paper_v2 as mod
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0';QTY=float(mod.SHARES);EPS=1e-9
PUB=['secondsLeft','directionScore','spotReturn250msBps','spotReturn1sBps','spotReturn3sBps','spotReturn5sBps','spotQueueImbalance','spotTakerImbalance250ms','spotTakerImbalance1s','futuresReturn250msBps','futuresReturn1sBps','futuresReturn3sBps','futuresReturn5sBps','futuresQueueImbalance','futuresTakerImbalance250ms','futuresTakerImbalance1s','spotMinusStrikeBps','chainlinkMinusStrikeBps','perpSpotBasisBps','predictReceiptAgeMs','predictSourceAgeMs','predictUpMid','predictDownMid','predictUpBid','predictUpAsk','predictDownBid','predictDownAsk']
def side_mid(z,s):
 v=z.get('predictUpMid') if s=='UP' else z.get('predictDownMid')
 if v is not None:return float(v)
 b=z.get('predictUpBid') if s=='UP' else z.get('predictDownBid');a=z.get('predictUpAsk') if s=='UP' else z.get('predictDownAsk');return (float(b)+float(a))/2 if b is not None and a is not None else None
def run(mid,t,side,ask,lat=1092,resp=273,horizon=2500):
 events,_,meta=tape_v1.build_archive_events(mid,trade_offset='mid');bt=ex.new_bt(events,entry_latency_ms=lat,response_latency_ms=resp,queue_model='risk');ex.initialize_bt(bt);sn=load_public_snapshots(mid)
 try:
  ex.advance_to(bt,t);maxp=min(.99,float(ask)+.02);native_side,native_px=ex.native_order(side,maxp);num=1
  rc=int(bt.submit_buy_order(0,num,native_px,QTY,ex.hbt.GTC,ex.LIMIT,False)) if native_side=='BUY' else int(bt.submit_sell_order(0,num,native_px,QTY,ex.hbt.GTC,ex.LIMIT,False));end=min(int(meta['lastReceivedMs']),t+horizon);ex.advance_to(bt,end);s=ex.order_snapshot(bt,num);q=float(s.get('cumExecQty') or 0);native=s.get('execPrice');ep=float(ask)
  if native is not None and math.isfinite(float(native)):ep=float(native) if side=='UP' else 1-float(native)
  fm=int((s.get('exchangeTs') or end*1_000_000)//1_000_000) if q>EPS else None
  fut=[z for z in sn if fm is not None and int(z['sampledAtMs'])>=fm+1000 and int(z['sampledAtMs'])<=fm+3500];m=side_mid(min(fut,key=lambda z:int(z['sampledAtMs'])),side) if fut else None
  fee=mod.taker_fee(q,ep,mod.FEE_BPS) if q>EPS else 0.0;reward=((float(m)-ep)*q-fee) if m is not None and q>EPS else 0.0
  return {'side':side,'filled':q,'fillPrice':ep if q>EPS else None,'fee':fee,'futureMid1s':m,'reward1sNetFee':reward,'submitRc':rc}
 finally:bt.close()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--market-ids',required=True);ap.add_argument('--checkpoints-per-market',type=int,default=2);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',')];rows=[]
 for i,m in enumerate(mids,1):
  s=sorted([z for z in load_public_snapshots(m) if all(z.get(k) is not None for k in ['predictUpBid','predictDownBid','predictUpAsk','predictDownAsk'])],key=lambda z:int(z['sampledAtMs']));lo=int(len(s)*.1);hi=max(lo+1,int(len(s)*.9));span=s[lo:hi];idx=[round(j*(len(span)-1)/(a.checkpoints_per_market-1)) for j in range(a.checkpoints_per_market)] if a.checkpoints_per_market>1 else [len(span)//2]
  for j in idx:
   z=span[int(j)];t=int(z['sampledAtMs']);acts=[run(m,t,'UP',float(z['predictUpAsk'])),run(m,t,'DOWN',float(z['predictDownAsk']))];rows.append({'marketId':m,'checkpointMs':t,'features':{k:z.get(k) for k in PUB},'actions':acts})
  print(json.dumps({'progress':i,'marketId':m}),flush=True)
 vals=[max([0.0]+[float(a0.get('reward1sNetFee') or 0) for a0 in r['actions']]) for r in rows];agg={s:sum(float(a0.get('reward1sNetFee') or 0) for r in rows for a0 in r['actions'] if a0['side']==s) for s in ['UP','DOWN']};rep={'version':'HFT_NATIVE_TAKER_TIMEGRID_V0','markets':mids,'rows':rows,'oracleReward':sum(vals),'oracleActRate':sum(v>0 for v in vals)/len(vals),'aggregate':agg};(OUT/a.output).write_text(json.dumps(rep,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'oracleReward':sum(vals),'oracleActRate':rep['oracleActRate'],'aggregate':agg}))
if __name__=='__main__':main()
