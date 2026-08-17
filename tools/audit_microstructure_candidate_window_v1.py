from __future__ import annotations

import argparse
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

TW = timezone(timedelta(hours=8))
FIELDS = (
    "spot_price",
    "futures_price",
    "spot_microprice",
    "futures_microprice",
    "spot_queue_imbalance",
    "futures_queue_imbalance",
    "spot_taker_imbalance_250ms",
    "futures_taker_imbalance_250ms",
    "spot_taker_imbalance_1s",
    "futures_taker_imbalance_1s",
    "perp_spot_basis_bps",
    "prediction_up_mid",
    "direction_score",
)
CORE = (
    "spot_price",
    "futures_price",
    "spot_queue_imbalance",
    "futures_queue_imbalance",
    "direction_score",
)


def parse(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return dt


def finite(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def tw(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, timezone.utc).astimezone(TW).isoformat()


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int((len(values) - 1) * q))]


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only bounded microstructure completeness audit")
    ap.add_argument("--db", default="data/microstructure.db")
    ap.add_argument("--start", default="2026-08-16T05:34:20+08:00")
    ap.add_argument("--end", default="2026-08-16T06:00:20+08:00")
    args = ap.parse_args()

    path = Path(args.db)
    if not path.exists():
        raise SystemExit(f"missing DB: {path.resolve()}")
    lo = int(parse(args.start).timestamp() * 1e9)
    hi = int(parse(args.end).timestamp() * 1e9)

    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    try:
        indexes = []
        for row in con.execute("PRAGMA index_list(microstructure_snapshots)"):
            name = str(row[1])
            cols = [str(x[2]) for x in con.execute(f'PRAGMA index_info("{name}")') if x[2] is not None]
            indexes.append((name, cols))
        idx = next((item for item in indexes if item[1] and item[1][0] == "timestamp_ns"), None)
        if idx is None:
            raise SystemExit("refusing query: timestamp_ns is not leftmost indexed")

        columns = {str(row[1]) for row in con.execute("PRAGMA table_info(microstructure_snapshots)")}
        available = [field for field in FIELDS if field in columns]
        sql = (
            "SELECT timestamp_ns, market_id, " + ", ".join(available)
            + " FROM microstructure_snapshots WHERE timestamp_ns>=? AND timestamp_ns<? ORDER BY timestamp_ns"
        )
        rows = list(con.execute(sql, (lo, hi)))
    finally:
        con.close()

    print("MICROSTRUCTURE_CANDIDATE_WINDOW_V1")
    print("index:", idx)
    print("window:", args.start, "->", args.end)
    print("rows:", len(rows))
    if not rows:
        return 0

    gaps = [(int(rows[i]["timestamp_ns"]) - int(rows[i-1]["timestamp_ns"])) / 1e6 for i in range(1, len(rows))]
    print("first:", tw(int(rows[0]["timestamp_ns"])))
    print("last :", tw(int(rows[-1]["timestamp_ns"])))
    print("gap_ms median/p95/p99/max:", pct(gaps, .50), pct(gaps, .95), pct(gaps, .99), max(gaps) if gaps else None)
    for limit in (1000, 2000, 5000):
        bad = [g for g in gaps if g > limit]
        print(f"gaps>{limit}ms:", len(bad), "max=", max(bad) if bad else None)

    print("\nfield coverage:")
    for field in available:
        ok = sum(finite(row[field]) for row in rows)
        print(f"{field:34} {ok:6d}/{len(rows):6d} {ok/len(rows):8.3%}")
    if all(field in available for field in CORE):
        complete = sum(all(finite(row[field]) for field in CORE) for row in rows)
        print(f"CORE_ALL_PRESENT                    {complete:6d}/{len(rows):6d} {complete/len(rows):8.3%}")

    print("\nby market:")
    market_ids = sorted({int(row["market_id"]) for row in rows if row["market_id"] is not None})
    for market_id in market_ids:
        rr = [row for row in rows if row["market_id"] == market_id]
        gg = [(int(rr[i]["timestamp_ns"]) - int(rr[i-1]["timestamp_ns"])) / 1e6 for i in range(1, len(rr))]
        core_rate = None
        if all(field in available for field in CORE):
            core_rate = sum(all(finite(row[field]) for field in CORE) for row in rr) / len(rr)
        print(
            market_id,
            "rows=", len(rr),
            tw(int(rr[0]["timestamp_ns"])), "->", tw(int(rr[-1]["timestamp_ns"])),
            "maxGapMs=", max(gg) if gg else None,
            "core=", f"{core_rate:.3%}" if core_rate is not None else "NA",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
