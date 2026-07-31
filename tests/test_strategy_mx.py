import hashlib
from pathlib import Path

import pytest

from predict_bot.server import (
    M0X_STRATEGIES,
    MX_ALL_STRATEGIES,
    MX_ENTRY_LIMIT,
    MX_STRATEGIES,
    Store,
    taker_fee,
)


def snapshot(market_id: int, *, elapsed: float = 1.0, **overrides):
    value = {
        "timestamp": f"2026-07-18T00:00:{elapsed:06.3f}+00:00",
        "topic_id": 90_000 + market_id,
        "market_id": market_id,
        "title": "BTC Up or Down 5m",
        "start_price": 100.0,
        "spot_price": 100.02,
        "seconds_left": 300.0 - elapsed,
        "up_ask": 0.40,
        "up_bid": 0.39,
        "down_ask": 0.61,
        "down_bid": 0.60,
        "up_ask_size": 100.0,
        "up_bid_size": 100.0,
        "down_ask_size": 100.0,
        "down_bid_size": 100.0,
        "book_skew_ms": 0.0,
        "book_age_ms": 0.0,
    }
    value.update(overrides)
    return value


def realtime(source: str, sequence: int, monotonic_ns: int, *, execute=False):
    context = {
        "signal_event_type": source,
        "trigger_source": source,
        "signal_event_sequence": f"{source}:{sequence}",
        "received_wall_ns": 1_800_000_000_000_000_000 + monotonic_ns,
        "received_monotonic_ns": monotonic_ns,
        "execution_eligible": execute,
    }
    if source == "prediction":
        context.update(
            {
                "prediction_book_received_wall_ns": context["received_wall_ns"],
                "prediction_book_received_monotonic_ns": monotonic_ns,
                "prediction_book_age_ms": 0.0,
            }
        )
    return context


def position(store: Store, strategy: str, market_id: int):
    return store.db.execute(
        """SELECT * FROM strategy_mx_positions
           WHERE strategy=? AND market_id=?""",
        (strategy, market_id),
    ).fetchone()


def order(store: Store, strategy: str, market_id: int, action: str, stage: str):
    return store.db.execute(
        """SELECT * FROM strategy_mx_orders
           WHERE strategy=? AND market_id=? AND action=? AND stage_key=?""",
        (strategy, market_id, action, stage),
    ).fetchone()


def freeze_up_signal(
    store: Store,
    market_id: int,
    *,
    monotonic_ns: int = 1_000,
    enable_variants: bool = True,
):
    store.update_config({
        "strategy_mx_enabled": True,
        "strategy_m0x_enabled": True,
        **({
            f"strategy_{strategy.lower()}_enabled": True
            for strategy in MX_ALL_STRATEGIES
            if f"strategy_{strategy.lower()}_enabled" in store.config()
        } if enable_variants else {}),
    })
    store.process_mx_event(
        snapshot(market_id),
        200,
        realtime_context=realtime("spot", 1, monotonic_ns),
    )


def prediction(
    store: Store,
    market_id: int,
    sequence: int,
    monotonic_ns: int,
    *,
    elapsed: float = 2.0,
    **overrides,
):
    store.process_mx_event(
        snapshot(market_id, elapsed=elapsed, **overrides),
        200,
        realtime_context=realtime(
            "prediction", sequence, monotonic_ns, execute=True
        ),
    )


def test_mx_freezes_one_common_signal_and_accumulates_entry_top_size(
    tmp_path: Path,
):
    store = Store(tmp_path / "mx.db")
    market_id = 10_001
    freeze_up_signal(store, market_id)

    rows = store.db.execute(
        "SELECT * FROM strategy_mx_positions WHERE market_id=?",
        (market_id,),
    ).fetchall()
    assert {row["strategy"] for row in rows} == set(MX_ALL_STRATEGIES)
    assert {row["side"] for row in rows if row["strategy"] in MX_STRATEGIES} == {"UP"}
    assert all(row["requested_entry_shares"] == pytest.approx(20.0) for row in rows)

    # A cached book from before the Spot signal is causally ineligible.
    prediction(store, market_id, 1, 999, up_ask_size=100.0)
    assert position(store, "MX_T60", market_id)["filled_entry_shares"] == 0

    prediction(store, market_id, 2, 2_000, up_ask=0.40, up_ask_size=5.0)
    assert position(store, "MX_T60", market_id)["filled_entry_shares"] == pytest.approx(5.0)
    # Event replay is idempotent at the persistent fill ledger.
    prediction(store, market_id, 2, 2_000, up_ask=0.40, up_ask_size=5.0)
    assert position(store, "MX_T60", market_id)["filled_entry_shares"] == pytest.approx(5.0)

    prediction(store, market_id, 3, 3_000, up_ask=0.45, up_ask_size=30.0)
    for strategy in MX_STRATEGIES:
        row = position(store, strategy, market_id)
        assert row["filled_entry_shares"] == pytest.approx(20.0)
        assert row["remaining_shares"] == pytest.approx(20.0)
        assert row["reference_entry_price"] == pytest.approx(0.4375)
        assert order(store, strategy, market_id, "ENTRY", "ENTRY")["status"] == "FILLED"
    p50 = order(store, "MX_P50", market_id, "EXIT", "P50_150")
    assert p50["limit_price"] == pytest.approx(0.4375 * 1.5)
    assert p50["requested_shares"] == pytest.approx(10.0)


