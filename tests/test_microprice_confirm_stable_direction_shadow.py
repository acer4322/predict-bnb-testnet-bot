from __future__ import annotations

import json
import sqlite3

from predict_bot import live_trading, research_forward, server
from predict_bot.microprice_confirm_stable_direction_shadow import (
    BASE_STRATEGY,
    HORIZON_SECONDS,
    MAX_RAW_TOP_ASK_EXCLUSIVE,
    MIN_MIDPOINT_DELTA,
    MIN_RAW_TOP_ASK,
    STRICT_MIN_EFFICIENCY_RATIO,
    STRICT_STRATEGY,
    open_stable_direction_shadow,
    open_stable_direction_strict_shadow,
    stable_direction_base_decision,
    stable_direction_strict_decision,
)


class FakeStore:
    def __init__(self, config: dict | None = None) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            """CREATE TABLE trades(
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   strategy TEXT NOT NULL,
                   market_id INTEGER NOT NULL,
                   strategy_version TEXT,
                   diagnostics_json TEXT
               )"""
        )
        self.opened: list[dict] = []
        self._config = {
            f"strategy_{BASE_STRATEGY.lower()}_enabled": True,
            f"strategy_{STRICT_STRATEGY.lower()}_enabled": True,
            f"strategy_{BASE_STRATEGY.lower()}_stake": 5.0,
            f"strategy_{STRICT_STRATEGY.lower()}_stake": 5.0,
            **(config or {}),
        }

    def config(self) -> dict:
        return dict(self._config)

    def open_trade(self, **values):
        self.opened.append(values)
        self.db.execute(
            "INSERT INTO trades(strategy, market_id, strategy_version, diagnostics_json) VALUES (?,?,?,?)",
            (
                values["strategy"],
                values["market_id"],
                values.get("strategy_version"),
                json.dumps(values.get("diagnostics") or {}),
            ),
        )
        self.db.commit()


def source(store: FakeStore, *, ask: float, delta: float) -> dict:
    diagnostics = {
        "raw_top_ask": ask,
        "midpoint_delta": delta,
        "confirmation_count": 3,
        "samples": [
            {"side": "UP"},
            {"side": "UP"},
            {"side": "UP"},
        ],
    }
    store.db.execute(
        "INSERT INTO trades(strategy, market_id, strategy_version, diagnostics_json) VALUES (?,?,?,?)",
        (
            "R_MICROPRICE_CONFIRM",
            7001,
            "SOURCE_V1",
            json.dumps(diagnostics),
        ),
    )
    store.db.commit()
    return {
        "strategy": "R_MICROPRICE_CONFIRM",
        "topic_id": 11,
        "market_id": 7001,
        "side": "UP",
        "entry_price": ask * 1.005,
        "raw_top_ask": ask,
        "stake": 5.0,
    }


def strict_context(**overrides) -> dict:
    return {
        "currentMarketId": 7001,
        "marketMatches": True,
        "currentEffectiveCrossovers": 1,
        "currentShortEr": STRICT_MIN_EFFICIENCY_RATIO,
        "currentMedianEr60s": 0.60,
        "currentBothSidesTouched": False,
        "currentStartMoveBps": 2.0,
        "signalAlignedWithStartMove": True,
        "directionFlipCount": 0,
        "dataQualityStatus": "READY",
        "indicatorReadiness": "READY",
        **overrides,
    }


def test_base_decision_requires_price_band_and_midpoint_advance() -> None:
    assert stable_direction_base_decision(
        MIN_RAW_TOP_ASK, MIN_MIDPOINT_DELTA
    )["allowed"] is True
    assert stable_direction_base_decision(
        MAX_RAW_TOP_ASK_EXCLUSIVE - 1e-8, 0.02
    )["allowed"] is True
    assert stable_direction_base_decision(
        MIN_RAW_TOP_ASK - 1e-8, 0.02
    )["allowed"] is False
    assert stable_direction_base_decision(
        MAX_RAW_TOP_ASK_EXCLUSIVE, 0.02
    )["allowed"] is False
    assert stable_direction_base_decision(
        0.70, MIN_MIDPOINT_DELTA - 1e-8
    )["allowed"] is False
    assert stable_direction_base_decision(None, 0.02)["allowed"] is False


