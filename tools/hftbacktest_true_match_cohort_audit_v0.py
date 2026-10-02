from __future__ import annotations

import argparse, csv, json, sqlite3, statistics, sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import hftbacktest_true_match_calibration_v0 as tm

MATCHED_CSV = ROOT / 'data' / 'research' / '8784_r2_vs_8786_cap100_fresh_v1_markets.csv'
OUT_DIR = ROOT / 'data' / 'research' / 'hftbacktest_execution_shift_v0'


def matched_ids() -> list[int]:
    with MATCHED_CSV.open(encoding='utf-8-sig', newline='') as f:
        rows=[int(r['marketId']) for r in csv.DictReader(f)]
        return rows[:126]


def market_ids(label: str, matched126: bool, max_markets: int | None) -> list[int]:
    con=sqlite3.connect(ex.STRATEGY_DB)
    try:
        ids=[int(r[0]) for r in con.execute("SELECT DISTINCT market_id FROM our_orders WHERE strategy_version=? AND channel='MAKER' ORDER BY market_id",(ex.VERSIONS[label],))]
    finally:
        con.close()
    if matched126:
        keep=set(matched_ids()); ids=[m for m in ids if m in keep]
    if max_markets is not None:
        ids=ids[:max(0,int(max_markets))]
    return ids


def run(label: str, *, matched126: bool, max_markets: int | None, entry_latency_ms: int, response_latency_ms: int, queue_model: str, trade_offset: str) -> dict[str,Any]:
    ids=market_ids(label,matched126,max_markets)
    book=sqlite3.connect(ex.BOOK_DB); book.row_factory=sqlite3.Row
    strat=sqlite3.connect(ex.STRATEGY_DB); strat.row_factory=sqlite3.Row
    rows=[]; errors=[]
    original=ex.build_market_events
    try:
        for mid in ids:
            try:
                events, update_times, meta = tm.depth_plus_true_trades(mid,trade_offset=trade_offset)
                def patched(_book, market_id:int, *, depletion_as_trade:bool, _mid=mid, _events=events, _times=update_times, _meta=meta):
                    if int(market_id)==int(_mid): return _events,_times,_meta
                    return original(_book,market_id,depletion_as_trade=depletion_as_trade)
                ex.build_market_events=patched
                row=ex.fixed_decision_market_audit(book,strat,version=ex.VERSIONS[label],market_id=mid,entry_latency_ms=entry_latency_ms,response_latency_ms=response_latency_ms,queue_model=queue_model,depletion_as_trade=False)
                rows.append(row)
            except Exception as exc:
                errors.append({'marketId':mid,'error':f'{type(exc).__name__}: {exc}'})
            finally:
                ex.build_market_events=original
    finally:
        book.close(); strat.close(); ex.build_market_events=original
    def total(k:str)->int: return sum(int(r.get(k) or 0) for r in rows)
    pf=total('paperFills')
    per=[(r['hftAnyFillByPaperTerminal']/r['paperFills']) for r in rows if r.get('paperFills')]
    summary={
        'markets':len(rows),'orders':total('orders'),'paperFills':pf,
        'hftAnyFillByPaperTerminal':total('hftAnyFillByPaperTerminal'),
        'hftFullFillByPaperTerminal':total('hftFullFillByPaperTerminal'),
        'paperFillHftNoAnyFill':total('paperFillHftNoAnyFill'),
        'paperFillHftNotFull':total('paperFillHftNotFull'),
        'paperCancelHftFill':total('paperCancelHftFill'),
        'paperFillBeforeMeasuredEntryLatency':total('paperFillBeforeMeasuredEntryLatency'),
        'paperFillAnyHftAgreementRate': total('hftAnyFillByPaperTerminal')/pf if pf else None,
        'paperFillFullHftAgreementRate': total('hftFullFillByPaperTerminal')/pf if pf else None,
        'perMarketAgreementMedian': statistics.median(per) if per else None,
        'zeroAgreementMarkets':sum((r.get('paperFills') or 0)>0 and (r.get('hftAnyFillByPaperTerminal') or 0)==0 for r in rows),
    }
    return {'version':'HFTBACKTEST_TRUE_MATCH_COHORT_AUDIT_V0','strategyLabel':label,'strategyVersion':ex.VERSIONS[label],'config':{'entryLatencyMs':entry_latency_ms,'responseLatencyMs':response_latency_ms,'queueModel':queue_model,'trueMatches':True,'tradeOffset':trade_offset,'matched126':matched126},'marketsRequested':len(ids),'marketsCompleted':len(rows),'errors':errors,'summary':summary,'markets':rows,'boundary':'Phase-1 fixed decision audit. Original placement/cancel times are frozen; HftBacktest replaces paper fill logic using public L2 plus Predict historical true match prints.'}


def main()->int:
    p=argparse.ArgumentParser(); p.add_argument('--strategy',choices=['R1','R2','CAP100'],required=True); p.add_argument('--matched126',action='store_true'); p.add_argument('--max-markets',type=int); p.add_argument('--entry-latency-ms',type=int,default=1092); p.add_argument('--response-latency-ms',type=int,default=273); p.add_argument('--queue-model',choices=['risk','log'],default='risk'); p.add_argument('--trade-offset',choices=['early','mid','late'],default='mid'); a=p.parse_args()
    report=run(a.strategy,matched126=a.matched126,max_markets=a.max_markets,entry_latency_ms=a.entry_latency_ms,response_latency_ms=a.response_latency_ms,queue_model=a.queue_model,trade_offset=a.trade_offset)
    OUT_DIR.mkdir(parents=True,exist_ok=True); tag='matched126' if a.matched126 else f'first{a.max_markets}' if a.max_markets else 'full'; out=OUT_DIR/f"{a.strategy.lower()}_fixed_execution_truematch_{a.trade_offset}_{tag}_lat{a.entry_latency_ms}_{a.queue_model}_v0.json"; out.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8'); print(json.dumps({'ok':True,'path':str(out),'summary':report['summary'],'errors':report['errors'][:3]},ensure_ascii=False)); return 0

if __name__=='__main__': raise SystemExit(main())
