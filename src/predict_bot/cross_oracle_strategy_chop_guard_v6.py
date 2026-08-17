from __future__ import annotations

import math
import os
import statistics
import threading
import time
from collections import defaultdict
from typing import Any, Callable

from . import cross_oracle_strategies as strategies
from . import cross_oracle_strategy_chop_guard_v5 as v5


def _parse_milestones(raw: str) -> tuple[float, ...]:
    values: list[float] = []
    for token in str(raw).split(","):
        try:
            value = float(token.strip())
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and 0.51 <= value <= 0.99:
            values.append(round(value, 6))
    return tuple(sorted(set(values))) or (0.60, 0.70, 0.80, 0.90)


LEAD_MILESTONES = _parse_milestones(
    os.environ.get("PREDICT_POLY_LEAD_VALIDATION_MILESTONES", "0.60,0.70,0.80,0.90")
)
LEAD_HEADLINE_MIN_MILESTONE = min(
    0.95,
    max(
        0.51,
        float(os.environ.get("PREDICT_POLY_LEAD_VALIDATION_HEADLINE_MIN", "0.70")),
    ),
)
LEAD_REARM_HYSTERESIS = max(
    0.005,
    min(
        0.15,
        float(os.environ.get("PREDICT_POLY_LEAD_VALIDATION_REARM_HYSTERESIS", "0.03")),
    ),
)
LEAD_MAX_PAIR_LAG_MS = max(
    1_000,
    int(os.environ.get("PREDICT_POLY_LEAD_VALIDATION_MAX_PAIR_LAG_MS", "15000")),
)
LEAD_TIE_MS = max(
    int(round(strategies.POLL_INTERVAL_SECONDS * 1000.0)),
    int(os.environ.get("PREDICT_POLY_LEAD_VALIDATION_TIE_MS", "300")),
)
LEAD_ANALYTICS_CACHE_MS = max(
    500,
    int(os.environ.get("PREDICT_POLY_LEAD_VALIDATION_CACHE_MS", "2000")),
)
LEAD_WINDOWS = (10, 30, 50)


# CrossOraclePaperEngine obtains Binance /api/realtime through the module-level
# helper. Wrap it once so v6 can persist the exact source observation timestamp
# and source freshness without issuing another HTTP request on the hot Paper loop.
_capture_local = threading.local()
if not getattr(strategies._http_json, "_poly_lead_capture_wrapper", False):
    _original_http_json = strategies._http_json

    def _capturing_http_json(url: str, timeout: float = 1.5) -> Any:
        payload = _original_http_json(url, timeout)
        if url == strategies.BINANCE_REALTIME_URL and isinstance(payload, dict):
            latest = payload.get("latest")
            _capture_local.binance_latest = dict(latest) if isinstance(latest, dict) else None
        return payload

    setattr(_capturing_http_json, "_poly_lead_capture_wrapper", True)
    strategies._http_json = _capturing_http_json


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _selected(up_mid: float, side: str) -> float:
    return up_mid if side == "UP" else 1.0 - up_mid


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * min(1.0, max(0.0, q))
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _seconds_stats(values: list[float]) -> dict[str, Any]:
    cleaned = [float(value) for value in values if math.isfinite(float(value))]
    return {
        "count": len(cleaned),
        "mean": statistics.fmean(cleaned) if cleaned else None,
        "median": statistics.median(cleaned) if cleaned else None,
        "p90": _percentile(cleaned, 0.90),
        "min": min(cleaned) if cleaned else None,
        "max": max(cleaned) if cleaned else None,
    }


def _wilson(successes: int, total: int) -> dict[str, float | None]:
    if total <= 0:
        return {"probability": None, "lower95": None, "upper95": None}
    p = successes / total
    z = 1.959963984540054
    z2 = z * z
    denominator = 1.0 + z2 / total
    center = (p + z2 / (2.0 * total)) / denominator
    radius = (
        z
        * math.sqrt((p * (1.0 - p) + z2 / (4.0 * total)) / total)
        / denominator
    )
    return {
        "probability": p,
        "lower95": max(0.0, center - radius),
        "upper95": min(1.0, center + radius),
    }


