from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WALLET_DB = ROOT / "data" / "predict_wallet_shadow.db"
DEFAULT_SIGNAL_DB = ROOT / "data" / "wallet_taker_signals.db"
DEFAULT_OUTPUT = ROOT / "artifacts" / "wallet_profit_strategy" / "target_maker_strategy_forward_report.json"
SIGNAL_FEATURES = (
    ("direction_score", 0.0),
    ("futures_taker_imbalance_1s", 0.0),
    ("spot_taker_imbalance_250ms", 0.0),
    ("futures_queue_imbalance", 0.0),
    ("predict_up_mid", 0.5),
)


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.floor((len(ordered) - 1) * q)))]


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _wilson(successes: int, total: int) -> list[float] | None:
    if total <= 0:
        return None
    z = 1.95996398454
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [center - half, center + half]


def _parents(db: sqlite3.Connection, deployed_at_ms: int = 0) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(
        """SELECT wallet,market_id,order_hash,side,quote_type,
                  MIN(event_ms) first_event_ms,MAX(event_ms) last_event_ms,
                  SUM(shares) shares,SUM(shares*price) notional_usdt,
                  SUM(shares*price)/SUM(shares) average_price,COUNT(*) fill_legs
             FROM wallet_shadow_target_events
            WHERE role='MAKER' AND event_ms>=? AND side IN ('UP','DOWN')
              AND quote_type='BID' AND order_hash IS NOT NULL
            GROUP BY wallet,market_id,order_hash,side,quote_type
            ORDER BY first_event_ms,order_hash""",
        (deployed_at_ms,),
    )]


def _market_end_times(db: sqlite3.Connection) -> dict[int, int]:
    result: dict[int, int] = {}
    for row in db.execute(
        "SELECT market_id,raw_json FROM wallet_shadow_target_events WHERE role='MAKER' GROUP BY market_id"
    ):
        try:
            raw = json.loads(row["raw_json"])
            value = str(raw["market"]["boostEndsAt"])
            result[int(row["market_id"])] = int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1_000)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return result


