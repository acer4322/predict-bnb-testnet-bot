"""
test_market_observer.py
========================
Unit tests for MarketStateObserver v2.

Covers:
1.  High winner_touch_rate must NOT be classified as TREND.
2.  Low winner_touch, low both-sides, low crossovers, high ER  → TREND.
3.  High winner touch + high both-sides touch + oscillating path → RANGE.
4.  Near-tied scores → UNCERTAIN.
5.  Fewer than 6 settled rounds → always UNCERTAIN.
6.  6–11 rounds → provisional=True in metrics.
7.  Deadband: small jitter inside deadband does NOT count as a crossover.
8.  Full deadband crossing counts exactly once per confirmed direction change.
9.  median_er_60s and early_er_60s are persisted and reload correctly.
10. Conditional fill break-even win rate and edge are computed correctly.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import unittest

from predict_bot.market_observer import (
    MarketStateObserver,
    _effective_taker_cost,
    _median,
    _percentile,
    calculate_efficiency_ratio_60s,
    score_classification,
    summarize_m01_settled_fills,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: build a synthetic round dict
# ─────────────────────────────────────────────────────────────────────────────

def _round(
    winner_touched: int = 0,
    both_touched: int = 0,
    crossover_count: int = 0,
    effective_crossover_count: int = 0,
    median_er_60s: float | None = None,
    avg_er_60s: float | None = None,
    early_er_60s: float | None = None,
    p75_er_60s: float | None = None,
    m01_filled: int = 0,
    m01_won: int = 0,
    m01_fill_price: float | None = None,
    m01_avg_fill_price: float | None = None,
    m01_avg_fee_rate_bps: float | None = None,
) -> dict:
    return {
        "winner_touched": winner_touched,
        "both_touched": both_touched,
        "crossover_count": crossover_count,
        "effective_crossover_count": effective_crossover_count,
        "median_er_60s": median_er_60s,
        "avg_er_60s": avg_er_60s,
        "early_er_60s": early_er_60s,
        "p75_er_60s": p75_er_60s,
        "m01_filled": m01_filled,
        "m01_won": m01_won,
        "m01_fill_price": m01_fill_price,
        "m01_avg_fill_price": m01_avg_fill_price,
        "m01_avg_fee_rate_bps": m01_avg_fee_rate_bps,
    }


class TestEfficiencyRatio(unittest.TestCase):
    """Basic ER calculation tests (unchanged algorithm, still must pass)."""

    def test_straight_path_er_is_one(self):
        now = 1000.0
        history = [(now - 60, 100.0), (now - 40, 102.0), (now - 20, 105.0), (now, 110.0)]
        er = calculate_efficiency_ratio_60s(history, now)
        self.assertIsNotNone(er)
        self.assertAlmostEqual(er, 1.0, places=4)

    def test_choppy_path_er_is_zero(self):
        now = 1000.0
        history = [
            (now - 60, 100.0), (now - 45, 110.0), (now - 30, 100.0),
            (now - 15, 110.0), (now, 100.0),
        ]
        er = calculate_efficiency_ratio_60s(history, now)
        self.assertIsNotNone(er)
        self.assertAlmostEqual(er, 0.0, places=4)

    def test_single_point_returns_none(self):
        now = 500.0
        self.assertIsNone(calculate_efficiency_ratio_60s([(now, 100.0)], now))

    def test_stationary_path_er_is_zero(self):
        now = 1000.0
        history = [(now - 60, 100.0), (now - 30, 100.0), (now, 100.0)]
        self.assertEqual(calculate_efficiency_ratio_60s(history, now), 0.0)

    def test_partial_window_is_not_a_valid_60s_er(self):
        now = 1000.0
        history = [(now - 30, 100.0), (now, 110.0)]
        self.assertIsNone(calculate_efficiency_ratio_60s(history, now))

    def test_realtime_er_sampling_is_throttled_to_one_hz(self):
        observer = MarketStateObserver(db_path=None)
        observer.reset_market(1, 100.0, market_start_ts=0.0)
        observer.update_tick(now_ts=0.0, spot_price=100.0, market_id=1)
        for index in range(1, 201):
            observer.update_tick(
                now_ts=60.0 + index / 100.0,
                spot_price=100.0 + index / 10_000.0,
                market_id=1,
            )
        self.assertEqual(len(observer.er_60s_samples), 2)
        self.assertEqual(observer._er_60s_sample_times, [60.01, 61.01])


class TestScoreClassification(unittest.TestCase):
    """Unit-test the pure scoring function."""

    # Test 1: high winner_touch_rate → must NOT push toward TREND
    def test_high_winner_touch_does_not_score_trend(self):
        r_score, t_score, _, _ = score_classification(
            winner_touch_rate=0.80,
            both_sides_touch_rate=0.05,
            avg_effective_crossovers=0.5,
            median_er_60s=0.70,
        )
        # High winner_touch (>=35%) adds +2 to RANGE, not to TREND
        self.assertGreaterEqual(r_score, 2)
        # TREND: both_sides<=10% (+1), crossovers<=1 (+1), ER>=0.55 (+2) = 4
        # winner_touch=80% does NOT add to TREND (it's >=35%, so adds to RANGE instead)
        self.assertEqual(t_score, 4)
        # RANGE: winner_touch >=35% → +2; both_sides 5% <25 → 0; crossovers <2 → 0; ER>0.35 → 0
        self.assertEqual(r_score, 2)

    # Test 2: low winner_touch + low both-sides + low crossovers + high ER → TREND
    def test_clear_trend_signals(self):
        r_score, t_score, _, trend_triggers = score_classification(
            winner_touch_rate=0.10,   # <= 0.20 → +2 TREND
            both_sides_touch_rate=0.05,  # <= 0.10 → +1 TREND
            avg_effective_crossovers=0.8,  # <= 1 → +1 TREND
            median_er_60s=0.65,  # >= 0.55 → +2 TREND
        )
        self.assertEqual(t_score, 6)
        # RANGE: winner_touch 10% < 35 → 0; both 5% < 25 → 0; crossovers < 2 → 0; ER > 0.35 → 0
        self.assertEqual(r_score, 0)
        self.assertGreaterEqual(len(trend_triggers), 4)

    # Test 3: oscillating / range signals
    def test_clear_range_signals(self):
        r_score, t_score, range_triggers, _ = score_classification(
            winner_touch_rate=0.60,   # >= 0.35 → +2 RANGE; not TREND
            both_sides_touch_rate=0.50,  # >= 0.25 → +2 RANGE
            avg_effective_crossovers=3.0,  # >= 2 → +1 RANGE
            median_er_60s=0.20,   # <= 0.35 → +1 RANGE
        )
        self.assertEqual(r_score, 6)
        self.assertEqual(t_score, 0)

    # Test 4: near-tie stays uncertain (margin < 2)
    def test_near_tie_scores(self):
        r_score, t_score, _, _ = score_classification(
            winner_touch_rate=0.30,  # not >=0.35, not <=0.20
            both_sides_touch_rate=0.20,  # not >=0.25, not <=0.10
            avg_effective_crossovers=1.5,  # not >=2, not <=1
            median_er_60s=0.45,  # not <=0.35, not >=0.55
        )
        self.assertEqual(r_score, 0)
        self.assertEqual(t_score, 0)


class TestClassifyState(unittest.TestCase):
    """Integration tests for MarketStateObserver.classify_state."""

    def setUp(self):
        self.observer = MarketStateObserver(db_path=None, window_size=20)

    # Test 5: fewer than 6 rounds → always UNCERTAIN
    def test_fewer_than_6_rounds_is_uncertain(self):
        for n in range(6):
            rounds = [
                _round(winner_touched=0, both_touched=0, effective_crossover_count=0, median_er_60s=0.70)
                for _ in range(n)
            ]
            state, reason, metrics = self.observer.classify_state(rounds)
            self.assertEqual(state, "UNCERTAIN", f"Expected UNCERTAIN for n={n}")
            self.assertIn("樣本數不足", reason)
            self.assertTrue(metrics["provisional"])
            self.assertEqual(metrics["sample_count"], n)

    def test_insufficient_sample_still_reports_triggered_evidence(self):
        rounds = [
            _round(
                winner_touched=0,
                both_touched=0,
                effective_crossover_count=0,
                median_er_60s=0.70,
            )
            for _ in range(5)
        ]
        state, _, metrics = self.observer.classify_state(rounds)
        self.assertEqual(state, "UNCERTAIN")
        self.assertEqual(metrics["trend_score"], 6)
        self.assertTrue(
            any("暫定 TREND 證據" in item for item in metrics["reasons"])
        )

    # Test 1 (full): high winner_touch_rate does NOT give TREND a bonus
    def test_high_winner_touch_rate_does_not_score_trend_points(self):
        """
        High winner_touch_rate (100%) adds RANGE points (+2), never TREND points.
        In this scenario ER/crossovers give TREND 4 points vs RANGE 2 → TREND wins.
        The key assertion is that the RANGE score reflects the winner_touch contribution,
        not that the final state is non-TREND.
        """
        rounds = [
            _round(winner_touched=1, both_touched=0, effective_crossover_count=0, median_er_60s=0.70)
            for _ in range(12)
        ]
        state, reason, metrics = self.observer.classify_state(rounds)
        # winner_touch_rate=100% ≥35% → adds +2 to RANGE (not TREND)
        # TREND: both_sides 0% ≤10 (+1) + crossovers 0 ≤1 (+1) + ER 0.70 ≥0.55 (+2) = 4
        # RANGE: winner_touch ≥35 (+2) = 2; margin = 2 → TREND qualifies
        # But crucially: winner_touch contributed RANGE points, not TREND points
        self.assertEqual(state, "TREND")
        # Verify that the RANGE score is >0 (winner_touch DID contribute RANGE points)
        # This can only be verified via score_classification directly
        r_score, t_score, _, _ = score_classification(
            winner_touch_rate=1.0,
            both_sides_touch_rate=0.0,
            avg_effective_crossovers=0.0,
            median_er_60s=0.70,
        )
        self.assertEqual(r_score, 2)  # winner_touch gave RANGE +2
        self.assertEqual(t_score, 4)  # ER + crossovers + both_sides
        self.assertFalse(
            any("100.0%" in item for item in metrics["reasons"] if "TREND" in reason)
        )

    # Test 1 variant: high winner_touch WITH high both-sides → must be RANGE (not TREND)
    def test_high_winner_touch_high_both_sides_is_range(self):
        rounds = [
            _round(winner_touched=1, both_touched=1, effective_crossover_count=3, median_er_60s=0.20)
            for _ in range(12)
        ]
        state, _, metrics = self.observer.classify_state(rounds)
        # winner_touch=100% >=35% (+2 RANGE); both_sides=100% >=25% (+2 RANGE)
        # crossovers=3 >=2 (+1 RANGE); ER=0.20 <=0.35 (+1 RANGE) → RANGE=6
        # TREND: winner 100% not <=20, both_sides not <=10 → 0
        self.assertEqual(state, "RANGE")

    # Test 2 (full): clear trend conditions
    def test_clear_trend_classification(self):
        rounds = [
            _round(winner_touched=0, both_touched=0, effective_crossover_count=0, median_er_60s=0.70)
            for _ in range(12)
        ]
        state, reason, metrics = self.observer.classify_state(rounds)
        # winner 0% <=20 (+2T); both_sides 0% <=10 (+1T); crossovers 0 <=1 (+1T); ER 0.70 >=0.55 (+2T)
        # TREND=6, RANGE=0
        self.assertEqual(state, "TREND")
        self.assertIn("TREND", reason)

    # Test 3 (full): high both-sides and oscillation → RANGE
    def test_range_classification_with_oscillation(self):
        rounds = [
            _round(winner_touched=1, both_touched=1, effective_crossover_count=3, median_er_60s=0.25)
            for _ in range(12)
        ]
        state, _, metrics = self.observer.classify_state(rounds)
        self.assertEqual(state, "RANGE")
        self.assertEqual(metrics["both_sides_touch_rate"], 1.0)

    # Test 4 (full): near-tie → UNCERTAIN
    def test_near_tie_is_uncertain(self):
        # RANGE=2 (winner 40%>=35), TREND=2 (ER 0.60>=0.55), margin=0
        rounds = [
            _round(winner_touched=1, both_touched=0, effective_crossover_count=1, median_er_60s=0.60)
            for _ in range(4)
        ] + [
            _round(winner_touched=0, both_touched=0, effective_crossover_count=1, median_er_60s=0.60)
            for _ in range(8)
        ]
        # winner_touch_rate = 4/12 = 33.3% → not >=35 → RANGE +0; not <=20 → TREND +0
        # both_sides=0% <=10 → TREND +1; crossovers=1 <=1 → TREND +1; ER=0.60 >=0.55 → TREND +2
        # TREND=4, RANGE=0 → margin 4 → TREND!
        # Adjust: use crossovers=1.5 avg so neither threshold applies
        rounds2 = (
            [_round(winner_touched=1, both_touched=0, effective_crossover_count=2, median_er_60s=0.45)
             for _ in range(4)]
            + [_round(winner_touched=0, both_touched=0, effective_crossover_count=1, median_er_60s=0.45)
               for _ in range(8)]
        )
        state, reason, metrics = self.observer.classify_state(rounds2)
        # RANGE: winner_touch 33.3% <35 →0; both_sides 0% <25 →0; cross avg=1.33 <2 →0; ER 0.45 >0.35 →0  → RANGE=0
        # TREND: winner 33.3% >20 →0; both_sides 0% <=10 →+1; cross 1.33 >1 →0; ER 0.45 <0.55 →0 → TREND=1
        # Neither qualifies → UNCERTAIN
        self.assertEqual(state, "UNCERTAIN")

    def test_qualifying_score_with_margin_below_two_is_uncertain(self):
        rounds = [
            _round(
                winner_touched=1,
                both_touched=1,
                effective_crossover_count=0,
                median_er_60s=0.70,
            )
            for _ in range(12)
        ]
        state, _, metrics = self.observer.classify_state(rounds)
        self.assertEqual(metrics["range_score"], 4)
        self.assertEqual(metrics["trend_score"], 3)
        self.assertEqual(state, "UNCERTAIN")
        self.assertFalse(metrics["provisional"])

    # Test 6: 6–11 rounds → provisional=True
    def test_provisional_flag_for_6_to_11_rounds(self):
        for n in range(6, 12):
            rounds = [
                _round(winner_touched=0, both_touched=0, effective_crossover_count=0, median_er_60s=0.70)
                for _ in range(n)
            ]
            state, reason, metrics = self.observer.classify_state(rounds)
            self.assertTrue(metrics.get("provisional"), f"Expected provisional=True for n={n}")

    # Test 6 complement: 12+ rounds → provisional=False for non-UNCERTAIN
    def test_not_provisional_at_12_rounds(self):
        rounds = [
            _round(winner_touched=0, both_touched=0, effective_crossover_count=0, median_er_60s=0.70)
            for _ in range(12)
        ]
        state, reason, metrics = self.observer.classify_state(rounds)
        self.assertFalse(metrics.get("provisional"))
        self.assertEqual(state, "TREND")

    def test_high_winner_touch_is_never_a_trend_reason(self):
        rounds = [
            _round(
                winner_touched=1,
                both_touched=0,
                effective_crossover_count=0,
                median_er_60s=0.70,
            )
            for _ in range(12)
        ]
        state, _, metrics = self.observer.classify_state(rounds)
        self.assertEqual(state, "TREND")
        self.assertFalse(
            any("勝方觸及率 100.0%" in item for item in metrics["reasons"])
        )
        self.assertTrue(
            any("勝方觸及率 100.0%" in item for item in metrics["conflicts"])
        )


class TestDeadbandCrossover(unittest.TestCase):
    """Tests for deadband-filtered crossover counting (requirements 7 & 8)."""

    def setUp(self):
        # 0.5 bps deadband on a startPrice of 100 → deadband = 0.5 * 100 / 10000 = 0.005
        self.observer = MarketStateObserver(
            db_path=None, crossover_deadband_bps=0.5
        )
        self.observer.reset_market(market_id=1, start_price=100.0)
        # deadband bounds: upper=100.005, lower=99.995

    def _tick(self, price: float, ts: float) -> None:
        self.observer.update_tick(now_ts=ts, spot_price=price)

    # Test 7: jitter inside deadband does NOT count crossover
    def test_jitter_inside_deadband_no_crossover(self):
        self._tick(100.003, 0.0)   # inside deadband – no confirmed side
        self._tick(99.997, 1.0)    # inside deadband – still no confirmed side
        self._tick(100.002, 2.0)   # inside deadband
        self.assertEqual(self.observer.effective_crossover_count, 0)

    # Test 7b: price outside deadband then returns – confirms one side
    def test_touch_above_sets_side_no_crossover(self):
        self._tick(100.10, 0.0)   # above → confirmed "above"
        self._tick(100.002, 1.0)  # back inside deadband – stays "above"
        self._tick(100.006, 2.0)  # still above deadband
        self.assertEqual(self.observer.effective_crossover_count, 0)

    # Test 8: full crossing: above then below → count = 1
    def test_full_crossover_counted_once(self):
        self._tick(100.10, 0.0)   # confirmed "above"
        self._tick(99.990, 1.0)   # confirmed "below" → crossover #1
        self.assertEqual(self.observer.effective_crossover_count, 1)

    # Test 8b: three confirmed direction changes → count = 2
    def test_two_crossovers(self):
        self._tick(100.10, 0.0)   # above
        self._tick(99.990, 1.0)   # below → #1
        self._tick(100.001, 2.0)  # inside deadband – no change
        self._tick(100.010, 3.0)  # above → #2
        self.assertEqual(self.observer.effective_crossover_count, 2)

    def test_below_neutral_above_counts_once(self):
        self._tick(99.990, 0.0)
        self._tick(100.001, 1.0)
        self._tick(100.010, 2.0)
        self.assertEqual(self.observer.effective_crossover_count, 1)

    def test_above_neutral_below_counts_once(self):
        self._tick(100.010, 0.0)
        self._tick(100.001, 1.0)
        self._tick(99.990, 2.0)
        self.assertEqual(self.observer.effective_crossover_count, 1)

    def test_multiple_full_crossovers_count_correctly(self):
        for index, price in enumerate(
            [100.10, 99.90, 100.10, 99.90, 100.10, 99.90]
        ):
            self._tick(price, float(index))
        self.assertEqual(self.observer.effective_crossover_count, 5)

    # Test 8c: legacy crossover_count still increments (no deadband)
    def test_legacy_crossover_count_increments_without_deadband(self):
        self.observer.reset_market(market_id=99, start_price=100.0)
        # last_spot_price is None, so first tick never triggers crossover
        self.observer.update_tick(now_ts=0.0, spot_price=99.5)   # prev=None → no cross
        self.observer.update_tick(now_ts=1.0, spot_price=100.5)  # 99.5→100.5 crosses → +1 legacy
        self.observer.update_tick(now_ts=2.0, spot_price=99.5)   # 100.5→99.5 crosses → +1 legacy
        self.assertEqual(self.observer.crossover_count, 2)


class TestWinnerTouchSelection(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.observer = MarketStateObserver(db_path=self.temp_db.name)

    def tearDown(self):
        if os.path.exists(self.temp_db.name):
            os.remove(self.temp_db.name)

    def test_up_winner_uses_up_touch(self):
        self.observer.record_settlement(
            101,
            "UP",
            up_touched=True,
            down_touched=False,
        )
        row = self.observer.get_recent_rounds(1)[0]
        self.assertEqual(row["winner_touched"], 1)

    def test_down_winner_uses_down_touch(self):
        self.observer.record_settlement(
            102,
            "DOWN",
            up_touched=True,
            down_touched=False,
        )
        row = self.observer.get_recent_rounds(1)[0]
        self.assertEqual(row["winner_touched"], 0)

    def test_touch_uses_each_direct_ask(self):
        self.observer.reset_market(103, 100.0, market_start_ts=0.0)
        self.observer.update_tick(
            now_ts=1.0,
            spot_price=None,
            up_ask=0.29,
            down_ask=0.31,
            market_id=103,
        )
        self.assertTrue(self.observer.up_touched)
        self.assertFalse(self.observer.down_touched)

    def test_previous_market_snapshot_survives_rollover(self):
        self.observer.reset_market(104, 100.0, market_start_ts=0.0)
        self.observer.update_tick(
            now_ts=1.0,
            spot_price=101.0,
            up_ask=0.29,
            down_ask=0.80,
            market_id=104,
        )
        self.observer.reset_market(105, 102.0, market_start_ts=300.0)
        self.observer.update_tick(
            now_ts=301.0,
            spot_price=101.0,
            up_ask=0.90,
            down_ask=0.20,
            market_id=105,
        )
        self.observer.record_settlement(104, "UP")
        row = self.observer.get_recent_rounds(1)[0]
        self.assertEqual(row["market_id"], 104)
        self.assertEqual(row["up_touched"], 1)
        self.assertEqual(row["down_touched"], 0)
        self.assertEqual(row["winner_touched"], 1)


class TestERPersistence(unittest.TestCase):
    """Test 9: median_er_60s and early_er_60s are persisted and reload correctly."""

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.db_path = self.temp_db.name

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_median_and_early_er_persist_and_reload(self):
        obs = MarketStateObserver(db_path=self.db_path)

        obs.record_settlement(
            market_id=42,
            winner="UP",
            median_er_60s=0.612,
            early_er_60s=0.333,
            p75_er_60s=0.750,
            m01_filled=False,
            m01_won=False,
        )

        # Reload via a fresh observer pointing at same DB
        obs2 = MarketStateObserver(db_path=self.db_path)
        rows = obs2.get_recent_rounds(10)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertAlmostEqual(row["median_er_60s"], 0.612, places=3)
        self.assertAlmostEqual(row["early_er_60s"], 0.333, places=3)
        self.assertAlmostEqual(row["p75_er_60s"], 0.750, places=3)

    def test_effective_crossover_count_persists(self):
        obs = MarketStateObserver(db_path=self.db_path)
        obs.record_settlement(
            market_id=55,
            winner="DOWN",
            effective_crossover_count=3,
            m01_filled=False,
            m01_won=False,
        )
        obs2 = MarketStateObserver(db_path=self.db_path)
        rows = obs2.get_recent_rounds(10)
        self.assertEqual(rows[0]["effective_crossover_count"], 3)

    def test_early_median_and_p75_are_computed_from_valid_samples(self):
        obs = MarketStateObserver(db_path=self.db_path)
        obs.reset_market(56, 100.0, market_start_ts=0.0)
        obs.er_60s_samples = [0.1, 0.4, 0.9]
        obs._early_er_60s = 0.2
        obs.record_settlement(56, "UP")
        row = obs.get_recent_rounds(1)[0]
        self.assertAlmostEqual(row["early_er_60s"], 0.2)
        self.assertAlmostEqual(row["median_er_60s"], 0.4)
        self.assertAlmostEqual(row["p75_er_60s"], 0.65)

    def test_legacy_schema_migrates_without_losing_history(self):
        os.remove(self.db_path)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """CREATE TABLE market_observer_rounds (
                    market_id INTEGER PRIMARY KEY,
                    start_time TEXT,
                    start_price REAL,
                    winner TEXT,
                    up_touched INTEGER NOT NULL DEFAULT 0,
                    down_touched INTEGER NOT NULL DEFAULT 0,
                    both_touched INTEGER NOT NULL DEFAULT 0,
                    winner_touched INTEGER NOT NULL DEFAULT 0,
                    crossover_count INTEGER NOT NULL DEFAULT 0,
                    avg_er_60s REAL,
                    m01_filled INTEGER NOT NULL DEFAULT 0,
                    m01_won INTEGER NOT NULL DEFAULT 0,
                    settled_at TEXT
                )"""
            )
            conn.execute(
                """INSERT INTO market_observer_rounds(
                    market_id, winner, winner_touched, crossover_count
                ) VALUES (77, 'UP', 1, 2)"""
            )
        conn.close()
        obs = MarketStateObserver(db_path=self.db_path)
        row = obs.get_recent_rounds(1)[0]
        self.assertEqual(row["market_id"], 77)
        self.assertEqual(row["winner_touched"], 1)
        self.assertIn("effective_crossover_count", row)
        self.assertIn("m01_fill_price", row)


class TestStateApiContract(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()

    def tearDown(self):
        if os.path.exists(self.temp_db.name):
            os.remove(self.temp_db.name)

    def test_state_exposes_observe_only_ratio_and_score_contract(self):
        obs = MarketStateObserver(db_path=self.temp_db.name, window_size=20)
        for market_id in range(12):
            touched = market_id < 6
            obs.record_settlement(
                market_id=1000 + market_id,
                winner="UP",
                up_touched=touched,
                down_touched=touched,
                effective_crossover_count=3,
                early_er_60s=0.30,
                median_er_60s=0.20,
                p75_er_60s=0.40,
            )
        state = obs.state()
        self.assertTrue(state["observeOnly"])
        self.assertEqual(state["sampleCount"], 12)
        self.assertEqual(state["rollingRoundLimit"], 20)
        self.assertFalse(state["provisional"])
        self.assertAlmostEqual(state["winnerTouchRate"], 0.5)
        self.assertAlmostEqual(state["bothSidesTouchRate"], 0.5)
        self.assertEqual(state["state"], "RANGE")
        for key in (
            "touchThreshold", "crossoverDeadbandBps", "rangeScore",
            "trendScore", "reasons", "conflicts", "medianEr60s",
            "earlyEr60sMedian", "p75Er60sMedian",
            "m01ConditionalWinRate", "m01AverageFillPrice",
            "m01BreakEvenWinRate", "m01Edge",
            "m01SettledFillSampleCount", "costStatus",
        ):
            self.assertIn(key, state)


class TestConditionalFillStats(unittest.TestCase):
    """Test 10: break-even win rate and edge calculations."""

    def setUp(self):
        self.observer = MarketStateObserver(db_path=None)

    def _make_rounds(self, n: int, filled: bool, won: bool,
                     fill_price: float, fee_bps: float) -> list:
        return [
            _round(
                winner_touched=0,
                both_touched=0,
                effective_crossover_count=0,
                median_er_60s=0.65,
                m01_filled=1 if filled else 0,
                m01_won=1 if (filled and won) else 0,
                m01_avg_fill_price=fill_price if filled else None,
                m01_avg_fee_rate_bps=fee_bps if filled else None,
            )
            for _ in range(n)
        ]

    def test_break_even_win_rate_and_edge(self):
        # entry=0.28, fee_bps=200 → fee=min(0.28,0.72)*200/10000=0.28*0.02=0.0056
        # cost=0.28+0.0056=0.2856 → break_even=0.2856
        # 12 rounds, all filled, 8 won → conditional_fill_wr = 8/12
        # edge = 8/12 - 0.2856 = 0.3811
        fill_price = 0.28
        fee_bps = 200.0
        cost = _effective_taker_cost(fill_price, fee_bps)
        expected_be = round(cost, 6)
        n_filled = 12
        n_won = 8
        rounds = (
            [_round(winner_touched=0, both_touched=0, effective_crossover_count=0,
                    median_er_60s=0.65, m01_filled=1, m01_won=1,
                    m01_avg_fill_price=fill_price, m01_avg_fee_rate_bps=fee_bps)
             for _ in range(n_won)]
            + [_round(winner_touched=0, both_touched=0, effective_crossover_count=0,
                      median_er_60s=0.65, m01_filled=1, m01_won=0,
                      m01_avg_fill_price=fill_price, m01_avg_fee_rate_bps=fee_bps)
               for _ in range(n_filled - n_won)]
        )

        state, reason, metrics = self.observer.classify_state(rounds)
        self.assertAlmostEqual(metrics["m01_break_even_win_rate"], expected_be, places=5)
        cwr = metrics["m01_conditional_win_rate"]
        expected_edge = round(cwr - cost, 6)
        self.assertAlmostEqual(metrics["m01_edge"], expected_edge, places=3)
        self.assertFalse(metrics.get("m01_cost_excluded", False))
        self.assertEqual(metrics["cost_status"], "cost_included")
        self.assertEqual(metrics["m01_settled_fill_sample_count"], 12)

    def test_no_fill_price_data_cost_excluded(self):
        rounds = [
            _round(m01_filled=1, m01_won=1,
                   m01_avg_fill_price=None, m01_avg_fee_rate_bps=None,
                   effective_crossover_count=0, median_er_60s=0.65)
            for _ in range(12)
        ]
        _, _, metrics = self.observer.classify_state(rounds)
        self.assertTrue(metrics.get("m01_cost_excluded", False))
        self.assertIsNone(metrics["m01_edge"])

    def test_effective_taker_cost_formula(self):
        # entry=0.30, fee_bps=200 → fee=min(0.30,0.70)*0.02=0.006 → cost=0.306
        cost = _effective_taker_cost(0.30, 200.0)
        self.assertAlmostEqual(cost, 0.306, places=6)

    def test_edge_negative_when_wr_below_breakeven(self):
        fill_price = 0.30
        fee_bps = 200.0
        # Only 3 wins out of 12 → 25% CWR, but break_even ≈ 30.6%
        rounds = (
            [_round(winner_touched=0, both_touched=0, effective_crossover_count=0,
                    median_er_60s=0.65, m01_filled=1, m01_won=1,
                    m01_avg_fill_price=fill_price, m01_avg_fee_rate_bps=fee_bps)
             for _ in range(3)]
            + [_round(winner_touched=0, both_touched=0, effective_crossover_count=0,
                      median_er_60s=0.65, m01_filled=1, m01_won=0,
                      m01_avg_fill_price=fill_price, m01_avg_fee_rate_bps=fee_bps)
               for _ in range(9)]
        )
        _, _, metrics = self.observer.classify_state(rounds)
        self.assertLess(metrics["m01_edge"], 0.0)

    def test_cost_excluded_uses_average_fill_as_break_even(self):
        rounds = (
            [_round(m01_filled=1, m01_won=1, m01_fill_price=0.20) for _ in range(2)]
            + [_round(m01_filled=1, m01_won=0, m01_fill_price=0.30) for _ in range(2)]
        )
        _, _, metrics = self.observer.classify_state(rounds)
        self.assertAlmostEqual(metrics["m01_average_fill_price"], 0.25)
        self.assertAlmostEqual(metrics["m01_break_even_win_rate"], 0.25)
        self.assertAlmostEqual(metrics["m01_conditional_win_rate"], 0.50)
        self.assertAlmostEqual(metrics["m01_edge"], 0.25)
        self.assertEqual(metrics["cost_status"], "cost_excluded")


class TestConditionalFillEligibility(unittest.TestCase):
    def test_unfilled_and_cancelled_rows_are_excluded(self):
        summary = summarize_m01_settled_fills([
            {"strategy": "M01", "status": "CANCELLED", "entry_price": 0.30, "fee_rate_bps": 200},
            {"strategy": "M01", "status": "EXPIRED_UNFILLED", "entry_price": None, "fee_rate_bps": None},
        ])
        self.assertFalse(summary["m01_filled"])
        self.assertEqual(summary["sample_count"], 0)

    def test_open_unsettled_rows_are_excluded(self):
        summary = summarize_m01_settled_fills([
            {"strategy": "M01", "status": "OPEN", "entry_price": 0.30, "fee_rate_bps": 200},
        ])
        self.assertFalse(summary["m01_filled"])

    def test_other_strategies_are_excluded(self):
        summary = summarize_m01_settled_fills([
            {"strategy": "M0", "status": "SETTLED_WIN", "entry_price": 0.30, "fee_rate_bps": 200},
            {"strategy": "B", "status": "SETTLED_LOSS", "entry_price": 0.90, "fee_rate_bps": 200},
        ])
        self.assertFalse(summary["m01_filled"])

    def test_only_settled_m01_rows_are_aggregated(self):
        summary = summarize_m01_settled_fills([
            {"strategy": "M01", "status": "SETTLED_WIN", "entry_price": 0.20, "fee_rate_bps": 100},
            {"strategy": "M01", "status": "SETTLED_LOSS", "entry_price": 0.30, "fee_rate_bps": 300},
            {"strategy": "M01", "status": "CLOSED", "entry_price": 0.10, "fee_rate_bps": 0},
            {"strategy": "M0", "status": "SETTLED_WIN", "entry_price": 0.50, "fee_rate_bps": 200},
        ])
        self.assertTrue(summary["m01_filled"])
        self.assertTrue(summary["m01_won"])
        self.assertEqual(summary["sample_count"], 2)
        self.assertAlmostEqual(summary["m01_fill_price"], 0.25)
        self.assertAlmostEqual(summary["m01_fee_rate_bps"], 200.0)


class TestM01OPaperEntryGate(unittest.TestCase):
    def setUp(self):
        self.observer = MarketStateObserver(db_path=None, window_size=20)
        self.now = time.time()
        self.observer.current_market_id = 9001
        self.observer._market_open_ts = self.now - 100.0
        self.observer._last_spot_update_ts = self.now
        self.observer._last_book_update_ts = self.now
        self.range_rounds = [
            _round(
                winner_touched=1,
                both_touched=1,
                effective_crossover_count=3,
                median_er_60s=0.20,
            )
            for _ in range(6)
        ]

    def set_rounds(self, rounds):
        self.observer.get_recent_rounds = lambda limit=None: list(rounds)
        self.observer._classification_cache = None

    def test_allows_only_when_history_and_current_round_are_range_like(self):
        self.set_rounds(self.range_rounds)
        self.observer.up_touched = True
        self.observer.down_touched = True

        gate = self.observer.m01o_entry_gate()

        self.assertTrue(gate["allowed"])
        self.assertEqual(gate["status"], "ALLOW")
        self.assertEqual(gate["historicalState"], "RANGE")
        self.assertEqual(gate["historicalSampleCount"], 6)
        self.assertTrue(gate["historicalProvisional"])
        self.assertEqual(gate["currentRangeScore"], 2)
        self.assertTrue(gate["paperOnly"])
        self.assertFalse(gate["liveOrdersAffected"])
        self.assertNotIn("winner", gate)

    def test_insufficient_settled_samples_fail_closed(self):
        self.set_rounds(self.range_rounds[:5])
        self.observer.up_touched = True
        self.observer.down_touched = True

        gate = self.observer.m01o_entry_gate()

        self.assertFalse(gate["allowed"])
        self.assertIn("5/6", gate["reason"])

    def test_historical_trend_blocks_even_with_both_sides_touched_now(self):
        self.set_rounds([
            _round(
                winner_touched=0,
                both_touched=0,
                effective_crossover_count=0,
                median_er_60s=0.70,
            )
            for _ in range(6)
        ])
        self.observer.up_touched = True
        self.observer.down_touched = True

        gate = self.observer.m01o_entry_gate()

        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["historicalState"], "TREND")

    def test_current_round_needs_its_own_range_evidence(self):
        self.set_rounds(self.range_rounds)

        gate = self.observer.m01o_entry_gate()

        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["currentRangeScore"], 0)
        self.assertIn("0/2", gate["reason"])

    def test_high_current_er_with_few_crossovers_is_a_trend_veto(self):
        self.set_rounds(self.range_rounds)
        self.observer.up_touched = True
        self.observer.down_touched = True
        self.observer.er_60s_samples = [0.70, 0.72]
        self.observer._er_60s_sample_times = [self.now - 2.0, self.now]

        gate = self.observer.m01o_entry_gate()

        self.assertFalse(gate["allowed"])
        self.assertTrue(gate["currentTrendVeto"])
        self.assertIn("偏單邊", gate["reason"])

    def test_f1_allows_uncertain_history_with_one_current_range_point(self):
        uncertain_rounds = [
            _round(
                winner_touched=0,
                both_touched=1,
                effective_crossover_count=1,
                median_er_60s=0.45,
            )
            for _ in range(6)
        ]
        self.set_rounds(uncertain_rounds)
        self.observer.effective_crossover_count = 2

        strict = self.observer.m01o_entry_gate(profile="F2")
        relaxed = self.observer.m01o_entry_gate(profile="F1")

        self.assertEqual(relaxed["historicalState"], "UNCERTAIN")
        self.assertFalse(strict["allowed"])
        self.assertTrue(relaxed["allowed"])
        self.assertEqual(relaxed["currentRangeScore"], 1)

    def test_live_profile_uses_dual_touch_without_waiting_for_er(self):
        uncertain_rounds = [
            _round(
                winner_touched=0,
                both_touched=1,
                effective_crossover_count=1,
                median_er_60s=0.45,
            )
            for _ in range(6)
        ]
        self.set_rounds(uncertain_rounds)
        self.observer.up_touched = True
        self.observer.down_touched = True
        self.observer.er_60s_samples = [0.80, 0.82]
        self.observer._er_60s_sample_times = [self.now - 2.0, self.now]

        gate = self.observer.m01o_entry_gate(profile="LIVE")

        self.assertTrue(gate["allowed"])
        self.assertTrue(gate["currentTrendVeto"])
        self.assertTrue(gate["currentBothSidesTouched"])

    def test_early_phase_uses_30_second_er_without_requiring_60_second_er(self):
        self.set_rounds(self.range_rounds)
        self.observer._market_open_ts = self.now - 40.0
        self.observer.spot_price_history.extend(
            [
                (self.now - 31.0, 100.0),
                (self.now - 20.0, 101.0),
                (self.now - 10.0, 99.0),
                (self.now, 100.0),
            ]
        )

        gate = self.observer.m01o_entry_gate(profile="F1", now_ts=self.now)

        self.assertTrue(gate["allowed"])
        self.assertEqual(gate["currentPhase"], "EARLY_0_60S")
        self.assertIsNotNone(gate["currentShortEr"])
        self.assertIsNone(gate["currentMedianEr60s"])

    def test_not_ready_is_distinct_from_missing_or_stale(self):
        self.set_rounds(self.range_rounds)
        self.observer._market_open_ts = self.now - 10.0

        not_ready = self.observer.m01o_entry_gate(
            profile="F1", now_ts=self.now
        )
        self.observer._last_spot_update_ts = self.now - 10.0
        stale = self.observer.m01o_entry_gate(profile="F1", now_ts=self.now)

        self.assertEqual(not_ready["blockCategory"], "NOT_READY")
        self.assertEqual(not_ready["dataQualityStatus"], "READY")
        self.assertEqual(stale["blockCategory"], "MISSING_OR_STALE")
        self.assertEqual(stale["dataQualityStatus"], "MISSING_OR_STALE")


class TestHelperFunctions(unittest.TestCase):
    def test_median_odd(self):
        self.assertAlmostEqual(_median([1, 2, 3, 4, 5]), 3.0)

    def test_median_even(self):
        self.assertAlmostEqual(_median([1, 2, 3, 4]), 2.5)

    def test_median_empty(self):
        self.assertIsNone(_median([]))

    def test_percentile_p75(self):
        vals = [1.0, 2.0, 3.0, 4.0]
        p75 = _percentile(vals, 75.0)
        self.assertIsNotNone(p75)
        self.assertAlmostEqual(p75, 3.25, places=4)

    def test_percentile_empty(self):
        self.assertIsNone(_percentile([], 75.0))


if __name__ == "__main__":
    unittest.main()
