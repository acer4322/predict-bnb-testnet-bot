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


def ms_at_utc(date_text: str) -> tuple[int, int]:
    day = dt.datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    return int(day.timestamp() * 1000), int((day + dt.timedelta(days=1)).timestamp() * 1000)


def ensure_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS flash_market_lifecycle_v1 (
            market_id INTEGER PRIMARY KEY,
            last_seen_ms INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_flash_market_lifecycle_last_seen
            ON flash_market_lifecycle_v1(last_seen_ms);
        CREATE TABLE IF NOT EXISTS flash_market_compact_summary_v1 (
            strategy_version TEXT NOT NULL,
            market_id INTEGER NOT NULL,
            first_decision_ms INTEGER,
            last_decision_ms INTEGER,
            decision_count INTEGER NOT NULL,
            order_count INTEGER NOT NULL,
            fill_count INTEGER NOT NULL,
            cancelled_order_count INTEGER NOT NULL,
            up_shares REAL NOT NULL,
            down_shares REAL NOT NULL,
            total_cost_usdt REAL NOT NULL,
            settle_up_pnl_usdt REAL NOT NULL,
            settle_down_pnl_usdt REAL NOT NULL,
            archived_at_ms INTEGER NOT NULL,
            PRIMARY KEY(strategy_version, market_id)
        );
        """
    )


def ensure_archive_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS flash_market_posthoc_summary_v1 (
            strategy_version TEXT NOT NULL,
            market_id INTEGER NOT NULL,
            window_end_ms INTEGER,
            winner TEXT,
            decision_count INTEGER NOT NULL,
            order_count INTEGER NOT NULL,
            fill_count INTEGER NOT NULL,
            cancelled_order_count INTEGER NOT NULL,
            up_shares REAL NOT NULL,
            down_shares REAL NOT NULL,
            total_cost_usdt REAL NOT NULL,
            settle_up_pnl_usdt REAL NOT NULL,
            settle_down_pnl_usdt REAL NOT NULL,
            realized_pnl_usdt REAL,
            archived_at_ms INTEGER NOT NULL,
            PRIMARY KEY(strategy_version, market_id)
        );
        CREATE TABLE IF NOT EXISTS archive_runs_v1 (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at_ms INTEGER NOT NULL,
            completed_at_ms INTEGER,
            date_utc TEXT NOT NULL,
            cutoff_ms INTEGER NOT NULL,
            markets_seen INTEGER NOT NULL DEFAULT 0,
            markets_with_raw INTEGER NOT NULL DEFAULT 0,
            strategy_summaries INTEGER NOT NULL DEFAULT 0
        );
        """
    )


