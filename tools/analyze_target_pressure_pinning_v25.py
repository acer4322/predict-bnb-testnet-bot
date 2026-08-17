from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORT_VERSION = "TARGET_PRESSURE_PINNING_V25"
STRESS = "STRESS_2026_08_16"
DEFAULT_DB = ROOT / "data" / "microstructure.db"
DEFAULT_BURSTS = ROOT / "data" / "research" / "target_controller_hazard_v21_directional_bursts.csv"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_pressure_pinning_v25_report.json"
DEFAULT_ROWS = ROOT / "data" / "research" / "target_pressure_pinning_v25_actions.csv"
FRESHNESS_MS = 750
DIRECTION_THRESHOLD = 0.20
HORIZONS_MS = (1000, 3000, 5000)
PIN_RANGE_BPS = (0.5, 1.0, 2.0)
WINDOWS = {
    "PRIMARY_CLEAN": ("2026-08-16T05:45:00.877043+08:00", "2026-08-16T06:00:02.210840+08:00"),
    "SECONDARY_EXTENDED": ("2026-08-16T05:40:05.009737+08:00", "2026-08-16T06:00:02.210840+08:00"),
}
SNAPSHOT_FIELDS = (
    "timestamp_ns", "market_id", "spot_price", "futures_price",
    "spot_queue_imbalance", "futures_queue_imbalance",
    "spot_taker_imbalance_250ms", "futures_taker_imbalance_250ms",
    "spot_taker_imbalance_1s", "futures_taker_imbalance_1s",
    "prediction_up_mid", "direction_score",
)


def _iso_ms(value: str) -> int:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return int(dt.timestamp() * 1000)


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _sign(direction: str) -> int:
    return 1 if direction == "UP" else -1 if direction == "DOWN" else 0


def _opposite(direction: str) -> str:
    return "DOWN" if direction == "UP" else "UP" if direction == "DOWN" else "UNKNOWN"


def exposure_direction(row: dict[str, Any]) -> str:
    side = str(row.get("side") or "").upper()
    bids = _int(row.get("bid_parent_count"))
    asks = _int(row.get("ask_parent_count"))
    if side not in {"UP", "DOWN"}:
        return "UNKNOWN"
    if bids > 0 and asks == 0:
        return side
    if asks > 0 and bids == 0:
        return _opposite(side)
    return "UNKNOWN"


def _distribution(values: list[float]) -> dict[str, Any]:
    xs = sorted(x for x in values if math.isfinite(x))
    if not xs:
        return {"n": 0}
    def q(p: float) -> float:
        if len(xs) == 1:
            return xs[0]
        pos = p * (len(xs) - 1)
        lo, hi = math.floor(pos), math.ceil(pos)
        if lo == hi:
            return xs[lo]
        f = pos - lo
        return xs[lo] * (1 - f) + xs[hi] * f
    return {"n": len(xs), "min": xs[0], "p25": q(.25), "median": q(.5), "p75": q(.75), "max": xs[-1], "mean": statistics.fmean(xs)}


def load_bursts(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.expanduser().resolve().open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            if str(raw.get("regime") or "") != STRESS:
                continue
            row = dict(raw)
            row["market_id"] = _int(row.get("market_id"))
            row["segment_id"] = _int(row.get("segment_id"))
            row["first_event_ms"] = _int(row.get("first_event_ms"))
            row["last_event_ms"] = _int(row.get("last_event_ms"))
            row["purpose"] = str(row.get("purpose") or "").upper()
            row["exposure_direction"] = exposure_direction(row)
            rows.append(row)
    rows.sort(key=lambda r: (r["market_id"], r["segment_id"], r["first_event_ms"], str(r.get("burst_id"))))
    return rows


def load_snapshots(path: Path, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    cols = ", ".join(SNAPSHOT_FIELDS)
    with sqlite3.connect(path.expanduser().resolve()) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            f"SELECT {cols} FROM microstructure_snapshots WHERE timestamp_ns >= ? AND timestamp_ns <= ? ORDER BY timestamp_ns",
            ((start_ms - max(HORIZONS_MS) - 1000) * 1_000_000, end_ms * 1_000_000),
        )]


