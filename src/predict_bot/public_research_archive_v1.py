from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

from . import cross_oracle as cross
from .microstructure import MicrostructureObserver

ROOT = Path(__file__).resolve().parents[2]
VERSION = "PUBLIC_RESEARCH_ARCHIVE_V1"
HOST = os.environ.get("PREDICT_PUBLIC_RESEARCH_ARCHIVE_HOST", "127.0.0.1")
PORT = int(os.environ.get("PREDICT_PUBLIC_RESEARCH_ARCHIVE_PORT", "8783"))
DB_PATH = Path(os.environ.get("PREDICT_PUBLIC_RESEARCH_ARCHIVE_DB", ROOT / "data" / "public_research_archive_v1.db"))
PREDICT_STATE_URL = os.environ.get("PREDICT_PUBLIC_RESEARCH_ARCHIVE_PREDICT_URL", "http://127.0.0.1:8771/state")
PREDICT_API_BASE = os.environ.get("PREDICT_FUN_API_BASE", "https://api.predict.fun").rstrip("/")
SAMPLE_INTERVAL_MS = max(100, int(os.environ.get("PREDICT_PUBLIC_RESEARCH_ARCHIVE_INTERVAL_MS", "250")))
RETENTION_DAYS = max(1.0, float(os.environ.get("PREDICT_PUBLIC_RESEARCH_ARCHIVE_RETENTION_DAYS", "30")))
CLEANUP_INTERVAL_SECONDS = max(30.0, float(os.environ.get("PREDICT_PUBLIC_RESEARCH_ARCHIVE_CLEANUP_SECONDS", "60")))
DELETE_BATCH_ROWS = max(1_000, int(os.environ.get("PREDICT_PUBLIC_RESEARCH_ARCHIVE_DELETE_BATCH_ROWS", "50000")))
WAL_CHECKPOINT_SECONDS = max(60.0, float(os.environ.get("PREDICT_PUBLIC_RESEARCH_ARCHIVE_WAL_CHECKPOINT_SECONDS", "900")))

# Preserve the legacy signal-table contract used by the Maker EBM builders,
# while keeping this producer completely target-blind.
LEGACY_SIGNAL_COLUMNS = (
    "sampled_at_ms", "seconds_left", "strike_price",
    "predict_up_bid", "predict_up_ask", "predict_up_mid",
    "predict_down_bid", "predict_down_ask", "predict_down_mid",
    "spot_price", "spot_microprice", "spot_queue_imbalance",
    "spot_taker_imbalance_250ms", "spot_taker_imbalance_1s",
    "spot_return_250ms_bps", "spot_return_1s_bps", "spot_return_3s_bps", "spot_return_5s_bps",
    "futures_price", "futures_microprice", "futures_queue_imbalance",
    "futures_taker_imbalance_250ms", "futures_taker_imbalance_1s",
    "futures_return_250ms_bps", "futures_return_1s_bps", "futures_return_3s_bps", "futures_return_5s_bps",
    "perp_spot_basis_bps", "spot_minus_strike_bps", "chainlink_minus_strike_bps",
    "spot_minus_chainlink_bps", "direction_score",
)

ALL_INSERT_COLUMNS = (
    "market_id", *LEGACY_SIGNAL_COLUMNS,
    "bucket_start_sec", "window_end_ms",
    "predict_source_age_ms", "predict_receipt_age_ms",
    "chainlink_price", "chainlink_source_age_ms", "chainlink_receipt_age_ms",
    "direction_bias", "volatility_alert", "micro_writer_lag_ms",
    "spot_trade_stream_status", "spot_book_stream_status", "futures_stream_status",
    "spot_trade_event_rate", "spot_book_event_rate", "futures_event_rate",
    "spot_trade_reconnect_requests", "micro_dropped_events",
    "missing_feature_count", "feature_completeness_ratio",
)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _positive_int(value: Any) -> int | None:
    try:
        result = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result > 0 else None


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _unwrap(payload: Any) -> dict[str, Any]:
    row = _record(payload)
    state = row.get("state")
    return _record(state) if isinstance(state, dict) else row


def _db_bytes(path: Path) -> int:
    total = 0
    for candidate in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        try:
            total += candidate.stat().st_size
        except OSError:
            pass
    return total


