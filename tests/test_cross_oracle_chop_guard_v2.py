from __future__ import annotations

from pathlib import Path

from predict_bot.cross_oracle_strategy_chop_guard_v2 import ImmediateChopBreakerPaperEngine


def test_current_market_becomes_immediate_entry_block_after_two_reversals(tmp_path: Path) -> None:
    engine = ImmediateChopBreakerPaperEngine(tmp_path / "guard-v2.db", lambda: {})
    try:
        engine._chop_current = {
            "marketId": 777,
            "polySlug": "btc-updown-5m-777",
            "baselineDirection": "UP",
            "reversals": 2,
            "distinctReceipts": 12,
            "lastReceiptMs": 12_000,
            "firstSeenAtMs": 1_000,
            "lastSeenAtMs": 12_000,
            "evaluable": True,
            "reason": None,
        }
        state = engine.snapshot()["chopGuard"]
        assert state["currentMarketChoppy"] is True
        assert state["blockNewEntries"] is True
        assert state["persistentPaused"] is False
        assert state["sameMarketImmediateBreaker"] is True
    finally:
        engine.stop()


def test_calm_current_market_does_not_block_when_persistent_guard_is_clear(tmp_path: Path) -> None:
    engine = ImmediateChopBreakerPaperEngine(tmp_path / "guard-v2-calm.db", lambda: {})
    try:
        engine._chop_current = {
            "marketId": 778,
            "polySlug": "btc-updown-5m-778",
            "baselineDirection": "DOWN",
            "reversals": 1,
            "distinctReceipts": 10,
            "lastReceiptMs": 10_000,
            "firstSeenAtMs": 1_000,
            "lastSeenAtMs": 10_000,
            "evaluable": True,
            "reason": None,
        }
        state = engine.snapshot()["chopGuard"]
        assert state["currentMarketChoppy"] is False
        assert state["blockNewEntries"] is False
    finally:
        engine.stop()