def test_mx_disabled_variants_do_not_create_new_intents(tmp_path: Path):
    store = Store(tmp_path / "mx-disabled.db")
    store.update_config(
        {
            "strategy_mx_t60_enabled": False,
            "strategy_mx_t80_enabled": False,
            "strategy_mx_t98_enabled": False,
        }
    )
    market_id = 10_099

    freeze_up_signal(store, market_id, enable_variants=False)

    rows = store.db.execute(
        "SELECT strategy FROM strategy_mx_positions WHERE market_id=?",
        (market_id,),
    ).fetchall()
    created = {str(row["strategy"]) for row in rows}
    assert not {"MX_T60", "MX_T80", "MX_T98"}.intersection(created)
    assert created == (set(MX_ALL_STRATEGIES) - {"MX_T60", "MX_T80", "MX_T98"})
    assert store.config()["strategy_mx_t60_enabled"] is False


def test_m_and_m0_exit_families_are_paused_by_default(tmp_path: Path):
    store = Store(tmp_path / "mx-master-paused.db")
    assert store.config()["strategy_mx_enabled"] is False
    assert store.config()["strategy_m0x_enabled"] is False
    store.process_mx_event(
        snapshot(10_098),
        200,
        realtime_context=realtime("spot", 1, 1_000),
    )
    assert store.db.execute(
        "SELECT COUNT(*) FROM strategy_mx_positions WHERE market_id=10098"
    ).fetchone()[0] == 0


def test_mx_target_exit_uses_visible_bid_size_across_events(tmp_path: Path):
    store = Store(tmp_path / "mx.db")
    market_id = 10_002
    freeze_up_signal(store, market_id)
    prediction(store, market_id, 1, 2_000, up_ask=0.40, up_ask_size=20.0)

    prediction(
        store,
        market_id,
        2,
        3_000,
        elapsed=3.0,
        up_ask=0.61,
        up_bid=0.60,
        up_bid_size=7.0,
    )
    row = position(store, "MX_T60", market_id)
    assert row["filled_exit_shares"] == pytest.approx(7.0)
    assert row["remaining_shares"] == pytest.approx(13.0)
    target = order(store, "MX_T60", market_id, "EXIT", "TARGET")
    assert target["status"] == "PARTIAL"

    prediction(
        store,
        market_id,
        3,
        4_000,
        elapsed=4.0,
        up_ask=0.62,
        up_bid=0.61,
        up_bid_size=50.0,
    )
    row = position(store, "MX_T60", market_id)
    assert row["filled_exit_shares"] == pytest.approx(20.0)
    assert row["remaining_shares"] == pytest.approx(0.0)
    assert row["status"] == "CLOSED_TARGET"
    assert order(store, "MX_T60", market_id, "EXIT", "TARGET")["status"] == "FILLED"
    summary = store.mx_state()["summaries"]["MX_T60"]
    assert summary["trades"] == 1
    assert summary["wins"] == 1
    assert summary["losses"] == 0


