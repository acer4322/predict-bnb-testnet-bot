from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'wallet_maker_book_inference.db'
COHORT=ROOT/'data'/'research'/'8784_r2_vs_8786_cap100_fresh_v1_markets.csv'
API='https://api.predict.fun'

def iso_ms(s:str)->int:
    dt=datetime.fromisoformat(s.replace('Z','+00:00'))
    if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp()*1000)

def enc(v:Any)->bytes:
    return zlib.compress(json.dumps(v,separators=(',',':'),sort_keys=True,ensure_ascii=False).encode('utf-8'),6)

def ensure_schema(con:sqlite3.Connection)->None:
    con.executescript('''
    CREATE TABLE IF NOT EXISTS maker_execution_matches_v1 (
      match_key TEXT PRIMARY KEY,
      market_id INTEGER NOT NULL,
      settlement_id TEXT,
      transaction_hash TEXT,
      executed_at TEXT NOT NULL,
      executed_at_ms INTEGER NOT NULL,
      amount_filled TEXT,
      price_executed TEXT,
      raw_json_z BLOB NOT NULL,
      fetched_at_ms INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_execution_matches_market_time
      ON maker_execution_matches_v1(market_id,executed_at_ms);
    ''')
    con.commit()

def fetch(client:httpx.Client, market_id:int)->list[dict[str,Any]]:
    rows=[]; after=None
    for _ in range(100):
        params={'first':500,'marketId':market_id}
        if after: params['after']=after
        r=None
        for attempt in range(8):
            r=client.get(f'{API}/v1/orders/matches',params=params)
            if r.status_code not in {429,500,502,503,504}: break
            retry=r.headers.get('retry-after')
            try: delay=float(retry) if retry else min(20.0,2.0*(attempt+1))
            except Exception: delay=min(20.0,2.0*(attempt+1))
            time.sleep(max(1.0,delay))
        if r is None: raise RuntimeError('no response')
        r.raise_for_status(); j=r.json()
        if j.get('success') is False: raise RuntimeError(str(j.get('error')))
        data=[x for x in (j.get('data') or []) if isinstance(x,dict)]
        rows.extend(data)
        cur=str(j.get('cursor') or '').strip() or None
        if not data or not cur or cur==after: break
        after=cur; time.sleep(.2)
    return rows

def market_ids(limit:int|None)->list[int]:
    import csv
    with COHORT.open(newline='',encoding='utf-8-sig') as f:
        rows=list(csv.DictReader(f))
    ids=[int(r['marketId']) for r in rows[:126]]
    return ids if limit is None else ids[:limit]

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--start',type=int,default=0); ap.add_argument('--count',type=int); ap.add_argument('--market-id',type=int); ap.add_argument('--refresh',action='store_true'); args=ap.parse_args()
    key=str(os.environ.get('PREDICT_FUN_API_KEY') or '').strip()
    if not key: raise RuntimeError('PREDICT_FUN_API_KEY missing')
    ids=[int(args.market_id)] if args.market_id else market_ids(None)[args.start: args.start+args.count if args.count else None]
    con=sqlite3.connect(DB); ensure_schema(con)
    client=httpx.Client(timeout=30,trust_env=False,headers={'x-api-key':key})
    done=0; total_rows=0
    try:
        for m in ids:
            existing=con.execute('select count(*) from maker_execution_matches_v1 where market_id=?',(m,)).fetchone()[0]
            if existing and not args.refresh:
                print(json.dumps({'marketId':m,'cached':existing}),flush=True); done+=1; total_rows+=existing; continue
            if args.refresh:
                con.execute('delete from maker_execution_matches_v1 where market_id=?',(m,)); con.commit()
            rows=fetch(client,m); fetched=int(time.time()*1000); ins=[]
            for raw in rows:
                executed=str(raw.get('executedAt') or '').strip()
                if not executed: continue
                canonical=json.dumps(raw,separators=(',',':'),sort_keys=True,ensure_ascii=False)
                mk=hashlib.sha256(canonical.encode()).hexdigest()
                ins.append((mk,m,str(raw.get('settlementId') or '') or None,str(raw.get('transactionHash') or '') or None,executed,iso_ms(executed),str(raw.get('amountFilled') or '') or None,str(raw.get('priceExecuted') or '') or None,enc(raw),fetched))
            con.executemany('insert or ignore into maker_execution_matches_v1(match_key,market_id,settlement_id,transaction_hash,executed_at,executed_at_ms,amount_filled,price_executed,raw_json_z,fetched_at_ms) values (?,?,?,?,?,?,?,?,?,?)',ins); con.commit()
            print(json.dumps({'marketId':m,'rawMatches':len(ins)}),flush=True); done+=1; total_rows+=len(ins); time.sleep(.25)
    finally:
        client.close(); con.close()
    print(json.dumps({'ok':True,'markets':done,'rawMatches':total_rows}),flush=True)
    return 0
if __name__=='__main__': raise SystemExit(main())
