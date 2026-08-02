from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from predict_bot.m_realtime import (
    LIVE_FORWARDABLE_OBSERVER_STRATEGIES,
    LIVE_FORWARDABLE_PAPER_STRATEGIES,
)
from predict_bot.research_forward import (
    CONFIRMATION_ADD_STRATEGY,
    CONTINUOUS_CALIBRATION_STRATEGIES,
    PAIRED_REVERSE_STRATEGIES,
    FUTURES_LEAD_EXPERIMENT_STRATEGIES,
    FUTURES_LEAD_FILTER_STRATEGIES,
    FUTURES_LEAD_OBSERVER_STRATEGIES,
    FUTURES_LEAD_OBSERVER_VERSIONS,
    OBSERVER_COMBINATION_STRATEGIES,
    OBSERVER_AUTO_V6_STRATEGIES,
    RESEARCH_STRATEGIES,
    ResearchSampleBuffer,
    confirmation_add_book_is_safe,
    confirmation_add_levels,
    confirmed_futures_lead_signal,
    continuous_calibration_decision,
    execution_candidate,
    reverse_source_signal,
    filtered_futures_lead_signal,
    futures_lead_observer_decision,
    observer_v6_auto_decision,
    regime_futures_lead_signal,
    reverse_futures_lead_signal,
    signal_for_strategy,
    terminal_probability_from_distance,
)
from predict_bot.server import DEFAULT_CONFIG, Store


def sample(*, timestamp_ns: int, seconds_left: float, current: bool) -> dict:
    if current:
        values = {
            "up_bid": 0.41,
            "up_ask": 0.42,
            "up_bid_size": 100.0,
            "up_ask_size": 100.0,
            "down_bid": 0.58,
            "down_ask": 0.59,
            "down_bid_size": 1.0,
            "down_ask_size": 100.0,
            "spot_price": 100.1,
            "futures_price": 100.2,
        }
    else:
        values = {
            "up_bid": 0.40,
            "up_ask": 0.41,
            "up_bid_size": 10.0,
            "up_ask_size": 10.0,
            "down_bid": 0.59,
            "down_ask": 0.60,
            "down_bid_size": 10.0,
            "down_ask_size": 10.0,
            "spot_price": 100.0,
            "futures_price": 100.0,
        }
    return {
        "timestamp": "2026-07-27T00:00:00+00:00",
        "timestamp_ns": timestamp_ns,
        "topic_id": 1,
        "market_id": 11,
        "title": "BTC Up or Down",
        "market_start_ms": 1_000_000,
        "market_end_ms": 1_300_000,
        "start_price": 100.0,
        "seconds_left": seconds_left,
        "book_age_ms": 20.0,
        "book_skew_ms": 0.0,
        "spot_age_ms": 100.0,
        "futures_age_ms": 100.0,
        **values,
    }


def test_confirmation_add_ladder_is_fixed_and_never_live_forwardable():
    assert confirmation_add_levels(0.20) == pytest.approx((0.20, 0.22, 0.24, 0.26, 0.28))
    assert CONFIRMATION_ADD_STRATEGY not in LIVE_FORWARDABLE_PAPER_STRATEGIES
    assert CONFIRMATION_ADD_STRATEGY not in LIVE_FORWARDABLE_OBSERVER_STRATEGIES

def test_reverse_source_signal_flips_side_and_probability() -> None:
    microprice = reverse_source_signal(
        "R_MICROPRICE",
        "UP",
        0.75,
    )

    assert microprice is not None
    assert microprice["side"] == "DOWN"
    assert microprice["signal"] == pytest.approx(-0.75)
    assert microprice["source_strategy"] == "R_MICROPRICE"
    assert microprice["direction_reversed"] is True

    calibrated = reverse_source_signal(
        "R_CALIBRATED_VALUE",
        "DOWN",
        0.04,
        source_probability=0.72,
    )

    assert calibrated is not None
    assert calibrated["side"] == "UP"
    assert calibrated["signal"] == pytest.approx(-0.04)
    assert calibrated["model_probability"] == pytest.approx(0.28)
    assert calibrated["source_strategy"] == "R_CALIBRATED_VALUE"

def test_microprice_reverse_opens_only_beside_source(tmp_path) -> None:
    store = Store(tmp_path / "simulation.db")

    values = {
        key: False
        for key in DEFAULT_CONFIG
        if key.endswith("_enabled")
    }
    values["strategy_r_microprice_enabled"] = True
    values["strategy_r_microprice_reverse_enabled"] = True
    store.update_config(values)

    current = sample(
        timestamp_ns=20_000_000_000,
        seconds_left=180.0,
        current=True,
    )

    opened = store.maybe_enter_m_series(
        current,
        0,
        realtime_context=prediction_context(1),
    )

    assert [
        (item["strategy"], item["side"])
        for item in opened
    ] == [
        ("R_MICROPRICE", "UP"),
        ("R_MICROPRICE_REVERSE", "DOWN"),
    ]

    reverse = store.db.execute(
        """
        SELECT diagnostics_json
        FROM trades
        WHERE strategy='R_MICROPRICE_REVERSE'
        """
    ).fetchone()

    diagnostics = json.loads(reverse["diagnostics_json"])

    assert diagnostics["shadow_only"] is True
    assert diagnostics["direction_reversed"] is True
    assert diagnostics["source_strategy"] == "R_MICROPRICE"
    assert diagnostics["source_trade_id"] > 0

def test_reverse_shadows_never_generate_independent_signal() -> None:
    current = sample(
        timestamp_ns=20_000_000_000,
        seconds_left=180.0,
        current=True,
    )

    for strategy in PAIRED_REVERSE_STRATEGIES:
        assert signal_for_strategy(
            strategy,
            current,
            None,
            fee_bps=0,
            slippage_bps=50.0,
        ) is None

def test_confirmation_add_book_gate_fails_closed_at_cutoff_and_on_stale_book():
    book = sample(timestamp_ns=1, seconds_left=31.0, current=True)
    safe, reason = confirmation_add_book_is_safe(book, "UP")
    assert safe is True
    assert reason == "safe"

    cutoff = {**book, "seconds_left": 30.0}
    assert confirmation_add_book_is_safe(cutoff, "UP") == (
        False,
        "confirmation cutoff reached",
    )
    stale = {**book, "book_age_ms": 2001.0}
    assert confirmation_add_book_is_safe(stale, "UP") == (
        False,
        "book age exceeds confirmation limit",
    )


def paper_config(store: Store, enabled: str) -> None:
    values = {
        key: False for key in DEFAULT_CONFIG if key.endswith("_enabled")
    }
    values[f"strategy_{enabled.lower()}_enabled"] = True
    store.update_config(values)


def prediction_context(
    sequence: int = 1,
    gate: dict | None = None,
    sampling_mode: str = "event_stream",
) -> dict:
    context = {
        "signal_event_type": "prediction",
        "trigger_source": "prediction",
        "execution_eligible": True,
        "market_data_integrity_ok": True,
        "received_wall_ns": 20_000_000_000,
        "received_monotonic_ns": 20_000_000_000,
        "signal_event_sequence": f"prediction:{sequence}",
        "prediction_sampling_mode": sampling_mode,
    }
    if gate is not None:
        context["m01o_observer_gates"] = {"F1": gate}
    return context


