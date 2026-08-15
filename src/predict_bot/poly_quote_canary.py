from __future__ import annotations

import json
import math
import os
import queue
import sqlite3
import threading
import time
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Any

from .core import BinancePredictionTradingClient
from .cross_oracle_strategies import (
    POLY_DOWN_THRESHOLD,
    POLY_UP_THRESHOLD,
    SCALP_MIN_EDGE,
    STRATEGIES,
    STRATEGY_POLY_GAP_SCALP,
    STRATEGY_POLY_LEAD_ENTRY,
    STRATEGY_POLY_LEAD_EXIT,
    probability_direction,
    probability_mid,
    selected_probability,
)

BINANCE_REALTIME_URL = os.environ.get(
    "PREDICT_CROSS_ORACLE_BINANCE_REALTIME_URL",
    "http://127.0.0.1:8766/api/realtime",
)
CROSS_ORACLE_STATE_URL = os.environ.get(
    "PREDICT_CROSS_ORACLE_STATE_URL",
    "http://127.0.0.1:8767/state",
)
QUOTE_CANARY_ENABLED = os.environ.get(
    "PREDICT_POLY_QUOTE_CANARY_ENABLED", "true"
).strip().lower() not in {"0", "false", "no", "off"}
QUOTE_REPRICE_GAP = max(
    0.0,
    float(os.environ.get("PREDICT_POLY_QUOTE_CANARY_REPRICE_GAP", "0.05")),
)
MIN_QUOTE_COVERAGE = min(
    1.0,
    max(0.01, float(os.environ.get("PREDICT_POLY_QUOTE_CANARY_MIN_COVERAGE", "0.95"))),
)
MIN_EXPIRY_HEADROOM_MS = max(
    0,
    int(os.environ.get("PREDICT_POLY_QUOTE_CANARY_MIN_EXPIRY_MS", "500")),
)
PRIME_POLL_SECONDS = max(
    0.25,
    float(os.environ.get("PREDICT_POLY_QUOTE_CANARY_PRIME_SECONDS", "0.50")),
)
EVENT_QUEUE_MAX = max(
    10,
    int(os.environ.get("PREDICT_POLY_QUOTE_CANARY_QUEUE_MAX", "500")),
)


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


def _ms() -> int:
    return int(time.time() * 1000)


def _http_json(url: str, timeout: float = 1.0) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "BTC-5M-Lab-Poly-Quote-Canary/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _credential_pair() -> tuple[str | None, str | None, str]:
    live_key = os.environ.get("BINANCE_LIVE_API_KEY")
    live_secret = os.environ.get("BINANCE_LIVE_API_SECRET")
    if live_key and live_secret:
        return live_key, live_secret, "BINANCE_LIVE_*"
    key = os.environ.get("BINANCE_API_KEY")
    secret = os.environ.get("BINANCE_API_SECRET")
    if key and secret:
        return key, secret, "BINANCE_API_*"
    return None, None, "UNAVAILABLE"


def _amount_wei(value: float) -> int:
    number = _decimal(value)
    if number is None or number <= 0:
        return 0
    return int((number * Decimal(10**18)).to_integral_value(rounding=ROUND_DOWN))


