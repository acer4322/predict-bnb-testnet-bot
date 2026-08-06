from __future__ import annotations

from datetime import datetime, timedelta, timezone

from predict_bot import server
from predict_bot.research_forward import GENERIC_SIGNAL_RESEARCH_STRATEGIES
from predict_bot.strong_trend_guard_shadows import (
    SOURCE_TO_SHADOW,
    STRATEGIES,
    evaluate_strong_trend,
)


def rows(prices: list[float], *, side_seconds: float = 250.0, age: float = 20.0):
    return [
        {
            "start_price": 100.0,
            "spot_price": price,
            "seconds_left": side_seconds,
            "spot_age_ms": age,
        }
        for price in prices
    ]


def test_decision_boundaries_and_direction():
    blocked = evaluate_strong_trend(rows([99.99, 99.98, 99.96]), "UP")
    assert blocked["decision"] == "BLOCK_STRONG_OPPOSING_TREND"
    assert blocked["startMoveBps"] <= -2.5
    assert blocked["pathEfficiencyRatio"] >= 0.40

    aligned = evaluate_strong_trend(rows([99.99, 99.98, 99.96]), "DOWN")
    assert aligned["decision"] == "ALLOW"

    early = evaluate_strong_trend(rows([99.99, 99.98, 99.96], side_seconds=280.1), "UP")
    assert early["decision"] == "ALLOW"

    stale = evaluate_strong_trend(rows([99.99, 99.98, 99.96], age=3000.0), "UP")
    assert stale["decision"] == "ALLOW_NOT_EVALUABLE"


def insert_observations(store, market_id: int, prices: list[float], seconds_left: float = 250.0):
    now = datetime.now(timezone.utc) - timedelta(seconds=10)
    with store.lock:
        for index, price in enumerate(prices):
            store.db.execute(
                """INSERT INTO observations(
                       timestamp, topic_id, market_id, title, start_price, spot_price,
                       spot_age_ms, seconds_left, up_ask, up_bid, down_ask, down_bid,
                       up_ask_size, up_bid_size, down_ask_size, down_bid_size,
                       book_skew_ms, book_age_ms
                   ) VALUES (?, 1, ?, 'test', 100.0, ?, 10.0, ?, .20, .19, .80, .79,
                             100, 100, 100, 100, 10, 10)""",
                ((now + timedelta(seconds=index)).isoformat(), market_id, price, seconds_left),
            )
        store.db.commit()


def test_store_creates_allow_shadow_and_records_block(tmp_path):
    store = server.Store(tmp_path / "simulation.db")

    insert_observations(store, 101, [99.99, 99.98, 99.96])
    store.open_trade(
        strategy="M01",
        topic_id=1,
        market_id=101,
        side="UP",
        entry=.20,
        target=None,
        stake=10.0,
        fee_rate_bps=200,
        note="source",
    )
    blocked = store.db.execute(
        "SELECT * FROM strong_trend_guard_decisions WHERE market_id=101"
    ).fetchone()
    assert blocked["decision"] == "BLOCK_STRONG_OPPOSING_TREND"
    assert blocked["shadow_trade_id"] is None

    insert_observations(store, 102, [99.99, 100.00, 100.04])
    store.open_trade(
        strategy="M01",
        topic_id=1,
        market_id=102,
        side="UP",
        entry=.20,
        target=None,
        stake=10.0,
        fee_rate_bps=200,
        note="source",
    )
    allowed = store.db.execute(
        "SELECT * FROM strong_trend_guard_decisions WHERE market_id=102"
    ).fetchone()
    assert allowed["decision"] == "ALLOW"
    shadow = store.db.execute(
        "SELECT * FROM trades WHERE strategy='R_STRONG_TREND_GUARD_M01' AND market_id=102"
    ).fetchone()
    assert shadow is not None
    assert shadow["side"] == "UP"
    assert shadow["entry_price"] == .20
    assert shadow["stake"] == 5.0


def test_eight_strategies_are_derived_paper_shadows():
    assert len(SOURCE_TO_SHADOW) == 8
    assert len(STRATEGIES) == 8
    assert not (set(STRATEGIES) & set(GENERIC_SIGNAL_RESEARCH_STRATEGIES))
    for strategy in STRATEGIES:
        assert server.DEFAULT_CONFIG[f"strategy_{strategy.lower()}_enabled"] is True
        assert server.DEFAULT_CONFIG[f"strategy_{strategy.lower()}_stake"] == 5.0
