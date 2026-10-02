from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import websocket

from .gemini_prediction_sandbox_v1 import (
    GeminiPredictionSandboxClient,
    event_snapshot,
    merge_public_baseline,
    number,
    parse_iso_ms,
    prepare_order,
    stable_market_id,
)

ROOT = Path(__file__).resolve().parents[2]
VERSION = "GEMINI_BTC5M_TESTNET_BRIDGE_V1"
HOST = os.environ.get("GEMINI_BTC5M_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("GEMINI_BTC5M_BRIDGE_PORT", "8810"))
POLL_SECONDS = max(0.5, float(os.environ.get("GEMINI_BTC5M_POLL_SECONDS", "1.0")))
BASELINE_URL = os.environ.get("GEMINI_BTC5M_BASELINE_SOURCE_URL", "http://127.0.0.1:8783/current-public")
BASELINE_TIMEOUT = max(0.1, float(os.environ.get("GEMINI_BTC5M_BASELINE_TIMEOUT_SECONDS", "0.35")))
BINANCE_FALLBACK_ENABLED = str(os.environ.get("GEMINI_BTC5M_BINANCE_FALLBACK_ENABLED", "true")).strip().lower() in {"1", "true", "yes", "on"}
BINANCE_SPOT_BOOK_URL = os.environ.get("GEMINI_BTC5M_BINANCE_SPOT_BOOK_URL", "https://api.binance.com/api/v3/ticker/bookTicker?symbol=BTCUSDT")
BINANCE_FUTURES_BOOK_URL = os.environ.get("GEMINI_BTC5M_BINANCE_FUTURES_BOOK_URL", "https://fapi.binance.com/fapi/v1/ticker/bookTicker?symbol=BTCUSDT")
RETENTION_HOURS = max(12.0, float(os.environ.get("GEMINI_BTC5M_RETENTION_HOURS", "72")))
DB_PATH = Path(os.environ.get("GEMINI_BTC5M_DB", ROOT / "data" / "gemini_btc5m_sandbox_v1.db"))


def now_ms() -> int:
    return int(time.time() * 1000)


class BridgeStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        self.db.execute("pragma journal_mode=wal")
        self.db.execute("pragma synchronous=normal")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS gemini_btc5m_snapshots_v1(
                sampled_at_ms INTEGER NOT NULL,
                market_id INTEGER NOT NULL,
                event_ticker TEXT NOT NULL,
                instrument_symbol TEXT NOT NULL,
                phase TEXT,
                seconds_left REAL,
                strike_price REAL,
                up_bid REAL,
                up_ask REAL,
                down_bid REAL,
                down_ask REAL,
                last_trade_price REAL,
                spot_price REAL,
                futures_price REAL,
                chainlink_price REAL,
                baseline_ready INTEGER NOT NULL,
                PRIMARY KEY(sampled_at_ms, instrument_symbol)
            );
            CREATE INDEX IF NOT EXISTS idx_gemini_btc5m_snapshots_market
                ON gemini_btc5m_snapshots_v1(market_id, sampled_at_ms);

            CREATE TABLE IF NOT EXISTS gemini_btc5m_actions_v1(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requested_at_ms INTEGER NOT NULL,
                market_id INTEGER,
                event_ticker TEXT,
                instrument_symbol TEXT,
                model_version TEXT,
                channel TEXT,
                side TEXT,
                price REAL,
                quantity REAL,
                status TEXT NOT NULL,
                client_order_id TEXT,
                reason TEXT,
                request_json TEXT NOT NULL,
                response_json TEXT
            );
            CREATE TABLE IF NOT EXISTS gemini_btc5m_settlements_v1(
                event_ticker TEXT PRIMARY KEY,
                market_id INTEGER NOT NULL,
                instrument_symbol TEXT,
                resolved_at_ms INTEGER,
                winner TEXT,
                strike_price REAL,
                settlement_value REAL,
                source TEXT,
                recorded_at_ms INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_gemini_btc5m_settlements_resolved
                ON gemini_btc5m_settlements_v1(resolved_at_ms);
            """
        )
        self.db.commit()
        self.last_cleanup_ms = 0

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def record_snapshot(self, snapshot: dict[str, Any], *, baseline_ready: bool) -> None:
        symbol = str(snapshot.get("geminiInstrumentSymbol") or "")
        ticker = str(snapshot.get("geminiEventTicker") or "")
        if not symbol or not ticker:
            return
        with self.lock:
            self.db.execute(
                """INSERT OR REPLACE INTO gemini_btc5m_snapshots_v1(
                       sampled_at_ms,market_id,event_ticker,instrument_symbol,phase,seconds_left,strike_price,
                       up_bid,up_ask,down_bid,down_ask,last_trade_price,spot_price,futures_price,chainlink_price,
                       baseline_ready
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(snapshot.get("sampledAtMs") or now_ms()),
                    int(snapshot.get("marketId") or 0),
                    ticker,
                    symbol,
                    str(snapshot.get("geminiPhase") or ""),
                    number(snapshot.get("secondsLeft")),
                    number(snapshot.get("strikePrice")),
                    number(snapshot.get("predictUpBid")),
                    number(snapshot.get("predictUpAsk")),
                    number(snapshot.get("predictDownBid")),
                    number(snapshot.get("predictDownAsk")),
                    number(snapshot.get("geminiLastTradePrice")),
                    number(snapshot.get("spotPrice")),
                    number(snapshot.get("futuresPrice")),
                    number(snapshot.get("chainlinkPrice")),
                    1 if baseline_ready else 0,
                ),
            )
            at = now_ms()
            if at - self.last_cleanup_ms >= 600_000:
                cutoff = int(at - RETENTION_HOURS * 3_600_000)
                self.db.execute("DELETE FROM gemini_btc5m_snapshots_v1 WHERE sampled_at_ms<?", (cutoff,))
                self.last_cleanup_ms = at
            self.db.commit()

    def record_settlement(self, event: dict[str, Any]) -> dict[str, Any] | None:
        if str(event.get("series") or "").upper() != "BTC05M":
            return None
        ticker = str(event.get("ticker") or "")
        contracts = [row for row in (event.get("contracts") or []) if isinstance(row, dict)]
        contract = next(
            (row for row in contracts if str(row.get("ticker") or "").upper() == "UP"),
            contracts[0] if contracts else None,
        )
        if not ticker or contract is None:
            return None
        resolution = str(contract.get("resolutionSide") or "").lower()
        winner = "UP" if resolution == "yes" else "DOWN" if resolution == "no" else None
        strike_obj = contract.get("strike") if isinstance(contract.get("strike"), dict) else {}
        settlement_obj = event.get("settlement") if isinstance(event.get("settlement"), dict) else {}
        row = {
            "eventTicker": ticker,
            "marketId": stable_market_id(ticker),
            "instrumentSymbol": contract.get("instrumentSymbol"),
            "resolvedAtMs": parse_iso_ms(contract.get("resolvedAt") or event.get("resolvedAt")),
            "winner": winner,
            "strikePrice": number(strike_obj.get("value")),
            "settlementValue": number(contract.get("settlementValue") or settlement_obj.get("value")),
            "source": contract.get("source") or event.get("source"),
        }
        with self.lock:
            self.db.execute(
                """INSERT OR REPLACE INTO gemini_btc5m_settlements_v1(
                       event_ticker,market_id,instrument_symbol,resolved_at_ms,winner,strike_price,
                       settlement_value,source,recorded_at_ms
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    row["eventTicker"], row["marketId"], row["instrumentSymbol"], row["resolvedAtMs"],
                    row["winner"], row["strikePrice"], row["settlementValue"], row["source"], now_ms(),
                ),
            )
            self.db.commit()
        return row

    def record_action(
        self,
        *,
        snapshot: dict[str, Any] | None,
        request: dict[str, Any],
        prepared: Any,
        status: str,
        response: dict[str, Any] | None,
    ) -> None:
        snap = snapshot or {}
        with self.lock:
            self.db.execute(
                """INSERT INTO gemini_btc5m_actions_v1(
                       requested_at_ms,market_id,event_ticker,instrument_symbol,model_version,channel,side,
                       price,quantity,status,client_order_id,reason,request_json,response_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    now_ms(),
                    int(snap.get("marketId") or 0) or None,
                    snap.get("geminiEventTicker"),
                    snap.get("geminiInstrumentSymbol"),
                    request.get("modelVersion"),
                    request.get("channel"),
                    request.get("side"),
                    number(getattr(prepared, "price", None) if prepared is not None else request.get("price")),
                    number(getattr(prepared, "quantity", None) if prepared is not None else request.get("shares")),
                    status,
                    getattr(prepared, "client_order_id", None),
                    request.get("reason"),
                    json.dumps(request, ensure_ascii=False, separators=(",", ":"), default=str),
                    json.dumps(response, ensure_ascii=False, separators=(",", ":"), default=str) if response is not None else None,
                ),
            )
            self.db.commit()


class GeminiBTC5MBridge:
    def __init__(self) -> None:
        self.started_at_ms = now_ms()
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.client = GeminiPredictionSandboxClient(timeout_seconds=4.0)
        self.baseline_http = httpx.Client(timeout=BASELINE_TIMEOUT, trust_env=False)
        self.binance_http = httpx.Client(timeout=1.5, trust_env=False)
        self.store = BridgeStore(DB_PATH)
        self.last_loop_ms: int | None = None
        self.last_success_ms: int | None = None
        self.last_error: str | None = None
        self.last_baseline_error: str | None = None
        self.latest_snapshot: dict[str, Any] | None = None
        self.latest_contract: dict[str, Any] | None = None
        self.latest_event: dict[str, Any] | None = None
        self.phase: str | None = None
        self.fetch_count = 0
        self.action_count = 0
        self.order_submit_count = 0
        self.last_settlement_poll_ms: int | None = None
        self.last_settlement_error: str | None = None
        self.recent_settlements: list[dict[str, Any]] = []
        self.ws_book: dict[str, Any] = {}
        self.ws_symbol: str | None = None
        self.ws_last_message_ms: int | None = None
        self.ws_error: str | None = None
        self.binance_fallback_last_ms: int | None = None
        self.binance_fallback_error: str | None = None
        self.market_history: list[tuple[int, float | None, float | None]] = []

    def start(self) -> None:
        threading.Thread(target=self._loop, name="gemini-btc5m-testnet-bridge-v1", daemon=True).start()
        threading.Thread(target=self._ws_market_loop, name="gemini-btc5m-public-book-v1", daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()
        self.client.close()
        self.baseline_http.close()
        self.binance_http.close()
        self.store.close()

    @staticmethod
    def _camelize_public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
        def camel(name: str) -> str:
            parts = str(name).split("_")
            return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])
        out = dict(snapshot)
        for key, value in list(snapshot.items()):
            if "_" in str(key):
                out.setdefault(camel(str(key)), value)
        return out

    def _fetch_baseline(self) -> tuple[dict[str, Any] | None, str | None]:
        if not BASELINE_URL:
            return None, "BASELINE_DISABLED"
        try:
            response = self.baseline_http.get(BASELINE_URL)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("baseline source returned non-object")
            health = payload.get("health") if isinstance(payload.get("health"), dict) else {}
            snapshot = payload.get("latestPublicSnapshot")
            if not isinstance(snapshot, dict):
                snapshot = payload.get("snapshot")
            if not isinstance(snapshot, dict):
                raise RuntimeError("baseline public snapshot unavailable")
            if health and health.get("targetEventsUsedForDecision") is True:
                raise RuntimeError("baseline contamination: Target events used for decision")
            if payload.get("targetBlind") is False or payload.get("officialTruthInputs") is True:
                raise RuntimeError("baseline contamination: source is not target-blind")
            return self._camelize_public_snapshot(snapshot), None
        except Exception as exc:
            return None, f"{type(exc).__name__}: {str(exc)[:300]}"

    @staticmethod
    def _book_features(row: dict[str, Any], *, prefix: str) -> dict[str, Any]:
        bid = number(row.get("bidPrice"))
        ask = number(row.get("askPrice"))
        bid_qty = number(row.get("bidQty"))
        ask_qty = number(row.get("askQty"))
        mid = (bid + ask) / 2.0 if bid is not None and ask is not None else None
        micro = None
        imbalance = None
        if bid is not None and ask is not None and bid_qty is not None and ask_qty is not None and bid_qty + ask_qty > 0:
            micro = (ask * bid_qty + bid * ask_qty) / (bid_qty + ask_qty)
            imbalance = (bid_qty - ask_qty) / (bid_qty + ask_qty)
        return {
            f"{prefix}Price": mid,
            f"{prefix}Microprice": micro,
            f"{prefix}QueueImbalance": imbalance,
            f"{prefix}BestBid": bid,
            f"{prefix}BestAsk": ask,
            f"{prefix}BestBidQty": bid_qty,
            f"{prefix}BestAskQty": ask_qty,
        }

    def _history_return(self, current: float | None, at_ms: int, horizon_ms: int, *, futures: bool = False) -> float | None:
        if current is None or current <= 0:
            return None
        idx = 2 if futures else 1
        target = at_ms - int(horizon_ms)
        previous = None
        for row in reversed(self.market_history):
            if row[0] <= target:
                previous = row[idx]
                break
        return (current / previous - 1.0) * 10_000 if previous is not None and previous > 0 else None

    def _fetch_binance_fallback(self, at_ms: int) -> tuple[dict[str, Any] | None, str | None]:
        if not BINANCE_FALLBACK_ENABLED:
            return None, "BINANCE_FALLBACK_DISABLED"
        try:
            spot_r = self.binance_http.get(BINANCE_SPOT_BOOK_URL)
            futures_r = self.binance_http.get(BINANCE_FUTURES_BOOK_URL)
            spot_r.raise_for_status()
            futures_r.raise_for_status()
            spot_row = spot_r.json()
            futures_row = futures_r.json()
            if not isinstance(spot_row, dict) or not isinstance(futures_row, dict):
                raise RuntimeError("Binance fallback returned non-object")
            spot = self._book_features(spot_row, prefix="spot")
            futures = self._book_features(futures_row, prefix="futures")
            spot_price = number(spot.get("spotPrice"))
            futures_price = number(futures.get("futuresPrice"))
            self.market_history.append((int(at_ms), spot_price, futures_price))
            cutoff = int(at_ms) - 15_000
            self.market_history = [row for row in self.market_history if row[0] >= cutoff]
            out: dict[str, Any] = {
                **spot,
                **futures,
                "spotTakerImbalance250ms": None,
                "spotTakerImbalance1s": None,
                "futuresTakerImbalance250ms": None,
                "futuresTakerImbalance1s": None,
                "chainlinkPrice": None,
                "chainlinkSourceAgeMs": None,
                "chainlinkReceiptAgeMs": None,
                "directionScore": None,
                "directionBias": None,
                "volatilityAlert": None,
                "marketDataFallback": "BINANCE_PUBLIC_BOOKTICKER",
            }
            for prefix, value, is_futures in (
                ("spot", spot_price, False),
                ("futures", futures_price, True),
            ):
                for horizon in (1_000, 3_000, 5_000):
                    suffix = f"{horizon // 1000}s"
                    out[f"{prefix}Return{suffix[0].upper() + suffix[1:]}Bps"] = self._history_return(
                        value, at_ms, horizon, futures=is_futures
                    )
                out[f"{prefix}Return250msBps"] = None
            out["perpSpotBasisBps"] = (
                (futures_price / spot_price - 1.0) * 10_000
                if spot_price is not None and futures_price is not None and spot_price > 0
                else None
            )
            self.binance_fallback_last_ms = int(at_ms)
            self.binance_fallback_error = None
            return out, None
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
            self.binance_fallback_error = error
            return None, error

    def _ws_market_loop(self) -> None:
        while not self.stop_event.is_set():
            with self.lock:
                contract = dict(self.latest_contract) if self.latest_contract else None
            symbol = str((contract or {}).get("instrumentSymbol") or "")
            if not symbol:
                self.stop_event.wait(0.25)
                continue
            self.ws_symbol = symbol
            ws = None
            try:
                url = self.client.ws_url + ("&" if "?" in self.client.ws_url else "?") + "snapshot=-1"
                ws = websocket.create_connection(url, timeout=2.0)
                ws.send(json.dumps({
                    "id": "btc5m-book",
                    "method": "subscribe",
                    "params": [f"{symbol}@bookTicker"],
                }))
                self.ws_error = None
                while not self.stop_event.is_set():
                    with self.lock:
                        current_symbol = str((self.latest_contract or {}).get("instrumentSymbol") or "")
                    if current_symbol and current_symbol.lower() != symbol.lower():
                        break
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    if not raw:
                        continue
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(msg, dict) or str(msg.get("s") or "").lower() != symbol.lower():
                        continue
                    received = now_ms()
                    book = {
                        "symbol": symbol,
                        "bid": number(msg.get("b")),
                        "bidQty": number(msg.get("B")),
                        "ask": number(msg.get("a")),
                        "askQty": number(msg.get("A")),
                        "lastTradePrice": number(msg.get("c")),
                        "eventNs": msg.get("E"),
                        "receivedAtMs": received,
                    }
                    with self.lock:
                        self.ws_book = book
                        self.ws_last_message_ms = received
            except Exception as exc:
                self.ws_error = f"{type(exc).__name__}: {str(exc)[:300]}"
                self.stop_event.wait(0.5)
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass

    def _step(self) -> None:
        observed = now_ms()
        selected = self.client.current_btc5m(now_ms=observed, allow_upcoming=True)
        self.fetch_count += 1
        if selected is None:
            with self.lock:
                self.latest_snapshot = None
                self.latest_contract = None
                self.latest_event = None
                self.phase = None
            return
        event, contract, phase = selected
        gemini = event_snapshot(event, contract, observed_at_ms=observed, phase=phase)

        symbol = str(contract.get("instrumentSymbol") or "")
        with self.lock:
            ws_book = dict(self.ws_book) if self.ws_book and str(self.ws_book.get("symbol") or "").lower() == symbol.lower() else {}
        ws_bid = number(ws_book.get("bid"))
        ws_ask = number(ws_book.get("ask"))
        if ws_bid is not None and ws_ask is not None:
            gemini["predictUpBid"] = ws_bid
            gemini["predictUpAsk"] = ws_ask
            gemini["predictUpMid"] = (ws_bid + ws_ask) / 2.0
            gemini["predictDownBid"] = 1.0 - ws_ask
            gemini["predictDownAsk"] = 1.0 - ws_bid
            gemini["predictDownMid"] = 1.0 - gemini["predictUpMid"]
            gemini["geminiBookSource"] = "WS_BOOK_TICKER"
            gemini["geminiUpBidQty"] = number(ws_book.get("bidQty"))
            gemini["geminiUpAskQty"] = number(ws_book.get("askQty"))
            gemini["geminiBookAgeMs"] = observed - int(ws_book.get("receivedAtMs") or observed)
            if number(ws_book.get("lastTradePrice")) is not None:
                gemini["geminiLastTradePrice"] = number(ws_book.get("lastTradePrice"))
        else:
            gemini["geminiBookSource"] = "REST_GET_EVENT"
            gemini["geminiBookAgeMs"] = None

        baseline, baseline_error = self._fetch_baseline()
        fallback = None
        fallback_error = None
        baseline_source = "PROJECT_PUBLIC_8783"
        if baseline is None:
            fallback, fallback_error = self._fetch_binance_fallback(observed)
            if fallback is not None:
                baseline = fallback
                baseline_source = "BINANCE_PUBLIC_FALLBACK"
        merged = merge_public_baseline(gemini, baseline)
        merged["baselineVenue"] = baseline_source if baseline is not None else None
        merged["externalTestOnly"] = True
        merged["realMoney"] = False
        merged["sandboxOnly"] = True
        merged["baselineReady"] = baseline is not None and baseline_source == "PROJECT_PUBLIC_8783"
        merged["marketDataFallbackReady"] = fallback is not None
        merged["marketDataSource"] = baseline_source if baseline is not None else None
        merged["baselineError"] = baseline_error
        merged["binanceFallbackError"] = fallback_error

        # Canonical cross-venue aliases. Frozen research models prefer the
        # historical snake_case contract when both spellings exist, so ensure
        # those names point to the Gemini market identity/book and the current
        # target-blind public microstructure rather than any source-venue market.
        canonical_pairs = {
            "sampled_at_ms": "sampledAtMs",
            "seconds_left": "secondsLeft",
            "strike_price": "strikePrice",
            "predict_up_bid": "predictUpBid",
            "predict_up_ask": "predictUpAsk",
            "predict_up_mid": "predictUpMid",
            "predict_down_bid": "predictDownBid",
            "predict_down_ask": "predictDownAsk",
            "predict_down_mid": "predictDownMid",
            "predict_receipt_age_ms": "predictReceiptAgeMs",
            "spot_minus_strike_bps": "spotMinusStrikeBps",
            "chainlink_minus_strike_bps": "chainlinkMinusStrikeBps",
            "direction_score": "directionScore",
            "spot_queue_imbalance": "spotQueueImbalance",
            "spot_taker_imbalance_1s": "spotTakerImbalance1s",
            "spot_return_1s_bps": "spotReturn1sBps",
            "spot_return_3s_bps": "spotReturn3sBps",
            "futures_queue_imbalance": "futuresQueueImbalance",
            "futures_taker_imbalance_1s": "futuresTakerImbalance1s",
            "futures_return_1s_bps": "futuresReturn1sBps",
            "futures_return_3s_bps": "futuresReturn3sBps",
        }
        for snake, camel in canonical_pairs.items():
            merged[snake] = merged.get(camel)
        with self.lock:
            self.latest_event = dict(event)
            self.latest_contract = dict(contract)
            self.latest_snapshot = merged
            self.phase = phase
            self.last_baseline_error = baseline_error
            self.last_success_ms = observed
        self.store.record_snapshot(merged, baseline_ready=bool(merged.get("baselineReady")))
        self._maybe_poll_settlements(observed)

    def _maybe_poll_settlements(self, at_ms: int) -> None:
        if self.last_settlement_poll_ms is not None and at_ms - self.last_settlement_poll_ms < 15_000:
            return
        self.last_settlement_poll_ms = int(at_ms)
        try:
            rows = self.client.recently_settled_events(limit=200)
            settled: list[dict[str, Any]] = []
            for event in rows:
                row = self.store.record_settlement(event)
                if row is not None:
                    settled.append(row)
            with self.lock:
                self.recent_settlements = settled[:30]
                self.last_settlement_error = None
        except Exception as exc:
            with self.lock:
                self.last_settlement_error = f"{type(exc).__name__}: {str(exc)[:300]}"

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                self._step()
                self.last_error = None
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {str(exc)[:500]}"
                at = now_ms()
                with self.lock:
                    if self.latest_snapshot is not None:
                        end_ms = int(number(self.latest_snapshot.get("windowEndMs")) or 0)
                        if end_ms > 0 and at >= end_ms:
                            stale = dict(self.latest_snapshot)
                            stale["secondsLeft"] = 0.0
                            stale["geminiPhase"] = "STALE_EXPIRED"
                            stale["staleExpired"] = True
                            self.latest_snapshot = stale
                            self.phase = "STALE_EXPIRED"
            self.last_loop_ms = now_ms()
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(0.05, POLL_SECONDS - elapsed))

    def snapshot(self) -> dict[str, Any]:
        at = now_ms()
        with self.lock:
            snap = dict(self.latest_snapshot) if self.latest_snapshot else None
            contract = dict(self.latest_contract) if self.latest_contract else None
            event = dict(self.latest_event) if self.latest_event else None
            phase = self.phase
            last_success = self.last_success_ms
            baseline_error = self.last_baseline_error
            recent_settlements = [dict(row) for row in self.recent_settlements]
            last_settlement_error = self.last_settlement_error
            ws_error = self.ws_error
            ws_last_message_ms = self.ws_last_message_ms
            binance_fallback_error = self.binance_fallback_error
            binance_fallback_last_ms = self.binance_fallback_last_ms
        end_ms = int(number((snap or {}).get("windowEndMs")) or 0)
        active = bool(snap and phase == "ACTIVE" and (end_ms <= 0 or at < end_ms))
        prices_ready = bool(
            snap
            and number(snap.get("predictUpBid")) is not None
            and number(snap.get("predictUpAsk")) is not None
            and number(snap.get("predictDownBid")) is not None
            and number(snap.get("predictDownAsk")) is not None
        )
        status = (
            "DEGRADED" if self.last_error
            else "ACTIVE_SANDBOX" if active and prices_ready
            else "WAITING_PRICES" if active
            else "WAITING_NEXT_BTC05M"
        )
        return {
            "ok": self.last_error is None and snap is not None,
            "status": status,
            "version": VERSION,
            "sandboxOnly": True,
            "realMoney": False,
            "externalTestOnly": True,
            "liveProductionOrdersPossible": False,
            "sandboxOrdersEnabled": self.client.order_enabled,
            "sandboxCredentialsPresent": bool(
                (os.environ.get("GEMINI_PM_SANDBOX_API_KEY") or os.environ.get("gemini_sandbox_APIKEY"))
                and
                (os.environ.get("GEMINI_PM_SANDBOX_API_SECRET") or os.environ.get("gemini_sandbox_APISecret"))
            ),
            "venue": "GEMINI_PREDICTION_MARKETS_SANDBOX",
            "series": "BTC05M",
            "restUrl": self.client.rest_url,
            "wsUrl": self.client.ws_url,
            "baselineUrl": BASELINE_URL,
            "baselineReady": bool(snap and snap.get("baselineReady")),
            "marketDataFallbackReady": bool(snap and snap.get("marketDataFallbackReady")),
            "marketDataSource": snap.get("marketDataSource") if snap else None,
            "baselineError": baseline_error,
            "currentMarket": {
                "active": active,
                "phase": phase,
                "eventId": event.get("id") if event else None,
                "ticker": event.get("ticker") if event else None,
                "title": event.get("title") if event else None,
                "instrumentSymbol": contract.get("instrumentSymbol") if contract else None,
                "startTime": event.get("startTime") if event else None,
                "expiryDate": event.get("expiryDate") if event else None,
            },
            "latestPublicSnapshot": snap,
            "health": {
                "processAlive": True,
                "processHealthy": self.last_error is None,
                "strategyInputReady": active and prices_ready,
                "lastLoopAgeMs": at - self.last_loop_ms if self.last_loop_ms else None,
                "lastSuccessAgeMs": at - last_success if last_success else None,
                "lastError": self.last_error,
                "fetchCount": self.fetch_count,
                "actionCount": self.action_count,
                "orderSubmitCount": self.order_submit_count,
                "pollSeconds": POLL_SECONDS,
                "rawRetentionHours": RETENTION_HOURS,
                "dbPath": str(self.store.path),
                "lastSettlementPollAgeMs": at - self.last_settlement_poll_ms if self.last_settlement_poll_ms else None,
                "lastSettlementError": last_settlement_error,
                "wsBookAgeMs": at - ws_last_message_ms if ws_last_message_ms else None,
                "wsBookError": ws_error,
                "binanceFallbackAgeMs": at - binance_fallback_last_ms if binance_fallback_last_ms else None,
                "binanceFallbackError": binance_fallback_error,
            },
            "recentSettlements": recent_settlements,
            "dataIntegrity": {
                "targetEventsUsedForDecision": False,
                "predictFunStrikeReused": False,
                "geminiStrikeMappingStatus": snap.get("geminiStrikeMappingStatus") if snap else None,
                "baselinePublicOnly": True,
                "binanceFallbackPublicOnly": True,
                "marketDataSource": snap.get("marketDataSource") if snap else None,
            },
        }

    def auth_status(self) -> dict[str, Any]:
        meta = self.client.credential_metadata()
        result: dict[str, Any] = {
            "ok": False,
            "sandboxOnly": True,
            "credentialMetadata": meta,
            "accountScopeProbe": None,
            "termsStatus": None,
        }
        if not meta.get("present"):
            result["error"] = "MISSING_SANDBOX_CREDENTIALS"
            return result
        try:
            scope = self.client.account_scope_probe()
            result["accountScopeProbe"] = scope
            terms = self.client.terms_status()
            result["termsStatus"] = terms
            result["ok"] = bool(terms.get("httpStatus") == 200)
            if not result["ok"]:
                result["error"] = terms.get("error") or f"HTTP_{terms.get('httpStatus')}"
            return result
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
            return result

    def action(self, request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.action_count += 1
        with self.lock:
            snap = dict(self.latest_snapshot) if self.latest_snapshot else None
            contract = dict(self.latest_contract) if self.latest_contract else None
            phase = self.phase
        end_ms = int(number((snap or {}).get("windowEndMs")) or 0) if snap is not None else 0
        if snap is None or contract is None or phase != "ACTIVE" or (end_ms > 0 and now_ms() >= end_ms):
            result = {"ok": False, "error": "NO_ACTIVE_BTC05M_MARKET", "sandboxOnly": True}
            self.store.record_action(snapshot=snap, request=request, prepared=None, status="REJECTED_NO_MARKET", response=result)
            return 409, result

        channel = str(request.get("channel") or "").upper()
        side = str(request.get("side") or "").upper()
        qty = number(request.get("shares") if request.get("shares") is not None else request.get("quantity"))
        px = number(request.get("price"))
        if px is None and channel == "TAKER":
            px = number(snap.get("predictUpAsk") if side == "UP" else snap.get("predictDownAsk"))
        if px is None and channel == "MAKER":
            px = number(snap.get("predictUpBid") if side == "UP" else snap.get("predictDownBid"))
        if qty is None:
            result = {"ok": False, "error": "MISSING_QUANTITY", "sandboxOnly": True}
            self.store.record_action(snapshot=snap, request=request, prepared=None, status="REJECTED_VALIDATION", response=result)
            return 400, result
        try:
            prepared = prepare_order(
                contract,
                channel=channel,
                side=side,
                price=float(px) if px is not None else float("nan"),
                quantity=float(qty),
                client_order_id=str(request.get("clientOrderId") or "") or None,
            )
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "sandboxOnly": True}
            self.store.record_action(snapshot=snap, request=request, prepared=None, status="REJECTED_VALIDATION", response=result)
            return 400, result

        if not self.client.order_enabled:
            result = {
                "ok": True,
                "dryRun": True,
                "submitted": False,
                "reason": "SANDBOX_ORDER_GATE_DISABLED",
                "sandboxOnly": True,
                "preparedOrder": prepared.message,
            }
            self.store.record_action(snapshot=snap, request=request, prepared=prepared, status="DRY_RUN_GATE_DISABLED", response=result)
            return 200, result

        try:
            meta = self.client.credential_metadata()
            submit = (
                self.client.submit_order(prepared)
                if meta.get("accountScoped")
                else self.client.submit_order_rest(prepared)
            )
            self.order_submit_count += 1
            result = {"ok": bool(submit.get("ok")), "dryRun": False, "submitted": True, **submit}
            self.store.record_action(snapshot=snap, request=request, prepared=prepared, status="SUBMITTED_SANDBOX", response=result)
            return 200 if result["ok"] else 502, result
        except Exception as exc:
            result = {
                "ok": False,
                "dryRun": False,
                "submitted": False,
                "sandboxOnly": True,
                "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                "preparedOrder": prepared.message,
            }
            self.store.record_action(snapshot=snap, request=request, prepared=prepared, status="SUBMIT_ERROR", response=result)
            return 502, result


