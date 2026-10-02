from __future__ import annotations

import json
import sqlite3
from typing import Any

from predict_bot import live_trading, research_forward
from predict_bot.microprice_confirm_observer_guard_patch import (
    COMBINATION_STRATEGY,
    OBSERVER_VERSION,
    SOURCE_STRATEGY,
    _wrap_dashboard,
    _wrap_open_trade,
    microprice_confirm_observer_guard_decision,
)


def gate(
    *,
    market_id: int = 123,
    state: str = "UNCERTAIN",
    range_score: int = 2,
    trend_score: int = 3,
    samples: int = 20,
    quality: str = "READY",
) -> dict[str, Any]:
    return {
        "profile": "F1",
        "dataQualityStatus": quality,
        "historicalSampleCount": samples,
        "minSettledSamples": 6,
        "historicalState": state,
        "historicalRangeScore": range_score,
        "historicalTrendScore": trend_score,
        "historicalProvisional": False,
        "currentMarketId": market_id,
    }


def source_diagnostics(observer_gate: dict[str, Any]) -> dict[str, Any]:
    return {
        "realtime_context": {
            "m01o_observer_gates": {"F1": observer_gate},
        }
    }


def test_guard_blocks_trend_leaning_uncertain_transition() -> None:
    decision = microprice_confirm_observer_guard_decision(
        gate(),
        expected_market_id=123,
    )
    assert decision["allowed"] is False
    assert decision["status"] == "BLOCK"
    assert decision["transitionRisk"] is True
    assert decision["historicalState"] == "UNCERTAIN"
    assert decision["historicalRangeScore"] == 2
    assert decision["historicalTrendScore"] == 3
    assert decision["currentRoundOutcomeUsed"] is False


def test_guard_allows_other_historical_states_and_scores() -> None:
    cases = (
        gate(state="RANGE", range_score=4, trend_score=2),
        gate(state="TREND", range_score=0, trend_score=5),
        gate(state="UNCERTAIN", range_score=3, trend_score=3),
        gate(state="UNCERTAIN", range_score=2, trend_score=2),
    )
    for current in cases:
        decision = microprice_confirm_observer_guard_decision(
            current,
            expected_market_id=123,
        )
        assert decision["allowed"] is True
        assert decision["status"] == "ALLOW"
        assert decision["transitionRisk"] is False


def test_guard_fails_closed_for_missing_stale_or_wrong_market_gate() -> None:
    assert microprice_confirm_observer_guard_decision(None)["allowed"] is False
    assert microprice_confirm_observer_guard_decision(
        gate(samples=5), expected_market_id=123
    )["allowed"] is False
    assert microprice_confirm_observer_guard_decision(
        gate(quality="DEGRADED"), expected_market_id=123
    )["allowed"] is False
    assert microprice_confirm_observer_guard_decision(
        gate(), expected_market_id=999
    )["allowed"] is False


def test_installer_registers_observer_version_not_live_strategy() -> None:
    assert OBSERVER_VERSION in research_forward.FUTURES_LEAD_OBSERVER_VERSIONS
    assert OBSERVER_VERSION in live_trading.FUTURES_LEAD_OBSERVER_VERSIONS
    assert SOURCE_STRATEGY in live_trading.LIVE_OBSERVER_STRATEGIES
    assert SOURCE_STRATEGY in live_trading.LIVE_SUPPORTED_STRATEGIES
    assert COMBINATION_STRATEGY not in live_trading.LIVE_SUPPORTED_STRATEGIES

    blocked = live_trading.futures_lead_observer_decision(
        OBSERVER_VERSION,
        gate(),
        expected_market_id=123,
    )
    assert blocked["allowed"] is False
    allowed = live_trading.futures_lead_observer_decision(
        OBSERVER_VERSION,
        gate(state="RANGE", range_score=4, trend_score=2),
        expected_market_id=123,
    )
    assert allowed["allowed"] is True


