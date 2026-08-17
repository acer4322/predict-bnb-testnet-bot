from __future__ import annotations

import argparse
import json
import os
import statistics
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
    TrajectoryIndex,
    api_get,
    extract_role_fill_events,
    fetch_signer_matches_window,
    finite,
    norm_address,
    parse_assets,
    parse_durations,
    reconstruct_parents,
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


def fetch_market_details(
    ids: list[int], api_key: str, delay_ms: int = 20
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
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
        "directionsDisagree": (
            poly_direction is not None
            and binance_direction is not None
            and poly_direction != binance_direction
        ),
        "polyConfidence": abs(poly_up - 0.5) if poly_up is not None else None,
        "binancePredictionConfidence": abs(binance_up - 0.5) if binance_up is not None else None,
        "polyMinusBinanceUp": poly_up - binance_up if poly_up is not None and binance_up is not None else None,
    }


def parse_clock(value: str) -> timedelta:
    text = str(value or "").strip()
    if text == "24:00" or text == "24:00:00":
        return timedelta(days=1)
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
            return timedelta(hours=parsed.hour, minutes=parsed.minute, seconds=parsed.second)
        except ValueError:
            continue
    raise ValueError(f"invalid clock time {value!r}; expected HH:MM or HH:MM:SS")


def resolve_local_window(
    date_text: str,
    tz_name: str,
    start_time: str,
    end_time: str,
) -> tuple[datetime, datetime, datetime, datetime]:
    tz = ZoneInfo(tz_name)
    day = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=tz)
    local_start = day + parse_clock(start_time)
    end_delta = parse_clock(end_time)
    local_end = day + end_delta
    if local_end <= local_start:
        local_end += timedelta(days=1)
    return local_start, local_end, local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def parse_float_list(value: str, *, positive: bool = True) -> tuple[float, ...]:
    out = []
    for piece in str(value or "").split(","):
        piece = piece.strip()
        if not piece:
            continue
        number = float(piece)
        if positive and number <= 0:
            continue
        out.append(number)
    return tuple(sorted(set(out), reverse=True))


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _share_true(values: list[Any]) -> float | None:
    usable = [value for value in values if value is not None]
    return sum(value is True for value in usable) / len(usable) if usable else None


def aggregate_flow(parents: list[dict[str, Any]], winner: str | None) -> dict[str, Any]:
    buys = [row for row in parents if str(row.get("quoteType")) == "BID"]
    sells = [row for row in parents if str(row.get("quoteType")) == "ASK"]
    buy_cost = {
        side: sum((finite(row.get("costUsdtApprox")) or 0.0) for row in buys if row.get("side") == side)
        for side in ("UP", "DOWN")
    }
    buy_shares = {
        side: sum((finite(row.get("shares")) or 0.0) for row in buys if row.get("side") == side)
        for side in ("UP", "DOWN")
    }
    sell_proceeds = {
        side: sum((finite(row.get("costUsdtApprox")) or 0.0) for row in sells if row.get("side") == side)
        for side in ("UP", "DOWN")
    }
    sell_shares = {
        side: sum((finite(row.get("shares")) or 0.0) for row in sells if row.get("side") == side)
        for side in ("UP", "DOWN")
    }
    net_shares = {side: buy_shares[side] - sell_shares[side] for side in ("UP", "DOWN")}
    buy_cost_tilt = (
        "UP" if buy_cost["UP"] > buy_cost["DOWN"]
        else "DOWN" if buy_cost["DOWN"] > buy_cost["UP"]
        else None
    )
    net_share_tilt = (
        "UP" if net_shares["UP"] > net_shares["DOWN"]
        else "DOWN" if net_shares["DOWN"] > net_shares["UP"]
        else None
    )
    total_buy_cost = buy_cost["UP"] + buy_cost["DOWN"]
    total_abs_net_shares = abs(net_shares["UP"]) + abs(net_shares["DOWN"])
    cashflow = sum(sell_proceeds.values()) - sum(buy_cost.values())
    settlement_pnl = cashflow + (net_shares.get(winner, 0.0) if winner else 0.0)
    return {
        "parents": len(parents),
        "buyParents": len(buys),
        "sellParents": len(sells),
        "buyCost": buy_cost,
        "buyShares": buy_shares,
        "sellProceeds": sell_proceeds,
        "sellShares": sell_shares,
        "netShares": net_shares,
        "buyCostTiltSide": buy_cost_tilt,
        "buyCostTiltHit": buy_cost_tilt == winner if buy_cost_tilt and winner else None,
        "buyCostTiltStrength": (
            abs(buy_cost["UP"] - buy_cost["DOWN"]) / total_buy_cost if total_buy_cost > 0 else None
        ),
        "netShareTiltSide": net_share_tilt,
        "netShareTiltHit": net_share_tilt == winner if net_share_tilt and winner else None,
        "netShareTiltStrength": (
            abs(net_shares["UP"] - net_shares["DOWN"]) / total_abs_net_shares
            if total_abs_net_shares > 0
            else None
        ),
        "settlementPnlApprox": settlement_pnl if winner else None,
    }


