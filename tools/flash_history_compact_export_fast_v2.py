from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'data' / 'strategy_target_flash_v1.db'
DST = ROOT / 'data' / 'research' / 'flash_sandbox_history_compact_v2.db'

SCHEMA = '''
CREATE TABLE IF NOT EXISTS flash_decisions_compact_v2(
 decision_id TEXT,strategy_version TEXT NOT NULL,market_id INTEGER NOT NULL,
 decision_ms INTEGER NOT NULL,source_snapshot_ms INTEGER,seconds_left REAL,phase TEXT,
 desired_portfolio_action TEXT NOT NULL,execution_choice TEXT NOT NULL,side TEXT,size REAL,
 primary_reason TEXT NOT NULL,created_at_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS flash_orders_compact_v2(
 order_id TEXT,strategy_version TEXT NOT NULL,market_id INTEGER NOT NULL,
 placement_decision_id TEXT,channel TEXT NOT NULL,side TEXT NOT NULL,quote_type TEXT NOT NULL,
 price REAL NOT NULL,shares REAL NOT NULL,placed_at_ms INTEGER NOT NULL,status TEXT NOT NULL,
 filled_at_ms INTEGER,fill_price REAL,cancelled_at_ms INTEGER,cancel_reason TEXT,updated_at_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS flash_fills_compact_v2(
 fill_id TEXT,strategy_version TEXT NOT NULL,market_id INTEGER NOT NULL,
 decision_id TEXT,order_id TEXT,channel TEXT NOT NULL,purpose TEXT,side TEXT NOT NULL,
 quote_type TEXT NOT NULL,price REAL NOT NULL,shares REAL NOT NULL,filled_at_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS flash_market_compact_summary_v2(
 strategy_version TEXT NOT NULL,market_id INTEGER NOT NULL,first_decision_ms INTEGER,last_decision_ms INTEGER,
 decision_count INTEGER NOT NULL,order_count INTEGER NOT NULL,fill_count INTEGER NOT NULL,
 cancelled_order_count INTEGER NOT NULL,up_shares REAL NOT NULL,down_shares REAL NOT NULL,
 total_cost_usdt REAL NOT NULL,settle_up_pnl_usdt REAL NOT NULL,settle_down_pnl_usdt REAL NOT NULL,
 archived_at_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS export_progress_v2(
 table_name TEXT PRIMARY KEY,max_source_rowid INTEGER NOT NULL,rows_exported INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS export_meta_v2(key TEXT PRIMARY KEY,value TEXT NOT NULL);
'''

TABLES = {
 'decisions': ('our_decisions','flash_decisions_compact_v2',
  'decision_id,strategy_version,market_id,decision_ms,source_snapshot_ms,seconds_left,phase,desired_portfolio_action,execution_choice,side,size,primary_reason,created_at_ms'),
 'orders': ('our_orders','flash_orders_compact_v2',
  'order_id,strategy_version,market_id,placement_decision_id,channel,side,quote_type,price,shares,placed_at_ms,status,filled_at_ms,fill_price,cancelled_at_ms,cancel_reason,updated_at_ms'),
 'fills': ('our_fills','flash_fills_compact_v2',
  'fill_id,strategy_version,market_id,decision_id,order_id,channel,purpose,side,quote_type,price,shares,filled_at_ms'),
}


def init(dst: sqlite3.Connection) -> None:
    dst.executescript(SCHEMA)
    dst.commit()


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--table',choices=['decisions','orders','fills','summary','meta'],required=True)
    ap.add_argument('--rowid-start',type=int,default=0)
    ap.add_argument('--rowid-end',type=int,default=2**63-1)
    args=ap.parse_args()
    DST.parent.mkdir(parents=True,exist_ok=True)
    src=sqlite3.connect(f"file:{SRC.resolve().as_posix()}?mode=ro",uri=True,timeout=30)
    dst=sqlite3.connect(DST,timeout=30)
    dst.execute('PRAGMA journal_mode=WAL')
    dst.execute('PRAGMA synchronous=NORMAL')
    init(dst)
    if args.table in TABLES:
        st,dtbl,cols=TABLES[args.table]
        lo,hi=args.rowid_start,args.rowid_end
        before=dst.execute(f'SELECT COUNT(*) FROM {dtbl}').fetchone()[0]
        # Stream source rows in batches so the destination transaction stays bounded.
        q=f'SELECT rowid,{cols} FROM {st} WHERE rowid BETWEEN ? AND ? ORDER BY rowid'
        cur=src.execute(q,(lo,hi))
        placeholders=','.join('?' for _ in cols.split(','))
        insert=f'INSERT INTO {dtbl}({cols}) VALUES({placeholders})'
        added=0; maxrid=0
        while True:
            rows=cur.fetchmany(20000)
            if not rows: break
            maxrid=max(maxrid,int(rows[-1][0]))
            dst.executemany(insert,[tuple(r[1:]) for r in rows])
            dst.commit(); added+=len(rows)
        prior=dst.execute('SELECT max_source_rowid,rows_exported FROM export_progress_v2 WHERE table_name=?',(args.table,)).fetchone()
        total=(int(prior[1]) if prior else 0)+added
        dst.execute('INSERT OR REPLACE INTO export_progress_v2 VALUES(?,?,?)',(args.table,max(maxrid,hi if added==0 else maxrid),total)); dst.commit()
        after=dst.execute(f'SELECT COUNT(*) FROM {dtbl}').fetchone()[0]
        print({'table':args.table,'start':lo,'end':hi,'before':before,'after':after,'added':added,'max_source_rowid':maxrid})
    elif args.table=='summary':
        try:
            rows=src.execute('SELECT * FROM flash_market_compact_summary_v1').fetchall()
        except sqlite3.Error:
            rows=[]
        dst.executemany('INSERT INTO flash_market_compact_summary_v2 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',rows)
        dst.commit(); print({'table':'summary','added':len(rows)})
    else:
        rows=src.execute('SELECT key,value FROM compare_meta').fetchall()
        dst.executemany('INSERT OR REPLACE INTO export_meta_v2 VALUES(?,?)',rows); dst.commit(); print({'table':'meta','added':len(rows)})
    src.close(); dst.close(); return 0

if __name__=='__main__': raise SystemExit(main())
