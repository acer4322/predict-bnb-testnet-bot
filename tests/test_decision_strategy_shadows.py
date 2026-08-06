from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from predict_bot import live_trading, m_realtime
from predict_bot.decision_strategy_frozen_rules_patch import (
    PATCH_VERSION,
    RANK1_MIN_HISTORY,
    RANK2_MIN_CONTEXT_HISTORY,
    _context_key,
    _exp_stats,
)
from predict_bot.decision_strategy_shadows import (
    DECISION_STRATEGIES,
    EXCLUDED_FAMILIES,
    FAMILY_SOURCES,
    RANK1_STRATEGY,
    RANK2_STRATEGY,
    DecisionStrategyTracker,
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
            fee_rate_bps INTEGER,
            opened_at TEXT,
            closed_at TEXT,
            model_probability REAL,
            model_edge REAL,
            diagnostics_json TEXT
        );
        CREATE TABLE observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            market_id INTEGER NOT NULL,
            start_price REAL,
            spot_price REAL,
            seconds_left REAL
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


def _history(*, n: int, utility: float, probability: float) -> dict[str, float | int]:
    return {
        "n": n,
        "utility": utility,
        "p": probability,
        "averageWin": 0.0,
        "averageLoss": -5.0,
        "weightSum": float(n),
    }


def _family(
    *,
    strategy: str,
    side: str,
    entry: float,
    rank1: dict[str, float | int],
    rank2: dict[str, float | int],
) -> dict[str, object]:
    return {
        "source": {
            "id": hash(strategy) & 0x7FFFFFFF,
            "strategy": strategy,
            "side": side,
            "entry_price": entry,
            "fee_rate_bps": 200,
            "opened_at": "2026-08-01T00:00:00+00:00",
        },
        "history": {
            "rank1": rank1,
            "rank2": rank2,
            "sourceContext": {
                "available": True,
                "context": ["MID", "ALIGNED", "P_030_055", "ER_MID"],
            },
        },
    }


def test_decision_strategies_are_live_selectable_and_forwardable() -> None:
    assert set(DECISION_STRATEGIES).issubset(live_trading.LIVE_RESEARCH_STRATEGIES)
    assert set(DECISION_STRATEGIES).issubset(live_trading.LIVE_SUPPORTED_STRATEGIES)
    assert set(DECISION_STRATEGIES).issubset(
        m_realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES
    )


def test_frozen_rule_patch_is_active() -> None:
    from predict_bot import decision_strategy_shadows

    assert decision_strategy_shadows.DECISION_VERSION == PATCH_VERSION
    assert decision_strategy_shadows.MIN_HISTORY == RANK1_MIN_HISTORY


def test_m01_microprice_and_ofi_are_not_decision_families() -> None:
    assert set(EXCLUDED_FAMILIES) == {"M01", "MICROPRICE", "OFI"}
    assert set(FAMILY_SOURCES) == {
        "FUTURES_LEAD",
        "CALIBRATED_VALUE",
        "CONSENSUS",
    }
    assert RANK1_STRATEGY not in FAMILY_SOURCES.values()
    assert RANK2_STRATEGY not in FAMILY_SOURCES.values()


def test_exponential_stats_match_frozen_normalization_and_beta_prior() -> None:
    rows = [
        {"stake": 10.0, "pnl": 10.0},
        {"stake": 10.0, "pnl": -10.2},
    ]
    stats = _exp_stats(rows, limit=60, half_life=20.0)
    assert stats["n"] == 2
    assert -5.10 <= float(stats["utility"]) <= 10.0
    assert 0.0 < float(stats["p"]) < 1.0


def test_context_key_uses_phase_alignment_price_and_path_er() -> None:
    context = _context_key(
        {
            "elapsed": 120.0,
            "startMoveBps": 3.0,
            "entryPrice": 0.42,
            "pathEr": 0.45,
            "side": "UP",
        }
    )
    assert context == ("MID", "ALIGNED", "P_030_055", "ER_MID")


