from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .core import taker_fee
from .cross_oracle_strategies import (
    BINANCE_REALTIME_URL,
    CrossOraclePaperEngine,
    STRATEGY_POLY_GAP_SCALP,
    STRATEGY_POLY_LEAD_ENTRY,
    STRATEGY_POLY_LEAD_EXIT,
    _http_json,
    selected_quote,
)


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("PREDICT_CROSS_ORACLE_DB", ROOT / "data" / "cross_oracle.db"))
SIM_DB_PATH = Path(os.environ.get("PREDICT_SIM_DB", ROOT / "data" / "simulation.db"))
HOST = os.environ.get("PREDICT_CROSS_ORACLE_STRATEGY_HOST", "127.0.0.1")
PORT = int(os.environ.get("PREDICT_CROSS_ORACLE_STRATEGY_PORT", "8768"))
CROSS_ORACLE_STATE_URL = os.environ.get(
    "PREDICT_CROSS_ORACLE_STATE_URL",
    "http://127.0.0.1:8767/state",
)
MAX_POLY_AGE_MS = max(250.0, float(os.environ.get("PREDICT_POLY_STRATEGY_MAX_POLY_AGE_MS", "2000")))
MAX_BINANCE_OBSERVATION_AGE_MS = max(
    250.0,
    float(os.environ.get("PREDICT_POLY_STRATEGY_MAX_BINANCE_OBSERVATION_AGE_MS", "1500")),
)
MAX_BINANCE_BOOK_AGE_MS = max(
    250.0,
    float(os.environ.get("PREDICT_POLY_STRATEGY_MAX_BINANCE_BOOK_AGE_MS", "2000")),
)
MAX_BINANCE_BOOK_SKEW_MS = max(
    50.0,
    float(os.environ.get("PREDICT_POLY_STRATEGY_MAX_BINANCE_BOOK_SKEW_MS", "500")),
)
LEAD_RETRY_WINDOW_MS = max(
    250,
    int(os.environ.get("PREDICT_POLY_LEAD_RETRY_WINDOW_MS", "2000")),
)
CONFIDENCE_ATTACH_MAX_LAG_MS = max(
    500,
    int(os.environ.get("PREDICT_POLY_CONFIDENCE_MAX_ATTACH_LAG_MS", "2500")),
)

CONFIDENCE_SOURCES: dict[str, str] = {
    "R_CALIBRATED_VALUE": "R_POLY_CONFIDENCE_EXIT_CALIBRATED_VALUE",
    "R_MICROPRICE": "R_POLY_CONFIDENCE_EXIT_MICROPRICE",
    "R_MICROPRICE_CONFIRM": "R_POLY_CONFIDENCE_EXIT_MICROPRICE_CONFIRM",
    "R_FUTURES_LEAD": "R_POLY_CONFIDENCE_EXIT_FUTURES_LEAD",
    "R_OFI": "R_POLY_CONFIDENCE_EXIT_OFI",
}
CONFIDENCE_VERSION = "poly_confidence_exit_v1"
CONFIDENCE_ACTIVE_STATUSES = {"PENDING_ATTACH", "ARMED", "EXIT_TRIGGERED"}
CONFIDENCE_FINAL_STATUSES = {
    "FINALIZED_EXIT",
    "FINALIZED_NO_EXIT",
    "FINALIZED_TRIGGER_MISSED",
}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and result not in (float("inf"), float("-inf")) else None