def _lifetime_profile(parents: list[dict[str, Any]], market_end_ms: dict[int, int]) -> dict[str, Any]:
    market_ids = sorted({int(row["market_id"]) for row in parents if int(row["market_id"]) in market_end_ms})
    bins: dict[int, dict[int, int]] = {lower: defaultdict(int) for lower in range(0, 300, 15)}
    for parent in parents:
        market_id = int(parent["market_id"])
        if market_id not in market_end_ms:
            continue
        seconds_left = (market_end_ms[market_id] - int(parent["first_event_ms"])) / 1_000
        if 0 <= seconds_left < 300:
            lower = int(seconds_left // 15) * 15
            bins[lower][market_id] += 1

    rows = []
    for lower in reversed(range(0, 300, 15)):
        values = [bins[lower].get(market_id, 0) for market_id in market_ids]
        rows.append({
            "secondsLeftFrom": lower,
            "secondsLeftTo": lower + 15,
            "parents": sum(values),
            "meanParentsPerMarket": sum(values) / len(values) if values else None,
            "medianParentsPerMarket": _median([float(value) for value in values]),
            "activeMarketShare": sum(value > 0 for value in values) / len(values) if values else None,
        })
    prior_30 = sum(row["parents"] for row in rows if 30 <= row["secondsLeftFrom"] < 60)
    final_30 = sum(row["parents"] for row in rows if 0 <= row["secondsLeftFrom"] < 30)
    return {
        "markets": len(market_ids),
        "binSeconds": 15,
        "bins": rows,
        "last30SecondsParents": final_30,
        "prior30SecondsParents": prior_30,
        "last30VsPrior30Change": final_30 / prior_30 - 1 if prior_30 else None,
        "frozenActiveCutoffSecondsLeft": 30,
        "interpretation": "stop new/replacement quotes at 30 seconds: total parent fills fall sharply and fewer than half of markets remain active; isolated late volatility is not treated as broad activity",
    }


def _strict_pre_snapshot(
    db: sqlite3.Connection, market_id: int, event_ms: int, offset_ms: int = 0
) -> dict[str, Any] | None:
    cutoff_ns = (event_ms - offset_ms - 250) * 1_000_000
    floor_ns = (event_ms - offset_ms - 2_000) * 1_000_000
    row = db.execute(
        """SELECT * FROM wallet_taker_signal_snapshots
            WHERE market_id=? AND timestamp_ns BETWEEN ? AND ?
            ORDER BY timestamp_ns DESC LIMIT 1""",
        (market_id, floor_ns, cutoff_ns),
    ).fetchone()
    return dict(row) if row else None


def _pair_opposite_within(
    parents: list[dict[str, Any]], window_ms: int = 5_000
) -> list[dict[str, Any]]:
    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for parent in parents:
        by_market[int(parent["market_id"])].append(parent)
    pairs: list[dict[str, Any]] = []
    for market_id, rows in by_market.items():
        up = [row for row in rows if row["side"] == "UP"]
        down = [row for row in rows if row["side"] == "DOWN"]
        used_down: set[int] = set()
        for up_parent in up:
            candidates = [
                (abs(int(down_parent["first_event_ms"]) - int(up_parent["first_event_ms"])), index, down_parent)
                for index, down_parent in enumerate(down)
                if index not in used_down
                and abs(int(down_parent["first_event_ms"]) - int(up_parent["first_event_ms"])) <= window_ms
            ]
            if not candidates:
                continue
            lag_ms, index, down_parent = min(candidates, key=lambda item: (item[0], item[1]))
            used_down.add(index)
            paired_shares = min(float(up_parent["shares"]), float(down_parent["shares"]))
            price_sum = float(up_parent["average_price"]) + float(down_parent["average_price"])
            pairs.append({
                "marketId": market_id,
                "lagMs": lag_ms,
                "upPrice": float(up_parent["average_price"]),
                "downPrice": float(down_parent["average_price"]),
                "priceSum": price_sum,
                "pairedShares": paired_shares,
                "grossLockedEdgeUsdt": paired_shares * (1.0 - price_sum),
                "bothObservedExactly18": (
                    abs(float(up_parent["shares"]) - 18.0) <= 1e-6
                    and abs(float(down_parent["shares"]) - 18.0) <= 1e-6
                ),
            })
    return pairs


def _structural_profile(parents: list[dict[str, Any]]) -> dict[str, Any]:
    shares = [float(parent["shares"]) for parent in parents]
    prices = [float(parent["average_price"]) for parent in parents]
    legs = [int(parent["fill_legs"]) for parent in parents]
    by_stream: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for parent in parents:
        by_stream[(int(parent["market_id"]), str(parent["side"]))].append(parent)
        by_market[int(parent["market_id"])].append(parent)

    unique_levels = [len({round(float(row["average_price"]), 6) for row in rows}) for rows in by_stream.values()]
    gaps: list[float] = []
    same_price = same_price_within_5s = 0
    transitions = 0
    for rows in by_stream.values():
        rows.sort(key=lambda row: (int(row["first_event_ms"]), str(row["order_hash"])))
        for previous, current in zip(rows, rows[1:]):
            gap = int(current["first_event_ms"]) - int(previous["first_event_ms"])
            gaps.append(gap / 1_000.0)
            transitions += 1
            equal_price = abs(float(current["average_price"]) - float(previous["average_price"])) <= 1e-9
            same_price += int(equal_price)
            same_price_within_5s += int(equal_price and gap <= 5_000)

    imbalance_reducing = directional = 0
    final_pair_coverages: list[float] = []
    same_side_run_lengths: list[int] = []
    for rows in by_market.values():
        rows.sort(key=lambda row: (int(row["first_event_ms"]), str(row["order_hash"])))
        up = down = 0.0
        run_side: str | None = None
        run = 0
        for row in rows:
            before = abs(up - down)
            if row["side"] == "UP":
                up += float(row["shares"])
            else:
                down += float(row["shares"])
            after = abs(up - down)
            if before > 0:
                directional += 1
                imbalance_reducing += int(after < before)
            if row["side"] == run_side:
                run += 1
            else:
                if run:
                    same_side_run_lengths.append(run)
                run_side = str(row["side"])
                run = 1
        if run:
            same_side_run_lengths.append(run)
        total = up + down
        final_pair_coverages.append(2 * min(up, down) / total if total else 0.0)

    pairs = _pair_opposite_within(parents)
    pair_sums = [float(pair["priceSum"]) for pair in pairs]
    pair_lags = [float(pair["lagMs"]) for pair in pairs]
    return {
        "parents": len(parents),
        "markets": len(by_market),
        "marketSideStreams": len(by_stream),
        "observedParentSize": {
            "modeAndMedianShares": 18.0 if shares and _median(shares) == 18.0 else _median(shares),
            "exact18Share": sum(abs(value - 18.0) <= 1e-6 for value in shares) / len(shares) if shares else None,
            "below18Share": sum(value < 18.0 - 1e-6 for value in shares) / len(shares) if shares else None,
            "above18Share": sum(value > 18.0 + 1e-6 for value in shares) / len(shares) if shares else None,
            "multiFillParentShare": sum(value > 1 for value in legs) / len(legs) if legs else None,
            "interpretation": "18 shares behaves like the parent order cap; smaller observed totals can be partial fills, not smaller requested orders",
        },
        "centGrid": {
            "integerCentPriceShare": sum(abs(value * 100 - round(value * 100)) <= 1e-6 for value in prices) / len(prices) if prices else None,
            "uniqueLevelsPerMarketSideMedian": _median([float(value) for value in unique_levels]),
            "uniqueLevelsPerMarketSideP90": _quantile([float(value) for value in unique_levels], 0.9),
        },
        "refillAndRuns": {
            "sameSideParentGapMedianSeconds": _median(gaps),
            "sameSideTransitionsWithin5s": sum(value <= 5 for value in gaps) / len(gaps) if gaps else None,
            "samePriceTransitionShare": same_price / transitions if transitions else None,
            "samePriceWithin5sShare": same_price_within_5s / transitions if transitions else None,
            "sameSideRunLengthMedian": _median([float(value) for value in same_side_run_lengths]),
            "sameSideRunLengthP90": _quantile([float(value) for value in same_side_run_lengths], 0.9),
        },
        "softInventory": {
            "individualParentReducesShareImbalance": imbalance_reducing / directional if directional else None,
            "finalPairedShareCoverageMedian": _median(final_pair_coverages),
            "interpretation": "weak event-by-event balancing plus high final pairing supports pooled soft inventory control, not strict alternating generations",
        },
        "oppositePairProxy5s": {
            "oneToOnePairs": len(pairs),
            "parentCoverage": 2 * len(pairs) / len(parents) if parents else None,
            "lagMedianMs": _median(pair_lags),
            "within1s": sum(value <= 1_000 for value in pair_lags) / len(pair_lags) if pair_lags else None,
            "priceSumMedian": _median(pair_sums),
            "priceSumBelow1Share": sum(value < 1.0 for value in pair_sums) / len(pair_sums) if pair_sums else None,
            "priceSumAtMost099Share": sum(value <= 0.99 + 1e-9 for value in pair_sums) / len(pair_sums) if pair_sums else None,
            "bothObservedExactly18Share": sum(bool(pair["bothObservedExactly18"]) for pair in pairs) / len(pairs) if pairs else None,
            "pairedShares": sum(float(pair["pairedShares"]) for pair in pairs),
            "grossLockedEdgeUsdtApprox": sum(float(pair["grossLockedEdgeUsdt"]) for pair in pairs),
            "caveat": "nearest one-to-one fills are a pairing proxy, not proof that those orders belonged to one private generation",
        },
    }


def _performance(db: sqlite3.Connection) -> dict[str, Any]:
    rows = [dict(row) for row in db.execute(
        "SELECT * FROM wallet_shadow_target_market_results WHERE maker_event_count>0 ORDER BY resolved_at_ms"
    )]

    def summarize(selected: list[dict[str, Any]]) -> dict[str, Any]:
        cost = sum(float(row["maker_notional_usdt"]) for row in selected)
        pnl = sum(float(row["maker_pnl_usdt"]) for row in selected)
        return {
            "markets": len(selected),
            "positiveMarkets": sum(float(row["maker_pnl_usdt"]) > 0 for row in selected),
            "negativeMarkets": sum(float(row["maker_pnl_usdt"]) < 0 for row in selected),
            "makerNotionalUsdt": cost,
            "makerPnlUsdt": pnl,
            "makerRoi": pnl / cost if cost else None,
            "medianMarketPnlUsdt": _median([float(row["maker_pnl_usdt"]) for row in selected]),
        }

    return {
        "allRetainedAccounting": summarize(rows),
        "liveObservedSettlementOnly": summarize([row for row in rows if not bool(row["historical_reconstruction"])]),
        "caveat": "maker PnL allocates settlement payout to observed Maker shares; it excludes unobserved open/cancelled orders, queue position and maker rebates",
    }


def _signal_profile(
    parents: list[dict[str, Any]], signal_db: sqlite3.Connection
) -> dict[str, Any]:
    matched: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for parent in parents:
        snapshot = _strict_pre_snapshot(signal_db, int(parent["market_id"]), int(parent["first_event_ms"]))
        if snapshot:
            matched.append((parent, snapshot))
    features: dict[str, Any] = {}
    for feature, neutral in SIGNAL_FEATURES:
        samples: list[bool] = []
        for parent, snapshot in matched:
            value = snapshot.get(feature)
            if value is None or float(value) == neutral:
                continue
            signal_side = "UP" if float(value) > neutral else "DOWN"
            samples.append(signal_side == parent["side"])
        matches = sum(samples)
        features[feature] = {
            "parents": len(samples),
            "sideMatches": matches,
            "sideMatchRate": matches / len(samples) if samples else None,
            "wilson95": _wilson(matches, len(samples)),
        }

    bid_deltas: list[float] = []
    mid_deltas: list[float] = []
    ages: list[float] = []
    seconds_left: list[float] = []
    for parent, snapshot in matched:
        side = str(parent["side"]).lower()
        price = float(parent["average_price"])
        bid = snapshot.get(f"predict_{side}_bid")
        mid = snapshot.get(f"predict_{side}_mid")
        if bid is not None:
            bid_deltas.append(price - float(bid))
        if mid is not None:
            mid_deltas.append(price - float(mid))
        ages.append((int(parent["first_event_ms"]) * 1_000_000 - int(snapshot["timestamp_ns"])) / 1_000_000)
        if snapshot.get("seconds_left") is not None:
            seconds_left.append(float(snapshot["seconds_left"]))

    def placement(values: list[float]) -> dict[str, Any]:
        return {
            "parents": len(values),
            "median": _median(values),
            "p10": _quantile(values, 0.1),
            "p90": _quantile(values, 0.9),
            "withinOneTick": sum(abs(value) <= 0.011 for value in values) / len(values) if values else None,
            "atOrBelowReference": sum(value <= 0.001 for value in values) / len(values) if values else None,
        }

    return {
        "deploymentBoundaryMs": int(signal_db.execute(
            "SELECT value FROM wallet_taker_signal_meta WHERE key='deployed_at_ms'"
        ).fetchone()[0]),
        "parents": len(parents),
        "matchedParents": len(matched),
        "pairingRate": len(matched) / len(parents) if parents else None,
        "markets": len({int(parent["market_id"]) for parent, _ in matched}),
        "snapshotAgeMs": {
            "median": _median(ages), "p90": _quantile(ages, 0.9), "maximum": max(ages) if ages else None,
        },
        "features": features,
        "makerFillPriceMinusPreEventBid": placement(bid_deltas),
        "makerFillPriceMinusPreEventMid": placement(mid_deltas),
        "secondsLeft": {
            "median": _median(seconds_left), "p10": _quantile(seconds_left, 0.1), "p90": _quantile(seconds_left, 0.9),
        },
        "interpretation": "near-50% side alignment rejects a directional Maker trigger; price placement and later recentering are the stronger mechanism evidence",
    }


def build_report(wallet_db_path: Path, signal_db_path: Path) -> dict[str, Any]:
    wallet_db = sqlite3.connect(wallet_db_path)
    wallet_db.row_factory = sqlite3.Row
    signal_db = sqlite3.connect(signal_db_path)
    signal_db.row_factory = sqlite3.Row
    deployed_at_ms = int(signal_db.execute(
        "SELECT value FROM wallet_taker_signal_meta WHERE key='deployed_at_ms'"
    ).fetchone()[0])
    all_parents = _parents(wallet_db)
    forward_parents = _parents(wallet_db, deployed_at_ms)
    report = {
        "generatedAt": datetime.now().astimezone().isoformat(),
        "scope": {
            "targetRole": "MAKER BID fills",
            "source": "public Predict.fun match fills retained by Wallet Shadow plus strict pre-fill public signal snapshots",
            "observable": "filled parent hash, fill time, filled shares, price, side and official settlement",
            "notObservable": "original placement time, unfilled requested remainder, queue position, cancel/replace events and private fair value",
            "causality": "signal snapshots are at least 250ms before second-resolution first fill and at most 2s old",
        },
        "historicalStructure": _structural_profile(all_parents),
        "marketLifetime": _lifetime_profile(all_parents, _market_end_times(wallet_db)),
        "forwardSignalEvidence": _signal_profile(forward_parents, signal_db),
        "performance": _performance(wallet_db),
        "reconstructedMechanism": {
            "highConfidence": [
                "resting two-sided BID ladders on an integer-cent grid",
                "18-share requested parent cap with smaller public totals mostly representing partial fills",
                "multiple price levels can fill in one second, implying pre-existing ladder depth rather than one decision per fill",
                "soft pooled inventory and complement-price capture rather than strict UP/DOWN alternating generations",
            ],
            "mediumConfidence": [
                "ladder center follows market probability changes while individual Maker side is direction-neutral",
                "Taker is a separate active layer that handles directional conviction and residual risk",
            ],
            "notYetRecoverable": [
                "exact concurrent ladder width and order lifetime",
                "cancel/recenter threshold and whether it uses Predict, Spot, Futures or a shared upstream fair value",
                "queue-aware placement and actual maker rebate economics",
            ],
            "firstForwardCandidate": {
                "name": "MAKER_GRID18_DEPTH_3_7_15",
                "paperOnly": True,
                "status": "FORWARD_DEPLOYED_NO_RESULTS_YET",
                "rule": "quote both outcomes in 18-share one-cent ladder units; compare 3/7/15 levels; require each order >=1 USDT and each paired level <=0.99; batch recenter after >=5s and >=2 ticks; stop new/replacement quotes at 30 seconds left",
                "fillProxy": "only a later observed ask touching a bid that rested at least 250ms; no queue/rebate credit",
            },
        },
    }
    signal_db.close()
    wallet_db.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconstruct target Maker structure and strict pre-fill signal evidence")
    parser.add_argument("--wallet-db", type=Path, default=DEFAULT_WALLET_DB)
    parser.add_argument("--signal-db", type=Path, default=DEFAULT_SIGNAL_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build_report(args.wallet_db, args.signal_db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "historicalStructure": report["historicalStructure"],
        "marketLifetime": report["marketLifetime"],
        "forwardSignalEvidence": report["forwardSignalEvidence"],
        "performance": report["performance"],
        "reconstructedMechanism": report["reconstructedMechanism"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
