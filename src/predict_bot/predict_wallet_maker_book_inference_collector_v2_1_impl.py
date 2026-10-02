from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import predict_wallet_maker_book_inference_collector as base
from . import predict_wallet_maker_book_inference_collector_v2 as v2

VERSION = "TARGET_MAKER_BOOK_INFERENCE_V2_1_CONSUMABLE_LIFECYCLE_FORWARD_ONLY"
TARGET_UNIT = 18.0
LOOKBACK_MS = 60_000
FILL_WINDOW_MS = 3_000
POST_WINDOW_MS = 5_000
CANCEL_MIN_REST_MS = 250
CANCEL_MAX_REST_MS = 60_000
RECONCILE_CURRENT_MS = 10_000
RECONCILE_BACKLOG_MS = 750
SNAPSHOT_CACHE_MS = 5_000


def _score_size(a: float, b: float) -> float:
    a = max(1e-9, abs(float(a)))
    b = max(1e-9, abs(float(b)))
    return min(a, b) / max(a, b)


def _pctile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(float(x) for x in values)
    p = (len(xs) - 1) * q
    lo, hi = math.floor(p), math.ceil(p)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - p) + xs[hi] * (p - lo)


class MakerBookConsumableLifecycleCollector(v2.MakerBookLifecycleInferenceCollector):
    """V2.1: quantity-consuming public-book allocation around retained target Maker fills.

    V2 associated each target fill with its best-looking public +depth/-depth event
    independently, so one public change could explain several target fills. V2.1
    rebuilds evidence per market with consumable quantities. A public delta can be
    shared only up to its actual quantity, and target fills sharing one order hash
    are grouped into one parent lifecycle before placement/refill statistics are
    calculated.
    """

    def __init__(self, db_path: Path = base.DB_PATH, target_db_path: Path = base.TARGET_DB_PATH) -> None:
        self.v21_last_current_ms = 0
        self.v21_last_backlog_ms = 0
        self.v21_backlog_index = 0
        self.v21_markets_reconciled_run = 0
        self.v21_cache_at = 0
        self.v21_cache: dict[str, Any] | None = None
        super().__init__(db_path, target_db_path)
        if self.asset == "BTC":
            self.version = VERSION

    def _create_schema(self) -> None:
        super()._create_schema()
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS maker_book_inference_v21_market_meta (
                    market_id INTEGER PRIMARY KEY,
                    reconciled_at_ms INTEGER NOT NULL,
                    target_event_count INTEGER NOT NULL,
                    source_max_ms INTEGER,
                    algorithm_version TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS maker_book_inference_v21_parent_lifecycles (
                    parent_id TEXT PRIMARY KEY,
                    market_id INTEGER NOT NULL,
                    order_hash TEXT,
                    target_side TEXT NOT NULL,
                    native_book_side TEXT NOT NULL,
                    target_price REAL NOT NULL,
                    native_price REAL NOT NULL,
                    first_target_ms INTEGER NOT NULL,
                    last_target_ms INTEGER NOT NULL,
                    target_fill_count INTEGER NOT NULL,
                    target_filled_shares REAL NOT NULL,
                    expected_parent_shares REAL NOT NULL,
                    allocated_fill_shares REAL NOT NULL,
                    fill_allocation_coverage REAL NOT NULL,
                    placement_allocated_shares REAL NOT NULL,
                    placement_coverage REAL NOT NULL,
                    placement_first_ms INTEGER,
                    placement_last_ms INTEGER,
                    resting_ms INTEGER,
                    post_action TEXT NOT NULL,
                    post_action_delay_ms INTEGER,
                    post_action_native_price REAL,
                    multi_fill_parent INTEGER NOT NULL,
                    observed_filled_near_18 INTEGER NOT NULL,
                    placement_supports_18 INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    evidence_json TEXT NOT NULL,
                    inferred_at_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_maker_book_v21_parent_market_time
                    ON maker_book_inference_v21_parent_lifecycles(market_id,first_target_ms);
                CREATE TABLE IF NOT EXISTS maker_book_inference_v21_allocations (
                    allocation_id TEXT PRIMARY KEY,
                    market_id INTEGER NOT NULL,
                    allocation_kind TEXT NOT NULL,
                    parent_id TEXT,
                    target_leg_id TEXT,
                    update_id INTEGER NOT NULL,
                    source_ms INTEGER NOT NULL,
                    native_book_side TEXT NOT NULL,
                    native_price REAL NOT NULL,
                    public_delta_quantity REAL NOT NULL,
                    allocated_quantity REAL NOT NULL,
                    score REAL NOT NULL,
                    evidence_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_maker_book_v21_alloc_market_kind
                    ON maker_book_inference_v21_allocations(market_id,allocation_kind,source_ms);
                CREATE TABLE IF NOT EXISTS maker_book_inference_v21_cancel_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    market_id INTEGER NOT NULL,
                    target_side TEXT NOT NULL,
                    native_book_side TEXT NOT NULL,
                    target_price REAL NOT NULL,
                    native_price REAL NOT NULL,
                    placement_source_ms INTEGER NOT NULL,
                    cancel_source_ms INTEGER NOT NULL,
                    allocated_quantity REAL NOT NULL,
                    resting_ms INTEGER NOT NULL,
                    post_action TEXT NOT NULL,
                    post_action_native_price REAL,
                    likely_reason TEXT NOT NULL,
                    pressure_side TEXT,
                    seconds_left REAL,
                    signal_age_ms INTEGER,
                    confidence REAL NOT NULL,
                    confidence_label TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    inferred_at_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_maker_book_v21_cancel_market_time
                    ON maker_book_inference_v21_cancel_candidates(market_id,cancel_source_ms);
                """
            )
            self.db.commit()

    def _book_events(self, market_id: int) -> list[dict[str, Any]]:
        with self.db_lock:
            rows = [dict(row) for row in self.db.execute(
                """SELECT id,source_timestamp_ms,is_checkpoint,changes_z
                     FROM maker_book_inference_updates
                    WHERE market_id=? ORDER BY source_timestamp_ms,id""",
                (int(market_id),),
            )]
        events: list[dict[str, Any]] = []
        for row in rows:
            changes = base.record(base.decode_json(row.get("changes_z") or b""))
            for key, side in (("bids", "BID"), ("asks", "ASK")):
                values = changes.get(key, []) if isinstance(changes.get(key), list) else []
                for index, change in enumerate(values):
                    price = base.finite(change.get("price"))
                    delta = base.finite(change.get("delta"))
                    if price is None or delta is None or abs(delta) <= 1e-9:
                        continue
                    events.append({
                        "key": f"{row['id']}:{side}:{price:.12g}:{index}",
                        "updateId": int(row["id"]),
                        "sourceMs": int(row["source_timestamp_ms"]),
                        "side": side,
                        "price": float(price),
                        "delta": float(delta),
                        "quantity": abs(float(delta)),
                        "checkpoint": bool(row["is_checkpoint"]),
                    })
        return events

    def _target_events_for_market(self, market_id: int) -> list[dict[str, Any]]:
        with self.db_lock:
            return [dict(row) for row in self.db.execute(
                """SELECT leg_id,order_hash,market_id,target_event_ms,side,target_price,target_shares,
                          native_book_side,native_price,matched_source_ms,match_confidence
                     FROM maker_book_inference_target_events
                    WHERE market_id=? AND status='MATCHED' AND matched_source_ms IS NOT NULL
                    ORDER BY matched_source_ms,target_event_ms,leg_id""",
                (int(market_id),),
            )]

    @staticmethod
    def _parent_id(row: dict[str, Any]) -> str:
        identity = str(row.get("order_hash") or row.get("leg_id") or "NO_ORDER")
        return f"{row['market_id']}:{identity}:{row['side']}:{float(row['target_price']):.12g}"

    def _insert_allocation(
        self,
        *,
        kind: str,
        market_id: int,
        parent_id: str | None,
        target_leg_id: str | None,
        event: dict[str, Any],
        quantity: float,
        score: float,
        sequence: int,
        note: str,
    ) -> tuple[Any, ...]:
        allocation_id = f"{market_id}:{kind}:{event['key']}:{parent_id or target_leg_id or 'NONE'}:{sequence}"
        return (
            allocation_id, int(market_id), kind, parent_id, target_leg_id,
            int(event["updateId"]), int(event["sourceMs"]), str(event["side"]), float(event["price"]),
            float(event["quantity"]), float(quantity), float(score),
            json.dumps({"consumableQuantity": True, "basis": note}, separators=(",", ":")),
        )

    def _reconcile_market(self, market_id: int) -> bool:
        targets = self._target_events_for_market(market_id)
        if not targets:
            return False
        events = self._book_events(market_id)
        if not events:
            return False

        positive = [event for event in events if event["delta"] > 0]
        negative = [event for event in events if event["delta"] < 0]
        pos_remaining = {event["key"]: float(event["quantity"]) for event in positive}
        neg_remaining = {event["key"]: float(event["quantity"]) for event in negative}
        allocations: list[tuple[Any, ...]] = []
        allocation_sequence = 0

        # First consume public decreases against known target fills. One decrease
        # can serve multiple target fills only while real quantity remains.
        fill_allocated_by_leg: dict[str, float] = defaultdict(float)
        for target in targets:
            wanted = max(0.0, float(target["target_shares"]))
            remaining = wanted
            reference = int(target["matched_source_ms"])
            candidates = [
                event for event in negative
                if event["side"] == str(target["native_book_side"])
                and abs(float(event["price"]) - float(target["native_price"])) <= 1e-9
                and abs(int(event["sourceMs"]) - reference) <= FILL_WINDOW_MS
                and neg_remaining[event["key"]] > 1e-9
            ]
            candidates.sort(key=lambda event: (
                abs(int(event["sourceMs"]) - reference),
                -_score_size(neg_remaining[event["key"]], remaining),
            ))
            for event in candidates:
                if remaining <= 1e-9:
                    break
                available = neg_remaining[event["key"]]
                quantity = min(remaining, available)
                if quantity <= 1e-9:
                    continue
                time_score = max(0.0, 1.0 - abs(int(event["sourceMs"]) - reference) / FILL_WINDOW_MS)
                score = 0.65 * _score_size(quantity, wanted) + 0.35 * time_score
                allocation_sequence += 1
                allocations.append(self._insert_allocation(
                    kind="TARGET_FILL_DECREASE", market_id=market_id,
                    parent_id=self._parent_id(target), target_leg_id=str(target["leg_id"]),
                    event=event, quantity=quantity, score=score, sequence=allocation_sequence,
                    note="known target fill consumes same-price anonymous public decrease capacity",
                ))
                neg_remaining[event["key"]] -= quantity
                remaining -= quantity
                fill_allocated_by_leg[str(target["leg_id"])] += quantity

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for target in targets:
            grouped[self._parent_id(target)].append(target)

        parent_rows: dict[str, dict[str, Any]] = {}
        parent_order = sorted(
            grouped.items(),
            key=lambda item: min(int(row["matched_source_ms"]) for row in item[1]),
        )

        # Then consume prior public increases as placement evidence per parent.
        # The expected parent size is deliberately 18 when observed fills are <=18;
        # low placement coverage therefore counts against the 18-share hypothesis.
        for parent_id, rows in parent_order:
            rows = sorted(rows, key=lambda row: (int(row["matched_source_ms"]), int(row["target_event_ms"])))
            first = rows[0]
            first_fill_ms = int(first["matched_source_ms"])
            last_fill_ms = max(int(row["matched_source_ms"]) for row in rows)
            target_filled = sum(float(row["target_shares"]) for row in rows)
            expected = TARGET_UNIT if target_filled <= TARGET_UNIT + 0.05 else target_filled
            remaining = expected
            wanted_side = str(first["native_book_side"])
            wanted_price = float(first["native_price"])
            candidates = [
                event for event in positive
                if event["side"] == wanted_side
                and abs(float(event["price"]) - wanted_price) <= 1e-9
                and first_fill_ms - LOOKBACK_MS <= int(event["sourceMs"]) < first_fill_ms
                and pos_remaining[event["key"]] > 1e-9
            ]
            candidates.sort(key=lambda event: (
                first_fill_ms - int(event["sourceMs"]),
                -_score_size(pos_remaining[event["key"]], remaining),
            ))
            placement_allocations: list[tuple[int, float, float]] = []
            for event in candidates:
                if remaining <= 1e-9:
                    break
                available = pos_remaining[event["key"]]
                quantity = min(remaining, available)
                if quantity <= 1e-9:
                    continue
                recency = max(0.0, 1.0 - (first_fill_ms - int(event["sourceMs"])) / LOOKBACK_MS)
                score = 0.70 * _score_size(quantity, expected) + 0.30 * recency
                allocation_sequence += 1
                allocations.append(self._insert_allocation(
                    kind="PARENT_PLACEMENT", market_id=market_id, parent_id=parent_id,
                    target_leg_id=None, event=event, quantity=quantity, score=score,
                    sequence=allocation_sequence,
                    note="parent placement consumes same-price public increase capacity before first known fill",
                ))
                pos_remaining[event["key"]] -= quantity
                remaining -= quantity
                placement_allocations.append((int(event["sourceMs"]), quantity, score))

            placement_quantity = sum(item[1] for item in placement_allocations)
            placement_first = min((item[0] for item in placement_allocations), default=None)
            placement_last = max((item[0] for item in placement_allocations), default=None)
            allocated_fill = sum(fill_allocated_by_leg[str(row["leg_id"])] for row in rows)
            placement_coverage = min(1.0, placement_quantity / expected) if expected > 0 else 0.0
            fill_coverage = min(1.0, allocated_fill / target_filled) if target_filled > 0 else 0.0
            placement_score = (
                sum(item[1] * item[2] for item in placement_allocations) / placement_quantity
                if placement_quantity > 0 else 0.0
            )
            target_match_score = statistics.mean(float(row.get("match_confidence") or 0.0) for row in rows)
            confidence = min(0.97, 0.45 * target_match_score + 0.30 * fill_coverage + 0.25 * placement_score)
            parent_rows[parent_id] = {
                "parentId": parent_id,
                "marketId": int(market_id),
                "orderHash": first.get("order_hash"),
                "targetSide": str(first["side"]),
                "nativeSide": wanted_side,
                "targetPrice": float(first["target_price"]),
                "nativePrice": wanted_price,
                "firstTargetMs": min(int(row["target_event_ms"]) for row in rows),
                "lastTargetMs": max(int(row["target_event_ms"]) for row in rows),
                "firstFillMs": first_fill_ms,
                "lastFillMs": last_fill_ms,
                "targetFillCount": len(rows),
                "targetFilledShares": target_filled,
                "expectedParentShares": expected,
                "allocatedFillShares": allocated_fill,
                "fillCoverage": fill_coverage,
                "placementQuantity": placement_quantity,
                "placementCoverage": placement_coverage,
                "placementFirstMs": placement_first,
                "placementLastMs": placement_last,
                "restingMs": first_fill_ms - placement_last if placement_last is not None else None,
                "multiFill": len(rows) > 1,
                "observedFilledNear18": abs(target_filled - TARGET_UNIT) <= 0.25,
                "placementSupports18": placement_quantity >= TARGET_UNIT * 0.85,
                "confidence": confidence,
            }

        # Refill/reprice is derived from the next *allocated parent placement*;
        # the same +depth is therefore one placement and one relationship, not
        # two independently counted pieces of evidence.
        ordered_parents = sorted(parent_rows.values(), key=lambda row: row["firstFillMs"])
        for parent in ordered_parents:
            action = "NO_CONFIRMED_NEXT_PARENT_5S"
            delay = None
            action_price = None
            candidates = [
                nxt for nxt in ordered_parents
                if nxt["parentId"] != parent["parentId"]
                and nxt["placementFirstMs"] is not None
                and parent["lastFillMs"] < int(nxt["placementFirstMs"]) <= parent["lastFillMs"] + POST_WINDOW_MS
                and nxt["nativeSide"] == parent["nativeSide"]
            ]
            candidates.sort(key=lambda row: int(row["placementFirstMs"]))
            for nxt in candidates:
                distance = abs(float(nxt["nativePrice"]) - float(parent["nativePrice"]))
                if distance <= 1e-9:
                    action = "SAME_PRICE_REFILL_CONFIRMED_NEXT_PARENT"
                elif distance <= 0.030000001:
                    action = "REPRICE_1_3_TICKS_CONFIRMED_NEXT_PARENT"
                else:
                    continue
                delay = int(nxt["placementFirstMs"]) - int(parent["lastFillMs"])
                action_price = float(nxt["nativePrice"])
                break
            parent["postAction"] = action
            parent["postActionDelayMs"] = delay
            parent["postActionPrice"] = action_price

        # Remaining target-like +depth and remaining -depth can form speculative
        # cancel candidates. Both sides consume quantity, preventing the V2
        # combinatorial many-to-one explosion.
        cancel_rows: list[tuple[Any, ...]] = []
        negatives_by_level: dict[tuple[str, float], list[dict[str, Any]]] = defaultdict(list)
        for event in negative:
            if neg_remaining[event["key"]] > 1e-9:
                negatives_by_level[(str(event["side"]), float(event["price"]))].append(event)
        for values in negatives_by_level.values():
            values.sort(key=lambda event: int(event["sourceMs"]))

        for add in sorted(positive, key=lambda event: int(event["sourceMs"])):
            add_remaining = pos_remaining[add["key"]]
            if add_remaining < TARGET_UNIT * 0.70:
                continue
            if _score_size(add_remaining, TARGET_UNIT) < 0.70:
                continue
            level = (str(add["side"]), float(add["price"]))
            candidates = [
                cut for cut in negatives_by_level.get(level, [])
                if add["sourceMs"] + CANCEL_MIN_REST_MS <= cut["sourceMs"] <= add["sourceMs"] + CANCEL_MAX_REST_MS
                and neg_remaining[cut["key"]] > 1e-9
            ]
            if not candidates:
                continue
            cut = min(candidates, key=lambda event: int(event["sourceMs"]))
            quantity = min(add_remaining, neg_remaining[cut["key"]])
            if quantity < TARGET_UNIT * 0.50:
                continue
            pos_remaining[add["key"]] -= quantity
            neg_remaining[cut["key"]] -= quantity
            target_side = "UP" if add["side"] == "BID" else "DOWN"
            target_price = float(add["price"]) if target_side == "UP" else 1.0 - float(add["price"])
            future = [
                event for event in positive
                if cut["sourceMs"] < event["sourceMs"] <= cut["sourceMs"] + 3_000
                and event["side"] == add["side"] and pos_remaining[event["key"]] > 1e-9
            ]
            same = [event for event in future if abs(float(event["price"]) - float(add["price"])) <= 1e-9]
            near = [event for event in future if 1e-9 < abs(float(event["price"]) - float(add["price"])) <= 0.030000001]
            next_event = min(same, key=lambda event: int(event["sourceMs"]), default=None)
            post_action = "SAME_PRICE_REFRESH" if next_event is not None else "NO_VISIBLE_REPLACEMENT_3S"
            if next_event is None:
                next_event = min(near, key=lambda event: int(event["sourceMs"]), default=None)
                if next_event is not None:
                    post_action = "REPRICE_1_3_TICKS"
            signal = self._signal(int(market_id), int(cut["sourceMs"]))
            pressure = self._pressure(signal)
            pressure_side = pressure.get("side")
            opposing = (
                (target_side == "UP" and pressure_side == "DOWN")
                or (target_side == "DOWN" and pressure_side == "UP")
            )
            seconds_left = base.finite(signal.get("seconds_left")) if signal else None
            likely_reason = (
                post_action if post_action != "NO_VISIBLE_REPLACEMENT_3S"
                else "TOXIC_FLOW_PULL" if opposing
                else "LATE_RISK_REDUCTION" if seconds_left is not None and seconds_left <= 60
                else "UNKNOWN_PUBLIC_DECREASE"
            )
            confidence = min(
                0.74,
                0.30 * _score_size(add_remaining, TARGET_UNIT)
                + 0.30 * _score_size(quantity, add_remaining)
                + 0.20 * max(0.0, 1.0 - (cut["sourceMs"] - add["sourceMs"]) / CANCEL_MAX_REST_MS)
                + 0.10 * int(next_event is not None)
                + 0.10 * int(likely_reason != "UNKNOWN_PUBLIC_DECREASE"),
            )
            candidate_id = f"{market_id}:{add['key']}:{cut['key']}"
            cancel_rows.append((
                candidate_id, int(market_id), target_side, str(add["side"]), target_price,
                float(add["price"]), int(add["sourceMs"]), int(cut["sourceMs"]), float(quantity),
                int(cut["sourceMs"] - add["sourceMs"]), post_action,
                float(next_event["price"]) if next_event is not None else None,
                likely_reason, pressure_side, seconds_left,
                int(cut["sourceMs"] - int(signal["sampled_at_ms"])) if signal else None,
                confidence, "MEDIUM_SPECULATIVE" if confidence >= 0.58 else "LOW_SPECULATIVE",
                json.dumps({
                    "identityIsProbabilistic": True,
                    "consumableQuantity": True,
                    "ownershipNotProven": True,
                    "publicSignal": pressure,
                }, separators=(",", ":")),
                base.now_ms(),
            ))

        parent_values: list[tuple[Any, ...]] = []
        for parent in parent_rows.values():
            parent_values.append((
                parent["parentId"], parent["marketId"], parent["orderHash"], parent["targetSide"],
                parent["nativeSide"], parent["targetPrice"], parent["nativePrice"],
                parent["firstTargetMs"], parent["lastTargetMs"], parent["targetFillCount"],
                parent["targetFilledShares"], parent["expectedParentShares"], parent["allocatedFillShares"],
                parent["fillCoverage"], parent["placementQuantity"], parent["placementCoverage"],
                parent["placementFirstMs"], parent["placementLastMs"], parent["restingMs"],
                parent["postAction"], parent["postActionDelayMs"], parent["postActionPrice"],
                int(parent["multiFill"]), int(parent["observedFilledNear18"]), int(parent["placementSupports18"]),
                parent["confidence"], json.dumps({
                    "identityIsProbabilistic": True,
                    "consumableQuantity": True,
                    "targetFillIdentityKnown": True,
                    "parentGrouping": "market + order_hash (fallback leg_id) + side + price",
                    "expectedParentRule": "18 shares when cumulative observed fills <=18.05; otherwise cumulative observed fills",
                }, separators=(",", ":")), base.now_ms(),
            ))

        with self.db_lock:
            self.db.execute("DELETE FROM maker_book_inference_v21_allocations WHERE market_id=?", (int(market_id),))
            self.db.execute("DELETE FROM maker_book_inference_v21_parent_lifecycles WHERE market_id=?", (int(market_id),))
            self.db.execute("DELETE FROM maker_book_inference_v21_cancel_candidates WHERE market_id=?", (int(market_id),))
            if allocations:
                self.db.executemany(
                    "INSERT INTO maker_book_inference_v21_allocations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    allocations,
                )
            if parent_values:
                self.db.executemany(
                    "INSERT INTO maker_book_inference_v21_parent_lifecycles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    parent_values,
                )
            if cancel_rows:
                self.db.executemany(
                    "INSERT INTO maker_book_inference_v21_cancel_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    cancel_rows,
                )
            source_max = max((int(event["sourceMs"]) for event in events), default=None)
            self.db.execute(
                """INSERT INTO maker_book_inference_v21_market_meta VALUES (?,?,?,?,?)
                   ON CONFLICT(market_id) DO UPDATE SET
                       reconciled_at_ms=excluded.reconciled_at_ms,
                       target_event_count=excluded.target_event_count,
                       source_max_ms=excluded.source_max_ms,
                       algorithm_version=excluded.algorithm_version""",
                (int(market_id), base.now_ms(), len(targets), source_max, VERSION),
            )
            self.db.commit()
        self.v21_markets_reconciled_run += 1
        self.v21_cache_at = 0
        return True

    def _backlog_markets(self) -> list[int]:
        with self.db_lock:
            rows = self.db.execute(
                """SELECT t.market_id,COUNT(*) target_count,MAX(t.target_event_ms) latest,
                          COALESCE(m.target_event_count,-1) old_count
                     FROM maker_book_inference_target_events t
                     LEFT JOIN maker_book_inference_v21_market_meta m ON m.market_id=t.market_id
                    WHERE t.status='MATCHED'
                    GROUP BY t.market_id
                    HAVING old_count!=target_count
                    ORDER BY latest DESC"""
            ).fetchall()
        return [int(row[0]) for row in rows]

    def _advance_v21(self) -> None:
        if self.asset != "BTC":
            return
        now = base.now_ms()
        current = int(self.current_market_id) if self.current_market_id is not None else None
        if current is not None and now - self.v21_last_current_ms >= RECONCILE_CURRENT_MS:
            self.v21_last_current_ms = now
            self._reconcile_market(current)
        if now - self.v21_last_backlog_ms < RECONCILE_BACKLOG_MS:
            return
        self.v21_last_backlog_ms = now
        backlog = [market for market in self._backlog_markets() if market != current]
        if backlog:
            self._reconcile_market(backlog[0])

    def _match_pending_events(self) -> None:
        # Deliberately bypass V2's independent best-candidate lifecycle/cancel
        # matcher. Keep the proven V1 target-fill matcher, then build V2.1 using
        # consumable quantities only.
        base.MakerBookInferenceCollector._match_pending_events(self)
        self._advance_v21()

    @staticmethod
    def _capital_lower_bound(rows: list[dict[str, Any]]) -> dict[str, Any]:
        by_market: dict[int, list[tuple[int, float]]] = defaultdict(list)
        for row in rows:
            if row.get("placement_last_ms") is None or row.get("placement_allocated_shares") is None:
                continue
            quantity = float(row["placement_allocated_shares"])
            notional = quantity * float(row["target_price"])
            if quantity <= 0 or notional <= 0:
                continue
            market = int(row["market_id"])
            by_market[market].append((int(row["placement_last_ms"]), notional))
            by_market[market].append((int(row["first_target_ms"]), -notional))
        peaks: list[float] = []
        for events in by_market.values():
            current = peak = 0.0
            for _, delta in sorted(events, key=lambda item: (item[0], -item[1])):
                current += delta
                peak = max(peak, current)
            peaks.append(peak)
        return {
            "markets": len(peaks),
            "medianPeakUsdt": statistics.median(peaks) if peaks else None,
            "p90PeakUsdt": _pctile(peaks, 0.90),
            "interpretation": "lower bound from uniquely quantity-allocated parent placement intervals; unfilled/unattributed orders remain excluded",
        }

    def _v21_snapshot(self) -> dict[str, Any]:
        now = base.now_ms()
        if self.v21_cache is not None and now - self.v21_cache_at < SNAPSHOT_CACHE_MS:
            return dict(self.v21_cache)
        with self.db_lock:
            summary = dict(self.db.execute(
                """SELECT COUNT(*) parents,
                          COALESCE(SUM(placement_allocated_shares>0),0) placed,
                          AVG(placement_coverage) avg_placement_coverage,
                          AVG(fill_allocation_coverage) avg_fill_coverage,
                          COALESCE(SUM(post_action='SAME_PRICE_REFILL_CONFIRMED_NEXT_PARENT'),0) refill,
                          COALESCE(SUM(post_action='REPRICE_1_3_TICKS_CONFIRMED_NEXT_PARENT'),0) reprice,
                          COALESCE(SUM(multi_fill_parent),0) multi_fill,
                          COALESCE(SUM(observed_filled_near_18),0) observed18,
                          COALESCE(SUM(placement_supports_18),0) placement18
                     FROM maker_book_inference_v21_parent_lifecycles"""
            ).fetchone())
            recent = [dict(row) for row in self.db.execute(
                """SELECT parent_id,market_id,order_hash,target_side,target_price,first_target_ms,last_target_ms,
                          target_fill_count,target_filled_shares,expected_parent_shares,allocated_fill_shares,
                          fill_allocation_coverage,placement_allocated_shares,placement_coverage,
                          placement_first_ms,placement_last_ms,resting_ms,post_action,post_action_delay_ms,
                          post_action_native_price,multi_fill_parent,observed_filled_near_18,placement_supports_18,confidence
                     FROM maker_book_inference_v21_parent_lifecycles
                    ORDER BY last_target_ms DESC LIMIT 30"""
            )]
            all_rows = [dict(row) for row in self.db.execute(
                """SELECT market_id,target_price,first_target_ms,placement_last_ms,
                          placement_allocated_shares,resting_ms,target_filled_shares,target_fill_count
                     FROM maker_book_inference_v21_parent_lifecycles"""
            )]
            cancel_count = int(self.db.execute(
                "SELECT COUNT(*) FROM maker_book_inference_v21_cancel_candidates"
            ).fetchone()[0])
            cancel_reasons = [dict(row) for row in self.db.execute(
                """SELECT likely_reason reason,COUNT(*) count,AVG(confidence) averageConfidence,
                          SUM(allocated_quantity) allocatedShares
                     FROM maker_book_inference_v21_cancel_candidates
                    GROUP BY likely_reason ORDER BY allocatedShares DESC"""
            )]
            recent_cancels = [dict(row) for row in self.db.execute(
                """SELECT candidate_id,market_id,target_side,target_price,native_book_side,native_price,
                          placement_source_ms,cancel_source_ms,allocated_quantity,resting_ms,post_action,
                          post_action_native_price,likely_reason,pressure_side,seconds_left,signal_age_ms,
                          confidence,confidence_label
                     FROM maker_book_inference_v21_cancel_candidates
                    ORDER BY cancel_source_ms DESC LIMIT 20"""
            )]
            allocation = dict(self.db.execute(
                """SELECT COUNT(*) allocations,COALESCE(SUM(allocated_quantity),0) allocated_shares,
                          COUNT(DISTINCT update_id||':'||native_book_side||':'||native_price||':'||source_ms) public_events
                     FROM maker_book_inference_v21_allocations"""
            ).fetchone())
            reconciled_markets = int(self.db.execute(
                "SELECT COUNT(*) FROM maker_book_inference_v21_market_meta"
            ).fetchone()[0])
        n = int(summary.get("parents") or 0)
        resting = [float(row["resting_ms"]) for row in all_rows if row.get("resting_ms") is not None]
        parent_filled = [float(row["target_filled_shares"]) for row in all_rows]
        out = {
            "version": VERSION,
            "enabled": True,
            "readOnly": True,
            "forwardEvidenceOnly": True,
            "retainedForwardEvidenceBackfill": True,
            "consumableQuantityAllocation": True,
            "identityBoundary": "target fills/order hashes are known; anonymous public placement/cancel ownership remains probabilistic",
            "parents": n,
            "reconciledMarkets": reconciled_markets,
            "parentsWithPlacement": int(summary.get("placed") or 0),
            "parentPlacementRate": int(summary.get("placed") or 0) / n if n else None,
            "averagePlacementCoverage": summary.get("avg_placement_coverage"),
            "averageFillAllocationCoverage": summary.get("avg_fill_coverage"),
            "medianRestingMs": statistics.median(resting) if resting else None,
            "p90RestingMs": _pctile(resting, 0.90),
            "confirmedSamePriceRefillRate": int(summary.get("refill") or 0) / n if n else None,
            "confirmedRepriceRate": int(summary.get("reprice") or 0) / n if n else None,
            "multiFillParentRate": int(summary.get("multi_fill") or 0) / n if n else None,
            "observedParentFilledNear18Rate": int(summary.get("observed18") or 0) / n if n else None,
            "placementSupports18Rate": int(summary.get("placement18") or 0) / n if n else None,
            "medianObservedParentFilledShares": statistics.median(parent_filled) if parent_filled else None,
            "capitalLowerBound": self._capital_lower_bound(all_rows),
            "cancelCandidates": cancel_count,
            "cancelReasonBreakdown": cancel_reasons,
            "allocationDiagnostics": allocation,
            "recentParents": recent,
            "recentCancelCandidates": recent_cancels,
            "run": {"marketsReconciledThisRun": self.v21_markets_reconciled_run},
            "v2Correction": "public +depth/-depth quantities are consumed; multiple target fills sharing one order hash are grouped before placement/refill statistics",
        }
        self.v21_cache = dict(out)
        self.v21_cache_at = now
        return out

    def snapshot(self) -> dict[str, Any]:
        # Call the original V1 collector snapshot directly so the obsolete V2
        # many-to-one lifecycle summary is not exposed as if it were current.
        payload = base.MakerBookInferenceCollector.snapshot(self)
        payload["version"] = VERSION if self.asset == "BTC" else payload.get("version")
        if self.asset == "BTC":
            payload["lifecycleInference"] = self._v21_snapshot()
            payload["lifecycleInferenceV2Retired"] = {
                "status": "RETIRED_MANY_TO_ONE_EVIDENCE_REUSE",
                "historicalTablesPreserved": True,
            }
        return payload


def main() -> int:
    collector = MakerBookConsumableLifecycleCollector(base.DB_PATH, base.TARGET_DB_PATH)
    collector.start()
    handler = type("MakerBookInferenceV21Handler", (base.Handler,), {"collector": collector})
    server = base.ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"{VERSION} listening on http://{base.HOST}:{base.PORT}/state; asset={base.ASSET}; "
        "consumableQuantityAllocation=true; readOnly=true; liveOrdersAffected=false",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        collector.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
