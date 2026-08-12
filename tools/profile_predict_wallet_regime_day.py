from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from profile_predict_wallet_fair_value_execution_v2_impl import (  # noqa: E402
    TrajectoryIndex, api_get, extract_role_fill_events, fetch_signer_matches_window,
    finite, norm_address, parse_assets, parse_durations, reconstruct_parents,
)


def norm_side(value: Any) -> str | None:
    text = str(value or "").upper().strip()
    if text in {"UP", "YES_UP"} or text.startswith("UP "):
        return "UP"
    if text in {"DOWN", "YES_DOWN"} or text.startswith("DOWN "):
        return "DOWN"
    return None


def winner_from_market(data: dict[str, Any]) -> tuple[str | None, str]:
    outcomes = data.get("outcomes") if isinstance(data.get("outcomes"), list) else []
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        status = str(outcome.get("status") or "").upper()
        is_winner = outcome.get("isWinner") is True or outcome.get("won") is True or status == "WON"
        if is_winner:
            side = norm_side(outcome.get("name") or outcome.get("outcome"))
            if side:
                return side, "outcomes.status"
    resolution = data.get("resolution") if isinstance(data.get("resolution"), dict) else {}
    if str(resolution.get("status") or "").upper() == "WON":
        side = norm_side(resolution.get("name"))
        if side:
            return side, "resolution.name"
    variant = data.get("variantData") if isinstance(data.get("variantData"), dict) else {}
    start = finite(variant.get("startPrice"))
    end = finite(variant.get("endPrice"))
    if start is not None and end is not None and start > 0 and abs(end - start) > 1e-15:
        return ("UP" if end > start else "DOWN"), "variantData.startPrice/endPrice"
    return None, "UNKNOWN"


def market_price_meta(data: dict[str, Any]) -> dict[str, Any]:
    variant = data.get("variantData") if isinstance(data.get("variantData"), dict) else {}
    start = finite(variant.get("startPrice"))
    end = finite(variant.get("endPrice"))
    move_bps = ((end - start) / start * 10000.0) if start is not None and end is not None and start > 0 else None
    return {
        "startPrice": start,
        "endPrice": end,
        "finalMoveBps": move_bps,
        "absFinalMoveBps": abs(move_bps) if move_bps is not None else None,
    }


def fetch_market_details(ids: list[int], api_key: str, delay_ms: int = 20) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    failures = []
    for index, market_id in enumerate(ids, 1):
        try:
            payload = api_get(f"/v1/markets/{market_id}", {}, api_key)
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            out[market_id] = data
        except Exception as exc:
            failures.append({"marketId": market_id, "error": f"{type(exc).__name__}: {exc}"})
        if delay_ms > 0 and index < len(ids):
            time.sleep(delay_ms / 1000.0)
    return out, {"requested": len(ids), "received": len(out), "failures": failures[:50]}


def direction(probability: float | None, deadband: float) -> str | None:
    if probability is None:
        return None
    if probability >= 0.5 + deadband:
        return "UP"
    if probability <= 0.5 - deadband:
        return "DOWN"
    return None


def market_observer_snapshot(
    index: TrajectoryIndex,
    asset: str,
    bucket: int,
    duration: int,
    seconds_before_end: float,
    max_age_ms: int,
    deadband: float,
) -> dict[str, Any] | None:
    end_ms = (bucket + duration * 60) * 1000
    target_ms = int(end_ms - seconds_before_end * 1000)
    sample = index.at_or_before(asset, bucket, target_ms, max_age_ms)
    if not sample:
        return None
    poly_up = finite(sample.get("poly_up_mid"))
    binance_up = finite(sample.get("binance_up_mid"))
    poly_direction = direction(poly_up, deadband)
    binance_direction = direction(binance_up, deadband)
    return {
        "sampledAtMs": int(sample["sampled_at_ms"]),
        "secondsBeforeEnd": seconds_before_end,
        "polyUpMid": poly_up,
        "binanceUpMid": binance_up,
        "polyDirection": poly_direction,
        "binancePredictionDirection": binance_direction,
        "directionsDisagree": poly_direction is not None and binance_direction is not None and poly_direction != binance_direction,
        "polyConfidence": abs(poly_up - 0.5) if poly_up is not None else None,
        "binancePredictionConfidence": abs(binance_up - 0.5) if binance_up is not None else None,
        "polyMinusBinanceUp": poly_up - binance_up if poly_up is not None and binance_up is not None else None,
    }


