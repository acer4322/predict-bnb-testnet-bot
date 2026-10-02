from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COLLECTOR_DB = ROOT / "data" / "cross_oracle.db"
DEFAULT_STRATEGY_DB = ROOT / "data" / "cross_oracle_strategy.db"
MIGRATION_KEY = "collector_to_strategy_split_v1"

# Everything in these families is owned by the 8768 Paper/leader process, not by
# the 8767 market-data collector. Keep the list prefix-based so later quote-canary
# or lead-validation extensions do not silently fall back into cross_oracle.db.
STRATEGY_TABLE_PREFIXES = (
    "cross_oracle_strategy_",
    "poly_binance_lead_",
    "poly_chop_guard_",
    "poly_confidence_",
    "poly_quote_canary_",
)


def collector_db_path() -> Path:
    return Path(os.environ.get("PREDICT_CROSS_ORACLE_DB", DEFAULT_COLLECTOR_DB))


def strategy_db_path() -> Path:
    return Path(
        os.environ.get("PREDICT_CROSS_ORACLE_STRATEGY_DB", DEFAULT_STRATEGY_DB)
    )


def is_strategy_table(name: str) -> bool:
    normalized = str(name or "").strip()
    return bool(normalized) and any(
        normalized.startswith(prefix) for prefix in STRATEGY_TABLE_PREFIXES
    )


def _quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _table_names(db: sqlite3.Connection) -> list[str]:
    rows = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows if is_strategy_table(str(row[0]))]


def _table_sql(db: sqlite3.Connection, table: str) -> str | None:
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def _index_sql(db: sqlite3.Connection, table: str) -> list[str]:
    rows = db.execute(
        """SELECT sql FROM sqlite_master
           WHERE type='index' AND tbl_name=? AND sql IS NOT NULL
           ORDER BY name""",
        (table,),
    ).fetchall()
    return [str(row[0]) for row in rows if row[0]]


def _ensure_meta(db: sqlite3.Connection) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS strategy_db_split_meta (
               key TEXT PRIMARY KEY,
               value TEXT NOT NULL,
               updated_at_ms INTEGER NOT NULL
           )"""
    )
    db.commit()


def _already_migrated(db: sqlite3.Connection) -> bool:
    _ensure_meta(db)
    row = db.execute(
        "SELECT value FROM strategy_db_split_meta WHERE key=?",
        (MIGRATION_KEY,),
    ).fetchone()
    return row is not None


def bootstrap_strategy_db(
    *,
    source_path: Path | None = None,
    destination_path: Path | None = None,
) -> dict[str, Any]:
    """One-time migration of 8768-owned tables out of cross_oracle.db.

    This intentionally never copies collector-owned raw tables such as
    polymarket_events, chainlink_ticks, polymarket_markets, or feed-gap archives.
    After the split, 8768 is launched with PREDICT_CROSS_ORACLE_DB redirected to
    the dedicated strategy DB, while 8767 keeps the normal collector DB.
    """

    source = Path(source_path or collector_db_path())
    destination = Path(destination_path or strategy_db_path())
    destination.parent.mkdir(parents=True, exist_ok=True)

    if source.resolve() == destination.resolve():
        raise RuntimeError("strategy DB must be different from collector DB")

    target = sqlite3.connect(destination, timeout=30.0)
    target.row_factory = sqlite3.Row
    target.execute("PRAGMA journal_mode=WAL")
    target.execute("PRAGMA synchronous=NORMAL")
    target.execute("PRAGMA busy_timeout=30000")
    try:
        if _already_migrated(target):
            return {
                "status": "ALREADY_SPLIT",
                "source": str(source),
                "destination": str(destination),
                "tables": [],
                "rows": 0,
            }

        if not source.exists():
            payload = {
                "status": "NO_LEGACY_SOURCE",
                "source": str(source),
                "destination": str(destination),
                "tables": [],
                "rows": 0,
            }
            target.execute(
                "INSERT OR REPLACE INTO strategy_db_split_meta(key,value,updated_at_ms) VALUES(?,?,?)",
                (MIGRATION_KEY, json.dumps(payload, separators=(",", ":")), int(time.time() * 1000)),
            )
            target.commit()
            return payload

        uri = f"file:{source.resolve().as_posix()}?mode=ro"
        legacy = sqlite3.connect(uri, uri=True, timeout=30.0)
        legacy.row_factory = sqlite3.Row
        legacy.execute("PRAGMA query_only=ON")
        legacy.execute("PRAGMA busy_timeout=30000")
        copied_tables: list[dict[str, Any]] = []
        copied_rows = 0
        try:
            table_names = _table_names(legacy)
            # Create all tables first so indexes/relationships can be added after
            # the data copy without changing the source DB.
            for table in table_names:
                sql = _table_sql(legacy, table)
                if sql:
                    target.execute(sql)
            target.commit()

            for table in table_names:
                quoted = _quote_identifier(table)
                column_rows = legacy.execute(f"PRAGMA table_info({quoted})").fetchall()
                columns = [str(row[1]) for row in column_rows]
                if not columns:
                    continue
                column_sql = ",".join(_quote_identifier(column) for column in columns)
                placeholders = ",".join("?" for _ in columns)
                cursor = legacy.execute(f"SELECT {column_sql} FROM {quoted}")
                table_rows = 0
                while True:
                    rows = cursor.fetchmany(5000)
                    if not rows:
                        break
                    target.executemany(
                        f"INSERT OR IGNORE INTO {quoted} ({column_sql}) VALUES ({placeholders})",
                        [tuple(row) for row in rows],
                    )
                    table_rows += len(rows)
                    copied_rows += len(rows)
                    target.commit()
                for index_sql in _index_sql(legacy, table):
                    try:
                        target.execute(index_sql)
                    except sqlite3.OperationalError as exc:
                        # A duplicate index name is harmless on a resumed/partly
                        # initialized destination; anything else should surface.
                        if "already exists" not in str(exc).lower():
                            raise
                target.commit()
                copied_tables.append({"table": table, "rowsRead": table_rows})
        finally:
            legacy.close()

        payload = {
            "status": "MIGRATED",
            "source": str(source),
            "destination": str(destination),
            "tables": copied_tables,
            "rows": copied_rows,
        }
        target.execute(
            "INSERT OR REPLACE INTO strategy_db_split_meta(key,value,updated_at_ms) VALUES(?,?,?)",
            (MIGRATION_KEY, json.dumps(payload, separators=(",", ":")), int(time.time() * 1000)),
        )
        target.commit()
        return payload
    finally:
        target.close()


def strategy_child_environment() -> dict[str, str]:
    """Return an env where legacy DB consumers inside 8768 see the strategy DB."""
    env = dict(os.environ)
    destination = strategy_db_path()
    env["PREDICT_CROSS_ORACLE_STRATEGY_DB"] = str(destination)
    # Existing 8768 modules historically read PREDICT_CROSS_ORACLE_DB. Override
    # it only in the 8768 child process; the supervisor/8767 environment remains
    # pointed at the collector DB.
    env["PREDICT_CROSS_ORACLE_DB"] = str(destination)
    return env
