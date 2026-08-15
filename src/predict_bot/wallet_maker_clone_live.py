from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import sqlite3
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

from .core import ApiHttpError, ApiTransportError, BinancePredictionTradingClient
from .poly_quote_canary import _credential_pair


ROOT = Path(__file__).resolve().parents[2]
ASSET = os.environ.get("PREDICT_WALLET_MAKER_CLONE_ASSET", "").strip().upper()
if ASSET not in {"ETH", "BNB"}:
    raise RuntimeError("PREDICT_WALLET_MAKER_CLONE_ASSET must be ETH or BNB")
SYMBOL = os.environ.get("PREDICT_WALLET_MAKER_CLONE_SYMBOL", f"{ASSET}USDT").strip().upper()
DB_PATH = Path(
    os.environ.get(
        "PREDICT_WALLET_MAKER_CLONE_DB",
        ROOT / "data" / f"wallet_maker_clone_{ASSET.lower()}.db",
    )
)
HOST = os.environ.get("PREDICT_WALLET_MAKER_CLONE_HOST", "127.0.0.1")
PORT = int(os.environ.get("PREDICT_WALLET_MAKER_CLONE_PORT", "8774" if ASSET == "ETH" else "8775"))
MASTER_ENABLED = os.environ.get("PREDICT_WALLET_MAKER_CLONE_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
NORMAL_LIVE_URL = os.environ.get(
    "PREDICT_WALLET_MAKER_CLONE_NORMAL_LIVE_URL",
    "http://127.0.0.1:8772/state" if ASSET == "ETH" else "http://127.0.0.1:8773/state",
)
LOOP_SECONDS = max(0.20, float(os.environ.get("PREDICT_WALLET_MAKER_CLONE_LOOP_SECONDS", "0.50")))
BOOK_POLL_SECONDS = max(0.20, float(os.environ.get("PREDICT_WALLET_MAKER_CLONE_BOOK_SECONDS", "0.50")))
ORDER_POLL_SECONDS = max(0.50, float(os.environ.get("PREDICT_WALLET_MAKER_CLONE_ORDER_SECONDS", "1.00")))
TICK_SIZE = max(0.001, float(os.environ.get("PREDICT_WALLET_MAKER_CLONE_TICK_SIZE", "0.01")))
QUOTE_SLIPPAGE_BPS = max(1, int(os.environ.get("PREDICT_WALLET_MAKER_CLONE_SLIPPAGE_BPS", "100")))
ACCOUNT_TYPE = os.environ.get("PREDICT_LIVE_ACCOUNT_TYPE", "SPOT").strip().upper()
if ACCOUNT_TYPE not in {"SPOT", "FUNDING"}:
    ACCOUNT_TYPE = "SPOT"

TERMINAL_ORDER_STATES = {
    "FILLED",
    "CANCELED",
    "CANCELLED",
    "REJECTED",
    "EXPIRED",
    "FAILED",
    "AMBIGUOUS",
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _to_wei(value: float) -> str:
    return str(max(1, int(float(value) * 10**18)))


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
    return (min(parsed, key=lambda row: row[0]) if side == "ask" else max(parsed, key=lambda row: row[0]))


def _order_rows(payload: Any) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            order_id = value.get("orderId") or value.get("order_id")
            if order_id is not None:
                found[str(order_id)] = value
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return list(found.values())


def _first_text(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value):
            return str(value)
    return None


class ClonePredictionClient(BinancePredictionTradingClient):
    """Prediction client extension for the batch-cancel endpoint.

    Binance Prediction batch-cancel has a documented raw bracket-key signing
    quirk. Keep keys such as cancelInfoList[0].orderId unescaped in the exact
    body that is signed and transmitted.
    """

    def batch_cancel_orders_raw(
        self,
        *,
        wallet_address: str,
        wallet_id: str,
        order_ids: list[str],
    ) -> dict[str, Any]:
        ids = [str(value) for value in order_ids if str(value)]
        if not ids:
            return {"success": True, "orders": []}
        fields: list[tuple[str, str]] = [
            ("walletAddress", wallet_address),
            ("walletId", wallet_id),
        ]
        for index, order_id in enumerate(ids):
            fields.append((f"cancelInfoList[{index}].orderId", order_id))
        fields.extend(
            [
                ("recvWindow", "5000"),
                ("timestamp", str(self.server_timestamp_ms())),
            ]
        )
        canonical = "&".join(
            f"{key}={urllib.parse.quote_plus(str(value), safe='')}" for key, value in fields
        )
        signature = hmac.new(
            self.api_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        body = f"{canonical}&signature={signature}".encode("utf-8")
        path = "/sapi/v1/w3w/wallet/prediction/trade/batch-cancel"
        try:
            response = self.http_client.post(
                path,
                content=body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "btc5m-wallet-maker-clone/0.1",
                    "X-MBX-APIKEY": self.api_key,
                },
            )
        except httpx.RequestError as exc:
            raise ApiTransportError(
                f"Request failed for {self.base_url}{path}: {type(exc).__name__}"
            ) from exc
        if response.status_code >= 400:
            self._raise_http_error(response, path=path)
        self._capture_rate_limits(response)
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ApiTransportError(f"Invalid JSON response from {self.base_url}{path}") from exc
        if not isinstance(payload, dict):
            raise ApiTransportError(f"Unexpected response type from {self.base_url}{path}")
        if payload.get("success") is False:
            raise RuntimeError(f"batch cancel rejected: {payload}")
        return payload


class WalletMakerCloneEngine:
    VERSION = "WALLET_MAKER_CLONE_LIVE_V1"

    def __init__(self, db_path: Path = DB_PATH) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.db = sqlite3.connect(db_path, check_same_thread=False, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db_lock = threading.RLock()
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.http = httpx.Client(timeout=httpx.Timeout(0.8, connect=0.3))
        self.client: ClonePredictionClient | None = None
        self.wallet_address: str | None = None
        self.wallet_id: str | None = None
        self.credential_source = "UNAVAILABLE"
        self.market: dict[str, Any] | None = None
        self.books: dict[str, dict[str, Any]] = {"UP": {}, "DOWN": {}}
        self.last_error: str | None = None
        self.last_interlock: dict[str, Any] | None = None
        self.last_book_poll = 0.0
        self.last_order_poll = 0.0
        self.status = "MASTER_DISABLED" if not MASTER_ENABLED else "SAFE_PAUSED"
        self._create_schema()
        self._ensure_defaults()
        # Every process start is intentionally safe-paused. Existing clone order
        # IDs are reconciled/cancelled before the operator may resume.
        self._set_setting("runtime_enabled", "0")
        self._event("WARN", "CLONE_SAFE_PAUSE_STARTUP", None, None, "process startup force-paused new clone orders")

    def _create_schema(self) -> None:
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS wallet_maker_clone_settings(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wallet_maker_clone_pairs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    topic_id INTEGER NOT NULL,
                    market_end_ms INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    pair_place_started_at_ms INTEGER,
                    pair_place_completed_at_ms INTEGER,
                    pair_place_skew_ms REAL,
                    close_reason TEXT,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    UNIQUE(market_id)
                );
                CREATE TABLE IF NOT EXISTS wallet_maker_clone_orders(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pair_id INTEGER NOT NULL,
                    market_id INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    target_price REAL,
                    best_bid_at_plan REAL,
                    best_ask_at_plan REAL,
                    target_profit_usdt REAL,
                    planned_cost_usdt REAL,
                    planned_shares REAL,
                    quote_id TEXT,
                    quote_average REAL,
                    quote_amount_in_wei TEXT,
                    quote_amount_out_wei TEXT,
                    quote_expire_at_ms INTEGER,
                    quote_started_at_ms INTEGER,
                    quote_completed_at_ms INTEGER,
                    quote_rtt_ms REAL,
                    place_started_at_ms INTEGER,
                    place_completed_at_ms INTEGER,
                    place_rtt_ms REAL,
                    order_id TEXT,
                    vendor_order_id TEXT,
                    order_status TEXT,
                    maker_usdt_amount REAL,
                    maker_share_qty REAL,
                    filled_usdt_amount REAL,
                    filled_share_qty REAL,
                    fill_percentage REAL,
                    last_reconciled_at_ms INTEGER,
                    raw_status_json TEXT,
                    error_kind TEXT,
                    error_message TEXT,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    UNIQUE(pair_id, side)
                );
                CREATE INDEX IF NOT EXISTS idx_wallet_clone_orders_order_id
                    ON wallet_maker_clone_orders(order_id);
                CREATE INDEX IF NOT EXISTS idx_wallet_clone_orders_state
                    ON wallet_maker_clone_orders(state, market_id);
                CREATE TABLE IF NOT EXISTS wallet_maker_clone_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    at_ms INTEGER NOT NULL,
                    level TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    market_id INTEGER,
                    pair_id INTEGER,
                    message TEXT NOT NULL
                );
                """
            )
            self.db.commit()

    def _ensure_defaults(self) -> None:
        defaults = {
            "runtime_enabled": "0",
            "target_profit_usdt": "1.0",
            "bid_offset_ticks": "0",
            "minimum_order_usdt": "1.0",
            "maximum_order_usdt": "25.0",
            "minimum_remaining_seconds": "30.0",
            "auto_requote": "0",
            "requote_ticks": "2",
            "max_order_age_seconds": "30.0",
        }
        now = _now_ms()
        with self.db_lock:
            for key, value in defaults.items():
                self.db.execute(
                    "INSERT OR IGNORE INTO wallet_maker_clone_settings(key,value,updated_at_ms) VALUES(?,?,?)",
                    (key, value, now),
                )
            self.db.commit()

    def _setting(self, key: str, fallback: str) -> str:
        with self.db_lock:
            row = self.db.execute(
                "SELECT value FROM wallet_maker_clone_settings WHERE key=?", (key,)
            ).fetchone()
        return str(row["value"]) if row else fallback

    def _set_setting(self, key: str, value: Any) -> None:
        with self.db_lock:
            self.db.execute(
                """INSERT INTO wallet_maker_clone_settings(key,value,updated_at_ms)
                   VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET
                   value=excluded.value,updated_at_ms=excluded.updated_at_ms""",
                (key, str(value), _now_ms()),
            )
            self.db.commit()

    def _settings(self) -> dict[str, Any]:
        return {
            "runtimeEnabled": self._setting("runtime_enabled", "0") == "1",
            "targetPotentialProfitUsdt": float(self._setting("target_profit_usdt", "1.0")),
            "bidOffsetTicks": int(self._setting("bid_offset_ticks", "0")),
            "minimumOrderUsdt": float(self._setting("minimum_order_usdt", "1.0")),
            "maximumOrderUsdt": float(self._setting("maximum_order_usdt", "25.0")),
            "minimumRemainingSeconds": float(self._setting("minimum_remaining_seconds", "30.0")),
            "autoRequote": self._setting("auto_requote", "0") == "1",
            "requoteTicks": int(self._setting("requote_ticks", "2")),
            "maxOrderAgeSeconds": float(self._setting("max_order_age_seconds", "30.0")),
        }

    def _event(
        self,
        level: str,
        event_type: str,
        market_id: int | None,
        pair_id: int | None,
        message: str,
    ) -> None:
        with self.db_lock:
            self.db.execute(
                "INSERT INTO wallet_maker_clone_events(at_ms,level,event_type,market_id,pair_id,message) VALUES(?,?,?,?,?,?)",
                (_now_ms(), level, event_type, market_id, pair_id, str(message)[:1000]),
            )
            self.db.commit()

    def _normal_live_conflict(self) -> dict[str, Any]:
        result = {
            "checkedAtMs": _now_ms(),
            "blocked": False,
            "runtimeEnabled": None,
            "activeRound": False,
            "reason": "normal live state unavailable",
        }
        try:
            response = self.http.get(NORMAL_LIVE_URL)
            response.raise_for_status()
            payload = response.json()
            state = payload.get("state") if isinstance(payload, dict) and isinstance(payload.get("state"), dict) else payload
            if not isinstance(state, dict):
                return result
            settings = state.get("settings") if isinstance(state.get("settings"), dict) else {}
            runtime = settings.get("runtimeEnabled") is True
            active = isinstance(state.get("activeRound"), dict) and bool(state.get("activeRound"))
            result.update(
                blocked=runtime or active,
                runtimeEnabled=runtime,
                activeRound=active,
                reason=(
                    "normal ETH/BNB live engine is enabled or managing a position"
                    if runtime or active
                    else "normal live engine is idle and paused"
                ),
            )
        except Exception as exc:
            result["reason"] = f"normal live interlock unavailable: {str(exc)[:240]}"
        self.last_interlock = dict(result)
        return result

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "runtimeEnabled",
            "targetPotentialProfitUsdt",
            "bidOffsetTicks",
            "minimumOrderUsdt",
            "maximumOrderUsdt",
            "minimumRemainingSeconds",
            "autoRequote",
            "requoteTicks",
            "maxOrderAgeSeconds",
        }
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError("unsupported settings: " + ", ".join(unknown))

        current = self._settings()

        def number(name: str, low: float, high: float) -> float:
            raw = values.get(name, current[name])
            try:
                value = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be a number") from exc
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} must be between {low:g} and {high:g}")
            return value

        target_profit = number("targetPotentialProfitUsdt", 0.10, 100.0)
        minimum_cost = number("minimumOrderUsdt", 0.01, 1000.0)
        maximum_cost = number("maximumOrderUsdt", 0.10, 10000.0)
        min_remaining = number("minimumRemainingSeconds", 5.0, 299.0)
        max_age = number("maxOrderAgeSeconds", 2.0, 299.0)
        bid_offset = int(values.get("bidOffsetTicks", current["bidOffsetTicks"]))
        requote_ticks = int(values.get("requoteTicks", current["requoteTicks"]))
        if not 0 <= bid_offset <= 20:
            raise ValueError("bidOffsetTicks must be between 0 and 20")
        if not 1 <= requote_ticks <= 20:
            raise ValueError("requoteTicks must be between 1 and 20")
        if minimum_cost > maximum_cost:
            raise ValueError("minimumOrderUsdt must be <= maximumOrderUsdt")

        mapping = {
            "targetPotentialProfitUsdt": ("target_profit_usdt", target_profit),
            "bidOffsetTicks": ("bid_offset_ticks", bid_offset),
            "minimumOrderUsdt": ("minimum_order_usdt", minimum_cost),
            "maximumOrderUsdt": ("maximum_order_usdt", maximum_cost),
            "minimumRemainingSeconds": ("minimum_remaining_seconds", min_remaining),
            "autoRequote": ("auto_requote", "1" if bool(values.get("autoRequote", current["autoRequote"])) else "0"),
            "requoteTicks": ("requote_ticks", requote_ticks),
            "maxOrderAgeSeconds": ("max_order_age_seconds", max_age),
        }
        for name, (key, value) in mapping.items():
            if name in values:
                self._set_setting(key, value)

        if "runtimeEnabled" in values:
            enabled = bool(values["runtimeEnabled"])
            if enabled:
                if not MASTER_ENABLED:
                    raise ValueError(f"PREDICT_WALLET_MAKER_CLONE_{ASSET}_ENABLED master switch is off")
                interlock = self._normal_live_conflict()
                if interlock.get("blocked") is True:
                    raise ValueError(f"cannot resume clone: {interlock.get('reason')}")
                if not self._ensure_client():
                    raise ValueError(self.last_error or "Prediction live client unavailable")
                self._set_setting("runtime_enabled", "1")
                self._event("WARN", "CLONE_RUNTIME_RESUMED", None, None, "dual-sided real-money clone resumed")
            else:
                self._set_setting("runtime_enabled", "0")
                self._event("WARN", "CLONE_RUNTIME_PAUSED", None, None, "clone paused; recorded resting orders will be cancelled")
                self._cancel_recorded_active("RUNTIME_PAUSED")
        return self.snapshot()

    def _ensure_client(self) -> bool:
        with self.lock:
            if self.client is not None and self.wallet_address and self.wallet_id:
                return True
        api_key, api_secret, source = _credential_pair()
        self.credential_source = source
        if not api_key or not api_secret:
            self.status = "CONFIG_REQUIRED"
            self.last_error = "Binance live credentials unavailable"
            return False
        try:
            client = ClonePredictionClient(api_key, api_secret)
            wallets = client.wallets().get("wallets") or []
            if len(wallets) != 1:
                raise RuntimeError(f"expected one Prediction wallet, got {len(wallets)}")
            wallet_address = str(wallets[0].get("walletAddress") or "")
            wallet_id = str(wallets[0].get("walletId") or "")
            if not wallet_address or not wallet_id:
                raise RuntimeError("Prediction wallet metadata incomplete")
            client.server_timestamp_ms()
            with self.lock:
                self.client = client
                self.wallet_address = wallet_address
                self.wallet_id = wallet_id
            self.last_error = None
            return True
        except Exception as exc:
            self.status = "BLOCKED_PREFLIGHT"
            self.last_error = str(exc)[:500]
            return False

    def _prime_market(self) -> dict[str, Any] | None:
        if not self._ensure_client():
            return None
        with self.lock:
            client = self.client
        assert client is not None
        summary = client.find_market_summary(SYMBOL, max_pages=5)
        if not isinstance(summary, dict):
            self.last_error = f"current {SYMBOL} 5m Prediction market unavailable"
            return None
        selected = summary.get("_selectedMarket")
        if not isinstance(selected, dict):
            return None
        market = selected.get("market")
        up = selected.get("up")
        down = selected.get("down")
        if not all(isinstance(value, dict) for value in (market, up, down)):
            return None
        row = {
            "asset": ASSET,
            "symbol": SYMBOL,
            "market_id": int(market.get("marketId") or 0),
            "topic_id": int(summary.get("marketTopicId") or 0),
            "end_ms": int(summary.get("endDate") or 0),
            "up_token_id": str(up.get("tokenId") or ""),
            "down_token_id": str(down.get("tokenId") or ""),
            "fee_rate_bps": int(summary.get("feeRateBps") or market.get("feeRateBps") or 200),
            "precision": int(summary.get("decimalPrecision") or market.get("decimalPrecision") or 2),
        }
        if row["market_id"] <= 0 or not row["up_token_id"] or not row["down_token_id"]:
            self.last_error = "current market token metadata incomplete"
            return None
        self.market = dict(row)
        return row

    def _poll_books(self, market: dict[str, Any]) -> None:
        if time.monotonic() - self.last_book_poll < BOOK_POLL_SECONDS:
            return
        self.last_book_poll = time.monotonic()
        with self.lock:
            client = self.client
        if client is None:
            return
        jobs = {"UP": str(market["up_token_id"]), "DOWN": str(market["down_token_id"])}
        results: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix=f"clone-book-{ASSET.lower()}") as pool:
            futures = {
                pool.submit(client.orderbook, int(market["market_id"]), token): side
                for side, token in jobs.items()
            }
            for future in as_completed(futures):
                side = futures[future]
                try:
                    book = future.result()
                    bid, bid_size = _best_level(book, "bid")
                    ask, ask_size = _best_level(book, "ask")
                    results[side] = {
                        "bestBid": bid,
                        "bestBidSize": bid_size,
                        "bestAsk": ask,
                        "bestAskSize": ask_size,
                        "observedAtMs": _now_ms(),
                    }
                except Exception as exc:
                    results[side] = {"error": str(exc)[:300], "observedAtMs": _now_ms()}
        self.books.update(results)

    def _current_pair(self) -> dict[str, Any] | None:
        with self.db_lock:
            row = self.db.execute(
                "SELECT * FROM wallet_maker_clone_pairs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def _pair_orders(self, pair_id: int) -> list[dict[str, Any]]:
        with self.db_lock:
            rows = self.db.execute(
                "SELECT * FROM wallet_maker_clone_orders WHERE pair_id=? ORDER BY CASE side WHEN 'UP' THEN 0 ELSE 1 END",
                (int(pair_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def _new_pair(self, market: dict[str, Any]) -> dict[str, Any]:
        now = _now_ms()
        with self.db_lock:
            cursor = self.db.execute(
                """INSERT OR IGNORE INTO wallet_maker_clone_pairs(
                       asset,market_id,topic_id,market_end_ms,state,created_at_ms,updated_at_ms
                   ) VALUES(?,?,?,?, 'READY',?,?)""",
                (ASSET, int(market["market_id"]), int(market["topic_id"]), int(market["end_ms"]), now, now),
            )
            self.db.commit()
            if cursor.lastrowid:
                row = self.db.execute("SELECT * FROM wallet_maker_clone_pairs WHERE id=?", (cursor.lastrowid,)).fetchone()
            else:
                row = self.db.execute("SELECT * FROM wallet_maker_clone_pairs WHERE market_id=?", (int(market["market_id"]),)).fetchone()
        assert row is not None
        pair = dict(row)
        if cursor.lastrowid:
            self._event("INFO", "PAIR_READY", int(market["market_id"]), int(pair["id"]), "new ETH/BNB 5m dual-sided clone pair ready")
        return pair

    def _set_pair(self, pair_id: int, **values: Any) -> None:
        allowed = {
            "state",
            "pair_place_started_at_ms",
            "pair_place_completed_at_ms",
            "pair_place_skew_ms",
            "close_reason",
        }
        updates = {key: value for key, value in values.items() if key in allowed}
        updates["updated_at_ms"] = _now_ms()
        assignments = ",".join(f"{key}=?" for key in updates)
        with self.db_lock:
            self.db.execute(
                f"UPDATE wallet_maker_clone_pairs SET {assignments} WHERE id=?",
                (*updates.values(), int(pair_id)),
            )
            self.db.commit()

    def _plan_order(self, side: str, market: dict[str, Any]) -> dict[str, Any] | None:
        settings = self._settings()
        book = self.books.get(side) or {}
        bid = _finite(book.get("bestBid"))
        ask = _finite(book.get("bestAsk"))
        if bid is None or ask is None:
            return None
        price = bid - int(settings["bidOffsetTicks"]) * TICK_SIZE
        precision = max(1, int(market.get("precision") or 2))
        price = round(price, precision)
        if price <= 0 or price >= 1:
            return None
        # Soft post-only. LIMIT/GTC has no explicit post-only flag on the Binance
        # Prediction wrapper, so a BUY is never intentionally sent at/crossing Ask.
        if price + 1e-12 >= ask:
            return {
                "side": side,
                "blocked": True,
                "reason": f"soft post-only blocked price {price:.6f} >= ask {ask:.6f}",
                "price": price,
                "bestBid": bid,
                "bestAsk": ask,
            }
        target_profit = float(settings["targetPotentialProfitUsdt"])
        shares = target_profit / max(1e-9, 1.0 - price)
        cost = shares * price
        cost = max(float(settings["minimumOrderUsdt"]), cost)
        cost = min(float(settings["maximumOrderUsdt"]), cost)
        shares = cost / price
        return {
            "side": side,
            "blocked": False,
            "price": price,
            "bestBid": bid,
            "bestAsk": ask,
            "targetProfit": shares * (1.0 - price),
            "plannedCost": cost,
            "plannedShares": shares,
            "tokenId": str(market[f"{side.lower()}_token_id"]),
        }

    def _insert_order_plan(self, pair_id: int, market_id: int, plan: dict[str, Any]) -> int:
        now = _now_ms()
        with self.db_lock:
            cursor = self.db.execute(
                """INSERT OR REPLACE INTO wallet_maker_clone_orders(
                       pair_id,market_id,side,token_id,state,target_price,best_bid_at_plan,best_ask_at_plan,
                       target_profit_usdt,planned_cost_usdt,planned_shares,created_at_ms,updated_at_ms
                   ) VALUES(?,?,?,?, 'PLANNED',?,?,?,?,?,?,?,?)""",
                (
                    int(pair_id), int(market_id), str(plan["side"]), str(plan["tokenId"]),
                    float(plan["price"]), float(plan["bestBid"]), float(plan["bestAsk"]),
                    float(plan["targetProfit"]), float(plan["plannedCost"]), float(plan["plannedShares"]), now, now,
                ),
            )
            self.db.commit()
        return int(cursor.lastrowid)

    def _update_order(self, order_row_id: int, **values: Any) -> None:
        allowed = {
            "state", "quote_id", "quote_average", "quote_amount_in_wei", "quote_amount_out_wei",
            "quote_expire_at_ms", "quote_started_at_ms", "quote_completed_at_ms", "quote_rtt_ms",
            "place_started_at_ms", "place_completed_at_ms", "place_rtt_ms", "order_id", "vendor_order_id",
            "order_status", "maker_usdt_amount", "maker_share_qty", "filled_usdt_amount", "filled_share_qty",
            "fill_percentage", "last_reconciled_at_ms", "raw_status_json", "error_kind", "error_message",
        }
        updates = {key: value for key, value in values.items() if key in allowed}
        updates["updated_at_ms"] = _now_ms()
        assignments = ",".join(f"{key}=?" for key in updates)
        with self.db_lock:
            self.db.execute(
                f"UPDATE wallet_maker_clone_orders SET {assignments} WHERE id=?",
                (*updates.values(), int(order_row_id)),
            )
            self.db.commit()

    def _place_one(self, order_row_id: int, plan: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            client = self.client
            wallet_address = self.wallet_address
            wallet_id = self.wallet_id
        if client is None or not wallet_address or not wallet_id:
            return {"ok": False, "completedAtMs": _now_ms(), "error": "live client unavailable"}
        side = str(plan["side"])
        self._update_order(order_row_id, state="QUOTING", quote_started_at_ms=_now_ms())
        q0 = time.monotonic()
        try:
            quote = client.get_quote(
                wallet_address=wallet_address,
                token_id=str(plan["tokenId"]),
                amount_in_wei=_to_wei(float(plan["plannedCost"])),
                price_limit=f"{float(plan['price']):.6f}",
                slippage_bps=QUOTE_SLIPPAGE_BPS,
                fee_rate_bps=int(market.get("fee_rate_bps") or 200),
                funding_source="MPC",
                side="BUY",
                order_type="LIMIT",
            )
        except Exception as exc:
            self._update_order(
                order_row_id,
                state="REJECTED",
                quote_completed_at_ms=_now_ms(),
                quote_rtt_ms=(time.monotonic() - q0) * 1000.0,
                error_kind="QUOTE_REJECTED",
                error_message=str(exc)[:500],
            )
            return {"ok": False, "completedAtMs": _now_ms(), "error": str(exc)[:500]}
        quote_id = str(quote.get("quoteId") or "")
        self._update_order(
            order_row_id,
            state="QUOTE_READY",
            quote_id=quote_id or None,
            quote_average=_finite(quote.get("averagePrice")),
            quote_amount_in_wei=str(quote.get("amountIn") or "") or None,
            quote_amount_out_wei=str(quote.get("amountOut") or "") or None,
            quote_expire_at_ms=int(quote.get("expireAt") or 0) or None,
            quote_completed_at_ms=_now_ms(),
            quote_rtt_ms=(time.monotonic() - q0) * 1000.0,
        )
        if not quote_id:
            self._update_order(order_row_id, state="REJECTED", error_kind="QUOTE_MISSING_ID", error_message="LIMIT quote returned no quoteId")
            return {"ok": False, "completedAtMs": _now_ms(), "error": "quote missing id"}

        # Re-read this side immediately before placement. This is the second
        # soft-post-only check and is intentionally independent per leg.
        try:
            latest_book = client.orderbook(int(market["market_id"]), str(plan["tokenId"]))
            latest_ask, _ = _best_level(latest_book, "ask")
        except Exception as exc:
            self._update_order(order_row_id, state="REJECTED", error_kind="PREPLACE_BOOK_FAILED", error_message=str(exc)[:500])
            return {"ok": False, "completedAtMs": _now_ms(), "error": str(exc)[:500]}
        if latest_ask is None or float(plan["price"]) + 1e-12 >= latest_ask:
            self._update_order(
                order_row_id,
                state="REJECTED",
                error_kind="SOFT_POST_ONLY_BLOCK",
                error_message=f"pre-place {side} price {plan['price']} >= latest ask {latest_ask}",
            )
            return {"ok": False, "completedAtMs": _now_ms(), "error": "soft post-only block"}

        p0_ms = _now_ms()
        p0 = time.monotonic()
        self._update_order(order_row_id, state="PLACING", place_started_at_ms=p0_ms)
        try:
            placed = client.place_limit_order(
                wallet_address=wallet_address,
                wallet_id=wallet_id,
                quote_id=quote_id,
                price_limit=f"{float(plan['price']):.6f}",
                slippage_bps=QUOTE_SLIPPAGE_BPS,
                account_type=ACCOUNT_TYPE,
                funding_source="MPC",
            )
        except ApiTransportError as exc:
            self._update_order(
                order_row_id,
                state="AMBIGUOUS",
                place_completed_at_ms=_now_ms(),
                place_rtt_ms=(time.monotonic() - p0) * 1000.0,
                error_kind="PLACE_AMBIGUOUS",
                error_message=str(exc)[:500],
            )
            return {"ok": False, "ambiguous": True, "completedAtMs": _now_ms(), "error": str(exc)[:500]}
        except Exception as exc:
            self._update_order(
                order_row_id,
                state="REJECTED",
                place_completed_at_ms=_now_ms(),
                place_rtt_ms=(time.monotonic() - p0) * 1000.0,
                error_kind="PLACE_REJECTED",
                error_message=str(exc)[:500],
            )
            return {"ok": False, "completedAtMs": _now_ms(), "error": str(exc)[:500]}
        completed = _now_ms()
        order_id = _first_text(placed, "orderId", "order_id", "id")
        vendor_order_id = _first_text(placed, "vendorOrderId", "vendor_order_id")
        if not order_id:
            self._update_order(
                order_row_id,
                state="AMBIGUOUS",
                place_completed_at_ms=completed,
                place_rtt_ms=(time.monotonic() - p0) * 1000.0,
                error_kind="PLACE_MISSING_ORDER_ID",
                error_message="place response returned no orderId",
                raw_status_json=json.dumps(placed, separators=(",", ":"), default=str),
            )
            return {"ok": False, "ambiguous": True, "completedAtMs": completed, "error": "missing order id"}
        self._update_order(
            order_row_id,
            state="RESTING",
            place_completed_at_ms=completed,
            place_rtt_ms=(time.monotonic() - p0) * 1000.0,
            order_id=order_id,
            vendor_order_id=vendor_order_id,
            order_status=_first_text(placed, "status", "orderStatus", "order_status") or "NEW",
            raw_status_json=json.dumps(placed, separators=(",", ":"), default=str),
            error_kind=None,
            error_message=None,
        )
        return {"ok": True, "completedAtMs": completed, "orderId": order_id}

    def _place_pair(self, pair: dict[str, Any], market: dict[str, Any]) -> None:
        settings = self._settings()
        seconds_left = (int(market["end_ms"]) - _now_ms()) / 1000.0
        if seconds_left < float(settings["minimumRemainingSeconds"]):
            self.status = "WAITING_NEXT_MARKET"
            return
        plans = {side: self._plan_order(side, market) for side in ("UP", "DOWN")}
        if any(plan is None for plan in plans.values()):
            self.status = "WAITING_BOTH_BOOKS"
            return
        blocked = [plan for plan in plans.values() if plan and plan.get("blocked")]
        if blocked:
            self.status = "SOFT_POST_ONLY_BLOCKED"
            for plan in blocked:
                self._event("INFO", "SOFT_POST_ONLY_BLOCK", int(market["market_id"]), int(pair["id"]), str(plan.get("reason")))
            return
        order_ids = {
            side: self._insert_order_plan(int(pair["id"]), int(market["market_id"]), plans[side] or {})
            for side in ("UP", "DOWN")
        }
        started = _now_ms()
        self._set_pair(int(pair["id"]), state="PLACING_BOTH", pair_place_started_at_ms=started)
        self._event(
            "WARN",
            "PAIR_PLACE_STARTED",
            int(market["market_id"]),
            int(pair["id"]),
            f"placing UP@{plans['UP']['price']:.4f} and DOWN@{plans['DOWN']['price']:.4f} concurrently",
        )
        results: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix=f"clone-place-{ASSET.lower()}") as pool:
            futures = {
                pool.submit(self._place_one, order_ids[side], plans[side] or {}, market): side
                for side in ("UP", "DOWN")
            }
            for future in as_completed(futures):
                side = futures[future]
                try:
                    results[side] = future.result()
                except Exception as exc:
                    results[side] = {"ok": False, "completedAtMs": _now_ms(), "error": str(exc)[:500]}
        completed_times = [int(value.get("completedAtMs") or 0) for value in results.values() if value.get("completedAtMs")]
        skew = max(completed_times) - min(completed_times) if len(completed_times) == 2 else None
        all_ok = all(bool(results.get(side, {}).get("ok")) for side in ("UP", "DOWN"))
        ambiguous = any(bool(results.get(side, {}).get("ambiguous")) for side in ("UP", "DOWN"))
        state = "BOTH_RESTING" if all_ok else ("AMBIGUOUS" if ambiguous else "PARTIAL_SUBMISSION")
        self._set_pair(
            int(pair["id"]),
            state=state,
            pair_place_completed_at_ms=max(completed_times) if completed_times else _now_ms(),
            pair_place_skew_ms=float(skew) if skew is not None else None,
        )
        self._event(
            "INFO" if all_ok else "ERROR",
            "PAIR_PLACE_COMPLETED",
            int(market["market_id"]),
            int(pair["id"]),
            f"state={state}; skewMs={skew}; UP={results.get('UP')}; DOWN={results.get('DOWN')}",
        )
        if not all_ok:
            # A one-sided resting order is precisely the failure mode this clone
            # is meant to make visible and bounded. Cancel any successfully
            # recorded leg instead of silently carrying accidental asymmetry.
            self._cancel_pair(int(pair["id"]), "PAIR_SUBMISSION_INCOMPLETE")

    def _normalize_order_update(self, row: dict[str, Any]) -> dict[str, Any]:
        status = (_first_text(row, "status", "orderStatus", "order_status") or "UNKNOWN").upper()
        fill_pct = _finite(row.get("fillPercentage"))
        if fill_pct is not None and fill_pct > 1.0:
            fill_pct /= 100.0
        return {
            "order_status": status,
            "vendor_order_id": _first_text(row, "vendorOrderId", "vendor_order_id"),
            "maker_usdt_amount": _finite(row.get("makerUsdtAmount")),
            "maker_share_qty": _finite(row.get("makerShareQty")),
            "filled_usdt_amount": _finite(row.get("filledUsdtAmount")),
            "filled_share_qty": _finite(row.get("filledShareQty")),
            "fill_percentage": fill_pct,
            "last_reconciled_at_ms": _now_ms(),
            "raw_status_json": json.dumps(row, separators=(",", ":"), allow_nan=False, default=str),
            "state": (
                "FILLED"
                if status == "FILLED"
                else "CANCELED"
                if status in {"CANCELED", "CANCELLED", "EXPIRED"}
                else "PARTIAL_FILL"
                if status in {"PARTIALLY_FILLED", "PARTIAL_FILLED"} or (fill_pct is not None and 0 < fill_pct < 1)
                else "RESTING"
            ),
        }

    def _reconcile_pair(self, pair: dict[str, Any], market: dict[str, Any]) -> None:
        if time.monotonic() - self.last_order_poll < ORDER_POLL_SECONDS:
            return
        self.last_order_poll = time.monotonic()
        with self.lock:
            client = self.client
            wallet_address = self.wallet_address
        if client is None or not wallet_address:
            return
        orders = self._pair_orders(int(pair["id"]))
        ids = {str(row.get("order_id") or "") for row in orders if row.get("order_id")}
        if not ids:
            return
        try:
            active_payload = client.active_orders(wallet_address, market_id=int(market["market_id"]), limit=100)
            active = {str(row.get("orderId") or row.get("order_id")): row for row in _order_rows(active_payload)}
        except Exception as exc:
            self.last_error = f"active order reconciliation: {str(exc)[:300]}"
            return
        missing = ids - set(active)
        history: dict[str, dict[str, Any]] = {}
        if missing:
            try:
                history_payload = client.order_history(wallet_address, limit=100)
                history = {str(row.get("orderId") or row.get("order_id")): row for row in _order_rows(history_payload)}
            except Exception as exc:
                self.last_error = f"order history reconciliation: {str(exc)[:300]}"
        for order in orders:
            oid = str(order.get("order_id") or "")
            remote = active.get(oid) or history.get(oid)
            if remote is None:
                continue
            previous_state = str(order.get("state") or "")
            update = self._normalize_order_update(remote)
            self._update_order(int(order["id"]), **update)
            if update["state"] != previous_state:
                self._event(
                    "INFO",
                    f"ORDER_{update['state']}",
                    int(pair["market_id"]),
                    int(pair["id"]),
                    f"{order['side']} order {oid} {previous_state}->{update['state']}; filledShares={update.get('filled_share_qty')}",
                )
        refreshed = self._pair_orders(int(pair["id"]))
        states = {str(row["side"]): str(row["state"]) for row in refreshed}
        if all(states.get(side) == "FILLED" for side in ("UP", "DOWN")):
            pair_state = "BOTH_FILLED"
        elif any(states.get(side) == "FILLED" for side in ("UP", "DOWN")):
            pair_state = "ONE_FILLED"
        elif any(states.get(side) == "PARTIAL_FILL" for side in ("UP", "DOWN")):
            pair_state = "PARTIAL_FILL"
        elif all(states.get(side) in {"CANCELED", "FILLED"} for side in ("UP", "DOWN")):
            pair_state = "TERMINAL"
        else:
            pair_state = "BOTH_RESTING"
        self._set_pair(int(pair["id"]), state=pair_state)

        if self._settings()["autoRequote"] and pair_state == "BOTH_RESTING":
            self._maybe_requote(pair, refreshed, market)

    def _maybe_requote(self, pair: dict[str, Any], orders: list[dict[str, Any]], market: dict[str, Any]) -> None:
        settings = self._settings()
        threshold = int(settings["requoteTicks"]) * TICK_SIZE
        max_age_ms = float(settings["maxOrderAgeSeconds"]) * 1000.0
        now = _now_ms()
        should = False
        for row in orders:
            book = self.books.get(str(row["side"])) or {}
            latest_bid = _finite(book.get("bestBid"))
            old_price = _finite(row.get("target_price"))
            if latest_bid is not None and old_price is not None and abs(latest_bid - old_price) + 1e-12 >= threshold:
                should = True
            if now - int(row.get("created_at_ms") or now) >= max_age_ms:
                should = True
        if not should:
            return
        if self._cancel_pair(int(pair["id"]), "AUTO_REQUOTE"):
            # V1 deliberately does not reuse the same market UNIQUE pair row for
            # repeated generations. Auto-requote remains observable but disabled
            # by default until a multi-generation schema is introduced.
            self._set_setting("auto_requote", "0")
            self._event(
                "WARN",
                "AUTO_REQUOTE_STOPPED_V1",
                int(pair["market_id"]),
                int(pair["id"]),
                "orders cancelled after stale quote; V1 auto-requote self-disabled to avoid duplicate generation",
            )

    def _cancel_pair(self, pair_id: int, reason: str) -> bool:
        with self.lock:
            client = self.client
            wallet_address = self.wallet_address
            wallet_id = self.wallet_id
        if client is None or not wallet_address or not wallet_id:
            return False
        orders = self._pair_orders(pair_id)
        order_ids = [
            str(row["order_id"])
            for row in orders
            if row.get("order_id") and str(row.get("state") or "") not in {"FILLED", "CANCELED"}
        ]
        if not order_ids:
            return True
        pair = self._current_pair() or {}
        market_id = int(pair.get("market_id") or 0) or None
        self._set_pair(pair_id, state="CANCELING", close_reason=reason)
        self._event("WARN", "PAIR_CANCEL_REQUESTED", market_id, pair_id, f"reason={reason}; orderIds={order_ids}")
        try:
            client.batch_cancel_orders_raw(
                wallet_address=wallet_address,
                wallet_id=wallet_id,
                order_ids=order_ids,
            )
        except Exception as exc:
            self.last_error = f"batch cancel: {str(exc)[:400]}"
            self._event("ERROR", "PAIR_CANCEL_FAILED", market_id, pair_id, self.last_error)
            return False
        for row in orders:
            if str(row.get("order_id") or "") in order_ids:
                self._update_order(int(row["id"]), state="CANCELED", order_status="CANCEL_REQUEST_ACCEPTED")
        self._set_pair(pair_id, state="CANCELED", close_reason=reason)
        self._event("INFO", "PAIR_CANCELED", market_id, pair_id, f"reason={reason}; count={len(order_ids)}")
        return True

    def _cancel_recorded_active(self, reason: str) -> None:
        if not self._ensure_client():
            return
        pair = self._current_pair()
        if pair is None:
            return
        orders = self._pair_orders(int(pair["id"]))
        if any(str(row.get("state") or "") not in {"FILLED", "CANCELED", "REJECTED"} and row.get("order_id") for row in orders):
            self._cancel_pair(int(pair["id"]), reason)

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, name=f"wallet-maker-clone-{ASSET.lower()}", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3.0)
        try:
            self._cancel_recorded_active("PROCESS_STOP")
        except Exception:
            pass
        try:
            self.http.close()
        except Exception:
            pass
        with self.lock:
            client = self.client
        try:
            if client:
                client.close()
        except Exception:
            pass
        with self.db_lock:
            self.db.commit()
            self.db.close()

    def _run(self) -> None:
        startup_reconciled = False
        while not self.stop_event.is_set():
            try:
                if not startup_reconciled and self._ensure_client():
                    self._cancel_recorded_active("STARTUP_RECONCILIATION")
                    startup_reconciled = True
                self._tick()
            except ApiHttpError as exc:
                self.last_error = str(exc)[:500]
                self.status = "RATE_LIMITED" if exc.status_code == 429 else "DEGRADED"
            except Exception as exc:
                self.last_error = str(exc)[:500]
                self.status = "DEGRADED"
            self.stop_event.wait(LOOP_SECONDS)

    def _tick(self) -> None:
        settings = self._settings()
        if not MASTER_ENABLED:
            self.status = "MASTER_DISABLED"
            return
        if not self._ensure_client():
            return

        interlock = self._normal_live_conflict()
        if interlock.get("blocked") is True:
            if settings["runtimeEnabled"]:
                self._set_setting("runtime_enabled", "0")
                self._event("ERROR", "NORMAL_LIVE_INTERLOCK_TRIPPED", None, None, str(interlock.get("reason")))
                self._cancel_recorded_active("NORMAL_LIVE_INTERLOCK")
            self.status = "BLOCKED_NORMAL_LIVE"
            return
        if str(interlock.get("reason") or "").startswith("normal live interlock unavailable"):
            self.status = "BLOCKED_INTERLOCK_UNVERIFIED"
            return

        market = self._prime_market()
        if market is None:
            self.status = "WAITING_MARKET"
            return
        self._poll_books(market)
        pair = self._current_pair()
        if pair is not None and int(pair["market_id"]) != int(market["market_id"]):
            self._cancel_pair(int(pair["id"]), "MARKET_ROLLOVER")
            self._set_pair(int(pair["id"]), state="ROLLED", close_reason="MARKET_ROLLOVER")
            pair = None
        if pair is None:
            pair = self._new_pair(market)

        self._reconcile_pair(pair, market)
        if not settings["runtimeEnabled"]:
            self.status = "PAUSED"
            return
        seconds_left = max(0.0, (int(market["end_ms"]) - _now_ms()) / 1000.0)
        if seconds_left < float(settings["minimumRemainingSeconds"]):
            self.status = "WAITING_NEXT_MARKET"
            return
        orders = self._pair_orders(int(pair["id"]))
        if not orders and str(pair.get("state") or "") in {"READY", "CANCELED"}:
            self._place_pair(pair, market)
            self.status = "PAIR_ACTIVE"
            return
        self.status = "PAIR_ACTIVE"

    def _recent_pairs(self, limit: int = 12) -> list[dict[str, Any]]:
        with self.db_lock:
            rows = self.db.execute(
                "SELECT * FROM wallet_maker_clone_pairs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def _recent_events(self, limit: int = 40) -> list[dict[str, Any]]:
        with self.db_lock:
            rows = self.db.execute(
                "SELECT * FROM wallet_maker_clone_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def snapshot(self) -> dict[str, Any]:
        pair = self._current_pair()
        orders = self._pair_orders(int(pair["id"])) if pair else []
        by_side = {str(row["side"]): row for row in orders}
        now = _now_ms()
        market = dict(self.market or {})
        market["secondsLeft"] = max(0.0, (int(market.get("end_ms") or 0) - now) / 1000.0) if market.get("end_ms") else None
        summary = {
            "filledCostUsdt": sum(float(row.get("filled_usdt_amount") or 0.0) for row in orders),
            "upFilledShares": float(by_side.get("UP", {}).get("filled_share_qty") or 0.0),
            "downFilledShares": float(by_side.get("DOWN", {}).get("filled_share_qty") or 0.0),
        }
        return {
            "version": self.VERSION,
            "strategy": "R_WALLET_MAKER_CLONE",
            "realMoney": True,
            "asset": ASSET,
            "symbol": SYMBOL,
            "masterEnabled": MASTER_ENABLED,
            "status": self.status,
            "credentialSource": self.credential_source,
            "settings": self._settings(),
            "market": market,
            "books": {"UP": dict(self.books.get("UP") or {}), "DOWN": dict(self.books.get("DOWN") or {})},
            "normalLiveInterlock": self.last_interlock,
            "currentPair": pair,
            "orders": {"UP": by_side.get("UP"), "DOWN": by_side.get("DOWN")},
            "summary": summary,
            "executionPath": "UNKNOWN_UNTIL_MATCH_RECONCILIATION",
            "rules": {
                "dualSided": True,
                "placement": "concurrent LIMIT/GTC UP + DOWN",
                "softPostOnly": True,
                "secondPrePlaceBookCheck": True,
                "targetSizing": "shares*(1-price)=targetPotentialProfitUsdt, bounded by min/max cost",
                "holdFilledSharesToResolution": True,
                "automaticSell": False,
                "autoRequoteDefault": False,
                "pauseCancelsRecordedRestingOrders": True,
                "rolloverCancelsRecordedRestingOrders": True,
                "normalLiveRuntimeInterlock": True,
                "mintClassificationLive": False,
            },
            "lastError": self.last_error,
            "recentPairs": self._recent_pairs(),
            "recentEvents": self._recent_events(),
        }


class _Handler(BaseHTTPRequestHandler):
    engine: WalletMakerCloneEngine

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
        self._send(200, {"ok": True, "state": self.engine.snapshot()})

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/settings", "/api/settings"}:
            self._send(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 64_000:
                raise ValueError("invalid request body length")
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("settings body must be a JSON object")
            self._send(200, {"ok": True, "state": self.engine.update_settings(body)})
        except ValueError as exc:
            self._send(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send(500, {"ok": False, "error": str(exc)[:500]})

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def main() -> int:
    engine = WalletMakerCloneEngine()
    engine.start()
    handler = type("WalletMakerCloneHandler", (_Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{engine.VERSION} {ASSET} listening on http://{HOST}:{PORT}/state; "
        f"masterEnabled={MASTER_ENABLED}; db={DB_PATH}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        engine.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
