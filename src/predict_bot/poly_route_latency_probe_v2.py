from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .poly_feed_latency_probe import (
    DEFAULT_POLL_MS,
    DEFAULT_REPORT_SECONDS,
    ROOT,
    PolyFeedLatencyProbe,
    _timestamp_ms,
)


MAX_RAW_SAMPLES = 250_000
POLY_TIME_URL = "https://clob.polymarket.com/time"


def _safe_label(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value.strip())
    return cleaned[:80] or "route"


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(len(ordered) - 1, lo + 1)
    weight = pos - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def _stats(values: list[float]) -> dict[str, float | int | None]:
    clean = [float(v) for v in values]
    return {
        "count": len(clean),
        "mean": statistics.fmean(clean) if clean else None,
        "median": statistics.median(clean) if clean else None,
        "p10": _percentile(clean, 0.10),
        "p90": _percentile(clean, 0.90),
        "p95": _percentile(clean, 0.95),
        "p99": _percentile(clean, 0.99),
        "min": min(clean) if clean else None,
        "max": max(clean) if clean else None,
    }


def _pick(payload: dict[str, Any], *path: str) -> float | None:
    value: Any = payload
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return _finite(value)


def _delta(current: dict[str, Any], baseline: dict[str, Any], *path: str) -> dict[str, float | None]:
    now = _pick(current, *path)
    before = _pick(baseline, *path)
    delta = now - before if now is not None and before is not None else None
    pct = (delta / before * 100.0) if delta is not None and before not in (None, 0.0) else None
    return {"baselineMs": before, "currentMs": now, "deltaMs": delta, "deltaPct": pct}


