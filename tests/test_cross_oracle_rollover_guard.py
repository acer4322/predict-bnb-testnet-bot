from __future__ import annotations

import threading
from pathlib import Path

from predict_bot.cross_oracle_rollover_guard import _invalidate_stale_market_snapshot


class _FakeWs:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeCollector:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.market = {
            "slug": "btc-updown-5m-1000",
            "upTokenId": "old-up",
            "downTokenId": "old-down",
        }
        self.polymarket_generation = 7
        self.polymarket_ws = _FakeWs()
        self.polymarket = {
            "status": "LIVE",
            "receivedTimestampMs": 123456,
            "sourceTimestampMs": 123000,
            "error": None,
            "up": {
                "tokenId": "old-up",
                "bestBid": 0.99,
                "bestAsk": 1.0,
                "lastTrade": 1.0,
            },
            "down": {
                "tokenId": "old-down",
                "bestBid": 0.0,
                "bestAsk": 0.01,
                "lastTrade": 0.0,
            },
            "startPrice": 100.0,
            "startPriceTimestampMs": 123000,
            "startPriceOffsetMs": 0,
        }


def test_rollover_immediately_invalidates_previous_market_snapshot() -> None:
    collector = _FakeCollector()
    old_ws = collector.polymarket_ws

    _invalidate_stale_market_snapshot(collector, "btc-updown-5m-1300")

    assert collector.polymarket_generation == 8
    assert old_ws.closed is True
    assert collector.polymarket_ws is None
    assert collector.market is None
    assert collector.polymarket["status"] == "WAITING_MARKET"
    assert collector.polymarket["receivedTimestampMs"] is None
    assert collector.polymarket["sourceTimestampMs"] is None
    assert collector.polymarket["up"] == {
        "tokenId": None,
        "bestBid": None,
        "bestAsk": None,
        "lastTrade": None,
    }
    assert collector.polymarket["down"] == {
        "tokenId": None,
        "bestBid": None,
        "bestAsk": None,
        "lastTrade": None,
    }
    assert collector.polymarket["marketDiscovery"]["status"] == "ROLLOVER_INVALIDATED"


def test_same_target_does_not_repeatedly_bump_generation() -> None:
    collector = _FakeCollector()

    _invalidate_stale_market_snapshot(collector, "btc-updown-5m-1300")
    generation = collector.polymarket_generation
    count = collector._rollover_invalidation_count

    _invalidate_stale_market_snapshot(collector, "btc-updown-5m-1300")

    assert collector.polymarket_generation == generation
    assert collector._rollover_invalidation_count == count


def test_supervisor_runs_rollover_guarded_collector() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "predict_bot"
        / "supervisor.py"
    ).read_text(encoding="utf-8")
    assert "predict_bot.cross_oracle_rollover_guard" in source
    assert "predict_bot.cross_oracle_transport_hardening\"]" not in source
