from __future__ import annotations

import json
import time
from pathlib import Path

from predict_bot import cross_oracle as cross
from predict_bot import cross_oracle_trade_readiness as readiness


def _market_row(*, outcomes: list[str], bucket: int, suffix: str = "good") -> dict[str, object]:
    return {
        "id": f"market-{suffix}",
        "slug": f"underlying-{suffix}",
        "conditionId": f"condition-{suffix}",
        "clobTokenIds": json.dumps([f"up-{suffix}", f"down-{suffix}"]),
        "outcomes": json.dumps(outcomes),
        "active": True,
        "closed": False,
        "enableOrderBook": True,
        "acceptingOrders": True,
        "endDate": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(bucket + 300)),
    }


def test_event_recovery_refuses_generic_yes_no_market() -> None:
    bucket = 1_800_000_000
    slug = f"btc-updown-5m-{bucket}"
    event = {
        "slug": slug,
        "markets": [_market_row(outcomes=["Yes", "No"], bucket=bucket)],
    }
    assert readiness._strict_candidate_market(event, event_slug=slug, bucket=bucket) is None


def test_event_recovery_accepts_only_explicit_up_down_tokens() -> None:
    bucket = 1_800_000_000
    slug = f"btc-updown-5m-{bucket}"
    event = {
        "slug": slug,
        "markets": [_market_row(outcomes=["Up", "Down"], bucket=bucket)],
    }
    result = readiness._strict_candidate_market(event, event_slug=slug, bucket=bucket)
    assert result is not None
    assert result["slug"] == slug
    assert result["eventSlug"] == slug
    assert result["gammaMarketSlug"] == "underlying-good"
    assert result["_strictUpDownValidated"] is True


def test_event_list_slug_fallback_is_exact(monkeypatch) -> None:
    bucket = 1_800_000_000
    slug = f"btc-updown-5m-{bucket}"
    wrong_slug = f"btc-updown-5m-{bucket - 300}"
    calls: list[str] = []

    def fake_transport(url: str, *, timeout: float = 4.0):
        calls.append(url)
        return [
            {
                "slug": wrong_slug,
                "markets": [_market_row(outcomes=["Up", "Down"], bucket=bucket, suffix="wrong")],
            },
            {
                "slug": slug,
                "markets": [_market_row(outcomes=["Up", "Down"], bucket=bucket, suffix="right")],
            },
        ]

    monkeypatch.setattr(readiness, "_BASE_TRANSPORT", fake_transport)
    result = readiness._event_list(slug, bucket)
    assert result is not None
    assert result["gammaMarketSlug"] == "underlying-right"
    assert calls
    assert "slug=btc-updown-5m-" in calls[0]


def test_socket_open_alone_is_not_trade_ready_and_both_books_are_required(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cross, "DB_PATH", tmp_path / "cross-oracle.db")
    collector = readiness.Collector()
    try:
        slug, bucket = cross.current_btc_5m_slug()
        with collector.lock:
            collector.market = {
                "id": "m1",
                "slug": slug,
                "eventSlug": slug,
                "gammaMarketSlug": "underlying",
                "conditionId": "condition",
                "windowStartMs": bucket * 1000,
                "windowEndMs": (bucket + 300) * 1000,
                "upTokenId": "up-token",
                "downTokenId": "down-token",
            }
            collector.polymarket_generation = 3
            collector._poly_ws_session = 1
            readiness._reset_ready_locked(
                collector,
                "test subscription",
                clear_prices=True,
            )

        state = collector.snapshot()
        assert state["tradeReadiness"]["tradeReady"] is False
        assert state["polymarket"]["status"] != "LIVE"

        up_book = {
            "event_type": "book",
            "asset_id": "up-token",
            "bids": [{"price": "0.50", "size": "10"}],
            "asks": [{"price": "0.52", "size": "10"}],
        }
        collector._apply_poly_quote(
            up_book,
            "book",
            None,
            time.time_ns(),
            json.dumps(up_book),
        )
        state = collector.snapshot()
        assert state["tradeReadiness"]["upInitialBookSeen"] is True
        assert state["tradeReadiness"]["downInitialBookSeen"] is False
        assert state["tradeReadiness"]["tradeReady"] is False

        down_book = {
            "event_type": "book",
            "asset_id": "down-token",
            "bids": [{"price": "0.48", "size": "10"}],
            "asks": [{"price": "0.50", "size": "10"}],
        }
        collector._apply_poly_quote(
            down_book,
            "book",
            None,
            time.time_ns(),
            json.dumps(down_book),
        )
        state = collector.snapshot()
        assert state["tradeReadiness"]["upInitialBookSeen"] is True
        assert state["tradeReadiness"]["downInitialBookSeen"] is True
        assert state["tradeReadiness"]["tradeReady"] is True
        assert state["polymarket"]["status"] == "LIVE"
    finally:
        collector.stop()
