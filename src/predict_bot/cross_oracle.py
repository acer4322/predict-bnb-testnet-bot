from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    import websocket
except ImportError:  # pragma: no cover - exposed through /state.
    websocket = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(
    os.environ.get("PREDICT_CROSS_ORACLE_DB", ROOT / "data" / "cross_oracle.db")
)
HOST = os.environ.get("PREDICT_CROSS_ORACLE_HOST", "127.0.0.1")
PORT = int(os.environ.get("PREDICT_CROSS_ORACLE_PORT", "8767"))
MARKET_REFRESH_SECONDS = max(
    1.0, float(os.environ.get("PREDICT_CROSS_ORACLE_MARKET_REFRESH_SECONDS", "3.0"))
)
CHAINLINK_WS_URL = "wss://ws-live-data.polymarket.com"
POLYMARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_MARKET_BY_SLUG = "https://gamma-api.polymarket.com/markets/slug/{slug}"
CLOB_BOOK_URL = "https://clob.polymarket.com/book?token_id={token_id}"
CHAINLINK_SYMBOL = "btc/usd"
SCHEMA_VERSION = "cross_oracle_v1"
USER_AGENT = "BTC-5M-Lab-Cross-Oracle/1.0"


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _timestamp_ms(value: Any) -> int | None:
    parsed = _int(value)
    if parsed is None or parsed <= 0:
        return None
    # Accept seconds, milliseconds, microseconds, or nanoseconds.
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


def _levels(value: Any, *, bids: bool) -> list[list[float]]:
    rows: list[list[float]] = []
    for item in value or []:
        if isinstance(item, dict):
            price = _float(item.get("price") or item.get("p"))
            size = _float(item.get("size") or item.get("quantity") or item.get("q"))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            price = _float(item[0])
            size = _float(item[1])
        else:
            continue
        if price is None or size is None or price <= 0 or size < 0:
            continue
        rows.append([price, size])
    rows.sort(key=lambda row: row[0], reverse=bids)
    return rows[:20]


