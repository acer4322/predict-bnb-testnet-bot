"""
market_observer.py
==================
Market State Observer for the BTC 5-minute Prediction market.

PURPOSE: Observation, display, persistence, and fail-closed entry gates.
         This module never places orders.  Its F1 gate may influence real-money
         entry only when the user explicitly selects M01O_F1 in the separate
         live executor, which independently revalidates every gate field.

Changes from v1
---------------
* Score-based RANGE / UNCERTAIN / TREND classification (replaces heuristic booleans).
* Winner Touch Rate direction corrected: HIGH means reversal/range, LOW means trend.
* Deadband-filtered startPrice crossover counting.
* Median (not mean) 60 s ER is used for classification; early_er_60s and p75_er_60s
  are also persisted.
* Provisional flag when 6 <= sample_count <= 11; forced UNCERTAIN when < 6.
* Improved conditional-fill stats: average fill price, break-even win rate, and edge.
  Break-even is derived from effective_taker_cost = entry_price + fee, or labelled
  cost_excluded when no cost data are available.
"""

from __future__ import annotations

import sqlite3
import statistics
import threading
import time
from collections import deque
from typing import Any, Dict, List, Mapping, Optional, Tuple


ER_SAMPLE_INTERVAL_SECONDS = 1.0


# ---------------------------------------------------------------------------
# Pure functions (no side-effects, easy to unit-test)
# ---------------------------------------------------------------------------

def calculate_efficiency_ratio(
    price_history: List[Tuple[float, float]],
    now_ts: float,
    window_seconds: float,
) -> Optional[float]:
    """Kaufman Efficiency Ratio over one causal trailing window."""
    cutoff = now_ts - float(window_seconds)
    ordered = sorted(
        (float(ts), float(price))
        for ts, price in price_history
        if ts <= now_ts
    )
    anchor = next(
        ((ts, price) for ts, price in reversed(ordered) if ts <= cutoff),
        None,
    )
    if anchor is None:
        return None
    recent_points = [anchor] + [
        (ts, price) for ts, price in ordered if cutoff < ts <= now_ts
    ]
    recent = [price for _, price in recent_points]
    if len(recent) < 2:
        return None
    net_change = abs(recent[-1] - recent[0])
    sum_abs_diffs = sum(abs(recent[i] - recent[i - 1]) for i in range(1, len(recent)))
    if sum_abs_diffs == 0.0:
        return 0.0
    return max(0.0, min(1.0, net_change / sum_abs_diffs))


def calculate_efficiency_ratio_60s(
    price_history: List[Tuple[float, float]], now_ts: float
) -> Optional[float]:
    """Backward-compatible 60-second Efficiency Ratio helper."""
    return calculate_efficiency_ratio(price_history, now_ts, 60.0)


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return statistics.median(values)


def _percentile(values: List[float], pct: float) -> Optional[float]:
    """Linear-interpolation percentile (0 <= pct <= 100)."""
    if not values:
        return None
    sorted_v = sorted(values)
    n = len(sorted_v)
    if n == 1:
        return sorted_v[0]
    idx = (pct / 100.0) * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_v[lo] + frac * (sorted_v[hi] - sorted_v[lo])


def _effective_taker_cost(entry_price: float, fee_rate_bps: float) -> float:
    """
    Mirror of core.effective_taker_cost without importing the full core module.
    cost_per_share = entry_price + shares * min(price, 1-price) * bps / 10000
    For 1 share: cost = entry + min(p, 1-p) * bps / 10000
    """
    fee = min(entry_price, 1.0 - entry_price) * fee_rate_bps / 10_000.0
    return entry_price + fee


