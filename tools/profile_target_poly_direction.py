from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import re
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

API_BASE = os.environ.get("PREDICT_FUN_API_BASE", "https://api.predict.fun").rstrip("/")
TARGET_DEFAULT = "0x9ddbdc8bdf41ec79cd4f5c4dec14050f4b591a18"
WEI = 10**18
TIERS = (1.0, 2.0, 5.0, 10.0, 50.0)
ET = ZoneInfo("America/New_York")
FIVE_MIN_TITLE = re.compile(
    r"^(Ethereum|BNB) Up or Down - (.+),\s*(\d{1,2})(?::(\d{2}))?(AM|PM)-"
    r"(\d{1,2})(?::(\d{2}))?(AM|PM) ET$"
)
DEFAULT_OFFSETS = (0.0, 0.5, 1.0, 2.0, 3.0, 5.0)


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def dec(value: Any) -> float | None:
    try:
        return float(value) / WEI
    except (TypeError, ValueError):
        return None


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def dt_ms(value: Any) -> int | None:
    parsed = parse_iso(value)
    return int(parsed.timestamp() * 1000) if parsed is not None else None


def signer(row: dict[str, Any]) -> str:
    return str(row.get("signer") or "").lower().strip()


def outcome(row: dict[str, Any]) -> str:
    raw = row.get("outcome")
    if isinstance(raw, dict):
        raw = raw.get("name")
    return str(raw or "").upper().strip()


def quote_type(row: dict[str, Any]) -> str:
    return str(row.get("quoteType") or row.get("quote_type") or "").upper().strip()


def asset_from_title(title: Any) -> str | None:
    text = str(title or "").strip()
    if text.startswith("Ethereum Up or Down - "):
        return "ETH"
    if text.startswith("BNB Up or Down - "):
        return "BNB"
    return None


def minute_of_day(hour: int, minute: int, ampm: str) -> int:
    hour %= 12
    if ampm.upper() == "PM":
        hour += 12
    return hour * 60 + minute


def is_exact_5m_title(title: Any) -> bool:
    match = FIVE_MIN_TITLE.match(str(title or "").strip())
    if not match:
        return False
    start = minute_of_day(int(match.group(3)), int(match.group(4) or 0), match.group(5))
    end = minute_of_day(int(match.group(6)), int(match.group(7) or 0), match.group(8))
    if end < start:
        end += 24 * 60
    return end - start == 5


def infer_market_bucket(title: Any, event_ms: int) -> int:
    """Infer UTC 5m start, preferring the Predict title to avoid end-boundary drift."""
    match = FIVE_MIN_TITLE.match(str(title or "").strip())
    event_dt = datetime.fromtimestamp(event_ms / 1000.0, tz=timezone.utc)
    if match:
        date_text = match.group(2).strip()
        hour = int(match.group(3)) % 12 + (12 if match.group(5).upper() == "PM" else 0)
        minute = int(match.group(4) or 0)
        local_year = event_dt.astimezone(ET).year
        candidates: list[datetime] = []
        for year in (local_year - 1, local_year, local_year + 1):
            parsed = None
            for fmt in ("%B %d %Y", "%b %d %Y", "%B %d, %Y", "%b %d, %Y"):
                try:
                    parsed = datetime.strptime(f"{date_text} {year}", fmt)
                    break
                except ValueError:
                    continue
            if parsed is None:
                continue
            local = datetime(parsed.year, parsed.month, parsed.day, hour, minute, tzinfo=ET)
            candidates.append(local.astimezone(timezone.utc))
        if candidates:
            chosen = min(candidates, key=lambda row: abs((row - event_dt).total_seconds()))
            return int(chosen.timestamp())
    return int(event_ms // 300_000) * 300


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    rows = sorted(values)
    if len(rows) == 1:
        return rows[0]
    x = max(0.0, min(1.0, q)) * (len(rows) - 1)
    lo = int(x)
    hi = min(len(rows) - 1, lo + 1)
    weight = x - lo
    return rows[lo] * (1.0 - weight) + rows[hi] * weight


def median(values: Iterable[float | None]) -> float | None:
    rows = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return statistics.median(rows) if rows else None


def nearest_tier(value: float) -> tuple[float, float]:
    tier = min(TIERS, key=lambda item: abs(value - item))
    return tier, abs(value - tier) / tier


def direction_from_up(up_mid: float | None, deadband: float) -> str | None:
    if up_mid is None:
        return None
    if up_mid >= 0.5 + deadband:
        return "UP"
    if up_mid <= 0.5 - deadband:
        return "DOWN"
    return None


def side_probability(sample: dict[str, Any], side: str, prefix: str) -> float | None:
    direct = finite(sample.get(f"{prefix}_{'up' if side == 'UP' else 'down'}_mid"))
    if direct is not None:
        return direct
    up = finite(sample.get(f"{prefix}_up_mid"))
    if up is not None:
        return up if side == "UP" else 1.0 - up
    return None


def api_get(path: str, params: dict[str, Any], api_key: str) -> dict[str, Any]:
    query = urlencode({key: value for key, value in params.items() if value is not None})
    request = Request(
        f"{API_BASE}{path}?{query}",
        headers={
            "x-api-key": api_key,
            "Accept": "application/json",
            "User-Agent": "BTC-5M-Lab-TargetPolyDirection/1.0",
        },
    )
    with urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Predict response")
    return payload


def fetch_target_matches(
    *, wallet: str, maker: bool, api_key: str, hours: float, pages: int, page_size: int
) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(0.1, hours))
    results: list[dict[str, Any]] = []
    after: str | None = None
    seen: set[str] = set()
    for _ in range(max(1, pages)):
        payload = api_get(
            "/v1/orders/matches",
            {
                "first": max(1, min(500, page_size)),
                "after": after,
                "signerAddress": wallet,
                "isSignerMaker": "true" if maker else "false",
            },
            api_key,
        )
        rows = payload.get("data") if isinstance(payload.get("data"), list) else []
        if not rows:
            break
        older_seen = False
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            executed = parse_iso(raw.get("executedAt"))
            if executed is not None and executed < cutoff:
                older_seen = True
                continue
            results.append(raw)
        cursor = str(payload.get("cursor") or "").strip()
        if older_seen or not cursor or cursor in seen:
            break
        seen.add(cursor)
        after = cursor
    return results


