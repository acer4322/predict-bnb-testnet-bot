from __future__ import annotations

from pathlib import Path

from predict_bot.poly_gap_live_v14 import MarketBoundPolyGapLiveEngine
from predict_bot.poly_gap_live_v15 import PrefetchedBinanceMarketPolyGapLiveEngine


def cache(start_ms: int, market_id: int) -> dict[str, object]:
    return {
        "market_id": market_id,
        "topic_id": market_id + 1000,
        "up_token_id": f"up-{market_id}",
        "down_token_id": f"down-{market_id}",
        "fee_rate_bps": 200,
        "start_ms": start_ms,
        "end_ms": start_ms + 300_000,
    }


def test_v15_builds_on_v14() -> None:
    assert issubclass(PrefetchedBinanceMarketPolyGapLiveEngine, MarketBoundPolyGapLiveEngine)


def test_prefetched_market_promotes_only_for_exact_target_window(tmp_path: Path) -> None:
    engine = PrefetchedBinanceMarketPolyGapLiveEngine(tmp_path / "v15-promote.db")
    try:
        current_start = 1_800_000_000_000
        next_market = cache(current_start + 300_000, 22)
        with engine.lock:
            engine.binance_prefetched_market = dict(next_market)
            engine.binance_prefetch_target_start_ms = current_start + 300_000

        assert engine._promote_prefetched(current_start) is None
        with engine.lock:
            assert engine.market_cache is None
            assert engine.binance_prefetched_market is not None

        promoted = engine._promote_prefetched(current_start + 300_000)
        assert promoted is not None
        assert promoted["market_id"] == 22
        with engine.lock:
            assert engine.market_cache is not None
            assert engine.binance_prefetched_market is None
        assert engine.binance_prefetch_promotions == 1
    finally:
        engine.stop()


def test_stale_old_binance_cache_is_not_reused_for_new_bucket(tmp_path: Path, monkeypatch) -> None:
    engine = PrefetchedBinanceMarketPolyGapLiveEngine(tmp_path / "v15-stale.db")
    try:
        current_start = 1_800_000_300_000
        with engine.lock:
            engine.market_cache = cache(current_start - 300_000, 11)

        monkeypatch.setattr(engine, "_binance_server_now_ms", lambda: current_start + 1_000)
        monkeypatch.setattr(engine, "_fetch_exact_cache", lambda _target: None)
        result = engine._prime_market(force=True)
        assert result is None
        with engine.lock:
            assert engine.market_cache is None
        assert engine.binance_exact_current_misses == 1
        assert "waiting for exact current Binance" in str(engine.binance_last_current_error)
    finally:
        engine.stop()


def test_v15_snapshot_keeps_v14_binding_and_exposes_prefetch(tmp_path: Path) -> None:
    engine = PrefetchedBinanceMarketPolyGapLiveEngine(tmp_path / "v15-snapshot.db")
    try:
        state = engine.snapshot()
        assert state["version"] == "POLY_GAP_DEDICATED_LIVE_V15"
        prefetch = state["binanceMarketPrefetch"]
        assert prefetch["enabled"] is True
        assert prefetch["neverUsesNearestWrongWindow"] is True
        assert prefetch["v14PolyMarketBindingStillRequired"] is True
        binding = state["entryMarketBinding"]
        assert binding["requiresExactPolyEventSlug"] is True
        assert binding["requiresCurrentWindowReceipt"] is True
    finally:
        engine.stop()
