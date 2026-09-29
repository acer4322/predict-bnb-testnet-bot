from __future__ import annotations

import bisect
import csv
import json
import lzma
import math
import sqlite3
import sys
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
ABIDES_ROOT = ROOT / ".tmp" / "abides-jpmc-public"
ABIDES_CORE = ABIDES_ROOT / "abides-core"
ABIDES_MARKETS = ABIDES_ROOT / "abides-markets"
for source_path in (ROOT, ABIDES_CORE, ABIDES_MARKETS):
    if str(source_path) not in sys.path:
        sys.path.insert(0, str(source_path))

from abides_markets.order_book import OrderBook
from abides_markets.orders import LimitOrder, Order, Side

from tools.hftbacktest_true_match_calibration_v0 import normalize_match


OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT_DIR / "predict_latent_exchange_smoke_v1_preregistered.json"
REPORT = OUT_DIR / "predict_latent_exchange_smoke_v1_report.json"
ROWS_CSV = OUT_DIR / "predict_latent_exchange_smoke_v1_validation_parents.csv"
BOOK_DB = ROOT / "data" / "wallet_maker_book_inference.db"
TAPE_PATH = ROOT / "data" / "execution_tape_v1" / "markets" / "1569361.json.xz"
BASELINE_CSV = OUT_DIR / "target_replay_fidelity_gate_v1_parents.csv"
MARKET_ID = 1569361
VALIDATION_PLACEMENT_CUTOFF_MS = 1_787_343_770_000
PARTICLES = 128
Q_SCALE = 100
SYMBOL = "PREDICT_YES"
BACKGROUND_AGENT = 10
TARGET_AGENT = 20
AGGRESSOR_AGENT = 30
EPS = 1e-12
ABIDES_EXPECTED_COMMIT = "f9cbe51342b7dedd9587e4e069040d68a5c6477f"


class FakeExchangeAgent:
    """The narrow owner interface used by ABIDES-Markets OrderBook."""

    def __init__(self) -> None:
        self.messages: list[tuple[int, Any]] = []
        self.current_time = 0
        self.mkt_open = 0
        self.book_logging = None
        self.stream_history = 10

    def send_message(self, recipient_id: int, message: Any, _: int = 0) -> None:
        self.messages.append((recipient_id, message))

    def logEvent(self, *args: Any, **kwargs: Any) -> None:
        return None


