from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from build_target_taker_direct_eligibility_special_regime_official_v2 import (
    DEFAULT_OFFICIAL_TARGET_DB,
    _merge_labels,
)
from predict_bot.target_maker_taker_link import DEFAULT_SHADOW_DB
from predict_bot.target_taker_direct_eligibility_special_regime_v1 import DEFAULT_OUTPUT as DEFAULT_ELIGIBILITY_DATASET

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "data" / "research" / "target_taker_intramarket_sequence_v1_report.json"
DEFAULT_EVENTS_CSV = ROOT / "data" / "research" / "target_taker_intramarket_sequence_v1_events.csv"
DEFAULT_RISKSET_CSV = ROOT / "data" / "research" / "target_taker_intramarket_riskset_v1.csv"
REPORT_VERSION = "TARGET_TAKER_INTRAMARKET_SEQUENCE_V1"

PHASE_BINS = (
    (240.0, math.inf, "T300_240"),
    (180.0, 240.0, "T240_180"),
    (120.0, 180.0, "T180_120"),
    (60.0, 120.0, "T120_060"),
    (30.0, 60.0, "T060_030"),
    (-math.inf, 30.0, "T030_000"),
)


def _epoch_ms(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        pass
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"time must include timezone offset or be epoch ms, got {value!r}")
    return int(dt.timestamp() * 1000)


def _iso_taipei(value: int | None) -> str | None:
    if value is None:
        return None
    tz = timezone(timedelta(hours=8))
    return datetime.fromtimestamp(int(value) / 1000.0, tz=tz).isoformat()


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _phase(seconds_left: float | None) -> str:
    if seconds_left is None or not math.isfinite(seconds_left):
        return "UNKNOWN"
    for low, high, name in PHASE_BINS:
        if seconds_left > low and seconds_left <= high:
            return name
    return "UNKNOWN"


def _macro_phase(seconds_left: float | None) -> str:
    if seconds_left is None or not math.isfinite(seconds_left):
        return "UNKNOWN"
    if seconds_left > 180.0:
        return "OPEN"
    if seconds_left > 60.0:
        return "MID"
    return "TAIL"


def _quantiles(values: list[float]) -> dict[str, Any]:
    clean = sorted(float(v) for v in values if math.isfinite(float(v)))
    if not clean:
        return {"count": 0}

    def q(frac: float) -> float:
        if len(clean) == 1:
            return clean[0]
        position = (len(clean) - 1) * frac
        left = int(math.floor(position))
        right = int(math.ceil(position))
        if left == right:
            return clean[left]
        weight = position - left
        return clean[left] * (1.0 - weight) + clean[right] * weight

    return {
        "count": len(clean),
        "min": clean[0],
        "p10": q(0.10),
        "p25": q(0.25),
        "median": q(0.50),
        "p75": q(0.75),
        "p90": q(0.90),
        "max": clean[-1],
        "mean": float(statistics.fmean(clean)),
    }


def _counter(counter: Counter[str]) -> dict[str, int]:
    return {key: int(counter[key]) for key in sorted(counter)}


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(resolved)


def _load_dataset(path: Path) -> Any:
    try:
        import pandas as pd
    except ImportError as exc:
        raise SystemExit('Research dependencies missing. Run: pip install -e ".[research]"') from exc

    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    usecols = [
        "market_id",
        "decision_sampled_at_ms",
        "decision_bucket_start_ms",
        "seconds_left",
        "ms_to_next_target_taker_bucket",
        "label_next_target_taker_any_1s",
        "label_next_target_taker_any_2s",
        "label_next_target_taker_any_5s",
    ]
    frame = pd.read_csv(resolved, usecols=usecols)
    frame["market_id"] = pd.to_numeric(frame["market_id"], errors="raise").astype(int)
    frame["decision_sampled_at_ms"] = pd.to_numeric(
        frame["decision_sampled_at_ms"], errors="raise"
    ).astype("int64")
    frame["decision_bucket_start_ms"] = pd.to_numeric(
        frame["decision_bucket_start_ms"], errors="raise"
    ).astype("int64")
    frame["seconds_left"] = pd.to_numeric(frame["seconds_left"], errors="coerce")
    frame.sort_values(["market_id", "decision_sampled_at_ms"], inplace=True)
    return frame


