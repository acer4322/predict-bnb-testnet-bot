from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_DB = ROOT / "data" / "public_source_snapshot_archive_v2.db"


def load_raw_public_snapshots(market_id: int, db_path: Path = ARCHIVE_DB) -> list[dict[str, Any]]:
    if not Path(db_path).exists():
        return []
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """SELECT sampled_at_ms,timestamp_ns,snapshot_json
               FROM public_source_snapshots_v2
               WHERE market_id=? ORDER BY sampled_at_ms,id""",
            (int(market_id),),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in rows:
        try:
            snap = json.loads(str(row["snapshot_json"] or "{}"))
        except Exception:
            continue
        if not isinstance(snap, dict):
            continue
        ms = int(snap.get("sampledAtMs") or row["sampled_at_ms"] or 0)
        if ms <= 0 or ms in seen:
            continue
        seen.add(ms)
        snap["sampledAtMs"] = ms
        if not snap.get("timestampNs"):
            snap["timestampNs"] = int(row["timestamp_ns"] or ms * 1_000_000)
        out.append(snap)
    return out


def assess_raw_public_snapshots(market_id: int, db_path: Path = ARCHIVE_DB) -> dict[str, Any]:
    rows = load_raw_public_snapshots(int(market_id), db_path)
    if not rows:
        return {
            "marketId": int(market_id), "rows": 0, "status": "MISSING",
            "complete": False, "reasons": ["NO_RAW_SOURCE_ARCHIVE"],
        }
    times = [int(x["sampledAtMs"]) for x in rows]
    gaps = [b - a for a, b in zip(times, times[1:]) if b >= a]
    seconds = []
    for x in rows:
        try:
            v = float(x.get("secondsLeft"))
            if math.isfinite(v):
                seconds.append(v)
        except Exception:
            pass
    first_sec = seconds[0] if seconds else None
    last_sec = seconds[-1] if seconds else None
    max_gap = max(gaps) if gaps else None
    med_gap = sorted(gaps)[len(gaps)//2] if gaps else None
    reasons: list[str] = []
    # Structural replay-quality gate only. No winner/PnL information is used.
    if len(rows) < 500:
        reasons.append("ROW_COUNT_LT_500")
    if first_sec is None or first_sec < 285.0:
        reasons.append("MISSING_OPENING_COVERAGE")
    if last_sec is None or last_sec > 15.0:
        reasons.append("MISSING_TAIL_COVERAGE")
    if max_gap is None or max_gap > 2000:
        reasons.append("SOURCE_GAP_GT_2S")
    complete = not reasons
    return {
        "marketId": int(market_id),
        "rows": len(rows),
        "firstSampledAtMs": times[0],
        "lastSampledAtMs": times[-1],
        "durationMs": times[-1] - times[0],
        "firstSecondsLeft": first_sec,
        "lastSecondsLeft": last_sec,
        "medianGapMs": med_gap,
        "maxGapMs": max_gap,
        "status": "COMPLETE_SOURCE_V2" if complete else "INCOMPLETE_SOURCE_V2",
        "complete": complete,
        "reasons": reasons,
        "boundary": "Replay-source quality only; no Target, winner, or PnL fields are consulted.",
    }
