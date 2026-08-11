from __future__ import annotations

import argparse
import json
import os
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE = os.environ.get("PREDICT_FUN_API_BASE", "https://api.predict.fun").rstrip("/")
WEI = 10**18
TARGET_DEFAULT = "0x9ddbdc8bdf41ec79cd4f5c4dec14050f4b591a18"
TIERS = (1.0, 2.0, 5.0, 10.0, 50.0)


def dec(value: Any) -> float | None:
    try:
        return float(value) / WEI
    except (TypeError, ValueError):
        return None


def api_get(path: str, params: dict[str, Any], api_key: str) -> dict[str, Any]:
    query = urlencode({k: v for k, v in params.items() if v is not None})
    req = Request(
        f"{API_BASE}{path}?{query}",
        headers={"x-api-key": api_key, "Accept": "application/json", "User-Agent": "BTC-5M-Lab-MakerHashProfiler/1.0"},
    )
    with urlopen(req, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Predict response")
    return payload


def signer(row: dict[str, Any]) -> str:
    return str(row.get("signer") or "").lower()


def outcome(row: dict[str, Any]) -> str:
    value = row.get("outcome") if isinstance(row.get("outcome"), dict) else {}
    return str(value.get("name") or "").upper()


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
    rows = sorted(values)
    if len(rows) == 1:
        return rows[0]
    x = max(0.0, min(1.0, q)) * (len(rows) - 1)
    lo = int(x)
    hi = min(len(rows) - 1, lo + 1)
    w = x - lo
    return rows[lo] * (1.0 - w) + rows[hi] * w


def nearest_tier(value: float) -> dict[str, Any]:
    tier = min(TIERS, key=lambda t: abs(value - t))
    rel = abs(value - tier) / tier if tier > 0 else None
    return {"tier": tier, "relativeError": rel, "within5Pct": bool(rel is not None and rel <= 0.05)}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Profile Predict makerHash fragmentation so many small match legs can be distinguished "
            "from many submitted parent orders. Read-only."
        )
    )
    parser.add_argument("--wallet", default=TARGET_DEFAULT)
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--pages", type=int, default=30)
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--output", default="data/target_maker_hash_profile.json")
    args = parser.parse_args()

    api_key = str(os.environ.get("PREDICT_FUN_API_KEY") or "").strip()
    if not api_key:
        raise SystemExit("PREDICT_FUN_API_KEY is required")
    wallet = str(args.wallet).lower().strip()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(0.1, args.hours))

    matches: list[dict[str, Any]] = []
    after: str | None = None
    seen_cursors: set[str] = set()
    for _ in range(max(1, args.pages)):
        payload = api_get(
            "/v1/orders/matches",
            {
                "first": max(1, min(500, args.page_size)),
                "after": after,
                "signerAddress": wallet,
                "isSignerMaker": "true",
            },
            api_key,
        )
        rows = payload.get("data") if isinstance(payload.get("data"), list) else []
        if not rows:
            break
        stop = False
        for match in rows:
            if not isinstance(match, dict):
                continue
            dt = iso_dt(match.get("executedAt"))
            if dt is not None and dt < cutoff:
                stop = True
                continue
            matches.append(match)
        cursor = str(payload.get("cursor") or "").strip()
        if stop or not cursor or cursor in seen_cursors:
            break
        seen_cursors.add(cursor)
        after = cursor

    groups: dict[str, dict[str, Any]] = {}
    raw_legs = 0
    for match in matches:
        market = match.get("market") if isinstance(match.get("market"), dict) else {}
        market_id = int(market.get("id") or 0)
        executed = str(match.get("executedAt") or "")
        makers = [x for x in (match.get("makers") or []) if isinstance(x, dict)]
        for maker in makers:
            if signer(maker) != wallet:
                continue
            raw_legs += 1
            h = str(maker.get("hash") or maker.get("orderHash") or "").lower().strip()
            if not h:
                h = f"MISSING_HASH:{market_id}:{outcome(maker)}:{dec(maker.get('price'))}:{raw_legs}"
            amount = dec(maker.get("amount")) or 0.0
            price = dec(maker.get("price")) or 0.0
            row = groups.setdefault(
                h,
                {
                    "makerHash": h,
                    "marketId": market_id,
                    "marketTitle": market.get("title") or market.get("question"),
                    "outcome": outcome(maker),
                    "quoteType": str(maker.get("quoteType") or "").upper(),
                    "prices": [],
                    "fillLegs": 0,
                    "totalShares": 0.0,
                    "totalCostUsdtApprox": 0.0,
                    "totalPotentialProfitUsdtApprox": 0.0,
                    "firstExecutedAt": executed,
                    "lastExecutedAt": executed,
                    "transactionHashes": [],
                },
            )
            row["fillLegs"] += 1
            row["totalShares"] += amount
            row["totalCostUsdtApprox"] += amount * price
            row["totalPotentialProfitUsdtApprox"] += amount * (1.0 - price)
            row["prices"].append(price)
            if executed and (not row["firstExecutedAt"] or executed < row["firstExecutedAt"]):
                row["firstExecutedAt"] = executed
            if executed and executed > row["lastExecutedAt"]:
                row["lastExecutedAt"] = executed
            tx = str(match.get("transactionHash") or "")
            if tx and tx not in row["transactionHashes"]:
                row["transactionHashes"].append(tx)

    parent_rows = list(groups.values())
    for row in parent_rows:
        prices = [float(x) for x in row.pop("prices") if float(x) > 0]
        row["price"] = statistics.median(prices) if prices else None
        row["priceMin"] = min(prices) if prices else None
        row["priceMax"] = max(prices) if prices else None
        row["costTier"] = nearest_tier(float(row["totalCostUsdtApprox"]))
        row["potentialProfitTier"] = nearest_tier(float(row["totalPotentialProfitUsdtApprox"]))

    parent_rows.sort(key=lambda r: (str(r.get("firstExecutedAt") or ""), str(r.get("makerHash"))), reverse=True)
    leg_counts = [float(r["fillLegs"]) for r in parent_rows]
    multi = [r for r in parent_rows if int(r["fillLegs"]) > 1]
    legs_from_multi = sum(int(r["fillLegs"]) for r in multi)

    hashes_by_market: dict[int, set[str]] = defaultdict(set)
    hashes_by_market_side: dict[str, set[str]] = defaultdict(set)
    for row in parent_rows:
        mid = int(row.get("marketId") or 0)
        hashes_by_market[mid].add(str(row["makerHash"]))
        hashes_by_market_side[f"{mid}:{row.get('outcome')}"] .add(str(row["makerHash"]))

    summary = {
        "wallet": wallet,
        "windowHours": args.hours,
        "matchRowsFetched": len(matches),
        "makerFillLegs": raw_legs,
        "uniqueMakerHashesApproxParentOrders": len(parent_rows),
        "fillLegsPerMakerHash": {
            "mean": (sum(leg_counts) / len(leg_counts)) if leg_counts else None,
            "median": statistics.median(leg_counts) if leg_counts else None,
            "p90": percentile(leg_counts, 0.90),
            "p99": percentile(leg_counts, 0.99),
            "max": max(leg_counts) if leg_counts else None,
        },
        "multiFillMakerHashes": len(multi),
        "shareOfMakerHashesWithMultipleFillLegs": len(multi) / len(parent_rows) if parent_rows else None,
        "shareOfFillLegsFromMultiFillHashes": legs_from_multi / raw_legs if raw_legs else None,
        "uniqueMakerHashesPerMarket": {
            "median": statistics.median([len(x) for x in hashes_by_market.values()]) if hashes_by_market else None,
            "p90": percentile([float(len(x)) for x in hashes_by_market.values()], 0.90),
            "max": max((len(x) for x in hashes_by_market.values()), default=0),
        },
        "uniqueMakerHashesPerMarketOutcome": {
            "median": statistics.median([len(x) for x in hashes_by_market_side.values()]) if hashes_by_market_side else None,
            "p90": percentile([float(len(x)) for x in hashes_by_market_side.values()], 0.90),
            "max": max((len(x) for x in hashes_by_market_side.values()), default=0),
        },
        "potentialProfitTier5PctCount": sum(1 for r in parent_rows if r["potentialProfitTier"]["within5Pct"]),
        "costTier5PctCount": sum(1 for r in parent_rows if r["costTier"]["within5Pct"]),
    }

    report = {
        "summary": summary,
        "interpretationGuide": {
            "manyMatchLegsButFewMakerHashes": "fragmented fills: a small number of parent resting orders are being consumed by many takers",
            "manyMakerHashesPerMarketOutcome": "genuine repeated parent-order placement/replenishment",
            "sameHashSamePriceManyLegs": "strong evidence of one parent order split across multiple settlement matches",
            "caveat": "makerHash grouping reconstructs parent orders from public match events; placement/cancel events for the target wallet are not directly exposed by this endpoint",
        },
        "parentOrdersByMakerHash": parent_rows,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"wrote {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