def _iso_ms(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _poly_snapshot() -> dict[str, Any]:
    request = urllib.request.Request(
        CROSS_ORACLE_STATE_URL,
        headers={"Accept": "application/json", "User-Agent": "BTC-5M-Lab-Poly-Strategy/1.0"},
    )
    with urllib.request.urlopen(request, timeout=1.5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    polymarket = payload.get("polymarket") if isinstance(payload, dict) else None
    if not isinstance(polymarket, dict):
        raise RuntimeError("cross-oracle /state has no Polymarket snapshot")
    age_ms = _number(polymarket.get("ageMs"))
    if age_ms is None or age_ms > MAX_POLY_AGE_MS:
        raise RuntimeError(f"Polymarket quote is stale: age={age_ms!r} ms")
    return polymarket


def _fresh_binance_latest() -> dict[str, Any]:
    payload = _http_json(BINANCE_REALTIME_URL)
    latest = payload.get("latest") if isinstance(payload, dict) else None
    if not isinstance(latest, dict):
        raise RuntimeError("Binance /api/realtime has no latest snapshot")
    now_ms = int(time.time() * 1000)
    observed_ms = _number(latest.get("observed_timestamp_ms"))
    if observed_ms is not None and now_ms - observed_ms > MAX_BINANCE_OBSERVATION_AGE_MS:
        raise RuntimeError(
            f"Binance observation is stale: age={now_ms - observed_ms:.0f} ms"
        )
    book_age_ms = _number(latest.get("book_age_ms"))
    if book_age_ms is not None and book_age_ms > MAX_BINANCE_BOOK_AGE_MS:
        raise RuntimeError(f"Binance Prediction book is stale: age={book_age_ms:.0f} ms")
    book_skew_ms = _number(latest.get("book_skew_ms"))
    if book_skew_ms is not None and book_skew_ms > MAX_BINANCE_BOOK_SKEW_MS:
        raise RuntimeError(f"Binance UP/DOWN book skew is too large: {book_skew_ms:.0f} ms")
    return latest


class ResilientCrossOraclePaperEngine(CrossOraclePaperEngine):
    """Cross-market Paper engine plus source-mirroring Polymarket exit shadows."""

    def __init__(self, db_path: Path, poly_snapshot_provider: Any) -> None:
        super().__init__(db_path, poly_snapshot_provider)
        self.sim_lock = threading.RLock()
        self.sim_db: sqlite3.Connection | None = None
        self.confidence_error: str | None = None
        self.last_confidence_flip_ms = int(time.time() * 1000)
        self._open_simulation_db()
        self._init_confidence_schema()
        self.confidence_started_at_ms, self.last_source_trade_id = self._init_confidence_meta()

    def _open_simulation_db(self) -> None:
        try:
            uri = f"file:{SIM_DB_PATH.resolve().as_posix()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=2.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA busy_timeout=2000")
            self.sim_db = connection
        except sqlite3.Error as exc:
            self.confidence_error = f"simulation DB unavailable: {exc}"
            self.sim_db = None

    def _init_confidence_schema(self) -> None:
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS poly_confidence_shadow_meta (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    forward_started_at_ms INTEGER NOT NULL,
                    source_trade_id_floor INTEGER NOT NULL,
                    last_scanned_source_trade_id INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS poly_confidence_shadows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_trade_id INTEGER NOT NULL UNIQUE,
                    source_strategy TEXT NOT NULL,
                    shadow_strategy TEXT NOT NULL,
                    binance_market_id INTEGER NOT NULL,
                    poly_market_slug TEXT,
                    side TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_entry_price REAL NOT NULL,
                    source_stake_usdt REAL NOT NULL,
                    source_shares REAL NOT NULL,
                    source_fee_rate_bps INTEGER NOT NULL,
                    source_entry_fees REAL NOT NULL,
                    opened_at TEXT NOT NULL,
                    opened_at_ms INTEGER NOT NULL,
                    attached_at_ms INTEGER,
                    attach_lag_ms INTEGER,
                    entry_poly_direction TEXT,
                    entry_poly_up_mid REAL,
                    triggered_at_ms INTEGER,
                    trigger_poly_from TEXT,
                    trigger_poly_to TEXT,
                    trigger_poly_up_mid REAL,
                    trigger_binance_up_mid REAL,
                    exit_price REAL,
                    exit_at_ms INTEGER,
                    exit_reason TEXT,
                    shadow_exit_pnl_usdt REAL,
                    shadow_effective_pnl_usdt REAL,
                    source_final_status TEXT,
                    source_final_pnl_usdt REAL,
                    counterfactual_no_exit_pnl_usdt REAL,
                    avoided_loss_usdt REAL NOT NULL DEFAULT 0,
                    sacrificed_profit_usdt REAL NOT NULL DEFAULT 0,
                    net_protection_usdt REAL NOT NULL DEFAULT 0,
                    finalized_at_ms INTEGER,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_poly_confidence_source
                    ON poly_confidence_shadows(source_strategy, source_trade_id);
                CREATE INDEX IF NOT EXISTS idx_poly_confidence_market_status
                    ON poly_confidence_shadows(binance_market_id, status, id);
                """
            )
            self.db.commit()

    def _max_sim_trade_id(self) -> int:
        if self.sim_db is None:
            return 0
        with self.sim_lock:
            try:
                row = self.sim_db.execute("SELECT COALESCE(MAX(id), 0) FROM trades").fetchone()
            except sqlite3.Error as exc:
                self.confidence_error = f"source cursor read failed: {exc}"
                return 0
        return int(row[0]) if row is not None else 0

    def _init_confidence_meta(self) -> tuple[int, int]:
        with self.db_lock:
            row = self.db.execute(
                "SELECT * FROM poly_confidence_shadow_meta WHERE singleton=1"
            ).fetchone()
            if row is None:
                now_ms = int(time.time() * 1000)
                floor = self._max_sim_trade_id()
                self.db.execute(
                    """INSERT INTO poly_confidence_shadow_meta(
                           singleton, forward_started_at_ms, source_trade_id_floor,
                           last_scanned_source_trade_id
                       ) VALUES (1, ?, ?, ?)""",
                    (now_ms, floor, floor),
                )
                self.db.commit()
                return now_ms, floor
            return int(row["forward_started_at_ms"]), int(row["last_scanned_source_trade_id"])

    def stop(self) -> None:
        if self.sim_db is not None:
            with self.sim_lock:
                try:
                    self.sim_db.close()
                except sqlite3.Error:
                    pass
            self.sim_db = None
        super().stop()

    def _source_rows_after_cursor(self) -> list[sqlite3.Row]:
        if self.sim_db is None:
            return []
        placeholders = ",".join("?" for _ in CONFIDENCE_SOURCES)
        with self.sim_lock:
            try:
                rows = self.sim_db.execute(
                    f"""SELECT id, strategy, market_id, side, status, entry_price,
                               stake, shares, fees, fee_rate_bps, pnl, opened_at, closed_at
                        FROM trades
                        WHERE id > ? AND strategy IN ({placeholders})
                        ORDER BY id ASC""",
                    (self.last_source_trade_id, *CONFIDENCE_SOURCES.keys()),
                ).fetchall()
            except sqlite3.Error as exc:
                self.confidence_error = f"source trade scan failed: {exc}"
                return []
        return rows

    def _source_trade(self, source_trade_id: int) -> sqlite3.Row | None:
        if self.sim_db is None:
            return None
        with self.sim_lock:
            try:
                return self.sim_db.execute(
                    """SELECT id, strategy, market_id, side, status, entry_price,
                              stake, shares, fees, fee_rate_bps, pnl, opened_at, closed_at
                       FROM trades WHERE id=?""",
                    (int(source_trade_id),),
                ).fetchone()
            except sqlite3.Error as exc:
                self.confidence_error = f"source trade lookup failed: {exc}"
                return None

    def _persist_source_cursor(self) -> None:
        with self.db_lock:
            self.db.execute(
                "UPDATE poly_confidence_shadow_meta SET last_scanned_source_trade_id=? WHERE singleton=1",
                (int(self.last_source_trade_id),),
            )
            self.db.commit()

    def _sync_source_trades(self, now_ms: int) -> None:
        rows = self._source_rows_after_cursor()
        if not rows:
            return
        for row in rows:
            self.last_source_trade_id = max(self.last_source_trade_id, int(row["id"]))
            opened_ms = _iso_ms(row["opened_at"])
            if opened_ms is None or opened_ms < self.confidence_started_at_ms:
                continue
            if row["pnl"] is not None or row["closed_at"] is not None:
                continue
            side = str(row["side"] or "").upper()
            if side not in {"UP", "DOWN"}:
                continue
            entry_price = _number(row["entry_price"])
            stake = _number(row["stake"])
            shares = _number(row["shares"])
            fee_bps = int(_number(row["fee_rate_bps"]) or 200)
            if entry_price is None or stake is None or shares is None or min(entry_price, stake, shares) <= 0:
                continue
            recorded_fees = _number(row["fees"])
            entry_fees = (
                recorded_fees
                if recorded_fees is not None and recorded_fees >= 0
                else taker_fee(shares, entry_price, fee_bps)
            )
            with self.db_lock:
                self.db.execute(
                    """INSERT OR IGNORE INTO poly_confidence_shadows(
                           source_trade_id, source_strategy, shadow_strategy,
                           binance_market_id, side, status, source_entry_price,
                           source_stake_usdt, source_shares, source_fee_rate_bps,
                           source_entry_fees, opened_at, opened_at_ms, metadata_json
                       ) VALUES (?, ?, ?, ?, ?, 'PENDING_ATTACH', ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        int(row["id"]),
                        str(row["strategy"]),
                        CONFIDENCE_SOURCES[str(row["strategy"])],
                        int(row["market_id"]),
                        side,
                        entry_price,
                        stake,
                        shares,
                        fee_bps,
                        entry_fees,
                        str(row["opened_at"]),
                        opened_ms,
                        json.dumps({"sourceStatusAtMirror": row["status"]}, separators=(",", ":")),
                    ),
                )
                self.db.commit()
        self._persist_source_cursor()

    def _activate_pending_sources(self, runtime: dict[str, Any], now_ms: int) -> None:
        with self.db_lock:
            rows = self.db.execute(
                "SELECT * FROM poly_confidence_shadows WHERE status='PENDING_ATTACH' ORDER BY id"
            ).fetchall()
        runtime_market = runtime.get("binanceMarketId")
        runtime_slug = str(runtime.get("polyMarketSlug") or "")
        runtime_aligned = runtime.get("aligned") is True
        poly_direction = str(runtime.get("polyDirection") or "") or None
        poly_up_mid = _number(runtime.get("polyUpMid"))
        for row in rows:
            source = self._source_trade(int(row["source_trade_id"]))
            if source is not None and source["pnl"] is not None:
                with self.db_lock:
                    self.db.execute(
                        "UPDATE poly_confidence_shadows SET status='NOT_EVALUABLE_SOURCE_FINAL', finalized_at_ms=? WHERE id=?",
                        (now_ms, int(row["id"])),
                    )
                    self.db.commit()
                continue
            attach_lag = now_ms - int(row["opened_at_ms"])
            if attach_lag > CONFIDENCE_ATTACH_MAX_LAG_MS:
                with self.db_lock:
                    self.db.execute(
                        """UPDATE poly_confidence_shadows
                           SET status='NOT_EVALUABLE_LATE_ATTACH', attach_lag_ms=?, finalized_at_ms=?
                           WHERE id=?""",
                        (attach_lag, now_ms, int(row["id"])),
                    )
                    self.db.commit()
                continue
            if not runtime_aligned:
                continue
            try:
                same_market = int(runtime_market) == int(row["binance_market_id"])
            except (TypeError, ValueError):
                same_market = False
            if not same_market or not runtime_slug:
                continue
            with self.db_lock:
                self.db.execute(
                    """UPDATE poly_confidence_shadows
                       SET status='ARMED', poly_market_slug=?, attached_at_ms=?,
                           attach_lag_ms=?, entry_poly_direction=?, entry_poly_up_mid=?
                       WHERE id=? AND status='PENDING_ATTACH'""",
                    (
                        runtime_slug,
                        now_ms,
                        attach_lag,
                        poly_direction,
                        poly_up_mid,
                        int(row["id"]),
                    ),
                )
                self.db.commit()

    def _arm_confidence_exits_from_flip(
        self,
        last_flip: dict[str, Any] | None,
        runtime: dict[str, Any],
    ) -> None:
        if not last_flip:
            return
        flip_at = int(last_flip.get("atMs") or 0)
        if flip_at <= self.last_confidence_flip_ms:
            return
        self.last_confidence_flip_ms = flip_at
        new_direction = str(last_flip.get("to") or "")
        old_direction = str(last_flip.get("from") or "")
        if new_direction not in {"UP", "DOWN"} or old_direction not in {"UP", "DOWN"}:
            return
        market_id = last_flip.get("binanceMarketId") or runtime.get("binanceMarketId")
        try:
            market_id = int(market_id)
        except (TypeError, ValueError):
            return
        with self.db_lock:
            rows = self.db.execute(
                """SELECT id, side, opened_at_ms FROM poly_confidence_shadows
                   WHERE status='ARMED' AND binance_market_id=?""",
                (market_id,),
            ).fetchall()
            for row in rows:
                if int(row["opened_at_ms"]) >= flip_at or str(row["side"]) == new_direction:
                    continue
                self.db.execute(
                    """UPDATE poly_confidence_shadows
                       SET status='EXIT_TRIGGERED', triggered_at_ms=?,
                           trigger_poly_from=?, trigger_poly_to=?, trigger_poly_up_mid=?,
                           trigger_binance_up_mid=?, exit_reason='POLY_CONFIDENT_DIRECTION_FLIP'
                       WHERE id=? AND status='ARMED'""",
                    (
                        flip_at,
                        old_direction,
                        new_direction,
                        _number(last_flip.get("polyUpMid")),
                        _number(last_flip.get("binanceUpMid")),
                        int(row["id"]),
                    ),
                )
            self.db.commit()

    def _retry_confidence_exits(self, latest: dict[str, Any], now_ms: int) -> None:
        try:
            market_id = int(latest.get("market_id"))
        except (TypeError, ValueError):
            return
        with self.db_lock:
            rows = self.db.execute(
                """SELECT * FROM poly_confidence_shadows
                   WHERE status='EXIT_TRIGGERED' AND binance_market_id=? ORDER BY id""",
                (market_id,),
            ).fetchall()
        for row in rows:
            bid = selected_quote(latest, str(row["side"]), "bid")
            if bid is None:
                continue
            shares = float(row["source_shares"])
            stake = float(row["source_stake_usdt"])
            entry_fees = float(row["source_entry_fees"])
            fee_bps = int(row["source_fee_rate_bps"])
            exit_fee = taker_fee(shares, bid, fee_bps)
            pnl = shares * bid - stake - entry_fees - exit_fee
            metadata = {
                "exitBookAgeMs": _number(latest.get("book_age_ms")),
                "exitBookSkewMs": _number(latest.get("book_skew_ms")),
                "exitFeeUsdt": exit_fee,
            }
            with self.db_lock:
                self.db.execute(
                    """UPDATE poly_confidence_shadows
                       SET status='EXITED', exit_price=?, exit_at_ms=?,
                           shadow_exit_pnl_usdt=?, metadata_json=?
                       WHERE id=? AND status='EXIT_TRIGGERED'""",
                    (
                        bid,
                        now_ms,
                        pnl,
                        json.dumps(metadata, separators=(",", ":"), sort_keys=True),
                        int(row["id"]),
                    ),
                )
                self.db.commit()

    def _finalize_confidence_rows(self, now_ms: int) -> None:
        with self.db_lock:
            rows = self.db.execute(
                """SELECT * FROM poly_confidence_shadows
                   WHERE status IN ('ARMED','EXIT_TRIGGERED','EXITED') ORDER BY id"""
            ).fetchall()
        for row in rows:
            source = self._source_trade(int(row["source_trade_id"]))
            if source is None or source["pnl"] is None:
                continue
            source_pnl = float(source["pnl"])
            source_status = str(source["status"] or "FINAL")
            current_status = str(row["status"])
            if current_status == "EXITED" and row["shadow_exit_pnl_usdt"] is not None:
                effective = float(row["shadow_exit_pnl_usdt"])
                final_status = "FINALIZED_EXIT"
                delta = effective - source_pnl
                avoided = min(max(0.0, delta), max(0.0, -source_pnl)) if source_pnl < 0 else 0.0
                sacrificed = min(max(0.0, -delta), max(0.0, source_pnl)) if source_pnl > 0 else 0.0
                net = delta
            elif current_status == "EXIT_TRIGGERED":
                effective = source_pnl
                final_status = "FINALIZED_TRIGGER_MISSED"
                avoided = sacrificed = net = 0.0
            else:
                effective = source_pnl
                final_status = "FINALIZED_NO_EXIT"
                avoided = sacrificed = net = 0.0
            with self.db_lock:
                self.db.execute(
                    """UPDATE poly_confidence_shadows
                       SET status=?, shadow_effective_pnl_usdt=?, source_final_status=?,
                           source_final_pnl_usdt=?, counterfactual_no_exit_pnl_usdt=?,
                           avoided_loss_usdt=?, sacrificed_profit_usdt=?,
                           net_protection_usdt=?, finalized_at_ms=?
                       WHERE id=?""",
                    (
                        final_status,
                        effective,
                        source_status,
                        source_pnl,
                        source_pnl,
                        avoided,
                        sacrificed,
                        net,
                        now_ms,
                        int(row["id"]),
                    ),
                )
                self.db.commit()

    @staticmethod
    def _confidence_state_for_row(row: sqlite3.Row | None, poly_direction: str | None) -> str:
        if row is None:
            return "WAITING_SOURCE_ENTRY"
        status = str(row["status"])
        if status == "PENDING_ATTACH":
            return "ATTACHING"
        if status == "EXIT_TRIGGERED":
            return "EXIT_PENDING"
        if status == "EXITED":
            return "EXITED_ON_POLY_FLIP"
        if status.startswith("FINALIZED"):
            return status
        if status.startswith("NOT_EVALUABLE"):
            return status
        if poly_direction not in {"UP", "DOWN"}:
            return "POLY_NEUTRAL"
        return "SUPPORTED" if str(row["side"]) == poly_direction else "OPPOSED_NO_NEW_FLIP"

    def _confidence_summary(self, poly_direction: str | None) -> dict[str, Any]:
        strategies: dict[str, Any] = {}
        with self.db_lock:
            all_rows = self.db.execute(
                "SELECT * FROM poly_confidence_shadows ORDER BY id DESC"
            ).fetchall()
        for source_strategy, shadow_strategy in CONFIDENCE_SOURCES.items():
            rows = [row for row in all_rows if str(row["source_strategy"]) == source_strategy]
            finalized = [row for row in rows if str(row["status"]) in CONFIDENCE_FINAL_STATUSES]
            exited_final = [row for row in finalized if str(row["status"]) == "FINALIZED_EXIT"]
            effective = [float(row["shadow_effective_pnl_usdt"]) for row in finalized if row["shadow_effective_pnl_usdt"] is not None]
            wins = sum(value > 0 for value in effective)
            losses = sum(value < 0 for value in effective)
            latest_active = next((row for row in rows if str(row["status"]) in CONFIDENCE_ACTIVE_STATUSES or str(row["status"]) == "EXITED"), None)
            strategies[source_strategy] = {
                "sourceStrategy": source_strategy,
                "shadowStrategy": shadow_strategy,
                "sourceTrades": len(rows),
                "active": sum(str(row["status"]) in CONFIDENCE_ACTIVE_STATUSES for row in rows),
                "notEvaluable": sum(str(row["status"]).startswith("NOT_EVALUABLE") for row in rows),
                "flipTriggered": sum(row["triggered_at_ms"] is not None for row in rows),
                "earlyExited": sum(row["exit_at_ms"] is not None for row in rows),
                "finalized": len(finalized),
                "wins": wins,
                "losses": losses,
                "winRate": wins / (wins + losses) if wins + losses else None,
                "realizedPnl": sum(effective),
                "counterfactualNoExitPnl": sum(
                    float(row["counterfactual_no_exit_pnl_usdt"])
                    for row in finalized
                    if row["counterfactual_no_exit_pnl_usdt"] is not None
                ),
                "avoidedLossUsdt": sum(float(row["avoided_loss_usdt"] or 0) for row in exited_final),
                "sacrificedProfitUsdt": sum(float(row["sacrificed_profit_usdt"] or 0) for row in exited_final),
                "netProtectionUsdt": sum(float(row["net_protection_usdt"] or 0) for row in exited_final),
                "beneficialExits": sum(float(row["net_protection_usdt"] or 0) > 0 for row in exited_final),
                "harmfulExits": sum(float(row["net_protection_usdt"] or 0) < 0 for row in exited_final),
                "confidenceState": self._confidence_state_for_row(latest_active, poly_direction),
                "currentSide": str(latest_active["side"]) if latest_active is not None else None,
                "currentMarketId": int(latest_active["binance_market_id"]) if latest_active is not None else None,
            }
        recent = []
        for row in all_rows[:60]:
            recent.append({
                "id": int(row["id"]),
                "sourceTradeId": int(row["source_trade_id"]),
                "sourceStrategy": str(row["source_strategy"]),
                "shadowStrategy": str(row["shadow_strategy"]),
                "marketId": int(row["binance_market_id"]),
                "polyMarketSlug": row["poly_market_slug"],
                "side": str(row["side"]),
                "status": str(row["status"]),
                "openedAt": row["opened_at"],
                "entryPrice": float(row["source_entry_price"]),
                "entryPolyDirection": row["entry_poly_direction"],
                "entryPolyUpMid": row["entry_poly_up_mid"],
                "triggeredAtMs": row["triggered_at_ms"],
                "triggerPolyFrom": row["trigger_poly_from"],
                "triggerPolyTo": row["trigger_poly_to"],
                "triggerPolyUpMid": row["trigger_poly_up_mid"],
                "exitPrice": row["exit_price"],
                "exitAtMs": row["exit_at_ms"],
                "exitPnlUsdt": row["shadow_exit_pnl_usdt"],
                "effectivePnlUsdt": row["shadow_effective_pnl_usdt"],
                "noExitPnlUsdt": row["counterfactual_no_exit_pnl_usdt"],
                "avoidedLossUsdt": row["avoided_loss_usdt"],
                "sacrificedProfitUsdt": row["sacrificed_profit_usdt"],
                "netProtectionUsdt": row["net_protection_usdt"],
                "sourceFinalStatus": row["source_final_status"],
            })
        return {"strategies": strategies, "recent": recent}

    def _evaluate_once(self) -> None:
        # Fail closed before either Paper family can create a fill from stale
        # Binance top-of-book data. Polymarket freshness is checked by provider.
        latest = _fresh_binance_latest()
        super()._evaluate_once()

        with self.lock:
            runtime = dict(self.runtime)
            last_flip = dict(self.last_flip) if self.last_flip else None
        now_ms = int(time.time() * 1000)

        self._sync_source_trades(now_ms)
        self._activate_pending_sources(runtime, now_ms)
        self._arm_confidence_exits_from_flip(last_flip, runtime)
        self._retry_confidence_exits(latest, now_ms)
        self._finalize_confidence_rows(now_ms)

        # Existing lead-entry family retry behavior.
        if runtime.get("aligned") is not True:
            return
        poly_direction = str(runtime.get("polyDirection") or "")
        if poly_direction not in {"UP", "DOWN"}:
            return
        market_id_raw = runtime.get("binanceMarketId")
        try:
            market_id = int(market_id_raw)
        except (TypeError, ValueError):
            return

        for strategy in (STRATEGY_POLY_LEAD_EXIT, STRATEGY_POLY_GAP_SCALP):
            trade = self._open_trade_for_market(strategy, market_id)
            if trade is not None and str(trade["side"]) != poly_direction:
                self._exit_trade_at_bid(
                    trade,
                    latest,
                    now_ms,
                    "POLY_DIRECTION_FLIP_RETRY",
                )

        if not last_flip:
            return
        if int(last_flip.get("binanceMarketId") or -1) != market_id:
            return
        flip_at_ms = int(last_flip.get("atMs") or 0)
        if flip_at_ms <= 0 or now_ms - flip_at_ms > LEAD_RETRY_WINDOW_MS:
            return
        if runtime.get("binanceDirection") == poly_direction:
            return
        poly_up_mid = _number(runtime.get("polyUpMid"))
        binance_up_mid = _number(runtime.get("binanceUpMid"))
        poly_slug = str(runtime.get("polyMarketSlug") or "")
        if poly_up_mid is None or binance_up_mid is None or not poly_slug:
            return
        for strategy in (STRATEGY_POLY_LEAD_ENTRY, STRATEGY_POLY_LEAD_EXIT):
            if self._has_trade_for_market(strategy, market_id):
                continue
            self._open_trade(
                strategy=strategy,
                latest=latest,
                binance_market_id=market_id,
                poly_slug=poly_slug,
                side=poly_direction,
                poly_up_mid=poly_up_mid,
                binance_up_mid=binance_up_mid,
                now_ms=now_ms,
                reason="POLY_FLIP_LEADS_BINANCE_RETRY",
                metadata={"flipAgeMs": now_ms - flip_at_ms},
            )

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        parameters = payload.setdefault("parameters", {})
        parameters.update(
            {
                "maxPolyAgeMs": MAX_POLY_AGE_MS,
                "maxBinanceObservationAgeMs": MAX_BINANCE_OBSERVATION_AGE_MS,
                "maxBinanceBookAgeMs": MAX_BINANCE_BOOK_AGE_MS,
                "maxBinanceBookSkewMs": MAX_BINANCE_BOOK_SKEW_MS,
                "leadRetryWindowMs": LEAD_RETRY_WINDOW_MS,
                "confidenceAttachMaxLagMs": CONFIDENCE_ATTACH_MAX_LAG_MS,
            }
        )
        with self.lock:
            runtime = dict(self.runtime)
        poly_direction = str(runtime.get("polyDirection") or "") or None
        confidence = self._confidence_summary(poly_direction)
        payload["confidenceShadows"] = {
            "version": CONFIDENCE_VERSION,
            "paperOnly": True,
            "liveOrdersAffected": False,
            "forwardOnly": True,
            "feesIncluded": True,
            "forwardStartedAtMs": self.confidence_started_at_ms,
            "status": "ERROR" if self.confidence_error else "LIVE",
            "error": self.confidence_error,
            "rules": {
                "sources": list(CONFIDENCE_SOURCES),
                "trigger": "source opens first, then a new confident Polymarket flip to the opposite side",
                "exitExecution": "current fresh Binance held-side best bid",
                "noFlip": "mirror source strategy until its own final PnL",
                "counterfactual": "source trade final PnL if the Polymarket exit had not been taken",
            },
            **confidence,
        }
        payload["executionQuoteSource"] = "Binance /api/realtime executable top-of-book"
        payload["timingNote"] = (
            "Polymarket is event-streamed; Binance Paper execution quotes are the newest "
            "fresh /api/realtime observation. Confidence shadows only react to a new "
            "confident Poly flip occurring after the mirrored source trade opened."
        )
        return payload


class _Handler(BaseHTTPRequestHandler):
    engine: ResilientCrossOraclePaperEngine

    def do_GET(self) -> None:  # noqa: N802
        if self.path not in {"/state", "/health", "/api/state"}:
            self.send_response(404)
            self.end_headers()
            return
        payload = self.engine.snapshot()
        payload["generatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        body = json.dumps(payload, allow_nan=False, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def main() -> int:
    engine = ResilientCrossOraclePaperEngine(DB_PATH, _poly_snapshot)
    engine.start()
    handler = type("CrossOracleStrategyHandler", (_Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Cross-oracle Paper strategies listening on http://{HOST}:{PORT}/state; db={DB_PATH}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        engine.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