class FakeStore:
    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            """CREATE TABLE trades (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   strategy TEXT NOT NULL,
                   market_id INTEGER NOT NULL,
                   side TEXT NOT NULL,
                   status TEXT NOT NULL,
                   entry_price REAL NOT NULL,
                   stake REAL NOT NULL,
                   pnl REAL,
                   opened_at TEXT NOT NULL,
                   closed_at TEXT,
                   diagnostics_json TEXT
               )"""
        )

    def open_trade(
        self,
        *,
        strategy: str,
        topic_id: int,
        market_id: int,
        side: str,
        entry: float,
        target: float | None,
        stake: float,
        fee_rate_bps: int,
        note: str,
        strategy_version: str | None = None,
        model_probability: float | None = None,
        model_edge: float | None = None,
        model_sigma: float | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        self.db.execute(
            """INSERT INTO trades(
                   strategy, market_id, side, status, entry_price, stake,
                   pnl, opened_at, closed_at, diagnostics_json
               ) VALUES (?, ?, ?, 'OPEN', ?, ?, NULL, ?, NULL, ?)""",
            (
                strategy,
                market_id,
                side,
                entry,
                stake,
                "2026-08-06T00:00:00+00:00",
                json.dumps(diagnostics or {}, sort_keys=True),
            ),
        )
        self.db.commit()

    def dashboard(self) -> dict[str, Any]:
        return {"researchForward": {"strategies": {}}, "summaries": {}}


def test_source_open_creates_only_allowed_paper_combination_shadow() -> None:
    _wrap_open_trade(FakeStore)
    store = FakeStore()

    store.open_trade(
        strategy=SOURCE_STRATEGY,
        topic_id=1,
        market_id=100,
        side="UP",
        entry=0.4,
        target=None,
        stake=5.0,
        fee_rate_bps=200,
        note="source",
        diagnostics=source_diagnostics(
            gate(market_id=100, state="RANGE", range_score=4, trend_score=2)
        ),
    )
    rows = store.db.execute(
        "SELECT strategy, diagnostics_json FROM trades WHERE market_id=100 ORDER BY id"
    ).fetchall()
    assert [row["strategy"] for row in rows] == [SOURCE_STRATEGY, COMBINATION_STRATEGY]
    shadow_diagnostics = json.loads(rows[1]["diagnostics_json"])
    assert shadow_diagnostics["paper_only"] is True
    assert shadow_diagnostics["live_orders_affected"] is False
    assert shadow_diagnostics["observer_decision"]["allowed"] is True

    store.open_trade(
        strategy=SOURCE_STRATEGY,
        topic_id=2,
        market_id=101,
        side="DOWN",
        entry=0.5,
        target=None,
        stake=5.0,
        fee_rate_bps=200,
        note="source",
        diagnostics=source_diagnostics(gate(market_id=101)),
    )
    blocked_rows = store.db.execute(
        "SELECT strategy FROM trades WHERE market_id=101 ORDER BY id"
    ).fetchall()
    assert [row["strategy"] for row in blocked_rows] == [SOURCE_STRATEGY]


def test_dashboard_injects_guard_observation_stats() -> None:
    _wrap_open_trade(FakeStore)
    _wrap_dashboard(FakeStore)
    store = FakeStore()
    store.open_trade(
        strategy=SOURCE_STRATEGY,
        topic_id=1,
        market_id=200,
        side="UP",
        entry=0.4,
        target=None,
        stake=5.0,
        fee_rate_bps=200,
        note="source",
        diagnostics=source_diagnostics(gate(market_id=200)),
    )
    payload = store.dashboard()
    experiment = payload["researchForward"]["micropriceConfirmObserverGuard"]
    assert experiment["evaluations"] == 1
    assert experiment["blockedEvaluations"] == 1
    assert experiment["registeredAsLiveStrategy"] is False
    assert payload["summaries"][COMBINATION_STRATEGY]["trades"] == 0
