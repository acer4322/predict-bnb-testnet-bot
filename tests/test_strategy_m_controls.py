import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from predict_bot.server import M7_DELAYS, Store


def snapshot(market_id: int, **overrides):
    value = {
        "timestamp": "2026-07-17T00:00:00+00:00",
        "topic_id": 80_000 + market_id,
        "market_id": market_id,
        "title": "BTC Up or Down 5m",
        "start_price": 100.0,
        "spot_price": 100.02,
        "futures_price": 99.98,
        "futures_timestamp_ms": 1_700_000_000_000,
        "futures_age_ms": 3.0,
        "futures_agg_trade_id": 1234,
        "seconds_left": 299.0,
        "up_ask": 0.55,
        "up_bid": 0.54,
        "down_ask": 0.46,
        "down_bid": 0.45,
        "up_ask_size": 100.0,
        "up_bid_size": 100.0,
        "down_ask_size": 100.0,
        "down_bid_size": 100.0,
        "book_skew_ms": 0.0,
        "book_age_ms": 0.0,
    }
    value.update(overrides)
    return value


def realtime(event_type: str, sequence: int, monotonic_ns: int, *, execute=False):
    context = {
        "signal_event_type": event_type,
        "trigger_source": event_type,
        "signal_event_sequence": sequence,
        "exchange_event_ms": 1_700_000_000_000 + sequence,
        "received_wall_ns": 1_800_000_000_000_000_000 + monotonic_ns,
        "received_monotonic_ns": monotonic_ns,
        "execution_eligible": execute,
    }
    if event_type == "prediction":
        context.update(
            {
                "prediction_book_received_wall_ns": context["received_wall_ns"],
                "prediction_book_received_monotonic_ns": monotonic_ns,
                "prediction_book_age_ms": 0.0,
            }
        )
    return context


def enable_only(store: Store, *strategies: str):
    enabled = set(strategies)
    patch = {
        "strategy_m_enabled": "M" in enabled,
        "strategy_m01_enabled": "M01" in enabled,
        "strategy_m01t180_enabled": "M01T180" in enabled,
        "strategy_m01t180d_enabled": "M01T180D" in enabled,
        "strategy_m01tasym_enabled": "M01TASYM" in enabled,
        "strategy_m01o_enabled": "M01O" in enabled,
        "strategy_m01o_f1_enabled": "M01O_F1" in enabled,
        "strategy_m01o_live_enabled": "M01O_LIVE" in enabled,
        "strategy_m01f_enabled": "M01F" in enabled,
        "strategy_m01r_enabled": "M01R" in enabled,
        "strategy_m0w_enabled": "M0W" in enabled,
        "strategy_m01w_enabled": "M01W" in enabled,
        **{
            f"strategy_m{number}_enabled": f"M{number}" in enabled
            for number in range(7)
        },
        **{
            f"strategy_{name.lower()}_enabled": name in enabled
            for name in M7_DELAYS
        },
    }
    store.update_config(patch)


def trades(store: Store, strategy: str, market_id: int):
    return store.db.execute(
        "SELECT * FROM trades WHERE strategy=? AND market_id=? ORDER BY id",
        (strategy, market_id),
    ).fetchall()