def test_ofi_forward_paper_uses_full_real_top_level_depth(tmp_path) -> None:
    store = Store(tmp_path / "simulation.db")
    paper_config(store, "R_OFI")
    store.maybe_enter_m_series(
        sample(timestamp_ns=10_000_000_000, seconds_left=70.2, current=False),
        200,
        realtime_context=prediction_context(),
    )
    opened = store.maybe_enter_m_series(
        sample(timestamp_ns=20_200_000_000, seconds_left=60.0, current=True),
        200,
        realtime_context=prediction_context(),
    )

    assert [item["strategy"] for item in opened] == ["R_OFI"]
    assert opened[0]["paper_only"] is True
    row = store.db.execute(
        "SELECT * FROM trades WHERE strategy='R_OFI'"
    ).fetchone()
    assert row is not None
    assert row["stake"] == pytest.approx(5.0)
    assert row["entry_price"] == pytest.approx(0.42 * 1.005)
    diagnostics = json.loads(row["diagnostics_json"])
    assert diagnostics["fill_ratio"] == 1.0
    assert diagnostics["partial_fill"] is False
    assert diagnostics["shared_cap_usdt"] == 100.0
    assert diagnostics["live_orders_affected"] is False


def test_research_execution_rejects_insufficient_top_level_depth() -> None:
    current = sample(timestamp_ns=1, seconds_left=60.0, current=True)
    current["up_ask_size"] = 1.0
    candidate = execution_candidate(
        "R_OFI",
        {"side": "UP", "signal": 1.0},
        current,
        current,
        stake=5.0,
        minimum_stake=2.0,
        slippage_bps=50.0,
        max_spread=0.03,
        min_entry=0.05,
        max_book_age_ms=2000.0,
        max_book_skew_ms=500.0,
    )
    assert candidate is None


def test_ofi_min040_shadow_rejects_lower_entry_without_changing_original() -> None:
    current = sample(timestamp_ns=1, seconds_left=60.0, current=True)
    current["up_ask"] = 0.39
    current["up_bid"] = 0.38
    arguments = dict(
        signal={"side": "UP", "signal": 1.0},
        current=current,
        snapshot=current,
        stake=5.0,
        minimum_stake=2.0,
        slippage_bps=50.0,
        max_spread=0.03,
        min_entry=0.05,
        max_book_age_ms=2000.0,
        max_book_skew_ms=500.0,
    )
    assert execution_candidate("R_OFI", **arguments) is not None
    assert execution_candidate("R_OFI_MIN040", **arguments) is None


def test_futures_lead_reverse_is_derived_from_opened_source_trade() -> None:
    previous = sample(timestamp_ns=10_000_000_000, seconds_left=184.0, current=False)
    current = sample(timestamp_ns=14_000_000_000, seconds_left=180.0, current=True)

    forward = signal_for_strategy(
        "R_FUTURES_LEAD", current, previous, fee_bps=200, slippage_bps=50.0
    )
    assert forward is not None
    assert signal_for_strategy(
        "R_FUTURES_LEAD_REVERSE",
        current,
        previous,
        fee_bps=200,
        slippage_bps=50.0,
    ) is None
    reverse = reverse_futures_lead_signal(forward["side"], forward["signal"])
    assert reverse is not None
    assert forward["side"] == "UP"
    assert reverse["side"] == "DOWN"
    assert reverse["signal"] == pytest.approx(-forward["signal"])
    assert reverse["source_strategy"] == "R_FUTURES_LEAD"
    assert reverse["source_side"] == forward["side"]
    assert reverse["direction_reversed"] is True


@pytest.mark.parametrize("strategy", ["R_FUTURES_LEAD", "R_CONSENSUS"])
def test_base_cross_market_signals_reject_stale_source_samples(strategy: str) -> None:
    previous = sample(timestamp_ns=10_000_000_000, seconds_left=184.0, current=False)
    current = sample(timestamp_ns=14_000_000_000, seconds_left=180.0, current=True)
    current["spot_age_ms"] = 501.0

    assert signal_for_strategy(
        strategy,
        current,
        previous,
        fee_bps=200,
        slippage_bps=50.0,
    ) is None


def test_futures_lead_filter_variants_use_frozen_source_trade_fields() -> None:
    assert set(FUTURES_LEAD_FILTER_STRATEGIES) == {
        "R_FUTURES_LEAD_SIGNAL_100",
        "R_FUTURES_LEAD_MIN_ENTRY_020",
    }
    assert filtered_futures_lead_signal(
        "R_FUTURES_LEAD_SIGNAL_100",
        source_side="UP",
        source_signal=1.0,
        source_entry=0.19,
    ) is not None
    assert filtered_futures_lead_signal(
        "R_FUTURES_LEAD_SIGNAL_100",
        source_side="UP",
        source_signal=0.999,
        source_entry=0.40,
    ) is None
    assert filtered_futures_lead_signal(
        "R_FUTURES_LEAD_MIN_ENTRY_020",
        source_side="DOWN",
        source_signal=-0.50,
        source_entry=0.200001,
    ) is not None
    assert filtered_futures_lead_signal(
        "R_FUTURES_LEAD_MIN_ENTRY_020",
        source_side="DOWN",
        source_signal=-2.0,
        source_entry=0.20,
    ) is None


def test_futures_lead_filter_shadows_open_only_after_source_trade(tmp_path) -> None:
    store = Store(tmp_path / "simulation.db")
    values = {key: False for key in DEFAULT_CONFIG if key.endswith("_enabled")}
    values.update(
        {
            "strategy_r_futures_lead_enabled": True,
            "strategy_r_futures_lead_signal_100_enabled": True,
            "strategy_r_futures_lead_min_entry_020_enabled": True,
        }
    )
    store.update_config(values)
    previous = sample(
        timestamp_ns=10_000_000_000, seconds_left=184.0, current=False
    )
    current = sample(
        timestamp_ns=14_000_000_000, seconds_left=180.0, current=True
    )

    store.maybe_enter_m_series(
        previous, 200, realtime_context=prediction_context(1)
    )
    opened = store.maybe_enter_m_series(
        current, 200, realtime_context=prediction_context(2)
    )

    assert [item["strategy"] for item in opened] == [
        "R_FUTURES_LEAD",
        "R_FUTURES_LEAD_SIGNAL_100",
        "R_FUTURES_LEAD_MIN_ENTRY_020",
    ]
    rows = store.db.execute(
        "SELECT id, strategy, diagnostics_json FROM trades WHERE market_id=11"
    ).fetchall()
    diagnostics = {
        row["strategy"]: json.loads(row["diagnostics_json"]) for row in rows
    }
    source_id = next(row["id"] for row in rows if row["strategy"] == "R_FUTURES_LEAD")
    for strategy in FUTURES_LEAD_FILTER_STRATEGIES:
        assert diagnostics[strategy]["source_strategy"] == "R_FUTURES_LEAD"
        assert diagnostics[strategy]["source_trade_id"] == source_id


def test_futures_lead_reverse_does_not_trigger_when_original_does_not() -> None:
    previous = sample(timestamp_ns=10_000_000_000, seconds_left=184.0, current=False)
    current = sample(timestamp_ns=14_000_000_000, seconds_left=180.0, current=True)
    current["futures_price"] = current["spot_price"]

    assert signal_for_strategy(
        "R_FUTURES_LEAD", current, previous, fee_bps=200, slippage_bps=50.0
    ) is None