def _filter_events(
    rows: list[dict[str, Any]],
    *,
    start_ms: int | None,
    end_ms: int | None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        event_ms = int(row["target_event_ms"])
        if start_ms is not None and event_ms < start_ms:
            continue
        if end_ms is not None and event_ms >= end_ms:
            continue
        result.append(dict(row))
    return result


def _dataset_index(frame: Any) -> dict[int, dict[str, list[Any]]]:
    result: dict[int, dict[str, list[Any]]] = {}
    for market_id, group in frame.groupby("market_id", sort=False):
        sampled = [int(v) for v in group["decision_sampled_at_ms"].tolist()]
        seconds = [
            _number(v)
            for v in group["seconds_left"].tolist()
        ]
        result[int(market_id)] = {
            "sampled": sampled,
            "seconds": seconds,
        }
    return result


def _nearest_phase_snapshot(
    index: dict[int, dict[str, list[Any]]],
    *,
    market_id: int,
    event_ms: int,
    max_distance_ms: int = 2500,
) -> tuple[int | None, float | None, int | None]:
    market = index.get(int(market_id))
    if not market:
        return None, None, None
    times = market["sampled"]
    if not times:
        return None, None, None
    pos = bisect.bisect_left(times, int(event_ms))
    candidates: list[int] = []
    if pos < len(times):
        candidates.append(pos)
    if pos > 0:
        candidates.append(pos - 1)
    best = min(candidates, key=lambda i: abs(times[i] - int(event_ms)))
    distance = int(times[best] - int(event_ms))
    if abs(distance) > int(max_distance_ms):
        return None, None, None
    return int(times[best]), market["seconds"][best], distance


def _sequence_rows(
    events: list[dict[str, Any]],
    phase_index: dict[int, dict[str, list[Any]]],
) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for raw in events:
        grouped[int(raw["market_id"])].append(dict(raw))

    output: list[dict[str, Any]] = []
    for market_id in sorted(grouped):
        market_events = sorted(
            grouped[market_id],
            key=lambda row: (int(row["target_event_ms"]), str(row.get("parent_id") or "")),
        )
        previous: dict[str, Any] | None = None
        first_ms = int(market_events[0]["target_event_ms"])
        for index, event in enumerate(market_events, 1):
            event_ms = int(event["target_event_ms"])
            side = str(event.get("side") or "").upper()
            aligned_ms, seconds_left, alignment_delta = _nearest_phase_snapshot(
                phase_index,
                market_id=market_id,
                event_ms=event_ms,
            )
            if previous is None:
                event_type = "FIRST_ENTRY"
                gap_ms = None
            else:
                prior_side = str(previous.get("side") or "").upper()
                event_type = "SAME_SIDE_REENTRY" if side and side == prior_side else "SIDE_FLIP"
                gap_ms = event_ms - int(previous["target_event_ms"])

            output.append(
                {
                    "market_id": market_id,
                    "event_index": index,
                    "market_event_count": len(market_events),
                    "parent_id": event.get("parent_id"),
                    "order_hash": event.get("order_hash"),
                    "label_source": event.get("label_source"),
                    "target_event_ms": event_ms,
                    "target_event_taipei": _iso_taipei(event_ms),
                    "side": side,
                    "event_type": event_type,
                    "gap_from_previous_parent_ms": gap_ms,
                    "elapsed_from_first_parent_ms": event_ms - first_ms,
                    "aligned_public_sample_ms": aligned_ms,
                    "phase_alignment_delta_ms": alignment_delta,
                    "seconds_left": seconds_left,
                    "phase": _phase(seconds_left),
                    "macro_phase": _macro_phase(seconds_left),
                }
            )
            previous = event
    return output


def _riskset_rows(
    frame: Any,
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events_by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in events:
        events_by_market[int(row["market_id"])].append(dict(row))
    for market_id in list(events_by_market):
        events_by_market[market_id].sort(
            key=lambda row: (int(row["target_event_ms"]), str(row.get("parent_id") or ""))
        )

    output: list[dict[str, Any]] = []
    for market_id, group in frame.groupby("market_id", sort=False):
        market_id = int(market_id)
        market_events = events_by_market.get(market_id, [])
        event_buckets = [(int(row["target_event_ms"]) // 1000) * 1000 for row in market_events]
        for raw in group.itertuples(index=False):
            sampled = int(raw.decision_sampled_at_ms)
            bucket = int(raw.decision_bucket_start_ms)
            seconds_left = _number(raw.seconds_left)

            # A target parent stamped in the current second is not treated as known at
            # the decision boundary. This mirrors the strict future-second research rule.
            prior_count = bisect.bisect_left(event_buckets, bucket)
            previous = market_events[prior_count - 1] if prior_count > 0 else None
            next_index = bisect.bisect_right(event_buckets, bucket)
            next_event = market_events[next_index] if next_index < len(market_events) else None

            previous_side = str(previous.get("side") or "").upper() if previous else ""
            next_side = str(next_event.get("side") or "").upper() if next_event else ""
            if next_event is None:
                next_type = "NONE"
            elif previous is None:
                next_type = "FIRST_ENTRY"
            elif next_side and next_side == previous_side:
                next_type = "SAME_SIDE_REENTRY"
            else:
                next_type = "SIDE_FLIP"

            previous_ms = int(previous["target_event_ms"]) if previous else None
            next_ms = int(next_event["target_event_ms"]) if next_event else None
            output.append(
                {
                    "market_id": market_id,
                    "decision_sampled_at_ms": sampled,
                    "decision_bucket_start_ms": bucket,
                    "seconds_left": seconds_left,
                    "phase": _phase(seconds_left),
                    "macro_phase": _macro_phase(seconds_left),
                    "target_parent_count_before_decision": prior_count,
                    "position_state": "PRE_FIRST_ENTRY" if previous is None else "POST_FIRST_ENTRY",
                    "previous_target_side": previous_side,
                    "previous_target_parent_ms": previous_ms,
                    "ms_since_previous_target_parent": (
                        sampled - previous_ms if previous_ms is not None else None
                    ),
                    "next_target_parent_ms": next_ms,
                    "next_target_side": next_side,
                    "next_target_event_type": next_type,
                    "ms_to_next_target_parent": (
                        next_ms - bucket if next_ms is not None else None
                    ),
                    "label_next_target_taker_any_1s": int(raw.label_next_target_taker_any_1s),
                    "label_next_target_taker_any_2s": int(raw.label_next_target_taker_any_2s),
                    "label_next_target_taker_any_5s": int(raw.label_next_target_taker_any_5s),
                }
            )
    return output


def _event_report(events: list[dict[str, Any]]) -> dict[str, Any]:
    if not events:
        return {"events": 0, "markets": 0}

    market_counts = Counter(int(row["market_id"]) for row in events)
    type_counts = Counter(str(row["event_type"]) for row in events)
    phase_counts = Counter(str(row["phase"]) for row in events)
    macro_counts = Counter(str(row["macro_phase"]) for row in events)
    by_type_phase: dict[str, Counter[str]] = defaultdict(Counter)
    by_type_macro: dict[str, Counter[str]] = defaultdict(Counter)
    for row in events:
        by_type_phase[str(row["event_type"])][str(row["phase"])] += 1
        by_type_macro[str(row["event_type"])][str(row["macro_phase"])] += 1

    first = [row for row in events if row["event_type"] == "FIRST_ENTRY"]
    reentries = [row for row in events if row["event_type"] == "SAME_SIDE_REENTRY"]
    flips = [row for row in events if row["event_type"] == "SIDE_FLIP"]
    subsequent = reentries + flips
    gaps_all = [float(row["gap_from_previous_parent_ms"]) for row in subsequent if row["gap_from_previous_parent_ms"] is not None]
    gaps_same = [float(row["gap_from_previous_parent_ms"]) for row in reentries if row["gap_from_previous_parent_ms"] is not None]
    gaps_flip = [float(row["gap_from_previous_parent_ms"]) for row in flips if row["gap_from_previous_parent_ms"] is not None]

    same_side_within: dict[str, Any] = {}
    for horizon_s in (1, 2, 5, 10, 30, 60):
        threshold = horizon_s * 1000
        count = sum(gap <= threshold for gap in gaps_same)
        same_side_within[f"within{horizon_s}s"] = {
            "count": int(count),
            "shareOfSameSideReentries": (count / len(gaps_same)) if gaps_same else None,
        }

    market_event_counts = [int(v) for v in market_counts.values()]
    multi_market = {
        "marketsWith2PlusParents": sum(v >= 2 for v in market_event_counts),
        "marketsWith3PlusParents": sum(v >= 3 for v in market_event_counts),
        "marketsWith5PlusParents": sum(v >= 5 for v in market_event_counts),
        "marketsWith10PlusParents": sum(v >= 10 for v in market_event_counts),
    }
    total_markets = len(market_counts)
    for key, count in list(multi_market.items()):
        multi_market[key + "Share"] = count / total_markets if total_markets else None

    aligned = [row for row in events if row["seconds_left"] is not None]
    return {
        "events": len(events),
        "markets": total_markets,
        "eventCountPerMarket": _quantiles([float(v) for v in market_event_counts]),
        "multiParentMarkets": multi_market,
        "eventTypes": _counter(type_counts),
        "eventTypeShares": {
            key: value / len(events) for key, value in sorted(type_counts.items())
        },
        "sameSideShareAmongSubsequent": (
            len(reentries) / len(subsequent) if subsequent else None
        ),
        "sideFlipShareAmongSubsequent": (
            len(flips) / len(subsequent) if subsequent else None
        ),
        "phaseCounts": _counter(phase_counts),
        "macroPhaseCounts": _counter(macro_counts),
        "eventTypeByPhase": {
            key: _counter(value) for key, value in sorted(by_type_phase.items())
        },
        "eventTypeByMacroPhase": {
            key: _counter(value) for key, value in sorted(by_type_macro.items())
        },
        "firstEntrySecondsLeft": _quantiles(
            [float(row["seconds_left"]) for row in first if row["seconds_left"] is not None]
        ),
        "allSubsequentGapMs": _quantiles(gaps_all),
        "sameSideReentryGapMs": _quantiles(gaps_same),
        "sideFlipGapMs": _quantiles(gaps_flip),
        "sameSideReentryTiming": same_side_within,
        "phaseAlignment": {
            "alignedEvents": len(aligned),
            "unmatchedEvents": len(events) - len(aligned),
            "alignedShare": len(aligned) / len(events) if events else None,
            "alignmentDeltaMs": _quantiles(
                [
                    float(row["phase_alignment_delta_ms"])
                    for row in aligned
                    if row["phase_alignment_delta_ms"] is not None
                ]
            ),
            "note": (
                "Nearest public snapshot is used only to estimate seconds_left/phase. "
                "It is not used as a causal pre-event feature row."
            ),
        },
    }


def _riskset_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    state_counts = Counter(str(row["position_state"]) for row in rows)
    next_counts = Counter(str(row["next_target_event_type"]) for row in rows)
    phase_counts = Counter(str(row["phase"]) for row in rows)
    by_phase_next: dict[str, Counter[str]] = defaultdict(Counter)
    by_state_next: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        by_phase_next[str(row["phase"])][str(row["next_target_event_type"])] += 1
        by_state_next[str(row["position_state"])][str(row["next_target_event_type"])] += 1

    label_rates: dict[str, Any] = {}
    for horizon in (1, 2, 5):
        key = f"label_next_target_taker_any_{horizon}s"
        label_rates[f"next{horizon}s"] = (
            sum(int(row[key]) for row in rows) / len(rows) if rows else None
        )

    return {
        "rows": len(rows),
        "positionStates": _counter(state_counts),
        "nextEventTypes": _counter(next_counts),
        "phaseCounts": _counter(phase_counts),
        "nextEventTypeByPhase": {
            key: _counter(value) for key, value in sorted(by_phase_next.items())
        },
        "nextEventTypeByPositionState": {
            key: _counter(value) for key, value in sorted(by_state_next.items())
        },
        "labelPositiveRates": label_rates,
        "researchOnlyColumns": [
            "target_parent_count_before_decision",
            "position_state",
            "previous_target_side",
            "previous_target_parent_ms",
            "ms_since_previous_target_parent",
            "next_target_parent_ms",
            "next_target_side",
            "next_target_event_type",
            "ms_to_next_target_parent",
        ],
        "leakageWarning": (
            "The risk-set CSV contains retrospective Target-event annotations for research. "
            "Do NOT expose next_* ground-truth columns to a runtime model."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reconstruct Target Taker parent-level behavior across each BTC 5m market: "
            "first entry, same-side reentry/add, and side flip, aligned to public seconds_left."
        )
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_ELIGIBILITY_DATASET)
    parser.add_argument("--shadow-db", type=Path, default=DEFAULT_SHADOW_DB)
    parser.add_argument("--official-target-db", type=Path, default=DEFAULT_OFFICIAL_TARGET_DB)
    parser.add_argument("--start", default=None, help="Optional ISO time or epoch ms.")
    parser.add_argument("--end", default=None, help="Optional ISO time or epoch ms; exclusive.")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--events-csv", type=Path, default=DEFAULT_EVENTS_CSV)
    parser.add_argument("--riskset-csv", type=Path, default=DEFAULT_RISKSET_CSV)
    args = parser.parse_args()

    start_ms = _epoch_ms(args.start)
    end_ms = _epoch_ms(args.end)
    if start_ms is not None and end_ms is not None and end_ms <= start_ms:
        raise SystemExit("--end must be after --start")

    print("TARGET_TAKER_INTRAMARKET_SEQUENCE_V1", flush=True)
    print("[1/5] Merge legacy + official Target TAKER parent truth...", flush=True)
    merged, source_stats, _deployed, excluded_market_id = _merge_labels(
        args.shadow_db, args.official_target_db
    )
    events = _filter_events(merged, start_ms=start_ms, end_ms=end_ms)
    if excluded_market_id is not None:
        events = [row for row in events if int(row["market_id"]) != int(excluded_market_id)]
    print(
        f"      merged parents={len(merged):,} | selected window parents={len(events):,}",
        flush=True,
    )

    print("[2/5] Load public per-second eligibility dataset...", flush=True)
    frame = _load_dataset(args.dataset)
    if start_ms is not None:
        frame = frame[frame["decision_sampled_at_ms"] >= int(start_ms)].copy()
    if end_ms is not None:
        frame = frame[frame["decision_sampled_at_ms"] < int(end_ms)].copy()
    print(
        f"      public rows={len(frame):,} | markets={frame['market_id'].nunique():,}",
        flush=True,
    )

    print("[3/5] Reconstruct parent sequence + align event phase...", flush=True)
    phase_index = _dataset_index(frame)
    sequence = _sequence_rows(events, phase_index)
    event_columns = [
        "market_id",
        "event_index",
        "market_event_count",
        "parent_id",
        "order_hash",
        "label_source",
        "target_event_ms",
        "target_event_taipei",
        "side",
        "event_type",
        "gap_from_previous_parent_ms",
        "elapsed_from_first_parent_ms",
        "aligned_public_sample_ms",
        "phase_alignment_delta_ms",
        "seconds_left",
        "phase",
        "macro_phase",
    ]
    _write_csv(args.events_csv, sequence, event_columns)

    print("[4/5] Build retrospective per-second risk-set state...", flush=True)
    riskset = _riskset_rows(frame, events)
    riskset_columns = [
        "market_id",
        "decision_sampled_at_ms",
        "decision_bucket_start_ms",
        "seconds_left",
        "phase",
        "macro_phase",
        "target_parent_count_before_decision",
        "position_state",
        "previous_target_side",
        "previous_target_parent_ms",
        "ms_since_previous_target_parent",
        "next_target_parent_ms",
        "next_target_side",
        "next_target_event_type",
        "ms_to_next_target_parent",
        "label_next_target_taker_any_1s",
        "label_next_target_taker_any_2s",
        "label_next_target_taker_any_5s",
    ]
    _write_csv(args.riskset_csv, riskset, riskset_columns)

    print("[5/5] Aggregate first-entry / reentry / flip timing...", flush=True)
    report = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "window": {
            "startMs": start_ms,
            "startTaipei": _iso_taipei(start_ms),
            "endMs": end_ms,
            "endTaipei": _iso_taipei(end_ms),
        },
        "inputs": {
            "dataset": str(args.dataset.expanduser().resolve()),
            "shadowDb": str(args.shadow_db.expanduser().resolve()),
            "officialTargetDb": str(args.official_target_db.expanduser().resolve()),
            "targetSources": source_stats,
        },
        "outputs": {
            "eventsCsv": str(args.events_csv.expanduser().resolve()),
            "risksetCsv": str(args.riskset_csv.expanduser().resolve()),
        },
        "phaseDefinition": {
            "fine": [name for _low, _high, name in PHASE_BINS],
            "macro": {
                "OPEN": "seconds_left > 180",
                "MID": "60 < seconds_left <= 180",
                "TAIL": "seconds_left <= 60",
            },
        },
        "events": _event_report(sequence),
        "riskSet": _riskset_report(riskset),
        "nextResearchStep": (
            "Score frozen16 continuously on every public second, report first-entry versus "
            "same-side-reentry capture by phase, then connect PUBLIC_ACTOR reentry only after "
            "the post-first-entry risk set is validated."
        ),
    }
    resolved_report = args.report.expanduser().resolve()
    resolved_report.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved_report.with_suffix(resolved_report.suffix + ".tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(resolved_report)

    events_summary = report["events"]
    print("\nINTRAMARKET SUMMARY", flush=True)
    print(
        f"markets={events_summary.get('markets', 0):,} parents={events_summary.get('events', 0):,} | "
        f"types={events_summary.get('eventTypes', {})}",
        flush=True,
    )
    print(
        f"macro phases={events_summary.get('macroPhaseCounts', {})}",
        flush=True,
    )
    print(
        f"same-side share among subsequent={events_summary.get('sameSideShareAmongSubsequent')}",
        flush=True,
    )
    print(f"events CSV:  {args.events_csv.expanduser().resolve()}", flush=True)
    print(f"riskset CSV: {args.riskset_csv.expanduser().resolve()}", flush=True)
    print(f"report:      {resolved_report}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