def summarize_m01_settled_fills(
    rows: List[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Summarize only successful, officially settled M01 paper fills.

    The caller must invoke this after official settlement statuses have been
    written.  OPEN, CLOSED, cancelled/unfilled, and non-M01 rows are excluded.
    """
    eligible = [
        dict(row)
        for row in rows
        if str(dict(row).get("strategy") or "").upper() == "M01"
        and str(dict(row).get("status") or "").upper()
        in {"SETTLED_WIN", "SETTLED_LOSS"}
    ]
    fill_prices = [
        float(row["entry_price"])
        for row in eligible
        if row.get("entry_price") is not None
    ]
    fee_rates = [
        float(row["fee_rate_bps"])
        for row in eligible
        if row.get("fee_rate_bps") is not None
    ]
    return {
        "m01_filled": bool(eligible),
        "m01_won": any(
            str(row.get("status") or "").upper() == "SETTLED_WIN"
            for row in eligible
        ),
        "m01_fill_price": (
            sum(fill_prices) / len(fill_prices) if fill_prices else None
        ),
        "m01_fee_rate_bps": (
            sum(fee_rates) / len(fee_rates)
            if eligible and len(fee_rates) == len(eligible)
            else None
        ),
        "sample_count": len(eligible),
    }


def score_classification(
    winner_touch_rate: float,
    both_sides_touch_rate: float,
    avg_effective_crossovers: float,
    median_er_60s: Optional[float],
) -> Tuple[int, int, List[str], List[str]]:
    """
    Compute (range_score, trend_score, range_triggers, trend_triggers).
    Does not handle the provisional / insufficient-sample guard; caller does that.
    """
    range_score = 0
    trend_score = 0
    range_triggers: List[str] = []
    trend_triggers: List[str] = []

    # --- RANGE points ---
    if winner_touch_rate >= 0.35:
        range_score += 2
        range_triggers.append(f"勝方觸及率 {winner_touch_rate:.1%} ≥ 35% (+2)")
    if both_sides_touch_rate >= 0.25:
        range_score += 2
        range_triggers.append(f"雙邊觸及率 {both_sides_touch_rate:.1%} ≥ 25% (+2)")
    if avg_effective_crossovers >= 2.0:
        range_score += 1
        range_triggers.append(f"平均穿越 {avg_effective_crossovers:.2f} 次 ≥ 2 (+1)")
    if median_er_60s is not None and median_er_60s <= 0.35:
        range_score += 1
        range_triggers.append(f"中位 ER {median_er_60s:.3f} ≤ 0.35 (+1)")

    # --- TREND points ---
    if winner_touch_rate <= 0.20:
        trend_score += 2
        trend_triggers.append(f"勝方觸及率 {winner_touch_rate:.1%} ≤ 20% (+2)")
    if both_sides_touch_rate <= 0.10:
        trend_score += 1
        trend_triggers.append(f"雙邊觸及率 {both_sides_touch_rate:.1%} ≤ 10% (+1)")
    if avg_effective_crossovers <= 1.0:
        trend_score += 1
        trend_triggers.append(f"平均穿越 {avg_effective_crossovers:.2f} 次 ≤ 1 (+1)")
    if median_er_60s is not None and median_er_60s >= 0.55:
        trend_score += 2
        trend_triggers.append(f"中位 ER {median_er_60s:.3f} ≥ 0.55 (+2)")

    return range_score, trend_score, range_triggers, trend_triggers


# ---------------------------------------------------------------------------
# Main observer class
# ---------------------------------------------------------------------------

class MarketStateObserver:
    """
    Observes 5-minute prediction market rounds and computes microstructural stats:
    - Winner Touch Rate (HIGH → reversal/range; LOW → trend)
    - Both-Sides Touch Rate
    - Conditional Fill Win Rate (M01), break-even win rate, and edge
    - startPrice effective crossover count (deadband-filtered)
    - 60 s Efficiency Ratio (early, median, p75)
    - Score-based market state: RANGE / UNCERTAIN / TREND

    Used for display, monitoring, database persistence, and the M01O paper-only
    entry gate.  It NEVER controls live/real-money orders.
    """

    _COMPAT_SCHEMA_COLUMNS = {
        "effective_crossover_count": "INTEGER NOT NULL DEFAULT 0",
        "median_er_60s": "REAL",
        "early_er_60s": "REAL",
        "p75_er_60s": "REAL",
        "m01_fill_price": "REAL",
        "m01_avg_fill_price": "REAL",
        "m01_avg_fee_rate_bps": "REAL",
    }

    def __init__(
        self,
        db_path: Optional[str] = None,
        window_size: int = 20,
        crossover_deadband_bps: float = 0.5,
        touch_threshold: float = 0.30,
    ) -> None:
        self.db_path = db_path
        self.window_size = window_size
        # Deadband: price must move at least this many bps away from startPrice
        # (in each direction) to constitute a confirmed crossing.
        self.crossover_deadband_bps = crossover_deadband_bps
        self.touch_threshold = touch_threshold
        self.lock = threading.RLock()
        self.last_error: Optional[str] = None

        # ── Active-round tracking ────────────────────────────────────────────
        self.current_market_id: Optional[int] = None
        self.start_price: Optional[float] = None
        self.last_spot_price: Optional[float] = None

        # Deadband-filtered crossover state
        # "above" / "below" / None (never confirmed either side yet)
        self._cross_confirmed_side: Optional[str] = None
        self.crossover_count: int = 0          # legacy (no deadband) – kept for API compat
        self.effective_crossover_count: int = 0  # deadband-filtered

        self.up_min_ask: Optional[float] = None
        self.down_min_ask: Optional[float] = None
        self.up_touched: bool = False
        self.down_touched: bool = False

        self.spot_price_history: deque = deque(maxlen=3600)  # (ts, price)
        self.er_60s_samples: List[float] = []
        self._market_open_ts: Optional[float] = None
        self._early_er_60s: Optional[float] = None  # ER from first 60 s of the round
        self._last_er_sample_ts: Optional[float] = None
        self._er_60s_sample_times: List[float] = []
        self._last_spot_update_ts: Optional[float] = None
        self._last_book_update_ts: Optional[float] = None
        self._pending_rounds: Dict[int, Dict[str, Any]] = {}
        self._classification_cache: Optional[
            Tuple[
                float,
                List[Dict[str, Any]],
                str,
                str,
                Dict[str, Any],
            ]
        ] = None

        self._init_db()

    # ── DB helpers ─────────────────────────────────────────────────────────

    def _get_connection(self) -> Optional[sqlite3.Connection]:
        if not self.db_path:
            return None
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        conn = self._get_connection()
        if not conn:
            return
        try:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS market_observer_rounds (
                        market_id INTEGER PRIMARY KEY,
                        start_time TEXT,
                        start_price REAL,
                        winner TEXT,
                        up_touched INTEGER NOT NULL DEFAULT 0,
                        down_touched INTEGER NOT NULL DEFAULT 0,
                        both_touched INTEGER NOT NULL DEFAULT 0,
                        winner_touched INTEGER NOT NULL DEFAULT 0,
                        crossover_count INTEGER NOT NULL DEFAULT 0,
                        effective_crossover_count INTEGER NOT NULL DEFAULT 0,
                        avg_er_60s REAL,
                        median_er_60s REAL,
                        early_er_60s REAL,
                        p75_er_60s REAL,
                        m01_filled INTEGER NOT NULL DEFAULT 0,
                        m01_won INTEGER NOT NULL DEFAULT 0,
                        m01_fill_price REAL,
                        m01_avg_fill_price REAL,
                        m01_avg_fee_rate_bps REAL,
                        settled_at TEXT
                    )
                    """
                )
                # Migrate older databases that may lack v2 columns
                existing_cols = {
                    row[1]
                    for row in conn.execute(
                        "PRAGMA table_info(market_observer_rounds)"
                    ).fetchall()
                }
                for col, declaration in self._COMPAT_SCHEMA_COLUMNS.items():
                    if col not in existing_cols:
                        conn.execute(
                            f"ALTER TABLE market_observer_rounds "
                            f"ADD COLUMN {col} {declaration}"
                        )
        finally:
            conn.close()

    # ── Per-round lifecycle ────────────────────────────────────────────────

    def _round_snapshot_locked(self) -> Dict[str, Any]:
        samples = list(self.er_60s_samples)
        return {
            "start_price": self.start_price,
            "market_open_ts": self._market_open_ts,
            "up_touched": self.up_touched,
            "down_touched": self.down_touched,
            "crossover_count": self.crossover_count,
            "effective_crossover_count": self.effective_crossover_count,
            "avg_er_60s": (sum(samples) / len(samples)) if samples else None,
            "median_er_60s": _median(samples),
            "early_er_60s": self._early_er_60s,
            "p75_er_60s": _percentile(samples, 75.0),
        }

    def reset_market(
        self,
        market_id: int,
        start_price: Optional[float] = None,
        market_start_ts: Optional[float] = None,
    ) -> None:
        with self.lock:
            if self.current_market_id == market_id:
                if start_price is not None:
                    self.start_price = start_price
                if market_start_ts is not None:
                    self._market_open_ts = market_start_ts
                return
            if self.current_market_id is not None:
                self._pending_rounds[self.current_market_id] = (
                    self._round_snapshot_locked()
                )
                while len(self._pending_rounds) > 8:
                    oldest_market_id = next(iter(self._pending_rounds))
                    self._pending_rounds.pop(oldest_market_id, None)
            self.current_market_id = market_id
            self.start_price = start_price
            self.last_spot_price = None
            self._cross_confirmed_side = None
            self.crossover_count = 0
            self.effective_crossover_count = 0
            self.up_min_ask = None
            self.down_min_ask = None
            self.up_touched = False
            self.down_touched = False
            self.spot_price_history.clear()
            self.er_60s_samples = []
            self._market_open_ts = (
                float(market_start_ts)
                if market_start_ts is not None
                else time.time()
            )
            self._early_er_60s = None
            self._last_er_sample_ts = None
            self._er_60s_sample_times = []
            self._last_spot_update_ts = None
            self._last_book_update_ts = None

    def update_tick(
        self,
        now_ts: float,
        spot_price: Optional[float],
        up_ask: Optional[float] = None,
        down_ask: Optional[float] = None,
        touch_threshold: Optional[float] = None,
        market_id: Optional[int] = None,
    ) -> None:
        with self.lock:
            if market_id is not None and market_id != self.current_market_id:
                return
            threshold = (
                self.touch_threshold
                if touch_threshold is None
                else float(touch_threshold)
            )
            if spot_price is not None and spot_price > 0:
                self.spot_price_history.append((now_ts, spot_price))
                self._last_spot_update_ts = float(now_ts)

                # ── Legacy crossover (no deadband) ──────────────────────────
                if (
                    self.start_price
                    and self.start_price > 0
                    and self.last_spot_price
                    and self.last_spot_price > 0
                ):
                    prev_diff = self.last_spot_price - self.start_price
                    curr_diff = spot_price - self.start_price
                    if (prev_diff > 0 and curr_diff < 0) or (prev_diff < 0 and curr_diff > 0):
                        self.crossover_count += 1

                # ── Deadband-filtered crossover ──────────────────────────────
                if self.start_price and self.start_price > 0:
                    db_abs = self.start_price * self.crossover_deadband_bps / 10_000.0
                    upper = self.start_price + db_abs
                    lower = self.start_price - db_abs

                    if spot_price >= upper:
                        new_side = "above"
                    elif spot_price <= lower:
                        new_side = "below"
                    else:
                        new_side = None  # inside deadband, no change

                    if new_side is not None:
                        if (
                            self._cross_confirmed_side is not None
                            and self._cross_confirmed_side != new_side
                        ):
                            self.effective_crossover_count += 1
                        self._cross_confirmed_side = new_side

                self.last_spot_price = spot_price

                # ── 60 s ER sample ─────────────────────────────────────────
                if (
                    self._market_open_ts is not None
                    and now_ts >= self._market_open_ts + 60.0
                ):
                    if self._early_er_60s is None:
                        self._early_er_60s = calculate_efficiency_ratio_60s(
                            list(self.spot_price_history),
                            self._market_open_ts + 60.0,
                        )
                    # Spot trades can arrive tens of times per second.  ER is
                    # a 60-second indicator, so recomputing and sorting the
                    # full rolling window on every trade adds no useful
                    # resolution and can starve the realtime strategy queue.
                    # One causal sample per second retains the intended
                    # statistic while keeping Observer work bounded.
                    sample_due = bool(
                        self._last_er_sample_ts is None
                        or now_ts - self._last_er_sample_ts
                        >= ER_SAMPLE_INTERVAL_SECONDS
                    )
                    if sample_due:
                        er = calculate_efficiency_ratio_60s(
                            list(self.spot_price_history), now_ts
                        )
                        if er is not None:
                            self.er_60s_samples.append(er)
                            self._er_60s_sample_times.append(float(now_ts))
                            self._last_er_sample_ts = now_ts

            # ── Prediction-book touches ────────────────────────────────────
            if up_ask is not None and up_ask > 0:
                self._last_book_update_ts = float(now_ts)
                if self.up_min_ask is None or up_ask < self.up_min_ask:
                    self.up_min_ask = up_ask
                if up_ask <= threshold:
                    self.up_touched = True

            if down_ask is not None and down_ask > 0:
                self._last_book_update_ts = float(now_ts)
                if self.down_min_ask is None or down_ask < self.down_min_ask:
                    self.down_min_ask = down_ask
                if down_ask <= threshold:
                    self.down_touched = True

    # ── Settlement recording ────────────────────────────────────────────────

    def record_settlement(
        self,
        market_id: int,
        winner: str,
        start_price: Optional[float] = None,
        m01_filled: bool = False,
        m01_won: bool = False,
        up_touched: Optional[bool] = None,
        down_touched: Optional[bool] = None,
        crossover_count: Optional[int] = None,
        effective_crossover_count: Optional[int] = None,
        avg_er_60s: Optional[float] = None,
        median_er_60s: Optional[float] = None,
        early_er_60s: Optional[float] = None,
        p75_er_60s: Optional[float] = None,
        m01_fill_price: Optional[float] = None,
        m01_avg_fill_price: Optional[float] = None,
        m01_avg_fee_rate_bps: Optional[float] = None,
        settled_at: Optional[str] = None,
    ) -> None:
        with self.lock:
            if market_id == self.current_market_id:
                round_snapshot = self._round_snapshot_locked()
            else:
                round_snapshot = dict(self._pending_rounds.get(market_id) or {})
            s_price = (
                start_price
                if start_price is not None
                else round_snapshot.get("start_price")
            )
            up_t = (
                up_touched
                if up_touched is not None
                else bool(round_snapshot.get("up_touched", False))
            )
            down_t = (
                down_touched
                if down_touched is not None
                else bool(round_snapshot.get("down_touched", False))
            )
            cross_c = (
                crossover_count
                if crossover_count is not None
                else int(round_snapshot.get("crossover_count") or 0)
            )
            eff_cross_c = (
                effective_crossover_count
                if effective_crossover_count is not None
                else int(round_snapshot.get("effective_crossover_count") or 0)
            )

            if avg_er_60s is None:
                avg_er_60s = round_snapshot.get("avg_er_60s")
            if median_er_60s is None:
                median_er_60s = round_snapshot.get("median_er_60s")
            if early_er_60s is None:
                early_er_60s = round_snapshot.get("early_er_60s")
            if p75_er_60s is None:
                p75_er_60s = round_snapshot.get("p75_er_60s")
            resolved_fill_price = (
                m01_fill_price
                if m01_fill_price is not None
                else m01_avg_fill_price
            )

            both_t = up_t and down_t
            winner_clean = winner.upper() if winner else ""
            winner_t = (
                (winner_clean == "UP" and up_t)
                or (winner_clean == "DOWN" and down_t)
            )

            conn = self._get_connection()
            if conn:
                try:
                    with conn:
                        conn.execute(
                            """
                            INSERT INTO market_observer_rounds (
                                market_id, start_time, start_price, winner,
                                up_touched, down_touched, both_touched, winner_touched,
                                crossover_count, effective_crossover_count,
                                avg_er_60s, median_er_60s, early_er_60s, p75_er_60s,
                                m01_filled, m01_won,
                                m01_fill_price, m01_avg_fill_price,
                                m01_avg_fee_rate_bps,
                                settled_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(market_id) DO UPDATE SET
                                winner = excluded.winner,
                                up_touched = excluded.up_touched,
                                down_touched = excluded.down_touched,
                                both_touched = excluded.both_touched,
                                winner_touched = excluded.winner_touched,
                                crossover_count = excluded.crossover_count,
                                effective_crossover_count = excluded.effective_crossover_count,
                                avg_er_60s = excluded.avg_er_60s,
                                median_er_60s = excluded.median_er_60s,
                                early_er_60s = excluded.early_er_60s,
                                p75_er_60s = excluded.p75_er_60s,
                                m01_filled = excluded.m01_filled,
                                m01_won = excluded.m01_won,
                                m01_fill_price = excluded.m01_fill_price,
                                m01_avg_fill_price = excluded.m01_avg_fill_price,
                                m01_avg_fee_rate_bps = excluded.m01_avg_fee_rate_bps,
                                settled_at = excluded.settled_at
                            """,
                            (
                                market_id,
                                time.strftime(
                                    "%Y-%m-%d %H:%M:%S",
                                    time.gmtime(
                                        float(round_snapshot.get("market_open_ts"))
                                        if round_snapshot.get("market_open_ts") is not None
                                        else time.time()
                                    ),
                                ),
                                s_price,
                                winner_clean,
                                1 if up_t else 0,
                                1 if down_t else 0,
                                1 if both_t else 0,
                                1 if winner_t else 0,
                                cross_c,
                                eff_cross_c,
                                avg_er_60s,
                                median_er_60s,
                                early_er_60s,
                                p75_er_60s,
                                1 if m01_filled else 0,
                                1 if m01_won else 0,
                                resolved_fill_price,
                                resolved_fill_price,
                                m01_avg_fee_rate_bps,
                                settled_at or time.strftime(
                                    "%Y-%m-%d %H:%M:%S", time.gmtime()
                                ),
                            ),
                        )
                finally:
                    conn.close()
            self.last_error = None
            self._pending_rounds.pop(market_id, None)
            # A newly settled round changes the historical regime immediately.
            self._classification_cache = None

    # ── Data retrieval ─────────────────────────────────────────────────────

    def get_recent_rounds(
        self, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        if not conn:
            return []
        lim = limit or self.window_size
        try:
            rows = conn.execute(
                """
                SELECT * FROM market_observer_rounds
                ORDER BY market_id DESC LIMIT ?
                """,
                (lim,),
            ).fetchall()
            return [dict(row) for row in rows]
        except Exception:
            return []
        finally:
            conn.close()

    # ── Classification ────────────────────────────────────────────────────

    def classify_state(
        self, rounds: List[Dict[str, Any]]
    ) -> Tuple[str, str, Dict[str, Any]]:
        """
        Score-based classification: RANGE / UNCERTAIN / TREND.
        Returns (state, reason, metrics_dict).
        Observation only – result is NEVER used to place or cancel live orders.
        """
        sample_size = len(rounds)

        # ── Compute aggregate metrics ──────────────────────────────────────
        winner_touches = sum(1 for r in rounds if r.get("winner_touched") == 1)
        both_touches = sum(1 for r in rounds if r.get("both_touched") == 1)

        # Prefer the deadband-filtered count even when it is exactly zero.
        crossover_values = []
        for row in rounds:
            value = row.get("effective_crossover_count")
            if value is None:
                value = row.get("crossover_count")
            crossover_values.append(int(value or 0))

        median_er_vals = []
        for row in rounds:
            value = row.get("median_er_60s")
            if value is None:
                value = row.get("avg_er_60s")
            if value is not None:
                median_er_vals.append(float(value))
        early_er_vals = [
            float(row["early_er_60s"])
            for row in rounds
            if row.get("early_er_60s") is not None
        ]
        p75_er_vals = [
            float(row["p75_er_60s"])
            for row in rounds
            if row.get("p75_er_60s") is not None
        ]

        m01_filled_rounds = [r for r in rounds if r.get("m01_filled") == 1]
        m01_wins = sum(1 for r in m01_filled_rounds if r.get("m01_won") == 1)

        fill_observations: List[Tuple[float, Optional[float]]] = []
        for row in m01_filled_rounds:
            fill_price = row.get("m01_fill_price")
            if fill_price is None:
                fill_price = row.get("m01_avg_fill_price")
            if fill_price is None:
                continue
            fee_rate = row.get("m01_avg_fee_rate_bps")
            fill_observations.append(
                (
                    float(fill_price),
                    float(fee_rate) if fee_rate is not None else None,
                )
            )

        # ── Derived statistics ─────────────────────────────────────────────
        winner_touch_rate = winner_touches / sample_size if sample_size else None
        both_sides_touch_rate = both_touches / sample_size if sample_size else None
        avg_effective_crossovers = (
            sum(crossover_values) / sample_size if sample_size else None
        )
        median_er_60s = _median(median_er_vals)
        early_er_60s_median = _median(early_er_vals)
        p75_er_60s_median = _median(p75_er_vals)

        conditional_fill_win_rate = (
            m01_wins / len(m01_filled_rounds) if m01_filled_rounds else None
        )

        # Break-even win rate and edge
        m01_avg_fill_price_out: Optional[float] = None
        m01_break_even_win_rate: Optional[float] = None
        m01_edge: Optional[float] = None
        cost_status = "unavailable"

        if fill_observations:
            fill_prices = [price for price, _ in fill_observations]
            avg_fp = sum(fill_prices) / len(fill_prices)
            m01_avg_fill_price_out = round(avg_fp, 6)
            complete_fee_data = (
                len(fill_observations) == len(m01_filled_rounds)
                and all(fee_rate is not None for _, fee_rate in fill_observations)
            )
            if complete_fee_data:
                costs = [
                    _effective_taker_cost(price, float(fee_rate))
                    for price, fee_rate in fill_observations
                    if fee_rate is not None
                ]
                m01_break_even_win_rate = round(sum(costs) / len(costs), 6)
                cost_status = "cost_included"
            else:
                m01_break_even_win_rate = round(avg_fp, 6)
                cost_status = "cost_excluded"
            if conditional_fill_win_rate is not None:
                m01_edge = round(
                    conditional_fill_win_rate - m01_break_even_win_rate,
                    6,
                )
        elif m01_filled_rounds:
            cost_status = "cost_excluded"

        score_winner_rate = winner_touch_rate if winner_touch_rate is not None else 0.0
        score_both_rate = (
            both_sides_touch_rate if both_sides_touch_rate is not None else 0.0
        )
        score_crossovers = (
            avg_effective_crossovers
            if avg_effective_crossovers is not None
            else 0.0
        )
        if sample_size:
            r_score, t_score, r_triggers, t_triggers = score_classification(
                score_winner_rate,
                score_both_rate,
                score_crossovers,
                median_er_60s,
            )
        else:
            r_score, t_score, r_triggers, t_triggers = 0, 0, [], []

        provisional = sample_size < 12
        reasons: List[str] = []
        conflicts: List[str] = []

        # ── Final decision ─────────────────────────────────────────────────
        if sample_size < 6:
            state = "UNCERTAIN"
            reasons.append(f"樣本數不足：{sample_size}/6 輪")
            reasons.extend([f"暫定 RANGE 證據：{item}" for item in r_triggers])
            reasons.extend([f"暫定 TREND 證據：{item}" for item in t_triggers])
            if r_triggers and t_triggers:
                conflicts.append("樣本不足，且 RANGE 與 TREND 訊號同時存在")
        elif r_score >= 4 and r_score >= t_score + 2:
            state = "RANGE"
            reasons.extend(r_triggers)
            conflicts.extend(t_triggers)
        elif t_score >= 4 and t_score >= r_score + 2:
            state = "TREND"
            reasons.extend(t_triggers)
            conflicts.extend(r_triggers)
        else:
            state = "UNCERTAIN"
            reasons.append("RANGE／TREND 分數未達門檻或分差不足 2")
            reasons.extend([f"RANGE 證據：{item}" for item in r_triggers])
            reasons.extend([f"TREND 證據：{item}" for item in t_triggers])
            if r_triggers and t_triggers:
                conflicts.append("RANGE 與 TREND 訊號同時存在")

        phase = "暫定" if provisional else "正式"
        reason = (
            f"樣本 {sample_size}/{self.window_size}（{phase}）｜"
            f"RANGE {r_score} vs TREND {t_score}｜"
            + "；".join(reasons or ["資料累積中"])
        )

        metrics: Dict[str, Any] = {
            "winner_touch_rate": (
                round(winner_touch_rate, 6)
                if winner_touch_rate is not None
                else None
            ),
            "both_sides_touch_rate": (
                round(both_sides_touch_rate, 6)
                if both_sides_touch_rate is not None
                else None
            ),
            "conditional_fill_win_rate": (
                round(conditional_fill_win_rate, 6)
                if conditional_fill_win_rate is not None
                else None
            ),
            "m01_average_fill_price": m01_avg_fill_price_out,
            "m01_conditional_win_rate": (
                round(conditional_fill_win_rate, 6)
                if conditional_fill_win_rate is not None
                else None
            ),
            "m01_break_even_win_rate": m01_break_even_win_rate,
            "m01_edge": m01_edge,
            "m01_settled_fill_sample_count": len(m01_filled_rounds),
            "cost_status": cost_status,
            "m01_cost_excluded": cost_status == "cost_excluded",
            "average_effective_crossovers": (
                round(avg_effective_crossovers, 3)
                if avg_effective_crossovers is not None
                else None
            ),
            "avg_effective_crossover_count": (
                round(avg_effective_crossovers, 3)
                if avg_effective_crossovers is not None
                else None
            ),
            "median_er_60s": round(median_er_60s, 3) if median_er_60s is not None else None,
            "early_er_60s_median": (
                round(early_er_60s_median, 3)
                if early_er_60s_median is not None
                else None
            ),
            "p75_er_60s_median": (
                round(p75_er_60s_median, 3)
                if p75_er_60s_median is not None
                else None
            ),
            "range_score": r_score,
            "trend_score": t_score,
            "reasons": reasons,
            "conflicts": conflicts,
            "sample_count": sample_size,
            "sample_size": sample_size,
            "rolling_round_limit": self.window_size,
            "provisional": provisional,
        }
        return state, reason, metrics

    def _historical_classification_locked(
        self,
    ) -> Tuple[List[Dict[str, Any]], str, str, Dict[str, Any]]:
        """Return the rolling classification with at most one DB read per second."""
        now = time.monotonic()
        cached = self._classification_cache
        if cached is not None and now - cached[0] < 1.0:
            return cached[1], cached[2], cached[3], cached[4]
        recent_rounds = self.get_recent_rounds(self.window_size)
        state_name, reason, metrics = self.classify_state(recent_rounds)
        self._classification_cache = (
            now,
            recent_rounds,
            state_name,
            reason,
            metrics,
        )
        return recent_rounds, state_name, reason, metrics

    def _m01o_entry_gate_v1(
        self,
        *,
        min_settled_samples: int = 6,
        min_current_range_score: int = 2,
    ) -> Dict[str, Any]:
        """Build a causal, fail-closed gate for the M01O paper strategy.

        Historical evidence uses only already-settled rounds.  Current-round
        evidence uses only direct Ask touches, deadband crossovers, and 60 s ER;
        it never uses the current winner or any future settlement information.
        """
        with self.lock:
            _, historical_state, historical_reason, metrics = (
                self._historical_classification_locked()
            )
            sample_count = int(metrics["sample_count"])
            current_er = _median(list(self.er_60s_samples))
            effective_crossovers = int(self.effective_crossover_count)
            both_touched = bool(self.up_touched and self.down_touched)

            current_range_score = 0
            current_evidence: List[str] = []
            if both_touched:
                current_range_score += 2
                current_evidence.append("當輪 UP／DOWN 直接 Ask 都曾 ≤ 0.30 (+2)")
            if effective_crossovers >= 2:
                current_range_score += 1
                current_evidence.append(
                    f"當輪有效穿越 {effective_crossovers} 次 ≥ 2 (+1)"
                )
            if current_er is not None and current_er <= 0.35:
                current_range_score += 1
                current_evidence.append(f"當輪 Median 60s ER {current_er:.3f} ≤ 0.35 (+1)")

            current_trend_veto = bool(
                current_er is not None
                and current_er >= 0.55
                and effective_crossovers <= 1
            )
            blockers: List[str] = []
            if self.last_error:
                blockers.append("觀測器目前為 DEGRADED")
            if self.current_market_id is None:
                blockers.append("尚未建立當輪觀測")
            if sample_count < int(min_settled_samples):
                blockers.append(
                    f"已結算樣本 {sample_count}/{int(min_settled_samples)} 不足"
                )
            if historical_state != "RANGE":
                blockers.append(f"歷史狀態為 {historical_state}，不是 RANGE")
            if current_trend_veto:
                blockers.append("當輪 ER 偏單邊且有效穿越不超過 1 次")
            if current_range_score < int(min_current_range_score):
                blockers.append(
                    "當輪震盪證據 "
                    f"{current_range_score}/{int(min_current_range_score)} 不足"
                )

            allowed = not blockers
            return {
                "allowed": allowed,
                "status": "ALLOW" if allowed else "BLOCK",
                "reason": (
                    "歷史與當輪皆符合非單邊門檻"
                    if allowed
                    else "；".join(blockers)
                ),
                "paperOnly": True,
                "liveOrdersAffected": False,
                "historicalState": historical_state,
                "historicalReason": historical_reason,
                "historicalSampleCount": sample_count,
                "historicalProvisional": bool(metrics["provisional"]),
                "historicalRangeScore": int(metrics["range_score"]),
                "historicalTrendScore": int(metrics["trend_score"]),
                "currentMarketId": self.current_market_id,
                "currentRangeScore": current_range_score,
                "currentBothSidesTouched": both_touched,
                "currentEffectiveCrossovers": effective_crossovers,
                "currentMedianEr60s": (
                    round(current_er, 3) if current_er is not None else None
                ),
                "currentTrendVeto": current_trend_veto,
                "currentEvidence": current_evidence,
                "blockers": blockers,
                "minSettledSamples": int(min_settled_samples),
                "minCurrentRangeScore": int(min_current_range_score),
            }

    def m01o_entry_gate(
        self,
        *,
        min_settled_samples: int = 6,
        min_current_range_score: int = 2,
        profile: str = "F2",
        now_ts: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Build one causal paper-only gate from the shared M01 opportunity."""
        with self.lock:
            normalized_profile = str(profile).strip().upper()
            if normalized_profile not in {"F2", "F1", "LIVE"}:
                raise ValueError("M01O gate profile must be F2, F1, or LIVE")
            _, historical_state, historical_reason, metrics = (
                self._historical_classification_locked()
            )
            sample_count = int(metrics["sample_count"])
            evaluation_ts = float(now_ts) if now_ts is not None else time.time()
            market_age = (
                max(0.0, evaluation_ts - float(self._market_open_ts))
                if self._market_open_ts is not None
                else None
            )
            phase = (
                "EARLY_0_60S"
                if market_age is not None and market_age < 60.0
                else "MATURE_60S_PLUS"
            )
            er_samples = list(self.er_60s_samples)
            current_er = _median(er_samples)
            short_er_30s = (
                calculate_efficiency_ratio(
                    list(self.spot_price_history), evaluation_ts, 30.0
                )
                if market_age is not None and market_age >= 30.0
                else None
            )
            er_sample_span = (
                max(
                    0.0,
                    self._er_60s_sample_times[-1]
                    - self._er_60s_sample_times[0],
                )
                if len(self._er_60s_sample_times) >= 2
                else 0.0
            )
            effective_crossovers = int(self.effective_crossover_count)
            both_touched = bool(self.up_touched and self.down_touched)

            current_range_score = 0
            current_evidence: List[str] = []
            if both_touched:
                current_range_score += 2
                current_evidence.append("當輪 UP、DOWN Ask 都曾 ≤0.30（+2）")
            if effective_crossovers >= 2:
                current_range_score += 1
                current_evidence.append(
                    f"當輪有效穿越 {effective_crossovers} 次 ≥2（+1）"
                )
            phase_er = short_er_30s if phase == "EARLY_0_60S" else current_er
            phase_er_label = (
                "短窗 30s ER"
                if phase == "EARLY_0_60S"
                else "Median 60s ER"
            )
            if phase_er is not None and phase_er <= 0.35:
                current_range_score += 1
                current_evidence.append(
                    f"當輪 {phase_er_label} {phase_er:.3f} ≤0.35（+1）"
                )

            trend_veto_ready = bool(
                phase == "MATURE_60S_PLUS"
                and len(er_samples) >= 2
                and er_sample_span >= 1.0
            )
            current_trend_veto = bool(
                trend_veto_ready
                and current_er is not None
                and current_er >= 0.55
                and effective_crossovers <= 1
            )
            spot_age = (
                max(0.0, evaluation_ts - self._last_spot_update_ts)
                if self._last_spot_update_ts is not None
                else None
            )
            book_age = (
                max(0.0, evaluation_ts - self._last_book_update_ts)
                if self._last_book_update_ts is not None
                else None
            )
            data_issues: List[str] = []
            if self.last_error:
                data_issues.append("市場觀測器為 DEGRADED")
            if self.current_market_id is None or market_age is None:
                data_issues.append("尚未建立當輪市場")
            if market_age is not None and market_age >= 5.0:
                if spot_age is None:
                    data_issues.append("當輪尚無 Spot 資料")
                elif spot_age > 5.0:
                    data_issues.append(f"Spot 資料已延遲 {spot_age:.1f} 秒")
                if book_age is None:
                    data_issues.append("當輪尚無 Prediction Ask 資料")
                elif book_age > 5.0:
                    data_issues.append(
                        f"Prediction Ask 已延遲 {book_age:.1f} 秒"
                    )

            blockers: List[str] = []
            blocker_categories: List[str] = []
            if data_issues:
                blockers.extend(data_issues)
                blocker_categories.append("MISSING_OR_STALE")
            if sample_count < int(min_settled_samples):
                blockers.append(
                    f"歷史樣本 {sample_count}/{int(min_settled_samples)} 尚未成熟"
                )
                blocker_categories.append("NOT_READY")

            if normalized_profile == "F2" and historical_state != "RANGE":
                blockers.append(f"F2 要求歷史 RANGE，目前為 {historical_state}")
                blocker_categories.append("FILTERED")
            elif (
                normalized_profile in {"F1", "LIVE"}
                and historical_state == "TREND"
            ):
                blockers.append("歷史狀態為 TREND")
                blocker_categories.append("FILTERED")

            if normalized_profile != "LIVE" and current_trend_veto:
                blockers.append(
                    "當輪偏單邊：Median 60s ER ≥0.55、有效穿越 ≤1，且已有至少兩筆跨時觀測"
                )
                blocker_categories.append("FILTERED")

            required_score = 2 if normalized_profile == "F2" else 1
            if normalized_profile == "LIVE" and not both_touched:
                blockers.append("LIVE 等待當輪雙邊 Ask 都曾 ≤0.30")
                blocker_categories.append("NOT_READY")
            elif (
                normalized_profile != "LIVE"
                and current_range_score < required_score
            ):
                blockers.append(
                    f"當輪震盪分 {current_range_score}/{required_score} 尚未達標"
                )
                indicator_not_ready = bool(
                    (
                        phase == "EARLY_0_60S"
                        and market_age is not None
                        and market_age < 30.0
                    )
                    or (phase == "MATURE_60S_PLUS" and current_er is None)
                )
                blocker_categories.append(
                    "NOT_READY" if indicator_not_ready else "FILTERED"
                )

            allowed = not blockers
            block_category = (
                "ALLOW"
                if allowed
                else "MISSING_OR_STALE"
                if "MISSING_OR_STALE" in blocker_categories
                else "NOT_READY"
                if "NOT_READY" in blocker_categories
                else "FILTERED"
            )
            return {
                "allowed": allowed,
                "status": "ALLOW" if allowed else "BLOCK",
                "profile": normalized_profile,
                "blockCategory": block_category,
                "reason": (
                    f"{normalized_profile} 條件成立"
                    if allowed
                    else "；".join(blockers)
                ),
                "paperOnly": True,
                "liveOrdersAffected": False,
                "historicalState": historical_state,
                "historicalReason": historical_reason,
                "historicalSampleCount": sample_count,
                "historicalProvisional": bool(metrics["provisional"]),
                "historicalRangeScore": int(metrics["range_score"]),
                "historicalTrendScore": int(metrics["trend_score"]),
                "currentMarketId": self.current_market_id,
                "currentPhase": phase,
                "currentMarketAgeSeconds": (
                    round(market_age, 3) if market_age is not None else None
                ),
                "currentRangeScore": current_range_score,
                "currentBothSidesTouched": both_touched,
                "currentEffectiveCrossovers": effective_crossovers,
                "currentShortErWindowSeconds": 30,
                "currentShortEr": (
                    round(short_er_30s, 3)
                    if short_er_30s is not None
                    else None
                ),
                "currentMedianEr60s": (
                    round(current_er, 3) if current_er is not None else None
                ),
                "validEr60sObservations": len(er_samples),
                "er60sObservationSpanSeconds": round(er_sample_span, 3),
                "trendVetoReady": trend_veto_ready,
                "currentTrendVeto": current_trend_veto,
                "spotAgeSeconds": (
                    round(spot_age, 3) if spot_age is not None else None
                ),
                "bookAgeSeconds": (
                    round(book_age, 3) if book_age is not None else None
                ),
                "dataQualityStatus": (
                    "MISSING_OR_STALE" if data_issues else "READY"
                ),
                "indicatorReadiness": (
                    "NOT_READY" if phase_er is None else "READY"
                ),
                "currentEvidence": current_evidence,
                "blockers": blockers,
                "minSettledSamples": int(min_settled_samples),
                "minCurrentRangeScore": (
                    None if normalized_profile == "LIVE" else required_score
                ),
            }

    def m01o_entry_gates(
        self,
        *,
        min_settled_samples: int = 6,
        min_current_range_score: int = 2,
        now_ts: Optional[float] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Return F2, F1, and LIVE from one observer snapshot."""
        return {
            profile: self.m01o_entry_gate(
                min_settled_samples=min_settled_samples,
                min_current_range_score=(
                    min_current_range_score if profile == "F2" else 1
                ),
                profile=profile,
                now_ts=now_ts,
            )
            for profile in ("F2", "F1", "LIVE")
        }

    # ── Public state snapshot ─────────────────────────────────────────────

    def state(
        self,
        *,
        m01o_min_settled_samples: int = 6,
        m01o_min_current_range_score: int = 2,
    ) -> Dict[str, Any]:
        with self.lock:
            recent_rounds, state_name, reason, metrics = (
                self._historical_classification_locked()
            )
            er_samples = list(self.er_60s_samples)
            current_er = _median(er_samples)
            m01o_gates = self.m01o_entry_gates(
                min_settled_samples=m01o_min_settled_samples,
                min_current_range_score=m01o_min_current_range_score,
            )

            return {
                "status": "DEGRADED" if self.last_error else "LIVE",
                "lastError": self.last_error,
                "observeOnly": True,
                "state": state_name,
                "reason": reason,
                "provisional": metrics["provisional"],
                "sampleCount": metrics["sample_count"],
                "rollingRoundLimit": self.window_size,
                "touchThreshold": self.touch_threshold,
                "crossoverDeadbandBps": self.crossover_deadband_bps,
                "winnerTouchRate": metrics["winner_touch_rate"],
                "bothSidesTouchRate": metrics["both_sides_touch_rate"],
                "averageEffectiveCrossovers": metrics[
                    "average_effective_crossovers"
                ],
                "medianEr60s": metrics["median_er_60s"],
                "earlyEr60sMedian": metrics["early_er_60s_median"],
                "p75Er60sMedian": metrics["p75_er_60s_median"],
                "rangeScore": metrics["range_score"],
                "trendScore": metrics["trend_score"],
                "reasons": metrics["reasons"],
                "conflicts": metrics["conflicts"],
                "m01ConditionalWinRate": metrics[
                    "m01_conditional_win_rate"
                ],
                "m01AverageFillPrice": metrics["m01_average_fill_price"],
                "m01BreakEvenWinRate": metrics[
                    "m01_break_even_win_rate"
                ],
                "m01Edge": metrics["m01_edge"],
                "m01SettledFillSampleCount": metrics[
                    "m01_settled_fill_sample_count"
                ],
                "costStatus": metrics["cost_status"],
                "m01oGate": m01o_gates["F2"],
                "m01oGates": m01o_gates,
                "metrics": metrics,
                "currentRound": {
                    "marketId": self.current_market_id,
                    "startPrice": self.start_price,
                    "crossoverCount": self.crossover_count,
                    "effectiveCrossoverCount": self.effective_crossover_count,
                    "upMinAsk": self.up_min_ask,
                    "downMinAsk": self.down_min_ask,
                    "upTouched": self.up_touched,
                    "downTouched": self.down_touched,
                    "bothTouched": self.up_touched and self.down_touched,
                    "currentMedianEr60s": (
                        round(current_er, 3) if current_er is not None else None
                    ),
                    "currentEr60s": (
                        round(er_samples[-1], 3) if er_samples else None
                    ),
                    "earlyEr60s": (
                        round(self._early_er_60s, 3)
                        if self._early_er_60s is not None
                        else None
                    ),
                },
                "roundsHistory": recent_rounds,
            }
