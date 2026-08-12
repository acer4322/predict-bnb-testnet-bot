from __future__ import annotations

from predict_bot import predict_wallet_shadow_observer as base
from predict_bot.predict_wallet_shadow_observer_v4 import (
    V1_MAX_EVENT_SHARES,
    V1_MAX_NET_SHARES,
    WalletShadowObserver,
    taker_v1_similarity,
    v1_size_after_limits,
)


def test_v1_size_is_small_and_net_capped() -> None:
    assert v1_size_after_limits(current_delta=0, current_total=0, side="UP", requested=100) == V1_MAX_EVENT_SHARES
    remaining = v1_size_after_limits(
        current_delta=V1_MAX_NET_SHARES - 3,
        current_total=100,
        side="UP",
        requested=12,
    )
    assert remaining == 3
    reducing = v1_size_after_limits(
        current_delta=V1_MAX_NET_SHARES,
        current_total=100,
        side="DOWN",
        requested=12,
    )
    assert reducing == 12


def test_v1_maker_follow_emits_small_same_side_taker(tmp_path) -> None:
    observer = WalletShadowObserver(tmp_path / "v1.db")
    observer.market_id = 123
    observer._register_v1_market(123)
    maker = base.ShadowEvent(
        id="maker-fill",
        market_id=123,
        at_ms=base._now_ms(),
        event_type="MAKER_FILL_PROXY",
        role="MAKER",
        side="UP",
        price=0.50,
        shares=18.0,
        inference="INFERRED_FILL",
        reason="test",
        core_side="UP",
        core_source="test",
        maker_up_shares=18.0,
        maker_down_shares=0.0,
        taker_up_shares=0.0,
        taker_down_shares=0.0,
    )
    observer._advance_taker_v1(
        [maker],
        {"upAsk": 0.51, "downAsk": 0.49},
        {"side": "UP", "source": "test"},
    )
    assert len(observer.taker_v1_events) == 1
    event = observer.taker_v1_events[0]
    assert event["side"] == "UP"
    assert event["trigger"] == "MAKER_FOLLOW"
    assert 0 < event["shares"] <= 18
    observer.stop()


def test_v1_core_flip_can_trade_opposite_side(tmp_path) -> None:
    observer = WalletShadowObserver(tmp_path / "flip.db")
    observer.market_id = 456
    observer._register_v1_market(456)
    observer._advance_taker_v1([], {"upAsk": 0.55, "downAsk": 0.45}, {"side": "UP", "source": "test"})
    assert not observer.taker_v1_events
    observer._advance_taker_v1([], {"upAsk": 0.45, "downAsk": 0.55}, {"side": "DOWN", "source": "test"})
    assert len(observer.taker_v1_events) == 1
    assert observer.taker_v1_events[0]["side"] == "DOWN"
    assert observer.taker_v1_events[0]["trigger"] == "CORE_FLIP"
    observer.stop()


def test_v1_market_window_excludes_pre_v1_maker_history(tmp_path) -> None:
    observer = WalletShadowObserver(tmp_path / "causal.db")
    market_id = 789
    start = base._now_ms()
    observer.market_id = market_id
    with observer.db_lock:
        observer.db.execute(
            "INSERT OR REPLACE INTO wallet_shadow_taker_v1_markets(wallet,market_id,started_at_ms) VALUES (?,?,?)",
            (observer.wallet, market_id, start),
        )
        for event_id, at_ms in (("old", start - 1000), ("new", start + 1000)):
            observer.db.execute(
                """INSERT INTO wallet_shadow_events(
                    id,wallet,market_id,at_ms,event_type,role,side,price,shares,
                    core_side,core_source,reason,payload_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (event_id, observer.wallet, market_id, at_ms, "MAKER_FILL_PROXY", "MAKER", "UP", 0.4, 18, "UP", "test", "test", "{}"),
            )
        observer.db.execute(
            """INSERT INTO wallet_shadow_taker_v1_events(
                id,wallet,market_id,at_ms,side,price,shares,trigger,core_side,reason,payload_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            ("v1", observer.wallet, market_id, start + 1500, "DOWN", 0.6, 12, "CORE_FLIP", "DOWN", "test", "{}"),
        )
        observer.db.commit()
    events = observer._market_v1_events(market_id)
    assert [event["event_type"] for event in events] == ["MAKER_FILL_PROXY", "TAKER_INTENT"]
    observer.stop()


def test_v1_similarity_measures_taker_only() -> None:
    target = [
        base.ParentEvent(
            id="TAKER:a",
            role="TAKER",
            market_id=1,
            side="UP",
            quote_type="BID",
            order_hash="a",
            first_event_ms=10_000,
            last_event_ms=10_000,
            average_price=0.5,
            shares=12,
            fill_legs=1,
        )
    ]
    result = taker_v1_similarity(
        target,
        [{"eventType": "TAKER_V1_INTENT", "atMs": 10_500, "side": "UP", "shares": 12}],
    )
    assert result["eventCountRatioV1ToTarget"] == 1
    assert result["sideMatchWithin5s"] == 1
    assert result["sameSideTimingWithin3s"] == 1
    assert result["residualSideMatch"] is True
