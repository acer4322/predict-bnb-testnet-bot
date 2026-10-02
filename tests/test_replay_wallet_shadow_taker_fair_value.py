from __future__ import annotations

import sqlite3

from tools.replay_wallet_shadow_taker_fair_value import fair_value_candidate


def test_fair_value_candidate_uses_only_trajectory_threshold_and_cooldown() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE multi_prediction_trajectory(
               asset TEXT,market_bucket INTEGER,sampled_at_ms INTEGER,
               executable_edge_up REAL,executable_edge_down REAL,
               binance_up_ask REAL,binance_down_ask REAL)"""
    )
    conn.executemany(
        "INSERT INTO multi_prediction_trajectory VALUES (?,?,?,?,?,?,?)",
        [
            ("BTC", 300, 301_000, 0.06, -0.10, 0.40, 0.62),
            ("BTC", 300, 302_000, 0.07, 0.08, 0.41, 0.61),
            ("BTC", 300, 307_000, 0.08, 0.09, 0.42, 0.60),
            ("ETH", 300, 307_000, 0.50, 0.50, 0.20, 0.20),
        ],
    )

    events = fair_value_candidate(conn, 1, 300_000, minimum_edge=0.05, cooldown_ms=5_000)

    assert [(row.at_ms, row.side, row.price) for row in events] == [
        (301_000, "UP", 0.40),
        (302_000, "DOWN", 0.61),
        (307_000, "UP", 0.42),
        (307_000, "DOWN", 0.60),
    ]