def _dedupe_series(
    rows: list[dict[str, Any]], *, time_key: str, value_key: str
) -> list[tuple[int, float]]:
    points: dict[int, float] = {}
    for row in rows:
        timestamp = _int(row.get(time_key))
        value = _number(row.get(value_key))
        if timestamp is None or timestamp <= 0 or value is None or not 0.0 <= value <= 1.0:
            continue
        points[timestamp] = value
    return sorted(points.items())


def _crossings(
    series: list[tuple[int, float]], *, side: str, threshold: float
) -> list[int]:
    if not series:
        return []
    result: list[int] = []
    first_selected = _selected(series[0][1], side)
    armed = first_selected < threshold
    reset_level = max(0.0, threshold - LEAD_REARM_HYSTERESIS)
    for timestamp, up_mid in series:
        value = _selected(up_mid, side)
        if not armed and value <= reset_level + 1e-12:
            armed = True
        if armed and value + 1e-12 >= threshold:
            result.append(int(timestamp))
            armed = False
    return result


def _pair_crossings(
    poly_times: list[int],
    binance_times: list[int],
    *,
    market_id: int,
    poly_slug: str,
    side: str,
    threshold: float,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    i = 0
    j = 0
    while i < len(poly_times) and j < len(binance_times):
        poly_at = int(poly_times[i])
        binance_at = int(binance_times[j])
        delta_ms = binance_at - poly_at
        if abs(delta_ms) <= LEAD_MAX_PAIR_LAG_MS:
            if abs(delta_ms) <= LEAD_TIE_MS:
                leader = "TIE"
            elif delta_ms > 0:
                leader = "POLY"
            else:
                leader = "BINANCE"
            events.append(
                {
                    "marketId": market_id,
                    "polyMarketSlug": poly_slug,
                    "side": side,
                    "milestone": threshold,
                    "matched": True,
                    "leader": leader,
                    "polyAtMs": poly_at,
                    "binanceAtMs": binance_at,
                    "signedPolyLeadSeconds": delta_ms / 1000.0,
                    "absoluteLagSeconds": abs(delta_ms) / 1000.0,
                }
            )
            i += 1
            j += 1
            continue
        if poly_at < binance_at:
            events.append(
                {
                    "marketId": market_id,
                    "polyMarketSlug": poly_slug,
                    "side": side,
                    "milestone": threshold,
                    "matched": False,
                    "leader": "POLY_ONLY",
                    "polyAtMs": poly_at,
                    "binanceAtMs": None,
                    "signedPolyLeadSeconds": None,
                    "absoluteLagSeconds": None,
                }
            )
            i += 1
        else:
            events.append(
                {
                    "marketId": market_id,
                    "polyMarketSlug": poly_slug,
                    "side": side,
                    "milestone": threshold,
                    "matched": False,
                    "leader": "BINANCE_ONLY",
                    "polyAtMs": None,
                    "binanceAtMs": binance_at,
                    "signedPolyLeadSeconds": None,
                    "absoluteLagSeconds": None,
                }
            )
            j += 1
    while i < len(poly_times):
        events.append(
            {
                "marketId": market_id,
                "polyMarketSlug": poly_slug,
                "side": side,
                "milestone": threshold,
                "matched": False,
                "leader": "POLY_ONLY",
                "polyAtMs": int(poly_times[i]),
                "binanceAtMs": None,
                "signedPolyLeadSeconds": None,
                "absoluteLagSeconds": None,
            }
        )
        i += 1
    while j < len(binance_times):
        events.append(
            {
                "marketId": market_id,
                "polyMarketSlug": poly_slug,
                "side": side,
                "milestone": threshold,
                "matched": False,
                "leader": "BINANCE_ONLY",
                "polyAtMs": None,
                "binanceAtMs": int(binance_times[j]),
                "signedPolyLeadSeconds": None,
                "absoluteLagSeconds": None,
            }
        )
        j += 1
    return events


class LeadLagValidationPaperEngine(v5.InvertedPricePaperEngine):
    """Paper research sidecar validating whether Poly still leads Binance.

    The validation intentionally does not infer lead from correlation. It records
    aligned Poly/Binance probability trajectories and asks which venue first
    crosses the same directional probability milestone. After a crossing, that
    side/milestone is re-armed only after price falls by the configured hysteresis,
    so a later reversal/re-acceleration becomes a new event instead of repeatedly
    counting one sustained move.

    A matched event exists only when the other venue reaches the same milestone
    within the maximum pair-lag window. A one-sided crossing is reported
    separately and never counted as a successful lead. Headline market/regime
    statistics use milestones >= 0.70 by default; 0.60 remains an early diagnostic.
    """

    def __init__(self, db_path: Any, poly_snapshot_provider: Callable[[], dict[str, Any]]) -> None:
        self._latest_poly_snapshot: dict[str, Any] | None = None
        self._lead_cache: dict[str, Any] | None = None
        self._lead_cache_at_ms = 0

        def tracked_poly_snapshot() -> dict[str, Any]:
            snapshot = poly_snapshot_provider()
            self._latest_poly_snapshot = dict(snapshot) if isinstance(snapshot, dict) else None
            return snapshot

        super().__init__(db_path, tracked_poly_snapshot)
        self._ensure_lead_meta()

    def _create_schema(self) -> None:
        super()._create_schema()
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS poly_binance_lead_validation_meta (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    collection_started_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS poly_binance_lead_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    binance_market_id INTEGER NOT NULL,
                    poly_market_slug TEXT NOT NULL,
                    observed_at_ms INTEGER NOT NULL,
                    poly_received_at_ms INTEGER,
                    binance_observed_at_ms INTEGER,
                    poly_up_mid REAL NOT NULL,
                    binance_up_mid REAL NOT NULL,
                    seconds_left_skew REAL,
                    binance_book_age_ms REAL,
                    binance_book_skew_ms REAL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_poly_binance_lead_sample_unique
                    ON poly_binance_lead_samples(binance_market_id, observed_at_ms);
                CREATE INDEX IF NOT EXISTS idx_poly_binance_lead_market_time
                    ON poly_binance_lead_samples(binance_market_id, observed_at_ms);
                """
            )
            self.db.commit()

    def _ensure_lead_meta(self) -> None:
        with self.db_lock:
            self.db.execute(
                """INSERT OR IGNORE INTO poly_binance_lead_validation_meta(
                       singleton, collection_started_at_ms
                   ) VALUES (1, ?)""",
                (int(time.time() * 1000),),
            )
            self.db.commit()

    def _collection_started_at_ms(self) -> int | None:
        with self.db_lock:
            row = self.db.execute(
                "SELECT collection_started_at_ms FROM poly_binance_lead_validation_meta WHERE singleton=1"
            ).fetchone()
        return int(row[0]) if row is not None else None

    def _evaluate_once(self) -> None:
        _capture_local.binance_latest = None
        super()._evaluate_once()
        with self.lock:
            runtime = dict(self.runtime)
        if runtime.get("aligned") is not True:
            return
        poly_mid = _number(runtime.get("polyUpMid"))
        binance_mid = _number(runtime.get("binanceUpMid"))
        market_id = _int(runtime.get("binanceMarketId"))
        poly_slug = str(runtime.get("polyMarketSlug") or "")
        observed_at_ms = _int(runtime.get("updatedAtMs")) or int(time.time() * 1000)
        if (
            poly_mid is None
            or binance_mid is None
            or market_id is None
            or market_id <= 0
            or not poly_slug
        ):
            return

        latest = getattr(_capture_local, "binance_latest", None)
        latest = latest if isinstance(latest, dict) else {}
        poly = self._latest_poly_snapshot if isinstance(self._latest_poly_snapshot, dict) else {}
        poly_received_at_ms = _int(poly.get("receivedTimestampMs")) or observed_at_ms
        binance_observed_at_ms = _int(latest.get("observed_timestamp_ms")) or observed_at_ms
        seconds_left_skew = _number(runtime.get("secondsLeftSkew"))
        book_age_ms = _number(latest.get("book_age_ms"))
        book_skew_ms = _number(latest.get("book_skew_ms"))

        with self.db_lock:
            self.db.execute(
                """INSERT OR IGNORE INTO poly_binance_lead_samples(
                       binance_market_id, poly_market_slug, observed_at_ms,
                       poly_received_at_ms, binance_observed_at_ms,
                       poly_up_mid, binance_up_mid, seconds_left_skew,
                       binance_book_age_ms, binance_book_skew_ms
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    int(market_id),
                    poly_slug,
                    int(observed_at_ms),
                    int(poly_received_at_ms),
                    int(binance_observed_at_ms),
                    float(poly_mid),
                    float(binance_mid),
                    seconds_left_skew,
                    book_age_ms,
                    book_skew_ms,
                ),
            )
            self.db.commit()
        self._lead_cache = None

    def _market_rows(self, current_market_id: int | None) -> list[tuple[int, int]]:
        with self.db_lock:
            rows = self.db.execute(
                """SELECT binance_market_id, MAX(observed_at_ms) AS last_seen_at_ms
                     FROM poly_binance_lead_samples
                    WHERE (? IS NULL OR binance_market_id <> ?)
                    GROUP BY binance_market_id
                    ORDER BY last_seen_at_ms DESC, binance_market_id DESC
                    LIMIT 50""",
                (current_market_id, current_market_id),
            ).fetchall()
        return [(int(row[0]), int(row[1])) for row in rows]

    def _samples_for_markets(self, market_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
        if not market_ids:
            return {}
        placeholders = ",".join("?" for _ in market_ids)
        with self.db_lock:
            rows = self.db.execute(
                f"""SELECT binance_market_id, poly_market_slug, observed_at_ms,
                            poly_received_at_ms, binance_observed_at_ms,
                            poly_up_mid, binance_up_mid, seconds_left_skew,
                            binance_book_age_ms, binance_book_skew_ms
                       FROM poly_binance_lead_samples
                      WHERE binance_market_id IN ({placeholders})
                      ORDER BY binance_market_id, observed_at_ms""",
                market_ids,
            ).fetchall()
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[int(row["binance_market_id"])].append(dict(row))
        return dict(grouped)

    def _market_analysis(self, market_id: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
        poly_series = _dedupe_series(
            rows, time_key="poly_received_at_ms", value_key="poly_up_mid"
        )
        binance_series = _dedupe_series(
            rows, time_key="binance_observed_at_ms", value_key="binance_up_mid"
        )
        slug = str(rows[-1].get("poly_market_slug") or "") if rows else ""
        events: list[dict[str, Any]] = []
        for side in ("UP", "DOWN"):
            for threshold in LEAD_MILESTONES:
                poly_crossings = _crossings(poly_series, side=side, threshold=threshold)
                binance_crossings = _crossings(binance_series, side=side, threshold=threshold)
                events.extend(
                    _pair_crossings(
                        poly_crossings,
                        binance_crossings,
                        market_id=market_id,
                        poly_slug=slug,
                        side=side,
                        threshold=threshold,
                    )
                )

        headline = [
            event
            for event in events
            if float(event["milestone"]) + 1e-12 >= LEAD_HEADLINE_MIN_MILESTONE
        ]
        matched = [event for event in headline if event.get("matched") is True]
        signed = [
            float(event["signedPolyLeadSeconds"])
            for event in matched
            if event.get("signedPolyLeadSeconds") is not None
        ]
        median_signed = statistics.median(signed) if signed else None
        if median_signed is None:
            leader = "INSUFFICIENT"
        elif median_signed > LEAD_TIE_MS / 1000.0:
            leader = "POLY"
        elif median_signed < -LEAD_TIE_MS / 1000.0:
            leader = "BINANCE"
        else:
            leader = "TIE_MIXED"

        poly_first = sum(event.get("leader") == "POLY" for event in matched)
        binance_first = sum(event.get("leader") == "BINANCE" for event in matched)
        ties = sum(event.get("leader") == "TIE" for event in matched)
        poly_only = sum(event.get("leader") == "POLY_ONLY" for event in headline)
        binance_only = sum(event.get("leader") == "BINANCE_ONLY" for event in headline)
        strongest = max(
            (float(event["milestone"]) for event in matched),
            default=None,
        )
        sample_start = min((int(row["observed_at_ms"]) for row in rows), default=None)
        sample_end = max((int(row["observed_at_ms"]) for row in rows), default=None)
        avg_book_age = statistics.fmean(
            values
            for row in rows
            if (values := _number(row.get("binance_book_age_ms"))) is not None
        ) if any(_number(row.get("binance_book_age_ms")) is not None for row in rows) else None

        return {
            "marketId": market_id,
            "polyMarketSlug": slug,
            "samples": len(rows),
            "sampleStartMs": sample_start,
            "sampleEndMs": sample_end,
            "marketLeader": leader,
            "matchedHeadlineEvents": len(matched),
            "polyFirstEvents": poly_first,
            "binanceFirstEvents": binance_first,
            "tieEvents": ties,
            "polyOnlyEvents": poly_only,
            "binanceOnlyEvents": binance_only,
            "medianSignedPolyLeadSeconds": median_signed,
            "meanSignedPolyLeadSeconds": statistics.fmean(signed) if signed else None,
            "strongestMatchedMilestone": strongest,
            "averageBinanceBookAgeMs": avg_book_age,
            "events": sorted(
                headline,
                key=lambda event: max(
                    int(event.get("polyAtMs") or 0), int(event.get("binanceAtMs") or 0)
                ),
            )[-24:],
        }

    def _window_stats(self, markets: list[dict[str, Any]], window: int) -> dict[str, Any]:
        selected = markets[:window]
        evaluable = [market for market in selected if market["marketLeader"] != "INSUFFICIENT"]
        poly_markets = sum(market["marketLeader"] == "POLY" for market in evaluable)
        binance_markets = sum(market["marketLeader"] == "BINANCE" for market in evaluable)
        mixed_markets = sum(market["marketLeader"] == "TIE_MIXED" for market in evaluable)
        matched_events = sum(int(market["matchedHeadlineEvents"]) for market in selected)
        poly_events = sum(int(market["polyFirstEvents"]) for market in selected)
        binance_events = sum(int(market["binanceFirstEvents"]) for market in selected)
        tie_events = sum(int(market["tieEvents"]) for market in selected)
        poly_only = sum(int(market["polyOnlyEvents"]) for market in selected)
        binance_only = sum(int(market["binanceOnlyEvents"]) for market in selected)
        poly_lead_seconds: list[float] = []
        binance_lead_seconds: list[float] = []
        signed_seconds: list[float] = []
        for market in selected:
            for event in market.get("events") or []:
                if event.get("matched") is not True:
                    continue
                signed = _number(event.get("signedPolyLeadSeconds"))
                if signed is None:
                    continue
                signed_seconds.append(signed)
                if event.get("leader") == "POLY":
                    poly_lead_seconds.append(abs(signed))
                elif event.get("leader") == "BINANCE":
                    binance_lead_seconds.append(abs(signed))

        market_total = len(evaluable)
        event_decided = poly_events + binance_events + tie_events
        return {
            "requestedMarkets": window,
            "sampledMarkets": len(selected),
            "evaluableMarkets": market_total,
            "insufficientMarkets": len(selected) - market_total,
            "polyLeadMarkets": poly_markets,
            "binanceLeadMarkets": binance_markets,
            "tieMixedMarkets": mixed_markets,
            "polyLeadMarketProbability": _wilson(poly_markets, market_total),
            "binanceLeadMarketProbability": _wilson(binance_markets, market_total),
            "tieMixedMarketProbability": _wilson(mixed_markets, market_total),
            "matchedHeadlineEvents": matched_events,
            "polyFirstEvents": poly_events,
            "binanceFirstEvents": binance_events,
            "tieEvents": tie_events,
            "polyOnlyUnmatchedEvents": poly_only,
            "binanceOnlyUnmatchedEvents": binance_only,
            "polyFirstEventProbability": _wilson(poly_events, event_decided),
            "binanceFirstEventProbability": _wilson(binance_events, event_decided),
            "tieEventProbability": _wilson(tie_events, event_decided),
            "polyLeadSeconds": _seconds_stats(poly_lead_seconds),
            "binanceLeadSeconds": _seconds_stats(binance_lead_seconds),
            "signedPolyLeadSeconds": _seconds_stats(signed_seconds),
            "marketIds": [int(market["marketId"]) for market in selected],
        }

    def _lead_validation_snapshot(self) -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        if (
            self._lead_cache is not None
            and now_ms - self._lead_cache_at_ms < LEAD_ANALYTICS_CACHE_MS
        ):
            return dict(self._lead_cache)

        with self.lock:
            current_market_id = _int(self.runtime.get("binanceMarketId"))
        market_order = self._market_rows(current_market_id)
        market_ids = [market_id for market_id, _last_seen in market_order]
        grouped = self._samples_for_markets(market_ids)
        analyses = [
            self._market_analysis(market_id, grouped.get(market_id, []))
            for market_id in market_ids
        ]
        windows = {
            str(window): self._window_stats(analyses, window)
            for window in LEAD_WINDOWS
        }
        recent10 = windows["10"]
        if int(recent10["evaluableMarkets"]) < 5:
            regime = "INSUFFICIENT_DATA"
        else:
            poly_probability = _number(
                (recent10["polyLeadMarketProbability"] or {}).get("probability")
            ) or 0.0
            binance_probability = _number(
                (recent10["binanceLeadMarketProbability"] or {}).get("probability")
            ) or 0.0
            if poly_probability >= 0.60 and poly_probability > binance_probability:
                regime = "POLY_LEADING"
            elif binance_probability >= 0.50 and binance_probability >= poly_probability:
                regime = "BINANCE_LEADING_RISK"
            else:
                regime = "MIXED"

        with self.db_lock:
            sample_row = self.db.execute(
                "SELECT COUNT(*) AS n, COUNT(DISTINCT binance_market_id) AS markets FROM poly_binance_lead_samples"
            ).fetchone()
        payload = {
            "version": "poly_binance_lead_validation_v1",
            "paperOnly": True,
            "liveOrdersAffected": False,
            "forwardOnly": True,
            "collectionStartedAtMs": self._collection_started_at_ms(),
            "sampleRows": int(sample_row["n"] if sample_row else 0),
            "sampledMarketsTotal": int(sample_row["markets"] if sample_row else 0),
            "currentMarketExcludedFromWindows": True,
            "currentRegime": regime,
            "definition": (
                "For each UP/DOWN directional milestone, record which venue first crosses the same "
                "selected-side probability. Pair only if the other venue reaches that same price "
                "within the max lag. Re-arm after both trajectories move back below the milestone "
                "by the hysteresis, allowing a later reversal/re-acceleration to become a new event."
            ),
            "milestones": list(LEAD_MILESTONES),
            "headlineMinimumMilestone": LEAD_HEADLINE_MIN_MILESTONE,
            "rearmHysteresis": LEAD_REARM_HYSTERESIS,
            "maxPairLagMs": LEAD_MAX_PAIR_LAG_MS,
            "tieToleranceMs": LEAD_TIE_MS,
            "samplingTargetMs": int(round(strategies.POLL_INTERVAL_SECONDS * 1000.0)),
            "probabilityIntervals": "Wilson 95%",
            "windows": windows,
            "recentMarkets": analyses,
        }
        self._lead_cache = payload
        self._lead_cache_at_ms = now_ms
        return dict(payload)

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["polyBinanceLeadValidation"] = self._lead_validation_snapshot()
        parameters = payload.setdefault("parameters", {})
        parameters.update(
            leadValidationMilestones=list(LEAD_MILESTONES),
            leadValidationHeadlineMinMilestone=LEAD_HEADLINE_MIN_MILESTONE,
            leadValidationRearmHysteresis=LEAD_REARM_HYSTERESIS,
            leadValidationMaxPairLagMs=LEAD_MAX_PAIR_LAG_MS,
            leadValidationTieMs=LEAD_TIE_MS,
        )
        return payload


# Preserve all v5 strategies and the v4 heartbeat. Replace only the concrete
# Paper engine installed into the 8768 launch chain.
v5.rolling.base.guard.stable.launch.strategy_module.GapAwareCrossOraclePaperEngine = (
    LeadLagValidationPaperEngine
)


def main() -> int:
    return v5.main()


if __name__ == "__main__":
    raise SystemExit(main())
