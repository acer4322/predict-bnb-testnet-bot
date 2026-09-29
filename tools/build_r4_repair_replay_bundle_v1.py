from __future__ import annotations
import argparse, json, shutil, sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
STRATEGY_SRC=ROOT/'data/strategy_target_compare_v1.db'
BOOK_SRC=ROOT/'data/wallet_maker_book_inference.db'
TAPE_SRC=ROOT/'data/execution_tape_v1/markets'
TABLES=['our_decisions','our_orders','our_fills']
BOOK_TABLE='maker_book_inference_updates'

def clone_subset(src:Path,dst:Path,tables:list[str],ids:list[int]):
    if dst.exists(): dst.unlink()
    s=sqlite3.connect(str(src)); d=sqlite3.connect(str(dst))
    try:
        for t in tables:
            row=s.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone()
            if not row: raise RuntimeError(f'missing table {t}')
            d.execute(row[0])
            cols=[r[1] for r in s.execute(f'PRAGMA table_info({t})')]
            q=','.join('?'*len(ids)); c=','.join(cols); ph=','.join('?'*len(cols))
            rows=s.execute(f'SELECT {c} FROM {t} WHERE market_id IN ({q})',ids).fetchall()
            if rows: d.executemany(f'INSERT INTO {t} ({c}) VALUES ({ph})',rows)
        d.commit()
    finally:
        s.close(); d.close()

def clone_book(src:Path,dst:Path,ids:list[int]):
    if dst.exists(): dst.unlink()
    s=sqlite3.connect(str(src)); d=sqlite3.connect(str(dst))
    try:
        row=s.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(BOOK_TABLE,)).fetchone()
        if not row: raise RuntimeError('missing book table')
        d.execute(row[0])
        cols=[r[1] for r in s.execute(f'PRAGMA table_info({BOOK_TABLE})')]
        q=','.join('?'*len(ids)); c=','.join(cols); ph=','.join('?'*len(cols))
        rows=s.execute(f'SELECT {c} FROM {BOOK_TABLE} WHERE market_id IN ({q})',ids).fetchall()
        if rows: d.executemany(f'INSERT INTO {BOOK_TABLE} ({c}) VALUES ({ph})',rows)
        d.commit()
    finally:
        s.close(); d.close()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--ids',required=True); ap.add_argument('--out',required=True)
    a=ap.parse_args(); ids=[int(x) for x in a.ids.split(',') if x.strip()]; out=ROOT/a.out; out.mkdir(parents=True,exist_ok=True)
    clone_subset(STRATEGY_SRC,out/'strategy_shard.db',TABLES,ids)
    clone_book(BOOK_SRC,out/'book_shard.db',ids)
    td=out/'tapes'; td.mkdir(exist_ok=True)
    missing=[]
    for mid in ids:
        src=TAPE_SRC/f'{mid}.json.xz'
        if src.exists(): shutil.copy2(src,td/src.name)
        else: missing.append(mid)
    con=sqlite3.connect(out/'strategy_shard.db'); counts={t:con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in TABLES}; con.close()
    con=sqlite3.connect(out/'book_shard.db'); counts[BOOK_TABLE]=con.execute(f'SELECT COUNT(*) FROM {BOOK_TABLE}').fetchone()[0]; con.close()
    rep={'version':'R4_REPAIR_REPLAY_BUNDLE_V1','marketIds':ids,'marketCount':len(ids),'counts':counts,'missingTapes':missing}
    (out/'manifest.json').write_text(json.dumps(rep,indent=2),encoding='utf-8')
    print(json.dumps(rep,indent=2))
if __name__=='__main__': main()