def test_mx_partial_ladders_share_one_visible_bid_quantity(tmp_path: Path):
    store = Store(tmp_path / "mx.db")
    market_id = 10_003
    freeze_up_signal(store, market_id)
    prediction(store, market_id, 1, 2_000, up_ask=0.40, up_ask_size=20.0)
    prediction(
        store,
        market_id,
        2,
        3_000,
        elapsed=3.0,
        up_ask=0.61,
        up_bid=0.60,
        up_bid_size=7.0,
    )
    p10_fills = store.db.execute(
        """SELECT * FROM strategy_mx_fills
           WHERE strategy='MX_P10' AND market_id=? AND action='EXIT'
           ORDER BY id ASC""",
        (market_id,),
    ).fetchall()
    assert sum(float(row["shares"]) for row in p10_fills) == pytest.approx(7.0)
    assert [row["stage_key"] for row in p10_fills] == [
        "P10_110", "P10_120", "P10_130", "P10_140"
    ]
    assert [float(row["shares"]) for row in p10_fills] == pytest.approx(
        [2.0, 2.0, 2.0, 1.0]
    )


def test_mx_reversal_cancels_entry_and_drains_over_later_books(tmp_path: Path):
    store = Store(tmp_path / "mx.db")
    market_id = 10_004
    freeze_up_signal(store, market_id)
    prediction(store, market_id, 1, 2_000, up_ask=0.40, up_ask_size=8.0)

    store.process_mx_event(
        snapshot(market_id, elapsed=4.0, spot_price=100.0),
        200,
        realtime_context=realtime("spot", 2, 4_000),
    )
    entry = order(store, "MX_REV", market_id, "ENTRY", "ENTRY")
    assert entry["status"] == "CANCELLED_PARTIAL"
    assert position(store, "MX_REV", market_id)["reverse_triggered"] == 1

    prediction(
        store,
        market_id,
        3,
        5_000,
        elapsed=5.0,
        up_ask=0.50,
        up_bid=0.48,
        up_ask_size=100.0,
        up_bid_size=3.0,
    )
    assert position(store, "MX_REV", market_id)["remaining_shares"] == pytest.approx(5.0)
    prediction(
        store,
        market_id,
        4,
        6_000,
        elapsed=6.0,
        up_ask=0.50,
        up_bid=0.47,
        up_ask_size=100.0,
        up_bid_size=10.0,
    )
    row = position(store, "MX_REV", market_id)
    assert row["remaining_shares"] == pytest.approx(0.0)
    assert row["filled_entry_shares"] == pytest.approx(8.0)
    assert row["filled_exit_shares"] == pytest.approx(8.0)
    assert row["status"] == "CLOSED_REVERSAL"


def test_mx_window_expiry_ratios_and_api_contract(tmp_path: Path):
    store = Store(tmp_path / "mx.db")
    freeze_up_signal(store, 10_005)
    # No eligible ask before expiry.
    prediction(
        store, 10_005, 1, 2_000, up_ask=0.55, up_bid=0.54,
    )
    store.process_mx_event(
        snapshot(10_005, elapsed=10.1),
        200,
        realtime_context=realtime("spot", 2, 11_000),
    )

    freeze_up_signal(store, 10_006, monotonic_ns=20_000)
    prediction(
        store, 10_006, 3, 21_000, up_ask=0.40, up_ask_size=5.0,
    )
    store.process_mx_event(
        snapshot(10_006, elapsed=10.1),
        200,
        realtime_context=realtime("spot", 4, 31_000),
    )

    state = store.mx_state()
    assert state["strategyIds"] == list(MX_STRATEGIES)
    assert state["contractVersion"] == 2
    assert state["status"] == "LIVE"
    summary = state["strategies"]["MX_T60"]
    assert summary["requestedEntryShares"] == pytest.approx(40.0)
    assert summary["filledEntryShares"] == pytest.approx(5.0)
    assert summary["entryFillRatio"] == pytest.approx(0.125)
    assert summary["fullyUnfilledIntentRatio"] == pytest.approx(0.5)
    assert summary["partialFillIntentRatio"] == pytest.approx(0.5)
    assert summary["requestedExitShares"] == pytest.approx(5.0)
    assert summary["remainingShares"] == pytest.approx(5.0)
    assert summary["recentPositions"][0]["experimentId"] == "MX_T60"
    assert summary["recentOrders"][0]["remainingQty"] >= 0
    assert summary["recentFills"][0]["qty"] == pytest.approx(5.0)
    card = state["summaries"]["MX_T60"]
    assert card["requestedEntryQty"] == pytest.approx(40.0)
    assert card["filledEntryQty"] == pytest.approx(5.0)
    assert card["openPositions"] == summary["open"]
    assert card["trades"] == 1
    assert card["wins"] == 0
    assert card["losses"] == 0
    assert state["positions"][0]["experimentId"] == "MX_T60"
    assert state["orders"][0]["createdAt"] is not None
    assert state["fills"][0]["timestamp"] is not None