def cutoff_snapshot(
    parents: list[dict[str, Any]],
    winner: str | None,
    market_end_ms: int,
    seconds_before_end: float,
) -> dict[str, Any]:
    cutoff_ms = int(market_end_ms - seconds_before_end * 1000)
    eligible = [row for row in parents if int(row.get("eventMs") or 0) <= cutoff_ms]
    maker = [row for row in eligible if row.get("role") == "MAKER"]
    taker = [row for row in eligible if row.get("role") == "TAKER"]
    return {
        "secondsBeforeEnd": seconds_before_end,
        "cutoffMs": cutoff_ms,
        "maker": aggregate_flow(maker, winner),
        "taker": aggregate_flow(taker, winner),
        "combined": aggregate_flow(eligible, winner),
    }


def summarize_cutoffs(rows: list[dict[str, Any]], cutoffs: tuple[float, ...]) -> dict[str, Any]:
    resolved = [row for row in rows if row.get("winner") in {"UP", "DOWN"}]
    out: dict[str, Any] = {}
    for seconds in cutoffs:
        key = f"{seconds:g}s"
        per_market = [row.get("decisionCutoffs", {}).get(key) for row in resolved]
        per_market = [snap for snap in per_market if isinstance(snap, dict)]
        role_summary: dict[str, Any] = {}
        for role in ("maker", "taker", "combined"):
            stats = [snap.get(role) for snap in per_market]
            stats = [item for item in stats if isinstance(item, dict)]
            buy_decisions = [item for item in stats if item.get("buyCostTiltSide") in {"UP", "DOWN"}]
            net_decisions = [item for item in stats if item.get("netShareTiltSide") in {"UP", "DOWN"}]
            buy_strength = [finite(item.get("buyCostTiltStrength")) for item in buy_decisions]
            buy_strength = [value for value in buy_strength if value is not None]
            net_strength = [finite(item.get("netShareTiltStrength")) for item in net_decisions]
            net_strength = [value for value in net_strength if value is not None]
            role_summary[role] = {
                "marketsWithParents": sum(int(item.get("parents") or 0) > 0 for item in stats),
                "buyCostTiltDecisionMarkets": len(buy_decisions),
                "buyCostTiltHitShare": _share_true([item.get("buyCostTiltHit") for item in buy_decisions]),
                "medianBuyCostTiltStrength": _median(buy_strength),
                "netShareTiltDecisionMarkets": len(net_decisions),
                "netShareTiltHitShare": _share_true([item.get("netShareTiltHit") for item in net_decisions]),
                "medianNetShareTiltStrength": _median(net_strength),
                "settlementPnlApprox": sum(finite(item.get("settlementPnlApprox")) or 0.0 for item in stats),
            }
        out[key] = role_summary
    return out


def summarize_markets(rows: list[dict[str, Any]], label: str, cutoffs: tuple[float, ...]) -> dict[str, Any]:
    resolved = [row for row in rows if row.get("winner") in {"UP", "DOWN"}]

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
        return _share_true(values)

    signal_summary: dict[str, Any] = {}
    for seconds in cutoffs:
        key = f"{seconds:g}s"
        signal_summary[key] = {
            "polyHitShare": signal_hit(key, "polyHit"),
            "binancePredictionHitShare": signal_hit(key, "binancePredictionHit"),
        }

    return {
        "label": label,
        "markets": len(rows),
        "resolvedMarkets": len(resolved),
        "target": {
            "buyParents": len(parent_hits),
            "buyParentHitShare": sum(parent_hits) / len(parent_hits) if parent_hits else None,
            "winningSideShareOfBuyCost": winning_buy_cost / total_buy_cost if total_buy_cost else None,
            "buyOnlyHoldToResolutionPnlApprox": buy_only_pnl,
            "buyCostTiltMarketHitShare": _share_true([row.get("buyCostTiltHit") for row in resolved]),
            "netShareTiltMarketHitShare": _share_true([row.get("netShareTiltHit") for row in resolved]),
            "netFlowSettlementPnlApprox": sum(
                finite(row.get("netFlowSettlementPnlApprox")) or 0.0 for row in resolved
            ),
        },
        "decisionCutoffs": summarize_cutoffs(rows, cutoffs),
        "signalsByCutoff": signal_summary,
    }


