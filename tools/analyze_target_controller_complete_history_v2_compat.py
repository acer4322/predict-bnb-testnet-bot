from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import analyze_target_controller_complete_history_v2 as v2


def _load_source_compat(path: Path, source: str, asset: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load legacy/official Target events while tolerating legacy schemas without an asset column."""
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)

    db = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    try:
        table = "wallet_shadow_target_events"
        cols = {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}
        required = {
            "leg_id",
            "wallet",
            "market_id",
            "role",
            "side",
            "quote_type",
            "order_hash",
            "event_ms",
            "price",
            "shares",
        }
        missing = sorted(required - cols)
        if missing:
            raise RuntimeError(f"{resolved}: {table} missing columns: {', '.join(missing)}")

        has_asset = "asset" in cols
        select_cols = [
            "leg_id",
            "wallet",
            "market_id",
            "role",
            "side",
            "quote_type",
            "order_hash",
            "event_ms",
            "price",
            "shares",
        ]
        if has_asset:
            select_cols.insert(2, "asset")

        where = [
            "role IN ('MAKER','TAKER')",
            "side IN ('UP','DOWN')",
            "quote_type IN ('BID','ASK')",
            "shares>0",
        ]
        params: list[Any] = []
        if has_asset:
            where.insert(0, "asset=?")
            params.append(asset)

        sql = (
            f"SELECT {','.join(select_cols)} FROM {table} "
            f"WHERE {' AND '.join(where)} ORDER BY event_ms,leg_id"
        )

        rows: list[dict[str, Any]] = []
        for raw in db.execute(sql, tuple(params)):
            row = dict(raw)
            row["asset"] = str(row.get("asset") or asset).upper()
            row["wallet"] = str(row.get("wallet") or "").lower()
            row["market_id"] = int(row["market_id"])
            row["event_ms"] = int(row["event_ms"])
            row["price"] = float(row["price"])
            row["shares"] = float(row["shares"])
            row["source_version"] = source
            rows.append(row)

        return rows, {
            "path": str(resolved),
            "sourceVersion": source,
            "rows": len(rows),
            "markets": len({r["market_id"] for r in rows}),
            "firstEventMs": min((r["event_ms"] for r in rows), default=None),
            "lastEventMs": max((r["event_ms"] for r in rows), default=None),
            "assetColumnPresent": has_asset,
            "assetFilterApplied": has_asset,
            "assetInjectedFromCli": not has_asset,
            "effectiveAsset": asset,
        }
    finally:
        db.close()


# Keep the validated V2 replay / burst / transition logic untouched; replace only its source adapter.
v2._load_source = _load_source_compat


if __name__ == "__main__":
    raise SystemExit(v2.main())