@pytest.mark.parametrize(
    "reverse_after_three_losses,expected_side,expected_signal",
    [(False, "UP", 1.25), (True, "DOWN", -1.25)],
)
def test_futures_lead_regime_signal_follows_or_reverses_frozen_source(
    reverse_after_three_losses: bool,
    expected_side: str,
    expected_signal: float,
) -> None:
    signal = regime_futures_lead_signal(
        "UP",
        1.25,
        reverse_after_three_losses=reverse_after_three_losses,
    )

    assert signal is not None
    assert signal["side"] == expected_side
    assert signal["signal"] == pytest.approx(expected_signal)
    assert signal["regime_reversed"] is reverse_after_three_losses
    assert signal["source_strategy"] == "R_FUTURES_LEAD"


def test_futures_lead_reverse_does_not_open_when_source_entry_is_rejected(
    tmp_path,
) -> None:
    store = Store(tmp_path / "simulation.db")
    values = {key: False for key in DEFAULT_CONFIG if key.endswith("_enabled")}
    values["strategy_r_futures_lead_enabled"] = True
    values["strategy_r_futures_lead_reverse_enabled"] = True
    store.update_config(values)
    previous = sample(
        timestamp_ns=10_000_000_000, seconds_left=184.0, current=False
    )
    current = sample(
        timestamp_ns=14_000_000_000, seconds_left=180.0, current=True
    )
    current.update({
        "up_bid": 0.89,
        "up_ask": 0.90,
        "down_bid": 0.10,
        "down_ask": 0.11,
    })

    store.maybe_enter_m_series(
        previous, 200, realtime_context=prediction_context(1)
    )
    opened = store.maybe_enter_m_series(
        current, 200, realtime_context=prediction_context(2)
    )

    assert opened == []
    assert store.db.execute(
        "SELECT COUNT(*) FROM trades WHERE market_id=11 AND strategy IN "
        "('R_FUTURES_LEAD','R_FUTURES_LEAD_REVERSE')"
    ).fetchone()[0] == 0
    assert signal_for_strategy(
        "R_FUTURES_LEAD_REVERSE",
        current,
        previous,
        fee_bps=200,
        slippage_bps=50.0,
    ) is None


def test_futures_lead_reverse_isolated_shadow_opens_beside_original(tmp_path) -> None:
    store = Store(tmp_path / "simulation.db")
    values = {key: False for key in DEFAULT_CONFIG if key.endswith("_enabled")}
    values["strategy_r_futures_lead_enabled"] = True
    values["strategy_r_futures_lead_reverse_enabled"] = True
    store.update_config(values)
    store.update_config({"strategy_research_shared_cap_usdt": 5.0})
    previous = sample(
        timestamp_ns=10_000_000_000, seconds_left=184.0, current=False
    )
    current = sample(
        timestamp_ns=14_000_000_000, seconds_left=180.0, current=True
    )

    store.maybe_enter_m_series(
        previous, 200, realtime_context=prediction_context(1)
    )
    opened = store.maybe_enter_m_series(
        current, 200, realtime_context=prediction_context(2)
    )

    assert [(item["strategy"], item["side"]) for item in opened] == [
        ("R_FUTURES_LEAD", "UP"),
        ("R_FUTURES_LEAD_REVERSE", "DOWN"),
    ]
    reverse_candidate = opened[1]
    assert reverse_candidate["dependent_live_pair"] is True
    assert reverse_candidate["source_strategy"] == "R_FUTURES_LEAD"
    assert reverse_candidate["source_side"] == "UP"
    assert reverse_candidate["source_trade_id"] > 0
    shadow = store.db.execute(
        "SELECT diagnostics_json FROM trades "
        "WHERE strategy='R_FUTURES_LEAD_REVERSE'"
    ).fetchone()
    diagnostics = json.loads(shadow["diagnostics_json"])
    assert diagnostics["shadow_only"] is True
    assert diagnostics["direction_reversed"] is True
    assert diagnostics["source_strategy"] == "R_FUTURES_LEAD"
    assert diagnostics["source_trade_id"] > 0
    assert diagnostics["dependency_rule"] == (
        "open_only_after_same_market_R_FUTURES_LEAD_trade"
    )


def test_futures_lead_observer_shadows_open_only_after_source_and_frozen_gate(
    tmp_path,
) -> None:
    store = Store(tmp_path / "simulation.db")
    values = {key: False for key in DEFAULT_CONFIG if key.endswith("_enabled")}
    values["strategy_r_futures_lead_enabled"] = True
    for strategy in FUTURES_LEAD_OBSERVER_STRATEGIES:
        values[f"strategy_{strategy.lower()}_enabled"] = True
    store.update_config(values)
    previous = sample(
        timestamp_ns=10_000_000_000, seconds_left=184.0, current=False
    )
    current = sample(
        timestamp_ns=14_000_000_000, seconds_left=180.0, current=True
    )

    store.maybe_enter_m_series(
        previous, 200, realtime_context=prediction_context(1, observer_gate())
    )
    opened = store.maybe_enter_m_series(
        current, 200, realtime_context=prediction_context(2, observer_gate())
    )

    assert [item["strategy"] for item in opened] == [
        "R_FUTURES_LEAD",
        *FUTURES_LEAD_OBSERVER_STRATEGIES,
    ]
    assert {item["side"] for item in opened} == {"UP"}
    diagnostics = json.loads(store.db.execute(
        "SELECT diagnostics_json FROM trades "
        "WHERE strategy='R_FUTURES_LEAD_OBSERVER_V6'"
    ).fetchone()[0])
    assert diagnostics["observer_version"] == "V6"
    assert diagnostics["source_trade_id"] > 0
    assert diagnostics["direction_reversed"] is False
    assert diagnostics["chronological_sample_index"] == 1


def test_ofi_observer_v3_depends_on_opened_ofi_and_frozen_gate(tmp_path) -> None:
    store = Store(tmp_path / "simulation.db")
    values = {key: False for key in DEFAULT_CONFIG if key.endswith("_enabled")}
    values["strategy_r_ofi_enabled"] = True
    values["strategy_r_ofi_observer_v3_enabled"] = True
    store.update_config(values)
    store.maybe_enter_m_series(
        sample(timestamp_ns=10_000_000_000, seconds_left=70.2, current=False),
        200,
        realtime_context=prediction_context(1, observer_gate()),
    )
    opened = store.maybe_enter_m_series(
        sample(timestamp_ns=20_200_000_000, seconds_left=60.0, current=True),
        200,
        realtime_context=prediction_context(2, observer_gate()),
    )

    assert [item["strategy"] for item in opened] == [
        "R_OFI",
        "R_OFI_OBSERVER_V3",
    ]
    assert {item["side"] for item in opened} == {"UP"}
    diagnostics = json.loads(store.db.execute(
        "SELECT diagnostics_json FROM trades "
        "WHERE strategy='R_OFI_OBSERVER_V3'"
    ).fetchone()[0])
    assert diagnostics["source_strategy"] == "R_OFI"
    assert diagnostics["source_trade_id"] > 0
    assert diagnostics["observer_version"] == "V3"
    assert diagnostics["direction_reversed"] is False
    assert diagnostics["chronological_sample_index"] == 1

    blocked = Store(tmp_path / "blocked.db")
    blocked.update_config(values)
    blocked.maybe_enter_m_series(
        sample(timestamp_ns=10_000_000_000, seconds_left=70.2, current=False),
        200,
        realtime_context=prediction_context(1, observer_gate()),
    )
    blocked_opened = blocked.maybe_enter_m_series(
        sample(timestamp_ns=20_200_000_000, seconds_left=60.0, current=True),
        200,
        realtime_context=prediction_context(
            2, observer_gate(currentEffectiveCrossovers=1)
        ),
    )
    assert [item["strategy"] for item in blocked_opened] == ["R_OFI"]

    missing_source = Store(tmp_path / "missing-source.db")
    missing_values = {
        key: False for key in DEFAULT_CONFIG if key.endswith("_enabled")
    }
    missing_values["strategy_r_ofi_observer_v3_enabled"] = True
    missing_source.update_config(missing_values)
    missing_source.maybe_enter_m_series(
        sample(timestamp_ns=10_000_000_000, seconds_left=70.2, current=False),
        200,
        realtime_context=prediction_context(1, observer_gate()),
    )
    assert missing_source.maybe_enter_m_series(
        sample(timestamp_ns=20_200_000_000, seconds_left=60.0, current=True),
        200,
        realtime_context=prediction_context(2, observer_gate()),
    ) == []


