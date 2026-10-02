from __future__ import annotations

import json
import sqlite3
from typing import Any

from predict_bot.microprice_confirm_observer_guard_patch import (
    COMBINATION_STRATEGY,
    SOURCE_STRATEGY,
    _database_state,
    _wrap_open_trade,
)
from predict_bot.microprice_confirm_observer_guard_realtime_patch import (
    _wrap_store_paper_context,
)


def observer_gate(
    *,
    market_id: int,
    state: str,
    range_score: int,
    trend_score: int,
) -> dict[str, Any]:
    return {
        "profile": "F1",
        "dataQualityStatus": "READY",
        "historicalSampleCount": 20,
        "minSettledSamples": 6,
        "historicalState": state,
        "historicalRangeScore": range_score,
        "historicalTrendScore": trend_score,
        "historicalProvisional": False,
        "currentMarketId": market_id,
    }


class PaperStore:
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
                "2026-08-06T01:00:00+00:00",
                json.dumps(diagnostics or {}, sort_keys=True),
            ),
        )
        self.db.commit()

    def maybe_enter_m_series(
        self,
        snapshot: dict[str, Any],
        fee_bps: int,
        *,
        realtime_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        self.open_trade(
            strategy=SOURCE_STRATEGY,
            topic_id=1,
            market_id=int(snapshot["market_id"]),
            side="UP",
            entry=0.40,
            target=None,
            stake=5.0,
            fee_rate_bps=fee_bps,
            note="paper source",
            strategy_version="PAPER_SOURCE_V1",
            diagnostics={"paper_only": True},
        )
        return []


def prepared_store() -> PaperStore:
    _wrap_open_trade(PaperStore)
    store = PaperStore()
    _wrap_store_paper_context(store)
    return store


def test_allowed_paper_simulation_creates_guard_shadow_without_live_order() -> None:
    store = prepared_store()
    market_id = 7001
    gate = observer_gate(
        market_id=market_id,
        state="RANGE",
        range_score=4,
        trend_score=2,
    )

    store.maybe_enter_m_series(
        {"market_id": market_id},
        200,
        realtime_context={"m01o_observer_gates": {"F1": gate}},
    )

    rows = store.db.execute(
        "SELECT strategy, diagnostics_json FROM trades ORDER BY id"
    ).fetchall()
    assert [row["strategy"] for row in rows] == [
        SOURCE_STRATEGY,
        COMBINATION_STRATEGY,
    ]
    source_diagnostics = json.loads(rows[0]["diagnostics_json"])
    assert source_diagnostics["paper_only"] is True
    assert source_diagnostics["realtime_context"]["m01o_observer_gates"]["F1"] == gate
    shadow_diagnostics = json.loads(rows[1]["diagnostics_json"])
    assert shadow_diagnostics["paper_only"] is True
    assert shadow_diagnostics["live_orders_affected"] is False
    assert shadow_diagnostics["observer_decision"]["allowed"] is True

    state = _database_state(store)
    assert state["allowedEvaluations"] == 1
    assert state["blockedEvaluations"] == 0
    assert state["unavailableEvaluations"] == 0
    assert state["shadowPerformance"]["trades"] == 1


def test_blocked_paper_simulation_counts_counterfactual_without_shadow() -> None:
    store = prepared_store()
    market_id = 7002
    gate = observer_gate(
        market_id=market_id,
        state="UNCERTAIN",
        range_score=2,
        trend_score=3,
    )

    store.maybe_enter_m_series(
        {"market_id": market_id},
        200,
        realtime_context={"m01o_observer_gates": {"F1": gate}},
    )

    rows = store.db.execute(
        "SELECT strategy FROM trades ORDER BY id"
    ).fetchall()
    assert [row["strategy"] for row in rows] == [SOURCE_STRATEGY]

    state = _database_state(store)
    assert state["allowedEvaluations"] == 0
    assert state["blockedEvaluations"] == 1
    assert state["unavailableEvaluations"] == 0
    assert state["blockedSourceCounterfactual"]["trades"] == 1
    assert state["shadowPerformance"]["trades"] == 0
