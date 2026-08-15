from __future__ import annotations

import json
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
DB_PATH = Path(os.environ.get("PREDICT_WALLET_TAKER_SIGNAL_DB", ROOT / "data" / "wallet_taker_signals.db"))
MICRO_DB_PATH = Path(os.environ.get("PREDICT_WALLET_TAKER_MICRO_DB", ROOT / "data" / "wallet_taker_microstructure.db"))
PRIVATE_ARCHIVE_DB_PATH = Path(os.environ.get("PREDICT_WALLET_TAKER_PRIVATE_ARCHIVE_DB", ROOT / "data" / "wallet_taker_private_archive.db"))
PREDICT_STATE_URL = os.environ.get("PREDICT_WALLET_TAKER_PREDICT_STATE_URL", "http://127.0.0.1:8771/state")
PREDICT_API_BASE = os.environ.get("PREDICT_FUN_API_BASE", "https://api.predict.fun")
HOST = os.environ.get("PREDICT_WALLET_TAKER_SIGNAL_HOST", "127.0.0.1")
PORT = int(os.environ.get("PREDICT_WALLET_TAKER_SIGNAL_PORT", "8777"))
SAMPLE_INTERVAL_MS = max(100, int(os.environ.get("PREDICT_WALLET_TAKER_SIGNAL_INTERVAL_MS", "250")))
RETENTION_HOURS = max(6.0, float(os.environ.get("PREDICT_WALLET_TAKER_SIGNAL_RETENTION_HOURS", "72")))
VERSION = "PREDICT_WALLET_TAKER_SIGNAL_COLLECTOR_V1"


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class TakerSignalCollector:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.started_at_ms = int(time.time() * 1000)
        self.last_error: str | None = None
        self.last_sample_ms: int | None = None
        self.current_predict: dict[str, Any] = {}
        self.current_market_detail: dict[str, Any] = {}
        self.current_market_id: int | None = None
        self.samples_written = 0
        self.chainlink_ws: Any = None
        self.chainlink = {
            "status": "STARTING", "price": None, "sourceTimestampMs": None,
            "receivedTimestampMs": None, "error": None,
        }
        self.history: deque[tuple[int, float | None, float | None]] = deque(maxlen=120)
        self.client = httpx.Client(timeout=httpx.Timeout(2.0, connect=0.5), trust_env=False)
        self.predict_client = httpx.Client(
            timeout=httpx.Timeout(3.0, connect=0.8),
            trust_env=False,
            headers={
                "Accept": "application/json",
                "User-Agent": "Wallet-Taker-Signal-Collector/1.0",
                **({"x-api-key": os.environ["PREDICT_FUN_API_KEY"]} if os.environ.get("PREDICT_FUN_API_KEY") else {}),
            },
        )
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self._create_schema()
        PRIVATE_ARCHIVE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.private_archive_db = sqlite3.connect(PRIVATE_ARCHIVE_DB_PATH, check_same_thread=False, timeout=5.0)
        self.private_archive_db.row_factory = sqlite3.Row
        self.private_archive_db.execute("PRAGMA journal_mode=WAL")
        self.private_archive_db.execute("PRAGMA synchronous=NORMAL")
        self.private_archive_db.execute("PRAGMA busy_timeout=5000")
        self.private_archive_db.execute(
            """CREATE TABLE IF NOT EXISTS wallet_taker_private_signal_archive(
                   market_id INTEGER NOT NULL,sample_second_ms INTEGER NOT NULL,timestamp_ns INTEGER NOT NULL,
                   sampled_at_ms INTEGER NOT NULL,window_end_ms INTEGER,snapshot_json TEXT NOT NULL,
                   PRIMARY KEY(market_id,sample_second_ms))"""
        )
        self.private_archive_db.commit()
        self._seed_private_archive()
        self.micro = MicrostructureObserver(
            api_key=None,
            api_secret=None,
            current_market_id=lambda: self.current_market_id,
            db_path=MICRO_DB_PATH,
        )
        self.thread = threading.Thread(target=self._loop, name="wallet-taker-signal", daemon=True)

    def _create_schema(self) -> None:
        with self.db:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS wallet_taker_signal_snapshots (
                    timestamp_ns INTEGER PRIMARY KEY,
                    sampled_at_ms INTEGER NOT NULL,
                    market_id INTEGER,
                    bucket_start_sec INTEGER,
                    window_end_ms INTEGER,
                    seconds_left REAL,
                    strike_price REAL,
                    predict_up_bid REAL,
                    predict_up_ask REAL,
                    predict_up_mid REAL,
                    predict_down_bid REAL,
                    predict_down_ask REAL,
                    predict_down_mid REAL,
                    predict_source_age_ms REAL,
                    predict_receipt_age_ms REAL,
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
                    chainlink_price REAL,
                    chainlink_source_age_ms REAL,
                    chainlink_receipt_age_ms REAL,
                    chainlink_minus_strike_bps REAL,
                    spot_minus_chainlink_bps REAL,
                    direction_score REAL,
                    direction_bias TEXT,
                    volatility_alert TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS wallet_taker_signal_market_time_idx
                    ON wallet_taker_signal_snapshots(market_id,timestamp_ns);
                CREATE TABLE IF NOT EXISTS wallet_taker_signal_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            self.db.execute(
                "INSERT OR IGNORE INTO wallet_taker_signal_meta(key,value) VALUES ('deployed_at_ms',?)",
                (str(int(time.time() * 1000)),),
            )
            existing = {row[1] for row in self.db.execute("PRAGMA table_info(wallet_taker_signal_snapshots)")}
            for name in (
                "chainlink_price", "chainlink_source_age_ms", "chainlink_receipt_age_ms",
                "chainlink_minus_strike_bps", "spot_minus_chainlink_bps",
            ):
                if name not in existing:
                    self.db.execute(f"ALTER TABLE wallet_taker_signal_snapshots ADD COLUMN {name} REAL")

    def _chainlink_loop(self) -> None:
        if cross.websocket is None:
            with self.lock:
                self.chainlink.update(status="DEPENDENCY_MISSING", error="websocket-client unavailable")
            return
        while not self.stop_event.is_set():
            with self.lock:
                self.chainlink.update(status="CONNECTING", error=None)

            def on_open(ws: Any) -> None:
                subscription = {
                    "action": "subscribe",
                    "subscriptions": [{
                        "topic": "crypto_prices_chainlink",
                        "type": "*",
                        "filters": json.dumps({"symbol": cross.CHAINLINK_SYMBOL}, separators=(",", ":")),
                    }],
                }
                ws.send(json.dumps(subscription, separators=(",", ":")))
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
                        status="LIVE", price=float(parsed["price"]),
                        sourceTimestampMs=parsed.get("sourceTimestampMs"),
                        receivedTimestampMs=received_ms, error=None,
                    )

            def on_error(_ws: Any, error: Any) -> None:
                with self.lock:
                    self.chainlink.update(status="ERROR", error=str(error)[:300])

            def on_close(_ws: Any, _code: Any, reason: Any) -> None:
                if not self.stop_event.is_set():
                    with self.lock:
                        self.chainlink.update(status="RETRYING", error=str(reason or "")[:300] or None)

            ws = cross.websocket.WebSocketApp(
                cross.CHAINLINK_WS_URL, on_open=on_open, on_message=on_message,
                on_error=on_error, on_close=on_close,
            )
            self.chainlink_ws = ws
            try:
                ws.run_forever(ping_interval=15, ping_timeout=10)
            except Exception as exc:
                on_error(ws, exc)
            if self.stop_event.wait(2.0):
                break

    def _refresh_predict(self) -> None:
        response = self.client.get(PREDICT_STATE_URL)
        response.raise_for_status()
        payload = _record(response.json())
        state = _record(payload.get("state") or payload.get("data") or payload)
        btc = _record(_record(state.get("assets")).get("BTC"))
        market = _record(btc.get("market"))
        market_id = int(market.get("id")) if market.get("id") is not None else None
        with self.lock:
            self.current_predict = btc
            changed = market_id is not None and market_id != self.current_market_id
            self.current_market_id = market_id
            cached_variant = _record(self.current_market_detail.get("variantData"))
            needs_detail = changed or _number(cached_variant.get("startPrice")) is None
        # A newly registered five-minute market can briefly return without
        # variantData. Retry until the immutable start price is available; a
        # single rollover-time miss must not disable strike features all market.
        if market_id is not None and needs_detail:
            try:
                detail_response = self.predict_client.get(f"{PREDICT_API_BASE}/v1/markets/{market_id}")
                detail_response.raise_for_status()
                detail_payload = _record(detail_response.json())
                detail = _record(detail_payload.get("data") or detail_payload)
                with self.lock:
                    self.current_market_detail = detail
            except Exception as exc:
                with self.lock:
                    self.current_market_detail = {}
                    self.last_error = f"market detail {market_id}: {exc}"[:500]

    def _asof_return(self, price: float | None, now_ns: int, horizon_ms: int, *, futures: bool) -> float | None:
        if price is None or price <= 0:
            return None
        cutoff = now_ns - horizon_ms * 1_000_000
        index = 2 if futures else 1
        prior = None
        for row in reversed(self.history):
            if row[0] <= cutoff and row[index] is not None:
                prior = row[index]
                break
        return (price / prior - 1.0) * 10_000 if prior and prior > 0 else None

    def _snapshot(self) -> dict[str, Any]:
        now_ns = time.time_ns()
        now_ms = now_ns // 1_000_000
        with self.micro.state_lock:
            micro = dict(self.micro.latest_snapshot)
        with self.lock:
            predict = dict(self.current_predict)
            detail = dict(self.current_market_detail)
            market_id = self.current_market_id
            chainlink = dict(self.chainlink)
        market = _record(predict.get("market"))
        up = _record(predict.get("up"))
        down = _record(predict.get("down"))
        variant = _record(detail.get("variantData"))
        strike = _number(variant.get("startPrice"))
        spot = _number(micro.get("spot_price"))
        futures = _number(micro.get("futures_price"))
        chainlink_price = _number(chainlink.get("price"))
        chainlink_source_ms = _number(chainlink.get("sourceTimestampMs"))
        chainlink_received_ms = _number(chainlink.get("receivedTimestampMs"))
        self.history.append((now_ns, spot, futures))
        result = {
            "timestampNs": now_ns,
            "sampledAtMs": now_ms,
            "marketId": market_id,
            "bucketStartSec": market.get("bucketStartSec") or predict.get("bucketStartSec"),
            "windowEndMs": market.get("windowEndMs") or predict.get("windowEndMs"),
            "secondsLeft": _number(predict.get("secondsLeft")),
            "strikePrice": strike,
            "predictUpBid": _number(up.get("bid")),
            "predictUpAsk": _number(up.get("ask")),
            "predictUpMid": _number(up.get("mid")),
            "predictDownBid": _number(down.get("bid")),
            "predictDownAsk": _number(down.get("ask")),
            "predictDownMid": _number(down.get("mid")),
            "predictSourceAgeMs": _number(predict.get("sourceAgeMs")),
            "predictReceiptAgeMs": _number(predict.get("receiptAgeMs")),
            "spotPrice": spot,
            "spotMicroprice": _number(micro.get("spot_microprice")),
            "spotQueueImbalance": _number(micro.get("spot_queue_imbalance")),
            "spotTakerImbalance250ms": _number(micro.get("spot_taker_imbalance_250ms")),
            "spotTakerImbalance1s": _number(micro.get("spot_taker_imbalance_1s")),
            "futuresPrice": futures,
            "futuresMicroprice": _number(micro.get("futures_microprice")),
            "futuresQueueImbalance": _number(micro.get("futures_queue_imbalance")),
            "futuresTakerImbalance250ms": _number(micro.get("futures_taker_imbalance_250ms")),
            "futuresTakerImbalance1s": _number(micro.get("futures_taker_imbalance_1s")),
            "perpSpotBasisBps": _number(micro.get("perp_spot_basis_bps")),
            "chainlinkPrice": chainlink_price,
            "chainlinkSourceAgeMs": now_ms - chainlink_source_ms if chainlink_source_ms else None,
            "chainlinkReceiptAgeMs": now_ms - chainlink_received_ms if chainlink_received_ms else None,
            "directionScore": _number(micro.get("direction_score")),
            "directionBias": micro.get("direction_bias"),
            "volatilityAlert": micro.get("volatility_alert"),
        }
        for prefix, price, is_futures in (("spot", spot, False), ("futures", futures, True)):
            for horizon in (250, 1_000, 3_000, 5_000):
                result[f"{prefix}Return{horizon if horizon == 250 else horizon // 1000}{'ms' if horizon == 250 else 's'}Bps"] = self._asof_return(
                    price, now_ns, horizon, futures=is_futures
                )
        result["spotMinusStrikeBps"] = (spot / strike - 1.0) * 10_000 if spot and strike and strike > 0 else None
        result["chainlinkMinusStrikeBps"] = (chainlink_price / strike - 1.0) * 10_000 if chainlink_price and strike and strike > 0 else None
        result["spotMinusChainlinkBps"] = (spot / chainlink_price - 1.0) * 10_000 if spot and chainlink_price and chainlink_price > 0 else None
        return result

    def _persist(self, item: dict[str, Any]) -> None:
        columns = [
            "timestamp_ns", "sampled_at_ms", "market_id", "bucket_start_sec", "window_end_ms", "seconds_left", "strike_price",
            "predict_up_bid", "predict_up_ask", "predict_up_mid", "predict_down_bid", "predict_down_ask", "predict_down_mid",
            "predict_source_age_ms", "predict_receipt_age_ms", "spot_price", "spot_microprice", "spot_queue_imbalance",
            "spot_taker_imbalance_250ms", "spot_taker_imbalance_1s", "spot_return_250ms_bps", "spot_return_1s_bps",
            "spot_return_3s_bps", "spot_return_5s_bps", "futures_price", "futures_microprice", "futures_queue_imbalance",
            "futures_taker_imbalance_250ms", "futures_taker_imbalance_1s", "futures_return_250ms_bps", "futures_return_1s_bps",
            "futures_return_3s_bps", "futures_return_5s_bps", "perp_spot_basis_bps", "spot_minus_strike_bps",
            "chainlink_price", "chainlink_source_age_ms", "chainlink_receipt_age_ms", "chainlink_minus_strike_bps", "spot_minus_chainlink_bps",
            "direction_score", "direction_bias", "volatility_alert", "payload_json",
        ]
        keys = [
            "timestampNs", "sampledAtMs", "marketId", "bucketStartSec", "windowEndMs", "secondsLeft", "strikePrice",
            "predictUpBid", "predictUpAsk", "predictUpMid", "predictDownBid", "predictDownAsk", "predictDownMid",
            "predictSourceAgeMs", "predictReceiptAgeMs", "spotPrice", "spotMicroprice", "spotQueueImbalance",
            "spotTakerImbalance250ms", "spotTakerImbalance1s", "spotReturn250msBps", "spotReturn1sBps",
            "spotReturn3sBps", "spotReturn5sBps", "futuresPrice", "futuresMicroprice", "futuresQueueImbalance",
            "futuresTakerImbalance250ms", "futuresTakerImbalance1s", "futuresReturn250msBps", "futuresReturn1sBps",
            "futuresReturn3sBps", "futuresReturn5sBps", "perpSpotBasisBps", "spotMinusStrikeBps",
            "chainlinkPrice", "chainlinkSourceAgeMs", "chainlinkReceiptAgeMs", "chainlinkMinusStrikeBps", "spotMinusChainlinkBps",
            "directionScore", "directionBias", "volatilityAlert",
        ]
        values = [item.get(key) for key in keys] + [json.dumps(item, separators=(",", ":"), default=str)]
        with self.db:
            self.db.execute(
                f"INSERT OR IGNORE INTO wallet_taker_signal_snapshots({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                values,
            )
        market_id = item.get("marketId")
        sampled_at_ms = int(item.get("sampledAtMs") or 0)
        if market_id is not None and sampled_at_ms > 0:
            # One durable row per market-second is enough for later timing
            # controls and keeps the research archive much smaller than 250ms raw data.
            with self.private_archive_db:
                self.private_archive_db.execute(
                    """INSERT INTO wallet_taker_private_signal_archive(
                           market_id,sample_second_ms,timestamp_ns,sampled_at_ms,window_end_ms,snapshot_json
                       ) VALUES (?,?,?,?,?,?)
                       ON CONFLICT(market_id,sample_second_ms) DO UPDATE SET
                           timestamp_ns=excluded.timestamp_ns,sampled_at_ms=excluded.sampled_at_ms,
                           window_end_ms=excluded.window_end_ms,snapshot_json=excluded.snapshot_json""",
                    (int(market_id), sampled_at_ms // 1_000 * 1_000, int(item["timestampNs"]), sampled_at_ms,
                     item.get("windowEndMs"), json.dumps(item, separators=(",", ":"), default=str)),
                )
        self.samples_written += 1

    def _seed_private_archive(self) -> None:
        # The archive is maintained incrementally by _persist. Re-scanning the
        # multi-GB 250 ms source ledger on every cold start can delay the 8777
        # health endpoint for minutes even when a durable archive already
        # exists. A non-empty archive therefore proves that bootstrap has run;
        # any later rows are handled by the normal forward writer.
        if self.private_archive_db.execute(
            "SELECT 1 FROM wallet_taker_private_signal_archive LIMIT 1"
        ).fetchone() is not None:
            return
        latest_by_second: dict[tuple[int, int], sqlite3.Row] = {}
        for row in self.db.execute("SELECT * FROM wallet_taker_signal_snapshots ORDER BY timestamp_ns"):
            market_id = int(row["market_id"] or 0)
            sampled_at_ms = int(row["sampled_at_ms"] or 0)
            if market_id > 0 and sampled_at_ms > 0:
                latest_by_second[(market_id, sampled_at_ms // 1_000 * 1_000)] = row
        with self.private_archive_db:
            self.private_archive_db.executemany(
                "INSERT OR REPLACE INTO wallet_taker_private_signal_archive VALUES (?,?,?,?,?,?)",
                [(
                    market_id, second_ms, int(row["timestamp_ns"]), int(row["sampled_at_ms"]), row["window_end_ms"],
                    json.dumps(dict(row), separators=(",", ":"), default=str),
                ) for (market_id, second_ms), row in latest_by_second.items()],
            )

    def _cleanup(self) -> None:
        cutoff_ns = time.time_ns() - int(RETENTION_HOURS * 3600 * 1e9)
        with self.db:
            self.db.execute(
                "DELETE FROM wallet_taker_signal_snapshots WHERE timestamp_ns IN (SELECT timestamp_ns FROM wallet_taker_signal_snapshots WHERE timestamp_ns<? ORDER BY timestamp_ns LIMIT 20000)",
                (cutoff_ns,),
            )

    def _loop(self) -> None:
        next_predict = 0.0
        next_cleanup = time.monotonic() + 3600
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                if started >= next_predict:
                    self._refresh_predict()
                    next_predict = started + 0.5
                item = self._snapshot()
                self._persist(item)
                with self.lock:
                    self.last_sample_ms = int(item["sampledAtMs"])
                    self.last_error = None
                if started >= next_cleanup:
                    self._cleanup()
                    next_cleanup = started + 3600
            except Exception as exc:
                with self.lock:
                    self.last_error = str(exc)[:500]
            self.stop_event.wait(max(0.01, SAMPLE_INTERVAL_MS / 1000.0 - (time.monotonic() - started)))

    def start(self) -> None:
        self.micro.start()
        threading.Thread(target=self._chainlink_loop, name="wallet-taker-chainlink", daemon=True).start()
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.chainlink_ws is not None:
            try:
                self.chainlink_ws.close()
            except Exception:
                pass
        self.thread.join(timeout=3)
        self.micro.stop()
        self.client.close()
        self.predict_client.close()
        self.db.close()
        self.private_archive_db.close()

    def state(self) -> dict[str, Any]:
        with self.lock:
            last_sample_ms = self.last_sample_ms
            market_id = self.current_market_id
            error = self.last_error
        micro = self.micro.state()
        now_ms = int(time.time() * 1000)
        status = "LIVE" if last_sample_ms and now_ms - last_sample_ms < 2_000 and micro.get("status") in {"LIVE", "PARTIAL"} else "DEGRADED" if last_sample_ms else "STARTING"
        row = self.db.execute("SELECT * FROM wallet_taker_signal_snapshots ORDER BY timestamp_ns DESC LIMIT 1").fetchone()
        deployed = self.db.execute("SELECT value FROM wallet_taker_signal_meta WHERE key='deployed_at_ms'").fetchone()
        return {
            "version": VERSION,
            "status": status,
            "paperOnly": True,
            "readOnlyMarketData": True,
            "liveOrdersAffected": False,
            "startedAtMs": self.started_at_ms,
            "forwardDeployedAtMs": int(deployed[0]) if deployed else self.started_at_ms,
            "lastSampleMs": last_sample_ms,
            "sampleAgeMs": now_ms - last_sample_ms if last_sample_ms else None,
            "marketId": market_id,
            "error": error,
            "latest": dict(row) if row else None,
            "microstructure": micro,
            "chainlink": dict(self.chainlink),
            "storage": {
                "signalDb": str(DB_PATH),
                "microDb": str(MICRO_DB_PATH),
                "privateOneSecondArchiveDb": str(PRIVATE_ARCHIVE_DB_PATH),
                "samplesWrittenThisRun": self.samples_written,
                "retentionHours": RETENTION_HOURS,
                "sampleIntervalMs": SAMPLE_INTERVAL_MS,
            },
            "researchUse": "strict pre-event as-of joins to target Taker fills; never a live execution signal",
        }


class Handler(BaseHTTPRequestHandler):
    collector: TakerSignalCollector

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] not in {"/state", "/health", "/api/state"}:
            self.send_error(404)
            return
        body = json.dumps({"ok": True, "state": self.collector.state()}, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        self.send_error(405, "research collector is read-only")

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def main() -> int:
    collector = TakerSignalCollector()
    collector.start()
    handler = type("WalletTakerSignalHandler", (Handler,), {"collector": collector})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(f"{VERSION} listening on http://{HOST}:{PORT}/state; paper-only", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
        collector.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