class _VolatileMicroStore:
    """Keep FeatureEngine live without persisting raw websocket traffic."""

    def __init__(self) -> None:
        self.path = Path(":memory:")
        self.counts = {"events": 0, "snapshots": 0, "liquidity": 0, "gaps": 0}

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(":memory:")

    def insert_event(self, _db: sqlite3.Connection, _event: dict[str, Any]) -> None:
        return None

    def insert_snapshot(self, _db: sqlite3.Connection, _snapshot: dict[str, Any]) -> None:
        return None

    def insert_liquidity(self, _db: sqlite3.Connection, _item: dict[str, Any]) -> None:
        return None

    def insert_gap(self, _db: sqlite3.Connection, _item: dict[str, Any]) -> None:
        return None

    def cleanup(self, _db: sqlite3.Connection, _now_ns: int) -> None:
        return None


class PublicResearchArchive:
    """Compact BTC public-feature archive for later target-label offline joins."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.started_at_ms = int(time.time() * 1000)
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.db_lock = threading.RLock()

        self.db = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self._create_schema()
        self._write_meta()

        self.client = httpx.Client(timeout=httpx.Timeout(2.0, connect=0.5), trust_env=False)
        self.predict_api_client = httpx.Client(
            timeout=httpx.Timeout(3.0, connect=0.8),
            trust_env=False,
            headers={
                "Accept": "application/json",
                "User-Agent": "Public-Research-Archive-V1/1.0",
                **({"x-api-key": os.environ["PREDICT_FUN_API_KEY"]} if os.environ.get("PREDICT_FUN_API_KEY") else {}),
            },
        )

        self.current_predict: dict[str, Any] = {}
        self.current_market_detail: dict[str, Any] = {}
        self.current_market_id: int | None = None
        self.last_predict_fetch_ms: int | None = None
        self.last_sampled_at_ms: int | None = None
        self.last_written_at_ms: int | None = None
        self.last_cleanup_ms: int | None = None
        self.last_checkpoint_ms: int | None = None
        self.last_error: str | None = None
        self.samples_written_run = 0
        self.samples_skipped_no_market = 0
        self.samples_skipped_duplicate = 0
        self.rows_deleted_run = 0
        self.history: deque[tuple[int, float | None, float | None]] = deque(maxlen=600)

        self.chainlink_ws: Any = None
        self.chainlink: dict[str, Any] = {
            "status": "STARTING", "price": None,
            "sourceTimestampMs": None, "receivedTimestampMs": None, "error": None,
        }

        self.micro = MicrostructureObserver(
            api_key=None,
            api_secret=None,
            current_market_id=lambda: self.current_market_id,
            db_path=Path(":memory:"),
        )
        self.micro.store = _VolatileMicroStore()

        self.sample_thread = threading.Thread(target=self._sample_loop, name="public-research-archive-sampler", daemon=True)
        self.chainlink_thread = threading.Thread(target=self._chainlink_loop, name="public-research-archive-chainlink", daemon=True)

    def _create_schema(self) -> None:
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS public_research_archive_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS wallet_taker_signal_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id INTEGER NOT NULL,
                    sampled_at_ms INTEGER NOT NULL,
                    seconds_left REAL,
                    strike_price REAL,
                    predict_up_bid REAL,
                    predict_up_ask REAL,
                    predict_up_mid REAL,
                    predict_down_bid REAL,
                    predict_down_ask REAL,
                    predict_down_mid REAL,
                    spot_price REAL,
                    spot_microprice REAL,
                    spot_queue_imbalance REAL,
                    spot_taker_imbalance_250ms REAL,
                    spot_taker_imbalance_1s REAL,
                    spot_return_250ms_bps REAL,
                    spot_return_1s_bps REAL,
                    spot_return_3s_bps REAL,
                    spot_return_5s_bps REAL,
                    futures_price REAL,
                    futures_microprice REAL,
                    futures_queue_imbalance REAL,
                    futures_taker_imbalance_250ms REAL,
                    futures_taker_imbalance_1s REAL,
                    futures_return_250ms_bps REAL,
                    futures_return_1s_bps REAL,
                    futures_return_3s_bps REAL,
                    futures_return_5s_bps REAL,
                    perp_spot_basis_bps REAL,
                    spot_minus_strike_bps REAL,
                    chainlink_minus_strike_bps REAL,
                    spot_minus_chainlink_bps REAL,
                    direction_score REAL,
                    bucket_start_sec INTEGER,
                    window_end_ms INTEGER,
                    predict_source_age_ms REAL,
                    predict_receipt_age_ms REAL,
                    chainlink_price REAL,
                    chainlink_source_age_ms REAL,
                    chainlink_receipt_age_ms REAL,
                    direction_bias TEXT,
                    volatility_alert INTEGER,
                    micro_writer_lag_ms REAL,
                    spot_trade_stream_status TEXT,
                    spot_book_stream_status TEXT,
                    futures_stream_status TEXT,
                    spot_trade_event_rate REAL,
                    spot_book_event_rate REAL,
                    futures_event_rate REAL,
                    spot_trade_reconnect_requests INTEGER,
                    micro_dropped_events INTEGER,
                    missing_feature_count INTEGER NOT NULL,
                    feature_completeness_ratio REAL NOT NULL,
                    UNIQUE(market_id, sampled_at_ms)
                );

                CREATE INDEX IF NOT EXISTS idx_public_research_market_time
                    ON wallet_taker_signal_snapshots(market_id, sampled_at_ms);
                CREATE INDEX IF NOT EXISTS idx_public_research_sample_time
                    ON wallet_taker_signal_snapshots(sampled_at_ms);
                """
            )
            self.db.commit()

    def _write_meta(self) -> None:
        policy = {
            "version": VERSION,
            "asset": "BTC",
            "targetBlind": True,
            "targetWalletInputs": False,
            "officialTruthInputs": False,
            "strategyOutputsUsed": False,
            "sampleIntervalMs": SAMPLE_INTERVAL_MS,
            "retentionDays": RETENTION_DAYS,
            "rawMicrostructurePersisted": False,
            "legacyCompatibleTable": "wallet_taker_signal_snapshots",
            "predictStateUrl": PREDICT_STATE_URL,
        }
        with self.db_lock:
            values = {
                "version": VERSION,
                "created_at_ms": str(self.started_at_ms),
                "policy_json": json.dumps(policy, separators=(",", ":"), sort_keys=True),
            }
            for key, value in values.items():
                self.db.execute(
                    "INSERT INTO public_research_archive_meta(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )
            self.db.commit()

    def _refresh_predict(self) -> None:
        response = self.client.get(PREDICT_STATE_URL)
        response.raise_for_status()
        state = _unwrap(response.json())
        btc = _record(_record(state.get("assets")).get("BTC"))
        market = _record(btc.get("market"))
        market_id = _positive_int(market.get("id") or market.get("marketId"))

        with self.lock:
            previous_market_id = self.current_market_id
            self.current_predict = btc
            self.current_market_id = market_id
            self.last_predict_fetch_ms = int(time.time() * 1000)
            cached_start = _number(_record(self.current_market_detail.get("variantData")).get("startPrice"))
            needs_detail = market_id is not None and (market_id != previous_market_id or cached_start is None)

        if market_id is None or not needs_detail:
            return
        try:
            detail_response = self.predict_api_client.get(f"{PREDICT_API_BASE}/v1/markets/{market_id}")
            detail_response.raise_for_status()
            detail_payload = _record(detail_response.json())
            detail = _record(detail_payload.get("data") or detail_payload)
            with self.lock:
                if self.current_market_id == market_id:
                    self.current_market_detail = detail
        except Exception as exc:
            with self.lock:
                if self.current_market_id == market_id:
                    self.current_market_detail = {}
                self.last_error = f"market detail {market_id}: {exc}"[:500]

    def _chainlink_loop(self) -> None:
        if cross.websocket is None:
            with self.lock:
                self.chainlink.update(status="DEPENDENCY_MISSING", error="websocket-client unavailable")
            return
        while not self.stop_event.is_set():
            with self.lock:
                self.chainlink.update(status="CONNECTING", error=None)

            def on_open(ws: Any) -> None:
                ws.send(json.dumps({
                    "action": "subscribe",
                    "subscriptions": [{
                        "topic": "crypto_prices_chainlink",
                        "type": "*",
                        "filters": json.dumps({"symbol": cross.CHAINLINK_SYMBOL}, separators=(",", ":")),
                    }],
                }, separators=(",", ":")))
                with self.lock:
                    self.chainlink.update(status="LIVE", error=None)

            def on_message(_ws: Any, raw: str) -> None:
                if raw == "PONG":
                    return
                parsed = cross.parse_chainlink_message(raw)
                if parsed is None:
                    return
                received_ms = int(time.time() * 1000)
                with self.lock:
                    self.chainlink.update(
                        status="LIVE",
                        price=float(parsed["price"]),
                        sourceTimestampMs=parsed.get("sourceTimestampMs"),
                        receivedTimestampMs=received_ms,
                        error=None,
                    )

            def on_error(_ws: Any, error: Any) -> None:
                with self.lock:
                    self.chainlink.update(status="ERROR", error=str(error)[:300])

            def on_close(_ws: Any, _code: Any, reason: Any) -> None:
                if not self.stop_event.is_set():
                    with self.lock:
                        self.chainlink.update(status="RETRYING", error=str(reason or "")[:300] or None)

            ws = cross.websocket.WebSocketApp(
                cross.CHAINLINK_WS_URL,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            self.chainlink_ws = ws
            try:
                ws.run_forever(ping_interval=15, ping_timeout=10)
            except Exception as exc:
                on_error(ws, exc)
            if self.stop_event.wait(2.0):
                break

    def _asof_return(self, price: float | None, now_ns: int, horizon_ms: int, *, futures: bool) -> float | None:
        if price is None or price <= 0:
            return None
        cutoff = now_ns - horizon_ms * 1_000_000
        index = 2 if futures else 1
        prior: float | None = None
        for row in reversed(self.history):
            if row[0] <= cutoff and row[index] is not None:
                prior = row[index]
                break
        return (price / prior - 1.0) * 10_000 if prior and prior > 0 else None

    def _build_snapshot(self) -> dict[str, Any]:
        now_ns = time.time_ns()
        now_ms = now_ns // 1_000_000
        with self.micro.state_lock:
            micro_snapshot = dict(self.micro.latest_snapshot)
        micro_state = self.micro.state()
        with self.lock:
            predict = dict(self.current_predict)
            detail = dict(self.current_market_detail)
            chainlink = dict(self.chainlink)

        market = _record(predict.get("market"))
        up = _record(predict.get("up"))
        down = _record(predict.get("down"))
        variant = _record(detail.get("variantData"))
        market_id = _positive_int(market.get("id") or market.get("marketId"))
        strike = _number(variant.get("startPrice"))
        spot = _number(micro_snapshot.get("spot_price"))
        futures = _number(micro_snapshot.get("futures_price"))
        chainlink_price = _number(chainlink.get("price"))
        chainlink_source_ms = _number(chainlink.get("sourceTimestampMs"))
        chainlink_received_ms = _number(chainlink.get("receivedTimestampMs"))
        self.history.append((now_ns, spot, futures))

        result: dict[str, Any] = {
            "market_id": market_id,
            "sampled_at_ms": now_ms,
            "seconds_left": _number(predict.get("secondsLeft")),
            "strike_price": strike,
            "predict_up_bid": _number(up.get("bid")),
            "predict_up_ask": _number(up.get("ask")),
            "predict_up_mid": _number(up.get("mid")),
            "predict_down_bid": _number(down.get("bid")),
            "predict_down_ask": _number(down.get("ask")),
            "predict_down_mid": _number(down.get("mid")),
            "spot_price": spot,
            "spot_microprice": _number(micro_snapshot.get("spot_microprice")),
            "spot_queue_imbalance": _number(micro_snapshot.get("spot_queue_imbalance")),
            "spot_taker_imbalance_250ms": _number(micro_snapshot.get("spot_taker_imbalance_250ms")),
            "spot_taker_imbalance_1s": _number(micro_snapshot.get("spot_taker_imbalance_1s")),
            "futures_price": futures,
            "futures_microprice": _number(micro_snapshot.get("futures_microprice")),
            "futures_queue_imbalance": _number(micro_snapshot.get("futures_queue_imbalance")),
            "futures_taker_imbalance_250ms": _number(micro_snapshot.get("futures_taker_imbalance_250ms")),
            "futures_taker_imbalance_1s": _number(micro_snapshot.get("futures_taker_imbalance_1s")),
            "perp_spot_basis_bps": _number(micro_snapshot.get("perp_spot_basis_bps")),
            "chainlink_price": chainlink_price,
            "direction_score": _number(micro_snapshot.get("direction_score")),
            "direction_bias": micro_snapshot.get("direction_bias"),
            "volatility_alert": None if micro_snapshot.get("volatility_alert") is None else int(bool(micro_snapshot.get("volatility_alert"))),
            "bucket_start_sec": market.get("bucketStartSec") or predict.get("bucketStartSec"),
            "window_end_ms": market.get("windowEndMs") or predict.get("windowEndMs"),
            "predict_source_age_ms": _number(predict.get("sourceAgeMs")),
            "predict_receipt_age_ms": _number(predict.get("receiptAgeMs")),
            "chainlink_source_age_ms": now_ms - chainlink_source_ms if chainlink_source_ms else None,
            "chainlink_receipt_age_ms": now_ms - chainlink_received_ms if chainlink_received_ms else None,
        }

        for prefix, price, is_futures in (("spot", spot, False), ("futures", futures, True)):
            for horizon in (250, 1_000, 3_000, 5_000):
                key = f"{prefix}_return_250ms_bps" if horizon == 250 else f"{prefix}_return_{horizon // 1000}s_bps"
                result[key] = self._asof_return(price, now_ns, horizon, futures=is_futures)

        result["spot_minus_strike_bps"] = ((spot / strike - 1.0) * 10_000 if spot is not None and strike is not None and strike > 0 else None)
        result["chainlink_minus_strike_bps"] = ((chainlink_price / strike - 1.0) * 10_000 if chainlink_price is not None and strike is not None and strike > 0 else None)
        result["spot_minus_chainlink_bps"] = ((spot / chainlink_price - 1.0) * 10_000 if spot is not None and chainlink_price is not None and chainlink_price > 0 else None)

        streams = _record(micro_state.get("streams"))
        spot_trade_stream = _record(streams.get("spot_trade"))
        spot_book_stream = _record(streams.get("spot_book"))
        futures_stream = _record(streams.get("futures"))
        storage = _record(micro_state.get("storage"))
        result.update({
            "micro_writer_lag_ms": _number(storage.get("writerLagMs")),
            "spot_trade_stream_status": spot_trade_stream.get("status"),
            "spot_book_stream_status": spot_book_stream.get("status"),
            "futures_stream_status": futures_stream.get("status"),
            "spot_trade_event_rate": _number(spot_trade_stream.get("eventRate")),
            "spot_book_event_rate": _number(spot_book_stream.get("eventRate")),
            "futures_event_rate": _number(futures_stream.get("eventRate")),
            "spot_trade_reconnect_requests": _positive_int(spot_trade_stream.get("reconnectRequests")) or 0,
            "micro_dropped_events": int(storage.get("droppedEvents") or 0),
        })

        feature_keys = tuple(key for key in LEGACY_SIGNAL_COLUMNS if key != "sampled_at_ms")
        missing = sum(1 for key in feature_keys if result.get(key) is None)
        result["missing_feature_count"] = missing
        result["feature_completeness_ratio"] = (len(feature_keys) - missing) / len(feature_keys) if feature_keys else 1.0
        return result

    def _write_snapshot(self, snapshot: dict[str, Any]) -> bool:
        market_id = _positive_int(snapshot.get("market_id"))
        if market_id is None:
            self.samples_skipped_no_market += 1
            return False
        placeholders = ",".join("?" for _ in ALL_INSERT_COLUMNS)
        columns = ",".join(ALL_INSERT_COLUMNS)
        values = tuple(snapshot.get(key) for key in ALL_INSERT_COLUMNS)
        with self.db_lock:
            cursor = self.db.execute(
                f"INSERT OR IGNORE INTO wallet_taker_signal_snapshots({columns}) VALUES({placeholders})",
                values,
            )
            self.db.commit()
        inserted = int(cursor.rowcount or 0) > 0
        if inserted:
            self.samples_written_run += 1
            self.last_written_at_ms = int(snapshot["sampled_at_ms"])
        else:
            self.samples_skipped_duplicate += 1
        return inserted

    def _cleanup(self, now_ms: int) -> None:
        cutoff_ms = now_ms - int(RETENTION_DAYS * 86_400_000)
        with self.db_lock:
            cursor = self.db.execute(
                """DELETE FROM wallet_taker_signal_snapshots
                   WHERE id IN (
                       SELECT id FROM wallet_taker_signal_snapshots
                       WHERE sampled_at_ms < ? ORDER BY id LIMIT ?
                   )""",
                (cutoff_ms, DELETE_BATCH_ROWS),
            )
            deleted = max(0, int(cursor.rowcount or 0))
            self.rows_deleted_run += deleted
            if deleted:
                self.db.execute("PRAGMA incremental_vacuum(2000)")
            self.db.commit()
        self.last_cleanup_ms = now_ms

    def _checkpoint(self, now_ms: int) -> None:
        with self.db_lock:
            try:
                self.db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
            except sqlite3.DatabaseError as exc:
                self.last_error = f"WAL checkpoint: {exc}"[:500]
                return
        self.last_checkpoint_ms = now_ms

    def _sample_once(self) -> None:
        try:
            self._refresh_predict()
            snapshot = self._build_snapshot()
            self.last_sampled_at_ms = int(snapshot["sampled_at_ms"])
            self._write_snapshot(snapshot)
            with self.lock:
                self.last_error = None
        except Exception as exc:
            with self.lock:
                self.last_error = str(exc)[:500]

    def _sample_loop(self) -> None:
        next_sample = time.monotonic()
        next_cleanup = next_sample + CLEANUP_INTERVAL_SECONDS
        next_checkpoint = next_sample + WAL_CHECKPOINT_SECONDS
        interval = SAMPLE_INTERVAL_MS / 1000.0
        while not self.stop_event.is_set():
            self._sample_once()
            now_mono = time.monotonic()
            now_ms = int(time.time() * 1000)
            if now_mono >= next_cleanup:
                try:
                    self._cleanup(now_ms)
                except Exception as exc:
                    with self.lock:
                        self.last_error = f"cleanup: {exc}"[:500]
                next_cleanup = now_mono + CLEANUP_INTERVAL_SECONDS
            if now_mono >= next_checkpoint:
                self._checkpoint(now_ms)
                next_checkpoint = now_mono + WAL_CHECKPOINT_SECONDS
            next_sample += interval
            delay = next_sample - time.monotonic()
            if delay < -interval:
                next_sample = time.monotonic() + interval
                delay = interval
            self.stop_event.wait(max(0.0, delay))

    def start(self) -> None:
        self.micro.start()
        self.chainlink_thread.start()
        self.sample_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        try:
            if self.chainlink_ws is not None:
                self.chainlink_ws.close()
        except Exception:
            pass
        self.micro.stop()
        if self.sample_thread.is_alive():
            self.sample_thread.join(timeout=3)
        if self.chainlink_thread.is_alive():
            self.chainlink_thread.join(timeout=3)
        self.client.close()
        self.predict_api_client.close()
        with self.db_lock:
            try:
                self.db.commit()
                self.db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
            finally:
                self.db.close()

    def _row_count(self) -> int:
        with self.db_lock:
            row = self.db.execute("SELECT COUNT(*) FROM wallet_taker_signal_snapshots").fetchone()
        return int(row[0]) if row else 0

    def _market_count(self) -> int:
        with self.db_lock:
            row = self.db.execute("SELECT COUNT(DISTINCT market_id) FROM wallet_taker_signal_snapshots").fetchone()
        return int(row[0]) if row else 0

    def _coverage(self) -> dict[str, Any]:
        with self.db_lock:
            row = self.db.execute(
                "SELECT MIN(sampled_at_ms),MAX(sampled_at_ms),AVG(feature_completeness_ratio) FROM wallet_taker_signal_snapshots"
            ).fetchone()
        return {
            "firstSampleMs": row[0] if row else None,
            "lastSampleMs": row[1] if row else None,
            "averageFeatureCompleteness": row[2] if row else None,
        }

    def state(self) -> dict[str, Any]:
        with self.lock:
            predict = dict(self.current_predict)
            chainlink = dict(self.chainlink)
            last_error = self.last_error
            current_market_id = self.current_market_id
        micro_state = self.micro.state()
        process_healthy = bool(
            self.sample_thread.is_alive()
            and self.chainlink_thread.is_alive()
            and str(micro_state.get("status") or "") != "ERROR"
        )
        return {
            "ok": True,
            "version": VERSION,
            "status": "ONLINE" if process_healthy else "DEGRADED",
            "processHealthy": process_healthy,
            "port": PORT,
            "asset": "BTC",
            "targetBlind": True,
            "targetWalletInputs": False,
            "officialTruthInputs": False,
            "strategyOutputsUsed": False,
            "currentMarketId": current_market_id,
            "sampleIntervalMs": SAMPLE_INTERVAL_MS,
            "retentionDays": RETENTION_DAYS,
            "database": str(self.db_path),
            "storage": {
                "dbBytes": _db_bytes(self.db_path),
                "rows": self._row_count(),
                "markets": self._market_count(),
                "rowsWrittenRun": self.samples_written_run,
                "rowsDeletedRun": self.rows_deleted_run,
                "skippedNoMarket": self.samples_skipped_no_market,
                "skippedDuplicate": self.samples_skipped_duplicate,
                "rawMicrostructurePersisted": False,
                "legacyCompatibleTable": "wallet_taker_signal_snapshots",
                **self._coverage(),
            },
            "predict": {
                "status": "AVAILABLE" if predict else "WAITING",
                "lastFetchMs": self.last_predict_fetch_ms,
                "sourceAgeMs": _number(predict.get("sourceAgeMs")),
                "receiptAgeMs": _number(predict.get("receiptAgeMs")),
            },
            "chainlink": chainlink,
            "microstructure": {
                "status": micro_state.get("status"),
                "streams": micro_state.get("streams"),
                "storage": micro_state.get("storage"),
            },
            "lastSampledAtMs": self.last_sampled_at_ms,
            "lastWrittenAtMs": self.last_written_at_ms,
            "lastCleanupMs": self.last_cleanup_ms,
            "lastCheckpointMs": self.last_checkpoint_ms,
            "lastError": last_error,
        }

    def health(self) -> dict[str, Any]:
        state = self.state()
        storage = _record(state.get("storage"))
        return {
            "ok": bool(state.get("processHealthy")),
            "version": VERSION,
            "status": state.get("status"),
            "processHealthy": state.get("processHealthy"),
            "port": PORT,
            "currentMarketId": state.get("currentMarketId"),
            "lastWrittenAtMs": state.get("lastWrittenAtMs"),
            "rows": storage.get("rows"),
            "dbBytes": storage.get("dbBytes"),
            "retentionDays": RETENTION_DAYS,
            "targetBlind": True,
            "lastError": state.get("lastError"),
        }


class Handler(BaseHTTPRequestHandler):
    archive: PublicResearchArchive

    def log_message(self, *_args: Any) -> None:
        return

    @staticmethod
    def _disconnected(exc: BaseException) -> bool:
        if isinstance(exc, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            return True
        return isinstance(exc, OSError) and getattr(exc, "winerror", None) in {10053, 10054}

    def _write(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception as exc:
            if not self._disconnected(exc):
                raise

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in {"/", "/state"}:
            self._write(self.archive.state())
            return
        if path == "/health":
            health = self.archive.health()
            self._write(health, 200 if health.get("ok") else 503)
            return
        self._write({"ok": False, "error": "not found"}, 404)


def main() -> int:
    archive = PublicResearchArchive()
    archive.start()
    handler = type("PublicResearchArchiveHandler", (Handler,), {"archive": archive})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}/state; db={DB_PATH}; "
        f"intervalMs={SAMPLE_INTERVAL_MS}; retentionDays={RETENTION_DAYS}; targetBlind=true",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
        archive.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