def test_mx_residual_waits_for_official_settlement(tmp_path: Path):
    store = Store(tmp_path / "mx.db")
    market_id = 10_007
    freeze_up_signal(store, market_id)
    prediction(store, market_id, 1, 2_000, up_ask=0.40, up_ask_size=20.0)

    store.settle_market(
        market_id,
        "DOWN",
        official=False,
        topic_id=90_000 + market_id,
        start_price=100.0,
    )
    assert position(store, "MX_T70", market_id)["remaining_shares"] == pytest.approx(20.0)

    store.settle_market(
        market_id,
        "UP",
        official=True,
        topic_id=90_000 + market_id,
        start_price=100.0,
        end_price=101.0,
    )
    row = position(store, "MX_T70", market_id)
    assert row["status"] == "SETTLED_WIN"
    assert row["remaining_shares"] == pytest.approx(0.0)
    assert row["filled_exit_shares"] == pytest.approx(20.0)
    expected = 20.0 - 20.0 * 0.40 - taker_fee(20.0, 0.40, 200)
    assert row["realized_pnl"] == pytest.approx(expected)
    settlement = order(store, "MX_T70", market_id, "EXIT", "SETTLEMENT")
    assert settlement["status"] == "FILLED"
    summary = store.mx_state()["strategies"]["MX_T70"]
    assert summary["requestedExitShares"] == pytest.approx(20.0)
    assert summary["filledExitShares"] == pytest.approx(0.0)
    assert summary["settlementExitShares"] == pytest.approx(20.0)
    assert summary["exitFillRatio"] == pytest.approx(0.0)
    card = store.mx_state()["summaries"]["MX_T70"]
    assert card["trades"] == 1
    assert card["wins"] == 1
    assert card["losses"] == 0


def test_mx_closed_settlement_loss_is_counted(tmp_path: Path):
    store = Store(tmp_path / "mx.db")
    market_id = 10_011
    freeze_up_signal(store, market_id)
    prediction(
        store,
        market_id,
        1,
        2_000,
        up_ask=0.40,
        up_ask_size=20.0,
    )
    store.settle_market(
        market_id,
        "DOWN",
        official=True,
        topic_id=90_000 + market_id,
        start_price=100.0,
        end_price=99.0,
    )
    card = store.mx_state()["summaries"]["MX_T70"]
    assert card["trades"] == 1
    assert card["wins"] == 0
    assert card["losses"] == 1


def test_mx_bid_stop_arms_above_half_then_triggers_on_down_cross(tmp_path: Path):
    store = Store(tmp_path / "mx.db")
    market_id = 10_008
    freeze_up_signal(store, market_id)
    prediction(
        store,
        market_id,
        1,
        2_000,
        up_ask=0.40,
        up_bid=0.39,
        up_ask_size=20.0,
        up_bid_size=20.0,
    )
    first = position(store, "MX_REV", market_id)
    assert first["filled_entry_shares"] == pytest.approx(20.0)
    assert first["reverse_triggered"] == 0
    assert first["rev_bid_armed"] == 0

    prediction(
        store,
        market_id,
        2,
        3_000,
        elapsed=3.0,
        up_ask=0.56,
        up_bid=0.55,
        up_bid_size=20.0,
    )
    assert position(store, "MX_REV", market_id)["rev_bid_armed"] == 1
    prediction(
        store,
        market_id,
        3,
        4_000,
        elapsed=4.0,
        up_ask=0.51,
        up_bid=0.50,
        up_bid_size=20.0,
    )
    row = position(store, "MX_REV", market_id)
    assert row["status"] == "CLOSED_REVERSAL"
    assert row["reverse_triggered"] == 1
    assert row["filled_exit_shares"] == pytest.approx(20.0)


def test_mx_restart_replay_is_idempotent(tmp_path: Path):
    path = tmp_path / "mx.db"
    market_id = 10_009
    store = Store(path)
    freeze_up_signal(store, market_id)
    prediction(store, market_id, 1, 2_000, up_ask=0.40, up_ask_size=5.0)
    before = store.db.execute(
        "SELECT COUNT(*) FROM strategy_mx_fills WHERE market_id=?",
        (market_id,),
    ).fetchone()[0]
    store.db.close()

    reopened = Store(path)
    prediction(reopened, market_id, 1, 2_000, up_ask=0.40, up_ask_size=5.0)
    after = reopened.db.execute(
        "SELECT COUNT(*) FROM strategy_mx_fills WHERE market_id=?",
        (market_id,),
    ).fetchone()[0]
    assert after == before
    assert len(MX_STRATEGIES) <= before <= len(MX_ALL_STRATEGIES)
    assert position(reopened, "MX_T60", market_id)["filled_entry_shares"] == pytest.approx(5.0)


