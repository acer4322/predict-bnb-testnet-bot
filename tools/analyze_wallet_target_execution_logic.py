from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.predict_bot import predict_wallet_shadow_observer as base


DEFAULT_DB = ROOT / "data" / "predict_wallet_shadow.db"
DEFAULT_OUTPUT = ROOT / "artifacts" / "wallet_profit_strategy" / "target_execution_logic_report.json"


def _market_end_ms(raw_json: str) -> int | None:
    try:
        raw = json.loads(raw_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    market = raw.get("market") if isinstance(raw.get("market"), dict) else {}
    return base._iso_ms(market.get("boostEndsAt"))


def _side(up: float, down: float) -> str | None:
    return "UP" if up > down else "DOWN" if down > up else None


def _timing_bucket(seconds_left: float | None) -> str:
    if seconds_left is None:
        return "UNKNOWN"
    if seconds_left < 0:
        return "AFTER_END"
    if seconds_left <= 10:
        return "0_10S"
    if seconds_left <= 30:
        return "10_30S"
    if seconds_left <= 60:
        return "30_60S"
    if seconds_left <= 120:
        return "60_120S"
    if seconds_left <= 180:
        return "120_180S"
    if seconds_left <= 240:
        return "180_240S"
    return "240S_PLUS"


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _parent_orders(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        key = (
            event["role"], event.get("order_hash") or event["leg_id"], event["side"],
            event["quote_type"], event["event_ms"],
        )
        grouped[key].append(event)
    result = []
    for (role, order_hash, side, quote_type, event_ms), legs in grouped.items():
        shares = sum(float(x.get("shares") or 0.0) for x in legs)
        notional = sum(float(x.get("shares") or 0.0) * float(x.get("price") or 0.0) for x in legs)
        end_ms = next((_market_end_ms(str(x.get("raw_json") or "")) for x in legs if x.get("raw_json")), None)
        seconds_left = (end_ms - int(event_ms)) / 1000.0 if end_ms is not None else None
        result.append({
            "orderHash": order_hash,
            "role": role,
            "side": side,
            "quoteType": quote_type,
            "eventMs": int(event_ms),
            "scheduledSecondsLeft": seconds_left,
            "timingBucket": _timing_bucket(seconds_left),
            "shares": shares,
            "notionalUsdt": notional,
            "averagePrice": notional / shares if shares else None,
            "legs": len(legs),
        })
    return sorted(result, key=lambda item: (item["eventMs"], item["orderHash"]))


def _analyse_market(events: list[dict[str, Any]], settled: dict[str, Any] | None) -> dict[str, Any]:
    orders = _parent_orders(events)
    final_up_cost = sum(x["notionalUsdt"] for x in orders if x["side"] == "UP" and x["quoteType"] == "BID")
    final_down_cost = sum(x["notionalUsdt"] for x in orders if x["side"] == "DOWN" and x["quoteType"] == "BID")
    final_capital_side = _side(final_up_cost, final_down_cost)
    up_shares = down_shares = up_cost = down_cost = 0.0
    previous_share_side = previous_capital_side = None
    first_share_flip = first_capital_flip = None
    reason_counts: Counter[str] = Counter()

    for order in orders:
        before_share_side = _side(up_shares, down_shares)
        before_capital_side = _side(up_cost, down_cost)
        sign = 1.0 if order["quoteType"] == "BID" else -1.0
        if order["side"] == "UP":
            up_shares += sign * order["shares"]
            up_cost += sign * order["notionalUsdt"]
        else:
            down_shares += sign * order["shares"]
            down_cost += sign * order["notionalUsdt"]
        after_share_side = _side(up_shares, down_shares)
        after_capital_side = _side(up_cost, down_cost)

        price = float(order.get("averagePrice") or 0.0)
        if price <= 0.12 and order["side"] != final_capital_side:
            reason = "LOW_PRICE_INSURANCE_OR_TAIL_HEDGE"
        elif order["role"] == "MAKER" and abs(order["shares"] / 18.0 - round(order["shares"] / 18.0)) < 1e-6:
            reason = "MAKER_18_SHARE_BASE_UNIT_ACCUMULATION"
        elif order["role"] == "MAKER":
            reason = "MAKER_PASSIVE_ACCUMULATION"
        elif before_capital_side not in {None, order["side"]} and after_capital_side == order["side"]:
            reason = "TAKER_CAPITAL_CONVICTION_FLIP"
        elif order["side"] == final_capital_side:
            reason = "TAKER_DIRECTIONAL_ADD_OR_RESIDUAL_CORRECTION"
        else:
            reason = "TAKER_REBALANCE_OR_INSURANCE"
        order["inferredReason"] = reason
        order["reasonConfidence"] = "STRUCTURAL_EVIDENCE_ONLY"
        order["before"] = {
            "shareSide": before_share_side, "capitalSide": before_capital_side,
            "upShares": up_shares - (sign * order["shares"] if order["side"] == "UP" else 0),
            "downShares": down_shares - (sign * order["shares"] if order["side"] == "DOWN" else 0),
        }
        order["after"] = {
            "shareSide": after_share_side, "capitalSide": after_capital_side,
            "upShares": up_shares, "downShares": down_shares,
            "upCostUsdt": up_cost, "downCostUsdt": down_cost,
        }
        reason_counts[reason] += 1
        if previous_share_side and after_share_side and previous_share_side != after_share_side and first_share_flip is None:
            first_share_flip = {"eventMs": order["eventMs"], "scheduledSecondsLeft": order["scheduledSecondsLeft"], "to": after_share_side}
        if previous_capital_side and after_capital_side and previous_capital_side != after_capital_side and first_capital_flip is None:
            first_capital_flip = {"eventMs": order["eventMs"], "scheduledSecondsLeft": order["scheduledSecondsLeft"], "to": after_capital_side}
        previous_share_side = after_share_side
        previous_capital_side = after_capital_side

    maker_orders = [x for x in orders if x["role"] == "MAKER"]
    taker_orders = [x for x in orders if x["role"] == "TAKER"]
    maker_shares = sum(x["shares"] for x in maker_orders)
    taker_shares = sum(x["shares"] for x in taker_orders)
    maker_notional = sum(x["notionalUsdt"] for x in maker_orders)
    taker_notional = sum(x["notionalUsdt"] for x in taker_orders)
    first = orders[0] if orders else None
    last = orders[-1] if orders else None
    return {
        "marketId": int(events[0]["market_id"]) if events else None,
        "firstEventMs": first["eventMs"] if first else None,
        "lastEventMs": last["eventMs"] if last else None,
        "changePoints": {
            "firstMaker": next(({"eventMs": x["eventMs"], "scheduledSecondsLeft": x["scheduledSecondsLeft"]} for x in maker_orders), None),
            "firstTaker": next(({"eventMs": x["eventMs"], "scheduledSecondsLeft": x["scheduledSecondsLeft"]} for x in taker_orders), None),
            "firstShareConvictionFlip": first_share_flip,
            "firstCapitalConvictionFlip": first_capital_flip,
        },
        "allocation": {
            "makerOrders": len(maker_orders), "takerOrders": len(taker_orders),
            "makerShares": maker_shares, "takerShares": taker_shares,
            "makerNotionalUsdt": maker_notional, "takerNotionalUsdt": taker_notional,
            "makerToTakerSharesRatio": _ratio(maker_shares, taker_shares),
            "makerToTakerCapitalRatio": _ratio(maker_notional, taker_notional),
            "finalShareConvictionSide": _side(up_shares, down_shares),
            "finalCapitalConvictionSide": _side(up_cost, down_cost),
            "finalUpShares": up_shares, "finalDownShares": down_shares,
            "finalUpCostUsdt": up_cost, "finalDownCostUsdt": down_cost,
        },
        "reasonCounts": dict(reason_counts),
        "officialResult": settled,
        "orders": orders,
    }


def _cohort_profile(markets: list[dict[str, Any]]) -> dict[str, Any]:
    maker_notional = sum(float(x["allocation"]["makerNotionalUsdt"]) for x in markets)
    taker_notional = sum(float(x["allocation"]["takerNotionalUsdt"]) for x in markets)
    first_takers = [x["changePoints"]["firstTaker"]["scheduledSecondsLeft"] for x in markets if x["changePoints"]["firstTaker"]]
    return {
        "markets": len(markets),
        "makerNotionalUsdt": maker_notional,
        "takerNotionalUsdt": taker_notional,
        "makerToTakerCapitalRatio": _ratio(maker_notional, taker_notional),
        "marketsWithTaker": len(first_takers),
        "medianFirstTakerSecondsLeft": sorted(first_takers)[len(first_takers) // 2] if first_takers else None,
    }


def build_report(db_path: Path) -> dict[str, Any]:
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    events = [dict(row) for row in db.execute(
        "SELECT * FROM wallet_shadow_target_events ORDER BY market_id,event_ms,leg_id"
    )]
    settled = {int(row["market_id"]): dict(row) for row in db.execute(
        "SELECT * FROM wallet_shadow_target_market_results ORDER BY resolved_at_ms"
    )}
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[int(event["market_id"])].append(event)
    markets = [_analyse_market(grouped[market_id], settled.get(market_id)) for market_id in sorted(grouped)]
    markets.sort(key=lambda item: item["firstEventMs"] or 0)
    split = len(markets) // 2
    timing: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    for market in markets:
        reasons.update(market["reasonCounts"])
        timing.update(order["timingBucket"] for order in market["orders"])
    settled_rows = [x for x in markets if x["officialResult"]]
    pnl = sum(float(x["officialResult"]["net_pnl_usdt"]) for x in settled_rows)
    cost = sum(float(x["officialResult"]["buy_notional_usdt"]) for x in settled_rows)
    wins = sum(x["officialResult"]["status"] == "WIN" for x in settled_rows)
    db.close()
    return {
        "generatedAt": datetime.now().astimezone().isoformat(),
        "scope": {
            "source": "retained wallet_shadow_target_events plus official target settlement accounting",
            "historicalBackfill": "target accounting only; excluded from every forward strategy cohort",
            "causalityWarning": "inferredReason describes observable structural evidence, not proven private intent or proof of a Spot-vs-strike implementation",
        },
        "performance": {
            "settledMarkets": len(settled_rows), "wins": wins,
            "losses": sum(x["officialResult"]["status"] == "LOSS" for x in settled_rows),
            "flats": sum(x["officialResult"]["status"] == "FLAT" for x in settled_rows),
            "winRate": wins / len(settled_rows) if settled_rows else None,
            "buyNotionalUsdt": cost, "netPnlUsdt": pnl, "netRoi": pnl / cost if cost else None,
        },
        "behavior": {
            "markets": len(markets), "timingBuckets": dict(timing), "inferredReasonCounts": dict(reasons),
            "regimeComparison": {
                "earlierHalf": _cohort_profile(markets[:split]),
                "laterHalf": _cohort_profile(markets[split:]),
                "boundaryMarketId": markets[split]["marketId"] if split < len(markets) else None,
                "boundaryFirstEventMs": markets[split]["firstEventMs"] if split < len(markets) else None,
                "interpretation": "A descriptive before/after fingerprint. It flags when allocation/timing changed but does not by itself identify the external cause.",
            },
        },
        "markets": list(reversed(markets)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconstruct target wallet timing, allocation, change points, official W/L and PnL")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build_report(args.db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "performance": report["performance"], "behavior": report["behavior"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