def test_microprice_observer_v3_v6_are_independent_source_shadows(tmp_path) -> None:
    store = Store(tmp_path / "simulation.db")
    values = {key: False for key in DEFAULT_CONFIG if key.endswith("_enabled")}
    values["strategy_r_microprice_enabled"] = True
    values["strategy_r_microprice_observer_v3_enabled"] = True
    values["strategy_r_microprice_observer_v6_enabled"] = True
    store.update_config(values)
    store.maybe_enter_m_series(
        sample(timestamp_ns=10_000_000_000, seconds_left=190.2, current=False),
        200,
        realtime_context=prediction_context(1, observer_gate()),
    )
    opened = store.maybe_enter_m_series(
        sample(timestamp_ns=20_200_000_000, seconds_left=180.0, current=True),
        200,
        realtime_context=prediction_context(2, observer_gate()),
    )

    assert [item["strategy"] for item in opened] == [
        "R_MICROPRICE",
        "R_MICROPRICE_OBSERVER_V3",
        "R_MICROPRICE_OBSERVER_V6",
    ]
    assert {item["side"] for item in opened} == {"UP"}
    rows = store.db.execute(
        "SELECT strategy, diagnostics_json FROM trades "
        "WHERE strategy LIKE 'R_MICROPRICE_OBSERVER_%' ORDER BY strategy"
    ).fetchall()
    assert len(rows) == 2
    for row in rows:
        diagnostics = json.loads(row["diagnostics_json"])
        assert diagnostics["source_strategy"] == "R_MICROPRICE"
        assert diagnostics["source_trade_id"] > 0
        assert diagnostics["observer_version"] in {"V3", "V6"}
        assert diagnostics["chronological_sample_index"] == 1


def test_calibrated_value_observer_v6_depends_on_source_and_frozen_gate(
    tmp_path,
) -> None:
    store = Store(tmp_path / "simulation.db")
    values = {key: False for key in DEFAULT_CONFIG if key.endswith("_enabled")}
    values["strategy_r_calibrated_value_enabled"] = True
    values["strategy_r_calibrated_value_observer_v6_enabled"] = True
    store.update_config(values)
    current = sample(timestamp_ns=20_200_000_000, seconds_left=60.0, current=True)
    current.update({
        "up_bid": 0.20,
        "up_ask": 0.21,
        "down_bid": 0.40,
        "down_ask": 0.41,
    })
    opened = store.maybe_enter_m_series(
        current,
        200,
        realtime_context=prediction_context(2, observer_gate()),
    )

    assert [item["strategy"] for item in opened] == [
        "R_CALIBRATED_VALUE",
        "R_CALIBRATED_VALUE_OBSERVER_V6",
    ]
    assert {item["side"] for item in opened} == {"DOWN"}
    diagnostics = json.loads(store.db.execute(
        "SELECT diagnostics_json FROM trades "
        "WHERE strategy='R_CALIBRATED_VALUE_OBSERVER_V6'"
    ).fetchone()[0])
    assert diagnostics["source_strategy"] == "R_CALIBRATED_VALUE"
    assert diagnostics["source_trade_id"] > 0
    assert diagnostics["observer_version"] == "V6"
    assert diagnostics["direction_reversed"] is False
    assert diagnostics["chronological_sample_index"] == 1

    blocked = Store(tmp_path / "blocked.db")
    blocked.update_config(values)
    blocked_opened = blocked.maybe_enter_m_series(
        current,
        200,
        realtime_context=prediction_context(
            2, observer_gate(currentBothSidesTouched=True)
        ),
    )
    assert [item["strategy"] for item in blocked_opened] == [
        "R_CALIBRATED_VALUE"
    ]


def test_auto_v6_switch_requires_profitable_allowed_and_losing_blocked_cohorts() -> None:
    history = [
        {
            "market_id": index,
            "v6_allowed": index % 2 == 0,
            "unit_pnl": 0.20 if index % 2 == 0 else -0.20,
        }
        for index in range(100)
    ]
    applied = observer_v6_auto_decision(
        history,
        observer_gate(currentMarketId=200),
        expected_market_id=200,
    )
    assert applied["mode"] == "APPLY_V6"
    assert applied["allowed"] is True
    assert applied["fastWindow"]["samples"] == 30
    assert applied["slowWindow"]["samples"] == 100

    blocked_current = observer_v6_auto_decision(
        history,
        observer_gate(currentMarketId=200, currentEffectiveCrossovers=1),
        expected_market_id=200,
    )
    assert blocked_current["mode"] == "APPLY_V6"
    assert blocked_current["allowed"] is False

    bypass_history = [
        {**row, "unit_pnl": 0.20}
        for row in history
    ]
    bypassed = observer_v6_auto_decision(
        bypass_history,
        observer_gate(currentMarketId=200, currentEffectiveCrossovers=0),
        expected_market_id=200,
    )
    assert bypassed["mode"] == "BYPASS_V6"
    assert bypassed["reason"] == "V6_NOT_PROVEN_BETTER"
    assert bypassed["allowed"] is True


def test_auto_v6_shadow_bypasses_during_official_history_warmup(tmp_path) -> None:
    store = Store(tmp_path / "simulation.db")
    values = {key: False for key in DEFAULT_CONFIG if key.endswith("_enabled")}
    values["strategy_r_microprice_enabled"] = True
    values["strategy_r_microprice_observer_auto_v6_enabled"] = True
    store.update_config(values)
    store.maybe_enter_m_series(
        sample(timestamp_ns=10_000_000_000, seconds_left=190.2, current=False),
        200,
        realtime_context=prediction_context(
            1, observer_gate(currentEffectiveCrossovers=0)
        ),
    )
    opened = store.maybe_enter_m_series(
        sample(timestamp_ns=20_200_000_000, seconds_left=180.0, current=True),
        200,
        realtime_context=prediction_context(
            2, observer_gate(currentEffectiveCrossovers=0)
        ),
    )

    assert [item["strategy"] for item in opened] == [
        "R_MICROPRICE",
        "R_MICROPRICE_OBSERVER_AUTO_V6",
    ]
    assert set(OBSERVER_AUTO_V6_STRATEGIES).issubset(RESEARCH_STRATEGIES)
    diagnostics = json.loads(store.db.execute(
        "SELECT diagnostics_json FROM trades "
        "WHERE strategy='R_MICROPRICE_OBSERVER_AUTO_V6'"
    ).fetchone()[0])
    decision = diagnostics["observer_auto_v6"]
    assert decision["mode"] == "BYPASS_V6"
    assert decision["reason"] == "OFFICIAL_HISTORY_WARMUP"
    assert decision["currentV6Decision"]["allowed"] is False
    assert diagnostics["official_history_only"] is True
    assert diagnostics["current_market_excluded_from_history"] is True