def summarize_market(db: sqlite3.Connection, market_id: int, archived_at_ms: int) -> list[dict]:
    versions = {
        str(row[0])
        for sql in (
            "SELECT DISTINCT strategy_version FROM our_decisions WHERE market_id=?",
            "SELECT DISTINCT strategy_version FROM our_orders WHERE market_id=?",
            "SELECT DISTINCT strategy_version FROM our_fills WHERE market_id=?",
        )
        for row in db.execute(sql, (market_id,)).fetchall()
        if row[0] is not None
    }
    out: list[dict] = []
    for version in sorted(versions):
        decision = db.execute(
            "SELECT COUNT(*),MIN(decision_ms),MAX(decision_ms) FROM our_decisions WHERE market_id=? AND strategy_version=?",
            (market_id, version),
        ).fetchone()
        order = db.execute(
            "SELECT COUNT(*),SUM(CASE WHEN status='CANCELLED' THEN 1 ELSE 0 END) FROM our_orders WHERE market_id=? AND strategy_version=?",
            (market_id, version),
        ).fetchone()
        fill = db.execute(
            """SELECT COUNT(*),
                      COALESCE(SUM(CASE WHEN side='UP' THEN shares ELSE 0 END),0),
                      COALESCE(SUM(CASE WHEN side='DOWN' THEN shares ELSE 0 END),0),
                      COALESCE(SUM(price*shares),0)
               FROM our_fills WHERE market_id=? AND strategy_version=?""",
            (market_id, version),
        ).fetchone()
        row = {
            "strategy_version": version,
            "market_id": market_id,
            "first_decision_ms": int(decision[1]) if decision[1] is not None else None,
            "last_decision_ms": int(decision[2]) if decision[2] is not None else None,
            "decision_count": int(decision[0] or 0),
            "order_count": int(order[0] or 0),
            "fill_count": int(fill[0] or 0),
            "cancelled_order_count": int(order[1] or 0),
            "up_shares": float(fill[1] or 0.0),
            "down_shares": float(fill[2] or 0.0),
            "total_cost_usdt": float(fill[3] or 0.0),
            "archived_at_ms": archived_at_ms,
        }
        row["settle_up_pnl_usdt"] = row["up_shares"] - row["total_cost_usdt"]
        row["settle_down_pnl_usdt"] = row["down_shares"] - row["total_cost_usdt"]
        out.append(row)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="UTC date YYYY-MM-DD")
    ap.add_argument("--retention-hours", type=float, default=48.0)
    args = ap.parse_args()

    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - int(args.retention_hours * 3_600_000)
    start_ms, end_ms = ms_at_utc(args.date)
    end_ms = min(end_ms, cutoff_ms)
    if end_ms <= start_ms:
        print({"date": args.date, "skipped": "inside_retention_window", "cutoff_ms": cutoff_ms})
        return 0

    ARCHIVE_DB.parent.mkdir(parents=True, exist_ok=True)
    flash = sqlite3.connect(FLASH_DB, timeout=30)
    flash.execute("PRAGMA busy_timeout=30000")
    flash.execute("PRAGMA journal_mode=WAL")
    ensure_schema(flash)
    archive = sqlite3.connect(ARCHIVE_DB, timeout=30)
    archive.execute("PRAGMA journal_mode=WAL")
    ensure_archive_schema(archive)
    target = sqlite3.connect(f"file:{TARGET_DB.resolve().as_posix()}?mode=ro", uri=True, timeout=30)

    run_id = archive.execute(
        "INSERT INTO archive_runs_v1(started_at_ms,date_utc,cutoff_ms) VALUES(?,?,?)",
        (now_ms, args.date, cutoff_ms),
    ).lastrowid
    archive.commit()

    markets = target.execute(
        """SELECT market_id,window_end_ms,winner FROM target_markets
             WHERE asset='BTC' AND window_end_ms>=? AND window_end_ms<? ORDER BY window_end_ms""",
        (start_ms, end_ms),
    ).fetchall()

    markets_with_raw = 0
    summaries = 0
    deleted_decisions = deleted_orders = deleted_fills = 0
    for idx, (market_id, window_end_ms, winner) in enumerate(markets, 1):
        market_id = int(market_id)
        rows = summarize_market(flash, market_id, now_ms)
        if not rows:
            continue
        markets_with_raw += 1
        for row in rows:
            flash.execute(
                """INSERT OR REPLACE INTO flash_market_compact_summary_v1(
                       strategy_version,market_id,first_decision_ms,last_decision_ms,decision_count,order_count,fill_count,
                       cancelled_order_count,up_shares,down_shares,total_cost_usdt,settle_up_pnl_usdt,
                       settle_down_pnl_usdt,archived_at_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["strategy_version"], row["market_id"], row["first_decision_ms"], row["last_decision_ms"],
                    row["decision_count"], row["order_count"], row["fill_count"], row["cancelled_order_count"],
                    row["up_shares"], row["down_shares"], row["total_cost_usdt"], row["settle_up_pnl_usdt"],
                    row["settle_down_pnl_usdt"], row["archived_at_ms"],
                ),
            )
            realized = None
            if winner == "UP":
                realized = row["settle_up_pnl_usdt"]
            elif winner == "DOWN":
                realized = row["settle_down_pnl_usdt"]
            archive.execute(
                """INSERT OR REPLACE INTO flash_market_posthoc_summary_v1(
                       strategy_version,market_id,window_end_ms,winner,decision_count,order_count,fill_count,
                       cancelled_order_count,up_shares,down_shares,total_cost_usdt,settle_up_pnl_usdt,
                       settle_down_pnl_usdt,realized_pnl_usdt,archived_at_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["strategy_version"], market_id, window_end_ms, winner, row["decision_count"], row["order_count"],
                    row["fill_count"], row["cancelled_order_count"], row["up_shares"], row["down_shares"],
                    row["total_cost_usdt"], row["settle_up_pnl_usdt"], row["settle_down_pnl_usdt"], realized, now_ms,
                ),
            )
            summaries += 1
        deleted_fills += flash.execute("DELETE FROM our_fills WHERE market_id=?", (market_id,)).rowcount
        deleted_orders += flash.execute("DELETE FROM our_orders WHERE market_id=?", (market_id,)).rowcount
        deleted_decisions += flash.execute("DELETE FROM our_decisions WHERE market_id=?", (market_id,)).rowcount
        flash.execute("DELETE FROM flash_market_lifecycle_v1 WHERE market_id=?", (market_id,))
        if idx % 20 == 0:
            flash.commit()
            archive.commit()

    flash.commit()
    archive.execute(
        """UPDATE archive_runs_v1 SET completed_at_ms=?,markets_seen=?,markets_with_raw=?,strategy_summaries=? WHERE run_id=?""",
        (int(time.time() * 1000), len(markets), markets_with_raw, summaries, run_id),
    )
    archive.commit()
    try:
        flash.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        pass
    flash.close(); archive.close(); target.close()
    print({
        "date": args.date,
        "markets_seen": len(markets),
        "markets_with_raw": markets_with_raw,
        "strategy_summaries": summaries,
        "deleted_decisions": deleted_decisions,
        "deleted_orders": deleted_orders,
        "deleted_fills": deleted_fills,
        "cutoff_ms": cutoff_ms,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
