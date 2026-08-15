from __future__ import annotations

from pathlib import Path

from predict_bot.poly_gap_live_v13 import PaperChopGuardedPolyGapLiveEngine
from predict_bot.poly_gap_live_v14 import (
    ENTRY_POLY_WARMUP_MS,
    ENTRY_POLY_WARMUP_SAMPLES,
    MarketBoundPolyGapLiveEngine,
)


def _engine(tmp_path: Path) -> MarketBoundPolyGapLiveEngine:
    return MarketBoundPolyGapLiveEngine(tmp_path / "v14.db")


def _market(engine: MarketBoundPolyGapLiveEngine, bucket: int) -> None:
    with engine.lock:
        engine.market_cache = {
            "market_id": 123,
            "topic_id": 456,
            "up_token_id": "binance-up",
            "down_token_id": "binance-down",
            "fee_rate_bps": 200,
            "end_ms": (bucket + 300) * 1000,
        }


def _poly(bucket: int, *, slug_bucket: int | None = None, receipt_ms: int | None = None, generation: int = 7):
    slug_bucket = bucket if slug_bucket is None else slug_bucket
    return {
        "slug": f"btc-updown-5m-{slug_bucket}",
        "windowEndMs": (bucket + 300) * 1000,
        "receivedTimestampMs": receipt_ms if receipt_ms is not None else (bucket * 1000 + 1_000),
        "gapGeneration": generation,
        "direction": "UP",
        "selectedMid": 0.70,
        "upMid": 0.70,
        "ageMs": 10,
    }


def test_v14_builds_on_v13() -> None:
    assert issubclass(MarketBoundPolyGapLiveEngine, PaperChopGuardedPolyGapLiveEngine)


def test_old_poly_slug_is_rejected_even_when_timestamp_looks_fresh(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        bucket = 1_800_000_000
        _market(engine, bucket)
        now_ms = (bucket + 20) * 1000
        stale_identity = _poly(
            bucket,
            slug_bucket=bucket - 300,
            receipt_ms=now_ms - 50,
        )
        assert engine._strict_entry_binding(stale_identity, now_ms=now_ms) is None
        assert "slug mismatch" in str(engine.last_entry_market_gate_reason)
    finally:
        engine.stop()


def test_receipt_from_previous_window_is_rejected(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        bucket = 1_800_000_000
        _market(engine, bucket)
        now_ms = (bucket + 20) * 1000
        previous_receipt = _poly(bucket, receipt_ms=bucket * 1000 - 1)
        assert engine._strict_entry_binding(previous_receipt, now_ms=now_ms) is None
        assert "before current market window" in str(engine.last_entry_market_gate_reason)
    finally:
        engine.stop()


def test_new_market_requires_distinct_receipts_and_warmup_time(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        bucket = 1_800_000_000
        _market(engine, bucket)
        key = (123, f"btc-updown-5m-{bucket}", 9)

        assert engine._warm_entry_market(key, bucket * 1000 + 100, bucket * 1000 + 100) is False
        # Re-reading the same receipt can never advance the warm-up.
        assert engine._warm_entry_market(key, bucket * 1000 + 100, bucket * 1000 + 500) is False
        assert engine.entry_market_warmup is not None
        assert engine.entry_market_warmup["samples"] == 1

        assert engine._warm_entry_market(key, bucket * 1000 + 350, bucket * 1000 + 400) is False
        assert engine.entry_market_warmup is not None
        assert engine.entry_market_warmup["samples"] == 2

        assert engine._warm_entry_market(
            key,
            bucket * 1000 + ENTRY_POLY_WARMUP_MS + 50,
            bucket * 1000 + ENTRY_POLY_WARMUP_MS + 100,
        ) is True
        assert engine.entry_market_ready_key == key
        assert ENTRY_POLY_WARMUP_SAMPLES == 3
    finally:
        engine.stop()


def test_gap_generation_change_requires_new_warmup(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        bucket = 1_800_000_000
        _market(engine, bucket)
        old_key = (123, f"btc-updown-5m-{bucket}", 4)
        engine.entry_market_ready_key = old_key
        engine.entry_market_ready_at_ms = bucket * 1000 + 1_000
        engine.entry_market_ready_receipt_ms = bucket * 1000 + 1_000

        new_key = (123, f"btc-updown-5m-{bucket}", 5)
        assert engine._warm_entry_market(new_key, bucket * 1000 + 1_100, bucket * 1000 + 1_100) is False
        assert engine.entry_market_ready_key == old_key
        assert engine.entry_market_warmup is not None
        assert tuple(engine.entry_market_warmup["key"]) == new_key
    finally:
        engine.stop()


def test_v14_snapshot_declares_no_old_poly_fallback(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        state = engine.snapshot()
        assert state["version"] == "POLY_GAP_DEDICATED_LIVE_V14"
        gate = state["entryMarketBinding"]
        assert gate["requiresExactPolyEventSlug"] is True
        assert gate["requiresCurrentWindowReceipt"] is True
        assert gate["gapGenerationIsPartOfIdentity"] is True
        assert gate["signedQuoteRevalidatesSameIdentityBeforePlacement"] is True
        assert gate["collectorNotReadyNeverFallsBackToOldPoly"] is True
    finally:
        engine.stop()
