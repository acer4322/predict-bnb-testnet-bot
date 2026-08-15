from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
import urllib.parse
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

try:
    import websocket
except ImportError:  # pragma: no cover - exposed through /state.
    websocket = None  # type: ignore[assignment]

from .core import ApiHttpError, BinancePredictionClient, select_binary_market


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(
    os.environ.get(
        "PREDICT_MULTI_PREDICTION_DB",
        ROOT / "data" / "multi_prediction_observer.db",
    )
)
HOST = os.environ.get("PREDICT_MULTI_PREDICTION_HOST", "127.0.0.1")
PORT = int(os.environ.get("PREDICT_MULTI_PREDICTION_PORT", "8770"))
BTC_REALTIME_URL = os.environ.get(
    "PREDICT_MULTI_PREDICTION_BTC_REALTIME_URL",
    "http://127.0.0.1:8766/api/realtime",
)
BTC_POLY_URL = os.environ.get(
    "PREDICT_MULTI_PREDICTION_BTC_POLY_URL",
    "http://127.0.0.1:8767/state",
)
POLL_SECONDS = max(
    0.50,
    float(os.environ.get("PREDICT_MULTI_PREDICTION_POLL_SECONDS", "1.0")),
)
SAMPLE_MS = max(
    500,
    int(os.environ.get("PREDICT_MULTI_PREDICTION_SAMPLE_MS", "1000")),
)
RETENTION_HOURS = max(
    1.0,
    float(os.environ.get("PREDICT_MULTI_PREDICTION_RETENTION_HOURS", "24")),
)
TRAJECTORY_POINTS = max(
    120,
    int(os.environ.get("PREDICT_MULTI_PREDICTION_TRAJECTORY_POINTS", "420")),
)
GAMMA_PATH_TIMEOUT_SECONDS = max(
    0.75,
    float(os.environ.get("PREDICT_MULTI_PREDICTION_GAMMA_TIMEOUT_SECONDS", "1.5")),
)
POLYMARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
CLOB_BOOK_URL = "https://clob.polymarket.com/book?token_id={token_id}"
QUOTE_EVENT_TYPES = {"book", "price_change", "best_bid_ask"}
VERSION = "MULTI_PREDICTION_OBSERVER_V1"

