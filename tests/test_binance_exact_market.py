from __future__ import annotations

from predict_bot.binance_exact_market import (
    find_exact_market_summary,
    live_cache_from_summary,
    validate_exact_topic,
)


def topic(start_ms: int, *, market_id: int, names: tuple[str, str] = ("UP", "DOWN")):
    return {
        "chartType": "CRYPTO_UP_DOWN",
        "symbol": "BTCUSDT",
        "marketTopicId": market_id + 1000,
        "startDate": start_ms,
        "endDate": start_ms + 300_000,
        "feeRateBps": 200,
        "markets": [
            {
                "marketId": market_id,
                "tradingStatus": "OPEN",
                "outcomes": [
                    {"name": names[0], "tokenId": f"token-{market_id}-a"},
                    {"name": names[1], "tokenId": f"token-{market_id}-b"},
                ],
            }
        ],
    }


class FakeClient:
    def __init__(self, rows):
        self.rows = rows

    def list_markets(self, offset: int = 0, limit: int = 100):
        return {
            "marketTopics": list(self.rows),
            "hasMore": False,
            "limit": limit,
        }


def test_exact_resolver_never_uses_nearest_wrong_window() -> None:
    start = 1_800_000_000_000
    client = FakeClient(
        [
            topic(start - 300_000, market_id=1),
            topic(start + 300_000, market_id=3),
        ]
    )
    assert (
        find_exact_market_summary(
            client,
            symbol="BTCUSDT",
            target_start_ms=start,
        )
        is None
    )


def test_exact_resolver_accepts_explicit_up_down_current_window() -> None:
    start = 1_800_000_000_000
    client = FakeClient([topic(start, market_id=2)])
    summary = find_exact_market_summary(
        client,
        symbol="BTCUSDT",
        target_start_ms=start,
    )
    assert summary is not None
    cache = live_cache_from_summary(summary)
    assert cache is not None
    assert cache["market_id"] == 2
    assert cache["start_ms"] == start
    assert cache["end_ms"] == start + 300_000
    assert cache["up_token_id"] == "token-2-a"
    assert cache["down_token_id"] == "token-2-b"


def test_exact_resolver_refuses_yes_no_orientation_for_crypto_up_down() -> None:
    start = 1_800_000_000_000
    assert (
        validate_exact_topic(
            topic(start, market_id=4, names=("YES", "NO")),
            symbol="BTCUSDT",
            target_start_ms=start,
        )
        is None
    )