@pytest.mark.parametrize(
    "recent_statuses,direction_mode,expected_side,expected_reversed,expected_automatic",
    [
        (
            ("SETTLED_LOSS", "SETTLED_LOSS", "SETTLED_LOSS"),
            0,
            "DOWN",
            True,
            "REVERSE",
        ),
        (
            ("SETTLED_LOSS", "SETTLED_WIN", "SETTLED_LOSS"),
            0,
            "UP",
            False,
            "FORWARD",
        ),
        (
            ("SETTLED_LOSS", "SETTLED_LOSS", "SETTLED_LOSS"),
            1,
            "UP",
            False,
            "REVERSE",
        ),
        (
            ("SETTLED_LOSS", "SETTLED_WIN", "SETTLED_LOSS"),
            2,
            "DOWN",
            True,
            "FORWARD",
        ),
    ],
)
def test_futures_lead_regime_shadow_uses_previous_three_original_results(
    tmp_path,
    recent_statuses: tuple[str, str, str],
    direction_mode: int,
    expected_side: str,
    expected_reversed: bool,
    expected_automatic: str,
) -> None:
    store = Store(tmp_path / "simulation.db")
    values = {key: False for key in DEFAULT_CONFIG if key.endswith("_enabled")}
    values["strategy_r_futures_lead_enabled"] = True
    values["strategy_r_futures_lead_regime_reverse_3l_enabled"] = True
    store.update_config(values)
    store.update_config(
        {
            "strategy_research_shared_cap_usdt": 5.0,
            "strategy_r_futures_lead_regime_reverse_3l_direction_mode": direction_mode,
        }
    )
    for index, status in enumerate(recent_statuses, start=1):
        store.open_trade(
            strategy="R_FUTURES_LEAD",
            topic_id=index,
            market_id=index,
            side="UP",
            entry=0.40,
            target=None,
            stake=5.0,
            fee_rate_bps=200,
            note="regime history fixture",
        )
        store.db.execute(
            """UPDATE trades SET status=?, pnl=?, closed_at=?
                 WHERE strategy='R_FUTURES_LEAD' AND market_id=?""",
            (
                status,
                -5.1 if status == "SETTLED_LOSS" else 7.4,
                f"2026-07-28T00:0{index}:00+00:00",
                index,
            ),
        )
    store.db.commit()
    previous = sample(
        timestamp_ns=10_000_000_000, seconds_left=184.0, current=False
    )
    current = sample(
        timestamp_ns=14_000_000_000, seconds_left=180.0, current=True
    )

    store.maybe_enter_m_series(
        previous, 200, realtime_context=prediction_context(1)
    )
    opened = store.maybe_enter_m_series(
        current, 200, realtime_context=prediction_context(2)
    )

    assert [(item["strategy"], item["side"]) for item in opened] == [
        ("R_FUTURES_LEAD", "UP"),
        ("R_FUTURES_LEAD_REGIME_REVERSE_3L", expected_side),
    ]
    candidate = opened[1]
    assert candidate["regime_reversed"] is expected_reversed
    assert candidate["recent_lead_results"] == list(reversed(recent_statuses))
    assert candidate["direction_mode"] == {0: "AUTO", 1: "FORWARD", 2: "REVERSE"}[
        direction_mode
    ]
    assert candidate["automatic_direction"] == expected_automatic
    assert candidate["effective_direction"] == (
        "REVERSE" if expected_reversed else "FORWARD"
    )
    assert candidate["manual_direction_override"] is (direction_mode != 0)
    row = store.db.execute(
        "SELECT diagnostics_json FROM trades "
        "WHERE strategy='R_FUTURES_LEAD_REGIME_REVERSE_3L'"
    ).fetchone()
    diagnostics = json.loads(row["diagnostics_json"])
    assert diagnostics["regime_reversed"] is expected_reversed
    assert diagnostics["history_ready"] is True
    assert diagnostics["history_uses_original_lead_counterfactual"] is True
    assert diagnostics["regime_rule"] == (
        "auto_reverse_after_3_original_lead_losses_with_manual_override"
    )
    assert diagnostics["direction_mode"] == candidate["direction_mode"]
    assert diagnostics["automatic_direction"] == expected_automatic
    assert diagnostics["effective_direction"] == candidate["effective_direction"]
    assert diagnostics["manual_direction_override"] is (direction_mode != 0)


def test_futures_lead_regime_direction_mode_rejects_invalid_values(tmp_path) -> None:
    store = Store(tmp_path / "simulation.db")
    for invalid in (-1, 0.5, 3):
        with pytest.raises(ValueError):
            store.update_config(
                {
                    "strategy_r_futures_lead_regime_reverse_3l_direction_mode": invalid
                }
            )


def test_cumulative_event_ofi_sums_distinct_prediction_events() -> None:
    buffer = ResearchSampleBuffer()
    current = None
    for sequence, bid_size in enumerate((10.0, 20.0, 40.0, 80.0), start=1):
        row = sample(
            timestamp_ns=sequence * 1_000_000_000,
            seconds_left=64.0 - sequence,
            current=False,
        )
        row["up_bid_size"] = bid_size
        current = buffer.append(11, row)
        assert current is not None
        buffer.append_event_ofi(11, current, f"prediction:{sequence}")
    cumulative = buffer.cumulative_event_ofi(11, current, 10.0, 3)
    signal = signal_for_strategy(
        "R_OFI_EVENT_CUM",
        current,
        None,
        fee_bps=200,
        slippage_bps=50.0,
        cumulative_event_ofi=cumulative,
    )
    assert signal is not None
    assert signal["side"] == "UP"
    assert signal["event_ofi_count"] == 3


def test_cumulative_event_ofi_does_not_count_periodic_rest_snapshots(tmp_path) -> None:
    store = Store(tmp_path / "simulation.db")
    paper_config(store, "R_OFI_EVENT_CUM")
    current = None
    for sequence, bid_size in enumerate((10.0, 20.0, 40.0, 80.0), start=1):
        current = sample(
            timestamp_ns=sequence * 1_000_000_000,
            seconds_left=64.0 - sequence,
            current=False,
        )
        current["up_bid_size"] = bid_size
        store.maybe_enter_m_series(
            current,
            200,
            realtime_context=prediction_context(
                sequence,
                sampling_mode="periodic_snapshot",
            ),
        )

    assert current is not None
    buffered = store._research_samples.append(11, current)
    assert buffered is not None
    assert store._research_samples.cumulative_event_ofi(
        11, buffered, 10.0, 1
    ) is None
    assert store.db.execute(
        "SELECT COUNT(*) FROM trades WHERE strategy='R_OFI_EVENT_CUM'"
    ).fetchone()[0] == 0


def test_filtered_cumulative_ofi_requires_stronger_signal_without_changing_original() -> None:
    current = sample(timestamp_ns=1, seconds_left=60.0, current=True)
    cumulative = {"signal": 1.5, "event_count": 8}

    original = signal_for_strategy(
        "R_OFI_EVENT_CUM",
        current,
        None,
        fee_bps=200,
        slippage_bps=50.0,
        cumulative_event_ofi=cumulative,
    )
    filtered = signal_for_strategy(
        "R_OFI_EVENT_CUM_FILTERED",
        current,
        None,
        fee_bps=200,
        slippage_bps=50.0,
        cumulative_event_ofi=cumulative,
    )

    assert original is not None
    assert filtered is None


