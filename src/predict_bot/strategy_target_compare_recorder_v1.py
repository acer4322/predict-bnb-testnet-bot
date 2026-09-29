from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "strategy_target_compare_v1.db"
VERSION = "STRATEGY_TARGET_COMPARE_RECORDER_V1"


def now_ms() -> int:
    return int(time.time() * 1000)


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"), default=str)


class StrategyTargetCompareRecorder:
    """OUR-side only research recorder.

    Critical boundary: this class never reads Target wallet data. Target joins happen
    post-hoc so target future events cannot leak into strategy decisions.
    """

    def __init__(self, path: Path = DEFAULT_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self._schema()

    def _schema(self) -> None:
        with self.lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS compare_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS our_decisions (
                    decision_id TEXT PRIMARY KEY,
                    strategy_version TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    decision_ms INTEGER NOT NULL,
                    source_snapshot_ms INTEGER,
                    seconds_left REAL,
                    phase TEXT,
                    desired_portfolio_action TEXT NOT NULL,
                    execution_choice TEXT NOT NULL,
                    side TEXT,
                    size REAL,
                    primary_reason TEXT NOT NULL,
                    supporting_reasons_json TEXT NOT NULL,
                    veto_reasons_json TEXT NOT NULL,
                    direction_state_json TEXT NOT NULL,
                    portfolio_state_json TEXT NOT NULL,
                    economics_state_json TEXT NOT NULL,
                    arbitration_state_json TEXT NOT NULL,
                    public_state_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_compare_decisions_market_time
                    ON our_decisions(market_id,decision_ms);
                CREATE TABLE IF NOT EXISTS our_orders (
                    order_id TEXT PRIMARY KEY,
                    strategy_version TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    placement_decision_id TEXT,
                    channel TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quote_type TEXT NOT NULL,
                    price REAL NOT NULL,
                    shares REAL NOT NULL,
                    placed_at_ms INTEGER NOT NULL,
                    placement_state_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    filled_at_ms INTEGER,
                    fill_price REAL,
                    fill_state_json TEXT,
                    cancelled_at_ms INTEGER,
                    cancel_reason TEXT,
                    updated_at_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_compare_orders_market_time
                    ON our_orders(market_id,placed_at_ms,status);
                CREATE TABLE IF NOT EXISTS our_fills (
                    fill_id TEXT PRIMARY KEY,
                    strategy_version TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    decision_id TEXT,
                    order_id TEXT,
                    channel TEXT NOT NULL,
                    purpose TEXT,
                    side TEXT NOT NULL,
                    quote_type TEXT NOT NULL,
                    price REAL NOT NULL,
                    shares REAL NOT NULL,
                    filled_at_ms INTEGER NOT NULL,
                    decision_state_json TEXT NOT NULL,
                    fill_state_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_compare_fills_market_time
                    ON our_fills(market_id,filled_at_ms,channel);
                """
            )
            self.db.execute(
                "INSERT OR REPLACE INTO compare_meta(key,value,updated_at_ms) VALUES('service',?,?)",
                (_json({"version": VERSION, "targetDataRead": False, "researchOnly": True}), now_ms()),
            )
            self.db.commit()

    def close(self) -> None:
        with self.lock:
            self.db.commit()
            self.db.execute("PRAGMA wal_checkpoint(PASSIVE)")
            self.db.close()

    def record_decision(
        self,
        *,
        decision_id: str,
        strategy_version: str,
        market_id: int,
        decision_ms: int,
        desired_portfolio_action: str,
        execution_choice: str,
        primary_reason: str,
        source_snapshot_ms: int | None = None,
        seconds_left: float | None = None,
        phase: str | None = None,
        side: str | None = None,
        size: float | None = None,
        supporting_reasons: Any = None,
        veto_reasons: Any = None,
        direction_state: Mapping[str, Any] | None = None,
        portfolio_state: Mapping[str, Any] | None = None,
        economics_state: Mapping[str, Any] | None = None,
        arbitration_state: Mapping[str, Any] | None = None,
        public_state: Mapping[str, Any] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        with self.lock:
            self.db.execute(
                """INSERT OR REPLACE INTO our_decisions(
                       decision_id,strategy_version,market_id,decision_ms,source_snapshot_ms,seconds_left,phase,
                       desired_portfolio_action,execution_choice,side,size,primary_reason,
                       supporting_reasons_json,veto_reasons_json,direction_state_json,portfolio_state_json,
                       economics_state_json,arbitration_state_json,public_state_json,payload_json,created_at_ms
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(decision_id), str(strategy_version), int(market_id), int(decision_ms),
                    int(source_snapshot_ms) if source_snapshot_ms is not None else None,
                    float(seconds_left) if seconds_left is not None else None,
                    str(phase) if phase is not None else None,
                    str(desired_portfolio_action), str(execution_choice), str(side) if side is not None else None,
                    float(size) if size is not None else None, str(primary_reason),
                    _json(supporting_reasons), _json(veto_reasons), _json(direction_state), _json(portfolio_state),
                    _json(economics_state), _json(arbitration_state), _json(public_state), _json(payload), now_ms(),
                ),
            )
            self.db.commit()

    def record_order_placement(
        self,
        *,
        order_id: str,
        strategy_version: str,
        market_id: int,
        placement_decision_id: str | None,
        channel: str,
        side: str,
        quote_type: str,
        price: float,
        shares: float,
        placed_at_ms: int,
        placement_state: Mapping[str, Any] | None,
    ) -> None:
        with self.lock:
            self.db.execute(
                """INSERT OR REPLACE INTO our_orders(
                       order_id,strategy_version,market_id,placement_decision_id,channel,side,quote_type,
                       price,shares,placed_at_ms,placement_state_json,status,updated_at_ms
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'ACTIVE',?)""",
                (
                    str(order_id), str(strategy_version), int(market_id), placement_decision_id,
                    str(channel), str(side), str(quote_type), float(price), float(shares), int(placed_at_ms),
                    _json(placement_state), now_ms(),
                ),
            )
            self.db.commit()

    def record_order_fill(
        self,
        *,
        order_id: str,
        fill_id: str,
        filled_at_ms: int,
        fill_price: float,
        fill_state: Mapping[str, Any] | None,
        purpose: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        with self.lock:
            row = self.db.execute("SELECT * FROM our_orders WHERE order_id=?", (str(order_id),)).fetchone()
            if row is None:
                raise KeyError(f"unknown order_id: {order_id}")
            self.db.execute(
                """UPDATE our_orders SET status='FILLED',filled_at_ms=?,fill_price=?,fill_state_json=?,updated_at_ms=?
                   WHERE order_id=?""",
                (int(filled_at_ms), float(fill_price), _json(fill_state), now_ms(), str(order_id)),
            )
            self.db.execute(
                """INSERT OR REPLACE INTO our_fills(
                       fill_id,strategy_version,market_id,decision_id,order_id,channel,purpose,side,quote_type,
                       price,shares,filled_at_ms,decision_state_json,fill_state_json,payload_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(fill_id), str(row["strategy_version"]), int(row["market_id"]), row["placement_decision_id"],
                    str(order_id), str(row["channel"]), purpose, str(row["side"]), str(row["quote_type"]),
                    float(fill_price), float(row["shares"]), int(filled_at_ms), str(row["placement_state_json"]),
                    _json(fill_state), _json(payload),
                ),
            )
            self.db.commit()

    def record_order_fill_delta(
        self,
        *,
        order_id: str,
        fill_id: str,
        filled_at_ms: int,
        fill_price: float,
        shares: float,
        fill_state: Mapping[str, Any] | None,
        purpose: str | None = None,
        payload: Mapping[str, Any] | None = None,
        terminal: bool = False,
    ) -> bool:
        """Append one execution-confirmed fill delta without inventing full size.

        ``fill_id`` is the idempotency key.  Replaying the same venue event is a
        no-op, which makes controller restart/event replay safe for the recorder.
        Partial deltas leave the order ACTIVE so cancellation bookkeeping remains
        compatible with the existing recorder schema; only a terminal fill marks
        the order FILLED.
        """
        if float(shares) <= 0:
            return False
        with self.lock:
            row = self.db.execute("SELECT * FROM our_orders WHERE order_id=?", (str(order_id),)).fetchone()
            if row is None:
                raise KeyError(f"unknown order_id: {order_id}")
            exists = self.db.execute("SELECT 1 FROM our_fills WHERE fill_id=?", (str(fill_id),)).fetchone()
            if exists is not None:
                return False
            self.db.execute(
                """INSERT INTO our_fills(
                       fill_id,strategy_version,market_id,decision_id,order_id,channel,purpose,side,quote_type,
                       price,shares,filled_at_ms,decision_state_json,fill_state_json,payload_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(fill_id), str(row["strategy_version"]), int(row["market_id"]), row["placement_decision_id"],
                    str(order_id), str(row["channel"]), purpose, str(row["side"]), str(row["quote_type"]),
                    float(fill_price), float(shares), int(filled_at_ms), str(row["placement_state_json"]),
                    _json(fill_state), _json(payload),
                ),
            )
            if terminal:
                self.db.execute(
                    """UPDATE our_orders SET status='FILLED',filled_at_ms=?,fill_price=?,fill_state_json=?,updated_at_ms=?
                       WHERE order_id=?""",
                    (int(filled_at_ms), float(fill_price), _json(fill_state), now_ms(), str(order_id)),
                )
            self.db.commit()
            return True

    def record_order_cancel(self, *, order_id: str, cancelled_at_ms: int, reason: str) -> None:
        with self.lock:
            self.db.execute(
                """UPDATE our_orders SET status='CANCELLED',cancelled_at_ms=?,cancel_reason=?,updated_at_ms=?
                   WHERE order_id=? AND status='ACTIVE'""",
                (int(cancelled_at_ms), str(reason), now_ms(), str(order_id)),
            )
            self.db.commit()

    def record_taker_fill(
        self,
        *,
        fill_id: str,
        strategy_version: str,
        market_id: int,
        decision_id: str | None,
        purpose: str,
        side: str,
        price: float,
        shares: float,
        filled_at_ms: int,
        decision_state: Mapping[str, Any] | None,
        fill_state: Mapping[str, Any] | None,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        with self.lock:
            self.db.execute(
                """INSERT OR REPLACE INTO our_fills(
                       fill_id,strategy_version,market_id,decision_id,order_id,channel,purpose,side,quote_type,
                       price,shares,filled_at_ms,decision_state_json,fill_state_json,payload_json
                   ) VALUES(?,?,?,?,NULL,'TAKER',?,?,'BID',?,?,?,?,?,?)""",
                (
                    str(fill_id), str(strategy_version), int(market_id), decision_id, str(purpose), str(side),
                    float(price), float(shares), int(filled_at_ms), _json(decision_state), _json(fill_state), _json(payload),
                ),
            )
            self.db.commit()