def strict_past_snapshot(rows: list[dict[str, Any]], timestamps: list[int], action_ms: int) -> tuple[dict[str, Any] | None, str]:
    action_ns = action_ms * 1_000_000
    pos = bisect.bisect_left(timestamps, action_ns) - 1
    if pos < 0:
        return None, "NO_STRICT_PAST"
    row = rows[pos]
    age_ms = (action_ns - timestamps[pos]) / 1_000_000.0
    if age_ms > FRESHNESS_MS:
        return None, "STALE"
    out = dict(row)
    out["snapshot_age_ms"] = age_ms
    return out, "OK"


def window_metrics(rows: list[dict[str, Any]], timestamps: list[int], action_ms: int, micro_market_id: int, direction: str, horizon_ms: int) -> dict[str, Any]:
    sign = _sign(direction)
    if not sign:
        return {"usable": False, "reason": "UNKNOWN_DIRECTION"}
    action_ns = action_ms * 1_000_000
    start_ns = (action_ms - horizon_ms) * 1_000_000
    lo = bisect.bisect_left(timestamps, start_ns)
    hi = bisect.bisect_left(timestamps, action_ns)
    xs = [r for r in rows[lo:hi] if _int(r.get("market_id")) == micro_market_id]
    if len(xs) < 2:
        return {"usable": False, "reason": "TOO_FEW_SNAPSHOTS", "n": len(xs)}
    first_ns, last_ns = _int(xs[0].get("timestamp_ns")), _int(xs[-1].get("timestamp_ns"))
    coverage = max(0.0, (last_ns - first_ns) / (horizon_ms * 1_000_000.0))
    if coverage < 0.65:
        return {"usable": False, "reason": "LOW_HISTORY_COVERAGE", "n": len(xs), "coverage": coverage}

    scores = [_num(r.get("direction_score")) for r in xs]
    scores = [v * sign for v in scores if v is not None]
    spot_qi = [_num(r.get("spot_queue_imbalance")) for r in xs]
    spot_qi = [v * sign for v in spot_qi if v is not None]
    fut_qi = [_num(r.get("futures_queue_imbalance")) for r in xs]
    fut_qi = [v * sign for v in fut_qi if v is not None]

    def price_stats(field: str) -> tuple[float | None, float | None]:
        vals = [_num(r.get(field)) for r in xs]
        vals = [v for v in vals if v is not None and v > 0]
        if len(vals) < 2:
            return None, None
        ret = (vals[-1] / vals[0] - 1.0) * 10000.0 * sign
        rng = (max(vals) - min(vals)) / vals[0] * 10000.0
        return ret, rng

    spot_ret, spot_range = price_stats("spot_price")
    fut_ret, fut_range = price_stats("futures_price")
    mean_score = statistics.fmean(scores) if scores else None
    support_share = sum(v >= DIRECTION_THRESHOLD for v in scores) / len(scores) if scores else None
    reverse_share = sum(v <= -DIRECTION_THRESHOLD for v in scores) / len(scores) if scores else None
    persistent = bool(mean_score is not None and support_share is not None and mean_score >= DIRECTION_THRESHOLD and support_share >= 0.60)
    max_range = max(v for v in (spot_range, fut_range) if v is not None) if spot_range is not None or fut_range is not None else None
    pinned = {str(t): bool(persistent and max_range is not None and max_range <= t) for t in PIN_RANGE_BPS}
    return {
        "usable": True, "n": len(xs), "coverage": coverage,
        "alignedDirectionScoreMean": mean_score,
        "alignedDirectionScoreMedian": statistics.median(scores) if scores else None,
        "strongSupportShare": support_share, "strongReverseShare": reverse_share,
        "alignedSpotQIMean": statistics.fmean(spot_qi) if spot_qi else None,
        "alignedFuturesQIMean": statistics.fmean(fut_qi) if fut_qi else None,
        "alignedSpotReturnBps": spot_ret, "alignedFuturesReturnBps": fut_ret,
        "spotRangeBps": spot_range, "futuresRangeBps": fut_range,
        "persistentPressure": persistent, "persistentPressurePinnedByRangeBps": pinned,
    }


