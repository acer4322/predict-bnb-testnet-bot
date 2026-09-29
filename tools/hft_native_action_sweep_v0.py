from __future__ import annotations
import argparse, json, math
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from tools.hftbacktest_r2_execution_school_v0 import load_public_snapshots
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
from src.predict_bot import unified_controller_paper_v2 as mod

OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
GRID=float(mod.GRID); QTY=float(mod.SHARES); EPS=1e-9


def side_mid(z:dict[str,Any],side:str)->float|None:
    side=side.upper()
    v=z.get('predictUpMid') if side=='UP' else z.get('predictDownMid')
    if v is not None:
        try:return float(v)
        except Exception:pass
    b=z.get('predictUpBid') if side=='UP' else z.get('predictDownBid')
    a=z.get('predictUpAsk') if side=='UP' else z.get('predictDownAsk')
    try:
        if b is not None and a is not None:return (float(b)+float(a))/2
    except Exception:pass
    return None


def future_mid(snaps:list[dict[str,Any]],side:str,t:int,window_ms:int=2500)->float|None:
    cand=[z for z in snaps if int(z['sampledAtMs'])>=int(t) and int(z['sampledAtMs'])<=int(t)+window_ms]
    if not cand:return None
    z=min(cand,key=lambda q:int(q['sampledAtMs']))
    return side_mid(z,side)


def run_action(mid:int,checkpoint_ms:int,side:str,bid:float,offset:int,horizon_ms:int=5000,entry_latency_ms:int=1092,response_latency_ms:int=273)->dict[str,Any]:
    px=round(float(bid)-int(offset)*GRID,2)
    if px<float(mod.MIN_PRICE) or px>0.99:
        return {'offset':offset,'price':px,'invalid':True}
    events,_,meta=tape_v1.build_archive_events(mid,trade_offset='mid')
    bt=ex.new_bt(events,entry_latency_ms=int(entry_latency_ms),response_latency_ms=int(response_latency_ms),queue_model='risk')
    ex.initialize_bt(bt)
    snaps=load_public_snapshots(mid)
    try:
        ex.advance_to(bt,int(checkpoint_ms))
        num=1
        rc=ex.submit_native(bt,num,side,px,QTY)
        end=min(int(meta['lastReceivedMs']),int(checkpoint_ms)+int(horizon_ms))
        ex.advance_to(bt,end)
        s=ex.order_snapshot(bt,num)
        cum=float(s.get('cumExecQty') or 0.0)
        native=s.get('execPrice')
        fill_px=px
        if native is not None and math.isfinite(float(native)):
            fill_px=float(native) if side=='UP' else 1.0-float(native)
        fill_ms=None
        if cum>EPS:
            fill_ms=int((s.get('exchangeTs') or end*1_000_000)//1_000_000)
        m1=future_mid(snaps,side,fill_ms+1000) if fill_ms is not None else None
        mtm1=(float(m1)-float(fill_px))*cum if m1 is not None and cum>EPS else 0.0
        markout_ticks=((float(m1)-float(fill_px))/GRID) if m1 is not None and cum>EPS else None
        return {'offset':int(offset),'price':float(px),'submitRc':int(rc),'filledShares5s':cum,'fillPrice':float(fill_px) if cum>EPS else None,'fillMs':fill_ms,'futureMid1s':m1,'markout1sTicksFromExec':markout_ticks,'mtm1sUsdt':float(mtm1),'status':s.get('status')}
    finally:
        bt.close()


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--source',required=True); ap.add_argument('--market-count',type=int,default=15); ap.add_argument('--placements-per-market',type=int,default=2); ap.add_argument('--output',required=True); a=ap.parse_args()
    src=json.loads((OUT/a.source).read_text(encoding='utf-8'))
    placements=src.get('placementRows') or []
    mids=[]
    for m in src.get('markets') or []:
        if int(m) not in mids:mids.append(int(m))
    mids=mids[:a.market_count]
    rows=[]
    for mi,mid in enumerate(mids,1):
        pp=[z for z in placements if int(z.get('market_id'))==mid]
        pp=sorted(pp,key=lambda z:int(z['checkpoint_ms']))
        if not pp:continue
        # deterministic spread across the market rather than cherry-picking outcomes.
        if len(pp)<=a.placements_per_market: chosen=pp
        else:
            idx=[round(i*(len(pp)-1)/(a.placements_per_market-1)) for i in range(a.placements_per_market)] if a.placements_per_market>1 else [len(pp)//2]
            chosen=[pp[int(i)] for i in idx]
        snaps=load_public_snapshots(mid)
        for p in chosen:
            side=str(p['side']).upper(); t=int(p['checkpoint_ms']); bid=float(p['current_bid'])
            base={'marketId':mid,'checkpointMs':t,'side':side,'observedBid':bid,'observedAsk':float(p['current_ask']),'originalOffsetTicks':float(p.get('quote_offset_ticks') or math.nan),'originalPrice':float(p.get('quote_price') or math.nan),'portfolio':{k:p.get(k) for k in ['maker_net','maker_abs_net','maker_paired_coverage','combined_net','combined_abs_net','combined_paired_coverage','worst_case_floor','best_case_pnl']}}
            acts=[run_action(mid,t,side,bid,o) for o in (0,1,2)]
            valid=[x for x in acts if not x.get('invalid')]
            best=max(valid,key=lambda x:(float(x.get('mtm1sUsdt') or 0.0),float(x.get('filledShares5s') or 0.0))) if valid else None
            rows.append({**base,'actions':acts,'bestOffsetByMtm1s':best.get('offset') if best else None,'bestMtm1sUsdt':best.get('mtm1sUsdt') if best else None})
        print(json.dumps({'progress':mi,'marketId':mid,'checkpoints':len(chosen)},ensure_ascii=False),flush=True)
    # Aggregate without settlement/winner PnL.
    agg={}
    for o in (0,1,2):
        zz=[a0 for r in rows for a0 in r['actions'] if a0.get('offset')==o and not a0.get('invalid')]
        agg[str(o)]={'n':len(zz),'filledStates':sum(float(z.get('filledShares5s') or 0)>EPS for z in zz),'filledShares':sum(float(z.get('filledShares5s') or 0) for z in zz),'mtm1sUsdt':sum(float(z.get('mtm1sUsdt') or 0) for z in zz),'positiveMtmStates':sum(float(z.get('mtm1sUsdt') or 0)>0 for z in zz),'negativeMtmStates':sum(float(z.get('mtm1sUsdt') or 0)<0 for z in zz)}
    best_counts={str(o):sum(r.get('bestOffsetByMtm1s')==o for r in rows) for o in (0,1,2)}
    rep={'version':'HFT_NATIVE_ACTION_SWEEP_V0','researchOnly':True,'dreamFillAllowed':False,'winnerOrSettlementUsed':False,'source':a.source,'markets':mids,'checkpoints':len(rows),'actionSpace':['SAME_SIDE_OFFSET_0','SAME_SIDE_OFFSET_1','SAME_SIDE_OFFSET_2'],'horizonMs':5000,'entryLatencyMs':1092,'responseLatencyMs':273,'queueModel':'risk','metric':'1s post-fill mark-to-market from actual HftBacktest execution price; no-fill=0','aggregate':agg,'bestOffsetCounts':best_counts,'rows':rows}
    (OUT/a.output).write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'ok':True,'output':str(OUT/a.output),'checkpoints':len(rows),'aggregate':agg,'bestOffsetCounts':best_counts},ensure_ascii=False))

if __name__=='__main__':main()
