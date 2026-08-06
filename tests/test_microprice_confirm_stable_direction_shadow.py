from __future__ import annotations

import json
import sqlite3

from predict_bot import live_trading, research_forward
from predict_bot.microprice_confirm_stable_direction_shadow import (
    MAX_RAW_TOP_ASK_EXCLUSIVE,
    MIN_MIDPOINT_DELTA,
    MIN_RAW_TOP_ASK,
    STRATEGY,
    open_stable_direction_shadow,
    stable_direction_signal_decision,
)


class FakeStore:
    def __init__(self) -> None:
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
        "samples": [{"side": "UP"}, {"side": "UP"}, {"side": "UP"}],
    }
    store.db.execute(
        "INSERT INTO trades(strategy, market_id, strategy_version, diagnostics_json) VALUES (?,?,?,?)",
        ("R_MICROPRICE_CONFIRM", 7001, "SOURCE_V1", json.dumps(diagnostics)),
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


def test_decision_requires_price_band_and_midpoint_advance() -> None:
    assert stable_direction_signal_decision(MIN_RAW_TOP_ASK, MIN_MIDPOINT_DELTA)["allowed"] is True
    assert stable_direction_signal_decision(MAX_RAW_TOP_ASK_EXCLUSIVE - 1e-8, 0.02)["allowed"] is True
    assert stable_direction_signal_decision(MIN_RAW_TOP_ASK - 1e-8, 0.02)["allowed"] is False
    assert stable_direction_signal_decision(MAX_RAW_TOP_ASK_EXCLUSIVE, 0.02)["allowed"] is False
    assert stable_direction_signal_decision(0.70, MIN_MIDPOINT_DELTA - 1e-8)["allowed"] is False
    assert stable_direction_signal_decision(None, 0.02)["allowed"] is False


def test_shadow_opens_from_confirm_source_without_observer() -> None:
    store = FakeStore()
    opened = open_stable_direction_shadow(store, source(store, ask=0.70, delta=0.015), 200)
    assert opened is not None
    assert opened["strategy"] == STRATEGY
    assert opened["paper_only"] is True
    assert opened["live_forwardable_when_selected"] is False
    assert len(store.opened) == 1
    diagnostics = store.opened[0]["diagnostics"]
    assert diagnostics["rule"]["observerRequired"] is False
    assert diagnostics["rule"]["f1RangeScoreRequired"] is False


def test_shadow_fails_closed_when_midpoint_delta_is_missing() -> None:
    store = FakeStore()
    item = source(store, ask=0.70, delta=0.005)
    assert open_stable_direction_shadow(store, item, 200) is None
    assert store.opened == []


def test_strategy_is_native_research_shadow_and_never_live_selectable() -> None:
    assert STRATEGY in research_forward.SHADOW_RESEARCH_STRATEGIES
    assert STRATEGY in research_forward.RESEARCH_STRATEGIES
    assert STRATEGY not in live_trading.LIVE_SUPPORTED_STRATEGIES
    assert STRATEGY not in live_trading.LIVE_RESEARCH_STRATEGIES
