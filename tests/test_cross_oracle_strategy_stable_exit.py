from __future__ import annotations

from pathlib import Path

from predict_bot.cross_oracle_strategy_stable_exit import (
    STABLE_EXIT_CONFIRM_MS,
    STABLE_EXIT_CONFIRM_SAMPLES,
    STRATEGY_POLY_GAP_SCALP_STABLE,
    StableExitComparisonPaperEngine,
)
from predict_bot.cross_oracle_strategies import STRATEGY_POLY_GAP_SCALP


def engine(tmp_path: Path) -> StableExitComparisonPaperEngine:
    return StableExitComparisonPaperEngine(tmp_path / "stable-paper.db", lambda: {})


def test_stable_variant_opens_beside_original_with_same_entry_snapshot(tmp_path: Path) -> None:
    e = engine(tmp_path)
    try:
        latest = {
            "up_ask": 0.40,
            "up_bid": 0.38,
            "down_ask": 0.63,
            "down_bid": 0.60,
        }
        e._stable_last_poly_receipt_ms = 1_000
        e._maybe_open_gap_scalp(
            latest=latest,
            binance_market_id=456,
            poly_slug="btc-updown-5m-test",
            poly_direction="UP",
            poly_up_mid=0.70,
            binance_up_mid=0.39,
            now_ms=1_000,
        )
        original = e._open_trade_for_market(STRATEGY_POLY_GAP_SCALP, 456)
        stable = e._open_trade_for_market(STRATEGY_POLY_GAP_SCALP_STABLE, 456)
        assert original is not None
        assert stable is not None
        assert float(original["entry_price"]) == float(stable["entry_price"]) == 0.40
        assert int(original["opened_at_ms"]) == int(stable["opened_at_ms"]) == 1_000
    finally:
        e.stop()


def test_stable_exit_waits_for_three_distinct_receipts_and_500ms(tmp_path: Path) -> None:
    e = engine(tmp_path)
    try:
        market_id = 789
        slug = "btc-updown-5m-stable"
        entry_latest = {
            "up_ask": 0.40,
            "up_bid": 0.38,
            "down_ask": 0.63,
            "down_bid": 0.60,
        }
        e._stable_last_poly_receipt_ms = 1_000
        e._maybe_open_gap_scalp(
            latest=entry_latest,
            binance_market_id=market_id,
            poly_slug=slug,
            poly_direction="UP",
            poly_up_mid=0.70,
            binance_up_mid=0.39,
            now_ms=1_000,
        )
        stable = e._open_trade_for_market(STRATEGY_POLY_GAP_SCALP_STABLE, market_id)
        assert stable is not None

        flip_latest = {
            "up_ask": 0.62,
            "up_bid": 0.30,
            "down_ask": 0.62,
            "down_bid": 0.58,
        }
        e._stable_last_poly_receipt_ms = 2_000
        e._maybe_open_gap_scalp(
            latest=flip_latest,
            binance_market_id=market_id,
            poly_slug=slug,
            poly_direction="DOWN",
            poly_up_mid=0.40,
            binance_up_mid=0.50,
            now_ms=2_000,
        )
        assert e._open_trade_for_market(STRATEGY_POLY_GAP_SCALP_STABLE, market_id) is not None

        # Same receipt is just a local re-read and must not advance confirmation.
        e._stable_last_poly_receipt_ms = 2_000
        e._maybe_open_gap_scalp(
            latest=flip_latest,
            binance_market_id=market_id,
            poly_slug=slug,
            poly_direction="DOWN",
            poly_up_mid=0.40,
            binance_up_mid=0.50,
            now_ms=2_300,
        )
        assert e.stable_exit_candidate is not None
        assert e.stable_exit_candidate["samples"] == 1

        e._stable_last_poly_receipt_ms = 2_250
        e._maybe_open_gap_scalp(
            latest=flip_latest,
            binance_market_id=market_id,
            poly_slug=slug,
            poly_direction="DOWN",
            poly_up_mid=0.40,
            binance_up_mid=0.50,
            now_ms=2_300,
        )
        assert e.stable_exit_candidate is not None
        assert e.stable_exit_candidate["samples"] == 2

        e._stable_last_poly_receipt_ms = 2_600
        e._maybe_open_gap_scalp(
            latest=flip_latest,
            binance_market_id=market_id,
            poly_slug=slug,
            poly_direction="DOWN",
            poly_up_mid=0.40,
            binance_up_mid=0.50,
            now_ms=2_600,
        )
        with e.db_lock:
            row = e.db.execute(
                """SELECT * FROM cross_oracle_strategy_trades
                    WHERE strategy=? AND binance_market_id=?
                    ORDER BY id ASC LIMIT 1""",
                (STRATEGY_POLY_GAP_SCALP_STABLE, market_id),
            ).fetchone()
        assert row is not None
        assert row["status"] == "EXITED"
        assert row["exit_reason"] == "POLY_DIRECTION_FLIP_STABLE_CONFIRMED"
        assert e.stable_exit_confirmed_count == 1
    finally:
        e.stop()


def test_snapshot_exposes_original_and_stable_ab(tmp_path: Path) -> None:
    e = engine(tmp_path)
    try:
        state = e.snapshot()
        assert STRATEGY_POLY_GAP_SCALP in state["summaries"]
        assert STRATEGY_POLY_GAP_SCALP_STABLE in state["summaries"]
        comparison = state["stableExitComparison"]
        assert comparison["originalStrategyUnchanged"] is True
        assert comparison["normalConfirmMs"] == STABLE_EXIT_CONFIRM_MS
        assert comparison["normalConfirmDistinctSamples"] == STABLE_EXIT_CONFIRM_SAMPLES
        assert comparison["requiresDistinctPolyReceiptTimestamps"] is True
        assert comparison["signedQuoteCanaryEnabledForVariant"] is False
    finally:
        e.stop()
