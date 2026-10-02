from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORT_VERSION = "TARGET_POLY_URGENCY_HAZARD_V1"
DEFAULT_TRANSITIONS = ROOT / "data" / "research" / "target_controller_complete_history_v2_official_transitions.csv"
DEFAULT_BURSTS = ROOT / "data" / "research" / "target_controller_complete_history_v2_official_bursts.csv"
DEFAULT_CROSS_DB = ROOT / "data" / "cross_oracle.db"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_poly_urgency_hazard_v1_report.json"
DEFAULT_STATES = ROOT / "data" / "research" / "target_poly_urgency_hazard_v1_states.csv"
DEFAULT_START = "2026-08-16T03:40:00+08:00"
DEFAULT_END = "2026-08-16T11:35:00+08:00"
HORIZONS_MS = (1000, 3000, 5000, 15000)
STRENGTH_BINS = (
    (0.00, 0.03, "0.00-0.03"),
    (0.03, 0.08, "0.03-0.08"),
    (0.08, 0.15, "0.08-0.15"),
    (0.15, 0.25, "0.15-0.25"),
    (0.25, float("inf"), ">=0.25"),
)
STATE_FIELDS = [
    "market_id", "maker_completed_ms", "poly_market_slug", "mapping_method",
    "poly_source_ms", "poly_lag_ms", "poly_up_mid", "poly_strength", "poly_favored_side",
    "risk_deficit", "abs_payoff_gap", "maker_abs_payoff_gap",
    "next_taker_burst_ms", "next_taker_delay_ms", "next_taker_effect", "next_taker_clean_side",
    "taker_within_1s", "taker_within_3s", "taker_within_5s", "taker_within_15s",
]


def _parse_ms(text: str) -> int:
    dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return int(dt.timestamp() * 1000)


def _fmt_utc(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _quantile(values: list[float], q: float) -> float | None:
    xs = sorted(float(x) for x in values if math.isfinite(float(x)))
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = min(1.0, max(0.0, q)) * (len(xs) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def _distribution(values: list[float]) -> dict[str, Any]:
    xs = [float(x) for x in values if math.isfinite(float(x))]
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs),
        "min": min(xs),
        "p10": _quantile(xs, 0.10),
        "p25": _quantile(xs, 0.25),
        "median": _quantile(xs, 0.50),
        "p75": _quantile(xs, 0.75),
        "p90": _quantile(xs, 0.90),
        "max": max(xs),
        "mean": statistics.fmean(xs),
    }


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    denom = math.sqrt(sum(x * x for x in dx) * sum(y * y for y in dy))
    if denom <= 1e-12:
        return None
    return sum(a * b for a, b in zip(dx, dy)) / denom


