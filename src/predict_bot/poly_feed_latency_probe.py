from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import httpx

try:
    import websocket
except ImportError:  # pragma: no cover
    websocket = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_STATE_URL = os.environ.get(
    "PREDICT_POLY_LATENCY_COLLECTOR_URL", "http://127.0.0.1:8767/state"
)
POLY_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
POLY_BOOKS_URL = "https://clob.polymarket.com/books"
DEFAULT_POLL_MS = max(
    100, int(os.environ.get("PREDICT_POLY_LATENCY_REST_POLL_MS", "250"))
)
DEFAULT_REPORT_SECONDS = max(
    2.0, float(os.environ.get("PREDICT_POLY_LATENCY_REPORT_SECONDS", "10"))
)
MAX_SEEN_STATES = 20_000
USER_AGENT = "BTC-5M-Lab-Poly-Feed-Latency-Probe/1.0"


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _timestamp_ms(value: Any) -> int | None:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    if parsed < 10_000_000_000:
        return parsed * 1000
    if parsed >= 10_000_000_000_000_000:
        return parsed // 1_000_000
    if parsed >= 10_000_000_000_000:
        return parsed // 1000
    return parsed


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _best(levels: Any, *, bids: bool) -> float | None:
    prices: list[float] = []
    if not isinstance(levels, list):
        return None
    for row in levels:
        if not isinstance(row, dict):
            continue
        price = _float(row.get("price"))
        if price is not None and 0 <= price <= 1:
            prices.append(price)
    if not prices:
        return None
    return max(prices) if bids else min(prices)


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