@pytest.mark.parametrize("ask", [0.39, 0.70])
def test_filtered_cumulative_ofi_rejects_prices_outside_040_to_069(ask: float) -> None:
    current = sample(timestamp_ns=1, seconds_left=60.0, current=True)
    current["up_ask"] = ask
    current["up_bid"] = ask - 0.01
    candidate = execution_candidate(
        "R_OFI_EVENT_CUM_FILTERED",
        {"side": "UP", "signal": 3.0, "event_ofi_count": 8},
        current,
        current,
        stake=5.0,
        minimum_stake=2.0,
        slippage_bps=50.0,
        max_spread=0.03,
        min_entry=0.05,
        max_book_age_ms=2000.0,
        max_book_skew_ms=500.0,
    )
    assert candidate is None


def test_filtered_cumulative_ofi_accepts_price_inside_range() -> None:
    current = sample(timestamp_ns=1, seconds_left=60.0, current=True)
    candidate = execution_candidate(
        "R_OFI_EVENT_CUM_FILTERED",
        {"side": "UP", "signal": 3.0, "event_ofi_count": 8},
        current,
        current,
        stake=5.0,
        minimum_stake=2.0,
        slippage_bps=50.0,
        max_spread=0.03,
        min_entry=0.05,
        max_book_age_ms=2000.0,
        max_book_skew_ms=500.0,
    )
    assert candidate is not None


def test_ofi_shadow_is_not_charged_to_primary_shared_cap(tmp_path) -> None:
    store = Store(tmp_path / "simulation.db")
    values = {key: False for key in DEFAULT_CONFIG if key.endswith("_enabled")}
    values["strategy_r_ofi_enabled"] = True
    values["strategy_r_ofi_min040_enabled"] = True
    store.update_config(values)
    store.update_config({"strategy_research_shared_cap_usdt": 5.0})
    store.maybe_enter_m_series(
        sample(timestamp_ns=10_000_000_000, seconds_left=70.2, current=False),
        200,
        realtime_context=prediction_context(1),
    )
    opened = store.maybe_enter_m_series(
        sample(timestamp_ns=20_200_000_000, seconds_left=60.0, current=True),
        200,
        realtime_context=prediction_context(2),
    )
    assert [item["strategy"] for item in opened] == ["R_OFI", "R_OFI_MIN040"]
    shadow = store.db.execute(
        "SELECT diagnostics_json FROM trades WHERE strategy='R_OFI_MIN040'"
    ).fetchone()
    diagnostics = json.loads(shadow["diagnostics_json"])
    assert diagnostics["shadow_only"] is True
    assert diagnostics["capital_model"] == "isolated_counterfactual_shadow_not_shared_cap"


def test_shared_cap_blocks_another_research_trade(tmp_path) -> None:
    store = Store(tmp_path / "simulation.db")
    paper_config(store, "R_OFI")
    store.update_config({"strategy_research_shared_cap_usdt": 5.0})
    store.open_trade(
        strategy="R_MICROPRICE",
        topic_id=1,
        market_id=10,
        side="UP",
        entry=0.50,
        target=None,
        stake=5.0,
        fee_rate_bps=200,
        note="existing shared exposure",
    )
    store.maybe_enter_m_series(
        sample(timestamp_ns=10_000_000_000, seconds_left=70.2, current=False),
        200,
        realtime_context=prediction_context(),
    )
    opened = store.maybe_enter_m_series(
        sample(timestamp_ns=20_200_000_000, seconds_left=60.0, current=True),
        200,
        realtime_context=prediction_context(),
    )
    assert opened == []
    assert store.db.execute(
        "SELECT COUNT(*) FROM trades WHERE strategy='R_OFI'"
    ).fetchone()[0] == 0


def test_research_strategies_cannot_reuse_reserved_book_depth(tmp_path) -> None:
    store = Store(tmp_path / "simulation.db")
    values = {key: False for key in DEFAULT_CONFIG if key.endswith("_enabled")}
    values["strategy_r_microprice_enabled"] = True
    values["strategy_r_consensus_enabled"] = True
    store.update_config(values)
    previous = sample(
        timestamp_ns=10_000_000_000, seconds_left=190.2, current=False
    )
    current = sample(
        timestamp_ns=20_200_000_000, seconds_left=180.0, current=True
    )
    current["up_ask_size"] = 15.0
    store.maybe_enter_m_series(
        previous, 200, realtime_context=prediction_context()
    )
    opened = store.maybe_enter_m_series(
        current, 200, realtime_context=prediction_context()
    )

    assert [item["strategy"] for item in opened] == ["R_MICROPRICE"]
    assert store.db.execute(
        "SELECT COUNT(*) FROM trades WHERE strategy LIKE 'R_%'"
    ).fetchone()[0] == 1


def test_minimum_stake_floor_and_live_isolation(tmp_path) -> None:
    store = Store(tmp_path / "simulation.db")
    with pytest.raises(ValueError, match="cannot be below 1.5"):
        store.update_config({"strategy_research_min_stake_usdt": 1.49})
    assert set(RESEARCH_STRATEGIES).isdisjoint(LIVE_FORWARDABLE_OBSERVER_STRATEGIES)
    assert "R_FUTURES_LEAD_REVERSE" in LIVE_FORWARDABLE_PAPER_STRATEGIES
    assert "R_FUTURES_LEAD_REGIME_REVERSE_3L" in (
        LIVE_FORWARDABLE_PAPER_STRATEGIES
    )
    assert "R_FUTURES_LEAD_DISTANCE" in LIVE_FORWARDABLE_PAPER_STRATEGIES
    assert {
        "R_FUTURES_LEAD_EXIT30",
        "R_FUTURES_LEAD_EXIT30_DISTANCE",
    }.isdisjoint(LIVE_FORWARDABLE_PAPER_STRATEGIES)
    assert all(
        strategy not in LIVE_FORWARDABLE_PAPER_STRATEGIES
        for strategy in FUTURES_LEAD_OBSERVER_STRATEGIES
    )
    assert all(
        strategy not in LIVE_FORWARDABLE_PAPER_STRATEGIES
        for strategy in OBSERVER_COMBINATION_STRATEGIES
    )
    assert all(
        strategy not in LIVE_FORWARDABLE_PAPER_STRATEGIES
        for strategy in CONTINUOUS_CALIBRATION_STRATEGIES
    )


def test_continuous_calibration_requires_causal_total_and_bucket_warmup() -> None:
    history = [
        {
            "market_id": index,
            "side": "UP",
            "signal": 0.3 if index < 4 else 1.2,
            "model_probability": None,
            "won": int(index % 2 == 0),
        }
        for index in range(20)
    ]

    decision = continuous_calibration_decision(
        "R_FUTURES_LEAD_CONTINUOUS_V2",
        source_side="UP",
        source_signal=0.3,
        source_probability=None,
        effective_cost=0.40,
        history=history,
    )

    assert decision["allowed"] is False
    assert decision["reason"] == "BUCKET_WARMUP"
    assert decision["history_samples"] == 20
    assert decision["bucket_samples"] == 4
    assert decision["causal_prior_official_only"] is True