def threshold_slice_name(threshold: float) -> str:
    text = f"{threshold:g}".replace(".", "p")
    return f"finalAbsMoveLe{text}bps"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only window/regime discrimination test for a Predict wallet. "
            "Focuses on a chosen local-time anomaly window and reconstructs Maker/Taker directional tilt at T-60/T-30/T-20/T-10."
        )
    )
    parser.add_argument("--wallet", default="0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03")
    parser.add_argument("--date", required=True, help="Local calendar date, YYYY-MM-DD")
    parser.add_argument("--date-tz", default="Asia/Taipei")
    parser.add_argument("--start-time", default="00:00", help="Local anomaly-window start, HH:MM[:SS]")
    parser.add_argument("--end-time", default="24:00", help="Local anomaly-window end, HH:MM[:SS]; <= start means next day")
    parser.add_argument("--assets", default="BTC")
    parser.add_argument("--durations", default="5")
    parser.add_argument("--observer-db", default="data/multi_prediction_observer.db")
    parser.add_argument("--pages", type=int, default=1200)
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--max-sample-age-ms", type=int, default=3000)
    parser.add_argument("--signal-deadband", type=float, default=0.02)
    parser.add_argument("--near-strike-final-bps", type=float, default=1.0)
    parser.add_argument("--final-move-thresholds-bps", default="0.02,0.05,0.1,0.5,1.0")
    parser.add_argument("--decision-cutoffs-seconds", default="60,30,20,10")
    parser.add_argument("--poly-strong-distance", type=float, default=0.10)
    parser.add_argument("--binance-pinned-distance", type=float, default=0.05)
    parser.add_argument("--market-detail-delay-ms", type=int, default=20)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    wallet = norm_address(args.wallet)
    assets = parse_assets(args.assets)
    durations = parse_durations(args.durations)
    if not wallet:
        raise SystemExit("--wallet must be a 0x address")
    if not assets or not durations:
        raise SystemExit("--assets and --durations must select at least one supported scope")

    try:
        local_start, local_end, start, end = resolve_local_window(
            args.date, args.date_tz, args.start_time, args.end_time
        )
    except (ValueError, KeyError) as exc:
        raise SystemExit(str(exc)) from exc

    cutoffs = parse_float_list(args.decision_cutoffs_seconds)
    if not cutoffs:
        cutoffs = (60.0, 30.0, 20.0, 10.0)
    final_move_thresholds = tuple(sorted(parse_float_list(args.final_move_thresholds_bps)))
    if not final_move_thresholds:
        final_move_thresholds = (0.02, 0.05, 0.1, 0.5, 1.0)

    api_key = str(os.environ.get("PREDICT_FUN_API_KEY") or "").strip()
    if not api_key:
        raise SystemExit("PREDICT_FUN_API_KEY is required")

    # Fetch through the end of the last selected market so a window ending on a market boundary
    # does not truncate that market's late fills. Markets are selected by market start time below.
    fetch_end = end + timedelta(minutes=max(durations))
    maker_matches, maker_cov = fetch_signer_matches_window(
        wallet=wallet,
        is_maker=True,
        api_key=api_key,
        start=start,
        end=fetch_end,
        pages=args.pages,
        page_size=args.page_size,
    )
    taker_matches, taker_cov = fetch_signer_matches_window(
        wallet=wallet,
        is_maker=False,
        api_key=api_key,
        start=start,
        end=fetch_end,
        pages=args.pages,
        page_size=args.page_size,
    )
    maker_fills, maker_parse = extract_role_fill_events(
        maker_matches, wallet, assets, durations, "MAKER", bid_only=False
    )
    taker_fills, taker_parse = extract_role_fill_events(
        taker_matches, wallet, assets, durations, "TAKER", bid_only=False
    )
    maker_parents_all = reconstruct_parents(maker_fills, "MAKER")
    taker_parents_all = reconstruct_parents(taker_fills, "TAKER")

    start_sec = int(start.timestamp())
    end_sec = int(end.timestamp())

    def market_starts_in_window(row: dict[str, Any]) -> bool:
        bucket = int(row.get("marketBucket") or 0)
        return start_sec <= bucket < end_sec

    maker_parents = [row for row in maker_parents_all if market_starts_in_window(row)]
    taker_parents = [row for row in taker_parents_all if market_starts_in_window(row)]
    parents = maker_parents + taker_parents

    market_ids = sorted({int(row["marketId"]) for row in parents if int(row.get("marketId") or 0) > 0})
    details, detail_cov = fetch_market_details(
        market_ids, api_key, max(0, args.market_detail_delay_ms)
    )

    observer_note = None
    index: TrajectoryIndex | None = None
    try:
        index = TrajectoryIndex(Path(args.observer_db), assets)
    except SystemExit as exc:
        observer_note = str(exc)

    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for parent in parents:
        by_market[int(parent["marketId"])].append(parent)

    observer_offsets = tuple(sorted(set(cutoffs) | {60.0, 20.0, 5.0, 1.0}, reverse=True))
    market_rows = []
    for market_id, plist in sorted(by_market.items()):
        plist.sort(key=lambda row: (int(row["eventMs"]), str(row["role"]), str(row.get("orderHash") or "")))
        first = plist[0]
        duration = int(first["durationMinutes"])
        market_end_ms = (int(first["marketBucket"]) + duration * 60) * 1000
        data = details.get(market_id, {})
        winner, winner_source = winner_from_market(data)
        price_meta = market_price_meta(data)
        full = aggregate_flow(plist, winner)
        buys = [row for row in plist if str(row.get("quoteType")) == "BID"]
        sells = [row for row in plist if str(row.get("quoteType")) == "ASK"]

        observer = {}
        if index is not None:
            for seconds in observer_offsets:
                snap = market_observer_snapshot(
                    index,
                    str(first["asset"]),
                    int(first["marketBucket"]),
                    duration,
                    seconds,
                    max(100, args.max_sample_age_ms),
                    max(0.0, min(0.49, args.signal_deadband)),
                )
                if snap and winner:
                    snap["polyHit"] = snap.get("polyDirection") == winner if snap.get("polyDirection") else None
                    snap["binancePredictionHit"] = (
                        snap.get("binancePredictionDirection") == winner
                        if snap.get("binancePredictionDirection")
                        else None
                    )
                observer[f"{seconds:g}s"] = snap

        t20 = observer.get("20s") if isinstance(observer.get("20s"), dict) else {}
        poly_conf = finite(t20.get("polyConfidence"))
        binance_conf = finite(t20.get("binancePredictionConfidence"))
        proxy_pinned = (
            poly_conf is not None
            and binance_conf is not None
            and poly_conf >= args.poly_strong_distance
            and binance_conf <= args.binance_pinned_distance
        )
        decision_cutoffs = {
            f"{seconds:g}s": cutoff_snapshot(plist, winner, market_end_ms, seconds)
            for seconds in cutoffs
        }
        threshold_flags = {
            f"{threshold:g}": (
                price_meta["absFinalMoveBps"] is not None
                and price_meta["absFinalMoveBps"] <= threshold
            )
            for threshold in final_move_thresholds
        }
        market_start_utc = datetime.fromtimestamp(int(first["marketBucket"]), tz=timezone.utc)
        market_rows.append({
            "marketId": market_id,
            "asset": first["asset"],
            "durationMinutes": duration,
            "marketBucket": first["marketBucket"],
            "marketStartUtc": market_start_utc.isoformat(),
            "marketStartLocal": market_start_utc.astimezone(ZoneInfo(args.date_tz)).isoformat(),
            "marketTitle": first["marketTitle"],
            "winner": winner,
            "winnerSource": winner_source,
            **price_meta,
            "nearStrikeFinal": (
                price_meta["absFinalMoveBps"] is not None
                and price_meta["absFinalMoveBps"] <= args.near_strike_final_bps
            ),
            "finalMoveThresholdFlags": threshold_flags,
            "buyParents": buys,
            "sellParents": sells,
            "buyCost": full["buyCost"],
            "buyShares": full["buyShares"],
            "sellProceeds": full["sellProceeds"],
            "sellShares": full["sellShares"],
            "netShares": full["netShares"],
            "buyCostTiltSide": full["buyCostTiltSide"],
            "buyCostTiltHit": full["buyCostTiltHit"],
            "netShareTiltSide": full["netShareTiltSide"],
            "netShareTiltHit": full["netShareTiltHit"],
            "netFlowSettlementPnlApprox": full["settlementPnlApprox"],
            "decisionCutoffs": decision_cutoffs,
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

    slices: dict[str, list[dict[str, Any]]] = {
        "all": market_rows,
        "nearStrikeFinal": [row for row in market_rows if row["nearStrikeFinal"]],
        "t20PolyBinanceDisagree": [row for row in market_rows if row["t20PolyBinanceDisagree"]],
        "t20ProxyPinnedPolyStrong": [row for row in market_rows if row["t20ProxyPinnedPolyStrong"]],
        "nearStrikeAndT20PolyBinanceDisagree": [
            row for row in market_rows if row["nearStrikeFinal"] and row["t20PolyBinanceDisagree"]
        ],
        "nearStrikeAndT20ProxyPinnedPolyStrong": [
            row for row in market_rows if row["nearStrikeFinal"] and row["t20ProxyPinnedPolyStrong"]
        ],
    }
    for threshold in final_move_thresholds:
        key = f"{threshold:g}"
        slices[threshold_slice_name(threshold)] = [
            row for row in market_rows if row.get("finalMoveThresholdFlags", {}).get(key) is True
        ]

    slice_summaries = {
        name: summarize_markets(rows, name, cutoffs) for name, rows in slices.items()
    }

    report = {
        "summary": {
            "version": "PREDICT_WALLET_REGIME_WINDOW_V2",
            "wallet": wallet,
            "date": args.date,
            "dateTimezone": args.date_tz,
            "localWindow": {
                "start": local_start.isoformat(),
                "end": local_end.isoformat(),
            },
            "marketStartWindowUtc": {"start": start.isoformat(), "end": end.isoformat()},
            "apiFetchWindowUtc": {"start": start.isoformat(), "end": fetch_end.isoformat()},
            "filter": f"assets={','.join(sorted(assets))}; durations={','.join(map(str, sorted(durations)))}m",
            "makerFetchCoverage": maker_cov,
            "takerFetchCoverage": taker_cov,
            "makerParseCounts": maker_parse,
            "takerParseCounts": taker_parse,
            "makerParentsBeforeMarketStartWindowFilter": len(maker_parents_all),
            "takerParentsBeforeMarketStartWindowFilter": len(taker_parents_all),
            "makerParents": len(maker_parents),
            "takerParents": len(taker_parents),
            "markets": len(market_rows),
            "marketDetailCoverage": detail_cov,
            "observerDb": args.observer_db,
            "observerNote": observer_note,
            "observerCoverageByAsset": observer_coverage,
            "decisionCutoffsSeconds": list(cutoffs),
            "finalMoveThresholdsBps": list(final_move_thresholds),
            "thresholds": {
                "nearStrikeFinalBps": args.near_strike_final_bps,
                "signalDeadband": args.signal_deadband,
                "polyStrongDistanceFrom50": args.poly_strong_distance,
                "binancePinnedDistanceFrom50": args.binance_pinned_distance,
            },
            "slices": slice_summaries,
        },
        "interpretation": {
            "windowPurpose": (
                "Use a narrow anomaly window so normal-day markets do not dilute the stress-regime result. "
                "Markets are selected by market start time in the requested local-time window."
            ),
            "decisionCutoffPurpose": (
                "T-60/T-30/T-20/T-10 reconstruct what the wallet's observed Maker, Taker, and combined public-fill inventory "
                "already implied at each cutoff, before later fills can make the final direction look artificially accurate."
            ),
            "ordinarySignalHypothesis": (
                "If the wallet's early directional tilt collapses in the anomaly window while a Poly-like source remains accurate, "
                "that supports an ordinary-market/underlying signal model."
            ),
            "polyLikeSignalHypothesis": (
                "If the wallet's T-60/T-30/T-20 tilt remains accurate specifically in the abnormal/pinned window, especially when "
                "Poly and Binance Prediction disagree, that supports a Poly-like/faster-source signal. It still does not prove "
                "direct Polymarket usage because both can share another upstream source."
            ),
            "pnlCaveat": (
                "settlementPnlApprox is reconstructed from public fills as sells minus buys plus winning net shares. MINT mechanics, "
                "starting inventory, rebates/fees, and missing/truncated history can make it differ from account PnL."
            ),
            "pinCaveat": (
                "finalAbsMove slices use only final start/end distance from strike. They do not prove the underlying stayed pinned "
                "throughout the market; intramarket trajectory data is required for that stronger claim."
            ),
        },
        "markets": market_rows,
    }
    default_suffix = f"{args.date}_{args.start_time.replace(':', '')}-{args.end_time.replace(':', '')}_regime_window"
    output = Path(args.output or f"data/{wallet[2:8]}_{default_suffix}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    print(f"wrote {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
