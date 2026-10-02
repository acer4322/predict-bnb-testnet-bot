from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import hftbacktest_execution_shift_audit_v0 as ex  # noqa: E402

OUT_DIR = ROOT / 'data' / 'research' / 'hftbacktest_execution_shift_v0'
CACHE_DIR = OUT_DIR / 'predict_match_cache_v0'


def iso_ms(s: str) -> int:
    dt = datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def wei(v: Any) -> float:
    return float(int(str(v))) / 1e18


def normalize_match(row: dict[str, Any]) -> dict[str, Any] | None:
    taker = row.get('taker') if isinstance(row.get('taker'), dict) else {}
    outcome = taker.get('outcome') if isinstance(taker.get('outcome'), dict) else {}
    name = str(outcome.get('name') or '').strip().upper()
    quote = str(taker.get('quoteType') or '').strip().upper()
    if name not in {'UP','DOWN','YES','NO'} or quote not in {'BID','ASK'}:
        return None
    px = wei(row.get('priceExecuted'))
    qty = wei(row.get('amountFilled'))
    if qty <= 0 or not 0 < px < 1:
        return None
    is_up = name in {'UP','YES'}
    native_px = px if is_up else 1.0 - px
    if is_up:
        aggressor = 'BUY' if quote == 'BID' else 'SELL'
    else:
        aggressor = 'SELL' if quote == 'BID' else 'BUY'
    return {
        'executedAt': row.get('executedAt'),
        'tsMs': iso_ms(row.get('executedAt')),
        'qty': qty,
        'outcome': name,
        'quoteType': quote,
        'outcomePrice': px,
        'nativeYesPrice': round(native_px, 12),
        'nativeAggressor': aggressor,
        'transactionHash': row.get('transactionHash'),
        'makerCount': len(row.get('makers') or []),
    }


def fetch_matches(market_id: int, *, refresh: bool=False) -> list[dict[str, Any]]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f'{market_id}.json'
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding='utf-8')).get('trades', [])
    key = str(os.environ.get('PREDICT_FUN_API_KEY') or '').strip()
    if not key:
        raise RuntimeError('PREDICT_FUN_API_KEY missing')
    rows: list[dict[str, Any]] = []
    after: str | None = None
    with httpx.Client(timeout=30, trust_env=False, headers={'x-api-key': key}) as c:
        for page in range(100):
            params: dict[str, Any] = {'first': 100, 'marketId': int(market_id)}
            if after:
                params['after'] = after
            r = None
            for attempt in range(8):
                r = c.get('https://api.predict.fun/v1/orders/matches', params=params)
                if r.status_code not in {429,500,502,503,504}:
                    break
                retry = r.headers.get('retry-after')
                try:
                    delay = float(retry) if retry is not None else min(20.0, 2.0 * (attempt + 1))
                except Exception:
                    delay = min(20.0, 2.0 * (attempt + 1))
                time.sleep(max(1.0, delay))
            if r is None:
                raise RuntimeError('matches request produced no response')
            r.raise_for_status()
            j = r.json()
            if j.get('success') is False:
                raise RuntimeError(f'matches rejected: {j.get("error")}')
            data = [x for x in (j.get('data') or []) if isinstance(x, dict)]
            for x in data:
                n = normalize_match(x)
                if n is not None:
                    rows.append(n)
            cur = str(j.get('cursor') or '').strip() or None
            if not data or not cur or cur == after:
                break
            after = cur
            time.sleep(0.20)
    rows.sort(key=lambda x: (int(x['tsMs']), str(x.get('transactionHash') or ''), float(x['nativeYesPrice']), float(x['qty'])))
    path.write_text(json.dumps({'version':'PREDICT_MATCH_CACHE_V0','marketId':market_id,'trades':rows}, indent=2, ensure_ascii=False), encoding='utf-8')
    return rows


def depth_plus_true_trades(market_id: int, *, trade_offset: str) -> tuple[np.ndarray,list[int],dict[str,Any]]:
    import sqlite3
    con=sqlite3.connect(ex.BOOK_DB); con.row_factory=sqlite3.Row
    try:
        depth, update_times, meta = ex.build_market_events(con, market_id, depletion_as_trade=False)
    finally:
        con.close()
    trades=fetch_matches(market_id)
    grouped: dict[int,list[dict[str,Any]]] = defaultdict(list)
    for t in trades:
        grouped[int(t['tsMs'])].append(t)
    extra=[]
    for ts_ms, vals in grouped.items():
        # REST historical matches are second-granular. Spread equal-second prints by nanoseconds,
        # preserving the exact second without pretending millisecond precision.
        n=len(vals)
        for i,t in enumerate(vals):
            row=ex.event_row(ex.TRADE_EVENT | (ex.BUY_EVENT if t['nativeAggressor']=='BUY' else ex.SELL_EVENT), ts_ms, float(t['nativeYesPrice']), float(t['qty']))
            if trade_offset == 'late':
                offset_ns = 999_000_000 + min(i,999)*1000
            elif trade_offset == 'mid':
                offset_ns = 500_000_000 + min(i,999)*1000
            else:
                offset_ns = min(i,999)*1000
            # executedAt timestamps are whole seconds; ts_ms is on second boundary.
            row['exch_ts'] += offset_ns
            row['local_ts'] += offset_ns
            extra.append(row)
    arr=np.concatenate([depth, np.asarray(extra,dtype=ex.event_dtype)]) if extra else depth
    arr.sort(order=['local_ts','exch_ts'])
    meta=dict(meta)
    meta.update({'trueMatchTrades':len(trades),'trueMatchQty':sum(float(t['qty']) for t in trades),'trueMatchTimestampPrecision':'REST executedAt is second-granular','trueMatchOffsetPolicy':trade_offset})
    return arr, sorted(set(update_times + [int(t['tsMs']) for t in trades])), meta


def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument('--market-id',type=int,default=1513668)
    p.add_argument('--entry-latency-ms',type=int,default=1092)
    p.add_argument('--response-latency-ms',type=int,default=273)
    p.add_argument('--queue-model',choices=['risk','log'],default='risk')
    p.add_argument('--trade-offset',choices=['early','mid','late'],default='mid')
    p.add_argument('--refresh',action='store_true')
    args=p.parse_args()
    if args.refresh:
        fetch_matches(args.market_id, refresh=True)
    events, update_times, meta=depth_plus_true_trades(args.market_id, trade_offset=args.trade_offset)
    original=ex.build_market_events
    def patched(_book, market_id:int, *, depletion_as_trade:bool):
        if int(market_id)!=int(args.market_id):
            return original(_book,market_id,depletion_as_trade=depletion_as_trade)
        return events, update_times, meta
    ex.build_market_events=patched
    try:
        if args.market_id != 1513668:
            raise RuntimeError('V0 calibration currently compares Echtgeld truth only for market 1513668')
        report=ex.live_calibration_1513668(entry_latency_ms=args.entry_latency_ms,response_latency_ms=args.response_latency_ms,queue_model=args.queue_model,depletion_as_trade=False)
    finally:
        ex.build_market_events=original
    report['trueMatchFeed']=meta
    OUT_DIR.mkdir(parents=True,exist_ok=True)
    out=OUT_DIR/f'cap100_1513668_hft_calibration_true_matches_{args.trade_offset}_{args.queue_model}_lat{args.entry_latency_ms}_resp{args.response_latency_ms}_v0.json'
    out.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8')
    print(json.dumps({'ok':True,'path':str(out),'summary':report['summary'],'feed':meta},ensure_ascii=False))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
