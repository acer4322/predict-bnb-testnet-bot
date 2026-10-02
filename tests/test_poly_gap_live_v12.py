from __future__ import annotations

from pathlib import Path

from predict_bot.poly_gap_live_v11 import LegacyAwareExitPolyGapLiveEngine
from predict_bot.poly_gap_live_v12 import (
    STABLE_EXIT_CONFIRM_MS,
    STABLE_EXIT_CONFIRM_SAMPLES,
    StableExitPolyGapLiveEngine,
)


def active(side: str = "UP") -> dict[str, object]:
    return {
        "id": 7,
        "market_id": 123,
        "round_no": 2,
        "side": side,
        "state": "OPEN",
    }


def test_v12_builds_on_v11() -> None:
    assert issubclass(StableExitPolyGapLiveEngine, LegacyAwareExitPolyGapLiveEngine)


def test_normal_exit_requires_distinct_receipts_and_time(tmp_path: Path) -> None:
    engine = StableExitPolyGapLiveEngine(tmp_path / "v12-confirm.db")
    try:
        row = active("UP")
        assert engine._observe_exit_flip(
            row,
            direction="DOWN",
            up_mid=0.40,
            receipt_ms=1_000,
            now_ms=1_000,
        ) is False
        assert engine.exit_flip_candidate is not None
        assert engine.exit_flip_candidate["samples"] == 1

        # Re-reading the same Poly snapshot must not count as another confirmation.
        assert engine._observe_exit_flip(
            row,
            direction="DOWN",
            up_mid=0.40,
            receipt_ms=1_000,
            now_ms=1_300,
        ) is False
        assert engine.exit_flip_candidate is not None
        assert engine.exit_flip_candidate["samples"] == 1

        assert engine._observe_exit_flip(
            row,
            direction="DOWN",
            up_mid=0.40,
            receipt_ms=1_250,
            now_ms=1_300,
        ) is False
        assert engine.exit_flip_candidate is not None
        assert engine.exit_flip_candidate["samples"] == 2

        # Third distinct receipt arrives after the 500 ms persistence window.
        assert engine._observe_exit_flip(
            row,
            direction="DOWN",
            up_mid=0.40,
            receipt_ms=1_600,
            now_ms=1_600,
        ) is True
        assert engine.exit_flip_candidate is None
        assert engine.exit_flip_confirmed_count == 1
    finally:
        engine.stop()


def test_emergency_exit_bypasses_debounce(tmp_path: Path) -> None:
    engine = StableExitPolyGapLiveEngine(tmp_path / "v12-emergency.db")
    try:
        assert engine._observe_exit_flip(
            active("UP"),
            direction="DOWN",
            up_mid=0.29,
            receipt_ms=2_000,
            now_ms=2_000,
        ) is True
        assert engine.exit_emergency_count == 1
        assert engine.exit_flip_candidate is None
    finally:
        engine.stop()


def test_v12_snapshot_declares_stability_first_exit_rules(tmp_path: Path) -> None:
    engine = StableExitPolyGapLiveEngine(tmp_path / "v12-snapshot.db")
    try:
        state = engine.snapshot()
        assert state["version"] == "POLY_GAP_DEDICATED_LIVE_V12"
        stable = state["stableExit"]
        assert stable["normalConfirmMs"] == STABLE_EXIT_CONFIRM_MS
        assert stable["normalConfirmDistinctSamples"] == STABLE_EXIT_CONFIRM_SAMPLES
        assert stable["requiresDistinctPolyReceiptTimestamps"] is True
        assert stable["sellExecutionToleranceChangedFromV11"] is False
    finally:
        engine.stop()