def test_mx_late_start_quiets_spot_path_without_creating_intents(tmp_path: Path):
    store = Store(tmp_path / "mx.db")
    market_id = 10_010
    store.process_mx_event(
        snapshot(market_id, elapsed=20.0),
        200,
        realtime_context=realtime("spot", 1, 20_000),
    )
    assert market_id in store._mx_spot_quiet_markets
    assert store.db.execute(
        "SELECT COUNT(*) FROM strategy_mx_positions WHERE market_id=?",
        (market_id,),
    ).fetchone()[0] == 0


def test_m0x_uses_seeded_m0_direction_with_same_quantity_and_separate_state(
    tmp_path: Path,
):
    store = Store(tmp_path / "mx.db")
    market_id = 10_011
    freeze_up_signal(store, market_id)

    seed = int(store.config()["strategy_m0_seed"])
    digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
    expected_side = "UP" if digest[0] < 128 else "DOWN"
    m0_rows = store.db.execute(
        """SELECT * FROM strategy_mx_positions
           WHERE market_id=? AND strategy LIKE 'M0X_%'""",
        (market_id,),
    ).fetchall()
    assert {row["strategy"] for row in m0_rows} == set(M0X_STRATEGIES)
    assert {row["side"] for row in m0_rows} == {expected_side}
    assert all(row["requested_entry_shares"] == pytest.approx(20.0) for row in m0_rows)
    assert position(store, "MX_T60", market_id)["requested_entry_shares"] == pytest.approx(
        position(store, "M0X_T60", market_id)["requested_entry_shares"]
    )

    prediction(
        store,
        market_id,
        2,
        2_000,
        up_ask=0.40,
        up_bid=0.39,
        down_ask=0.40,
        down_bid=0.39,
        up_ask_size=20.0,
        down_ask_size=20.0,
    )
    assert position(store, "M0X_T60", market_id)["filled_entry_shares"] == pytest.approx(20.0)
    assert order(store, "M0X_P50", market_id, "EXIT", "P50_150")["requested_shares"] == pytest.approx(10.0)

    state = store.mx_state(M0X_STRATEGIES, signal_family="M0")
    assert state["signalFamily"] == "M0"
    assert state["strategyIds"] == list(M0X_STRATEGIES)
    assert set(state["summaries"]) == set(M0X_STRATEGIES)
    assert not set(state["summaries"]).intersection(MX_STRATEGIES)


def test_m0x_reversal_waits_until_random_side_has_been_reached(tmp_path: Path):
    store = Store(tmp_path / "mx.db")
    seed = int(store.config()["strategy_m0_seed"])
    market_id = next(
        candidate
        for candidate in range(10_100, 10_200)
        if hashlib.sha256(f"M0:{seed}:{candidate}".encode()).digest()[0] >= 128
    )
    freeze_up_signal(store, market_id)
    row = position(store, "M0X_REV", market_id)
    assert row["side"] == "DOWN"
    assert row["rev_spot_armed"] == 0

    # Remaining above startPrice is not a crossing for a random DOWN signal.
    store.process_mx_event(
        snapshot(market_id, elapsed=2.0, spot_price=100.03),
        200,
        realtime_context=realtime("spot", 2, 2_000),
    )
    assert position(store, "M0X_REV", market_id)["reverse_triggered"] == 0

    # Reach the chosen DOWN side first, then a later cross back above can exit.
    store.process_mx_event(
        snapshot(market_id, elapsed=3.0, spot_price=99.99),
        200,
        realtime_context=realtime("spot", 3, 3_000),
    )
    assert position(store, "M0X_REV", market_id)["rev_spot_armed"] == 1
    store.process_mx_event(
        snapshot(market_id, elapsed=4.0, spot_price=100.01),
        200,
        realtime_context=realtime("spot", 4, 4_000),
    )
    assert position(store, "M0X_REV", market_id)["reverse_triggered"] == 1
