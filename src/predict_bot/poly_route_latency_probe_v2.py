from __future__ import annotations

import argparse
import json
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


def _safe_label(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value.strip())
    return cleaned[:80] or "route"


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


class PolyRouteLatencyProbeV2(PolyFeedLatencyProbe):
    """V2 keeps raw WS timestamp deltas even when negative or over 60 seconds."""

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
        self._last_ws_mono_ns: int | None = None

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
                            self._record_ws_state(change, int(received_ms), source_ms)
            elif event_type in {"book", "best_bid_ask"}:
                self._record_ws_state(event, int(received_ms), source_ms)

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
        payload.update({
            "version": "poly_route_latency_probe_v2",
            "label": self.label,
            "wsRawSourceAgeMs": _stats(raw_age),
            "wsInterArrivalMs": _stats(interarrival),
            "wsEventTypes": by_type,
            "wsTimestampDiagnostics": {
                **dict(self.ws_timestamp_diag),
                "rawTimestampExamplesByEventType": dict(self.ws_timestamp_examples),
            },
            "measurementMeaningV2": {
                "wsRawSourceAgeMs": "local receive wall clock minus Polymarket event timestamp; negative values are preserved",
                "wsInterArrivalMs": "monotonic local gap between successive raw WS messages",
            },
            "measurementWarnings": [
                "Raw source age is useful for same-machine nearby-run A/B only when clock offset is stable; it is not proof of pure one-way network latency.",
                "REST-minus-WS identical-hash timing still includes REST polling phase and backend publication semantics.",
                "Compare price_change and book rawSourceAgeMs separately before choosing a route.",
            ],
        })
        return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Polymarket route latency probe V2")
    parser.add_argument("--label", default="DIRECT")
    parser.add_argument("--minutes", type=float, default=3.0)
    parser.add_argument("--poll-ms", type=int, default=DEFAULT_POLL_MS)
    parser.add_argument("--report-seconds", type=float, default=DEFAULT_REPORT_SECONDS)
    parser.add_argument("--output")
    args = parser.parse_args()
    label = _safe_label(args.label)
    output = Path(args.output) if args.output else ROOT / "data" / f"poly_route_latency_v2_{label}.json"
    duration = None if args.minutes <= 0 else max(1.0, args.minutes * 60.0)
    probe = PolyRouteLatencyProbeV2(args.poll_ms, args.report_seconds, output, label=label)
    return probe.run(duration)


if __name__ == "__main__":
    raise SystemExit(main())