def compare_summaries(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    return {
        "baselineLabel": baseline.get("label"),
        "currentLabel": current.get("label"),
        "median": {
            "wsRawSourceAge": _delta(current, baseline, "wsRawSourceAgeMs", "median"),
            "priceChangeRawSourceAge": _delta(
                current, baseline, "wsEventTypes", "price_change", "rawSourceAgeMs", "median"
            ),
            "bookRawSourceAge": _delta(current, baseline, "wsEventTypes", "book", "rawSourceAgeMs", "median"),
            "restRtt": _delta(current, baseline, "restRttMs", "median"),
        },
        "p95": {
            "wsRawSourceAge": _delta(current, baseline, "wsRawSourceAgeMs", "p95"),
            "priceChangeRawSourceAge": _delta(
                current, baseline, "wsEventTypes", "price_change", "rawSourceAgeMs", "p95"
            ),
            "bookRawSourceAge": _delta(current, baseline, "wsEventTypes", "book", "rawSourceAgeMs", "p95"),
            "wsInterArrival": _delta(current, baseline, "wsInterArrivalMs", "p95"),
            "restRtt": _delta(current, baseline, "restRttMs", "p95"),
        },
        "notes": [
            "Lower raw source age is better only if both runs use the same machine and clock offset stayed stable.",
            "Prefer price_change-to-price_change and book-to-book comparisons instead of mixing event semantics.",
            "Identical-hash REST-vs-WS timing remains diagnostic only because REST polling phase is part of the metric.",
        ],
    }


class PolyRouteLatencyProbeV2(PolyFeedLatencyProbe):
    """V2 preserves raw source ages and separates WebSocket event semantics."""

    def __init__(self, poll_ms: int, report_seconds: float, output_path: Path, *, label: str) -> None:
        super().__init__(poll_ms, report_seconds, output_path)
        self.label = label
        self.ws_event_counts: Counter[str] = Counter()
        self.ws_timestamp_diag: Counter[str] = Counter()
        self.ws_timestamp_diag_by_type: dict[str, Counter[str]] = defaultdict(Counter)
        self.ws_raw_age_ms: list[float] = []
        self.ws_raw_age_by_type: dict[str, list[float]] = defaultdict(list)
        self.ws_timestamp_examples: dict[str, list[str]] = defaultdict(list)
        self.ws_interarrival_ms: list[float] = []
        self.ws_hash_event_type: dict[tuple[str, str], str] = {}
        self._last_ws_mono_ns: int | None = None
        self.clock_calibration: dict[str, Any] | None = None

    def _trim(self, values: list[float]) -> None:
        if len(values) > MAX_RAW_SAMPLES:
            del values[: len(values) - MAX_RAW_SAMPLES]

    def _record_timestamp(
        self,
        event_type: str,
        raw_timestamp: Any,
        source_ms: int | None,
        received_ms: float,
    ) -> None:
        diag = self.ws_timestamp_diag_by_type[event_type]
        self.ws_timestamp_diag["eventsSeen"] += 1
        diag["eventsSeen"] += 1
        if raw_timestamp in (None, ""):
            self.ws_timestamp_diag["missingTimestamp"] += 1
            diag["missingTimestamp"] += 1
            return
        if len(self.ws_timestamp_examples[event_type]) < 4:
            self.ws_timestamp_examples[event_type].append(str(raw_timestamp))
        if source_ms is None:
            self.ws_timestamp_diag["unparseableTimestamp"] += 1
            diag["unparseableTimestamp"] += 1
            return
        self.ws_timestamp_diag["parsedTimestamp"] += 1
        diag["parsedTimestamp"] += 1
        age_ms = float(received_ms - source_ms)
        self.ws_raw_age_ms.append(age_ms)
        self.ws_raw_age_by_type[event_type].append(age_ms)
        self._trim(self.ws_raw_age_ms)
        self._trim(self.ws_raw_age_by_type[event_type])
        if age_ms < 0:
            self.ws_timestamp_diag["negativeRawAge"] += 1
            diag["negativeRawAge"] += 1
        elif age_ms >= 60_000:
            self.ws_timestamp_diag["rawAgeAtLeast60s"] += 1
            diag["rawAgeAtLeast60s"] += 1
        else:
            self.ws_timestamp_diag["acceptedNonNegativeUnder60s"] += 1
            diag["acceptedNonNegativeUnder60s"] += 1
            self.ws_source_ages_ms.append(age_ms)

    def _remember_hash_event_type(self, event: dict[str, Any], event_type: str) -> None:
        token = str(event.get("asset_id") or "")
        book_hash = str(event.get("hash") or "")
        if not token or not book_hash:
            return
        with self.lock:
            if len(self.ws_hash_event_type) >= MAX_RAW_SAMPLES:
                self.ws_hash_event_type.clear()
            self.ws_hash_event_type[(token, book_hash)] = event_type

    def _on_ws_message(self, raw: str, generation: int) -> None:
        if raw == "PONG":
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        events = payload if isinstance(payload, list) else [payload]
        received_ms = time.time_ns() / 1_000_000.0
        mono_ns = time.perf_counter_ns()
        with self.lock:
            if generation != self.generation:
                return
            if self._last_ws_mono_ns is not None:
                self.ws_interarrival_ms.append((mono_ns - self._last_ws_mono_ns) / 1_000_000.0)
                self._trim(self.ws_interarrival_ms)
            self._last_ws_mono_ns = mono_ns
            self.ws_messages += 1
            self.last_ws_at_ms = int(received_ms)

        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("event_type") or "unknown").strip().lower() or "unknown"
            raw_timestamp = event.get("timestamp")
            source_ms = _timestamp_ms(raw_timestamp)
            with self.lock:
                self.ws_event_counts[event_type] += 1
                self._record_timestamp(event_type, raw_timestamp, source_ms, received_ms)
            if event_type == "price_change":
                changes = event.get("price_changes")
                if isinstance(changes, list):
                    for change in changes:
                        if isinstance(change, dict):
                            self._remember_hash_event_type(change, event_type)
                            self._record_ws_state(change, int(received_ms), source_ms)
            elif event_type == "book":
                self._remember_hash_event_type(event, event_type)
                self._record_ws_state(event, int(received_ms), source_ms)

    def _clock_sanity_check(self, samples: int = 5) -> dict[str, Any]:
        rows: list[dict[str, float]] = []
        errors: list[str] = []
        for _ in range(max(1, samples)):
            send_ns = time.time_ns()
            started_ns = time.perf_counter_ns()
            try:
                response = self.http.get(POLY_TIME_URL)
                response.raise_for_status()
                server_seconds = int(float(response.json()))
                recv_ns = time.time_ns()
                rtt_ms = (time.perf_counter_ns() - started_ns) / 1_000_000.0
                local_mid_ms = (send_ns + recv_ns) / 2_000_000.0
                server_center_ms = server_seconds * 1000.0 + 499.5
                rows.append({
                    "rttMs": rtt_ms,
                    "localMinusPolyApproxMs": local_mid_ms - server_center_ms,
                    "uncertaintyAtLeastMs": 500.0 + rtt_ms / 2.0,
                })
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
            time.sleep(0.05)
        if not rows:
            return {"usable": False, "samples": 0, "errors": errors[-3:]}
        best = min(rows, key=lambda row: row["rttMs"])
        return {
            "usable": True,
            "samples": len(rows),
            "medianRttMs": statistics.median(row["rttMs"] for row in rows),
            "medianLocalMinusPolyApproxMs": statistics.median(row["localMinusPolyApproxMs"] for row in rows),
            "bestSampleRttMs": best["rttMs"],
            "bestSampleLocalMinusPolyApproxMs": best["localMinusPolyApproxMs"],
            "bestSampleUncertaintyAtLeastMs": best["uncertaintyAtLeastMs"],
            "errors": errors[-3:],
            "note": (
                "Coarse clock sanity check only. Polymarket /time is documented in whole Unix seconds, "
                "so uncertainty is at least about +/-500ms plus half RTT."
            ),
        }

    def _matched_by_event_type(self) -> dict[str, Any]:
        grouped: dict[str, list[float]] = defaultdict(list)
        for row in self.matches:
            key = (str(row.get("token") or ""), str(row.get("hash") or ""))
            event_type = self.ws_hash_event_type.get(key, "unknown")
            value = _finite(row.get("restMinusWsMs"))
            if value is not None:
                grouped[event_type].append(value)
        result: dict[str, Any] = {}
        for event_type, values in sorted(grouped.items()):
            result[event_type] = {
                "matchedBookStates": len(values),
                "wsFirst": sum(value > 0 for value in values),
                "restFirst": sum(value < 0 for value in values),
                "ties": sum(value == 0 for value in values),
                "restMinusWsMs": _stats(values),
            }
        return result

    def summary(self) -> dict[str, Any]:
        payload = super().summary()
        with self.lock:
            by_type = {
                event_type: {
                    "messages": int(self.ws_event_counts[event_type]),
                    "timestampDiagnostics": dict(self.ws_timestamp_diag_by_type[event_type]),
                    "rawTimestampExamples": list(self.ws_timestamp_examples[event_type]),
                    "rawSourceAgeMs": _stats(list(self.ws_raw_age_by_type[event_type])),
                }
                for event_type in sorted(self.ws_event_counts)
            }
            raw_age = list(self.ws_raw_age_ms)
            interarrival = list(self.ws_interarrival_ms)
            matched_by_type = self._matched_by_event_type()
        payload.update({
            "version": "poly_route_latency_probe_v2",
            "label": self.label,
            "wsRawSourceAgeMs": _stats(raw_age),
            "wsInterArrivalMs": _stats(interarrival),
            "wsEventTypes": by_type,
            "matchesByWsEventType": matched_by_type,
            "wsTimestampDiagnostics": {
                **dict(self.ws_timestamp_diag),
                "rawTimestampExamplesByEventType": dict(self.ws_timestamp_examples),
            },
            "clockCalibration": self.clock_calibration,
            "measurementMeaningV2": {
                "wsRawSourceAgeMs": "local receive wall clock minus Polymarket event timestamp; negative values are preserved",
                "wsInterArrivalMs": "monotonic local gap between successive raw WS messages",
                "matchesByWsEventType": "identical hash REST-vs-WS timing split by book versus price_change",
            },
            "measurementWarnings": [
                "Raw source age is useful for same-machine nearby-run A/B only when clock offset is stable; it is not proof of pure one-way network latency.",
                "REST-minus-WS identical-hash timing includes REST polling phase and backend publication semantics.",
                "Polymarket /time is second-resolution, so clockCalibration detects gross skew but cannot correct 10ms-scale source ages.",
                "Compare price_change and book rawSourceAgeMs separately before choosing a route.",
            ],
        })
        return payload

    def run(self, duration_seconds: float | None) -> int:
        self.clock_calibration = self._clock_sanity_check()
        return super().run(duration_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="Polymarket route latency probe V2")
    parser.add_argument("--label", default="DIRECT")
    parser.add_argument("--minutes", type=float, default=3.0)
    parser.add_argument("--poll-ms", type=int, default=DEFAULT_POLL_MS)
    parser.add_argument("--report-seconds", type=float, default=DEFAULT_REPORT_SECONDS)
    parser.add_argument("--compare-to")
    parser.add_argument("--output")
    args = parser.parse_args()
    label = _safe_label(args.label)
    output = Path(args.output) if args.output else ROOT / "data" / f"poly_route_latency_v2_{label}.json"
    duration = None if args.minutes <= 0 else max(1.0, args.minutes * 60.0)
    probe = PolyRouteLatencyProbeV2(args.poll_ms, args.report_seconds, output, label=label)
    exit_code = probe.run(duration)

    if args.compare_to:
        payload = json.loads(output.read_text(encoding="utf-8"))
        baseline_path = Path(args.compare_to)
        if baseline_path.exists():
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            payload["comparison"] = compare_summaries(payload, baseline)
        else:
            payload["comparison"] = None
            payload["comparisonWarnings"] = [f"Baseline file not found: {baseline_path}"]
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
