from __future__ import annotations

from pathlib import Path

from predict_bot import cross_oracle_prefetch_recovery as recovery


def _market_payload(slug: str) -> dict[str, object]:
    return {
        "id": "market-1",
        "slug": slug,
        "question": "Bitcoin Up or Down - 5 Minutes",
        "conditionId": "condition-1",
        "active": True,
        "closed": False,
        "outcomes": '["Up","Down"]',
        "clobTokenIds": '["token-up","token-down"]',
    }


def test_prefetch_cache_accepts_only_exact_valid_slug() -> None:
    bucket = 1_786_190_100
    slug = f"btc-updown-5m-{bucket}"
    assert recovery._cache_market(slug, _market_payload(slug), bucket, "TEST") is True
    cached = recovery._cached_payload(slug)
    assert cached is not None
    assert cached["slug"] == slug

    wrong = _market_payload("btc-updown-5m-999")
    assert recovery._cache_market(slug, wrong, bucket, "TEST") is False


def test_supervisor_uses_prefetch_recovery_collector() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "predict_bot"
        / "supervisor.py"
    ).read_text(encoding="utf-8")
    assert "predict_bot.cross_oracle_prefetch_recovery" in source
    assert "predict_bot.cross_oracle_rollover_guard" not in source


def test_recovery_source_discovers_before_hard_invalidation() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "predict_bot"
        / "cross_oracle_prefetch_recovery.py"
    ).read_text(encoding="utf-8")
    discover_index = source.index("if self._discover_market(slug, bucket):")
    invalidate_index = source.index("rollover._invalidate_stale_market_snapshot(self, slug)")
    assert discover_index < invalidate_index
    assert '"PREFETCH_CACHE"' in source
    assert '"ACTIVE_WINDOW_SCAN"' in source