class PolyQuoteCanary:
    """Signed get-quote canary for the three Polymarket/Binance Paper strategies.

    It deliberately stops after Binance returns the signed quote.  This module
    contains no place-order call and therefore cannot submit a real order.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db_lock = threading.RLock()
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.events: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=EVENT_QUEUE_MAX)
        self.worker: threading.Thread | None = None
        self.primer: threading.Thread | None = None
        self.client: BinancePredictionTradingClient | None = None
        self.metadata_client: BinancePredictionTradingClient | None = None
        self.wallet_address: str | None = None
        self.credential_source = "UNAVAILABLE"
        self.status = "DISABLED" if not QUOTE_CANARY_ENABLED else "STARTING"
        self.error: str | None = None
        self.market_cache: dict[str, Any] | None = None
        self.last_prime_at_ms: int | None = None
        self.last_quote_at_ms: int | None = None
        self.dropped_events = 0
        self._create_schema()

    def _create_schema(self) -> None:
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS poly_quote_canary_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER NOT NULL,
                    strategy TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    binance_market_id INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    signal_at_ms INTEGER NOT NULL,
                    queued_at_ms INTEGER NOT NULL,
                    quote_started_at_ms INTEGER,
                    quote_completed_at_ms INTEGER,
                    paper_price REAL NOT NULL,
                    paper_stake_usdt REAL NOT NULL,
                    paper_shares REAL NOT NULL,
                    poly_up_mid REAL,
                    poly_selected_mid REAL,
                    signal_binance_up_mid REAL,
                    current_binance_up_mid REAL,
                    current_poly_up_mid REAL,
                    token_id TEXT,
                    token_cache_hit INTEGER NOT NULL DEFAULT 0,
                    token_lookup_ms REAL,
                    fee_rate_bps INTEGER,
                    requested_amount_wei TEXT,
                    price_limit REAL,
                    quote_average_price REAL,
                    quote_amount_in_wei TEXT,
                    quote_amount_out_wei TEXT,
                    quote_expires_at_ms INTEGER,
                    quote_expiry_headroom_ms REAL,
                    quote_coverage_ratio REAL,
                    quote_rtt_ms REAL,
                    signal_to_quote_start_ms REAL,
                    signal_to_quote_response_ms REAL,
                    adverse_price_move REAL,
                    executable_edge_after_quote REAL,
                    signal_still_valid INTEGER,
                    would_submit INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    reason TEXT,
                    quote_id_present INTEGER NOT NULL DEFAULT 0,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    UNIQUE(trade_id, phase)
                );
                CREATE INDEX IF NOT EXISTS idx_poly_quote_canary_strategy
                    ON poly_quote_canary_attempts(strategy, phase, id);
                CREATE INDEX IF NOT EXISTS idx_poly_quote_canary_market
                    ON poly_quote_canary_attempts(binance_market_id, id);
                """
            )
            self.db.commit()

    def start(self) -> None:
        if not QUOTE_CANARY_ENABLED:
            return
        if self.worker and self.worker.is_alive():
            return
        self.worker = threading.Thread(
            target=self._worker_loop,
            name="poly-quote-canary-worker",
            daemon=True,
        )
        self.primer = threading.Thread(
            target=self._prime_loop,
            name="poly-quote-canary-primer",
            daemon=True,
        )
        self.worker.start()
        self.primer.start()

    def stop(self) -> None:
        self.stop_event.set()
        for thread in (self.worker, self.primer):
            if thread:
                thread.join(timeout=2.0)
        for client in (self.client, self.metadata_client):
            try:
                if client is not None:
                    client.close()
            except Exception:
                pass
        with self.db_lock:
            self.db.commit()
            self.db.close()

    def enqueue_entry(self, trade: dict[str, Any]) -> None:
        self._enqueue(trade, "ENTRY")

    def enqueue_exit(self, trade: dict[str, Any]) -> None:
        if str(trade.get("strategy")) == STRATEGY_POLY_LEAD_ENTRY:
            return
        self._enqueue(trade, "EXIT")

    def _enqueue(self, trade: dict[str, Any], phase: str) -> None:
        if not QUOTE_CANARY_ENABLED:
            return
        strategy = str(trade.get("strategy") or "")
        if strategy not in STRATEGIES:
            return
        trade_id = int(trade.get("id") or 0)
        if trade_id <= 0:
            return
        signal_at_ms = int(
            trade.get("opened_at_ms") if phase == "ENTRY" else trade.get("closed_at_ms")
            or _ms()
        )
        paper_price = _finite(
            trade.get("entry_price") if phase == "ENTRY" else trade.get("exit_price")
        )
        if paper_price is None or not 0 < paper_price <= 1:
            return
        metadata: dict[str, Any] = {}
        try:
            parsed = json.loads(str(trade.get("metadata_json") or "{}"))
            if isinstance(parsed, dict):
                metadata = parsed
        except json.JSONDecodeError:
            pass
        event = {
            "trade_id": trade_id,
            "strategy": strategy,
            "phase": phase,
            "market_id": int(trade.get("binance_market_id") or 0),
            "side": str(trade.get("side") or "").upper(),
            "signal_at_ms": signal_at_ms,
            "queued_at_ms": _ms(),
            "paper_price": paper_price,
            "stake_usdt": float(trade.get("stake_usdt") or 0.0),
            "shares": float(trade.get("shares") or 0.0),
            "poly_up_mid": _finite(trade.get("entry_poly_up_mid")),
            "signal_binance_up_mid": _finite(trade.get("entry_binance_up_mid")),
            "poly_selected_mid": _finite(metadata.get("polySelectedMid")),
        }
        if event["poly_selected_mid"] is None and event["poly_up_mid"] is not None:
            event["poly_selected_mid"] = selected_probability(
                float(event["poly_up_mid"]), event["side"]
            )
        try:
            self.events.put_nowait(event)
        except queue.Full:
            with self.lock:
                self.dropped_events += 1
                self.status = "DEGRADED"
                self.error = "quote canary event queue overflow"

    def _ensure_clients(self) -> bool:
        with self.lock:
            if self.client is not None and self.metadata_client is not None and self.wallet_address:
                return True
        api_key, api_secret, source = _credential_pair()
        if not api_key or not api_secret:
            with self.lock:
                self.status = "CONFIG_REQUIRED"
                self.error = "Binance credentials unavailable for signed quote simulation"
                self.credential_source = source
            return False
        try:
            quote_client = BinancePredictionTradingClient(api_key, api_secret)
            metadata_client = BinancePredictionTradingClient(api_key, api_secret)
            wallets = quote_client.wallets().get("wallets") or []
            if len(wallets) != 1:
                raise RuntimeError(
                    f"expected exactly one Prediction wallet, received {len(wallets)}"
                )
            wallet_address = str(wallets[0].get("walletAddress") or "")
            if not wallet_address:
                raise RuntimeError("Prediction wallet response has no walletAddress")
            # Warm the server-time offset outside measured quote RTTs.
            quote_client.server_timestamp_ms()
            metadata_client.server_timestamp_ms()
            with self.lock:
                self.client = quote_client
                self.metadata_client = metadata_client
                self.wallet_address = wallet_address
                self.credential_source = source
                self.status = "READY"
                self.error = None
            return True
        except Exception as exc:
            with self.lock:
                self.status = "BLOCKED_PREFLIGHT"
                self.error = str(exc)[:400]
                self.credential_source = source
            return False

    def _prime_loop(self) -> None:
        last_seen_market_id: int | None = None
        while not self.stop_event.is_set():
            try:
                if not self._ensure_clients():
                    self.stop_event.wait(2.0)
                    continue
                realtime = _http_json(BINANCE_REALTIME_URL, timeout=1.0)
                latest = realtime.get("latest") if isinstance(realtime, dict) else None
                market_id = int(latest.get("market_id") or 0) if isinstance(latest, dict) else 0
                with self.lock:
                    cached_market_id = int((self.market_cache or {}).get("market_id") or 0)
                if market_id > 0 and (market_id != last_seen_market_id or cached_market_id != market_id):
                    self._prime_market(expected_market_id=market_id)
                    last_seen_market_id = market_id
            except Exception as exc:
                with self.lock:
                    if self.status == "READY":
                        self.status = "DEGRADED"
                    self.error = f"market prime: {str(exc)[:300]}"
            self.stop_event.wait(PRIME_POLL_SECONDS)

    def _prime_market(self, *, expected_market_id: int | None = None) -> dict[str, Any] | None:
        if not self._ensure_clients():
            return None
        with self.lock:
            client = self.metadata_client
        if client is None:
            return None
        started = time.monotonic()
        summary = client.find_market_summary("BTCUSDT", max_pages=1)
        lookup_ms = max(0.0, (time.monotonic() - started) * 1000.0)
        if not isinstance(summary, dict):
            raise RuntimeError("Binance Prediction current 5m market unavailable")
        selected = summary.get("_selectedMarket")
        if not isinstance(selected, dict):
            raise RuntimeError("Binance Prediction binary market selection unavailable")
        market = selected.get("market")
        up = selected.get("up")
        down = selected.get("down")
        if not isinstance(market, dict) or not isinstance(up, dict) or not isinstance(down, dict):
            raise RuntimeError("Binance Prediction market metadata incomplete")
        market_id = int(market.get("marketId") or 0)
        if expected_market_id is not None and market_id != int(expected_market_id):
            raise RuntimeError(
                f"Binance token cache market mismatch: expected {expected_market_id}, got {market_id}"
            )
        cache = {
            "market_id": market_id,
            "topic_id": int(summary.get("marketTopicId") or 0),
            "up_token_id": str(up.get("tokenId") or ""),
            "down_token_id": str(down.get("tokenId") or ""),
            "fee_rate_bps": int(
                summary.get("feeRateBps")
                or market.get("feeRateBps")
                or 200
            ),
            "end_ms": int(summary.get("endDate") or 0),
            "lookup_ms": lookup_ms,
            "cached_at_ms": _ms(),
        }
        if not cache["up_token_id"] or not cache["down_token_id"]:
            raise RuntimeError("Binance Prediction token IDs unavailable")
        with self.lock:
            self.market_cache = cache
            self.last_prime_at_ms = cache["cached_at_ms"]
            if self.status in {"STARTING", "DEGRADED"}:
                self.status = "READY"
                self.error = None
        return dict(cache)

    def _market_for_event(self, market_id: int) -> tuple[dict[str, Any] | None, bool, float | None]:
        with self.lock:
            cached = dict(self.market_cache or {})
        if int(cached.get("market_id") or 0) == int(market_id):
            return cached, True, 0.0
        started = time.monotonic()
        try:
            cache = self._prime_market(expected_market_id=market_id)
        except Exception:
            return None, False, max(0.0, (time.monotonic() - started) * 1000.0)
        return cache, False, max(0.0, (time.monotonic() - started) * 1000.0)

    @staticmethod
    def _current_binance_state() -> dict[str, Any] | None:
        try:
            payload = _http_json(BINANCE_REALTIME_URL, timeout=0.7)
        except Exception:
            return None
        latest = payload.get("latest") if isinstance(payload, dict) else None
        return dict(latest) if isinstance(latest, dict) else None

    @staticmethod
    def _current_poly_up_mid() -> float | None:
        try:
            payload = _http_json(CROSS_ORACLE_STATE_URL, timeout=0.7)
        except Exception:
            return None
        poly = payload.get("polymarket") if isinstance(payload, dict) else None
        up = poly.get("up") if isinstance(poly, dict) else None
        if not isinstance(up, dict):
            return None
        return probability_mid(up.get("bestBid"), up.get("bestAsk"))

    @staticmethod
    def _binance_up_mid(latest: dict[str, Any] | None) -> float | None:
        if not isinstance(latest, dict):
            return None
        return probability_mid(latest.get("up_bid"), latest.get("up_ask"))

    def _price_limit(
        self,
        event: dict[str, Any],
        current_latest: dict[str, Any] | None,
    ) -> float:
        paper_price = float(event["paper_price"])
        phase = str(event["phase"])
        side = str(event["side"])
        if phase == "EXIT":
            current_bid = _finite(
                (current_latest or {}).get(f"{side.lower()}_bid")
            )
            return current_bid if current_bid is not None and current_bid > 0 else paper_price

        ceiling = min(0.99999999, paper_price + QUOTE_REPRICE_GAP)
        if event["strategy"] == STRATEGY_POLY_GAP_SCALP:
            poly_selected = _finite(event.get("poly_selected_mid"))
            if poly_selected is not None:
                ceiling = min(ceiling, max(0.00000001, poly_selected - SCALP_MIN_EDGE))
        current_ask = _finite(
            (current_latest or {}).get(f"{side.lower()}_ask")
        )
        if current_ask is not None and current_ask > 0:
            return min(max(paper_price, current_ask), ceiling)
        return ceiling

    def _signal_still_valid(
        self,
        event: dict[str, Any],
        *,
        quote_average: float | None,
        current_binance_up_mid: float | None,
        current_poly_up_mid: float | None,
    ) -> tuple[bool, str, float | None]:
        strategy = str(event["strategy"])
        phase = str(event["phase"])
        side = str(event["side"])
        if phase == "EXIT":
            return True, "exit quote is evaluated against current bid/limit", None
        if quote_average is None:
            return False, "quote average price unavailable", None

        if strategy in {STRATEGY_POLY_LEAD_ENTRY, STRATEGY_POLY_LEAD_EXIT}:
            direction = probability_direction(
                current_binance_up_mid,
                up_threshold=POLY_UP_THRESHOLD,
                down_threshold=POLY_DOWN_THRESHOLD,
            )
            if direction == side:
                return False, "Binance caught up before quote response", None
            return True, "Binance still has not crossed to the Poly direction", None

        poly_up_mid = (
            current_poly_up_mid
            if current_poly_up_mid is not None
            else _finite(event.get("poly_up_mid"))
        )
        if poly_up_mid is None:
            return False, "current Polymarket midpoint unavailable", None
        poly_selected = selected_probability(poly_up_mid, side)
        edge = poly_selected - quote_average
        if edge + 1e-12 < SCALP_MIN_EDGE:
            return False, f"signed quote leaves only {edge:.4f} edge", edge
        return True, f"signed quote preserves {edge:.4f} edge", edge

    def _record_initial(self, event: dict[str, Any]) -> bool:
        now_ms = _ms()
        with self.db_lock:
            cursor = self.db.execute(
                """INSERT OR IGNORE INTO poly_quote_canary_attempts(
                       trade_id, strategy, phase, binance_market_id, side,
                       signal_at_ms, queued_at_ms, paper_price, paper_stake_usdt,
                       paper_shares, poly_up_mid, poly_selected_mid,
                       signal_binance_up_mid, status, created_at_ms, updated_at_ms
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'QUEUED',?,?)""",
                (
                    int(event["trade_id"]),
                    str(event["strategy"]),
                    str(event["phase"]),
                    int(event["market_id"]),
                    str(event["side"]),
                    int(event["signal_at_ms"]),
                    int(event["queued_at_ms"]),
                    float(event["paper_price"]),
                    float(event["stake_usdt"]),
                    float(event["shares"]),
                    event.get("poly_up_mid"),
                    event.get("poly_selected_mid"),
                    event.get("signal_binance_up_mid"),
                    now_ms,
                    now_ms,
                ),
            )
            self.db.commit()
        return bool(cursor.rowcount)

    def _update_attempt(self, trade_id: int, phase: str, **values: Any) -> None:
        allowed = {
            "quote_started_at_ms", "quote_completed_at_ms", "current_binance_up_mid",
            "current_poly_up_mid", "token_id", "token_cache_hit", "token_lookup_ms",
            "fee_rate_bps", "requested_amount_wei", "price_limit", "quote_average_price",
            "quote_amount_in_wei", "quote_amount_out_wei", "quote_expires_at_ms",
            "quote_expiry_headroom_ms", "quote_coverage_ratio", "quote_rtt_ms",
            "signal_to_quote_start_ms", "signal_to_quote_response_ms", "adverse_price_move",
            "executable_edge_after_quote", "signal_still_valid", "would_submit",
            "status", "reason", "quote_id_present",
        }
        updates = {key: value for key, value in values.items() if key in allowed}
        updates["updated_at_ms"] = _ms()
        assignments = ", ".join(f"{key}=?" for key in updates)
        with self.db_lock:
            self.db.execute(
                f"UPDATE poly_quote_canary_attempts SET {assignments} "
                "WHERE trade_id=? AND phase=?",
                (*updates.values(), int(trade_id), str(phase)),
            )
            self.db.commit()

    def _worker_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                event = self.events.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                if self._record_initial(event):
                    self._process_event(event)
            except Exception as exc:
                self._update_attempt(
                    int(event["trade_id"]),
                    str(event["phase"]),
                    status="ERROR",
                    reason=str(exc)[:500],
                )
                with self.lock:
                    self.status = "DEGRADED"
                    self.error = str(exc)[:400]
            finally:
                self.events.task_done()

    def _process_event(self, event: dict[str, Any]) -> None:
        trade_id = int(event["trade_id"])
        phase = str(event["phase"])
        signal_at_ms = int(event["signal_at_ms"])
        if not self._ensure_clients():
            self._update_attempt(
                trade_id,
                phase,
                status="CONFIG_REQUIRED",
                reason=self.error or "signed quote client unavailable",
            )
            return

        market, cache_hit, lookup_ms = self._market_for_event(int(event["market_id"]))
        if market is None:
            self._update_attempt(
                trade_id,
                phase,
                token_cache_hit=0,
                token_lookup_ms=lookup_ms,
                status="MARKET_METADATA_UNAVAILABLE",
                reason="could not resolve matching Binance Prediction token metadata",
            )
            return

        side = str(event["side"])
        token_id = str(market[f"{side.lower()}_token_id"])
        fee_bps = int(market.get("fee_rate_bps") or 200)
        current_before = self._current_binance_state()
        price_limit = self._price_limit(event, current_before)
        if not 0 < price_limit < 1:
            self._update_attempt(
                trade_id,
                phase,
                status="INVALID_PRICE_LIMIT",
                reason=f"computed price limit {price_limit} is invalid",
            )
            return

        if phase == "BUY":  # defensive legacy path; current values are ENTRY/EXIT
            phase = "ENTRY"
        if phase == "ENTRY":
            requested_amount_wei = _amount_wei(float(event["stake_usdt"]))
            quote_side = "BUY"
        else:
            requested_amount_wei = _amount_wei(float(event["shares"]))
            quote_side = "SELL"
        if requested_amount_wei <= 0:
            self._update_attempt(
                trade_id,
                str(event["phase"]),
                status="INVALID_AMOUNT",
                reason="quote simulation amount is not positive",
            )
            return

        quote_started_at_ms = _ms()
        signal_to_start = max(0.0, float(quote_started_at_ms - signal_at_ms))
        self._update_attempt(
            trade_id,
            str(event["phase"]),
            quote_started_at_ms=quote_started_at_ms,
            token_id=token_id,
            token_cache_hit=1 if cache_hit else 0,
            token_lookup_ms=lookup_ms,
            fee_rate_bps=fee_bps,
            requested_amount_wei=str(requested_amount_wei),
            price_limit=price_limit,
            signal_to_quote_start_ms=signal_to_start,
            status="QUOTE_REQUESTING",
        )

        with self.lock:
            client = self.client
            wallet_address = self.wallet_address
        if client is None or not wallet_address:
            raise RuntimeError("signed quote client lost preflight state")

        started = time.monotonic()
        try:
            quote = client.get_quote(
                wallet_address=wallet_address,
                token_id=token_id,
                amount_in_wei=str(requested_amount_wei),
                price_limit=format(Decimal(str(price_limit)).normalize(), "f"),
                slippage_bps=100,
                fee_rate_bps=fee_bps,
                funding_source="MPC",
                side=quote_side,
                order_type="LIMIT",
            )
        except Exception as exc:
            completed_ms = _ms()
            self._update_attempt(
                trade_id,
                str(event["phase"]),
                quote_completed_at_ms=completed_ms,
                quote_rtt_ms=max(0.0, (time.monotonic() - started) * 1000.0),
                signal_to_quote_response_ms=max(0.0, float(completed_ms - signal_at_ms)),
                status="QUOTE_REJECTED",
                reason=str(exc)[:500],
            )
            return

        quote_rtt_ms = max(0.0, (time.monotonic() - started) * 1000.0)
        completed_ms = _ms()
        server_now_ms = int(client.server_timestamp_ms())
        quote_average = _finite(quote.get("averagePrice"))
        quote_amount_in = _decimal(quote.get("amountIn"))
        quote_amount_out = _decimal(quote.get("amountOut"))
        expiry_ms = int(quote.get("expireAt") or 0)
        expiry_headroom = float(expiry_ms - server_now_ms) if expiry_ms else None
        coverage = (
            float(quote_amount_in / Decimal(requested_amount_wei))
            if quote_amount_in is not None and requested_amount_wei > 0
            else None
        )
        quote_id_present = bool(quote.get("quoteId"))
        price_safe = (
            quote_average is not None
            and (
                quote_average <= price_limit + 1e-8
                if quote_side == "BUY"
                else quote_average + 1e-8 >= price_limit
            )
        )
        amount_safe = (
            coverage is not None
            and coverage >= MIN_QUOTE_COVERAGE
            and coverage <= 1.00000001
            and quote_amount_out is not None
            and quote_amount_out > 0
        )
        expiry_safe = expiry_headroom is None or expiry_headroom >= MIN_EXPIRY_HEADROOM_MS

        current_after = self._current_binance_state()
        current_binance_up_mid = self._binance_up_mid(current_after)
        current_poly_up_mid = self._current_poly_up_mid()
        still_valid, validity_reason, executable_edge = self._signal_still_valid(
            event,
            quote_average=quote_average,
            current_binance_up_mid=current_binance_up_mid,
            current_poly_up_mid=current_poly_up_mid,
        )
        if quote_side == "BUY":
            adverse_move = (
                quote_average - float(event["paper_price"])
                if quote_average is not None else None
            )
        else:
            adverse_move = (
                float(event["paper_price"]) - quote_average
                if quote_average is not None else None
            )

        would_submit = bool(
            quote_id_present and price_safe and amount_safe and expiry_safe and still_valid
        )
        if not quote_id_present:
            status, reason = "FAIL_NO_QUOTE_ID", "signed quote response has no quoteId"
        elif not price_safe:
            status, reason = "FAIL_PRICE_MOVED", (
                f"quote average {quote_average} is outside limit {price_limit}"
            )
        elif not amount_safe:
            status, reason = "FAIL_QUOTE_CAPACITY", (
                f"quote coverage {coverage if coverage is not None else 'unknown'} "
                f"is below required {MIN_QUOTE_COVERAGE:.0%}"
            )
        elif not expiry_safe:
            status, reason = "FAIL_QUOTE_EXPIRY", (
                f"quote expiry headroom {expiry_headroom:.0f}ms is below "
                f"{MIN_EXPIRY_HEADROOM_MS}ms"
            )
        elif not still_valid:
            status, reason = "FAIL_SIGNAL_GONE", validity_reason
        else:
            status, reason = "PASS_SIMULATED_PLACE", validity_reason

        self._update_attempt(
            trade_id,
            str(event["phase"]),
            quote_completed_at_ms=completed_ms,
            current_binance_up_mid=current_binance_up_mid,
            current_poly_up_mid=current_poly_up_mid,
            quote_average_price=quote_average,
            quote_amount_in_wei=(str(quote.get("amountIn")) if quote.get("amountIn") is not None else None),
            quote_amount_out_wei=(str(quote.get("amountOut")) if quote.get("amountOut") is not None else None),
            quote_expires_at_ms=expiry_ms or None,
            quote_expiry_headroom_ms=expiry_headroom,
            quote_coverage_ratio=coverage,
            quote_rtt_ms=quote_rtt_ms,
            signal_to_quote_response_ms=max(0.0, float(completed_ms - signal_at_ms)),
            adverse_price_move=adverse_move,
            executable_edge_after_quote=executable_edge,
            signal_still_valid=1 if still_valid else 0,
            would_submit=1 if would_submit else 0,
            status=status,
            reason=reason,
            quote_id_present=1 if quote_id_present else 0,
        )
        with self.lock:
            self.last_quote_at_ms = completed_ms
            if self.status != "CONFIG_REQUIRED":
                self.status = "READY"
                self.error = None

    def _summary_for(self, strategy: str) -> dict[str, Any]:
        with self.db_lock:
            rows = self.db.execute(
                """SELECT * FROM poly_quote_canary_attempts
                    WHERE strategy=? ORDER BY id ASC""",
                (strategy,),
            ).fetchall()
        values = [dict(row) for row in rows]
        completed = [
            row for row in values
            if str(row["status"]).startswith("PASS_")
            or str(row["status"]).startswith("FAIL_")
            or str(row["status"]) in {"QUOTE_REJECTED", "ERROR"}
        ]
        passed = [row for row in completed if int(row.get("would_submit") or 0) == 1]
        quote_rtts = [float(row["quote_rtt_ms"]) for row in completed if row.get("quote_rtt_ms") is not None]
        total_lags = [
            float(row["signal_to_quote_response_ms"])
            for row in completed
            if row.get("signal_to_quote_response_ms") is not None
        ]
        adverse = [
            float(row["adverse_price_move"])
            for row in completed
            if row.get("adverse_price_move") is not None
        ]
        return {
            "attempts": len(values),
            "completed": len(completed),
            "wouldSubmit": len(passed),
            "wouldSubmitRate": len(passed) / len(completed) if completed else None,
            "quoteRejected": sum(str(row["status"]) == "QUOTE_REJECTED" for row in completed),
            "priceMoved": sum(str(row["status"]) == "FAIL_PRICE_MOVED" for row in completed),
            "signalGone": sum(str(row["status"]) == "FAIL_SIGNAL_GONE" for row in completed),
            "capacityFailed": sum(str(row["status"]) == "FAIL_QUOTE_CAPACITY" for row in completed),
            "expiryFailed": sum(str(row["status"]) == "FAIL_QUOTE_EXPIRY" for row in completed),
            "avgQuoteRttMs": sum(quote_rtts) / len(quote_rtts) if quote_rtts else None,
            "maxQuoteRttMs": max(quote_rtts) if quote_rtts else None,
            "avgSignalToQuoteMs": sum(total_lags) / len(total_lags) if total_lags else None,
            "maxSignalToQuoteMs": max(total_lags) if total_lags else None,
            "avgAdversePriceMove": sum(adverse) / len(adverse) if adverse else None,
        }

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            status = self.status
            error = self.error
            cache = dict(self.market_cache or {})
            credential_source = self.credential_source
            last_prime = self.last_prime_at_ms
            last_quote = self.last_quote_at_ms
            dropped = self.dropped_events
            queue_depth = self.events.qsize()
        with self.db_lock:
            recent = [
                dict(row)
                for row in self.db.execute(
                    """SELECT * FROM poly_quote_canary_attempts
                        ORDER BY id DESC LIMIT 40"""
                ).fetchall()
            ]
        for row in recent:
            token = str(row.pop("token_id", "") or "")
            row["token"] = (
                f"{token[:7]}…{token[-5:]}" if len(token) > 16 else token or None
            )
            row.pop("requested_amount_wei", None)
            row.pop("quote_amount_in_wei", None)
            row.pop("quote_amount_out_wei", None)
        return {
            "version": "POLY_SIGNED_QUOTE_CANARY_V1",
            "status": status,
            "error": error,
            "enabled": QUOTE_CANARY_ENABLED,
            "paperOnly": True,
            "signedQuoteRequested": True,
            "placeOrderCalled": False,
            "liveOrdersAffected": False,
            "credentialSource": credential_source,
            "parameters": {
                "repriceGap": QUOTE_REPRICE_GAP,
                "minQuoteCoverage": MIN_QUOTE_COVERAGE,
                "minExpiryHeadroomMs": MIN_EXPIRY_HEADROOM_MS,
                "slippageBps": 100,
                "scalpMinExecutableEdge": SCALP_MIN_EDGE,
            },
            "marketCache": {
                "marketId": cache.get("market_id"),
                "cachedAtMs": cache.get("cached_at_ms"),
                "lookupMs": cache.get("lookup_ms"),
                "lastPrimeAtMs": last_prime,
            },
            "lastQuoteAtMs": last_quote,
            "queueDepth": queue_depth,
            "droppedEvents": dropped,
            "strategies": {strategy: self._summary_for(strategy) for strategy in STRATEGIES},
            "recent": recent,
        }