def extract_taker_buys(matches: list[dict[str, Any]], wallet: str) -> tuple[list[dict[str, Any]], int]:
    buys: list[dict[str, Any]] = []
    non_bid = 0
    for match in matches:
        market = match.get("market") if isinstance(match.get("market"), dict) else {}
        title = market.get("title") or market.get("question")
        asset = asset_from_title(title)
        if asset is None or not is_exact_5m_title(title):
            continue
        candidates: list[dict[str, Any]] = []
        if isinstance(match.get("taker"), dict):
            candidates.append(match["taker"])
        if isinstance(match.get("takers"), list):
            candidates.extend(row for row in match["takers"] if isinstance(row, dict))
        if isinstance(match.get("order"), dict) and signer(match["order"]) == wallet:
            candidates.append(match["order"])
        for taker in candidates:
            if signer(taker) != wallet:
                continue
            if quote_type(taker) != "BID":
                non_bid += 1
                continue
            side = outcome(taker)
            event_ms = dt_ms(match.get("executedAt"))
            if side not in {"UP", "DOWN"} or event_ms is None:
                continue
            price = dec(taker.get("price"))
            shares = dec(taker.get("amount"))
            buys.append(
                {
                    "kind": "TAKER_BUY",
                    "asset": asset,
                    "marketId": int(market.get("id") or 0),
                    "marketTitle": title,
                    "side": side,
                    "price": price,
                    "shares": shares,
                    "costUsdtApprox": shares * price if shares is not None and price is not None else None,
                    "eventAt": match.get("executedAt"),
                    "eventMs": event_ms,
                    "marketBucket": infer_market_bucket(title, event_ms),
                    "transactionHash": match.get("transactionHash"),
                    "orderHash": taker.get("hash") or taker.get("orderHash"),
                }
            )
    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in buys:
        key = (row["marketId"], row["side"], row["eventMs"], row.get("orderHash"), row.get("transactionHash"))
        deduped[key] = row
    return list(deduped.values()), non_bid


