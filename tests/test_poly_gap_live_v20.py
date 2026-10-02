from __future__ import annotations

from pathlib import Path

from predict_bot.poly_gap_live_v20 import RolloverRaceSafeSharedMarketPolyGapLiveEngine


def _payload(
    start_ms: int,
    *,
    reference_market_id: int = 6836000,
    latest_market_id: int | None = None,
    realtime_market_id: int | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "binanceMarketReference": {
            "market_id": reference_market_id,
            "topic_id": reference_market_id + 1000,
            "start_ms": start_ms,
            "end_ms": start_ms + 300_000,
            "fee_bps": 200,
            "up_token_id": f"up-{reference_market_id}",
            "down_token_id": f"down-{reference_market_id}",
        },
    }
    if latest_market_id is not None:
        payload["latest"] = {"market_id": latest_market_id, "seconds_left": 1.0}
    if realtime_market_id is not None:
        payload["mRealtime"] = {"marketId": realtime_market_id}
    return payload


def test_v20_accepts_exact_new_reference_while_latest_snapshot_is_still_previous_market() -> None:
    start_ms = 1_800_000_000_000
    cache, reason = RolloverRaceSafeSharedMarketPolyGapLiveEngine._reference_cache(
        _payload(
            start_ms,
            reference_market_id=6836001,
            latest_market_id=6835999,
            realtime_market_id=6835999,
        ),
        target_start_ms=start_ms,
    )
    assert reason is None
    assert cache is not None
    assert cache["market_id"] == 6836001
    assert cache["_8766_latest_market_id"] == 6835999
    assert cache["_8766_m_realtime_market_id"] == 6835999


def test_v20_still_rejects_previous_or_future_reference_window() -> None:
    start_ms = 1_800_000_000_000
    previous, previous_reason = RolloverRaceSafeSharedMarketPolyGapLiveEngine._reference_cache(
        _payload(start_ms - 300_000),
        target_start_ms=start_ms,
    )
    future, future_reason = RolloverRaceSafeSharedMarketPolyGapLiveEngine._reference_cache(
        _payload(start_ms + 300_000),
        target_start_ms=start_ms,
    )
    assert previous is None
    assert future is None
    assert previous_reason and "start mismatch" in previous_reason
    assert future_reason and "start mismatch" in future_reason


def test_v20_snapshot_keeps_strict_binding_but_makes_trajectory_id_diagnostic(tmp_path: Path) -> None:
    engine = RolloverRaceSafeSharedMarketPolyGapLiveEngine(tmp_path / "v20.db")
    try:
        state = engine.snapshot()
        assert state["version"] == "POLY_GAP_DEDICATED_LIVE_V20"
        shared = state["binanceSharedMarketReference"]
        assert shared["requiresExactCurrentFiveMinuteWindow"] is True
        assert shared["requiresSame8766TrajectoryMarketId"] is False
        assert shared["trajectoryMarketIdIsReadinessDiagnosticOnly"] is True
        assert shared["stalePreviousBucketCacheClearedImmediately"] is True
        assert shared["directBookStillRequiredBeforeEntry"] is True
        assert shared["v14PolyBinanceExactBindingStillRequired"] is True
        assert shared["nearestWrongWindowFallback"] is False
        assert state["entryMarketBinding"]["requiresExactPolyEventSlug"] is True
        assert state["settlementRecovery"]["unresolvedExecutionStillBlocksNewMarket"] is True
    finally:
        engine.stop()


def test_supervisor_launches_v20() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "predict_bot"
        / "supervisor.py"
    ).read_text(encoding="utf-8")
    assert "predict_bot.server_binance_prefetch_v4" in source
    assert "predict_bot.poly_gap_live_v20" in source
