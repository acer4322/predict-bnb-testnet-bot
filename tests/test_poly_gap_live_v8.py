from __future__ import annotations

import inspect
from pathlib import Path

from predict_bot.poly_gap_live_v7 import ReArmingScalpPolyGapLiveEngine
from predict_bot.poly_gap_live_v8 import (
    ENTRY_MAX_DETERIORATION_BPS,
    ENTRY_SLIPPAGE_BPS,
    EXIT_SLIPPAGE_BPS,
    MomentumTolerancePolyGapLiveEngine,
)


def test_v8_defaults_match_market_entry_and_exit_priority() -> None:
    assert ENTRY_SLIPPAGE_BPS == 1000
    assert ENTRY_MAX_DETERIORATION_BPS == 1000
    assert EXIT_SLIPPAGE_BPS == 2000
    assert EXIT_SLIPPAGE_BPS > ENTRY_SLIPPAGE_BPS


def test_v8_builds_on_repeatable_v7_scalp_cycle() -> None:
    assert issubclass(MomentumTolerancePolyGapLiveEngine, ReArmingScalpPolyGapLiveEngine)


def test_v8_snapshot_exposes_relaxed_signed_quote_rule(tmp_path: Path) -> None:
    engine = MomentumTolerancePolyGapLiveEngine(tmp_path / "poly_gap_live_v8.db")
    try:
        state = engine.snapshot()
        tuning = state["executionTuning"]
        assert state["version"] == "POLY_GAP_DEDICATED_LIVE_V8"
        assert tuning["entrySlippageBps"] == 1000
        assert tuning["exitSlippageBps"] == 2000
        assert tuning["initialSignalMinimumEdge"] > 0
        assert tuning["minimumExecutableEntryEdge"] is None
        assert tuning["entrySignedQuoteMaxDeteriorationBps"] == 1000
        assert tuning["entrySignedQuoteMaxDeteriorationPct"] == 10.0
        assert tuning["entryEdgeRuleChanged"] is True
        assert tuning["exitPriority"] is True
        assert state["entryExecution"]["priceCapRejected"] == 0
    finally:
        engine.stop()


def test_v8_entry_uses_price_cap_not_post_quote_three_percent_edge() -> None:
    source = inspect.getsource(MomentumTolerancePolyGapLiveEngine._open_round)
    assert "average > price_cap" in source
    assert "ENTRY_SIGNED_QUOTE_ABOVE_PRICE_CAP" in source
    assert "current_direction != row[\"side\"]" in source
    # edgeAfterQuote remains diagnostic, but must not be the acceptance gate.
    assert "edge_after + 1e-12 < base.SCALP_MIN_EDGE" not in source


def test_v8_supervisor_entrypoint_is_current() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "predict_bot"
        / "supervisor.py"
    ).read_text(encoding="utf-8")
    assert "predict_bot.poly_gap_live_v8" in source
    assert "10% momentum entry cap" in source
    assert "20% exit-priority tolerance" in source
