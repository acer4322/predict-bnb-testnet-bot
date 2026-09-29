from __future__ import annotations

import argparse, csv, json, math, sqlite3, sys, statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import hftbacktest_true_match_calibration_v0 as tm

DB = ROOT / 'data' / 'strategy_target_compare_v1.db'
CSV = ROOT / 'data' / 'research' / '8784_r2_vs_8786_cap100_fresh_v1_markets.csv'
OUT = ROOT / 'data' / 'research' / 'hftbacktest_execution_shift_v0'
VER = 'UNIFIED_PROMOTED_OWNSTATE_V4_R2_CAP100_KEEP18_FORWARD_PAPER'
SHARES = 18.0
EPS = 1e-9


def jload(v: Any) -> dict[str, Any]:
    try: return json.loads(v) if v else {}
    except Exception: return {}


def frozen_ids(start: int, n: int) -> list[int]:
    with CSV.open(encoding='utf-8-sig', newline='') as f:
        ids = [int(r['marketId']) for r in csv.DictReader(f)][:126]
    return ids[start:start+n]


def load_market(mid: int):
    con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
    try:
        orders=[dict(r) for r in con.execute('''select order_id,side,price,shares,placed_at_ms,status,filled_at_ms,cancelled_at_ms,placement_state_json
            from our_orders where strategy_version=? and market_id=? and channel='MAKER' order by placed_at_ms,order_id''',(VER,mid))]
        dec=[dict(r) for r in con.execute('''select decision_ms,payload_json from our_decisions where strategy_version=? and market_id=? order by decision_ms''',(VER,mid))]
    finally: con.close()
    for o in orders:
        ps=jload(o.get('placement_state_json')); o['occupiedBefore']=bool(ps.get('occupiedBefore'))
    starts=[]
    for d in dec:
        p=jload(d.get('payload_json')); ep=p.get('episode') if isinstance(p,dict) else None
        if isinstance(ep,dict) and str(ep.get('kind'))=='OVERLAP' and ep.get('start_ms') is not None:
            starts.append((int(ep['start_ms']),str(ep.get('side') or '')))
    # same episode appears across many decision rows; retain unique start-ms/side.
    paper=[]; seen=set()
    for x in starts:
        if x not in seen: paper.append({'atMs':x[0],'side':x[1]}); seen.add(x)
    return orders,paper