def summarize_markets(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    resolved = [row for row in rows if row.get("winner") in {"UP", "DOWN"}]

    def share_true(values: list[Any]) -> float | None:
        usable = [value for value in values if value is not None]
        return sum(value is True for value in usable) / len(usable) if usable else None

    parent_hits: list[bool] = []
    total_buy_cost = 0.0
    winning_buy_cost = 0.0
    buy_only_pnl = 0.0
    for row in resolved:
        winner = row["winner"]
        for parent in row.get("buyParents", []):
            qty = finite(parent.get("shares")) or 0.0
            price = finite(parent.get("price")) or 0.0
            cost = finite(parent.get("costUsdtApprox")) or qty * price
            hit = parent.get("side") == winner
            parent_hits.append(hit)
            total_buy_cost += cost
            if hit:
                winning_buy_cost += cost
                buy_only_pnl += qty * (1.0 - price)
            else:
                buy_only_pnl -= cost

    def signal_hit(offset: str, field: str) -> float | None:
        values = []
        for row in resolved:
            snap = row.get("observer", {}).get(offset)
            values.append(snap.get(field) if isinstance(snap, dict) else None)
        return share_true(values)

    return {
        "label": label,
        "markets": len(rows),
        "resolvedMarkets": len(resolved),
        "target": {
            "buyParents": len(parent_hits),
            "buyParentHitShare": sum(parent_hits) / len(parent_hits) if parent_hits else None,
            "winningSideShareOfBuyCost": winning_buy_cost / total_buy_cost if total_buy_cost else None,
            "buyOnlyHoldToResolutionPnlApprox": buy_only_pnl,
            "buyCostTiltMarketHitShare": share_true([row.get("buyCostTiltHit") for row in resolved]),
            "netShareTiltMarketHitShare": share_true([row.get("netShareTiltHit") for row in resolved]),
            "netFlowSettlementPnlApprox": sum(finite(row.get("netFlowSettlementPnlApprox")) or 0.0 for row in resolved),
        },
        "signals": {
            "polyT20HitShare": signal_hit("20s", "polyHit"),
            "binancePredictionT20HitShare": signal_hit("20s", "binancePredictionHit"),
            "polyT5HitShare": signal_hit("5s", "polyHit"),
            "binancePredictionT5HitShare": signal_hit("5s", "binancePredictionHit"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only day/regime discrimination test for a Predict wallet. "
            "Compares wallet hit/PnL with Poly and Binance Prediction on a chosen BTC 5m date."
        )
    )
    parser.add_argument("--wallet", default="0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03")
    parser.add_argument("--date", required=True, help="Local calendar date, YYYY-MM-DD")
    parser.add_argument("--date-tz", default="Asia/Taipei")
    parser.add_argument("--assets", default="BTC")
    parser.add_argument("--durations", default="5")
    parser.add_argument("--observer-db", default="data/multi_prediction_observer.db")
    parser.add_argument("--pages", type=int, default=1200)
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--max-sample-age-ms", type=int, default=3000)
    parser.add_argument("--signal-deadband", type=float, default=0.02)
    parser.add_argument("--near-strike-final-bps", type=float, default=1.0)
    parser.add_argument("--poly-strong-distance", type=float, default=0.10)
    parser.add_argument("--binance-pinned-distance", type=float, default=0.05)
    parser.add_argument("--market-detail-delay-ms", type=int, default=20)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    wallet = norm_address(args.wallet)
    assets = parse_assets(args.assets)
    durations = parse_durations(args.durations)
    tz = ZoneInfo(args.date_tz)
    if not wallet:
        raise SystemExit("--wallet must be a 0x address")

    local_start = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=tz)
    local_end = local_start + timedelta(days=1)
    start = local_start.astimezone(timezone.utc)
    end = local_end.astimezone(timezone.utc)
    api_key = str(os.environ.get("PREDICT_FUN_API_KEY") or "").strip()
    if not api_key:
        raise SystemExit("PREDICT_FUN_API_KEY is required")

    maker_matches, maker_cov = fetch_signer_matches_window(
        wallet=wallet, is_maker=True, api_key=api_key, start=start, end=end,
        pages=args.pages, page_size=args.page_size,
    )
    taker_matches, taker_cov = fetch_signer_matches_window(
        wallet=wallet, is_maker=False, api_key=api_key, start=start, end=end,
        pages=args.pages, page_size=args.page_size,
    )
    maker_fills, maker_parse = extract_role_fill_events(maker_matches, wallet, assets, durations, "MAKER", bid_only=False)
    taker_fills, taker_parse = extract_role_fill_events(taker_matches, wallet, assets, durations, "TAKER", bid_only=False)
    maker_parents = reconstruct_parents(maker_fills, "MAKER")
    taker_parents = reconstruct_parents(taker_fills, "TAKER")
    parents = maker_parents + taker_parents

    market_ids = sorted({int(row["marketId"]) for row in parents if int(row.get("marketId") or 0) > 0})
    details, detail_cov = fetch_market_details(market_ids, api_key, max(0, args.market_detail_delay_ms))

    observer_note = None
    index: TrajectoryIndex | None = None
    try:
        index = TrajectoryIndex(Path(args.observer_db), assets)
    except SystemExit as exc:
        observer_note = str(exc)

    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for parent in parents:
        by_market[int(parent["marketId"])].append(parent)

    market_rows = []
    for market_id, plist in sorted(by_market.items()):
        plist.sort(key=lambda row: (int(row["eventMs"]), str(row["role"]), str(row.get("orderHash") or "")))
        first = plist[0]
        data = details.get(market_id, {})
        winner, winner_source = winner_from_market(data)
        price_meta = market_price_meta(data)
        buys = [row for row in plist if str(row.get("quoteType")) == "BID"]
        sells = [row for row in plist if str(row.get("quoteType")) == "ASK"]
        buy_cost = {side: sum((finite(row.get("costUsdtApprox")) or 0.0) for row in buys if row.get("side") == side) for side in ("UP", "DOWN")}
        buy_shares = {side: sum((finite(row.get("shares")) or 0.0) for row in buys if row.get("side") == side) for side in ("UP", "DOWN")}
        sell_proceeds = {side: sum((finite(row.get("costUsdtApprox")) or 0.0) for row in sells if row.get("side") == side) for side in ("UP", "DOWN")}
        sell_shares = {side: sum((finite(row.get("shares")) or 0.0) for row in sells if row.get("side") == side) for side in ("UP", "DOWN")}
        net_shares = {side: buy_shares[side] - sell_shares[side] for side in ("UP", "DOWN")}
        buy_cost_tilt = "UP" if buy_cost["UP"] > buy_cost["DOWN"] else "DOWN" if buy_cost["DOWN"] > buy_cost["UP"] else None
        net_share_tilt = "UP" if net_shares["UP"] > net_shares["DOWN"] else "DOWN" if net_shares["DOWN"] > net_shares["UP"] else None
        cashflow = sum(sell_proceeds.values()) - sum(buy_cost.values())
        net_flow_pnl = cashflow + (net_shares.get(winner, 0.0) if winner else 0.0)

        observer = {}
        if index is not None:
            for seconds in (60.0, 20.0, 5.0, 1.0):
                snap = market_observer_snapshot(
                    index, str(first["asset"]), int(first["marketBucket"]), int(first["durationMinutes"]),
                    seconds, max(100, args.max_sample_age_ms), max(0.0, min(0.49, args.signal_deadband)),
                )
                if snap and winner:
                    snap["polyHit"] = snap.get("polyDirection") == winner if snap.get("polyDirection") else None
                    snap["binancePredictionHit"] = snap.get("binancePredictionDirection") == winner if snap.get("binancePredictionDirection") else None
                observer[f"{seconds:g}s"] = snap

        t20 = observer.get("20s") if isinstance(observer.get("20s"), dict) else {}
        poly_conf = finite(t20.get("polyConfidence"))
        binance_conf = finite(t20.get("binancePredictionConfidence"))
        proxy_pinned = (
            poly_conf is not None and binance_conf is not None
            and poly_conf >= args.poly_strong_distance
            and binance_conf <= args.binance_pinned_distance
        )
        market_rows.append({
            "marketId": market_id,
            "asset": first["asset"],
            "durationMinutes": first["durationMinutes"],
            "marketBucket": first["marketBucket"],
            "marketTitle": first["marketTitle"],
            "winner": winner,
            "winnerSource": winner_source,
            **price_meta,
            "nearStrikeFinal": price_meta["absFinalMoveBps"] is not None and price_meta["absFinalMoveBps"] <= args.near_strike_final_bps,
            "buyParents": buys,
            "sellParents": sells,
            "buyCost": buy_cost,
            "buyShares": buy_shares,
            "sellProceeds": sell_proceeds,
            "sellShares": sell_shares,
            "netShares": net_shares,
            "buyCostTiltSide": buy_cost_tilt,
            "buyCostTiltHit": buy_cost_tilt == winner if buy_cost_tilt and winner else None,
            "netShareTiltSide": net_share_tilt,
            "netShareTiltHit": net_share_tilt == winner if net_share_tilt and winner else None,
            "netFlowSettlementPnlApprox": net_flow_pnl if winner else None,
            "observer": observer,
            "t20PolyBinanceDisagree": bool(t20.get("directionsDisagree")) if t20 else False,
            "t20ProxyPinnedPolyStrong": proxy_pinned,
        })

    observer_coverage = None
    if index is not None:
        observer_coverage = {
            asset: {
                **coverage,
                "minUtc": datetime.fromtimestamp(coverage["minMs"] / 1000, tz=timezone.utc).isoformat(),
                "maxUtc": datetime.fromtimestamp(coverage["maxMs"] / 1000, tz=timezone.utc).isoformat(),
            }
            for asset, coverage in index.coverage_by_asset.items()
        }
        index.close()

    slices = {
        "all": market_rows,
        "nearStrikeFinal": [row for row in market_rows if row["nearStrikeFinal"]],
        "t20PolyBinanceDisagree": [row for row in market_rows if row["t20PolyBinanceDisagree"]],
        "t20ProxyPinnedPolyStrong": [row for row in market_rows if row["t20ProxyPinnedPolyStrong"]],
        "nearStrikeAndT20PolyBinanceDisagree": [row for row in market_rows if row["nearStrikeFinal"] and row["t20PolyBinanceDisagree"]],
        "nearStrikeAndT20ProxyPinnedPolyStrong": [row for row in market_rows if row["nearStrikeFinal"] and row["t20ProxyPinnedPolyStrong"]],
    }
    slice_summaries = {name: summarize_markets(rows, name) for name, rows in slices.items()}

    report = {
        "summary": {
            "version": "PREDICT_WALLET_REGIME_DAY_V1",
            "wallet": wallet,
            "date": args.date,
            "dateTimezone": args.date_tz,
            "windowUtc": {"start": start.isoformat(), "end": end.isoformat()},
            "filter": f"assets={','.join(sorted(assets))}; durations={','.join(map(str, sorted(durations)))}m",
            "makerFetchCoverage": maker_cov,
            "takerFetchCoverage": taker_cov,
            "makerParseCounts": maker_parse,
            "takerParseCounts": taker_parse,
            "makerParents": len(maker_parents),
            "takerParents": len(taker_parents),
            "markets": len(market_rows),
            "marketDetailCoverage": detail_cov,
            "observerDb": args.observer_db,
            "observerNote": observer_note,
            "observerCoverageByAsset": observer_coverage,
            "thresholds": {
                "nearStrikeFinalBps": args.near_strike_final_bps,
                "signalDeadband": args.signal_deadband,
                "polyStrongDistanceFrom50": args.poly_strong_distance,
                "binancePinnedDistanceFrom50": args.binance_pinned_distance,
            },
            "slices": slice_summaries,
        },
        "interpretation": {
            "ordinarySignalHypothesis": (
                "If target hit/PnL collapses on the Aug-9 stress slice similarly to Binance Prediction while Poly stays accurate, "
                "that supports an ordinary-market/underlying signal model."
            ),
            "polyLikeSignalHypothesis": (
                "If target directional tilt remains accurate/profitable specifically when Poly and Binance Prediction disagree "
                "or the proxy-pinned slice is active, that supports a Poly-like/faster-source signal. It does not prove direct "
                "Polymarket usage because both can share another upstream source."
            ),
            "pnlCaveat": (
                "netFlowSettlementPnlApprox is reconstructed from public fills as sells minus buys plus winning net shares. "
                "MINT mechanics, starting inventory, rebates/fees, and missing/truncated history can make it differ from account PnL."
            ),
            "pinCaveat": (
                "nearStrikeFinal uses final start/end price distance, not an intramarket path. t20ProxyPinnedPolyStrong is a "
                "prediction-proxy regime, not proof the underlying BTC price was physically pinned at strike."
            ),
        },
        "markets": market_rows,
    }
    output = Path(args.output or f"data/{wallet[2:8]}_{args.date}_regime_day.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    print(f"wrote {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
