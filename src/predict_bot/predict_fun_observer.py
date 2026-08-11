from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

try:
    import websocket
except ImportError:  # pragma: no cover
    websocket = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("PREDICT_FUN_OBSERVER_DB", ROOT / "data" / "predict_fun_observer.db"))
HOST = os.environ.get("PREDICT_FUN_OBSERVER_HOST", "127.0.0.1")
PORT = int(os.environ.get("PREDICT_FUN_OBSERVER_PORT", "8771"))
API_BASE = os.environ.get("PREDICT_FUN_API_BASE", "https://api.predict.fun").rstrip("/")
WS_URL = os.environ.get("PREDICT_FUN_WS_URL", "wss://ws.predict.fun/ws")
API_KEY_ENV = "PREDICT_FUN_API_KEY"
DISCOVERY_RETRY_SECONDS = max(5.0, float(os.environ.get("PREDICT_FUN_DISCOVERY_RETRY_SECONDS", "5")))
SAMPLE_MS = max(500, int(os.environ.get("PREDICT_FUN_SAMPLE_MS", "1000")))
TRAJECTORY_POINTS = max(120, int(os.environ.get("PREDICT_FUN_TRAJECTORY_POINTS", "420")))
RETENTION_HOURS = max(1.0, float(os.environ.get("PREDICT_FUN_RETENTION_HOURS", "24")))
VERSION = "PREDICT_FUN_MULTI_OBSERVER_V1"

