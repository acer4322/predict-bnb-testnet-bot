from __future__ import annotations

from pathlib import Path
from typing import Any

from predict_bot.binance_exact_market import find_exact_market_summary
from predict_bot.poly_gap_live_v18 import DeepExactBinanceDiscoveryPolyGapLiveEngine


def _topic(start_ms: int, *, topic_id: int, market_id: int, symbol: str = "BTCUSDT") -> dict[str, Any]:
    return {
        "marketTopicId": topic_id,
        "chartType": "CRYPTO_UP_DOWN",
        "symbol": symbol,
        "startDate": start_ms,
        "endDate": start_ms + 300_000,
        "markets": [
            {
                "marketId": market_id,
                "tradingStatus": "OPEN",
                "outcomes": [
                    {"name": "UP", "tokenId": f"up-{market_id}"},
                    {"name": "DOWN", "tokenId": f"down-{market_id}"},
                ],
            }
        ],
    }


class FakeClient:
    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        self.pages = pages
        self.calls: list[int] = []

    def list_markets(self, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        self.calls.append(offset)
        page = offset // 100
        rows = self.pages[page] if page < len(self.pages) else []
        return {
            "marketTopics": rows,
            "limit": 100,
            "hasMore": page + 1 < len(self.pages),
        }


def test_exact_market_can_be_found_beyond_first_page() -> None:
    target = 1_800_000_000_000
    wrong = [_topic(target + 300_000, topic_id=1000 + i, market_id=2000 + i, symbol="ETHUSDT") for i in range(100)]
    exact = _topic(target, topic_id=9999, market_id=8888)
    client = FakeClient([wrong, [exact]])

    result = find_exact_market_summary(
        client,
        symbol="BTCUSDT",
        target_start_ms=target,
        max_pages=5,
    )

    assert result is not None
    assert int(result["marketTopicId"]) == 9999
    assert client.calls == [0, 100]


def test_one_page_lookup_would_reproduce_false_waiting_market() -> None:
    target = 1_800_000_000_000
    wrong = [_topic(target + 300_000, topic_id=1000 + i, market_id=2000 + i, symbol="ETHUSDT") for i in range(100)]
    exact = _topic(target, topic_id=9999, market_id=8888)
    client = FakeClient([wrong, [exact]])

    result = find_exact_market_summary(
        client,
        symbol="BTCUSDT",
        target_start_ms=target,
        max_pages=1,
    )

    assert result is None
    assert client.calls == [0]


def test_v18_keeps_strict_market_binding_and_exposes_deep_discovery(tmp_path: Path) -> None:
    engine = DeepExactBinanceDiscoveryPolyGapLiveEngine(tmp_path / "v18.db")
    try:
        state = engine.snapshot()
        assert state["version"] == "POLY_GAP_DEDICATED_LIVE_V18"
        discovery = state["binanceExactDiscovery"]
        assert discovery["maxPages"] == 5
        assert discovery["strictExactWindowOnly"] is True
        assert discovery["nearestMarketFallback"] is False
        assert state["entryMarketBinding"]["requiresExactPolyEventSlug"] is True
        assert state["settlementRecovery"]["unresolvedExecutionStillBlocksNewMarket"] is True
    finally:
        engine.stop()


def test_supervisor_keeps_v18_lineage_but_launches_v19_and_server_v4() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "predict_bot"
        / "supervisor.py"
    ).read_text(encoding="utf-8")
    assert "predict_bot.poly_gap_live_v18" in source
    assert "predict_bot.poly_gap_live_v19" in source
    assert "predict_bot.server_binance_prefetch_v4" in source