def _open_ro(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    db = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def _require_index(db: sqlite3.Connection, table: str, leftmost: str) -> dict[str, Any]:
    for row in db.execute(f"PRAGMA index_list({table})"):
        name = str(row[1])
        cols = [str(x[2]) for x in db.execute(f"PRAGMA index_info({name})")]
        if cols and cols[0] == leftmost:
            return {"name": name, "columns": cols}
    raise RuntimeError(f"{table}.{leftmost}: no leftmost index; refusing range scan")


def _poly_overlap(db: sqlite3.Connection, start_ms: int, end_ms: int) -> tuple[int, int] | None:
    first = db.execute(
        "SELECT source_timestamp_ms FROM polymarket_events "
        "WHERE source_timestamp_ms>=? AND source_timestamp_ms<? "
        "ORDER BY source_timestamp_ms LIMIT 1",
        (start_ms, end_ms),
    ).fetchone()
    last = db.execute(
        "SELECT source_timestamp_ms FROM polymarket_events "
        "WHERE source_timestamp_ms>=? AND source_timestamp_ms<? "
        "ORDER BY source_timestamp_ms DESC LIMIT 1",
        (start_ms, end_ms),
    ).fetchone()
    if first is None or last is None:
        return None
    return int(first[0]), int(last[0])


def _load_poly_series(
    db: sqlite3.Connection,
    start_ms: int,
    end_ms: int,
    lookback_ms: int,
) -> tuple[dict[str, tuple[list[int], list[dict[str, Any]]]], dict[str, Any]]:
    query_start = max(0, start_ms - max(0, lookback_ms))
    rows = db.execute(
        """
        SELECT id,market_slug,outcome,best_bid,best_ask,last_trade,source_timestamp_ms
          FROM polymarket_events
         WHERE source_timestamp_ms>=? AND source_timestamp_ms<?
         ORDER BY source_timestamp_ms,id
        """,
        (query_start, end_ms),
    )
    state: dict[str, dict[str, float | None]] = defaultdict(
        lambda: {
            "UP": None,
            "DOWN": None,
            "UP_BID": None,
            "UP_ASK": None,
            "DOWN_BID": None,
            "DOWN_ASK": None,
        }
    )
    series_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    event_count = 0
    for raw in rows:
        event_count += 1
        slug = str(raw["market_slug"])
        outcome = str(raw["outcome"] or "").upper()
        if outcome not in {"UP", "DOWN"}:
            continue
        bid = _finite(raw["best_bid"])
        ask = _finite(raw["best_ask"])
        if bid is not None and ask is not None and 0 <= bid <= ask <= 1:
            mid = (bid + ask) / 2.0
            state[slug][outcome] = mid
            state[slug][f"{outcome}_BID"] = bid
            state[slug][f"{outcome}_ASK"] = ask
        up_direct = state[slug]["UP"]
        down_direct = state[slug]["DOWN"]
        if up_direct is not None:
            up_mid = float(up_direct)
        elif down_direct is not None:
            up_mid = 1.0 - float(down_direct)
        else:
            continue
        ts = int(raw["source_timestamp_ms"])
        spread = None
        if state[slug]["UP_BID"] is not None and state[slug]["UP_ASK"] is not None:
            spread = float(state[slug]["UP_ASK"]) - float(state[slug]["UP_BID"])
        series_rows[slug].append({"source_ms": ts, "up_mid": up_mid, "up_spread": spread})
    out: dict[str, tuple[list[int], list[dict[str, Any]]]] = {}
    for slug, items in series_rows.items():
        dedup: list[dict[str, Any]] = []
        for item in items:
            if dedup and item["source_ms"] == dedup[-1]["source_ms"]:
                dedup[-1] = item
            else:
                dedup.append(item)
        out[slug] = ([int(x["source_ms"]) for x in dedup], dedup)
    return out, {"eventRowsRead": event_count, "marketSlugs": len(out), "queryStartMs": query_start}


def _strict_asof(
    series: dict[str, tuple[list[int], list[dict[str, Any]]]],
    slug: str,
    at_ms: int,
) -> dict[str, Any] | None:
    payload = series.get(slug)
    if payload is None:
        return None
    times, rows = payload
    idx = bisect.bisect_left(times, int(at_ms)) - 1
    return None if idx < 0 else rows[idx]


def _load_csv(path: Path) -> list[dict[str, str]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    with resolved.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _dedupe_grid(rows: list[dict[str, str]], grid_ms: int) -> list[dict[str, str]]:
    if grid_ms <= 0:
        return sorted(rows, key=lambda r: (int(r["maker_completed_ms"]), int(r["market_id"])))
    selected: dict[tuple[int, int], dict[str, str]] = {}
    for row in rows:
        key = (int(row["market_id"]), int(row["maker_completed_ms"]) // grid_ms)
        prior = selected.get(key)
        if prior is None or int(row["maker_completed_ms"]) > int(prior["maker_completed_ms"]):
            selected[key] = row
    return sorted(selected.values(), key=lambda r: (int(r["maker_completed_ms"]), int(r["market_id"])))


def _burst_index(rows: list[dict[str, str]]) -> dict[int, tuple[list[int], list[dict[str, str]]]]:
    grouped: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["market_id"])].append(row)
    out = {}
    for mid, items in grouped.items():
        items.sort(key=lambda r: int(r["first_event_ms"]))
        out[mid] = ([int(r["first_event_ms"]) for r in items], items)
    return out


def _next_burst(
    index: dict[int, tuple[list[int], list[dict[str, str]]]],
    market_id: int,
    at_ms: int,
) -> dict[str, str] | None:
    payload = index.get(int(market_id))
    if payload is None:
        return None
    times, rows = payload
    idx = bisect.bisect_right(times, int(at_ms))
    return None if idx >= len(rows) else rows[idx]


def _slug_mapping(db: sqlite3.Connection, market_ids: list[int]) -> dict[int, str]:
    tables = {str(r[0]) for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "poly_chop_guard_markets" not in tables:
        return {}
    cols = {str(r[1]) for r in db.execute("PRAGMA table_info(poly_chop_guard_markets)")}
    if not {"market_id", "poly_market_slug"} <= cols:
        return {}
    out: dict[int, str] = {}
    for market_id in sorted(set(market_ids)):
        row = db.execute(
            "SELECT poly_market_slug FROM poly_chop_guard_markets WHERE market_id=? LIMIT 1",
            (market_id,),
        ).fetchone()
        if row is not None and row[0]:
            out[market_id] = str(row[0])
    return out


def _derived_slug(at_ms: int) -> str:
    bucket_sec = (int(at_ms) // 300_000) * 300
    return f"btc-updown-5m-{bucket_sec}"


def _rate(rows: list[dict[str, Any]], key: str) -> float | None:
    if not rows:
        return None
    return sum(int(r[key]) for r in rows) / len(rows)


def _surface(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for lo, hi, label in STRENGTH_BINS:
        bucket = [r for r in rows if float(r["poly_strength"]) >= lo and float(r["poly_strength"]) < hi]
        out.append({
            "bin": label,
            "n": len(bucket),
            "strengthMedian": _quantile([float(r["poly_strength"]) for r in bucket], 0.5),
            "polyUpMidMedian": _quantile([float(r["poly_up_mid"]) for r in bucket], 0.5),
            "takerWithin1sRate": _rate(bucket, "taker_within_1s"),
            "takerWithin3sRate": _rate(bucket, "taker_within_3s"),
            "takerWithin5sRate": _rate(bucket, "taker_within_5s"),
            "takerWithin15sRate": _rate(bucket, "taker_within_15s"),
            "nextEffectMixWithin5s": dict(Counter(
                str(r["next_taker_effect"] or "UNKNOWN")
                for r in bucket if int(r["taker_within_5s"])
            )),
        })
    return out


def _quantile_surface(rows: list[dict[str, Any]], bins: int = 5) -> list[dict[str, Any]]:
    ordered = sorted(
        rows,
        key=lambda r: (float(r["poly_strength"]), int(r["maker_completed_ms"]), int(r["market_id"])),
    )
    if not ordered:
        return []
    out = []
    for i in range(bins):
        lo = (len(ordered) * i) // bins
        hi = (len(ordered) * (i + 1)) // bins
        bucket = ordered[lo:hi]
        if not bucket:
            continue
        out.append({
            "quantile": i + 1,
            "n": len(bucket),
            "strengthMin": min(float(r["poly_strength"]) for r in bucket),
            "strengthMedian": _quantile([float(r["poly_strength"]) for r in bucket], 0.5),
            "strengthMax": max(float(r["poly_strength"]) for r in bucket),
            "takerWithin1sRate": _rate(bucket, "taker_within_1s"),
            "takerWithin3sRate": _rate(bucket, "taker_within_3s"),
            "takerWithin5sRate": _rate(bucket, "taker_within_5s"),
            "takerWithin15sRate": _rate(bucket, "taker_within_15s"),
        })
    return out


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp = resolved.with_suffix(resolved.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=STATE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(resolved)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp = resolved.with_suffix(resolved.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(resolved)


def run(
    transitions_path: Path,
    bursts_path: Path,
    cross_db_path: Path,
    start_ms: int,
    end_ms: int,
    max_poly_lag_ms: int,
    grid_ms: int,
    lookback_ms: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    transitions = _load_csv(transitions_path)
    bursts = _load_csv(bursts_path)

    with _open_ro(cross_db_path) as db:
        index_audit = _require_index(db, "polymarket_events", "source_timestamp_ms")
        overlap = _poly_overlap(db, start_ms, end_ms)
        if overlap is None:
            return [], {
                "status": "NO_POLY_ROWS",
                "requestedStartMs": start_ms,
                "requestedEndMs": end_ms,
                "polyIndex": index_audit,
            }
        poly_first_ms, poly_last_ms = overlap
        analysis_start = max(start_ms, poly_first_ms)
        analysis_end = min(end_ms, poly_last_ms + 1)

        transition_window = [
            r for r in transitions
            if r.get("source_version", "OFFICIAL").upper() == "OFFICIAL"
            and analysis_start <= int(r["maker_completed_ms"]) < analysis_end
        ]
        states_before_grid = len(transition_window)
        transition_window = _dedupe_grid(transition_window, grid_ms)
        market_ids = sorted({int(r["market_id"]) for r in transition_window})
        mapping = _slug_mapping(db, market_ids)
        poly_series, poly_audit = _load_poly_series(db, analysis_start, analysis_end, lookback_ms)

    burst_rows = [
        r for r in bursts
        if r.get("source_version", "OFFICIAL").upper() == "OFFICIAL"
        and int(r["first_event_ms"]) >= analysis_start
    ]
    bindex = _burst_index(burst_rows)

    states: list[dict[str, Any]] = []
    missing_poly = stale_poly = 0
    mapping_counts = Counter()
    for row in transition_window:
        market_id = int(row["market_id"])
        at_ms = int(row["maker_completed_ms"])
        slug = mapping.get(market_id)
        mapping_method = "TABLE"
        if not slug:
            slug = _derived_slug(at_ms)
            mapping_method = "DERIVED_TIME_BUCKET"
        mapping_counts[mapping_method] += 1
        poly = _strict_asof(poly_series, slug, at_ms)
        if poly is None:
            missing_poly += 1
            continue
        lag = at_ms - int(poly["source_ms"])
        if lag < 0 or lag > max_poly_lag_ms:
            stale_poly += 1
            continue
        up_mid = float(poly["up_mid"])
        if not 0 <= up_mid <= 1:
            continue
        nxt = _next_burst(bindex, market_id, at_ms)
        next_ms = int(nxt["first_event_ms"]) if nxt is not None else None
        delay = next_ms - at_ms if next_ms is not None else None
        if delay is not None and delay <= 0:
            raise AssertionError("next Taker burst must start strictly after Maker state")
        payload: dict[str, Any] = {
            "market_id": market_id,
            "maker_completed_ms": at_ms,
            "poly_market_slug": slug,
            "mapping_method": mapping_method,
            "poly_source_ms": int(poly["source_ms"]),
            "poly_lag_ms": lag,
            "poly_up_mid": up_mid,
            "poly_strength": abs(up_mid - 0.5),
            "poly_favored_side": "UP" if up_mid > 0.5 else ("DOWN" if up_mid < 0.5 else "EVEN"),
            "risk_deficit": _finite(row.get("risk_deficit")),
            "abs_payoff_gap": _finite(row.get("abs_payoff_gap")),
            "maker_abs_payoff_gap": _finite(row.get("maker_abs_payoff_gap")),
            "next_taker_burst_ms": next_ms if next_ms is not None else "",
            "next_taker_delay_ms": delay if delay is not None else "",
            "next_taker_effect": str(nxt.get("portfolio_effect") or "") if nxt else "",
            "next_taker_clean_side": str(nxt.get("clean_side") or "") if nxt else "",
        }
        for horizon in HORIZONS_MS:
            payload[f"taker_within_{horizon // 1000}s"] = int(delay is not None and 0 < delay <= horizon)
        states.append(payload)

    strengths = [float(r["poly_strength"]) for r in states]
    correlations = {}
    for horizon in HORIZONS_MS:
        key = f"taker_within_{horizon // 1000}s"
        correlations[key] = _pearson(strengths, [float(r[key]) for r in states])

    audit = {
        "status": "OK",
        "requestedStartMs": start_ms,
        "requestedEndMs": end_ms,
        "polyFirstMs": poly_first_ms,
        "polyLastMs": poly_last_ms,
        "polyFirstUtc": _fmt_utc(poly_first_ms),
        "polyLastUtc": _fmt_utc(poly_last_ms),
        "analysisStartMs": analysis_start,
        "analysisEndMs": analysis_end,
        "analysisStartUtc": _fmt_utc(analysis_start),
        "analysisEndUtcExclusive": _fmt_utc(analysis_end),
        "polyIndex": index_audit,
        "polyAudit": poly_audit,
        "makerStatesInRawOverlapBeforeGrid": states_before_grid,
        "makerStatesAfterGrid": len(transition_window),
        "makerStatesMatchedStrictPastPoly": len(states),
        "missingPolyState": missing_poly,
        "stalePolyState": stale_poly,
        "maxPolyLagMs": max_poly_lag_ms,
        "gridMs": grid_ms,
        "marketCount": len({int(r["market_id"]) for r in states}),
        "mappingMethods": dict(mapping_counts),
        "polyLagMs": _distribution([float(r["poly_lag_ms"]) for r in states]),
        "polyStrength": _distribution(strengths),
        "hazardCorrelations": correlations,
        "hazardByFixedStrength": _surface(states),
        "hazardByStrengthQuantile": _quantile_surface(states),
    }
    return states, audit


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test strict-past Polymarket price strength as a Target Maker->Taker urgency input."
    )
    parser.add_argument("--transitions", type=Path, default=DEFAULT_TRANSITIONS)
    parser.add_argument("--bursts", type=Path, default=DEFAULT_BURSTS)
    parser.add_argument("--cross-db", type=Path, default=DEFAULT_CROSS_DB)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--states", type=Path, default=DEFAULT_STATES)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--max-poly-lag-ms", type=int, default=2000)
    parser.add_argument("--grid-ms", type=int, default=1000)
    parser.add_argument("--lookback-ms", type=int, default=30000)
    args = parser.parse_args()

    start_ms, end_ms = _parse_ms(args.start), _parse_ms(args.end)
    if end_ms <= start_ms:
        raise SystemExit("--end must be after --start")
    states, audit = run(
        args.transitions,
        args.bursts,
        args.cross_db,
        start_ms,
        end_ms,
        max(0, args.max_poly_lag_ms),
        max(0, args.grid_ms),
        max(0, args.lookback_ms),
    )
    _write_csv(args.states, states)
    report = {
        "reportVersion": REPORT_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "paperResearchOnly": True,
        "noModelFit": True,
        "hypothesis": "Poly price strength may raise Target Taker urgency / shrink inventory tolerance.",
        "strictPast": "Poly source_timestamp_ms must be strictly earlier than Maker completed timestamp.",
        "targetHazard": "Next Taker burst onset after each Maker state, ignoring intervening Maker parents.",
        "audit": audit,
        "outputs": {"report": str(args.report), "statesCsv": str(args.states)},
    }
    _write_json(args.report, report)
    print(REPORT_VERSION)
    print(
        f"status={audit.get('status')} poly={audit.get('polyFirstUtc')} -> {audit.get('polyLastUtc')} "
        f"maker_states={audit.get('makerStatesAfterGrid', 0):,} "
        f"matched={audit.get('makerStatesMatchedStrictPastPoly', 0):,}"
    )
    if audit.get("status") == "OK":
        print(f"markets={audit['marketCount']:,} max_poly_lag_ms={audit['maxPolyLagMs']}")
        for row in audit["hazardByFixedStrength"]:
            print(
                f"  strength {row['bin']:>9} n={row['n']:>5} "
                f"T5={row['takerWithin5sRate']} T15={row['takerWithin15sRate']}"
            )
    print(f"report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