def test_ws_signal_waits_for_first_post_signal_prediction_book(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    enable_only(store, "M", "M2")
    row = snapshot(2001)

    store.maybe_enter_m_series(
        row, 200, realtime_context=realtime("spot", 1, 1_000, execute=False)
    )
    assert trades(store, "M", 2001) == []
    assert trades(store, "M2", 2001) == []

    # A cached book received before the signal cannot be used as a fill.
    store.maybe_enter_m_series(
        row, 200, realtime_context=realtime("prediction", 2, 999, execute=True)
    )
    assert trades(store, "M", 2001) == []

    execution = snapshot(2001, up_ask=0.61, up_bid=0.60)
    store.maybe_enter_m_series(
        execution,
        200,
        realtime_context=realtime("prediction", 3, 2_000, execute=True),
    )
    for strategy in ("M", "M2"):
        trade = trades(store, strategy, 2001)[0]
        assert trade["side"] == "UP"
        assert trade["entry_price"] == pytest.approx(0.61)
        assert "_v2_ws_" in trade["strategy_version"]
        diagnostics = json.loads(trade["diagnostics_json"])
        assert diagnostics["signal_realtime_context"]["trigger_source"] == "spot"
        assert diagnostics["realtime_context"]["trigger_source"] == "prediction"
        assert diagnostics["realtime_context"]["simulated_order_submitted_monotonic_ns"]


def test_m0_is_deterministic_hash_control_and_m1_is_always_up(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    enable_only(store, "M0", "M1")
    market_id = 2002
    context = realtime("prediction", 1, 5_000, execute=True)
    store.maybe_enter_m_series(snapshot(market_id), 200, realtime_context=context)

    seed = int(store.config()["strategy_m0_seed"])
    digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
    assert trades(store, "M0", market_id)[0]["side"] == (
        "UP" if digest[0] < 128 else "DOWN"
    )
    assert trades(store, "M1", market_id)[0]["side"] == "UP"


def test_previous_m0_win_gates_m0w_and_m01w(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    seed = int(store.config()["strategy_m0_seed"])

    # M01 is intentionally disabled: M01W must now use the previous M0 result
    # even when the previous market never produced an M01 trade.
    enable_only(store, "M0")
    previous_market = 2010
    previous_book = snapshot(
        previous_market,
        up_ask=0.30,
        up_bid=0.29,
        down_ask=0.30,
        down_bid=0.29,
    )
    store.maybe_enter_m_series(
        previous_book,
        200,
        realtime_context=realtime("prediction", 1, 1_000, execute=True),
    )
    previous_side = trades(store, "M0", previous_market)[0]["side"]
    assert trades(store, "M01", previous_market) == []
    store.settle_market(previous_market, winner=previous_side, official=True)

    enable_only(store, "M0W", "M01W")
    current_market = 2011
    first_book = snapshot(
        current_market,
        up_ask=0.31,
        up_bid=0.30,
        down_ask=0.31,
        down_bid=0.30,
    )
    opened = store.maybe_enter_m_series(
        first_book,
        200,
        realtime_context=realtime("prediction", 3, 3_000, execute=True),
    )

    digest = hashlib.sha256(f"M0:{seed}:{current_market}".encode()).digest()
    expected_side = "UP" if digest[0] < 128 else "DOWN"
    m0w = trades(store, "M0W", current_market)[0]
    assert [candidate["strategy"] for candidate in opened] == ["M0W"]
    assert opened[0]["entry_price"] == pytest.approx(m0w["entry_price"])
    assert m0w["side"] == expected_side
    assert trades(store, "M01W", current_market) == []
    diagnostics = json.loads(m0w["diagnostics_json"])
    assert diagnostics["previous_m0_market_id"] == previous_market
    assert diagnostics["previous_m0_status"] == "SETTLED_WIN"

    at_limit = snapshot(
        current_market,
        seconds_left=200.0,
        up_ask=0.30,
        up_bid=0.29,
        down_ask=0.30,
        down_bid=0.29,
        up_ask_size=4.0,
        down_ask_size=4.0,
    )
    opened_at_limit = store.maybe_enter_m_series(
        at_limit,
        200,
        realtime_context=realtime("prediction", 4, 4_000, execute=True),
    )
    m01w = trades(store, "M01W", current_market)[0]
    assert m01w["side"] == expected_side
    assert m01w["entry_price"] == pytest.approx(0.30)
    assert m01w["shares"] == pytest.approx(4.0)
    assert [candidate["strategy"] for candidate in opened_at_limit] == ["M01W"]
    assert opened_at_limit[0]["m0w_gate"]["previous_market_id"] == previous_market
    assert opened_at_limit[0]["maximum_entry_price"] == pytest.approx(0.30)
    diagnostics = json.loads(m01w["diagnostics_json"])
    assert diagnostics["previous_m0_market_id"] == previous_market
    assert diagnostics["previous_m0_status"] == "SETTLED_WIN"
    assert "previous_m01_status" not in diagnostics
    assert diagnostics["maximum_entry_price"] == pytest.approx(0.30)


def test_previous_m0_loss_or_unknown_skips_both_win_gated_variants(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    enable_only(store, "M0W", "M01W")
    store.maybe_enter_m_series(
        snapshot(2020, up_ask=0.20, up_bid=0.19, down_ask=0.20, down_bid=0.19),
        200,
        realtime_context=realtime("prediction", 1, 1_000, execute=True),
    )
    assert trades(store, "M0W", 2020) == []
    assert trades(store, "M01W", 2020) == []

    enable_only(store, "M0")
    previous_market = 2021
    store.maybe_enter_m_series(
        snapshot(previous_market),
        200,
        realtime_context=realtime("prediction", 2, 2_000, execute=True),
    )
    previous_side = trades(store, "M0", previous_market)[0]["side"]
    losing_winner = "DOWN" if previous_side == "UP" else "UP"
    store.settle_market(previous_market, winner=losing_winner, official=True)

    enable_only(store, "M0W", "M01W")
    store.maybe_enter_m_series(
        snapshot(2022, up_ask=0.20, up_bid=0.19, down_ask=0.20, down_bid=0.19),
        200,
        realtime_context=realtime("prediction", 3, 3_000, execute=True),
    )
    assert trades(store, "M0W", 2022) == []
    assert trades(store, "M01W", 2022) == []


def test_win_gate_does_not_reuse_an_older_m0_win_when_adjacent_market_has_no_m0(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    enable_only(store, "M0")
    store.maybe_enter_m_series(
        snapshot(2030),
        200,
        realtime_context=realtime("prediction", 1, 1_000, execute=True),
    )
    winning_side = trades(store, "M0", 2030)[0]["side"]
    store.settle_market(2030, winner=winning_side, official=True)

    # The truly adjacent market settled without an M0 trade.
    enable_only(store)
    store.maybe_enter_m_series(
        snapshot(2031),
        200,
        realtime_context=realtime("prediction", 2, 2_000, execute=True),
    )
    store.settle_market(2031, winner="UP", official=True)
    enable_only(store, "M0W", "M01W")
    store.maybe_enter_m_series(
        snapshot(2032, up_ask=0.20, up_bid=0.19, down_ask=0.20, down_bid=0.19),
        200,
        realtime_context=realtime("prediction", 3, 3_000, execute=True),
    )
    assert trades(store, "M0W", 2032) == []
    assert trades(store, "M01W", 2032) == []


def test_m0w_never_reuses_older_win_while_adjacent_m0_is_unresolved_or_lost(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    enable_only(store, "M0")

    older_market = 2033
    store.maybe_enter_m_series(
        snapshot(older_market),
        200,
        realtime_context=realtime("prediction", 1, 1_000, execute=True),
    )
    older_side = trades(store, "M0", older_market)[0]["side"]
    store.settle_market(older_market, winner=older_side, official=True)

    adjacent_market = 2034
    store.maybe_enter_m_series(
        snapshot(adjacent_market),
        200,
        realtime_context=realtime("prediction", 2, 2_000, execute=True),
    )
    adjacent_side = trades(store, "M0", adjacent_market)[0]["side"]

    current_market = 2035
    enable_only(store, "M0W")
    store.maybe_enter_m_series(
        snapshot(current_market),
        200,
        realtime_context=realtime("prediction", 3, 3_000, execute=True),
    )
    assert trades(store, "M0W", current_market) == []

    losing_winner = "DOWN" if adjacent_side == "UP" else "UP"
    store.settle_market(adjacent_market, winner=losing_winner, official=True)
    store.maybe_enter_m_series(
        snapshot(current_market, seconds_left=295.0),
        200,
        realtime_context=realtime("prediction", 4, 4_000, execute=True),
    )
    assert trades(store, "M0W", current_market) == []


def test_m0w_waits_for_the_adjacent_official_win_then_opens_with_auditable_gate(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    enable_only(store, "M0")
    adjacent_market = 2040
    store.maybe_enter_m_series(
        snapshot(adjacent_market),
        200,
        realtime_context=realtime("prediction", 1, 1_000, execute=True),
    )
    adjacent_side = trades(store, "M0", adjacent_market)[0]["side"]

    current_market = 2041
    enable_only(store, "M0W")
    store.maybe_enter_m_series(
        snapshot(current_market),
        200,
        realtime_context=realtime("prediction", 2, 2_000, execute=True),
    )
    assert trades(store, "M0W", current_market) == []

    store.settle_market(adjacent_market, winner=adjacent_side, official=True)
    opened = store.maybe_enter_m_series(
        snapshot(current_market, seconds_left=295.0),
        200,
        realtime_context=realtime("prediction", 3, 3_000, execute=True),
    )

    assert [candidate["strategy"] for candidate in opened] == ["M0W"]
    gate = opened[0]["m0w_gate"]
    assert gate["previous_market_id"] == adjacent_market
    assert gate["previous_m0_status"] == "SETTLED_WIN"
    assert gate["previous_settlement_status"] == "OFFICIAL"
    assert gate["previous_market_is_adjacent"] is True


def test_m0w_recovers_exact_adjacent_schedule_from_observations_after_restart(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    boundary_ms = int(
        datetime(2026, 7, 17, 0, 5, tzinfo=timezone.utc).timestamp() * 1_000
    )
    previous_market = 2050
    previous = snapshot(
        previous_market,
        timestamp="2026-07-17T00:00:01+00:00",
        seconds_left=299.0,
        market_start_ms=boundary_ms - 300_000,
        market_end_ms=boundary_ms,
    )
    store.observe(previous)
    enable_only(store, "M0")
    store.maybe_enter_m_series(
        previous,
        200,
        realtime_context=realtime("prediction", 1, 1_000, execute=True),
    )
    previous_side = trades(store, "M0", previous_market)[0]["side"]
    store.settle_market(previous_market, winner=previous_side, official=True)

    # Simulate the first launch after the market-sequence migration.
    store.db.execute("DELETE FROM strategy_m_market_sequence")
    store.db.commit()

    current_market = 987_654
    enable_only(store, "M0W")
    opened = store.maybe_enter_m_series(
        snapshot(
            current_market,
            timestamp="2026-07-17T00:05:01+00:00",
            seconds_left=299.0,
            market_start_ms=boundary_ms,
            market_end_ms=boundary_ms + 300_000,
        ),
        200,
        realtime_context=realtime("prediction", 2, 2_000, execute=True),
    )

    assert [candidate["strategy"] for candidate in opened] == ["M0W"]
    gate = opened[0]["m0w_gate"]
    assert gate["previous_market_id"] == previous_market
    assert gate["previous_market_start_ms"] == boundary_ms - 300_000
    assert gate["previous_market_end_ms"] == boundary_ms
    assert gate["current_market_start_ms"] == boundary_ms


def test_m01_freezes_m0_direction_and_waits_for_selected_ask_below_030(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    enable_only(store, "M01")
    market_id = 2010
    seed = int(store.config()["strategy_m0_seed"])
    digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
    side = "UP" if digest[0] < 128 else "DOWN"
    ask_key = f"{side.lower()}_ask"
    bid_key = f"{side.lower()}_bid"
    size_key = f"{side.lower()}_ask_size"

    store.maybe_enter_m_series(
        snapshot(market_id),
        200,
        realtime_context=realtime("spot", 1, 1_000),
    )
    frozen = store._get_m_signal("M01", market_id)
    assert frozen is not None
    assert frozen["side"] == side
    assert trades(store, "M01", market_id) == []

    too_high = snapshot(
        market_id,
        seconds_left=200.0,
        **{ask_key: 0.31, bid_key: 0.30, size_key: 5.0},
    )
    store.maybe_enter_m_series(
        too_high,
        200,
        realtime_context=realtime("prediction", 2, 2_000, execute=True),
    )
    assert trades(store, "M01", market_id) == []

    at_limit = snapshot(
        market_id,
        seconds_left=100.0,
        **{ask_key: 0.30, bid_key: 0.29, size_key: 5.0},
    )
    store.maybe_enter_m_series(
        at_limit,
        200,
        realtime_context=realtime("prediction", 3, 3_000, execute=True),
    )
    trade = trades(store, "M01", market_id)[0]
    assert trade["side"] == side
    assert trade["entry_price"] == pytest.approx(0.30)
    assert trade["shares"] == pytest.approx(5.0)
    diagnostics = json.loads(trade["diagnostics_json"])
    assert diagnostics["maximum_entry_price"] == pytest.approx(0.30)
    assert diagnostics["direction_rule"] == (
        "m0_direction_wait_selected_ask_at_or_below_limit"
    )


def test_m01t180_matches_m01_but_strictly_blocks_at_180_seconds_left(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    enable_only(store, "M01", "M01T180")

    def selected_book(market_id: int, seconds_left: float):
        seed = int(store.config()["strategy_m0_seed"])
        digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
        side = "UP" if digest[0] < 128 else "DOWN"
        return snapshot(
            market_id,
            seconds_left=seconds_left,
            **{
                f"{side.lower()}_ask": 0.30,
                f"{side.lower()}_bid": 0.29,
                f"{side.lower()}_ask_size": 5.0,
            },
        )

    early_market = 2110
    store.maybe_enter_m_series(
        snapshot(early_market),
        200,
        realtime_context=realtime("spot", 1, 1_000),
    )
    early_opened = store.maybe_enter_m_series(
        selected_book(early_market, 180.001),
        200,
        realtime_context=realtime("prediction", 2, 2_000, execute=True),
    )
    assert {candidate["strategy"] for candidate in early_opened} == {
        "M01",
        "M01T180",
    }
    baseline = trades(store, "M01", early_market)[0]
    control = trades(store, "M01T180", early_market)[0]
    assert control["side"] == baseline["side"]
    assert control["entry_price"] == baseline["entry_price"]
    control_diagnostics = json.loads(control["diagnostics_json"])
    assert "paper_only" not in control_diagnostics
    assert control_diagnostics["minimum_seconds_left_exclusive"] == 180

    boundary_market = 2111
    store.maybe_enter_m_series(
        snapshot(boundary_market),
        200,
        realtime_context=realtime("spot", 3, 3_000),
    )
    boundary_opened = store.maybe_enter_m_series(
        selected_book(boundary_market, 180.0),
        200,
        realtime_context=realtime("prediction", 4, 4_000, execute=True),
    )
    assert [candidate["strategy"] for candidate in boundary_opened] == ["M01"]
    assert trades(store, "M01T180", boundary_market) == []

    store.maybe_enter_m_series(
        selected_book(boundary_market, 179.999),
        200,
        realtime_context=realtime("prediction", 5, 5_000, execute=True),
    )
    assert trades(store, "M01T180", boundary_market) == []


def test_m01t180d_and_m01tasym_apply_direction_specific_strict_cutoffs(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    enable_only(store, "M01T180D", "M01TASYM")
    seed = int(store.config()["strategy_m0_seed"])

    def side_for(market_id: int) -> str:
        digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
        return "UP" if digest[0] < 128 else "DOWN"

    def market_for(side: str, start: int) -> int:
        return next(value for value in range(start, start + 1000) if side_for(value) == side)

    def selected_book(market_id: int, seconds_left: float):
        side = side_for(market_id)
        return snapshot(
            market_id,
            seconds_left=seconds_left,
            **{
                f"{side.lower()}_ask": 0.30,
                f"{side.lower()}_bid": 0.29,
                f"{side.lower()}_ask_size": 5.0,
            },
        )

    down_market = market_for("DOWN", 3100)
    store.maybe_enter_m_series(
        snapshot(down_market),
        200,
        realtime_context=realtime("spot", 1, 1_000),
    )
    opened = store.maybe_enter_m_series(
        selected_book(down_market, 180.001),
        200,
        realtime_context=realtime("prediction", 2, 2_000, execute=True),
    )
    assert {candidate["strategy"] for candidate in opened} == {
        "M01T180D",
        "M01TASYM",
    }
    assert all(candidate["paper_only"] is True for candidate in opened)

    up_market = market_for("UP", down_market + 1)
    store.maybe_enter_m_series(
        snapshot(up_market),
        200,
        realtime_context=realtime("spot", 3, 3_000),
    )
    opened = store.maybe_enter_m_series(
        selected_book(up_market, 210.001),
        200,
        realtime_context=realtime("prediction", 4, 4_000, execute=True),
    )
    assert [candidate["strategy"] for candidate in opened] == ["M01TASYM"]
    assert trades(store, "M01T180D", up_market) == []

    up_boundary_market = market_for("UP", up_market + 1)
    store.maybe_enter_m_series(
        snapshot(up_boundary_market),
        200,
        realtime_context=realtime("spot", 5, 5_000),
    )
    opened = store.maybe_enter_m_series(
        selected_book(up_boundary_market, 210.0),
        200,
        realtime_context=realtime("prediction", 6, 6_000, execute=True),
    )
    assert opened == []
    assert trades(store, "M01TASYM", up_boundary_market) == []


def test_m01o_matches_m01_but_requires_the_observer_gate(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    enable_only(store, "M01O")
    market_id = 2090
    seed = int(store.config()["strategy_m0_seed"])
    digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
    side = "UP" if digest[0] < 128 else "DOWN"
    ask_key = f"{side.lower()}_ask"
    bid_key = f"{side.lower()}_bid"
    size_key = f"{side.lower()}_ask_size"

    store.maybe_enter_m_series(
        snapshot(market_id),
        200,
        realtime_context=realtime("spot", 1, 1_000),
    )
    assert store._get_m_signal("M01O", market_id)["side"] == side

    at_limit = snapshot(
        market_id,
        seconds_left=100.0,
        **{ask_key: 0.30, bid_key: 0.29, size_key: 5.0},
    )
    store.maybe_enter_m_series(
        at_limit,
        200,
        realtime_context=realtime("prediction", 2, 2_000, execute=True),
    )
    assert trades(store, "M01O", market_id) == []

    context = realtime("prediction", 3, 3_000, execute=True)
    context["m01o_observer_gate"] = {
        "allowed": True,
        "status": "ALLOW",
        "reason": "range evidence",
        "historicalState": "RANGE",
        "historicalSampleCount": 6,
        "currentRangeScore": 2,
        "paperOnly": True,
        "liveOrdersAffected": False,
    }
    opened = store.maybe_enter_m_series(
        at_limit,
        200,
        realtime_context=context,
    )

    trade = trades(store, "M01O", market_id)[0]
    assert trade["side"] == side
    assert trade["entry_price"] == pytest.approx(0.30)
    assert trade["shares"] == pytest.approx(5.0)
    assert opened[0]["strategy"] == "M01O"
    assert opened[0]["paper_only"] is True
    assert opened[0]["market_observer_gate"]["allowed"] is True
    diagnostics = json.loads(trade["diagnostics_json"])
    assert diagnostics["market_observer_gate"]["historicalState"] == "RANGE"
    assert diagnostics["paper_only"] is True
    assert diagnostics["direction_rule"] == (
        "shared_m01_candidate_then_f2_observer_gate"
    )


def test_m01o_profiles_share_direction_price_and_fill_candidate(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    enable_only(store, "M01", "M01O", "M01O_F1", "M01O_LIVE")
    market_id = 2095
    seed = int(store.config()["strategy_m0_seed"])
    digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
    side = "UP" if digest[0] < 128 else "DOWN"
    ask_key = f"{side.lower()}_ask"
    bid_key = f"{side.lower()}_bid"
    size_key = f"{side.lower()}_ask_size"

    store.maybe_enter_m_series(
        snapshot(market_id),
        200,
        realtime_context=realtime("spot", 1, 1_000),
    )
    execution = snapshot(
        market_id,
        seconds_left=200.0,
        **{ask_key: 0.30, bid_key: 0.29, size_key: 5.0},
    )
    context = realtime("prediction", 2, 2_000, execute=True)
    context["m01o_observer_gates"] = {
        "F2": {
            "allowed": False,
            "status": "BLOCK",
            "profile": "F2",
            "blockCategory": "FILTERED",
            "reason": "strict blocked",
        },
        "F1": {
            "allowed": True,
            "status": "ALLOW",
            "profile": "F1",
            "blockCategory": "ALLOW",
            "reason": "relaxed allowed",
        },
        "LIVE": {
            "allowed": True,
            "status": "ALLOW",
            "profile": "LIVE",
            "blockCategory": "ALLOW",
            "reason": "dual touch allowed",
        },
    }

    opened = store.maybe_enter_m_series(
        execution, 200, realtime_context=context
    )

    assert [row["strategy"] for row in opened] == [
        "M01",
        "M01O_F1",
        "M01O_LIVE",
    ]
    opened_trades = [
        trades(store, strategy, market_id)[0]
        for strategy in ("M01", "M01O_F1", "M01O_LIVE")
    ]
    assert {row["side"] for row in opened_trades} == {side}
    assert {float(row["entry_price"]) for row in opened_trades} == {0.30}
    assert len({float(row["shares"]) for row in opened_trades}) == 1
    decisions = store.db.execute(
        """SELECT strategy, profile, last_block_category, opened
           FROM strategy_m01o_gate_decisions
           WHERE market_id=? ORDER BY strategy""",
        (market_id,),
    ).fetchall()
    assert [(row["profile"], row["opened"]) for row in decisions] == [
        ("F2", 0),
        ("F1", 1),
        ("LIVE", 1),
    ]

    context = realtime("prediction", 3, 3_000, execute=True)
    context["m01o_observer_gates"] = {
        **{
            profile: {
                "allowed": True,
                "status": "ALLOW",
                "profile": profile,
                "blockCategory": "ALLOW",
                "reason": "allowed",
            }
            for profile in ("F2", "F1", "LIVE")
        }
    }
    opened = store.maybe_enter_m_series(
        execution, 200, realtime_context=context
    )
    assert [row["strategy"] for row in opened] == ["M01O"]
    strict_decision = store.db.execute(
        """SELECT ever_allowed, opened FROM strategy_m01o_gate_decisions
           WHERE strategy='M01O' AND market_id=?""",
        (market_id,),
    ).fetchone()
    assert dict(strict_decision) == {"ever_allowed": 1, "opened": 1}


@pytest.mark.parametrize(
    "seconds_left, should_open",
    [(30.001, True), (30.0, False), (29.999, False)],
)
def test_m01o_f1_paper_strictly_blocks_at_thirty_seconds_and_below(
    tmp_path: Path, seconds_left: float, should_open: bool,
):
    store = Store(tmp_path / "sim.db")
    enable_only(store, "M01O_F1")
    market_id = 2100
    seed = int(store.config()["strategy_m0_seed"])
    digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
    side = "UP" if digest[0] < 128 else "DOWN"
    ask_key = f"{side.lower()}_ask"
    bid_key = f"{side.lower()}_bid"

    store.maybe_enter_m_series(
        snapshot(market_id),
        200,
        realtime_context=realtime("spot", 1, 1_000),
    )
    context = realtime("prediction", 2, 2_000, execute=True)
    context["m01o_observer_gates"] = {
        "F1": {
            "allowed": True,
            "status": "ALLOW",
            "profile": "F1",
            "blockCategory": "ALLOW",
            "reason": "relaxed allowed",
        }
    }
    opened = store.maybe_enter_m_series(
        snapshot(
            market_id,
            seconds_left=seconds_left,
            **{ask_key: 0.30, bid_key: 0.29},
        ),
        200,
        realtime_context=context,
    )

    assert bool(opened) is should_open
    assert bool(trades(store, "M01O_F1", market_id)) is should_open
    decision = store.db.execute(
        """SELECT last_block_category, opened
           FROM strategy_m01o_gate_decisions
           WHERE strategy='M01O_F1' AND market_id=?""",
        (market_id,),
    ).fetchone()
    assert decision is not None
    if should_open:
        assert dict(decision) == {"last_block_category": "ALLOW", "opened": 1}
    else:
        assert dict(decision) == {
            "last_block_category": "ENTRY_CUTOFF",
            "opened": 0,
        }


def test_m01o_filter_experiment_reports_quality_and_blocked_counterfactuals(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    enable_only(store, "M01", "M01O", "M01O_F1", "M01O_LIVE")
    market_id = 2096
    store.maybe_enter_m_series(
        snapshot(market_id),
        200,
        realtime_context=realtime("spot", 1, 1_000),
    )
    context = realtime("prediction", 2, 2_000, execute=True)
    context["m01o_observer_gates"] = {
        "F2": {
            "allowed": False,
            "profile": "F2",
            "blockCategory": "FILTERED",
            "reason": "strict filtered",
        },
        "F1": {
            "allowed": True,
            "profile": "F1",
            "blockCategory": "ALLOW",
            "reason": "relaxed allowed",
        },
        "LIVE": {
            "allowed": False,
            "profile": "LIVE",
            "blockCategory": "NOT_READY",
            "reason": "waiting for both touches",
        },
    }
    opened = store.maybe_enter_m_series(
        snapshot(
            market_id,
            up_ask=0.30,
            up_bid=0.29,
            down_ask=0.30,
            down_bid=0.29,
        ),
        200,
        realtime_context=context,
    )
    assert [candidate["strategy"] for candidate in opened] == [
        "M01",
        "M01O_F1",
    ]
    store.db.execute(
        "UPDATE trades SET status='SETTLED_LOSS', pnl=-0.31 "
        "WHERE strategy='M01' AND market_id=?",
        (market_id,),
    )
    store.db.execute(
        "UPDATE trades SET status='SETTLED_WIN', pnl=0.69 "
        "WHERE strategy='M01O_F1' AND market_id=?",
        (market_id,),
    )
    store.db.commit()

    state = store.m01o_filter_experiment_state()
    strict = state["groups"]["F2"]
    relaxed = state["groups"]["F1"]
    live = state["groups"]["LIVE"]

    assert state["paperOnly"] is True
    assert state["liveOrdersAffected"] is False
    assert strict["candidateMarkets"] == 1
    assert strict["openedTrades"] == 0
    assert strict["tradeCoverageRate"] == pytest.approx(0.0)
    assert strict["blockedCounterfactualWins"] == 0
    assert strict["blockedCounterfactualLosses"] == 1
    assert strict["blockedCounterfactualPnl"] == pytest.approx(-0.31)
    assert strict["blockCategories"]["FILTERED"] == 1
    assert relaxed["openedTrades"] == 1
    assert relaxed["actualFillRate"] == pytest.approx(1.0)
    assert relaxed["wins"] == 1
    assert relaxed["winRate"] == pytest.approx(1.0)
    assert relaxed["averageEntryPrice"] == pytest.approx(0.30)
    assert relaxed["averagePnl"] == pytest.approx(0.69)
    assert relaxed["profitFactorInfinite"] is True
    assert relaxed["maxDrawdown"] == pytest.approx(0.0)
    assert relaxed["zeroTradeDays"] == 0
    assert live["blockCategories"]["NOT_READY"] == 1


def test_m01_is_unchanged_when_m01o_observer_gate_blocks(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    enable_only(store, "M01", "M01O")
    market_id = 2091
    blocked = realtime("prediction", 1, 1_000, execute=True)
    blocked["m01o_observer_gate"] = {
        "allowed": False,
        "status": "BLOCK",
        "reason": "historical state TREND",
    }
    store.maybe_enter_m_series(
        snapshot(market_id),
        200,
        realtime_context=realtime("spot", 0, 500),
    )

    opened = store.maybe_enter_m_series(
        snapshot(
            market_id,
            up_ask=0.30,
            up_bid=0.29,
            down_ask=0.30,
            down_bid=0.29,
        ),
        200,
        realtime_context=blocked,
    )

    assert [candidate["strategy"] for candidate in opened] == ["M01"]
    assert len(trades(store, "M01", market_id)) == 1
    assert trades(store, "M01O", market_id) == []


def test_m01o_observer_threshold_config_is_validated(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    with pytest.raises(ValueError, match="samples must be between 6 and 20"):
        store.update_config({"strategy_m01o_min_observer_samples": 5})
    with pytest.raises(ValueError, match="range score must be between 1 and 4"):
        store.update_config({"strategy_m01o_min_current_range_score": 5})


def test_m01_floor_waits_for_selected_ask_inside_020_to_030_band(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    enable_only(store, "M01F")
    market_id = 2011
    seed = int(store.config()["strategy_m0_seed"])
    digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
    side = "UP" if digest[0] < 128 else "DOWN"
    ask_key = f"{side.lower()}_ask"
    bid_key = f"{side.lower()}_bid"
    size_key = f"{side.lower()}_ask_size"

    store.maybe_enter_m_series(
        snapshot(market_id),
        200,
        realtime_context=realtime("spot", 1, 1_000),
    )
    frozen = store._get_m_signal("M01F", market_id)
    assert frozen is not None
    assert frozen["side"] == side

    above_band = snapshot(
        market_id,
        seconds_left=200.0,
        **{ask_key: 0.31, bid_key: 0.30, size_key: 5.0},
    )
    store.maybe_enter_m_series(
        above_band,
        200,
        realtime_context=realtime("prediction", 2, 2_000, execute=True),
    )
    assert trades(store, "M01F", market_id) == []

    below_band = snapshot(
        market_id,
        seconds_left=150.0,
        **{ask_key: 0.19, bid_key: 0.18, size_key: 5.0},
    )
    store.maybe_enter_m_series(
        below_band,
        200,
        realtime_context=realtime("prediction", 3, 3_000, execute=True),
    )
    assert trades(store, "M01F", market_id) == []

    at_floor = snapshot(
        market_id,
        seconds_left=100.0,
        **{ask_key: 0.20, bid_key: 0.19, size_key: 5.0},
    )
    opened = store.maybe_enter_m_series(
        at_floor,
        200,
        realtime_context=realtime("prediction", 4, 4_000, execute=True),
    )
    trade = trades(store, "M01F", market_id)[0]
    assert [candidate["strategy"] for candidate in opened] == ["M01F"]
    assert trade["side"] == side
    assert trade["entry_price"] == pytest.approx(0.20)
    assert trade["shares"] == pytest.approx(5.0)
    diagnostics = json.loads(trade["diagnostics_json"])
    assert diagnostics["minimum_entry_price"] == pytest.approx(0.20)
    assert diagnostics["maximum_entry_price"] == pytest.approx(0.30)
    assert diagnostics["direction_rule"] == (
        "m0_direction_wait_selected_ask_inside_floor_band"
    )


@pytest.mark.parametrize(
    ("market_id", "anchor_price", "trigger_price"),
    ((2012, 0.10, 0.20), (2013, 0.30, 0.40)),
)
def test_m01_rebound_buys_after_exact_010_rise_from_low_anchor(
    tmp_path: Path,
    market_id: int,
    anchor_price: float,
    trigger_price: float,
):
    store = Store(tmp_path / "sim.db")
    enable_only(store, "M01R")
    seed = int(store.config()["strategy_m0_seed"])
    digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
    side = "UP" if digest[0] < 128 else "DOWN"
    ask_key = f"{side.lower()}_ask"
    bid_key = f"{side.lower()}_bid"
    size_key = f"{side.lower()}_ask_size"

    store.maybe_enter_m_series(
        snapshot(market_id),
        200,
        realtime_context=realtime("spot", 1, 1_000),
    )
    assert store._get_m_signal("M01R", market_id)["side"] == side

    anchor = snapshot(
        market_id,
        seconds_left=200.0,
        **{
            ask_key: anchor_price,
            bid_key: max(0.01, anchor_price - 0.01),
            size_key: 100.0,
        },
    )
    store.maybe_enter_m_series(
        anchor,
        200,
        realtime_context=realtime("prediction", 2, 2_000, execute=True),
    )
    assert trades(store, "M01R", market_id) == []
    state = store.db.execute(
        "SELECT * FROM strategy_m01r_state WHERE market_id=?", (market_id,)
    ).fetchone()
    assert state["side"] == side
    assert state["low_ask"] == pytest.approx(anchor_price)
    assert state["trigger_price"] == pytest.approx(trigger_price)

    before_trigger = snapshot(
        market_id,
        seconds_left=150.0,
        **{
            ask_key: trigger_price - 0.01,
            bid_key: trigger_price - 0.02,
            size_key: 100.0,
        },
    )
    store.maybe_enter_m_series(
        before_trigger,
        200,
        realtime_context=realtime("prediction", 3, 3_000, execute=True),
    )
    assert trades(store, "M01R", market_id) == []

    at_trigger = snapshot(
        market_id,
        seconds_left=100.0,
        **{
            ask_key: trigger_price,
            bid_key: trigger_price - 0.01,
            size_key: 100.0,
        },
    )
    opened = store.maybe_enter_m_series(
        at_trigger,
        200,
        realtime_context=realtime("prediction", 4, 4_000, execute=True),
    )
    trade = trades(store, "M01R", market_id)[0]
    assert [candidate["strategy"] for candidate in opened] == ["M01R"]
    assert trade["side"] == side
    assert trade["entry_price"] == pytest.approx(trigger_price)
    diagnostics = json.loads(trade["diagnostics_json"])
    assert diagnostics["maximum_anchor_price"] == pytest.approx(0.30)
    assert diagnostics["configured_rebound_amount"] == pytest.approx(0.10)
    assert diagnostics["rebound_anchor_price"] == pytest.approx(anchor_price)
    assert diagnostics["rebound_trigger_price"] == pytest.approx(trigger_price)
    assert diagnostics["observed_rebound_amount"] == pytest.approx(0.10)
    assert diagnostics["direction_rule"] == (
        "m0_direction_buy_after_selected_ask_rebounds_from_low_water"
    )


def test_m01_rebound_moves_trigger_down_when_a_new_low_is_observed(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    enable_only(store, "M01R")
    market_id = 2014
    seed = int(store.config()["strategy_m0_seed"])
    digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
    side = "UP" if digest[0] < 128 else "DOWN"
    ask_key = f"{side.lower()}_ask"
    bid_key = f"{side.lower()}_bid"

    store.maybe_enter_m_series(
        snapshot(market_id), 200,
        realtime_context=realtime("spot", 1, 1_000),
    )
    for sequence, ask in ((2, 0.30), (3, 0.15), (4, 0.24)):
        store.maybe_enter_m_series(
            snapshot(
                market_id,
                seconds_left=250.0 - sequence,
                **{ask_key: ask, bid_key: max(0.01, ask - 0.01)},
            ),
            200,
            realtime_context=realtime(
                "prediction", sequence, sequence * 1_000, execute=True
            ),
        )
    assert trades(store, "M01R", market_id) == []
    state = store.db.execute(
        "SELECT * FROM strategy_m01r_state WHERE market_id=?", (market_id,)
    ).fetchone()
    assert state["low_ask"] == pytest.approx(0.15)
    assert state["trigger_price"] == pytest.approx(0.25)

    store.maybe_enter_m_series(
        snapshot(
            market_id,
            seconds_left=100.0,
            **{ask_key: 0.25, bid_key: 0.24},
        ),
        200,
        realtime_context=realtime("prediction", 5, 5_000, execute=True),
    )
    assert trades(store, "M01R", market_id)[0]["entry_price"] == pytest.approx(0.25)


def test_m01_rebound_rejects_impossible_anchor_plus_rebound(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    with pytest.raises(ValueError, match="anchor plus rebound cannot exceed 1"):
        store.update_config(
            {
                "strategy_m01r_max_anchor": 0.95,
                "strategy_m01r_rebound": 0.10,
            }
        )


def test_m3_threshold_and_m4_only_count_new_spot_sequences(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    enable_only(store, "M3")
    market_id = 2003

    store.maybe_enter_m_series(
        snapshot(market_id, spot_price=100.005),
        200,
        realtime_context=realtime("spot", 1, 10_000),
    )
    assert store._get_m_signal("M3", market_id) is None
    store.update_config({"strategy_m4_enabled": True})

    qualifying = snapshot(market_id, spot_price=100.02)
    store.maybe_enter_m_series(
        qualifying, 200, realtime_context=realtime("spot", 2, 20_000)
    )
    # Replaying the same spot sequence and a Prediction event do not advance M4.
    store.maybe_enter_m_series(
        qualifying, 200, realtime_context=realtime("spot", 2, 21_000)
    )
    store.maybe_enter_m_series(
        qualifying, 200, realtime_context=realtime("prediction", 3, 22_000)
    )
    assert store._get_m_signal("M4", market_id) is None

    store.maybe_enter_m_series(
        qualifying, 200, realtime_context=realtime("spot", 4, 30_000)
    )
    assert store._get_m_signal("M4", market_id) is not None
    store.maybe_enter_m_series(
        qualifying,
        200,
        realtime_context=realtime("prediction", 5, 40_000, execute=True),
    )
    assert trades(store, "M3", market_id)[0]["side"] == "UP"
    assert trades(store, "M4", market_id)[0]["side"] == "UP"


def test_m5_uses_usdm_perpetual_last_trade_not_spot(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    enable_only(store, "M5")
    market_id = 2004
    row = snapshot(market_id, spot_price=101.0, futures_price=99.0)
    store.maybe_enter_m_series(
        row, 200, realtime_context=realtime("futures", 1, 50_000)
    )
    store.maybe_enter_m_series(
        row,
        200,
        realtime_context=realtime("prediction", 2, 60_000, execute=True),
    )
    trade = trades(store, "M5", market_id)[0]
    assert trade["side"] == "DOWN"
    diagnostics = json.loads(trade["diagnostics_json"])
    assert diagnostics["signal_price_field"] == "futures_price"
    assert diagnostics["futures_agg_trade_id"] == 1234


def test_m6_uses_only_prior_market_opening_basis(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    enable_only(store, "M6")
    for offset in range(20):
        store.maybe_enter_m_series(
            snapshot(3000 + offset, spot_price=100.02, seconds_left=300.0),
            200,
            realtime_context=realtime("spot", offset + 1, 10_000 + offset),
        )

    samples = store.db.execute(
        "SELECT * FROM strategy_m_basis_samples ORDER BY id"
    ).fetchall()
    assert len(samples) == 20
    assert all(row["source"].startswith("binance_spot_ws") for row in samples)
    assert store.db.execute(
        "SELECT COUNT(*) FROM strategy_m_signals WHERE strategy IN ('M', 'M2')"
    ).fetchone()[0] == 0

    market_id = 4000
    current = snapshot(market_id, spot_price=100.01)
    store.maybe_enter_m_series(
        current, 200, realtime_context=realtime("spot", 1, 70_000)
    )
    store.maybe_enter_m_series(
        current,
        200,
        realtime_context=realtime("prediction", 2, 80_000, execute=True),
    )
    trade = trades(store, "M6", market_id)[0]
    assert trade["side"] == "DOWN"
    diagnostics = json.loads(trade["diagnostics_json"])
    assert diagnostics["basis_sample_count"] == 20
    assert diagnostics["basis_mean_bps"] == pytest.approx(2.0)
    assert diagnostics["adjusted_distance_bps"] == pytest.approx(-1.0)
    assert diagnostics["basis_source_table"] == "strategy_m_basis_samples"
    assert diagnostics["no_lookahead"] is True


def test_m6_causal_cutoff_excludes_samples_inserted_after_current_market(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    enable_only(store, "M6")
    for offset in range(20):
        store.maybe_enter_m_series(
            snapshot(6000 + offset, spot_price=100.02),
            200,
            realtime_context=realtime("spot", offset + 1, 100_000 + offset),
        )
    current_market = 7000
    store.maybe_enter_m_series(
        snapshot(current_market, spot_price=100.01),
        200,
        realtime_context=realtime("spot", 21, 200_000),
    )
    before = store._strategy_m6_basis_context(current_market, store.config())
    assert before is not None
    assert before["basis_mean_bps"] == pytest.approx(2.0)

    # This row is persisted later and therefore lies beyond current's causal id.
    store.maybe_enter_m_series(
        snapshot(1, spot_price=99.0),
        200,
        realtime_context=realtime("spot", 22, 300_000),
    )
    after = store._strategy_m6_basis_context(current_market, store.config())
    assert after is not None
    assert after["basis_mean_bps"] == pytest.approx(2.0)
    assert after["basis_sample_count"] == 20
    assert after["basis_causal_cutoff_sample_id"] == before[
        "basis_causal_cutoff_sample_id"
    ]


def test_m6_basis_is_restart_stable_and_missing_result_is_not_permanent(
    tmp_path: Path,
):
    db_path = tmp_path / "sim.db"
    store = Store(db_path)
    enable_only(store, "M6")
    current_market = 9000
    assert store._strategy_m6_basis_context(current_market, store.config()) is None
    for offset in range(20):
        store.maybe_enter_m_series(
            snapshot(8000 + offset, spot_price=100.03),
            200,
            realtime_context=realtime("spot", offset + 1, 400_000 + offset),
        )
    store.maybe_enter_m_series(
        snapshot(current_market, spot_price=100.01),
        200,
        realtime_context=realtime("spot", 21, 500_000),
    )
    expected = store._strategy_m6_basis_context(current_market, store.config())
    assert expected is not None
    assert expected["basis_mean_bps"] == pytest.approx(3.0)
    store.db.close()

    reopened = Store(db_path)
    actual = reopened._strategy_m6_basis_context(current_market, reopened.config())
    assert actual == expected


def test_m7_uses_independent_absolute_deadlines_and_first_later_book(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    enable_only(store, "M7_1", "M7_2", "M7_3", "M7_5")
    market_id = 5000
    cohorts = (
        (1, "M7_1", 100.02, "UP"),
        (2, "M7_2", 99.98, "DOWN"),
        (3, "M7_3", 100.03, "UP"),
        (5, "M7_5", 99.97, "DOWN"),
    )
    for delay, strategy, asof_spot, expected_side in cohorts:
        deadline_ns = delay * 1_000_000_000
        scheduler_context = realtime(
            "scheduler", 100 + delay, deadline_ns, execute=False
        )
        scheduler_context.update(
            {
                "m7_deadline_seconds": float(delay),
                "m7_deadline_monotonic_ns": deadline_ns,
                "m7_scheduler_lateness_ms": 0.3,
                "m7_asof_spot_received_monotonic_ns": deadline_ns - 200_000_000,
                "m7_asof_spot_event_sequence": f"spot:trade:{delay}",
                "m7_asof_spot_age_ms": 200.0,
            }
        )
        store.maybe_enter_m_series(
            snapshot(
                market_id,
                seconds_left=300.0 - delay,
                spot_price=asof_spot,
            ),
            200,
            realtime_context=scheduler_context,
        )
        assert trades(store, strategy, market_id) == []

        # Even if processed later, a book timestamped before the absolute
        # deadline cannot execute this cohort.
        store.maybe_enter_m_series(
            snapshot(market_id, seconds_left=300.0 - delay - 0.05),
            200,
            realtime_context=realtime(
                "prediction", 200 + delay, deadline_ns - 1, execute=True
            ),
        )
        assert trades(store, strategy, market_id) == []

        execution = snapshot(
            market_id,
            seconds_left=300.0 - delay - 0.1,
            # A later live Spot reversal must not change the as-of direction.
            spot_price=99.0 if expected_side == "UP" else 101.0,
            up_ask=0.50 + delay / 100,
            up_bid=0.49 + delay / 100,
            down_ask=0.49 - delay / 100,
            down_bid=0.48 - delay / 100,
        )
        store.maybe_enter_m_series(
            execution,
            200,
            realtime_context=realtime(
                "prediction",
                300 + delay,
                deadline_ns + 100_000_000,
                execute=True,
            ),
        )
        trade = trades(store, strategy, market_id)[0]
        assert trade["side"] == expected_side
        assert trade["strategy_version"] == (
            f"{strategy}_v2_market_open_deadline_ws_hold"
        )
        diagnostics = json.loads(trade["diagnostics_json"])
        assert diagnostics["requested_delay_seconds"] == delay
        assert diagnostics["actual_delay_seconds"] == pytest.approx(delay + 0.1)
        assert diagnostics["execution_lag_seconds"] == pytest.approx(0.1)
        assert diagnostics["signal_realtime_context"]["m7_deadline_monotonic_ns"] == deadline_ns
        assert diagnostics["signal_realtime_context"]["m7_asof_spot_age_ms"] == 200.0
