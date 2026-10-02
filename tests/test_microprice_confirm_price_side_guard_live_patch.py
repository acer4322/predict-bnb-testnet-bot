from __future__ import annotations

import sqlite3
from decimal import Decimal
from types import SimpleNamespace

from predict_bot import live_trading, m_realtime, research_forward
from predict_bot import microprice_confirm_price_side_guard_live_patch as patch
from predict_bot.microprice_confirm_optimization_shadows import (
    OPTIMIZATION_VERSION,
    PRICE_SIDE_GUARD_STRATEGY,
    SOURCE_STRATEGY,
)


def test_price_side_guard_is_registered_for_explicit_live_selection() -> None:
    strategy = PRICE_SIDE_GUARD_STRATEGY

    assert patch.PRICE_SIDE_GUARD_LIVE_VERSION.endswith("_V1")
    assert strategy in live_trading.LIVE_RESEARCH_STRATEGIES
    assert strategy in live_trading.LIVE_SUPPORTED_STRATEGIES
    assert strategy in m_realtime.LIVE_RESEARCH_STRATEGIES
    assert strategy in m_realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES
    assert strategy in research_forward.CONFIRMATION_ADD_SOURCE_STRATEGIES
    assert strategy in live_trading.CONFIRMATION_ADD_SOURCE_STRATEGIES
    assert live_trading.LIVE_RESEARCH_REPRICE_GAPS[strategy] == Decimal("0.05")


def test_live_rules_accept_price_side_guard_in_fixed_and_confirmation_add_modes() -> None:
    base = {
        "strategy": PRICE_SIDE_GUARD_STRATEGY,
        "strategies": [PRICE_SIDE_GUARD_STRATEGY],
        "maxStakeUsdt": 5.0,
        "strategyStakesUsdt": [5.0],
        "strategyExecutionModes": ["FIXED"],
        "strategyInitialStakesUsdt": [1.0],
        "strategyConfirmationAddStakesUsdt": [1.0],
        "minHourlyWinRatePct": 50.0,
        "maxHourlyWinThenLossRatePct": 50.0,
        "futuresLeadObserverEnabled": False,
        "futuresLeadObserverVersion": "F1",
        "strategyObserverEnabled": [False],
        "strategyObserverVersions": ["F1"],
        "strategyDrawdownControlEnabled": [False],
        "strategyLossCooldownEnabled": [False],
        "reliabilityGateTags": [],
    }
    fixed = live_trading.normalize_live_rules(base)
    assert fixed["strategies"] == [PRICE_SIDE_GUARD_STRATEGY]
    assert fixed["strategyExecutionModes"] == ["FIXED"]

    confirmation = live_trading.normalize_live_rules(
        {**base, "strategyExecutionModes": ["CONFIRMATION_ADD"]}
    )
    assert confirmation["strategyExecutionModes"] == ["CONFIRMATION_ADD"]
    assert confirmation["strategyInitialStakesUsdt"] == [1.0]
    assert confirmation["strategyConfirmationAddStakesUsdt"] == [1.0]


def test_signed_live_signal_rechecks_direction_price_dead_zones() -> None:
    gate = live_trading.LiveM0WEngine._research_signal_price_is_allowed

    allowed, reason = gate(
        {"side": "UP", "entry_price": 0.399999},
        PRICE_SIDE_GUARD_STRATEGY,
    )
    assert allowed is False
    assert "UP_ENTRY_BELOW_040" in reason

    allowed, reason = gate(
        {"side": "DOWN", "entry_price": 0.55},
        PRICE_SIDE_GUARD_STRATEGY,
    )
    assert allowed is False
    assert "DOWN_ENTRY_050_060" in reason

    assert gate(
        {"side": "UP", "entry_price": 0.40},
        PRICE_SIDE_GUARD_STRATEGY,
    ) == (True, "")
    assert gate(
        {"side": "DOWN", "entry_price": 0.4999},
        PRICE_SIDE_GUARD_STRATEGY,
    ) == (True, "")
    assert gate(
        {"side": "DOWN", "entry_price": 0.60},
        PRICE_SIDE_GUARD_STRATEGY,
    ) == (True, "")


def test_down_below_050_cannot_reprice_into_excluded_band() -> None:
    maximum = live_trading.LiveM0WEngine._maximum_reprice_limit(
        {"side": "DOWN", "entry_price": 0.49},
        PRICE_SIDE_GUARD_STRATEGY,
        Decimal("0.49"),
    )
    assert maximum == Decimal("0.49999999")

    above_dead_zone = live_trading.LiveM0WEngine._maximum_reprice_limit(
        {"side": "DOWN", "entry_price": 0.61},
        PRICE_SIDE_GUARD_STRATEGY,
        Decimal("0.61"),
    )
    assert above_dead_zone == Decimal("0.66")


def _tracker_with_guard_trade(*, side: str, entry: float):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        """CREATE TABLE trades (
               id INTEGER PRIMARY KEY,
               strategy TEXT,
               market_id INTEGER,
               side TEXT,
               entry_price REAL,
               strategy_version TEXT
           )"""
    )
    db.execute(
        """INSERT INTO trades(
               id, strategy, market_id, side, entry_price, strategy_version
           ) VALUES (1, ?, 101, ?, ?, ?)""",
        (
            PRICE_SIDE_GUARD_STRATEGY,
            side,
            entry,
            OPTIMIZATION_VERSION,
        ),
    )
    db.commit()
    return SimpleNamespace(store=SimpleNamespace(db=db))


def test_passing_paper_shadow_is_exposed_as_live_forwardable_candidate() -> None:
    tracker = _tracker_with_guard_trade(side="UP", entry=0.45)
    source = {
        "strategy": SOURCE_STRATEGY,
        "topic_id": 202,
        "market_id": 101,
        "side": "UP",
        "entry_price": 0.45,
        "stake": 5.0,
        "paper_only": True,
    }

    opened = patch._append_guard_candidate(tracker, [source])

    assert [row["strategy"] for row in opened] == [
        SOURCE_STRATEGY,
        PRICE_SIDE_GUARD_STRATEGY,
    ]
    candidate = opened[-1]
    assert candidate["paper_only"] is True
    assert candidate["live_forwardable_when_selected"] is True
    assert candidate["price_side_guard_passed"] is True
    assert candidate["variant_mode"] == "FOLLOW_V2_WITH_PRICE_SIDE_GUARD"


def test_blocked_source_is_not_exposed_to_live_executor() -> None:
    tracker = _tracker_with_guard_trade(side="UP", entry=0.35)
    source = {
        "strategy": SOURCE_STRATEGY,
        "market_id": 101,
        "side": "UP",
        "entry_price": 0.35,
    }

    assert patch._append_guard_candidate(tracker, [source]) == [source]
