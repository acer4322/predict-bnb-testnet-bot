from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
REPORT_VERSION = "TARGET_OPPORTUNITY_RISK_ALIGNMENT_V24"
STRESS = "STRESS_2026_08_16"
DEFAULT_DB = ROOT / "data" / "microstructure.db"
DEFAULT_BURSTS = ROOT / "data" / "research" / "target_controller_hazard_v21_directional_bursts.csv"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_opportunity_risk_alignment_v24_report.json"
DEFAULT_ACTIONS = ROOT / "data" / "research" / "target_opportunity_risk_alignment_v24_actions.csv"
DEFAULT_TRIPLETS = ROOT / "data" / "research" / "target_opportunity_risk_alignment_v24_triplets.csv"
DEFAULT_FRESHNESS_MS = 750
DIRECTION_THRESHOLD = 0.20

WINDOWS = {
    "PRIMARY_CLEAN": (
        "2026-08-16T05:45:00.877043+08:00",
        "2026-08-16T06:00:02.210840+08:00",
        [6991528, 6991536, 6991538],
    ),
    "SECONDARY_EXTENDED": (
        "2026-08-16T05:40:05.009737+08:00",
        "2026-08-16T06:00:02.210840+08:00",
        [6991509, 6991528, 6991536, 6991538],
    ),
}

SNAPSHOT_FIELDS = (
    "timestamp_ns", "market_id",
    "spot_price", "futures_price",
    "spot_microprice", "futures_microprice",
    "spot_queue_imbalance", "futures_queue_imbalance",
    "spot_taker_imbalance_250ms", "futures_taker_imbalance_250ms",
    "spot_taker_imbalance_1s", "futures_taker_imbalance_1s",
    "perp_spot_basis_bps", "prediction_up_mid",
    "direction_score", "direction_bias",
)
CORE_FIELDS = (
    "spot_price", "futures_price",
    "spot_queue_imbalance", "futures_queue_imbalance",
    "direction_score",
)


def _iso_ms(value: str) -> int:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return int(dt.timestamp() * 1000)


def _num(value: Any) -> float | None:
    if value is None or value == "":
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


def _opposite(side: str) -> str:
    return "DOWN" if side == "UP" else "UP" if side == "DOWN" else "UNKNOWN"


def _sign(side: str) -> float:
    return 1.0 if side == "UP" else -1.0 if side == "DOWN" else 0.0


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


def _distribution(values: Iterable[float | None]) -> dict[str, Any]:
    xs = sorted(float(x) for x in values if x is not None and math.isfinite(float(x)))
    if not xs:
        return {"n": 0}

    def q(p: float) -> float:
        if len(xs) == 1:
            return xs[0]
        pos = p * (len(xs) - 1)
        lo = int(math.floor(pos))
        hi = int(math.ceil(pos))
        if lo == hi:
            return xs[lo]
        frac = pos - lo
        return xs[lo] * (1.0 - frac) + xs[hi] * frac

    return {
        "n": len(xs), "min": xs[0], "p10": q(0.10), "p25": q(0.25),
        "median": q(0.50), "p75": q(0.75), "p90": q(0.90),
        "max": xs[-1], "mean": statistics.fmean(xs),
    }


def _load_bursts(path: Path) -> list[dict[str, Any]]:
    required = {
        "market_id", "segment_id", "regime", "burst_id", "first_event_ms", "last_event_ms",
        "side", "bid_parent_count", "ask_parent_count", "shares", "purpose",
        "pre_risk_deficit", "post_risk_deficit", "pre_abs_payoff_gap", "post_abs_payoff_gap",
    }
    rows: list[dict[str, Any]] = []
    with path.expanduser().resolve().open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise RuntimeError("directional bursts CSV missing columns: " + ", ".join(missing))
        for raw in reader:
            if str(raw.get("regime") or "") != STRESS:
                continue
            row = dict(raw)
            row["market_id"] = _int(row.get("market_id"))
            row["segment_id"] = _int(row.get("segment_id"))
            row["first_event_ms"] = _int(row.get("first_event_ms"))
            row["last_event_ms"] = _int(row.get("last_event_ms"))
            row["shares"] = _num(row.get("shares")) or 0.0
            row["purpose"] = str(row.get("purpose") or "").upper()
            row["exposure_direction"] = exposure_direction(row)
            rows.append(row)
    rows.sort(key=lambda r: (r["market_id"], r["segment_id"], r["first_event_ms"], str(r["burst_id"])))
    return rows


