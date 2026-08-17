from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVENTS = ROOT / "data" / "research" / "target_taker_intramarket_sequence_v1_events.csv"
DEFAULT_SCORES = ROOT / "data" / "research" / "target_taker_continuous_hazard_v1_scores.csv"
DEFAULT_HAZARD_REPORT = ROOT / "data" / "research" / "target_taker_continuous_hazard_v1_report.json"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_taker_action_bursts_v1_report.json"
DEFAULT_BURSTS = ROOT / "data" / "research" / "target_taker_action_bursts_v1.csv"
REPORT_VERSION = "TARGET_TAKER_ACTION_BURSTS_V1"
DEFAULT_GAP_SECONDS = (0, 1, 2, 5)
CAPTURE_MODES = ("OPENING_ONLY", "FIRST_SIGNAL_ONCE", "CONTINUOUS")


def _parse_gap_seconds(text: str) -> list[int]:
    values: list[int] = []
    for raw in str(text).split(","):
        raw = raw.strip()
        if not raw:
            continue
        value = int(raw)
        if value < 0:
            raise ValueError("burst gap seconds must be >= 0")
        if value not in values:
            values.append(value)
    if not values:
        raise ValueError("at least one burst gap is required")
    return values


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(resolved)


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(resolved)


def _load_events(path: Path) -> list[dict[str, Any]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    result: list[dict[str, Any]] = []
    with resolved.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            row = dict(raw)
            row["market_id"] = int(raw["market_id"])
            row["target_event_ms"] = int(raw["target_event_ms"])
            row["side"] = str(raw.get("side") or "").upper()
            row["macro_phase"] = str(raw.get("macro_phase") or "UNKNOWN")
            row["phase"] = str(raw.get("phase") or "UNKNOWN")
            result.append(row)
    result.sort(
        key=lambda row: (
            int(row["market_id"]),
            int(row["target_event_ms"]),
            int(row.get("event_index") or 0),
            str(row.get("parent_id") or ""),
        )
    )
    return result


def _load_scores(path: Path) -> list[dict[str, Any]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    result: list[dict[str, Any]] = []
    with resolved.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            sampled = int(raw["decision_sampled_at_ms"])
            row: dict[str, Any] = {
                "market_id": int(raw["market_id"]),
                "decision_sampled_at_ms": sampled,
                "decision_bucket_start_ms": (sampled // 1000) * 1000,
                "seconds_left": _number(raw.get("seconds_left")),
                "macro_phase": str(raw.get("macro_phase") or "UNKNOWN"),
                "position_state": str(raw.get("position_state") or "UNKNOWN"),
            }
            for horizon in (5, 2, 1):
                key = f"raw_score_{horizon}s"
                if raw.get(key) not in (None, ""):
                    row[key] = float(raw[key])
            result.append(row)
    result.sort(key=lambda row: (row["market_id"], row["decision_sampled_at_ms"]))
    return result


def _load_raw_gate_rules(path: Path) -> dict[int, list[dict[str, Any]]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    result: dict[int, list[dict[str, Any]]] = {}
    for task_name, task in payload.get("tasks", {}).items():
        if not task_name.startswith("hazard_") or not task_name.endswith("s"):
            continue
        try:
            horizon = int(task_name[len("hazard_") : -1])
        except ValueError:
            continue
        rules: list[dict[str, Any]] = []
        for gate in task.get("gates", []):
            if str(gate.get("variant")) != "RAW":
                continue
            rules.append(
                {
                    "name": str(gate.get("name")),
                    "calibrationTopFraction": gate.get("calibrationTopFraction"),
                    "threshold": float(gate["threshold"]),
                }
            )
        if rules:
            result[horizon] = rules
    return result


def _cluster_market_events(events: list[dict[str, Any]], gap_ms: int) -> list[list[dict[str, Any]]]:
    if not events:
        return []
    groups: list[list[dict[str, Any]]] = [[events[0]]]
    for event in events[1:]:
        previous = groups[-1][-1]
        gap = int(event["target_event_ms"]) - int(previous["target_event_ms"])
        if gap <= int(gap_ms):
            groups[-1].append(event)
        else:
            groups.append([event])
    return groups


def _build_bursts(events: list[dict[str, Any]], gap_s: int) -> list[dict[str, Any]]:
    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_market[int(event["market_id"])].append(event)

    output: list[dict[str, Any]] = []
    for market_id in sorted(by_market):
        market_events = sorted(
            by_market[market_id],
            key=lambda row: (
                int(row["target_event_ms"]),
                int(row.get("event_index") or 0),
                str(row.get("parent_id") or ""),
            ),
        )
        clusters = _cluster_market_events(market_events, int(gap_s) * 1000)
        previous_clean_side = ""
        for burst_index, cluster in enumerate(clusters, 1):
            onset_ms = int(cluster[0]["target_event_ms"])
            end_ms = int(cluster[-1]["target_event_ms"])
            side_counts = Counter(str(row.get("side") or "").upper() for row in cluster)
            side_counts.pop("", None)
            clean_sides = sorted(side_counts)
            mixed = len(clean_sides) != 1
            clean_side = clean_sides[0] if len(clean_sides) == 1 else ""

            if burst_index == 1 and not mixed:
                burst_type = "FIRST_ENTRY"
            elif mixed:
                burst_type = "MIXED"
            elif clean_side and previous_clean_side and clean_side == previous_clean_side:
                burst_type = "SAME_SIDE_REENTRY"
            elif clean_side and previous_clean_side:
                burst_type = "SIDE_FLIP"
            else:
                # There is no reliable clean prior state because earlier burst(s) were mixed.
                burst_type = "STATE_UNKNOWN"

            first = cluster[0]
            output.append(
                {
                    "gap_seconds": int(gap_s),
                    "market_id": market_id,
                    "burst_index": burst_index,
                    "market_burst_count": len(clusters),
                    "burst_onset_ms": onset_ms,
                    "burst_end_ms": end_ms,
                    "burst_duration_ms": end_ms - onset_ms,
                    "parent_count": len(cluster),
                    "unique_event_seconds": len({int(row["target_event_ms"]) for row in cluster}),
                    "side": clean_side,
                    "up_parent_count": int(side_counts.get("UP", 0)),
                    "down_parent_count": int(side_counts.get("DOWN", 0)),
                    "mixed_sides": bool(mixed),
                    "burst_type": burst_type,
                    "phase": str(first.get("phase") or "UNKNOWN"),
                    "macro_phase": str(first.get("macro_phase") or "UNKNOWN"),
                    "first_parent_event_type": str(first.get("event_type") or "UNKNOWN"),
                }
            )
            # Conservative state rule: mixed bursts do not update direction state because
            # parent timestamps are second-quantized and their within-second order is unsafe.
            if clean_side:
                previous_clean_side = clean_side
    return output


def _summary(bursts: list[dict[str, Any]], parent_count: int) -> dict[str, Any]:
    burst_count = len(bursts)
    market_counts = Counter(int(row["market_id"]) for row in bursts)
    type_counts = Counter(str(row["burst_type"]) for row in bursts)
    macro_counts = Counter(str(row["macro_phase"]) for row in bursts)
    phase_counts = Counter(str(row["phase"]) for row in bursts)
    mixed_count = int(type_counts.get("MIXED", 0))
    clean_subsequent = int(type_counts.get("SAME_SIDE_REENTRY", 0) + type_counts.get("SIDE_FLIP", 0))
    by_type_macro: dict[str, Counter[str]] = defaultdict(Counter)
    for row in bursts:
        by_type_macro[str(row["burst_type"])][str(row["macro_phase"])] += 1

    return {
        "parents": int(parent_count),
        "bursts": burst_count,
        "markets": len(market_counts),
        "compressionParentsPerBurst": parent_count / burst_count if burst_count else None,
        "burstCountPerMarket": _quantiles([float(v) for v in market_counts.values()]),
        "parentsPerBurst": _quantiles([float(row["parent_count"]) for row in bursts]),
        "burstDurationMs": _quantiles([float(row["burst_duration_ms"]) for row in bursts]),
        "uniqueEventSecondsPerBurst": _quantiles(
            [float(row["unique_event_seconds"]) for row in bursts]
        ),
        "burstTypes": {key: int(value) for key, value in sorted(type_counts.items())},
        "burstTypeShares": {
            key: value / burst_count if burst_count else None
            for key, value in sorted(type_counts.items())
        },
        "mixedBurstShare": mixed_count / burst_count if burst_count else None,
        "sameSideShareAmongCleanSubsequent": (
            type_counts.get("SAME_SIDE_REENTRY", 0) / clean_subsequent
            if clean_subsequent
            else None
        ),
        "sideFlipShareAmongCleanSubsequent": (
            type_counts.get("SIDE_FLIP", 0) / clean_subsequent
            if clean_subsequent
            else None
        ),
        "macroPhaseCounts": {key: int(value) for key, value in sorted(macro_counts.items())},
        "phaseCounts": {key: int(value) for key, value in sorted(phase_counts.items())},
        "burstTypeByMacroPhase": {
            key: {k: int(v) for k, v in sorted(counter.items())}
            for key, counter in sorted(by_type_macro.items())
        },
    }


def _score_indexes(scores: list[dict[str, Any]], horizon: int) -> dict[int, tuple[list[int], list[float]]]:
    key = f"raw_score_{horizon}s"
    by_market_bucket: dict[int, dict[int, float]] = defaultdict(dict)
    for row in scores:
        if key not in row:
            continue
        market_id = int(row["market_id"])
        bucket = int(row["decision_bucket_start_ms"])
        score = float(row[key])
        prior = by_market_bucket[market_id].get(bucket)
        if prior is None or score > prior:
            by_market_bucket[market_id][bucket] = score

    result: dict[int, tuple[list[int], list[float]]] = {}
    for market_id, mapping in by_market_bucket.items():
        ordered = sorted(mapping.items())
        result[market_id] = (
            [int(bucket) for bucket, _score in ordered],
            [float(score) for _bucket, score in ordered],
        )
    return result


def _burst_capture(
    bursts: list[dict[str, Any]],
    score_index: dict[int, tuple[list[int], list[float]]],
    *,
    threshold: float,
    horizon_s: int,
    mode: str,
) -> dict[str, Any]:
    horizon_ms = int(horizon_s) * 1000
    first_signal: dict[int, int | None] = {}
    opening_signal: dict[int, int | None] = {}
    if mode in {"FIRST_SIGNAL_ONCE", "OPENING_ONLY"}:
        for market_id, (times, values) in score_index.items():
            selected = [times[i] for i, score in enumerate(values) if score >= threshold]
            first_signal[market_id] = selected[0] if selected else None
            opening_signal[market_id] = (
                times[0] if times and values[0] >= threshold else None
            )

    captured = 0
    eligible = 0
    by_type: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_macro: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    lead_times: list[int] = []

    for burst in bursts:
        market_id = int(burst["market_id"])
        entry = score_index.get(market_id)
        if entry is None:
            continue
        eligible += 1
        onset_bucket = (int(burst["burst_onset_ms"]) // 1000) * 1000
        burst_type = str(burst["burst_type"])
        macro = str(burst["macro_phase"])
        by_type[burst_type][1] += 1
        by_macro[macro][1] += 1
        hit_time: int | None = None

        if mode == "CONTINUOUS":
            times, values = entry
            left = bisect.bisect_left(times, onset_bucket - horizon_ms)
            right = bisect.bisect_left(times, onset_bucket)
            for index in range(right - 1, left - 1, -1):
                if values[index] >= threshold:
                    hit_time = times[index]
                    break
        elif mode == "FIRST_SIGNAL_ONCE":
            candidate = first_signal.get(market_id)
            if candidate is not None and onset_bucket - horizon_ms <= candidate < onset_bucket:
                hit_time = candidate
        elif mode == "OPENING_ONLY":
            candidate = opening_signal.get(market_id)
            if candidate is not None and onset_bucket - horizon_ms <= candidate < onset_bucket:
                hit_time = candidate
        else:
            raise ValueError(mode)

        if hit_time is not None:
            captured += 1
            by_type[burst_type][0] += 1
            by_macro[macro][0] += 1
            lead_times.append(onset_bucket - hit_time)

    def summarize(groups: dict[str, list[int]]) -> dict[str, Any]:
        return {
            key: {
                "captured": values[0],
                "bursts": values[1],
                "captureRate": values[0] / values[1] if values[1] else None,
            }
            for key, values in sorted(groups.items())
        }

    return {
        "mode": mode,
        "capturedBursts": captured,
        "eligibleBursts": eligible,
        "captureRate": captured / eligible if eligible else None,
        "byBurstType": summarize(by_type),
        "byMacroPhase": summarize(by_macro),
        "leadTimeMs": {
            "count": len(lead_times),
            "median": float(statistics.median(lead_times)) if lead_times else None,
            "mean": float(statistics.fmean(lead_times)) if lead_times else None,
        },
    }


def _trigger_precision(
    bursts: list[dict[str, Any]],
    score_index: dict[int, tuple[list[int], list[float]]],
    *,
    threshold: float,
    horizon_s: int,
) -> dict[str, Any]:
    horizon_ms = int(horizon_s) * 1000
    onset_by_market: dict[int, list[int]] = defaultdict(list)
    for burst in bursts:
        onset_by_market[int(burst["market_id"])].append(
            (int(burst["burst_onset_ms"]) // 1000) * 1000
        )
    for market_id in onset_by_market:
        onset_by_market[market_id] = sorted(set(onset_by_market[market_id]))

    selected_rows = 0
    leading_rows = 0
    selected_markets: set[int] = set()
    for market_id, (times, values) in score_index.items():
        onsets = onset_by_market.get(market_id, [])
        for bucket, score in zip(times, values):
            if score < threshold:
                continue
            selected_rows += 1
            selected_markets.add(market_id)
            pos = bisect.bisect_right(onsets, bucket)
            if pos < len(onsets) and 0 < onsets[pos] - bucket <= horizon_ms:
                leading_rows += 1

    return {
        "selectedSignalRows": selected_rows,
        "selectedMarkets": len(selected_markets),
        "signalRowsLeadingToBurst": leading_rows,
        "burstWindowPrecision": leading_rows / selected_rows if selected_rows else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Collapse second-quantized Target Taker parents into candidate action bursts "
            "and replay frozen16 hazard capture against burst onsets."
        )
    )
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--scores", type=Path, default=DEFAULT_SCORES)
    parser.add_argument("--hazard-report", type=Path, default=DEFAULT_HAZARD_REPORT)
    parser.add_argument("--gap-seconds", default=",".join(str(v) for v in DEFAULT_GAP_SECONDS))
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--bursts-csv", type=Path, default=DEFAULT_BURSTS)
    args = parser.parse_args()

    try:
        gaps = _parse_gap_seconds(args.gap_seconds)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    print("TARGET_TAKER_ACTION_BURSTS_V1", flush=True)
    print("[1/5] Load aligned Target parent sequence...", flush=True)
    events = _load_events(args.events)
    if not events:
        raise SystemExit("no Target parent events found")
    print(
        f"      parents={len(events):,} markets={len(set(int(r['market_id']) for r in events)):,}",
        flush=True,
    )

    print("[2/5] Load existing per-second frozen16 scores + historical gate thresholds...", flush=True)
    scores = _load_scores(args.scores)
    rules = _load_raw_gate_rules(args.hazard_report)
    if not scores:
        raise SystemExit("no per-second hazard scores found")
    print(
        f"      score rows={len(scores):,} | horizons={sorted(rules)} | no EBM refit required",
        flush=True,
    )

    print("[3/5] Cluster parents into action bursts for gap sensitivity...", flush=True)
    bursts_by_gap: dict[int, list[dict[str, Any]]] = {}
    all_bursts: list[dict[str, Any]] = []
    for index, gap_s in enumerate(gaps, 1):
        bursts = _build_bursts(events, gap_s)
        bursts_by_gap[gap_s] = bursts
        all_bursts.extend(bursts)
        summary = _summary(bursts, len(events))
        print(
            f"      [{index}/{len(gaps)}] gap<={gap_s}s: bursts={summary['bursts']:,} "
            f"parents/burst={summary['compressionParentsPerBurst']:.2f} "
            f"mixed={summary['mixedBurstShare']:.1%}",
            flush=True,
        )

    print("[4/5] Replay burst-onset capture using existing frozen16 scores...", flush=True)
    sensitivity: dict[str, Any] = {}
    for gap_s in gaps:
        bursts = bursts_by_gap[gap_s]
        entry: dict[str, Any] = {
            "summary": _summary(bursts, len(events)),
            "hazardReplay": {},
        }
        for horizon in sorted(rules, reverse=True):
            score_index = _score_indexes(scores, horizon)
            if not score_index:
                continue
            gate_results: list[dict[str, Any]] = []
            for rule in rules[horizon]:
                threshold = float(rule["threshold"])
                gate_results.append(
                    {
                        **rule,
                        "triggerPrecision": _trigger_precision(
                            bursts,
                            score_index,
                            threshold=threshold,
                            horizon_s=horizon,
                        ),
                        "burstCapture": {
                            mode: _burst_capture(
                                bursts,
                                score_index,
                                threshold=threshold,
                                horizon_s=horizon,
                                mode=mode,
                            )
                            for mode in CAPTURE_MODES
                        },
                    }
                )
            entry["hazardReplay"][f"hazard_{horizon}s"] = gate_results
        sensitivity[str(gap_s)] = entry

    print("[5/5] Write report + burst table...", flush=True)
    columns = [
        "gap_seconds",
        "market_id",
        "burst_index",
        "market_burst_count",
        "burst_onset_ms",
        "burst_end_ms",
        "burst_duration_ms",
        "parent_count",
        "unique_event_seconds",
        "side",
        "up_parent_count",
        "down_parent_count",
        "mixed_sides",
        "burst_type",
        "phase",
        "macro_phase",
        "first_parent_event_type",
    ]
    _write_csv(args.bursts_csv, all_bursts, columns)
    report = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "purpose": (
            "Test whether dense Target parent orders represent fewer strategy-level action bursts, "
            "then evaluate frozen16 timing against burst onsets rather than every parent order."
        ),
        "clustering": {
            "gapSeconds": gaps,
            "rule": (
                "Within each market, adjacent parents join the same burst when their timestamp gap "
                "is <= the configured threshold. gap=0s therefore merges all same-second parents."
            ),
            "mixedStateRule": (
                "A burst containing both UP and DOWN is MIXED and does not update the clean direction "
                "state used to label later SAME_SIDE_REENTRY versus SIDE_FLIP. This is conservative "
                "because parent timestamps are only second-quantized."
            ),
        },
        "inputs": {
            "events": str(args.events.expanduser().resolve()),
            "scores": str(args.scores.expanduser().resolve()),
            "hazardReport": str(args.hazard_report.expanduser().resolve()),
            "parents": len(events),
            "scoreRows": len(scores),
        },
        "sensitivity": sensitivity,
        "outputs": {
            "report": str(args.report.expanduser().resolve()),
            "burstsCsv": str(args.bursts_csv.expanduser().resolve()),
        },
        "interpretationGuide": {
            "compressionParentsPerBurst": (
                "How many parent orders collapse into one candidate strategy action. Large changes "
                "across 0/1/2/5s mean the decision-unit definition is sensitive to the gap threshold."
            ),
            "mixedBurstShare": (
                "High MIXED share suggests apparent parent-level SIDE_FLIP activity includes split execution, "
                "rebalancing, or timestamp-order ambiguity rather than independent directional decisions."
            ),
            "burstWindowPrecision": (
                "Among threshold-crossing score rows, the share followed by a distinct burst onset within the horizon."
            ),
            "burstCapture": (
                "Recall of distinct burst onsets. This is the preferred timing metric over parent-event capture."
            ),
            "warning": (
                "Burst clustering is a retrospective research abstraction, not proof of Target intent. "
                "Do not translate burst counts directly into live order counts or sizing."
            ),
        },
    }
    _write_json(args.report, report)

    print("\nBURST SENSITIVITY COMPLETE", flush=True)
    for gap_s in gaps:
        summary = sensitivity[str(gap_s)]["summary"]
        print(
            f"  gap<={gap_s}s | bursts={summary['bursts']:,} | "
            f"parents/burst={summary['compressionParentsPerBurst']:.2f} | "
            f"mixed={summary['mixedBurstShare']:.1%} | "
            f"types={summary['burstTypes']}",
            flush=True,
        )
        hazard_5s = sensitivity[str(gap_s)]["hazardReplay"].get("hazard_5s", [])
        for gate in hazard_5s:
            if gate.get("name") == "CAL_TOP_20PCT":
                continuous = gate["burstCapture"]["CONTINUOUS"]
                precision = gate["triggerPrecision"]["burstWindowPrecision"]
                print(
                    f"      5s TOP20: continuous burst capture={continuous['captureRate']} "
                    f"burst-window precision={precision}",
                    flush=True,
                )
                break
    print(f"report: {args.report.expanduser().resolve()}", flush=True)
    print(f"bursts: {args.bursts_csv.expanduser().resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
