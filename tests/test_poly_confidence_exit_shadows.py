from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import predict_bot.cross_oracle_strategy_server as strategy_server


def _create_sim_db(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL,
            market_id INTEGER NOT NULL,
            side TEXT NOT NULL,
            status TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stake REAL NOT NULL,
            shares REAL NOT NULL,
            fees REAL NOT NULL,
            fee_rate_bps INTEGER NOT NULL,
            pnl REAL,
            opened_at TEXT NOT NULL,
            closed_at TEXT
        )
        """
    )
    db.commit()
    return db


def _engine(tmp_path: Path, monkeypatch) -> tuple[strategy_server.ResilientCrossOraclePaperEngine, sqlite3.Connection]:
    sim_path = tmp_path / "simulation.db"
    writer = _create_sim_db(sim_path)
    monkeypatch.setattr(strategy_server, "SIM_DB_PATH", sim_path)
    engine = strategy_server.ResilientCrossOraclePaperEngine(
        tmp_path / "cross_oracle.db",
        lambda: {},
    )
    return engine, writer


def _insert_source(
    writer: sqlite3.Connection,
    *,
    strategy: str = "R_MICROPRICE",
    market_id: int = 123,
    side: str = "UP",
    entry_price: float = 0.40,
    stake: float = 5.0,
    shares: float = 12.5,
    fees: float = 0.04,
) -> tuple[int, int]:
    opened_ms = int(time.time() * 1000)
    opened_at = datetime.fromtimestamp(opened_ms / 1000, timezone.utc).isoformat()
    cursor = writer.execute(
        """INSERT INTO trades(
               strategy, market_id, side, status, entry_price, stake, shares,
               fees, fee_rate_bps, pnl, opened_at, closed_at
           ) VALUES (?, ?, ?, 'OPEN', ?, ?, ?, ?, 200, NULL, ?, NULL)""",
        (strategy, market_id, side, entry_price, stake, shares, fees, opened_at),
    )
    writer.commit()
    return int(cursor.lastrowid), opened_ms


def test_confidence_sources_cover_requested_primary_strategies() -> None:
    assert set(strategy_server.CONFIDENCE_SOURCES) == {
        "R_CALIBRATED_VALUE",
        "R_MICROPRICE",
        "R_MICROPRICE_CONFIRM",
        "R_FUTURES_LEAD",
        "R_OFI",
    }


def test_poly_flip_after_source_entry_exits_at_binance_bid_and_keeps_no_exit_counterfactual(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, writer = _engine(tmp_path, monkeypatch)
    try:
        source_id, opened_ms = _insert_source(writer)
        engine._sync_source_trades(opened_ms + 100)
        runtime = {
            "aligned": True,
            "binanceMarketId": 123,
            "polyMarketSlug": "btc-updown-5m-test",
            "polyDirection": "UP",
            "polyUpMid": 0.70,
        }
        engine._activate_pending_sources(runtime, opened_ms + 150)
        row = engine.db.execute(
            "SELECT * FROM poly_confidence_shadows WHERE source_trade_id=?",
            (source_id,),
        ).fetchone()
        assert row is not None
        assert row["status"] == "ARMED"

        flip_ms = opened_ms + 250
        engine._arm_confidence_exits_from_flip(
            {
                "atMs": flip_ms,
                "from": "UP",
                "to": "DOWN",
                "polyUpMid": 0.20,
                "binanceUpMid": 0.48,
                "binanceMarketId": 123,
            },
            {"binanceMarketId": 123},
        )
        row = engine.db.execute(
            "SELECT * FROM poly_confidence_shadows WHERE source_trade_id=?",
            (source_id,),
        ).fetchone()
        assert row["status"] == "EXIT_TRIGGERED"

        latest = {
            "market_id": 123,
            "up_bid": 0.30,
            "up_ask": 0.32,
            "down_bid": 0.67,
            "down_ask": 0.69,
            "book_age_ms": 100,
            "book_skew_ms": 10,
        }
        engine._retry_confidence_exits(latest, flip_ms + 20)
        row = engine.db.execute(
            "SELECT * FROM poly_confidence_shadows WHERE source_trade_id=?",
            (source_id,),
        ).fetchone()
        assert row["status"] == "EXITED"
        assert float(row["exit_price"]) == 0.30
        assert row["shadow_exit_pnl_usdt"] is not None

        writer.execute(
            "UPDATE trades SET status='SETTLED_LOSS', pnl=-5.0, closed_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), source_id),
        )
        writer.commit()
        engine._finalize_confidence_rows(flip_ms + 500)
        row = engine.db.execute(
            "SELECT * FROM poly_confidence_shadows WHERE source_trade_id=?",
            (source_id,),
        ).fetchone()
        assert row["status"] == "FINALIZED_EXIT"
        assert float(row["counterfactual_no_exit_pnl_usdt"]) == -5.0
        expected_net = float(row["shadow_exit_pnl_usdt"]) + 5.0
        assert abs(float(row["net_protection_usdt"]) - expected_net) < 1e-9
        assert float(row["avoided_loss_usdt"]) > 0
        assert float(row["sacrificed_profit_usdt"]) == 0
    finally:
        engine.stop()
        writer.close()


def test_flip_before_source_entry_is_not_a_causal_exit(tmp_path: Path, monkeypatch) -> None:
    engine, writer = _engine(tmp_path, monkeypatch)
    try:
        source_id, opened_ms = _insert_source(writer, strategy="R_OFI")
        engine._sync_source_trades(opened_ms + 100)
        engine._activate_pending_sources(
            {
                "aligned": True,
                "binanceMarketId": 123,
                "polyMarketSlug": "btc-updown-5m-test",
                "polyDirection": "UP",
                "polyUpMid": 0.70,
            },
            opened_ms + 150,
        )
        engine._arm_confidence_exits_from_flip(
            {
                "atMs": opened_ms - 1,
                "from": "UP",
                "to": "DOWN",
                "polyUpMid": 0.20,
                "binanceUpMid": 0.48,
                "binanceMarketId": 123,
            },
            {"binanceMarketId": 123},
        )
        row = engine.db.execute(
            "SELECT * FROM poly_confidence_shadows WHERE source_trade_id=?",
            (source_id,),
        ).fetchone()
        assert row is not None
        assert row["status"] == "ARMED"
        assert row["triggered_at_ms"] is None
    finally:
        engine.stop()
        writer.close()
