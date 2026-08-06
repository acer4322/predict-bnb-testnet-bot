from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from predict_bot import live_trading, m_realtime
from predict_bot.decision_strategy_shadows import (
    DECISION_STRATEGIES,
    EXCLUDED_FAMILIES,
    FAMILY_SOURCES,
    RANK1_STRATEGY,
    RANK2_STRATEGY,
    DecisionStrategyTracker,
    _cap_weights,
)


def _store() -> SimpleNamespace:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL,
            topic_id INTEGER,
            market_id INTEGER NOT NULL,
            side TEXT,
            status TEXT,
            entry_price REAL,
            stake REAL,
            pnl REAL,
            opened_at TEXT,
            closed_at TEXT,
            model_probability REAL,
            model_edge REAL,
            diagnostics_json TEXT
        );
        CREATE TABLE observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id INTEGER NOT NULL,
            spot_price REAL
        );
        CREATE TABLE strategy_measurement_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT,
            cutoff_trade_id INTEGER,
            reset_at TEXT
        );
        """
    )
    return SimpleNamespace(db=db)


def test_decision_strategies_are_live_selectable_and_forwardable() -> None:
    assert set(DECISION_STRATEGIES).issubset(live_trading.LIVE_RESEARCH_STRATEGIES)
    assert set(DECISION_STRATEGIES).issubset(live_trading.LIVE_SUPPORTED_STRATEGIES)
    assert set(DECISION_STRATEGIES).issubset(m_realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES)


def test_m01_microprice_and_ofi_are_not_decision_families() -> None:
    assert set(EXCLUDED_FAMILIES) == {"M01", "MICROPRICE", "OFI"}
    assert set(FAMILY_SOURCES) == {"FUTURES_LEAD", "CALIBRATED_VALUE", "CONSENSUS"}
    assert RANK1_STRATEGY not in FAMILY_SOURCES.values()
    assert RANK2_STRATEGY not in FAMILY_SOURCES.values()


def test_weight_cap_is_normalized_and_respects_feasible_cap() -> None:
    weights = _cap_weights({"A": 9.0, "B": 1.0, "C": 1.0}, 0.45)
    assert abs(sum(weights.values()) - 1.0) < 1e-12
    assert max(weights.values()) <= 0.45 + 1e-12

    two_family_weights = _cap_weights({"A": 9.0, "B": 1.0}, 0.45)
    assert abs(sum(two_family_weights.values()) - 1.0) < 1e-12
    assert max(two_family_weights.values()) <= 0.50 + 1e-12


def test_rank1_requires_two_positive_history_families() -> None:
    store = _store()
    tracker = DecisionStrategyTracker(SimpleNamespace(), store)
    waiting = tracker._rank1(
        {
            "FUTURES_LEAD": {
                "source": {"side": "UP"},
                "history": {"ready": True, "utility": 0.2, "posteriorProbability": 0.7},
            },
            "CALIBRATED_VALUE": {
                "source": None,
                "history": {"ready": True, "utility": 0.2, "posteriorProbability": 0.7},
            },
            "CONSENSUS": {
                "source": None,
                "history": {"ready": True, "utility": 0.2, "posteriorProbability": 0.7},
            },
        }
    )
    assert waiting["status"] == "WAITING"

    candidate = tracker._rank1(
        {
            "FUTURES_LEAD": {
                "source": {"id": 1, "strategy": "R_FUTURES_LEAD", "side": "UP"},
                "history": {"ready": True, "utility": 0.2, "posteriorProbability": 0.72},
            },
            "CALIBRATED_VALUE": {
                "source": {"id": 2, "strategy": "R_CALIBRATED_VALUE", "side": "UP"},
                "history": {"ready": True, "utility": 0.1, "posteriorProbability": 0.68},
            },
            "CONSENSUS": {
                "source": None,
                "history": {"ready": True, "utility": 0.2, "posteriorProbability": 0.7},
            },
        }
    )
    assert candidate["status"] == "CANDIDATE"
    assert candidate["side"] == "UP"
    assert candidate["agreementWeight"] == 1.0


def test_strong_opposing_trend_blocks_and_missing_spot_fails_closed() -> None:
    store = _store()
    store.db.executemany(
        "INSERT INTO observations(market_id, spot_price) VALUES (?, ?)",
        [(7, 100.0), (7, 100.1), (7, 100.2), (7, 100.3)],
    )
    tracker = DecisionStrategyTracker(SimpleNamespace(), store)
    snapshot = {
        "market_id": 7,
        "seconds_left": 250.0,
        "start_price": 100.0,
        "spot_price": 100.3,
        "spot_age_ms": 100.0,
    }
    blocked = tracker._trend(snapshot, "DOWN")
    assert blocked["status"] == "BLOCK_OPPOSES_STRONG_TREND"
    assert blocked["passed"] is False

    missing = tracker._trend({**snapshot, "spot_age_ms": None}, "UP")
    assert missing["status"] == "NOT_EVALUABLE"
    assert missing["passed"] is False
