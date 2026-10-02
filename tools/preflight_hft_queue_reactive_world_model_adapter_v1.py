from __future__ import annotations

import argparse
import bisect
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.predict_bot.execution_tape_archive_v1 import load_archive
from src.predict_bot.execution_tape_quality_v1 import assess_archive
from tools.hftbacktest_true_match_calibration_v0 import normalize_match

ARCHIVE_DIR = ROOT / "data" / "execution_tape_v1" / "markets"
OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT_DIR / "hft_queue_reactive_world_model_adapter_v1_preregistered.json"
DEFAULT_OUTPUT = OUT_DIR / "hft_queue_reactive_world_model_adapter_v1_report.json"
TRAIN_MARKETS = (1573252, 1574038, 1574352)
VALIDATION_MARKETS = (1574538, 1574737)
PRICE_TOLERANCE = 0.005
TIME_TOLERANCE_MS = 1500
MIN_TRAIN_STATE_ROWS = 3
EPSILON = 1e-9


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(ordered[lo])
    return float(ordered[lo] * (hi - pos) + ordered[hi] * (pos - lo))


def state_base(
    bids: dict[float, float], asks: dict[float, float], timestamp_ms: int, end_ms: int
) -> tuple[int, int, int, float] | None:
    live_bids = [(price, qty) for price, qty in bids.items() if qty > EPSILON]
    live_asks = [(price, qty) for price, qty in asks.items() if qty > EPSILON]
    if not live_bids or not live_asks:
        return None
    best_bid, bid_qty = max(live_bids, key=lambda row: row[0])
    best_ask, ask_qty = min(live_asks, key=lambda row: row[0])
    if best_ask <= best_bid or bid_qty + ask_qty <= EPSILON:
        return None
    seconds_left = max(0.0, (end_ms - timestamp_ms) / 1000.0)
    time_bin = min(5, int(seconds_left // 50.0))
    spread_ticks = min(6, max(1, int(round((best_ask - best_bid) / 0.01))))
    imbalance = (bid_qty - ask_qty) / (bid_qty + ask_qty)
    imbalance_bin = bisect.bisect_right((-0.6, -0.2, 0.2, 0.6), imbalance)
    return time_bin, spread_ticks, imbalance_bin, bid_qty + ask_qty


def total_best_bin(value: float, quantiles: tuple[float, float, float]) -> int:
    return bisect.bisect_right(quantiles, value)


def apply_update(
    bids: dict[float, float], asks: dict[float, float], row: list[Any]
) -> None:
    if int(row[3]) == 1 and row[4] is not None and row[5] is not None:
        bids.clear()
        asks.clear()
        bids.update({float(price): float(qty) for price, qty in (row[4] or {}).items() if float(qty) > EPSILON})
        asks.update({float(price): float(qty) for price, qty in (row[5] or {}).items() if float(qty) > EPSILON})
        return
    changes = row[6] or {}
    for side, book in (("bids", bids), ("asks", asks)):
        for item in changes.get(side, []) or []:
            price, _before, after, _delta = map(float, item)
            if after > EPSILON:
                book[price] = after
            else:
                book.pop(price, None)


def align_trades(
    removals: list[dict[str, Any]],
    removal_index: dict[tuple[str, int], list[int]],
    trades: list[dict[str, Any]],
    *,
    time_shift_ms: int = 0,
    flip_side: bool = False,
    price_shift_ticks: int = 0,
) -> dict[str, float | int | None]:
    remaining_capacity = [float(row["qty"]) for row in removals]
    trade_qty = 0.0
    aligned_trade_qty = 0.0
    trades_with_alignment = 0
    for trade in trades:
        timestamp = int(trade["tsMs"]) + 500 + time_shift_ms
        book_side = "asks" if str(trade["nativeAggressor"]) == "BUY" else "bids"
        if flip_side:
            book_side = "bids" if book_side == "asks" else "asks"
        price_tick = int(round(float(trade["nativeYesPrice"]) * 100))
        if price_shift_ticks:
            price_tick = ((price_tick - 1 + price_shift_ticks) % 99) + 1
        price = price_tick / 100.0
        qty = float(trade["qty"])
        trade_qty += qty
        candidates = []
        low_tick = int(math.floor((price - PRICE_TOLERANCE) * 100))
        high_tick = int(math.ceil((price + PRICE_TOLERANCE) * 100))
        for candidate_tick in range(low_tick, high_tick + 1):
            for index in removal_index.get((book_side, candidate_tick), []):
                if remaining_capacity[index] <= EPSILON:
                    continue
                distance = abs(int(removals[index]["timestamp"]) - timestamp)
                if distance <= TIME_TOLERANCE_MS:
                    candidates.append((distance, index))
        remaining = qty
        assigned = 0.0
        for _distance, index in sorted(candidates):
            if remaining <= EPSILON:
                break
            take = min(remaining, remaining_capacity[index])
            remaining_capacity[index] -= take
            remaining -= take
            assigned += take
        if assigned > EPSILON:
            trades_with_alignment += 1
            aligned_trade_qty += assigned
    return {
        "tradesWithAlignment": trades_with_alignment,
        "tradeCountAlignmentRate": trades_with_alignment / len(trades) if trades else None,
        "alignedTradeQty": aligned_trade_qty,
        "tradeQtyAlignmentRate": aligned_trade_qty / trade_qty if trade_qty > EPSILON else None,
    }


def market_frame(market_id: int) -> dict[str, Any]:
    path = ARCHIVE_DIR / f"{market_id}.json.xz"
    if not path.exists():
        raise RuntimeError(f"missing tape {path}")
    tape = load_archive(path)
    quality = assess_archive(path)
    updates = sorted(tape.get("updates") or [], key=lambda row: (int(row[1]), int(row[0])))
    if not updates:
        raise RuntimeError(f"market {market_id} has no updates")
    first_ms = min(int(row[1]) for row in updates)
    last_ms = max(int(row[1]) for row in updates)
    end_ms = int((tape.get("market") or {}).get("window_end_ms") or last_ms)
    gaps = [float(int(right[1]) - int(left[1])) for left, right in zip(updates, updates[1:]) if int(right[1]) >= int(left[1])]

    bids: dict[float, float] = {}
    asks: dict[float, float] = {}
    state_bases: list[tuple[int, int, int, float]] = []
    removals: list[dict[str, Any]] = []
    positive_events = 0
    negative_events = 0
    positive_qty = 0.0
    negative_qty = 0.0
    delta_updates = 0
    multi_change_updates = 0
    checkpoint_rows = 0

    for row in updates:
        timestamp = int(row[1])
        if int(row[3]) == 1:
            checkpoint_rows += 1
        else:
            changes = row[6] or {}
            items = [
                (side, item)
                for side in ("bids", "asks")
                for item in (changes.get(side, []) or [])
            ]
            if items:
                delta_updates += 1
            if len(items) > 1:
                multi_change_updates += 1
            for side, item in items:
                price, _before, _after, delta = map(float, item)
                if delta > EPSILON:
                    positive_events += 1
                    positive_qty += delta
                elif delta < -EPSILON:
                    qty = -delta
                    negative_events += 1
                    negative_qty += qty
                    removals.append(
                        {
                            "timestamp": timestamp,
                            "bookSide": side,
                            "price": price,
                            "qty": qty,
                        }
                    )
        apply_update(bids, asks, row)
        state = state_base(bids, asks, timestamp, end_ms)
        if state is not None:
            state_bases.append(state)

    normalized_all = []
    for raw in tape.get("matches") or []:
        normalized = normalize_match(raw)
        if normalized is not None:
            normalized_all.append(normalized)
    trades = [
        row
        for row in normalized_all
        if first_ms - TIME_TOLERANCE_MS <= int(row["tsMs"]) + 500 <= last_ms + TIME_TOLERANCE_MS
    ]
    trades.sort(key=lambda row: (int(row["tsMs"]), str(row.get("transactionHash") or "")))

    removal_index: dict[tuple[str, int], list[int]] = {}
    for index, row in enumerate(removals):
        key = (str(row["bookSide"]), int(round(float(row["price"]) * 100)))
        removal_index.setdefault(key, []).append(index)

    trade_qty = sum(float(trade["qty"]) for trade in trades)
    observed = align_trades(removals, removal_index, trades)
    null_time = align_trades(removals, removal_index, trades, time_shift_ms=30_000)
    null_side = align_trades(removals, removal_index, trades, flip_side=True)
    null_price = align_trades(removals, removal_index, trades, price_shift_ticks=17)
    aligned_trade_qty = float(observed["alignedTradeQty"] or 0.0)

    return {
        "marketId": market_id,
        "quality": quality,
        "updates": len(updates),
        "checkpointRows": checkpoint_rows,
        "deltaUpdates": delta_updates,
        "multiChangeUpdates": multi_change_updates,
        "multiChangeUpdateRate": multi_change_updates / delta_updates if delta_updates else None,
        "medianUpdateGapMs": statistics.median(gaps) if gaps else None,
        "p90UpdateGapMs": percentile(gaps, 0.9),
        "netAddEvents": positive_events,
        "netAddQty": positive_qty,
        "netRemovalEvents": negative_events,
        "netRemovalQty": negative_qty,
        "normalizedMatchesAllLifetime": len(normalized_all),
        "normalizedMatchesInWindow": len(trades),
        "inWindowTradeQty": trade_qty,
        "tradesWithRemovalAlignment": observed["tradesWithAlignment"],
        "tradeCountAlignmentRate": observed["tradeCountAlignmentRate"],
        "alignedTradeQty": aligned_trade_qty,
        "tradeQtyAlignmentRate": observed["tradeQtyAlignmentRate"],
        "nullTimeShiftAlignedTradeQty": null_time["alignedTradeQty"],
        "nullTimeShiftTradeQtyAlignmentRate": null_time["tradeQtyAlignmentRate"],
        "nullSideFlipAlignedTradeQty": null_side["alignedTradeQty"],
        "nullSideFlipTradeQtyAlignmentRate": null_side["tradeQtyAlignmentRate"],
        "nullPriceShiftAlignedTradeQty": null_price["alignedTradeQty"],
        "nullPriceShiftTradeQtyAlignmentRate": null_price["tradeQtyAlignmentRate"],
        "netRemovalQtyAttributedToTrades": aligned_trade_qty / negative_qty if negative_qty > EPSILON else None,
        "stateBases": state_bases,
    }


def compact_market(row: dict[str, Any], quantiles: tuple[float, float, float], trained: Counter[tuple[int, int, int, int]]) -> dict[str, Any]:
    keys = [(*base[:3], total_best_bin(base[3], quantiles)) for base in row["stateBases"]]
    covered = sum(trained[key] >= MIN_TRAIN_STATE_ROWS for key in keys)
    return {
        key: value
        for key, value in row.items()
        if key not in {"stateBases", "quality"}
    } | {
        "qualityStatus": row["quality"]["qualityStatus"],
        "stateRows": len(keys),
        "stateCoverage": covered / len(keys) if keys else None,
        "distinctStates": len(set(keys)),
    }


def weighted_rate(rows: list[dict[str, Any]], numerator: str, denominator: str) -> float | None:
    den = sum(float(row[denominator]) for row in rows)
    return sum(float(row[numerator]) for row in rows) / den if den > EPSILON else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    rows = {market_id: market_frame(market_id) for market_id in (*TRAIN_MARKETS, *VALIDATION_MARKETS)}
    train_totals = [base[3] for market_id in TRAIN_MARKETS for base in rows[market_id]["stateBases"]]
    if not train_totals:
        raise RuntimeError("no train state rows")
    quantiles = (
        float(percentile(train_totals, 0.25)),
        float(percentile(train_totals, 0.50)),
        float(percentile(train_totals, 0.75)),
    )
    trained: Counter[tuple[int, int, int, int]] = Counter(
        (*base[:3], total_best_bin(base[3], quantiles))
        for market_id in TRAIN_MARKETS
        for base in rows[market_id]["stateBases"]
    )
    train = [compact_market(rows[market_id], quantiles, trained) for market_id in TRAIN_MARKETS]
    validation = [compact_market(rows[market_id], quantiles, trained) for market_id in VALIDATION_MARKETS]

    validation_state_rows = sum(row["stateRows"] for row in validation)
    validation_covered_rows = sum(row["stateRows"] * float(row["stateCoverage"] or 0.0) for row in validation)
    validation_state_coverage = validation_covered_rows / validation_state_rows if validation_state_rows else None
    validation_trade_alignment = weighted_rate(validation, "alignedTradeQty", "inWindowTradeQty")
    validation_nulls = {
        "timeShift30s": weighted_rate(validation, "nullTimeShiftAlignedTradeQty", "inWindowTradeQty"),
        "sideFlip": weighted_rate(validation, "nullSideFlipAlignedTradeQty", "inWindowTradeQty"),
        "priceShift17Ticks": weighted_rate(validation, "nullPriceShiftAlignedTradeQty", "inWindowTradeQty"),
    }
    strongest_null = max(float(value or 0.0) for value in validation_nulls.values())
    alignment_specificity_lift = (
        validation_trade_alignment - strongest_null
        if validation_trade_alignment is not None
        else None
    )
    all_median_gaps = [float(row["medianUpdateGapMs"]) for row in (*train, *validation) if row["medianUpdateGapMs"] is not None]
    median_gap = statistics.median(all_median_gaps) if all_median_gaps else None

    gates = {
        "depthCadence": median_gap is not None and median_gap <= float(prereg["gates"]["medianDepthUpdateGapMsMax"]),
        "validationStateCoverage": validation_state_coverage is not None and validation_state_coverage >= float(prereg["gates"]["validationStateCoverageMin"]),
        "validationTradeQtyAlignment": validation_trade_alignment is not None and validation_trade_alignment >= float(prereg["gates"]["validationTradeQtyAlignedToNetRemovalMin"]),
        "minimumValidationRows": all(row["stateRows"] >= int(prereg["gates"]["minimumValidationStateRowsPerMarket"]) for row in validation),
        "alignmentSpecificity": alignment_specificity_lift is not None and alignment_specificity_lift >= 0.20,
    }
    state_gates = gates["depthCadence"] and gates["validationStateCoverage"] and gates["minimumValidationRows"]
    if all(gates.values()):
        decision = "KEEP_NET_QR_WORLD_MODEL_PILOT"
    elif state_gates:
        decision = "NEED_REVISED_DATA_CAPTURE"
    else:
        decision = "REJECT_QR_WITH_CURRENT_TAPE"

    report = {
        "version": "HFT_QUEUE_REACTIVE_WORLD_MODEL_ADAPTER_V1_REPORT",
        "researchOnly": True,
        "preregistration": PREREG.name,
        "externalReference": prereg["externalReference"],
        "cohort": prereg["cohort"],
        "directExternalMbp10Compatibility": False,
        "directCompatibilityReason": "Predict Tape V1 records L2 net deltas and second-granular raw matches, not an exchange-sequenced per-order Add/Cancel/Trade stream with order counts per level.",
        "lockedAlignment": prereg["lockedAlignment"],
        "state": {
            **prereg["lockedState"],
            "trainOnlyTotalBestQuartiles": list(quantiles),
            "trainDistinctStates": len(trained),
        },
        "trainMarkets": train,
        "chronologicalValidationMarkets": validation,
        "summary": {
            "medianMarketUpdateGapMs": median_gap,
            "validationStateRows": validation_state_rows,
            "validationStateCoverage": validation_state_coverage,
            "validationTradeQtyAlignmentRate": validation_trade_alignment,
            "validationNullTradeQtyAlignmentRates": validation_nulls,
            "alignmentSpecificityLiftVsStrongestNull": alignment_specificity_lift,
            "validationTradeCountAlignmentRate": weighted_rate(validation, "tradesWithRemovalAlignment", "normalizedMatchesInWindow"),
            "gates": gates,
            "decision": decision,
        },
        "executionSemantics": {
            "preflightOnly": True,
            "hftbacktestRun": False,
            "waitActRate": None,
            "oracleValueCeiling": None,
            "learnedPolicyRealizedValue": None,
            "performanceBoundary": prereg["performanceBoundary"],
        },
        "next": (
            "Fit the smallest net-event QR generator on the three train markets and compare generated versus real chronological validation stylized facts; do not train a policy yet."
            if decision == "KEEP_NET_QR_WORLD_MODEL_PILOT"
            else "Do not build or train a Queue-Reactive policy world model from the current decomposition; first improve event identity/timing capture or pivot to a model defined directly on net depth transitions."
        ),
    }
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"path": str(args.output), "summary": report["summary"]}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
