import time
from types import SimpleNamespace

from predict_bot import live_trading, m_realtime, research_forward
from predict_bot.m_realtime import MSeriesRealtimeEngine
from predict_bot.microprice_confirm_optimization_shadows import (
    EXIT_098_STRATEGY,
    EXIT_TARGET_PRICE,
    OPTIMIZATION_VERSION,
    PRICE_SIDE_GUARD_STRATEGY,
    SOURCE_STRATEGY,
)
from predict_bot.microprice_variants import (
    MICROPRICE_REVERSION_STRATEGY,
    MicropriceVariantTracker,
)
from predict_bot.server import DEFAULT_CONFIG, Store


def _market(market_id=101):
    now_ms = time.time_ns() / 1_000_000
    return {
        "market_id": market_id,
        "topic_id": 202,
        "title": "BTC Up or Down",
        "start_price": 65_000.0,
        "start_ms": now_ms - 120_000,
        "end_ms": now_ms + 180_000,
        "fee_bps": 200,
        "server_clock_offset_ms": 0.0,
    }


def _direct_event(market_id=101):
    return {
        "source": "prediction",
        "stream": "orderbook",
        "market_id": market_id,
        "prediction_data_source": "dual_token_rest",
        "direct_outcome_books": True,
        "feature_eligible": True,
    }


def _snapshot(
    sequence: int,
    received_ns: int,
    shift: float,
    *,
    market_id=101,
    down_bid=None,
    down_ask=None,
    down_bid_size=200.0,
):
    return {
        "timestamp": f"2026-08-05T00:00:0{sequence}+00:00",
        "timestamp_ns": 1_000_000_000 + sequence,
        "topic_id": 202,
        "market_id": market_id,
        "seconds_left": 180.0 - sequence * 0.2,
        "start_price": 65_000.0,
        "spot_price": 65_001.0,
        "spot_age_ms": 100.0,
        "futures_price": 65_002.0,
        "futures_age_ms": 100.0,
        "up_bid": 0.68 - shift,
        "up_ask": 0.70 - shift,
        "up_bid_size": 30.0,
        "up_ask_size": 200.0,
        "down_bid": 0.29 + shift if down_bid is None else down_bid,
        "down_ask": 0.31 + shift if down_ask is None else down_ask,
        "down_bid_size": down_bid_size,
        "down_ask_size": 30.0,
        "book_age_ms": 100.0,
        "book_skew_ms": 20.0,
        "received_monotonic_ns": received_ns,
        "signal_event_sequence": f"prediction:{market_id}:{sequence}",
    }


def _context(sequence: int, received_ns: int, market_id=101):
    return {
        "trigger_source": "prediction",
        "signal_event_type": "prediction",
        "execution_eligible": True,
        "prediction_data_source": "dual_token_rest",
        "signal_event_sequence": f"prediction:{market_id}:{sequence}",
        "received_monotonic_ns": received_ns,
        "signal_received_monotonic_ns": received_ns,
        "market_data_integrity_ok": True,
    }


def _engine(tmp_path, market_id=101):
    store = Store(tmp_path / "simulation.db")
    disabled = {
        key: False
        for key in DEFAULT_CONFIG
        if key.endswith("_enabled")
    }
    disabled["strategy_r_microprice_enabled"] = True
    store.update_config(disabled)
    engine = MSeriesRealtimeEngine(
        store=store,
        current_market=lambda: _market(market_id),
    )
    engine.prediction_event = _direct_event(market_id)
    tracker = engine.microprice_variant_tracker
    assert isinstance(tracker, MicropriceVariantTracker)
    return store, engine, tracker


def _open_confirm_pair(tracker, market_id=101):
    base_ns = 50_000_000_000
    first = tracker.process(
        _snapshot(1, base_ns, 0.0, market_id=market_id),
        200,
        _context(1, base_ns, market_id),
    )
    assert first == []
    return tracker.process(
        _snapshot(
            2,
            base_ns + 200_000_000,
            0.001,
            market_id=market_id,
        ),
        200,
        _context(2, base_ns + 200_000_000, market_id),
    )


def _open_synthetic_source(store, *, market_id, side, entry):
    store.open_trade(
        strategy=SOURCE_STRATEGY,
        topic_id=202,
        market_id=market_id,
        side=side,
        entry=entry,
        target=None,
        stake=5.0,
        fee_rate_bps=200,
        note="synthetic confirmed V2 source",
        strategy_version="MICROPRICE_VARIANTS_V2_RELAXED",
        diagnostics={
            "variant_mode": "FOLLOW_CONFIRMED_IMBALANCE",
            "source_side": side,
            "selected_side": side,
            "source_signal": 1.2 if side == "UP" else -1.2,
            "confirmation_count": 2,
            "confirmation_duration_ms": 200.0,
        },
    )


