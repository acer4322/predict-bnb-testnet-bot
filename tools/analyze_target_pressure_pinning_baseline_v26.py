from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import analyze_target_pressure_pinning_v25 as v25

ROOT = Path(__file__).resolve().parents[1]
REPORT_VERSION = "TARGET_PRESSURE_PINNING_BASELINE_V26"
STRESS = "STRESS_2026_08_16"
DEFAULT_DB = ROOT / "data" / "microstructure.db"
DEFAULT_STATES = ROOT / "data" / "research" / "target_controller_hazard_v21_states.csv"
DEFAULT_BURSTS = ROOT / "data" / "research" / "target_controller_hazard_v21_directional_bursts.csv"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_pressure_pinning_baseline_v26_report.json"
DEFAULT_ROWS = ROOT / "data" / "research" / "target_pressure_pinning_baseline_v26_states.csv"
HISTORY_HORIZONS_MS = (1000, 3000, 5000)
FUTURE_HORIZONS_S = (1, 3, 5)
RANGE_PIN_BPS = (0.25, 0.5, 1.0, 2.0)
FOLLOW_THROUGH_BPS = (0.10, 0.25, 0.50)
WINDOWS = {
    "PRIMARY_CLEAN": ("2026-08-16T05:45:00.877043+08:00", "2026-08-16T06:00:02.210840+08:00"),
    "SECONDARY_EXTENDED": ("2026-08-16T05:40:05.009737+08:00", "2026-08-16T06:00:02.210840+08:00"),
}


def _iso_ms(value: str) -> int:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return int(dt.timestamp() * 1000)


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _load_states(path: Path) -> list[dict[str, Any]]:
    required = {
        "market_id", "segment_id", "regime", "sample_ms", "seconds_left",
        "risk_deficit", "abs_payoff_gap",
        *[f"add_within_{h}s" for h in FUTURE_HORIZONS_S],
        *[f"repair_within_{h}s" for h in FUTURE_HORIZONS_S],
    }
    rows: list[dict[str, Any]] = []
    with path.expanduser().resolve().open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise RuntimeError("states CSV missing columns: " + ", ".join(missing))
        for raw in reader:
            if str(raw.get("regime") or "") != STRESS:
                continue
            row = dict(raw)
            row["market_id"] = _int(row.get("market_id"))
            row["segment_id"] = _int(row.get("segment_id"))
            row["sample_ms"] = _int(row.get("sample_ms"))
            row["seconds_left"] = _num(row.get("seconds_left"))
            row["risk_deficit"] = _num(row.get("risk_deficit"))
            row["abs_payoff_gap"] = _num(row.get("abs_payoff_gap"))
            for h in FUTURE_HORIZONS_S:
                row[f"add_within_{h}s"] = _int(row.get(f"add_within_{h}s"))
                row[f"repair_within_{h}s"] = _int(row.get(f"repair_within_{h}s"))
            rows.append(row)
    rows.sort(key=lambda r: (r["sample_ms"], r["market_id"], r["segment_id"]))
    return rows


def _load_snapshots(path: Path, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    cols = ", ".join(v25.SNAPSHOT_FIELDS)
    with sqlite3.connect(path.expanduser().resolve()) as conn:
        conn.row_factory = sqlite3.Row
        return [
            dict(r)
            for r in conn.execute(
                f"SELECT {cols} FROM microstructure_snapshots "
                "WHERE timestamp_ns >= ? AND timestamp_ns <= ? ORDER BY timestamp_ns",
                ((start_ms - max(HISTORY_HORIZONS_MS) - 1000) * 1_000_000, end_ms * 1_000_000),
            )
        ]


def _burst_groups(bursts: list[dict[str, Any]]) -> dict[tuple[int, int], tuple[list[int], list[dict[str, Any]]]]:
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in bursts:
        grouped[(int(row["market_id"]), int(row["segment_id"]))].append(row)
    out: dict[tuple[int, int], tuple[list[int], list[dict[str, Any]]]] = {}
    for key, rows in grouped.items():
        rows.sort(key=lambda r: (int(r["first_event_ms"]), str(r.get("burst_id"))))
        out[key] = ([int(r["first_event_ms"]) for r in rows], rows)
    return out


def _next_add_direction(
    groups: dict[tuple[int, int], tuple[list[int], list[dict[str, Any]]]],
    market_id: int,
    segment_id: int,
    sample_ms: int,
    horizon_s: int,
) -> str | None:
    bundle = groups.get((int(market_id), int(segment_id)))
    if not bundle:
        return None
    starts, rows = bundle
    pos = bisect.bisect_right(starts, int(sample_ms))
    limit = int(sample_ms) + int(horizon_s) * 1000
    while pos < len(rows) and int(rows[pos]["first_event_ms"]) <= limit:
        row = rows[pos]
        if row.get("purpose") == "ADD" and row.get("exposure_direction") in {"UP", "DOWN"}:
            return str(row["exposure_direction"])
        pos += 1
    return None


def _pressure_metrics(
    snaps: list[dict[str, Any]],
    timestamps: list[int],
    sample_ms: int,
    micro_market_id: int,
    history_ms: int,
) -> dict[str, Any]:
    # First get the raw signed history in UP coordinates. The sign of the mean
    # selects the ex-ante pressure direction; then recompute aligned metrics in
    # that selected direction. Only strict-past snapshots are used.
    raw = v25.window_metrics(snaps, timestamps, sample_ms, micro_market_id, "UP", history_ms)
    if not raw.get("usable"):
        return dict(raw)
    mean_raw = _num(raw.get("alignedDirectionScoreMean"))
    if mean_raw is None or abs(mean_raw) < 1e-12:
        direction = "NEUTRAL"
        aligned = raw
    else:
        direction = "UP" if mean_raw > 0 else "DOWN"
        aligned = v25.window_metrics(snaps, timestamps, sample_ms, micro_market_id, direction, history_ms)
    if not aligned.get("usable"):
        return dict(aligned)

    pressure_mean = _num(aligned.get("alignedDirectionScoreMean"))
    persistent = bool(aligned.get("persistentPressure"))
    spot_range = _num(aligned.get("spotRangeBps"))
    fut_range = _num(aligned.get("futuresRangeBps"))
    max_range = None
    ranges = [x for x in (spot_range, fut_range) if x is not None]
    if ranges:
        max_range = max(ranges)

    spot_ret = _num(aligned.get("alignedSpotReturnBps"))
    fut_ret = _num(aligned.get("alignedFuturesReturnBps"))
    follows = [x for x in (spot_ret, fut_ret) if x is not None]
    max_follow = max(follows) if follows else None

    result = dict(aligned)
    result.update(
        pressureDirection=direction,
        pressureMagnitude=pressure_mean,
        maxRangeBps=max_range,
        maxAlignedFollowThroughBps=max_follow,
        rangePinned={
            str(t): bool(persistent and max_range is not None and max_range <= t)
            for t in RANGE_PIN_BPS
        },
        noFollowThrough={
            str(t): bool(persistent and max_follow is not None and max_follow <= t)
            for t in FOLLOW_THROUGH_BPS
        },
    )
    return result


def _rate(rows: list[dict[str, Any]], field: str) -> float | None:
    if not rows:
        return None
    return sum(_int(r.get(field)) > 0 for r in rows) / len(rows)


def _ratio(x: float | None, y: float | None) -> float | None:
    return x / y if x is not None and y is not None and y > 0 else None


def _condition_summary(
    all_rows: list[dict[str, Any]],
    subset: list[dict[str, Any]],
    history_ms: int,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "states": len(subset),
        "prevalence": len(subset) / len(all_rows) if all_rows else None,
    }
    for future_s in FUTURE_HORIZONS_S:
        label = f"add_within_{future_s}s"
        base = _rate(all_rows, label)
        hit = _rate(subset, label)
        aligned_positive = [
            r for r in subset
            if _int(r.get(label)) > 0 and r.get(f"h{history_ms}_pressureDirection") in {"UP", "DOWN"}
        ]
        matches = 0
        directional_n = 0
        for row in aligned_positive:
            nxt = row.get(f"next_add_direction_{future_s}s")
            pressure_dir = row.get(f"h{history_ms}_pressureDirection")
            if nxt in {"UP", "DOWN"}:
                directional_n += 1
                if nxt == pressure_dir:
                    matches += 1
        out[f"addWithin{future_s}s"] = {
            "baselineRate": base,
            "conditionRate": hit,
            "liftVsBaseline": _ratio(hit, base),
            "difference": None if hit is None or base is None else hit - base,
            "directionMatchRate": matches / directional_n if directional_n else None,
            "directionMatchN": directional_n,
        }
    return out


def analyze_window(
    name: str,
    start_ms: int,
    end_ms: int,
    states: list[dict[str, Any]],
    bursts: list[dict[str, Any]],
    snaps: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    selected = [r for r in states if start_ms <= int(r["sample_ms"]) <= end_ms]
    timestamps = [_int(r.get("timestamp_ns")) for r in snaps]
    groups = _burst_groups(bursts)
    rows: list[dict[str, Any]] = []

    for state in selected:
        sample_ms = int(state["sample_ms"])
        snap, status = v25.strict_past_snapshot(snaps, timestamps, sample_ms)
        out = {
            "window": name,
            "target_market_id": state["market_id"],
            "segment_id": state["segment_id"],
            "sample_ms": sample_ms,
            "seconds_left": state.get("seconds_left"),
            "risk_deficit": state.get("risk_deficit"),
            "abs_payoff_gap": state.get("abs_payoff_gap"),
            "join_status": status,
        }
        for future_s in FUTURE_HORIZONS_S:
            out[f"add_within_{future_s}s"] = state.get(f"add_within_{future_s}s", 0)
            out[f"repair_within_{future_s}s"] = state.get(f"repair_within_{future_s}s", 0)
            out[f"next_add_direction_{future_s}s"] = _next_add_direction(
                groups, state["market_id"], state["segment_id"], sample_ms, future_s
            )
        if snap is not None:
            micro_market_id = _int(snap.get("market_id"))
            out["micro_market_id"] = micro_market_id
            out["snapshot_age_ms"] = snap.get("snapshot_age_ms")
            for history_ms in HISTORY_HORIZONS_MS:
                met = _pressure_metrics(snaps, timestamps, sample_ms, micro_market_id, history_ms)
                for key, value in met.items():
                    if isinstance(value, dict):
                        out[f"h{history_ms}_{key}"] = json.dumps(value, sort_keys=True)
                    else:
                        out[f"h{history_ms}_{key}"] = value
        rows.append(out)

    report: dict[str, Any] = {
        "start": WINDOWS[name][0],
        "end": WINDOWS[name][1],
        "fixedGridStates": len(selected),
        "joinedFresh": sum(r.get("join_status") == "OK" for r in rows),
        "history": {},
    }
    for history_ms in HISTORY_HORIZONS_MS:
        usable = [r for r in rows if r.get("join_status") == "OK" and r.get(f"h{history_ms}_usable") is True]
        persistent = [r for r in usable if bool(r.get(f"h{history_ms}_persistentPressure"))]
        h: dict[str, Any] = {
            "usableStates": len(usable),
            "baselineAddHazard": {str(f): _rate(usable, f"add_within_{f}s") for f in FUTURE_HORIZONS_S},
            "persistentPressure": _condition_summary(usable, persistent, history_ms),
            "rangePinned": {},
            "noFollowThrough": {},
        }
        for threshold in RANGE_PIN_BPS:
            key = str(threshold)
            subset = [
                r for r in usable
                if json.loads(r.get(f"h{history_ms}_rangePinned") or "{}").get(key, False)
            ]
            h["rangePinned"][key] = _condition_summary(usable, subset, history_ms)
        for threshold in FOLLOW_THROUGH_BPS:
            key = str(threshold)
            subset = [
                r for r in usable
                if json.loads(r.get(f"h{history_ms}_noFollowThrough") or "{}").get(key, False)
            ]
            h["noFollowThrough"][key] = _condition_summary(usable, subset, history_ms)
        report["history"][str(history_ms)] = h
    return report, rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        resolved.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with resolved.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fixed-grid baseline for pressure/pinning opportunity hypothesis")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--states", type=Path, default=DEFAULT_STATES)
    parser.add_argument("--bursts", type=Path, default=DEFAULT_BURSTS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--rows", type=Path, default=DEFAULT_ROWS)
    args = parser.parse_args()

    states = _load_states(args.states)
    bursts = v25.load_bursts(args.bursts)
    global_start = min(_iso_ms(v[0]) for v in WINDOWS.values())
    global_end = max(_iso_ms(v[1]) for v in WINDOWS.values())
    snaps = _load_snapshots(args.db, global_start, global_end)

    report = {
        "version": REPORT_VERSION,
        "policy": {
            "fixedGrid": "V2.1 strict-past 1-second states; no active parent/burst at sample time",
            "microJoin": "nearest strict-past microstructure snapshot, freshness <= 750ms, time-only across namespaces",
            "history": list(HISTORY_HORIZONS_MS),
            "futureAddHorizonsSeconds": list(FUTURE_HORIZONS_S),
            "persistentPressure": "abs mean direction_score >= 0.20 in selected pressure direction and >=60% snapshots strongly support that direction",
            "rangePinnedBps": list(RANGE_PIN_BPS),
            "noFollowThroughBps": list(FOLLOW_THROUGH_BPS),
            "guardrails": [
                "All pressure and price-response features use snapshots strictly before sample_ms.",
                "Future ADD labels come from V2.1 strict-future hazard labels; directional match uses the first future ADD burst only.",
                "Condition prevalence is always reported so common 1-second pinning cannot masquerade as predictive edge.",
                "No model fitting and no synthetic expected-profit score.",
            ],
        },
        "source": {
            "states": str(args.states.expanduser().resolve()),
            "bursts": str(args.bursts.expanduser().resolve()),
            "db": str(args.db.expanduser().resolve()),
            "stressStatesLoaded": len(states),
            "stressBurstsLoaded": len(bursts),
            "snapshotRowsLoaded": len(snaps),
        },
        "windows": {},
    }
    all_rows: list[dict[str, Any]] = []
    for name, (start_iso, end_iso) in WINDOWS.items():
        summary, rows = analyze_window(name, _iso_ms(start_iso), _iso_ms(end_iso), states, bursts, snaps)
        report["windows"][name] = summary
        all_rows.extend(rows)

    args.report.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    args.report.expanduser().resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(args.rows, all_rows)

    print(REPORT_VERSION)
    for name, payload in report["windows"].items():
        print(name, "states=", payload["fixedGridStates"], "fresh=", payload["joinedFresh"])
        for history_ms, h in payload["history"].items():
            p = h["persistentPressure"]
            print(" ", history_ms, "ms persistent prevalence=", p["prevalence"], "ADD5 lift=", p["addWithin5s"]["liftVsBaseline"])
            print("    noFT<=0.25bps prevalence=", h["noFollowThrough"]["0.25"]["prevalence"], "ADD5 lift=", h["noFollowThrough"]["0.25"]["addWithin5s"]["liftVsBaseline"])


if __name__ == "__main__":
    main()
