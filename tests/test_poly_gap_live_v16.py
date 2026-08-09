from __future__ import annotations

from pathlib import Path

from predict_bot.poly_gap_live_v15 import PrefetchedBinanceMarketPolyGapLiveEngine
from predict_bot.poly_gap_live_v16 import TimeSyncHardenedPolyGapLiveEngine


def test_v16_builds_on_v15_without_replacing_trading_rules() -> None:
    assert issubclass(TimeSyncHardenedPolyGapLiveEngine, PrefetchedBinanceMarketPolyGapLiveEngine)


def test_v16_snapshot_exposes_self_healing_clock_contract(tmp_path: Path) -> None:
    engine = TimeSyncHardenedPolyGapLiveEngine(tmp_path / "v16.db")
    try:
        state = engine.snapshot()
        assert state["version"] == "POLY_GAP_DEDICATED_LIVE_V16"
        clock = state["binanceTimeSync"]
        assert clock["periodicRefresh"] is True
        assert clock["wallClockJumpDetection"] is True
        assert clock["signedGet1021AutoRetryOnce"] is True
        assert clock["signedPost1021NeverBlindRetried"] is True
        assert clock["manualRestartShouldNotBeRequired"] is True
        assert state["binanceMarketPrefetch"]["v14PolyMarketBindingStillRequired"] is True
        assert state["entryMarketBinding"]["collectorNotReadyNeverFallsBackToOldPoly"] is True
    finally:
        engine.stop()
