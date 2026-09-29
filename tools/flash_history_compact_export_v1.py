from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'data' / 'strategy_target_flash_v1.db'
DST = ROOT / 'data' / 'research' / 'flash_sandbox_history_compact_v1.db'

SCHEMA = '''
CREATE TABLE IF NOT EXISTS flash_decisions_compact_v1(
 decision_id TEXT PRIMARY KEY,strategy_version TEXT NOT NULL,market_id INTEGER NOT NULL,
 decision_ms INTEGER NOT NULL,source_snapshot_ms INTEGER,seconds_left REAL,phase TEXT,
 desired_portfolio_action TEXT NOT NULL,execution_choice TEXT NOT NULL,side TEXT,size REAL,
 primary_reason TEXT NOT NULL,created_at_ms INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_fh_dec_market_time ON flash_decisions_compact_v1(market_id,decision_ms);
CREATE INDEX IF NOT EXISTS idx_fh_dec_version_time ON flash_decisions_compact_v1(strategy_version,decision_ms);
CREATE TABLE IF NOT EXISTS flash_orders_compact_v1(
 order_id TEXT PRIMARY KEY,strategy_version TEXT NOT NULL,market_id INTEGER NOT NULL,
 placement_decision_id TEXT,channel TEXT NOT NULL,side TEXT NOT NULL,quote_type TEXT NOT NULL,
 price REAL NOT NULL,shares REAL NOT NULL,placed_at_ms INTEGER NOT NULL,status TEXT NOT NULL,
 filled_at_ms INTEGER,fill_price REAL,cancelled_at_ms INTEGER,cancel_reason TEXT,updated_at_ms INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_fh_ord_market_time ON flash_orders_compact_v1(market_id,placed_at_ms);
CREATE INDEX IF NOT EXISTS idx_fh_ord_version_time ON flash_orders_compact_v1(strategy_version,placed_at_ms);
CREATE TABLE IF NOT EXISTS flash_fills_compact_v1(
 fill_id TEXT PRIMARY KEY,strategy_version TEXT NOT NULL,market_id INTEGER NOT NULL,
 decision_id TEXT,order_id TEXT,channel TEXT NOT NULL,purpose TEXT,side TEXT NOT NULL,
 quote_type TEXT NOT NULL,price REAL NOT NULL,shares REAL NOT NULL,filled_at_ms INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_fh_fill_market_time ON flash_fills_compact_v1(market_id,filled_at_ms);
CREATE INDEX IF NOT EXISTS idx_fh_fill_version_time ON flash_fills_compact_v1(strategy_version,filled_at_ms);
CREATE TABLE IF NOT EXISTS flash_market_compact_summary_v1(
 strategy_version TEXT NOT NULL,market_id INTEGER NOT NULL,first_decision_ms INTEGER,last_decision_ms INTEGER,
 decision_count INTEGER NOT NULL,order_count INTEGER NOT NULL,fill_count INTEGER NOT NULL,
 cancelled_order_count INTEGER NOT NULL,up_shares REAL NOT NULL,down_shares REAL NOT NULL,
 total_cost_usdt REAL NOT NULL,settle_up_pnl_usdt REAL NOT NULL,settle_down_pnl_usdt REAL NOT NULL,
 archived_at_ms INTEGER NOT NULL,PRIMARY KEY(strategy_version,market_id));
CREATE TABLE IF NOT EXISTS export_meta_v1(key TEXT PRIMARY KEY,value TEXT NOT NULL);
'''


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--table',choices=['decisions','orders','fills','summary','meta'],required=True)
    ap.add_argument('--rowid-start',type=int,default=0)
    ap.add_argument('--rowid-end',type=int,default=2**63-1)
    args=ap.parse_args()
    DST.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(SRC,timeout=30)
    db.execute(f"ATTACH DATABASE '{DST.as_posix()}' AS hist")
    # Create the compact schema only in the attached archive DB.
    for stmt in [x.strip() for x in SCHEMA.split(';') if x.strip()]:
        if stmt.startswith('CREATE TABLE IF NOT EXISTS '):
            stmt=stmt.replace('CREATE TABLE IF NOT EXISTS ','CREATE TABLE IF NOT EXISTS hist.',1)
        elif stmt.startswith('CREATE INDEX IF NOT EXISTS '):
            parts=stmt.split(' ON ',1)
            parts[0]=parts[0].replace('CREATE INDEX IF NOT EXISTS ','CREATE INDEX IF NOT EXISTS hist.',1)
            stmt=' ON '.join(parts)
        db.execute(stmt)
    lo,hi=args.rowid_start,args.rowid_end
    if args.table=='decisions':
        sql='''INSERT OR IGNORE INTO hist.flash_decisions_compact_v1
        SELECT decision_id,strategy_version,market_id,decision_ms,source_snapshot_ms,seconds_left,phase,
               desired_portfolio_action,execution_choice,side,size,primary_reason,created_at_ms
        FROM main.our_decisions WHERE rowid BETWEEN ? AND ?'''
        before=db.execute('SELECT count(*) FROM hist.flash_decisions_compact_v1').fetchone()[0]
        db.execute(sql,(lo,hi)); db.commit()
        after=db.execute('SELECT count(*) FROM hist.flash_decisions_compact_v1').fetchone()[0]
    elif args.table=='orders':
        sql='''INSERT OR IGNORE INTO hist.flash_orders_compact_v1
        SELECT order_id,strategy_version,market_id,placement_decision_id,channel,side,quote_type,price,shares,
               placed_at_ms,status,filled_at_ms,fill_price,cancelled_at_ms,cancel_reason,updated_at_ms
        FROM main.our_orders WHERE rowid BETWEEN ? AND ?'''
        before=db.execute('SELECT count(*) FROM hist.flash_orders_compact_v1').fetchone()[0]
        db.execute(sql,(lo,hi)); db.commit(); after=db.execute('SELECT count(*) FROM hist.flash_orders_compact_v1').fetchone()[0]
    elif args.table=='fills':
        sql='''INSERT OR IGNORE INTO hist.flash_fills_compact_v1
        SELECT fill_id,strategy_version,market_id,decision_id,order_id,channel,purpose,side,quote_type,price,shares,filled_at_ms
        FROM main.our_fills WHERE rowid BETWEEN ? AND ?'''
        before=db.execute('SELECT count(*) FROM hist.flash_fills_compact_v1').fetchone()[0]
        db.execute(sql,(lo,hi)); db.commit(); after=db.execute('SELECT count(*) FROM hist.flash_fills_compact_v1').fetchone()[0]
    elif args.table=='summary':
        before=db.execute('SELECT count(*) FROM hist.flash_market_compact_summary_v1').fetchone()[0]
        db.execute('INSERT OR REPLACE INTO hist.flash_market_compact_summary_v1 SELECT * FROM main.flash_market_compact_summary_v1')
        db.commit(); after=db.execute('SELECT count(*) FROM hist.flash_market_compact_summary_v1').fetchone()[0]
    else:
        before=0
        for k,v in db.execute('SELECT key,value FROM main.compare_meta'):
            db.execute('INSERT OR REPLACE INTO hist.export_meta_v1(key,value) VALUES(?,?)',(k,v))
        db.commit(); after=db.execute('SELECT count(*) FROM hist.export_meta_v1').fetchone()[0]
    print({'table':args.table,'rowid_start':lo,'rowid_end':hi,'before':before,'after':after,'added':after-before})
    db.close(); return 0

if __name__=='__main__': raise SystemExit(main())
