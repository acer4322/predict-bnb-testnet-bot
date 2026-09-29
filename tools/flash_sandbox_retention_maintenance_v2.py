from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLASH_DB = ROOT / "data" / "strategy_target_flash_v1.db"
TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
ARCHIVE_DB = ROOT / "data" / "research" / "flash_sandbox_archive_v1.db"


def day_bounds(date_text: str) -> tuple[int, int]:
    day = dt.datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    return int(day.timestamp() * 1000), int((day + dt.timedelta(days=1)).timestamp() * 1000)


def ensure_flash_schema(db: sqlite3.Connection) -> None:
    db.executescript("""
    CREATE TABLE IF NOT EXISTS flash_market_lifecycle_v1(
      market_id INTEGER PRIMARY KEY,last_seen_ms INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_flash_market_lifecycle_last_seen
      ON flash_market_lifecycle_v1(last_seen_ms);
    CREATE TABLE IF NOT EXISTS flash_market_compact_summary_v1(
      strategy_version TEXT NOT NULL,market_id INTEGER NOT NULL,
      first_decision_ms INTEGER,last_decision_ms INTEGER,
      decision_count INTEGER NOT NULL,order_count INTEGER NOT NULL,
      fill_count INTEGER NOT NULL,cancelled_order_count INTEGER NOT NULL,
      up_shares REAL NOT NULL,down_shares REAL NOT NULL,total_cost_usdt REAL NOT NULL,
      settle_up_pnl_usdt REAL NOT NULL,settle_down_pnl_usdt REAL NOT NULL,
      archived_at_ms INTEGER NOT NULL,
      PRIMARY KEY(strategy_version,market_id));
    """)


def ensure_archive_schema(db: sqlite3.Connection) -> None:
    db.executescript("""
    CREATE TABLE IF NOT EXISTS flash_market_posthoc_summary_v1(
      strategy_version TEXT NOT NULL,market_id INTEGER NOT NULL,window_end_ms INTEGER,winner TEXT,
      decision_count INTEGER NOT NULL,order_count INTEGER NOT NULL,fill_count INTEGER NOT NULL,
      cancelled_order_count INTEGER NOT NULL,up_shares REAL NOT NULL,down_shares REAL NOT NULL,
      total_cost_usdt REAL NOT NULL,settle_up_pnl_usdt REAL NOT NULL,settle_down_pnl_usdt REAL NOT NULL,
      realized_pnl_usdt REAL,archived_at_ms INTEGER NOT NULL,
      PRIMARY KEY(strategy_version,market_id));
    CREATE TABLE IF NOT EXISTS flash_market_archive_checks_v1(
      market_id INTEGER PRIMARY KEY,date_utc TEXT NOT NULL,window_end_ms INTEGER,
      had_raw INTEGER NOT NULL,strategy_summaries INTEGER NOT NULL,checked_at_ms INTEGER NOT NULL);
    """)