class Handler(BaseHTTPRequestHandler):
    runtime: GeminiBTC5MBridge

    def log_message(self, *_args: Any) -> None:
        return

    def _write(self, payload: Any, code: int = 200) -> None:
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
        state = self.runtime.snapshot()
        if path in {"/", "/state"}:
            self._write(state)
        elif path == "/health":
            self._write({"ok": state["ok"], "status": state["status"], **state["health"]})
        elif path == "/rules":
            snap = state.get("latestPublicSnapshot") or {}
            self._write({
                "ok": True,
                "sandboxOnly": True,
                "series": "BTC05M",
                "instrumentSymbol": snap.get("geminiInstrumentSymbol"),
                "quantityIncrement": snap.get("geminiQuantityIncrement"),
                "quantityMinimum": snap.get("geminiQuantityMinimum"),
                "priceIncrement": snap.get("geminiPriceIncrement"),
                "priceMinimum": snap.get("geminiPriceMinimum"),
                "makerTimeInForce": "MOC",
                "takerTimeInForce": "IOC",
                "upOutcome": "YES",
                "downOutcome": "NO",
            })
        elif path == "/auth-status":
            self._write(self.runtime.auth_status())
        else:
            self._write({"ok": False, "error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/action":
            self._write({"ok": False, "error": "not found"}, 404)
            return
        try:
            length = min(64_000, max(0, int(self.headers.get("Content-Length") or "0")))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("request must be a JSON object")
        except Exception as exc:
            self._write({"ok": False, "error": f"invalid JSON: {exc}"}, 400)
            return
        code, result = self.runtime.action(payload)
        self._write(result, code)


def main() -> int:
    runtime = GeminiBTC5MBridge()
    runtime.start()
    handler = type("GeminiBTC5MBridgeHandler", (Handler,), {"runtime": runtime})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}/state; "
        f"venue=GeminiPredictionSandbox; series=BTC05M; ordersEnabled={runtime.client.order_enabled}; "
        "productionOrders=false",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
