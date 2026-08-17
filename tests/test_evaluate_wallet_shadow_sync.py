from __future__ import annotations

import sqlite3

from tools.evaluate_wallet_shadow_sync import (
    Event,
    collapse_bursts,
    eligible_markets,
    one_to_one_matches,
    score,
)


def event(at_ms: int, side: str = "UP", price: float = 0.5, event_id: str = "e") -> Event:
    return Event(at_ms=at_ms, side=side, price=price, event_id=event_id)


def test_one_shadow_cannot_match_multiple_target_events() -> None:
    targets = [event(1_000, event_id="t1"), event(1_100, event_id="t2")]
    shadows = [event(1_050, event_id="s1")]

    matches = one_to_one_matches(targets, shadows, max_lag_ms=3_000, max_price_delta=0.01)

    assert len(matches) == 1
    metrics = score(targets, shadows, matches)
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 0.5
    assert metrics["f1"] == 2 / 3


def test_side_timing_and_price_are_all_required() -> None:
    targets = [event(10_000, "UP", 0.50, "target")]
    shadows = [
        event(10_100, "DOWN", 0.50, "wrong-side"),
        event(14_000, "UP", 0.50, "late"),
        event(10_100, "UP", 0.53, "wrong-price"),
    ]

    matches = one_to_one_matches(targets, shadows, max_lag_ms=3_000, max_price_delta=0.02)

    assert matches == []


def test_over_emission_reduces_precision_and_f1() -> None:
    targets = [event(1_000, event_id="target")]
    shadows = [event(1_000 + i, event_id=f"shadow-{i}") for i in range(4)]
    matches = one_to_one_matches(targets, shadows, max_lag_ms=3_000, max_price_delta=0.01)

    metrics = score(targets, shadows, matches)

    assert metrics["matches"] == 1
    assert metrics["precision"] == 0.25
    assert metrics["recall"] == 1.0
    assert metrics["f1"] == 0.4


def test_bursts_collapse_only_same_side_events_within_gap() -> None:
    events = [
        event(1_000, "UP", event_id="u1"),
        event(1_500, "UP", event_id="u2"),
        event(1_600, "DOWN", event_id="d1"),
        event(3_000, "UP", event_id="u3"),
    ]

    bursts = collapse_bursts(events, 1_000)

    assert [(row.at_ms, row.side, row.event_id) for row in bursts] == [
        (1_500, "UP", "u1+u2"),
        (1_600, "DOWN", "d1"),
        (3_000, "UP", "u3"),
    ]


def test_eligible_markets_excludes_unsettled_cohort() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE wallet_shadow_taker_v1_markets(wallet TEXT,market_id INTEGER,started_at_ms INTEGER);
        CREATE TABLE wallet_shadow_taker_v1_market_results(wallet TEXT,market_id INTEGER,resolved_at_ms INTEGER);
        CREATE TABLE wallet_shadow_target_events(wallet TEXT,market_id INTEGER,event_ms INTEGER);
        CREATE TABLE wallet_shadow_taker_v1_events(wallet TEXT,market_id INTEGER,at_ms INTEGER);
        INSERT INTO wallet_shadow_taker_v1_markets VALUES ('w',1,100),('w',2,200);
        INSERT INTO wallet_shadow_taker_v1_market_results VALUES ('w',1,500);
        """
    )

    assert eligible_markets(conn, None) == [(1, 100)]