def load_maker_buys(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("parentOrdersByMakerHash") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise SystemExit("maker input does not contain parentOrdersByMakerHash")
    output: list[dict[str, Any]] = []
    for parent in rows:
        if not isinstance(parent, dict):
            continue
        title = parent.get("marketTitle")
        asset = asset_from_title(title)
        if asset is None or not is_exact_5m_title(title) or str(parent.get("quoteType") or "").upper() != "BID":
            continue
        side = str(parent.get("outcome") or "").upper()
        event_ms = dt_ms(parent.get("firstExecutedAt"))
        if side not in {"UP", "DOWN"} or event_ms is None:
            continue
        pp = finite(parent.get("totalPotentialProfitUsdtApprox"))
        tier = tier_error = None
        if pp is not None and pp > 0:
            tier, tier_error = nearest_tier(pp)
        output.append(
            {
                "kind": "MAKER_FIRST_FILL",
                "asset": asset,
                "marketId": int(parent.get("marketId") or 0),
                "marketTitle": title,
                "side": side,
                "price": finite(parent.get("price")),
                "shares": finite(parent.get("totalShares")),
                "costUsdtApprox": finite(parent.get("totalCostUsdtApprox")),
                "potentialProfitUsdtApprox": pp,
                "matchedPotentialTier": tier if tier_error is not None and tier_error <= 0.05 else None,
                "tierRelativeError": tier_error,
                "eventAt": parent.get("firstExecutedAt"),
                "eventMs": event_ms,
                "lastEventMs": dt_ms(parent.get("lastExecutedAt")),
                "marketBucket": infer_market_bucket(title, event_ms),
                "makerHash": parent.get("makerHash"),
                "fillLegs": int(parent.get("fillLegs") or 0),
            }
        )
    return output


class TrajectoryIndex:
    def __init__(self, db_path: Path) -> None:
        if not db_path.exists():
            raise SystemExit(f"observer DB not found: {db_path}")
        uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
        self.db = sqlite3.connect(uri, uri=True, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        if self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='multi_prediction_trajectory'"
        ).fetchone() is None:
            raise SystemExit(f"{db_path} does not contain multi_prediction_trajectory")
        self.by_market: dict[tuple[str, int], tuple[list[int], list[dict[str, Any]]]] = {}
        self.min_ms: int | None = None
        self.max_ms: int | None = None
        grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
        rows = self.db.execute(
            """SELECT asset,market_bucket,sampled_at_ms,seconds_left,
                      poly_up_mid,poly_down_mid,binance_up_mid,binance_down_mid,
                      binance_up_ask,binance_down_ask,mid_gap,
                      executable_edge_up,executable_edge_down,
                      poly_source_age_ms,poly_receipt_age_ms
               FROM multi_prediction_trajectory
               WHERE asset IN ('ETH','BNB')
               ORDER BY asset,market_bucket,sampled_at_ms"""
        ).fetchall()
        for raw in rows:
            row = dict(raw)
            grouped[(str(row["asset"]).upper(), int(row["market_bucket"]))].append(row)
            ms = int(row["sampled_at_ms"])
            self.min_ms = ms if self.min_ms is None else min(self.min_ms, ms)
            self.max_ms = ms if self.max_ms is None else max(self.max_ms, ms)
        for key, values in grouped.items():
            self.by_market[key] = ([int(row["sampled_at_ms"]) for row in values], values)

    def close(self) -> None:
        self.db.close()

    def at_or_before(self, asset: str, bucket: int, target_ms: int, max_age_ms: int) -> dict[str, Any] | None:
        block = self.by_market.get((asset, int(bucket)))
        if block is None:
            return None
        times, rows = block
        pos = bisect.bisect_right(times, int(target_ms)) - 1
        if pos < 0:
            return None
        row = rows[pos]
        age = int(target_ms) - int(row["sampled_at_ms"])
        return row if 0 <= age <= max_age_ms else None

    def poly_flip_into_side(
        self, asset: str, bucket: int, event_ms: int, side: str, *, deadband: float, lookback_ms: int
    ) -> int | None:
        block = self.by_market.get((asset, int(bucket)))
        if block is None:
            return None
        times, rows = block
        start = bisect.bisect_left(times, event_ms - lookback_ms)
        end = bisect.bisect_right(times, event_ms)
        previous: str | None = None
        last_flip: int | None = None
        for row in rows[start:end]:
            current = direction_from_up(finite(row.get("poly_up_mid")), deadband)
            if current == side and previous != side:
                last_flip = int(row["sampled_at_ms"])
            if current is not None:
                previous = current
        return last_flip


def align_event(
    event: dict[str, Any], index: TrajectoryIndex, *, offsets: tuple[float, ...], max_sample_age_ms: int, deadband: float
) -> dict[str, Any]:
    snapshots: dict[str, Any] = {}
    for offset in offsets:
        requested_ms = int(event["eventMs"] - offset * 1000)
        sample = index.at_or_before(event["asset"], event["marketBucket"], requested_ms, max_sample_age_ms)
        key = f"{offset:g}s"
        if sample is None:
            snapshots[key] = None
            continue
        poly_up = finite(sample.get("poly_up_mid"))
        binance_up = finite(sample.get("binance_up_mid"))
        poly_direction = direction_from_up(poly_up, deadband)
        binance_direction = direction_from_up(binance_up, deadband)
        side = str(event["side"])
        poly_prob = side_probability(sample, side, "poly")
        binance_prob = side_probability(sample, side, "binance")
        price = finite(event.get("price"))
        snapshots[key] = {
            "sampledAtMs": int(sample["sampled_at_ms"]),
            "sampleAgeAtRequestedOffsetMs": requested_ms - int(sample["sampled_at_ms"]),
            "secondsLeft": finite(sample.get("seconds_left")),
            "polyUpMid": poly_up,
            "polyDownMid": finite(sample.get("poly_down_mid")),
            "binanceUpMid": binance_up,
            "binanceDownMid": finite(sample.get("binance_down_mid")),
            "polyDirection": poly_direction,
            "binanceDirection": binance_direction,
            "targetMatchesPoly": poly_direction == side if poly_direction is not None else None,
            "targetMatchesBinance": binance_direction == side if binance_direction is not None else None,
            "polyBinanceDisagree": poly_direction is not None and binance_direction is not None and poly_direction != binance_direction,
            "polyProbabilityForTargetSide": poly_prob,
            "binanceProbabilityForTargetSide": binance_prob,
            "polyFairEdgeVsTargetPrice": poly_prob - price if poly_prob is not None and price is not None else None,
            "binanceMidEdgeVsTargetPrice": binance_prob - price if binance_prob is not None and price is not None else None,
            "polyMinusBinanceForTargetSide": poly_prob - binance_prob if poly_prob is not None and binance_prob is not None else None,
            "polySourceAgeMs": finite(sample.get("poly_source_age_ms")),
            "polyReceiptAgeMs": finite(sample.get("poly_receipt_age_ms")),
        }
    flip_ms = index.poly_flip_into_side(
        event["asset"], event["marketBucket"], event["eventMs"], event["side"], deadband=deadband, lookback_ms=30_000
    )
    result = dict(event)
    result["snapshots"] = snapshots
    result["polyFlipIntoTargetSideLeadMs"] = event["eventMs"] - flip_ms if flip_ms is not None else None
    return result


def summarize_alignment(events: list[dict[str, Any]], offsets: tuple[float, ...]) -> dict[str, Any]:
    by_offset: dict[str, Any] = {}
    for offset in offsets:
        key = f"{offset:g}s"
        samples = [event.get("snapshots", {}).get(key) for event in events]
        samples = [sample for sample in samples if isinstance(sample, dict)]
        directed = [sample for sample in samples if sample.get("polyDirection") in {"UP", "DOWN"}]
        matches = [sample for sample in directed if sample.get("targetMatchesPoly") is True]
        disagreement = [sample for sample in samples if sample.get("polyBinanceDisagree") is True]
        disagree_poly = [sample for sample in disagreement if sample.get("targetMatchesPoly") is True]
        disagree_binance = [sample for sample in disagreement if sample.get("targetMatchesBinance") is True]
        edges = [finite(sample.get("polyFairEdgeVsTargetPrice")) for sample in samples]
        edges = [value for value in edges if value is not None]
        cross = [finite(sample.get("polyMinusBinanceForTargetSide")) for sample in samples]
        cross = [value for value in cross if value is not None]
        by_offset[key] = {
            "eventsWithObserverSample": len(samples),
            "eventsWithStrongPolyDirection": len(directed),
            "targetMatchesPolyCount": len(matches),
            "targetMatchesPolyShare": len(matches) / len(directed) if directed else None,
            "polyBinanceDisagreement": {
                "count": len(disagreement),
                "targetMatchesPolyCount": len(disagree_poly),
                "targetMatchesPolyShare": len(disagree_poly) / len(disagreement) if disagreement else None,
                "targetMatchesBinanceCount": len(disagree_binance),
                "targetMatchesBinanceShare": len(disagree_binance) / len(disagreement) if disagreement else None,
            },
            "polyFairEdgeVsTargetPrice": {
                "median": median(edges),
                "positiveShare": sum(1 for value in edges if value > 0) / len(edges) if edges else None,
            },
            "polyMinusBinanceForTargetSide": {
                "median": median(cross),
                "positiveShare": sum(1 for value in cross if value > 0) / len(cross) if cross else None,
            },
        }
    leads = [finite(event.get("polyFlipIntoTargetSideLeadMs")) for event in events]
    leads = [value for value in leads if value is not None and 0 <= value <= 30_000]
    return {
        "events": len(events),
        "alignmentByOffsetSeconds": by_offset,
        "polyFlipIntoTargetSideLeadMs": {
            "count": len(leads),
            "median": median(leads),
            "p10": percentile(leads, 0.10),
            "p90": percentile(leads, 0.90),
            "within1sShare": sum(1 for value in leads if value <= 1000) / len(leads) if leads else None,
            "within3sShare": sum(1 for value in leads if value <= 3000) / len(leads) if leads else None,
            "within5sShare": sum(1 for value in leads if value <= 5000) / len(leads) if leads else None,
        },
    }


def maker_reprice_summary(events: list[dict[str, Any]], reference_offset: float) -> dict[str, Any]:
    key = f"{reference_offset:g}s"
    streams: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        streams[(int(event["marketId"]), str(event["side"]))].append(event)
    rows: list[dict[str, Any]] = []
    for stream in streams.values():
        stream.sort(key=lambda row: int(row["eventMs"]))
        for previous, current in zip(stream, stream[1:]):
            prev_sample = previous.get("snapshots", {}).get(key)
            cur_sample = current.get("snapshots", {}).get(key)
            if not isinstance(prev_sample, dict) or not isinstance(cur_sample, dict):
                continue
            p0, p1 = finite(previous.get("price")), finite(current.get("price"))
            f0 = finite(prev_sample.get("polyProbabilityForTargetSide"))
            f1 = finite(cur_sample.get("polyProbabilityForTargetSide"))
            if p0 is None or p1 is None or f0 is None or f1 is None:
                continue
            price_delta = p1 - p0
            poly_delta = f1 - f0
            sign_match = None if abs(price_delta) < 1e-12 or abs(poly_delta) < 1e-12 else (price_delta > 0) == (poly_delta > 0)
            rows.append(
                {
                    "marketId": current["marketId"],
                    "asset": current["asset"],
                    "side": current["side"],
                    "previousHash": previous.get("makerHash"),
                    "nextHash": current.get("makerHash"),
                    "gapSeconds": (int(current["eventMs"]) - int(previous["eventMs"])) / 1000.0,
                    "priceDelta": price_delta,
                    "polySelectedProbabilityDelta": poly_delta,
                    "sameSign": sign_match,
                }
            )
    directional = [row for row in rows if row["sameSign"] is not None]
    matches = sum(1 for row in directional if row["sameSign"] is True)
    return {
        "referenceOffsetSeconds": reference_offset,
        "transitionsWithBothSamples": len(rows),
        "directionalTransitions": len(directional),
        "priceMoveSameDirectionAsPolyCount": matches,
        "priceMoveSameDirectionAsPolyShare": matches / len(directional) if directional else None,
        "examples": rows[:100],
    }


def maker_tier_edge_summary(events: list[dict[str, Any]], reference_offset: float) -> dict[str, Any]:
    key = f"{reference_offset:g}s"
    grouped: dict[str, list[float]] = defaultdict(list)
    for event in events:
        tier = event.get("matchedPotentialTier")
        sample = event.get("snapshots", {}).get(key)
        if tier is None or not isinstance(sample, dict):
            continue
        edge = finite(sample.get("polyFairEdgeVsTargetPrice"))
        if edge is not None:
            grouped[f"{float(tier):g}"].append(edge)
    return {
        tier: {
            "count": len(values),
            "medianPolyFairEdge": median(values),
            "positiveEdgeShare": sum(1 for value in values if value > 0) / len(values) if values else None,
        }
        for tier, values in sorted(grouped.items(), key=lambda item: float(item[0]))
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only test of whether 0x9Dd ETH/BNB 5m activity aligns with locally-recorded "
            "Polymarket direction, especially when Poly and Binance Prediction disagree."
        )
    )
    parser.add_argument("--wallet", default=TARGET_DEFAULT)
    parser.add_argument("--maker-input", default="data/target_maker_hash_profile.json")
    parser.add_argument("--observer-db", default="data/multi_prediction_observer.db")
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--pages", type=int, default=30)
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--poly-deadband", type=float, default=0.02)
    parser.add_argument("--max-sample-age-ms", type=int, default=2500)
    parser.add_argument("--offsets", default="0,0.5,1,2,3,5")
    parser.add_argument("--skip-takers", action="store_true")
    parser.add_argument("--output", default="data/target_poly_direction_profile.json")
    args = parser.parse_args()

    offsets = tuple(sorted({max(0.0, float(piece.strip())) for piece in str(args.offsets).split(",") if piece.strip()}))
    if not offsets:
        offsets = DEFAULT_OFFSETS
    deadband = max(0.0, min(0.49, float(args.poly_deadband)))
    max_sample_age_ms = max(100, int(args.max_sample_age_ms))
    wallet = str(args.wallet).lower().strip()

    maker_input = Path(args.maker_input)
    maker_events = load_maker_buys(maker_input)
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(hours=max(0.1, args.hours))).timestamp() * 1000)
    maker_events = [event for event in maker_events if int(event["eventMs"]) >= cutoff_ms]

    taker_events: list[dict[str, Any]] = []
    taker_non_bid = 0
    taker_fetch_note = None
    if not args.skip_takers:
        api_key = str(os.environ.get("PREDICT_FUN_API_KEY") or "").strip()
        if not api_key:
            taker_fetch_note = "PREDICT_FUN_API_KEY unavailable; maker-only analysis produced"
        else:
            matches = fetch_target_matches(
                wallet=wallet,
                maker=False,
                api_key=api_key,
                hours=args.hours,
                pages=args.pages,
                page_size=args.page_size,
            )
            taker_events, taker_non_bid = extract_taker_buys(matches, wallet)

    index = TrajectoryIndex(Path(args.observer_db))
    db_min_ms, db_max_ms = index.min_ms, index.max_ms
    try:
        maker_aligned = [
            align_event(event, index, offsets=offsets, max_sample_age_ms=max_sample_age_ms, deadband=deadband)
            for event in maker_events
        ]
        taker_aligned = [
            align_event(event, index, offsets=offsets, max_sample_age_ms=max_sample_age_ms, deadband=deadband)
            for event in taker_events
        ]
    finally:
        index.close()

    reference_offset = 1.0 if 1.0 in offsets else offsets[0]
    asset_summary: dict[str, Any] = {}
    for asset in ("ETH", "BNB"):
        asset_summary[asset] = {
            "makerFirstFill": summarize_alignment([row for row in maker_aligned if row["asset"] == asset], offsets),
            "takerBuy": summarize_alignment([row for row in taker_aligned if row["asset"] == asset], offsets),
        }

    def iso_from_ms(value: int | None) -> str | None:
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat() if value is not None else None

    summary = {
        "wallet": wallet,
        "makerInput": str(maker_input),
        "observerDb": str(args.observer_db),
        "observerCoverageUtc": {"min": iso_from_ms(db_min_ms), "max": iso_from_ms(db_max_ms)},
        "filter": "ETH/BNB exact 5-minute titles; BUY/BID only",
        "polyDirectionDeadband": deadband,
        "maxObserverSampleAgeMs": max_sample_age_ms,
        "offsetSeconds": list(offsets),
        "makerParents": len(maker_aligned),
        "takerBuys": len(taker_aligned),
        "takerNonBidIgnored": taker_non_bid,
        "takerFetchNote": taker_fetch_note,
        "makerFirstFill": summarize_alignment(maker_aligned, offsets),
        "takerBuy": summarize_alignment(taker_aligned, offsets),
        "byAsset": asset_summary,
        "makerRepriceVsPoly": maker_reprice_summary(maker_aligned, reference_offset),
        "makerPotentialProfitTierVsPolyEdge": maker_tier_edge_summary(maker_aligned, reference_offset),
    }
    report = {
        "summary": summary,
        "interpretationGuide": {
            "highestValueTest": (
                "Use Taker BUY behavior during Poly/Binance disagreement. A high targetMatchesPolyShare "
                "there is stronger evidence than ordinary same-direction correlation."
            ),
            "makerCaveat": (
                "Maker firstExecutedAt is first observed fill, not placement time. Maker alignment/repricing "
                "is lower-confidence and cannot prove the target saw Poly before placing."
            ),
            "commonCauseCaveat": (
                "Strong Poly alignment does not prove direct Polymarket API usage; both may react to the same "
                "faster underlying/futures/order-flow source."
            ),
            "observerCaveat": (
                "Only timestamps present in local multi_prediction_observer.db are tested. Missing historical "
                "Polymarket books are not invented."
            ),
        },
        "makerEvents": maker_aligned,
        "takerEvents": taker_aligned,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"wrote {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
