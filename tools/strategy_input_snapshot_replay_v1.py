from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_DB = ROOT / "data" / "strategy_input_snapshot_archive_v1.db"


def load_strategy_input_snapshots(market_id: int, controller_version: str, db_path: Path = ARCHIVE_DB) -> list[dict[str, Any]]:
    if not Path(db_path).exists():
        return []
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """SELECT sampled_at_ms,timestamp_ns,consumed_at_ms,snapshot_json
               FROM strategy_input_snapshots_v1
               WHERE market_id=? AND controller_version=?
               ORDER BY consumed_at_ms,id""",
            (int(market_id), str(controller_version)),
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
        snap["_consumerConsumedAtMs"] = int(row["consumed_at_ms"])
        out.append(snap)
    return out


def assess_strategy_input_snapshots(market_id: int, controller_version: str, db_path: Path = ARCHIVE_DB) -> dict[str, Any]:
    rows = load_strategy_input_snapshots(market_id, controller_version, db_path)
    if not rows:
        return {"marketId": int(market_id), "rows": 0, "status": "MISSING", "complete": False, "reasons": ["NO_CONSUMER_ARCHIVE"]}
    sampled = [int(x["sampledAtMs"]) for x in rows]
    consumed = [int(x["_consumerConsumedAtMs"]) for x in rows]
    sample_gaps = [b-a for a,b in zip(sampled,sampled[1:]) if b>=a]
    consume_gaps = [b-a for a,b in zip(consumed,consumed[1:]) if b>=a]
    seconds: list[float] = []
    for x in rows:
        try:
            v=float(x.get("secondsLeft"))
            if math.isfinite(v): seconds.append(v)
        except Exception:
            pass
    first_sec=seconds[0] if seconds else None
    last_sec=seconds[-1] if seconds else None
    reasons: list[str] = []
    # 8784 polls around 400ms; a complete 5m market should yield roughly 650-800 unique consumed source snapshots.
    if len(rows) < 600: reasons.append("ROW_COUNT_LT_600")
    if first_sec is None or first_sec < 285.0: reasons.append("MISSING_OPENING_COVERAGE")
    if last_sec is None or last_sec > 15.0: reasons.append("MISSING_TAIL_COVERAGE")
    if not consume_gaps or max(consume_gaps) > 2000: reasons.append("CONSUMER_GAP_GT_2S")
    if not sample_gaps or max(sample_gaps) > 2500: reasons.append("SOURCE_SAMPLE_GAP_GT_2P5S")
    complete=not reasons
    # Replay fidelity is a different question from production input health.
    # This archive is written at the Strategy Brain _step() boundary, so an
    # internal/tail gap can be the actual runtime truth (the controller simply
    # consumed no snapshot there).  For deterministic trajectory replay the
    # non-negotiable requirement is that capture started at the market opening.
    replay_reasons: list[str] = []
    if first_sec is None or first_sec < 285.0:
        replay_reasons.append("MISSING_OPENING_CONSUMER_TRACE")
    replay_eligible = not replay_reasons
    med=lambda xs: sorted(xs)[len(xs)//2] if xs else None
    return {
        "marketId": int(market_id), "controllerVersion": str(controller_version), "rows": len(rows),
        "firstSampledAtMs": sampled[0], "lastSampledAtMs": sampled[-1],
        "firstConsumedAtMs": consumed[0], "lastConsumedAtMs": consumed[-1],
        "firstSecondsLeft": first_sec, "lastSecondsLeft": last_sec,
        "medianSampleGapMs": med(sample_gaps), "maxSampleGapMs": max(sample_gaps) if sample_gaps else None,
        "medianConsumerGapMs": med(consume_gaps), "maxConsumerGapMs": max(consume_gaps) if consume_gaps else None,
        "status": "COMPLETE_STRATEGY_INPUT_V1" if complete else "INCOMPLETE_STRATEGY_INPUT_V1",
        "complete": complete, "reasons": reasons,
        "replayFidelityEligible": replay_eligible, "replayFidelityReasons": replay_reasons,
        "inputHealthComplete": complete,
        "boundary": "Exact snapshots consumed by the paper Strategy Brain. replayFidelityEligible requires opening capture; inputHealthComplete separately measures full-market source/consumer health. Neither uses winner or PnL.",
    }