def chunks(items: list[int], n: int):
    for i in range(0, len(items), n):
        yield items[i:i+n]


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--date',required=True)
    ap.add_argument('--retention-hours',type=float,default=48.0)
    ap.add_argument('--batch-size',type=int,default=300)
    args=ap.parse_args()
    now_ms=int(time.time()*1000)
    cutoff=now_ms-int(args.retention_hours*3_600_000)
    lo,hi=day_bounds(args.date); hi=min(hi,cutoff)
    if hi<=lo:
        print({'date':args.date,'skipped':'inside_retention_window','cutoff_ms':cutoff}); return 0

    target=sqlite3.connect(f"file:{TARGET_DB.resolve().as_posix()}?mode=ro",uri=True,timeout=30)
    markets=target.execute("""SELECT market_id,window_end_ms,winner FROM target_markets
        WHERE asset='BTC' AND window_end_ms>=? AND window_end_ms<? ORDER BY window_end_ms""",(lo,hi)).fetchall()
    target.close()
    meta={int(mid):(int(end) if end is not None else None,winner) for mid,end,winner in markets}

    archive=sqlite3.connect(ARCHIVE_DB,timeout=30); archive.execute('PRAGMA journal_mode=WAL'); ensure_archive_schema(archive)
    checked={int(r[0]) for r in archive.execute(
        f"SELECT market_id FROM flash_market_archive_checks_v1 WHERE market_id IN ({','.join('?' for _ in meta)})",tuple(meta)
    ).fetchall()} if meta else set()
    pending=[mid for mid in meta if mid not in checked]
    flash=sqlite3.connect(FLASH_DB,timeout=30); flash.execute('PRAGMA busy_timeout=30000'); ensure_flash_schema(flash)

    total_raw_markets=0; total_summaries=0; del_d=del_o=del_f=0; checked_now=0
    for batch in chunks(pending,max(1,args.batch_size)):
        q=','.join('?' for _ in batch)
        dec={}
        for mid,sv,cnt,mn,mx in flash.execute(
            f"SELECT market_id,strategy_version,COUNT(*),MIN(decision_ms),MAX(decision_ms) FROM our_decisions WHERE market_id IN ({q}) GROUP BY market_id,strategy_version",batch):
            dec[(int(mid),str(sv))]=(int(cnt),int(mn) if mn is not None else None,int(mx) if mx is not None else None)
        orders={}
        for mid,sv,cnt,cancelled in flash.execute(
            f"SELECT market_id,strategy_version,COUNT(*),SUM(CASE WHEN status='CANCELLED' THEN 1 ELSE 0 END) FROM our_orders WHERE market_id IN ({q}) GROUP BY market_id,strategy_version",batch):
            orders[(int(mid),str(sv))]=(int(cnt),int(cancelled or 0))
        fills={}
        for mid,sv,cnt,ups,downs,cost in flash.execute(
            f"""SELECT market_id,strategy_version,COUNT(*),
                COALESCE(SUM(CASE WHEN side='UP' THEN shares ELSE 0 END),0),
                COALESCE(SUM(CASE WHEN side='DOWN' THEN shares ELSE 0 END),0),
                COALESCE(SUM(price*shares),0)
                FROM our_fills WHERE market_id IN ({q}) GROUP BY market_id,strategy_version""",batch):
            fills[(int(mid),str(sv))]=(int(cnt),float(ups or 0),float(downs or 0),float(cost or 0))
        keys=set(dec)|set(orders)|set(fills)
        by_market={mid:0 for mid in batch}
        for mid,sv in sorted(keys):
            dc,mn,mx=dec.get((mid,sv),(0,None,None)); oc,cc=orders.get((mid,sv),(0,0)); fc,ups,downs,cost=fills.get((mid,sv),(0,0.0,0.0,0.0))
            su=ups-cost; sd=downs-cost
            flash.execute("""INSERT OR REPLACE INTO flash_market_compact_summary_v1(
                strategy_version,market_id,first_decision_ms,last_decision_ms,decision_count,order_count,fill_count,
                cancelled_order_count,up_shares,down_shares,total_cost_usdt,settle_up_pnl_usdt,settle_down_pnl_usdt,archived_at_ms)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sv,mid,mn,mx,dc,oc,fc,cc,ups,downs,cost,su,sd,now_ms))
            end,winner=meta[mid]
            realized=su if winner=='UP' else sd if winner=='DOWN' else None
            archive.execute("""INSERT OR REPLACE INTO flash_market_posthoc_summary_v1(
                strategy_version,market_id,window_end_ms,winner,decision_count,order_count,fill_count,cancelled_order_count,
                up_shares,down_shares,total_cost_usdt,settle_up_pnl_usdt,settle_down_pnl_usdt,realized_pnl_usdt,archived_at_ms)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sv,mid,end,winner,dc,oc,fc,cc,ups,downs,cost,su,sd,realized,now_ms))
            by_market[mid]+=1; total_summaries+=1
        total_raw_markets+=sum(1 for n in by_market.values() if n)
        # Archive commits before raw deletion: a crash can duplicate work but cannot lose the compact result.
        for mid in batch:
            end,_=meta[mid]
            archive.execute("INSERT OR REPLACE INTO flash_market_archive_checks_v1 VALUES(?,?,?,?,?,?)",
                (mid,args.date,end,1 if by_market[mid] else 0,by_market[mid],now_ms))
        archive.commit()
        del_f+=flash.execute(f"DELETE FROM our_fills WHERE market_id IN ({q})",batch).rowcount
        del_o+=flash.execute(f"DELETE FROM our_orders WHERE market_id IN ({q})",batch).rowcount
        del_d+=flash.execute(f"DELETE FROM our_decisions WHERE market_id IN ({q})",batch).rowcount
        flash.execute(f"DELETE FROM flash_market_lifecycle_v1 WHERE market_id IN ({q})",batch)
        flash.commit(); checked_now+=len(batch)

    try: flash.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    except sqlite3.Error: pass
    remaining=len(meta)-len(checked)-checked_now
    flash.close(); archive.close()
    print({'date':args.date,'eligible_markets':len(meta),'previously_checked':len(checked),'checked_now':checked_now,
           'remaining':remaining,'markets_with_raw_now':total_raw_markets,'strategy_summaries_now':total_summaries,
           'deleted_decisions':del_d,'deleted_orders':del_o,'deleted_fills':del_f,'cutoff_ms':cutoff})
    return 0

if __name__=='__main__': raise SystemExit(main())
