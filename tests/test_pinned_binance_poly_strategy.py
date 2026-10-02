from __future__ import annotations

import time
from pathlib import Path

from predict_bot.poly_gap_live_v45 import PinnedStrategySelectablePolyGapLiveEngine


def _observer_state(now_ms: int, *, pinned: bool = True) -> dict:
    points = []
    for offset in range(6, -1, -1):
        up = 0.50 if pinned else (0.50 if offset >= 3 else 0.64)
        down = 0.50 if pinned else (0.50 if offset >= 3 else 0.36)
        points.append(
            {
                "sampledAtMs": now_ms - offset * 1000,
                "binanceUp": up,
                "binanceDown": down,
            }
        )
    return {
        "windowEndMs": now_ms + 120_000,
        "secondsLeft": 120.0,
        "poly": {"status": "LIVE"},
        "binance": {
            "status": "LIVE",
            "observedAtMs": now_ms,
        },
        "comparison": {
            "binanceUpMid": 0.50 if pinned else 0.64,
            "binanceDownMid": 0.50 if pinned else 0.36,
        },
        "trajectory": points,
    }


def _engine(tmp_path: Path) -> PinnedStrategySelectablePolyGapLiveEngine:
    engine = PinnedStrategySelectablePolyGapLiveEngine(db_path=tmp_path / "pinned.db")
    now_ms = int(time.time() * 1000)
    engine.market_cache = {
        "market_id": 12345,
        "topic_id": 10,
        "up_token_id": "up",
        "down_token_id": "down",
        "fee_rate_bps": 200,
        "end_ms": now_ms + 120_000,
    }
    engine.update_settings(
        {
            "entryStrategyMode": "PINNED_DIVERGENCE",
            "pinnedBinanceCenter": 0.50,
            "pinnedBinanceHalfWidth": 0.03,
            "pinnedMinimumDurationSeconds": 5,
            "pinnedMaximumRange": 0.04,
            "pinnedRequiredRatio": 0.80,
            "pinnedPolyThreshold": 0.85,
            "pinnedMinimumGap": 0.30,
            "pinnedMinimumRemainingSeconds": 20,
            "pinnedMaximumSelectedAsk": 0.60,
            "pinnedOneEntryPerMarket": True,
        }
    )
    return engine


def test_pinned_divergence_allows_strong_poly_against_stable_half_half(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    now_ms = int(time.time() * 1000)
    engine.market_cache["end_ms"] = now_ms + 120_000
    engine._observer_asset_state_for_pin = lambda: _observer_state(now_ms, pinned=True)  # type: ignore[method-assign]

    result = engine._evaluate_pinned_divergence(
        {"direction": "UP", "selectedMid": 0.90}
    )

    assert result["allowed"] is True
    assert result["state"] == "ARMED"
    assert result["direction"] == "UP"
    assert result["divergenceGap"] >= 0.39
    engine.stop()


def test_pinned_divergence_rejects_binance_that_already_moved(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    now_ms = int(time.time() * 1000)
    engine.market_cache["end_ms"] = now_ms + 120_000
    engine._observer_asset_state_for_pin = lambda: _observer_state(now_ms, pinned=False)  # type: ignore[method-assign]

    result = engine._evaluate_pinned_divergence(
        {"direction": "UP", "selectedMid": 0.90}
    )

    assert result["allowed"] is False
    assert result["state"] in {"WAITING_BINANCE_UNPINNED", "PINNING"}
    engine.stop()


def test_pinned_divergence_requires_strong_poly_threshold(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    now_ms = int(time.time() * 1000)
    engine.market_cache["end_ms"] = now_ms + 120_000
    engine._observer_asset_state_for_pin = lambda: _observer_state(now_ms, pinned=True)  # type: ignore[method-assign]

    result = engine._evaluate_pinned_divergence(
        {"direction": "UP", "selectedMid": 0.80}
    )

    assert result["allowed"] is False
    assert result["state"] == "WAITING_STRONG_POLY"
    engine.stop()


def test_strategy_defaults_to_existing_poly_gap_mode(tmp_path: Path) -> None:
    engine = PinnedStrategySelectablePolyGapLiveEngine(db_path=tmp_path / "default.db")
    assert engine._settings()["entryStrategyMode"] == "POLY_GAP"
    engine.stop()
