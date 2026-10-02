from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE = os.environ.get("PREDICT_FUN_API_BASE", "https://api.predict.fun").rstrip("/")
WEI = 10**18
COMMON_TIERS = (1.0, 2.0, 5.0, 10.0, 50.0)
CRYPTO_SLUG_RE = re.compile(r"^(?P<asset>[a-z0-9]+)-updown-(?P<minutes>\d+)m-(?P<epoch>\d+)$", re.I)


def dec_wei(value: Any) -> float | None:
    try:
        return float(value) / WEI
    except (TypeError, ValueError, OverflowError):
        return None


def iso_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    rows = sorted(float(x) for x in values)
    if len(rows) == 1:
        return rows[0]
    x = max(0.0, min(1.0, q)) * (len(rows) - 1)
    lo = int(math.floor(x))
    hi = min(len(rows) - 1, lo + 1)
    weight = x - lo
    return rows[lo] * (1.0 - weight) + rows[hi] * weight


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def norm_address(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text.startswith("0x") and len(text) == 42 else ""


def side_quote_type(row: dict[str, Any]) -> str:
    return str(row.get("quoteType") or "").upper().strip()


def outcome_name(row: dict[str, Any]) -> str:
    outcome = row.get("outcome") if isinstance(row.get("outcome"), dict) else {}
    return str(outcome.get("name") or "").upper().strip()


def market_meta(match: dict[str, Any]) -> dict[str, Any]:
    market = match.get("market") if isinstance(match.get("market"), dict) else {}
    slug = str(market.get("categorySlug") or "").strip().lower()
    title = str(market.get("title") or market.get("question") or "").strip()
    variant = str(market.get("marketVariant") or "").upper().strip()
    variant_data = market.get("variantData") if isinstance(market.get("variantData"), dict) else {}
    crypto_type = str(variant_data.get("type") or "").upper().strip()
    m = CRYPTO_SLUG_RE.match(slug)

    asset = ""
    duration_minutes: int | None = None
    bucket_start_sec: int | None = None
    if m:
        asset = str(m.group("asset") or "").upper()
        duration_minutes = int(m.group("minutes"))
        bucket_start_sec = int(m.group("epoch"))
    else:
        symbol = str(variant_data.get("priceFeedSymbol") or "").upper().strip()
        for suffix in ("USDT", "USD"):
            if symbol.endswith(suffix) and len(symbol) > len(suffix):
                asset = symbol[: -len(suffix)]
                break

    is_crypto = bool(
        m
        or variant == "CRYPTO_UP_DOWN"
        or crypto_type == "CRYPTO_UP_DOWN"
        or "-updown-" in slug
    )
    return {
        "marketId": int(market.get("id") or 0),
        "title": title,
        "slug": slug,
        "asset": asset,
        "durationMinutes": duration_minutes,
        "bucketStartSec": bucket_start_sec,
        "isCrypto": is_crypto,
    }


def seconds_left(meta: dict[str, Any], executed: datetime | None) -> float | None:
    if executed is None:
        return None
    start = meta.get("bucketStartSec")
    minutes = meta.get("durationMinutes")
    if start is None or minutes is None:
        return None
    end_s = float(start) + float(minutes) * 60.0
    return end_s - executed.timestamp()


def nearest_tier(value: float) -> tuple[float, float]:
    tier = min(COMMON_TIERS, key=lambda t: abs(float(value) - t))
    rel = abs(float(value) - tier) / tier if tier > 0 else math.inf
    return tier, rel


def api_get(
    path: str,
    params: dict[str, Any],
    api_key: str,
    *,
    timeout: float = 20.0,
    retries: int = 4,
) -> dict[str, Any]:
    query = urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{API_BASE}{path}"
    if query:
        url += f"?{query}"
    headers = {
        "x-api-key": api_key,
        "Accept": "application/json",
        "User-Agent": "BTC-5M-Lab-PredictQuantWalletScreener/1.0",
    }

    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        req = Request(url, headers=headers)
        try:
            with urlopen(req, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("Unexpected Predict response shape")
            return payload
        except HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            last_error = RuntimeError(f"Predict GET {path} HTTP {exc.code}: {detail[:500]}")
            if exc.code not in {429, 500, 502, 503, 504} or attempt + 1 >= retries:
                raise last_error
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt + 1 >= retries:
                raise RuntimeError(f"Predict GET {path} failed: {type(exc).__name__}: {exc}") from exc
        time.sleep(min(5.0, 0.5 * (2**attempt)))
    raise RuntimeError(f"Predict GET {path} failed: {last_error}")


@dataclass
class Event:
    wallet: str
    role: str
    market_id: int
    market_title: str
    category_slug: str
    asset: str
    duration_minutes: int | None
    is_crypto: bool
    executed_at: datetime | None
    seconds_left: float | None
    outcome: str
    quote_type: str
    order_hash: str
    shares: float
    price: float
    cost_usdt: float
    potential_profit_usdt: float
    counterparty_wallets: tuple[str, ...] = field(default_factory=tuple)


def _event_from_order(
    *,
    order: dict[str, Any],
    role: str,
    match: dict[str, Any],
    meta: dict[str, Any],
    executed: datetime | None,
    counterparties: Iterable[str],
    synthetic_id: str,
) -> Event | None:
    wallet = norm_address(order.get("signer") or order.get("maker"))
    if not wallet:
        return None
    shares = dec_wei(order.get("amount")) or 0.0
    price = dec_wei(order.get("price")) or 0.0
    order_hash = str(order.get("hash") or order.get("orderHash") or "").strip().lower()
    if not order_hash:
        order_hash = synthetic_id
    sl = seconds_left(meta, executed)
    return Event(
        wallet=wallet,
        role=role,
        market_id=int(meta["marketId"] or 0),
        market_title=str(meta["title"] or ""),
        category_slug=str(meta["slug"] or ""),
        asset=str(meta["asset"] or ""),
        duration_minutes=meta["durationMinutes"],
        is_crypto=bool(meta["isCrypto"]),
        executed_at=executed,
        seconds_left=sl,
        outcome=outcome_name(order),
        quote_type=side_quote_type(order),
        order_hash=order_hash,
        shares=shares,
        price=price,
        cost_usdt=shares * price,
        potential_profit_usdt=shares * max(0.0, 1.0 - price),
        counterparty_wallets=tuple(sorted({x for x in counterparties if x and x != wallet})),
    )


def events_from_match(match: dict[str, Any], sequence: int = 0) -> list[Event]:
    meta = market_meta(match)
    executed = iso_dt(match.get("executedAt"))
    makers = [x for x in (match.get("makers") or []) if isinstance(x, dict)]
    taker = match.get("taker") if isinstance(match.get("taker"), dict) else None

    maker_addresses = [norm_address(x.get("signer") or x.get("maker")) for x in makers]
    maker_addresses = [x for x in maker_addresses if x]
    taker_address = norm_address((taker or {}).get("signer") or (taker or {}).get("maker"))

    out: list[Event] = []
    for idx, maker in enumerate(makers):
        ev = _event_from_order(
            order=maker,
            role="MAKER",
            match=match,
            meta=meta,
            executed=executed,
            counterparties=[taker_address] if taker_address else [],
            synthetic_id=f"MISSING_MAKER_HASH:{sequence}:{idx}",
        )
        if ev is not None:
            out.append(ev)

    if taker is not None:
        ev = _event_from_order(
            order=taker,
            role="TAKER",
            match=match,
            meta=meta,
            executed=executed,
            counterparties=maker_addresses,
            synthetic_id=f"MISSING_TAKER_HASH:{sequence}",
        )
        if ev is not None:
            out.append(ev)
    return out


def fetch_recent_matches(
    *,
    api_key: str,
    hours: float,
    pages: int,
    page_size: int,
    request_delay: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(0.05, float(hours)))
    rows: list[dict[str, Any]] = []
    after: str | None = None
    seen_cursors: set[str] = set()
    oldest: datetime | None = None
    reached_cutoff = False
    page_count = 0

    for _ in range(max(1, int(pages))):
        payload = api_get(
            "/v1/orders/matches",
            {
                "first": max(1, min(500, int(page_size))),
                "after": after,
            },
            api_key,
        )
        page_count += 1
        page = payload.get("data") if isinstance(payload.get("data"), list) else []
        if not page:
            break

        for item in page:
            if not isinstance(item, dict):
                continue
            dt = iso_dt(item.get("executedAt"))
            if dt is not None:
                oldest = dt if oldest is None or dt < oldest else oldest
                if dt < cutoff:
                    reached_cutoff = True
                    continue
            rows.append(item)

        cursor = str(payload.get("cursor") or "").strip()
        if reached_cutoff or not cursor or cursor in seen_cursors:
            break
        seen_cursors.add(cursor)
        after = cursor
        if request_delay > 0:
            time.sleep(request_delay)

    coverage = {
        "requestedHours": hours,
        "cutoffUtc": cutoff.isoformat(),
        "pagesFetched": page_count,
        "matchRows": len(rows),
        "oldestFetchedUtc": oldest.isoformat() if oldest else None,
        "reachedRequestedCutoff": reached_cutoff,
        "truncatedByPageLimit": bool(not reached_cutoff and page_count >= max(1, int(pages))),
    }
    return rows, coverage


def parse_assets(value: str) -> set[str] | None:
    text = str(value or "").strip()
    if not text or text.upper() in {"ALL", "*"}:
        return None
    return {x.strip().upper() for x in text.split(",") if x.strip()}


def event_in_scope(event: Event, assets: set[str] | None, durations: set[int] | None) -> bool:
    if not event.is_crypto:
        return False
    if assets is not None and event.asset.upper() not in assets:
        return False
    if durations is not None and event.duration_minutes not in durations:
        return False
    return True


def timing_regularity(events: list[Event]) -> dict[str, Any]:
    first_bids: dict[int, Event] = {}
    for ev in sorted(events, key=lambda x: x.executed_at or datetime.max.replace(tzinfo=timezone.utc)):
        if ev.quote_type != "BID" or ev.seconds_left is None:
            continue
        if ev.market_id not in first_bids:
            first_bids[ev.market_id] = ev
    values = [
        float(ev.seconds_left)
        for ev in first_bids.values()
        if ev.seconds_left is not None and -5 <= float(ev.seconds_left) <= 7200
    ]
    bins = Counter(int(max(0.0, x) // 10) * 10 for x in values)
    top_bin, top_count = (bins.most_common(1)[0] if bins else (None, 0))
    concentration = top_count / len(values) if values else 0.0
    return {
        "samples": len(values),
        "medianSecondsLeft": statistics.median(values) if values else None,
        "p25SecondsLeft": percentile(values, 0.25),
        "p75SecondsLeft": percentile(values, 0.75),
        "top10sBinStart": top_bin,
        "top10sBinConcentration": concentration if values else None,
    }


def maker_parent_stats(events: list[Event]) -> dict[str, Any]:
    parents: dict[str, dict[str, Any]] = {}
    for ev in events:
        if ev.role != "MAKER":
            continue
        key = ev.order_hash
        row = parents.setdefault(
            key,
            {
                "marketId": ev.market_id,
                "outcome": ev.outcome,
                "quoteType": ev.quote_type,
                "fillLegs": 0,
                "shares": 0.0,
                "cost": 0.0,
                "potentialProfit": 0.0,
            },
        )
        row["fillLegs"] += 1
        row["shares"] += ev.shares
        row["cost"] += ev.cost_usdt
        row["potentialProfit"] += ev.potential_profit_usdt

    parent_rows = list(parents.values())
    multi = sum(1 for x in parent_rows if int(x["fillLegs"]) > 1)
    potential_matches = 0
    cost_matches = 0
    for row in parent_rows:
        if float(row["potentialProfit"]) > 0:
            _, rel = nearest_tier(float(row["potentialProfit"]))
            potential_matches += int(rel <= 0.05)
        if float(row["cost"]) > 0:
            _, rel = nearest_tier(float(row["cost"]))
            cost_matches += int(rel <= 0.05)

    unique_by_stream: dict[tuple[int, str], set[str]] = defaultdict(set)
    for ev in events:
        if ev.role == "MAKER":
            unique_by_stream[(ev.market_id, ev.outcome)].add(ev.order_hash)
    stream_counts = [len(x) for x in unique_by_stream.values()]
    return {
        "uniqueMakerParentsApprox": len(parent_rows),
        "multiFillMakerParents": multi,
        "multiFillMakerParentShare": multi / len(parent_rows) if parent_rows else None,
        "potentialProfitTierWithin5PctShare": potential_matches / len(parent_rows) if parent_rows else None,
        "costTierWithin5PctShare": cost_matches / len(parent_rows) if parent_rows else None,
        "makerParentsPerMarketOutcomeMedian": statistics.median(stream_counts) if stream_counts else None,
        "makerParentsPerMarketOutcomeP90": percentile([float(x) for x in stream_counts], 0.90),
    }


def classify(profile: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    maker = float(profile.get("makerShare") or 0.0)
    two = float(profile.get("twoSidedBidMarketShare") or 0.0)
    refill = float(profile.get("repeatedSameOutcomeBidMarketShare") or 0.0)
    crypto = float(profile.get("cryptoShareAllParticipation") or 0.0)
    fills = int(profile.get("fillLegs") or 0)
    markets = int(profile.get("markets") or 0)

    if maker >= 0.80 and two >= 0.50:
        tags.append("MARKET_MAKER")
    if maker >= 0.70 and two >= 0.65:
        tags.append("COMPLEMENT_CANDIDATE")
    if refill >= 0.70 and maker >= 0.60:
        tags.append("REPLENISHMENT_BOT")
    if crypto >= 0.80 and two <= 0.30 and refill >= 0.45:
        tags.append("DIRECTIONAL_QUANT")
    if fills >= 500 or markets >= 100:
        tags.append("HIGH_FREQUENCY")
    if not tags:
        tags.append("ACTIVE_UNKNOWN")
    return tags


def score_profile(profile: dict[str, Any]) -> tuple[float, dict[str, float]]:
    maker_share = float(profile.get("makerShare") or 0.0)
    crypto_share = float(profile.get("cryptoShareAllParticipation") or 0.0)
    refill_share = float(profile.get("repeatedSameOutcomeBidMarketShare") or 0.0)
    two_sided = float(profile.get("twoSidedBidMarketShare") or 0.0)
    markets = int(profile.get("markets") or 0)
    fills = int(profile.get("fillLegs") or 0)
    timing = profile.get("timing") if isinstance(profile.get("timing"), dict) else {}
    timing_conc = float(timing.get("top10sBinConcentration") or 0.0)
    parent = profile.get("makerParent") if isinstance(profile.get("makerParent"), dict) else {}
    potential_tier = float(parent.get("potentialProfitTierWithin5PctShare") or 0.0)
    cost_tier = float(parent.get("costTierWithin5PctShare") or 0.0)
    sizing = max(potential_tier, cost_tier)

    components = {
        "makerActivity": 20.0 * clamp01(maker_share),
        "cryptoConcentration": 15.0 * clamp01(crypto_share),
        "replenishment": 15.0 * clamp01(refill_share),
        "sizingRegularity": 15.0 * clamp01(sizing / 0.50),
        "marketBreadth": 10.0 * clamp01(markets / 100.0),
        "twoSidedStructure": 10.0 * clamp01(two_sided),
        "timingRegularity": 10.0 * clamp01(timing_conc / 0.25),
        "activityDepth": 5.0 * clamp01(fills / 1000.0),
    }
    return round(sum(components.values()), 3), {k: round(v, 3) for k, v in components.items()}


def build_profiles(
    events: list[Event],
    *,
    assets: set[str] | None,
    durations: set[int] | None,
    min_fills: int,
    min_markets: int,
    excluded: set[str],
) -> list[dict[str, Any]]:
    all_by_wallet: dict[str, list[Event]] = defaultdict(list)
    scoped_by_wallet: dict[str, list[Event]] = defaultdict(list)
    for ev in events:
        if ev.wallet in excluded:
            continue
        all_by_wallet[ev.wallet].append(ev)
        if event_in_scope(ev, assets, durations):
            scoped_by_wallet[ev.wallet].append(ev)

    profiles: list[dict[str, Any]] = []
    for wallet, scoped in scoped_by_wallet.items():
        if not scoped:
            continue
        all_events = all_by_wallet[wallet]
        markets = {ev.market_id for ev in scoped if ev.market_id}
        if len(scoped) < min_fills or len(markets) < min_markets:
            continue

        maker = [ev for ev in scoped if ev.role == "MAKER"]
        taker = [ev for ev in scoped if ev.role == "TAKER"]
        bids = [ev for ev in scoped if ev.quote_type == "BID"]
        asks = [ev for ev in scoped if ev.quote_type == "ASK"]
        bid_markets = {ev.market_id for ev in bids}

        bid_orders_by_market_outcome: dict[tuple[int, str], set[str]] = defaultdict(set)
        outcomes_by_market: dict[int, set[str]] = defaultdict(set)
        for ev in bids:
            if ev.market_id:
                outcomes_by_market[ev.market_id].add(ev.outcome)
                bid_orders_by_market_outcome[(ev.market_id, ev.outcome)].add(ev.order_hash)

        repeated_markets = {
            market_id
            for (market_id, _outcome), hashes in bid_orders_by_market_outcome.items()
            if len(hashes) >= 2
        }
        two_sided_markets = {mid for mid, outs in outcomes_by_market.items() if len({x for x in outs if x}) >= 2}

        crypto_all = sum(1 for ev in all_events if ev.is_crypto)
        asset_counts = Counter(ev.asset or "UNKNOWN" for ev in scoped)
        duration_counts = Counter(str(ev.duration_minutes or "UNKNOWN") for ev in scoped)
        counterparties = Counter(cp for ev in scoped for cp in ev.counterparty_wallets)

        timing = timing_regularity(scoped)
        parent_stats = maker_parent_stats(scoped)

        profile: dict[str, Any] = {
            "wallet": wallet,
            "fillLegs": len(scoped),
            "makerFillLegs": len(maker),
            "takerFillLegs": len(taker),
            "makerShare": len(maker) / len(scoped) if scoped else None,
            "buyBidLegs": len(bids),
            "sellAskLegs": len(asks),
            "markets": len(markets),
            "bidMarkets": len(bid_markets),
            "twoSidedBidMarkets": len(two_sided_markets),
            "twoSidedBidMarketShare": len(two_sided_markets) / len(bid_markets) if bid_markets else None,
            "repeatedSameOutcomeBidMarkets": len(repeated_markets),
            "repeatedSameOutcomeBidMarketShare": len(repeated_markets) / len(bid_markets) if bid_markets else None,
            "cryptoShareAllParticipation": crypto_all / len(all_events) if all_events else None,
            "assets": dict(asset_counts.most_common()),
            "durationsMinutes": dict(duration_counts.most_common()),
            "timing": timing,
            "makerParent": parent_stats,
            "topCounterparties": [
                {"wallet": addr, "matchLegInteractions": count}
                for addr, count in counterparties.most_common(8)
            ],
        }
        score, components = score_profile(profile)
        profile["quantCandidateScore"] = score
        profile["scoreComponents"] = components
        profile["tags"] = classify(profile)
        profiles.append(profile)

    profiles.sort(
        key=lambda p: (
            float(p.get("quantCandidateScore") or 0.0),
            int(p.get("fillLegs") or 0),
            int(p.get("markets") or 0),
        ),
        reverse=True,
    )
    return profiles


def write_csv(path: Path, profiles: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "rank",
        "wallet",
        "quantCandidateScore",
        "tags",
        "fillLegs",
        "markets",
        "makerShare",
        "cryptoShareAllParticipation",
        "twoSidedBidMarketShare",
        "repeatedSameOutcomeBidMarketShare",
        "makerParentsApprox",
        "potentialProfitTierWithin5PctShare",
        "timingTop10sBinConcentration",
        "assets",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for rank, profile in enumerate(profiles, start=1):
            parent = profile.get("makerParent") or {}
            timing = profile.get("timing") or {}
            writer.writerow(
                {
                    "rank": rank,
                    "wallet": profile.get("wallet"),
                    "quantCandidateScore": profile.get("quantCandidateScore"),
                    "tags": "|".join(profile.get("tags") or []),
                    "fillLegs": profile.get("fillLegs"),
                    "markets": profile.get("markets"),
                    "makerShare": profile.get("makerShare"),
                    "cryptoShareAllParticipation": profile.get("cryptoShareAllParticipation"),
                    "twoSidedBidMarketShare": profile.get("twoSidedBidMarketShare"),
                    "repeatedSameOutcomeBidMarketShare": profile.get("repeatedSameOutcomeBidMarketShare"),
                    "makerParentsApprox": parent.get("uniqueMakerParentsApprox"),
                    "potentialProfitTierWithin5PctShare": parent.get("potentialProfitTierWithin5PctShare"),
                    "timingTop10sBinConcentration": timing.get("top10sBinConcentration"),
                    "assets": json.dumps(profile.get("assets") or {}, ensure_ascii=False, separators=(",", ":")),
                }
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only Predict API wallet screener. Scans recent order-match events and ranks addresses "
            "by quant/bot-like execution structure. The score is a research-priority score, NOT a profitability score."
        )
    )
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--pages", type=int, default=60, help="Maximum /v1/orders/matches pages (500 rows/page max).")
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--request-delay", type=float, default=0.15)
    parser.add_argument(
        "--assets",
        default="BTC,ETH,BNB",
        help="Comma-separated crypto assets to score, or ALL for every CRYPTO_UP_DOWN asset.",
    )
    parser.add_argument(
        "--durations",
        default="5,15",
        help="Comma-separated CRYPTO_UP_DOWN durations in minutes, or ALL.",
    )
    parser.add_argument("--min-fills", type=int, default=50)
    parser.add_argument("--min-markets", type=int, default=10)
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--exclude", action="append", default=[], help="Wallet address to exclude; repeatable.")
    parser.add_argument("--output", default="data/predict_quant_wallet_candidates.json")
    parser.add_argument("--csv", default="data/predict_quant_wallet_candidates.csv")
    args = parser.parse_args()

    api_key = str(os.environ.get("PREDICT_FUN_API_KEY") or "").strip()
    if not api_key:
        raise SystemExit("PREDICT_FUN_API_KEY is required")

    assets = parse_assets(args.assets)
    durations = None if str(args.durations).strip().upper() in {"ALL", "*", ""} else {
        int(x.strip()) for x in str(args.durations).split(",") if x.strip()
    }
    excluded = {norm_address(x) for x in args.exclude}
    excluded.discard("")

    print(
        f"fetching Predict matches: hours={args.hours:g} pages<={args.pages} "
        f"assets={sorted(assets) if assets is not None else 'ALL'} "
        f"durations={sorted(durations) if durations is not None else 'ALL'}"
    )
    matches, coverage = fetch_recent_matches(
        api_key=api_key,
        hours=args.hours,
        pages=args.pages,
        page_size=args.page_size,
        request_delay=args.request_delay,
    )

    events: list[Event] = []
    for idx, match in enumerate(matches):
        events.extend(events_from_match(match, idx))

    profiles = build_profiles(
        events,
        assets=assets,
        durations=durations,
        min_fills=max(1, args.min_fills),
        min_markets=max(1, args.min_markets),
        excluded=excluded,
    )
    top_n = profiles[: max(1, args.top)]

    report = {
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "apiBase": API_BASE,
        "scope": {
            "assets": sorted(assets) if assets is not None else "ALL_CRYPTO_UP_DOWN",
            "durationsMinutes": sorted(durations) if durations is not None else "ALL",
            "minimumFillLegs": max(1, args.min_fills),
            "minimumMarkets": max(1, args.min_markets),
        },
        "coverage": coverage,
        "summary": {
            "participationEventsParsed": len(events),
            "uniqueWalletsSeen": len({ev.wallet for ev in events}),
            "qualifiedCandidates": len(profiles),
            "returnedCandidates": len(top_n),
        },
        "scoreMeaning": {
            "type": "RESEARCH_PRIORITY_NOT_PROFITABILITY",
            "weights": {
                "makerActivity": 20,
                "cryptoConcentration": 15,
                "replenishment": 15,
                "sizingRegularity": 15,
                "marketBreadth": 10,
                "twoSidedStructure": 10,
                "timingRegularity": 10,
                "activityDepth": 5,
            },
            "caveats": [
                "Public matches expose executions, not all placements/cancellations.",
                "A maker hash can be only partially filled; reconstructed parent size is a lower bound on requested size.",
                "Current/open position PnL is intentionally not used in the score.",
                "High score means the address looks algorithmic and worth deeper analysis; it does not mean the strategy is profitable.",
                "If coverage.truncatedByPageLimit is true, raise --pages before comparing 24h activity totals.",
            ],
        },
        "candidates": top_n,
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    csv_path = Path(args.csv)
    write_csv(csv_path, top_n)

    print(
        f"matches={coverage['matchRows']} events={len(events)} "
        f"wallets={report['summary']['uniqueWalletsSeen']} qualified={len(profiles)}"
    )
    if coverage["truncatedByPageLimit"]:
        print("warning: page limit was reached before the requested time cutoff; increase --pages for fuller coverage")
    print("\nTop candidates:")
    print("rank score maker% crypto% twoSide% refill% markets fills wallet tags")
    for rank, p in enumerate(top_n[: min(20, len(top_n))], start=1):
        def pct(v: Any) -> str:
            return f"{100.0 * float(v):5.1f}" if v is not None else "  n/a"
        print(
            f"{rank:>4} {float(p['quantCandidateScore']):>5.1f} "
            f"{pct(p.get('makerShare'))} {pct(p.get('cryptoShareAllParticipation'))} "
            f"{pct(p.get('twoSidedBidMarketShare'))} {pct(p.get('repeatedSameOutcomeBidMarketShare'))} "
            f"{int(p.get('markets') or 0):>7} {int(p.get('fillLegs') or 0):>5} "
            f"{p['wallet']} {','.join(p.get('tags') or [])}"
        )
    print(f"\nwrote {out.resolve()}")
    print(f"wrote {csv_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
