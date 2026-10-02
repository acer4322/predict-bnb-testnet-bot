from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

# Importing the hardening module applies the existing read-only signed GET
# timestamp recovery patch process-wide.  The probe never instantiates the
# trading client and contains no order-placement path.
from . import binance_time_sync_hardening as _time_sync_hardening  # noqa: F401
from .core import BinancePredictionClient, binance_top_of_book
from .m_realtime import direct_rest_prediction_event


ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_REALTIME_URL = os.environ.get(
    "PREDICT_BINANCE_LATENCY_REALTIME_URL", "http://127.0.0.1:8766/api/realtime"
)
MARKET_REFERENCE_URL = os.environ.get(
    "PREDICT_BINANCE_LATENCY_MARKET_REFERENCE_URL",
    "http://127.0.0.1:8766/api/binance-market-reference",
)
DEFAULT_DIRECT_POLL_MS = max(
    200, int(os.environ.get("PREDICT_BINANCE_LATENCY_DIRECT_POLL_MS", "500"))
)
DEFAULT_COLLECTOR_POLL_MS = max(
    20, int(os.environ.get("PREDICT_BINANCE_LATENCY_COLLECTOR_POLL_MS", "50"))
)
DEFAULT_REPORT_SECONDS = max(
    2.0, float(os.environ.get("PREDICT_BINANCE_LATENCY_REPORT_SECONDS", "10"))
)
MAX_SEEN_STATES = 20_000
USER_AGENT = "BTC-5M-Lab-Binance-Prediction-Latency-Probe/1.0"


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lo = int(math.floor(position))
    hi = int(math.ceil(position))
    if lo == hi:
        return ordered[lo]
    weight = position - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def _stats(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "mean": statistics.fmean(values) if values else None,
        "median": statistics.median(values) if values else None,
        "p90": _percentile(values, 0.90),
        "p95": _percentile(values, 0.95),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def _credentials() -> tuple[str | None, str | None, str]:
    live_key = os.environ.get("BINANCE_LIVE_API_KEY")
    live_secret = os.environ.get("BINANCE_LIVE_API_SECRET")
    if live_key and live_secret:
        return live_key, live_secret, "BINANCE_LIVE_*"
    key = os.environ.get("BINANCE_API_KEY")
    secret = os.environ.get("BINANCE_API_SECRET")
    if key and secret:
        return key, secret, "BINANCE_API_*"
    return None, None, "UNAVAILABLE"


def _quote_signature(
    market_id: int,
    up_bid: float | None,
    up_ask: float | None,
    down_bid: float | None,
    down_ask: float | None,
) -> tuple[Any, ...] | None:
    values = (up_bid, up_ask, down_bid, down_ask)
    if any(value is None for value in values):
        return None
    return (
        "Q",
        int(market_id),
        *(round(float(value), 8) for value in values if value is not None),
    )


def _state_key(observation: dict[str, Any]) -> tuple[Any, ...] | None:
    market_id = _int(observation.get("marketId"))
    if market_id is None or market_id <= 0:
        return None
    up_version = _int(observation.get("upBookTimestampMs"))
    down_version = _int(observation.get("downBookTimestampMs"))
    if up_version and down_version:
        return ("V", market_id, up_version, down_version)
    return _quote_signature(
        market_id,
        _finite(observation.get("upBid")),
        _finite(observation.get("upAsk")),
        _finite(observation.get("downBid")),
        _finite(observation.get("downAsk")),
    )


def _latest_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    latest = payload.get("latest")
    if isinstance(latest, dict):
        return latest
    nested = payload.get("state")
    if isinstance(nested, dict) and isinstance(nested.get("latest"), dict):
        return nested["latest"]
    return None


def _collector_observation(latest: dict[str, Any], received_at_ms: int) -> dict[str, Any] | None:
    market_id = _int(latest.get("market_id") or latest.get("marketId"))
    if market_id is None or market_id <= 0:
        return None
    observation = {
        "source": "COLLECTOR_8766",
        "marketId": market_id,
        "upBid": _finite(latest.get("up_bid") if latest.get("up_bid") is not None else latest.get("upBid")),
        "upAsk": _finite(latest.get("up_ask") if latest.get("up_ask") is not None else latest.get("upAsk")),
        "downBid": _finite(latest.get("down_bid") if latest.get("down_bid") is not None else latest.get("downBid")),
        "downAsk": _finite(latest.get("down_ask") if latest.get("down_ask") is not None else latest.get("downAsk")),
        "upBookTimestampMs": _int(
            latest.get("up_book_timestamp_ms")
            if latest.get("up_book_timestamp_ms") is not None
            else latest.get("upBookTimestampMs")
        ),
        "downBookTimestampMs": _int(
            latest.get("down_book_timestamp_ms")
            if latest.get("down_book_timestamp_ms") is not None
            else latest.get("downBookTimestampMs")
        ),
        "bookAgeMs": _finite(
            latest.get("book_age_ms")
            if latest.get("book_age_ms") is not None
            else latest.get("bookAgeMs")
        ),
        "receivedAtMs": int(received_at_ms),
    }
    return observation if _state_key(observation) is not None else None


class BinanceFeedLatencyProbe:
    """Passive A/B probe for 8766 Prediction snapshots versus direct REST books.

    The production collector remains untouched.  The probe polls the local 8766
    realtime endpoint quickly, while independently fetching the same current UP
    and DOWN Binance Prediction order books through the read-only signed REST API.

    Identical states are paired by the two outcome book timestamps when available,
    falling back to an exact top-of-book signature. ``collectorMinusDirectMs`` is
    positive when direct REST observed that exact state before 8766 exposed it.
    """

    def __init__(
        self,
        *,
        direct_poll_ms: int,
        collector_poll_ms: int,
        report_seconds: float,
        output_path: Path,
    ) -> None:
        self.direct_poll_seconds = max(0.2, direct_poll_ms / 1000.0)
        self.collector_poll_seconds = max(0.02, collector_poll_ms / 1000.0)
        self.report_seconds = max(2.0, float(report_seconds))
        self.output_path = output_path
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.local_http = httpx.Client(
            timeout=httpx.Timeout(connect=1.0, read=1.0, write=1.0, pool=0.5),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4, keepalive_expiry=30.0),
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            transport=httpx.HTTPTransport(retries=0),
        )
        key, secret, source = _credentials()
        if not key or not secret:
            raise RuntimeError(
                "Binance Prediction read-only latency probe needs BINANCE_LIVE_API_KEY/SECRET "
                "or BINANCE_API_KEY/SECRET"
            )
        self.credential_source = source
        self.client = BinancePredictionClient(key, secret)
        self.book_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="binance-latency-book")

        self.current_reference: dict[str, Any] | None = None
        self.reference_market_id: int | None = None
        self.market_switches = 0

        self.collector_states: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.direct_states: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.matched_keys: set[tuple[Any, ...]] = set()
        self.key_fifo: deque[tuple[Any, ...]] = deque()
        self.matches: list[dict[str, Any]] = []

        self.collector_polls = 0
        self.collector_successes = 0
        self.collector_errors = 0
        self.direct_polls = 0
        self.direct_successes = 0
        self.direct_errors = 0
        self.collector_api_rtts_ms: list[float] = []
        self.direct_rest_rtts_ms: list[float] = []
        self.direct_book_ages_ms: list[float] = []
        self.collector_reported_book_ages_ms: list[float] = []
        self.quote_divergences: list[dict[str, float]] = []
        self.last_rate_limits: dict[str, str] = {}
        self.last_error: str | None = None
        self.started_at_ms = _now_ms()
        self.last_collector_at_ms: int | None = None
        self.last_direct_at_ms: int | None = None

    def close(self) -> None:
        self.stop_event.set()
        self.book_pool.shutdown(wait=False, cancel_futures=True)
        self.client.close()
        self.local_http.close()

    def _market_reference(self) -> dict[str, Any] | None:
        response = self.local_http.get(MARKET_REFERENCE_URL)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        reference = payload.get("marketReference")
        if not isinstance(reference, dict):
            return None
        try:
            normalized = {
                "marketId": int(reference.get("market_id") or reference.get("marketId")),
                "upTokenId": str(reference.get("up_token_id") or reference.get("upTokenId") or ""),
                "downTokenId": str(reference.get("down_token_id") or reference.get("downTokenId") or ""),
                "endMs": int(reference.get("end_ms") or reference.get("endMs") or 0),
            }
        except (TypeError, ValueError):
            return None
        if (
            normalized["marketId"] <= 0
            or not normalized["upTokenId"]
            or not normalized["downTokenId"]
            or normalized["upTokenId"] == normalized["downTokenId"]
        ):
            return None
        return normalized

    def _refresh_reference(self) -> dict[str, Any] | None:
        reference = self._market_reference()
        if reference is None:
            return None
        market_id = int(reference["marketId"])
        with self.lock:
            if market_id != self.reference_market_id:
                self.reference_market_id = market_id
                self.current_reference = dict(reference)
                self.market_switches += 1
                self.collector_states.clear()
                self.direct_states.clear()
                self.matched_keys.clear()
                self.key_fifo.clear()
            else:
                self.current_reference = dict(reference)
        return reference

    def _record_state(self, source: str, observation: dict[str, Any]) -> None:
        key = _state_key(observation)
        if key is None:
            return
        with self.lock:
            target = self.collector_states if source == "COLLECTOR" else self.direct_states
            if key in target:
                return
            target[key] = observation
            self.key_fifo.append(key)
            other = self.direct_states.get(key) if source == "COLLECTOR" else self.collector_states.get(key)
            if other is not None and key not in self.matched_keys:
                self.matched_keys.add(key)
                collector = self.collector_states[key]
                direct = self.direct_states[key]
                delta = float(collector["receivedAtMs"] - direct["receivedAtMs"])
                self.matches.append(
                    {
                        "marketId": observation.get("marketId"),
                        "stateKey": list(key),
                        "collectorReceivedAtMs": collector["receivedAtMs"],
                        "directReceivedAtMs": direct["receivedAtMs"],
                        "collectorMinusDirectMs": delta,
                        "upBid": direct.get("upBid"),
                        "upAsk": direct.get("upAsk"),
                        "downBid": direct.get("downBid"),
                        "downAsk": direct.get("downAsk"),
                        "upBookTimestampMs": direct.get("upBookTimestampMs"),
                        "downBookTimestampMs": direct.get("downBookTimestampMs"),
                    }
                )
            while len(self.key_fifo) > MAX_SEEN_STATES:
                old = self.key_fifo.popleft()
                self.collector_states.pop(old, None)
                self.direct_states.pop(old, None)
                self.matched_keys.discard(old)

    def _collector_loop(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            with self.lock:
                self.collector_polls += 1
            try:
                response = self.local_http.get(COLLECTOR_REALTIME_URL)
                response.raise_for_status()
                received_ms = _now_ms()
                rtt_ms = max(0.0, (time.monotonic() - started) * 1000.0)
                latest = _latest_payload(response.json())
                observation = _collector_observation(latest, received_ms) if latest else None
                with self.lock:
                    self.collector_successes += 1
                    self.collector_api_rtts_ms.append(rtt_ms)
                    self.last_collector_at_ms = received_ms
                    if observation and observation.get("bookAgeMs") is not None:
                        self.collector_reported_book_ages_ms.append(float(observation["bookAgeMs"]))
                if observation is not None:
                    self._record_state("COLLECTOR", observation)
            except Exception as exc:
                with self.lock:
                    self.collector_errors += 1
                    self.last_error = f"collector: {str(exc)[:300]}"
            self.stop_event.wait(max(0.0, self.collector_poll_seconds - (time.monotonic() - started)))

    def _direct_books(self, reference: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        market_id = int(reference["marketId"])
        up_future = self.book_pool.submit(
            self.client.orderbook, market_id, str(reference["upTokenId"])
        )
        down_future = self.book_pool.submit(
            self.client.orderbook, market_id, str(reference["downTokenId"])
        )
        return up_future.result(), down_future.result()

    def _direct_loop(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            with self.lock:
                self.direct_polls += 1
            try:
                reference = self._refresh_reference()
                if reference is None:
                    raise RuntimeError("8766 exact current Binance market reference unavailable")
                up_book, down_book = self._direct_books(reference)
                received_wall_ns = time.time_ns()
                received_mono_ns = time.monotonic_ns()
                received_ms = received_wall_ns // 1_000_000
                rtt_ms = max(0.0, (time.monotonic() - started) * 1000.0)
                event = direct_rest_prediction_event(
                    market_id=int(reference["marketId"]),
                    up_book=up_book,
                    down_book=down_book,
                    received_wall_ns=received_wall_ns,
                    received_monotonic_ns=received_mono_ns,
                    current_timestamp_ms=received_ms,
                )
                top = binance_top_of_book(up_book, down_book, current_timestamp_ms=received_ms)
                observation = {
                    "source": "DIRECT_REST",
                    "marketId": int(reference["marketId"]),
                    "upBid": top.up_bid,
                    "upAsk": top.up_ask,
                    "downBid": top.down_bid,
                    "downAsk": top.down_ask,
                    "upBookTimestampMs": _int(event.get("up_book_timestamp_ms")),
                    "downBookTimestampMs": _int(event.get("down_book_timestamp_ms")),
                    "bookAgeMs": _finite(event.get("book_age_ms")),
                    "bookSkewMs": _finite(event.get("book_skew_ms")),
                    "receivedAtMs": received_ms,
                }
                with self.lock:
                    self.direct_successes += 1
                    self.direct_rest_rtts_ms.append(rtt_ms)
                    self.last_direct_at_ms = received_ms
                    self.last_rate_limits = dict(self.client.last_rate_limits)
                    if observation.get("bookAgeMs") is not None:
                        self.direct_book_ages_ms.append(float(observation["bookAgeMs"]))
                self._record_state("DIRECT", observation)
                self._sample_quote_divergence(observation)
            except Exception as exc:
                with self.lock:
                    self.direct_errors += 1
                    self.last_error = f"direct: {str(exc)[:300]}"
            self.stop_event.wait(max(0.0, self.direct_poll_seconds - (time.monotonic() - started)))

    def _sample_quote_divergence(self, direct: dict[str, Any]) -> None:
        with self.lock:
            candidates = [
                obs for obs in self.collector_states.values()
                if int(obs.get("marketId") or 0) == int(direct.get("marketId") or -1)
            ]
            if not candidates:
                return
            collector = max(candidates, key=lambda row: int(row.get("receivedAtMs") or 0))
        row: dict[str, float] = {}
        for name in ("upBid", "upAsk", "downBid", "downAsk"):
            a = _finite(collector.get(name))
            b = _finite(direct.get(name))
            if a is not None and b is not None:
                row[name] = a - b
        if row:
            with self.lock:
                self.quote_divergences.append(row)
                if len(self.quote_divergences) > 20_000:
                    self.quote_divergences = self.quote_divergences[-10_000:]

    def summary(self) -> dict[str, Any]:
        with self.lock:
            deltas = [float(row["collectorMinusDirectMs"]) for row in self.matches]
            direct_first = sum(delta > 0 for delta in deltas)
            collector_first = sum(delta < 0 for delta in deltas)
            ties = sum(delta == 0 for delta in deltas)
            divergence_values = [
                abs(value)
                for row in self.quote_divergences
                for value in row.values()
                if math.isfinite(value)
            ]
            return {
                "probe": "BINANCE_PREDICTION_COLLECTOR_VS_DIRECT_REST",
                "passiveOnly": True,
                "liveOrdersAffected": False,
                "startedAtMs": self.started_at_ms,
                "generatedAtMs": _now_ms(),
                "credentialSource": self.credential_source,
                "collectorRealtimeUrl": COLLECTOR_REALTIME_URL,
                "marketReferenceUrl": MARKET_REFERENCE_URL,
                "directPollMs": int(self.direct_poll_seconds * 1000),
                "collectorPollMs": int(self.collector_poll_seconds * 1000),
                "marketId": self.reference_market_id,
                "marketSwitches": self.market_switches,
                "matchedBookStates": len(deltas),
                "directFirst": direct_first,
                "collectorFirst": collector_first,
                "ties": ties,
                "collectorMinusDirectMs": _stats(deltas),
                "directRestRttMs": _stats(list(self.direct_rest_rtts_ms)),
                "collectorApiRttMs": _stats(list(self.collector_api_rtts_ms)),
                "directBookAgeMs": _stats(list(self.direct_book_ages_ms)),
                "collectorReportedBookAgeMs": _stats(list(self.collector_reported_book_ages_ms)),
                "absoluteCollectorVsDirectQuoteGap": _stats(divergence_values),
                "collectorPolls": self.collector_polls,
                "collectorSuccesses": self.collector_successes,
                "collectorErrors": self.collector_errors,
                "directPolls": self.direct_polls,
                "directSuccesses": self.direct_successes,
                "directErrors": self.direct_errors,
                "lastCollectorAtMs": self.last_collector_at_ms,
                "lastDirectAtMs": self.last_direct_at_ms,
                "lastRateLimits": dict(self.last_rate_limits),
                "lastError": self.last_error,
                "matching": (
                    "same UP/DOWN book timestamps when exposed by 8766; exact four-price top-of-book "
                    "signature fallback otherwise"
                ),
                "interpretation": (
                    "positive collectorMinusDirectMs means independent direct Binance REST observed "
                    "that exact Prediction book state before the production 8766 collector exposed it"
                ),
            }

    def run(self, seconds: float) -> dict[str, Any]:
        collector_thread = threading.Thread(
            target=self._collector_loop, name="binance-latency-collector", daemon=True
        )
        direct_thread = threading.Thread(
            target=self._direct_loop, name="binance-latency-direct", daemon=True
        )
        collector_thread.start()
        direct_thread.start()
        deadline = time.monotonic() + max(1.0, seconds)
        next_report = time.monotonic() + self.report_seconds
        try:
            while not self.stop_event.is_set() and time.monotonic() < deadline:
                wait = min(0.25, max(0.0, deadline - time.monotonic()))
                self.stop_event.wait(wait)
                if time.monotonic() >= next_report:
                    print(json.dumps(self.summary(), ensure_ascii=False, separators=(",", ":")), flush=True)
                    next_report = time.monotonic() + self.report_seconds
        finally:
            self.stop_event.set()
            collector_thread.join(timeout=2.0)
            direct_thread.join(timeout=2.0)
        payload = self.summary()
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Passive Binance Prediction 8766 collector vs direct REST latency probe"
    )
    parser.add_argument("--minutes", type=float, default=15.0)
    parser.add_argument("--poll-ms", type=int, default=DEFAULT_DIRECT_POLL_MS)
    parser.add_argument("--collector-poll-ms", type=int, default=DEFAULT_COLLECTOR_POLL_MS)
    parser.add_argument("--report-seconds", type=float, default=DEFAULT_REPORT_SECONDS)
    parser.add_argument(
        "--output",
        default=str(ROOT / "data" / "binance_feed_latency_probe_summary.json"),
    )
    args = parser.parse_args()
    probe = BinanceFeedLatencyProbe(
        direct_poll_ms=args.poll_ms,
        collector_poll_ms=args.collector_poll_ms,
        report_seconds=args.report_seconds,
        output_path=Path(args.output),
    )
    try:
        payload = probe.run(max(1.0, float(args.minutes) * 60.0))
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
        return 0
    finally:
        probe.close()


if __name__ == "__main__":
    raise SystemExit(main())
