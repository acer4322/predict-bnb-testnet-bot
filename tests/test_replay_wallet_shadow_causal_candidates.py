from __future__ import annotations

import sqlite3

from tools.replay_wallet_shadow_causal_candidates import quote_change_candidate


def test_quote_candidate_is_derived_only_from_quote_changes() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE wallet_shadow_events(id TEXT,market_id INTEGER,at_ms INTEGER,side TEXT,price REAL,event_type TEXT)"
    )
    conn.executemany(
        "INSERT INTO wallet_shadow_events VALUES (?,?,?,?,?,?)",
        [
            ("q1", 1, 1_000, "UP", 0.50, "MAKER_QUOTE"),
            ("target-like-noise", 1, 1_050, "UP", 0.12, "MAKER_FILL_PROXY"),
            ("q2", 1, 2_000, "UP", 0.51, "MAKER_QUOTE"),
            ("q3", 1, 2_500, "DOWN", 0.48, "MAKER_QUOTE"),
            ("q4", 1, 3_000, "DOWN", 0.47, "MAKER_QUOTE"),
        ],
    )

    events = quote_change_candidate(conn, 1, 0, cooldown_ms=0, direction="ANY")

    assert [(event.at_ms, event.side, event.price) for event in events] == [
        (2_000, "UP", 0.51),
        (3_000, "DOWN", 0.47),
    ]


def test_down_only_candidate_rejects_upward_bid_change() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE wallet_shadow_events(id TEXT,market_id INTEGER,at_ms INTEGER,side TEXT,price REAL,event_type TEXT)"
    )
    conn.executemany(
        "INSERT INTO wallet_shadow_events VALUES (?,?,?,?,?,?)",
        [
            ("q1", 1, 1_000, "UP", 0.50, "MAKER_QUOTE"),
            ("q2", 1, 2_000, "UP", 0.51, "MAKER_QUOTE"),
            ("q3", 1, 3_000, "UP", 0.49, "MAKER_QUOTE"),
        ],
    )

    events = quote_change_candidate(conn, 1, 0, cooldown_ms=0, direction="DOWN_ONLY")

    assert [(event.at_ms, event.price) for event in events] == [(3_000, 0.49)]
