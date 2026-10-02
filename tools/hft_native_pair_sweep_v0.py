from __future__ import annotations
import argparse,json,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools.hftbacktest_r2_execution_school_v0 import load_public_snapshots
from src.predict_bot import unified_controller_paper_v2 as mod
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0';GRID=float(mod.GRID);QTY=float(mod.SHARES);EPS=1e-9

def mid(z,side):
 v=z.get('predictUpMid') if side=='UP' else z.get('predictDownMid')
 if v is not None:return float(v)
 b=z.get('predictUpBid') if side=='UP' else z.get('predictDownBid');a=z.get('predictUpAsk') if side=='UP' else z.get('predictDownAsk');return (float(b)+float(a))/2 if b is not None and a is not None else None

def run_pair(midid,t,upbid,dnbid,off,lat=1092,resp=273,horizon=5000):
 up=round(float(upbid)-off*GRID,2);dn=round(float(dnbid)-off*GRID,2)
 if min(up,dn)<float(mod.MIN_PRICE) or max(up,dn)>.99:return {'offset':off,'invalid':True}
 events,_,meta=tape_v1.build_archive_events(midid,trade_offset='mid');bt=ex.new_bt(events,entry_latency_ms=lat,response_latency_ms=resp,queue_model='risk');ex.initialize_bt(bt);snaps=load_public_snapshots(midid)
 try:
  ex.advance_to(bt,t); r1=ex.submit_native(bt,1,'UP',up,QTY);r2=ex.submit_native(bt,2,'DOWN',dn,QTY);end=min(int(meta['lastReceivedMs']),t+horizon);ex.advance_to(bt,end)
  out={}
  reward=0.0
  for num,side,px in [(1,'UP',up),(2,'DOWN',dn)]:
   s=ex.order_snapshot(bt,num);q=float(s.get('cumExecQty') or 0);native=s.get('execPrice');epx=px
   if native is not None and math.isfinite(float(native)):epx=float(native) if side=='UP' else 1-float(native)
   future=[z for z in snaps if int(z['sampledAtMs'])>=end and int(z['sampledAtMs'])<=end+2500];m=mid(min(future,key=lambda z:int(z['sampledAtMs'])),side) if future else None
   mtm=(float(m)-epx)*q if m is not None and q>EPS else 0.0;reward+=mtm;out[side]={'filled':q,'fillPrice':epx if q>EPS else None,'endMid':m,'mtm':mtm}
  paired=min(out['UP']['filled'],out['DOWN']['filled']);locked=paired*(1-float(out['UP']['fillPrice'] or up)-float(out['DOWN']['fillPrice'] or dn)) if paired>EPS else 0.0
  return {'offset':off,'submitRc':[int(r1),int(r2)],'upPrice':up,'downPrice':dn,'reward5sMtm':reward,'pairedShares':paired,'lockedPairEdgeUsdt':locked,'legs':out}
 finally:bt.close()

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--market-ids',required=True);ap.add_argument('--checkpoints-per-market',type=int,default=2);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',')];rows=[]
 for i,m in enumerate(mids,1):
  s=sorted([z for z in load_public_snapshots(m) if all(z.get(k) is not None for k in ['predictUpBid','predictDownBid','predictUpAsk','predictDownAsk'])],key=lambda z:int(z['sampledAtMs']));lo=int(len(s)*.1);hi=max(lo+1,int(len(s)*.9));span=s[lo:hi]
  idx=[round(j*(len(span)-1)/(a.checkpoints_per_market-1)) for j in range(a.checkpoints_per_market)] if a.checkpoints_per_market>1 else [len(span)//2]
  for j in idx:
   z=span[int(j)];t=int(z['sampledAtMs']);acts=[run_pair(m,t,float(z['predictUpBid']),float(z['predictDownBid']),o) for o in (0,1,2)];rows.append({'marketId':m,'checkpointMs':t,'directionScore':z.get('directionScore'),'upBid':z['predictUpBid'],'downBid':z['predictDownBid'],'actions':acts})
  print(json.dumps({'progress':i,'marketId':m}),flush=True)
 vals=[max([0.0]+[float(a0.get('reward5sMtm') or 0) for a0 in r['actions'] if not a0.get('invalid')]) for r in rows];agg={str(o):{'reward':sum(float(a0.get('reward5sMtm') or 0) for r in rows for a0 in r['actions'] if a0.get('offset')==o and not a0.get('invalid')),'locked':sum(float(a0.get('lockedPairEdgeUsdt') or 0) for r in rows for a0 in r['actions'] if a0.get('offset')==o and not a0.get('invalid')),'paired':sum(float(a0.get('pairedShares') or 0) for r in rows for a0 in r['actions'] if a0.get('offset')==o and not a0.get('invalid'))} for o in (0,1,2)}
 rep={'version':'HFT_NATIVE_PAIR_SWEEP_V0','markets':mids,'rows':rows,'oracleReward':sum(vals),'oracleActRate':sum(v>0 for v in vals)/len(vals),'aggregate':agg};(OUT/a.output).write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'oracleReward':sum(vals),'oracleActRate':rep['oracleActRate'],'aggregate':agg}))
if __name__=='__main__':main()
