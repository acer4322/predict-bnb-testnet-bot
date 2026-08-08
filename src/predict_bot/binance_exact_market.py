from __future__ import annotations

from typing import Any

from .core import ApiError


DEFAULT_WINDOW_MS = 300_000
DEFAULT_BOUNDARY_SKEW_MS = 1_500


def bucket_start_ms(now_ms: int) -> int:
    return (int(now_ms) // DEFAULT_WINDOW_MS) * DEFAULT_WINDOW_MS


def strict_up_down_market(topic: dict[str, Any]) -> dict[str, Any] | None:
    """Return an unambiguous UP/DOWN binary market from one topic.

    CRYPTO_UP_DOWN live/paper execution must never infer orientation from outcome
    order.  A future-prefetched market is allowed to be not-yet-OPEN, but its
    two outcome names and token IDs must already be explicit.
    """

    rows = topic.get("markets")
    if not isinstance(rows, list):
        return None

    candidates: list[tuple[int, dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for market in rows:
        if not isinstance(market, dict):
            continue
        outcomes = market.get("outcomes")
        if not isinstance(outcomes, list) or len(outcomes) != 2:
            continue
        by_name: dict[str, dict[str, Any]] = {}
        valid = True
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                valid = False
                break
            name = str(outcome.get("name") or "").strip().upper()
            token_id = str(outcome.get("tokenId") or "")
            if name not in {"UP", "DOWN"} or not token_id:
                valid = False
                break
            by_name[name] = outcome
        if not valid or set(by_name) != {"UP", "DOWN"}:
            continue
        try:
            market_id = int(market.get("marketId") or 0)
        except (TypeError, ValueError):
            market_id = 0
        if market_id <= 0:
            continue
        status = str(market.get("tradingStatus") or "").upper()
        score = 2 if status == "OPEN" else 1
        candidates.append((score, market, by_name["UP"], by_name["DOWN"]))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    _score, market, up, down = candidates[0]
    return {"market": market, "up": up, "down": down}


def validate_exact_topic(
    topic: dict[str, Any],
    *,
    symbol: str,
    target_start_ms: int,
    boundary_skew_ms: int = DEFAULT_BOUNDARY_SKEW_MS,
) -> dict[str, Any] | None:
    if str(topic.get("chartType") or "") != "CRYPTO_UP_DOWN":
        return None
    if str(topic.get("symbol") or "").upper() != str(symbol).upper():
        return None
    try:
        start_ms = int(topic.get("startDate") or 0)
        end_ms = int(topic.get("endDate") or 0)
        topic_id = int(topic.get("marketTopicId") or 0)
    except (TypeError, ValueError):
        return None
    expected_end_ms = int(target_start_ms) + DEFAULT_WINDOW_MS
    if topic_id <= 0:
        return None
    if abs(start_ms - int(target_start_ms)) > int(boundary_skew_ms):
        return None
    if abs(end_ms - expected_end_ms) > int(boundary_skew_ms):
        return None
    if not 299_000 <= end_ms - start_ms <= 301_000:
        return None

    selected = strict_up_down_market(topic)
    if selected is None:
        return None
    summary = dict(topic)
    summary["_selectedMarket"] = selected
    return summary


def find_exact_market_summary(
    client: Any,
    *,
    symbol: str,
    target_start_ms: int,
    max_pages: int = 1,
    boundary_skew_ms: int = DEFAULT_BOUNDARY_SKEW_MS,
) -> dict[str, Any] | None:
    """Find only the Binance Prediction topic for one exact five-minute window."""

    offset = 0
    pages = 0
    while offset < 500 and pages < max(1, int(max_pages)):
        response = client.list_markets(offset=offset, limit=100)
        pages += 1
        topics = response.get("marketTopics") if isinstance(response, dict) else None
        if not isinstance(topics, list):
            raise ApiError("Binance prediction market/list returned no marketTopics")
        for topic in topics:
            if not isinstance(topic, dict):
                continue
            validated = validate_exact_topic(
                topic,
                symbol=symbol,
                target_start_ms=int(target_start_ms),
                boundary_skew_ms=int(boundary_skew_ms),
            )
            if validated is not None:
                return validated
        if not response.get("hasMore"):
            break
        offset += int(response.get("limit") or 100)
    return None


def live_cache_from_summary(summary: dict[str, Any]) -> dict[str, Any] | None:
    selected = summary.get("_selectedMarket")
    if not isinstance(selected, dict):
        return None
    market = selected.get("market")
    up = selected.get("up")
    down = selected.get("down")
    if not isinstance(market, dict) or not isinstance(up, dict) or not isinstance(down, dict):
        return None
    try:
        cache = {
            "market_id": int(market.get("marketId") or 0),
            "topic_id": int(summary.get("marketTopicId") or 0),
            "up_token_id": str(up.get("tokenId") or ""),
            "down_token_id": str(down.get("tokenId") or ""),
            "fee_rate_bps": int(summary.get("feeRateBps") or market.get("feeRateBps") or 200),
            "start_ms": int(summary.get("startDate") or 0),
            "end_ms": int(summary.get("endDate") or 0),
        }
    except (TypeError, ValueError):
        return None
    if (
        cache["market_id"] <= 0
        or cache["topic_id"] <= 0
        or not cache["up_token_id"]
        or not cache["down_token_id"]
        or cache["up_token_id"] == cache["down_token_id"]
        or cache["start_ms"] <= 0
        or cache["end_ms"] <= cache["start_ms"]
    ):
        return None
    return cache