def current_btc_5m_slug(now_seconds: float | None = None) -> tuple[str, int]:
    now = time.time() if now_seconds is None else float(now_seconds)
    bucket = int(now // 300) * 300
    return f"btc-updown-5m-{bucket}", bucket


def parse_gamma_market(payload: dict[str, Any], *, bucket_start: int) -> dict[str, Any] | None:
    tokens = [str(value) for value in _json_list(payload.get("clobTokenIds"))]
    outcomes = [str(value) for value in _json_list(payload.get("outcomes"))]
    if len(tokens) < 2:
        return None

    mapping: dict[str, str] = {}
    for index, token_id in enumerate(tokens):
        outcome = outcomes[index] if index < len(outcomes) else ""
        mapping[outcome.strip().upper()] = token_id
    up_token = mapping.get("UP") or tokens[0]
    down_token = mapping.get("DOWN") or (tokens[1] if len(tokens) > 1 else None)
    if not up_token or not down_token or up_token == down_token:
        return None

    slug = str(payload.get("slug") or f"btc-updown-5m-{bucket_start}")
    return {
        "id": str(payload.get("id") or "") or None,
        "slug": slug,
        "question": str(payload.get("question") or "BTC Up or Down 5m"),
        "conditionId": str(payload.get("conditionId") or "") or None,
        "bucketStartSec": int(bucket_start),
        "windowStartMs": int(bucket_start) * 1000,
        "windowEndMs": (int(bucket_start) + 300) * 1000,
        "upTokenId": up_token,
        "downTokenId": down_token,
        "active": bool(payload.get("active", True)),
        "closed": bool(payload.get("closed", False)),
    }


def parse_chainlink_message(raw: str) -> dict[str, Any] | None:
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(envelope, dict):
        return None
    topic = str(envelope.get("topic") or "")
    if topic not in {"crypto_prices_chainlink", "prices.crypto.chainlink"}:
        return None
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        payload = envelope.get("data")
    if not isinstance(payload, dict):
        return None
    symbol = str(payload.get("symbol") or "").lower()
    if symbol and symbol != CHAINLINK_SYMBOL:
        return None
    price = _float(payload.get("value") if payload.get("value") is not None else payload.get("price"))
    if price is None or price <= 0:
        return None
    source_timestamp_ms = _timestamp_ms(payload.get("timestamp") or envelope.get("timestamp"))
    return {
        "topic": topic,
        "symbol": symbol or CHAINLINK_SYMBOL,
        "price": price,
        "sourceTimestampMs": source_timestamp_ms,
    }


def _http_json(url: str, *, timeout: float = 4.0) -> Any:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class CrossOracleCollector:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.db_lock = threading.RLock()
        self.stop_event = threading.Event()
        self.started_at_ms = int(time.time() * 1000)
        self.chainlink_ws: Any = None
        self.polymarket_ws: Any = None
        self.polymarket_generation = 0
        self.market: dict[str, Any] | None = None
        self.chainlink_ticks: list[tuple[int, float]] = []
        self.chainlink = {
            "status": "STARTING",
            "symbol": CHAINLINK_SYMBOL,
            "price": None,
            "sourceTimestampMs": None,
            "receivedTimestampMs": None,
            "error": None,
        }
        self.polymarket = {
            "status": "WAITING_MARKET",
            "receivedTimestampMs": None,
            "sourceTimestampMs": None,
            "error": None,
            "up": {"tokenId": None, "bestBid": None, "bestAsk": None, "lastTrade": None},
            "down": {"tokenId": None, "bestBid": None, "bestAsk": None, "lastTrade": None},
            "startPrice": None,
            "startPriceTimestampMs": None,
            "startPriceOffsetMs": None,
        }
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self._create_schema()
        self.chainlink_rows = int(self.db.execute("SELECT COUNT(*) FROM chainlink_ticks").fetchone()[0])
        self.polymarket_rows = int(self.db.execute("SELECT COUNT(*) FROM polymarket_events").fetchone()[0])

    def _create_schema(self) -> None:
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS chainlink_ticks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    price REAL NOT NULL,
                    source_timestamp_ms INTEGER,
                    received_wall_ns INTEGER NOT NULL,
                    topic TEXT,
                    raw_json TEXT NOT NULL,
                    schema_version TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chainlink_ticks_source
                    ON chainlink_ticks(source_timestamp_ms);
                CREATE INDEX IF NOT EXISTS idx_chainlink_ticks_received
                    ON chainlink_ticks(received_wall_ns);

                CREATE TABLE IF NOT EXISTS polymarket_markets (
                    slug TEXT PRIMARY KEY,
                    market_id TEXT,
                    condition_id TEXT,
                    question TEXT,
                    window_start_ms INTEGER NOT NULL,
                    window_end_ms INTEGER NOT NULL,
                    up_token_id TEXT NOT NULL,
                    down_token_id TEXT NOT NULL,
                    discovered_at_ms INTEGER NOT NULL,
                    gamma_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS polymarket_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_slug TEXT NOT NULL,
                    condition_id TEXT,
                    token_id TEXT,
                    outcome TEXT,
                    event_type TEXT NOT NULL,
                    best_bid REAL,
                    best_ask REAL,
                    last_trade REAL,
                    source_timestamp_ms INTEGER,
                    received_wall_ns INTEGER NOT NULL,
                    bids_json TEXT,
                    asks_json TEXT,
                    raw_json TEXT NOT NULL,
                    schema_version TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_poly_events_market_received
                    ON polymarket_events(market_slug, received_wall_ns);
                CREATE INDEX IF NOT EXISTS idx_poly_events_source
                    ON polymarket_events(source_timestamp_ms);
                """
            )
            self.db.commit()

    def start(self) -> None:
        if websocket is None:
            with self.lock:
                self.chainlink["status"] = "ERROR"
                self.chainlink["error"] = "websocket-client is not installed"
                self.polymarket["status"] = "ERROR"
                self.polymarket["error"] = "websocket-client is not installed"
            return
        threading.Thread(target=self._chainlink_supervisor, name="cross-oracle-chainlink", daemon=True).start()
        threading.Thread(target=self._market_supervisor, name="cross-oracle-market", daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()
        for ws in (self.chainlink_ws, self.polymarket_ws):
            try:
                if ws is not None:
                    ws.close()
            except Exception:
                pass
        with self.db_lock:
            self.db.commit()
            self.db.close()

    def _chainlink_supervisor(self) -> None:
        while not self.stop_event.is_set():
            with self.lock:
                self.chainlink["status"] = "CONNECTING"
                self.chainlink["error"] = None
            ws = websocket.WebSocketApp(
                CHAINLINK_WS_URL,
                on_open=self._chainlink_open,
                on_message=self._chainlink_message,
                on_error=self._chainlink_error,
                on_close=self._chainlink_close,
            )
            self.chainlink_ws = ws
            try:
                ws.run_forever()
            except Exception as exc:  # pragma: no cover - network dependent.
                self._chainlink_error(ws, exc)
            if not self.stop_event.wait(2.0):
                continue

    def _chainlink_open(self, ws: Any) -> None:
        subscription = {
            "action": "subscribe",
            "subscriptions": [
                {
                    "topic": "crypto_prices_chainlink",
                    "type": "*",
                    "filters": json.dumps({"symbol": CHAINLINK_SYMBOL}, separators=(",", ":")),
                }
            ],
        }
        ws.send(json.dumps(subscription, separators=(",", ":")))
        with self.lock:
            self.chainlink["status"] = "LIVE"
            self.chainlink["error"] = None
        threading.Thread(
            target=self._text_heartbeat,
            args=(ws, 5.0, "chainlink"),
            name="cross-oracle-chainlink-heartbeat",
            daemon=True,
        ).start()

    def _chainlink_message(self, _ws: Any, raw: str) -> None:
        if raw == "PONG":
            return
        parsed = parse_chainlink_message(raw)
        if parsed is None:
            return
        received_wall_ns = time.time_ns()
        received_ms = received_wall_ns // 1_000_000
        source_ms = parsed["sourceTimestampMs"] or received_ms
        price = float(parsed["price"])
        with self.lock:
            self.chainlink.update(
                status="LIVE",
                price=price,
                sourceTimestampMs=parsed["sourceTimestampMs"],
                receivedTimestampMs=received_ms,
                error=None,
            )
            self.chainlink_ticks.append((source_ms, price))
            cutoff = received_ms - 15 * 60 * 1000
            if len(self.chainlink_ticks) > 20_000:
                self.chainlink_ticks = self.chainlink_ticks[-10_000:]
            while self.chainlink_ticks and self.chainlink_ticks[0][0] < cutoff:
                self.chainlink_ticks.pop(0)
            self._maybe_set_start_price_locked()
        with self.db_lock:
            self.db.execute(
                """INSERT INTO chainlink_ticks(
                       symbol, price, source_timestamp_ms, received_wall_ns,
                       topic, raw_json, schema_version
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    CHAINLINK_SYMBOL,
                    price,
                    parsed["sourceTimestampMs"],
                    received_wall_ns,
                    parsed["topic"],
                    raw,
                    SCHEMA_VERSION,
                ),
            )
            self.db.commit()
            self.chainlink_rows += 1

    def _chainlink_error(self, _ws: Any, error: Any) -> None:
        with self.lock:
            self.chainlink["status"] = "ERROR"
            self.chainlink["error"] = str(error)[:300]

    def _chainlink_close(self, _ws: Any, status_code: Any, message: Any) -> None:
        if self.stop_event.is_set():
            return
        with self.lock:
            self.chainlink["status"] = "RECONNECTING"
            if status_code or message:
                self.chainlink["error"] = f"close {status_code}: {message}"[:300]

    def _text_heartbeat(self, ws: Any, interval: float, stream: str, generation: int | None = None) -> None:
        while not self.stop_event.wait(interval):
            if generation is not None and generation != self.polymarket_generation:
                return
            try:
                ws.send("PING")
            except Exception as exc:
                with self.lock:
                    target = self.polymarket if stream == "polymarket" else self.chainlink
                    target["error"] = f"heartbeat: {exc}"[:300]
                return

    def _market_supervisor(self) -> None:
        last_bucket: int | None = None
        while not self.stop_event.is_set():
            slug, bucket = current_btc_5m_slug()
            should_refresh = bucket != last_bucket or self.market is None
            if should_refresh:
                if self._discover_market(slug, bucket):
                    last_bucket = bucket
            self.stop_event.wait(MARKET_REFRESH_SECONDS)

    def _discover_market(self, slug: str, bucket: int) -> bool:
        try:
            payload = _http_json(GAMMA_MARKET_BY_SLUG.format(slug=urllib.parse.quote(slug)))
            if isinstance(payload, list):
                payload = payload[0] if payload else None
            if not isinstance(payload, dict):
                raise ValueError("Gamma returned no market object")
            market = parse_gamma_market(payload, bucket_start=bucket)
            if market is None:
                raise ValueError("Gamma market lacks usable UP/DOWN CLOB token IDs")
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            with self.lock:
                self.polymarket["status"] = "WAITING_MARKET"
                self.polymarket["error"] = f"market discovery: {exc}"[:300]
            return False

        changed = self.market is None or self.market.get("slug") != market["slug"]
        with self.lock:
            self.market = market
            self.polymarket["up"] = {
                "tokenId": market["upTokenId"],
                "bestBid": None,
                "bestAsk": None,
                "lastTrade": None,
            }
            self.polymarket["down"] = {
                "tokenId": market["downTokenId"],
                "bestBid": None,
                "bestAsk": None,
                "lastTrade": None,
            }
            self.polymarket["startPrice"] = None
            self.polymarket["startPriceTimestampMs"] = None
            self.polymarket["startPriceOffsetMs"] = None
            self.polymarket["status"] = "CONNECTING" if changed else self.polymarket["status"]
            self.polymarket["error"] = None
            self._maybe_set_start_price_locked()
        with self.db_lock:
            self.db.execute(
                """INSERT INTO polymarket_markets(
                       slug, market_id, condition_id, question, window_start_ms,
                       window_end_ms, up_token_id, down_token_id, discovered_at_ms,
                       gamma_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(slug) DO UPDATE SET
                       market_id=excluded.market_id,
                       condition_id=excluded.condition_id,
                       question=excluded.question,
                       up_token_id=excluded.up_token_id,
                       down_token_id=excluded.down_token_id,
                       gamma_json=excluded.gamma_json""",
                (
                    market["slug"],
                    market["id"],
                    market["conditionId"],
                    market["question"],
                    market["windowStartMs"],
                    market["windowEndMs"],
                    market["upTokenId"],
                    market["downTokenId"],
                    int(time.time() * 1000),
                    json.dumps(payload, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()
        if changed:
            self._restart_polymarket_stream()
        return True

    def _maybe_set_start_price_locked(self) -> None:
        if self.market is None or self.polymarket.get("startPrice") is not None:
            return
        target_ms = int(self.market["windowStartMs"])
        candidates = list(self.chainlink_ticks)
        if not candidates:
            with self.db_lock:
                rows = self.db.execute(
                    """SELECT source_timestamp_ms, price FROM chainlink_ticks
                       WHERE source_timestamp_ms BETWEEN ? AND ?
                       ORDER BY ABS(source_timestamp_ms - ?) ASC LIMIT 1""",
                    (target_ms - 10_000, target_ms + 10_000, target_ms),
                ).fetchall()
            candidates = [
                (int(row["source_timestamp_ms"]), float(row["price"]))
                for row in rows
                if row["source_timestamp_ms"] is not None
            ]
        if not candidates:
            return
        timestamp_ms, price = min(candidates, key=lambda row: abs(row[0] - target_ms))
        offset_ms = int(timestamp_ms - target_ms)
        if abs(offset_ms) > 10_000:
            return
        self.polymarket["startPrice"] = price
        self.polymarket["startPriceTimestampMs"] = timestamp_ms
        self.polymarket["startPriceOffsetMs"] = offset_ms

    def _restart_polymarket_stream(self) -> None:
        self.polymarket_generation += 1
        generation = self.polymarket_generation
        try:
            if self.polymarket_ws is not None:
                self.polymarket_ws.close()
        except Exception:
            pass
        threading.Thread(
            target=self._polymarket_stream_loop,
            args=(generation,),
            name=f"cross-oracle-polymarket-{generation}",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._prime_polymarket_books,
            args=(generation,),
            name=f"cross-oracle-prime-{generation}",
            daemon=True,
        ).start()

    def _polymarket_stream_loop(self, generation: int) -> None:
        while not self.stop_event.is_set() and generation == self.polymarket_generation:
            ws = websocket.WebSocketApp(
                POLYMARKET_WS_URL,
                on_open=lambda app: self._polymarket_open(app, generation),
                on_message=lambda app, raw: self._polymarket_message(app, raw, generation),
                on_error=lambda app, error: self._polymarket_error(app, error, generation),
                on_close=lambda app, code, message: self._polymarket_close(app, code, message, generation),
            )
            self.polymarket_ws = ws
            try:
                ws.run_forever()
            except Exception as exc:  # pragma: no cover - network dependent.
                self._polymarket_error(ws, exc, generation)
            if self.stop_event.wait(2.0):
                return

    def _polymarket_open(self, ws: Any, generation: int) -> None:
        if generation != self.polymarket_generation:
            ws.close()
            return
        with self.lock:
            market = dict(self.market or {})
        tokens = [market.get("upTokenId"), market.get("downTokenId")]
        tokens = [str(token) for token in tokens if token]
        if len(tokens) != 2:
            ws.close()
            return
        ws.send(
            json.dumps(
                {
                    "assets_ids": tokens,
                    "type": "market",
                    "custom_feature_enabled": True,
                },
                separators=(",", ":"),
            )
        )
        with self.lock:
            self.polymarket["status"] = "LIVE"
            self.polymarket["error"] = None
        threading.Thread(
            target=self._text_heartbeat,
            args=(ws, 10.0, "polymarket", generation),
            name=f"cross-oracle-poly-heartbeat-{generation}",
            daemon=True,
        ).start()

    def _polymarket_message(self, _ws: Any, raw: str, generation: int) -> None:
        if generation != self.polymarket_generation or raw == "PONG":
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        events = payload if isinstance(payload, list) else [payload]
        for event in events:
            if isinstance(event, dict):
                self._handle_polymarket_event(event, raw)

    def _handle_polymarket_event(self, event: dict[str, Any], raw: str) -> None:
        event_type = str(event.get("event_type") or event.get("eventType") or event.get("type") or "unknown")
        source_ms = _timestamp_ms(event.get("timestamp"))
        received_wall_ns = time.time_ns()
        received_ms = received_wall_ns // 1_000_000
        if event_type in {"price_change", "priceChange"}:
            changes = event.get("price_changes") or event.get("priceChanges") or []
            if isinstance(changes, list):
                for change in changes:
                    if isinstance(change, dict):
                        self._apply_poly_quote(change, "price_change", source_ms, received_wall_ns, raw)
            return
        self._apply_poly_quote(event, event_type, source_ms, received_wall_ns, raw)
        with self.lock:
            self.polymarket["receivedTimestampMs"] = received_ms
            if source_ms is not None:
                self.polymarket["sourceTimestampMs"] = source_ms
            self.polymarket["status"] = "LIVE"
            self.polymarket["error"] = None

    def _apply_poly_quote(
        self,
        event: dict[str, Any],
        event_type: str,
        source_ms: int | None,
        received_wall_ns: int,
        raw: str,
    ) -> None:
        token_id = str(
            event.get("asset_id")
            or event.get("assetId")
            or event.get("token_id")
            or event.get("tokenId")
            or ""
        )
        with self.lock:
            market = dict(self.market or {})
        outcome = "UP" if token_id and token_id == market.get("upTokenId") else "DOWN" if token_id and token_id == market.get("downTokenId") else None
        if outcome is None:
            return

        bids = _levels(event.get("bids"), bids=True)
        asks = _levels(event.get("asks"), bids=False)
        best_bid = _float(event.get("best_bid") if event.get("best_bid") is not None else event.get("bestBid"))
        best_ask = _float(event.get("best_ask") if event.get("best_ask") is not None else event.get("bestAsk"))
        if best_bid is None and bids:
            best_bid = bids[0][0]
        if best_ask is None and asks:
            best_ask = asks[0][0]
        last_trade = _float(
            event.get("last_trade_price")
            if event.get("last_trade_price") is not None
            else event.get("lastTradePrice")
        )
        if event_type == "last_trade_price" and last_trade is None:
            last_trade = _float(event.get("price"))

        side_key = "up" if outcome == "UP" else "down"
        received_ms = received_wall_ns // 1_000_000
        with self.lock:
            side = self.polymarket[side_key]
            if best_bid is not None:
                side["bestBid"] = best_bid
            if best_ask is not None:
                side["bestAsk"] = best_ask
            if last_trade is not None:
                side["lastTrade"] = last_trade
            self.polymarket["receivedTimestampMs"] = received_ms
            if source_ms is not None:
                self.polymarket["sourceTimestampMs"] = source_ms
            self.polymarket["status"] = "LIVE"
            self.polymarket["error"] = None

        with self.db_lock:
            self.db.execute(
                """INSERT INTO polymarket_events(
                       market_slug, condition_id, token_id, outcome, event_type,
                       best_bid, best_ask, last_trade, source_timestamp_ms,
                       received_wall_ns, bids_json, asks_json, raw_json, schema_version
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    market.get("slug") or "",
                    market.get("conditionId"),
                    token_id,
                    outcome,
                    event_type,
                    best_bid,
                    best_ask,
                    last_trade,
                    source_ms,
                    received_wall_ns,
                    json.dumps(bids, separators=(",", ":")) if bids else None,
                    json.dumps(asks, separators=(",", ":")) if asks else None,
                    raw,
                    SCHEMA_VERSION,
                ),
            )
            self.db.commit()
            self.polymarket_rows += 1

    def _prime_polymarket_books(self, generation: int) -> None:
        with self.lock:
            market = dict(self.market or {})
        for outcome, token_id in (("UP", market.get("upTokenId")), ("DOWN", market.get("downTokenId"))):
            if generation != self.polymarket_generation or not token_id:
                return
            try:
                payload = _http_json(CLOB_BOOK_URL.format(token_id=urllib.parse.quote(str(token_id))))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            payload = dict(payload)
            payload["asset_id"] = str(token_id)
            payload["event_type"] = "rest_book_prime"
            self._apply_poly_quote(
                payload,
                "rest_book_prime",
                _timestamp_ms(payload.get("timestamp")),
                time.time_ns(),
                json.dumps(payload, separators=(",", ":"), default=str),
            )

    def _polymarket_error(self, _ws: Any, error: Any, generation: int) -> None:
        if generation != self.polymarket_generation:
            return
        with self.lock:
            self.polymarket["status"] = "ERROR"
            self.polymarket["error"] = str(error)[:300]

    def _polymarket_close(self, _ws: Any, code: Any, message: Any, generation: int) -> None:
        if self.stop_event.is_set() or generation != self.polymarket_generation:
            return
        with self.lock:
            self.polymarket["status"] = "RECONNECTING"
            if code or message:
                self.polymarket["error"] = f"close {code}: {message}"[:300]

    def snapshot(self) -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        with self.lock:
            chainlink = dict(self.chainlink)
            polymarket = {
                **self.polymarket,
                "up": dict(self.polymarket["up"]),
                "down": dict(self.polymarket["down"]),
            }
            market = dict(self.market) if self.market is not None else None
        chainlink_received = _int(chainlink.get("receivedTimestampMs"))
        poly_received = _int(polymarket.get("receivedTimestampMs"))
        if market is not None:
            seconds_left = max(0.0, (int(market["windowEndMs"]) - now_ms) / 1000.0)
            market["secondsLeft"] = seconds_left
        else:
            seconds_left = None
        try:
            db_bytes = DB_PATH.stat().st_size
        except OSError:
            db_bytes = None
        statuses = {str(chainlink.get("status")), str(polymarket.get("status"))}
        overall = "LIVE" if statuses == {"LIVE"} else "PARTIAL" if "LIVE" in statuses else "STARTING"
        return {
            "status": overall,
            "paperOnly": True,
            "liveOrdersAffected": False,
            "generatedAtMs": now_ms,
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ms / 1000)),
            "chainlink": {
                **chainlink,
                "ageMs": now_ms - chainlink_received if chainlink_received else None,
            },
            "polymarket": {
                **polymarket,
                "ageMs": now_ms - poly_received if poly_received else None,
                "market": market,
                "secondsLeft": seconds_left,
            },
            "storage": {
                "schemaVersion": SCHEMA_VERSION,
                "dbPath": str(DB_PATH),
                "dbBytes": db_bytes,
                "chainlinkRows": self.chainlink_rows,
                "polymarketRows": self.polymarket_rows,
                "rawPayloadsStored": True,
                "sourceAndReceiptTimestampsStored": True,
            },
        }


class _Handler(BaseHTTPRequestHandler):
    collector: CrossOracleCollector

    def do_GET(self) -> None:  # noqa: N802
        if self.path not in {"/state", "/health", "/api/state"}:
            self.send_response(404)
            self.end_headers()
            return
        payload = self.collector.snapshot()
        body = json.dumps(payload, allow_nan=False, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def main() -> int:
    collector = CrossOracleCollector()
    collector.start()
    handler = type("CrossOracleHandler", (_Handler,), {"collector": collector})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Cross-oracle collector listening on http://{HOST}:{PORT}/state; db={DB_PATH}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        collector.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
