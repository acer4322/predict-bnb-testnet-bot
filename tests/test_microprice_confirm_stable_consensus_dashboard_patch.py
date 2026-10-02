from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

from predict_bot.microprice_confirm_stable_consensus_dashboard_patch import (
    _database_state,
    _inject_dashboard,
)
from predict_bot.microprice_confirm_stable_consensus_guard import (
    SOURCE_STRATEGY,
    STRATEGY,
)


def ready_gate(market_id: int) -> dict[str, object]:
    return {
        "profile": "F1",
        "dataQualityStatus": "READY",
        "historicalSampleCount": 20,
        "minSettledSamples": 6,
        "currentMarketId": market_id,
        "historicalState": "RANGE",
        "historicalRangeScore": 4,
        "historicalTrendScore": 0,
        "historicalProvisional": False,
    }


def make_store() -> SimpleNamespace:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        """CREATE TABLE trades (
               id INTEGER PRIMARY KEY,
               strategy TEXT NOT NULL,
               market_id INTEGER NOT NULL,
               side TEXT NOT NULL,
               status TEXT NOT NULL,
               entry_price REAL NOT NULL,
               stake REAL NOT NULL,
               pnl REAL,
               opened_at TEXT,
               closed_at TEXT,
               diagnostics_json TEXT
           )"""
    )
    return SimpleNamespace(db=db)


def insert_trade(
    store: SimpleNamespace,
    *,
    strategy: str,
    market_id: int,
    status: str,
    pnl: float | None,
    raw_top_ask: float,
) -> None:
    diagnostics = {
        "raw_top_ask": raw_top_ask,
        "realtime_context": {
            "m01o_observer_gates": {
                "F1": ready_gate(market_id),
            }
        },
    }
    store.db.execute(
        """INSERT INTO trades(
               strategy, market_id, side, status, entry_price, stake, pnl,
               opened_at, closed_at, diagnostics_json
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            strategy,
            market_id,
            "UP",
            status,
            raw_top_ask,
            1.0,
            pnl,
            "2026-08-06T00:00:00+00:00",
            "2026-08-06T00:05:00+00:00" if pnl is not None else None,
            json.dumps(diagnostics),
        ),
    )
    store.db.commit()


def test_dashboard_counts_allowed_and_price_blocked_source_rows() -> None:
    store = make_store()
    try:
        insert_trade(
            store,
            strategy=SOURCE_STRATEGY,
            market_id=1001,
            status="SETTLED_WIN",
            pnl=0.4,
            raw_top_ask=0.70,
        )
        insert_trade(
            store,
            strategy=SOURCE_STRATEGY,
            market_id=1002,
            status="SETTLED_LOSS",
            pnl=-1.0,
            raw_top_ask=0.27,
        )
        insert_trade(
            store,
            strategy=STRATEGY,
            market_id=1001,
            status="SETTLED_WIN",
            pnl=0.4,
            raw_top_ask=0.70,
        )

        state = _database_state(store)

        assert state["evaluations"] == 2
        assert state["allowedEvaluations"] == 1
        assert state["blockedEvaluations"] == 1
        assert state["priceBandBlockedEvaluations"] == 1
        assert state["transitionBlockedEvaluations"] == 0
        assert state["unavailableEvaluations"] == 0
        assert state["shadowPerformance"]["trades"] == 1
        assert state["shadowPerformance"]["wins"] == 1
        assert state["blockedSourceCounterfactual"]["losses"] == 1
        assert state["blockedSourceCounterfactual"]["realizedPnl"] == -1.0
    finally:
        store.db.close()


def test_dashboard_injects_observer_panel_and_summary_payloads() -> None:
    store = make_store()
    try:
        insert_trade(
            store,
            strategy=SOURCE_STRATEGY,
            market_id=2001,
            status="OPEN",
            pnl=None,
            raw_top_ask=0.72,
        )
        insert_trade(
            store,
            strategy=STRATEGY,
            market_id=2001,
            status="OPEN",
            pnl=None,
            raw_top_ask=0.72,
        )
        payload = {
            "researchForward": {"strategies": {}},
            "summaries": {},
        }

        result = _inject_dashboard(payload, store)

        experiment = result["researchForward"][
            "micropriceConfirmStableConsensusGuard"
        ]
        assert experiment["shadowPerformance"]["trades"] == 1
        assert STRATEGY in result["researchForward"]["strategies"]
        assert result["summaries"][STRATEGY]["trades"] == 1
        assert result["summaries"][STRATEGY]["open"] == 1
    finally:
        store.db.close()