class PolyFeedLatencyProbe:
    """Passive A/B measurement of Polymarket WS versus REST observation latency.

    It does not modify the production 8767 collector and never sends an order.
    The probe reads the current token IDs from 8767, opens its own public market
    websocket, polls the public POST /books endpoint with a persistent HTTP
    connection, and matches identical order-book hashes observed by both paths.

    ``restMinusWsMs`` is the main comparison metric:
      positive -> WebSocket observed that exact book state first
      negative -> REST observed that exact book state first

    REST RTT and source-age metrics are reported separately so polling cadence is
    not confused with network request time.
    """

    def __init__(self, poll_ms: int, report_seconds: float, output_path: Path) -> None:
        self.poll_seconds = max(0.1, poll_ms / 1000.0)
        self.report_seconds = max(2.0, float(report_seconds))
        self.output_path = output_path
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.http = httpx.Client(
            timeout=httpx.Timeout(connect=2.0, read=2.0, write=2.0, pool=1.0),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4, keepalive_expiry=30.0),
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            transport=httpx.HTTPTransport(retries=0),
        )
        self.market_slug: str | None = None
        self.tokens: dict[str, str] = {}
        self.token_outcome: dict[str, str] = {}
        self.generation = 0
        self.ws: Any = None

        self.ws_states: dict[tuple[str, str], dict[str, Any]] = {}
        self.rest_states: dict[tuple[str, str], dict[str, Any]] = {}
        self.matched_keys: set[tuple[str, str]] = set()
        self.key_fifo: deque[tuple[str, str]] = deque()

        self.matches: list[dict[str, Any]] = []
        self.ws_source_ages_ms: list[float] = []
        self.rest_source_ages_ms: list[float] = []
        self.rest_rtts_ms: list[float] = []
        self.ws_messages = 0
        self.ws_book_states = 0
        self.ws_reconnects = 0
        self.ws_errors = 0
        self.rest_polls = 0
        self.rest_successes = 0
        self.rest_errors = 0
        self.market_switches = 0
        self.started_at_ms = _now_ms()
        self.last_ws_at_ms: int | None = None
        self.last_rest_at_ms: int | None = None
        self.last_error: str | None = None

    def close(self) -> None:
        self.stop_event.set()
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:
            pass
        self.http.close()

    def _collector_tokens(self) -> tuple[str, dict[str, str]] | None:
        response = self.http.get(COLLECTOR_STATE_URL)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        poly = payload.get("polymarket")
        if not isinstance(poly, dict):
            return None
        market = poly.get("market")
        if not isinstance(market, dict):
            market = payload.get("market")
        slug = str((market or {}).get("slug") or "") if isinstance(market, dict) else ""
        up = poly.get("up")
        down = poly.get("down")
        if not isinstance(up, dict) or not isinstance(down, dict):
            return None
        up_token = str(up.get("tokenId") or "")
        down_token = str(down.get("tokenId") or "")
        if not slug or not up_token or not down_token or up_token == down_token:
            return None
        return slug, {"UP": up_token, "DOWN": down_token}

    def _set_market(self, slug: str, tokens: dict[str, str]) -> None:
        with self.lock:
            if slug == self.market_slug and tokens == self.tokens:
                return
            self.market_slug = slug
            self.tokens = dict(tokens)
            self.token_outcome = {token: outcome for outcome, token in tokens.items()}
            self.generation += 1
            generation = self.generation
            self.market_switches += 1
            self.ws_states.clear()
            self.rest_states.clear()
            self.matched_keys.clear()
            self.key_fifo.clear()
            old_ws = self.ws
        try:
            if old_ws is not None:
                old_ws.close()
        except Exception:
            pass
        threading.Thread(
            target=self._ws_loop,
            args=(generation,),
            name=f"poly-latency-ws-{generation}",
            daemon=True,
        ).start()

    def _ws_loop(self, generation: int) -> None:
        if websocket is None:
            self.last_error = "websocket-client is not installed"
            return
        first_attempt = True
        while not self.stop_event.is_set():
            with self.lock:
                if generation != self.generation:
                    return
                tokens = list(self.tokens.values())
            if len(tokens) != 2:
                return
            if not first_attempt:
                with self.lock:
                    self.ws_reconnects += 1
            first_attempt = False

            ws = websocket.WebSocketApp(
                POLY_WS_URL,
                on_open=lambda app: app.send(json.dumps({
                    "assets_ids": tokens,
                    "type": "market",
                    "custom_feature_enabled": True,
                }, separators=(",", ":"))),
                on_message=lambda _app, raw: self._on_ws_message(raw, generation),
                on_error=lambda _app, error: self._on_ws_error(error, generation),
            )
            with self.lock:
                if generation != self.generation:
                    return
                self.ws = ws
            try:
                ws.run_forever()
            except Exception as exc:  # pragma: no cover - network dependent
                self._on_ws_error(exc, generation)
            if self.stop_event.wait(1.0):
                return

    def _on_ws_error(self, error: Any, generation: int) -> None:
        with self.lock:
            if generation != self.generation:
                return
            self.ws_errors += 1
            self.last_error = f"WS: {str(error)[:300]}"

    def _on_ws_message(self, raw: str, generation: int) -> None:
        if raw == "PONG":
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        events = payload if isinstance(payload, list) else [payload]
        received_ms = _now_ms()
        with self.lock:
            if generation != self.generation:
                return
            self.ws_messages += 1
            self.last_ws_at_ms = received_ms
        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("event_type") or "")
            source_ms = _timestamp_ms(event.get("timestamp"))
            if event_type == "price_change":
                changes = event.get("price_changes")
                if isinstance(changes, list):
                    for change in changes:
                        if isinstance(change, dict):
                            self._record_ws_state(change, received_ms, source_ms)
                continue
            if event_type in {"book", "best_bid_ask"}:
                self._record_ws_state(event, received_ms, source_ms)

    def _record_ws_state(self, event: dict[str, Any], received_ms: int, source_ms: int | None) -> None:
        token = str(event.get("asset_id") or "")
        book_hash = str(event.get("hash") or "")
        if not token or not book_hash:
            return
        bid = _float(event.get("best_bid"))
        ask = _float(event.get("best_ask"))
        if bid is None:
            bid = _best(event.get("bids"), bids=True)
        if ask is None:
            ask = _best(event.get("asks"), bids=False)
        observation = {
            "source": "WS",
            "market": self.market_slug,
            "token": token,
            "outcome": self.token_outcome.get(token),
            "hash": book_hash,
            "bestBid": bid,
            "bestAsk": ask,
            "sourceTimestampMs": source_ms,
            "receivedAtMs": received_ms,
        }
        if source_ms is not None and 0 <= received_ms - source_ms < 60_000:
            with self.lock:
                self.ws_source_ages_ms.append(float(received_ms - source_ms))
        self._record_state("WS", observation)

    def _record_rest_state(self, book: dict[str, Any], received_ms: int) -> None:
        token = str(book.get("asset_id") or "")
        book_hash = str(book.get("hash") or "")
        if not token or not book_hash:
            return
        source_ms = _timestamp_ms(book.get("timestamp"))
        bid = _best(book.get("bids"), bids=True)
        ask = _best(book.get("asks"), bids=False)
        observation = {
            "source": "REST",
            "market": self.market_slug,
            "token": token,
            "outcome": self.token_outcome.get(token),
            "hash": book_hash,
            "bestBid": bid,
            "bestAsk": ask,
            "sourceTimestampMs": source_ms,
            "receivedAtMs": received_ms,
        }
        if source_ms is not None and 0 <= received_ms - source_ms < 60_000:
            with self.lock:
                self.rest_source_ages_ms.append(float(received_ms - source_ms))
        self._record_state("REST", observation)

    def _record_state(self, source: str, observation: dict[str, Any]) -> None:
        key = (str(observation["token"]), str(observation["hash"]))
        with self.lock:
            target = self.ws_states if source == "WS" else self.rest_states
            if key in target:
                return
            target[key] = observation
            self.key_fifo.append(key)
            other = self.rest_states.get(key) if source == "WS" else self.ws_states.get(key)
            if other is not None and key not in self.matched_keys:
                self.matched_keys.add(key)
                ws_obs = self.ws_states[key]
                rest_obs = self.rest_states[key]
                delta = float(rest_obs["receivedAtMs"] - ws_obs["receivedAtMs"])
                self.matches.append({
                    "market": observation.get("market"),
                    "token": observation.get("token"),
                    "outcome": observation.get("outcome"),
                    "hash": observation.get("hash"),
                    "wsReceivedAtMs": ws_obs["receivedAtMs"],
                    "restReceivedAtMs": rest_obs["receivedAtMs"],
                    "restMinusWsMs": delta,
                    "wsSourceTimestampMs": ws_obs.get("sourceTimestampMs"),
                    "restSourceTimestampMs": rest_obs.get("sourceTimestampMs"),
                    "bestBid": rest_obs.get("bestBid"),
                    "bestAsk": rest_obs.get("bestAsk"),
                })
            while len(self.key_fifo) > MAX_SEEN_STATES:
                old = self.key_fifo.popleft()
                self.ws_states.pop(old, None)
                self.rest_states.pop(old, None)
                self.matched_keys.discard(old)

    def _rest_poll_once(self) -> None:
        with self.lock:
            tokens = list(self.tokens.values())
        if len(tokens) != 2:
            return
        body = [{"token_id": token} for token in tokens]
        started = time.monotonic()
        with self.lock:
            self.rest_polls += 1
        try:
            response = self.http.post(POLY_BOOKS_URL, json=body)
            response.raise_for_status()
            books = response.json()
            received_ms = _now_ms()
            rtt_ms = max(0.0, (time.monotonic() - started) * 1000.0)
            if not isinstance(books, list):
                raise RuntimeError("/books returned non-list payload")
            with self.lock:
                self.rest_successes += 1
                self.rest_rtts_ms.append(rtt_ms)
                self.last_rest_at_ms = received_ms
            for book in books:
                if isinstance(book, dict):
                    self._record_rest_state(book, received_ms)
        except Exception as exc:
            with self.lock:
                self.rest_errors += 1
                self.last_error = f"REST: {str(exc)[:300]}"

    def summary(self) -> dict[str, Any]:
        with self.lock:
            deltas = [float(row["restMinusWsMs"]) for row in self.matches]
            ws_first = sum(value > 0 for value in deltas)
            rest_first = sum(value < 0 for value in deltas)
            ties = len(deltas) - ws_first - rest_first
            return {
                "startedAtMs": self.started_at_ms,
                "generatedAtMs": _now_ms(),
                "marketSlug": self.market_slug,
                "tokens": dict(self.tokens),
                "restPollMs": int(self.poll_seconds * 1000),
                "matchedBookStates": len(deltas),
                "wsFirst": ws_first,
                "restFirst": rest_first,
                "ties": ties,
                "restMinusWsMs": {
                    "mean": statistics.fmean(deltas) if deltas else None,
                    "median": statistics.median(deltas) if deltas else None,
                    "p10": _percentile(deltas, 0.10),
                    "p90": _percentile(deltas, 0.90),
                    "p95": _percentile(deltas, 0.95),
                    "interpretation": "positive means WS observed the identical hash first",
                },
                "wsSourceAgeMs": {
                    "median": statistics.median(self.ws_source_ages_ms) if self.ws_source_ages_ms else None,
                    "p95": _percentile(self.ws_source_ages_ms, 0.95),
                    "samples": len(self.ws_source_ages_ms),
                },
                "restSourceAgeMs": {
                    "median": statistics.median(self.rest_source_ages_ms) if self.rest_source_ages_ms else None,
                    "p95": _percentile(self.rest_source_ages_ms, 0.95),
                    "samples": len(self.rest_source_ages_ms),
                },
                "restRttMs": {
                    "median": statistics.median(self.rest_rtts_ms) if self.rest_rtts_ms else None,
                    "p95": _percentile(self.rest_rtts_ms, 0.95),
                    "samples": len(self.rest_rtts_ms),
                },
                "wsMessages": self.ws_messages,
                "wsBookStates": len(self.ws_states),
                "wsReconnects": self.ws_reconnects,
                "wsErrors": self.ws_errors,
                "restPolls": self.rest_polls,
                "restSuccesses": self.rest_successes,
                "restErrors": self.rest_errors,
                "marketSwitches": self.market_switches,
                "lastWsAgeMs": None if self.last_ws_at_ms is None else max(0, _now_ms() - self.last_ws_at_ms),
                "lastRestAgeMs": None if self.last_rest_at_ms is None else max(0, _now_ms() - self.last_rest_at_ms),
                "lastError": self.last_error,
                "productionFeedsModified": False,
                "ordersPossible": False,
            }

    def _write_output(self) -> None:
        payload = self.summary()
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def run(self, duration_seconds: float | None) -> int:
        deadline = None if duration_seconds is None else time.monotonic() + max(1.0, duration_seconds)
        next_report = 0.0
        next_market_check = 0.0
        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    break
                if now >= next_market_check:
                    next_market_check = now + 1.0
                    try:
                        resolved = self._collector_tokens()
                        if resolved is not None:
                            self._set_market(*resolved)
                    except Exception as exc:
                        with self.lock:
                            self.last_error = f"8767 state: {str(exc)[:300]}"
                if self.tokens:
                    self._rest_poll_once()
                if now >= next_report:
                    next_report = now + self.report_seconds
                    payload = self.summary()
                    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)
                    self._write_output()
                self.stop_event.wait(self.poll_seconds)
        except KeyboardInterrupt:
            return 130
        finally:
            self._write_output()
            self.close()
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Passive Polymarket WebSocket vs REST /books latency A/B probe"
    )
    parser.add_argument("--minutes", type=float, default=15.0, help="test duration; 0 runs until Ctrl-C")
    parser.add_argument("--poll-ms", type=int, default=DEFAULT_POLL_MS, help="REST /books polling interval")
    parser.add_argument("--report-seconds", type=float, default=DEFAULT_REPORT_SECONDS)
    parser.add_argument(
        "--output",
        default=str(ROOT / "data" / "poly_feed_latency_probe_summary.json"),
        help="summary JSON path",
    )
    args = parser.parse_args()
    duration = None if args.minutes <= 0 else args.minutes * 60.0
    probe = PolyFeedLatencyProbe(args.poll_ms, args.report_seconds, Path(args.output))
    return probe.run(duration)


if __name__ == "__main__":
    raise SystemExit(main())
