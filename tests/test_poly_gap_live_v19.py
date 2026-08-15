from __future__ import annotations

from pathlib import Path

from predict_bot.poly_gap_live_v19 import SharedCollectorBinanceMarketPolyGapLiveEngine


def _payload(start_ms: int, market_id: int = 6835000) -> dict[str, object]:
    return {
        "latest": {
            "market_id": market_id,
            "seconds_left": 240.0,
        },
        "binanceMarketReference": {
            "market_id": market_id,
            "topic_id": market_id + 1000,
            "start_ms": start_ms,
            "end_ms": start_ms + 300_000,
            "fee_bps": 200,
            "up_token_id": f"up-{market_id}",
            "down_token_id": f"down-{market_id}",
        },
    }


def test_shared_reference_accepts_only_exact_current_window() -> None:
    start_ms = 1_800_000_000_000
    cache, reason = SharedCollectorBinanceMarketPolyGapLiveEngine._reference_cache(
        _payload(start_ms),
        target_start_ms=start_ms,
    )
    assert reason is None
    assert cache is not None
    assert cache["market_id"] == 6835000
    assert cache["start_ms"] == start_ms
    assert cache["end_ms"] == start_ms + 300_000
    assert cache["up_token_id"] == "up-6835000"
    assert cache["down_token_id"] == "down-6835000"


def test_shared_reference_rejects_previous_window_even_if_trajectory_exists() -> None:
    start_ms = 1_800_000_000_000
    cache, reason = SharedCollectorBinanceMarketPolyGapLiveEngine._reference_cache(
        _payload(start_ms - 300_000),
        target_start_ms=start_ms,
    )
    assert cache is None
    assert reason is not None
    assert "start mismatch" in reason


def test_shared_reference_rejects_trajectory_identity_mismatch() -> None:
    start_ms = 1_800_000_000_000
    payload = _payload(start_ms)
    payload["latest"] = {"market_id": 9999999, "seconds_left": 240.0}
    cache, reason = SharedCollectorBinanceMarketPolyGapLiveEngine._reference_cache(
        payload,
        target_start_ms=start_ms,
    )
    assert cache is None
    assert reason is not None
    assert "trajectory/reference market mismatch" in reason


def test_v19_snapshot_preserves_prior_safety_and_disables_duplicate_prefetch(tmp_path: Path) -> None:
    engine = SharedCollectorBinanceMarketPolyGapLiveEngine(tmp_path / "v19.db")
    try:
        state = engine.snapshot()
        assert state["version"] == "POLY_GAP_DEDICATED_LIVE_V19"
        shared = state["binanceSharedMarketReference"]
        assert shared["enabled"] is True
        assert shared["requiresExactCurrentFiveMinuteWindow"] is True
        assert shared["requiresSame8766TrajectoryMarketId"] is True
        assert shared["nearestWrongWindowFallback"] is False
        assert shared["executionOrdersStillUseDedicated8769Client"] is True
        prefetch = state["binanceMarketPrefetch"]
        assert prefetch["dedicatedLivePrefetchEnabled"] is False
        assert prefetch["shared8766CollectorPrefetchPreferred"] is True
        assert state["entryMarketBinding"]["requiresExactPolyEventSlug"] is True
        assert state["settlementRecovery"]["unresolvedExecutionStillBlocksNewMarket"] is True
    finally:
        engine.stop()


def test_server_v4_exposes_market_reference_and_supervisor_launches_v19() -> None:
    root = Path(__file__).resolve().parents[1]
    server_source = (
        root / "src" / "predict_bot" / "server_binance_prefetch_v4.py"
    ).read_text(encoding="utf-8")
    supervisor_source = (
        root / "src" / "predict_bot" / "supervisor.py"
    ).read_text(encoding="utf-8")
    assert 'payload["binanceMarketReference"]' in server_source
    assert "current_m_market_reference" in server_source
    assert "predict_bot.server_binance_prefetch_v4" in supervisor_source
    assert "predict_bot.poly_gap_live_v19" in supervisor_source
