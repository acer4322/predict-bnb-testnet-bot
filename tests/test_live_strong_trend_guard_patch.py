from __future__ import annotations

from datetime import datetime, timedelta, timezone

from predict_bot import server
from predict_bot.live_strong_trend_guard_patch import (
    LIVE_GUARD_STRATEGIES,
    live_guard_candidates_for_opened,
)
from predict_bot.live_trading import (
    LIVE_RESEARCH_STRATEGIES,
    LIVE_SUPPORTED_STRATEGIES,
)


def insert_observations(
    store,
    market_id: int,
    prices: list[float],
    *,
    seconds_left: float = 250.0,
) -> None:
    now = datetime.now(timezone.utc) - timedelta(seconds=10)
    with store.lock:
        for index, price in enumerate(prices):
            store.db.execute(
                """INSERT INTO observations(
                       timestamp, topic_id, market_id, title, start_price,
                       spot_price, spot_age_ms, seconds_left, up_ask, up_bid,
                       down_ask, down_bid, up_ask_size, up_bid_size,
                       down_ask_size, down_bid_size, book_skew_ms, book_age_ms
                   ) VALUES (?, 1, ?, 'test', 100.0, ?, 10.0, ?,
                             .40, .39, .60, .59, 100, 100, 100, 100, 10, 10)""",
                (
                    (now + timedelta(seconds=index)).isoformat(),
                    market_id,
                    price,
                    seconds_left,
                ),
            )
        store.db.commit()


def raw_candidate(strategy: str, market_id: int, side: str) -> dict[str, object]:
    return {
        "strategy": strategy,
        "topic_id": 1,
        "market_id": market_id,
        "side": side,
        "entry_price": .40,
        "paper_only": True,
    }


def test_requested_strategies_are_live_selectable_once():
    requested = {
        "R_FUTURES_LEAD_REGIME_REVERSE_3L",
        "R_STRONG_TREND_GUARD_FUTURES_LEAD",
        "R_STRONG_TREND_GUARD_CONSENSUS",
    }
    assert requested <= set(LIVE_SUPPORTED_STRATEGIES)
    assert requested <= set(LIVE_RESEARCH_STRATEGIES)
    for strategy in requested:
        assert LIVE_SUPPORTED_STRATEGIES.count(strategy) == 1
    assert set(LIVE_GUARD_STRATEGIES) <= requested


def test_store_m_series_path_has_live_guard_bridge_installed():
    assert getattr(server.Store.maybe_enter_m_series, "_live_guard_bridge_v1", False)


def test_allowed_futures_lead_guard_creates_live_queue_candidate(tmp_path):
    store = server.Store(tmp_path / "simulation.db")
    insert_observations(store, 301, [99.99, 99.98, 99.96])
    store.open_trade(
        strategy="R_FUTURES_LEAD",
        topic_id=1,
        market_id=301,
        side="DOWN",
        entry=.40,
        target=None,
        stake=10.0,
        fee_rate_bps=200,
        note="allowed source",
    )

    candidates = live_guard_candidates_for_opened(
        store,
        [raw_candidate("R_FUTURES_LEAD", 301, "DOWN")],
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["strategy"] == "R_STRONG_TREND_GUARD_FUTURES_LEAD"
    assert candidate["side"] == "DOWN"
    assert candidate["entry_price"] == .40
    assert candidate["paper_only"] is True
    assert candidate["live_orders_affected"] is False
    assert candidate["live_guard_bridge"] is True
    assert int(candidate["shadow_trade_id"]) > 0


def test_blocked_consensus_guard_fails_closed_without_live_candidate(tmp_path):
    store = server.Store(tmp_path / "simulation.db")
    insert_observations(store, 302, [99.99, 99.98, 99.96])
    store.open_trade(
        strategy="R_CONSENSUS",
        topic_id=1,
        market_id=302,
        side="UP",
        entry=.40,
        target=None,
        stake=10.0,
        fee_rate_bps=200,
        note="blocked source",
    )

    candidates = live_guard_candidates_for_opened(
        store,
        [raw_candidate("R_CONSENSUS", 302, "UP")],
    )

    assert candidates == []