def _load_snapshots(path: Path, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    db_path = path.expanduser().resolve()
    cols = ", ".join(SNAPSHOT_FIELDS)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = [
            dict(row)
            for row in conn.execute(
                f"SELECT {cols} FROM microstructure_snapshots "
                "WHERE timestamp_ns >= ? AND timestamp_ns <= ? ORDER BY timestamp_ns",
                (start_ms * 1_000_000, end_ms * 1_000_000),
            )
        ]
    return rows


def build_snapshot_index(rows: list[dict[str, Any]]) -> dict[int, tuple[list[int], list[dict[str, Any]]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_int(row.get("market_id"))].append(row)
    out: dict[int, tuple[list[int], list[dict[str, Any]]]] = {}
    for market_id, items in grouped.items():
        items.sort(key=lambda row: _int(row.get("timestamp_ns")))
        out[market_id] = ([_int(row.get("timestamp_ns")) for row in items], items)
    return out


def strict_past_snapshot(
    index: dict[int, tuple[list[int], list[dict[str, Any]]]],
    market_id: int,
    action_ms: int,
    freshness_ms: int,
) -> tuple[dict[str, Any] | None, str]:
    bundle = index.get(int(market_id))
    if not bundle:
        return None, "NO_MARKET_SNAPSHOTS"
    timestamps, rows = bundle
    action_ns = int(action_ms) * 1_000_000
    pos = bisect.bisect_left(timestamps, action_ns) - 1
    if pos < 0:
        return None, "NO_STRICT_PAST_SNAPSHOT"
    snapshot = rows[pos]
    age_ms = (action_ns - timestamps[pos]) / 1_000_000.0
    if age_ms < 0:
        return None, "FUTURE_SNAPSHOT"
    if age_ms > freshness_ms:
        return None, "STALE_SNAPSHOT"
    if any(_num(snapshot.get(field)) is None for field in CORE_FIELDS):
        return None, "CORE_FIELD_MISSING"
    result = dict(snapshot)
    result["snapshot_age_ms"] = age_ms
    return result, "OK"


def _signal_state(score: float | None, threshold: float = DIRECTION_THRESHOLD) -> str:
    if score is None:
        return "UNKNOWN"
    if score >= threshold:
        return "SUPPORT"
    if score <= -threshold:
        return "REVERSE"
    return "NEUTRAL"


def _aligned(value: Any, direction: str) -> float | None:
    number = _num(value)
    sign = _sign(direction)
    return number * sign if number is not None and sign else None


def _action_alignment(row: dict[str, Any], snapshot: dict[str, Any] | None, status: str) -> dict[str, Any]:
    direction = str(row.get("exposure_direction") or "UNKNOWN")
    out = {
        "market_id": row["market_id"],
        "segment_id": row["segment_id"],
        "burst_id": row["burst_id"],
        "first_event_ms": row["first_event_ms"],
        "last_event_ms": row["last_event_ms"],
        "purpose": row["purpose"],
        "exposure_direction": direction,
        "shares": row["shares"],
        "pre_risk_deficit": _num(row.get("pre_risk_deficit")),
        "post_risk_deficit": _num(row.get("post_risk_deficit")),
        "join_status": status,
    }
    if snapshot is None:
        return out
    out.update({field: snapshot.get(field) for field in SNAPSHOT_FIELDS})
    out["snapshot_age_ms"] = snapshot.get("snapshot_age_ms")
    for field in (
        "direction_score", "spot_queue_imbalance", "futures_queue_imbalance",
        "spot_taker_imbalance_250ms", "futures_taker_imbalance_250ms",
        "spot_taker_imbalance_1s", "futures_taker_imbalance_1s",
    ):
        out[f"action_aligned_{field}"] = _aligned(snapshot.get(field), direction)
    out["action_signal_state"] = _signal_state(_num(out.get("action_aligned_direction_score")))
    return out


def _gap_ms(left: dict[str, Any], right: dict[str, Any]) -> int:
    return int(right["first_event_ms"]) - int(left["last_event_ms"])


def strict_tug_triplets(rows: list[dict[str, Any]], start_ms: int, end_ms: int, limit_ms: int = 5000) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    groups: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(int(row["market_id"]), int(row["segment_id"]))].append(row)
    out: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for group in groups.values():
        group.sort(key=lambda r: (r["first_event_ms"], str(r["burst_id"])))
        for i in range(len(group) - 2):
            a, b, c = group[i], group[i + 1], group[i + 2]
            if not (start_ms <= a["first_event_ms"] <= end_ms and start_ms <= b["first_event_ms"] <= end_ms and start_ms <= c["first_event_ms"] <= end_ms):
                continue
            da, db, dc = a["exposure_direction"], b["exposure_direction"], c["exposure_direction"]
            if (
                a["purpose"] == "ADD" and b["purpose"] == "REPAIR" and c["purpose"] == "ADD"
                and da in {"UP", "DOWN"} and db == _opposite(da) and dc == da
                and 0 <= _gap_ms(a, b) <= limit_ms
                and 0 <= _gap_ms(b, c) <= limit_ms
            ):
                out.append((a, b, c))
    return out


def _triplet_alignment(
    triplet: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
    index: dict[int, tuple[list[int], list[dict[str, Any]]]],
    freshness_ms: int,
) -> dict[str, Any]:
    a, b, c = triplet
    original = str(a["exposure_direction"])
    out: dict[str, Any] = {
        "market_id": a["market_id"], "segment_id": a["segment_id"],
        "a_burst_id": a["burst_id"], "b_burst_id": b["burst_id"], "c_burst_id": c["burst_id"],
        "original_direction": original,
        "a_to_b_ms": _gap_ms(a, b), "b_to_c_ms": _gap_ms(b, c),
        "a_shares": a["shares"], "b_shares": b["shares"], "c_shares": c["shares"],
    }
    statuses: list[str] = []
    for label, row in (("a", a), ("b", b), ("c", c)):
        snapshot, status = strict_past_snapshot(index, row["market_id"], row["first_event_ms"], freshness_ms)
        statuses.append(status)
        out[f"{label}_join_status"] = status
        out[f"{label}_first_event_ms"] = row["first_event_ms"]
        if snapshot is None:
            continue
        out[f"{label}_snapshot_age_ms"] = snapshot["snapshot_age_ms"]
        for field in (
            "direction_score", "spot_queue_imbalance", "futures_queue_imbalance",
            "spot_taker_imbalance_250ms", "futures_taker_imbalance_250ms",
            "spot_taker_imbalance_1s", "futures_taker_imbalance_1s",
            "prediction_up_mid", "spot_price", "futures_price", "perp_spot_basis_bps",
        ):
            out[f"{label}_{field}"] = snapshot.get(field)
        for field in (
            "direction_score", "spot_queue_imbalance", "futures_queue_imbalance",
            "spot_taker_imbalance_250ms", "futures_taker_imbalance_250ms",
            "spot_taker_imbalance_1s", "futures_taker_imbalance_1s",
        ):
            out[f"{label}_original_aligned_{field}"] = _aligned(snapshot.get(field), original)
        out[f"{label}_original_signal_state"] = _signal_state(_num(out.get(f"{label}_original_aligned_direction_score")))
    out["all_three_fresh"] = all(status == "OK" for status in statuses)
    if out["all_three_fresh"]:
        a_score = _num(out.get("a_original_aligned_direction_score"))
        b_score = _num(out.get("b_original_aligned_direction_score"))
        c_score = _num(out.get("c_original_aligned_direction_score"))
        out["abc_strong_persistent"] = bool(
            a_score is not None and b_score is not None and c_score is not None
            and a_score >= DIRECTION_THRESHOLD and b_score >= DIRECTION_THRESHOLD and c_score >= DIRECTION_THRESHOLD
        )
        out["b_opportunity_state"] = _signal_state(b_score)
        b_spot = _num(out.get("b_original_aligned_spot_queue_imbalance"))
        b_fut = _num(out.get("b_original_aligned_futures_queue_imbalance"))
        out["b_both_books_support_original"] = bool(b_spot is not None and b_fut is not None and b_spot > 0 and b_fut > 0)
        out["b_both_books_oppose_original"] = bool(b_spot is not None and b_fut is not None and b_spot < 0 and b_fut < 0)
    return out


def _rate(rows: list[dict[str, Any]], predicate) -> float | None:
    if not rows:
        return None
    return sum(1 for row in rows if predicate(row)) / len(rows)


def _summarize_actions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for purpose in ("ADD", "REPAIR"):
        subset = [row for row in rows if row.get("purpose") == purpose]
        ok = [row for row in subset if row.get("join_status") == "OK" and row.get("exposure_direction") in {"UP", "DOWN"}]
        out[purpose] = {
            "bursts": len(subset),
            "freshCoreAligned": len(ok),
            "freshCoreAlignedRate": len(ok) / len(subset) if subset else None,
            "actionAlignedDirectionScore": _distribution(_num(row.get("action_aligned_direction_score")) for row in ok),
            "actionAlignedSpotQI": _distribution(_num(row.get("action_aligned_spot_queue_imbalance")) for row in ok),
            "actionAlignedFuturesQI": _distribution(_num(row.get("action_aligned_futures_queue_imbalance")) for row in ok),
            "strongSupportActionRate": _rate(ok, lambda row: row.get("action_signal_state") == "SUPPORT"),
            "neutralActionRate": _rate(ok, lambda row: row.get("action_signal_state") == "NEUTRAL"),
            "strongOpposeActionRate": _rate(ok, lambda row: row.get("action_signal_state") == "REVERSE"),
        }
    out["joinStatusCounts"] = dict(Counter(str(row.get("join_status")) for row in rows))
    return out


def _summarize_triplets(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [row for row in rows if row.get("all_three_fresh")]
    b_support = [row for row in ok if row.get("b_opportunity_state") == "SUPPORT"]
    return {
        "strictTriplets": len(rows),
        "allThreeFreshCore": len(ok),
        "allThreeFreshCoreRate": len(ok) / len(rows) if rows else None,
        "originalDirectionCounts": dict(Counter(str(row.get("original_direction")) for row in rows)),
        "bOriginalAlignedDirectionScore": _distribution(_num(row.get("b_original_aligned_direction_score")) for row in ok),
        "bOriginalAlignedSpotQI": _distribution(_num(row.get("b_original_aligned_spot_queue_imbalance")) for row in ok),
        "bOriginalAlignedFuturesQI": _distribution(_num(row.get("b_original_aligned_futures_queue_imbalance")) for row in ok),
        "bStrongSupportsOriginalRate": _rate(ok, lambda row: row.get("b_opportunity_state") == "SUPPORT"),
        "bNeutralRate": _rate(ok, lambda row: row.get("b_opportunity_state") == "NEUTRAL"),
        "bStrongReversesOriginalRate": _rate(ok, lambda row: row.get("b_opportunity_state") == "REVERSE"),
        "abcStrongPersistentRate": _rate(ok, lambda row: bool(row.get("abc_strong_persistent"))),
        "bBothBooksSupportOriginalRate": _rate(ok, lambda row: bool(row.get("b_both_books_support_original"))),
        "bBothBooksOpposeOriginalRate": _rate(ok, lambda row: bool(row.get("b_both_books_oppose_original"))),
        "riskRepairAgainstOpportunityCount": len(b_support),
        "trajectory": {
            "aOriginalAlignedDirectionScore": _distribution(_num(row.get("a_original_aligned_direction_score")) for row in ok),
            "bOriginalAlignedDirectionScore": _distribution(_num(row.get("b_original_aligned_direction_score")) for row in ok),
            "cOriginalAlignedDirectionScore": _distribution(_num(row.get("c_original_aligned_direction_score")) for row in ok),
        },
    }


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
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp = resolved.with_suffix(resolved.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(resolved)


def analyze_window(
    name: str,
    start_iso: str,
    end_iso: str,
    expected_markets: list[int],
    bursts: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    freshness_ms: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    start_ms, end_ms = _iso_ms(start_iso), _iso_ms(end_iso)
    expected = set(expected_markets)
    window_bursts = [
        row for row in bursts
        if start_ms <= row["first_event_ms"] <= end_ms and row["market_id"] in expected
    ]
    window_snapshots = [
        row for row in snapshots
        if start_ms * 1_000_000 <= _int(row.get("timestamp_ns")) <= end_ms * 1_000_000
        and _int(row.get("market_id")) in expected
    ]
    index = build_snapshot_index(window_snapshots)
    actions: list[dict[str, Any]] = []
    for row in window_bursts:
        snapshot, status = strict_past_snapshot(index, row["market_id"], row["first_event_ms"], freshness_ms)
        aligned = _action_alignment(row, snapshot, status)
        aligned["window"] = name
        actions.append(aligned)

    raw_triplets = strict_tug_triplets(
        [row for row in bursts if row["market_id"] in expected], start_ms, end_ms, 5000
    )
    triplets: list[dict[str, Any]] = []
    for triplet in raw_triplets:
        aligned = _triplet_alignment(triplet, index, freshness_ms)
        aligned["window"] = name
        triplets.append(aligned)

    summary = {
        "start": start_iso, "end": end_iso,
        "expectedMarkets": expected_markets,
        "snapshotRows": len(window_snapshots),
        "snapshotMarkets": sorted(index),
        "targetBursts": len(window_bursts),
        "targetMarkets": sorted({int(row["market_id"]) for row in window_bursts}),
        "actions": _summarize_actions(actions),
        "tugOfWar": _summarize_triplets(triplets),
    }
    return summary, actions, triplets


def main() -> int:
    parser = argparse.ArgumentParser(description="Strict-past Target ADD/REPAIR vs saved Binance microstructure alignment.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--bursts", type=Path, default=DEFAULT_BURSTS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--actions-csv", type=Path, default=DEFAULT_ACTIONS)
    parser.add_argument("--triplets-csv", type=Path, default=DEFAULT_TRIPLETS)
    parser.add_argument("--freshness-ms", type=int, default=DEFAULT_FRESHNESS_MS)
    args = parser.parse_args()

    bursts = _load_bursts(args.bursts)
    global_start = min(_iso_ms(spec[0]) for spec in WINDOWS.values())
    global_end = max(_iso_ms(spec[1]) for spec in WINDOWS.values())
    snapshots = _load_snapshots(args.db, global_start - 2000, global_end + 1000)

    report: dict[str, Any] = {
        "version": REPORT_VERSION,
        "policy": {
            "regime": STRESS,
            "strictPast": "snapshot.timestamp_ns < Target burst first_event_ms; equal timestamp excluded",
            "freshnessMs": args.freshness_ms,
            "marketIdMustMatch": True,
            "directionThreshold": DIRECTION_THRESHOLD,
            "coreFields": list(CORE_FIELDS),
            "interpretation": {
                "B_SUPPORT": "During opposite REPAIR, saved opportunity pressure still supports the original A/C ADD direction.",
                "B_REVERSE": "During opposite REPAIR, saved opportunity pressure strongly reversed; a single fast-changing market signal remains plausible.",
            },
            "guardrails": [
                "No model fitting.",
                "No forward fill across missing market snapshots.",
                "250ms/1s taker-flow NULL is preserved and is not treated as collector failure.",
                "Prediction price is descriptive only in V2.4; no synthetic fair-value/expected-profit score is invented.",
            ],
        },
        "source": {
            "db": str(args.db.expanduser().resolve()),
            "bursts": str(args.bursts.expanduser().resolve()),
            "snapshotRowsLoaded": len(snapshots),
            "stressBurstsLoaded": len(bursts),
        },
        "windows": {},
    }
    all_actions: list[dict[str, Any]] = []
    all_triplets: list[dict[str, Any]] = []
    for name, (start_iso, end_iso, markets) in WINDOWS.items():
        summary, actions, triplets = analyze_window(
            name, start_iso, end_iso, markets, bursts, snapshots, args.freshness_ms
        )
        report["windows"][name] = summary
        all_actions.extend(actions)
        all_triplets.extend(triplets)

    _write_json(args.report, report)
    _write_csv(args.actions_csv, all_actions)
    _write_csv(args.triplets_csv, all_triplets)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"report: {args.report}")
    print(f"actions: {args.actions_csv}")
    print(f"triplets: {args.triplets_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
