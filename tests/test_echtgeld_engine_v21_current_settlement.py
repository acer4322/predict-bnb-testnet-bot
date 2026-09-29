from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from predict_bot import echtgeld_engine_v21 as v21


class _FakeExecutor:
    def __init__(self, config) -> None:
        self.config = config

    def close(self) -> None:
        return

    def available_balance_snapshot(self) -> dict:
        return {"status": "OK", "availableUsdt": 100.0, "source": "test"}

    def execute(self, **_kwargs) -> dict:
        raise AssertionError("settlement test must never execute an order")


def _official_db(path: Path, *, status: str = "SETTLED", winner: str | None = "UP") -> None:
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE target_markets(
               market_id INTEGER PRIMARY KEY,
               asset TEXT,
               title TEXT,
               window_end_ms INTEGER,
               first_seen_ms INTEGER,
               last_seen_ms INTEGER,
               status TEXT,
               winner TEXT,
               resolved_at_ms INTEGER
           )"""
    )
    db.execute(
        "INSERT INTO target_markets VALUES(1505882,'BTC','test',2000,1000,3000,?,?,3000)",
        (status, winner),
    )
    db.commit()
    db.close()


def _engine(tmp_path: Path, *, status: str = "SETTLED", winner: str | None = "UP") -> v21.EchtgeldEngine:
    official = tmp_path / "target_wallet_official.db"
    _official_db(official, status=status, winner=winner)
    engine = v21.EchtgeldEngine(
        tmp_path / "echtgeld.db",
        executor_factory=_FakeExecutor,
        start_worker=False,
        settlement_db_path=tmp_path / "retired-wallet-shadow.db",
        target_official_db_path=official,
    )
    with engine.db_lock:
        engine.db.execute(
            """INSERT INTO engine_intents(
                   intent_id,dedupe_key,strategy,cohort,market_id,signal_id,side,signal_ask,
                   created_at_ms,received_at_ms,status,payload_json
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "target-intent-1", "target-dedupe-1", "TARGET_TAKER_PUBLIC_SIDE_V1_EBM_FORWARD",
                "TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY", 1505882, "signal-1", "UP", 0.62,
                1000, 1000, "SUBMITTED", "{}",
            ),
        )
        engine.db.execute(
            """INSERT INTO engine_orders(
                   dedupe_key,intent_id,strategy,cohort,market_id,venue,side,signal_ask,
                   target_notional_usdt,status,attempted_at_ms,completed_at_ms,execution_price,
                   shares,submitted_usdt,vendor_order_id,vendor_order_hash,error_message,result_json,context_json
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "target-dedupe-1", "target-intent-1", "TARGET_TAKER_PUBLIC_SIDE_V1_EBM_FORWARD",
                "TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY", 1505882, "binance", "UP", 0.62,
                1.0, "SUBMITTED", 1000, 1100, 0.62, 1.57, 0.98, "order-1", "hash-1", None,
                json.dumps({"status": "SUBMITTED"}), "{}",
            ),
        )
        engine.db.commit()
    return engine


def test_current_target_official_settlement_feeds_target_pnl_and_stop_loss(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        perf = engine._performance_snapshot(force_sync=True)
        assert perf["settledCounted"] == 1
        assert perf["targetTakerNetPnlUsdt"] == pytest.approx(0.59)
        assert perf["netPnlUsdt"] == pytest.approx(0.59)

        settlement = engine.db.execute(
            "SELECT winner,source FROM engine_settlements WHERE cohort=? AND market_id=?",
            ("TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY", 1505882),
        ).fetchone()
        assert settlement["winner"] == "UP"
        assert settlement["source"] == v21.TARGET_SETTLEMENT_SOURCE

        engine.stop_loss_usdt = 0.5
        risk = engine._risk_snapshot(perf)
        assert risk["currentNetPnlUsdt"] == pytest.approx(0.59)
        assert risk["tripped"] is False
    finally:
        engine.close()


def test_active_or_missing_winner_is_not_guessed(tmp_path: Path) -> None:
    engine = _engine(tmp_path, status="ACTIVE", winner=None)
    try:
        perf = engine._performance_snapshot(force_sync=True)
        assert perf["settledCounted"] == 0
        assert perf["targetTakerNetPnlUsdt"] == 0.0
        assert engine.db.execute(
            "SELECT 1 FROM engine_settlements WHERE market_id=1505882"
        ).fetchone() is None
    finally:
        engine.close()


def test_poly_fast_intent_ids_are_not_imported_as_target_source_markets(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        with engine.db_lock:
            engine.db.execute(
                """INSERT INTO engine_orders(
                       dedupe_key,intent_id,strategy,cohort,market_id,venue,side,signal_ask,
                       target_notional_usdt,status,attempted_at_ms,result_json,context_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "poly-dedupe", "poly-fast:BTC:1505882:1:ENTRY", "R_POLY_GAP_SCALP_LIVE",
                    "TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY", 1505882, "binance", "UP", 0.5,
                    1.0, "SUBMITTED", 1000, "{}", "{}",
                ),
            )
            engine.db.commit()
        markets = engine._target_taker_markets()
        assert 1505882 in markets  # from target-intent-1
        # The imported cohort set is unchanged by the Poly row.
        assert markets[1505882] == {"TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY"}
    finally:
        engine.close()
