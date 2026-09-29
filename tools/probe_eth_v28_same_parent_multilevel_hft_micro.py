from __future__ import annotations
import argparse,json,lzma,tempfile,zipfile,shutil,sys,os
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_hbt_fallback=ROOT/'hftbacktest_244'
if _hbt_fallback.exists() and str(_hbt_fallback) not in sys.path: sys.path.insert(0,str(_hbt_fallback))
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import hftbacktest_execution_tape_feed_v1 as feed
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9

def main():
 ap=argparse.ArgumentParser()
 ap.add_argument('--bundle',required=True)
 ap.add_argument('--market-id',type=int,required=True)
 ap.add_argument('--probe-t',type=int,required=True)
 ap.add_argument('--repair-side',choices=['UP','DOWN'],required=True)
 ap.add_argument('--first-price',type=float,required=True)
 ap.add_argument('--residual',type=float,required=True)
 ap.add_argument('--hold-ms',type=int,default=1500)
 a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v28_micro_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp)
  tape=tmp/'tapes'/f'{a.market_id}.json.xz'
  payload=json.loads(lzma.decompress(tape.read_bytes()).decode('utf-8'))
  feed.ARCHIVE_DIR=tape.parent
  events,times,meta=feed.build_archive_events(a.market_id,trade_offset='mid')
  bt=ex.new_bt(events,entry_latency_ms=250,response_latency_ms=250,queue_model='risk');ex.initialize_bt(bt)
  try:
   book={'bids':{},'asks':{}}
   ups=sorted(payload['updates'],key=lambda u:(int(u[1]),int(u[0])))
   first=int(meta['firstReceivedMs']);ex.advance_to(bt,first)
   for u in ups:
    if int(u[1])>a.probe_t:break
    v1.apply(book,u)
   qv=v1.quotes(book)
   if not qv:raise RuntimeError('no quotes at probe')
   side=a.repair_side;ask=float(qv[side]['ask']);ceiling=1.0-a.first_price-0.01
   if side=='UP': raw=[float(p) for p in book['bids'].keys()]
   else: raw=[1.0-float(x) for x in book['asks'].keys()]
   levels=sorted({round(p,12) for p in raw if p>EPS and p<ask-EPS and p<=ceiling+EPS and a.first_price+p<1.0-EPS},reverse=True)
   if len(levels)<2:
    out={'ok':False,'reason':'fewer_than_two_legal_levels','marketId':a.market_id,'probeT':a.probe_t,'repairSide':side,'ask':ask,'ceiling':ceiling,'levels':levels[:10]}
   else:
    p1,p2=levels[:2];q1=1.0/p1;q2=1.0/p2;reserved=q1+q2
    if reserved>a.residual+1e-7:
     out={'ok':False,'reason':'insufficient_residual_for_two_legal_minima','marketId':a.market_id,'probeT':a.probe_t,'repairSide':side,'prices':[p1,p2],'qtys':[q1,q2],'reserved':reserved,'residual':a.residual,'ask':ask,'ceiling':ceiling}
    else:
     ex.advance_to(bt,a.probe_t)
     ex.submit_native(bt,900001,side,p1,q1);ex.submit_native(bt,900002,side,p2,q2)
     ex.advance_to(bt,a.probe_t+300)
     s1=ex.order_snapshot(bt,900001);s2=ex.order_snapshot(bt,900002)
     ex.advance_to(bt,a.probe_t+a.hold_ms)
     e1=ex.order_snapshot(bt,900001);e2=ex.order_snapshot(bt,900002)
     c1=float(e1.get('cumExecQty') or 0.0);c2=float(e2.get('cumExecQty') or 0.0)
     accepted=lambda s: str(s.get('status') or '').upper() in {'NEW','PARTIALLY_FILLED','FILLED'}
     out={'ok':True,'version':'ETH_V28_SAME_PARENT_MULTILEVEL_HFT_MICRO','marketId':a.market_id,'probeT':a.probe_t,'repairSide':side,'firstPrice':a.first_price,'ask':ask,'ceiling':ceiling,'prices':[p1,p2],'qtys':[q1,q2],'reserved':reserved,'residual':a.residual,'headroom':a.residual-reserved,'after300ms':[s1,s2],'afterHold':[e1,e2],'actualFilledTotal':c1+c2,'gates':{'twoDistinctLegalPassiveLevels':p1!=p2 and p1<ask-EPS and p2<ask-EPS,'reservationWithinResidual':reserved<=a.residual+1e-7,'bothAcceptedOrFilled':accepted(e1) and accepted(e2),'actualFillCannotExceedInitialReservation':c1+c2<=reserved+1e-7,'actualFillWithinResidual':c1+c2<=a.residual+1e-7},'boundary':['consumed HFT kernel-only probe','probe timestamp/residual taken from frozen V23 trace, not runtime policy input','no Target future action used for decision authority','two native passive levels from public book','each child venue-minimum tranche','no Taker','no PnL tuning']}
     out['kernelPass']=all(out['gates'].values())
   op=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False),flush=True)
  finally:bt.close()
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