ASSETS = {
    "BTC": {"query": "BTC Up or Down 5m", "slugPrefix": "btc-updown-5m"},
    "ETH": {"query": "ETH Up or Down 5m", "slugPrefix": "eth-updown-5m"},
    "BNB": {"query": "BNB Up or Down 5m", "slugPrefix": "bnb-updown-5m"},
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_int(value: Any) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _iso_ms(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(parsed.timestamp() * 1000)


def current_bucket(now_seconds: float | None = None) -> int:
    now = time.time() if now_seconds is None else float(now_seconds)
    return int(now // 300) * 300


def expected_slug(asset: str, bucket: int) -> str:
    config = ASSETS[str(asset).upper()]
    return f"{config['slugPrefix']}-{int(bucket)}"


def complement_price(value: Any, decimal_precision: int) -> float | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not number.is_finite() or number < 0 or number > 1:
        return None
    precision = max(0, min(12, int(decimal_precision)))
    quantum = Decimal(1).scaleb(-precision)
    return float((Decimal(1) - number).quantize(quantum))


def parse_predict_orderbook(payload: Any, *, decimal_precision: int) -> dict[str, Any] | None:
    data = _record(payload)
    if isinstance(data.get("data"), dict) and ("success" in data or len(data) <= 3):
        data = _record(data.get("data"))
    asks = data.get("asks")
    bids = data.get("bids")
    asks = asks if isinstance(asks, list) else []
    bids = bids if isinstance(bids, list) else []

    def prices(levels: list[Any]) -> list[float]:
        values: list[float] = []
        for level in levels:
            if not isinstance(level, (list, tuple)) or not level:
                continue
            price = _finite(level[0])
            if price is not None and 0 <= price <= 1:
                values.append(price)
        return values

    ask_prices = prices(asks)
    bid_prices = prices(bids)
    up_ask = min(ask_prices) if ask_prices else None
    up_bid = max(bid_prices) if bid_prices else None
    down_bid = complement_price(up_ask, decimal_precision) if up_ask is not None else None
    down_ask = complement_price(up_bid, decimal_precision) if up_bid is not None else None

    def mid(bid: float | None, ask: float | None) -> float | None:
        if bid is not None and ask is not None:
            return (bid + ask) / 2.0
        return bid if bid is not None else ask

    return {
        "marketId": _positive_int(data.get("marketId")),
        "updateTimestampMs": _positive_int(data.get("updateTimestampMs")),
        "orderCount": _positive_int(data.get("orderCount")) or 0,
        "upBid": up_bid,
        "upAsk": up_ask,
        "upMid": mid(up_bid, up_ask),
        "downBid": down_bid,
        "downAsk": down_ask,
        "downMid": mid(down_bid, down_ask),
    }


def _candidate_market(raw: Any, *, category: dict[str, Any] | None = None) -> dict[str, Any] | None:
    market = _record(raw)
    market_id = _positive_int(market.get("id"))
    if market_id is None:
        return None
    if str(market.get("tradingStatus") or "OPEN").upper() not in {"OPEN", "TRADING"}:
        return None
    if market.get("isVisible") is False:
        return None
    category = category or {}
    variant = _record(market.get("variantData")) or _record(category.get("variantData"))
    variant_type = str(variant.get("type") or market.get("marketVariant") or category.get("marketVariant") or "").upper()
    if variant_type and variant_type not in {"CRYPTO_UP_DOWN", "CRYPTOUPDOWN"}:
        return None
    return market


def select_predict_market(payload: Any, *, asset: str, bucket: int, now_ms: int | None = None) -> dict[str, Any] | None:
    asset = str(asset).upper()
    if asset not in ASSETS:
        return None
    body = _record(payload)
    data = _record(body.get("data")) if isinstance(body.get("data"), dict) else body
    categories = data.get("categories") if isinstance(data.get("categories"), list) else []
    top_markets = data.get("markets") if isinstance(data.get("markets"), list) else []
    slug = expected_slug(asset, bucket)
    now = _now_ms() if now_ms is None else int(now_ms)
    candidates: list[tuple[int, dict[str, Any], dict[str, Any], str]] = []

    def score(market: dict[str, Any], category: dict[str, Any], source: str) -> int:
        text = " ".join(str(market.get(k) or "") for k in ("title", "question", "description")).lower()
        category_text = " ".join(str(category.get(k) or "") for k in ("slug", "title", "description")).lower()
        category_slug = str(market.get("categorySlug") or category.get("slug") or "").lower()
        variant = _record(market.get("variantData")) or _record(category.get("variantData"))
        feed_symbol = str(variant.get("priceFeedSymbol") or "").upper()
        result = 0
        if category_slug == slug:
            result += 1000
        elif slug in category_slug:
            result += 600
        if asset.lower() in text or asset.lower() in category_text or asset in feed_symbol:
            result += 100
        if "up or down" in text or "up or down" in category_text:
            result += 60
        if any(token in text or token in category_text for token in ("5m", "5 min", "5-minute", "5 minute")):
            result += 60
        if str(market.get("tradingStatus") or "").upper() == "OPEN":
            result += 30
        starts = _iso_ms(category.get("startsAt"))
        ends = _iso_ms(category.get("endsAt"))
        if starts is not None and ends is not None and starts <= now < ends:
            result += 200
        created = _iso_ms(market.get("createdAt")) or 0
        if created > 0:
            result += max(0, min(20, int((created - bucket * 1000) / 15_000) + 10))
        if source == "CATEGORY":
            result += 10
        return result

    for raw_category in categories:
        category = _record(raw_category)
        markets = category.get("markets") if isinstance(category.get("markets"), list) else []
        for raw_market in markets:
            market = _candidate_market(raw_market, category=category)
            if market is not None:
                candidates.append((score(market, category, "CATEGORY"), market, category, "CATEGORY"))
    for raw_market in top_markets:
        market = _candidate_market(raw_market)
        if market is not None:
            candidates.append((score(market, {}, "MARKET"), market, {}, "MARKET"))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], _positive_int(item[1].get("id")) or 0), reverse=True)
    best_score, market, category, source = candidates[0]
    category_slug = str(market.get("categorySlug") or category.get("slug") or "").lower()
    text = " ".join(str(x or "") for x in (market.get("title"), market.get("question"), category.get("title"))).lower()
    strong_text = asset.lower() in text and "up or down" in text and any(token in text for token in ("5m", "5 min", "5-minute", "5 minute"))
    if category_slug != slug and not strong_text:
        return None

    starts = _iso_ms(category.get("startsAt"))
    ends = _iso_ms(category.get("endsAt"))
    variant = _record(market.get("variantData")) or _record(category.get("variantData"))
    precision_value = market.get("decimalPrecision")
    try:
        decimal_precision = max(0, min(12, int(precision_value)))
    except (TypeError, ValueError):
        decimal_precision = 3
    return {
        "asset": asset,
        "id": int(market["id"]),
        "title": str(market.get("title") or category.get("title") or f"{asset} Up or Down 5m"),
        "question": str(market.get("question") or "") or None,
        "categorySlug": category_slug or None,
        "conditionId": str(market.get("conditionId") or "") or None,
        "decimalPrecision": decimal_precision,
        "priceFeedSymbol": str(variant.get("priceFeedSymbol") or "") or None,
        "bucketStartSec": int(bucket),
        "windowStartMs": starts if starts is not None else int(bucket) * 1000,
        "windowEndMs": ends if ends is not None else (int(bucket) + 300) * 1000,
        "discoverySource": source,
        "discoveryScore": best_score,
    }


