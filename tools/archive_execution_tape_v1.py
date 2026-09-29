from __future__ import annotations

import argparse
import base64
import json
import lzma
import sqlite3
import zlib
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'wallet_maker_book_inference.db'
OUT=ROOT/'data'/'execution_tape_v1'/'markets'
VERSION='PREDICT_EXECUTION_TAPE_ARCHIVE_V1'

def dec(blob:bytes|None)->Any:
    return json.loads(zlib.decompress(blob).decode('utf-8')) if blob else None

def compact_changes(v:Any)->dict[str,list[list[float]]]:
    out={'bids':[],'asks':[]}
    if not isinstance(v,dict): return out
    for side in ('bids','asks'):
        rows=v.get(side) if isinstance(v.get(side),list) else []
        for r in rows:
            if not isinstance(r,dict): continue
            out[side].append([float(r.get('price',0)),float(r.get('before',0)),float(r.get('after',0)),float(r.get('delta',0))])
    return out

def archive_market(con:sqlite3.Connection, market_id:int, *, overwrite:bool=False)->dict[str,Any]:
    OUT.mkdir(parents=True,exist_ok=True)
    path=OUT/f'{market_id}.json.xz'
    if path.exists() and not overwrite:
        return {'marketId':market_id,'cached':True,'bytes':path.stat().st_size,'path':str(path)}
    con.row_factory=sqlite3.Row
    u=con.execute('select source_timestamp_ms,received_at_ms,order_count,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(market_id,)).fetchall()
    if not u: raise RuntimeError(f'no L2 rows for {market_id}')
    tables={r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    meta=[]
    if 'maker_execution_orderbook_meta_v1' in tables:
        for r in con.execute('select source_timestamp_ms,received_at_ms,order_count,last_order_settled_z,settlements_pending_z from maker_execution_orderbook_meta_v1 where market_id=? order by source_timestamp_ms,received_at_ms,id',(market_id,)):
            meta.append([int(r[0]),int(r[1]),int(r[2]),dec(r[3]),dec(r[4])])
    matches=[]
    if 'maker_execution_matches_v1' in tables:
        for r in con.execute('select raw_json_z from maker_execution_matches_v1 where market_id=? order by executed_at_ms,match_key',(market_id,)):
            matches.append(dec(r[0]))
    market_row=None
    if 'maker_book_inference_markets' in tables:
        rr=con.execute('select * from maker_book_inference_markets where market_id=?',(market_id,)).fetchone()
        market_row=dict(rr) if rr else None
    updates=[]
    for r in u:
        updates.append([
            int(r['source_timestamp_ms']),int(r['received_at_ms']),int(r['order_count']),int(r['is_checkpoint']),
            dec(r['native_bids_z']),dec(r['native_asks_z']),compact_changes(dec(r['changes_z']))
        ])
    payload={'version':VERSION,'marketId':market_id,'market':market_row,'schema':{
        'updates':'[sourceMs,receivedMs,orderCount,isCheckpoint,bids?,asks?,changes]','executionMeta':'[sourceMs,receivedMs,orderCount,lastOrderSettled,settlementsPending]','matches':'raw Predict /v1/orders/matches payloads'},
        'updates':updates,'executionMeta':meta,'matches':matches}
    raw=json.dumps(payload,separators=(',',':'),ensure_ascii=False,sort_keys=False).encode('utf-8')
    data=lzma.compress(raw,preset=3)
    tmp=path.with_suffix(path.suffix+'.tmp'); tmp.write_bytes(data); tmp.replace(path)
    return {'marketId':market_id,'rows':len(updates),'metaRows':len(meta),'matches':len(matches),'rawBytes':len(raw),'archiveBytes':len(data),'path':str(path)}

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--market-id',type=int,action='append'); ap.add_argument('--all',action='store_true'); ap.add_argument('--overwrite',action='store_true'); ap.add_argument('--limit',type=int); args=ap.parse_args()
    con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
    try:
        if args.market_id: ids=args.market_id
        elif args.all: ids=[int(r[0]) for r in con.execute('select distinct market_id from maker_book_inference_updates order by market_id')]
        else: raise SystemExit('use --market-id or --all')
        if args.limit: ids=ids[:args.limit]
        total=0
        for m in ids:
            x=archive_market(con,m,overwrite=args.overwrite); total+=int(x.get('archiveBytes') or x.get('bytes') or 0); print(json.dumps(x,ensure_ascii=False),flush=True)
        print(json.dumps({'ok':True,'markets':len(ids),'archiveBytes':total,'outDir':str(OUT)},ensure_ascii=False))
    finally: con.close()
    return 0
if __name__=='__main__': raise SystemExit(main())