def strict_triplets(rows: list[dict[str, Any]], start_ms: int, end_ms: int, limit_ms: int = 5000) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    groups: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["market_id"], row["segment_id"])].append(row)
    out = []
    for group in groups.values():
        group.sort(key=lambda r: r["first_event_ms"])
        for i in range(len(group) - 2):
            a, b, c = group[i:i+3]
            if not all(start_ms <= r["first_event_ms"] <= end_ms for r in (a, b, c)):
                continue
            da, db, dc = a["exposure_direction"], b["exposure_direction"], c["exposure_direction"]
            ab = b["first_event_ms"] - a["last_event_ms"]
            bc = c["first_event_ms"] - b["last_event_ms"]
            if a["purpose"] == "ADD" and b["purpose"] == "REPAIR" and c["purpose"] == "ADD" and da in {"UP", "DOWN"} and db == _opposite(da) and dc == da and 0 <= ab <= limit_ms and 0 <= bc <= limit_ms:
                out.append((a, b, c))
    return out


def analyze_window(name: str, start_ms: int, end_ms: int, bursts: list[dict[str, Any]], snaps: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    timestamps = [_int(r.get("timestamp_ns")) for r in snaps]
    actions = [r for r in bursts if start_ms <= r["first_event_ms"] <= end_ms and r["exposure_direction"] in {"UP", "DOWN"}]
    action_rows: list[dict[str, Any]] = []
    for row in actions:
        snap, status = strict_past_snapshot(snaps, timestamps, row["first_event_ms"])
        out = {"window": name, "target_market_id": row["market_id"], "burst_id": row.get("burst_id"), "purpose": row["purpose"], "direction": row["exposure_direction"], "first_event_ms": row["first_event_ms"], "join_status": status}
        if snap is not None:
            out["micro_market_id"] = _int(snap.get("market_id"))
            out["snapshot_age_ms"] = snap.get("snapshot_age_ms")
            for h in HORIZONS_MS:
                metrics = window_metrics(snaps, timestamps, row["first_event_ms"], out["micro_market_id"], row["exposure_direction"], h)
                for k, v in metrics.items():
                    out[f"h{h}_{k}"] = json.dumps(v, sort_keys=True) if isinstance(v, dict) else v
        action_rows.append(out)

    summary_actions: dict[str, Any] = {}
    for purpose in ("ADD", "REPAIR"):
        subset = [r for r in action_rows if r["purpose"] == purpose and r["join_status"] == "OK"]
        hs: dict[str, Any] = {}
        for h in HORIZONS_MS:
            usable = [r for r in subset if r.get(f"h{h}_usable") is True]
            hs[str(h)] = {
                "usable": len(usable),
                "alignedDirectionScoreMean": _distribution([float(r[f"h{h}_alignedDirectionScoreMean"]) for r in usable if r.get(f"h{h}_alignedDirectionScoreMean") is not None]),
                "strongSupportShare": _distribution([float(r[f"h{h}_strongSupportShare"]) for r in usable if r.get(f"h{h}_strongSupportShare") is not None]),
                "persistentPressureRate": sum(bool(r.get(f"h{h}_persistentPressure")) for r in usable) / len(usable) if usable else None,
                "pinnedPersistentPressureRateByRangeBps": {
                    str(t): sum(json.loads(r[f"h{h}_persistentPressurePinnedByRangeBps"]).get(str(t), False) for r in usable) / len(usable) if usable else None for t in PIN_RANGE_BPS
                },
                "spotRangeBps": _distribution([float(r[f"h{h}_spotRangeBps"]) for r in usable if r.get(f"h{h}_spotRangeBps") is not None]),
                "futuresRangeBps": _distribution([float(r[f"h{h}_futuresRangeBps"]) for r in usable if r.get(f"h{h}_futuresRangeBps") is not None]),
            }
        summary_actions[purpose] = {"bursts": len([r for r in action_rows if r["purpose"] == purpose]), "fresh": len(subset), "horizons": hs}

    trips = strict_triplets(bursts, start_ms, end_ms)
    trip_rows: list[dict[str, Any]] = []
    for a, b, c in trips:
        original = a["exposure_direction"]
        rowout = {"target_market_id": a["market_id"], "original_direction": original}
        micro_ids = []
        ok = True
        for label, action in (("a", a), ("b", b), ("c", c)):
            snap, status = strict_past_snapshot(snaps, timestamps, action["first_event_ms"])
            rowout[f"{label}_join"] = status
            if snap is None:
                ok = False
                continue
            mid = _int(snap.get("market_id")); micro_ids.append(mid); rowout[f"{label}_micro_market_id"] = mid
            for h in HORIZONS_MS:
                m = window_metrics(snaps, timestamps, action["first_event_ms"], mid, original, h)
                rowout[f"{label}_h{h}"] = m
        same = ok and len(set(micro_ids)) == 1
        rowout["all_fresh_same_micro_market"] = same
        trip_rows.append(rowout)

    trip_summary: dict[str, Any] = {"strictTriplets": len(trip_rows), "allFreshSameMicroMarket": sum(bool(r["all_fresh_same_micro_market"]) for r in trip_rows), "horizons": {}}
    valid = [r for r in trip_rows if r["all_fresh_same_micro_market"]]
    for h in HORIZONS_MS:
        usable = [r for r in valid if all(r.get(f"{label}_h{h}", {}).get("usable") for label in "abc")]
        b_persistent = [r for r in usable if r[f"b_h{h}"]["persistentPressure"]]
        trip_summary["horizons"][str(h)] = {
            "usableTriplets": len(usable),
            "bOriginalPersistentPressureRate": len(b_persistent) / len(usable) if usable else None,
            "bOriginalPinnedPersistentRateByRangeBps": {str(t): sum(r[f"b_h{h}"]["persistentPressurePinnedByRangeBps"][str(t)] for r in usable) / len(usable) if usable else None for t in PIN_RANGE_BPS},
            "trajectoryAlignedDirectionScoreMean": {label: _distribution([float(r[f"{label}_h{h}"]["alignedDirectionScoreMean"]) for r in usable if r[f"{label}_h{h}"]["alignedDirectionScoreMean"] is not None]) for label in "abc"},
            "trajectorySpotRangeBps": {label: _distribution([float(r[f"{label}_h{h}"]["spotRangeBps"]) for r in usable if r[f"{label}_h{h}"]["spotRangeBps"] is not None]) for label in "abc"},
            "trajectoryFuturesRangeBps": {label: _distribution([float(r[f"{label}_h{h}"]["futuresRangeBps"]) for r in usable if r[f"{label}_h{h}"]["futuresRangeBps"] is not None]) for label in "abc"},
        }
    return {"startMs": start_ms, "endMs": end_ms, "targetActions": len(action_rows), "actions": summary_actions, "tugOfWar": trip_summary}, action_rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path = path.expanduser().resolve(); path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8"); return
    fields = []
    for row in rows:
        for k in row:
            if k not in fields: fields.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--bursts", type=Path, default=DEFAULT_BURSTS)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    p.add_argument("--rows", type=Path, default=DEFAULT_ROWS)
    args = p.parse_args()
    bursts = load_bursts(args.bursts)
    global_start = min(_iso_ms(v[0]) for v in WINDOWS.values()); global_end = max(_iso_ms(v[1]) for v in WINDOWS.values())
    snaps = load_snapshots(args.db, global_start, global_end)
    report = {"version": REPORT_VERSION, "policy": {"strictPast": True, "freshnessMs": FRESHNESS_MS, "horizonsMs": HORIZONS_MS, "directionThreshold": DIRECTION_THRESHOLD, "pinRangeBpsSensitivity": PIN_RANGE_BPS, "interpretation": "Tests persistent order-flow/book pressure with weak realized price response; this is a descriptive opportunity proxy, not a fitted fair-value model."}, "source": {"snapshotsLoaded": len(snaps), "stressBurstsLoaded": len(bursts)}, "windows": {}}
    all_rows: list[dict[str, Any]] = []
    for name, (start, end) in WINDOWS.items():
        summary, rows = analyze_window(name, _iso_ms(start), _iso_ms(end), bursts, snaps)
        report["windows"][name] = summary; all_rows.extend(rows)
    args.report.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    args.report.expanduser().resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(args.rows, all_rows)
    print(REPORT_VERSION)
    for name, data in report["windows"].items():
        print(name, "actions=", data["targetActions"], "triplets=", data["tugOfWar"]["strictTriplets"], "valid=", data["tugOfWar"]["allFreshSameMicroMarket"])
        for h, hs in data["tugOfWar"]["horizons"].items():
            print(" ", h, "ms B persistent=", hs["bOriginalPersistentPressureRate"], "pinned=", hs["bOriginalPinnedPersistentRateByRangeBps"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
