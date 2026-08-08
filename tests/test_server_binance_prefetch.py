from __future__ import annotations

from predict_bot import server as base_server
from predict_bot import server_binance_prefetch as prefetch_server


def summary(start_ms: int) -> dict[str, object]:
    return {
        "startDate": start_ms,
        "endDate": start_ms + 300_000,
    }


def test_server_prefetch_requires_exact_window() -> None:
    start = 1_800_000_000_000
    assert prefetch_server._summary_matches_start(summary(start), start) is True
    assert prefetch_server._summary_matches_start(summary(start + 300_000), start) is False
    assert prefetch_server._summary_matches_start(summary(start - 300_000), start) is False


def test_server_collector_uses_prefetch_rollover_method() -> None:
    assert base_server.Collector._market_rollover_loop is prefetch_server._market_rollover_loop_prefetched
