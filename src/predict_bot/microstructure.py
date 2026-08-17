from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import queue
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable

try:
    import websocket
except ImportError:  # pragma: no cover - exercised through the public state fallback.
    websocket = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[2]
MICRO_DB_PATH = Path(os.environ.get("PREDICT_MICRO_DB", ROOT / "data" / "microstructure.db"))
RAW_RETENTION_HOURS = max(1.0, float(os.environ.get("PREDICT_MICRO_RAW_RETENTION_HOURS", "6")))
SNAPSHOT_RETENTION_HOURS = max(
    RAW_RETENTION_HOURS,
    float(os.environ.get("PREDICT_MICRO_SNAPSHOT_RETENTION_HOURS", "168")),
)
LIQUIDITY_RETENTION_HOURS = max(
    SNAPSHOT_RETENTION_HOURS,
    float(os.environ.get("PREDICT_MICRO_LIQUIDITY_RETENTION_HOURS", "720")),
)
SNAPSHOT_INTERVAL_MS = max(100, int(os.environ.get("PREDICT_MICRO_SNAPSHOT_INTERVAL_MS", "250")))
QUEUE_MAX = max(1_000, int(os.environ.get("PREDICT_MICRO_QUEUE_MAX", "50000")))
PARSER_VERSION = "microstructure_v2"

SPOT_TRADE_URL = (
    "wss://stream.binance.com:9443/stream?streams="
    "btcusdt@trade"
)
SPOT_BOOK_URL = (
    "wss://stream.binance.com:9443/stream?streams="
    "btcusdt@bookTicker/btcusdt@depth10@100ms"
)
SPOT_TRADE_WATCHDOG_SILENCE_SECONDS = max(
    4.5, float(os.environ.get("PREDICT_SPOT_TRADE_WATCHDOG_SILENCE_SECONDS", "5.0"))
)
SPOT_TRADE_RECONNECT_THROTTLE_SECONDS = max(
    5.0, float(os.environ.get("PREDICT_SPOT_TRADE_RECONNECT_THROTTLE_SECONDS", "15.0"))
)
FUTURES_PUBLIC_URL = (
    "wss://fstream.binance.com/public/stream?streams="
    "btcusdt@bookTicker/btcusdt@depth10@100ms"
)
FUTURES_MARKET_URL = (
    "wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade"
)
PREDICTION_BASE_URL = "wss://api.binance.com/sapi/wss"
PREDICTION_TOPIC = "web3_prediction_orderbook_data"
PREDICTION_ORIENTATION_RECEIPT_WINDOW_MS = 5_000
PREDICTION_ORIENTATION_MAX_PRICE_ERROR = 0.05
PREDICTION_ORIENTATION_MIN_ERROR_MARGIN = 0.01
PREDICTION_ORIENTATION_CONFIRMATIONS = 2
PREDICTION_ORIENTATION_TIMEOUT_SECONDS = 10.0
PREDICTION_ORIENTATION_RECONNECT_THROTTLE_SECONDS = 15.0
PREDICTION_STALE_RECONNECT_SECONDS = 10.0
PREDICTION_STALE_RECONNECT_THROTTLE_SECONDS = 15.0
PREDICTION_ROLLOVER_RECONNECT_THROTTLE_SECONDS = max(
    0.25,
    float(
        os.environ.get(
            "PREDICT_PREDICTION_ROLLOVER_RECONNECT_THROTTLE_SECONDS",
            "1.0",
        )
    ),
)
PREDICTION_MARKET_WATCH_SUPERVISOR_SECONDS = max(
    0.25,
    float(
        os.environ.get(
            "PREDICT_PREDICTION_MARKET_WATCH_SUPERVISOR_SECONDS",
            "0.5",
        )
    ),
)
PREDICTION_MARKET_WATCH_HEARTBEAT_STALE_SECONDS = max(
    1.0,
    float(
        os.environ.get(
            "PREDICT_PREDICTION_MARKET_WATCH_HEARTBEAT_STALE_SECONDS",
            "2.0",
        )
    ),
)
PREDICTION_CONTENT_WARNING_AGE_MS = max(
    250.0,
    float(
        os.environ.get(
            "PREDICT_PREDICTION_CONTENT_WARNING_AGE_MS",
            "2000",
        )
    ),
)
PREDICTION_MAX_VERSION_AGE_MS = max(
    1_000.0,
    float(os.environ.get("PREDICT_PREDICTION_MAX_VERSION_AGE_MS", "10000")),
)
PREDICTION_VERIFIED_ORIENTATIONS = frozenset(
    {"DIRECT_UP_VERIFIED", "INVERTED_TO_UP_VERIFIED"}
)


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_levels(value: Any, *, reverse: bool) -> list[list[float]]:
    levels: list[list[float]] = []
    for item in value or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        price = _float(item[0])
        size = _float(item[1])
        if price is None or size is None or price <= 0 or size <= 0:
            continue
        levels.append([price, size])
    levels.sort(key=lambda item: item[0], reverse=reverse)
    return levels[:10]


def queue_imbalance(bids: list[list[float]], asks: list[list[float]]) -> float | None:
    bid_size = sum(level[1] for level in bids[:10])
    ask_size = sum(level[1] for level in asks[:10])
    total = bid_size + ask_size
    return (bid_size - ask_size) / total if total > 0 else None


def microprice(
    bid: float | None,
    bid_size: float | None,
    ask: float | None,
    ask_size: float | None,
) -> float | None:
    if None in (bid, bid_size, ask, ask_size):
        return None
    assert bid is not None and bid_size is not None and ask is not None and ask_size is not None
    total = bid_size + ask_size
    if bid <= 0 or ask <= 0 or bid_size < 0 or ask_size < 0 or total <= 0:
        return None
    return (ask * bid_size + bid * ask_size) / total


def signed_aggressor_quantity(payload: dict[str, Any], *, futures: bool) -> tuple[float, float]:
    """Return signed and absolute visible quantity; buyer-maker means sell aggressor."""
    raw_quantity = payload.get("nq") if futures and payload.get("nq") is not None else payload.get("q")
    quantity = _float(raw_quantity) or 0.0
    signed = -quantity if bool(payload.get("m")) else quantity
    return signed, quantity


def build_prediction_ws_url(
    api_secret: str,
    *,
    timestamp_ms: int,
    random_value: str,
    topic: str = PREDICTION_TOPIC,
) -> tuple[str, str]:
    params = {
        "random": random_value,
        "recvWindow": "30000",
        "timestamp": str(timestamp_ms),
        "topic": topic,
    }
    canonical = urllib.parse.urlencode(sorted(params.items()))
    signature = hmac.new(
        api_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{PREDICTION_BASE_URL}?{canonical}&signature={signature}", canonical


def _base_event(
    source: str,
    stream: str,
    payload: dict[str, Any],
    *,
    received_wall_ns: int,
    received_monotonic_ns: int,
    session_id: str,
) -> dict[str, Any]:
    return {
        "source": source,
        "stream": stream,
        "market_id": None,
        "exchange_event_ms": _int(payload.get("E")),
        "exchange_trade_ms": _int(payload.get("T")),
        "received_wall_ns": received_wall_ns,
        "received_monotonic_ns": received_monotonic_ns,
        "enqueued_monotonic_ns": time.monotonic_ns(),
        "session_id": session_id,
        "update_id": _int(payload.get("u") or payload.get("lastUpdateId")),
        "first_update_id": _int(payload.get("U")),
        "previous_update_id": _int(payload.get("pu")),
        "trade_id": _int(payload.get("t") or payload.get("a")),
        "price": None,
        "quantity": None,
        "visible_quantity": None,
        "aggressor": None,
        "best_bid": None,
        "best_bid_qty": None,
        "best_ask": None,
        "best_ask_qty": None,
        "bids": [],
        "asks": [],
        "raw_json": json.dumps(payload, separators=(",", ":"), sort_keys=True),
        "parser_version": PARSER_VERSION,
    }


def parse_combined_message(
    raw: str,
    *,
    source: str,
    received_wall_ns: int,
    received_monotonic_ns: int,
    session_id: str,
) -> dict[str, Any] | None:
    envelope = json.loads(raw)
    if not isinstance(envelope, dict) or not isinstance(envelope.get("data"), dict):
        return None
    stream_name = str(envelope.get("stream") or "")
    payload = envelope["data"]
    is_futures = source == "futures"
    if is_futures and payload.get("st") not in (None, 1, "1"):
        return None

    if "aggTrade" in stream_name or stream_name.endswith("@trade"):
        kind = "aggTrade" if "aggTrade" in stream_name else "trade"
    elif "bookTicker" in stream_name:
        kind = "bookTicker"
    elif "depth" in stream_name:
        kind = "depth"
    else:
        return None

    event = _base_event(
        source,
        kind,
        payload,
        received_wall_ns=received_wall_ns,
        received_monotonic_ns=received_monotonic_ns,
        session_id=session_id,
    )
    # Preserve the complete wire frame for replay, including the combined
    # stream name and envelope fields, not only the normalized inner payload.
    event["raw_json"] = raw
    if kind in ("trade", "aggTrade"):
        signed, visible = signed_aggressor_quantity(payload, futures=is_futures)
        event.update(
            price=_float(payload.get("p")),
            quantity=_float(payload.get("q")),
            visible_quantity=visible,
            aggressor=1 if signed > 0 else -1 if signed < 0 else 0,
        )
    elif kind == "bookTicker":
        event.update(
            best_bid=_float(payload.get("b")),
            best_bid_qty=_float(payload.get("B")),
            best_ask=_float(payload.get("a")),
            best_ask_qty=_float(payload.get("A")),
        )
        # Spot bookTicker has no exchange event timestamp in the official schema.
        if source == "spot":
            event["exchange_event_ms"] = None
            event["exchange_trade_ms"] = None
    else:
        bids = normalize_levels(payload.get("b") or payload.get("bids"), reverse=True)
        asks = normalize_levels(payload.get("a") or payload.get("asks"), reverse=False)
        event.update(bids=bids, asks=asks)
        if bids:
            event.update(best_bid=bids[0][0], best_bid_qty=bids[0][1])
        if asks:
            event.update(best_ask=asks[0][0], best_ask_qty=asks[0][1])
    return event


def parse_prediction_message(
    raw: str,
    *,
    received_wall_ns: int,
    received_monotonic_ns: int,
    session_id: str,
) -> dict[str, Any] | None:
    envelope = json.loads(raw)
    # The documented envelope is TOPIC. The production dynamic orderbook topic
    # currently emits DATA, so accept both while keeping COMMAND responses out.
    if not isinstance(envelope, dict) or envelope.get("type") not in ("TOPIC", "DATA"):
        return None
    inner = envelope.get("data")
    if isinstance(inner, str):
        inner = json.loads(inner)
    if not isinstance(inner, dict) or inner.get("msgType") != "orderbook":
        return None
    event = _base_event(
        "prediction",
        "orderbook",
        inner,
        received_wall_ns=received_wall_ns,
        received_monotonic_ns=received_monotonic_ns,
        session_id=session_id,
    )
    event["raw_json"] = raw
    bids = normalize_levels(inner.get("bids"), reverse=True)
    asks = normalize_levels(inner.get("asks"), reverse=False)
    # updateTimestampMs identifies the version/content age of this orderbook.
    # It is not a transport-latency timestamp, but it must gate strategy and
    # execution freshness: the Prediction stream can replay minute-old books
    # while frames continue to arrive locally.  exchange_event_ms remains a
    # transitional database-compatibility alias.
    prediction_book_version_ms = _int(inner.get("updateTimestampMs"))
    event.update(
        market_id=_int(inner.get("marketId")),
        prediction_book_version_ms=prediction_book_version_ms,
        exchange_event_ms=prediction_book_version_ms,
        update_id=prediction_book_version_ms,
        bids=bids,
        asks=asks,
    )
    if bids:
        event.update(best_bid=bids[0][0], best_bid_qty=bids[0][1])
    if asks:
        event.update(best_ask=asks[0][0], best_ask_qty=asks[0][1])
    return event


def _imbalance_from_trades(trades: deque[tuple[int, float, float]], now_ns: int, window_ms: int) -> float | None:
    cutoff = now_ns - window_ms * 1_000_000
    signed = absolute = 0.0
    for timestamp_ns, signed_notional, absolute_notional in reversed(trades):
        if timestamp_ns < cutoff:
            break
        signed += signed_notional
        absolute += absolute_notional
    return signed / absolute if absolute > 0 else None


def _trade_count(trades: deque[tuple[int, float, float]], now_ns: int, window_ms: int) -> int:
    cutoff = now_ns - window_ms * 1_000_000
    count = 0
    for timestamp_ns, _, _ in reversed(trades):
        if timestamp_ns < cutoff:
            break
        count += 1
    return count


def _safe_error(value: Any) -> str:
    text = str(value)
    for marker in ("signature=", "X-MBX-APIKEY"):
        if marker in text:
            text = text.split(marker, 1)[0] + marker + "[redacted]"
    return text[:200]


def _utc_iso_from_ns(value: int) -> str:
    seconds, nanoseconds = divmod(int(value), 1_000_000_000)
    return (
        time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds))
        + f".{nanoseconds // 1_000:06d}Z"
    )


