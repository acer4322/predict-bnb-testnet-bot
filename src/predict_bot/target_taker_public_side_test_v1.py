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
from . import predict_wallet_target_taker_public_side_strategy_v1 as public_side


ROOT = Path(__file__).resolve().parents[2]
HOST = os.environ.get("PREDICT_TARGET_TAKER_PUBLIC_SIDE_TEST_HOST", "127.0.0.1")
PORT = int(os.environ.get("PREDICT_TARGET_TAKER_PUBLIC_SIDE_TEST_PORT", "8780"))
DB_PATH = Path(
    os.environ.get(
        "PREDICT_TARGET_TAKER_PUBLIC_SIDE_TEST_DB",
        ROOT / "data" / "target_taker_public_side_test_v1.db",
    )
)
MICRO_BOOTSTRAP_DB = Path(
    os.environ.get(
        "PREDICT_TARGET_TAKER_PUBLIC_SIDE_TEST_MICRO_BOOTSTRAP_DB",
        ROOT / "data" / "target_taker_public_side_test_micro_bootstrap.db",
    )
)
OFFICIAL_URL = os.environ.get(
    "PREDICT_TARGET_TAKER_PUBLIC_SIDE_OFFICIAL_URL",
    "http://127.0.0.1:8776/state",
)
PREDICT_STATE_URL = os.environ.get(
    "PREDICT_TARGET_TAKER_PUBLIC_SIDE_PREDICT_URL",
    "http://127.0.0.1:8771/state",
)
PREDICT_API_BASE = os.environ.get("PREDICT_FUN_API_BASE", "https://api.predict.fun")
SAMPLE_INTERVAL_MS = max(
    100,
    int(os.environ.get("PREDICT_TARGET_TAKER_PUBLIC_SIDE_TEST_INTERVAL_MS", "250")),
)
VERSION = "TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY_TEST_V1"
STRATEGY = public_side.SIDE_ONLY_COHORT


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _positive_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result > 0 else None


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _unwrap(payload: Any) -> dict[str, Any]:
    row = _record(payload)
    state = row.get("state")
    return _record(state) if isinstance(state, dict) else row


