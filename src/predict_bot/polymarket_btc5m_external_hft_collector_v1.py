from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
import zlib
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

try:
    import websocket
except Exception:  # pragma: no cover
    websocket = None

from .multi_prediction_observer import current_slug, parse_gamma_candidate

ROOT = Path(__file__).resolve().parents[2]
VERSION = "POLYMARKET_BTC5M_EXTERNAL_HFT_COLLECTOR_V1"
HOST = os.environ.get("POLYMARKET_BTC5M_EXTERNAL_HFT_HOST", "127.0.0.1")
PORT = int(os.environ.get("POLYMARKET_BTC5M_EXTERNAL_HFT_PORT", "8811"))
DB_PATH = Path(os.environ.get(
    "POLYMARKET_BTC5M_EXTERNAL_HFT_DB",
    ROOT / "data" / "polymarket_btc5m_external_hft_v1.db",
))
RETENTION_HOURS = max(1.0, float(os.environ.get("POLYMARKET_BTC5M_EXTERNAL_HFT_RETENTION_HOURS", "3")))
PRICE_BUNDLE_MS = max(20, int(os.environ.get("POLYMARKET_BTC5M_EXTERNAL_HFT_PRICE_BUNDLE_MS", "100")))
BOOK_CHECKPOINT_MS = max(1000, int(os.environ.get("POLYMARKET_BTC5M_EXTERNAL_HFT_BOOK_CHECKPOINT_MS", "5000")))
COMMIT_INTERVAL_MS = max(50, int(os.environ.get("POLYMARKET_BTC5M_EXTERNAL_HFT_COMMIT_INTERVAL_MS", "250")))
DISCOVERY_SECONDS = max(0.5, float(os.environ.get("POLYMARKET_BTC5M_EXTERNAL_HFT_DISCOVERY_SECONDS", "2")))
GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
CLOB_BOOK_URL = "https://clob.polymarket.com/book"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def now_ms() -> int:
    return int(time.time() * 1000)