def test_continuous_calibration_updates_value_and_lead_probabilities() -> None:
    value_history = [
        {
            "market_id": index,
            "side": "DOWN",
            "signal": 0.1,
            "model_probability": 0.65,
            "won": int(index < 15),
        }
        for index in range(20)
    ]
    value = continuous_calibration_decision(
        "R_CALIBRATED_VALUE_CONTINUOUS_V2",
        source_side="DOWN",
        source_signal=0.1,
        source_probability=0.65,
        effective_cost=0.55,
        history=value_history,
    )
    assert value["allowed"] is True
    assert value["reason"] == "ALLOW"
    assert value["calibrated_probability"] == pytest.approx(21.5 / 30)
    assert value["calibration_bucket"] == "DOWN:p0.6-0.7"

    lead_history = [
        {
            "market_id": index,
            "side": "UP",
            "signal": 0.3,
            "model_probability": None,
            "won": int(index < 14),
        }
        for index in range(20)
    ]
    lead = continuous_calibration_decision(
        "R_FUTURES_LEAD_CONTINUOUS_V2",
        source_side="UP",
        source_signal=0.3,
        source_probability=None,
        effective_cost=0.50,
        history=lead_history,
    )
    assert lead["allowed"] is True
    assert lead["reason"] == "ALLOW"
    assert lead["history_max_market_id"] == 19
    assert lead["calibrated_probability"] > 0.68


def test_continuous_lead_shadow_uses_only_prior_official_source_trades(
    tmp_path,
) -> None:
    store = Store(tmp_path / "simulation.db")
    values = {key: False for key in DEFAULT_CONFIG if key.endswith("_enabled")}
    values["strategy_r_futures_lead_enabled"] = True
    values["strategy_r_futures_lead_continuous_v2_enabled"] = True
    store.update_config(values)
    for index in range(20):
        market_id = 100 + index
        store.open_trade(
            strategy="R_FUTURES_LEAD",
            topic_id=market_id,
            market_id=market_id,
            side="UP",
            entry=0.42,
            target=None,
            stake=5.0,
            fee_rate_bps=200,
            note="continuous calibration history",
            diagnostics={"signal": 10.0},
        )
        store.settle_market(
            market_id,
            winner="UP" if index < 14 else "DOWN",
            official=True,
            topic_id=market_id,
            start_price=100.0,
            end_price=101.0 if index < 14 else 99.0,
        )
    store.open_trade(
        strategy="R_FUTURES_LEAD",
        topic_id=999,
        market_id=999,
        side="UP",
        entry=0.42,
        target=None,
        stake=5.0,
        fee_rate_bps=200,
        note="future market must be excluded",
        diagnostics={"signal": 10.0},
    )
    store.settle_market(
        999,
        winner="UP",
        official=True,
        topic_id=999,
        start_price=100.0,
        end_price=101.0,
    )

    previous = sample(
        timestamp_ns=10_000_000_000, seconds_left=184.0, current=False
    )
    current = sample(
        timestamp_ns=14_000_000_000, seconds_left=180.0, current=True
    )
    previous.update({"market_id": 200, "topic_id": 200})
    current.update({"market_id": 200, "topic_id": 200})
    store.maybe_enter_m_series(
        previous, 200, realtime_context=prediction_context(1)
    )
    opened = store.maybe_enter_m_series(
        current, 200, realtime_context=prediction_context(2)
    )

    assert [item["strategy"] for item in opened] == [
        "R_FUTURES_LEAD",
        "R_FUTURES_LEAD_CONTINUOUS_V2",
    ]
    shadow = store.db.execute(
        "SELECT strategy_version, diagnostics_json FROM trades "
        "WHERE strategy='R_FUTURES_LEAD_CONTINUOUS_V2'"
    ).fetchone()
    diagnostics = json.loads(shadow["diagnostics_json"])
    calibration = diagnostics["continuous_calibration"]
    assert shadow["strategy_version"].endswith("shadow_paper_v2")
    assert calibration["history_samples"] == 20
    assert calibration["history_max_market_id"] == 119
    assert diagnostics["official_history_only"] is True
    assert diagnostics["current_market_excluded_from_history"] is True
    assert diagnostics["source_trade_id"] > 0
    state = store._research_continuous_calibration_state(
        "R_FUTURES_LEAD_CONTINUOUS_V2"
    )
    assert state["status"] == "READY"
    assert state["officialSourceSamples"] == 21
    assert state["officialOnly"] is True
    assert state["causalNextMarketOnly"] is True


def observer_gate(**overrides) -> dict:
    gate = {
        "allowed": True,
        "status": "ALLOW",
        "profile": "F1",
        "dataQualityStatus": "READY",
        "historicalState": "UNCERTAIN",
        "historicalSampleCount": 12,
        "minSettledSamples": 6,
        "currentMarketId": 11,
        "currentRangeScore": 1,
        "currentEffectiveCrossovers": 2,
        "currentBothSidesTouched": False,
        "currentTrendVeto": False,
        "currentPhase": "EARLY_0_60S",
        "currentShortEr": 0.30,
        "currentMedianEr60s": None,
    }
    gate.update(overrides)
    return gate


@pytest.mark.parametrize("version", FUTURES_LEAD_OBSERVER_VERSIONS)
def test_frozen_futures_lead_observer_versions_allow_matching_gate(version) -> None:
    decision = futures_lead_observer_decision(
        version, observer_gate(), expected_market_id=11
    )
    assert decision["allowed"] is True


def test_futures_lead_observer_versions_keep_distinct_frozen_rules() -> None:
    historical_trend = observer_gate(
        allowed=False,
        status="BLOCK",
        historicalState="TREND",
        reason="historical TREND",
    )
    assert futures_lead_observer_decision("F1", historical_trend)["allowed"] is False
    assert futures_lead_observer_decision("V2", historical_trend)["allowed"] is True
    assert futures_lead_observer_decision(
        "V3", observer_gate(currentEffectiveCrossovers=1)
    )["allowed"] is False
    assert futures_lead_observer_decision(
        "V4", observer_gate(currentShortEr=0.36)
    )["allowed"] is False
    assert futures_lead_observer_decision(
        "V6", observer_gate(currentBothSidesTouched=True)
    )["allowed"] is False
    assert futures_lead_observer_decision(
        "V6", observer_gate(currentShortEr=None, currentMedianEr60s=None)
    )["allowed"] is True


@pytest.mark.parametrize(
    "gate",
    [None, observer_gate(dataQualityStatus="STALE"), observer_gate(currentMarketId=12)],
)
def test_futures_lead_observer_fails_closed_on_missing_stale_or_wrong_market(gate) -> None:
    assert futures_lead_observer_decision(
        "V2", gate, expected_market_id=11
    )["allowed"] is False


def test_signal_buffer_fails_closed_without_required_lag() -> None:
    buffer = ResearchSampleBuffer()
    current = buffer.append(
        11, sample(timestamp_ns=20_000_000_000, seconds_left=60.0, current=True)
    )
    assert current is not None
    assert buffer.lagged(11, current, 10.0) is None
    assert signal_for_strategy(
        "R_OFI", current, None, fee_bps=200, slippage_bps=50.0
    ) is None