class _VolatileMicroStore:
    """No-op archive used to keep FeatureEngine live without another raw-data DB."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
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


class TargetTakerPublicSideTest:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db_lock = threading.RLock()
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.started_at_ms = int(time.time() * 1000)
        self.last_loop_ms: int | None = None
        self.last_error: str | None = None
        self.last_public_snapshot: dict[str, Any] | None = None
        self.last_decision: dict[str, Any] | None = None
        self.last_decision_key: tuple[str, str, str] | None = None
        self.last_official_fetch_ms: int | None = None
        self.last_predict_fetch_ms: int | None = None
        self.official_state: dict[str, Any] = {}
        self.predict_btc: dict[str, Any] = {}
        self.current_market_detail: dict[str, Any] = {}
        self.current_market_id: int | None = None
        self.current_title: str | None = None
        self.current_trade: dict[str, Any] | None = None
        self.history: deque[tuple[int, float | None, float | None]] = deque(maxlen=180)
        self.chainlink_ws: Any = None
        self.chainlink: dict[str, Any] = {
            "status": "STARTING",
            "price": None,
            "sourceTimestampMs": None,
            "receivedTimestampMs": None,
            "error": None,
        }
        self.client = httpx.Client(timeout=httpx.Timeout(2.0, connect=0.5), trust_env=False)
        self.predict_api_client = httpx.Client(
            timeout=httpx.Timeout(3.0, connect=0.8),
            trust_env=False,
            headers={
                "Accept": "application/json",
                "User-Agent": "Target-Taker-Public-Side-Test/1.0",
                **(
                    {"x-api-key": os.environ["PREDICT_FUN_API_KEY"]}
                    if os.environ.get("PREDICT_FUN_API_KEY")
                    else {}
                ),
            },
        )
        self.model: dict[str, Any] | None = None
        self.model_error: str | None = None
        try:
            self.model = public_side.load_side_model()
        except Exception as exc:
            self.model_error = str(exc)[:1000]

        self._create_schema()
        self.deployed_at_ms, self.excluded_market_id = self._load_meta()

        # Reuse the proven public BTC Spot/Futures feature engine, but replace
        # its archive store before start. No raw microstructure events are
        # persisted by this strategy-test service.
        self.micro = MicrostructureObserver(
            api_key=None,
            api_secret=None,
            current_market_id=lambda: self.current_market_id,
            db_path=MICRO_BOOTSTRAP_DB,
        )
        self.micro.store = _VolatileMicroStore(MICRO_BOOTSTRAP_DB)
        self.thread = threading.Thread(target=self._loop, name="target-public-side-test", daemon=True)

    def _create_schema(self) -> None:
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS strategy_test_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_test_markets (
                    market_id INTEGER PRIMARY KEY,
                    title TEXT,
                    started_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_test_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id INTEGER NOT NULL,
                    decision_at_ms INTEGER NOT NULL,
                    snapshot_timestamp_ns INTEGER NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    side TEXT,
                    selected_probability REAL,
                    observed_ask REAL,
                    seconds_left REAL,
                    sample_age_ms REAL,
                    predict_receipt_age_ms REAL,
                    available_features INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_strategy_test_decisions_market_time
                    ON strategy_test_decisions(market_id,decision_at_ms);
                CREATE TABLE IF NOT EXISTS strategy_test_trades (
                    market_id INTEGER PRIMARY KEY,
                    decision_at_ms INTEGER NOT NULL,
                    snapshot_timestamp_ns INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    observed_ask REAL NOT NULL,
                    effective_unit_cost REAL NOT NULL,
                    stake_usdt REAL NOT NULL,
                    shares REAL NOT NULL,
                    selected_probability REAL NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_test_results (
                    market_id INTEGER PRIMARY KEY,
                    title TEXT,
                    winner TEXT NOT NULL,
                    resolved_at_ms INTEGER NOT NULL,
                    traded INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    side TEXT,
                    observed_ask REAL,
                    stake_usdt REAL NOT NULL,
                    shares REAL NOT NULL,
                    payout_usdt REAL NOT NULL,
                    net_pnl_usdt REAL NOT NULL,
                    net_roi REAL
                );
                """
            )
            self.db.commit()

    def _load_meta(self) -> tuple[int, int | None]:
        with self.db_lock:
            deployed_row = self.db.execute(
                "SELECT value FROM strategy_test_meta WHERE key='deployed_at_ms'"
            ).fetchone()
            if deployed_row is None:
                deployed = self.started_at_ms
                self.db.execute(
                    "INSERT INTO strategy_test_meta(key,value) VALUES('deployed_at_ms',?)",
                    (str(deployed),),
                )
            else:
                deployed = int(deployed_row[0])
            excluded_row = self.db.execute(
                "SELECT value FROM strategy_test_meta WHERE key='excluded_market_id'"
            ).fetchone()
            excluded = _positive_int(excluded_row[0]) if excluded_row else None
            self.db.execute(
                "INSERT INTO strategy_test_meta(key,value) VALUES('strategy',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (STRATEGY,),
            )
            self.db.execute(
                "INSERT INTO strategy_test_meta(key,value) VALUES('policy_json',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(public_side.policy(), separators=(",", ":"), default=str),),
            )
            self.db.commit()
        return deployed, excluded

    def _set_excluded_market(self, market_id: int) -> None:
        if self.excluded_market_id is not None:
            return
        self.excluded_market_id = int(market_id)
        with self.db_lock:
            self.db.execute(
                "INSERT INTO strategy_test_meta(key,value) VALUES('excluded_market_id',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(int(market_id)),),
            )
            self.db.commit()

    def _official_market(self) -> tuple[int | None, str | None]:
        state = self.official_state
        btc = _record(_record(state.get("assets")).get("BTC"))
        market = _record(btc.get("market"))
        market_id = _positive_int(market.get("marketId") or market.get("id"))
        title = str(market.get("title") or "") or None
        return market_id, title

    def _refresh_official(self) -> None:
        response = self.client.get(OFFICIAL_URL)
        response.raise_for_status()
        state = _unwrap(response.json())
        with self.lock:
            self.official_state = state
            self.last_official_fetch_ms = int(time.time() * 1000)
        self._settle_from_official(state)

    def _refresh_predict(self) -> None:
        response = self.client.get(PREDICT_STATE_URL)
        response.raise_for_status()
        state = _unwrap(response.json())
        btc = _record(_record(state.get("assets")).get("BTC"))
        market = _record(btc.get("market"))
        market_id = _positive_int(market.get("id") or market.get("marketId"))
        with self.lock:
            previous_market_id = _positive_int(_record(self.predict_btc.get("market")).get("id"))
            self.predict_btc = btc
            self.last_predict_fetch_ms = int(time.time() * 1000)
            cached_start = _number(_record(self.current_market_detail.get("variantData")).get("startPrice"))
            needs_detail = market_id is not None and (market_id != previous_market_id or cached_start is None)
        if market_id is not None and needs_detail:
            try:
                detail_response = self.predict_api_client.get(f"{PREDICT_API_BASE}/v1/markets/{market_id}")
                detail_response.raise_for_status()
                detail_payload = _record(detail_response.json())
                detail = _record(detail_payload.get("data") or detail_payload)
                with self.lock:
                    self.current_market_detail = detail
            except Exception as exc:
                with self.lock:
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
                ws.send(
                    json.dumps(
                        {
                            "action": "subscribe",
                            "subscriptions": [
                                {
                                    "topic": "crypto_prices_chainlink",
                                    "type": "*",
                                    "filters": json.dumps(
                                        {"symbol": cross.CHAINLINK_SYMBOL}, separators=(",", ":")
                                    ),
                                }
                            ],
                        },
                        separators=(",", ":"),
                    )
                )
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

    def _public_snapshot(self) -> dict[str, Any]:
        now_ns = time.time_ns()
        now_ms = now_ns // 1_000_000
        with self.micro.state_lock:
            micro = dict(self.micro.latest_snapshot)
        with self.lock:
            predict = dict(self.predict_btc)
            detail = dict(self.current_market_detail)
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
        result: dict[str, Any] = {
            "timestampNs": now_ns,
            "sampledAtMs": now_ms,
            "marketId": _positive_int(market.get("id") or market.get("marketId")),
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
                suffix = f"{horizon}ms" if horizon == 250 else f"{horizon // 1000}s"
                result[f"{prefix}Return{suffix[0].upper() + suffix[1:]}Bps"] = self._asof_return(
                    price, now_ns, horizon, futures=is_futures
                )
        result["spotMinusStrikeBps"] = (
            (spot / strike - 1.0) * 10_000 if spot and strike and strike > 0 else None
        )
        result["chainlinkMinusStrikeBps"] = (
            (chainlink_price / strike - 1.0) * 10_000
            if chainlink_price and strike and strike > 0
            else None
        )
        result["spotMinusChainlinkBps"] = (
            (spot / chainlink_price - 1.0) * 10_000
            if spot and chainlink_price and chainlink_price > 0
            else None
        )
        return result

    def _register_market(self, market_id: int, title: str | None) -> bool:
        if self.excluded_market_id is None:
            self._set_excluded_market(market_id)
        if market_id == self.excluded_market_id:
            return False
        with self.db_lock:
            self.db.execute(
                "INSERT OR IGNORE INTO strategy_test_markets(market_id,title,started_at_ms) VALUES(?,?,?)",
                (int(market_id), title, int(time.time() * 1000)),
            )
            self.db.commit()
            trade = self.db.execute(
                "SELECT * FROM strategy_test_trades WHERE market_id=?",
                (int(market_id),),
            ).fetchone()
        self.current_trade = dict(trade) if trade is not None else None
        return True

    def _record_decision(self, market_id: int, snapshot: dict[str, Any], decision: dict[str, Any]) -> None:
        key = (
            str(decision.get("decision") or ""),
            str(decision.get("reason") or ""),
            str(decision.get("side") or ""),
        )
        with self.lock:
            self.last_decision = {
                **decision,
                "marketId": market_id,
                "strategy": STRATEGY,
                "paperOnly": True,
                "targetEventsUsed": False,
            }
            if key == self.last_decision_key and decision.get("decision") != "TRADE":
                return
            self.last_decision_key = key
        signal = _record(decision.get("signal"))
        available = len(public_side.SIDE_EBM_EXPECTED_FEATURES) - len(signal.get("missingFeatures") or [])
        with self.db_lock:
            self.db.execute(
                """INSERT INTO strategy_test_decisions(
                     market_id,decision_at_ms,snapshot_timestamp_ns,decision,reason,side,selected_probability,
                     observed_ask,seconds_left,sample_age_ms,predict_receipt_age_ms,available_features,payload_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(market_id),
                    int(time.time() * 1000),
                    int(snapshot.get("timestampNs") or time.time_ns()),
                    str(decision.get("decision") or "SKIP"),
                    str(decision.get("reason") or "UNKNOWN"),
                    decision.get("side"),
                    _number(signal.get("selectedProbability")),
                    _number(decision.get("ask")),
                    _number(decision.get("secondsLeft")),
                    _number(decision.get("sampleAgeMs")),
                    _number(decision.get("predictReceiptAgeMs")),
                    int(available),
                    json.dumps(self.last_decision, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()

    def _maybe_trade(self, market_id: int, snapshot: dict[str, Any], decision: dict[str, Any]) -> None:
        if self.current_trade is not None or decision.get("decision") != "TRADE":
            return
        fill = public_side.execution(decision)
        if fill is None:
            return
        signal = _record(decision.get("signal"))
        probability = _number(signal.get("selectedProbability")) or 0.0
        payload = {
            "marketId": market_id,
            "strategy": STRATEGY,
            "side": decision.get("side"),
            "decisionAtMs": int(time.time() * 1000),
            "snapshotTimestampNs": int(snapshot.get("timestampNs") or time.time_ns()),
            "selectedProbability": probability,
            **fill,
            "paperOnly": True,
            "liveOrdersAffected": False,
            "targetEventsUsed": False,
            "oneEntryPerMarket": True,
        }
        with self.db_lock:
            self.db.execute(
                """INSERT OR IGNORE INTO strategy_test_trades(
                     market_id,decision_at_ms,snapshot_timestamp_ns,side,observed_ask,effective_unit_cost,
                     stake_usdt,shares,selected_probability,payload_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(market_id),
                    int(payload["decisionAtMs"]),
                    int(payload["snapshotTimestampNs"]),
                    str(payload["side"]),
                    float(fill["ask"]),
                    float(fill["effectiveUnitCost"]),
                    float(fill["stakeUsdt"]),
                    float(fill["shares"]),
                    float(probability),
                    json.dumps(payload, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()
            row = self.db.execute(
                "SELECT * FROM strategy_test_trades WHERE market_id=?", (int(market_id),)
            ).fetchone()
        self.current_trade = dict(row) if row is not None else payload

    def _settle_from_official(self, state: dict[str, Any]) -> None:
        recent = state.get("targetRecentMarkets")
        if not isinstance(recent, list):
            return
        by_id: dict[int, dict[str, Any]] = {}
        for item in recent:
            row = _record(item)
            market_id = _positive_int(row.get("market_id") or row.get("marketId"))
            winner = str(row.get("winner") or "").upper()
            if market_id and winner in {"UP", "DOWN"}:
                by_id[market_id] = row
        if not by_id:
            return
        with self.db_lock:
            pending = [
                dict(row)
                for row in self.db.execute(
                    """SELECT m.market_id,m.title,t.side,t.observed_ask,t.stake_usdt,t.shares
                         FROM strategy_test_markets m
                         LEFT JOIN strategy_test_trades t ON t.market_id=m.market_id
                         LEFT JOIN strategy_test_results r ON r.market_id=m.market_id
                        WHERE r.market_id IS NULL"""
                )
            ]
            for item in pending:
                market_id = int(item["market_id"])
                official = by_id.get(market_id)
                if official is None:
                    continue
                winner = str(official.get("winner") or "").upper()
                side = str(item.get("side") or "").upper() or None
                traded = 1 if side in {"UP", "DOWN"} else 0
                stake = float(item.get("stake_usdt") or 0.0)
                shares = float(item.get("shares") or 0.0)
                payout = shares if traded and side == winner else 0.0
                pnl = payout - stake if traded else 0.0
                roi = pnl / stake if stake > 1e-12 else None
                status = (
                    "NO_TRADE"
                    if not traded
                    else "WIN"
                    if pnl > 1e-9
                    else "LOSS"
                    if pnl < -1e-9
                    else "FLAT"
                )
                self.db.execute(
                    """INSERT OR REPLACE INTO strategy_test_results(
                         market_id,title,winner,resolved_at_ms,traded,status,side,observed_ask,stake_usdt,
                         shares,payout_usdt,net_pnl_usdt,net_roi
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        market_id,
                        str(official.get("title") or item.get("title") or "") or None,
                        winner,
                        int(official.get("resolved_at_ms") or official.get("resolvedAtMs") or time.time() * 1000),
                        traded,
                        status,
                        side,
                        item.get("observed_ask"),
                        stake,
                        shares,
                        payout,
                        pnl,
                        roi,
                    ),
                )
            self.db.commit()

    def _performance(self) -> dict[str, Any]:
        with self.db_lock:
            summary = dict(
                self.db.execute(
                    """SELECT COUNT(*) settled_markets,COALESCE(SUM(traded),0) traded_markets,
                              COALESCE(SUM(CASE WHEN traded=1 AND status='WIN' THEN 1 ELSE 0 END),0) wins,
                              COALESCE(SUM(CASE WHEN traded=1 AND status='LOSS' THEN 1 ELSE 0 END),0) losses,
                              COALESCE(SUM(stake_usdt),0) stake_usdt,COALESCE(SUM(net_pnl_usdt),0) net_pnl_usdt
                         FROM strategy_test_results"""
                ).fetchone()
            )
            market_count = int(self.db.execute("SELECT COUNT(*) FROM strategy_test_markets").fetchone()[0])
            ordered = [
                dict(row)
                for row in self.db.execute(
                    "SELECT status,net_pnl_usdt FROM strategy_test_results WHERE traded=1 ORDER BY resolved_at_ms,market_id"
                )
            ]
            recent = [
                dict(row)
                for row in self.db.execute(
                    """SELECT market_id,title,winner,status,side,observed_ask,stake_usdt,shares,payout_usdt,
                              net_pnl_usdt,net_roi,resolved_at_ms
                         FROM strategy_test_results ORDER BY resolved_at_ms DESC LIMIT 30"""
                )
            ]
        traded = int(summary.get("traded_markets") or 0)
        wins = int(summary.get("wins") or 0)
        stake = float(summary.get("stake_usdt") or 0.0)
        pnl = float(summary.get("net_pnl_usdt") or 0.0)
        equity = 0.0
        peak = 0.0
        max_drawdown = 0.0
        loss_streak = 0
        longest_loss_streak = 0
        for row in ordered:
            equity += float(row.get("net_pnl_usdt") or 0.0)
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
            if row.get("status") == "LOSS":
                loss_streak += 1
                longest_loss_streak = max(longest_loss_streak, loss_streak)
            else:
                loss_streak = 0
        settled = int(summary.get("settled_markets") or 0)
        return {
            "markets": market_count,
            "settledMarkets": settled,
            "pendingMarkets": max(0, market_count - settled),
            "tradedMarkets": traded,
            "tradeRate": traded / settled if settled else None,
            "wins": wins,
            "losses": int(summary.get("losses") or 0),
            "winRate": wins / traded if traded else None,
            "stakeUsdt": stake,
            "netPnlUsdt": pnl,
            "netRoi": pnl / stake if stake > 1e-12 else None,
            "maxDrawdownUsdt": max_drawdown,
            "longestLossStreak": longest_loss_streak,
            "recentMarkets": recent,
        }

    def _recent_decisions(self) -> list[dict[str, Any]]:
        with self.db_lock:
            return [
                dict(row)
                for row in self.db.execute(
                    """SELECT market_id,decision_at_ms,decision,reason,side,selected_probability,observed_ask,
                              seconds_left,sample_age_ms,predict_receipt_age_ms,available_features
                         FROM strategy_test_decisions ORDER BY id DESC LIMIT 30"""
                )
            ]

    def _advance(self) -> None:
        official_market_id, title = self._official_market()
        if official_market_id is None:
            return
        market_changed = official_market_id != self.current_market_id
        if market_changed:
            self.current_market_id = official_market_id
            self.current_title = title
            self.current_trade = None
            self.last_decision = None
            self.last_decision_key = None
        active = self._register_market(official_market_id, title)
        snapshot = self._public_snapshot()
        with self.lock:
            self.last_public_snapshot = snapshot
        if not active:
            self.last_decision = {
                "decision": "SKIP",
                "reason": "DEPLOYMENT_MARKET_EXCLUDED",
                "side": None,
                "marketId": official_market_id,
                "paperOnly": True,
                "targetEventsUsed": False,
            }
            return
        decision = public_side.decide_side(
            snapshot,
            self.model,
            expected_market_id=int(official_market_id),
            now_ms=int(time.time() * 1000),
        )
        self._record_decision(official_market_id, snapshot, decision)
        self._maybe_trade(official_market_id, snapshot, decision)

    def _loop(self) -> None:
        next_official = 0.0
        next_predict = 0.0
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                if started >= next_official:
                    self._refresh_official()
                    next_official = started + 0.75
                if started >= next_predict:
                    self._refresh_predict()
                    next_predict = started + 0.5
                self._advance()
                with self.lock:
                    self.last_loop_ms = int(time.time() * 1000)
                    self.last_error = None
            except Exception as exc:
                with self.lock:
                    self.last_error = str(exc)[:1000]
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(0.01, SAMPLE_INTERVAL_MS / 1000.0 - elapsed))

    def start(self) -> None:
        self.micro.start()
        threading.Thread(target=self._chainlink_loop, name="target-public-side-chainlink", daemon=True).start()
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
        self.predict_api_client.close()
        with self.db_lock:
            self.db.close()

    def health_snapshot(self) -> dict[str, Any]:
        now = int(time.time() * 1000)
        with self.lock:
            loop_ms = self.last_loop_ms
            error = self.last_error
            official_ms = self.last_official_fetch_ms
            predict_ms = self.last_predict_fetch_ms
        return {
            "ok": loop_ms is not None and now - loop_ms < 3_000 and error is None,
            "status": "ONLINE" if loop_ms is not None and now - loop_ms < 3_000 and error is None else "DEGRADED",
            "version": VERSION,
            "strategy": STRATEGY,
            "processAlive": True,
            "paperOnly": True,
            "liveOrdersAffected": False,
            "writesTo8776": False,
            "directOfficialDbAccess": False,
            "targetEventsUsedForDecision": False,
            "officialSourceUrl": OFFICIAL_URL,
            "predictSourceUrl": PREDICT_STATE_URL,
            "lastLoopAgeMs": now - loop_ms if loop_ms else None,
            "officialSourceAgeMs": now - official_ms if official_ms else None,
            "predictSourceAgeMs": now - predict_ms if predict_ms else None,
            "modelLoaded": self.model is not None,
            "modelError": self.model_error,
            "lastError": error,
        }

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            official = dict(self.official_state)
            public_snapshot = dict(self.last_public_snapshot) if self.last_public_snapshot else None
            decision = dict(self.last_decision) if self.last_decision else None
            trade = dict(self.current_trade) if self.current_trade else None
            market_id = self.current_market_id
            title = self.current_title
            chainlink = dict(self.chainlink)
        btc_official = _record(_record(official.get("assets")).get("BTC"))
        micro_state = self.micro.state()
        return {
            "ok": True,
            "version": VERSION,
            "strategy": STRATEGY,
            "paperOnly": True,
            "forwardOnly": True,
            "historicalBackfill": False,
            "liveOrdersAffected": False,
            "oneEntryPerMarket": True,
            "deployedAtMs": self.deployed_at_ms,
            "excludedDeploymentMarketId": self.excluded_market_id,
            "currentMarket": {
                "marketId": market_id,
                "title": title,
                "active": bool(market_id and market_id != self.excluded_market_id),
                "official": _record(btc_official.get("market")),
            },
            "lastDecision": decision,
            "currentTrade": trade,
            "latestPublicSnapshot": public_snapshot,
            "performance": self._performance(),
            "recentDecisions": self._recent_decisions(),
            "policy": public_side.policy(),
            "model": {
                "loaded": self.model is not None,
                "path": self.model.get("path") if self.model else str(public_side.DEFAULT_SIDE_MODEL_PATH),
                "reportVersion": self.model.get("reportVersion") if self.model else None,
                "features": list(public_side.SIDE_EBM_EXPECTED_FEATURES),
                "error": self.model_error,
            },
            "sources": {
                "targetOfficial": {
                    "url": OFFICIAL_URL,
                    "version": official.get("version"),
                    "status": official.get("status"),
                    "usage": "market identity + official settlement/benchmark only",
                    "directDbAccess": False,
                    "writesBack": False,
                },
                "predictPublic": {"url": PREDICT_STATE_URL, "usage": "public BTC 5m top-of-book only"},
                "spotFutures": {
                    "usage": "public Binance BTC spot/futures microstructure",
                    "archive": "VOLATILE_NO_RAW_DB",
                    "status": micro_state.get("status"),
                },
                "chainlink": chainlink,
            },
            "evidenceBoundary": (
                "TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY decision uses only the frozen 16 public pre-event features. "
                "8776 is read through HTTP for market identity and official settlement; Target fills/inventory/parent orders are never model inputs."
            ),
            "health": self.health_snapshot(),
            "storage": {
                "dbPath": str(self.db_path),
                "microRawArchiveEnabled": False,
                "retention": "PERMANENT_FORWARD_TEST_RESULTS",
            },
        }


class Handler(BaseHTTPRequestHandler):
    test: TargetTakerPublicSideTest

    def log_message(self, *_args: Any) -> None:
        return

    @staticmethod
    def _client_disconnected(exc: BaseException) -> bool:
        if isinstance(exc, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            return True
        return isinstance(exc, OSError) and getattr(exc, "winerror", None) in {10053, 10054}

    def _write_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception as exc:
            if not self._client_disconnected(exc):
                raise

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._write_json(self.test.health_snapshot())
            return
        if path in {"/state", "/"}:
            self._write_json(self.test.snapshot())
            return
        self._write_json({"ok": False, "error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        self._write_json({"ok": False, "error": "strategy test service is read-only"}, 405)


def main() -> int:
    test = TargetTakerPublicSideTest()
    test.start()
    handler = type("TargetTakerPublicSideTestHandler", (Handler,), {"test": test})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}/state; strategy={STRATEGY}; "
        "paperOnly=true; writesTo8776=false; targetEventsUsedForDecision=false; microArchive=volatile",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        test.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
