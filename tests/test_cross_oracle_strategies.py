from __future__ import annotations

from pathlib import Path

from predict_bot.cross_oracle_strategies import (
    CrossOraclePaperEngine,
    STRATEGY_POLY_GAP_SCALP,
    STRATEGY_POLY_LEAD_ENTRY,
    probability_direction,
    probability_mid,
)


def _engine(tmp_path: Path) -> CrossOraclePaperEngine:
    return CrossOraclePaperEngine(tmp_path / "cross_oracle_test.db", lambda: {})


def test_probability_direction_uses_deadband() -> None:
    assert probability_mid(0.58, 0.62) == 0.60
    assert probability_direction(0.56) == "UP"
    assert probability_direction(0.44) == "DOWN"
    assert probability_direction(0.50) is None


def test_paper_exit_uses_binance_bid_not_mid(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    latest = {
        "up_ask": 0.40,
        "up_bid": 0.31,
        "down_ask": 0.70,
        "down_bid": 0.60,
    }
    assert engine._open_trade(
        strategy=STRATEGY_POLY_LEAD_ENTRY,
        latest=latest,
        binance_market_id=123,
        poly_slug="btc-updown-5m-test",
        side="UP",
        poly_up_mid=0.70,
        binance_up_mid=0.45,
        now_ms=1_000,
        reason="TEST",
    )
    trade = engine._open_trade_for_market(STRATEGY_POLY_LEAD_ENTRY, 123)
    assert trade is not None
    assert float(trade["entry_price"]) == 0.40

    assert engine._exit_trade_at_bid(trade, latest, 2_000, "TEST_FLIP")
    row = engine.db.execute(
        "SELECT * FROM cross_oracle_strategy_trades WHERE id=?", (int(trade["id"]),)
    ).fetchone()
    assert row is not None
    assert row["status"] == "EXITED"
    assert float(row["exit_price"]) == 0.31
    expected = float(row["shares"]) * 0.31 - float(row["stake_usdt"])
    assert abs(float(row["gross_pnl_usdt"]) - expected) < 1e-12
    engine.stop()


def test_gap_scalp_can_reenter_same_market_after_poly_flip(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    market_id = 456
    slug = "btc-updown-5m-test"
    latest_up = {
        "up_ask": 0.40,
        "up_bid": 0.38,
        "down_ask": 0.63,
        "down_bid": 0.60,
    }
    engine._maybe_open_gap_scalp(
        latest=latest_up,
        binance_market_id=market_id,
        poly_slug=slug,
        poly_direction="UP",
        poly_up_mid=0.70,
        binance_up_mid=0.39,
        now_ms=1_000,
    )
    first = engine._open_trade_for_market(STRATEGY_POLY_GAP_SCALP, market_id)
    assert first is not None
    assert first["side"] == "UP"

    engine._handle_flip(
        latest=latest_up,
        binance_market_id=market_id,
        poly_slug=slug,
        new_direction="DOWN",
        poly_up_mid=0.20,
        binance_up_mid=0.55,
        binance_direction="UP",
        now_ms=2_000,
    )
    assert engine._open_trade_for_market(STRATEGY_POLY_GAP_SCALP, market_id) is None

    latest_down = {
        "up_ask": 0.68,
        "up_bid": 0.64,
        "down_ask": 0.35,
        "down_bid": 0.32,
    }
    engine._maybe_open_gap_scalp(
        latest=latest_down,
        binance_market_id=market_id,
        poly_slug=slug,
        poly_direction="DOWN",
        poly_up_mid=0.20,
        binance_up_mid=0.66,
        now_ms=2_250,
    )
    second = engine._open_trade_for_market(STRATEGY_POLY_GAP_SCALP, market_id)
    assert second is not None
    assert second["side"] == "DOWN"
    count = engine.db.execute(
        "SELECT COUNT(*) FROM cross_oracle_strategy_trades WHERE strategy=? AND binance_market_id=?",
        (STRATEGY_POLY_GAP_SCALP, market_id),
    ).fetchone()[0]
    assert int(count) == 2
    engine.stop()
