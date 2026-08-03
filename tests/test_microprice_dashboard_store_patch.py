from types import SimpleNamespace

from predict_bot.m_realtime import MSeriesRealtimeEngine
from predict_bot.microprice_variants import (
    MICROPRICE_CONFIRM_STRATEGY,
    MICROPRICE_REVERSION_STRATEGY,
)
from predict_bot.server import Store


def _market() -> dict[str, object]:
    return {
        "market_id": 6817001,
        "topic_id": 202,
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


def test_separate_dashboard_store_reports_variant_settlements(tmp_path) -> None:
    db_path = tmp_path / "simulation.db"
    execution_store = Store(db_path)
    # Production creates a second Store connection for /api/state statistics.
    dashboard_store = Store(db_path)

    MSeriesRealtimeEngine(
        store=execution_store,
        current_market=_market,
    )

    execution_store.open_trade(
        strategy=MICROPRICE_CONFIRM_STRATEGY,
        topic_id=202,
        market_id=6817001,
        side="DOWN",
        entry=0.915,
        target=None,
        stake=5.0,
        fee_rate_bps=200,
        note="paired dashboard integration test",
        strategy_version="MICROPRICE_VARIANTS_TEST",
        diagnostics={"paper_only": True},
    )
    execution_store.open_trade(
        strategy=MICROPRICE_REVERSION_STRATEGY,
        topic_id=202,
        market_id=6817001,
        side="UP",
        entry=0.100,
        target=None,
        stake=5.0,
        fee_rate_bps=200,
        note="paired dashboard integration test",
        strategy_version="MICROPRICE_VARIANTS_TEST",
        diagnostics={"paper_only": True},
    )
    execution_store.settle_market(
        6817001,
        "DOWN",
        True,
        topic_id=202,
        start_price=65_000.0,
        end_price=64_999.0,
    )

    payload = dashboard_store.dashboard(
        _collector(),
        include_experiments=False,
    )

    confirm = payload["summaries"][MICROPRICE_CONFIRM_STRATEGY]
    reversion = payload["summaries"][MICROPRICE_REVERSION_STRATEGY]
    experiment = payload["researchForward"]["micropricePairedExperiment"]

    assert confirm["trades"] == 1
    assert confirm["open"] == 0
    assert confirm["wins"] == 1
    assert confirm["losses"] == 0
    assert confirm["realized_pnl"] > 0

    assert reversion["trades"] == 1
    assert reversion["open"] == 0
    assert reversion["wins"] == 0
    assert reversion["losses"] == 1
    assert reversion["realized_pnl"] < 0

    assert experiment["pairedMarkets"] == 1
    assert experiment["strategies"][MICROPRICE_CONFIRM_STRATEGY]["wins"] == 1
    assert experiment["strategies"][MICROPRICE_REVERSION_STRATEGY]["losses"] == 1
