from types import SimpleNamespace

from predict_bot.m_realtime import MSeriesRealtimeEngine
from predict_bot.microprice_feature_shadows import (
    DEADZONE_EXCLUDED_STRATEGY,
    DOWN_ONLY_STRATEGY,
    FEATURE_SHADOW_VERSION,
    LOW_TAIL_THICK_STRATEGY,
    LOW_TAIL_THIN_STRATEGY,
    UP_ONLY_STRATEGY,
)
from predict_bot.server import Store


def _market() -> dict[str, object]:
    return {
        "market_id": 7000001,
        "topic_id": 9001,
        "title": "BTC Up or Down",
        "start_price": 65_000.0,
        "start_ms": 1_000,
        "end_ms": 301_000,
        "fee_bps": 200,
        "server_clock_offset_ms": 0.0,
    }


def _collector() -> SimpleNamespace:
    return SimpleNamespace(
        status="LIVE",
        error=None,
        updated_at=None,
        interval=1.0,
        prediction=None,
    )


def _open_source(
    store: Store,
    *,
    market_id: int,
    side: str,
    entry: float,
    available_ask_size: float,
) -> None:
    store.open_trade(
        strategy="R_MICROPRICE",
        topic_id=9001,
        market_id=market_id,
        side=side,
        entry=entry,
        target=None,
        stake=5.0,
        fee_rate_bps=200,
        note="feature shadow source",
        strategy_version="R_MICROPRICE_FORWARD_TEST",
        diagnostics={
            "signal": 0.85 if side == "UP" else -0.85,
            "available_ask_size_after_reservations": available_ask_size,
            "visible_ask_size": available_ask_size + 5.0,
            "raw_top_ask": entry,
            "spread": 0.01,
            "book_age_ms": 10.0,
            "book_skew_ms": 2.0,
            "realtime_context": {
                "trigger_source": "prediction",
                "prediction_data_source": "dual_token_rest",
                "market_data_integrity_ok": True,
                "m01o_observer_gate": {
                    "currentRangeScore": 2,
                    "currentTrendVeto": False,
                },
            },
        },
    )


def _strategies_for_market(store: Store, market_id: int) -> set[str]:
    rows = store.db.execute(
        "SELECT strategy FROM trades WHERE market_id=?",
        (market_id,),
    ).fetchall()
    return {str(row["strategy"]) for row in rows}


def test_feature_shadow_boundaries_and_control_cohorts(tmp_path) -> None:
    store = Store(tmp_path / "simulation.db")
    MSeriesRealtimeEngine(store=store, current_market=_market)

    _open_source(
        store,
        market_id=7000001,
        side="UP",
        entry=0.1999,
        available_ask_size=60.0,
    )
    first = _strategies_for_market(store, 7000001)
    assert DEADZONE_EXCLUDED_STRATEGY in first
    assert UP_ONLY_STRATEGY in first
    assert DOWN_ONLY_STRATEGY not in first
    assert LOW_TAIL_THIN_STRATEGY in first
    assert LOW_TAIL_THICK_STRATEGY not in first

    _open_source(
        store,
        market_id=7000002,
        side="DOWN",
        entry=0.2000,
        available_ask_size=20.0,
    )
    deadzone = _strategies_for_market(store, 7000002)
    assert DEADZONE_EXCLUDED_STRATEGY not in deadzone
    assert DOWN_ONLY_STRATEGY in deadzone
    assert UP_ONLY_STRATEGY not in deadzone
    assert LOW_TAIL_THIN_STRATEGY not in deadzone
    assert LOW_TAIL_THICK_STRATEGY not in deadzone

    _open_source(
        store,
        market_id=7000003,
        side="DOWN",
        entry=0.1500,
        available_ask_size=60.0001,
    )
    thick = _strategies_for_market(store, 7000003)
    assert DEADZONE_EXCLUDED_STRATEGY in thick
    assert DOWN_ONLY_STRATEGY in thick
    assert LOW_TAIL_THICK_STRATEGY in thick
    assert LOW_TAIL_THIN_STRATEGY not in thick


def test_non_microprice_trade_never_creates_feature_shadows(tmp_path) -> None:
    store = Store(tmp_path / "simulation.db")
    MSeriesRealtimeEngine(store=store, current_market=_market)

    store.open_trade(
        strategy="R_OFI",
        topic_id=9001,
        market_id=7000100,
        side="UP",
        entry=0.15,
        target=None,
        stake=5.0,
        fee_rate_bps=200,
        note="not a microprice source",
        strategy_version="TEST",
        diagnostics={"available_ask_size_after_reservations": 30.0},
    )

    assert _strategies_for_market(store, 7000100) == {"R_OFI"}


def test_read_only_dashboard_store_exposes_feature_settlements(tmp_path) -> None:
    db_path = tmp_path / "simulation.db"
    execution_store = Store(db_path)
    dashboard_store = Store.open_read_only(db_path)
    MSeriesRealtimeEngine(store=execution_store, current_market=_market)

    _open_source(
        execution_store,
        market_id=7000200,
        side="DOWN",
        entry=0.15,
        available_ask_size=45.0,
    )
    execution_store.settle_market(
        7000200,
        "DOWN",
        True,
        topic_id=9001,
        start_price=65_000.0,
        end_price=64_999.0,
    )

    payload = dashboard_store.dashboard(
        _collector(),
        include_experiments=False,
    )
    experiment = payload["researchForward"]["micropriceFeatureShadows"]

    assert experiment["version"] == FEATURE_SHADOW_VERSION
    assert experiment["familyCount"] == 3
    assert experiment["cohortCount"] == 5

    for strategy in (
        DEADZONE_EXCLUDED_STRATEGY,
        DOWN_ONLY_STRATEGY,
        LOW_TAIL_THIN_STRATEGY,
    ):
        summary = payload["summaries"][strategy]
        assert summary["trades"] == 1
        assert summary["open"] == 0
        assert summary["wins"] == 1
        assert summary["losses"] == 0
        assert summary["realized_pnl"] > 0
        assert experiment["strategies"][strategy]["wins"] == 1

    assert payload["summaries"][UP_ONLY_STRATEGY]["trades"] == 0
    assert payload["summaries"][LOW_TAIL_THICK_STRATEGY]["trades"] == 0


def test_each_shadow_is_created_at_most_once_per_market(tmp_path) -> None:
    store = Store(tmp_path / "simulation.db")
    MSeriesRealtimeEngine(store=store, current_market=_market)

    _open_source(
        store,
        market_id=7000300,
        side="UP",
        entry=0.15,
        available_ask_size=30.0,
    )
    _open_source(
        store,
        market_id=7000300,
        side="UP",
        entry=0.15,
        available_ask_size=30.0,
    )

    rows = store.db.execute(
        """SELECT strategy, COUNT(*) AS count
             FROM trades
            WHERE market_id=?
            GROUP BY strategy""",
        (7000300,),
    ).fetchall()
    counts = {str(row["strategy"]): int(row["count"]) for row in rows}

    assert counts["R_MICROPRICE"] == 2
    assert counts[DEADZONE_EXCLUDED_STRATEGY] == 1
    assert counts[UP_ONLY_STRATEGY] == 1
    assert counts[LOW_TAIL_THIN_STRATEGY] == 1
