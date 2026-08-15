from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from predict_bot import live_trading, m_realtime
from predict_bot.m_realtime import MSeriesRealtimeEngine
from predict_bot.microprice_confirm_stale_exhausted_guard import (
    MAX_GUARDED_ENTRY_PRICE,
    MIN_DELAY_TRIGGER_MS,
    MIN_EFFECTIVE_BOOK_AGE_MS,
    SOURCE_STRATEGY,
    STALE_EXHAUSTED_GUARD_STRATEGY,
    STALE_EXHAUSTED_GUARD_VERSION,
)
from predict_bot.server import DEFAULT_CONFIG, Store


def _market(market_id=101):
    now_ms = datetime.now(timezone.utc).timestamp() * 1_000
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


def _store_and_engine(tmp_path):
    store = Store(tmp_path / "simulation.db")
    disabled = {
        key: False
        for key in DEFAULT_CONFIG
        if key.endswith("_enabled")
    }
    disabled["strategy_r_microprice_enabled"] = True
    store.update_config(disabled)
    engine = MSeriesRealtimeEngine(store=store, current_market=_market)
    return store, engine


def _open_source(
    store,
    *,
    market_id,
    entry,
    book_age_ms,
    delay_ms,
    first_signal,
    final_signal,
    include_diagnostics=True,
):
    diagnostics = None
    if include_diagnostics:
        signal_at = datetime.now(timezone.utc) - timedelta(milliseconds=delay_ms)
        diagnostics = {
            "variant_mode": "FOLLOW_CONFIRMED_IMBALANCE",
            "source_side": "UP",
            "selected_side": "UP",
            "source_signal": final_signal,
            "confirmation_count": 2,
            "confirmation_duration_ms": 200.0,
            "book_age_ms": book_age_ms,
            "book_skew_ms": 20.0,
            "signal_timestamp": signal_at.isoformat(),
            "samples": [
                {"sequence": "first", "score": first_signal},
                {"sequence": "final", "score": final_signal},
            ],
        }
    store.open_trade(
        strategy=SOURCE_STRATEGY,
        topic_id=202,
        market_id=market_id,
        side="UP",
        entry=entry,
        target=None,
        stake=5.0,
        fee_rate_bps=200,
        note="synthetic confirmed V2 source",
        strategy_version="MICROPRICE_VARIANTS_V2_RELAXED",
        diagnostics=diagnostics,
    )


def test_blocks_low_entry_when_effective_book_is_stale_and_signal_decays(tmp_path):
    store, _ = _store_and_engine(tmp_path)
    _open_source(
        store,
        market_id=301,
        entry=0.50,
        book_age_ms=400.0,
        delay_ms=100.0,
        first_signal=1.20,
        final_signal=0.80,
    )

    assert store.db.execute(
        "SELECT 1 FROM trades WHERE strategy=? AND market_id=?",
        (STALE_EXHAUSTED_GUARD_STRATEGY, 301),
    ).fetchone() is None
    decision = store.db.execute(
        """SELECT eligible, block_reason, effective_book_age_ms,
                  signal_to_open_ms, signal_decayed
             FROM microprice_confirm_stale_exhausted_decisions
            WHERE market_id=?""",
        (301,),
    ).fetchone()
    assert decision is not None
    assert int(decision["eligible"]) == 0
    assert "STALE" in str(decision["block_reason"])
    assert float(decision["effective_book_age_ms"]) >= MIN_EFFECTIVE_BOOK_AGE_MS
    assert float(decision["signal_to_open_ms"]) >= MIN_DELAY_TRIGGER_MS
    assert int(decision["signal_decayed"]) == 1


def test_preserves_low_entry_when_data_is_fresh_and_signal_strengthens(tmp_path):
    store, _ = _store_and_engine(tmp_path)
    _open_source(
        store,
        market_id=302,
        entry=MAX_GUARDED_ENTRY_PRICE,
        book_age_ms=100.0,
        delay_ms=0.0,
        first_signal=0.80,
        final_signal=1.20,
    )

    trade = store.db.execute(
        """SELECT strategy_version, diagnostics_json
             FROM trades WHERE strategy=? AND market_id=?""",
        (STALE_EXHAUSTED_GUARD_STRATEGY, 302),
    ).fetchone()
    assert trade is not None
    assert trade["strategy_version"] == STALE_EXHAUSTED_GUARD_VERSION
    assert '"paper_only": true' in str(trade["diagnostics_json"])
    decision = store.db.execute(
        """SELECT eligible, block_reason, opened
             FROM microprice_confirm_stale_exhausted_decisions
            WHERE market_id=?""",
        (302,),
    ).fetchone()
    assert decision is not None
    assert int(decision["eligible"]) == 1
    assert decision["block_reason"] is None
    assert int(decision["opened"]) == 1


def test_high_entry_is_not_blocked_even_when_low_price_diagnostics_are_missing(tmp_path):
    store, _ = _store_and_engine(tmp_path)
    _open_source(
        store,
        market_id=303,
        entry=0.70,
        book_age_ms=0.0,
        delay_ms=0.0,
        first_signal=0.0,
        final_signal=0.0,
        include_diagnostics=False,
    )
    assert store.db.execute(
        "SELECT 1 FROM trades WHERE strategy=? AND market_id=?",
        (STALE_EXHAUSTED_GUARD_STRATEGY, 303),
    ).fetchone() is not None


def test_dashboard_exposes_forward_only_stats_without_live_registration(tmp_path):
    store, _ = _store_and_engine(tmp_path)
    _open_source(
        store,
        market_id=304,
        entry=0.50,
        book_age_ms=400.0,
        delay_ms=100.0,
        first_signal=1.20,
        final_signal=0.80,
    )
    _open_source(
        store,
        market_id=305,
        entry=0.50,
        book_age_ms=100.0,
        delay_ms=0.0,
        first_signal=0.80,
        final_signal=1.20,
    )

    collector = SimpleNamespace(
        status="LIVE",
        error=None,
        updated_at=None,
        interval=1.0,
        prediction=None,
    )
    payload = store.dashboard(collector, include_experiments=False)
    experiment = payload["researchForward"][
        "micropriceConfirmStaleExhaustedGuard"
    ]
    assert experiment["version"] == STALE_EXHAUSTED_GUARD_VERSION
    assert experiment["sourceMarkets"] == 2
    assert experiment["blocked"] == 1
    assert STALE_EXHAUSTED_GUARD_STRATEGY in experiment["strategies"]
    assert payload["summaries"][STALE_EXHAUSTED_GUARD_STRATEGY]["trades"] == 1

    assert STALE_EXHAUSTED_GUARD_STRATEGY not in live_trading.LIVE_SUPPORTED_STRATEGIES
    assert STALE_EXHAUSTED_GUARD_STRATEGY not in m_realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES
