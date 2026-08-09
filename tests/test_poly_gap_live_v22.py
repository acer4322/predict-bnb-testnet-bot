from __future__ import annotations

from pathlib import Path

from predict_bot import poly_gap_live as live_base
from predict_bot.binance_exact_market import bucket_start_ms
from predict_bot.poly_gap_live_v21 import LightweightSharedMarketPolyGapLiveEngine
from predict_bot.poly_gap_live_v22 import PreflightOrderedSharedMarketPolyGapLiveEngine


def _current_cache() -> dict[str, object]:
    start_ms = bucket_start_ms(live_base._now_ms())
    return {
        "market_id": 6836350,
        "topic_id": 4465601,
        "up_token_id": "up-token",
        "down_token_id": "down-token",
        "fee_rate_bps": 200,
        "start_ms": start_ms,
        "end_ms": start_ms + 300_000,
    }


def test_v22_preloads_8766_market_before_inherited_tick(
    tmp_path: Path, monkeypatch
) -> None:
    engine = PreflightOrderedSharedMarketPolyGapLiveEngine(tmp_path / "v22.db")
    try:
        cache = _current_cache()
        monkeypatch.setattr(engine, "_local_current_cache", lambda _target: dict(cache))
        observed: dict[str, object] = {}

        def fake_parent_tick(self: LightweightSharedMarketPolyGapLiveEngine) -> None:
            observed["market"] = dict(self.market_cache or {})

        monkeypatch.setattr(LightweightSharedMarketPolyGapLiveEngine, "_tick", fake_parent_tick)

        engine._tick()

        assert observed["market"] == cache
        assert engine.shared_market_preload_attempts == 1
        assert engine.shared_market_preload_hits == 1
        assert engine.shared_market_preload_last_market_id == 6836350
        assert engine.local_reference_attempts == 0  # local method was stubbed at the boundary
    finally:
        engine.stop()


def test_v22_does_not_mask_execution_preflight_failure_as_market_wait(
    tmp_path: Path, monkeypatch
) -> None:
    engine = PreflightOrderedSharedMarketPolyGapLiveEngine(tmp_path / "v22-status.db")
    try:
        cache = _current_cache()
        monkeypatch.setattr(engine, "_local_current_cache", lambda _target: dict(cache))
        monkeypatch.setattr(live_base, "MASTER_ENABLED", True)
        monkeypatch.setattr(
            engine,
            "_settings",
            lambda: {
                "runtimeEnabled": True,
                "lossTripped": False,
                "stakeUsdt": 1.0,
                "maximumLossEnabled": True,
                "maximumLossUsdt": 10.0,
            },
        )

        def fake_parent_tick(self: LightweightSharedMarketPolyGapLiveEngine) -> None:
            self.status = "WAITING_BINANCE_CURRENT_MARKET"
            self.last_error = "exact Binance 5m metadata unavailable"
            self.execution_preflight_ready = False
            self.execution_preflight_last_error = "wallet preflight timed out"

        monkeypatch.setattr(LightweightSharedMarketPolyGapLiveEngine, "_tick", fake_parent_tick)

        engine._tick()

        assert engine.status == "BLOCKED_EXECUTION_PREFLIGHT"
        assert engine.last_error == "wallet preflight timed out"
        assert engine.market_cache is not None
        assert int(engine.market_cache["market_id"]) == 6836350
    finally:
        engine.stop()


def test_v22_snapshot_exposes_ordered_preflight_diagnostics(tmp_path: Path) -> None:
    engine = PreflightOrderedSharedMarketPolyGapLiveEngine(tmp_path / "v22-state.db")
    try:
        state = engine.snapshot()
        assert state["version"] == "POLY_GAP_DEDICATED_LIVE_V22"
        assert state["marketIdentityPreflight"]["shared8766BeforeExecutionClient"] is True
        assert state["executionPreflight"]["marketDiscoveryFailureCannotMaskThis"] is True
        assert state["binanceSharedMarketReference"]["invokedBeforeExecutionPreflight"] is True
    finally:
        engine.stop()


def test_supervisor_launches_v22_and_server_prefetch_v5() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "predict_bot"
        / "supervisor.py"
    ).read_text(encoding="utf-8")
    assert "predict_bot.poly_gap_live_v22" in source
    assert "predict_bot.server_binance_prefetch_v5" in source
