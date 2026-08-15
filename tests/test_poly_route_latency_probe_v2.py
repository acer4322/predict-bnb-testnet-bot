from pathlib import Path

from predict_bot.poly_route_latency_probe_v2 import PolyRouteLatencyProbeV2


def test_v2_preserves_negative_raw_source_age(tmp_path: Path):
    probe = PolyRouteLatencyProbeV2(250, 10.0, tmp_path / "probe.json", label="TEST")
    try:
        with probe.lock:
            probe._record_timestamp(
                "price_change",
                "1700000000100",
                1_700_000_000_100,
                1_700_000_000_050.0,
            )
        summary = probe.summary()
        assert summary["wsRawSourceAgeMs"]["median"] == -50.0
        assert summary["wsTimestampDiagnostics"]["negativeRawAge"] == 1
        assert summary["wsSourceAgeMs"]["samples"] == 0
    finally:
        probe.close()


def test_v2_splits_price_change_and_book_timestamps(tmp_path: Path):
    probe = PolyRouteLatencyProbeV2(250, 10.0, tmp_path / "probe.json", label="TEST")
    try:
        now_ms = 1_700_000_000_200
        with probe.lock:
            probe._record_timestamp("price_change", str(now_ms - 20), now_ms - 20, float(now_ms))
            probe.ws_event_counts["price_change"] += 1
            probe._record_timestamp("book", str(now_ms - 40), now_ms - 40, float(now_ms))
            probe.ws_event_counts["book"] += 1
        summary = probe.summary()
        assert summary["wsEventTypes"]["price_change"]["rawSourceAgeMs"]["median"] == 20.0
        assert summary["wsEventTypes"]["book"]["rawSourceAgeMs"]["median"] == 40.0
        assert summary["wsSourceAgeMs"]["samples"] == 2
    finally:
        probe.close()