ASSETS: dict[str, dict[str, str]] = {
    "BTC": {
        "symbol": "BTCUSDT",
        "polyPrefix": "btc-updown-5m",
        "sourceMode": "EXISTING_BTC_SERVICES",
    },
    "ETH": {
        "symbol": "ETHUSDT",
        "polyPrefix": "eth-updown-5m",
        "sourceMode": "DEDICATED_OBSERVER",
    },
    "BNB": {
        "symbol": "BNBUSDT",
        "polyPrefix": "bnb-updown-5m",
        "sourceMode": "DEDICATED_OBSERVER",
    },
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


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


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _deep_find(payload: Any, keys: set[str], depth: int = 0) -> Any:
    if depth > 7:
        return None
    if isinstance(payload, dict):
        for key in keys:
            if key in payload and payload[key] is not None:
                return payload[key]
        for value in payload.values():
            found = _deep_find(value, keys, depth + 1)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _deep_find(value, keys, depth + 1)
            if found is not None:
                return found
    return None


def _mid(bid: Any, ask: Any) -> float | None:
    b = _finite(bid)
    a = _finite(ask)
    if b is not None and a is not None:
        return (b + a) / 2.0
    return b if b is not None else a


def _best_level(book: dict[str, Any], side: str) -> tuple[float | None, float | None]:
    levels = book.get("asks") if side == "ask" else book.get("bids")
    if not isinstance(levels, list) or not levels:
        return None, None
    parsed: list[tuple[float, float | None]] = []
    for level in levels:
        if isinstance(level, dict):
            price = _finite(level.get("price"))
            size = _finite(level.get("size", level.get("quantity")))
        elif isinstance(level, (list, tuple)) and level:
            price = _finite(level[0])
            size = _finite(level[1]) if len(level) > 1 else None
        else:
            continue
        if price is not None and 0 < price < 1:
            parsed.append((price, size))
    if not parsed:
        return None, None
    return min(parsed, key=lambda item: item[0]) if side == "ask" else max(parsed, key=lambda item: item[0])


def _iso_ms(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(parsed.timestamp() * 1000)


def current_slug(asset: str, now_seconds: float | None = None) -> tuple[str, int]:
    asset_key = str(asset or "").upper()
    config = ASSETS.get(asset_key)
    if config is None:
        raise ValueError(f"unsupported asset: {asset}")
    now = time.time() if now_seconds is None else float(now_seconds)
    bucket = int(now // 300) * 300
    return f"{config['polyPrefix']}-{bucket}", bucket


def parse_gamma_candidate(
    payload: dict[str, Any],
    *,
    asset: str,
    event_slug: str,
    bucket: int,
) -> dict[str, Any] | None:
    tokens = [str(value) for value in _json_list(payload.get("clobTokenIds"))]
    outcomes = [str(value).strip().upper() for value in _json_list(payload.get("outcomes"))]
    if len(tokens) != 2 or len(outcomes) != 2 or set(outcomes) != {"UP", "DOWN"}:
        return None
    condition_id = str(payload.get("conditionId") or "").strip()
    if not condition_id:
        return None
    mapping = {outcomes[index]: tokens[index] for index in range(2)}
    up_token = mapping.get("UP")
    down_token = mapping.get("DOWN")
    if not up_token or not down_token or up_token == down_token:
        return None

    expected_end_ms = (int(bucket) + 300) * 1000
    end_ms = _iso_ms(payload.get("endDate") or payload.get("endDateIso"))
    if end_ms is not None and abs(end_ms - expected_end_ms) > 30_000:
        return None

    return {
        "asset": str(asset).upper(),
        "eventSlug": event_slug,
        "gammaMarketSlug": str(payload.get("slug") or "") or None,
        "marketId": str(payload.get("id") or "") or None,
        "conditionId": condition_id,
        "question": str(payload.get("question") or f"{asset} Up or Down 5m"),
        "bucketStartSec": int(bucket),
        "windowStartMs": int(bucket) * 1000,
        "windowEndMs": expected_end_ms,
        "upTokenId": up_token,
        "downTokenId": down_token,
    }


def _candidate_from_event(
    event: dict[str, Any],
    *,
    asset: str,
    slug: str,
    bucket: int,
) -> dict[str, Any] | None:
    if str(event.get("slug") or "") != slug:
        return None
    markets = event.get("markets")
    if not isinstance(markets, list):
        return None
    candidates: list[tuple[int, dict[str, Any]]] = []
    for raw in markets:
        if not isinstance(raw, dict):
            continue
        candidate = parse_gamma_candidate(raw, asset=asset, event_slug=slug, bucket=bucket)
        if candidate is None:
            continue
        score = 0
        if bool(raw.get("active", True)):
            score += 4
        if not bool(raw.get("closed", False)):
            score += 4
        if bool(raw.get("enableOrderBook", True)):
            score += 2
        if bool(raw.get("acceptingOrders", True)):
            score += 1
        candidates.append((score, candidate))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


class MultiPredictionObserver:
    """Read-only BTC/ETH/BNB Polymarket vs Binance Prediction observer.

    BTC is sampled from the already-running 8766/8767 services so this process
    never duplicates the real-money BTC hot path's external requests. ETH and BNB
    use their own Polymarket market websocket and a read-only Binance Prediction
    client. This module contains no trading client and exposes no POST endpoint.
    """

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.lock = threading.RLock()
        self.db_lock = threading.RLock()
        self.stop_event = threading.Event()
        self.public_http = httpx.Client(
            timeout=httpx.Timeout(2.0, connect=0.75),
            verify=True,
            trust_env=False,
            follow_redirects=True,
            headers={"Accept": "application/json", "User-Agent": "BTC-5M-Lab-Multi-Prediction/1.0"},
        )
        self.local_http = httpx.Client(timeout=httpx.Timeout(1.5, connect=0.25))
        self.binance_client: BinancePredictionClient | None = None
        self.credential_source = "UNAVAILABLE"
        self.binance_backoff_until = 0.0
        self.poly_ws: Any = None
        self.poly_generation = 0
        self.poly_token_map: dict[str, tuple[str, str]] = {}
        self.last_poly_restart_tokens: tuple[str, ...] = ()
        self.started_at_ms = _now_ms()
        self.last_error: str | None = None
        self.assets: dict[str, dict[str, Any]] = {}
        for asset, config in ASSETS.items():
            self.assets[asset] = {
                "asset": asset,
                "symbol": config["symbol"],
                "sourceMode": config["sourceMode"],
                "bucketStartSec": None,
                "windowEndMs": None,
                "poly": {
                    "status": "WAITING_MARKET",
                    "error": None,
                    "market": None,
                    "up": {"bestBid": None, "bestAsk": None, "quoteSourceTimestampMs": None, "quoteReceivedTimestampMs": None},
                    "down": {"bestBid": None, "bestAsk": None, "quoteSourceTimestampMs": None, "quoteReceivedTimestampMs": None},
                },
                "binance": {
                    "status": "WAITING_MARKET",
                    "error": None,
                    "market": None,
                    "upBid": None,
                    "upAsk": None,
                    "downBid": None,
                    "downAsk": None,
                    "observedAtMs": None,
                    "bookRttMs": None,
                    "bookAgeMs": None,
                },
                "trajectory": deque(maxlen=TRAJECTORY_POINTS),
            }

        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.db = sqlite3.connect(db_path, check_same_thread=False, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self._create_schema()
        self._ensure_binance_client()

    def _create_schema(self) -> None:
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS multi_prediction_trajectory (
                    asset TEXT NOT NULL,
                    market_bucket INTEGER NOT NULL,
                    sample_bucket_ms INTEGER NOT NULL,
                    sampled_at_ms INTEGER NOT NULL,
                    seconds_left REAL,
                    poly_up_mid REAL,
                    poly_down_mid REAL,
                    binance_up_mid REAL,
                    binance_down_mid REAL,
                    binance_up_ask REAL,
                    binance_down_ask REAL,
                    mid_gap REAL,
                    executable_edge_up REAL,
                    executable_edge_down REAL,
                    poly_source_age_ms REAL,
                    poly_receipt_age_ms REAL,
                    PRIMARY KEY(asset, market_bucket, sample_bucket_ms)
                );
                CREATE INDEX IF NOT EXISTS idx_multi_prediction_sampled
                    ON multi_prediction_trajectory(sampled_at_ms);
                """
            )
            self.db.commit()

    def _ensure_binance_client(self) -> bool:
        if self.binance_client is not None:
            return True
        key = os.environ.get("BINANCE_API_KEY")
        secret = os.environ.get("BINANCE_API_SECRET")
        source = "BINANCE_API_*"
        if not key or not secret:
            key = os.environ.get("BINANCE_LIVE_API_KEY")
            secret = os.environ.get("BINANCE_LIVE_API_SECRET")
            source = "BINANCE_LIVE_*" if key and secret else "UNAVAILABLE"
        if not key or not secret:
            self.credential_source = "UNAVAILABLE"
            for asset in ("ETH", "BNB"):
                self.assets[asset]["binance"]["status"] = "CONFIG_REQUIRED"
                self.assets[asset]["binance"]["error"] = "read-only Binance Prediction credentials unavailable"
            return False
        self.binance_client = BinancePredictionClient(key, secret)
        self.credential_source = source
        return True

    def start(self) -> None:
        threading.Thread(target=self._market_loop, name="multi-prediction-markets", daemon=True).start()
        threading.Thread(target=self._binance_loop, name="multi-prediction-binance", daemon=True).start()
        threading.Thread(target=self._sample_loop, name="multi-prediction-samples", daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()
        try:
            if self.poly_ws is not None:
                self.poly_ws.close()
        except Exception:
            pass
        try:
            self.public_http.close()
        except Exception:
            pass
        try:
            self.local_http.close()
        except Exception:
            pass
        try:
            if self.binance_client is not None:
                self.binance_client.close()
        except Exception:
            pass
        with self.db_lock:
            self.db.commit()
            self.db.close()

    def _public_json(self, url: str, *, timeout: float = GAMMA_PATH_TIMEOUT_SECONDS) -> Any:
        response = self.public_http.get(url, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def _discover_poly_market(self, asset: str, slug: str, bucket: int) -> tuple[dict[str, Any] | None, str | None]:
        quoted = urllib.parse.quote(slug)
        expected_end = int(bucket) + 300
        end_min = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expected_end - 90))
        end_max = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expected_end + 90))

        def event_exact() -> dict[str, Any] | None:
            payload = self._public_json(f"https://gamma-api.polymarket.com/events/slug/{quoted}")
            return _candidate_from_event(payload, asset=asset, slug=slug, bucket=bucket) if isinstance(payload, dict) else None

        def event_list() -> dict[str, Any] | None:
            query = urllib.parse.urlencode({"slug": slug, "limit": 10})
            payload = self._public_json(f"{GAMMA_EVENTS_URL}?{query}")
            for event in payload if isinstance(payload, list) else []:
                if isinstance(event, dict):
                    candidate = _candidate_from_event(event, asset=asset, slug=slug, bucket=bucket)
                    if candidate is not None:
                        return candidate
            return None

        def event_window() -> dict[str, Any] | None:
            query = urllib.parse.urlencode(
                {
                    "active": "true",
                    "closed": "false",
                    "end_date_min": end_min,
                    "end_date_max": end_max,
                    "order": "endDate",
                    "ascending": "true",
                    "limit": 100,
                }
            )
            payload = self._public_json(f"{GAMMA_EVENTS_URL}?{query}")
            for event in payload if isinstance(payload, list) else []:
                if not isinstance(event, dict) or str(event.get("slug") or "") != slug:
                    continue
                candidate = _candidate_from_event(event, asset=asset, slug=slug, bucket=bucket)
                if candidate is not None:
                    return candidate
            return None

        paths = (("EVENT_EXACT", event_exact), ("EVENT_LIST", event_list), ("EVENT_WINDOW", event_window))
        executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix=f"multi-gamma-{asset.lower()}")
        futures = {executor.submit(fn): name for name, fn in paths}
        try:
            for future in as_completed(futures, timeout=GAMMA_PATH_TIMEOUT_SECONDS + 0.75):
                try:
                    candidate = future.result()
                except Exception:
                    candidate = None
                if candidate is not None:
                    for other in futures:
                        if other is not future:
                            other.cancel()
                    executor.shutdown(wait=False, cancel_futures=True)
                    return candidate, futures[future]
        except TimeoutError:
            pass
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        try:
            payload = self._public_json(f"https://gamma-api.polymarket.com/markets/slug/{quoted}")
            if isinstance(payload, list):
                payload = payload[0] if payload else None
            if isinstance(payload, dict):
                candidate = parse_gamma_candidate(payload, asset=asset, event_slug=slug, bucket=bucket)
                if candidate is not None:
                    return candidate, "STRICT_MARKET_FALLBACK"
        except Exception:
            pass
        return None, None

    def _market_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._refresh_btc_local()
                poly_changed = False
                for asset in ("ETH", "BNB"):
                    slug, bucket = current_slug(asset)
                    with self.lock:
                        current = _record(self.assets[asset]["poly"].get("market"))
                    if int(current.get("bucketStartSec") or 0) == bucket:
                        continue
                    candidate, method = self._discover_poly_market(asset, slug, bucket)
                    if candidate is None:
                        with self.lock:
                            poly = self.assets[asset]["poly"]
                            poly["status"] = "WAITING_MARKET"
                            poly["error"] = f"no strict current {asset} 5m UP/DOWN event for {slug}"
                        continue
                    with self.lock:
                        state = self.assets[asset]
                        state["bucketStartSec"] = bucket
                        state["windowEndMs"] = (bucket + 300) * 1000
                        state["trajectory"].clear()
                        state["poly"] = {
                            "status": "CONNECTING",
                            "error": None,
                            "discoveryMethod": method,
                            "market": candidate,
                            "up": {"bestBid": None, "bestAsk": None, "quoteSourceTimestampMs": None, "quoteReceivedTimestampMs": None},
                            "down": {"bestBid": None, "bestAsk": None, "quoteSourceTimestampMs": None, "quoteReceivedTimestampMs": None},
                        }
                        self._load_trajectory_locked(asset, bucket)
                    poly_changed = True
                if poly_changed:
                    self._restart_poly_ws()
                    self._prime_poly_books()
                self._refresh_binance_markets()
            except Exception as exc:
                self.last_error = f"market loop: {str(exc)[:400]}"
            self.stop_event.wait(0.75)

    def _refresh_btc_local(self) -> None:
        now = _now_ms()
        try:
            poly_response = self.local_http.get(BTC_POLY_URL)
            poly_response.raise_for_status()
            poly_payload = poly_response.json()
            poly = _record(poly_payload.get("polymarket")) if isinstance(poly_payload, dict) else {}
            market = _record(poly.get("market"))
            up = _record(poly.get("up"))
            down = _record(poly.get("down"))
            bucket = int(market.get("bucketStartSec") or int(now / 1000 // 300) * 300)
            with self.lock:
                state = self.assets["BTC"]
                if int(state.get("bucketStartSec") or 0) != bucket:
                    state["trajectory"].clear()
                    self._load_trajectory_locked("BTC", bucket)
                state["bucketStartSec"] = bucket
                state["windowEndMs"] = int(market.get("windowEndMs") or (bucket + 300) * 1000)
                state["poly"] = {
                    "status": str(poly.get("status") or "UNKNOWN"),
                    "error": poly.get("error"),
                    "discoveryMethod": "8767_EXISTING_BTC",
                    "market": market,
                    "receivedTimestampMs": poly.get("receivedTimestampMs"),
                    "ageMs": poly.get("ageMs"),
                    "up": dict(up),
                    "down": dict(down),
                }
        except Exception as exc:
            with self.lock:
                self.assets["BTC"]["poly"]["status"] = "UNAVAILABLE"
                self.assets["BTC"]["poly"]["error"] = str(exc)[:300]

        try:
            realtime_response = self.local_http.get(BTC_REALTIME_URL)
            realtime_response.raise_for_status()
            realtime = realtime_response.json()
            up_ask = _finite(_deep_find(realtime, {"up_ask", "upAsk"}))
            down_ask = _finite(_deep_find(realtime, {"down_ask", "downAsk"}))
            up_bid = _finite(_deep_find(realtime, {"up_bid", "upBid"}))
            down_bid = _finite(_deep_find(realtime, {"down_bid", "downBid"}))
            market_id = _deep_find(realtime, {"market_id", "marketId"})
            with self.lock:
                b = self.assets["BTC"]["binance"]
                b.update(
                    status="LIVE" if up_ask is not None or down_ask is not None else "WAITING_BOOK",
                    error=None,
                    market={"marketId": market_id, "source": "8766_EXISTING_BTC"},
                    upBid=up_bid,
                    upAsk=up_ask,
                    downBid=down_bid,
                    downAsk=down_ask,
                    observedAtMs=now,
                    bookRttMs=None,
                    bookAgeMs=_finite(_deep_find(realtime, {"book_age_ms", "bookAgeMs"})),
                )
        except Exception as exc:
            with self.lock:
                self.assets["BTC"]["binance"]["status"] = "UNAVAILABLE"
                self.assets["BTC"]["binance"]["error"] = str(exc)[:300]

    def _refresh_binance_markets(self) -> None:
        if not self._ensure_binance_client():
            return
        client = self.binance_client
        assert client is not None
        now_ms = _now_ms()
        need = []
        with self.lock:
            for asset in ("ETH", "BNB"):
                market = _record(self.assets[asset]["binance"].get("market"))
                if int(market.get("endMs") or 0) <= now_ms + 750:
                    need.append(asset)
        if not need:
            return

        candidates: dict[str, list[dict[str, Any]]] = {asset: [] for asset in need}
        offset = 0
        for _page in range(3):
            response = client.list_markets(offset=offset, limit=100)
            topics = response.get("marketTopics", []) if isinstance(response, dict) else []
            if not isinstance(topics, list):
                topics = []
            for topic in topics:
                if not isinstance(topic, dict):
                    continue
                symbol = str(topic.get("symbol") or "")
                asset = next((name for name in need if ASSETS[name]["symbol"] == symbol), None)
                if asset is None or topic.get("chartType") != "CRYPTO_UP_DOWN":
                    continue
                start_ms = int(topic.get("startDate") or 0)
                end_ms = int(topic.get("endDate") or 0)
                if end_ms > now_ms and 0 < end_ms - start_ms <= 301_000:
                    candidates[asset].append(topic)
            if all(candidates[asset] for asset in need) or not response.get("hasMore"):
                break
            offset += int(response.get("limit") or 100)

        for asset in need:
            rows = candidates[asset]
            if not rows:
                with self.lock:
                    self.assets[asset]["binance"]["status"] = "WAITING_MARKET"
                    self.assets[asset]["binance"]["error"] = f"no current {ASSETS[asset]['symbol']} 5m CRYPTO_UP_DOWN market"
                continue
            active = [row for row in rows if int(row.get("startDate") or 0) <= now_ms < int(row.get("endDate") or 0)]
            summary = min(active or rows, key=lambda row: abs(int(row.get("startDate") or 0) - now_ms))
            try:
                selected = select_binary_market(summary)
            except Exception as exc:
                with self.lock:
                    self.assets[asset]["binance"]["status"] = "WAITING_MARKET"
                    self.assets[asset]["binance"]["error"] = str(exc)[:300]
                continue
            market = _record(selected.get("market"))
            up = _record(selected.get("up"))
            down = _record(selected.get("down"))
            cache = {
                "topicId": int(summary.get("marketTopicId") or 0),
                "marketId": int(market.get("marketId") or 0),
                "startMs": int(summary.get("startDate") or 0),
                "endMs": int(summary.get("endDate") or 0),
                "upTokenId": str(up.get("tokenId") or ""),
                "downTokenId": str(down.get("tokenId") or ""),
            }
            with self.lock:
                b = self.assets[asset]["binance"]
                previous_id = int(_record(b.get("market")).get("marketId") or 0)
                b["market"] = cache
                b["status"] = "WAITING_BOOK"
                b["error"] = None
                if previous_id and previous_id != cache["marketId"]:
                    b.update(upBid=None, upAsk=None, downBid=None, downAsk=None, observedAtMs=None, bookRttMs=None, bookAgeMs=None)

    def _restart_poly_ws(self) -> None:
        if websocket is None:
            with self.lock:
                for asset in ("ETH", "BNB"):
                    self.assets[asset]["poly"]["status"] = "ERROR"
                    self.assets[asset]["poly"]["error"] = "websocket-client is not installed"
            return
        token_map: dict[str, tuple[str, str]] = {}
        with self.lock:
            for asset in ("ETH", "BNB"):
                market = _record(self.assets[asset]["poly"].get("market"))
                up = str(market.get("upTokenId") or "")
                down = str(market.get("downTokenId") or "")
                if up:
                    token_map[up] = (asset, "UP")
                if down:
                    token_map[down] = (asset, "DOWN")
        tokens = tuple(sorted(token_map))
        if not tokens or tokens == self.last_poly_restart_tokens:
            return
        self.last_poly_restart_tokens = tokens
        self.poly_token_map = token_map
        self.poly_generation += 1
        generation = self.poly_generation
        try:
            if self.poly_ws is not None:
                self.poly_ws.close()
        except Exception:
            pass
        threading.Thread(target=self._poly_ws_loop, args=(generation,), name=f"multi-poly-ws-{generation}", daemon=True).start()

    def _poly_ws_loop(self, generation: int) -> None:
        while not self.stop_event.is_set() and generation == self.poly_generation:
            ws = websocket.WebSocketApp(
                POLYMARKET_WS_URL,
                on_open=lambda app: self._poly_open(app, generation),
                on_message=lambda app, raw: self._poly_message(raw, generation),
                on_error=lambda app, error: self._poly_error(error, generation),
                on_close=lambda app, code, message: self._poly_close(code, message, generation),
            )
            self.poly_ws = ws
            try:
                ws.run_forever()
            except Exception as exc:  # pragma: no cover - network dependent.
                self._poly_error(exc, generation)
            if self.stop_event.wait(2.0):
                return

    def _poly_open(self, ws: Any, generation: int) -> None:
        if generation != self.poly_generation:
            ws.close()
            return
        tokens = list(self.last_poly_restart_tokens)
        ws.send(json.dumps({"assets_ids": tokens, "type": "market", "custom_feature_enabled": True}, separators=(",", ":")))
        with self.lock:
            for asset in ("ETH", "BNB"):
                self.assets[asset]["poly"]["status"] = "LIVE"
                self.assets[asset]["poly"]["error"] = None
        threading.Thread(target=self._poly_heartbeat, args=(ws, generation), daemon=True).start()

    def _poly_heartbeat(self, ws: Any, generation: int) -> None:
        while not self.stop_event.wait(10.0):
            if generation != self.poly_generation:
                return
            try:
                ws.send("PING")
            except Exception:
                return

    def _poly_message(self, raw: str, generation: int) -> None:
        if generation != self.poly_generation or raw == "PONG":
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        for event in payload if isinstance(payload, list) else [payload]:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("event_type") or event.get("eventType") or event.get("type") or "unknown").lower()
            source_ms = _timestamp_ms(event.get("timestamp"))
            received_ms = _now_ms()
            if event_type in {"price_change", "pricechange"}:
                changes = event.get("price_changes") or event.get("priceChanges") or []
                for change in changes if isinstance(changes, list) else []:
                    if isinstance(change, dict):
                        self._apply_poly_quote(change, "price_change", source_ms, received_ms)
            else:
                self._apply_poly_quote(event, event_type, source_ms, received_ms)

    def _apply_poly_quote(self, event: dict[str, Any], event_type: str, source_ms: int | None, received_ms: int) -> None:
        token = str(event.get("asset_id") or event.get("assetId") or event.get("token_id") or event.get("tokenId") or "")
        mapped = self.poly_token_map.get(token)
        if mapped is None:
            return
        asset, outcome = mapped
        bids = event.get("bids") or []
        asks = event.get("asks") or []
        best_bid = _finite(event.get("best_bid") if event.get("best_bid") is not None else event.get("bestBid"))
        best_ask = _finite(event.get("best_ask") if event.get("best_ask") is not None else event.get("bestAsk"))
        if best_bid is None and isinstance(bids, list):
            parsed = [_best_level({"bids": bids}, "bid")]
            best_bid = parsed[0][0]
        if best_ask is None and isinstance(asks, list):
            parsed = [_best_level({"asks": asks}, "ask")]
            best_ask = parsed[0][0]
        side_key = "up" if outcome == "UP" else "down"
        with self.lock:
            poly = self.assets[asset]["poly"]
            side = poly[side_key]
            if best_bid is not None:
                side["bestBid"] = best_bid
            if best_ask is not None:
                side["bestAsk"] = best_ask
            side["lastEventType"] = event_type
            side["receivedTimestampMs"] = received_ms
            side["sourceTimestampMs"] = source_ms
            if event_type in QUOTE_EVENT_TYPES:
                side["quoteSourceTimestampMs"] = source_ms
                side["quoteReceivedTimestampMs"] = received_ms
            poly["receivedTimestampMs"] = received_ms
            poly["sourceTimestampMs"] = source_ms
            poly["status"] = "LIVE"
            poly["error"] = None

    def _poly_error(self, error: Any, generation: int) -> None:
        if generation != self.poly_generation:
            return
        with self.lock:
            for asset in ("ETH", "BNB"):
                self.assets[asset]["poly"]["status"] = "RECONNECTING"
                self.assets[asset]["poly"]["error"] = str(error)[:300]

    def _poly_close(self, code: Any, message: Any, generation: int) -> None:
        if generation != self.poly_generation or self.stop_event.is_set():
            return
        self._poly_error(f"close {code}: {message}", generation)

    def _prime_poly_books(self) -> None:
        jobs: list[tuple[str, str, str]] = []
        with self.lock:
            for token, (asset, outcome) in self.poly_token_map.items():
                jobs.append((asset, outcome, token))
        if not jobs:
            return
        with ThreadPoolExecutor(max_workers=min(4, len(jobs)), thread_name_prefix="multi-poly-prime") as executor:
            futures = {
                executor.submit(self._public_json, CLOB_BOOK_URL.format(token_id=urllib.parse.quote(token)), timeout=2.0): (asset, outcome, token)
                for asset, outcome, token in jobs
            }
            for future in as_completed(futures):
                asset, outcome, token = futures[future]
                try:
                    book = future.result()
                except Exception:
                    continue
                if not isinstance(book, dict):
                    continue
                bid, _ = _best_level(book, "bid")
                ask, _ = _best_level(book, "ask")
                side_key = "up" if outcome == "UP" else "down"
                with self.lock:
                    side = self.assets[asset]["poly"][side_key]
                    if bid is not None:
                        side["bestBid"] = bid
                    if ask is not None:
                        side["bestAsk"] = ask
                    side["tokenId"] = token
                    side["lastEventType"] = "rest_book_prime"

    def _binance_loop(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                if time.monotonic() >= self.binance_backoff_until:
                    self._poll_binance_books()
            except Exception as exc:
                self.last_error = f"Binance books: {str(exc)[:400]}"
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(0.05, POLL_SECONDS - elapsed))

    def _poll_binance_books(self) -> None:
        if not self._ensure_binance_client():
            return
        client = self.binance_client
        assert client is not None
        jobs: list[tuple[str, str, int, str]] = []
        with self.lock:
            for asset in ("ETH", "BNB"):
                market = _record(self.assets[asset]["binance"].get("market"))
                market_id = int(market.get("marketId") or 0)
                for outcome, key in (("UP", "upTokenId"), ("DOWN", "downTokenId")):
                    token = str(market.get(key) or "")
                    if market_id > 0 and token:
                        jobs.append((asset, outcome, market_id, token))
        if not jobs:
            return

        results: dict[str, dict[str, Any]] = {"ETH": {}, "BNB": {}}
        with ThreadPoolExecutor(max_workers=min(4, len(jobs)), thread_name_prefix="multi-binance-book") as executor:
            futures = {}
            for asset, outcome, market_id, token in jobs:
                started = time.monotonic()
                future = executor.submit(client.orderbook, market_id, token)
                futures[future] = (asset, outcome, started)
            for future in as_completed(futures):
                asset, outcome, started = futures[future]
                try:
                    book = future.result()
                except ApiHttpError as exc:
                    if exc.status_code == 429:
                        retry = max(1.0, float(client.retry_after_seconds or 1.0))
                        self.binance_backoff_until = time.monotonic() + retry
                    with self.lock:
                        self.assets[asset]["binance"]["status"] = "ERROR"
                        self.assets[asset]["binance"]["error"] = str(exc)[:300]
                    continue
                except Exception as exc:
                    with self.lock:
                        self.assets[asset]["binance"]["status"] = "ERROR"
                        self.assets[asset]["binance"]["error"] = str(exc)[:300]
                    continue
                if not isinstance(book, dict):
                    continue
                bid, _bid_size = _best_level(book, "bid")
                ask, _ask_size = _best_level(book, "ask")
                update_ms = _timestamp_ms(book.get("updateTimestampMs") or book.get("timestamp"))
                results[asset][outcome] = {
                    "bid": bid,
                    "ask": ask,
                    "rttMs": max(0.0, (time.monotonic() - started) * 1000.0),
                    "updateTimestampMs": update_ms,
                }

        now_ms = _now_ms()
        with self.lock:
            for asset, outcome_rows in results.items():
                if not outcome_rows:
                    continue
                b = self.assets[asset]["binance"]
                up = _record(outcome_rows.get("UP"))
                down = _record(outcome_rows.get("DOWN"))
                if up:
                    b["upBid"] = up.get("bid")
                    b["upAsk"] = up.get("ask")
                if down:
                    b["downBid"] = down.get("bid")
                    b["downAsk"] = down.get("ask")
                rtts = [_finite(row.get("rttMs")) for row in (up, down) if row]
                rtts = [value for value in rtts if value is not None]
                timestamps = [_finite(row.get("updateTimestampMs")) for row in (up, down) if row]
                timestamps = [value for value in timestamps if value is not None]
                b["observedAtMs"] = now_ms
                b["bookRttMs"] = max(rtts) if rtts else None
                b["bookAgeMs"] = max(0.0, now_ms - min(timestamps)) if timestamps else None
                b["status"] = "LIVE" if b.get("upAsk") is not None and b.get("downAsk") is not None else "WAITING_BOOK"
                b["error"] = None

    def _load_trajectory_locked(self, asset: str, bucket: int) -> None:
        trajectory = self.assets[asset]["trajectory"]
        with self.db_lock:
            rows = self.db.execute(
                """SELECT * FROM multi_prediction_trajectory
                    WHERE asset=? AND market_bucket=?
                    ORDER BY sampled_at_ms ASC LIMIT ?""",
                (asset, int(bucket), TRAJECTORY_POINTS),
            ).fetchall()
        for row in rows:
            trajectory.append(self._trajectory_row(dict(row)))

    @staticmethod
    def _trajectory_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "sampledAtMs": row.get("sampled_at_ms"),
            "secondsLeft": row.get("seconds_left"),
            "polyUp": row.get("poly_up_mid"),
            "polyDown": row.get("poly_down_mid"),
            "binanceUp": row.get("binance_up_mid"),
            "binanceDown": row.get("binance_down_mid"),
            "binanceUpAsk": row.get("binance_up_ask"),
            "binanceDownAsk": row.get("binance_down_ask"),
            "midGap": row.get("mid_gap"),
            "edgeUp": row.get("executable_edge_up"),
            "edgeDown": row.get("executable_edge_down"),
            "polySourceAgeMs": row.get("poly_source_age_ms"),
            "polyReceiptAgeMs": row.get("poly_receipt_age_ms"),
        }

    def _sample_loop(self) -> None:
        cleanup_at = 0.0
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                rows = self._sample_once()
                if rows:
                    with self.db_lock:
                        self.db.executemany(
                            """INSERT OR REPLACE INTO multi_prediction_trajectory(
                                   asset,market_bucket,sample_bucket_ms,sampled_at_ms,seconds_left,
                                   poly_up_mid,poly_down_mid,binance_up_mid,binance_down_mid,
                                   binance_up_ask,binance_down_ask,mid_gap,executable_edge_up,
                                   executable_edge_down,poly_source_age_ms,poly_receipt_age_ms
                               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            rows,
                        )
                        self.db.commit()
                if time.monotonic() >= cleanup_at:
                    cutoff = _now_ms() - int(RETENTION_HOURS * 3600 * 1000)
                    with self.db_lock:
                        self.db.execute("DELETE FROM multi_prediction_trajectory WHERE sampled_at_ms < ?", (cutoff,))
                        self.db.commit()
                    cleanup_at = time.monotonic() + 60.0
            except Exception as exc:
                self.last_error = f"sample loop: {str(exc)[:400]}"
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(0.05, SAMPLE_MS / 1000.0 - elapsed))

    def _sample_once(self) -> list[tuple[Any, ...]]:
        now_ms = _now_ms()
        sample_bucket_ms = (now_ms // SAMPLE_MS) * SAMPLE_MS
        rows: list[tuple[Any, ...]] = []
        with self.lock:
            for asset in ASSETS:
                state = self.assets[asset]
                bucket = int(state.get("bucketStartSec") or 0)
                if bucket <= 0:
                    continue
                poly = _record(state.get("poly"))
                p_up = _record(poly.get("up"))
                p_down = _record(poly.get("down"))
                b = _record(state.get("binance"))
                poly_up = _mid(p_up.get("bestBid"), p_up.get("bestAsk"))
                poly_down = _mid(p_down.get("bestBid"), p_down.get("bestAsk"))
                if poly_down is None and poly_up is not None:
                    poly_down = max(0.0, min(1.0, 1.0 - poly_up))
                binance_up = _mid(b.get("upBid"), b.get("upAsk"))
                binance_down = _mid(b.get("downBid"), b.get("downAsk"))
                source_ms = _timestamp_ms(p_up.get("quoteSourceTimestampMs") or p_up.get("sourceTimestampMs"))
                receipt_ms = _timestamp_ms(p_up.get("quoteReceivedTimestampMs") or p_up.get("receivedTimestampMs") or poly.get("receivedTimestampMs"))
                source_age = max(0, now_ms - source_ms) if source_ms else None
                receipt_age = max(0, now_ms - receipt_ms) if receipt_ms else _finite(poly.get("ageMs"))
                seconds_left = max(0.0, ((bucket + 300) * 1000 - now_ms) / 1000.0)
                mid_gap = poly_up - binance_up if poly_up is not None and binance_up is not None else None
                up_ask = _finite(b.get("upAsk"))
                down_ask = _finite(b.get("downAsk"))
                edge_up = poly_up - up_ask if poly_up is not None and up_ask is not None else None
                edge_down = poly_down - down_ask if poly_down is not None and down_ask is not None else None
                point = {
                    "sampledAtMs": now_ms,
                    "secondsLeft": seconds_left,
                    "polyUp": poly_up,
                    "polyDown": poly_down,
                    "binanceUp": binance_up,
                    "binanceDown": binance_down,
                    "binanceUpAsk": up_ask,
                    "binanceDownAsk": down_ask,
                    "midGap": mid_gap,
                    "edgeUp": edge_up,
                    "edgeDown": edge_down,
                    "polySourceAgeMs": source_age,
                    "polyReceiptAgeMs": receipt_age,
                }
                if poly_up is not None or binance_up is not None:
                    state["trajectory"].append(point)
                rows.append(
                    (
                        asset,
                        bucket,
                        sample_bucket_ms,
                        now_ms,
                        seconds_left,
                        poly_up,
                        poly_down,
                        binance_up,
                        binance_down,
                        up_ask,
                        down_ask,
                        mid_gap,
                        edge_up,
                        edge_down,
                        source_age,
                        receipt_age,
                    )
                )
        return rows

    def _asset_snapshot(self, asset: str) -> dict[str, Any]:
        now_ms = _now_ms()
        state = self.assets[asset]
        poly = dict(_record(state.get("poly")))
        poly["up"] = dict(_record(poly.get("up")))
        poly["down"] = dict(_record(poly.get("down")))
        binance = dict(_record(state.get("binance")))
        p_up = poly["up"]
        p_down = poly["down"]
        poly_up = _mid(p_up.get("bestBid"), p_up.get("bestAsk"))
        poly_down = _mid(p_down.get("bestBid"), p_down.get("bestAsk"))
        if poly_down is None and poly_up is not None:
            poly_down = max(0.0, min(1.0, 1.0 - poly_up))
        binance_up = _mid(binance.get("upBid"), binance.get("upAsk"))
        binance_down = _mid(binance.get("downBid"), binance.get("downAsk"))
        source_ms = _timestamp_ms(p_up.get("quoteSourceTimestampMs") or p_up.get("sourceTimestampMs"))
        receipt_ms = _timestamp_ms(p_up.get("quoteReceivedTimestampMs") or p_up.get("receivedTimestampMs") or poly.get("receivedTimestampMs"))
        source_age = max(0, now_ms - source_ms) if source_ms else None
        receipt_age = max(0, now_ms - receipt_ms) if receipt_ms else _finite(poly.get("ageMs"))
        bucket = int(state.get("bucketStartSec") or 0)
        seconds_left = max(0.0, ((bucket + 300) * 1000 - now_ms) / 1000.0) if bucket > 0 else None
        up_ask = _finite(binance.get("upAsk"))
        down_ask = _finite(binance.get("downAsk"))
        comparison = {
            "polyUpMid": poly_up,
            "polyDownMid": poly_down,
            "binanceUpMid": binance_up,
            "binanceDownMid": binance_down,
            "midGap": poly_up - binance_up if poly_up is not None and binance_up is not None else None,
            "executableEdgeUp": poly_up - up_ask if poly_up is not None and up_ask is not None else None,
            "executableEdgeDown": poly_down - down_ask if poly_down is not None and down_ask is not None else None,
            "polySourceAgeMs": source_age,
            "polyReceiptAgeMs": receipt_age,
        }
        return {
            "asset": asset,
            "symbol": state.get("symbol"),
            "sourceMode": state.get("sourceMode"),
            "bucketStartSec": bucket or None,
            "windowEndMs": state.get("windowEndMs"),
            "secondsLeft": seconds_left,
            "poly": poly,
            "binance": binance,
            "comparison": comparison,
            "trajectory": list(state["trajectory"]),
        }

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            assets = {asset: self._asset_snapshot(asset) for asset in ASSETS}
        return {
            "version": VERSION,
            "readOnly": True,
            "tradingMethodsPresent": False,
            "credentialSource": self.credential_source,
            "startedAtMs": self.started_at_ms,
            "updatedAtMs": _now_ms(),
            "lastError": self.last_error,
            "assets": assets,
            "sampling": {
                "sampleMs": SAMPLE_MS,
                "binancePollSeconds": POLL_SECONDS,
                "trajectoryPoints": TRAJECTORY_POINTS,
                "retentionHours": RETENTION_HOURS,
                "btcExternalRequestsDuplicated": False,
                "btcSource": "existing 8766 + 8767",
                "ethBnbBinanceReadOnly": True,
                "ethBnbPolyDedicatedWs": True,
            },
        }


class _Handler(BaseHTTPRequestHandler):
    observer: MultiPredictionObserver

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, allow_nan=False, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path not in {"/state", "/health", "/api/state"}:
            self._send(404, {"ok": False, "error": "not found"})
            return
        self._send(200, {"ok": True, "state": self.observer.snapshot()})

    def do_POST(self) -> None:  # noqa: N802
        self._send(405, {"ok": False, "error": "read-only observer"})

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def main() -> int:
    observer = MultiPredictionObserver()
    observer.start()
    handler = type("MultiPredictionObserverHandler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Read-only BTC/ETH/BNB prediction observer listening on http://{HOST}:{PORT}/state; db={DB_PATH}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        observer.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