def test_confirmed_futures_lead_requires_fresh_same_direction_two_windows() -> None:
    earlier = sample(timestamp_ns=10_000_000_000, seconds_left=183.0, current=False)
    middle = sample(timestamp_ns=13_000_000_000, seconds_left=180.0, current=False)
    current = sample(timestamp_ns=16_000_000_000, seconds_left=177.0, current=True)
    middle.update({"spot_price": 100.01, "futures_price": 100.02})
    current.update({"spot_price": 100.02, "futures_price": 100.04})

    confirmed = confirmed_futures_lead_signal(
        current,
        middle,
        earlier,
        min_residual_bps=0.25,
        max_source_age_ms=500.0,
    )

    assert confirmed is not None
    assert confirmed["side"] == "UP"
    assert confirmed["confirmation_windows"] == 2
    assert confirmed["same_direction_required"] is True
    current["spot_price"] = 99.99
    assert confirmed_futures_lead_signal(
        current,
        middle,
        earlier,
        min_residual_bps=0.25,
        max_source_age_ms=500.0,
    ) is None
    current.update({"spot_price": 100.02, "spot_age_ms": 501.0})
    assert confirmed_futures_lead_signal(
        current,
        middle,
        earlier,
        min_residual_bps=0.25,
        max_source_age_ms=500.0,
    ) is None


def test_terminal_probability_uses_strike_distance_and_remaining_volatility() -> None:
    current = sample(timestamp_ns=1, seconds_left=180.0, current=True)
    current.update({"start_price": 100.0, "spot_price": 100.1})

    up = terminal_probability_from_distance(
        current, "UP", sigma_per_sqrt_second=0.00005
    )
    down = terminal_probability_from_distance(
        current, "DOWN", sigma_per_sqrt_second=0.00005
    )

    assert up is not None and down is not None
    assert up["model_probability"] > 0.5
    assert down["model_probability"] == pytest.approx(
        1.0 - up["model_probability"]
    )
    assert up["distance_to_strike_bps"] > 0
    assert up["remaining_sigma_bps"] > 0


def _experiment_snapshot(second: int) -> dict:
    timestamp_ns = (1_000 + second) * 1_000_000_000
    row = sample(
        timestamp_ns=timestamp_ns,
        seconds_left=240.0 - second,
        current=True,
    )
    row["timestamp"] = datetime.fromtimestamp(
        timestamp_ns / 1e9, tz=timezone.utc
    ).isoformat()
    # Create causal realized variance during warm-up, then two confirmed UP legs.
    if second < 57:
        row["spot_price"] = 100.0 + (0.01 if second % 2 else 0.0)
        row["futures_price"] = row["spot_price"]
    elif second < 60:
        row.update({"spot_price": 100.0, "futures_price": 100.0})
    elif second < 63:
        row.update({"spot_price": 100.01, "futures_price": 100.02})
    else:
        row.update({"spot_price": 100.02, "futures_price": 100.04})
    return row


def _enable_futures_lead_experiments(store: Store) -> None:
    values = {key: False for key in DEFAULT_CONFIG if key.endswith("_enabled")}
    for strategy in FUTURES_LEAD_EXPERIMENT_STRATEGIES:
        values[f"strategy_{strategy.lower()}_enabled"] = True
    store.update_config(values)


def test_three_futures_lead_experiments_open_as_independent_frozen_cohorts(
    tmp_path,
) -> None:
    store = Store(tmp_path / "simulation.db")
    _enable_futures_lead_experiments(store)
    opened = []
    for second in range(64):
        opened = store.maybe_enter_m_series(
            _experiment_snapshot(second),
            200,
            realtime_context=prediction_context(second + 1),
        ) or opened

    assert {item["strategy"] for item in opened} == set(
        FUTURES_LEAD_EXPERIMENT_STRATEGIES
    )
    rows = store.db.execute(
        "SELECT strategy, model_probability, model_edge, model_sigma, "
        "diagnostics_json FROM trades ORDER BY strategy"
    ).fetchall()
    assert len(rows) == 3
    for row in rows:
        diagnostics = json.loads(row["diagnostics_json"])
        assert diagnostics["chronological_sample_index"] == 1
        assert diagnostics["chronological_segment"] == "development"
        assert diagnostics["thresholds_frozen_before_collection"] is True
        assert diagnostics["confirmation_windows"] == 2
    distance_rows = [
        row for row in rows if "DISTANCE" in str(row["strategy"])
    ]
    assert all(row["model_probability"] is not None for row in distance_rows)
    assert all(row["model_edge"] is not None for row in distance_rows)
    assert all(row["model_sigma"] is not None for row in distance_rows)
    assert "R_FUTURES_LEAD_DISTANCE" in LIVE_FORWARDABLE_PAPER_STRATEGIES
    assert {
        "R_FUTURES_LEAD_EXIT30",
        "R_FUTURES_LEAD_EXIT30_DISTANCE",
    }.isdisjoint(LIVE_FORWARDABLE_PAPER_STRATEGIES)
    validation = store._research_experiment_validation_state(
        "R_FUTURES_LEAD_EXIT30"
    )
    assert validation["status"] == "COLLECTING_MINIMUM"
    assert validation["samples"] == 1
    assert validation["thresholdsFrozen"] is True


def test_30_second_variants_exit_on_first_fresh_full_depth_bid(tmp_path) -> None:
    store = Store(tmp_path / "simulation.db")
    _enable_futures_lead_experiments(store)
    for second in range(64):
        store.maybe_enter_m_series(
            _experiment_snapshot(second),
            200,
            realtime_context=prediction_context(second + 1),
        )
    exit_snapshot = _experiment_snapshot(93)
    exit_snapshot.update({
        "seconds_left": 147.0,
        "up_bid": 0.50,
        "up_bid_size": 1.0,
        "book_age_ms": 50.0,
    })

    store.maybe_enter_m_series(
        exit_snapshot,
        200,
        realtime_context=prediction_context(94),
    )
    assert store.db.execute(
        "SELECT status FROM trades WHERE strategy='R_FUTURES_LEAD_EXIT30'"
    ).fetchone()[0] == "OPEN"
    full_depth_snapshot = _experiment_snapshot(94)
    full_depth_snapshot.update({
        "seconds_left": 146.0,
        "up_bid": 0.50,
        "up_bid_size": 100.0,
        "book_age_ms": 50.0,
    })
    store.maybe_enter_m_series(
        full_depth_snapshot,
        200,
        realtime_context=prediction_context(95),
    )

    rows = {
        row["strategy"]: row
        for row in store.db.execute(
            "SELECT strategy, status, exit_price, pnl, diagnostics_json FROM trades"
        ).fetchall()
    }
    assert rows["R_FUTURES_LEAD_EXIT30"]["status"] == "TIMEOUT_EXIT"
    assert rows["R_FUTURES_LEAD_EXIT30_DISTANCE"]["status"] == "TIMEOUT_EXIT"
    assert rows["R_FUTURES_LEAD_DISTANCE"]["status"] == "OPEN"
    assert rows["R_FUTURES_LEAD_EXIT30"]["exit_price"] == pytest.approx(0.50)
    diagnostics = json.loads(
        rows["R_FUTURES_LEAD_EXIT30"]["diagnostics_json"]
    )
    assert diagnostics["time_exit"]["scheduled_seconds"] == 30.0
    assert diagnostics["time_exit"]["actual_elapsed_seconds"] == pytest.approx(31.0)
    assert diagnostics["time_exit"]["required_shares"] <= 100.0


@pytest.mark.parametrize(
    ("sample_index", "segment"),
    ((1, "development"), (60, "development"), (61, "validation"),
     (80, "validation"), (81, "holdout"), (100, "holdout"),
     (101, "confirmation"), (200, "confirmation"),
     (201, "post_confirmation")),
)
def test_futures_lead_experiment_split_is_frozen_chronologically(
    sample_index: int, segment: str
) -> None:
    assert Store._research_experiment_segment(sample_index) == segment
