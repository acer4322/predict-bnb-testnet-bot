from __future__ import annotations

from pathlib import Path

from predict_bot import cross_oracle_event_identity_recovery as event_recovery


def _market_payload(*, slug: str) -> dict:
    return {
        "id": "market-1",
        "slug": slug,
        "conditionId": "condition-1",
        "clobTokenIds": '["up-token","down-token"]',
        "outcomes": '["Up","Down"]',
        "active": True,
        "closed": False,
        "enableOrderBook": True,
        "acceptingOrders": True,
        "endDate": "2026-08-08T17:20:00Z",
    }


def test_event_slug_can_recover_different_underlying_market_slug(monkeypatch) -> None:
    event_slug = "btc-updown-5m-1786209300"
    underlying_slug = "btc-updown-market-6833776"
    event = {
        "id": "event-1",
        "slug": event_slug,
        "active": True,
        "closed": False,
        "markets": [_market_payload(slug=underlying_slug)],
    }

    monkeypatch.setattr(
        event_recovery,
        "_TRANSPORT_HTTP_JSON",
        lambda _url, timeout=4.0: event,
    )

    recovered = event_recovery._event_market_payload(event_slug, 1786209300)
    assert recovered is not None
    assert recovered["slug"] == event_slug
    assert recovered["eventSlug"] == event_slug
    assert recovered["gammaMarketSlug"] == underlying_slug
    assert recovered["conditionId"] == "condition-1"

    parsed = event_recovery._parse_gamma_market_with_identity(
        recovered,
        bucket_start=1786209300,
    )
    assert parsed is not None
    assert parsed["slug"] == event_slug
    assert parsed["eventSlug"] == event_slug
    assert parsed["gammaMarketSlug"] == underlying_slug
    assert parsed["identitySource"] == "EVENT_SLUG_MARKET"
    assert parsed["upTokenId"] == "up-token"
    assert parsed["downTokenId"] == "down-token"


def test_event_recovery_rejects_wrong_event_slug(monkeypatch) -> None:
    target = "btc-updown-5m-1786209300"
    wrong_event = {
        "slug": "btc-updown-5m-1786209000",
        "markets": [_market_payload(slug="some-market")],
    }
    monkeypatch.setattr(
        event_recovery,
        "_TRANSPORT_HTTP_JSON",
        lambda _url, timeout=4.0: wrong_event,
    )
    assert event_recovery._event_market_payload(target, 1786209300) is None


def test_supervisor_runs_event_identity_collector() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "predict_bot"
        / "supervisor.py"
    ).read_text(encoding="utf-8")
    assert "predict_bot.cross_oracle_event_identity_recovery" in source
    assert '"predict_bot.cross_oracle_prefetch_recovery"' not in source
