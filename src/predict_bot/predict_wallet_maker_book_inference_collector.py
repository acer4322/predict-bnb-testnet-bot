from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

from .predict_wallet_shadow_observer_v2 import normalize_match_leg

try:
    import websocket
except ImportError:  # pragma: no cover - surfaced through /state.
    websocket = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[2]
ASSET = str(os.environ.get("PREDICT_WALLET_MAKER_BOOK_ASSET", "BTC")).strip().upper()
if ASSET not in {"BTC", "ETH"}:
    raise ValueError(f"unsupported Maker book inference asset: {ASSET}")
COHORT = "TARGET_MAKER_BOOK_INFERENCE_V1" if ASSET == "BTC" else f"TARGET_MAKER_BOOK_INFERENCE_{ASSET}_5M_V1"
DEFAULT_DB_NAME = "wallet_maker_book_inference.db" if ASSET == "BTC" else f"wallet_maker_book_inference_{ASSET.lower()}5m.db"
DB_PATH = Path(os.environ.get("PREDICT_WALLET_MAKER_BOOK_DB", ROOT / "data" / DEFAULT_DB_NAME))
TARGET_DB_PATH = Path(os.environ.get("PREDICT_WALLET_SHADOW_DB", ROOT / "data" / "predict_wallet_shadow.db"))
PREDICT_STATE_URL = os.environ.get("PREDICT_WALLET_MAKER_BOOK_STATE_URL", "http://127.0.0.1:8771/state")
WS_URL = os.environ.get("PREDICT_FUN_WS_URL", "wss://ws.predict.fun/ws")
PREDICT_API_BASE = os.environ.get("PREDICT_FUN_API_BASE", "https://api.predict.fun").rstrip("/")
API_KEY_ENV = "PREDICT_FUN_API_KEY"
TARGET_WALLET = os.environ.get(
    "PREDICT_WALLET_SHADOW_TARGET_ADDRESS",
    "0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03",
).strip().lower()
HOST = os.environ.get("PREDICT_WALLET_MAKER_BOOK_HOST", "127.0.0.1")
PORT = int(os.environ.get("PREDICT_WALLET_MAKER_BOOK_PORT", "8778" if ASSET == "BTC" else "8779"))
RETENTION_HOURS = max(24.0, float(os.environ.get("PREDICT_WALLET_MAKER_BOOK_RETENTION_HOURS", "72")))
CHECKPOINT_MS = max(5_000, int(os.environ.get("PREDICT_WALLET_MAKER_BOOK_CHECKPOINT_MS", "10000")))
VERSION = (
    "TARGET_MAKER_BOOK_INFERENCE_V1_FORWARD_ONLY"
    if ASSET == "BTC"
    else f"TARGET_MAKER_BOOK_INFERENCE_{ASSET}_5M_V1_FORWARD_ONLY"
)


def now_ms() -> int:
    return int(time.time() * 1000)


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def positive_int(value: Any) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number > 0 else None


def record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def normalize_levels(value: Any) -> dict[float, float]:
    """Return an aggregated price -> size map from public CLOB levels."""

    result: dict[float, float] = {}
    if not isinstance(value, list):
        return result
    for item in value:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            price, size = finite(item[0]), finite(item[1])
        elif isinstance(item, dict):
            price = finite(item.get("price"))
            size = finite(item.get("size") if item.get("size") is not None else item.get("quantity"))
        else:
            continue
        if price is None or size is None or not 0 <= price <= 1 or size < 0:
            continue
        key = round(price, 12)
        result[key] = result.get(key, 0.0) + size
    return {price: size for price, size in result.items() if size > 1e-12}


def parse_full_book(payload: Any) -> dict[str, Any] | None:
    body = record(payload)
    if isinstance(body.get("data"), dict) and ("success" in body or len(body) <= 3):
        body = record(body.get("data"))
    bids = normalize_levels(body.get("bids"))
    asks = normalize_levels(body.get("asks"))
    if not bids and not asks:
        return None
    return {
        "marketId": positive_int(body.get("marketId")),
        "sourceTimestampMs": positive_int(body.get("updateTimestampMs")),
        "orderCount": positive_int(body.get("orderCount")) or 0,
        "bids": bids,
        "asks": asks,
    }


def level_changes(before: dict[float, float], after: dict[float, float]) -> list[dict[str, float]]:
    changes: list[dict[str, float]] = []
    for price in sorted(set(before) | set(after)):
        old = float(before.get(price, 0.0))
        new = float(after.get(price, 0.0))
        delta = new - old
        if abs(delta) <= 1e-9:
            continue
        changes.append({"price": price, "before": old, "after": new, "delta": delta})
    return changes


def native_level_for_target(side: str, price: float, precision: int) -> tuple[str, float]:
    side = str(side).upper()
    if side == "UP":
        return "BID", round(float(price), max(0, int(precision)))
    if side == "DOWN":
        return "ASK", round(1.0 - float(price), max(0, int(precision)))
    raise ValueError(f"unsupported target side: {side}")


