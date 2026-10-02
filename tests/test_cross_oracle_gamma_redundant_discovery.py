from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from predict_bot import cross_oracle_gamma_redundant_discovery as recovery


def _event(bucket: int, *, slug: str | None = None, market_slug: str = "underlying-btc-market") -> dict[str, Any]:
    event_slug = slug or f"btc-updown-5m-{bucket}"
    return {
        "id": "event-1",
        "slug": event_slug,
        "title": "Bitcoin Up or Down",
        "active": True,
        "closed": False,
        "endDate": "2026-08-09T14:05:00Z",
        "markets": [
            {
                "id": "market-1",
                "slug": market_slug,
                "conditionId": "0xcondition",
                "active": True,
                "closed": False,
                "enableOrderBook": True,
                "acceptingOrders": True,
                "endDate": "2026-08-09T14:05:00Z",
                "outcomes": json.dumps(["Up", "Down"]),
                "clobTokenIds": json.dumps(["up-token", "down-token"]),
            }
        ],
    }


def test_event_window_scan_accepts_exact_event_with_different_underlying_market_slug(monkeypatch) -> None:
    bucket = 1786284000
    target_slug = f"btc-updown-5m-{bucket}"
    payload = [_event(bucket, market_slug="btc-up-or-down-august-9-1005am-et")]

    def fake_transport(url: str, *, timeout: float = 4.0) -> Any:
        assert "/events?" in url
        return payload

    monkeypatch.setattr(recovery, "_DIRECT_TRANSPORT", fake_transport)
    result = recovery._event_window_scan(target_slug, bucket)

    assert result is not None
    assert result["slug"] == target_slug
    assert result["eventSlug"] == target_slug
    assert result["gammaMarketSlug"] == "btc-up-or-down-august-9-1005am-et"
    parsed = recovery.cross.parse_gamma_market(result, bucket_start=bucket)
    assert parsed is not None
    assert parsed["upTokenId"] == "up-token"
    assert parsed["downTokenId"] == "down-token"


def test_event_window_scan_refuses_adjacent_event_even_when_tokens_are_valid(monkeypatch) -> None:
    bucket = 1786284000
    target_slug = f"btc-updown-5m-{bucket}"
    payload = [_event(bucket + 300, slug=f"btc-updown-5m-{bucket + 300}")]

    monkeypatch.setattr(
        recovery,
        "_DIRECT_TRANSPORT",
        lambda _url, timeout=4.0: payload,
    )

    assert recovery._event_window_scan(target_slug, bucket) is None


def test_parallel_recovery_can_win_from_window_path(monkeypatch) -> None:
    bucket = 1786284000
    target_slug = f"btc-updown-5m-{bucket}"
    candidate = recovery.ready._strict_candidate_market(
        _event(bucket), event_slug=target_slug, bucket=bucket
    )
    assert candidate is not None

    monkeypatch.setattr(recovery, "_event_exact_fast", lambda _slug, _bucket: None)
    monkeypatch.setattr(recovery, "_event_slug_list_fast", lambda _slug, _bucket: None)
    monkeypatch.setattr(recovery, "_event_window_scan", lambda _slug, _bucket: candidate)

    result, method = recovery._parallel_event_discovery(target_slug, bucket)
    assert result is not None
    assert method == "EVENT_WINDOW_SCAN"


def test_supervisor_launches_redundant_gamma_discovery() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "predict_bot"
        / "supervisor.py"
    ).read_text(encoding="utf-8")
    assert "predict_bot.cross_oracle_trade_readiness" in source
    assert "predict_bot.cross_oracle_gamma_redundant_discovery" in source