def run_one(mid: int, entry: int, response: int, queue: str, offset: str) -> dict[str,Any]:
    events, update_times, meta = tm.depth_plus_true_trades(mid, trade_offset=offset)
    orders,paper=load_market(mid)
    bt=ex.new_bt(events,entry_latency_ms=entry,response_latency_ms=response,queue_model=queue); ex.initialize_bt(bt)
    numbered={}; by_submit=defaultdict(list); by_cancel=defaultdict(list)
    for num,o0 in enumerate(orders,1):
        o=dict(o0); o['num']=num; numbered[num]=o; by_submit[int(o['placed_at_ms'])].append(o)
        # only explicit paper cancels retire the realistic resting order; dream FILLED does not.
        if o.get('cancelled_at_ms') is not None: by_cancel[int(o['cancelled_at_ms'])].append(o)
    timeline=sorted(set(update_times)|set(by_submit)|set(by_cancel))
    submitted=set(); prev=defaultdict(float); up=down=0.0; hft=[]
    def collect(t:int):
        nonlocal up,down
        for num in sorted(submitted):
            o=numbered[num]; snap=ex.order_snapshot(bt,num); cum=float(snap.get('cumExecQty') or 0.0); old=float(prev[num])
            if cum<=old+1e-9: continue
            delta=cum-old; pre_net=up-down; pre_abs=abs(pre_net)
            if o['side']=='UP': up+=delta
            else: down+=delta
            post_net=up-down
            predom='UP' if pre_net>EPS else 'DOWN' if pre_net<-EPS else None
            if bool(o.get('occupiedBefore')) and predom==o['side'] and pre_abs>=SHARES-EPS and abs(post_net)>pre_abs+1.0:
                fill_ms=int((snap.get('exchangeTs') or t*1_000_000)//1_000_000)
                hft.append({'atMs':fill_ms,'observedAtMs':t,'side':o['side'],'deltaShares':delta,'preNet':pre_net,'postNet':post_net,'orderId':o['order_id']})
            prev[num]=cum
    try:
        for t in timeline:
            if int(bt.current_timestamp)<=t*1_000_000: ex.advance_to(bt,t)
            collect(t)
            for o in by_cancel.get(t,[]):
                num=int(o['num']); cur=bt.orders(0).get(num)
                if cur is not None and int(cur.status) in {int(ex.NEW),int(ex.PARTIALLY_FILLED)} and bool(cur.cancellable):
                    try: bt.cancel(0,num,False)
                    except Exception: pass
            for o in by_submit.get(t,[]):
                num=int(o['num']); ex.submit_native(bt,num,str(o['side']),float(o['price']),float(o['shares'])); submitted.add(num); prev[num]=0.0
        ex.advance_to(bt,int(meta['lastReceivedMs'])); collect(int(meta['lastReceivedMs']))
    finally: bt.close()

    def match_window(ms:int)->dict[str,Any]:
        used=set(); matched=[]
        for i,p in enumerate(paper):
            best=None
            for j,h in enumerate(hft):
                if j in used or p['side']!=h['side']: continue
                d=abs(int(h['atMs'])-int(p['atMs']))
                if d<=ms and (best is None or d<best[0]): best=(d,j)
            if best is not None: used.add(best[1]); matched.append(best[0])
        return {'matched':len(matched),'paperRecall':len(matched)/len(paper) if paper else None,'hftPrecisionVsPaper':len(matched)/len(hft) if hft else None,'medianAbsDeltaMs':statistics.median(matched) if matched else None}
    return {'marketId':mid,'paperOverlapTriggers':paper,'hftOverlapTriggers':hft,'paperCount':len(paper),'hftCount':len(hft),'paperHas':bool(paper),'hftHas':bool(hft),'presenceAgreement':bool(paper)==bool(hft),'match1s':match_window(1000),'match3s':match_window(3000),'match5s':match_window(5000),'match10s':match_window(10000)}


def summarize(rows):
    n=len(rows); pc=sum(r['paperCount'] for r in rows); hc=sum(r['hftCount'] for r in rows)
    return {'markets':n,'paperOverlapTriggerCount':pc,'hftOverlapTriggerCount':hc,'paperOverlapMarkets':sum(r['paperHas'] for r in rows),'hftOverlapMarkets':sum(r['hftHas'] for r in rows),'presenceAgreementRate':sum(r['presenceAgreement'] for r in rows)/n if n else None,'paperOnlyMarkets':sum(r['paperHas'] and not r['hftHas'] for r in rows),'hftOnlyMarkets':sum(r['hftHas'] and not r['paperHas'] for r in rows),'bothMarkets':sum(r['hftHas'] and r['paperHas'] for r in rows),'matchedWithin1s':sum(r['match1s']['matched'] for r in rows),'matchedWithin3s':sum(r['match3s']['matched'] for r in rows),'matchedWithin5s':sum(r['match5s']['matched'] for r in rows),'matchedWithin10s':sum(r['match10s']['matched'] for r in rows),'paperTriggerRecallWithin5s':sum(r['match5s']['matched'] for r in rows)/pc if pc else None,'paperTriggerRecallWithin10s':sum(r['match10s']['matched'] for r in rows)/pc if pc else None}


def main():
    p=argparse.ArgumentParser(); p.add_argument('--start-index',type=int,default=0); p.add_argument('--markets',type=int,default=10); p.add_argument('--entry-latency-ms',type=int,default=1092); p.add_argument('--response-latency-ms',type=int,default=273); p.add_argument('--queue-model',choices=['risk','log'],default='risk'); p.add_argument('--trade-offset',choices=['early','mid','late'],default='mid'); a=p.parse_args()
    rows=[]; errs=[]
    for mid in frozen_ids(a.start_index,a.markets):
        try: rows.append(run_one(mid,a.entry_latency_ms,a.response_latency_ms,a.queue_model,a.trade_offset))
        except Exception as e: errs.append({'marketId':mid,'error':f'{type(e).__name__}: {e}'})
    report={'version':'HFTBACKTEST_OVERLAP_TRIGGER_AUDIT_V0','config':vars(a),'summary':summarize(rows),'errors':errs,'markets':rows,'boundary':'Fixed original CAP100 Maker intents; strategy models are not rerun. Paper OVERLAP start labels are compared with OVERLAP triggers produced by HftBacktest realistic fill deltas using the same occupiedBefore trigger rule.'}
    OUT.mkdir(parents=True,exist_ok=True); path=OUT/f'cap100_overlap_trigger_i{a.start_index}_n{a.markets}_truematch_{a.trade_offset}_lat{a.entry_latency_ms}_v0.json'; path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8'); print(json.dumps({'ok':True,'path':str(path),'summary':report['summary'],'errors':errs[:3]},ensure_ascii=False)); return 0

if __name__=='__main__': raise SystemExit(main())