class FeatureEngine:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.books: dict[str, dict[str, Any]] = {}
        self.trades: dict[str, deque[tuple[int, float, float]]] = {
            "spot": deque(maxlen=20_000),
            "futures": deque(maxlen=20_000),
        }
        self.last_trade_price: dict[str, float | None] = {"spot": None, "futures": None}
        self.last_trade_monotonic_ns: dict[str, int] = {"spot": 0, "futures": 0}
        self.prediction_market_id: int | None = None
        self.removal_direction: str | None = None
        self.removal_strength: float | None = None
        self.last_removal_monotonic_ns = 0
        self.recent_liquidity: deque[dict[str, Any]] = deque(maxlen=100)
        self.last_snapshot_ns = 0

    @staticmethod
    def _book_metrics(
        book: dict[str, Any] | None,
        now_ns: int,
        stale_after_ns: int = 2_000_000_000,
    ) -> tuple[float | None, float | None]:
        if not book:
            return None, None
        updated_ns = int(book.get("received_monotonic_ns") or 0)
        if updated_ns <= 0 or now_ns - updated_ns > stale_after_ns:
            return None, None
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        qi = queue_imbalance(bids, asks) if bids or asks else None
        mp = microprice(
            book.get("best_bid"), book.get("best_bid_qty"),
            book.get("best_ask"), book.get("best_ask_qty"),
        )
        return qi, mp

    def reset_prediction(self, market_id: int | None = None) -> None:
        """Invalidate the prior full snapshot so reconnects never create fake removals."""
        with self.lock:
            self.books.pop("prediction", None)
            self.prediction_market_id = market_id
            self.removal_direction = None
            self.removal_strength = None
            self.last_removal_monotonic_ns = 0

    @staticmethod
    def _top_removal(previous: dict[str, Any], current: dict[str, Any], side: str) -> float:
        price_key = f"best_{side}"
        size_key = f"best_{side}_qty"
        old_price = previous.get(price_key)
        old_size = previous.get(size_key)
        new_price = current.get(price_key)
        new_size = current.get(size_key)
        if old_price is None or old_size is None or old_size <= 0:
            return 0.0
        if new_price is None:
            return 1.0
        worsened = new_price > old_price if side == "ask" else new_price < old_price
        improved = new_price < old_price if side == "ask" else new_price > old_price
        if worsened:
            return 1.0
        if improved or new_size is None or new_price != old_price:
            return 0.0
        return max(0.0, min(1.0, 1.0 - new_size / old_size))

    def update(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        liquidity: list[dict[str, Any]] = []
        source = str(event["source"])
        stream = str(event["stream"])
        now_ns = int(event["received_monotonic_ns"])
        with self.lock:
            if stream in ("trade", "aggTrade") and source in self.trades:
                price = event.get("price") or 0.0
                quantity = event.get("visible_quantity") or event.get("quantity") or 0.0
                signed_notional = price * quantity * (event.get("aggressor") or 0)
                absolute_notional = abs(price * quantity)
                self.trades[source].append((now_ns, signed_notional, absolute_notional))
                if price > 0:
                    self.last_trade_price[source] = price
                    self.last_trade_monotonic_ns[source] = now_ns
            elif stream in ("bookTicker", "depth") and source in ("spot", "futures"):
                previous = self.books.get(source, {})
                merged = dict(previous)
                merged.update({
                    key: event.get(key)
                    for key in ("best_bid", "best_bid_qty", "best_ask", "best_ask_qty")
                    if event.get(key) is not None
                })
                if stream == "depth":
                    # An empty side is meaningful and must clear old levels.
                    merged["bids"] = event.get("bids") or []
                    merged["asks"] = event.get("asks") or []
                merged["received_monotonic_ns"] = now_ns
                self.books[source] = merged
            elif source == "prediction" and stream == "orderbook":
                market_id = event.get("market_id")
                if not event.get("feature_eligible", True):
                    self.books.pop("prediction", None)
                    self.prediction_market_id = market_id
                    self.removal_direction = None
                    self.removal_strength = None
                    self.last_removal_monotonic_ns = 0
                    return liquidity
                if self.prediction_market_id != market_id:
                    self.books.pop("prediction", None)
                    self.removal_direction = None
                    self.removal_strength = None
                    self.last_removal_monotonic_ns = 0
                previous = self.books.get("prediction")
                current = {
                    key: event.get(key)
                    for key in (
                        "best_bid", "best_bid_qty", "best_ask", "best_ask_qty", "bids", "asks"
                    )
                }
                current["received_monotonic_ns"] = now_ns
                self.books["prediction"] = current
                self.prediction_market_id = market_id
                if previous:
                    strongest: tuple[str, float] | None = None
                    for book_side, direction, kind in (
                        ("ask", "UP", "ASK_LIQUIDITY_REMOVED"),
                        ("bid", "DOWN", "BID_LIQUIDITY_REMOVED"),
                    ):
                        strength = self._top_removal(previous, current, book_side)
                        if strength < 0.50:
                            continue
                        item = {
                            "timestamp": time.strftime(
                                "%Y-%m-%dT%H:%M:%S", time.gmtime(event["received_wall_ns"] / 1e9)
                            ) + "Z",
                            "timestamp_ns": event["received_wall_ns"],
                            "market_id": event.get("market_id"),
                            "side": direction,
                            "kind": kind,
                            "strength": strength,
                            "price": previous.get(f"best_{book_side}"),
                            "detail": "top-level liquidity removed; cancellation vs fill is unknown",
                        }
                        liquidity.append(item)
                        self.recent_liquidity.appendleft(item)
                        if strongest is None or strength >= strongest[1]:
                            strongest = (direction, strength)
                    if strongest is not None:
                        # The newest full snapshot wins; do not let a slightly
                        # stronger but older opposite signal linger for 2 sec.
                        self.removal_direction, self.removal_strength = strongest
                        self.last_removal_monotonic_ns = now_ns
        return liquidity

    def snapshot(self, now_ns: int, wall_ns: int) -> dict[str, Any]:
        with self.lock:
            spot_qi, spot_micro = self._book_metrics(self.books.get("spot"), now_ns)
            futures_qi, futures_micro = self._book_metrics(self.books.get("futures"), now_ns)
            prediction_book = self.books.get("prediction")
            prediction = (
                prediction_book or {}
                if prediction_book
                and now_ns - int(prediction_book.get("received_monotonic_ns") or 0) <= 5_000_000_000
                else {}
            )
            prediction_bid = prediction.get("best_bid")
            prediction_ask = prediction.get("best_ask")
            prediction_mid = (
                (prediction_bid + prediction_ask) / 2
                if prediction_bid is not None and prediction_ask is not None
                else None
            )
            spot_price = (
                self.last_trade_price["spot"]
                if self.last_trade_monotonic_ns["spot"] > 0
                and now_ns - self.last_trade_monotonic_ns["spot"] <= 2_000_000_000
                else spot_micro
            )
            futures_price = (
                self.last_trade_price["futures"]
                if self.last_trade_monotonic_ns["futures"] > 0
                and now_ns - self.last_trade_monotonic_ns["futures"] <= 2_000_000_000
                else futures_micro
            )
            spot_250 = _imbalance_from_trades(self.trades["spot"], now_ns, 250)
            spot_1s = _imbalance_from_trades(self.trades["spot"], now_ns, 1_000)
            futures_250 = _imbalance_from_trades(self.trades["futures"], now_ns, 250)
            futures_1s = _imbalance_from_trades(self.trades["futures"], now_ns, 1_000)
            spot_trade_count_250 = _trade_count(self.trades["spot"], now_ns, 250)
            futures_trade_count_250 = _trade_count(self.trades["futures"], now_ns, 250)
            if self.last_removal_monotonic_ns and now_ns - self.last_removal_monotonic_ns > 2_000_000_000:
                self.removal_strength = None
                self.removal_direction = None
            basis = (
                (futures_price / spot_price - 1.0) * 10_000
                if futures_price and spot_price and spot_price > 0
                else None
            )
            directional_values = [
                value for value in (spot_qi, spot_250, futures_qi, futures_250) if value is not None
            ]
            direction_score = sum(directional_values) / len(directional_values) if directional_values else 0.0
            if self.removal_direction == "UP" and self.removal_strength:
                direction_score += 0.25 * self.removal_strength
            elif self.removal_direction == "DOWN" and self.removal_strength:
                direction_score -= 0.25 * self.removal_strength
            direction_score = max(-1.0, min(1.0, direction_score))
            direction_bias = "UP" if direction_score >= 0.20 else "DOWN" if direction_score <= -0.20 else "NEUTRAL"
            flow_alerts: list[float] = []
            if spot_250 is not None and spot_trade_count_250 >= 3:
                flow_alerts.append(spot_250)
            if futures_250 is not None and futures_trade_count_250 >= 3:
                flow_alerts.append(futures_250)
            cross_flow_high = (
                len(flow_alerts) == 2
                and flow_alerts[0] * flow_alerts[1] > 0
                and min(abs(value) for value in flow_alerts) >= 0.75
            )
            removal_sign = 1.0 if self.removal_direction == "UP" else -1.0
            removal_flow_high = bool(
                self.removal_strength
                and self.removal_strength >= 0.80
                and any(value * removal_sign >= 0.60 for value in flow_alerts)
            )
            watch = bool(
                (self.removal_strength or 0.0) >= 0.60
                or any(abs(value) >= 0.60 for value in flow_alerts)
            )
            volatility_alert = "HIGH" if cross_flow_high or removal_flow_high else "WATCH" if watch else "NORMAL"
            result = {
                "timestamp_ns": wall_ns,
                "monotonic_ns": now_ns,
                "market_id": self.prediction_market_id,
                "spot_price": spot_price,
                "spot_microprice": spot_micro,
                "spot_queue_imbalance": spot_qi,
                "spot_taker_imbalance_250ms": spot_250,
                "spot_taker_imbalance_1s": spot_1s,
                "futures_price": futures_price,
                "futures_microprice": futures_micro,
                "futures_queue_imbalance": futures_qi,
                "futures_taker_imbalance_250ms": futures_250,
                "futures_taker_imbalance_1s": futures_1s,
                "perp_spot_basis_bps": basis,
                "prediction_up_mid": prediction_mid,
                "prediction_removal_direction": self.removal_direction,
                "prediction_removal_strength": self.removal_strength,
                "volatility_alert": volatility_alert,
                "direction_bias": direction_bias,
                "direction_score": direction_score,
            }
            return result

    def recent(self) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(item) for item in list(self.recent_liquidity)[:8]]


class MicrostructureStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.counts = {"events": 0, "snapshots": 0, "liquidity": 0, "gaps": 0}
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS microstructure_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL, stream TEXT NOT NULL, market_id INTEGER,
                    exchange_event_ms INTEGER, exchange_trade_ms INTEGER,
                    prediction_book_version_ms INTEGER,
                    received_wall_ns INTEGER NOT NULL, received_monotonic_ns INTEGER NOT NULL,
                    enqueued_monotonic_ns INTEGER NOT NULL, written_wall_ns INTEGER NOT NULL,
                    session_id TEXT NOT NULL, update_id INTEGER, first_update_id INTEGER,
                    previous_update_id INTEGER, trade_id INTEGER, price REAL, quantity REAL,
                    visible_quantity REAL, aggressor INTEGER, best_bid REAL, best_bid_qty REAL,
                    best_ask REAL, best_ask_qty REAL, bids_json TEXT, asks_json TEXT,
                    raw_json TEXT, parser_version TEXT NOT NULL,
                    prediction_orientation TEXT, feature_eligible INTEGER
                );
                CREATE INDEX IF NOT EXISTS micro_events_receive_idx
                    ON microstructure_events(received_wall_ns);
                CREATE INDEX IF NOT EXISTS micro_events_source_idx
                    ON microstructure_events(source, stream, received_wall_ns);
                CREATE TABLE IF NOT EXISTS microstructure_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp_ns INTEGER NOT NULL, monotonic_ns INTEGER NOT NULL, market_id INTEGER,
                    spot_price REAL, spot_microprice REAL, spot_queue_imbalance REAL,
                    spot_taker_imbalance_250ms REAL, spot_taker_imbalance_1s REAL,
                    futures_price REAL, futures_microprice REAL, futures_queue_imbalance REAL,
                    futures_taker_imbalance_250ms REAL, futures_taker_imbalance_1s REAL,
                    perp_spot_basis_bps REAL, prediction_up_mid REAL,
                    prediction_removal_direction TEXT, prediction_removal_strength REAL,
                    volatility_alert TEXT, direction_bias TEXT, direction_score REAL
                );
                CREATE INDEX IF NOT EXISTS micro_snapshots_time_idx
                    ON microstructure_snapshots(timestamp_ns);
                CREATE TABLE IF NOT EXISTS microstructure_liquidity_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp_ns INTEGER NOT NULL, market_id INTEGER, side TEXT NOT NULL,
                    kind TEXT NOT NULL, strength REAL NOT NULL, price REAL, detail TEXT
                );
                CREATE INDEX IF NOT EXISTS micro_liquidity_time_idx
                    ON microstructure_liquidity_events(timestamp_ns);
                CREATE TABLE IF NOT EXISTS microstructure_gap_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    first_timestamp_ns INTEGER NOT NULL,
                    last_timestamp_ns INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    dropped_count INTEGER NOT NULL,
                    detail TEXT
                );
                CREATE INDEX IF NOT EXISTS micro_gap_time_idx
                    ON microstructure_gap_events(last_timestamp_ns);
                CREATE TABLE IF NOT EXISTS microstructure_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            event_columns = {
                str(row[1]) for row in db.execute("PRAGMA table_info(microstructure_events)")
            }
            if "prediction_orientation" not in event_columns:
                db.execute(
                    "ALTER TABLE microstructure_events ADD COLUMN prediction_orientation TEXT"
                )
            if "feature_eligible" not in event_columns:
                db.execute(
                    "ALTER TABLE microstructure_events ADD COLUMN feature_eligible INTEGER"
                )
            if "prediction_book_version_ms" not in event_columns:
                db.execute(
                    "ALTER TABLE microstructure_events "
                    "ADD COLUMN prediction_book_version_ms INTEGER"
                )
            # Versions before microstructure_v1 compared the first snapshot of
            # a new market with the previous market. Remove only those known
            # cross-market derived rows; raw events remain untouched.
            migration = "remove_cross_market_liquidity_v1"
            already_migrated = db.execute(
                "SELECT 1 FROM microstructure_meta WHERE key=?", (migration,)
            ).fetchone()
            if not already_migrated:
                db.execute(
                    """DELETE FROM microstructure_liquidity_events
                       WHERE timestamp_ns IN (
                           SELECT MIN(received_wall_ns)
                           FROM microstructure_events
                           WHERE source='prediction' AND market_id IS NOT NULL
                           GROUP BY market_id
                       )"""
                )
                db.execute(
                    "INSERT INTO microstructure_meta(key,value) VALUES (?,?)",
                    (migration, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
                )
            self.counts = {
                "events": int(db.execute("SELECT COUNT(*) FROM microstructure_events").fetchone()[0]),
                "snapshots": int(db.execute("SELECT COUNT(*) FROM microstructure_snapshots").fetchone()[0]),
                "liquidity": int(db.execute("SELECT COUNT(*) FROM microstructure_liquidity_events").fetchone()[0]),
                "gaps": int(db.execute("SELECT COUNT(*) FROM microstructure_gap_events").fetchone()[0]),
            }

    @staticmethod
    def insert_event(db: sqlite3.Connection, event: dict[str, Any]) -> None:
        db.execute(
            """INSERT INTO microstructure_events(
                   source, stream, market_id, exchange_event_ms, exchange_trade_ms,
                   prediction_book_version_ms,
                   received_wall_ns, received_monotonic_ns, enqueued_monotonic_ns,
                   written_wall_ns, session_id, update_id, first_update_id,
                   previous_update_id, trade_id, price, quantity, visible_quantity,
                   aggressor, best_bid, best_bid_qty, best_ask, best_ask_qty,
                   bids_json, asks_json, raw_json, parser_version,
                   prediction_orientation, feature_eligible
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                event.get("source"), event.get("stream"), event.get("market_id"),
                event.get("exchange_event_ms"), event.get("exchange_trade_ms"),
                event.get("prediction_book_version_ms"),
                event.get("received_wall_ns"), event.get("received_monotonic_ns"),
                event.get("enqueued_monotonic_ns"), time.time_ns(), event.get("session_id"),
                event.get("update_id"), event.get("first_update_id"),
                event.get("previous_update_id"), event.get("trade_id"), event.get("price"),
                event.get("quantity"), event.get("visible_quantity"), event.get("aggressor"),
                event.get("best_bid"), event.get("best_bid_qty"), event.get("best_ask"),
                event.get("best_ask_qty"), json.dumps(event.get("bids") or [], separators=(",", ":")),
                json.dumps(event.get("asks") or [], separators=(",", ":")), event.get("raw_json"),
                event.get("parser_version") or PARSER_VERSION,
                event.get("prediction_orientation"),
                1 if event.get("feature_eligible", True) else 0,
            ),
        )

    @staticmethod
    def insert_snapshot(db: sqlite3.Connection, snapshot: dict[str, Any]) -> None:
        keys = (
            "timestamp_ns", "monotonic_ns", "market_id", "spot_price", "spot_microprice",
            "spot_queue_imbalance", "spot_taker_imbalance_250ms", "spot_taker_imbalance_1s",
            "futures_price", "futures_microprice", "futures_queue_imbalance",
            "futures_taker_imbalance_250ms", "futures_taker_imbalance_1s",
            "perp_spot_basis_bps", "prediction_up_mid", "prediction_removal_direction",
            "prediction_removal_strength", "volatility_alert", "direction_bias", "direction_score",
        )
        db.execute(
            f"INSERT INTO microstructure_snapshots({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
            tuple(snapshot.get(key) for key in keys),
        )

    @staticmethod
    def insert_liquidity(db: sqlite3.Connection, item: dict[str, Any]) -> None:
        db.execute(
            """INSERT INTO microstructure_liquidity_events(
                   timestamp_ns, market_id, side, kind, strength, price, detail
               ) VALUES (?,?,?,?,?,?,?)""",
            (
                item["timestamp_ns"], item.get("market_id"), item["side"], item["kind"],
                item["strength"], item.get("price"), item.get("detail"),
            ),
        )

    @staticmethod
    def insert_gap(db: sqlite3.Connection, item: dict[str, Any]) -> None:
        db.execute(
            """INSERT INTO microstructure_gap_events(
                   first_timestamp_ns, last_timestamp_ns, source, reason,
                   dropped_count, detail
               ) VALUES (?,?,?,?,?,?)""",
            (
                item["first_timestamp_ns"], item["last_timestamp_ns"], item["source"],
                item["reason"], item["dropped_count"], item.get("detail"),
            ),
        )

    def cleanup(self, db: sqlite3.Connection, now_ns: int) -> None:
        event_cutoff = now_ns - int(RAW_RETENTION_HOURS * 3600 * 1e9)
        snapshot_cutoff = now_ns - int(SNAPSHOT_RETENTION_HOURS * 3600 * 1e9)
        liquidity_cutoff = now_ns - int(LIQUIDITY_RETENTION_HOURS * 3600 * 1e9)
        jobs = (
            ("microstructure_events", "received_wall_ns", event_cutoff, "events", 50_000),
            ("microstructure_snapshots", "timestamp_ns", snapshot_cutoff, "snapshots", 10_000),
            ("microstructure_liquidity_events", "timestamp_ns", liquidity_cutoff, "liquidity", 10_000),
            ("microstructure_gap_events", "last_timestamp_ns", liquidity_cutoff, "gaps", 10_000),
        )
        for table, column, cutoff, count_key, limit in jobs:
            cursor = db.execute(
                f"""DELETE FROM {table} WHERE id IN (
                        SELECT id FROM {table}
                        WHERE {column} < ? ORDER BY id LIMIT ?
                    )""",
                (cutoff, limit),
            )
            deleted = max(0, int(cursor.rowcount or 0))
            self.counts[count_key] = max(0, self.counts[count_key] - deleted)


class MicrostructureObserver:
    def __init__(
        self,
        *,
        api_key: str | None,
        api_secret: str | None,
        current_market_id: Callable[[], int | None],
        prediction_reference: Callable[[], dict[str, Any] | None] | None = None,
        realtime_event_sink: Callable[[dict[str, Any]], None] | None = None,
        db_path: Path = MICRO_DB_PATH,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.current_market_id = current_market_id
        self.prediction_reference = prediction_reference or (lambda: None)
        # The sink must be non-blocking.  It receives already parsed, ordered and
        # (for Prediction) UP-oriented events before the archival writer sees
        # them, so latency-sensitive paper strategies do not wait for a REST
        # polling cycle or a SQLite commit.
        self.realtime_event_sink = realtime_event_sink
        self.store = MicrostructureStore(db_path)
        self.engine = FeatureEngine()
        self.events: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=QUEUE_MAX)
        self.stop_event = threading.Event()
        self.writer_thread: threading.Thread | None = None
        self.socket_threads: list[threading.Thread] = []
        self.spot_trade_watchdog_thread: threading.Thread | None = None
        self.apps: list[Any] = []
        self.active_apps: dict[str, Any] = {}
        self.prediction_subscription_market_id: int | None = None
        self.prediction_orientation = "UNVERIFIED"
        self.prediction_orientation_market_id: int | None = None
        self.prediction_orientation_candidate: str | None = None
        self.prediction_orientation_candidate_count = 0
        self.prediction_orientation_unverified_since_ns: int | None = None
        self.orientation_attempts = 0
        self.orientation_direct_candidates = 0
        self.orientation_inverted_candidates = 0
        self.orientation_verification_successes = 0
        self.orientation_verification_failures = 0
        self.orientation_receipt_delta_ms: float | None = None
        self.orientation_direct_error: float | None = None
        self.orientation_inverted_error: float | None = None
        self.orientation_failure_reason: str | None = "WAITING_FOR_PREDICTION_MARKET"
        self.orientation_reconnect_requests = 0
        self.last_orientation_reconnect_request_ns = 0
        self.eligible_prediction_events = 0
        self.unverified_prediction_events = 0
        self.last_eligible_prediction_at: str | None = None
        self.last_prediction_receipt_monotonic_ns = 0
        self.last_prediction_book_version_ms: int | None = None
        # Transport receipt freshness and exchange content freshness
        # are deliberately tracked separately. Repeated WSS frames
        # can be locally fresh while carrying an unchanged book version.
        self.last_prediction_frame_receipt_monotonic_ns = 0
        self.last_prediction_unique_version_ms: int | None = None
        self.last_prediction_unique_version_receipt_monotonic_ns = 0
        self.last_prediction_unique_version_at: str | None = None
        self.last_prediction_top_of_book_signature: tuple[Any, ...] | None = None
        self.last_prediction_top_of_book_change_monotonic_ns = 0
        self.last_prediction_top_of_book_change_at: str | None = None
        self.prediction_content_frames = 0
        self.prediction_unique_version_events = 0
        self.prediction_same_version_events = 0
        self.prediction_same_version_consecutive_events = 0
        self.prediction_top_of_book_change_events = 0
        self.prediction_top_of_book_unchanged_events = 0
        self.prediction_top_of_book_unchanged_consecutive_events = 0
        self.stale_prediction_events = 0
        self.market_watch_thread: threading.Thread | None = None
        self.prediction_supervisor_thread: threading.Thread | None = None
        self.prediction_rollover_reconnect = threading.Event()
        self.last_prediction_stale_reconnect_request_ns = 0
        self.prediction_stale_reconnect_requests = 0
        self.market_watch_heartbeat_ns = 0
        self.market_watch_last_iteration_at: str | None = None
        self.market_watch_failures = 0
        self.market_watch_last_error: str | None = None
        self.market_watch_restarts = 0
        self.market_watch_supervisor_failures = 0
        self.market_watch_supervisor_last_error: str | None = None
        self.last_prediction_rollover_request_ns = 0
        self.prediction_rollover_reconnect_requests = 0
        self.last_rollover_wanted_market_id: int | None = None
        self.last_rollover_subscribed_market_id: int | None = None
        self.last_rollover_reconnect_at: str | None = None
        self.last_rollover_reconnect_reason: str | None = None
        self.state_lock = threading.RLock()
        self.latest_snapshot: dict[str, Any] = {}
        self.clock_offset_ms = 0.0
        self.dropped_events = 0
        self.dropped_by_stream: dict[str, int] = {
            name: 0 for name in (
                "spot_trade", "spot_book", "futures_public", "futures_market", "prediction"
            )
        }
        self.pending_gap_lock = threading.Lock()
        self.pending_gaps: dict[tuple[str, str], dict[str, Any]] = {}
        self.out_of_order = 0
        self.sequence_gaps = 0
        self.last_prediction_timestamp: dict[int, int] = {}
        self.last_spot_update: int | None = None
        self.last_futures_update: int | None = None
        self.writer_status = "STANDBY"
        self.writer_error: str | None = None
        self.writer_lag_ms: float | None = None
        self.writer_last_activity_ns = 0
        self.realtime_sink_errors = 0
        self.realtime_sink_last_error: str | None = None
        self.spot_trade_reconnect_requests = 0
        self.last_spot_trade_reconnect_request_ns = 0
        self.stream_stats: dict[str, dict[str, Any]] = {
            name: {
                "status": "STANDBY", "lastEventAt": None, "error": None,
                "openedMonotonicNs": None,
                "times": deque(maxlen=10_000), "latencies": deque(maxlen=200),
            }
            for name in (
                "spot_trade", "spot_book", "futures_public", "futures_market", "prediction"
            )
        }

    def _sync_clock(self) -> None:
        started_ms = time.time() * 1000
        try:
            with urllib.request.urlopen("https://api.binance.com/api/v3/time", timeout=3) as response:
                server_ms = float(json.load(response)["serverTime"])
            finished_ms = time.time() * 1000
            self.clock_offset_ms = server_ms - (started_ms + finished_ms) / 2
        except Exception:
            self.clock_offset_ms = 0.0

    def start(self) -> None:
        if websocket is None:
            with self.state_lock:
                for stats in self.stream_stats.values():
                    stats.update(status="DEPENDENCY_MISSING", error="websocket-client is not installed")
            return
        self._sync_clock()
        self.writer_thread = threading.Thread(target=self._writer_loop, name="micro-writer", daemon=True)
        self.writer_thread.start()
        sockets = [
            ("spot_trade", lambda: SPOT_TRADE_URL, None),
            ("spot_book", lambda: SPOT_BOOK_URL, None),
            ("futures_public", lambda: FUTURES_PUBLIC_URL, None),
            ("futures_market", lambda: FUTURES_MARKET_URL, None),
        ]
        if self.api_key and self.api_secret:
            sockets.append(("prediction", self._prediction_url, lambda: [f"X-MBX-APIKEY: {self.api_key}"]))
        else:
            self.stream_stats["prediction"].update(
                status="CONFIG_REQUIRED", error="read-only Binance credentials are unavailable"
            )
        for name, url_builder, header_builder in sockets:
            thread = threading.Thread(
                target=self._socket_loop,
                args=(name, url_builder, header_builder),
                name=f"micro-{name}", daemon=True,
            )
            thread.start()
            self.socket_threads.append(thread)
        self.spot_trade_watchdog_thread = threading.Thread(
            target=self._spot_trade_reconnect_watchdog,
            name="micro-spot-trade-watchdog",
            daemon=True,
        )
        self.spot_trade_watchdog_thread.start()
        if self.api_key and self.api_secret:
            self.market_watch_thread = threading.Thread(
                target=self._prediction_market_loop,
                name="micro-prediction-market-watch",
                daemon=True,
            )
            self.market_watch_thread.start()
            self.prediction_supervisor_thread = threading.Thread(
                target=self._prediction_market_watch_supervisor_loop,
                name="micro-prediction-market-supervisor",
                daemon=True,
            )
            self.prediction_supervisor_thread.start()

    def _spot_trade_reconnect_watchdog(self) -> None:
        """Reconnect only the trade socket after a throttled silence window.

        The callback updates stream telemetry before putting an event on the
        archival queue, so this watchdog detects a transport stall, not a
        slow SQLite writer or a full strategy queue.
        """
        silence_ns = int(SPOT_TRADE_WATCHDOG_SILENCE_SECONDS * 1_000_000_000)
        throttle_ns = int(SPOT_TRADE_RECONNECT_THROTTLE_SECONDS * 1_000_000_000)
        last_request_ns = 0
        while not self.stop_event.wait(1.0):
            now_ns = time.monotonic_ns()
            with self.state_lock:
                stats = self.stream_stats["spot_trade"]
                app = self.active_apps.get("spot_trade")
                last_event_ns = (
                    int(stats["times"][-1])
                    if stats["times"]
                    else int(stats.get("openedMonotonicNs") or 0)
                )
            if app is None or last_event_ns <= 0:
                continue
            if now_ns - last_event_ns <= silence_ns:
                continue
            if now_ns - last_request_ns < throttle_ns:
                continue
            last_request_ns = now_ns
            with self.state_lock:
                self.stream_stats["spot_trade"].update(
                    status="RETRYING",
                    error=(
                        "trade socket watchdog reconnect after "
                        f"{(now_ns - last_event_ns) / 1_000_000:.0f}ms silence"
                    ),
                )
                self.spot_trade_reconnect_requests = (
                    getattr(self, "spot_trade_reconnect_requests", 0) + 1
                )
                self.last_spot_trade_reconnect_request_ns = now_ns
            try:
                app.close()
            except Exception:
                pass

    def _prediction_url(self) -> str:
        assert self.api_secret is not None
        market_id = self.current_market_id()
        if market_id is None:
            raise RuntimeError("current Prediction market is not available yet")
        timestamp_ms = int(time.time() * 1000 + self.clock_offset_ms)
        url, _ = build_prediction_ws_url(
            self.api_secret,
            timestamp_ms=timestamp_ms,
            random_value=uuid.uuid4().hex,
            topic=f"web3_prediction_orderbook_{int(market_id)}",
        )
        self.prediction_subscription_market_id = int(market_id)
        return url

    def _request_prediction_rollover_reconnect(
        self,
        *,
        wanted_market_id: int,
        subscribed_market_id: int | None,
        reason: str,
    ) -> bool:
        """Invalidate stale state and force the Prediction socket to rebuild."""
        wanted = int(wanted_market_id)
        subscribed = (
            int(subscribed_market_id)
            if subscribed_market_id is not None
            else None
        )
        now_ns = time.monotonic_ns()
        throttle_ns = int(
            PREDICTION_ROLLOVER_RECONNECT_THROTTLE_SECONDS
            * 1_000_000_000
        )
        with self.state_lock:
            same_pair = (
                self.last_rollover_wanted_market_id == wanted
                and self.last_rollover_subscribed_market_id == subscribed
            )
            if (
                same_pair
                and self.last_prediction_rollover_request_ns > 0
                and now_ns - self.last_prediction_rollover_request_ns
                < throttle_ns
            ):
                return False
            self.last_prediction_rollover_request_ns = now_ns
            self.prediction_rollover_reconnect_requests += 1
            self.last_rollover_wanted_market_id = wanted
            self.last_rollover_subscribed_market_id = subscribed
            self.last_rollover_reconnect_at = _utc_iso_from_ns(
                time.time_ns()
            )
            self.last_rollover_reconnect_reason = str(reason)
            app = self.active_apps.get("prediction")

        self._invalidate_prediction_state(wanted)
        self._set_stream(
            "prediction",
            status="RETRYING",
            error=(
                f"{reason}: Prediction subscription market "
                f"{subscribed if subscribed is not None else 'unknown'} "
                f"does not match wanted market {wanted}; reconnecting"
            ),
        )
        self.prediction_rollover_reconnect.set()
        if app is not None:
            try:
                app.close()
            except Exception:
                pass
        return True

    def _prediction_market_loop(self) -> None:
        """Reconnect after rollover without dying on one callback error."""
        while not self.stop_event.wait(0.25):
            now_ns = time.monotonic_ns()
            with self.state_lock:
                self.market_watch_heartbeat_ns = now_ns
                self.market_watch_last_iteration_at = _utc_iso_from_ns(
                    time.time_ns()
                )
            try:
                wanted = self.current_market_id()
                subscribed = self.prediction_subscription_market_id
                if wanted is None or subscribed is None:
                    continue
                if int(wanted) == int(subscribed):
                    self._reconnect_unverified_prediction_if_timed_out()
                    self._reconnect_stale_prediction_if_needed()
                    continue
                self._request_prediction_rollover_reconnect(
                    wanted_market_id=int(wanted),
                    subscribed_market_id=int(subscribed),
                    reason="MARKET_WATCH_MARKET_ID_MISMATCH",
                )
            except Exception as exc:
                with self.state_lock:
                    self.market_watch_failures += 1
                    self.market_watch_last_error = _safe_error(exc)
                self._set_stream(
                    "prediction",
                    error=(
                        "Prediction market-watch iteration failed: "
                        f"{_safe_error(exc)}"
                    ),
                )

    def _prediction_market_watch_supervisor_loop(self) -> None:
        """Restart or backstop the primary Prediction rollover worker."""
        interval = PREDICTION_MARKET_WATCH_SUPERVISOR_SECONDS
        heartbeat_limit_ns = int(
            PREDICTION_MARKET_WATCH_HEARTBEAT_STALE_SECONDS
            * 1_000_000_000
        )
        while not self.stop_event.wait(interval):
            try:
                worker = self.market_watch_thread
                if worker is None or not worker.is_alive():
                    if self.stop_event.is_set():
                        break
                    with self.state_lock:
                        self.market_watch_restarts += 1
                        restart_number = self.market_watch_restarts
                    replacement = threading.Thread(
                        target=self._prediction_market_loop,
                        name=(
                            "micro-prediction-market-watch-restart-"
                            f"{restart_number}"
                        ),
                        daemon=True,
                    )
                    self.market_watch_thread = replacement
                    replacement.start()
                    continue

                wanted = self.current_market_id()
                subscribed = self.prediction_subscription_market_id
                if wanted is None or subscribed is None:
                    continue

                now_ns = time.monotonic_ns()
                heartbeat_ns = int(self.market_watch_heartbeat_ns or 0)
                heartbeat_stale = bool(
                    heartbeat_ns > 0
                    and now_ns - heartbeat_ns > heartbeat_limit_ns
                )
                if int(wanted) != int(subscribed):
                    self._request_prediction_rollover_reconnect(
                        wanted_market_id=int(wanted),
                        subscribed_market_id=int(subscribed),
                        reason="SUPERVISOR_MARKET_ID_MISMATCH",
                    )
                elif heartbeat_stale:
                    self._request_prediction_rollover_reconnect(
                        wanted_market_id=int(wanted),
                        subscribed_market_id=int(subscribed),
                        reason="SUPERVISOR_HEARTBEAT_STALE",
                    )
            except Exception as exc:
                with self.state_lock:
                    self.market_watch_supervisor_failures += 1
                    self.market_watch_supervisor_last_error = _safe_error(exc)
                self._set_stream(
                    "prediction",
                    error=(
                        "Prediction market-watch supervisor failed: "
                        f"{_safe_error(exc)}"
                    ),
                )

    def _invalidate_prediction_state(self, market_id: int | None) -> None:
        self.engine.reset_prediction(market_id)
        self.prediction_orientation = "UNVERIFIED"
        self.prediction_orientation_market_id = market_id
        self.prediction_orientation_candidate = None
        self.prediction_orientation_candidate_count = 0
        self.prediction_orientation_unverified_since_ns = (
            time.monotonic_ns() if market_id is not None else None
        )
        self.orientation_receipt_delta_ms = None
        self.orientation_direct_error = None
        self.orientation_inverted_error = None
        self.orientation_failure_reason = (
            "WAITING_FOR_ORIENTATION_CONFIRMATION"
            if market_id is not None
            else "WAITING_FOR_PREDICTION_MARKET"
        )
        self.last_prediction_receipt_monotonic_ns = 0
        self.last_prediction_book_version_ms = None
        self.last_prediction_frame_receipt_monotonic_ns = 0
        self.last_prediction_unique_version_ms = None
        self.last_prediction_unique_version_receipt_monotonic_ns = 0
        self.last_prediction_unique_version_at = None
        self.last_prediction_top_of_book_signature = None
        self.last_prediction_top_of_book_change_monotonic_ns = 0
        self.last_prediction_top_of_book_change_at = None
        self.prediction_content_frames = 0
        self.prediction_unique_version_events = 0
        self.prediction_same_version_events = 0
        self.prediction_same_version_consecutive_events = 0
        self.prediction_top_of_book_change_events = 0
        self.prediction_top_of_book_unchanged_events = 0
        self.prediction_top_of_book_unchanged_consecutive_events = 0
        if market_id is not None:
            self.last_prediction_timestamp.pop(int(market_id), None)
        with self.state_lock:
            for key in (
                "prediction_up_mid",
                "prediction_removal_direction",
                "prediction_removal_strength",
            ):
                self.latest_snapshot[key] = None
            stats = self.stream_stats["prediction"]
            stats["times"].clear()
            stats["latencies"].clear()
            stats["lastEventAt"] = None

    def _reset_orientation_candidate(self, reason: str) -> None:
        self.prediction_orientation = "UNVERIFIED"
        self.prediction_orientation_candidate = None
        self.prediction_orientation_candidate_count = 0
        self.orientation_verification_failures += 1
        self.orientation_failure_reason = reason

    def _reconnect_unverified_prediction_if_timed_out(self) -> None:
        if self.prediction_orientation in PREDICTION_VERIFIED_ORIENTATIONS:
            return
        started_ns = self.prediction_orientation_unverified_since_ns
        if started_ns is None:
            return
        now_ns = time.monotonic_ns()
        if now_ns - started_ns < int(
            PREDICTION_ORIENTATION_TIMEOUT_SECONDS * 1_000_000_000
        ):
            return
        self.orientation_failure_reason = "ORIENTATION_TIMEOUT"
        if now_ns - self.last_orientation_reconnect_request_ns < int(
            PREDICTION_ORIENTATION_RECONNECT_THROTTLE_SECONDS
            * 1_000_000_000
        ):
            return
        with self.state_lock:
            app = self.active_apps.get("prediction")
        if app is None:
            return
        self.last_orientation_reconnect_request_ns = now_ns
        self.orientation_reconnect_requests += 1
        self.prediction_rollover_reconnect.set()
        try:
            app.close()
        except Exception:
            pass

    def _reconnect_stale_prediction_if_needed(self) -> None:
        """Reconnect a verified Prediction socket that silently stopped producing books."""
        if self.prediction_orientation not in PREDICTION_VERIFIED_ORIENTATIONS:
            return

        last_receipt_ns = int(self.last_prediction_receipt_monotonic_ns or 0)
        if last_receipt_ns <= 0:
            return

        now_ns = time.monotonic_ns()
        stale_age_ns = now_ns - last_receipt_ns

        if stale_age_ns < int(
            PREDICTION_STALE_RECONNECT_SECONDS * 1_000_000_000
        ):
            return

        if (
            now_ns - self.last_prediction_stale_reconnect_request_ns
            < int(
                PREDICTION_STALE_RECONNECT_THROTTLE_SECONDS
                * 1_000_000_000
            )
        ):
            return

        with self.state_lock:
            app = self.active_apps.get("prediction")

        if app is None:
            return

        self.last_prediction_stale_reconnect_request_ns = now_ns
        self.prediction_stale_reconnect_requests += 1

        self._set_stream(
            "prediction",
            status="STALE",
            error=(
                "verified Prediction stream produced no accepted "
                f"orderbook for {stale_age_ns / 1_000_000:.0f} ms; reconnecting"
            ),
        )

        self.prediction_rollover_reconnect.set()

        try:
            app.close()
        except Exception:
            pass

    def _orient_prediction_event(self, event: dict[str, Any]) -> bool:
        """Fail closed until two causal REST/WSS comparisons agree on UP mapping."""
        market_id = _int(event.get("market_id"))
        if market_id != self.prediction_orientation_market_id:
            self._invalidate_prediction_state(market_id)
        previous_orientation = self.prediction_orientation
        if self.prediction_orientation not in PREDICTION_VERIFIED_ORIENTATIONS:
            self.orientation_attempts += 1
            reference = self.prediction_reference() or {}
            reference_market_id = _int(reference.get("market_id"))
            event_received_ns = _int(event.get("received_wall_ns"))
            reference_received_ns = _int(reference.get("received_wall_ns"))
            self.orientation_receipt_delta_ms = (
                abs(event_received_ns - reference_received_ns) / 1_000_000
                if event_received_ns is not None and reference_received_ns is not None
                else None
            )
            self.orientation_direct_error = None
            self.orientation_inverted_error = None

            failure_reason: str | None = None
            if market_id is None or reference_market_id is None:
                failure_reason = "MISSING_MARKET_ID"
            elif market_id != reference_market_id:
                failure_reason = "MARKET_ID_MISMATCH"
            elif self.orientation_receipt_delta_ms is None:
                failure_reason = "MISSING_RECEIPT_TIMESTAMP"
            elif (
                self.orientation_receipt_delta_ms
                > PREDICTION_ORIENTATION_RECEIPT_WINDOW_MS
            ):
                failure_reason = "RECEIPT_WINDOW_EXCEEDED"

            bid = _float(event.get("best_bid"))
            ask = _float(event.get("best_ask"))
            up_bid = _float(reference.get("up_bid"))
            up_ask = _float(reference.get("up_ask"))
            if failure_reason is None and None in (bid, ask, up_bid, up_ask):
                failure_reason = "MISSING_TOP_OF_BOOK"

            candidate: str | None = None
            if failure_reason is None:
                assert bid is not None and ask is not None
                assert up_bid is not None and up_ask is not None
                self.orientation_direct_error = abs(bid - up_bid) + abs(ask - up_ask)
                self.orientation_inverted_error = (
                    abs((1.0 - ask) - up_bid) + abs((1.0 - bid) - up_ask)
                )
                if (
                    self.orientation_direct_error
                    <= PREDICTION_ORIENTATION_MAX_PRICE_ERROR
                    and self.orientation_direct_error
                    + PREDICTION_ORIENTATION_MIN_ERROR_MARGIN
                    < self.orientation_inverted_error
                ):
                    candidate = "DIRECT_CANDIDATE"
                    self.orientation_direct_candidates += 1
                elif (
                    self.orientation_inverted_error
                    <= PREDICTION_ORIENTATION_MAX_PRICE_ERROR
                    and self.orientation_inverted_error
                    + PREDICTION_ORIENTATION_MIN_ERROR_MARGIN
                    < self.orientation_direct_error
                ):
                    candidate = "INVERTED_CANDIDATE"
                    self.orientation_inverted_candidates += 1
                else:
                    failure_reason = "AMBIGUOUS_OR_PRICE_ERROR"

            if candidate is None:
                self._reset_orientation_candidate(failure_reason or "UNVERIFIED")
            else:
                if self.prediction_orientation_candidate == candidate:
                    self.prediction_orientation_candidate_count += 1
                else:
                    self.prediction_orientation_candidate = candidate
                    self.prediction_orientation_candidate_count = 1
                self.prediction_orientation = candidate
                self.orientation_failure_reason = "AWAITING_SECOND_CONFIRMATION"
                if (
                    self.prediction_orientation_candidate_count
                    >= PREDICTION_ORIENTATION_CONFIRMATIONS
                ):
                    self.prediction_orientation = (
                        "DIRECT_UP_VERIFIED"
                        if candidate == "DIRECT_CANDIDATE"
                        else "INVERTED_TO_UP_VERIFIED"
                    )
                    self.orientation_verification_successes += 1
                    self.orientation_failure_reason = None

        if self.prediction_orientation != "INVERTED_TO_UP_VERIFIED":
            event["prediction_orientation"] = self.prediction_orientation
            event["feature_eligible"] = (
                self.prediction_orientation in PREDICTION_VERIFIED_ORIENTATIONS
            )
            return (
                previous_orientation not in PREDICTION_VERIFIED_ORIENTATIONS
                and self.prediction_orientation in PREDICTION_VERIFIED_ORIENTATIONS
            )
        inverted_bids = normalize_levels(
            [[1.0 - price, size] for price, size in event.get("asks") or []],
            reverse=True,
        )
        inverted_asks = normalize_levels(
            [[1.0 - price, size] for price, size in event.get("bids") or []],
            reverse=False,
        )
        event["bids"] = inverted_bids
        event["asks"] = inverted_asks
        event["best_bid"] = inverted_bids[0][0] if inverted_bids else None
        event["best_bid_qty"] = inverted_bids[0][1] if inverted_bids else None
        event["best_ask"] = inverted_asks[0][0] if inverted_asks else None
        event["best_ask_qty"] = inverted_asks[0][1] if inverted_asks else None
        event["prediction_orientation"] = self.prediction_orientation
        event["feature_eligible"] = True
        return previous_orientation not in PREDICTION_VERIFIED_ORIENTATIONS

    def _prediction_version_age_ms(self, event: dict[str, Any]) -> float | None:
        version_ms = _int(event.get("prediction_book_version_ms"))
        received_wall_ns = _int(event.get("received_wall_ns"))
        if version_ms is None or received_wall_ns is None:
            return None
        return max(
            0.0,
            received_wall_ns / 1_000_000 + self.clock_offset_ms - version_ms,
        )

    def _observe_prediction_content(
        self,
        event: dict[str, Any],
        version_ms: int | None,
    ) -> None:
        # Telemetry only: this method must never make a book eligible.
        received_mono_ns = int(event.get("received_monotonic_ns") or 0)
        received_wall_ns = int(event.get("received_wall_ns") or 0)
        if received_mono_ns <= 0:
            return
        normalized_version = (
            int(version_ms) if version_ms is not None else None
        )
        top_signature = (
            event.get("best_bid"),
            event.get("best_bid_qty"),
            event.get("best_ask"),
            event.get("best_ask_qty"),
        )
        with self.state_lock:
            self.last_prediction_frame_receipt_monotonic_ns = received_mono_ns
            self.prediction_content_frames += 1

            if normalized_version is not None:
                if normalized_version != self.last_prediction_unique_version_ms:
                    self.last_prediction_unique_version_ms = normalized_version
                    self.last_prediction_unique_version_receipt_monotonic_ns = (
                        received_mono_ns
                    )
                    self.last_prediction_unique_version_at = (
                        _utc_iso_from_ns(received_wall_ns)
                        if received_wall_ns > 0
                        else None
                    )
                    self.prediction_unique_version_events += 1
                    self.prediction_same_version_consecutive_events = 0
                else:
                    self.prediction_same_version_events += 1
                    self.prediction_same_version_consecutive_events += 1

            if top_signature != self.last_prediction_top_of_book_signature:
                self.last_prediction_top_of_book_signature = top_signature
                self.last_prediction_top_of_book_change_monotonic_ns = (
                    received_mono_ns
                )
                self.last_prediction_top_of_book_change_at = (
                    _utc_iso_from_ns(received_wall_ns)
                    if received_wall_ns > 0
                    else None
                )
                self.prediction_top_of_book_change_events += 1
                self.prediction_top_of_book_unchanged_consecutive_events = 0
            else:
                self.prediction_top_of_book_unchanged_events += 1
                self.prediction_top_of_book_unchanged_consecutive_events += 1
    def _set_stream(self, name: str, **values: Any) -> None:
        with self.state_lock:
            self.stream_stats[name].update(values)

    def _socket_loop(
        self,
        name: str,
        url_builder: Callable[[], str],
        header_builder: Callable[[], list[str]] | None,
    ) -> None:
        backoff = 1.0
        while not self.stop_event.is_set():
            if name == "prediction" and self.current_market_id() is None:
                self._set_stream(name, status="WAITING_MARKET", error=None)
                self.stop_event.wait(0.25)
                continue
            session_id = uuid.uuid4().hex
            self._set_stream(name, status="CONNECTING", error=None)

            def on_open(_: Any) -> None:
                nonlocal backoff
                backoff = 1.0
                if name == "spot_book":
                    self.last_spot_update = None
                elif name == "futures_public":
                    self.last_futures_update = None
                elif name == "prediction":
                    self._invalidate_prediction_state(self.prediction_subscription_market_id)
                self._set_stream(
                    name,
                    status="SYNCING" if name == "prediction" else "LIVE",
                    error=None,
                    openedMonotonicNs=time.monotonic_ns(),
                )

            def on_message(_: Any, raw: str) -> None:
                wall_ns = time.time_ns()
                monotonic_ns = time.monotonic_ns()
                try:
                    if name == "prediction":
                        event = parse_prediction_message(
                            raw,
                            received_wall_ns=wall_ns,
                            received_monotonic_ns=monotonic_ns,
                            session_id=session_id,
                        )
                    else:
                        source = "spot" if name in {"spot_trade", "spot_book"} else "futures"
                        event = parse_combined_message(
                            raw,
                            source=source,
                            received_wall_ns=wall_ns,
                            received_monotonic_ns=monotonic_ns,
                            session_id=session_id,
                        )
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    self._set_stream(name, error=f"parse error: {_safe_error(exc)}")
                    return
                if event is not None:
                    self._accept_event(name, event)

            def on_error(_: Any, error: Any) -> None:
                self._set_stream(name, status="ERROR", error=_safe_error(error))

            def on_close(_: Any, __: Any, reason: Any) -> None:
                if name == "prediction":
                    self._invalidate_prediction_state(self.prediction_subscription_market_id)
                if not self.stop_event.is_set():
                    self._set_stream(name, status="RETRYING", error=_safe_error(reason) if reason else None)

            app: Any | None = None
            try:
                app = websocket.WebSocketApp(
                    url_builder(),
                    header=header_builder() if header_builder else None,
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                )
                self.apps.append(app)
                with self.state_lock:
                    self.active_apps[name] = app
                # 15 seconds keeps Prediction's first client ping inside its 60s limit.
                app.run_forever(ping_interval=15, ping_timeout=10)
            except Exception as exc:
                self._set_stream(name, status="ERROR", error=_safe_error(exc))
            finally:
                with self.state_lock:
                    if self.active_apps.get(name) is app:
                        self.active_apps.pop(name, None)
            if name == "prediction":
                # A known market-id rollover should reconnect immediately.  Use
                # the rollover Event for the wait as well, otherwise a market
                # change during a 1-30 second transport backoff remains late.
                intentional = self.prediction_rollover_reconnect.wait(backoff)
                self.prediction_rollover_reconnect.clear()
                if self.stop_event.is_set():
                    break
                if intentional:
                    continue
            elif self.stop_event.wait(backoff):
                break
            backoff = min(30.0, backoff * 2.0)

    def _accept_event(self, stream_name: str, event: dict[str, Any]) -> None:
        # Unit-test and replay callers from the pre-split API may still label
        # Spot trade ingress as "spot".  Normalize that internal alias while
        # keeping the public health contract split into two streams.
        if stream_name == "spot":
            stream_name = "spot_trade"
        if event["source"] == "prediction":
            market_id = event.get("market_id")
            wanted = self.current_market_id()
            if market_id is None or wanted is None or int(market_id) != int(wanted):
                return
            version_age_ms = self._prediction_version_age_ms(event)
            event["prediction_book_version_age_ms"] = version_age_ms
            if (
                version_age_ms is None
                or version_age_ms > PREDICTION_MAX_VERSION_AGE_MS
            ):
                just_verified = False
                event["prediction_orientation"] = "STALE_CONTENT"
                event["feature_eligible"] = False
                self.stale_prediction_events += 1
                self.orientation_failure_reason = "STALE_BOOK_VERSION"
            else:
                just_verified = self._orient_prediction_event(event)
            timestamp = event.get("prediction_book_version_ms")
            if timestamp is None:
                timestamp = event.get("exchange_event_ms")
            self._observe_prediction_content(
                event,
                int(timestamp) if timestamp is not None else None,
            )
            previous = self.last_prediction_timestamp.get(int(market_id))
            if (
                timestamp is not None
                and previous is not None
                and (
                    int(timestamp) < previous
                    or (int(timestamp) == previous and not just_verified)
                )
            ):
                self.out_of_order += 1
                return
            if timestamp is not None:
                self.last_prediction_timestamp[int(market_id)] = int(timestamp)
                self.last_prediction_book_version_ms = int(timestamp)
            self.last_prediction_receipt_monotonic_ns = int(
                event.get("received_monotonic_ns") or 0
            )
            if event.get("feature_eligible") is True:
                self.eligible_prediction_events += 1
                self.last_eligible_prediction_at = _utc_iso_from_ns(
                    int(event.get("received_wall_ns") or time.time_ns())
                )
            else:
                self.unverified_prediction_events += 1
        elif stream_name == "spot_book" and event["stream"] == "depth":
            update_id = event.get("update_id")
            if (
                update_id is not None
                and self.last_spot_update is not None
                and int(update_id) <= int(self.last_spot_update)
            ):
                self.out_of_order += 1
                return
            if update_id is not None:
                self.last_spot_update = int(update_id)
        elif stream_name == "futures_public" and event["stream"] == "depth":
            previous_update = event.get("previous_update_id")
            update_id = event.get("update_id")
            if (
                update_id is not None
                and self.last_futures_update is not None
                and int(update_id) <= int(self.last_futures_update)
            ):
                self.out_of_order += 1
                return
            if (
                previous_update is not None
                and self.last_futures_update is not None
                and int(previous_update) != int(self.last_futures_update)
            ):
                # This feed is a complete top-10 partial snapshot, so a gap is
                # recorded but the current snapshot is still a valid baseline.
                self.sequence_gaps += 1
            if update_id is not None:
                self.last_futures_update = int(update_id)

        stats = self.stream_stats[stream_name]
        now_mono = int(event["received_monotonic_ns"])
        now_wall_ms = int(event["received_wall_ns"]) / 1e6
        with self.state_lock:
            stats["times"].append(now_mono)
            stats["lastEventAt"] = time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.gmtime(event["received_wall_ns"] / 1e9)
            ) + "Z"
            event_ms = event.get("exchange_event_ms")
            # Spot/Futures E/T is an event time and can estimate transport.
            # Prediction updateTimestampMs is only an orderbook version time.
            if event_ms is not None and event.get("source") != "prediction":
                latency = (
                    now_wall_ms
                    + self.clock_offset_ms
                    - float(event_ms)
                )
                if -1_000 <= latency <= 60_000:
                    stats["latencies"].append(latency)
            stats["status"] = "LIVE"
            stats["error"] = None
        if self.realtime_event_sink is not None:
            try:
                self.realtime_event_sink(event)
            except Exception as exc:
                # A paper-strategy consumer must never take a market-data
                # socket down.  The observer exposes the failure for the UI.
                self.realtime_sink_errors += 1
                self.realtime_sink_last_error = _safe_error(exc)
        try:
            self.events.put_nowait(event)
        except queue.Full:
            self.dropped_events += 1
            self.dropped_by_stream[stream_name] = self.dropped_by_stream.get(stream_name, 0) + 1
            self._record_gap(stream_name, "QUEUE_OVERFLOW")

    def _record_gap(
        self,
        source: str,
        reason: str,
        *,
        count: int = 1,
        first_timestamp_ns: int | None = None,
        last_timestamp_ns: int | None = None,
    ) -> None:
        now_ns = time.time_ns()
        first_ns = first_timestamp_ns or now_ns
        last_ns = last_timestamp_ns or now_ns
        key = (source, reason)
        with self.pending_gap_lock:
            existing = self.pending_gaps.get(key)
            if existing is None:
                self.pending_gaps[key] = {
                    "first_timestamp_ns": first_ns,
                    "last_timestamp_ns": last_ns,
                    "source": source,
                    "reason": reason,
                    "dropped_count": count,
                    "detail": "observer detected a data gap; derived windows crossing it are incomplete",
                }
            else:
                existing["first_timestamp_ns"] = min(existing["first_timestamp_ns"], first_ns)
                existing["last_timestamp_ns"] = max(existing["last_timestamp_ns"], last_ns)
                existing["dropped_count"] += count

    def _take_pending_gaps(self) -> list[dict[str, Any]]:
        with self.pending_gap_lock:
            result = list(self.pending_gaps.values())
            self.pending_gaps = {}
        return result

    def _has_pending_gaps(self) -> bool:
        with self.pending_gap_lock:
            return bool(self.pending_gaps)

    def _writer_loop(self) -> None:
        self.writer_status = "STARTING"
        while not self.stop_event.is_set() or not self.events.empty() or self._has_pending_gaps():
            db: sqlite3.Connection | None = None
            failed_event: dict[str, Any] | None = None
            failed_gaps: list[dict[str, Any]] = []
            try:
                db = self.store._connect()
                self.writer_status = "RUNNING"
                self.writer_error = None
                pending = 0
                last_commit = time.monotonic()
                last_cleanup = time.monotonic()
                last_snapshot_mono_ns = 0
                while not self.stop_event.is_set() or not self.events.empty() or self._has_pending_gaps():
                    try:
                        event = self.events.get(timeout=0.10)
                    except queue.Empty:
                        event = None
                    failed_event = event
                    if event is not None:
                        now_mono_ns = time.monotonic_ns()
                        self.writer_lag_ms = max(
                            0.0,
                            (now_mono_ns - int(event["received_monotonic_ns"])) / 1_000_000,
                        )
                        self.store.insert_event(db, event)
                        self.store.counts["events"] += 1
                        pending += 1
                        for item in self.engine.update(event):
                            self.store.insert_liquidity(db, item)
                            self.store.counts["liquidity"] += 1
                            pending += 1
                        failed_event = None
                    failed_gaps = self._take_pending_gaps()
                    for item in failed_gaps:
                        self.store.insert_gap(db, item)
                        self.store.counts["gaps"] += 1
                        pending += 1
                    failed_gaps = []
                    now_mono_ns = time.monotonic_ns()
                    if now_mono_ns - last_snapshot_mono_ns >= SNAPSHOT_INTERVAL_MS * 1_000_000:
                        snapshot = self.engine.snapshot(now_mono_ns, time.time_ns())
                        self.store.insert_snapshot(db, snapshot)
                        self.store.counts["snapshots"] += 1
                        with self.state_lock:
                            self.latest_snapshot = snapshot
                        last_snapshot_mono_ns = now_mono_ns
                        pending += 1
                    now = time.monotonic()
                    if pending >= 100 or now - last_commit >= 0.25:
                        db.commit()
                        self.writer_last_activity_ns = time.monotonic_ns()
                        pending = 0
                        last_commit = now
                    if now - last_cleanup >= 60:
                        self.store.cleanup(db, time.time_ns())
                        db.commit()
                        self.writer_last_activity_ns = time.monotonic_ns()
                        last_cleanup = now
                db.commit()
                self.writer_last_activity_ns = time.monotonic_ns()
            except Exception as exc:
                self.writer_status = "ERROR"
                self.writer_error = _safe_error(exc)
                if failed_event is not None:
                    self.dropped_events += 1
                    stream_name = str(failed_event.get("source") or "writer")
                    self.dropped_by_stream[stream_name] = self.dropped_by_stream.get(stream_name, 0) + 1
                    self._record_gap(stream_name, "WRITER_ERROR")
                for item in failed_gaps:
                    self._record_gap(
                        str(item["source"]), str(item["reason"]),
                        count=int(item["dropped_count"]),
                        first_timestamp_ns=int(item["first_timestamp_ns"]),
                        last_timestamp_ns=int(item["last_timestamp_ns"]),
                    )
                if self.stop_event.is_set():
                    break
                self.stop_event.wait(1.0)
            finally:
                if db is not None:
                    try:
                        db.close()
                    except Exception:
                        pass
        if self.writer_status != "ERROR":
            self.writer_status = "STOPPED"

    def _public_stream_state(self, names: list[str]) -> dict[str, Any]:
        with self.state_lock:
            entries = [self.stream_stats[name] for name in names]
            now_ns = time.monotonic_ns()
            statuses: list[str] = []
            stale_names: list[str] = []
            for name, item in zip(names, entries):
                item_status = str(item["status"])
                if item_status in ("LIVE", "SYNCING"):
                    last_ns = item["times"][-1] if item["times"] else item.get("openedMonotonicNs")
                    stale_after_ns = 10_000_000_000 if name == "prediction" else 5_000_000_000
                    if last_ns is not None and now_ns - int(last_ns) > stale_after_ns:
                        item_status = "STALE"
                        stale_names.append(name)
                statuses.append(item_status)
            if all(status == "LIVE" for status in statuses):
                status = "LIVE"
            elif any(status == "LIVE" for status in statuses):
                status = "PARTIAL"
            elif any(status == "STALE" for status in statuses):
                status = "STALE"
            elif any(status == "ERROR" for status in statuses):
                status = "ERROR"
            else:
                status = statuses[0] if statuses else "STANDBY"
            times = [timestamp for item in entries for timestamp in item["times"] if timestamp >= now_ns - 5_000_000_000]
            latencies = [value for item in entries for value in item["latencies"]]
            last_events = [item["lastEventAt"] for item in entries if item.get("lastEventAt")]
            errors = [item["error"] for item in entries if item.get("error")]
            if stale_names:
                errors.append("no accepted events: " + ", ".join(stale_names))
            return {
                "status": status,
                "eventRate": len(times) / 5.0,
                "lastEventAt": max(last_events) if last_events else None,
                "transportLatencyMs": sorted(latencies)[len(latencies) // 2] if latencies else None,
                "error": "; ".join(str(value) for value in errors)[:200] if errors else None,
            }

    def state(self) -> dict[str, Any]:
        spot_trade = self._public_stream_state(["spot_trade"])
        spot_book = self._public_stream_state(["spot_book"])
        futures = self._public_stream_state(["futures_public", "futures_market"])
        prediction = self._public_stream_state(["prediction"])
        prediction["marketId"] = self.prediction_subscription_market_id
        prediction["bookMapping"] = self.prediction_orientation
        now_mono_ns = time.monotonic_ns()
        now_server_ms = time.time() * 1_000 + self.clock_offset_ms
        unverified_since_ns = self.prediction_orientation_unverified_since_ns
        orientation_elapsed_ms = (
            max(0.0, (now_mono_ns - unverified_since_ns) / 1_000_000)
            if unverified_since_ns is not None
            and self.prediction_orientation not in PREDICTION_VERIFIED_ORIENTATIONS
            else None
        )
        orientation_timed_out = bool(
            orientation_elapsed_ms is not None
            and orientation_elapsed_ms
            > PREDICTION_ORIENTATION_TIMEOUT_SECONDS * 1_000
        )
        book_version_age_ms = (
            max(0.0, now_server_ms - self.last_prediction_book_version_ms)
            if self.last_prediction_book_version_ms is not None
            else None
        )
        transport_receipt_age_ms = (
            max(
                0.0,
                (
                    now_mono_ns
                    - self.last_prediction_frame_receipt_monotonic_ns
                )
                / 1_000_000,
            )
            if self.last_prediction_frame_receipt_monotonic_ns
            else None
        )
        unique_version_receipt_age_ms = (
            max(
                0.0,
                (
                    now_mono_ns
                    - self.last_prediction_unique_version_receipt_monotonic_ns
                )
                / 1_000_000,
            )
            if self.last_prediction_unique_version_receipt_monotonic_ns
            else None
        )
        top_of_book_unchanged_age_ms = (
            max(
                0.0,
                (
                    now_mono_ns
                    - self.last_prediction_top_of_book_change_monotonic_ns
                )
                / 1_000_000,
            )
            if self.last_prediction_top_of_book_change_monotonic_ns
            else None
        )
        same_version_receipt_ratio = (
            self.prediction_same_version_events
            / self.prediction_content_frames
            if self.prediction_content_frames
            else None
        )
        if transport_receipt_age_ms is None:
            content_freshness_classification = "NO_CURRENT_MARKET_FRAME"
        elif (
            transport_receipt_age_ms
            > PREDICTION_CONTENT_WARNING_AGE_MS
        ):
            content_freshness_classification = "TRANSPORT_STALE"
        elif book_version_age_ms is None:
            content_freshness_classification = "CONTENT_VERSION_UNAVAILABLE"
        elif (
            book_version_age_ms
            > PREDICTION_CONTENT_WARNING_AGE_MS
        ):
            content_freshness_classification = (
                "CONTENT_VERSION_OLD_TRANSPORT_LIVE"
            )
        else:
            content_freshness_classification = "CONTENT_CURRENT"
        version_healthy = bool(
            book_version_age_ms is not None
            and book_version_age_ms <= PREDICTION_MAX_VERSION_AGE_MS
        )
        # Unit tests and offline state inspection may construct an observer
        # with credentials without calling start(). Thread health becomes
        # required only after either runtime thread has actually been assigned.
        market_watch_required = bool(
            self.api_key
            and self.api_secret
            and (
                self.market_watch_thread is not None
                or self.prediction_supervisor_thread is not None
            )
        )
        market_watch_alive = bool(
            self.market_watch_thread is not None
            and self.market_watch_thread.is_alive()
        )
        prediction_supervisor_alive = bool(
            self.prediction_supervisor_thread is not None
            and self.prediction_supervisor_thread.is_alive()
        )
        market_watch_heartbeat_age_ms = (
            max(
                0.0,
                (now_mono_ns - self.market_watch_heartbeat_ns)
                / 1_000_000,
            )
            if self.market_watch_heartbeat_ns
            else None
        )
        market_watch_heartbeat_healthy = bool(
            not market_watch_required
            or (
                market_watch_alive
                and market_watch_heartbeat_age_ms is not None
                and market_watch_heartbeat_age_ms
                <= PREDICTION_MARKET_WATCH_HEARTBEAT_STALE_SECONDS
                * 1_000
            )
        )
        try:
            wanted_prediction_market_id = self.current_market_id()
            wanted_prediction_market_id = (
                int(wanted_prediction_market_id)
                if wanted_prediction_market_id is not None
                else None
            )
        except Exception:
            wanted_prediction_market_id = None
        prediction_market_mismatch = bool(
            wanted_prediction_market_id is not None
            and self.prediction_subscription_market_id is not None
            and int(wanted_prediction_market_id)
            != int(self.prediction_subscription_market_id)
        )
        market_watch_healthy = bool(
            not market_watch_required
            or (
                market_watch_alive
                and prediction_supervisor_alive
                and market_watch_heartbeat_healthy
                and not prediction_market_mismatch
            )
        )
        orientation_healthy = bool(
            self.prediction_orientation in PREDICTION_VERIFIED_ORIENTATIONS
            and version_healthy
            and market_watch_healthy
        )
        if prediction_market_mismatch:
            orientation_health_reason = "PREDICTION_MARKET_ID_MISMATCH"
        elif market_watch_required and not market_watch_alive:
            orientation_health_reason = "MARKET_WATCH_THREAD_DEAD"
        elif market_watch_required and not prediction_supervisor_alive:
            orientation_health_reason = "MARKET_WATCH_SUPERVISOR_DEAD"
        elif not market_watch_heartbeat_healthy:
            orientation_health_reason = "MARKET_WATCH_HEARTBEAT_STALE"
        elif not version_healthy and book_version_age_ms is not None:
            orientation_health_reason = "STALE_BOOK_VERSION"
        else:
            orientation_health_reason = self.orientation_failure_reason
        prediction.update(
            {
                "bookVersionAgeMs": book_version_age_ms,
                "contentVersionAgeMs": book_version_age_ms,
                "transportReceiptAgeMs": transport_receipt_age_ms,
                "uniqueVersionReceiptAgeMs": (
                    unique_version_receipt_age_ms
                ),
                "topOfBookUnchangedAgeMs": (
                    top_of_book_unchanged_age_ms
                ),
                "contentWarningAgeMs": (
                    PREDICTION_CONTENT_WARNING_AGE_MS
                ),
                "contentFreshnessClassification": (
                    content_freshness_classification
                ),
                "contentFreshnessHealthy": (
                    content_freshness_classification
                    == "CONTENT_CURRENT"
                ),
                "lastUniqueBookVersionMs": (
                    self.last_prediction_unique_version_ms
                ),
                "lastUniqueBookVersionAt": (
                    self.last_prediction_unique_version_at
                ),
                "lastTopOfBookChangeAt": (
                    self.last_prediction_top_of_book_change_at
                ),
                "contentFrames": self.prediction_content_frames,
                "uniqueVersionEvents": (
                    self.prediction_unique_version_events
                ),
                "sameVersionEvents": (
                    self.prediction_same_version_events
                ),
                "sameVersionConsecutiveEvents": (
                    self.prediction_same_version_consecutive_events
                ),
                "sameVersionReceiptRatio": (
                    same_version_receipt_ratio
                ),
                "topOfBookChangeEvents": (
                    self.prediction_top_of_book_change_events
                ),
                "topOfBookUnchangedEvents": (
                    self.prediction_top_of_book_unchanged_events
                ),
                "topOfBookUnchangedConsecutiveEvents": (
                    self.prediction_top_of_book_unchanged_consecutive_events
                ),
                "maxBookVersionAgeMs": PREDICTION_MAX_VERSION_AGE_MS,
                "staleReconnectRequests": self.prediction_stale_reconnect_requests,
                "rolloverReconnectRequests": (
                    self.prediction_rollover_reconnect_requests
                ),
                "marketWatchThreadAlive": market_watch_alive,
                "predictionSupervisorThreadAlive": (
                    prediction_supervisor_alive
                ),
                "marketWatchHealthy": market_watch_healthy,
                "marketWatchHeartbeatAgeMs": (
                    market_watch_heartbeat_age_ms
                ),
                "marketWatchHeartbeatLimitMs": (
                    PREDICTION_MARKET_WATCH_HEARTBEAT_STALE_SECONDS
                    * 1_000
                ),
                "marketWatchLastIterationAt": (
                    self.market_watch_last_iteration_at
                ),
                "marketWatchFailures": self.market_watch_failures,
                "marketWatchLastError": self.market_watch_last_error,
                "marketWatchRestarts": self.market_watch_restarts,
                "marketWatchSupervisorFailures": (
                    self.market_watch_supervisor_failures
                ),
                "marketWatchSupervisorLastError": (
                    self.market_watch_supervisor_last_error
                ),
                "wantedMarketId": wanted_prediction_market_id,
                "subscriptionMarketId": (
                    self.prediction_subscription_market_id
                ),
                "marketIdMismatch": prediction_market_mismatch,
                "lastRolloverWantedMarketId": (
                    self.last_rollover_wanted_market_id
                ),
                "lastRolloverSubscribedMarketId": (
                    self.last_rollover_subscribed_market_id
                ),
                "lastRolloverReconnectAt": (
                    self.last_rollover_reconnect_at
                ),
                "lastRolloverReconnectReason": (
                    self.last_rollover_reconnect_reason
                ),
                "bookVersionHealthy": version_healthy,
                "localReceiptAgeMs": (
                    max(
                        0.0,
                        (now_mono_ns - self.last_prediction_receipt_monotonic_ns)
                        / 1_000_000,
                    )
                    if self.last_prediction_receipt_monotonic_ns
                    else None
                ),
                "orientationHealthy": orientation_healthy,
                "orientationStatus": (
                    "HEALTHY"
                    if orientation_healthy
                    else "DEGRADED"
                    if orientation_timed_out
                    or (
                        book_version_age_ms is not None
                        and not version_healthy
                    )
                    else "PENDING"
                ),
                "orientationTimedOut": orientation_timed_out,
                "orientationUnverifiedAgeMs": orientation_elapsed_ms,
                "orientationAttempts": self.orientation_attempts,
                "orientationDirectCandidates": self.orientation_direct_candidates,
                "orientationInvertedCandidates": self.orientation_inverted_candidates,
                "orientationCandidateCount": self.prediction_orientation_candidate_count,
                "orientationVerificationSuccesses": (
                    self.orientation_verification_successes
                ),
                "orientationVerificationFailures": (
                    self.orientation_verification_failures
                ),
                "orientationReceiptDeltaMs": self.orientation_receipt_delta_ms,
                "orientationDirectError": self.orientation_direct_error,
                "orientationInvertedError": self.orientation_inverted_error,
                "orientationFailureReason": orientation_health_reason,
                "orientationReconnectRequests": self.orientation_reconnect_requests,
                "eligiblePredictionEvents": self.eligible_prediction_events,
                "unverifiedPredictionEvents": self.unverified_prediction_events,
                "stalePredictionEvents": self.stale_prediction_events,
                "lastEligiblePredictionAt": self.last_eligible_prediction_at,
            }
        )
        groups = (spot_trade, spot_book, futures, prediction)
        group_statuses = [str(item["status"]) for item in groups]
        if all(item == "LIVE" for item in group_statuses):
            status = "LIVE"
        elif any(item == "LIVE" for item in group_statuses):
            status = "PARTIAL"
        elif any(item == "ERROR" for item in group_statuses):
            status = "ERROR"
        elif any(item == "STALE" for item in group_statuses):
            status = "STALE"
        else:
            status = "STARTING"
        if status == "LIVE" and prediction.get("orientationHealthy") is not True:
            status = "DEGRADED"
        if self.writer_status == "ERROR":
            status = "ERROR"
        with self.state_lock:
            snapshot = dict(self.latest_snapshot)
        metrics = {
            "spotPrice": snapshot.get("spot_price"),
            "spotMicroprice": snapshot.get("spot_microprice"),
            "spotQueueImbalance": snapshot.get("spot_queue_imbalance"),
            "spotTakerImbalance250ms": snapshot.get("spot_taker_imbalance_250ms"),
            "spotTakerImbalance1s": snapshot.get("spot_taker_imbalance_1s"),
            "futuresPrice": snapshot.get("futures_price"),
            "futuresMicroprice": snapshot.get("futures_microprice"),
            "futuresQueueImbalance": snapshot.get("futures_queue_imbalance"),
            "futuresTakerImbalance250ms": snapshot.get("futures_taker_imbalance_250ms"),
            "futuresTakerImbalance1s": snapshot.get("futures_taker_imbalance_1s"),
            "perpSpotBasisBps": snapshot.get("perp_spot_basis_bps"),
            "predictionUpMid": snapshot.get("prediction_up_mid"),
            "predictionRemovalDirection": snapshot.get("prediction_removal_direction"),
            "predictionRemovalStrength": snapshot.get("prediction_removal_strength"),
            "volatilityAlert": snapshot.get("volatility_alert"),
            "directionBias": snapshot.get("direction_bias"),
        }
        try:
            db_bytes = self.store.path.stat().st_size
            wal = self.store.path.with_name(self.store.path.name + "-wal")
            if wal.exists():
                db_bytes += wal.stat().st_size
        except OSError:
            db_bytes = 0
        return {
            "status": status,
            "streams": {
                "spot_trade": {
                    **spot_trade,
                    "reconnectRequests": self.spot_trade_reconnect_requests,
                    "watchdogSilenceSeconds": SPOT_TRADE_WATCHDOG_SILENCE_SECONDS,
                    "reconnectThrottleSeconds": SPOT_TRADE_RECONNECT_THROTTLE_SECONDS,
                },
                "spot_book": spot_book,
                "futures": futures,
                "prediction": prediction,
            },
            "metrics": metrics,
            "recentLiquidityEvents": self.engine.recent(),
            "storage": {
                "dbBytes": db_bytes,
                "eventsRows": self.store.counts["events"],
                "snapshotsRows": self.store.counts["snapshots"],
                "liquidityRows": self.store.counts["liquidity"],
                "gapRows": self.store.counts["gaps"],
                "rawRetentionHours": RAW_RETENTION_HOURS,
                "snapshotRetentionHours": SNAPSHOT_RETENTION_HOURS,
                "liquidityRetentionHours": LIQUIDITY_RETENTION_HOURS,
                "droppedEvents": self.dropped_events,
                "droppedByStream": dict(self.dropped_by_stream),
                "outOfOrderEvents": self.out_of_order,
                "sequenceGaps": self.sequence_gaps,
                "queueDepth": self.events.qsize(),
                "writerStatus": self.writer_status,
                "writerError": self.writer_error,
                "writerLagMs": self.writer_lag_ms,
                "writerHeartbeatAgeMs": (
                    max(0.0, (time.monotonic_ns() - self.writer_last_activity_ns) / 1_000_000)
                    if self.writer_last_activity_ns
                    else None
                ),
                "realtimeSinkErrors": self.realtime_sink_errors,
                "realtimeSinkLastError": self.realtime_sink_last_error,
            },
        }

    def stop(self) -> None:
        self.stop_event.set()
        self.prediction_rollover_reconnect.set()
        for app in list(self.apps):
            try:
                app.close()
            except Exception:
                pass
        for thread in self.socket_threads:
            thread.join(timeout=2)
        if self.spot_trade_watchdog_thread:
            self.spot_trade_watchdog_thread.join(timeout=2)
        if self.prediction_supervisor_thread:
            self.prediction_supervisor_thread.join(timeout=2)
        if self.market_watch_thread:
            self.market_watch_thread.join(timeout=2)
        if self.writer_thread:
            self.writer_thread.join(timeout=5)