def test_strict_requires_every_entry_time_trend_field() -> None:
    assert stable_direction_strict_decision(
        0.70, 0.015, strict_context()
    )["allowed"] is True
    assert stable_direction_strict_decision(
        0.70, 0.015, strict_context(currentEffectiveCrossovers=2)
    )["allowed"] is False
    assert stable_direction_strict_decision(
        0.70, 0.015, strict_context(currentShortEr=0.54)
    )["allowed"] is False
    assert stable_direction_strict_decision(
        0.70, 0.015, strict_context(currentBothSidesTouched=True)
    )["allowed"] is False
    assert stable_direction_strict_decision(
        0.70, 0.015, strict_context(signalAlignedWithStartMove=False)
    )["allowed"] is False
    assert stable_direction_strict_decision(
        0.70, 0.015, strict_context(directionFlipCount=1)
    )["allowed"] is False
    assert stable_direction_strict_decision(
        0.70, 0.015, strict_context(dataQualityStatus="MISSING_OR_STALE")
    )["allowed"] is False
    assert stable_direction_strict_decision(
        0.70, 0.015, None
    )["allowed"] is False


def test_base_and_strict_open_independent_paper_ledgers() -> None:
    store = FakeStore()
    item = source(store, ask=0.70, delta=0.015)
    base = open_stable_direction_shadow(store, item, 200)
    strict = open_stable_direction_strict_shadow(
        store, item, 200, strict_context()
    )
    assert base is not None and base["strategy"] == BASE_STRATEGY
    assert strict is not None and strict["strategy"] == STRICT_STRATEGY
    assert len(store.opened) == 2
    assert all(item["diagnostics"]["paper_only"] for item in store.opened)
    assert all(
        item["diagnostics"]["live_orders_affected"] is False
        for item in store.opened
    )
    assert store.opened[0]["diagnostics"]["rule"]["observerRequired"] is False
    assert store.opened[1]["diagnostics"]["rule"]["observerRequired"] is True


def test_each_dashboard_toggle_controls_its_own_shadow() -> None:
    store = FakeStore(
        {
            f"strategy_{BASE_STRATEGY.lower()}_enabled": False,
            f"strategy_{STRICT_STRATEGY.lower()}_enabled": True,
        }
    )
    item = source(store, ask=0.70, delta=0.015)
    assert open_stable_direction_shadow(store, item, 200) is None
    strict = open_stable_direction_strict_shadow(
        store, item, 200, strict_context()
    )
    assert strict is not None
    assert [opened["strategy"] for opened in store.opened] == [STRICT_STRATEGY]


def test_each_shadow_uses_its_own_configured_stake() -> None:
    store = FakeStore(
        {
            f"strategy_{BASE_STRATEGY.lower()}_stake": 3.0,
            f"strategy_{STRICT_STRATEGY.lower()}_stake": 4.0,
        }
    )
    item = source(store, ask=0.70, delta=0.015)
    open_stable_direction_shadow(store, item, 200)
    open_stable_direction_strict_shadow(
        store, item, 200, strict_context()
    )
    assert [opened["stake"] for opened in store.opened] == [3.0, 4.0]


def test_strict_fails_closed_when_observer_data_is_missing() -> None:
    store = FakeStore()
    item = source(store, ask=0.70, delta=0.015)
    assert open_stable_direction_strict_shadow(
        store, item, 200, None
    ) is None
    assert store.opened == []


def test_both_ids_are_native_research_shadows_and_never_live_selectable() -> None:
    for strategy in (BASE_STRATEGY, STRICT_STRATEGY):
        assert strategy in research_forward.SHADOW_RESEARCH_STRATEGIES
        assert strategy in research_forward.RESEARCH_STRATEGIES
        assert strategy not in research_forward.PRIMARY_RESEARCH_STRATEGIES
        assert strategy not in research_forward.GENERIC_SIGNAL_RESEARCH_STRATEGIES
        assert strategy in research_forward.DERIVED_RESEARCH_STRATEGIES
        assert strategy not in live_trading.LIVE_SUPPORTED_STRATEGIES
        assert strategy not in live_trading.LIVE_RESEARCH_STRATEGIES
        assert server.DEFAULT_CONFIG[
            f"strategy_{strategy.lower()}_enabled"
        ] is True


def test_metadata_keeps_horizon_without_activating_generic_sampling() -> None:
    for strategy in (BASE_STRATEGY, STRICT_STRATEGY):
        params = research_forward.RESEARCH_PARAMETERS[strategy]
        assert params["horizon"] == HORIZON_SECONDS
        assert research_forward.sampling_active(
            HORIZON_SECONDS, {strategy}
        ) is False
        assert research_forward.signal_for_strategy(
            strategy,
            {},
            None,
            fee_bps=200,
            slippage_bps=50.0,
        ) is None
