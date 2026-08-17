from __future__ import annotations

import argparse
import bisect
import csv
import importlib.util
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
V24_PATH = ROOT / "tools" / "analyze_target_opportunity_risk_alignment_v24.py"
_spec = importlib.util.spec_from_file_location("target_opportunity_risk_alignment_v24", V24_PATH)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"cannot load {V24_PATH}")
v24 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v24)

REPORT_VERSION = "TARGET_OPPORTUNITY_RISK_ALIGNMENT_V241_TIME_JOIN"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_opportunity_risk_alignment_v241_report.json"
DEFAULT_ACTIONS = ROOT / "data" / "research" / "target_opportunity_risk_alignment_v241_actions.csv"
DEFAULT_TRIPLETS = ROOT / "data" / "research" / "target_opportunity_risk_alignment_v241_triplets.csv"


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _dist(values: list[float | None]) -> dict[str, Any]:
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


def _rate(rows: list[dict[str, Any]], predicate) -> float | None:
    return sum(1 for row in rows if predicate(row)) / len(rows) if rows else None


def build_time_index(rows: list[dict[str, Any]]) -> tuple[list[int], list[dict[str, Any]]]:
    items = sorted(rows, key=lambda row: int(row.get("timestamp_ns") or 0))
    return [int(row.get("timestamp_ns") or 0) for row in items], items


def strict_past_snapshot_by_time(
    index: tuple[list[int], list[dict[str, Any]]],
    action_ms: int,
    freshness_ms: int,
) -> tuple[dict[str, Any] | None, str]:
    timestamps, rows = index
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
    if any(_num(snapshot.get(field)) is None for field in v24.CORE_FIELDS):
        return None, "CORE_FIELD_MISSING"
    out = dict(snapshot)
    out["snapshot_age_ms"] = age_ms
    return out, "OK"


def _action_alignment(row: dict[str, Any], snapshot: dict[str, Any] | None, status: str) -> dict[str, Any]:
    direction = str(row.get("exposure_direction") or "UNKNOWN")
    out: dict[str, Any] = {
        "target_market_id": int(row["market_id"]),
        "segment_id": int(row["segment_id"]),
        "burst_id": row["burst_id"],
        "first_event_ms": int(row["first_event_ms"]),
        "purpose": row["purpose"],
        "exposure_direction": direction,
        "shares": row["shares"],
        "join_status": status,
    }
    if snapshot is None:
        return out
    out["micro_market_id"] = int(snapshot.get("market_id") or 0)
    out["snapshot_age_ms"] = snapshot.get("snapshot_age_ms")
    for field in v24.SNAPSHOT_FIELDS:
        out[field] = snapshot.get(field)
    for field in (
        "direction_score", "spot_queue_imbalance", "futures_queue_imbalance",
        "spot_taker_imbalance_250ms", "futures_taker_imbalance_250ms",
        "spot_taker_imbalance_1s", "futures_taker_imbalance_1s",
    ):
        out[f"action_aligned_{field}"] = v24._aligned(snapshot.get(field), direction)
    out["action_signal_state"] = v24._signal_state(_num(out.get("action_aligned_direction_score")))
    return out


def _triplet_alignment(
    triplet: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
    index: tuple[list[int], list[dict[str, Any]]],
    freshness_ms: int,
) -> dict[str, Any]:
    a, b, c = triplet
    original = str(a["exposure_direction"])
    out: dict[str, Any] = {
        "target_market_id": int(a["market_id"]),
        "segment_id": int(a["segment_id"]),
        "a_burst_id": a["burst_id"], "b_burst_id": b["burst_id"], "c_burst_id": c["burst_id"],
        "original_direction": original,
        "a_shares": a["shares"], "b_shares": b["shares"], "c_shares": c["shares"],
    }
    statuses: list[str] = []
    micro_ids: list[int] = []
    for label, row in (("a", a), ("b", b), ("c", c)):
        snapshot, status = strict_past_snapshot_by_time(index, int(row["first_event_ms"]), freshness_ms)
        statuses.append(status)
        out[f"{label}_join_status"] = status
        out[f"{label}_first_event_ms"] = int(row["first_event_ms"])
        if snapshot is None:
            continue
        micro_id = int(snapshot.get("market_id") or 0)
        micro_ids.append(micro_id)
        out[f"{label}_micro_market_id"] = micro_id
        out[f"{label}_snapshot_age_ms"] = snapshot.get("snapshot_age_ms")
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
            out[f"{label}_original_aligned_{field}"] = v24._aligned(snapshot.get(field), original)
        out[f"{label}_original_signal_state"] = v24._signal_state(
            _num(out.get(f"{label}_original_aligned_direction_score"))
        )
    out["all_three_fresh"] = all(status == "OK" for status in statuses)
    out["same_micro_market"] = bool(out["all_three_fresh"] and len(micro_ids) == 3 and len(set(micro_ids)) == 1)
    if out["same_micro_market"]:
        a_score = _num(out.get("a_original_aligned_direction_score"))
        b_score = _num(out.get("b_original_aligned_direction_score"))
        c_score = _num(out.get("c_original_aligned_direction_score"))
        out["abc_strong_persistent"] = bool(
            a_score is not None and b_score is not None and c_score is not None
            and a_score >= v24.DIRECTION_THRESHOLD
            and b_score >= v24.DIRECTION_THRESHOLD
            and c_score >= v24.DIRECTION_THRESHOLD
        )
        out["b_opportunity_state"] = v24._signal_state(b_score)
        b_spot = _num(out.get("b_original_aligned_spot_queue_imbalance"))
        b_fut = _num(out.get("b_original_aligned_futures_queue_imbalance"))
        out["b_both_books_support_original"] = bool(b_spot is not None and b_fut is not None and b_spot > 0 and b_fut > 0)
        out["b_both_books_oppose_original"] = bool(b_spot is not None and b_fut is not None and b_spot < 0 and b_fut < 0)
    return out


