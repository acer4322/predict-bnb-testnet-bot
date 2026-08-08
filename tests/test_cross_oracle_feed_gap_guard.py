from __future__ import annotations

import time
from pathlib import Path

import pytest

import predict_bot.cross_oracle as cross_oracle_base
import predict_bot.cross_oracle_resilient as cross_oracle_resilient
import predict_bot.cross_oracle_strategy_server as strategy_server
from predict_bot.cross_oracle_resilient import ResilientCrossOracleCollector
from predict_bot.cross_oracle_strategies import (
    STRATEGY_POLY_GAP_SCALP,
    STRATEGY_POLY_LEAD_ENTRY,
    STRATEGY_POLY_LEAD_EXIT,
)
from predict_bot.cross_oracle_strategy_resilient import GapAwareCrossOraclePaperEngine


def test_collector_continuity_generation_recovers_only_after_fresh_quote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "cross_oracle.db"
    monkeypatch.setattr(cross_oracle_base, "DB_PATH", db_path)
    monkeypatch.setattr(cross_oracle_resilient, "DB_PATH", db_path)
    collector = ResilientCrossOracleCollector()
    try:
        initial_generation = collector.gap_generation
        assert collector.gap_active is True
        slug, bucket = cross_oracle_base.current_btc_5m_slug()
        now_ms = int(time.time() * 1000)
        with collector.lock:
            collector.market = {
                "slug": slug,
                "windowStartMs": bucket * 1000,
                "windowEndMs": (bucket + 300) * 1000,
            }
            collector.polymarket["status"] = "LIVE"
            collector.polymarket["receivedTimestampMs"] = now_ms
        state = collector.snapshot()
        assert state["continuity"]["healthy"] is True
        assert state["continuity"]["gapActive"] is False
        assert state["continuity"]["gapGeneration"] == initial_generation

        collector._open_gap("TEST_GAP", "simulated sleep", slug)
        assert collector.gap_generation == initial_generation + 1
        assert collector.gap_active is True
        row = collector.db.execute(
            "SELECT * FROM cross_oracle_feed_gaps WHERE gap_generation=?",
            (collector.gap_generation,),
        ).fetchone()
        assert row is not None
        assert row["reason"] == "TEST_GAP"
    finally:
        collector.stop()


def _insert_cross_trade(engine: GapAwareCrossOraclePaperEngine, strategy: str, slug: str) -> None:
    with engine.db_lock:
        engine.db.execute(
            """INSERT INTO cross_oracle_strategy_trades(
                   strategy, binance_market_id, poly_market_slug, side, status,
                   entry_price, stake_usdt, shares, opened_at_ms,
                   entry_reason, metadata_json
               ) VALUES (?, 123, ?, 'UP', 'OPEN', .40, 1, 2.5, 1000, 'TEST', '{}')""",
            (strategy, slug),
        )
        engine.db.commit()


def test_gap_invalidates_only_flip_dependent_cross_market_positions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(strategy_server, "SIM_DB_PATH", tmp_path / "missing-simulation.db")
    engine = GapAwareCrossOraclePaperEngine(tmp_path / "cross_oracle.db", lambda: {})
    slug = "btc-updown-5m-test"
    try:
        _insert_cross_trade(engine, STRATEGY_POLY_LEAD_ENTRY, slug)
        _insert_cross_trade(engine, STRATEGY_POLY_LEAD_EXIT, slug)
        _insert_cross_trade(engine, STRATEGY_POLY_GAP_SCALP, slug)

        with pytest.raises(RuntimeError, match="feed gap active"):
            engine.observe_continuity(
                {
                    "gapGeneration": 9,
                    "gapActive": True,
                    "gapReason": "POLYMARKET_FEED_STALE",
                    "marketSlug": slug,
                    "targetMarketSlug": slug,
                }
            )

        rows = {
            row["strategy"]: row["status"]
            for row in engine.db.execute(
                "SELECT strategy, status FROM cross_oracle_strategy_trades"
            ).fetchall()
        }
        assert rows[STRATEGY_POLY_LEAD_ENTRY] == "OPEN"
        assert rows[STRATEGY_POLY_LEAD_EXIT] == "NOT_EVALUABLE_FEED_GAP"
        assert rows[STRATEGY_POLY_GAP_SCALP] == "NOT_EVALUABLE_FEED_GAP"
    finally:
        engine.stop()


def test_recovery_establishes_new_baseline_instead_of_counting_flip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(strategy_server, "SIM_DB_PATH", tmp_path / "missing-simulation.db")
    engine = GapAwareCrossOraclePaperEngine(tmp_path / "cross_oracle.db", lambda: {})
    slug = "btc-updown-5m-test"
    try:
        engine.last_confident_poly_direction[slug] = "UP"
        engine.last_flip = {"from": "DOWN", "to": "UP", "atMs": 10}
        with pytest.raises(RuntimeError, match="feed gap active"):
            engine.observe_continuity(
                {
                    "gapGeneration": 3,
                    "gapActive": True,
                    "gapReason": "MARKET_DISCOVERY_FAILED",
                    "marketSlug": slug,
                    "targetMarketSlug": slug,
                }
            )
        assert engine.last_confident_poly_direction == {}
        assert engine.last_flip is None

        with pytest.raises(RuntimeError, match="establishing a fresh direction baseline"):
            engine.observe_continuity(
                {
                    "gapGeneration": 3,
                    "gapActive": False,
                    "marketSlug": slug,
                    "targetMarketSlug": slug,
                    "lastGapDurationMs": 4200,
                }
            )
        # The next healthy poll is allowed, but there is still no synthetic flip.
        engine.observe_continuity(
            {
                "gapGeneration": 3,
                "gapActive": False,
                "marketSlug": slug,
                "targetMarketSlug": slug,
            }
        )
        assert engine.last_confident_poly_direction == {}
        assert engine.last_flip is None
        assert engine.feed_gap_resyncs == 1
    finally:
        engine.stop()
