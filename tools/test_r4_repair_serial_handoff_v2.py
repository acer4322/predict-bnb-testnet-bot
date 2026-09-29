from __future__ import annotations
import argparse,json,sqlite3,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools.run_r4_threeway10_benchmark_chunk_v1 import score
from tools.analyze_r4_economic_overrepair_replay_v1 import audit_rep
EPS=1e-9

def run_handoff(mid:int):
    orig_new=base.new_controller; events=[]; pending={}
    def handoff_new(a):
        c=orig_new(a); orig_add=c._add_order
        def add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack=True,bypass_guard=False):
            try:
                f=c.inventory.features(int(now)); net=float(f.get('combined_net') or 0.0); gap=abs(net); weak='DOWN' if net>EPS else 'UP' if net<-EPS else None
                if weak and side==weak and gap>EPS:
                    live=[]; reserved=0.0
                    for key,o in list(c.orders.items()):
                        if str(o.side)!=side: continue
                        s=a.snap(key); st=str(s.get('status') or '')
                        if st in {'NEW','PARTIALLY_FILLED'}:
                            q=max(0.0,float(s.get('leavesQty') or 0.0)); reserved+=q
                            live.append({'key':key,'order':o,'status':st,'leavesQty':q,'price':float(o.price),'placedAtMs':int(o.placed_at_ms),'cumExecQty':float(s.get('cumExecQty') or 0.0)})
                    if live and reserved>=gap-EPS:
                        old=min(live,key=lambda z:z['placedAtMs']); oid=str(old['order'].id)
                        if oid not in pending:
                            a.cancel(old['key']); pending[oid]={'requestedAtMs':int(now),'key':list(old['key']),'price':old['price'],'remainingAtRequest':old['leavesQty']}
                            action='CANCEL_REQUEST_WAIT_TERMINAL'
                        else:
                            action='WAIT_CANCEL_PENDING'
                        events.append({'marketId':mid,'atMs':int(now),'side':side,'reason':reason,'combinedNet':net,'absGap':gap,'liveReservedBefore':reserved,'carrierOrderId':oid,'carrierPrice':old['price'],'carrierRemaining':old['leavesQty'],'carrierAgeMs':int(now)-old['placedAtMs'],'carrierCumExecQty':old['cumExecQty'],'liveCarrierCountBefore':len(live),'action':action,'decisionId':decision_id})
                        return False
            except Exception as e:
                events.append({'marketId':mid,'atMs':int(now),'auditError':f'{type(e).__name__}:{e}'})
            return orig_add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack,bypass_guard)
        c._add_order=add;return c
    base.new_controller=handoff_new
    try:rep=r3ctl.run_market(mid,True)
    finally:base.new_controller=orig_new
    fills=rep.get('makerFillEvents') or []
    pending_fill=[]
    for oid,meta in pending.items():
        later=[x for x in fills if str(x.get('orderId'))==oid and int(x.get('atMs') or 0)>=int(meta['requestedAtMs'])]
        if later: pending_fill.append({'orderId':oid,'cancelRequestedAtMs':meta['requestedAtMs'],'fillsAfterCancelRequest':later})
    rep['repairSerialHandoffV2']={'events':events,'cancelRequests':pending,'fillsAfterCancelRequest':pending_fill};return rep

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids-json',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();dr=Path(a.data_root).resolve();base.STRATEGY_DB=dr/'strategy_target_compare_v1.db';base.mod.BOOK_DB=dr/'wallet_maker_book_inference.db';ex.BOOK_DB=dr/'wallet_maker_book_inference.db';tape_v1.ARCHIVE_DIR=dr/'execution_tape_v1/markets';settle=dr/'target_wallet_official_v1.db';ids=[int(x) for x in json.loads(Path(a.ids_json).read_text())];rows=[];errs=[]
    def winner(mid):
        c=sqlite3.connect(settle);r=c.execute("select winner from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone();c.close()
        if not r or str(r[0]) not in {'UP','DOWN'}:raise RuntimeError(f'no settlement {mid}')
        return str(r[0])
    for mid in ids:
        try:
            A=r3ctl.run_market(mid,True);B=run_handoff(mid);w=winner(mid);sa=score(A,w);sb=score(B,w);ar,_=audit_rep(mid,'R3',A);br,_=audit_rep(mid,'HANDOFF_V2',B);ha=sum(x['hardOverRepair'] for x in ar);hb=sum(x['hardOverRepair'] for x in br);d=B.get('repairSerialHandoffV2') or {};ev=d.get('events') or [];req=sum(x.get('action')=='CANCEL_REQUEST_WAIT_TERMINAL' for x in ev);wait=sum(x.get('action')=='WAIT_CANCEL_PENDING' for x in ev);late=len(d.get('fillsAfterCancelRequest') or [])
            row={'marketId':mid,'R3':sa,'handoffV2':sb,'deltaPnl':sb['pnlUsdt']-sa['pnlUsdt'],'deltaFloor':sb['finalFloor']-sa['finalFloor'],'deltaAbsNet':sb['finalAbsNet']-sa['finalAbsNet'],'cancelRequests':req,'waitPendingAttempts':wait,'fillsAfterCancelRequestOrders':late,'handoff':d,'hardOverRepairR3':ha,'hardOverRepairHandoffV2':hb};rows.append(row);print(json.dumps({k:row[k] for k in ['marketId','deltaPnl','deltaFloor','cancelRequests','waitPendingAttempts','fillsAfterCancelRequestOrders','hardOverRepairR3','hardOverRepairHandoffV2']},ensure_ascii=False),flush=True)
        except Exception as e:errs.append({'marketId':mid,'error':f'{type(e).__name__}:{e}'});print(json.dumps(errs[-1]),flush=True)
    ds=[r['deltaPnl'] for r in rows];out={'version':'R4_REPAIR_SERIAL_HANDOFF_V2','researchOnly':True,'rows':rows,'errors':errs,'aggregate':{'markets':len(rows),'errors':len(errs),'totalDeltaPnl':float(sum(ds)),'improvements':sum(x>1e-9 for x in ds),'degradations':sum(x<-1e-9 for x in ds),'ties':sum(abs(x)<=1e-9 for x in ds),'worstDeltaPnl':min(ds,default=None),'bestDeltaPnl':max(ds,default=None),'meanDeltaFloor':float(np.mean([r['deltaFloor'] for r in rows])) if rows else None,'meanDeltaAbsNet':float(np.mean([r['deltaAbsNet'] for r in rows])) if rows else None,'cancelRequests':sum(r['cancelRequests'] for r in rows),'waitPendingAttempts':sum(r['waitPendingAttempts'] for r in rows),'marketsWithFillAfterCancelRequest':sum(r['fillsAfterCancelRequestOrders']>0 for r in rows),'hardOverRepairR3':sum(r['hardOverRepairR3'] for r in rows),'hardOverRepairHandoffV2':sum(r['hardOverRepairHandoffV2'] for r in rows)}};Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps(out['aggregate'],ensure_ascii=False),flush=True)
if __name__=='__main__':main()
