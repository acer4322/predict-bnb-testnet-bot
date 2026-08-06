from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from predict_bot import server
from predict_bot.decision_strategy_native_v2 import _wrap_store
from predict_bot.decision_strategy_rules import (
    EXCLUDED_FAMILIES,
    FAMILY_SOURCES,
    RANK1_MIN_HISTORY,
    RANK1_STRATEGY,
    RANK2_MIN_CONTEXT_HISTORY,
    RANK2_STRATEGY,
    SOURCE_STRATEGIES,
    STRATEGIES,
    VERSION,
    cap_family_weights,
    context_key,
    rank1_decision,
    rank2_decision,
    weighted_stats,
)
from predict_bot.decision_strategy_store import (
    DecisionStrategyTracker,
    experiment_state,
)
from predict_bot.research_forward import (
    GENERIC_SIGNAL_RESEARCH_STRATEGIES,
    RESEARCH_STRATEGIES,
)


def _stats(n: int, utility: float, probability: float) -> dict[str, float | int]:
    return {
        "n": n,
        "utility": utility,
        "p": probability,
        "weightSum": float(n),
    }


def _family(
    trade_id: int,
    strategy: str,
    side: str,
    entry: float,
    *,
    rank1: dict[str, float | int],
    rank2: dict[str, float | int],
) -> dict[str, object]:
    return {
        "source": {
            "id": trade_id,
            "strategy": strategy,
            "side": side,
            "entry_price": entry,
            "fee_rate_bps": 200,
            "opened_at": f"2026-08-07T00:00:0{trade_id}+00:00",
        },
        "sourceContext": ("MID", "ALIGNED", "P_030_055", "ER_MID"),
        "rank1": rank1,
        "rank2": rank2,
    }


def _snapshot(market_id: int = 9001) -> dict[str, object]:
    return {
        "timestamp": "2026-08-07T00:01:00+00:00",
        "topic_id": 9101,
        "market_id": market_id,
        "seconds_left": 240.0,
        "start_price": 100.0,
        "spot_price": 100.05,
        "spot_age_ms": 25.0,
        "up_ask": 0.40,
        "up_bid": 0.39,
        "down_ask": 0.60,
        "down_bid": 0.59,
        "up_ask_size": 100.0,
        "down_ask_size": 100.0,
        "book_age_ms": 100.0,
        "book_skew_ms": 20.0,
    }


def _context() -> dict[str, object]:
    return {
        "signal_event_type": "prediction",
        "execution_eligible": True,
        "prediction_data_source": "dual_token_rest",
        "market_data_integrity_ok": True,
    }


def _insert_observations(store, market_id: int) -> None:
    started = datetime(2026, 8, 7, tzinfo=timezone.utc)
    with store.lock:
        for index, price in enumerate((100.0, 100.02, 100.05)):
            store.db.execute(
                """INSERT INTO observations(
                       timestamp, topic_id, market_id, title, start_price,
                       spot_price, spot_age_ms, seconds_left, up_ask, up_bid,
                       down_ask, down_bid, up_ask_size, up_bid_size,
                       down_ask_size, down_bid_size, book_skew_ms, book_age_ms
                   ) VALUES (?, 9101, ?, 'test', 100.0, ?, 25.0, ?,
                             .40, .39, .60, .59, 100, 100, 100, 100, 20, 100)""",
                (
                    (started + timedelta(seconds=index * 20)).isoformat(),
                    market_id,
                    price,
                    300.0 - index * 20,
                ),
            )
        store.db.commit()


def test_strategies_are_derived_shadows_and_exclusions_are_exact() -> None:
    assert set(STRATEGIES) <= set(RESEARCH_STRATEGIES)
    assert not (set(STRATEGIES) & set(GENERIC_SIGNAL_RESEARCH_STRATEGIES))
    assert set(FAMILY_SOURCES.values()) == set(SOURCE_STRATEGIES)
    assert set(EXCLUDED_FAMILIES) == {"M01", "MICROPRICE", "OFI"}


def test_context_and_beta_weighting_match_frozen_contract() -> None:
    assert context_key(
        side="UP",
        entry_price=0.42,
        elapsed_seconds=120.0,
        start_move_bps=3.0,
        path_er=0.45,
    ) == ("MID", "ALIGNED", "P_030_055", "ER_MID")
    rows = [
        {"stake": 10.0, "pnl": 10.0},
        {"stake": 10.0, "pnl": -10.2},
    ]
    stats = weighted_stats(rows, limit=60, half_life=20.0)
    assert stats["n"] == 2
    assert -5.10 <= float(stats["utility"]) <= 10.0
    assert 0.0 < float(stats["p"]) < 1.0


