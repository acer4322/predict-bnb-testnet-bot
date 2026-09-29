from __future__ import annotations
import argparse,json,lzma,sqlite3,zlib
from pathlib import Path

def dec(b): return json.loads(zlib.decompress(b).decode('utf-8')) if b else None

def compact_changes(v):
    out={'bids':[],'asks':[]}
    if not isinstance(v,dict): return out
    for side in ('bids','asks'):
        for r in (v.get(side) or []):
            if isinstance(r,dict): out[side].append([float(r.get('price',0)),float(r.get('before',0)),float(r.get('after',0)),float(r.get('delta',0))])
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',required=True); ap.add_argument('--market-id',type=int,required=True); ap.add_argument('--out',required=True); a=ap.parse_args()
    con=sqlite3.connect(f'file:{Path(a.db).resolve()}?mode=ro',uri=True); con.row_factory=sqlite3.Row
    mid=a.market_id
    u=con.execute('select source_timestamp_ms,received_at_ms,order_count,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(mid,)).fetchall()
    if not u: raise SystemExit('no updates')
    meta=[[int(r[0]),int(r[1]),int(r[2]),dec(r[3]),dec(r[4])] for r in con.execute('select source_timestamp_ms,received_at_ms,order_count,last_order_settled_z,settlements_pending_z from maker_execution_orderbook_meta_v1 where market_id=? order by source_timestamp_ms,received_at_ms,id',(mid,))]
    matches=[dec(r[0]) for r in con.execute('select raw_json_z from maker_execution_matches_v1 where market_id=? order by executed_at_ms,match_key',(mid,))]
    mr=con.execute('select * from maker_book_inference_markets where market_id=?',(mid,)).fetchone(); market=dict(mr) if mr else None
    updates=[[int(r['source_timestamp_ms']),int(r['received_at_ms']),int(r['order_count']),int(r['is_checkpoint']),dec(r['native_bids_z']),dec(r['native_asks_z']),compact_changes(dec(r['changes_z']))] for r in u]
    payload={'version':'PREDICT_EXECUTION_TAPE_ARCHIVE_V1_READONLY_ASSET','marketId':mid,'market':market,'schema':{'updates':'[sourceMs,receivedMs,orderCount,isCheckpoint,bids?,asks?,changes]','executionMeta':'[sourceMs,receivedMs,orderCount,lastOrderSettled,settlementsPending]','matches':'raw Predict /v1/orders/matches payloads'},'updates':updates,'executionMeta':meta,'matches':matches}
    out=Path(a.out); out.parent.mkdir(parents=True,exist_ok=True); out.write_bytes(lzma.compress(json.dumps(payload,separators=(',',':'),ensure_ascii=False).encode(),preset=3))
    print(json.dumps({'ok':True,'marketId':mid,'updates':len(updates),'meta':len(meta),'matches':len(matches),'out':str(out),'bytes':out.stat().st_size}))
if __name__=='__main__': main()
