from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"


def _connect_source(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=30.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA busy_timeout=30000")
    return db


def _connect_output(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30.0)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=30000")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS cross_oracle_history_samples (
            bucket_ms INTEGER PRIMARY KEY,
            market_slug TEXT NOT NULL,
            condition_id TEXT,
            market_start_ms INTEGER,
            market_end_ms INTEGER,
            poly_source_ms INTEGER,
            poly_received_ms INTEGER,
            up_bid REAL,
            up_ask REAL,
            up_last REAL,
            down_bid REAL,
            down_ask REAL,
            down_last REAL,
            chainlink_price REAL,
            chainlink_source_ms INTEGER,
            chainlink_received_ms INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_cross_oracle_history_market
            ON cross_oracle_history_samples(market_slug, bucket_ms);

        CREATE TABLE IF NOT EXISTS cross_oracle_chainlink_history (
            source_timestamp_ms INTEGER,
            received_wall_ns INTEGER PRIMARY KEY,
            price REAL NOT NULL,
            topic TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_cross_oracle_chainlink_source
            ON cross_oracle_chainlink_history(source_timestamp_ms);

        CREATE TABLE IF NOT EXISTS cross_oracle_market_history (
            slug TEXT PRIMARY KEY,
            market_id TEXT,
            condition_id TEXT,
            question TEXT,
            window_start_ms INTEGER NOT NULL,
            window_end_ms INTEGER NOT NULL,
            up_token_id TEXT NOT NULL,
            down_token_id TEXT NOT NULL,
            discovered_at_ms INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS cross_oracle_feed_gap_history (
            gap_generation INTEGER PRIMARY KEY,
            opened_at_ms INTEGER NOT NULL,
            recovered_at_ms INTEGER,
            duration_ms INTEGER,
            reason TEXT NOT NULL,
            detail TEXT,
            market_slug TEXT,
            target_slug TEXT
        );

        CREATE TABLE IF NOT EXISTS archive_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    db.commit()
    return db


def _tables(db: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _copy_small_tables(
    source: sqlite3.Connection,
    output: sqlite3.Connection,
    cutoff_ms: int,
) -> list[sqlite3.Row]:
    markets = source.execute(
        """SELECT slug, market_id, condition_id, question, window_start_ms,
                  window_end_ms, up_token_id, down_token_id, discovered_at_ms
             FROM polymarket_markets
            WHERE window_end_ms >= ?
            ORDER BY window_start_ms""",
        (cutoff_ms,),
    ).fetchall()
    output.executemany(
        """INSERT OR REPLACE INTO cross_oracle_market_history(
               slug, market_id, condition_id, question, window_start_ms,
               window_end_ms, up_token_id, down_token_id, discovered_at_ms
           ) VALUES (?,?,?,?,?,?,?,?,?)""",
        [tuple(row) for row in markets],
    )

    cutoff_ns = cutoff_ms * 1_000_000
    output.execute(
        "DELETE FROM cross_oracle_chainlink_history WHERE received_wall_ns < ?",
        (cutoff_ns,),
    )
    cursor = source.execute(
        """SELECT source_timestamp_ms, received_wall_ns, price, topic
             FROM chainlink_ticks
            WHERE received_wall_ns >= ?
            ORDER BY received_wall_ns""",
        (cutoff_ns,),
    )
    while True:
        rows = cursor.fetchmany(20_000)
        if not rows:
            break
        output.executemany(
            """INSERT OR REPLACE INTO cross_oracle_chainlink_history(
                   source_timestamp_ms, received_wall_ns, price, topic
               ) VALUES (?,?,?,?)""",
            [tuple(row) for row in rows],
        )
        output.commit()

    if "cross_oracle_feed_gaps" in _tables(source):
        gaps = source.execute(
            """SELECT gap_generation, opened_at_ms, recovered_at_ms, duration_ms,
                      reason, detail, market_slug, target_slug
                 FROM cross_oracle_feed_gaps
                WHERE opened_at_ms >= ?
                ORDER BY gap_generation""",
            (cutoff_ms,),
        ).fetchall()
        output.executemany(
            """INSERT OR REPLACE INTO cross_oracle_feed_gap_history(
                   gap_generation, opened_at_ms, recovered_at_ms, duration_ms,
                   reason, detail, market_slug, target_slug
               ) VALUES (?,?,?,?,?,?,?,?)""",
            [tuple(row) for row in gaps],
        )
    output.commit()
    return markets


def _export_market(
    source: sqlite3.Connection,
    output: sqlite3.Connection,
    market: sqlite3.Row,
    cutoff_ns: int,
    sample_ms: int,
) -> tuple[int, int]:
    slug = str(market["slug"])
    cursor = source.execute(
        """SELECT outcome, event_type, best_bid, best_ask, last_trade,
                  source_timestamp_ms, received_wall_ns
             FROM polymarket_events
            WHERE market_slug=? AND received_wall_ns >= ?
            ORDER BY received_wall_ns, id""",
        (slug, cutoff_ns),
    )

    state = {
        "UP": {"bid": None, "ask": None, "last": None},
        "DOWN": {"bid": None, "ask": None, "last": None},
    }
    current_bucket: int | None = None
    latest_source_ms: int | None = None
    latest_received_ms: int | None = None
    rows_read = 0
    rows_written = 0

    def flush(bucket_ms: int | None) -> None:
        nonlocal rows_written
        if bucket_ms is None:
            return
        up = state["UP"]
        down = state["DOWN"]
        if all(
            value is None
            for value in (
                up["bid"], up["ask"], up["last"],
                down["bid"], down["ask"], down["last"],
            )
        ):
            return
        output.execute(
            """INSERT OR REPLACE INTO cross_oracle_history_samples(
                   bucket_ms, market_slug, condition_id, market_start_ms,
                   market_end_ms, poly_source_ms, poly_received_ms,
                   up_bid, up_ask, up_last, down_bid, down_ask, down_last,
                   chainlink_price, chainlink_source_ms, chainlink_received_ms
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                bucket_ms,
                slug,
                market["condition_id"],
                market["window_start_ms"],
                market["window_end_ms"],
                latest_source_ms,
                latest_received_ms,
                up["bid"], up["ask"], up["last"],
                down["bid"], down["ask"], down["last"],
                None,
                None,
                None,
            ),
        )
        rows_written += 1

    while True:
        batch = cursor.fetchmany(20_000)
        if not batch:
            break
        for row in batch:
            rows_read += 1
            received_ns = int(row["received_wall_ns"])
            received_ms = received_ns // 1_000_000
            bucket_ms = (received_ms // sample_ms) * sample_ms
            if current_bucket is None:
                current_bucket = bucket_ms
            elif bucket_ms != current_bucket:
                flush(current_bucket)
                current_bucket = bucket_ms

            outcome = str(row["outcome"] or "").upper()
            if outcome not in state:
                continue
            side = state[outcome]
            if row["best_bid"] is not None:
                side["bid"] = float(row["best_bid"])
            if row["best_ask"] is not None:
                side["ask"] = float(row["best_ask"])
            if row["last_trade"] is not None:
                side["last"] = float(row["last_trade"])
            if row["source_timestamp_ms"] is not None:
                latest_source_ms = int(row["source_timestamp_ms"])
            latest_received_ms = received_ms
        output.commit()
    flush(current_bucket)
    output.commit()
    return rows_read, rows_written


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export recent cross_oracle history into a compact, resumable database "
            "without copying raw_json or full book-depth payloads."
        )
    )
    parser.add_argument(
        "--source",
        default=str(DATA / "cross_oracle.db"),
        help="source cross_oracle DB; stop BTC 5M Lab before reading a huge live DB",
    )
    parser.add_argument(
        "--output",
        default=str(DATA / "cross_oracle_history_7d.db"),
    )
    parser.add_argument("--days", type=float, default=7.0)
    parser.add_argument("--sample-ms", type=int, default=250)
    args = parser.parse_args()

    source_path = Path(args.source)
    output_path = Path(args.output)
    if not source_path.exists():
        raise SystemExit(f"source DB not found: {source_path}")
    days = max(0.25, float(args.days))
    sample_ms = max(100, int(args.sample_ms))
    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - int(days * 24 * 3600 * 1000)
    cutoff_ns = cutoff_ms * 1_000_000

    print(f"source={source_path}")
    print(f"output={output_path}")
    print(f"retentionDays={days:g}; sampleMs={sample_ms}")
    print("raw_json/full depth will NOT be copied")
    print("Chainlink is retained in cross_oracle_chainlink_history and is not joined per 250ms Poly row during export")

    source = _connect_source(source_path)
    output = _connect_output(output_path)
    try:
        markets = _copy_small_tables(source, output, cutoff_ms)
        print(f"recent markets={len(markets)}")
        total_read = 0
        total_written = 0
        for index, market in enumerate(markets, start=1):
            read_count, written_count = _export_market(
                source, output, market, cutoff_ns, sample_ms
            )
            total_read += read_count
            total_written += written_count
            print(
                f"[{index}/{len(markets)}] {market['slug']}: "
                f"rawRowsRead={read_count} compactRows={written_count}",
                flush=True,
            )
        for key, value in (
            ("exportedAtMs", str(int(time.time() * 1000))),
            ("sourcePath", str(source_path.resolve())),
            ("retentionDays", str(days)),
            ("sampleMs", str(sample_ms)),
            ("polyPayloadMode", "250ms_normalized_top_of_book_no_raw_json"),
            ("chainlinkMode", "full_recent_rows_separate_table"),
        ):
            output.execute(
                "INSERT OR REPLACE INTO archive_meta(key,value) VALUES (?,?)",
                (key, value),
            )
        output.commit()
        print(
            f"done: rawRowsRead={total_read}; compactRows={total_written}; "
            f"outputBytes={output_path.stat().st_size if output_path.exists() else 0}",
            flush=True,
        )
    finally:
        output.close()
        source.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