def ro(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def load_parents() -> list[dict[str, Any]]:
    connection = ro(BOOK_DB)
    try:
        return [
            dict(row)
            for row in connection.execute(
                """SELECT p.parent_id,p.target_side,p.native_book_side,p.target_price,p.native_price,
                          p.first_target_ms,p.last_target_ms,p.target_filled_shares,
                          p.expected_parent_shares,p.placement_allocated_shares,p.confidence,
                          MIN(u.received_at_ms) placement_received_ms,
                          COUNT(*) placement_allocation_count,
                          SUM(a.allocated_quantity) placement_allocation_shares
                     FROM maker_book_inference_v21_parent_lifecycles p
                     JOIN maker_book_inference_v21_allocations a
                       ON a.parent_id=p.parent_id AND a.allocation_kind='PARENT_PLACEMENT'
                     JOIN maker_book_inference_updates u ON u.id=a.update_id
                    WHERE p.market_id=?
                      AND p.confidence>=0.60
                      AND p.placement_coverage>=0.85
                      AND p.fill_allocation_coverage>=0.70
                      AND ABS(p.expected_parent_shares-p.target_filled_shares)<=0.05
                    GROUP BY p.parent_id
                   HAVING COUNT(*)=1
                      AND MIN(u.received_at_ms)<=p.first_target_ms+999
                    ORDER BY placement_received_ms,p.parent_id""",
                (MARKET_ID,),
            )
        ]
    finally:
        connection.close()


def load_tape() -> dict[str, Any]:
    with lzma.open(TAPE_PATH, "rt", encoding="utf-8") as handle:
        tape = json.load(handle)
    if tape.get("version") != "PREDICT_EXECUTION_TAPE_ARCHIVE_V1":
        raise RuntimeError(f"unexpected tape version: {tape.get('version')}")
    return tape


def side_enum(native_side: str) -> Side:
    return Side.BID if native_side.upper() == "BID" else Side.ASK


def price_units(price: float) -> int:
    return int(round(float(price) * 100))


def quantity_units(quantity: float) -> int:
    return max(0, int(round(float(quantity) * Q_SCALE)))


def current_level(book: OrderBook, side: Side, price: int) -> Any | None:
    levels = book.bids if side.is_bid() else book.asks
    return next((level for level in levels if int(level.price) == int(price)), None)


def iter_level_orders(book: OrderBook, side: Side, price: int) -> list[Order]:
    level = current_level(book, side, price)
    if level is None:
        return []
    return [order for order, _ in level.visible_orders]


def add_background(book: OrderBook, owner: FakeExchangeAgent, side: Side, price: int, quantity: int) -> None:
    if quantity <= 0:
        return
    order = LimitOrder(BACKGROUND_AGENT, owner.current_time, SYMBOL, int(quantity), side, int(price))
    book.enter_order(order, metadata={"role": "background"}, quiet=True)


def reduce_background(
    book: OrderBook,
    side: Side,
    price: int,
    quantity: int,
    cancel_front_probability: float,
    rng: np.random.Generator,
) -> int:
    remaining = int(quantity)
    removed = 0
    while remaining > 0:
        candidates = [order for order in iter_level_orders(book, side, price) if order.agent_id == BACKGROUND_AGENT]
        if not candidates:
            break
        chosen = candidates[0] if rng.random() < cancel_front_probability else candidates[-1]
        take = min(remaining, int(chosen.quantity))
        if take >= int(chosen.quantity):
            book.cancel_order(chosen, quiet=True)
        else:
            level = current_level(book, side, price)
            assert level is not None
            if not level.update_order_quantity(chosen.order_id, int(chosen.quantity) - take):
                raise RuntimeError("ABIDES background partial reduction failed")
        remaining -= take
        removed += take
    return removed


def target_remaining_at_level(trackers: dict[int, dict[str, Any]], side: Side, price: int) -> int:
    return sum(
        max(0, int(meta["quantity_units"]) - int(meta["filled_units"]))
        for meta in trackers.values()
        if meta["side"] == side and int(meta["price_units"]) == int(price)
    )


def sync_public_level(
    book: OrderBook,
    owner: FakeExchangeAgent,
    trackers: dict[int, dict[str, Any]],
    native_side: str,
    price: float,
    public_quantity: float,
    cancel_front_probability: float,
    rng: np.random.Generator,
) -> dict[str, float]:
    side = side_enum(native_side)
    px = price_units(price)
    observed = quantity_units(public_quantity)
    target_remaining = target_remaining_at_level(trackers, side, px)
    desired_background = max(0, observed - target_remaining)
    background_orders = [order for order in iter_level_orders(book, side, px) if order.agent_id == BACKGROUND_AGENT]
    background_now = sum(int(order.quantity) for order in background_orders)
    if desired_background > background_now:
        add_background(book, owner, side, px, desired_background - background_now)
    elif desired_background < background_now:
        removed = reduce_background(
            book,
            side,
            px,
            background_now - desired_background,
            cancel_front_probability,
            rng,
        )
        if removed != background_now - desired_background:
            raise RuntimeError("could not synchronize ABIDES background quantity")
    total_after = sum(int(order.quantity) for order in iter_level_orders(book, side, px))
    return {
        "observedUnits": float(observed),
        "targetRemainingUnits": float(target_remaining),
        "totalAfterUnits": float(total_after),
        "overhangUnits": float(max(0, total_after - observed)),
        "absoluteErrorUnits": float(abs(total_after - observed)),
    }


def apply_tape_row(public_book: dict[str, dict[float, float]], row: list[Any]) -> dict[str, list[tuple[float, float]]]:
    checkpoint = bool(int(row[3]))
    changed: dict[str, list[tuple[float, float]]] = {"BID": [], "ASK": []}
    snapshots = {"BID": row[4], "ASK": row[5]}
    raw_names = {"BID": "bids", "ASK": "asks"}
    for native_side in ("BID", "ASK"):
        snapshot = snapshots[native_side]
        state = public_book[native_side]
        if checkpoint and isinstance(snapshot, dict):
            replacement = {round(float(price), 2): float(quantity) for price, quantity in snapshot.items()}
            for price in sorted(set(state) | set(replacement)):
                after = float(replacement.get(price, 0.0))
                if abs(after - float(state.get(price, 0.0))) > EPS:
                    changed[native_side].append((price, after))
            public_book[native_side] = replacement
            continue
        for item in ((row[6] or {}).get(raw_names[native_side], []) or []):
            price, _, after, _ = map(float, item)
            price = round(price, 2)
            after = max(0.0, after)
            if after <= EPS:
                state.pop(price, None)
            else:
                state[price] = after
            changed[native_side].append((price, after))
    return changed


def build_window_feed(
    tape: dict[str, Any],
    start_ms: int,
    end_ms: int,
) -> tuple[dict[str, dict[float, float]], list[dict[str, Any]], list[int], list[dict[str, Any]]]:
    rows = sorted(list(tape.get("updates") or []), key=lambda row: (int(row[1]), int(row[0])))
    public_book: dict[str, dict[float, float]] = {"BID": {}, "ASK": {}}
    update_events: list[dict[str, Any]] = []
    update_times: list[int] = []
    initial: dict[str, dict[float, float]] | None = None
    for row in rows:
        received_ms = int(row[1])
        changes = apply_tape_row(public_book, row)
        update_times.append(received_ms)
        if received_ms <= start_ms:
            initial = deepcopy(public_book)
            continue
        if received_ms > end_ms:
            break
        update_events.append({"ts_ms": received_ms, "changes": changes})
    if initial is None or not initial["BID"] or not initial["ASK"]:
        raise RuntimeError(f"no complete public book before window start {start_ms}")
    trades: list[dict[str, Any]] = []
    for raw in tape.get("matches") or []:
        normalized = normalize_match(raw)
        if normalized is None:
            continue
        second_ms = int(normalized["tsMs"])
        if second_ms <= end_ms and second_ms + 999 >= start_ms:
            trades.append(normalized)
    trades.sort(
        key=lambda row: (
            int(row["tsMs"]),
            str(row.get("transactionHash") or ""),
            float(row["nativeYesPrice"]),
            float(row["qty"]),
        )
    )
    return initial, update_events, sorted(set(update_times)), trades


def sample_trade_events(trades: list[dict[str, Any]], rng: np.random.Generator) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        grouped[int(trade["tsMs"])].append(trade)
    events: list[dict[str, Any]] = []
    for second_ms, rows in grouped.items():
        offsets = sorted(int(value) for value in rng.integers(0, 1000, size=len(rows)))
        for offset, row in zip(offsets, rows):
            events.append({"ts_ms": second_ms + offset, "trade": row})
    return events


def execute_aggressive_trade(
    book: OrderBook,
    owner: FakeExchangeAgent,
    trackers: dict[int, dict[str, Any]],
    trade: dict[str, Any],
) -> None:
    side = Side.BID if str(trade["nativeAggressor"]).upper() == "BUY" else Side.ASK
    inbound = LimitOrder(
        AGGRESSOR_AGENT,
        owner.current_time,
        SYMBOL,
        quantity_units(float(trade["qty"])),
        side,
        price_units(float(trade["nativeYesPrice"])),
    )
    while inbound.quantity > 0:
        matched = book.execute_order(inbound)
        if matched is None:
            break
        tracker = trackers.get(int(matched.order_id))
        if tracker is None:
            continue
        fill_units = int(matched.quantity)
        tracker["filled_units"] += fill_units
        tracker["fills"].append({"ts_ms": int(owner.current_time // 1_000_000), "quantity_units": fill_units})


def placement_crosses(book: OrderBook, side: Side, price: int) -> bool:
    if side.is_bid():
        return bool(book.asks and int(book.asks[0].price) <= int(price))
    return bool(book.bids and int(book.bids[0].price) >= int(price))


def simulate_window(
    tape: dict[str, Any],
    parents: list[dict[str, Any]],
    cancel_front_probability: float,
    rng: np.random.Generator,
) -> dict[str, Any]:
    placement_times = sorted(int(parent["placement_received_ms"]) for parent in parents)
    all_update_times = sorted(int(row[1]) for row in tape.get("updates") or [])
    first_index = max(0, bisect.bisect_left(all_update_times, placement_times[0]) - 1)
    start_ms = all_update_times[first_index]
    end_ms = max(int(parent["last_target_ms"]) + 1_500 for parent in parents)
    initial, update_events, _, trades = build_window_feed(tape, start_ms, end_ms)
    owner = FakeExchangeAgent()
    owner.current_time = int(start_ms) * 1_000_000
    book = OrderBook(owner, SYMBOL)
    for native_side in ("BID", "ASK"):
        side = side_enum(native_side)
        for price, quantity in sorted(initial[native_side].items(), reverse=side.is_bid()):
            add_background(book, owner, side, price_units(price), quantity_units(quantity))

    placement_events: list[dict[str, Any]] = []
    for parent in parents:
        received_ms = int(parent["placement_received_ms"])
        previous_index = bisect.bisect_left(all_update_times, received_ms) - 1
        lower = all_update_times[previous_index] + 1 if previous_index >= 0 else received_ms
        lower = min(lower, received_ms)
        sampled_arrival = int(rng.integers(lower, received_ms + 1)) if lower < received_ms else received_ms
        placement_events.append({"ts_ms": sampled_arrival, "parent": parent, "interval_low_ms": lower})

    events: list[tuple[int, int, dict[str, Any]]] = []
    events.extend((int(event["ts_ms"]), 0, event) for event in placement_events)
    events.extend((int(event["ts_ms"]), 1, event) for event in sample_trade_events(trades, rng))
    events.extend((int(event["ts_ms"]), 2, event) for event in update_events)
    events.sort(key=lambda item: (item[0], item[1]))

    trackers: dict[int, dict[str, Any]] = {}
    tracker_by_parent: dict[str, dict[str, Any]] = {}
    sync_count = 0
    sync_absolute_error_units = 0.0
    max_overhang_units = 0.0
    crossing_placements = 0
    for ts_ms, priority, payload in events:
        if ts_ms < start_ms or ts_ms > end_ms:
            continue
        owner.current_time = int(ts_ms) * 1_000_000
        if priority == 0:
            parent = payload["parent"]
            side = side_enum(str(parent["native_book_side"]))
            px = price_units(float(parent["native_price"]))
            quantity = quantity_units(float(parent["expected_parent_shares"]))
            crossing_placements += int(placement_crosses(book, side, px))
            order = LimitOrder(TARGET_AGENT, owner.current_time, SYMBOL, quantity, side, px)
            book.enter_order(order, metadata={"role": "target", "parentId": parent["parent_id"]}, quiet=True)
            tracker = {
                "parent": parent,
                "order_id": int(order.order_id),
                "side": side,
                "price_units": px,
                "quantity_units": quantity,
                "filled_units": 0,
                "fills": [],
                "sampled_arrival_ms": ts_ms,
                "arrival_interval_low_ms": int(payload["interval_low_ms"]),
            }
            trackers[int(order.order_id)] = tracker
            tracker_by_parent[str(parent["parent_id"])] = tracker
        elif priority == 1:
            execute_aggressive_trade(book, owner, trackers, payload["trade"])
        else:
            for native_side in ("BID", "ASK"):
                for price, after in payload["changes"][native_side]:
                    audit = sync_public_level(
                        book,
                        owner,
                        trackers,
                        native_side,
                        price,
                        after,
                        cancel_front_probability,
                        rng,
                    )
                    sync_count += 1
                    sync_absolute_error_units += audit["absoluteErrorUnits"]
                    max_overhang_units = max(max_overhang_units, audit["overhangUnits"])

    outcomes: list[dict[str, Any]] = []
    for parent in parents:
        tracker = tracker_by_parent.get(str(parent["parent_id"]))
        if tracker is None:
            raise RuntimeError(f"Target parent was not placed: {parent['parent_id']}")
        window_end = int(parent["last_target_ms"]) + 999
        fills_in_window = [fill for fill in tracker["fills"] if int(fill["ts_ms"]) <= window_end]
        filled_units = min(int(tracker["quantity_units"]), sum(int(fill["quantity_units"]) for fill in fills_in_window))
        first_fill_ms = min((int(fill["ts_ms"]) for fill in tracker["fills"]), default=None)
        target_second = int(parent["first_target_ms"])
        first_second_hit = first_fill_ms is not None and target_second <= first_fill_ms <= target_second + 999
        if first_fill_ms is None:
            distance_ms = 5_000
        elif first_fill_ms < target_second:
            distance_ms = target_second - first_fill_ms
        elif first_fill_ms > target_second + 999:
            distance_ms = first_fill_ms - (target_second + 999)
        else:
            distance_ms = 0
        outcomes.append(
            {
                "parentId": str(parent["parent_id"]),
                "targetShares": float(parent["target_filled_shares"]),
                "targetFirstFillSecondMs": target_second,
                "targetLastFillSecondMs": int(parent["last_target_ms"]),
                "simulatedFilledByTargetWindow": filled_units / Q_SCALE,
                "simulatedAnyFillByTargetWindow": int(filled_units > 0),
                "simulatedFullFillByTargetWindow": int(filled_units >= int(tracker["quantity_units"])),
                "simulatedFirstFillMs": first_fill_ms,
                "simulatedFirstFillSecondHit": int(first_second_hit),
                "firstFillDistanceMs": int(distance_ms),
                "sampledArrivalMs": int(tracker["sampled_arrival_ms"]),
                "arrivalIntervalLowMs": int(tracker["arrival_interval_low_ms"]),
                "placementReceivedMs": int(parent["placement_received_ms"]),
            }
        )
    total_target_units = sum(quantity_units(float(parent["expected_parent_shares"])) for parent in parents)
    return {
        "outcomes": outcomes,
        "audit": {
            "windowStartMs": start_ms,
            "windowEndMs": end_ms,
            "windowDurationMs": end_ms - start_ms,
            "publicSyncLevels": sync_count,
            "meanAbsolutePublicSyncErrorShares": sync_absolute_error_units / max(1, sync_count) / Q_SCALE,
            "maxTargetOverhangShares": max_overhang_units / Q_SCALE,
            "maxTargetOverhangFractionPlaced": max_overhang_units / max(1, total_target_units),
            "crossingPlacements": crossing_placements,
            "crossingPlacementRate": crossing_placements / len(parents) if parents else 0.0,
            "normalizedTrades": len(trades),
        },
    }


def calibration_loss(rows: list[dict[str, Any]]) -> float:
    share_losses = [1.0 - min(1.0, float(row["simulatedFilledByTargetWindow"]) / float(row["targetShares"])) for row in rows]
    timing_losses = [min(5.0, float(row["firstFillDistanceMs"]) / 1000.0) for row in rows]
    return float(np.mean(share_losses) / 0.25 + np.mean(timing_losses) / 2.0)


def normalized_weights(losses: Iterable[float]) -> np.ndarray:
    values = np.asarray(list(losses), dtype=float)
    log_weights = -values
    log_weights -= float(np.max(log_weights))
    weights = np.exp(log_weights)
    return weights / float(weights.sum())


def weighted_quantile(values: list[float], weights: np.ndarray, quantile: float) -> float | None:
    if not values:
        return None
    arr = np.asarray(values, dtype=float)
    order = np.argsort(arr)
    arr = arr[order]
    selected_weights = np.asarray(weights, dtype=float)[order]
    cumulative = np.cumsum(selected_weights)
    cumulative /= cumulative[-1]
    return float(arr[min(len(arr) - 1, int(np.searchsorted(cumulative, quantile, side="left")))])


def baseline_for(parent_ids: set[str]) -> dict[str, Any]:
    rows = [
        row
        for row in csv.DictReader(BASELINE_CSV.open(encoding="utf-8"))
        if row["variant"] == "ARRIVAL_RAW_RISK"
        and int(row["marketId"]) == MARKET_ID
        and row["parentId"] in parent_ids
    ]
    target = sum(float(row["targetShares"]) for row in rows)
    return {
        "variant": "ARRIVAL_RAW_RISK",
        "parents": len(rows),
        "targetShares": target,
        "shareReproduction": sum(float(row["hftFilledByTargetWindow"]) for row in rows) / target,
        "anyFillRecall": sum(int(row["hftAnyFillByTargetWindow"]) for row in rows) / len(rows),
        "fullParentRecall": sum(int(row["hftFullFillByTargetWindow"]) for row in rows) / len(rows),
        "firstFillSecondHitRate": sum(int(row["firstFillTargetSecondHit"]) for row in rows) / len(rows),
    }


def aggregate_validation(
    particles: list[dict[str, Any]],
    weights: np.ndarray,
    parents: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    per_parent: list[dict[str, Any]] = []
    for parent in parents:
        parent_id = str(parent["parent_id"])
        rows = [next(row for row in particle["validation"]["outcomes"] if row["parentId"] == parent_id) for particle in particles]
        fills = [float(row["simulatedFilledByTargetWindow"]) for row in rows]
        first_values = [float(row["simulatedFirstFillMs"]) if row["simulatedFirstFillMs"] is not None else math.inf for row in rows]
        expected_fill = float(np.dot(weights, np.asarray(fills)))
        any_probability = float(np.dot(weights, np.asarray([row["simulatedAnyFillByTargetWindow"] for row in rows], dtype=float)))
        full_probability = float(np.dot(weights, np.asarray([row["simulatedFullFillByTargetWindow"] for row in rows], dtype=float)))
        first_hit_probability = float(np.dot(weights, np.asarray([row["simulatedFirstFillSecondHit"] for row in rows], dtype=float)))
        fill_q10 = weighted_quantile(fills, weights, 0.10)
        fill_q90 = weighted_quantile(fills, weights, 0.90)
        first_q10 = weighted_quantile(first_values, weights, 0.10)
        first_q90 = weighted_quantile(first_values, weights, 0.90)
        observed_first = int(parent["first_target_ms"])
        timing_interval_covers = bool(
            first_q10 is not None
            and first_q90 is not None
            and math.isfinite(first_q10)
            and observed_first <= first_q90
            and observed_first + 999 >= first_q10
        )
        target_shares = float(parent["target_filled_shares"])
        per_parent.append(
            {
                "parentId": parent_id,
                "targetSide": str(parent["target_side"]),
                "nativeBookSide": str(parent["native_book_side"]),
                "nativePrice": float(parent["native_price"]),
                "targetShares": target_shares,
                "placementReceivedMs": int(parent["placement_received_ms"]),
                "targetFirstFillSecondMs": observed_first,
                "posteriorExpectedFillByTargetWindow": expected_fill,
                "posteriorAnyFillProbability": any_probability,
                "posteriorFullFillProbability": full_probability,
                "posteriorFirstFillSecondHitProbability": first_hit_probability,
                "fillSharesQ10": fill_q10,
                "fillSharesQ90": fill_q90,
                "firstFillMsQ10": first_q10,
                "firstFillMsQ90": first_q90,
                "fillIntervalCoversObserved": bool(fill_q10 is not None and fill_q90 is not None and fill_q10 <= target_shares <= fill_q90),
                "timingIntervalCoversObservedSecond": timing_interval_covers,
            }
        )
    total_target = sum(float(row["targetShares"]) for row in per_parent)
    expected_fill = sum(float(row["posteriorExpectedFillByTargetWindow"]) for row in per_parent)
    weighted_overhang = float(
        np.dot(
            weights,
            np.asarray([particle["validation"]["audit"]["maxTargetOverhangFractionPlaced"] for particle in particles]),
        )
    )
    weighted_crossing = float(
        np.dot(
            weights,
            np.asarray([particle["validation"]["audit"]["crossingPlacementRate"] for particle in particles]),
        )
    )
    aggregate = {
        "parents": len(per_parent),
        "targetShares": total_target,
        "posteriorExpectedFillShares": expected_fill,
        "posteriorExpectedShareReproduction": expected_fill / total_target,
        "posteriorExpectedAnyFillRecall": float(np.mean([row["posteriorAnyFillProbability"] for row in per_parent])),
        "posteriorExpectedFullParentRecall": float(np.mean([row["posteriorFullFillProbability"] for row in per_parent])),
        "posteriorExpectedFirstFillSecondHitRate": float(np.mean([row["posteriorFirstFillSecondHitProbability"] for row in per_parent])),
        "fillIntervalCoverage": float(np.mean([row["fillIntervalCoversObserved"] for row in per_parent])),
        "timingIntervalCoverage": float(np.mean([row["timingIntervalCoversObservedSecond"] for row in per_parent])),
        "weightedMaxTargetOverhangFractionPlaced": weighted_overhang,
        "weightedCrossingPlacementRate": weighted_crossing,
    }
    return aggregate, per_parent


def self_test() -> None:
    owner = FakeExchangeAgent()
    book = OrderBook(owner, SYMBOL)
    add_background(book, owner, Side.BID, 50, 1_000)
    target = LimitOrder(TARGET_AGENT, 1, SYMBOL, 1_800, Side.BID, 50)
    book.enter_order(target, metadata={"role": "target"}, quiet=True)
    trackers = {
        target.order_id: {
            "side": Side.BID,
            "price_units": 50,
            "quantity_units": 1_800,
            "filled_units": 0,
            "fills": [],
        }
    }
    owner.current_time = 2_000_000
    execute_aggressive_trade(
        book,
        owner,
        trackers,
        {"nativeAggressor": "SELL", "nativeYesPrice": 0.50, "qty": 20.0},
    )
    assert trackers[target.order_id]["filled_units"] == 1_000
    audit = sync_public_level(book, owner, trackers, "BID", 0.50, 8.0, 0.5, np.random.default_rng(1))
    assert audit["absoluteErrorUnits"] == 0
    print(json.dumps({"ok": True, "selfTest": "ABIDES FIFO partial fill and public sync"}))


def main() -> None:
    if "--self-test" in sys.argv:
        self_test()
        return
    if not PREREG.exists():
        raise RuntimeError(f"missing preregistration: {PREREG}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if not prereg.get("writtenBeforeOutcomeGeneration"):
        raise RuntimeError("preregistration is not outcome-locked")
    tape = load_tape()
    parents = load_parents()
    calibration_parents = [parent for parent in parents if int(parent["placement_received_ms"]) < VALIDATION_PLACEMENT_CUTOFF_MS]
    validation_parents = [parent for parent in parents if int(parent["placement_received_ms"]) >= VALIDATION_PLACEMENT_CUTOFF_MS]
    if len(calibration_parents) != 9 or len(validation_parents) != 15:
        raise RuntimeError(f"cohort drift: calibration={len(calibration_parents)} validation={len(validation_parents)}")

    particles: list[dict[str, Any]] = []
    for particle_index in range(PARTICLES):
        parameter_rng = np.random.default_rng(71_000 + particle_index)
        cancel_front_probability = float(parameter_rng.beta(2.0, 2.0))
        calibration = simulate_window(
            tape,
            calibration_parents,
            cancel_front_probability,
            np.random.default_rng(81_000 + particle_index),
        )
        validation = simulate_window(
            tape,
            validation_parents,
            cancel_front_probability,
            np.random.default_rng(91_000 + particle_index),
        )
        loss = calibration_loss(calibration["outcomes"])
        particles.append(
            {
                "particle": particle_index,
                "cancelFrontProbability": cancel_front_probability,
                "calibrationLoss": loss,
                "calibration": calibration,
                "validation": validation,
            }
        )
    weights = normalized_weights(particle["calibrationLoss"] for particle in particles)
    effective_sample_size = float(1.0 / np.sum(np.square(weights)))
    validation, parent_rows = aggregate_validation(particles, weights, validation_parents)
    baseline = baseline_for({str(parent["parent_id"]) for parent in validation_parents})
    improvements = {
        "shareReproduction": validation["posteriorExpectedShareReproduction"] - baseline["shareReproduction"],
        "firstFillSecondHitRate": validation["posteriorExpectedFirstFillSecondHitRate"] - baseline["firstFillSecondHitRate"],
    }
    gates = {
        "shareReproductionImprovementAtLeast005": improvements["shareReproduction"] >= 0.05 - EPS,
        "firstFillSecondHitImprovementAtLeast010": improvements["firstFillSecondHitRate"] >= 0.10 - EPS,
        "timingIntervalCoverageAtLeast060": validation["timingIntervalCoverage"] >= 0.60 - EPS,
        "effectiveSampleSizeAtLeast16": effective_sample_size >= 16.0 - EPS,
        "weightedMaxTargetOverhangFractionAtMost010": validation["weightedMaxTargetOverhangFractionPlaced"] <= 0.10 + EPS,
        "weightedCrossingPlacementRateAtMost005": validation["weightedCrossingPlacementRate"] <= 0.05 + EPS,
    }
    integrity_failure = bool(
        validation["weightedMaxTargetOverhangFractionPlaced"] > 0.25 + EPS
        or validation["weightedCrossingPlacementRate"] > 0.10 + EPS
    )
    materially_no_better = bool(
        improvements["shareReproduction"] <= 0.02 + EPS
        and improvements["firstFillSecondHitRate"] <= 0.05 + EPS
    )
    if all(gates.values()):
        decision = "KEEP_LATENT_EXCHANGE_FOR_THREE_MARKET_REPLICATION"
    elif integrity_failure or materially_no_better:
        decision = "REJECT_CURRENT_LATENT_EXCHANGE_ADAPTER"
    else:
        decision = "NEED_MORE_DATA_LATENT_EXCHANGE"

    with ROWS_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(parent_rows[0]))
        writer.writeheader()
        writer.writerows(parent_rows)

    report = {
        "reportVersion": "PREDICT_LATENT_EXCHANGE_SMOKE_V1",
        "researchOnly": True,
        "preregisteredContract": PREREG.name,
        "question": "Can a mutable ABIDES FIFO book plus a strict prior over hidden placement time, trade time and cancellation location make actual Target fills typical on a chronological within-market holdout?",
        "dedupBoundary": "Not another net Queue-Reactive generator and not another HftBacktest queue model. Background events are the real Predict Tape; the new object is a mutable exchange containing the Target order, with uncertainty integrated rather than one guessed queue path.",
        "externalEngine": {
            "name": "ABIDES-Markets OrderBook",
            "repository": "https://github.com/jpmorganchase/abides-jpmc-public",
            "expectedCommit": ABIDES_EXPECTED_COMMIT,
            "sourcePath": str(ABIDES_ROOT.resolve()),
            "license": "BSD-3-Clause",
            "usedSemantics": ["price-time FIFO", "partial fill", "limit-price crossing", "cancel/quantity reduction"],
        },
        "cohort": {
            "marketId": MARKET_ID,
            "openedDevelopmentOnly": True,
            "calibrationParents": len(calibration_parents),
            "chronologicalValidationParents": len(validation_parents),
            "validationPlacementCutoffMs": VALIDATION_PLACEMENT_CUTOFF_MS,
            "officialHftForward": False,
            "special20260816": False,
            "supervisorFinal75To99": False,
        },
        "executionSemantics": {
            "particles": PARTICLES,
            "quantityScale": Q_SCALE,
            "targetOrder": "exact inferred Target Maker side/price/quantity, inserted into mutable ABIDES book",
            "placementPrior": "uniform between previous L2 receipt and inferred placement update receipt; no fill-time input",
            "tradeTimePrior": "order-preserving uniform order statistics inside each whole-second Predict match timestamp",
            "cancelLocationPrior": "per-particle Beta(2,2) probability of anonymous depth cancellation from queue front versus back",
            "targetVisibility": "one visible Target order; public depth synchronization removes simulated remaining Target quantity before adjusting anonymous background",
            "background": "actual Predict Execution Tape V1 L2 updates and normalized public matches",
            "partialFill": True,
            "dreamFill": False,
            "winnerSettlementPnlInput": False,
            "targetValidationFillInput": "used only after simulation for scoring; never used for arrival, queue or trade generation",
        },
        "posterior": {
            "calibrationLoss": "mean unfilled share fraction / 0.25 + mean capped first-fill distance seconds / 2",
            "effectiveSampleSize": effective_sample_size,
            "maxParticleWeight": float(np.max(weights)),
            "weightedCancelFrontProbability": float(np.dot(weights, np.asarray([particle["cancelFrontProbability"] for particle in particles]))),
            "unweightedCancelFrontProbabilityMean": float(np.mean([particle["cancelFrontProbability"] for particle in particles])),
        },
        "baseline": baseline,
        "validation": validation,
        "improvementsOverArrivalRawRisk": improvements,
        "lockedGate": gates,
        "decision": decision,
        "waitAct": {"waitRate": None, "actRate": None, "reason": "measurement-instrument fidelity smoke; no policy action selection"},
        "oracleValueCeiling": None,
        "learnedPolicyRealizedValue": None,
        "limitations": [
            "Filled-parent recall only; private Target unfilled/cancelled orders remain unavailable, so precision is still unmeasured.",
            "The background tape is conditionally replayed, not yet an endogenous learned agent population.",
            "A within-market chronological holdout can approve only a three-market replication, never strategy value or deployment.",
        ],
        "artifacts": {"validationParentRowsCsv": str(ROWS_CSV.resolve())},
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "report": str(REPORT.resolve()),
                "decision": decision,
                "baseline": baseline,
                "validation": validation,
                "improvements": improvements,
                "effectiveSampleSize": effective_sample_size,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
