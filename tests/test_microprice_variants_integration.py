import time
from types import SimpleNamespace

from predict_bot.m_realtime import MSeriesRealtimeEngine
from predict_bot.microprice_variants import (
    MICROPRICE_CONFIRM_STRATEGY,
    MICROPRICE_REVERSION_STRATEGY,
    MicropriceVariantTracker,
)
from predict_bot.server import DEFAULT_CONFIG, Store


def _market():
    now_ms = time.time_ns() / 1_000_000
    return {
        "market_id": 101,
        "topic_id": 202,
        "title": "BTC Up or Down",
        "start_price": 65_000.0,
        "start_ms": now_ms - 120_000,
        "end_ms": now_ms + 180_000,
        "fee_bps": 200,
        "server_clock_offset_ms": 0.0,
    }


def _direct_event():
    return {
        "source": "prediction",
        "stream": "orderbook",
        "market_id": 101,
        "prediction_data_source": "dual_token_rest",
        "direct_outcome_books": True,
        "feature_eligible": True,
    }


def _snapshot(sequence: int, received_ns: int, shift: float):
    return {
        "timestamp": f"2026-08-04T00:00:0{sequence}+00:00",
        "timestamp_ns": 1_000_000_000 + sequence,
        "topic_id": 202,
        "market_id": 101,
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
        "down_bid": 0.29 + shift,
        "down_ask": 0.31 + shift,
        "down_bid_size": 200.0,
        "down_ask_size": 30.0,
        "book_age_ms": 100.0,
        "book_skew_ms": 20.0,
        "received_monotonic_ns": received_ns,
        "signal_event_sequence": f"prediction:{sequence}",
    }


def _context(sequence: int, received_ns: int):
    return {
        "trigger_source": "prediction",
        "signal_event_type": "prediction",
        "execution_eligible": True,
        "prediction_data_source": "dual_token_rest",
        "signal_event_sequence": f"prediction:{sequence}",
        "received_monotonic_ns": received_ns,
        "signal_received_monotonic_ns": received_ns,
        "market_data_integrity_ok": True,
    }


def test_real_store_opens_settles_and_exposes_paired_experiment(tmp_path):
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
        current_market=_market,
    )
    engine.prediction_event = _direct_event()
    tracker = engine.microprice_variant_tracker
    assert isinstance(tracker, MicropriceVariantTracker)

    base_ns = 40_000_000_000
    for sequence, shift in ((1, 0.0), (2, 0.001), (3, 0.003)):
        tracker.process(
            _snapshot(
                sequence,
                base_ns + sequence * 200_000_000,
                shift,
            ),
            200,
            _context(
                sequence,
                base_ns + sequence * 200_000_000,
            ),
        )

    rows = store.db.execute(
        """SELECT strategy, side, status
             FROM trades
            WHERE strategy IN (?, ?)
            ORDER BY strategy""",
        (
            MICROPRICE_CONFIRM_STRATEGY,
            MICROPRICE_REVERSION_STRATEGY,
        ),
    ).fetchall()
    assert len(rows) == 2
    by_strategy = {str(row["strategy"]): row for row in rows}
    assert by_strategy[MICROPRICE_CONFIRM_STRATEGY]["side"] == "DOWN"
    assert by_strategy[MICROPRICE_REVERSION_STRATEGY]["side"] == "UP"
    assert all(str(row["status"]) == "OPEN" for row in rows)

    store.settle_market(
        101,
        "UP",
        True,
        topic_id=202,
        start_price=65_000.0,
        end_price=65_001.0,
    )

    settled = store.db.execute(
        """SELECT strategy, status, pnl
             FROM trades
            WHERE strategy IN (?, ?)
            ORDER BY strategy""",
        (
            MICROPRICE_CONFIRM_STRATEGY,
            MICROPRICE_REVERSION_STRATEGY,
        ),
    ).fetchall()
    settled_by_strategy = {
        str(row["strategy"]): row
        for row in settled
    }
    assert (
        settled_by_strategy[MICROPRICE_CONFIRM_STRATEGY]["status"]
        == "SETTLED_LOSS"
    )
    assert (
        settled_by_strategy[MICROPRICE_REVERSION_STRATEGY]["status"]
        == "SETTLED_WIN"
    )
    assert float(
        settled_by_strategy[MICROPRICE_CONFIRM_STRATEGY]["pnl"]
    ) < 0
    assert float(
        settled_by_strategy[MICROPRICE_REVERSION_STRATEGY]["pnl"]
    ) > 0

    experiment = tracker.database_state()
    assert experiment["pairedMarkets"] == 1
    assert experiment["strategies"][MICROPRICE_CONFIRM_STRATEGY][
        "losses"
    ] == 1
    assert experiment["strategies"][MICROPRICE_REVERSION_STRATEGY][
        "wins"
    ] == 1

    collector = SimpleNamespace(
        status="LIVE",
        error=None,
        updated_at=None,
        interval=1.0,
        prediction=None,
    )
    dashboard = store.dashboard(
        collector,
        include_experiments=False,
    )
    research = dashboard["researchForward"]
    assert research["micropricePairedExperiment"]["pairedMarkets"] == 1
    assert MICROPRICE_CONFIRM_STRATEGY in research["strategies"]
    assert MICROPRICE_REVERSION_STRATEGY in research["strategies"]
    assert dashboard["summaries"][MICROPRICE_CONFIRM_STRATEGY][
        "losses"
    ] == 1
    assert dashboard["summaries"][MICROPRICE_REVERSION_STRATEGY][
        "wins"
    ] == 1