def test_v2_source_opens_guard_and_exit_098_shadow_candidates(tmp_path):
    store, engine, tracker = _engine(tmp_path)
    opened = _open_confirm_pair(tracker)
    strategies = {str(item["strategy"]) for item in opened}
    assert SOURCE_STRATEGY in strategies
    assert MICROPRICE_REVERSION_STRATEGY in strategies
    assert EXIT_098_STRATEGY in strategies

    exit_candidate = next(
        item for item in opened
        if item["strategy"] == EXIT_098_STRATEGY
    )
    assert exit_candidate["paper_only"] is True
    assert exit_candidate["live_forwardable_when_selected"] is True
    assert exit_candidate["live_exit_target_price"] == EXIT_TARGET_PRICE

    rows = store.db.execute(
        """SELECT strategy, side, target_price, strategy_version
             FROM trades
            WHERE strategy IN (?, ?)
            ORDER BY strategy""",
        (PRICE_SIDE_GUARD_STRATEGY, EXIT_098_STRATEGY),
    ).fetchall()
    assert len(rows) == 2
    by_strategy = {str(row["strategy"]): row for row in rows}
    assert by_strategy[PRICE_SIDE_GUARD_STRATEGY]["side"] == "DOWN"
    assert by_strategy[PRICE_SIDE_GUARD_STRATEGY]["target_price"] is None
    assert float(by_strategy[EXIT_098_STRATEGY]["target_price"]) == 0.98
    assert by_strategy[EXIT_098_STRATEGY]["strategy_version"] == OPTIMIZATION_VERSION

    engine.market_id = 101
    engine.evaluation_horizon_seconds = 1.0
    engine._refresh_evaluation_horizon(force=True)
    assert engine.evaluation_horizon_seconds == 300.0


def test_price_side_guard_blocks_both_discovered_dead_zones(tmp_path):
    store, _, _ = _engine(tmp_path, market_id=303)
    _open_synthetic_source(
        store,
        market_id=303,
        side="UP",
        entry=0.35,
    )
    _open_synthetic_source(
        store,
        market_id=304,
        side="DOWN",
        entry=0.55,
    )

    for market_id, reason in (
        (303, "UP_ENTRY_BELOW_040"),
        (304, "DOWN_ENTRY_050_060"),
    ):
        assert store.db.execute(
            """SELECT 1 FROM trades
               WHERE strategy=? AND market_id=?""",
            (PRICE_SIDE_GUARD_STRATEGY, market_id),
        ).fetchone() is None
        assert store.db.execute(
            """SELECT 1 FROM trades
               WHERE strategy=? AND market_id=?""",
            (EXIT_098_STRATEGY, market_id),
        ).fetchone() is not None
        decision = store.db.execute(
            """SELECT price_side_guard_eligible,
                      price_side_guard_block_reason
                 FROM microprice_confirm_optimization_decisions
                WHERE market_id=?""",
            (market_id,),
        ).fetchone()
        assert decision is not None
        assert int(decision["price_side_guard_eligible"]) == 0
        assert decision["price_side_guard_block_reason"] == reason


def test_paper_exit_098_uses_bid_and_survives_official_settlement(tmp_path):
    store, _, tracker = _engine(tmp_path)
    _open_confirm_pair(tracker)
    base_ns = 51_000_000_000
    tracker.process(
        _snapshot(
            3,
            base_ns,
            0.0,
            down_bid=0.98,
            down_ask=0.99,
            down_bid_size=500.0,
        ),
        200,
        _context(3, base_ns),
    )

    target = store.db.execute(
        """SELECT status, exit_price, pnl, diagnostics_json
             FROM trades WHERE strategy=? AND market_id=101""",
        (EXIT_098_STRATEGY,),
    ).fetchone()
    assert target is not None
    assert target["status"] == "TARGET_FILLED"
    assert float(target["exit_price"]) == 0.98
    assert float(target["pnl"]) > 0
    assert '"observedBid": 0.98' in str(target["diagnostics_json"])

    store.settle_market(
        101,
        "UP",
        True,
        topic_id=202,
        start_price=65_000.0,
        end_price=65_001.0,
    )
    after = store.db.execute(
        """SELECT status, exit_price, pnl
             FROM trades WHERE strategy=? AND market_id=101""",
        (EXIT_098_STRATEGY,),
    ).fetchone()
    assert after is not None
    assert after["status"] == "TARGET_FILLED"
    assert float(after["exit_price"]) == 0.98
    assert float(after["pnl"]) > 0


def test_exit_098_is_live_selectable_and_dashboard_visible(tmp_path):
    assert EXIT_098_STRATEGY in live_trading.LIVE_RESEARCH_STRATEGIES
    assert EXIT_098_STRATEGY in live_trading.LIVE_SUPPORTED_STRATEGIES
    assert EXIT_098_STRATEGY in m_realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES
    assert EXIT_098_STRATEGY in research_forward.CONFIRMATION_ADD_SOURCE_STRATEGIES

    db_path = tmp_path / "simulation.db"
    execution_store = Store(db_path)
    disabled = {
        key: False
        for key in DEFAULT_CONFIG
        if key.endswith("_enabled")
    }
    disabled["strategy_r_microprice_enabled"] = True
    execution_store.update_config(disabled)
    engine = MSeriesRealtimeEngine(
        store=execution_store,
        current_market=_market,
    )
    engine.prediction_event = _direct_event()
    _open_confirm_pair(engine.microprice_variant_tracker)

    dashboard_store = Store.open_read_only(db_path)
    collector = SimpleNamespace(
        status="LIVE",
        error=None,
        updated_at=None,
        interval=1.0,
        prediction=None,
    )
    dashboard = dashboard_store.dashboard(
        collector,
        include_experiments=False,
    )
    experiment = dashboard["researchForward"][
        "micropriceConfirmOptimizationShadows"
    ]
    assert experiment["version"] == OPTIMIZATION_VERSION
    assert experiment["sourceMarkets"] == 1
    assert EXIT_098_STRATEGY in experiment["strategies"]
    assert PRICE_SIDE_GUARD_STRATEGY in experiment["strategies"]
    assert dashboard["summaries"][EXIT_098_STRATEGY]["trades"] == 1