class PredictFunObserver:
    """Read-only Predict.fun BTC/ETH/BNB 5m order-book observer."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.api_key = str(os.environ.get(API_KEY_ENV) or "").strip()
        self.lock = threading.RLock()
        self.db_lock = threading.RLock()
        self.stop_event = threading.Event()
        self.started_at_ms = _now_ms()
        self.last_error: str | None = None
        self.ws_generation = 0
        self.ws: Any = None
        self.ws_status = "CONFIG_REQUIRED" if not self.api_key else "DISCONNECTED"
        self.ws_last_heartbeat_ms: int | None = None
        self.ws_last_message_ms: int | None = None
        self.ws_error: str | None = None
        self.market_to_asset: dict[int, str] = {}
        self.last_subscription_ids: tuple[int, ...] = ()
        self.next_discovery_at: dict[str, float] = {asset: 0.0 for asset in ASSETS}
        self.http = httpx.Client(
            timeout=httpx.Timeout(2.5, connect=0.75),
            verify=True,
            trust_env=False,
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "User-Agent": "BTC-5M-Lab-PredictFun-Observer/1.0",
                **({"x-api-key": self.api_key} if self.api_key else {}),
            },
        )
        self.assets: dict[str, dict[str, Any]] = {}
        for asset in ASSETS:
            self.assets[asset] = {
                "asset": asset,
                "status": "CONFIG_REQUIRED" if not self.api_key else "WAITING_MARKET",
                "error": None if self.api_key else f"{API_KEY_ENV} is not configured",
                "bucketStartSec": None,
                "windowEndMs": None,
                "market": None,
                "book": {
                    "upBid": None, "upAsk": None, "upMid": None,
                    "downBid": None, "downAsk": None, "downMid": None,
                    "sourceTimestampMs": None, "receivedTimestampMs": None,
                    "orderCount": 0,
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

    def _create_schema(self) -> None:
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS predict_fun_trajectory (
                    asset TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    market_bucket INTEGER NOT NULL,
                    sample_bucket_ms INTEGER NOT NULL,
                    sampled_at_ms INTEGER NOT NULL,
                    seconds_left REAL,
                    up_mid REAL,
                    down_mid REAL,
                    up_bid REAL,
                    up_ask REAL,
                    down_bid REAL,
                    down_ask REAL,
                    source_age_ms REAL,
                    receipt_age_ms REAL,
                    transport_age_ms REAL,
                    PRIMARY KEY(asset, market_id, sample_bucket_ms)
                );
                CREATE INDEX IF NOT EXISTS idx_predict_fun_sampled
                    ON predict_fun_trajectory(sampled_at_ms);
                """
            )
            self.db.commit()

    def start(self) -> None:
        if not self.api_key:
            return
        threading.Thread(target=self._market_loop, name="predict-fun-markets", daemon=True).start()
        threading.Thread(target=self._sample_loop, name="predict-fun-samples", daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:
            pass
        try:
            self.http.close()
        except Exception:
            pass
        with self.db_lock:
            self.db.commit()
            self.db.close()

    def _search(self, asset: str) -> Any:
        params = {"query": ASSETS[asset]["query"], "includeResolved": "false", "limit": 20}
        response = self.http.get(f"{API_BASE}/v1/search", params=params)
        response.raise_for_status()
        return response.json()

    def _market_loop(self) -> None:
        while not self.stop_event.is_set():
            changed = False
            now_monotonic = time.monotonic()
            bucket = current_bucket()
            for asset in ASSETS:
                with self.lock:
                    current = _record(self.assets[asset].get("market"))
                if int(current.get("bucketStartSec") or 0) == bucket:
                    continue
                if now_monotonic < self.next_discovery_at[asset]:
                    continue
                self.next_discovery_at[asset] = now_monotonic + DISCOVERY_RETRY_SECONDS
                try:
                    payload = self._search(asset)
                    candidate = select_predict_market(payload, asset=asset, bucket=bucket)
                except Exception as exc:
                    with self.lock:
                        self.assets[asset]["status"] = "DISCOVERY_ERROR"
                        self.assets[asset]["error"] = str(exc)[:350]
                    continue
                if candidate is None:
                    with self.lock:
                        self.assets[asset]["status"] = "WAITING_MARKET"
                        self.assets[asset]["error"] = f"no strict current Predict.fun {asset} 5m market for {expected_slug(asset, bucket)}"
                    continue
                with self.lock:
                    state = self.assets[asset]
                    previous_id = _positive_int(_record(state.get("market")).get("id"))
                    state["market"] = candidate
                    state["bucketStartSec"] = bucket
                    state["windowEndMs"] = candidate["windowEndMs"]
                    state["status"] = "CONNECTING"
                    state["error"] = None
                    if previous_id != candidate["id"]:
                        state["book"] = {
                            "upBid": None, "upAsk": None, "upMid": None,
                            "downBid": None, "downAsk": None, "downMid": None,
                            "sourceTimestampMs": None, "receivedTimestampMs": None,
                            "orderCount": 0,
                        }
                        state["trajectory"].clear()
                        self._load_trajectory_locked(asset, candidate["id"])
                changed = True
            if changed:
                self._restart_ws()
            self.stop_event.wait(0.5)

    def _restart_ws(self) -> None:
        with self.lock:
            mapping = {
                int(market_id): asset
                for asset, state in self.assets.items()
                if (market_id := _positive_int(_record(state.get("market")).get("id"))) is not None
            }
        ids = tuple(sorted(mapping))
        if not ids or ids == self.last_subscription_ids:
            return
        self.last_subscription_ids = ids
        self.market_to_asset = mapping
        self.ws_generation += 1
        generation = self.ws_generation
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:
            pass
        threading.Thread(target=self._ws_loop, args=(generation,), name=f"predict-fun-ws-{generation}", daemon=True).start()

    def _ws_loop(self, generation: int) -> None:
        if websocket is None:
            self.ws_status = "ERROR"
            self.ws_error = "websocket-client is not installed"
            return
        while not self.stop_event.is_set() and generation == self.ws_generation:
            ws = websocket.WebSocketApp(
                WS_URL,
                header=[f"x-api-key: {self.api_key}"],
                on_open=lambda app: self._ws_open(app, generation),
                on_message=lambda app, raw: self._ws_message(app, raw, generation),
                on_error=lambda app, error: self._ws_error(error, generation),
                on_close=lambda app, code, message: self._ws_close(code, message, generation),
            )
            self.ws = ws
            self.ws_status = "CONNECTING"
            try:
                ws.run_forever()
            except Exception as exc:  # pragma: no cover
                self._ws_error(exc, generation)
            if self.stop_event.wait(2.0):
                return

    def _ws_open(self, ws: Any, generation: int) -> None:
        if generation != self.ws_generation:
            ws.close()
            return
        self.ws_status = "LIVE"
        self.ws_error = None
        request_id = 1
        for market_id in self.last_subscription_ids:
            ws.send(json.dumps({"method": "subscribe", "requestId": request_id, "params": [f"predictOrderbook/{market_id}"]}, separators=(",", ":")))
            request_id += 1

    def _ws_message(self, ws: Any, raw: str, generation: int) -> None:
        if generation != self.ws_generation:
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        self.ws_last_message_ms = _now_ms()
        if payload.get("type") == "R":
            if payload.get("success") is False:
                self.ws_error = str(payload.get("error") or "subscription rejected")[:350]
            return
        if payload.get("type") != "M":
            return
        topic = str(payload.get("topic") or "")
        data = payload.get("data")
        if topic == "heartbeat":
            self.ws_last_heartbeat_ms = _now_ms()
            try:
                ws.send(json.dumps({"method": "heartbeat", "data": data}, separators=(",", ":")))
            except Exception:
                pass
            return
        match = re.fullmatch(r"predictOrderbook/(\d+)", topic)
        if not match:
            return
        market_id = int(match.group(1))
        asset = self.market_to_asset.get(market_id)
        if asset is None:
            return
        with self.lock:
            market = _record(self.assets[asset].get("market"))
            try:
                precision = max(0, min(12, int(market.get("decimalPrecision"))))
            except (TypeError, ValueError):
                precision = 3
        book = parse_predict_orderbook(data, decimal_precision=precision)
        if book is None:
            return
        received_ms = _now_ms()
        source_ms = _positive_int(book.get("updateTimestampMs"))
        with self.lock:
            state = self.assets[asset]
            state["book"] = {**book, "sourceTimestampMs": source_ms, "receivedTimestampMs": received_ms}
            state["status"] = "LIVE"
            state["error"] = None

    def _ws_error(self, error: Any, generation: int) -> None:
        if generation != self.ws_generation:
            return
        self.ws_status = "RECONNECTING"
        self.ws_error = str(error)[:350]

    def _ws_close(self, code: Any, message: Any, generation: int) -> None:
        if generation != self.ws_generation or self.stop_event.is_set():
            return
        self._ws_error(f"close {code}: {message}", generation)

    def _sample_loop(self) -> None:
        next_prune = 0.0
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                self._sample_once()
                if started >= next_prune:
                    self._prune()
                    next_prune = started + 60.0
            except Exception as exc:
                self.last_error = f"sample: {str(exc)[:350]}"
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(0.05, SAMPLE_MS / 1000.0 - elapsed))

    def _sample_once(self) -> None:
        now = _now_ms()
        sample_bucket = (now // SAMPLE_MS) * SAMPLE_MS
        inserts: list[tuple[Any, ...]] = []
        with self.lock:
            for asset, state in self.assets.items():
                market = _record(state.get("market"))
                book = _record(state.get("book"))
                market_id = _positive_int(market.get("id"))
                bucket = _positive_int(market.get("bucketStartSec"))
                if market_id is None or bucket is None:
                    continue
                source_ms = _positive_int(book.get("sourceTimestampMs"))
                received_ms = _positive_int(book.get("receivedTimestampMs"))
                up_mid = _finite(book.get("upMid"))
                down_mid = _finite(book.get("downMid"))
                if up_mid is None and down_mid is None:
                    continue
                end_ms = _positive_int(market.get("windowEndMs")) or (bucket + 300) * 1000
                seconds_left = max(0.0, (end_ms - now) / 1000.0)
                source_age = max(0.0, now - source_ms) if source_ms is not None else None
                receipt_age = max(0.0, now - received_ms) if received_ms is not None else None
                transport_age = received_ms - source_ms if source_ms is not None and received_ms is not None else None
                point = {
                    "sampledAtMs": now,
                    "secondsLeft": seconds_left,
                    "up": up_mid,
                    "down": down_mid,
                    "sourceAgeMs": source_age,
                    "receiptAgeMs": receipt_age,
                }
                state["trajectory"].append(point)
                inserts.append((
                    asset, market_id, bucket, sample_bucket, now, seconds_left,
                    up_mid, down_mid, _finite(book.get("upBid")), _finite(book.get("upAsk")),
                    _finite(book.get("downBid")), _finite(book.get("downAsk")),
                    source_age, receipt_age, transport_age,
                ))
        if inserts:
            with self.db_lock:
                self.db.executemany(
                    """INSERT OR REPLACE INTO predict_fun_trajectory(
                           asset,market_id,market_bucket,sample_bucket_ms,sampled_at_ms,
                           seconds_left,up_mid,down_mid,up_bid,up_ask,down_bid,down_ask,
                           source_age_ms,receipt_age_ms,transport_age_ms
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    inserts,
                )
                self.db.commit()

    def _load_trajectory_locked(self, asset: str, market_id: int) -> None:
        with self.db_lock:
            rows = self.db.execute(
                """SELECT sampled_at_ms,seconds_left,up_mid,down_mid,source_age_ms,receipt_age_ms
                     FROM predict_fun_trajectory
                    WHERE asset=? AND market_id=?
                    ORDER BY sample_bucket_ms ASC LIMIT ?""",
                (asset, int(market_id), TRAJECTORY_POINTS),
            ).fetchall()
        trajectory = self.assets[asset]["trajectory"]
        for row in rows:
            trajectory.append({
                "sampledAtMs": int(row["sampled_at_ms"]),
                "secondsLeft": _finite(row["seconds_left"]),
                "up": _finite(row["up_mid"]),
                "down": _finite(row["down_mid"]),
                "sourceAgeMs": _finite(row["source_age_ms"]),
                "receiptAgeMs": _finite(row["receipt_age_ms"]),
            })

    def _prune(self) -> None:
        cutoff = _now_ms() - int(RETENTION_HOURS * 3600_000)
        with self.db_lock:
            self.db.execute("DELETE FROM predict_fun_trajectory WHERE sampled_at_ms < ?", (cutoff,))
            self.db.commit()

    def snapshot(self) -> dict[str, Any]:
        now = _now_ms()
        with self.lock:
            assets: dict[str, Any] = {}
            for asset, state in self.assets.items():
                market = dict(_record(state.get("market"))) or None
                book = dict(_record(state.get("book")))
                source_ms = _positive_int(book.get("sourceTimestampMs"))
                received_ms = _positive_int(book.get("receivedTimestampMs"))
                end_ms = _positive_int((market or {}).get("windowEndMs"))
                status = str(state.get("status") or "UNKNOWN")
                source_age = max(0, now - source_ms) if source_ms is not None else None
                receipt_age = max(0, now - received_ms) if received_ms is not None else None
                if status == "LIVE" and receipt_age is not None and receipt_age > 5_000:
                    status = "STALE"
                assets[asset] = {
                    "asset": asset,
                    "status": status,
                    "error": state.get("error"),
                    "bucketStartSec": state.get("bucketStartSec"),
                    "windowEndMs": end_ms,
                    "secondsLeft": max(0.0, (end_ms - now) / 1000.0) if end_ms is not None else None,
                    "market": market,
                    "up": {"bid": book.get("upBid"), "ask": book.get("upAsk"), "mid": book.get("upMid")},
                    "down": {"bid": book.get("downBid"), "ask": book.get("downAsk"), "mid": book.get("downMid")},
                    "sourceTimestampMs": source_ms,
                    "receivedTimestampMs": received_ms,
                    "sourceAgeMs": source_age,
                    "receiptAgeMs": receipt_age,
                    "transportAgeMs": (received_ms - source_ms) if source_ms is not None and received_ms is not None else None,
                    "orderCount": book.get("orderCount"),
                    "trajectory": list(state["trajectory"]),
                }
        return {
            "version": VERSION,
            "readOnly": True,
            "ordersSupported": False,
            "apiKeyConfigured": bool(self.api_key),
            "credentialSource": API_KEY_ENV if self.api_key else "UNAVAILABLE",
            "apiBase": API_BASE,
            "wsUrl": WS_URL,
            "startedAtMs": self.started_at_ms,
            "lastError": self.last_error,
            "ws": {
                "status": self.ws_status,
                "generation": self.ws_generation,
                "subscriptions": list(self.last_subscription_ids),
                "lastHeartbeatMs": self.ws_last_heartbeat_ms,
                "lastMessageMs": self.ws_last_message_ms,
                "error": self.ws_error,
            },
            "assets": assets,
        }


class _Handler(BaseHTTPRequestHandler):
    observer: PredictFunObserver

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
    observer = PredictFunObserver()
    observer.start()
    handler = type("PredictFunObserverHandler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Read-only Predict.fun BTC/ETH/BNB observer listening on http://{HOST}:{PORT}/state; "
        f"apiKeyConfigured={bool(observer.api_key)}; db={DB_PATH}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        observer.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
