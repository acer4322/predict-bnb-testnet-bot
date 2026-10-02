from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sqlite3
import threading
import time
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

import httpx
import websocket
from eth_account import Account
from eth_account.messages import encode_defunct
from predict_sdk import ChainId, OrderBuilder, OrderBuilderOptions

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "predict_own_wallet_lifecycle_v1.db"
DEFAULT_HOST = os.environ.get("PREDICT_OWN_WALLET_LIFECYCLE_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("PREDICT_OWN_WALLET_LIFECYCLE_PORT", "8795"))
WS_URL = os.environ.get("PREDICT_FUN_WS_URL", "wss://ws.predict.fun/ws")
VERSION = "PREDICT_OWN_WALLET_LIFECYCLE_COLLECTOR_V1"
EVENT_TYPES = {
    "orderAccepted",
    "orderNotAccepted",
    "orderExpired",
    "orderCancelled",
    "orderTransactionSubmitted",
    "orderTransactionSuccess",
    "orderTransactionFailed",
}


def now_ms() -> int:
    return int(time.time() * 1000)


def record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def text_value(value: Any) -> str | None:
    value = str(value or "").strip()
    return value or None


def int_value(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def float_value(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def encode_raw(value: Any) -> bytes:
    return zlib.compress(canonical_json(value).encode("utf-8"), level=6)


def event_key(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def safe_error(exc: Any, jwt: str | None = None) -> str:
    value = str(exc)
    if jwt:
        value = value.replace(jwt, "<redacted-jwt>")
    return value[:500]


class AuthExpired(RuntimeError):
    pass


class ReadOnlyPredictAuthClient:
    """Narrow challenge/JWT and authenticated-GET client; no order mutation methods exist."""

    _ALLOWED_REQUESTS = {
        ("GET", "/v1/auth/message"),
        ("POST", "/v1/auth"),
        ("GET", "/v1/orders"),
    }

    def __init__(
        self,
        api_key: str,
        *,
        jwt: str | None,
        auth_signer: str,
        sign_auth_message: Callable[[str], str],
    ) -> None:
        self.api_key = str(api_key)
        self.jwt = str(jwt or "").strip() or None
        self.auth_signer = str(auth_signer)
        self.sign_auth_message = sign_auth_message
        self.http = httpx.Client(
            base_url=os.environ.get("PREDICT_FUN_API_BASE", "https://api.predict.fun").rstrip("/"),
            timeout=httpx.Timeout(8.0, connect=3.0),
            verify=True,
            trust_env=False,
            follow_redirects=True,
            headers={"Accept": "application/json", "User-Agent": "BTC-5M-Lab-Own-Lifecycle-V1/1.0"},
        )
        self.auth_lock = threading.RLock()

    def close(self) -> None:
        self.http.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        auth: bool = False,
        retry_auth: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        method = str(method).upper()
        if (method, path) not in self._ALLOWED_REQUESTS:
            raise PermissionError(f"read-only Predict client refuses {method} {path}")
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["x-api-key"] = self.api_key
        if auth:
            if not self.jwt:
                self.authenticate()
            headers["Authorization"] = f"Bearer {self.jwt}"
        response = self.http.request(method, path, headers=headers, **kwargs)
        if response.status_code == 401 and auth and retry_auth:
            self.authenticate(force=True)
            return self.request(method, path, auth=True, retry_auth=False, **kwargs)
        try:
            payload = response.json()
        except Exception:
            payload = {"raw": response.text[:500]}
        if response.status_code >= 400 or not isinstance(payload, dict) or payload.get("success") is False:
            raise RuntimeError(f"Predict {method} {path} HTTP {response.status_code}: {str(payload)[:500]}")
        return payload

    def authenticate(self, *, force: bool = False) -> str:
        with self.auth_lock:
            if self.jwt and not force:
                return self.jwt
            challenge = self.request("GET", "/v1/auth/message")
            data = record(challenge.get("data"))
            message = str(data.get("message") or "")
            if not message:
                raise RuntimeError("Predict auth message response contained no message")
            token_payload = self.request(
                "POST",
                "/v1/auth",
                json={
                    "signer": self.auth_signer,
                    "signature": self.sign_auth_message(message),
                    "message": message,
                },
            )
            token = str(record(token_payload.get("data")).get("token") or "").strip()
            if not token:
                raise RuntimeError("Predict auth response contained no JWT token")
            self.jwt = token
            return token


class LifecycleStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self._create_schema()

    def _create_schema(self) -> None:
        with self.lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS own_wallet_lifecycle_sessions_v1 (
                    session_id TEXT PRIMARY KEY,
                    started_at_ms INTEGER NOT NULL,
                    connected_at_ms INTEGER,
                    subscribed_at_ms INTEGER,
                    disconnected_at_ms INTEGER,
                    wallet_address TEXT NOT NULL,
                    ws_endpoint TEXT NOT NULL,
                    close_code INTEGER,
                    close_reason TEXT,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS own_wallet_lifecycle_events_v1 (
                    event_key TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    received_at_ms INTEGER NOT NULL,
                    source_timestamp_ms INTEGER,
                    source_receive_latency_ms INTEGER,
                    event_type TEXT NOT NULL,
                    order_id TEXT,
                    order_hash TEXT,
                    wallet_address TEXT,
                    market_id INTEGER,
                    outcome_index INTEGER,
                    outcome TEXT,
                    quote_type TEXT,
                    quantity REAL,
                    quantity_filled REAL,
                    price REAL,
                    value REAL,
                    value_filled REAL,
                    strategy_type TEXT,
                    reason TEXT,
                    transaction_kind TEXT,
                    settlement_id TEXT,
                    fill_executed_price_wei TEXT,
                    fill_executed_size_wei TEXT,
                    fill_executed_value_wei TEXT,
                    is_maker INTEGER,
                    fee_amount_wei TEXT,
                    fee_type TEXT,
                    raw_json_z BLOB NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES own_wallet_lifecycle_sessions_v1(session_id)
                );
                CREATE INDEX IF NOT EXISTS idx_own_wallet_events_market_time
                    ON own_wallet_lifecycle_events_v1(market_id,source_timestamp_ms,received_at_ms);
                CREATE INDEX IF NOT EXISTS idx_own_wallet_events_order_hash
                    ON own_wallet_lifecycle_events_v1(order_hash,source_timestamp_ms);
                CREATE INDEX IF NOT EXISTS idx_own_wallet_events_order_id
                    ON own_wallet_lifecycle_events_v1(order_id,source_timestamp_ms);
                CREATE INDEX IF NOT EXISTS idx_own_wallet_events_settlement
                    ON own_wallet_lifecycle_events_v1(settlement_id,event_type);
                CREATE TABLE IF NOT EXISTS own_wallet_open_snapshot_runs_v1 (
                    snapshot_run_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    observed_at_ms INTEGER NOT NULL,
                    open_order_count INTEGER NOT NULL,
                    rest_status TEXT NOT NULL,
                    error TEXT,
                    FOREIGN KEY(session_id) REFERENCES own_wallet_lifecycle_sessions_v1(session_id)
                );
                CREATE TABLE IF NOT EXISTS own_wallet_open_order_snapshots_v1 (
                    snapshot_key TEXT PRIMARY KEY,
                    snapshot_run_id TEXT NOT NULL,
                    observed_at_ms INTEGER NOT NULL,
                    order_id TEXT,
                    order_hash TEXT,
                    market_id INTEGER,
                    status TEXT,
                    quantity REAL,
                    quantity_filled REAL,
                    price REAL,
                    raw_json_z BLOB NOT NULL,
                    FOREIGN KEY(snapshot_run_id) REFERENCES own_wallet_open_snapshot_runs_v1(snapshot_run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_own_wallet_open_snapshot_order
                    ON own_wallet_open_order_snapshots_v1(order_hash,observed_at_ms);
                """
            )
            self.db.commit()

    def start_session(self, session_id: str, wallet_address: str) -> None:
        with self.lock:
            self.db.execute(
                """INSERT INTO own_wallet_lifecycle_sessions_v1(
                       session_id,started_at_ms,wallet_address,ws_endpoint
                   ) VALUES (?,?,?,?)""",
                (session_id, now_ms(), wallet_address, WS_URL),
            )
            self.db.commit()

    def mark_connected(self, session_id: str) -> None:
        with self.lock:
            self.db.execute(
                "UPDATE own_wallet_lifecycle_sessions_v1 SET connected_at_ms=? WHERE session_id=?",
                (now_ms(), session_id),
            )
            self.db.commit()

    def mark_subscribed(self, session_id: str) -> None:
        with self.lock:
            self.db.execute(
                "UPDATE own_wallet_lifecycle_sessions_v1 SET subscribed_at_ms=? WHERE session_id=?",
                (now_ms(), session_id),
            )
            self.db.commit()

    def finish_session(
        self,
        session_id: str,
        *,
        close_code: int | None = None,
        close_reason: str | None = None,
        error: str | None = None,
    ) -> None:
        with self.lock:
            self.db.execute(
                """UPDATE own_wallet_lifecycle_sessions_v1
                      SET disconnected_at_ms=?,close_code=?,close_reason=?,error=?
                    WHERE session_id=?""",
                (now_ms(), close_code, close_reason, error, session_id),
            )
            self.db.commit()

    def store_event(self, session_id: str, payload: dict[str, Any], received_at_ms: int) -> bool:
        details = record(payload.get("details"))
        fill = record(payload.get("fill"))
        fee = record(payload.get("fee"))
        event_type = str(payload.get("type") or "")
        timestamp = int_value(payload.get("timestamp"))
        key = event_key(payload)
        with self.lock:
            cursor = self.db.execute(
                """INSERT OR IGNORE INTO own_wallet_lifecycle_events_v1(
                       event_key,session_id,received_at_ms,source_timestamp_ms,source_receive_latency_ms,
                       event_type,order_id,order_hash,wallet_address,market_id,outcome_index,outcome,
                       quote_type,quantity,quantity_filled,price,value,value_filled,strategy_type,reason,
                       transaction_kind,settlement_id,fill_executed_price_wei,fill_executed_size_wei,
                       fill_executed_value_wei,is_maker,fee_amount_wei,fee_type,raw_json_z
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    key,
                    session_id,
                    received_at_ms,
                    timestamp,
                    received_at_ms - timestamp if timestamp is not None else None,
                    event_type,
                    text_value(payload.get("orderId")),
                    text_value(payload.get("orderHash")),
                    text_value(payload.get("walletAddress")),
                    int_value(details.get("marketId")),
                    int_value(details.get("outcomeIndex")),
                    text_value(details.get("outcome")),
                    text_value(details.get("quoteType")),
                    float_value(details.get("quantity")),
                    float_value(details.get("quantityFilled")),
                    float_value(details.get("price")),
                    float_value(details.get("value")),
                    float_value(details.get("valueFilled")),
                    text_value(details.get("strategyType")),
                    text_value(payload.get("reason")),
                    text_value(payload.get("kind")),
                    text_value(payload.get("settlementId")),
                    text_value(fill.get("executedPriceWei")),
                    text_value(fill.get("executedSizeWei")),
                    text_value(fill.get("executedValueWei")),
                    int(bool(payload.get("isMaker"))) if payload.get("isMaker") is not None else None,
                    text_value(fee.get("amountWei")),
                    text_value(fee.get("type")),
                    encode_raw(payload),
                ),
            )
            self.db.commit()
            return cursor.rowcount == 1

    def store_open_snapshot(
        self,
        session_id: str,
        orders: list[dict[str, Any]],
        *,
        rest_status: str,
        error: str | None = None,
    ) -> str:
        observed = now_ms()
        run_id = str(uuid.uuid4())
        with self.lock:
            self.db.execute(
                """INSERT INTO own_wallet_open_snapshot_runs_v1(
                       snapshot_run_id,session_id,observed_at_ms,open_order_count,rest_status,error
                   ) VALUES (?,?,?,?,?,?)""",
                (run_id, session_id, observed, len(orders), rest_status, error),
            )
            for order in orders:
                details = record(order.get("details"))
                order_id = text_value(order.get("id") or order.get("orderId"))
                order_hash = text_value(order.get("hash") or order.get("orderHash"))
                market_id = int_value(order.get("marketId") or details.get("marketId"))
                status = text_value(order.get("status"))
                quantity = float_value(order.get("quantity") or details.get("quantity"))
                quantity_filled = float_value(order.get("quantityFilled") or details.get("quantityFilled"))
                price = float_value(order.get("price") or details.get("price"))
                key = hashlib.sha256(
                    f"{run_id}:{order_id}:{order_hash}:{canonical_json(order)}".encode("utf-8")
                ).hexdigest()
                self.db.execute(
                    """INSERT INTO own_wallet_open_order_snapshots_v1(
                           snapshot_key,snapshot_run_id,observed_at_ms,order_id,order_hash,market_id,
                           status,quantity,quantity_filled,price,raw_json_z
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        key,
                        run_id,
                        observed,
                        order_id,
                        order_hash,
                        market_id,
                        status,
                        quantity,
                        quantity_filled,
                        price,
                        encode_raw(order),
                    ),
                )
            self.db.commit()
        return run_id

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            totals = dict(
                self.db.execute(
                    """SELECT COUNT(*) events,
                              COUNT(DISTINCT COALESCE(order_hash,order_id)) orders,
                              COUNT(DISTINCT market_id) markets,
                              COALESCE(SUM(event_type='orderAccepted'),0) accepted,
                              COALESCE(SUM(event_type='orderNotAccepted'),0) rejected,
                              COALESCE(SUM(event_type='orderCancelled'),0) cancelled,
                              COALESCE(SUM(event_type='orderExpired'),0) expired,
                              COALESCE(SUM(event_type='orderTransactionSubmitted'),0) transaction_submitted,
                              COALESCE(SUM(event_type='orderTransactionSuccess'),0) transaction_success,
                              COALESCE(SUM(event_type='orderTransactionFailed'),0) transaction_failed,
                              MAX(received_at_ms) latest_received_at_ms
                         FROM own_wallet_lifecycle_events_v1"""
                ).fetchone()
            )
            sessions = dict(
                self.db.execute(
                    """SELECT COUNT(*) sessions,
                              COALESCE(SUM(subscribed_at_ms IS NOT NULL),0) subscribed_sessions,
                              MAX(subscribed_at_ms) latest_subscribed_at_ms
                         FROM own_wallet_lifecycle_sessions_v1"""
                ).fetchone()
            )
            bootstrap = dict(
                self.db.execute(
                    """SELECT COUNT(*) snapshot_runs,
                              COALESCE(SUM(open_order_count),0) open_order_observations,
                              MAX(observed_at_ms) latest_snapshot_at_ms
                         FROM own_wallet_open_snapshot_runs_v1"""
                ).fetchone()
            )
        return {**totals, **sessions, **bootstrap}

    def close(self) -> None:
        with self.lock:
            self.db.close()


class OwnWalletLifecycleCollector:
    def __init__(self, db_path: Path) -> None:
        self.store = LifecycleStore(db_path)
        self.stop_event = threading.Event()
        self.client: ReadOnlyPredictAuthClient | None = None
        self.ws: websocket.WebSocket | None = None
        self.wallet_address: str | None = None
        self.jwt: str | None = None
        self.status = "INITIALIZING"
        self.last_error: str | None = None
        self.connected_at_ms: int | None = None
        self.subscribed_at_ms: int | None = None
        self.last_message_at_ms: int | None = None
        self.last_heartbeat_at_ms: int | None = None
        self.last_event_at_ms: int | None = None
        self.inserted_events = 0
        self.duplicate_events = 0
        self.reconnects = 0
        self.current_session_id: str | None = None
        self._configure_auth()

    def _configure_auth(self) -> None:
        api_key = str(os.environ.get("PREDICT_FUN_API_KEY") or "").strip()
        private_key = str(
            os.environ.get("PREDICT_FUN_PRIVATE_KEY")
            or os.environ.get("PREDICT_FUN_PRIVY_PRIVATE_KEY")
            or ""
        ).strip()
        predict_account = str(os.environ.get("PREDICT_FUN_ACCOUNT_ADDRESS") or "").strip()
        static_jwt = str(os.environ.get("PREDICT_FUN_JWT") or "").strip() or None
        if not api_key or not private_key:
            raise RuntimeError(
                "PREDICT_FUN_API_KEY and PREDICT_FUN_PRIVATE_KEY (or PREDICT_FUN_PRIVY_PRIVATE_KEY) are required"
            )
        account = Account.from_key(private_key)
        builder = OrderBuilder.make(
            ChainId.BNB_MAINNET,
            private_key,
            OrderBuilderOptions(predict_account=predict_account or None, log_level="WARN"),
        )
        auth_signer = predict_account or account.address

        def sign_message(message: str) -> str:
            if predict_account:
                return builder.sign_predict_account_message(message)
            return account.sign_message(encode_defunct(text=message)).signature.hex()

        self.wallet_address = auth_signer
        self.client = ReadOnlyPredictAuthClient(
            api_key,
            jwt=static_jwt,
            auth_signer=auth_signer,
            sign_auth_message=sign_message,
        )
        self.api_key = api_key

    def authenticate(self, force: bool = False) -> str:
        assert self.client is not None
        self.status = "AUTHENTICATING"
        self.jwt = self.client.authenticate(force=force)
        return self.jwt

    def bootstrap_open_orders(self, session_id: str) -> list[dict[str, Any]]:
        assert self.client is not None
        orders: list[dict[str, Any]] = []
        after: str | None = None
        seen: set[str] = set()
        try:
            for _ in range(10):
                params: dict[str, Any] = {"first": 100, "status": "OPEN"}
                if after:
                    params["after"] = after
                payload = self.client.request("GET", "/v1/orders", auth=True, params=params)
                rows = [row for row in (payload.get("data") or []) if isinstance(row, dict)]
                orders.extend(rows)
                cursor = str(payload.get("cursor") or "").strip()
                if not cursor or cursor in seen or not rows:
                    break
                seen.add(cursor)
                after = cursor
            self.store.store_open_snapshot(session_id, orders, rest_status="OK")
            return orders
        except Exception as exc:
            error = safe_error(exc, self.jwt)
            self.store.store_open_snapshot(session_id, [], rest_status="ERROR", error=error)
            raise

    def _handle_message(self, ws: websocket.WebSocket, payload: dict[str, Any], session_id: str) -> bool:
        received = now_ms()
        self.last_message_at_ms = received
        if payload.get("type") == "R":
            if payload.get("success") is False:
                message = canonical_json(payload)
                if "invalid_credentials" in message.lower():
                    raise AuthExpired("wallet subscription JWT rejected")
                raise RuntimeError(f"wallet subscription rejected: {message[:350]}")
            self.subscribed_at_ms = received
            self.status = "LIVE"
            # A rejected/expired JWT can be recovered on the next authenticated
            # reconnect.  Do not keep that old error beside a healthy LIVE state.
            self.last_error = None
            self.store.mark_subscribed(session_id)
            return True
        if payload.get("type") != "M":
            return False
        topic = str(payload.get("topic") or "")
        if topic == "heartbeat":
            self.last_heartbeat_at_ms = received
            ws.send(json.dumps({"method": "heartbeat", "data": payload.get("data")}, separators=(",", ":")))
            return False
        if not topic.startswith("predictWalletEvents/"):
            return False
        data = payload.get("data")
        events = data if isinstance(data, list) else [data]
        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "")
            if event_type not in EVENT_TYPES:
                continue
            if self.store.store_event(session_id, event, received):
                self.inserted_events += 1
            else:
                self.duplicate_events += 1
            self.last_event_at_ms = received
        return False

    def run_connection(self, *, probe_seconds: float | None = None, force_auth: bool = False) -> bool:
        jwt = self.authenticate(force=force_auth)
        session_id = str(uuid.uuid4())
        self.current_session_id = session_id
        assert self.wallet_address is not None
        self.store.start_session(session_id, self.wallet_address)
        self.bootstrap_open_orders(session_id)
        ws: websocket.WebSocket | None = None
        subscribed = False
        deadline = time.monotonic() + float(probe_seconds) if probe_seconds is not None else None
        try:
            self.status = "CONNECTING"
            ws = websocket.create_connection(
                WS_URL,
                header=[f"x-api-key: {self.api_key}"],
                timeout=15,
                http_proxy_host=None,
                http_proxy_port=None,
            )
            self.ws = ws
            self.connected_at_ms = now_ms()
            self.store.mark_connected(session_id)
            topic = f"predictWalletEvents/{jwt}"
            ws.send(
                json.dumps(
                    {"method": "subscribe", "requestId": 1, "params": [topic]},
                    separators=(",", ":"),
                )
            )
            ws.settimeout(5)
            while not self.stop_event.is_set():
                if deadline is not None and time.monotonic() >= deadline:
                    break
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not raw:
                    raise RuntimeError("wallet websocket closed without a close frame")
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    subscribed = self._handle_message(ws, payload, session_id) or subscribed
            return subscribed
        except Exception as exc:
            self.store.finish_session(session_id, error=safe_error(exc, jwt))
            raise
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
            self.ws = None
            row = self.store.db.execute(
                "SELECT disconnected_at_ms FROM own_wallet_lifecycle_sessions_v1 WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if row is not None and row["disconnected_at_ms"] is None:
                self.store.finish_session(session_id)

    def run_forever(self) -> None:
        backoff = 1.0
        force_auth = False
        while not self.stop_event.is_set():
            try:
                self.run_connection(force_auth=force_auth)
                force_auth = False
                backoff = 1.0
            except AuthExpired as exc:
                self.status = "REAUTHENTICATING"
                self.last_error = safe_error(exc, self.jwt)
                force_auth = True
            except Exception as exc:
                self.status = "RECONNECTING"
                self.last_error = safe_error(exc, self.jwt)
                force_auth = False
            if self.stop_event.is_set():
                break
            self.reconnects += 1
            self.stop_event.wait(backoff)
            backoff = min(30.0, backoff * 2.0)
        self.status = "STOPPED"

    def stop(self) -> None:
        self.stop_event.set()
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass

    def snapshot(self) -> dict[str, Any]:
        storage = self.store.snapshot()
        at = now_ms()
        heartbeat_age_ms = at - self.last_heartbeat_at_ms if self.last_heartbeat_at_ms else None
        subscription_ready = bool(
            self.status == "LIVE"
            and self.connected_at_ms is not None
            and self.subscribed_at_ms is not None
            and self.ws is not None
        )
        heartbeat_fresh = bool(heartbeat_age_ms is not None and heartbeat_age_ms <= 45_000)
        return {
            "version": VERSION,
            "researchOnly": True,
            "readOnly": True,
            "canPlaceOrders": False,
            "canCancelOrders": False,
            "status": self.status,
            "walletAddress": self.wallet_address,
            "credentials": {
                "apiKeyPresent": bool(self.api_key),
                "privateSigningKeyPresent": True,
                "jwtInMemory": bool(self.jwt),
                "secretsStored": False,
            },
            "websocket": {
                "endpoint": WS_URL,
                "topic": "predictWalletEvents/<redacted-jwt>",
                "connectedAtMs": self.connected_at_ms,
                "subscribedAtMs": self.subscribed_at_ms,
                "lastMessageAtMs": self.last_message_at_ms,
                "lastHeartbeatAtMs": self.last_heartbeat_at_ms,
                "heartbeatAgeMs": heartbeat_age_ms,
                "heartbeatFresh": heartbeat_fresh,
                "subscriptionReady": subscription_ready,
                "dataCaptureReady": subscription_ready and heartbeat_fresh,
                "lastEventAtMs": self.last_event_at_ms,
                "reconnects": self.reconnects,
                "lastError": self.last_error,
            },
            "run": {"insertedEvents": self.inserted_events, "duplicateEvents": self.duplicate_events},
            "storage": {**storage, "database": str(self.store.path), "databaseBytes": self.store.path.stat().st_size},
            "calibrationReady": False,
            "calibrationBlocker": "Requires multi-market accepted/fill/cancel/no-fill support and later local-clock/Tape joins.",
        }


class StateHandler(BaseHTTPRequestHandler):
    collector: OwnWalletLifecycleCollector

    def do_GET(self) -> None:  # noqa: N802
        if self.path not in {"/state", "/health", "/healthz"}:
            self.send_error(404)
            return
        payload = self.collector.snapshot()
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def start_http(collector: OwnWalletLifecycleCollector, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("OwnWalletLifecycleStateHandler", (StateHandler,), {"collector": collector})
    server = ThreadingHTTPServer((host, port), handler)
    threading.Thread(target=server.serve_forever, name="own-wallet-lifecycle-state", daemon=True).start()
    return server


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Predict own-wallet lifecycle collector")
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("PREDICT_OWN_WALLET_LIFECYCLE_DB", DEFAULT_DB)))
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--probe-seconds", type=float)
    parser.add_argument("--no-http", action="store_true")
    args = parser.parse_args()

    collector = OwnWalletLifecycleCollector(args.db)
    server: ThreadingHTTPServer | None = None
    try:
        if args.probe_seconds is not None:
            subscribed = collector.run_connection(probe_seconds=max(1.0, args.probe_seconds))
            payload = collector.snapshot()
            payload["probeSubscribed"] = subscribed
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0 if subscribed else 2
        if not args.no_http:
            server = start_http(collector, args.host, args.port)
        signal.signal(signal.SIGINT, lambda *_: collector.stop())
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, lambda *_: collector.stop())
        collector.run_forever()
        return 0
    finally:
        collector.stop()
        if server is not None:
            server.shutdown()
            server.server_close()
        if collector.client is not None:
            collector.client.close()
        collector.store.close()


if __name__ == "__main__":
    raise SystemExit(main())
