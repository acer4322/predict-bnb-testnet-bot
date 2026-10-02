from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import statistics
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "predict_wallet_shadow.db"
DEFAULT_REPORT = ROOT / "artifacts" / "wallet_profit_strategy" / "target_maker_batch_pairing_report.json"
DEFAULT_PAIRS = ROOT / "artifacts" / "wallet_profit_strategy" / "target_maker_batch_pairs.csv"
DEFAULT_TRAJECTORY = ROOT / "artifacts" / "wallet_profit_strategy" / "target_maker_inventory_trajectory.csv"
CHECKPOINTS_SECONDS_LEFT = (240, 180, 120, 60, 30, 0)


def _quantile(values: Iterable[float], q: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    index = min(len(ordered) - 1, max(0, math.floor((len(ordered) - 1) * q)))
    return ordered[index]


def _median(values: Iterable[float]) -> float | None:
    rows = [float(value) for value in values]
    return statistics.median(rows) if rows else None


def _weighted_quantile(rows: list[dict[str, Any]], field: str, q: float) -> float | None:
    ordered = sorted(
        (
            (float(row[field]), float(row["pairedShares"]))
            for row in rows
            if row.get(field) is not None and float(row.get("pairedShares") or 0) > 0
        ),
        key=lambda item: item[0],
    )
    total = sum(weight for _, weight in ordered)
    if total <= 0:
        return None
    threshold = q * total
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative + 1e-12 >= threshold:
            return value
    return ordered[-1][0]


def _parents(db: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(
        """SELECT market_id,COALESCE(order_hash,leg_id) parent_id,side,
                  MIN(event_ms) first_event_ms,MAX(event_ms) last_event_ms,
                  SUM(shares) shares,SUM(shares*price) notional_usdt,
                  SUM(shares*price)/SUM(shares) average_price,COUNT(*) fill_legs
             FROM wallet_shadow_target_events
            WHERE role='MAKER' AND quote_type='BID' AND side IN ('UP','DOWN')
              AND shares>0 AND price>0
            GROUP BY market_id,COALESCE(order_hash,leg_id),side
            ORDER BY market_id,first_event_ms,parent_id"""
    )]


def _market_end_times(db: sqlite3.Connection) -> dict[int, int]:
    result: dict[int, int] = {}
    for row in db.execute(
        "SELECT market_id,raw_json FROM wallet_shadow_target_events WHERE role='MAKER' GROUP BY market_id"
    ):
        try:
            raw = json.loads(row["raw_json"])
            value = str(raw["market"]["boostEndsAt"])
            result[int(row["market_id"])] = int(
                datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1_000
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return result


def _winners(db: sqlite3.Connection) -> dict[int, str]:
    return {
        int(row["market_id"]): str(row["winner"])
        for row in db.execute("SELECT market_id,winner FROM wallet_shadow_target_market_results")
        if row["winner"] in {"UP", "DOWN"}
    }


def _group_batches(parents: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[tuple[int, int], dict[str, Any]] = {}
    for parent in parents:
        market_id = int(parent["market_id"])
        at_ms = int(parent["first_event_ms"])
        batch = grouped.setdefault((market_id, at_ms), {
            "marketId": market_id,
            "atMs": at_ms,
            "UP": {"shares": 0.0, "notional": 0.0, "parents": 0},
            "DOWN": {"shares": 0.0, "notional": 0.0, "parents": 0},
        })
        side = str(parent["side"])
        batch[side]["shares"] += float(parent["shares"])
        batch[side]["notional"] += float(parent["notional_usdt"])
        batch[side]["parents"] += 1
    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for batch in grouped.values():
        for side in ("UP", "DOWN"):
            shares = float(batch[side]["shares"])
            batch[side]["price"] = float(batch[side]["notional"]) / shares if shares else None
        result[int(batch["marketId"])].append(batch)
    for rows in result.values():
        rows.sort(key=lambda row: int(row["atMs"]))
    return dict(result)


def _pair_record(
    *, market_id: int, open_side: str, open_at_ms: int, open_price: float,
    close_side: str, close_at_ms: int, close_price: float, shares: float,
    within_batch: bool,
) -> dict[str, Any]:
    price_sum = float(open_price) + float(close_price)
    return {
        "marketId": int(market_id),
        "openSide": open_side,
        "closeSide": close_side,
        "openAtMs": int(open_at_ms),
        "closeAtMs": int(close_at_ms),
        "lagMs": max(0, int(close_at_ms) - int(open_at_ms)),
        "openPrice": float(open_price),
        "closePrice": float(close_price),
        "priceSum": price_sum,
        "pairedShares": float(shares),
        "grossLockedEdgeUsdt": float(shares) * (1.0 - price_sum),
        "withinSameSecondBatch": bool(within_batch),
    }


def pair_net_batches_fifo(
    parents: list[dict[str, Any]], market_end_ms: dict[int, int] | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    market_end_ms = market_end_ms or {}
    by_market = _group_batches(parents)
    pairs: list[dict[str, Any]] = []
    trajectory: list[dict[str, Any]] = []
    for market_id, batches in by_market.items():
        queues: dict[str, deque[dict[str, Any]]] = {"UP": deque(), "DOWN": deque()}
        cumulative = {"UP": 0.0, "DOWN": 0.0}
        cumulative_edge = 0.0
        for batch_index, batch in enumerate(batches):
            at_ms = int(batch["atMs"])
            up_shares = float(batch["UP"]["shares"])
            down_shares = float(batch["DOWN"]["shares"])
            up_price = batch["UP"]["price"]
            down_price = batch["DOWN"]["price"]
            cumulative["UP"] += up_shares
            cumulative["DOWN"] += down_shares
            internal = min(up_shares, down_shares)
            batch_pairs: list[dict[str, Any]] = []
            if internal > 1e-12 and up_price is not None and down_price is not None:
                batch_pairs.append(_pair_record(
                    market_id=market_id, open_side="BATCH", open_at_ms=at_ms,
                    open_price=float(up_price), close_side="BATCH", close_at_ms=at_ms,
                    close_price=float(down_price), shares=internal, within_batch=True,
                ))

            residual = up_shares - down_shares
            residual_side = "UP" if residual > 1e-12 else "DOWN" if residual < -1e-12 else None
            remaining = abs(residual)
            if residual_side:
                price = float(batch[residual_side]["price"])
                opposite = "DOWN" if residual_side == "UP" else "UP"
                while remaining > 1e-12 and queues[opposite]:
                    old = queues[opposite][0]
                    matched = min(remaining, float(old["shares"]))
                    batch_pairs.append(_pair_record(
                        market_id=market_id, open_side=opposite, open_at_ms=int(old["atMs"]),
                        open_price=float(old["price"]), close_side=residual_side, close_at_ms=at_ms,
                        close_price=price, shares=matched, within_batch=False,
                    ))
                    remaining -= matched
                    old["shares"] = float(old["shares"]) - matched
                    if float(old["shares"]) <= 1e-12:
                        queues[opposite].popleft()
                if remaining > 1e-12:
                    queues[residual_side].append({"atMs": at_ms, "price": price, "shares": remaining})

            pairs.extend(batch_pairs)
            cumulative_edge += sum(float(row["grossLockedEdgeUsdt"]) for row in batch_pairs)
            gross = cumulative["UP"] + cumulative["DOWN"]
            delta = cumulative["UP"] - cumulative["DOWN"]
            trajectory.append({
                "marketId": market_id,
                "batchIndex": batch_index,
                "atMs": at_ms,
                "secondsLeft": (
                    (market_end_ms[market_id] - at_ms) / 1_000
                    if market_id in market_end_ms else None
                ),
                "upParents": int(batch["UP"]["parents"]),
                "downParents": int(batch["DOWN"]["parents"]),
                "upShares": up_shares,
                "downShares": down_shares,
                "upAveragePrice": up_price,
                "downAveragePrice": down_price,
                "withinBatchPairedShares": internal,
                "batchPairedShares": sum(float(row["pairedShares"]) for row in batch_pairs),
                "cumulativeUpShares": cumulative["UP"],
                "cumulativeDownShares": cumulative["DOWN"],
                "cumulativeGrossShares": gross,
                "inventoryDeltaShares": delta,
                "inventoryResidualSide": "UP" if delta > 1e-12 else "DOWN" if delta < -1e-12 else None,
                "inventoryImbalanceRatio": abs(delta) / gross if gross else 0.0,
                "pairedCoverage": 1.0 - abs(delta) / gross if gross else None,
                "cumulativeLockedEdgeUsdt": cumulative_edge,
            })
    return pairs, trajectory


def pair_parent_fifo(parents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for parent in parents:
        by_market[int(parent["market_id"])].append(parent)
    result: list[dict[str, Any]] = []
    for market_id, rows in by_market.items():
        rows.sort(key=lambda row: (int(row["first_event_ms"]), str(row["parent_id"])))
        queues: dict[str, deque[dict[str, Any]]] = {"UP": deque(), "DOWN": deque()}
        for parent in rows:
            side = str(parent["side"])
            opposite = "DOWN" if side == "UP" else "UP"
            remaining = float(parent["shares"])
            while remaining > 1e-12 and queues[opposite]:
                old = queues[opposite][0]
                matched = min(remaining, float(old["shares"]))
                result.append(_pair_record(
                    market_id=market_id, open_side=opposite, open_at_ms=int(old["atMs"]),
                    open_price=float(old["price"]), close_side=side,
                    close_at_ms=int(parent["first_event_ms"]), close_price=float(parent["average_price"]),
                    shares=matched, within_batch=int(old["atMs"]) == int(parent["first_event_ms"]),
                ))
                remaining -= matched
                old["shares"] = float(old["shares"]) - matched
                if float(old["shares"]) <= 1e-12:
                    queues[opposite].popleft()
            if remaining > 1e-12:
                queues[side].append({
                    "atMs": int(parent["first_event_ms"]),
                    "price": float(parent["average_price"]),
                    "shares": remaining,
                })
    return result


def _pair_summary(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    shares = sum(float(row["pairedShares"]) for row in pairs)
    edge = sum(float(row["grossLockedEdgeUsdt"]) for row in pairs)
    profitable = sum(float(row["pairedShares"]) for row in pairs if float(row["priceSum"]) < 1.0 - 1e-12)
    at_most_099 = sum(float(row["pairedShares"]) for row in pairs if float(row["priceSum"]) <= 0.99 + 1e-12)
    same_batch = sum(float(row["pairedShares"]) for row in pairs if row["withinSameSecondBatch"])
    under_5s = sum(float(row["pairedShares"]) for row in pairs if float(row["lagMs"]) <= 5_000)
    joint = sum(
        float(row["pairedShares"]) for row in pairs
        if float(row["lagMs"]) <= 5_000 and float(row["priceSum"]) <= 0.99 + 1e-12
    )
    delay_bins = []
    boundaries = (
        ("sameSecond", 0, 0), ("0to1s", 1, 1_000), ("1to5s", 1_001, 5_000),
        ("5to15s", 5_001, 15_000), ("15to30s", 15_001, 30_000),
        ("30to60s", 30_001, 60_000), ("60to120s", 60_001, 120_000),
        ("over120s", 120_001, math.inf),
    )
    for label, lower, upper in boundaries:
        selected = [row for row in pairs if lower <= int(row["lagMs"]) <= upper]
        selected_shares = sum(float(row["pairedShares"]) for row in selected)
        selected_edge = sum(float(row["grossLockedEdgeUsdt"]) for row in selected)
        delay_bins.append({
            "bin": label,
            "pairSegments": len(selected),
            "pairedShares": selected_shares,
            "pairedShareFraction": selected_shares / shares if shares else None,
            "priceSumMedianShareWeighted": _weighted_quantile(selected, "priceSum", 0.5),
            "priceSumAtMost099Share": (
                sum(float(row["pairedShares"]) for row in selected if float(row["priceSum"]) <= 0.99 + 1e-12)
                / selected_shares if selected_shares else None
            ),
            "grossLockedEdgeUsdt": selected_edge,
            "edgePerPairedShare": selected_edge / selected_shares if selected_shares else None,
        })
    return {
        "pairSegments": len(pairs),
        "pairedShares": shares,
        "lagMsShareWeighted": {
            "p10": _weighted_quantile(pairs, "lagMs", 0.10),
            "median": _weighted_quantile(pairs, "lagMs", 0.50),
            "p90": _weighted_quantile(pairs, "lagMs", 0.90),
        },
        "priceSumShareWeighted": {
            "p10": _weighted_quantile(pairs, "priceSum", 0.10),
            "median": _weighted_quantile(pairs, "priceSum", 0.50),
            "p90": _weighted_quantile(pairs, "priceSum", 0.90),
        },
        "sameSecondBatchPairedShare": same_batch / shares if shares else None,
        "pairedWithin5sShare": under_5s / shares if shares else None,
        "priceSumBelow1Share": profitable / shares if shares else None,
        "priceSumAtMost099Share": at_most_099 / shares if shares else None,
        "within5sAndAtMost099Share": joint / shares if shares else None,
        "grossLockedEdgeUsdt": edge,
        "edgePerPairedShare": edge / shares if shares else None,
        "delayBins": delay_bins,
    }


def _trajectory_summary(
    parents: list[dict[str, Any]], trajectory: list[dict[str, Any]],
    market_end_ms: dict[int, int], winners: dict[int, str], pairs: list[dict[str, Any]],
) -> dict[str, Any]:
    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in trajectory:
        by_market[int(row["marketId"])].append(row)
    checkpoints = []
    eligible_markets = sorted(set(by_market) & set(market_end_ms))
    for seconds_left in CHECKPOINTS_SECONDS_LEFT:
        selected = []
        for market_id in eligible_markets:
            cutoff = int(market_end_ms[market_id] - seconds_left * 1_000)
            candidates = [row for row in by_market[market_id] if int(row["atMs"]) <= cutoff]
            if candidates:
                selected.append(candidates[-1])
            else:
                selected.append({
                    "cumulativeGrossShares": 0.0, "inventoryDeltaShares": 0.0,
                    "inventoryImbalanceRatio": 0.0, "pairedCoverage": None,
                })
        active = [row for row in selected if float(row["cumulativeGrossShares"]) > 0]
        checkpoints.append({
            "secondsLeft": seconds_left,
            "markets": len(selected),
            "activeMarkets": len(active),
            "activeMarketShare": len(active) / len(selected) if selected else None,
            "grossSharesMedian": _median(row["cumulativeGrossShares"] for row in active),
            "absoluteResidualSharesMedian": _median(abs(float(row["inventoryDeltaShares"])) for row in active),
            "imbalanceRatioMedian": _median(row["inventoryImbalanceRatio"] for row in active),
            "pairedCoverageMedian": _median(
                row["pairedCoverage"] for row in active if row.get("pairedCoverage") is not None
            ),
            "marketsAtOrBelow10PctImbalance": (
                sum(float(row["inventoryImbalanceRatio"]) <= 0.10 + 1e-12 for row in active) / len(active)
                if active else None
            ),
        })

    final_rows = [rows[-1] for rows in by_market.values() if rows]
    correct = 0
    comparable = 0
    for row in final_rows:
        winner = winners.get(int(row["marketId"]))
        side = row.get("inventoryResidualSide")
        if winner and side in {"UP", "DOWN"}:
            comparable += 1
            correct += int(side == winner)

    gross_shares = sum(float(parent["shares"]) for parent in parents)
    paired_shares = sum(float(row["pairedShares"]) for row in pairs)
    market_actual: dict[int, dict[str, float]] = defaultdict(lambda: {"UP": 0.0, "DOWN": 0.0, "cost": 0.0})
    for parent in parents:
        item = market_actual[int(parent["market_id"])]
        item[str(parent["side"])] += float(parent["shares"])
        item["cost"] += float(parent["notional_usdt"])
    actual_pnl = 0.0
    for market_id, item in market_actual.items():
        winner = winners.get(market_id)
        if winner:
            actual_pnl += item[winner] - item["cost"]
    locked_edge = sum(float(row["grossLockedEdgeUsdt"]) for row in pairs)
    return {
        "markets": len(by_market),
        "marketsWithClock": len(eligible_markets),
        "grossMakerShares": gross_shares,
        "pairedShares": paired_shares,
        "pairedShareCoverage": 2 * paired_shares / gross_shares if gross_shares else None,
        "finalPairedCoverageMedian": _median(row["pairedCoverage"] for row in final_rows),
        "finalImbalanceMedian": _median(row["inventoryImbalanceRatio"] for row in final_rows),
        "finalAtOrBelow10PctImbalanceMarketShare": (
            sum(float(row["inventoryImbalanceRatio"]) <= 0.10 + 1e-12 for row in final_rows) / len(final_rows)
            if final_rows else None
        ),
        "finalResidualOfficialDirectionAccuracy": correct / comparable if comparable else None,
        "finalResidualOfficialComparableMarkets": comparable,
        "grossLockedPairEdgeUsdtApprox": locked_edge,
        "actualMakerPnlUsdtFromObservedFills": actual_pnl,
        "unpairedResidualPnlUsdtApprox": actual_pnl - locked_edge,
        "checkpoints": checkpoints,
    }


def build_report(db_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=10.0)
    db.row_factory = sqlite3.Row
    parents = _parents(db)
    ends = _market_end_times(db)
    winners = _winners(db)
    pairs, trajectory = pair_net_batches_fifo(parents, ends)
    parent_fifo = pair_parent_fifo(parents)
    reconstruction_flags = {
        int(row["market_id"]): int(row["historical_reconstruction"])
        for row in db.execute(
            "SELECT market_id,historical_reconstruction FROM wallet_shadow_target_market_results"
        )
    }

    def robustness(selected_parents: list[dict[str, Any]]) -> dict[str, Any]:
        selected_pairs, selected_trajectory = pair_net_batches_fifo(selected_parents, ends)
        pair_stats = _pair_summary(selected_pairs)
        inventory_stats = _trajectory_summary(
            selected_parents, selected_trajectory, ends, winners, selected_pairs
        )
        return {
            "markets": len({int(row["market_id"]) for row in selected_parents}),
            "parents": len(selected_parents),
            "lagMedianMsShareWeighted": pair_stats["lagMsShareWeighted"]["median"],
            "lagP90MsShareWeighted": pair_stats["lagMsShareWeighted"]["p90"],
            "priceSumMedianShareWeighted": pair_stats["priceSumShareWeighted"]["median"],
            "priceSumAtMost099Share": pair_stats["priceSumAtMost099Share"],
            "edgePerPairedShare": pair_stats["edgePerPairedShare"],
            "finalPairedCoverageMedian": inventory_stats["finalPairedCoverageMedian"],
            "finalImbalanceMedian": inventory_stats["finalImbalanceMedian"],
            "actualMakerPnlUsdtFromObservedFills": inventory_stats["actualMakerPnlUsdtFromObservedFills"],
            "unpairedResidualPnlUsdtApprox": inventory_stats["unpairedResidualPnlUsdtApprox"],
        }
    report = {
        "generatedAt": datetime.now().astimezone().isoformat(),
        "scope": {
            "events": "retained target wallet Maker BID parents grouped by first fill second",
            "markets": len({int(row["market_id"]) for row in parents}),
            "parents": len(parents),
            "batches": len(trajectory),
            "observable": "filled parent side, first fill second, filled shares and weighted average fill price",
            "notObservable": "placement/cancel time, unfilled orders, queue position, private generation identity and rebates",
        },
        "method": {
            "primary": "within each market and second, pair simultaneous UP/DOWN shares at batch VWAP; match only the net residual against oldest opposite unmatched inventory (FIFO)",
            "sensitivity": "stable parent-hash FIFO without same-second netting; same-second parent order is not treated as known chronology",
            "causality": "pairs are formed only when the closing fill becomes observable; no future price is used to choose among old lots",
            "interpretationBoundary": "economic inventory pairing proxy, not proof that two fills came from the same private order generation",
        },
        "netBatchFifo": _pair_summary(pairs),
        "parentFifoSensitivity": _pair_summary(parent_fifo),
        "inventoryTrajectory": _trajectory_summary(parents, trajectory, ends, winners, pairs),
        "cohortRobustness": {
            "historicallyReconstructedSettlements": robustness([
                row for row in parents if reconstruction_flags.get(int(row["market_id"])) == 1
            ]),
            "liveObservedSettlements": robustness([
                row for row in parents if reconstruction_flags.get(int(row["market_id"])) == 0
            ]),
            "boundary": "this split checks structural stability only; neither segment is an authorization to tune a deployed forward policy",
        },
        "firstInference": {},
    }
    primary = report["netBatchFifo"]
    sensitivity = report["parentFifoSensitivity"]
    report["firstInference"] = {
        "pairingStableAcrossMethods": {
            "lagMedianDifferenceMs": (
                abs(float(primary["lagMsShareWeighted"]["median"]) - float(sensitivity["lagMsShareWeighted"]["median"]))
                if primary["lagMsShareWeighted"]["median"] is not None
                and sensitivity["lagMsShareWeighted"]["median"] is not None else None
            ),
            "priceSumMedianDifference": (
                abs(float(primary["priceSumShareWeighted"]["median"]) - float(sensitivity["priceSumShareWeighted"]["median"]))
                if primary["priceSumShareWeighted"]["median"] is not None
                and sensitivity["priceSumShareWeighted"]["median"] is not None else None
            ),
        },
        "questions": [
            "Does most inventory pair immediately, or over tens of seconds?",
            "Does slower completion preserve priceSum <= 0.99, suggesting patient inventory rather than emergency chasing?",
            "At what market age does median imbalance converge toward the final approximately 10% residual?",
            "Is observed Maker profit explained by paired locked edge while the residual loses money?",
        ],
    }
    db.close()
    return report, pairs, trajectory


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze target Maker batch pairing and inventory trajectory")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--pairs", type=Path, default=DEFAULT_PAIRS)
    parser.add_argument("--trajectory", type=Path, default=DEFAULT_TRAJECTORY)
    args = parser.parse_args()
    report, pairs, trajectory = build_report(args.db)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(args.pairs, pairs)
    _write_csv(args.trajectory, trajectory)
    print(json.dumps({
        "report": str(args.report),
        "pairsCsv": str(args.pairs),
        "trajectoryCsv": str(args.trajectory),
        "scope": report["scope"],
        "netBatchFifo": report["netBatchFifo"],
        "parentFifoSensitivity": report["parentFifoSensitivity"],
        "inventoryTrajectory": report["inventoryTrajectory"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
