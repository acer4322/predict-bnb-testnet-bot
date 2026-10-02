from __future__ import annotations

import json
import lzma
import sqlite3
import time
import zlib
from pathlib import Path
from typing import Any

VERSION = "PREDICT_EXECUTION_TAPE_ARCHIVE_V1"


def _dec(blob: bytes | None) -> Any:
    return json.loads(zlib.decompress(blob).decode("utf-8")) if blob else None


def _compact_changes(value: Any) -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = {"bids": [], "asks": []}
    if not isinstance(value, dict):
        return out
    for side in ("bids", "asks"):
        rows = value.get(side) if isinstance(value.get(side), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            out[side].append([
                float(row.get("price", 0.0)),
                float(row.get("before", 0.0)),
                float(row.get("after", 0.0)),
                float(row.get("delta", 0.0)),
            ])
    return out


def _ensure_manifest(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS maker_execution_archive_manifest_v1 (
            market_id INTEGER PRIMARY KEY,
            archive_path TEXT NOT NULL,
            archive_bytes INTEGER NOT NULL,
            l2_rows INTEGER NOT NULL,
            meta_rows INTEGER NOT NULL,
            match_rows INTEGER NOT NULL,
            archived_at_ms INTEGER NOT NULL,
            version TEXT NOT NULL
        );
        """
    )
    con.commit()


def archive_market_to_xz(
    db_path: Path,
    market_id: int,
    out_dir: Path,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    market_id = int(market_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{market_id}.json.xz"

    con = sqlite3.connect(db_path, timeout=10.0)
    con.row_factory = sqlite3.Row
    try:
        _ensure_manifest(con)
        existing = con.execute(
            "SELECT * FROM maker_execution_archive_manifest_v1 WHERE market_id=?",
            (market_id,),
        ).fetchone()
        if existing is not None and path.exists() and not overwrite:
            result = dict(existing)
            result.update(cached=True, path=str(path))
            return result

        updates = con.execute(
            """SELECT source_timestamp_ms,received_at_ms,order_count,is_checkpoint,
                      native_bids_z,native_asks_z,changes_z
                 FROM maker_book_inference_updates
                WHERE market_id=? ORDER BY source_timestamp_ms,id""",
            (market_id,),
        ).fetchall()
        if not updates:
            raise RuntimeError(f"no L2 rows for market {market_id}")

        tables = {str(r[0]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        meta: list[Any] = []
        if "maker_execution_orderbook_meta_v1" in tables:
            for row in con.execute(
                """SELECT source_timestamp_ms,received_at_ms,order_count,
                          last_order_settled_z,settlements_pending_z
                     FROM maker_execution_orderbook_meta_v1
                    WHERE market_id=? ORDER BY source_timestamp_ms,received_at_ms,id""",
                (market_id,),
            ):
                meta.append([
                    int(row[0]), int(row[1]), int(row[2]), _dec(row[3]), _dec(row[4])
                ])

        matches: list[Any] = []
        if "maker_execution_matches_v1" in tables:
            for row in con.execute(
                """SELECT raw_json_z FROM maker_execution_matches_v1
                    WHERE market_id=? ORDER BY executed_at_ms,match_key""",
                (market_id,),
            ):
                matches.append(_dec(row[0]))

        market_row = None
        if "maker_book_inference_markets" in tables:
            row = con.execute(
                "SELECT * FROM maker_book_inference_markets WHERE market_id=?",
                (market_id,),
            ).fetchone()
            market_row = dict(row) if row is not None else None

        compact_updates: list[Any] = []
        for row in updates:
            compact_updates.append([
                int(row["source_timestamp_ms"]),
                int(row["received_at_ms"]),
                int(row["order_count"]),
                int(row["is_checkpoint"]),
                _dec(row["native_bids_z"]),
                _dec(row["native_asks_z"]),
                _compact_changes(_dec(row["changes_z"])),
            ])

        payload = {
            "version": VERSION,
            "marketId": market_id,
            "market": market_row,
            "schema": {
                "updates": "[sourceMs,receivedMs,orderCount,isCheckpoint,bids?,asks?,changes]",
                "executionMeta": "[sourceMs,receivedMs,orderCount,lastOrderSettled,settlementsPending]",
                "matches": "raw Predict /v1/orders/matches payloads",
            },
            "updates": compact_updates,
            "executionMeta": meta,
            "matches": matches,
        }
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        compressed = lzma.compress(raw, preset=3)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(compressed)
        tmp.replace(path)

        archived_at_ms = int(time.time() * 1000)
        con.execute(
            """INSERT INTO maker_execution_archive_manifest_v1(
                   market_id,archive_path,archive_bytes,l2_rows,meta_rows,match_rows,archived_at_ms,version
               ) VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(market_id) DO UPDATE SET
                   archive_path=excluded.archive_path,
                   archive_bytes=excluded.archive_bytes,
                   l2_rows=excluded.l2_rows,
                   meta_rows=excluded.meta_rows,
                   match_rows=excluded.match_rows,
                   archived_at_ms=excluded.archived_at_ms,
                   version=excluded.version""",
            (
                market_id,
                str(path),
                len(compressed),
                len(compact_updates),
                len(meta),
                len(matches),
                archived_at_ms,
                VERSION,
            ),
        )
        con.commit()
        return {
            "marketId": market_id,
            "path": str(path),
            "archiveBytes": len(compressed),
            "rawBytes": len(raw),
            "l2Rows": len(compact_updates),
            "metaRows": len(meta),
            "matchRows": len(matches),
            "archivedAtMs": archived_at_ms,
            "version": VERSION,
        }
    finally:
        con.close()


def load_archive(path: Path) -> dict[str, Any]:
    return json.loads(lzma.decompress(path.read_bytes()).decode("utf-8"))
