from __future__ import annotations
import json, shutil, sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
IDS_PATH=P/'r4_adaptive_cycle_hft_formal20_ids_v1.json'
SRC_BUNDLE=P/'r4_target_sequence_v13_runtime20_worker_bundle'
OUT=P/'r4_adaptive_cycle_hft_formal20_worker_bundle_v1'
STRAT=ROOT/'data/strategy_target_compare_v1.db'
BOOK=ROOT/'data/wallet_maker_book_inference.db'
TAPE=ROOT/'data/execution_tape_v1/markets'
VERSION='UNIFIED_PROMOTED_OWNSTATE_V4_R2_RESIDUAL_FORWARD_PAPER'

def copy_table_subset(src_db:Path,dst_db:Path,table:str,where:str,args:list):
    if dst_db.exists(): dst_db.unlink()
    src=sqlite3.connect(src_db); dst=sqlite3.connect(dst_db)
    try:
        row=src.execute("select sql from sqlite_master where type='table' and name=?",(table,)).fetchone()
        if not row: raise RuntimeError(f'missing table {table}')
        dst.execute(row[0])
        cols=[r[1] for r in src.execute(f'pragma table_info({table})')]
        qs=','.join('?' for _ in args)
        sql=f"select {','.join(cols)} from {table} where {where}"
        rows=src.execute(sql,args).fetchall()
        dst.executemany(f"insert into {table} ({','.join(cols)}) values ({','.join('?' for _ in cols)})",rows)
        dst.commit()
        return len(rows)
    finally:
        src.close(); dst.close()

def main():
    ids=list(map(int,json.loads(IDS_PATH.read_text(encoding='utf-8'))))
    if len(ids)!=20 or len(set(ids))!=20: raise RuntimeError('formal20 IDs must be 20 unique markets')
    if OUT.exists(): shutil.rmtree(OUT)
    (OUT/'execution_tape_v1/markets').mkdir(parents=True)
    for fn in ['audit_r4_target_sequence_v13_wholemarket_shadow.py','r4_state_shaping_transport_support_gate_v1.joblib','r4_target_sequence_hazard_v1.pt','r4_target_sequence_teacher_v11_factorized.pt']:
        shutil.copy2(SRC_BUNDLE/fn,OUT/fn)
    (OUT/'validation20_ids.json').write_text(json.dumps(ids,indent=2),encoding='utf-8')
    (OUT/'chunk0_ids.json').write_text(json.dumps(ids[:10],indent=2),encoding='utf-8')
    (OUT/'chunk1_ids.json').write_text(json.dumps(ids[10:],indent=2),encoding='utf-8')
    # window ends come from frozen strategy public-state metadata; no outcome fields are read.
    con=sqlite3.connect(STRAT); wm={}; counts={}
    try:
        for mid in ids:
            rows=con.execute('select public_state_json from our_decisions where strategy_version=? and market_id=? order by decision_ms',(VERSION,mid)).fetchall()
            if not rows: raise RuntimeError(f'no strategy rows for {mid}')
            vals=[]
            for (s,) in rows:
                try:
                    v=int(json.loads(s).get('windowEndMs') or 0)
                    if v: vals.append(v)
                except Exception: pass
            if not vals: raise RuntimeError(f'no windowEndMs for {mid}')
            # Require a unique market end; this detects mixed/corrupt rows.
            u=sorted(set(vals))
            if len(u)!=1: raise RuntimeError(f'non-unique windowEndMs {mid}: {u[:5]}')
            wm[str(mid)]=u[0]; counts[str(mid)]={'decisionRows':len(rows)}
    finally: con.close()
    (OUT/'window_end_map.json').write_text(json.dumps(wm,indent=2,sort_keys=True),encoding='utf-8')
    # Strategy compact DB: preserve source schemas exactly, then copy only the frozen version + formal IDs.
    ssrc=sqlite3.connect(STRAT); sdst=sqlite3.connect(OUT/'strategy_compact.db')
    try:
        for table in ['our_decisions','our_orders','our_fills']:
            schema=ssrc.execute("select sql from sqlite_master where type='table' and name=?",(table,)).fetchone()[0]
            sdst.execute(schema)
            cols=[r[1] for r in ssrc.execute(f'pragma table_info({table})')]
            ph=','.join('?' for _ in ids)
            rows=ssrc.execute(f"select {','.join(cols)} from {table} where strategy_version=? and market_id in ({ph})",[VERSION,*ids]).fetchall()
            sdst.executemany(f"insert into {table} ({','.join(cols)}) values ({','.join('?' for _ in cols)})",rows)
            counts['strategy_'+table]=len(rows)
        sdst.execute('create index idx_decisions_market_time on our_decisions(market_id,decision_ms)')
        sdst.execute('create index idx_orders_market_time on our_orders(market_id,placed_at_ms)')
        sdst.execute('create index idx_fills_market_time on our_fills(market_id,filled_at_ms)')
        sdst.commit()
    finally: ssrc.close(); sdst.close()
    # Public book compact DB.
    bsrc=sqlite3.connect(BOOK); bdst=sqlite3.connect(OUT/'book_compact.db')
    try:
        table='maker_book_inference_updates'
        schema=bsrc.execute("select sql from sqlite_master where type='table' and name=?",(table,)).fetchone()[0]
        bdst.execute(schema)
        cols=[r[1] for r in bsrc.execute(f'pragma table_info({table})')]
        ph=','.join('?' for _ in ids)
        rows=bsrc.execute(f"select {','.join(cols)} from {table} where market_id in ({ph}) order by market_id,source_timestamp_ms",ids).fetchall()
        bdst.executemany(f"insert into {table} ({','.join(cols)}) values ({','.join('?' for _ in cols)})",rows)
        bdst.execute('create index idx_maker_book_updates_market_time on maker_book_inference_updates(market_id,source_timestamp_ms)')
        bdst.commit(); counts['bookRows']=len(rows)
    finally: bsrc.close(); bdst.close()
    missing=[]; tape_bytes=0
    for mid in ids:
        src=TAPE/f'{mid}.json.xz'; dst=OUT/'execution_tape_v1/markets'/src.name
        if not src.exists(): missing.append(mid); continue
        shutil.copy2(src,dst); tape_bytes+=dst.stat().st_size
    if missing: raise RuntimeError(f'missing tapes: {missing}')
    # Verify all IDs represented in all required sources.
    con=sqlite3.connect(OUT/'strategy_compact.db')
    dset={r[0] for r in con.execute('select distinct market_id from our_decisions')}; con.close()
    con=sqlite3.connect(OUT/'book_compact.db')
    bset={r[0] for r in con.execute('select distinct market_id from maker_book_inference_updates')}; con.close()
    missing_dec=[x for x in ids if x not in dset]; missing_book=[x for x in ids if x not in bset]
    if missing_dec or missing_book: raise RuntimeError(f'missing_dec={missing_dec} missing_book={missing_book}')
    manifest={'version':'R4_ADAPTIVE_CYCLE_HFT_FORMAL20_WORKER_BUNDLE_V1','strategyVersion':VERSION,'marketIds':ids,'windowEndMap':wm,'counts':counts,'tapeBytes':tape_bytes,'outcomeBlindPackaging':True}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(manifest,indent=2))
if __name__=='__main__': main()
