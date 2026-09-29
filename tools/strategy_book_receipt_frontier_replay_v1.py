from __future__ import annotations

import hashlib,json,sqlite3,zlib
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
ARCHIVE_DB=ROOT/'data/strategy_input_snapshot_archive_v1.db'
BOOK_DB=ROOT/'data/wallet_maker_book_inference.db'


def _dec(blob:bytes|None)->Any:
    return json.loads(zlib.decompress(blob).decode('utf-8')) if blob else None

def _apply_changes(book:dict[str,dict[float,float]],changes:Any)->None:
    if not isinstance(changes,dict):return
    for key in ('bids','asks'):
        for ch in changes.get(key,[]) or []:
            p=float(ch['price']);after=float(ch['after'])
            if after<=1e-12:book[key].pop(p,None)
            else:book[key][p]=after

def book_hash(book:dict[str,dict[float,float]])->str:
    bids=dict((book or {}).get('bids') or {});asks=dict((book or {}).get('asks') or {})
    canonical={'bids':[[format(float(k),'.10g'),format(float(v),'.12g')] for k,v in sorted(bids.items())],
               'asks':[[format(float(k),'.10g'),format(float(v),'.12g')] for k,v in sorted(asks.items())]}
    return hashlib.sha256(json.dumps(canonical,separators=(',',':'),sort_keys=True).encode('utf-8')).hexdigest()

def load_contexts(market_id:int,controller_version:str,archive_db:Path=ARCHIVE_DB)->dict[int,dict[str,Any]]:
    c=sqlite3.connect(archive_db);c.row_factory=sqlite3.Row
    try:
        rows=c.execute('''select sampled_at_ms,consumed_at_ms,book_source_ms,book_context_at_ms,book_state_hash,book_best_bid,book_best_ask
                          from strategy_input_snapshots_v1
                          where controller_version=? and market_id=? order by consumed_at_ms,id''',(str(controller_version),int(market_id))).fetchall()
    finally:c.close()
    return {int(r['sampled_at_ms']):dict(r) for r in rows}

class ReceiptFrontierBookTailer:
    """Replay the public book exactly as it was visible to the live Strategy Brain.

    The live PublicBookTailer filters by source_timestamp_ms against the consumed snapshot time,
    while the database itself only contained rows physically received by that wall-clock moment.
    Offline replay must therefore impose both frontiers: source_timestamp_ms<=sampled_at_ms and
    received_at_ms<=recorded book_context_at_ms. The recorder hash is checked after each advance.
    """
    def __init__(self,market_id:int,controller_version:str,book_db:Path=BOOK_DB,archive_db:Path=ARCHIVE_DB,strict_hash:bool=True):
        self.path=Path(book_db).resolve();self.db=sqlite3.connect(f'file:{self.path.as_posix()}?mode=ro',uri=True,timeout=10,check_same_thread=False);self.db.row_factory=sqlite3.Row;self.db.execute('pragma query_only=on')
        self.market_id:int|None=None;self.last_id=0;self.last_source_ms:int|None=None;self.book={'bids':{},'asks':{}}
        self.contexts=load_contexts(market_id,controller_version,archive_db);self.strict_hash=bool(strict_hash);self.hash_checks=0;self.hash_matches=0;self.hash_mismatches=[]
    def close(self):self.db.close()
    def _context(self,market_id:int,up_to_ms:int)->dict[str,Any]:
        if int(market_id) not in {int(market_id)}:pass
        ctx=self.contexts.get(int(up_to_ms))
        if ctx is None:raise RuntimeError(f'no recorded book frontier for market={market_id} sampled={up_to_ms}')
        if ctx.get('consumed_at_ms') is None:raise RuntimeError(f'consumed_at_ms missing for market={market_id} sampled={up_to_ms}')
        return ctx
    def _verify(self,market_id:int,up_to_ms:int)->None:
        ctx=self._context(market_id,up_to_ms);expected=ctx.get('book_state_hash')
        if not expected:return
        got=book_hash(self.book);self.hash_checks+=1
        if got==str(expected):self.hash_matches+=1;return
        rec={'marketId':int(market_id),'sampledAtMs':int(up_to_ms),'expected':str(expected),'got':got,'expectedSourceMs':ctx.get('book_source_ms'),'replayedSourceMs':self.last_source_ms}
        self.hash_mismatches.append(rec)
        if self.strict_hash:raise RuntimeError(f'receipt-frontier book hash mismatch: {rec}')
    def reset(self,market_id:int,up_to_ms:int)->bool:
        ctx=self._context(market_id,up_to_ms);wall=int(ctx['consumed_at_ms']);source_max=int(ctx.get('book_source_ms') or up_to_ms)
        self.market_id=int(market_id);self.last_id=0;self.last_source_ms=None;self.book={'bids':{},'asks':{}}
        row=self.db.execute('''select id,source_timestamp_ms,native_bids_z,native_asks_z
                               from maker_book_inference_updates
                               where market_id=? and is_checkpoint=1 and source_timestamp_ms<=? and received_at_ms<=?
                               order by id desc limit 1''',(int(market_id),source_max,wall)).fetchone()
        if row is None:return False
        self.book={'bids':{float(k):float(v) for k,v in (_dec(row['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (_dec(row['native_asks_z']) or {}).items()}}
        self.last_id=int(row['id']);self.last_source_ms=int(row['source_timestamp_ms']);return True
    def advance(self,market_id:int,up_to_ms:int,on_changes:Any=None)->bool:
        ctx=self._context(market_id,up_to_ms);wall=int(ctx['consumed_at_ms']);source_max=int(ctx.get('book_source_ms') or up_to_ms)
        if self.market_id!=int(market_id) or not self.book['bids'] or not self.book['asks']:
            if not self.reset(market_id,up_to_ms):return False
        rows=self.db.execute('''select id,source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z
                                from maker_book_inference_updates
                                where market_id=? and id>? and source_timestamp_ms<=? and received_at_ms<=?
                                order by id''',(int(market_id),int(self.last_id),source_max,wall)).fetchall()
        for row in rows:
            if int(row['is_checkpoint']):
                self.book={'bids':{float(k):float(v) for k,v in (_dec(row['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (_dec(row['native_asks_z']) or {}).items()}}
            else:
                changes=_dec(row['changes_z']) or {}
                if on_changes is not None:on_changes(changes,int(row['source_timestamp_ms']))
                _apply_changes(self.book,changes)
            self.last_id=int(row['id']);self.last_source_ms=int(row['source_timestamp_ms'])
        ok=bool(self.book['bids'] and self.book['asks'])
        if ok:self._verify(market_id,up_to_ms)
        return ok
    def quality(self)->dict[str,Any]:
        return {'hashChecks':self.hash_checks,'hashMatches':self.hash_matches,'hashMismatchCount':len(self.hash_mismatches),'hashMatchRate':self.hash_matches/self.hash_checks if self.hash_checks else None,'mismatches':self.hash_mismatches[:10]}