def test_rank1_requires_two_positive_weight_families_and_67pct_share() -> None:
    tracker = DecisionStrategyTracker(SimpleNamespace(), _store())
    ready = _history(n=RANK1_MIN_HISTORY, utility=1.0, probability=0.70)
    empty = _history(n=0, utility=0.0, probability=0.50)

    waiting = tracker._rank1(
        {
            "FUTURES_LEAD": _family(
                strategy="R_FUTURES_LEAD",
                side="UP",
                entry=0.40,
                rank1=ready,
                rank2=empty,
            ),
            "CALIBRATED_VALUE": {
                "source": None,
                "history": {"rank1": empty, "rank2": empty},
            },
            "CONSENSUS": {
                "source": None,
                "history": {"rank1": empty, "rank2": empty},
            },
        }
    )
    assert waiting["status"] == "WAITING"

    candidate = tracker._rank1(
        {
            "FUTURES_LEAD": _family(
                strategy="R_FUTURES_LEAD",
                side="UP",
                entry=0.40,
                rank1=ready,
                rank2=empty,
            ),
            "CALIBRATED_VALUE": _family(
                strategy="R_CALIBRATED_VALUE",
                side="UP",
                entry=0.43,
                rank1=_history(
                    n=RANK1_MIN_HISTORY,
                    utility=0.8,
                    probability=0.68,
                ),
                rank2=empty,
            ),
            "CONSENSUS": {
                "source": None,
                "history": {"rank1": empty, "rank2": empty},
            },
        }
    )
    assert candidate["status"] == "CANDIDATE"
    assert candidate["side"] == "UP"
    assert candidate["agreementWeight"] == 1.0
    assert set(candidate["supporters"]) == {
        "FUTURES_LEAD",
        "CALIBRATED_VALUE",
    }


def test_rank2_uses_same_context_edge_and_50pct_family_cap() -> None:
    tracker = DecisionStrategyTracker(SimpleNamespace(), _store())
    empty = _history(n=0, utility=0.0, probability=0.50)
    context_ready = _history(
        n=RANK2_MIN_CONTEXT_HISTORY,
        utility=0.5,
        probability=0.70,
    )
    candidate = tracker._rank2(
        {
            "FUTURES_LEAD": _family(
                strategy="R_FUTURES_LEAD",
                side="UP",
                entry=0.40,
                rank1=empty,
                rank2=context_ready,
            ),
            "CALIBRATED_VALUE": _family(
                strategy="R_CALIBRATED_VALUE",
                side="UP",
                entry=0.42,
                rank1=empty,
                rank2=_history(
                    n=RANK2_MIN_CONTEXT_HISTORY,
                    utility=0.4,
                    probability=0.69,
                ),
            ),
            "CONSENSUS": {
                "source": None,
                "history": {"rank1": empty, "rank2": empty},
            },
        }
    )
    assert candidate["status"] == "CANDIDATE"
    assert candidate["side"] == "UP"
    capped = candidate["cappedWeights"]
    assert abs(sum(capped.values()) - 2 * min(capped.values())) < 1e-12
    assert candidate["agreementWeight"] == 1.0


def test_strong_opposing_trend_blocks_and_missing_spot_fails_closed() -> None:
    store = _store()
    store.db.executemany(
        """INSERT INTO observations(
               timestamp, market_id, start_price, spot_price, seconds_left
           ) VALUES (?, ?, ?, ?, ?)""",
        [
            ("2026-08-01T00:00:00+00:00", 7, 100.0, 100.0, 299.0),
            ("2026-08-01T00:00:10+00:00", 7, 100.0, 100.1, 289.0),
            ("2026-08-01T00:00:20+00:00", 7, 100.0, 100.2, 279.0),
            ("2026-08-01T00:00:30+00:00", 7, 100.0, 100.3, 269.0),
        ],
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
