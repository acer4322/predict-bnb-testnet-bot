from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
TAIPEI = ZoneInfo("Asia/Taipei")
REPORT_VERSION = "POLY_SLUG_WINDOW_INTEGRITY_V1"
SLUG_RE = re.compile(r"^btc-updown-5m-(\d+)$")


def _parse_ms(text: str) -> int:
    dt = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TAIPEI)
    return int(dt.timestamp() * 1000)


def _fmt(ms: int | None, tz=TAIPEI) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(tz).isoformat()


def _slug_window(slug: str) -> tuple[int, int] | None:
    match = SLUG_RE.fullmatch(str(slug or "").strip())
    if match is None:
        return None
    start_ms = int(match.group(1)) * 1000
    return start_ms, start_ms + 300_000


def _expected_slugs(start_ms: int, end_ms: int) -> list[str]:
    if end_ms <= start_ms:
        return []
    first_sec = (start_ms // 1000 // 300) * 300
    last_sec = ((end_ms - 1) // 1000 // 300) * 300
    return [f"btc-updown-5m-{sec}" for sec in range(first_sec, last_sec + 1, 300)]


def _open_ro(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    db = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def _index_columns(db: sqlite3.Connection, table: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in db.execute(f'PRAGMA index_list("{table}")'):
        name = str(row["name"])
        out[name] = [str(info["name"]) for info in db.execute(f'PRAGMA index_info("{name}")')]
    return out


def _require_source_index(db: sqlite3.Connection) -> dict[str, Any]:
    indexes = _index_columns(db, "polymarket_events")
    for name, columns in indexes.items():
        if columns and columns[0] == "source_timestamp_ms":
            return {"name": name, "columns": columns}
    raise RuntimeError("polymarket_events requires an index whose leftmost column is source_timestamp_ms")


def _load_events(
    db: sqlite3.Connection, start_ms: int, end_ms: int, max_rows: int
) -> tuple[list[dict[str, Any]], bool]:
    sql = """
        SELECT id, market_slug, condition_id, token_id, outcome, event_type,
               best_bid, best_ask, last_trade, source_timestamp_ms
          FROM polymarket_events
         WHERE source_timestamp_ms >= ? AND source_timestamp_ms < ?
         ORDER BY source_timestamp_ms, id
    """
    rows: list[dict[str, Any]] = []
    truncated = False
    cur = db.execute(sql, (start_ms, end_ms))
    for raw in cur:
        if len(rows) >= max_rows:
            truncated = True
            break
        row = dict(raw)
        row["source_timestamp_ms"] = int(row["source_timestamp_ms"])
        rows.append(row)
    return rows, truncated


def _market_point_lookup(db: sqlite3.Connection, slugs: list[str]) -> dict[str, dict[str, Any]]:
    tables = {str(row["name"]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "polymarket_markets" not in tables:
        return {}
    cols = {str(row["name"]) for row in db.execute("PRAGMA table_info(polymarket_markets)")}
    wanted = ["slug", "market_id", "window_start_ms", "window_end_ms", "discovered_at_ms"]
    selected = [c for c in wanted if c in cols]
    if "slug" not in selected:
        return {}
    out: dict[str, dict[str, Any]] = {}
    sql = f"SELECT {','.join(selected)} FROM polymarket_markets WHERE slug=?"
    for slug in slugs:
        row = db.execute(sql, (slug,)).fetchone()
        if row is not None:
            out[slug] = dict(row)
    return out


def _load_feed_gaps(db: sqlite3.Connection, start_ms: int, end_ms: int, limit: int = 500) -> list[dict[str, Any]]:
    tables = {str(row["name"]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "cross_oracle_feed_gaps" not in tables:
        return []
    indexes = _index_columns(db, "cross_oracle_feed_gaps")
    if not any(cols and cols[0] == "opened_at_ms" for cols in indexes.values()):
        return []
    rows = db.execute(
        """
        SELECT opened_at_ms, recovered_at_ms, duration_ms, reason, detail, market_slug, target_slug
          FROM cross_oracle_feed_gaps
         WHERE opened_at_ms >= ? AND opened_at_ms < ?
         ORDER BY opened_at_ms
         LIMIT ?
        """,
        (start_ms, end_ms, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def _audit_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unparsable = 0
    for row in rows:
        slug = str(row.get("market_slug") or "")
        grouped[slug].append(row)
        if _slug_window(slug) is None:
            unparsable += 1

    slug_rows: list[dict[str, Any]] = []
    total_inside = total_before = total_after = 0
    clean_first: int | None = None
    clean_last: int | None = None
    for slug, items in sorted(grouped.items(), key=lambda kv: min(int(r["source_timestamp_ms"]) for r in kv[1])):
        times = [int(r["source_timestamp_ms"]) for r in items]
        parsed = _slug_window(slug)
        outcomes = Counter(str(r.get("outcome") or "UNKNOWN").upper() for r in items)
        event_types = Counter(str(r.get("event_type") or "UNKNOWN") for r in items)
        if parsed is None:
            slug_rows.append({
                "slug": slug,
                "parseable": False,
                "eventCount": len(items),
                "firstEventMs": min(times),
                "lastEventMs": max(times),
                "firstEventTaipei": _fmt(min(times)),
                "lastEventTaipei": _fmt(max(times)),
                "outcomes": dict(outcomes),
                "eventTypes": dict(event_types),
            })
            continue

        window_start, window_end = parsed
        inside = [t for t in times if window_start <= t < window_end]
        before = [t for t in times if t < window_start]
        after = [t for t in times if t >= window_end]
        total_inside += len(inside)
        total_before += len(before)
        total_after += len(after)
        if inside:
            clean_first = min(clean_first, min(inside)) if clean_first is not None else min(inside)
            clean_last = max(clean_last, max(inside)) if clean_last is not None else max(inside)

        slug_rows.append({
            "slug": slug,
            "parseable": True,
            "windowStartMs": window_start,
            "windowEndMsExclusive": window_end,
            "windowStartTaipei": _fmt(window_start),
            "windowEndTaipeiExclusive": _fmt(window_end),
            "eventCount": len(items),
            "firstEventMs": min(times),
            "lastEventMs": max(times),
            "firstEventTaipei": _fmt(min(times)),
            "lastEventTaipei": _fmt(max(times)),
            "insideWindowRows": len(inside),
            "beforeWindowRows": len(before),
            "afterWindowRows": len(after),
            "insideWindowRatio": len(inside) / len(items) if items else None,
            "firstInsideMs": min(inside) if inside else None,
            "lastInsideMs": max(inside) if inside else None,
            "firstLateMs": min(after) if after else None,
            "firstLateTaipei": _fmt(min(after)) if after else None,
            "maxLateMs": max((t - window_end for t in after), default=0),
            "outcomes": dict(outcomes),
            "eventTypes": dict(event_types),
        })

    return slug_rows, {
        "observedSlugCount": len(grouped),
        "parseableSlugCount": sum(bool(row.get("parseable")) for row in slug_rows),
        "unparseableEventRows": unparsable,
        "insideWindowRows": total_inside,
        "beforeWindowRows": total_before,
        "afterWindowRows": total_after,
        "cleanUsableSlugCount": sum(int(row.get("insideWindowRows") or 0) > 0 for row in slug_rows),
        "cleanFirstMs": clean_first,
        "cleanLastMs": clean_last,
        "cleanFirstTaipei": _fmt(clean_first),
        "cleanLastTaipei": _fmt(clean_last),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit whether historical Poly events stayed inside the 5m window encoded by market_slug.")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "cross_oracle.db")
    parser.add_argument("--report", type=Path, default=ROOT / "data" / "research" / "poly_slug_window_integrity_v1_report.json")
    parser.add_argument("--start", default="2026-08-16T03:40:00+08:00")
    parser.add_argument("--end", default="2026-08-16T11:35:00+08:00")
    parser.add_argument("--max-rows", type=int, default=200_000)
    args = parser.parse_args()

    start_ms = _parse_ms(args.start)
    end_ms = _parse_ms(args.end)
    if end_ms <= start_ms:
        raise SystemExit("--end must be later than --start")
    if args.max_rows <= 0:
        raise SystemExit("--max-rows must be > 0")

    db = _open_ro(args.db)
    try:
        poly_index = _require_source_index(db)
        rows, truncated = _load_events(db, start_ms, end_ms, args.max_rows)
        if not rows:
            report = {
                "reportVersion": REPORT_VERSION,
                "status": "NO_ROWS",
                "requestedWindow": {"start": args.start, "end": args.end},
                "polyIndex": poly_index,
                "guardrails": {"readOnly": True, "queryOnly": True, "indexedRangeQuery": True, "maxRows": args.max_rows},
            }
        else:
            slug_rows, summary = _audit_rows(rows)
            first_ms = min(int(r["source_timestamp_ms"]) for r in rows)
            last_ms = max(int(r["source_timestamp_ms"]) for r in rows)
            expected_envelope = _expected_slugs(first_ms, last_ms + 1)
            expected_requested = _expected_slugs(start_ms, end_ms)
            discovered = _market_point_lookup(db, expected_envelope)
            observed_slugs = {str(r.get("market_slug") or "") for r in rows}
            discovered_no_events = [slug for slug in expected_envelope if slug in discovered and slug not in observed_slugs]
            absent_from_discovery = [slug for slug in expected_envelope if slug not in discovered]
            feed_gaps = _load_feed_gaps(db, first_ms, last_ms + 1)

            if len(discovered) > len(observed_slugs):
                diagnosis = "MARKETS_DISCOVERED_BUT_EVENT_SLUGS_MISSING_OR_NOT_ROTATING"
            elif len(expected_envelope) > len(discovered):
                diagnosis = "MARKET_DISCOVERY_INCOMPLETE_OR_COLLECTOR_STARTED_LATE"
            elif summary["afterWindowRows"] or summary["beforeWindowRows"]:
                diagnosis = "EVENTS_PRESENT_OUTSIDE_SLUG_WINDOW"
            else:
                diagnosis = "NO_ROTATION_ANOMALY_DETECTED"

            report = {
                "reportVersion": REPORT_VERSION,
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "status": "TRUNCATED" if truncated else "OK",
                "requestedWindow": {
                    "start": args.start,
                    "end": args.end,
                    "startEpochMs": start_ms,
                    "endEpochMs": end_ms,
                    "expectedFiveMinuteSlugs": len(expected_requested),
                },
                "observedEnvelope": {
                    "firstEventMs": first_ms,
                    "lastEventMs": last_ms,
                    "firstEventTaipei": _fmt(first_ms),
                    "lastEventTaipei": _fmt(last_ms),
                    "expectedFiveMinuteSlugs": len(expected_envelope),
                    "expectedSlugs": expected_envelope,
                },
                "polyIndex": poly_index,
                "eventRowsRead": len(rows),
                "summary": {
                    **summary,
                    "observedExpectedSlugCoverage": len(observed_slugs.intersection(expected_envelope)) / len(expected_envelope) if expected_envelope else None,
                    "discoveredExpectedSlugCount": len(discovered),
                    "discoveredExpectedSlugCoverage": len(discovered) / len(expected_envelope) if expected_envelope else None,
                    "discoveredExpectedSlugsWithoutEvents": discovered_no_events,
                    "expectedSlugsAbsentFromMarketTable": absent_from_discovery,
                    "diagnosis": diagnosis,
                },
                "slugs": slug_rows,
                "expectedMarketPointLookups": [
                    {
                        "slug": slug,
                        "discovered": slug in discovered,
                        "market": discovered.get(slug),
                        "hasEvents": slug in observed_slugs,
                    }
                    for slug in expected_envelope
                ],
                "feedGapsInObservedEnvelope": feed_gaps,
                "guardrails": {
                    "readOnly": True,
                    "queryOnly": True,
                    "polyEventsUsesTimestampLeftmostIndex": True,
                    "marketDiscoveryUsesSlugPrimaryKeyPointLookups": True,
                    "feedGapsUsesOpenedAtIndexWhenAvailable": True,
                    "noDatabaseCopies": True,
                    "noWrites": True,
                    "maxRows": args.max_rows,
                },
                "paperResearchOnly": True,
                "noModelFit": True,
                "recommendation": "Only rows with slug_window_start <= source_timestamp_ms < slug_window_end are eligible for strict-past controller joins.",
            }

        out = args.report.expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(out)
        print(REPORT_VERSION)
        if report.get("status") == "NO_ROWS":
            print("no polymarket_events rows in requested window")
        else:
            s = report["summary"]
            print(
                f"rows={report['eventRowsRead']:,} observed_slugs={s['observedSlugCount']} "
                f"expected_in_observed_envelope={report['observedEnvelope']['expectedFiveMinuteSlugs']} "
                f"clean_rows={s['insideWindowRows']:,} stale_after={s['afterWindowRows']:,} "
                f"discovered_expected={s['discoveredExpectedSlugCount']}"
            )
            print(f"diagnosis={s['diagnosis']}")
        print(f"report: {args.report}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
