from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

from .core import (
    ApiHttpError,
    ApiTransportError,
    BinancePredictionTradingClient,
)
from .cross_oracle_strategies import (
    POLY_DOWN_THRESHOLD,
    POLY_UP_THRESHOLD,
    SCALP_MIN_EDGE,
    SIM_DB_PATH,
    probability_direction,
    probability_mid,
    selected_probability,
)
from .poly_quote_canary import _credential_pair

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("PREDICT_POLY_GAP_LIVE_DB", ROOT / "data" / "poly_gap_live.db"))
HOST = os.environ.get("PREDICT_POLY_GAP_LIVE_HOST", "127.0.0.1")
PORT = int(os.environ.get("PREDICT_POLY_GAP_LIVE_PORT", "8769"))
CROSS_ORACLE_URL = os.environ.get("PREDICT_CROSS_ORACLE_STATE_URL", "http://127.0.0.1:8767/state")
MASTER_ENABLED = os.environ.get("PREDICT_POLY_GAP_LIVE_ENABLED", "false").strip().lower() in {
    "1", "true", "yes", "on"
}
ACCOUNT_TYPE = os.environ.get("PREDICT_LIVE_ACCOUNT_TYPE", "SPOT").strip().upper()
if ACCOUNT_TYPE not in {"SPOT", "FUNDING"}:
    ACCOUNT_TYPE = "SPOT"

LOOP_SECONDS = max(0.02, float(os.environ.get("PREDICT_POLY_GAP_LIVE_LOOP_SECONDS", "0.05")))
BINANCE_BOOK_MIN_INTERVAL = max(
    0.05, float(os.environ.get("PREDICT_POLY_GAP_LIVE_BOOK_INTERVAL_SECONDS", "0.10"))
)
MARKET_REFRESH_SECONDS = max(
    0.10, float(os.environ.get("PREDICT_POLY_GAP_LIVE_MARKET_REFRESH_SECONDS", "0.50"))
)
MAX_POLY_AGE_MS = max(100.0, float(os.environ.get("PREDICT_POLY_GAP_LIVE_MAX_POLY_AGE_MS", "750")))
MAX_MARKET_END_SKEW_MS = max(
    1_000, int(os.environ.get("PREDICT_POLY_GAP_LIVE_MAX_MARKET_END_SKEW_MS", "10000"))
)
QUOTE_SLIPPAGE_BPS = max(0, int(os.environ.get("PREDICT_POLY_GAP_LIVE_SLIPPAGE_BPS", "100")))
MIN_QUOTE_EXPIRY_MS = max(0, int(os.environ.get("PREDICT_POLY_GAP_LIVE_MIN_QUOTE_EXPIRY_MS", "400")))
POSITION_SYNC_TIMEOUT_MS = max(
    500, int(os.environ.get("PREDICT_POLY_GAP_LIVE_POSITION_SYNC_TIMEOUT_MS", "5000"))
)
DEFAULT_STAKE_USDT = max(0.01, float(os.environ.get("PREDICT_POLY_GAP_LIVE_STAKE_USDT", "1.0")))
DEFAULT_MAX_LOSS_USDT = max(
    0.01, float(os.environ.get("PREDICT_POLY_GAP_LIVE_MAX_LOSS_USDT", "10.0"))
)
DEFAULT_MAX_LOSS_ENABLED = os.environ.get(
    "PREDICT_POLY_GAP_LIVE_MAX_LOSS_ENABLED", "true"
).strip().lower() not in {"0", "false", "no", "off"}

TERMINAL_STATES = {"CLOSED", "SETTLED", "REJECTED", "FAILED", "AMBIGUOUS", "HALTED"}
ACTIVE_STATES = {"ENTRY_QUOTE", "ENTRY_SYNC", "OPEN", "EXIT_QUOTE", "EXIT_SYNC"}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _to_wei(value: float) -> str:
    number = _decimal(value)
    if number is None or number <= 0:
        raise ValueError("amount must be positive")
    return str(int((number * Decimal(10**18)).to_integral_value(rounding=ROUND_DOWN)))


def _from_wei(value: Any) -> float | None:
    number = _decimal(value)
    if number is None or number < 0:
        return None
    return float(number / Decimal(10**18))


def _first_number(payload: Any, keys: tuple[str, ...]) -> float | None:
    if isinstance(payload, dict):
        for key in keys:
            value = _finite(payload.get(key))
            if value is not None:
                return value
        for value in payload.values():
            found = _first_number(value, keys)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _first_number(value, keys)
            if found is not None:
                return found
    return None


def _first_text(payload: Any, keys: tuple[str, ...]) -> str | None:
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if value is not None and str(value):
                return str(value)
        for value in payload.values():
            found = _first_text(value, keys)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _first_text(value, keys)
            if found:
                return found
    return None


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