def _mapping_summary(actions: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [row for row in actions if row.get("join_status") == "OK" and row.get("micro_market_id")]
    pairs = Counter((int(row["target_market_id"]), int(row["micro_market_id"])) for row in ok)
    by_target: dict[int, Counter[int]] = defaultdict(Counter)
    for (target_id, micro_id), count in pairs.items():
        by_target[target_id][micro_id] += count
    mapping: dict[str, Any] = {}
    for target_id, counts in sorted(by_target.items()):
        total = sum(counts.values())
        micro_id, dominant = counts.most_common(1)[0]
        mapping[str(target_id)] = {
            "matchedActions": total,
            "dominantMicroMarketId": micro_id,
            "dominantCount": dominant,
            "purity": dominant / total if total else None,
            "allMicroMarketCounts": {str(k): v for k, v in sorted(counts.items())},
        }
    return {
        "pairCounts": [
            {"targetMarketId": target, "microMarketId": micro, "count": count}
            for (target, micro), count in sorted(pairs.items())
        ],
        "byTargetMarket": mapping,
    }


def _summarize_actions(actions: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for purpose in ("ADD", "REPAIR"):
        subset = [row for row in actions if row.get("purpose") == purpose]
        ok = [row for row in subset if row.get("join_status") == "OK" and row.get("exposure_direction") in {"UP", "DOWN"}]
        out[purpose] = {
            "bursts": len(subset),
            "freshCoreAligned": len(ok),
            "freshCoreAlignedRate": len(ok) / len(subset) if subset else None,
            "actionAlignedDirectionScore": _dist([_num(row.get("action_aligned_direction_score")) for row in ok]),
            "actionAlignedSpotQI": _dist([_num(row.get("action_aligned_spot_queue_imbalance")) for row in ok]),
            "actionAlignedFuturesQI": _dist([_num(row.get("action_aligned_futures_queue_imbalance")) for row in ok]),
            "strongSupportActionRate": _rate(ok, lambda row: row.get("action_signal_state") == "SUPPORT"),
            "neutralActionRate": _rate(ok, lambda row: row.get("action_signal_state") == "NEUTRAL"),
            "strongOpposeActionRate": _rate(ok, lambda row: row.get("action_signal_state") == "REVERSE"),
        }
    out["joinStatusCounts"] = dict(Counter(str(row.get("join_status")) for row in actions))
    return out


def _summarize_triplets(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fresh = [row for row in rows if row.get("all_three_fresh")]
    ok = [row for row in rows if row.get("same_micro_market")]
    b_support = [row for row in ok if row.get("b_opportunity_state") == "SUPPORT"]
    return {
        "strictTriplets": len(rows),
        "allThreeFreshCore": len(fresh),
        "allThreeFreshCoreRate": len(fresh) / len(rows) if rows else None,
        "allThreeFreshSameMicroMarket": len(ok),
        "sameMicroMarketRateAmongFresh": len(ok) / len(fresh) if fresh else None,
        "crossMicroMarketExcluded": len(fresh) - len(ok),
        "originalDirectionCounts": dict(Counter(str(row.get("original_direction")) for row in rows)),
        "bOriginalAlignedDirectionScore": _dist([_num(row.get("b_original_aligned_direction_score")) for row in ok]),
        "bOriginalAlignedSpotQI": _dist([_num(row.get("b_original_aligned_spot_queue_imbalance")) for row in ok]),
        "bOriginalAlignedFuturesQI": _dist([_num(row.get("b_original_aligned_futures_queue_imbalance")) for row in ok]),
        "bStrongSupportsOriginalRate": _rate(ok, lambda row: row.get("b_opportunity_state") == "SUPPORT"),
        "bNeutralRate": _rate(ok, lambda row: row.get("b_opportunity_state") == "NEUTRAL"),
        "bStrongReversesOriginalRate": _rate(ok, lambda row: row.get("b_opportunity_state") == "REVERSE"),
        "abcStrongPersistentRate": _rate(ok, lambda row: bool(row.get("abc_strong_persistent"))),
        "bBothBooksSupportOriginalRate": _rate(ok, lambda row: bool(row.get("b_both_books_support_original"))),
        "bBothBooksOpposeOriginalRate": _rate(ok, lambda row: bool(row.get("b_both_books_oppose_original"))),
        "riskRepairAgainstOpportunityCount": len(b_support),
        "trajectory": {
            "aOriginalAlignedDirectionScore": _dist([_num(row.get("a_original_aligned_direction_score")) for row in ok]),
            "bOriginalAlignedDirectionScore": _dist([_num(row.get("b_original_aligned_direction_score")) for row in ok]),
            "cOriginalAlignedDirectionScore": _dist([_num(row.get("c_original_aligned_direction_score")) for row in ok]),
        },
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=v24.DEFAULT_DB)
    parser.add_argument("--bursts", type=Path, default=v24.DEFAULT_BURSTS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--actions", type=Path, default=DEFAULT_ACTIONS)
    parser.add_argument("--triplets", type=Path, default=DEFAULT_TRIPLETS)
    parser.add_argument("--freshness-ms", type=int, default=v24.DEFAULT_FRESHNESS_MS)
    args = parser.parse_args()

    bursts = v24._load_bursts(args.bursts)
    starts = [v24._iso_ms(window[0]) for window in v24.WINDOWS.values()]
    ends = [v24._iso_ms(window[1]) for window in v24.WINDOWS.values()]
    snapshots = v24._load_snapshots(args.db, min(starts), max(ends))

    report: dict[str, Any] = {
        "version": REPORT_VERSION,
        "policy": {
            "regime": v24.STRESS,
            "strictPast": "snapshot.timestamp_ns < Target burst first_event_ms; equal timestamp excluded",
            "freshnessMs": args.freshness_ms,
            "crossSourceJoin": "time-only strict-past join; Target market_id and microstructure market_id are different namespaces",
            "rolloverGuard": "A/B/C triplet is analyzed only when all three fresh snapshots map to the same microstructure market_id",
            "directionThreshold": v24.DIRECTION_THRESHOLD,
            "guardrails": [
                "No model fitting.",
                "No forward fill across stale snapshots.",
                "Cross-source market ids are reported as a mapping diagnostic, never required to be numerically equal.",
                "250ms/1s taker-flow NULL is preserved and is not treated as collector failure.",
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
    for name, (start_iso, end_iso, expected_micro_markets) in v24.WINDOWS.items():
        start_ms, end_ms = v24._iso_ms(start_iso), v24._iso_ms(end_iso)
        start_ns, end_ns = start_ms * 1_000_000, end_ms * 1_000_000
        snap_rows = [row for row in snapshots if start_ns <= int(row.get("timestamp_ns") or 0) <= end_ns]
        target_rows = [row for row in bursts if start_ms <= int(row["first_event_ms"]) <= end_ms]
        index = build_time_index(snap_rows)

        actions: list[dict[str, Any]] = []
        for row in target_rows:
            snapshot, status = strict_past_snapshot_by_time(index, int(row["first_event_ms"]), args.freshness_ms)
            aligned = _action_alignment(row, snapshot, status)
            aligned["window"] = name
            actions.append(aligned)

        triplets = v24.strict_tug_triplets(bursts, start_ms, end_ms)
        aligned_triplets: list[dict[str, Any]] = []
        for triplet in triplets:
            item = _triplet_alignment(triplet, index, args.freshness_ms)
            item["window"] = name
            aligned_triplets.append(item)

        report["windows"][name] = {
            "start": start_iso,
            "end": end_iso,
            "expectedMicrostructureMarkets": expected_micro_markets,
            "snapshotRows": len(snap_rows),
            "snapshotMarkets": sorted({int(row.get("market_id") or 0) for row in snap_rows}),
            "targetBursts": len(target_rows),
            "targetMarkets": sorted({int(row["market_id"]) for row in target_rows}),
            "mapping": _mapping_summary(actions),
            "actions": _summarize_actions(actions),
            "tugOfWar": _summarize_triplets(aligned_triplets),
        }
        all_actions.extend(actions)
        all_triplets.extend(aligned_triplets)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(args.actions, all_actions)
    _write_csv(args.triplets, all_triplets)
    print(REPORT_VERSION)
    for name, payload in report["windows"].items():
        print(
            name,
            "targetBursts=", payload["targetBursts"],
            "strictTriplets=", payload["tugOfWar"]["strictTriplets"],
            "freshSameMicro=", payload["tugOfWar"]["allThreeFreshSameMicroMarket"],
        )
    print("report:", args.report)


if __name__ == "__main__":
    main()
