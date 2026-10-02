from __future__ import annotations

from pathlib import Path
from typing import Any

from predict_bot.poly_gap_live_v21 import LightweightSharedMarketPolyGapLiveEngine


def _payload(start_ms: int, market_id: int = 12345) -> dict[str, Any]:
    return {
        "ok": True,
        "collectorStatus": "LIVE",
        "marketReference": {
            "market_id": market_id,
            "topic_id": market_id + 1000,
            "start_ms": start_ms,
            "end_ms": start_ms + 300_000,
            "fee_bps": 200,
            "up_token_id": f"up-{market_id}",
            "down_token_id": f"down-{market_id}",
        },
    }


def test_lightweight_reference_accepts_exact_current_window_without_dashboard_snapshot() -> None:
    start_ms = 1_800_000_000_000
    cache, reason = LightweightSharedMarketPolyGapLiveEngine._lightweight_reference_cache(
        _payload(start_ms),
        target_start_ms=start_ms,
    )
    assert reason is None
    assert cache is not None
    assert cache["market_id"] == 12345
    assert cache["start_ms"] == start_ms
    assert cache["end_ms"] == start_ms + 300_000


def test_lightweight_reference_rejects_previous_or_future_window() -> None:
    start_ms = 1_800_000_000_000
    cache, reason = LightweightSharedMarketPolyGapLiveEngine._lightweight_reference_cache(
        _payload(start_ms - 300_000),
        target_start_ms=start_ms,
    )
    assert cache is None
    assert "start mismatch" in str(reason)


def test_v21_snapshot_declares_lightweight_local_endpoint(tmp_path: Path) -> None:
    engine = LightweightSharedMarketPolyGapLiveEngine(tmp_path / "v21.db")
    try:
        state = engine.snapshot()
        assert state["version"] == "POLY_GAP_DEDICATED_LIVE_V21"
        shared = state["binanceSharedMarketReference"]
        assert shared["endpointMode"] == "LIGHTWEIGHT_MARKET_METADATA_ONLY"
        assert shared["usesFullRealtimeDashboardPayload"] is False
        assert shared["localHttpTrustEnv"] is False
        assert shared["nearestWrongWindowFallback"] is False
        assert state["entryMarketBinding"]["requiresExactPolyEventSlug"] is True
        assert state["settlementRecovery"]["unresolvedExecutionStillBlocksNewMarket"] is True
    finally:
        engine.stop()


def test_supervisor_launches_v21_and_lightweight_8766_server() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "predict_bot"
        / "supervisor.py"
    ).read_text(encoding="utf-8")
    assert "predict_bot.poly_gap_live_v21" in source
    assert "predict_bot.server_binance_prefetch_v5" in source