def encode_json(value: Any) -> bytes:
    return zlib.compress(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"), level=6)


def decode_json(value: bytes | None) -> Any:
    if not value:
        return None
    return json.loads(zlib.decompress(value).decode("utf-8"))


class MakerBookInferenceCollector:
    """Forward-only public-book recorder and post-fill target Maker matcher."""

    def __init__(self, db_path: Path = DB_PATH, target_db_path: Path = TARGET_DB_PATH) -> None:
        self.asset = ASSET
        self.cohort = COHORT
        self.version = VERSION
        self.api_key = str(os.environ.get(API_KEY_ENV) or "").strip()
        self.db_path = Path(db_path)
        self.target_db_path = Path(target_db_path)
        self.started_at_ms = now_ms()
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.db_lock = threading.RLock()
        self.http = httpx.Client(timeout=httpx.Timeout(2.0, connect=0.5), trust_env=False)
        self.predict_http = httpx.Client(
            timeout=httpx.Timeout(10.0, connect=3.0),
            trust_env=False,
            headers={"x-api-key": self.api_key} if self.api_key else None,
        )
        self.target_initialized_roles: set[tuple[int, str]] = set()
        self.ws: Any = None
        self.ws_generation = 0
        self.ws_status = "CONFIG_REQUIRED" if not self.api_key else "WAITING_MARKET"
        self.ws_error: str | None = None
        self.last_error: str | None = None
        self.current_market_id: int | None = None
        self.current_title: str | None = None
        self.current_precision = 2
        self.current_window_end_ms: int | None = None
        self.last_source_ms: int | None = None
        self.last_received_ms: int | None = None
        self.previous_book: dict[str, dict[float, float]] | None = None
        self.last_checkpoint_ms: int | None = None
        self.updates_written_run = 0
        self.checkpoints_written_run = 0
        self.level_changes_written_run = 0
        self.target_events_seen_run = 0
        self.target_poll_requests_run = {"MAKER": 0, "TAKER": 0}
        self.target_poll_rows_run = {"MAKER": 0, "TAKER": 0}
        self.last_target_poll_ms: int | None = None
        self.last_target_poll_market_id: int | None = None
        self.matches_written_run = 0
        self.unmatched_written_run = 0
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self._create_schema()
        self._initialize_meta()

    def _create_schema(self) -> None:
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS maker_book_inference_meta (
                    cohort TEXT PRIMARY KEY,
                    deployed_at_ms INTEGER NOT NULL,
                    excluded_market_id INTEGER,
                    target_wallet TEXT NOT NULL,
                    last_target_rowid INTEGER NOT NULL,
                    policy_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS maker_book_inference_markets (
                    market_id INTEGER PRIMARY KEY,
                    title TEXT,
                    decimal_precision INTEGER NOT NULL,
                    first_seen_ms INTEGER NOT NULL,
                    window_end_ms INTEGER,
                    status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS maker_book_inference_updates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id INTEGER NOT NULL,
                    source_timestamp_ms INTEGER NOT NULL,
                    received_at_ms INTEGER NOT NULL,
                    order_count INTEGER NOT NULL,
                    is_checkpoint INTEGER NOT NULL,
                    native_bids_z BLOB,
                    native_asks_z BLOB,
                    changes_z BLOB NOT NULL,
                    bid_level_count INTEGER NOT NULL,
                    ask_level_count INTEGER NOT NULL,
                    UNIQUE(market_id,source_timestamp_ms)
                );
                CREATE INDEX IF NOT EXISTS idx_maker_book_updates_market_time
                    ON maker_book_inference_updates(market_id,source_timestamp_ms);
                CREATE TABLE IF NOT EXISTS maker_book_inference_target_events (
                    leg_id TEXT PRIMARY KEY,
                    target_rowid INTEGER NOT NULL,
                    wallet TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    order_hash TEXT,
                    target_event_ms INTEGER NOT NULL,
                    target_observed_ms INTEGER,
                    side TEXT NOT NULL,
                    target_price REAL NOT NULL,
                    target_shares REAL NOT NULL,
                    native_book_side TEXT NOT NULL,
                    native_price REAL NOT NULL,
                    status TEXT NOT NULL,
                    matched_update_id INTEGER,
                    matched_source_ms INTEGER,
                    matched_received_ms INTEGER,
                    before_size REAL,
                    after_size REAL,
                    observed_decrease REAL,
                    event_delay_ms INTEGER,
                    match_confidence REAL,
                    confidence_label TEXT,
                    evidence_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_maker_book_target_market_time
                    ON maker_book_inference_target_events(market_id,target_event_ms,status);
                CREATE TABLE IF NOT EXISTS maker_book_inference_source_legs (
                    source_leg_id TEXT PRIMARY KEY,
                    market_id INTEGER NOT NULL,
                    target_event_id TEXT NOT NULL,
                    observed_at_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_maker_book_source_legs_market
                    ON maker_book_inference_source_legs(market_id,observed_at_ms);
                CREATE TABLE IF NOT EXISTS maker_book_inference_wallet_events (
                    source_leg_id TEXT PRIMARY KEY,
                    wallet TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    quote_type TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_hash TEXT,
                    event_ms INTEGER NOT NULL,
                    observed_at_ms INTEGER NOT NULL,
                    price REAL NOT NULL,
                    shares REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_maker_book_wallet_events_market_time
                    ON maker_book_inference_wallet_events(market_id,event_ms,role);
                """
            )
            self.db.commit()

    def _target_max_rowid(self) -> int:
        if not self.target_db_path.exists():
            return 0
        try:
            con = sqlite3.connect(f"file:{self.target_db_path.resolve()}?mode=ro", uri=True, timeout=5.0)
            try:
                row = con.execute("SELECT COALESCE(MAX(rowid),0) FROM wallet_shadow_target_events").fetchone()
                return int(row[0] or 0)
            finally:
                con.close()
        except sqlite3.Error:
            return 0

    def _initialize_meta(self) -> None:
        policy = {
            "cohort": self.cohort,
            "asset": self.asset,
            "timeframe": "5M",
            "readOnly": True,
            "forwardOnly": True,
            "historicalBackfill": False,
            "targetEventsDriveStrategy": False,
            "liveOrdersAffected": False,
            "source": "public Predict.fun CLOB full-book websocket",
            "storage": "per-update compressed level deltas plus periodic full checkpoints",
            "checkpointMs": CHECKPOINT_MS,
            "retentionHours": RETENTION_HOURS,
            "identityBoundary": "public levels are anonymous; target attribution is probabilistic and only scored after retained target Maker fills",
        }
        with self.db_lock:
            existing = self.db.execute(
                "SELECT 1 FROM maker_book_inference_meta WHERE cohort=?",
                (self.cohort,),
            ).fetchone()
            if existing is None:
                self.db.execute(
                    """INSERT INTO maker_book_inference_meta(
                           cohort,deployed_at_ms,excluded_market_id,target_wallet,last_target_rowid,policy_json
                       ) VALUES (?,?,?,?,?,?)""",
                    (self.cohort, self.started_at_ms, None, TARGET_WALLET, self._target_max_rowid(), json.dumps(policy, separators=(",", ":"))),
                )
            else:
                self.db.execute(
                    "UPDATE maker_book_inference_meta SET policy_json=? WHERE cohort=?",
                    (json.dumps(policy, separators=(",", ":")), self.cohort),
                )
            self.db.commit()

    def start(self) -> None:
        if not self.api_key:
            return
        threading.Thread(target=self._market_loop, name="maker-book-market", daemon=True).start()
        threading.Thread(target=self._target_loop, name="maker-book-target", daemon=True).start()
        threading.Thread(target=self._retention_loop, name="maker-book-retention", daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:
            pass
        self.http.close()
        self.predict_http.close()
        with self.db_lock:
            self.db.commit()
            self.db.close()

    def _meta(self) -> dict[str, Any]:
        with self.db_lock:
            row = self.db.execute(
                "SELECT * FROM maker_book_inference_meta WHERE cohort=?",
                (self.cohort,),
            ).fetchone()
        return dict(row) if row is not None else {}

    def _activate_market(self, market_id: int, *, title: str | None, precision: int, window_end_ms: int | None) -> bool:
        market_id = int(market_id)
        meta = self._meta()
        excluded = positive_int(meta.get("excluded_market_id"))
        if excluded is None:
            with self.db_lock:
                self.db.execute(
                    "UPDATE maker_book_inference_meta SET excluded_market_id=? WHERE cohort=?",
                    (market_id, self.cohort),
                )
                self.db.commit()
            excluded = market_id
        active = market_id != excluded
        with self.lock:
            changed = market_id != self.current_market_id
            self.current_market_id = market_id
            self.current_title = title
            self.current_precision = max(0, min(12, int(precision)))
            self.current_window_end_ms = window_end_ms
            if changed:
                self.previous_book = None
                self.last_checkpoint_ms = None
                self.last_source_ms = None
                self.last_received_ms = None
        if active:
            with self.db_lock:
                self.db.execute(
                    """INSERT INTO maker_book_inference_markets(
                           market_id,title,decimal_precision,first_seen_ms,window_end_ms,status
                       ) VALUES (?,?,?,?,?,'RECORDING')
                       ON CONFLICT(market_id) DO UPDATE SET title=excluded.title,
                           decimal_precision=excluded.decimal_precision,window_end_ms=excluded.window_end_ms,
                           status='RECORDING'""",
                    (market_id, title, self.current_precision, now_ms(), window_end_ms),
                )
                self.db.commit()
        return active

    def _market_loop(self) -> None:
        last_subscribed: int | None = None
        while not self.stop_event.is_set():
            try:
                response = self.http.get(PREDICT_STATE_URL)
                response.raise_for_status()
                payload = response.json()
                state = record(payload.get("state")) if isinstance(payload, dict) and isinstance(payload.get("state"), dict) else record(payload)
                asset_state = record(record(state.get("assets")).get(self.asset))
                market = record(asset_state.get("market"))
                market_id = positive_int(market.get("id"))
                if market_id is None:
                    raise RuntimeError(f"8771 has no current {self.asset} Predict.fun 5m market")
                precision = int(market.get("decimalPrecision") or 2)
                active = self._activate_market(
                    market_id,
                    title=str(market.get("title") or "") or None,
                    precision=precision,
                    window_end_ms=positive_int(market.get("windowEndMs") or asset_state.get("windowEndMs")),
                )
                if active and market_id != last_subscribed:
                    last_subscribed = market_id
                    self._restart_ws(market_id)
                elif not active:
                    self.ws_status = "WAITING_NEXT_COMPLETE_MARKET"
                self.last_error = None
            except Exception as exc:
                self.last_error = f"market: {str(exc)[:350]}"
            self.stop_event.wait(0.5)

    def _restart_ws(self, market_id: int) -> None:
        self.ws_generation += 1
        generation = self.ws_generation
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:
            pass
        threading.Thread(
            target=self._ws_loop,
            args=(generation, int(market_id)),
            name=f"maker-book-ws-{generation}",
            daemon=True,
        ).start()

    def _ws_loop(self, generation: int, market_id: int) -> None:
        if websocket is None:
            self.ws_status = "ERROR"
            self.ws_error = "websocket-client is not installed"
            return
        while not self.stop_event.is_set() and generation == self.ws_generation:
            ws = websocket.WebSocketApp(
                WS_URL,
                header=[f"x-api-key: {self.api_key}"],
                on_open=lambda app: self._ws_open(app, generation, market_id),
                on_message=lambda app, raw: self._ws_message(app, raw, generation, market_id),
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

    def _ws_open(self, ws: Any, generation: int, market_id: int) -> None:
        if generation != self.ws_generation:
            ws.close()
            return
        self.ws_status = "LIVE"
        self.ws_error = None
        ws.send(json.dumps({"method": "subscribe", "requestId": 1, "params": [f"predictOrderbook/{market_id}"]}, separators=(",", ":")))

    def _ws_message(self, ws: Any, raw: str, generation: int, market_id: int) -> None:
        if generation != self.ws_generation:
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        if payload.get("type") == "R":
            if payload.get("success") is False:
                self.ws_error = str(payload.get("error") or "subscription rejected")[:350]
            return
        if payload.get("type") != "M":
            return
        if str(payload.get("topic") or "") == "heartbeat":
            try:
                ws.send(json.dumps({"method": "heartbeat", "data": payload.get("data")}, separators=(",", ":")))
            except Exception:
                pass
            return
        if str(payload.get("topic") or "") != f"predictOrderbook/{market_id}":
            return
        self.process_book_payload(market_id, payload.get("data"), received_ms=now_ms())

    def _ws_error(self, error: Any, generation: int) -> None:
        if generation != self.ws_generation:
            return
        self.ws_status = "RECONNECTING"
        self.ws_error = str(error)[:350]

    def _ws_close(self, code: Any, message: Any, generation: int) -> None:
        if generation == self.ws_generation and not self.stop_event.is_set():
            self._ws_error(f"close {code}: {message}", generation)

    def process_book_payload(self, market_id: int, payload: Any, *, received_ms: int | None = None) -> int | None:
        book = parse_full_book(payload)
        if book is None:
            return None
        received = now_ms() if received_ms is None else int(received_ms)
        source = positive_int(book.get("sourceTimestampMs")) or received
        meta = self._meta()
        if int(market_id) == positive_int(meta.get("excluded_market_id")):
            return None
        with self.lock:
            if self.current_market_id != int(market_id):
                return None
            before = self.previous_book or {"bids": {}, "asks": {}}
            bid_changes = level_changes(before["bids"], book["bids"])
            ask_changes = level_changes(before["asks"], book["asks"])
            checkpoint = self.previous_book is None or self.last_checkpoint_ms is None or received - self.last_checkpoint_ms >= CHECKPOINT_MS
            if not bid_changes and not ask_changes and not checkpoint:
                self.last_source_ms = source
                self.last_received_ms = received
                return None
            self.previous_book = {"bids": dict(book["bids"]), "asks": dict(book["asks"])}
            if checkpoint:
                self.last_checkpoint_ms = received
            self.last_source_ms = source
            self.last_received_ms = received
        changes = {"bids": bid_changes, "asks": ask_changes}
        with self.db_lock:
            cursor = self.db.execute(
                """INSERT OR IGNORE INTO maker_book_inference_updates(
                       market_id,source_timestamp_ms,received_at_ms,order_count,is_checkpoint,
                       native_bids_z,native_asks_z,changes_z,bid_level_count,ask_level_count
                   ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(market_id), source, received, int(book["orderCount"]), int(checkpoint),
                    encode_json(book["bids"]) if checkpoint else None,
                    encode_json(book["asks"]) if checkpoint else None,
                    encode_json(changes), len(book["bids"]), len(book["asks"]),
                ),
            )
            self.db.commit()
            update_id = int(cursor.lastrowid or 0)
        if update_id:
            self.updates_written_run += 1
            self.checkpoints_written_run += int(checkpoint)
            self.level_changes_written_run += len(bid_changes) + len(ask_changes)
            return update_id
        return None

    def _target_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                if self.asset == "ETH":
                    self._poll_current_target_events()
                else:
                    self._ingest_new_target_events()
                self._match_pending_events()
            except Exception as exc:
                self.last_error = f"target: {str(exc)[:350]}"
            self.stop_event.wait(0.5)

    def _poll_current_target_events(self) -> None:
        """Collect ETH Maker and Taker fills without depending on BTC-only 8776."""

        with self.lock:
            market_id = self.current_market_id
        if market_id is None:
            return
        meta = self._meta()
        if int(market_id) == positive_int(meta.get("excluded_market_id")):
            return
        legs: list[dict[str, Any]] = []
        for role in ("MAKER", "TAKER"):
            role_key = (int(market_id), role)
            pages = 4 if role_key not in self.target_initialized_roles else 1
            after: str | None = None
            for _ in range(pages):
                params: dict[str, Any] = {
                    "first": 100,
                    "marketId": int(market_id),
                    "signerAddress": TARGET_WALLET,
                    "isSignerMaker": "true" if role == "MAKER" else "false",
                }
                if after:
                    params["after"] = after
                response = self.predict_http.get(f"{PREDICT_API_BASE}/v1/orders/matches", params=params)
                response.raise_for_status()
                payload = record(response.json())
                if payload.get("success") is False:
                    raise RuntimeError(f"Predict matches rejected for ETH {role} observation")
                rows = [item for item in payload.get("data", []) if isinstance(item, dict)] if isinstance(payload.get("data"), list) else []
                self.target_poll_requests_run[role] += 1
                self.target_poll_rows_run[role] += len(rows)
                self.last_target_poll_ms = now_ms()
                self.last_target_poll_market_id = int(market_id)
                for raw in rows:
                    participant_count = len(raw.get("makers") or []) if role == "MAKER" else 1
                    for index in range(participant_count):
                        leg = normalize_match_leg(
                            raw,
                            wallet=TARGET_WALLET,
                            role=role,
                            maker_index=index if role == "MAKER" else None,
                        )
                        if leg is not None and int(leg["marketId"]) == int(market_id):
                            legs.append(leg)
                cursor = str(payload.get("cursor") or "").strip() or None
                if not cursor or not rows:
                    break
                after = cursor
            self.target_initialized_roles.add(role_key)
        self._persist_direct_target_legs(legs, observed_ms=now_ms())

    def _persist_direct_target_legs(self, legs: list[dict[str, Any]], *, observed_ms: int) -> int:
        """Persist all wallet activity once; only Maker BID legs enter book matching."""

        meta = self._meta()
        deployed_at = int(meta.get("deployed_at_ms") or self.started_at_ms)
        excluded = positive_int(meta.get("excluded_market_id"))
        with self.db_lock:
            precision_by_market = {
                int(row["market_id"]): int(row["decimal_precision"])
                for row in self.db.execute("SELECT market_id,decimal_precision FROM maker_book_inference_markets")
            }
            known = {
                str(row[0])
                for row in self.db.execute(
                    "SELECT source_leg_id FROM maker_book_inference_wallet_events WHERE market_id=?",
                    (int(self.current_market_id or 0),),
                )
            }
        grouped: dict[tuple[int, str, int, str, float], dict[str, Any]] = {}
        accepted: list[tuple[Any, ...]] = []
        maker_sources: list[tuple[str, int, str, int]] = []
        for leg in legs:
            source_leg_id = str(leg.get("legId") or "")
            market_id = int(leg.get("marketId") or 0)
            event_ms = int(leg.get("eventMs") or 0)
            side = str(leg.get("side") or "").upper()
            role = str(leg.get("role") or "").upper()
            quote_type = str(leg.get("quoteType") or "").upper()
            price = finite(leg.get("price"))
            shares = finite(leg.get("shares"))
            if (
                not source_leg_id
                or source_leg_id in known
                or market_id not in precision_by_market
                or market_id == excluded
                or event_ms < deployed_at
                or side not in {"UP", "DOWN"}
                or role not in {"MAKER", "TAKER"}
                or quote_type not in {"BID", "ASK"}
                or price is None
                or shares is None
                or shares <= 0
            ):
                continue
            known.add(source_leg_id)
            accepted.append((
                source_leg_id, TARGET_WALLET, market_id, role, quote_type, side,
                leg.get("orderHash"), event_ms, int(observed_ms), price, shares,
            ))
            if role != "MAKER" or quote_type != "BID":
                continue
            parent = str(leg.get("orderHash") or leg.get("parentId") or source_leg_id)
            key = (market_id, parent, event_ms, side, price)
            group = grouped.setdefault(key, {"shares": 0.0, "orderHash": leg.get("orderHash")})
            group["shares"] = float(group["shares"]) + shares
            event_id = f"{parent}:{event_ms}:{side}:{price:.12g}"
            maker_sources.append((source_leg_id, market_id, event_id, int(observed_ms)))
        if not accepted:
            return 0
        inserts: list[tuple[Any, ...]] = []
        for (market_id, parent, event_ms, side, price), group in grouped.items():
            native_side, native_price = native_level_for_target(side, price, precision_by_market[market_id])
            event_id = f"{parent}:{event_ms}:{side}:{price:.12g}"
            inserts.append((
                event_id, 0, TARGET_WALLET, market_id, group["orderHash"], event_ms, int(observed_ms),
                side, price, float(group["shares"]), native_side, native_price, "PENDING",
            ))
        with self.db_lock:
            self.db.executemany(
                """INSERT OR IGNORE INTO maker_book_inference_wallet_events(
                       source_leg_id,wallet,market_id,role,quote_type,side,order_hash,
                       event_ms,observed_at_ms,price,shares
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                accepted,
            )
            self.db.executemany(
                "INSERT OR IGNORE INTO maker_book_inference_source_legs(source_leg_id,market_id,target_event_id,observed_at_ms) VALUES (?,?,?,?)",
                maker_sources,
            )
            self.db.executemany(
                """INSERT INTO maker_book_inference_target_events(
                       leg_id,target_rowid,wallet,market_id,order_hash,target_event_ms,target_observed_ms,
                       side,target_price,target_shares,native_book_side,native_price,status
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(leg_id) DO UPDATE SET
                       target_observed_ms=MAX(target_observed_ms,excluded.target_observed_ms),
                       target_shares=target_shares+excluded.target_shares,
                       status='PENDING'""",
                inserts,
            )
            self.db.commit()
        self.target_events_seen_run += len(accepted)
        return len(accepted)

    def _ingest_new_target_events(self) -> None:
        if not self.target_db_path.exists():
            return
        meta = self._meta()
        cursor_rowid = int(meta.get("last_target_rowid") or 0)
        deployed_at = int(meta.get("deployed_at_ms") or self.started_at_ms)
        excluded = positive_int(meta.get("excluded_market_id"))
        con = sqlite3.connect(f"file:{self.target_db_path.resolve()}?mode=ro", uri=True, timeout=5.0)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                """SELECT rowid target_rowid,leg_id,wallet,market_id,order_hash,event_ms,
                          role,quote_type,side,price,shares
                     FROM wallet_shadow_target_events
                    WHERE rowid>? ORDER BY rowid LIMIT 2000""",
                (cursor_rowid,),
            ).fetchall()
            if not rows:
                return
            context: dict[str, int] = {}
            leg_ids = [str(row["leg_id"]) for row in rows]
            for start in range(0, len(leg_ids), 500):
                batch = leg_ids[start:start + 500]
                placeholders = ",".join("?" for _ in batch)
                try:
                    context_rows = con.execute(
                        f"SELECT leg_id,observed_at_ms FROM wallet_shadow_target_event_context WHERE leg_id IN ({placeholders})",
                        batch,
                    )
                    for row in context_rows:
                        if row["observed_at_ms"] is not None:
                            context[str(row["leg_id"])] = int(row["observed_at_ms"])
                except sqlite3.OperationalError:
                    break
        finally:
            con.close()
        grouped: dict[tuple[int, str, int, str, float], dict[str, Any]] = {}
        maximum_rowid = cursor_rowid
        precision_by_market: dict[int, int] = {}
        with self.db_lock:
            for row in self.db.execute("SELECT market_id,decimal_precision FROM maker_book_inference_markets"):
                precision_by_market[int(row["market_id"])] = int(row["decimal_precision"])
        for row in rows:
            maximum_rowid = max(maximum_rowid, int(row["target_rowid"]))
            if (
                str(row["wallet"] or "").lower() != TARGET_WALLET
                or int(row["event_ms"] or 0) < deployed_at
                or int(row["market_id"] or 0) == excluded
                or str(row["role"] or "").upper() != "MAKER"
                or str(row["quote_type"] or "").upper() != "BID"
                or str(row["side"] or "").upper() not in {"UP", "DOWN"}
            ):
                continue
            market_id = int(row["market_id"])
            if market_id not in precision_by_market:
                continue
            side = str(row["side"]).upper()
            price = float(row["price"])
            parent = str(row["order_hash"] or row["leg_id"])
            key = (market_id, parent, int(row["event_ms"]), side, price)
            group = grouped.setdefault(key, {
                "targetRowId": int(row["target_rowid"]),
                "orderHash": str(row["order_hash"] or "") or None,
                "observedMs": context.get(str(row["leg_id"])),
                "shares": 0.0,
            })
            group["targetRowId"] = max(int(group["targetRowId"]), int(row["target_rowid"]))
            observed = context.get(str(row["leg_id"]))
            if observed is not None:
                group["observedMs"] = max(int(group.get("observedMs") or 0), observed)
            group["shares"] = float(group["shares"]) + float(row["shares"])
        inserts: list[tuple[Any, ...]] = []
        for (market_id, parent, event_ms, side, price), group in grouped.items():
            native_side, native_price = native_level_for_target(side, price, precision_by_market[market_id])
            event_id = f"{parent}:{event_ms}:{side}:{price:.12g}"
            inserts.append((
                event_id, int(group["targetRowId"]), TARGET_WALLET, market_id,
                group["orderHash"], event_ms, group["observedMs"],
                side, price, float(group["shares"]), native_side, native_price, "PENDING",
            ))
        with self.db_lock:
            if inserts:
                self.db.executemany(
                    """INSERT INTO maker_book_inference_target_events(
                           leg_id,target_rowid,wallet,market_id,order_hash,target_event_ms,target_observed_ms,
                           side,target_price,target_shares,native_book_side,native_price,status
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(leg_id) DO UPDATE SET
                           target_rowid=MAX(target_rowid,excluded.target_rowid),
                           target_observed_ms=CASE
                               WHEN target_observed_ms IS NULL THEN excluded.target_observed_ms
                               WHEN excluded.target_observed_ms IS NULL THEN target_observed_ms
                               ELSE MAX(target_observed_ms,excluded.target_observed_ms)
                           END,
                           target_shares=target_shares+excluded.target_shares""",
                    inserts,
                )
            self.db.execute(
                "UPDATE maker_book_inference_meta SET last_target_rowid=? WHERE cohort=?",
                (maximum_rowid, self.cohort),
            )
            self.db.commit()
        self.target_events_seen_run += len(inserts)

    def _match_pending_events(self) -> None:
        cutoff = now_ms() - 3_000
        with self.db_lock:
            pending = [dict(row) for row in self.db.execute(
                """SELECT * FROM maker_book_inference_target_events
                    WHERE status='PENDING' AND COALESCE(target_observed_ms,target_event_ms)<=?
                    ORDER BY target_event_ms LIMIT 500""",
                (cutoff,),
            )]
        for event in pending:
            refs = [int(event["target_event_ms"])]
            if event.get("target_observed_ms") is not None:
                refs.append(int(event["target_observed_ms"]))
            start_ms = min(refs) - 1_500
            end_ms = max(refs) + 3_000
            with self.db_lock:
                updates = [dict(row) for row in self.db.execute(
                    """SELECT id,source_timestamp_ms,received_at_ms,changes_z
                         FROM maker_book_inference_updates
                        WHERE market_id=? AND source_timestamp_ms BETWEEN ? AND ?
                        ORDER BY source_timestamp_ms""",
                    (int(event["market_id"]), start_ms, end_ms),
                )]
            candidates: list[dict[str, Any]] = []
            wanted_key = "bids" if event["native_book_side"] == "BID" else "asks"
            target_price = float(event["native_price"])
            target_shares = max(1e-9, float(event["target_shares"]))
            for update in updates:
                changes = record(decode_json(update["changes_z"] or b""))
                for change in changes.get(wanted_key, []) if isinstance(changes.get(wanted_key), list) else []:
                    if abs(float(change.get("price", -1)) - target_price) > 1e-9:
                        continue
                    delta = float(change.get("delta", 0.0))
                    if delta >= -1e-9:
                        continue
                    decrease = -delta
                    nearest_delay = min(abs(int(update["source_timestamp_ms"]) - ref) for ref in refs)
                    size_score = min(decrease, target_shares) / max(decrease, target_shares)
                    time_score = max(0.0, 1.0 - nearest_delay / 3_000.0)
                    before = float(change.get("before", 0.0))
                    confidence = min(1.0, 0.60 * size_score + 0.35 * time_score + (0.05 if before + 1e-9 >= target_shares else 0.0))
                    candidates.append({
                        "update": update,
                        "change": change,
                        "decrease": decrease,
                        "delay": nearest_delay,
                        "confidence": confidence,
                    })
            if candidates:
                best = max(candidates, key=lambda row: (row["confidence"], -row["delay"]))
                label = "HIGH" if best["confidence"] >= 0.75 else "MEDIUM" if best["confidence"] >= 0.45 else "LOW"
                evidence = {
                    "basis": "anonymous public level decrease near retained target Maker BID fill",
                    "candidateCount": len(candidates),
                    "identityIsProbabilistic": True,
                    "nativeMapping": {"side": event["native_book_side"], "price": target_price},
                }
                with self.db_lock:
                    self.db.execute(
                        """UPDATE maker_book_inference_target_events SET
                               status='MATCHED',matched_update_id=?,matched_source_ms=?,matched_received_ms=?,
                               before_size=?,after_size=?,observed_decrease=?,event_delay_ms=?,match_confidence=?,
                               confidence_label=?,evidence_json=? WHERE leg_id=?""",
                        (
                            int(best["update"]["id"]), int(best["update"]["source_timestamp_ms"]),
                            int(best["update"]["received_at_ms"]), float(best["change"]["before"]),
                            float(best["change"]["after"]), float(best["decrease"]), int(best["delay"]),
                            float(best["confidence"]), label, json.dumps(evidence, separators=(",", ":")),
                            event["leg_id"],
                        ),
                    )
                    self.db.commit()
                self.matches_written_run += 1
            else:
                evidence = {
                    "basis": "no same-price negative public level delta in the matching window",
                    "identityIsProbabilistic": True,
                    "possibleCauses": ["polling gap", "aggregate-level netting", "other participant activity", "event timestamp skew"],
                }
                with self.db_lock:
                    self.db.execute(
                        """UPDATE maker_book_inference_target_events SET status='UNMATCHED',
                               confidence_label='NONE',match_confidence=0,evidence_json=? WHERE leg_id=?""",
                        (json.dumps(evidence, separators=(",", ":")), event["leg_id"]),
                    )
                    self.db.commit()
                self.unmatched_written_run += 1

    def _retention_loop(self) -> None:
        while not self.stop_event.wait(60.0):
            cutoff = now_ms() - int(RETENTION_HOURS * 3_600_000)
            try:
                with self.db_lock:
                    self.db.execute("DELETE FROM maker_book_inference_wallet_events WHERE event_ms<?", (cutoff,))
                    self.db.execute("DELETE FROM maker_book_inference_source_legs WHERE observed_at_ms<?", (cutoff,))
                    self.db.execute("DELETE FROM maker_book_inference_target_events WHERE target_event_ms<?", (cutoff,))
                    self.db.execute("DELETE FROM maker_book_inference_updates WHERE received_at_ms<?", (cutoff,))
                    self.db.execute(
                        """DELETE FROM maker_book_inference_markets WHERE first_seen_ms<?
                             AND market_id NOT IN (SELECT DISTINCT market_id FROM maker_book_inference_updates)""",
                        (cutoff,),
                    )
                    self.db.commit()
            except sqlite3.Error as exc:
                self.last_error = f"retention: {str(exc)[:350]}"

    def snapshot(self) -> dict[str, Any]:
        meta = self._meta()
        with self.db_lock:
            totals = dict(self.db.execute(
                """SELECT COUNT(*) updates,COALESCE(SUM(is_checkpoint),0) checkpoints,
                          COUNT(DISTINCT market_id) markets,MAX(received_at_ms) latest_received_ms
                     FROM maker_book_inference_updates"""
            ).fetchone())
            target = dict(self.db.execute(
                """SELECT COUNT(*) target_events,
                          COALESCE(SUM(status='MATCHED'),0) matched,
                          COALESCE(SUM(status='UNMATCHED'),0) unmatched,
                          COALESCE(SUM(status='PENDING'),0) pending,
                          AVG(CASE WHEN status='MATCHED' THEN match_confidence END) average_confidence,
                          COALESCE(SUM(confidence_label='HIGH'),0) high_confidence
                     FROM maker_book_inference_target_events"""
            ).fetchone())
            recent = [dict(row) for row in self.db.execute(
                """SELECT leg_id,market_id,order_hash,target_event_ms,side,target_price,target_shares,
                          native_book_side,native_price,status,observed_decrease,event_delay_ms,
                          match_confidence,confidence_label
                     FROM maker_book_inference_target_events ORDER BY target_event_ms DESC LIMIT 20"""
            )]
            activity = dict(self.db.execute(
                """SELECT COUNT(*) total_events,
                          COALESCE(SUM(role='MAKER'),0) maker_events,
                          COALESCE(SUM(role='TAKER'),0) taker_events,
                          COALESCE(SUM(CASE WHEN role='MAKER' THEN shares ELSE 0 END),0) maker_shares,
                          COALESCE(SUM(CASE WHEN role='TAKER' THEN shares ELSE 0 END),0) taker_shares,
                          MAX(event_ms) latest_event_ms
                     FROM maker_book_inference_wallet_events"""
            ).fetchone())
            recent_activity = [dict(row) for row in self.db.execute(
                """SELECT source_leg_id,market_id,role,quote_type,side,order_hash,event_ms,
                          observed_at_ms,price,shares
                     FROM maker_book_inference_wallet_events ORDER BY event_ms DESC LIMIT 30"""
            )]
        current = self.current_market_id
        excluded = positive_int(meta.get("excluded_market_id"))
        latest_received = positive_int(totals.get("latest_received_ms"))
        return {
            "version": self.version,
            "cohort": self.cohort,
            "asset": self.asset,
            "timeframe": "5M",
            "status": (
                "CONFIG_REQUIRED" if not self.api_key else
                "WAITING_NEXT_COMPLETE_MARKET" if current is not None and current == excluded else
                "LIVE" if self.ws_status == "LIVE" and current is not None and self.last_error is None else
                "DEGRADED" if current is not None else "WAITING_MARKET"
            ),
            "readOnly": True,
            "paperOnly": True,
            "forwardOnly": True,
            "historicalBackfill": False,
            "targetEventsDriveStrategy": False,
            "ordersSupported": False,
            "liveOrdersAffected": False,
            "targetWallet": TARGET_WALLET,
            "deployedAtMs": positive_int(meta.get("deployed_at_ms")),
            "excludedDeploymentMarketId": excluded,
            "current": {
                "marketId": current,
                "title": self.current_title,
                "decimalPrecision": self.current_precision,
                "windowEndMs": self.current_window_end_ms,
                "phase": "WAITING_NEXT_COMPLETE_MARKET" if current == excluded else "RECORDING",
                "lastSourceMs": self.last_source_ms,
                "lastReceivedMs": self.last_received_ms,
                "sampleAgeMs": now_ms() - self.last_received_ms if self.last_received_ms else None,
            },
            "websocket": {"status": self.ws_status, "generation": self.ws_generation, "error": self.ws_error},
            "storage": {
                **totals,
                "latestAgeMs": now_ms() - latest_received if latest_received else None,
                "database": str(self.db_path),
                "databaseBytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
                "retentionHours": RETENTION_HOURS,
                "checkpointMs": CHECKPOINT_MS,
                "updatesWrittenThisRun": self.updates_written_run,
                "checkpointsWrittenThisRun": self.checkpoints_written_run,
                "levelChangesWrittenThisRun": self.level_changes_written_run,
            },
            "targetInference": {
                **target,
                "eventsSeenThisRun": self.target_events_seen_run,
                "matchesWrittenThisRun": self.matches_written_run,
                "unmatchedWrittenThisRun": self.unmatched_written_run,
                "identityBoundary": "MATCHED means a same-price aggregate level decrease, not proof that an anonymous resting order belonged to the target wallet",
                "recent": recent,
            },
            "targetActivity": {
                **activity,
                "latestAgeMs": now_ms() - int(activity["latest_event_ms"]) if activity.get("latest_event_ms") else None,
                "scope": "forward-only target wallet Maker and Taker fills for this asset/timeframe",
                "makerMatchingBoundary": "only Maker BID fills are eligible for anonymous public-book decrease matching",
                "pollDiagnostics": {
                    "lastMarketId": self.last_target_poll_market_id,
                    "lastPollMs": self.last_target_poll_ms,
                    "lastPollAgeMs": now_ms() - self.last_target_poll_ms if self.last_target_poll_ms else None,
                    "requestsThisRun": dict(self.target_poll_requests_run),
                    "rowsReturnedThisRun": dict(self.target_poll_rows_run),
                },
                "recent": recent_activity,
            },
            "lastError": self.last_error,
            "checkedAtMs": now_ms(),
        }


class Handler(BaseHTTPRequestHandler):
    collector: MakerBookInferenceCollector

    def log_message(self, *_args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] not in {"/", "/state", "/health", "/api/state"}:
            self._send(404, {"ok": False, "error": "not found"})
            return
        self._send(200, {"ok": True, "state": self.collector.snapshot()})

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    collector = MakerBookInferenceCollector(DB_PATH, TARGET_DB_PATH)
    collector.start()
    handler = type("MakerBookInferenceHandler", (Handler,), {"collector": collector})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}/state; asset={ASSET}; compressed full-book deltas; "
        "forwardOnly=true; readOnly=true; liveOrdersAffected=false",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        collector.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