class PolyGapLiveEngine:
    """Dedicated low-latency real-money executor for R_POLY_GAP_SCALP.

    The engine intentionally does not share LiveM0WEngine's (strategy, market_id)
    permanent dedupe. A market can contain many sequential rounds, but only one
    round may be active at a time and a new round is armed only after the prior
    position is confirmed flat or officially settled.

    Entry/exit placement uses signed MARKET/FOK quotes so an unfilled GTC order
    cannot remain behind and collide with a later scalp round. Any transport-
    ambiguous placement halts the current market instead of retrying blindly.
    """

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
        self.http = httpx.Client(timeout=httpx.Timeout(0.7, connect=0.3))
        self.client: BinancePredictionTradingClient | None = None
        self.metadata_client: BinancePredictionTradingClient | None = None
        self.wallet_address: str | None = None
        self.wallet_id: str | None = None
        self.credential_source = "UNAVAILABLE"
        self.market_cache: dict[str, Any] | None = None
        self.last_market_refresh = 0.0
        self.last_binance_book_at = 0.0
        self.last_poly_direction: str | None = None
        self.last_poly_generation: int | None = None
        self.last_poly: dict[str, Any] | None = None
        self.last_binance: dict[str, Any] | None = None
        self.last_entry_latency: dict[str, Any] | None = None
        self.last_exit_latency: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.status = "MASTER_DISABLED" if not MASTER_ENABLED else "PAUSED"
        self.halted_market_id: int | None = None
        self.halted_reason: str | None = None
        self._create_schema()
        self._ensure_defaults()

    def _create_schema(self) -> None:
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS poly_gap_live_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS poly_gap_live_rounds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id INTEGER NOT NULL,
                    topic_id INTEGER NOT NULL,
                    round_no INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    stake_usdt REAL NOT NULL,
                    entry_signal_at_ms INTEGER,
                    entry_poly_selected_mid REAL,
                    entry_binance_ask REAL,
                    entry_edge REAL,
                    entry_quote_started_at_ms INTEGER,
                    entry_quote_completed_at_ms INTEGER,
                    entry_quote_rtt_ms REAL,
                    entry_quote_average REAL,
                    entry_quote_amount_in_wei TEXT,
                    entry_quote_amount_out_wei TEXT,
                    entry_quote_expire_at_ms INTEGER,
                    entry_order_id TEXT,
                    entry_placed_at_ms INTEGER,
                    entry_sync_started_at_ms INTEGER,
                    entry_cost_usdt REAL,
                    shares REAL,
                    exit_signal_at_ms INTEGER,
                    exit_quote_started_at_ms INTEGER,
                    exit_quote_completed_at_ms INTEGER,
                    exit_quote_rtt_ms REAL,
                    exit_quote_average REAL,
                    exit_quote_amount_in_wei TEXT,
                    exit_quote_amount_out_wei TEXT,
                    exit_order_id TEXT,
                    exit_placed_at_ms INTEGER,
                    exit_sync_started_at_ms INTEGER,
                    exit_proceeds_usdt REAL,
                    pnl_usdt REAL,
                    official_winner TEXT,
                    close_reason TEXT,
                    error_kind TEXT,
                    error_message TEXT,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    UNIQUE(market_id, round_no)
                );
                CREATE INDEX IF NOT EXISTS idx_poly_gap_live_rounds_market
                    ON poly_gap_live_rounds(market_id, round_no);
                CREATE INDEX IF NOT EXISTS idx_poly_gap_live_rounds_state
                    ON poly_gap_live_rounds(state, id);
                CREATE TABLE IF NOT EXISTS poly_gap_live_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    at_ms INTEGER NOT NULL,
                    level TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    market_id INTEGER,
                    round_id INTEGER,
                    message TEXT NOT NULL
                );
                """
            )
            self.db.commit()

    def _ensure_defaults(self) -> None:
        defaults = {
            "runtime_enabled": "0",
            "stake_usdt": f"{DEFAULT_STAKE_USDT:.8f}",
            "max_loss_enabled": "1" if DEFAULT_MAX_LOSS_ENABLED else "0",
            "max_loss_usdt": f"{DEFAULT_MAX_LOSS_USDT:.8f}",
            "loss_reset_round_id": "0",
            "loss_tripped": "0",
        }
        now = _now_ms()
        with self.db_lock:
            for key, value in defaults.items():
                self.db.execute(
                    "INSERT OR IGNORE INTO poly_gap_live_settings(key,value,updated_at_ms) VALUES(?,?,?)",
                    (key, value, now),
                )
            self.db.commit()

    def _setting(self, key: str, fallback: str) -> str:
        with self.db_lock:
            row = self.db.execute(
                "SELECT value FROM poly_gap_live_settings WHERE key=?", (key,)
            ).fetchone()
        return str(row["value"]) if row else fallback

    def _set_setting(self, key: str, value: Any) -> None:
        with self.db_lock:
            self.db.execute(
                """INSERT INTO poly_gap_live_settings(key,value,updated_at_ms)
                   VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET
                   value=excluded.value, updated_at_ms=excluded.updated_at_ms""",
                (key, str(value), _now_ms()),
            )
            self.db.commit()

    def _settings(self) -> dict[str, Any]:
        return {
            "runtimeEnabled": self._setting("runtime_enabled", "0") == "1",
            "stakeUsdt": float(self._setting("stake_usdt", str(DEFAULT_STAKE_USDT))),
            "maximumLossEnabled": self._setting("max_loss_enabled", "1") == "1",
            "maximumLossUsdt": float(self._setting("max_loss_usdt", str(DEFAULT_MAX_LOSS_USDT))),
            "lossTripped": self._setting("loss_tripped", "0") == "1",
            "minimumEdge": SCALP_MIN_EDGE,
            "polyUpThreshold": POLY_UP_THRESHOLD,
            "polyDownThreshold": POLY_DOWN_THRESHOLD,
        }

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = {"runtimeEnabled", "stakeUsdt", "maximumLossEnabled", "maximumLossUsdt", "resetLoss"}
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError("unsupported settings: " + ", ".join(unknown))
        if "stakeUsdt" in values:
            stake = float(values["stakeUsdt"])
            if not math.isfinite(stake) or not 0.01 <= stake <= 100.0:
                raise ValueError("stakeUsdt must be between 0.01 and 100 USDT")
            self._set_setting("stake_usdt", f"{stake:.8f}")
        if "maximumLossEnabled" in values:
            self._set_setting("max_loss_enabled", "1" if bool(values["maximumLossEnabled"]) else "0")
        if "maximumLossUsdt" in values:
            limit = float(values["maximumLossUsdt"])
            if not math.isfinite(limit) or not 0.01 <= limit <= 1_000_000:
                raise ValueError("maximumLossUsdt must be between 0.01 and 1,000,000 USDT")
            self._set_setting("max_loss_usdt", f"{limit:.8f}")
        if values.get("resetLoss") is True:
            with self.db_lock:
                row = self.db.execute("SELECT COALESCE(MAX(id),0) AS id FROM poly_gap_live_rounds").fetchone()
            self._set_setting("loss_reset_round_id", int(row["id"] if row else 0))
            self._set_setting("loss_tripped", "0")
            self._event("WARN", "LOSS_COUNTER_RESET", None, None, "Poly GAP Live maximum-loss counter reset")
        if "runtimeEnabled" in values:
            enabled = bool(values["runtimeEnabled"])
            if enabled and not MASTER_ENABLED:
                raise ValueError("PREDICT_POLY_GAP_LIVE_ENABLED master switch is off")
            if enabled and self._setting("loss_tripped", "0") == "1":
                raise ValueError("maximum-loss guard is tripped; reset the loss counter before resuming")
            self._set_setting("runtime_enabled", "1" if enabled else "0")
            self._event(
                "WARN" if not enabled else "INFO",
                "RUNTIME_RESUMED" if enabled else "RUNTIME_PAUSED",
                None,
                None,
                "Poly GAP Live resumed" if enabled else "Poly GAP Live paused for new entries",
            )
            if enabled:
                self._ensure_clients()
        return self.snapshot()

    def _event(
        self,
        level: str,
        event_type: str,
        market_id: int | None,
        round_id: int | None,
        message: str,
    ) -> None:
        with self.db_lock:
            self.db.execute(
                "INSERT INTO poly_gap_live_events(at_ms,level,event_type,market_id,round_id,message) VALUES(?,?,?,?,?,?)",
                (_now_ms(), level, event_type, market_id, round_id, str(message)[:800]),
            )
            self.db.commit()

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, name="poly-gap-live", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3.0)
        for client in (self.client, self.metadata_client):
            try:
                if client:
                    client.close()
            except Exception:
                pass
        self.http.close()
        with self.db_lock:
            self.db.commit()
            self.db.close()

    def _ensure_clients(self) -> bool:
        with self.lock:
            if self.client is not None and self.metadata_client is not None and self.wallet_address and self.wallet_id:
                return True
        api_key, api_secret, source = _credential_pair()
        self.credential_source = source
        if not api_key or not api_secret:
            self.last_error = "Binance live credentials unavailable"
            self.status = "CONFIG_REQUIRED"
            return False
        try:
            execution = BinancePredictionTradingClient(api_key, api_secret)
            metadata = BinancePredictionTradingClient(api_key, api_secret)
            wallets = execution.wallets().get("wallets") or []
            if len(wallets) != 1:
                raise RuntimeError(f"expected one Prediction wallet, got {len(wallets)}")
            wallet_address = str(wallets[0].get("walletAddress") or "")
            wallet_id = str(wallets[0].get("walletId") or "")
            if not wallet_address or not wallet_id:
                raise RuntimeError("Prediction wallet metadata incomplete")
            execution.server_timestamp_ms()
            metadata.server_timestamp_ms()
            with self.lock:
                self.client = execution
                self.metadata_client = metadata
                self.wallet_address = wallet_address
                self.wallet_id = wallet_id
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = str(exc)[:500]
            self.status = "BLOCKED_PREFLIGHT"
            return False

    def _poly_state(self) -> dict[str, Any] | None:
        try:
            response = self.http.get(CROSS_ORACLE_URL)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self.last_error = f"Poly state: {str(exc)[:300]}"
            return None
        if not isinstance(payload, dict):
            return None
        continuity = payload.get("continuity")
        if isinstance(continuity, dict) and continuity.get("gapActive") is True:
            self.last_error = "Polymarket continuity gap active"
            return None
        poly = payload.get("polymarket")
        if not isinstance(poly, dict) or str(poly.get("status")) != "LIVE":
            self.last_error = "Polymarket feed is not LIVE"
            return None
        age = _finite(poly.get("ageMs"))
        if age is None or age > MAX_POLY_AGE_MS:
            self.last_error = f"Polymarket quote stale: {age}ms"
            return None
        up = poly.get("up")
        down = poly.get("down")
        market = poly.get("market")
        if not isinstance(up, dict) or not isinstance(down, dict) or not isinstance(market, dict):
            return None
        up_mid = probability_mid(up.get("bestBid"), up.get("bestAsk"))
        if up_mid is None:
            return None
        direction = probability_direction(up_mid)
        result = {
            "upMid": up_mid,
            "direction": direction,
            "selectedMid": selected_probability(up_mid, direction) if direction else None,
            "ageMs": age,
            "slug": market.get("slug"),
            "windowEndMs": int(market.get("windowEndMs") or 0),
            "receivedTimestampMs": poly.get("receivedTimestampMs"),
            "gapGeneration": (continuity or {}).get("gapGeneration") if isinstance(continuity, dict) else None,
        }
        self.last_poly = result
        return result

    def _prime_market(self, force: bool = False) -> dict[str, Any] | None:
        now = time.monotonic()
        with self.lock:
            cached = dict(self.market_cache or {})
            metadata = self.metadata_client
        now_ms = _now_ms()
        if not force and cached and int(cached.get("end_ms") or 0) > now_ms + 500:
            return cached
        if not force and now - self.last_market_refresh < MARKET_REFRESH_SECONDS:
            return cached or None
        self.last_market_refresh = now
        if metadata is None and not self._ensure_clients():
            return None
        with self.lock:
            metadata = self.metadata_client
        if metadata is None:
            return None
        summary = metadata.find_market_summary("BTCUSDT", max_pages=1)
        if not isinstance(summary, dict):
            self.last_error = "Binance Prediction current BTC 5m market unavailable"
            return None
        selected = summary.get("_selectedMarket")
        if not isinstance(selected, dict):
            return None
        market = selected.get("market")
        up = selected.get("up")
        down = selected.get("down")
        if not isinstance(market, dict) or not isinstance(up, dict) or not isinstance(down, dict):
            return None
        cache = {
            "market_id": int(market.get("marketId") or 0),
            "topic_id": int(summary.get("marketTopicId") or 0),
            "up_token_id": str(up.get("tokenId") or ""),
            "down_token_id": str(down.get("tokenId") or ""),
            "fee_rate_bps": int(summary.get("feeRateBps") or market.get("feeRateBps") or 200),
            "end_ms": int(summary.get("endDate") or 0),
        }
        if cache["market_id"] <= 0 or not cache["up_token_id"] or not cache["down_token_id"]:
            self.last_error = "Binance Prediction current market token metadata incomplete"
            return None
        with self.lock:
            self.market_cache = cache
        if self.halted_market_id is not None and self.halted_market_id != cache["market_id"]:
            self.halted_market_id = None
            self.halted_reason = None
        return dict(cache)

    def _current_active_round(self) -> dict[str, Any] | None:
        with self.db_lock:
            row = self.db.execute(
                """SELECT * FROM poly_gap_live_rounds
                    WHERE state IN ('ENTRY_QUOTE','ENTRY_SYNC','OPEN','EXIT_QUOTE','EXIT_SYNC')
                    ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        return dict(row) if row else None

    def _next_round_no(self, market_id: int) -> int:
        with self.db_lock:
            row = self.db.execute(
                "SELECT COALESCE(MAX(round_no),0)+1 AS n FROM poly_gap_live_rounds WHERE market_id=?",
                (int(market_id),),
            ).fetchone()
        return int(row["n"] if row else 1)

    def _insert_round(
        self,
        *,
        market: dict[str, Any],
        side: str,
        token_id: str,
        stake: float,
        poly_selected: float,
        ask: float,
        edge: float,
    ) -> dict[str, Any]:
        now = _now_ms()
        round_no = self._next_round_no(int(market["market_id"]))
        with self.db_lock:
            cursor = self.db.execute(
                """INSERT INTO poly_gap_live_rounds(
                       market_id,topic_id,round_no,side,token_id,state,stake_usdt,
                       entry_signal_at_ms,entry_poly_selected_mid,entry_binance_ask,
                       entry_edge,created_at_ms,updated_at_ms
                   ) VALUES(?,?,?,?,?,'ENTRY_QUOTE',?,?,?,?,?,?,?)""",
                (
                    int(market["market_id"]), int(market["topic_id"]), round_no,
                    side, token_id, stake, now, poly_selected, ask, edge, now, now,
                ),
            )
            self.db.commit()
            row = self.db.execute("SELECT * FROM poly_gap_live_rounds WHERE id=?", (cursor.lastrowid,)).fetchone()
        assert row is not None
        self._event("INFO", "ENTRY_SIGNAL", int(market["market_id"]), int(row["id"]), f"round {round_no} {side} edge {edge:.4f}")
        return dict(row)

    def _update_round(self, round_id: int, **values: Any) -> dict[str, Any] | None:
        allowed = {
            "state", "entry_quote_started_at_ms", "entry_quote_completed_at_ms",
            "entry_quote_rtt_ms", "entry_quote_average", "entry_quote_amount_in_wei",
            "entry_quote_amount_out_wei", "entry_quote_expire_at_ms", "entry_order_id",
            "entry_placed_at_ms", "entry_sync_started_at_ms", "entry_cost_usdt", "shares",
            "exit_signal_at_ms", "exit_quote_started_at_ms", "exit_quote_completed_at_ms",
            "exit_quote_rtt_ms", "exit_quote_average", "exit_quote_amount_in_wei",
            "exit_quote_amount_out_wei", "exit_order_id", "exit_placed_at_ms",
            "exit_sync_started_at_ms", "exit_proceeds_usdt", "pnl_usdt",
            "official_winner", "close_reason", "error_kind", "error_message",
        }
        updates = {key: value for key, value in values.items() if key in allowed}
        updates["updated_at_ms"] = _now_ms()
        if not updates:
            return None
        assignments = ",".join(f"{key}=?" for key in updates)
        with self.db_lock:
            self.db.execute(
                f"UPDATE poly_gap_live_rounds SET {assignments} WHERE id=?",
                (*updates.values(), int(round_id)),
            )
            self.db.commit()
            row = self.db.execute("SELECT * FROM poly_gap_live_rounds WHERE id=?", (int(round_id),)).fetchone()
        return dict(row) if row else None

    def _entry_allowed(self) -> bool:
        settings = self._settings()
        return bool(
            MASTER_ENABLED
            and settings["runtimeEnabled"]
            and not settings["lossTripped"]
            and self.halted_market_id is None
        )

    def _direct_book(self, market: dict[str, Any], side: str) -> tuple[float | None, float | None, float]:
        now = time.monotonic()
        wait = BINANCE_BOOK_MIN_INTERVAL - (now - self.last_binance_book_at)
        if wait > 0:
            self.stop_event.wait(wait)
        with self.lock:
            client = self.client
        if client is None:
            return None, None, 0.0
        token_id = str(market[f"{side.lower()}_token_id"])
        started = time.monotonic()
        book = client.orderbook(int(market["market_id"]), token_id)
        rtt_ms = max(0.0, (time.monotonic() - started) * 1000.0)
        self.last_binance_book_at = time.monotonic()
        ask, ask_size = _best_level(book, "ask")
        self.last_binance = {
            "marketId": int(market["market_id"]), "side": side,
            "ask": ask, "askSize": ask_size, "bookRttMs": rtt_ms,
            "observedAtMs": _now_ms(),
        }
        return ask, ask_size, rtt_ms

    def _quote_expiry_safe(self, quote: dict[str, Any], client: BinancePredictionTradingClient) -> bool:
        expire_at = int(quote.get("expireAt") or 0)
        if expire_at <= 0:
            return True
        return expire_at - int(client.server_timestamp_ms()) >= MIN_QUOTE_EXPIRY_MS

    def _open_round(self, row: dict[str, Any], poly: dict[str, Any]) -> None:
        if not self._entry_allowed():
            self._update_round(int(row["id"]), state="REJECTED", close_reason="ENTRY_DISABLED")
            return
        with self.lock:
            client = self.client
            wallet_address = self.wallet_address
            wallet_id = self.wallet_id
        if client is None or not wallet_address or not wallet_id:
            self._update_round(int(row["id"]), state="FAILED", error_kind="PREFLIGHT", error_message="live client unavailable")
            return
        poly_selected = _finite(poly.get("selectedMid"))
        if poly_selected is None:
            self._update_round(int(row["id"]), state="REJECTED", close_reason="POLY_SIGNAL_GONE")
            return
        price_ceiling = poly_selected - SCALP_MIN_EDGE
        if price_ceiling <= 0:
            self._update_round(int(row["id"]), state="REJECTED", close_reason="EDGE_TOO_SMALL")
            return
        amount_in = _to_wei(float(row["stake_usdt"]))
        started_ms = _now_ms()
        started = time.monotonic()
        self._update_round(int(row["id"]), entry_quote_started_at_ms=started_ms)
        try:
            quote = client.get_quote(
                wallet_address=wallet_address,
                token_id=str(row["token_id"]),
                amount_in_wei=amount_in,
                price_limit=None,
                slippage_bps=QUOTE_SLIPPAGE_BPS,
                fee_rate_bps=int((self.market_cache or {}).get("fee_rate_bps") or 200),
                funding_source="MPC",
                side="BUY",
                order_type="MARKET",
            )
        except Exception as exc:
            self._update_round(
                int(row["id"]), state="REJECTED", error_kind="ENTRY_QUOTE_REJECTED",
                error_message=str(exc)[:500], close_reason="ENTRY_QUOTE_REJECTED",
            )
            return
        completed_ms = _now_ms()
        rtt_ms = max(0.0, (time.monotonic() - started) * 1000.0)
        average = _finite(quote.get("averagePrice"))
        quote_id = str(quote.get("quoteId") or "")
        amount_out_wei = str(quote.get("amountOut") or "")
        cost_usdt = _from_wei(quote.get("amountIn"))
        shares = _from_wei(quote.get("amountOut"))
        current_poly = self._poly_state()
        current_selected = _finite((current_poly or {}).get("selectedMid"))
        current_direction = (current_poly or {}).get("direction")
        edge_after = (
            current_selected - average
            if current_selected is not None and average is not None and current_direction == row["side"]
            else None
        )
        self.last_entry_latency = {
            "signalToQuoteStartMs": max(0, started_ms - int(row["entry_signal_at_ms"] or started_ms)),
            "quoteRttMs": rtt_ms,
            "signalToQuoteResponseMs": max(0, completed_ms - int(row["entry_signal_at_ms"] or completed_ms)),
            "edgeAfterQuote": edge_after,
        }
        self._update_round(
            int(row["id"]),
            entry_quote_completed_at_ms=completed_ms,
            entry_quote_rtt_ms=rtt_ms,
            entry_quote_average=average,
            entry_quote_amount_in_wei=str(quote.get("amountIn") or ""),
            entry_quote_amount_out_wei=amount_out_wei,
            entry_quote_expire_at_ms=int(quote.get("expireAt") or 0) or None,
            entry_cost_usdt=cost_usdt,
            shares=shares,
        )
        if (
            not quote_id or average is None or shares is None or shares <= 0
            or edge_after is None or edge_after + 1e-12 < SCALP_MIN_EDGE
            or not self._quote_expiry_safe(quote, client)
        ):
            self._update_round(int(row["id"]), state="REJECTED", close_reason="SIGNED_QUOTE_EDGE_GONE")
            return
        try:
            placed = client.place_market_order(
                wallet_address=wallet_address,
                wallet_id=wallet_id,
                quote_id=quote_id,
                slippage_bps=QUOTE_SLIPPAGE_BPS,
                account_type=ACCOUNT_TYPE,
                funding_source="MPC",
            )
        except ApiTransportError as exc:
            self._halt_market(int(row["market_id"]), f"ambiguous BUY placement: {exc}", int(row["id"]))
            self._update_round(int(row["id"]), state="AMBIGUOUS", error_kind="ENTRY_PLACE_AMBIGUOUS", error_message=str(exc)[:500])
            return
        except Exception as exc:
            self._update_round(int(row["id"]), state="FAILED", error_kind="ENTRY_PLACE_REJECTED", error_message=str(exc)[:500], close_reason="ENTRY_PLACE_REJECTED")
            return
        placed_ms = _now_ms()
        order_id = _first_text(placed, ("orderId", "order_id", "id"))
        self._update_round(
            int(row["id"]), state="ENTRY_SYNC", entry_order_id=order_id,
            entry_placed_at_ms=placed_ms, entry_sync_started_at_ms=placed_ms,
        )
        self._event("WARN", "ENTRY_PLACED", int(row["market_id"]), int(row["id"]), f"round {row['round_no']} {row['side']} MARKET/FOK submitted")

    def _position_shares(self, token_id: str) -> float | None:
        with self.lock:
            client = self.client
            wallet_address = self.wallet_address
        if client is None or not wallet_address:
            return None
        payload = client.position_by_token(wallet_address, token_id)
        shares = _first_number(
            payload,
            ("availableShares", "availableShareQty", "shares", "shareQty", "quantity"),
        )
        return shares

    def _sync_entry(self, row: dict[str, Any]) -> None:
        try:
            shares = self._position_shares(str(row["token_id"]))
        except Exception as exc:
            self.last_error = f"entry position sync: {str(exc)[:300]}"
            shares = None
        expected = _finite(row.get("shares")) or 0.0
        if shares is not None and shares > 0:
            self._update_round(int(row["id"]), state="OPEN", shares=shares)
            self._event("INFO", "ENTRY_CONFIRMED", int(row["market_id"]), int(row["id"]), f"position confirmed {shares:.8f} shares")
            return
        started = int(row.get("entry_sync_started_at_ms") or _now_ms())
        if _now_ms() - started > POSITION_SYNC_TIMEOUT_MS:
            self._halt_market(int(row["market_id"]), "BUY placement succeeded but position could not be confirmed", int(row["id"]))
            self._update_round(
                int(row["id"]), state="AMBIGUOUS", error_kind="ENTRY_POSITION_UNCONFIRMED",
                error_message=f"expected about {expected:.8f} shares but wallet position was not confirmed",
            )

    def _exit_round(self, row: dict[str, Any], signal_ms: int) -> None:
        with self.lock:
            client = self.client
            wallet_address = self.wallet_address
            wallet_id = self.wallet_id
        if not MASTER_ENABLED or client is None or not wallet_address or not wallet_id:
            self._halt_market(int(row["market_id"]), "exit required but live master/client is unavailable", int(row["id"]))
            return
        try:
            available = self._position_shares(str(row["token_id"]))
        except Exception as exc:
            self.last_error = f"exit position read: {str(exc)[:300]}"
            return
        if available is not None and available <= 1e-9:
            # Position already flat; finalize conservatively from any recorded exit quote.
            proceeds = _finite(row.get("exit_proceeds_usdt")) or 0.0
            cost = _finite(row.get("entry_cost_usdt")) or float(row["stake_usdt"])
            self._finish_round(row, proceeds - cost, proceeds, "POSITION_ALREADY_FLAT")
            return
        if available is None or available <= 0:
            return
        self._update_round(int(row["id"]), state="EXIT_QUOTE", exit_signal_at_ms=signal_ms)
        started_ms = _now_ms()
        started = time.monotonic()
        self._update_round(int(row["id"]), exit_quote_started_at_ms=started_ms)
        try:
            quote = client.get_quote(
                wallet_address=wallet_address,
                token_id=str(row["token_id"]),
                amount_in_wei=_to_wei(available),
                price_limit=None,
                slippage_bps=QUOTE_SLIPPAGE_BPS,
                fee_rate_bps=int((self.market_cache or {}).get("fee_rate_bps") or 200),
                funding_source="MPC",
                side="SELL",
                order_type="MARKET",
            )
        except Exception as exc:
            self._update_round(int(row["id"]), state="OPEN", error_kind="EXIT_QUOTE_REJECTED", error_message=str(exc)[:500])
            return
        completed_ms = _now_ms()
        rtt_ms = max(0.0, (time.monotonic() - started) * 1000.0)
        average = _finite(quote.get("averagePrice"))
        quote_id = str(quote.get("quoteId") or "")
        proceeds = _from_wei(quote.get("amountOut"))
        self.last_exit_latency = {
            "signalToQuoteStartMs": max(0, started_ms - signal_ms),
            "quoteRttMs": rtt_ms,
            "signalToQuoteResponseMs": max(0, completed_ms - signal_ms),
        }
        self._update_round(
            int(row["id"]), exit_quote_completed_at_ms=completed_ms,
            exit_quote_rtt_ms=rtt_ms, exit_quote_average=average,
            exit_quote_amount_in_wei=str(quote.get("amountIn") or ""),
            exit_quote_amount_out_wei=str(quote.get("amountOut") or ""),
            exit_proceeds_usdt=proceeds,
        )
        if not quote_id or proceeds is None or proceeds < 0 or not self._quote_expiry_safe(quote, client):
            self._update_round(int(row["id"]), state="OPEN", error_kind="EXIT_QUOTE_INVALID", error_message="SELL quote lacked executable id/proceeds/expiry")
            return
        try:
            placed = client.place_market_order(
                wallet_address=wallet_address,
                wallet_id=wallet_id,
                quote_id=quote_id,
                slippage_bps=QUOTE_SLIPPAGE_BPS,
                account_type=ACCOUNT_TYPE,
                funding_source="MPC",
            )
        except ApiTransportError as exc:
            self._halt_market(int(row["market_id"]), f"ambiguous SELL placement: {exc}", int(row["id"]))
            self._update_round(int(row["id"]), state="AMBIGUOUS", error_kind="EXIT_PLACE_AMBIGUOUS", error_message=str(exc)[:500])
            return
        except Exception as exc:
            self._update_round(int(row["id"]), state="OPEN", error_kind="EXIT_PLACE_REJECTED", error_message=str(exc)[:500])
            return
        placed_ms = _now_ms()
        order_id = _first_text(placed, ("orderId", "order_id", "id"))
        self._update_round(
            int(row["id"]), state="EXIT_SYNC", exit_order_id=order_id,
            exit_placed_at_ms=placed_ms, exit_sync_started_at_ms=placed_ms,
        )
        self._event("WARN", "EXIT_PLACED", int(row["market_id"]), int(row["id"]), f"round {row['round_no']} SELL MARKET/FOK submitted")

    def _sync_exit(self, row: dict[str, Any]) -> None:
        try:
            shares = self._position_shares(str(row["token_id"]))
        except Exception as exc:
            self.last_error = f"exit position sync: {str(exc)[:300]}"
            return
        if shares is not None and shares <= 1e-9:
            proceeds = _finite(row.get("exit_proceeds_usdt")) or 0.0
            cost = _finite(row.get("entry_cost_usdt")) or float(row["stake_usdt"])
            self._finish_round(row, proceeds - cost, proceeds, "POLY_DIRECTION_FLIP")
            return
        started = int(row.get("exit_sync_started_at_ms") or _now_ms())
        if _now_ms() - started > POSITION_SYNC_TIMEOUT_MS:
            # A FOK should not leave a partial resting order. If shares remain,
            # return to OPEN so a fresh SELL quote can be attempted on the next tick.
            self._update_round(int(row["id"]), state="OPEN", shares=shares, error_kind="EXIT_NOT_FLAT", error_message="SELL FOK did not leave the wallet flat; retrying from a fresh position read")

    def _finish_round(self, row: dict[str, Any], pnl: float, proceeds: float, reason: str) -> None:
        self._update_round(
            int(row["id"]), state="CLOSED", exit_proceeds_usdt=proceeds,
            pnl_usdt=pnl, close_reason=reason, error_kind=None, error_message=None,
        )
        self._event("INFO" if pnl >= 0 else "WARN", "ROUND_CLOSED", int(row["market_id"]), int(row["id"]), f"round {row['round_no']} PnL {pnl:+.6f} USDT")
        self._check_max_loss()

    def _official_winner(self, market_id: int) -> str | None:
        if not SIM_DB_PATH.exists():
            return None
        try:
            db = sqlite3.connect(f"file:{SIM_DB_PATH}?mode=ro", uri=True, timeout=0.5)
            row = db.execute(
                """SELECT official_winner FROM market_settlements
                    WHERE market_id=? AND status='OFFICIAL'
                      AND official_winner IN ('UP','DOWN') LIMIT 1""",
                (int(market_id),),
            ).fetchone()
            db.close()
        except (sqlite3.Error, OSError):
            return None
        return str(row[0]) if row else None

    def _settle_hold(self, row: dict[str, Any]) -> None:
        winner = self._official_winner(int(row["market_id"]))
        if winner is None:
            return
        shares = _finite(row.get("shares")) or 0.0
        cost = _finite(row.get("entry_cost_usdt")) or float(row["stake_usdt"])
        payout = shares if str(row["side"]) == winner else 0.0
        pnl = payout - cost
        self._update_round(
            int(row["id"]), state="SETTLED", official_winner=winner,
            exit_proceeds_usdt=payout, pnl_usdt=pnl,
            close_reason="OFFICIAL_SETTLEMENT",
        )
        self._event("INFO" if pnl >= 0 else "WARN", "ROUND_SETTLED", int(row["market_id"]), int(row["id"]), f"official {winner}; PnL {pnl:+.6f} USDT")
        self._check_max_loss()

    def _loss_state(self) -> dict[str, Any]:
        reset_id = int(self._setting("loss_reset_round_id", "0"))
        with self.db_lock:
            row = self.db.execute(
                """SELECT COUNT(*) AS settled, COALESCE(SUM(pnl_usdt),0) AS pnl
                    FROM poly_gap_live_rounds
                    WHERE id>? AND state IN ('CLOSED','SETTLED') AND pnl_usdt IS NOT NULL""",
                (reset_id,),
            ).fetchone()
        pnl = float(row["pnl"] if row else 0.0)
        current_loss = max(0.0, -pnl)
        settings = self._settings()
        limit = float(settings["maximumLossUsdt"])
        return {
            "netPnlUsdt": pnl,
            "currentLossUsdt": current_loss,
            "maximumLossUsdt": limit,
            "remainingBeforePauseUsdt": max(0.0, limit - current_loss),
            "settledRounds": int(row["settled"] if row else 0),
            "enabled": bool(settings["maximumLossEnabled"]),
            "tripped": bool(settings["lossTripped"]),
        }

    def _check_max_loss(self) -> None:
        state = self._loss_state()
        if not state["enabled"] or state["currentLossUsdt"] + 1e-12 < state["maximumLossUsdt"]:
            return
        if self._setting("loss_tripped", "0") != "1":
            self._set_setting("loss_tripped", "1")
            self._set_setting("runtime_enabled", "0")
            self._event("ERROR", "MAXIMUM_LOSS_TRIPPED", None, None, f"loss {state['currentLossUsdt']:.4f} reached limit {state['maximumLossUsdt']:.4f}; new entries paused")

    def _halt_market(self, market_id: int, reason: str, round_id: int | None = None) -> None:
        self.halted_market_id = int(market_id)
        self.halted_reason = str(reason)[:500]
        self._event("ERROR", "MARKET_HALTED", int(market_id), round_id, reason)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._tick()
            except ApiHttpError as exc:
                self.last_error = str(exc)[:500]
                if exc.status_code == 429:
                    self.stop_event.wait(max(0.5, float(getattr(self.client, "retry_after_seconds", 1.0) or 1.0)))
            except Exception as exc:
                self.last_error = str(exc)[:500]
                self.status = "DEGRADED"
            self.stop_event.wait(LOOP_SECONDS)

    def _tick(self) -> None:
        settings = self._settings()
        active = self._current_active_round()
        if active is not None:
            market = self._prime_market()
            now_ms = _now_ms()
            if market and int(active["market_id"]) == int(market["market_id"]) and now_ms >= int(market["end_ms"] or 0):
                self._settle_hold(active)
                return
            if str(active["state"]) == "ENTRY_SYNC":
                self._sync_entry(active)
                return
            if str(active["state"]) == "EXIT_SYNC":
                self._sync_exit(active)
                return
            if str(active["state"]) in {"ENTRY_QUOTE", "EXIT_QUOTE"}:
                # These states are synchronous and should not survive a restart.
                self._halt_market(int(active["market_id"]), f"process restarted while round was in {active['state']}", int(active["id"]))
                self._update_round(int(active["id"]), state="AMBIGUOUS", error_kind="PROCESS_RESTART_DURING_WRITE", error_message="manual reconciliation required")
                return
            if str(active["state"]) == "OPEN":
                poly = self._poly_state()
                if poly is None:
                    self.status = "DEGRADED"
                    return
                new_direction = poly.get("direction")
                previous = self.last_poly_direction
                self.last_poly_direction = str(new_direction) if new_direction else None
                if new_direction and str(new_direction) != str(active["side"]):
                    self._exit_round(active, _now_ms())
                else:
                    self.status = "MANAGING_POSITION"
                return

        if not MASTER_ENABLED:
            self.status = "MASTER_DISABLED"
            return
        if not settings["runtimeEnabled"] or settings["lossTripped"]:
            self.status = "MAX_LOSS_TRIPPED" if settings["lossTripped"] else "PAUSED"
            return
        if not self._ensure_clients():
            return
        market = self._prime_market()
        poly = self._poly_state()
        if market is None or poly is None:
            self.status = "WAITING_DATA"
            return
        if self.halted_market_id == int(market["market_id"]):
            self.status = "MARKET_HALTED"
            return
        poly_end = int(poly.get("windowEndMs") or 0)
        if poly_end <= 0 or abs(poly_end - int(market["end_ms"])) > MAX_MARKET_END_SKEW_MS:
            self.status = "MARKET_MISMATCH"
            self.last_error = f"Poly/Binance end-time mismatch: {poly_end} vs {market['end_ms']}"
            return
        direction = poly.get("direction")
        self.last_poly_direction = str(direction) if direction else None
        if direction not in {"UP", "DOWN"}:
            self.status = "ARMED_WAITING_GAP"
            return
        selected_mid = _finite(poly.get("selectedMid"))
        if selected_mid is None:
            return
        ask, ask_size, book_rtt_ms = self._direct_book(market, str(direction))
        if ask is None:
            self.status = "WAITING_BINANCE_BOOK"
            return
        edge = selected_mid - ask
        self.status = "ARMED_WAITING_GAP"
        if edge + 1e-12 < SCALP_MIN_EDGE:
            return
        stake = float(settings["stakeUsdt"])
        # Visible depth is advisory only; signed quote is authoritative. Skip an
        # obviously impossible first level to avoid unnecessary signed quotes.
        if ask_size is not None and ask_size > 0 and ask_size * ask < min(stake, 0.01):
            return
        token_id = str(market[f"{str(direction).lower()}_token_id"])
        row = self._insert_round(
            market=market, side=str(direction), token_id=token_id, stake=stake,
            poly_selected=selected_mid, ask=ask, edge=edge,
        )
        self._open_round(row, poly)

    def snapshot(self) -> dict[str, Any]:
        settings = self._settings()
        loss = self._loss_state()
        active = self._current_active_round()
        with self.db_lock:
            recent = [
                dict(row) for row in self.db.execute(
                    "SELECT * FROM poly_gap_live_rounds ORDER BY id DESC LIMIT 30"
                ).fetchall()
            ]
            events = [
                dict(row) for row in self.db.execute(
                    "SELECT * FROM poly_gap_live_events ORDER BY id DESC LIMIT 30"
                ).fetchall()
            ]
            summary = self.db.execute(
                """SELECT COUNT(*) AS rounds,
                          COALESCE(SUM(CASE WHEN state IN ('CLOSED','SETTLED') THEN 1 ELSE 0 END),0) AS closed,
                          COALESCE(SUM(CASE WHEN pnl_usdt>0 THEN 1 ELSE 0 END),0) AS wins,
                          COALESCE(SUM(CASE WHEN pnl_usdt<0 THEN 1 ELSE 0 END),0) AS losses,
                          COALESCE(SUM(CASE WHEN state IN ('CLOSED','SETTLED') THEN pnl_usdt ELSE 0 END),0) AS pnl
                     FROM poly_gap_live_rounds"""
            ).fetchone()
        wins = int(summary["wins"] if summary else 0)
        losses = int(summary["losses"] if summary else 0)
        return {
            "version": "POLY_GAP_DEDICATED_LIVE_V1",
            "strategy": "R_POLY_GAP_SCALP_LIVE",
            "realMoney": True,
            "masterEnabled": MASTER_ENABLED,
            "status": self.status,
            "credentialSource": self.credential_source,
            "accountType": ACCOUNT_TYPE,
            "settings": settings,
            "lossGuard": loss,
            "rules": {
                "sameMarketMultipleRounds": True,
                "oneActiveRoundAtATime": True,
                "rearmRequiresFlatPosition": True,
                "entryExecution": "signed MARKET/FOK quote + place",
                "exitExecution": "fresh position read + signed SELL MARKET/FOK quote + place",
                "permanentStrategyMarketDedup": False,
                "transportAmbiguity": "HALT_CURRENT_MARKET_NO_BLIND_RETRY",
                "polyPollMs": int(LOOP_SECONDS * 1000),
                "binanceDirectBookMinIntervalMs": int(BINANCE_BOOK_MIN_INTERVAL * 1000),
                "maxPolyAgeMs": MAX_POLY_AGE_MS,
            },
            "market": dict(self.market_cache or {}),
            "poly": self.last_poly,
            "binance": self.last_binance,
            "activeRound": active,
            "lastEntryLatency": self.last_entry_latency,
            "lastExitLatency": self.last_exit_latency,
            "haltedMarketId": self.halted_market_id,
            "haltedReason": self.halted_reason,
            "lastError": self.last_error,
            "summary": {
                "rounds": int(summary["rounds"] if summary else 0),
                "closedRounds": int(summary["closed"] if summary else 0),
                "wins": wins,
                "losses": losses,
                "winRate": wins / (wins + losses) if wins + losses else None,
                "pnlUsdt": float(summary["pnl"] if summary else 0.0),
            },
            "recentRounds": recent,
            "recentEvents": events,
        }


class _Handler(BaseHTTPRequestHandler):
    engine: PolyGapLiveEngine

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
            state = self.engine.update_settings(body)
            self._send(200, {"ok": True, "state": state})
        except ValueError as exc:
            self._send(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send(500, {"ok": False, "error": str(exc)[:500]})

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def main() -> int:
    engine = PolyGapLiveEngine()
    engine.start()
    handler = type("PolyGapLiveHandler", (_Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Dedicated R_POLY_GAP_SCALP live executor listening on http://{HOST}:{PORT}/state; "
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