def test_rank1_requires_two_positive_supporters_and_67_percent() -> None:
    ready = _stats(RANK1_MIN_HISTORY, 1.0, 0.70)
    empty = _stats(0, 0.0, 0.50)
    decision = rank1_decision(
        {
            "FUTURES_LEAD": _family(
                1,
                "R_FUTURES_LEAD",
                "UP",
                0.40,
                rank1=ready,
                rank2=empty,
            ),
            "CALIBRATED_VALUE": _family(
                2,
                "R_CALIBRATED_VALUE",
                "UP",
                0.42,
                rank1=_stats(RANK1_MIN_HISTORY, 0.8, 0.68),
                rank2=empty,
            ),
            "CONSENSUS": _family(
                3,
                "R_CONSENSUS",
                "DOWN",
                0.58,
                rank1=_stats(RANK1_MIN_HISTORY, 0.1, 0.52),
                rank2=empty,
            ),
        }
    )
    assert decision["status"] == "CANDIDATE"
    assert decision["side"] == "UP"
    assert set(decision["supporters"]) == {
        "FUTURES_LEAD",
        "CALIBRATED_VALUE",
    }
    assert float(decision["agreementWeight"]) >= 0.67


def test_rank2_chooses_the_side_with_two_supporters_not_one_large_vote() -> None:
    ready = _stats(RANK2_MIN_CONTEXT_HISTORY, 0.5, 0.78)
    families = {
        "FUTURES_LEAD": _family(
            1,
            "R_FUTURES_LEAD",
            "DOWN",
            0.40,
            rank1=ready,
            rank2=ready,
        ),
        "CALIBRATED_VALUE": _family(
            2,
            "R_CALIBRATED_VALUE",
            "UP",
            0.40,
            rank1=ready,
            rank2=_stats(RANK2_MIN_CONTEXT_HISTORY, 0.4, 0.70),
        ),
        "CONSENSUS": _family(
            3,
            "R_CONSENSUS",
            "UP",
            0.41,
            rank1=ready,
            rank2=_stats(RANK2_MIN_CONTEXT_HISTORY, 0.4, 0.69),
        ),
    }
    decision = rank2_decision(families)
    assert decision["status"] == "CANDIDATE"
    assert decision["side"] == "UP"
    assert set(decision["supporters"]) == {
        "CALIBRATED_VALUE",
        "CONSENSUS",
    }
    capped = cap_family_weights({"A": 0.30, "B": 0.05, "C": 0.05})
    assert capped["A"] == pytest.approx(0.10)
    assert capped["A"] / sum(capped.values()) == pytest.approx(0.50)


def test_source_event_creates_durable_context_and_abstention_only(tmp_path) -> None:
    store = server.Store(tmp_path / "simulation.db")
    tracker = DecisionStrategyTracker(SimpleNamespace(), store)
    market_id = 9001
    _insert_observations(store, market_id)
    store.open_trade(
        strategy="R_FUTURES_LEAD",
        topic_id=9101,
        market_id=market_id,
        side="UP",
        entry=0.40,
        target=None,
        stake=5.0,
        fee_rate_bps=200,
        note="source",
    )
    opened = tracker.process(
        [
            {
                "strategy": "R_FUTURES_LEAD",
                "market_id": market_id,
                "topic_id": 9101,
                "side": "UP",
            }
        ],
        _snapshot(market_id),
        200,
        _context(),
    )
    assert opened == []
    context_count = store.db.execute(
        "SELECT COUNT(*) FROM decision_strategy_source_contexts"
    ).fetchone()[0]
    assert context_count == 1
    evaluations = store.db.execute(
        "SELECT controller, status FROM decision_strategy_evaluations ORDER BY controller"
    ).fetchall()
    assert {str(row["controller"]) for row in evaluations} == set(STRATEGIES)
    assert all(str(row["status"]).startswith("WAITING") for row in evaluations)
    assert store.db.execute(
        "SELECT COUNT(*) FROM trades WHERE strategy IN (?, ?)",
        STRATEGIES,
    ).fetchone()[0] == 0


def test_wrapper_preserves_base_candidates_when_controller_raises(
    tmp_path, monkeypatch
) -> None:
    store = server.Store(tmp_path / "simulation.db")
    base = {
        "strategy": "R_FUTURES_LEAD",
        "topic_id": 9101,
        "market_id": 9002,
        "side": "UP",
        "entry_price": 0.40,
    }
    store.maybe_enter_m_series = lambda *args, **kwargs: [dict(base)]
    engine = SimpleNamespace()
    _wrap_store(engine, store)
    tracker = store._decision_strategy_native_v2_tracker
    monkeypatch.setattr(
        tracker,
        "process",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert store.maybe_enter_m_series(
        _snapshot(9002),
        200,
        realtime_context=_context(),
    ) == [base]


def test_read_only_dashboard_keeps_base_payload_and_adds_small_experiment(
    tmp_path,
) -> None:
    path = tmp_path / "simulation.db"
    execution_store = server.Store(path)
    DecisionStrategyTracker(SimpleNamespace(), execution_store)
    dashboard_store = server.Store.open_read_only(path)
    collector = SimpleNamespace(
        status="LIVE",
        error=None,
        updated_at=None,
        interval=1.0,
        prediction=None,
    )
    payload = dashboard_store.dashboard(collector, include_experiments=False)
    assert payload["connection"]["status"] == "LIVE"
    assert isinstance(payload["summaries"], dict)
    assert "researchForward" in payload
    experiment = payload["researchForward"]["decisionStrategyExperiment"]
    assert experiment["version"] == VERSION
    assert experiment["status"] == "COLLECTING"
    assert experiment["rank2Warmup"].startswith("same-context history")
    assert experiment_state(dashboard_store)["version"] == VERSION
