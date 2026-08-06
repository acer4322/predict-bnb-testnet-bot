from __future__ import annotations

import bisect
from datetime import datetime
from typing import Any

from . import decision_strategy_frozen_rules_patch as _frozen
from . import decision_strategy_shadows as _decision


PATCH_VERSION = "DECISION_CONTEXT_BATCH_CACHE_V1"
SQLITE_PARAMETER_CHUNK = 400


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _timestamp(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _unavailable(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "available": False,
        "side": str(source.get("side") or "").upper(),
        "entryPrice": _finite(source.get("entry_price")),
        "feeRateBps": _finite(source.get("fee_rate_bps")) or 200.0,
        "elapsed": None,
        "startMoveBps": None,
        "pathEr": None,
        "observationTimestamp": None,
        "context": None,
    }


def _prime_context_cache(
    self: Any,
    sources: list[dict[str, Any]],
) -> None:
    cache = getattr(self, "_decision_frozen_context_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        self._decision_frozen_context_cache = cache

    pending: dict[int, dict[str, Any]] = {}
    by_market: dict[int, list[dict[str, Any]]] = {}
    for source in sources:
        trade_id = int(source.get("id") or 0)
        market_id = int(source.get("market_id") or 0)
        if trade_id <= 0 or trade_id in cache:
            continue
        if market_id <= 0 or _timestamp(source.get("opened_at")) is None:
            cache[trade_id] = _unavailable(source)
            continue
        pending[trade_id] = source
        by_market.setdefault(market_id, []).append(source)
    if not pending:
        return

    observations: dict[int, list[dict[str, Any]]] = {
        market_id: [] for market_id in by_market
    }
    market_ids = sorted(by_market)
    for offset in range(0, len(market_ids), SQLITE_PARAMETER_CHUNK):
        chunk = market_ids[offset : offset + SQLITE_PARAMETER_CHUNK]
        placeholders = ",".join("?" for _ in chunk)
        rows = self.store.db.execute(
            f"""SELECT id, market_id, timestamp, start_price,
                       spot_price, seconds_left
                  FROM observations
                 WHERE market_id IN ({placeholders})
                   AND start_price IS NOT NULL
                   AND spot_price IS NOT NULL
                 ORDER BY market_id ASC, timestamp ASC, id ASC""",
            chunk,
        ).fetchall()
        for row in rows:
            item = dict(row)
            market_id = int(item.get("market_id") or 0)
            if market_id in observations:
                observations[market_id].append(item)

    for market_id, market_sources in by_market.items():
        rows = observations.get(market_id) or []
        if not rows:
            for source in market_sources:
                cache[int(source["id"])] = _unavailable(source)
            continue

        times: list[float] = []
        starts: list[float | None] = []
        prices: list[float | None] = []
        seconds: list[float | None] = []
        timestamps: list[str] = []
        cumulative_travel: list[float] = []
        running = 0.0
        previous: float | None = None
        first_start: float | None = None
        for row in rows:
            observed_at = _timestamp(row.get("timestamp"))
            if observed_at is None:
                continue
            start = _finite(row.get("start_price"))
            price = _finite(row.get("spot_price"))
            seconds_left = _finite(row.get("seconds_left"))
            if first_start is None and start is not None:
                first_start = start
            if not times and price is not None and first_start is not None:
                running = abs(price - first_start)
            elif price is not None and previous is not None:
                running += abs(price - previous)
            if price is not None:
                previous = price
            times.append(observed_at)
            starts.append(start)
            prices.append(price)
            seconds.append(seconds_left)
            timestamps.append(str(row.get("timestamp") or ""))
            cumulative_travel.append(running)

        for source in market_sources:
            trade_id = int(source["id"])
            opened_at = _timestamp(source.get("opened_at"))
            if opened_at is None or not times:
                cache[trade_id] = _unavailable(source)
                continue
            index = bisect.bisect_right(times, opened_at) - 1
            if index < 0:
                cache[trade_id] = _unavailable(source)
                continue
            start = starts[index]
            price = prices[index]
            seconds_left = seconds[index]
            travel = cumulative_travel[index]
            if (
                start is None
                or start <= 0
                or price is None
                or seconds_left is None
                or travel <= 0
            ):
                cache[trade_id] = _unavailable(source)
                continue
            move = price - start
            values = {
                "available": True,
                "side": str(source.get("side") or "").upper(),
                "entryPrice": _finite(source.get("entry_price")),
                "feeRateBps": _finite(source.get("fee_rate_bps")) or 200.0,
                "elapsed": 300.0 - seconds_left,
                "startMoveBps": move / start * 10_000.0,
                "pathEr": abs(move) / travel,
                "observationTimestamp": timestamps[index],
            }
            values["context"] = _frozen._context_key(values)
            if values["context"] is None:
                values["available"] = False
            cache[trade_id] = values


def _source_context(
    self: Any,
    source: dict[str, Any],
) -> dict[str, Any]:
    _prime_context_cache(self, [source])
    cache = getattr(self, "_decision_frozen_context_cache", {})
    trade_id = int(source.get("id") or 0)
    result = cache.get(trade_id)
    return dict(result) if isinstance(result, dict) else _unavailable(source)


def _family_state(
    self: Any,
    snapshot: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    market_id = int(snapshot["market_id"])
    as_of = str(snapshot.get("timestamp") or "9999-12-31T23:59:59+00:00")
    prepared: dict[str, dict[str, Any]] = {}
    context_inputs: list[dict[str, Any]] = []

    for family, source_strategy in _decision.FAMILY_SOURCES.items():
        source = self._source_trade(market_id, source_strategy)
        history = self._closed_history(source_strategy, as_of)
        prepared[family] = {
            "sourceStrategy": source_strategy,
            "source": source,
            "closedHistory": history,
        }
        if source is not None:
            context_inputs.append(source)
        context_inputs.extend(history)

    _prime_context_cache(self, context_inputs)

    result: dict[str, dict[str, Any]] = {}
    for family, prepared_item in prepared.items():
        source = prepared_item["source"]
        history = prepared_item["closedHistory"]
        rank1_history = _frozen._exp_stats(
            history,
            limit=_frozen.RANK1_HISTORY,
            half_life=_frozen.RANK1_HALF_LIFE,
        )
        source_context = (
            _source_context(self, source)
            if isinstance(source, dict)
            else {"available": False, "context": None}
        )
        context_key = source_context.get("context")
        context_rows: list[dict[str, Any]] = []
        if isinstance(context_key, tuple):
            for row in history:
                if _source_context(self, row).get("context") == context_key:
                    context_rows.append(row)
        rank2_history = _frozen._exp_stats(
            context_rows,
            limit=_frozen.RANK2_HISTORY,
            half_life=_frozen.RANK2_HALF_LIFE,
        )
        result[family] = {
            "family": family,
            "sourceStrategy": prepared_item["sourceStrategy"],
            "source": source,
            "history": {
                "rank1": rank1_history,
                "rank2": rank2_history,
                "sourceContext": {
                    **source_context,
                    "context": (
                        list(context_key)
                        if isinstance(context_key, tuple)
                        else None
                    ),
                },
            },
        }
    return result


def install_decision_strategy_context_cache_patch() -> None:
    tracker = _decision.DecisionStrategyTracker
    tracker._prime_context_cache = _prime_context_cache
    tracker._source_context = _source_context
    tracker._family_state = _family_state