def number(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def ts_ms(v: Any) -> int | None:
    if v is None:
        return None
    try:
        x = int(float(v))
    except (TypeError, ValueError, OverflowError):
        return None
    if x <= 0:
        return None
    if x < 10_000_000_000:
        return x * 1000
    if x >= 10_000_000_000_000_000:
        return x // 1_000_000
    if x >= 10_000_000_000_000:
        return x // 1000
    return x


def unwrap_event(event: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    typ = str(event.get("event_type") or event.get("eventType") or event.get("type") or "unknown").lower()
    payload = event.get("payload")
    if isinstance(payload, dict):
        return typ, dict(payload)
    return typ, event


def parse_book_levels(rows: Any) -> dict[float, float]:
    out: dict[float, float] = {}
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        px = number(row.get("price"))
        qty = number(row.get("size"))
        if px is None or qty is None or px <= 0 or px >= 1 or qty < 0:
            continue
        if qty > 0:
            out[px] = qty
    return out


@dataclass
class MarketIdentity:
    market_id: str
    condition_id: str
    event_slug: str
    market_slug: str | None
    question: str
    bucket_start_sec: int
    window_start_ms: int
    window_end_ms: int
    up_token_id: str
    down_token_id: str

    @classmethod
    def from_candidate(cls, c: dict[str, Any]) -> "MarketIdentity":
        return cls(
            market_id=str(c.get("marketId") or ""),
            condition_id=str(c["conditionId"]),
            event_slug=str(c["eventSlug"]),
            market_slug=str(c.get("gammaMarketSlug") or "") or None,
            question=str(c.get("question") or "BTC Up or Down 5m"),
            bucket_start_sec=int(c["bucketStartSec"]),
            window_start_ms=int(c["windowStartMs"]),
            window_end_ms=int(c["windowEndMs"]),
            up_token_id=str(c["upTokenId"]),
            down_token_id=str(c["downTokenId"]),
        )

    def outcome_for(self, token: str) -> str | None:
        if str(token) == self.up_token_id:
            return "UP"
        if str(token) == self.down_token_id:
            return "DOWN"
        return None


class Collector:
    def __init__(self) -> None:
        self.db_path = DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self._schema()

        self.http = httpx.Client(
            timeout=httpx.Timeout(4.0, connect=1.0),
            trust_env=False,
            follow_redirects=True,
            headers={"Accept": "application/json", "User-Agent": "BTC5M-Lab-Poly-External-HFT/1.0"},
        )
        self.lock = threading.RLock()
        self.db_lock = threading.RLock()
        self.stop_event = threading.Event()
        self.market: MarketIdentity | None = None
        self.market_generation = 0
        self.ws: Any = None
        self.ws_status = "OFFLINE"
        self.ws_error: str | None = None
        self.last_error: str | None = None
        self.last_message_ms: int | None = None
        self.last_discovery_ms: int | None = None
        self.last_cleanup_ms: int | None = None
        self.event_count = 0
        self.trade_count = 0
        self.full_book_count = 0
        self.price_change_count = 0
        self.last_book_persist_ms = {"UP": 0, "DOWN": 0}
        self.pending_writes = 0
        self.last_commit_ms = now_ms()
        self.price_change_buffer: list[dict[str, Any]] = []
        self.price_change_buffer_started_ms: int | None = None
        self.books = {
            "UP": {"bids": {}, "asks": {}, "lastTradePrice": None, "tickSize": None, "sourceMs": None},
            "DOWN": {"bids": {}, "asks": {}, "lastTradePrice": None, "tickSize": None, "sourceMs": None},
        }

    def _schema(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS poly_external_markets_v1(
                condition_id TEXT PRIMARY KEY,
                market_id TEXT,
                event_slug TEXT NOT NULL,
                market_slug TEXT,
                question TEXT,
                bucket_start_sec INTEGER NOT NULL,
                window_start_ms INTEGER NOT NULL,
                window_end_ms INTEGER NOT NULL,
                up_token_id TEXT NOT NULL,
                down_token_id TEXT NOT NULL,
                first_seen_ms INTEGER NOT NULL,
                last_seen_ms INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS poly_external_events_v1(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                condition_id TEXT NOT NULL,
                token_id TEXT,
                outcome TEXT,
                event_type TEXT NOT NULL,
                source_ms INTEGER,
                received_ms INTEGER NOT NULL,
                native_side TEXT,
                price REAL,
                size REAL,
                best_bid REAL,
                best_ask REAL,
                tick_size REAL,
                transaction_hash TEXT,
                payload_json TEXT,
                payload_blob BLOB
            );
            CREATE INDEX IF NOT EXISTS idx_poly_ext_event_market_time
              ON poly_external_events_v1(condition_id, received_ms);
            CREATE INDEX IF NOT EXISTS idx_poly_ext_event_token_time
              ON poly_external_events_v1(token_id, received_ms);
            """
        )
        cols = {str(row[1]) for row in self.db.execute("PRAGMA table_info(poly_external_events_v1)").fetchall()}
        if "payload_blob" not in cols:
            self.db.execute("ALTER TABLE poly_external_events_v1 ADD COLUMN payload_blob BLOB")
        self.db.commit()

    def start(self) -> None:
        threading.Thread(target=self._discovery_loop, name="poly-ext-discovery", daemon=True).start()

    def close(self) -> None:
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
        try:
            self._flush_price_change_buffer()
            with self.db_lock:
                self.db.commit()
            self.db.close()
        except Exception:
            pass

    def _discover(self) -> MarketIdentity | None:
        slug, bucket = current_slug("BTC")
        response = self.http.get(GAMMA_EVENTS_URL, params={"slug": slug})
        response.raise_for_status()
        rows = response.json()
        if isinstance(rows, list):
            for event in rows:
                if not isinstance(event, dict):
                    continue
                markets = event.get("markets")
                if not isinstance(markets, list):
                    continue
                for raw in markets:
                    if not isinstance(raw, dict):
                        continue
                    c = parse_gamma_candidate(raw, asset="BTC", event_slug=slug, bucket=bucket)
                    if c is not None:
                        return MarketIdentity.from_candidate(c)

        response = self.http.get(GAMMA_MARKETS_URL, params={"slug": slug})
        response.raise_for_status()
        rows = response.json()
        if isinstance(rows, list):
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                c = parse_gamma_candidate(raw, asset="BTC", event_slug=slug, bucket=bucket)
                if c is not None:
                    return MarketIdentity.from_candidate(c)
        return None

    def _discovery_loop(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                market = self._discover()
                self.last_discovery_ms = now_ms()
                if market is not None:
                    self._activate_market(market)
                self.last_error = None
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {str(exc)[:500]}"
            at = now_ms()
            if self.last_cleanup_ms is None or at - self.last_cleanup_ms >= 60_000:
                self._cleanup(at)
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(0.1, DISCOVERY_SECONDS - elapsed))

    def _activate_market(self, market: MarketIdentity) -> None:
        current = self.market
        if current is not None and current.condition_id != market.condition_id:
            self._flush_price_change_buffer()
        with self.lock:
            same = self.market is not None and self.market.condition_id == market.condition_id
            self.market = market
            if same:
                self._touch_market(market)
                return
            self.market_generation += 1
            generation = self.market_generation
            self.books = {
                "UP": {"bids": {}, "asks": {}, "lastTradePrice": None, "tickSize": None, "sourceMs": None},
                "DOWN": {"bids": {}, "asks": {}, "lastTradePrice": None, "tickSize": None, "sourceMs": None},
            }
            self.last_book_persist_ms = {"UP": 0, "DOWN": 0}
            self._touch_market(market)
        self._prime_rest_books(market)
        self._restart_ws(market, generation)

    def _touch_market(self, market: MarketIdentity) -> None:
        at = now_ms()
        with self.db_lock:
            self.db.execute(
                """INSERT INTO poly_external_markets_v1(
                    condition_id,market_id,event_slug,market_slug,question,bucket_start_sec,
                    window_start_ms,window_end_ms,up_token_id,down_token_id,first_seen_ms,last_seen_ms)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(condition_id) DO UPDATE SET last_seen_ms=excluded.last_seen_ms""",
                (
                    market.condition_id, market.market_id, market.event_slug, market.market_slug,
                    market.question, market.bucket_start_sec, market.window_start_ms,
                    market.window_end_ms, market.up_token_id, market.down_token_id, at, at,
                ),
            )
            self.db.commit()

    def _prime_rest_books(self, market: MarketIdentity) -> None:
        for outcome, token in (("UP", market.up_token_id), ("DOWN", market.down_token_id)):
            try:
                r = self.http.get(CLOB_BOOK_URL, params={"token_id": token})
                r.raise_for_status()
                payload = r.json()
                received = now_ms()
                self._apply_full_book(outcome, token, payload, ts_ms(payload.get("timestamp")), received, "rest_book_prime")
            except Exception as exc:
                self.last_error = f"REST_BOOK_{outcome}: {type(exc).__name__}: {str(exc)[:300]}"

    def _restart_ws(self, market: MarketIdentity, generation: int) -> None:
        if websocket is None:
            self.ws_status = "ERROR"
            self.ws_error = "websocket-client unavailable"
            return
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:
            pass
        threading.Thread(
            target=self._ws_loop,
            args=(market, generation),
            name=f"poly-ext-ws-{generation}",
            daemon=True,
        ).start()

    def _ws_loop(self, market: MarketIdentity, generation: int) -> None:
        while not self.stop_event.is_set() and generation == self.market_generation:
            app = websocket.WebSocketApp(
                WS_URL,
                on_open=lambda ws: self._on_open(ws, market, generation),
                on_message=lambda _ws, raw: self._on_message(raw, generation),
                on_error=lambda _ws, err: self._on_error(err, generation),
                on_close=lambda _ws, code, msg: self._on_close(code, msg, generation),
            )
            self.ws = app
            try:
                app.run_forever()
            except Exception as exc:
                self._on_error(exc, generation)
            if self.stop_event.wait(1.0):
                return

    def _on_open(self, ws: Any, market: MarketIdentity, generation: int) -> None:
        if generation != self.market_generation:
            ws.close()
            return
        ws.send(json.dumps({
            "assets_ids": [market.up_token_id, market.down_token_id],
            "type": "market",
            "custom_feature_enabled": True,
        }, separators=(",", ":")))
        self.ws_status = "LIVE"
        self.ws_error = None
        threading.Thread(target=self._heartbeat, args=(ws, generation), daemon=True).start()

    def _heartbeat(self, ws: Any, generation: int) -> None:
        while not self.stop_event.wait(10):
            if generation != self.market_generation:
                return
            try:
                ws.send("PING")
            except Exception:
                return

    def _on_error(self, err: Any, generation: int) -> None:
        if generation != self.market_generation:
            return
        self.ws_status = "DEGRADED"
        self.ws_error = str(err)[:500]

    def _on_close(self, code: Any, msg: Any, generation: int) -> None:
        if generation != self.market_generation:
            return
        self.ws_status = "OFFLINE"
        if code not in {None, 1000}:
            self.ws_error = f"close={code} {str(msg)[:300]}"

    def _on_message(self, raw: str, generation: int) -> None:
        if generation != self.market_generation or raw == "PONG":
            return
        try:
            parsed = json.loads(raw)
        except Exception:
            return
        events = parsed if isinstance(parsed, list) else [parsed]
        for event in events:
            if isinstance(event, dict):
                self._handle_event(event)

    def _handle_event(self, event: dict[str, Any]) -> None:
        typ, payload = unwrap_event(event)
        received = now_ms()
        source = ts_ms(payload.get("timestamp") or event.get("timestamp"))
        self.last_message_ms = received
        self.event_count += 1
        market = self.market
        if market is None:
            return

        if typ == "book":
            token = str(payload.get("asset_id") or payload.get("assetId") or payload.get("token_id") or payload.get("tokenId") or "")
            outcome = market.outcome_for(token)
            if outcome:
                self._apply_full_book(outcome, token, payload, source, received, "book")
            return

        if typ in {"price_change", "pricechange"}:
            changes = payload.get("price_changes") or payload.get("priceChanges") or []
            if not isinstance(changes, list):
                return
            compact: list[dict[str, Any]] = []
            for ch in changes:
                if not isinstance(ch, dict):
                    continue
                token = str(ch.get("asset_id") or ch.get("assetId") or ch.get("token_id") or ch.get("tokenId") or "")
                outcome = market.outcome_for(token)
                if outcome:
                    row = self._apply_price_change(outcome, token, ch, source, received)
                    if row is not None:
                        compact.append(row)
            if compact:
                self._buffer_price_change_event(source, received, compact)
            return

        if typ in {"last_trade_price", "lasttradeprice"}:
            token = str(payload.get("asset_id") or payload.get("assetId") or payload.get("token_id") or payload.get("tokenId") or "")
            outcome = market.outcome_for(token)
            if outcome:
                self._apply_trade(outcome, token, payload, source, received)
            return

        if typ in {"tick_size_change", "ticksizechange"}:
            token = str(payload.get("asset_id") or payload.get("assetId") or payload.get("token_id") or payload.get("tokenId") or "")
            outcome = market.outcome_for(token)
            if outcome:
                tick = number(payload.get("new_tick_size") or payload.get("newTickSize"))
                if tick is not None:
                    with self.lock:
                        self.books[outcome]["tickSize"] = tick
                self._insert_event(token, outcome, typ, source, received, tick_size=tick, payload=payload)

    def _apply_full_book(
        self, outcome: str, token: str, payload: dict[str, Any],
        source: int | None, received: int, event_type: str,
    ) -> None:
        bids = parse_book_levels(payload.get("bids"))
        asks = parse_book_levels(payload.get("asks"))
        with self.lock:
            self.books[outcome]["bids"] = bids
            self.books[outcome]["asks"] = asks
            self.books[outcome]["sourceMs"] = source or received
            tick = number(payload.get("tick_size") or payload.get("tickSize"))
            if tick is not None:
                self.books[outcome]["tickSize"] = tick
            last = number(payload.get("last_trade_price") or payload.get("lastTradePrice"))
            if last is not None:
                self.books[outcome]["lastTradePrice"] = last
        self.full_book_count += 1
        should_persist = event_type == "rest_book_prime" or (
            int(received) - int(self.last_book_persist_ms.get(outcome, 0)) >= BOOK_CHECKPOINT_MS
        )
        if should_persist:
            self.last_book_persist_ms[outcome] = int(received)
            compact_payload = {
                "bids": [{"price": p, "size": q} for p, q in sorted(bids.items(), reverse=True)],
                "asks": [{"price": p, "size": q} for p, q in sorted(asks.items())],
                "tickSize": self.books[outcome].get("tickSize"),
                "lastTradePrice": self.books[outcome].get("lastTradePrice"),
            }
            self._insert_event(token, outcome, "book_checkpoint", source, received, payload=compact_payload)

    def _buffer_price_change_event(
        self, source: int | None, received: int, changes: list[dict[str, Any]],
    ) -> None:
        if not changes:
            return
        if self.price_change_buffer_started_ms is None:
            self.price_change_buffer_started_ms = int(received)
        self.price_change_buffer.append({
            "sourceMs": source,
            "receivedMs": int(received),
            "changes": changes,
        })
        if (
            int(received) - int(self.price_change_buffer_started_ms) >= PRICE_BUNDLE_MS
            or len(self.price_change_buffer) >= 500
        ):
            self._flush_price_change_buffer()

    def _flush_price_change_buffer(self) -> None:
        if not self.price_change_buffer:
            self.price_change_buffer_started_ms = None
            return
        events = self.price_change_buffer
        self.price_change_buffer = []
        self.price_change_buffer_started_ms = None
        first = events[0]
        last = events[-1]
        self._insert_event(
            None,
            None,
            "price_change_bundle",
            first.get("sourceMs"),
            int(last.get("receivedMs") or now_ms()),
            payload={"events": events},
        )

    def _apply_price_change(
        self, outcome: str, token: str, ch: dict[str, Any],
        source: int | None, received: int,
    ) -> dict[str, Any] | None:
        px = number(ch.get("price"))
        qty = number(ch.get("size"))
        side = str(ch.get("side") or "").upper()
        if px is None or qty is None or side not in {"BUY", "SELL"}:
            return None
        key = "bids" if side == "BUY" else "asks"
        with self.lock:
            levels: dict[float, float] = self.books[outcome][key]
            if qty <= 0:
                levels.pop(px, None)
            else:
                levels[px] = qty
            self.books[outcome]["sourceMs"] = source or received
        self.price_change_count += 1
        return {
            "tokenId": token,
            "outcome": outcome,
            "side": side,
            "price": px,
            "size": qty,
            "bestBid": number(ch.get("best_bid") or ch.get("bestBid")),
            "bestAsk": number(ch.get("best_ask") or ch.get("bestAsk")),
        }

    def _apply_trade(
        self, outcome: str, token: str, payload: dict[str, Any],
        source: int | None, received: int,
    ) -> None:
        px = number(payload.get("price"))
        qty = number(payload.get("size"))
        side = str(payload.get("side") or "").upper() or None
        with self.lock:
            if px is not None:
                self.books[outcome]["lastTradePrice"] = px
            self.books[outcome]["sourceMs"] = source or received
        self.trade_count += 1
        self._insert_event(
            token, outcome, "last_trade_price", source, received,
            native_side=side, price=px, size=qty,
            transaction_hash=str(payload.get("transaction_hash") or payload.get("transactionHash") or "") or None,
            payload=payload,
        )

    def _insert_event(
        self, token: str | None, outcome: str | None, event_type: str,
        source: int | None, received: int, *, native_side: str | None = None,
        price: float | None = None, size: float | None = None,
        best_bid: float | None = None, best_ask: float | None = None,
        tick_size: float | None = None, transaction_hash: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        market = self.market
        if market is None:
            return
        with self.db_lock:
            self.db.execute(
                """INSERT INTO poly_external_events_v1(
                    condition_id,token_id,outcome,event_type,source_ms,received_ms,native_side,
                    price,size,best_bid,best_ask,tick_size,transaction_hash,payload_json,payload_blob)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    market.condition_id, token, outcome, str(event_type), source, int(received),
                    native_side, price, size, best_bid, best_ask, tick_size, transaction_hash,
                    None,
                    sqlite3.Binary(zlib.compress(
                        json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                        level=3,
                    )),
                ),
            )
            self.pending_writes += 1
            if self.pending_writes >= 100 or int(received) - int(self.last_commit_ms) >= COMMIT_INTERVAL_MS:
                self.db.commit()
                self.pending_writes = 0
                self.last_commit_ms = int(received)

    def _cleanup(self, at_ms: int) -> None:
        cutoff = int(at_ms - RETENTION_HOURS * 3_600_000)
        with self.db_lock:
            self.db.execute("DELETE FROM poly_external_events_v1 WHERE received_ms < ?", (cutoff,))
            self.db.execute(
                "DELETE FROM poly_external_markets_v1 WHERE last_seen_ms < ? AND condition_id NOT IN (SELECT DISTINCT condition_id FROM poly_external_events_v1)",
                (cutoff,),
            )
            self.db.commit()
        self.last_cleanup_ms = int(at_ms)

    @staticmethod
    def _book_summary(book: dict[str, Any]) -> dict[str, Any]:
        bids = book["bids"]
        asks = book["asks"]
        best_bid = max(bids) if bids else None
        best_ask = min(asks) if asks else None
        return {
            "bestBid": best_bid,
            "bestAsk": best_ask,
            "spread": best_ask - best_bid if best_bid is not None and best_ask is not None else None,
            "bidLevels": len(bids),
            "askLevels": len(asks),
            "bidDepth": float(sum(bids.values())),
            "askDepth": float(sum(asks.values())),
            "topBidDepth": float(bids.get(best_bid, 0.0)) if best_bid is not None else None,
            "topAskDepth": float(asks.get(best_ask, 0.0)) if best_ask is not None else None,
            "lastTradePrice": book.get("lastTradePrice"),
            "tickSize": book.get("tickSize"),
            "sourceMs": book.get("sourceMs"),
        }

    def state(self) -> dict[str, Any]:
        at = now_ms()
        with self.lock:
            market = self.market
            books = {
                "UP": self._book_summary(self.books["UP"]),
                "DOWN": self._book_summary(self.books["DOWN"]),
            }
        return {
            "ok": self.last_error is None,
            "version": VERSION,
            "status": "LIVE" if market is not None and self.ws_status == "LIVE" else "WAITING_MARKET" if market is None else "DEGRADED",
            "readOnly": True,
            "ordersSupported": False,
            "liveOrdersAffected": False,
            "targetDataUsed": False,
            "venue": "POLYMARKET_PUBLIC_CLOB",
            "wsStatus": self.ws_status,
            "wsError": self.ws_error,
            "lastError": self.last_error,
            "lastMessageAgeMs": at - self.last_message_ms if self.last_message_ms else None,
            "lastDiscoveryAgeMs": at - self.last_discovery_ms if self.last_discovery_ms else None,
            "market": None if market is None else {
                "marketId": market.market_id,
                "conditionId": market.condition_id,
                "eventSlug": market.event_slug,
                "marketSlug": market.market_slug,
                "question": market.question,
                "bucketStartSec": market.bucket_start_sec,
                "windowStartMs": market.window_start_ms,
                "windowEndMs": market.window_end_ms,
                "secondsLeft": max(0.0, (market.window_end_ms - at) / 1000.0),
                "upTokenId": market.up_token_id,
                "downTokenId": market.down_token_id,
            },
            "books": books,
            "counts": {
                "events": self.event_count,
                "fullBooks": self.full_book_count,
                "priceChanges": self.price_change_count,
                "trades": self.trade_count,
            },
            "database": str(self.db_path),
            "retentionHours": RETENTION_HOURS,
            "storageMode": "INITIAL_PLUS_PERIODIC_FULL_BOOK_CHECKPOINTS__BATCHED_PRICE_CHANGE__ZLIB",
            "priceBundleMs": PRICE_BUNDLE_MS,
            "bookCheckpointMs": BOOK_CHECKPOINT_MS,
            "commitIntervalMs": COMMIT_INTERVAL_MS,
            "hftUse": "external real-L2/real-trade evidence; execution remains local paper until explicitly integrated",
        }


class Handler(BaseHTTPRequestHandler):
    runtime: Collector

    def log_message(self, *_args: Any) -> None:
        return

    def _send(self, payload: Any, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in {"/", "/state", "/health"}:
            state = self.runtime.state()
            self._send(state, 200 if state.get("ok") else 503 if path == "/health" else 200)
            return
        self._send({"ok": False, "error": "not found"}, 404)


def main() -> int:
    runtime = Collector()
    runtime.start()
    handler = type("PolymarketExternalHftHandler", (Handler,), {"runtime": runtime})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(f"{VERSION} listening on http://{HOST}:{PORT}/state; readOnly=true", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
