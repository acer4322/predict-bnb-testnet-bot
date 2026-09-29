from __future__ import annotations

import csv
import json
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1


OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT_DIR / "target_replay_fidelity_gate_v1_preregistered.json"
REPORT = OUT_DIR / "target_replay_fidelity_gate_v1_report.json"
ROWS_CSV = OUT_DIR / "target_replay_fidelity_gate_v1_parents.csv"
BOOK_DB = ROOT / "data" / "wallet_maker_book_inference.db"
MARKETS = [1569361, 1571387, 1572594]
CALIBRATION_MARKETS = MARKETS[:2]
VALIDATION_MARKETS = MARKETS[2:]
ENTRY_MS = 1092
RESPONSE_MS = 273
POLL_MS = 250
EPS = 1e-9
TERMINAL = {"FILLED", "REJECTED", "EXPIRED", "CANCELED"}
VARIANTS = {
    "FOLLOWER_RAW_RISK": {"arrivalAligned": False, "stripped": False, "queue": "risk"},
    "ARRIVAL_RAW_RISK": {"arrivalAligned": True, "stripped": False, "queue": "risk"},
    "ARRIVAL_STRIPPED_RISK": {"arrivalAligned": True, "stripped": True, "queue": "risk"},
    "ARRIVAL_STRIPPED_LOG": {"arrivalAligned": True, "stripped": True, "queue": "log"},
}
ARRIVAL_SELECTION_ORDER = ["ARRIVAL_RAW_RISK", "ARRIVAL_STRIPPED_RISK", "ARRIVAL_STRIPPED_LOG"]


def ro(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def load_parents(market_id: int) -> list[dict[str, Any]]:
    connection = ro(BOOK_DB)
    try:
        rows = [
            dict(row)
            for row in connection.execute(
                """SELECT p.parent_id,p.target_side,p.native_book_side,p.target_price,p.native_price,
                          p.first_target_ms,p.last_target_ms,p.target_filled_shares,
                          p.expected_parent_shares,p.placement_allocated_shares,p.confidence,
                          MIN(u.received_at_ms) placement_received_ms,
                          MAX(u.received_at_ms) placement_received_last_ms,
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
                (int(market_id),),
            )
        ]
        return rows
    finally:
        connection.close()


def load_strip_allocations(parent_ids: list[str]) -> list[dict[str, Any]]:
    if not parent_ids:
        return []
    connection = ro(BOOK_DB)
    try:
        placeholders = ",".join("?" for _ in parent_ids)
        return [
            dict(row)
            for row in connection.execute(
                f"""SELECT a.parent_id,a.allocation_kind,a.native_book_side,a.native_price,
                           a.allocated_quantity,u.received_at_ms
                      FROM maker_book_inference_v21_allocations a
                      JOIN maker_book_inference_updates u ON u.id=a.update_id
                     WHERE a.parent_id IN ({placeholders})
                       AND a.allocation_kind IN ('PARENT_PLACEMENT','TARGET_FILL_DECREASE')
                     ORDER BY u.received_at_ms,a.allocation_kind,a.allocation_id""",
                parent_ids,
            )
        ]
    finally:
        connection.close()


def is_depth_event(ev: int) -> bool:
    return bool(ev & int(ex.DEPTH_EVENT | ex.DEPTH_SNAPSHOT_EVENT))


def event_native_side(ev: int) -> str | None:
    if ev & int(ex.BUY_EVENT):
        return "BID"
    if ev & int(ex.SELL_EVENT):
        return "ASK"
    return None


def strip_target_depth(events: np.ndarray, allocations: list[dict[str, Any]]) -> tuple[np.ndarray, dict[str, Any]]:
    adjusted = events.copy()
    changes: list[tuple[int, tuple[str, float], float]] = []
    placement_qty = fill_qty = 0.0
    for row in allocations:
        kind = str(row["allocation_kind"])
        quantity = float(row["allocated_quantity"])
        delta = quantity if kind == "PARENT_PLACEMENT" else -quantity
        key = (str(row["native_book_side"]).upper(), round(float(row["native_price"]), 2))
        changes.append((int(row["received_at_ms"]), key, delta))
        if kind == "PARENT_PLACEMENT":
            placement_qty += quantity
        else:
            fill_qty += quantity
    changes.sort(key=lambda item: (item[0], 0 if item[2] > 0 else 1))
    outstanding: dict[tuple[str, float], float] = defaultdict(float)
    change_index = 0
    adjusted_events = 0
    clipped_qty = 0.0
    max_outstanding = 0.0
    for index in range(len(adjusted)):
        ts_ms = int(adjusted[index]["local_ts"] // 1_000_000)
        while change_index < len(changes) and changes[change_index][0] <= ts_ms:
            _, key, delta = changes[change_index]
            outstanding[key] = max(0.0, outstanding[key] + delta)
            max_outstanding = max(max_outstanding, outstanding[key])
            change_index += 1
        ev = int(adjusted[index]["ev"])
        if not is_depth_event(ev):
            continue
        side = event_native_side(ev)
        if side is None:
            continue
        key = (side, round(float(adjusted[index]["px"]), 2))
        remove = float(outstanding.get(key, 0.0))
        if remove <= EPS:
            continue
        original = float(adjusted[index]["qty"])
        clipped_qty += max(0.0, remove - original)
        adjusted[index]["qty"] = max(0.0, original - remove)
        adjusted_events += 1
    return adjusted, {
        "placementAllocationShares": placement_qty,
        "fillDecreaseAllocationShares": fill_qty,
        "adjustedDepthEvents": adjusted_events,
        "clippedSubtractionShares": clipped_qty,
        "clippedShareOfPlacement": clipped_qty / placement_qty if placement_qty > EPS else None,
        "maxOutstandingAtLevelShares": max_outstanding,
        "unappliedAllocationChangesAtDataEnd": len(changes) - change_index,
    }


def submit_native(bt: Any, order_num: int, native_side: str, native_price: float, quantity: float) -> int:
    if native_side == "BID":
        return int(bt.submit_buy_order(0, order_num, native_price, quantity, ex.hbt.GTC, ex.LIMIT, False))
    return int(bt.submit_sell_order(0, order_num, native_price, quantity, ex.hbt.GTC, ex.LIMIT, False))


def replay_variant(market_id: int, parents: list[dict[str, Any]], name: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config = VARIANTS[name]
    raw_events, _, feed = tape_v1.build_archive_events(market_id, trade_offset="mid")
    allocations = load_strip_allocations([str(parent["parent_id"]) for parent in parents])
    if config["stripped"]:
        events, strip_audit = strip_target_depth(raw_events, allocations)
    else:
        events = raw_events
        strip_audit = {
            "placementAllocationShares": sum(float(row["allocated_quantity"]) for row in allocations if row["allocation_kind"] == "PARENT_PLACEMENT"),
            "fillDecreaseAllocationShares": sum(float(row["allocated_quantity"]) for row in allocations if row["allocation_kind"] == "TARGET_FILL_DECREASE"),
            "adjustedDepthEvents": 0,
            "clippedSubtractionShares": 0.0,
            "clippedShareOfPlacement": 0.0,
            "maxOutstandingAtLevelShares": 0.0,
            "unappliedAllocationChangesAtDataEnd": 0,
        }
    bt = ex.new_bt(events, entry_latency_ms=ENTRY_MS, response_latency_ms=RESPONSE_MS, queue_model=str(config["queue"]))
    ex.initialize_bt(bt)
    first_ms = int(events[0]["local_ts"] // 1_000_000)
    last_ms = int(feed["lastReceivedMs"])
    qualified: list[dict[str, Any]] = []
    for parent in parents:
        submit_ms = int(parent["placement_received_ms"]) - (ENTRY_MS if config["arrivalAligned"] else 0)
        if submit_ms < first_ms or submit_ms > last_ms:
            continue
        qualified.append(
            {
                **parent,
                "submit_ms": submit_ms,
                "target_window_start_ms": int(parent["first_target_ms"]),
                "target_window_end_ms": int(parent["last_target_ms"]) + 999,
                "cancel_request_ms": int(parent["last_target_ms"]) + 1000,
            }
        )
    by_submit: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for parent in qualified:
        by_submit[int(parent["submit_ms"])].append(parent)
    poll_start = ((first_ms + POLL_MS - 1) // POLL_MS) * POLL_MS
    poll_times = range(poll_start, last_ms + 1, POLL_MS)
    cancel_times = {int(parent["cancel_request_ms"]) for parent in qualified}
    score_times = {min(last_ms, int(parent["target_window_end_ms"]) + RESPONSE_MS + 1) for parent in qualified}
    timeline = sorted(set(poll_times) | set(by_submit) | cancel_times | score_times | {last_ms})
    orders: dict[int, dict[str, Any]] = {}
    order_num = 0
    submit_rejects = 0

    def harvest(now_ms: int) -> None:
        for meta in orders.values():
            snapshot = ex.order_snapshot(bt, int(meta["order_num"]))
            cumulative = float(snapshot.get("cumExecQty") or 0.0)
            previous = float(meta.get("prev_cum") or 0.0)
            if cumulative > previous + EPS:
                exchange_ms = int((snapshot.get("exchangeTs") or now_ms * 1_000_000) // 1_000_000)
                meta["fills"].append({"quantity": cumulative - previous, "exchange_ms": exchange_ms, "observed_ms": now_ms})
                meta["prev_cum"] = cumulative
            meta["status"] = str(snapshot.get("status") or "NONE")

    def request_due_cancels(now_ms: int) -> None:
        for meta in orders.values():
            if meta.get("cancel_requested") or now_ms < int(meta["cancel_request_ms"]):
                continue
            snapshot = ex.order_snapshot(bt, int(meta["order_num"]))
            if str(snapshot.get("status") or "NONE") not in {"NEW", "PARTIALLY_FILLED"}:
                continue
            order = bt.orders(0).get(int(meta["order_num"]))
            if order is None or not bool(order.cancellable):
                continue
            bt.cancel(0, int(meta["order_num"]), False)
            meta["cancel_requested"] = True
            meta["cancel_requested_ms"] = now_ms

    try:
        for now_ms in timeline:
            if not ex.advance_to(bt, int(now_ms)):
                break
            harvest(int(now_ms))
            request_due_cancels(int(now_ms))
            for parent in by_submit.get(int(now_ms), []):
                order_num += 1
                quantity = float(parent["expected_parent_shares"])
                native_price = round(float(parent["native_price"]), 2)
                rc = submit_native(bt, order_num, str(parent["native_book_side"]), native_price, quantity)
                submit_rejects += int(rc != 0)
                orders[order_num] = {
                    **parent,
                    "order_num": order_num,
                    "quantity": quantity,
                    "native_price": native_price,
                    "submit_rc": rc,
                    "prev_cum": 0.0,
                    "fills": [],
                    "cancel_requested": False,
                    "status": "SUBMITTED",
                }
        harvest(last_ms)
    finally:
        bt.close()

    rows: list[dict[str, Any]] = []
    for meta in orders.values():
        target_end = int(meta["target_window_end_ms"])
        fills_by_window = [fill for fill in meta["fills"] if int(fill["exchange_ms"]) <= target_end]
        filled_by_window = sum(float(fill["quantity"]) for fill in fills_by_window)
        terminal_filled = sum(float(fill["quantity"]) for fill in meta["fills"])
        first_fill_ms = min((int(fill["exchange_ms"]) for fill in meta["fills"]), default=None)
        last_fill_ms = max((int(fill["exchange_ms"]) for fill in meta["fills"]), default=None)
        target_start = int(meta["target_window_start_ms"])
        first_hit = first_fill_ms is not None and target_start <= first_fill_ms <= target_start + 999
        if first_fill_ms is None:
            distance_ms = None
        elif first_fill_ms < target_start:
            distance_ms = target_start - first_fill_ms
        elif first_fill_ms > target_start + 999:
            distance_ms = first_fill_ms - (target_start + 999)
        else:
            distance_ms = 0
        rows.append(
            {
                "marketId": market_id,
                "variant": name,
                "parentId": meta["parent_id"],
                "targetSide": meta["target_side"],
                "nativeBookSide": meta["native_book_side"],
                "nativePrice": meta["native_price"],
                "targetShares": float(meta["target_filled_shares"]),
                "placementAllocationShares": float(meta["placement_allocation_shares"]),
                "placementReceivedMs": int(meta["placement_received_ms"]),
                "submitLocalMs": int(meta["submit_ms"]),
                "simulatedExchangeArrivalMs": int(meta["submit_ms"]) + ENTRY_MS,
                "targetFirstFillSecondMs": target_start,
                "targetLastFillSecondMs": int(meta["last_target_ms"]),
                "hftFilledByTargetWindow": filled_by_window,
                "hftTerminalFilled": terminal_filled,
                "hftAnyFillByTargetWindow": int(filled_by_window > EPS),
                "hftFullFillByTargetWindow": int(filled_by_window >= float(meta["target_filled_shares"]) - 0.05),
                "hftFirstFillMs": first_fill_ms,
                "hftLastFillMs": last_fill_ms,
                "firstFillTargetSecondHit": int(first_hit),
                "firstFillDistanceToTargetSecondMs": distance_ms,
                "submitRc": int(meta["submit_rc"]),
                "finalStatus": meta["status"],
                "cancelRequested": bool(meta["cancel_requested"]),
            }
        )
    target_shares = sum(float(row["targetShares"]) for row in rows)
    target_window_filled = sum(float(row["hftFilledByTargetWindow"]) for row in rows)
    terminal_filled = sum(float(row["hftTerminalFilled"]) for row in rows)
    summary = {
        "marketId": market_id,
        "variant": name,
        "qualifiedParents": len(rows),
        "targetShares": target_shares,
        "hftFilledByTargetWindow": target_window_filled,
        "targetWindowShareReproduction": target_window_filled / target_shares if target_shares > EPS else None,
        "hftTerminalFilled": terminal_filled,
        "terminalShareReproduction": terminal_filled / target_shares if target_shares > EPS else None,
        "anyFillRecallByTargetWindow": sum(int(row["hftAnyFillByTargetWindow"]) for row in rows) / len(rows) if rows else None,
        "fullParentRecallByTargetWindow": sum(int(row["hftFullFillByTargetWindow"]) for row in rows) / len(rows) if rows else None,
        "firstFillTargetSecondHitRate": sum(int(row["firstFillTargetSecondHit"]) for row in rows) / len(rows) if rows else None,
        "medianFirstFillDistanceToTargetSecondMs": float(np.median([float(row["firstFillDistanceToTargetSecondMs"]) for row in rows if row["firstFillDistanceToTargetSecondMs"] is not None])) if any(row["firstFillDistanceToTargetSecondMs"] is not None for row in rows) else None,
        "submitRejects": submit_rejects,
        "feed": {key: feed[key] for key in ("updates", "rawMatchRows", "normalizedTrades", "firstReceivedMs", "lastReceivedMs")},
        "stripAudit": strip_audit,
    }
    return summary, rows


def aggregate(rows: list[dict[str, Any]], market_ids: list[int]) -> dict[str, Any]:
    chosen = [row for row in rows if int(row["marketId"]) in market_ids]
    target_shares = sum(float(row["targetShares"]) for row in chosen)
    filled = sum(float(row["hftFilledByTargetWindow"]) for row in chosen)
    parents = len(chosen)
    distances = [float(row["firstFillDistanceToTargetSecondMs"]) for row in chosen if row["firstFillDistanceToTargetSecondMs"] is not None]
    return {
        "markets": market_ids,
        "parents": parents,
        "targetShares": target_shares,
        "hftFilledByTargetWindow": filled,
        "targetWindowShareReproduction": filled / target_shares if target_shares > EPS else None,
        "fullParentRecallByTargetWindow": sum(int(row["hftFullFillByTargetWindow"]) for row in chosen) / parents if parents else None,
        "anyFillRecallByTargetWindow": sum(int(row["hftAnyFillByTargetWindow"]) for row in chosen) / parents if parents else None,
        "firstFillTargetSecondHitRate": sum(int(row["firstFillTargetSecondHit"]) for row in chosen) / parents if parents else None,
        "medianFirstFillDistanceToTargetSecondMs": float(np.median(distances)) if distances else None,
    }


def main() -> None:
    if not PREREG.exists():
        raise RuntimeError(f"missing preregistration: {PREREG}")
    all_rows: list[dict[str, Any]] = []
    market_summaries: list[dict[str, Any]] = []
    qualification: dict[str, Any] = {}
    for market_id in MARKETS:
        parents = load_parents(market_id)
        qualification[str(market_id)] = {
            "qualifiedParents": len(parents),
            "targetShares": sum(float(parent["target_filled_shares"]) for parent in parents),
        }
        for name in VARIANTS:
            summary, rows = replay_variant(market_id, parents, name)
            market_summaries.append(summary)
            all_rows.extend(rows)
            print(
                json.dumps(
                    {
                        "marketId": market_id,
                        "variant": name,
                        "parents": summary["qualifiedParents"],
                        "shareReproduction": summary["targetWindowShareReproduction"],
                        "fullRecall": summary["fullParentRecallByTargetWindow"],
                    }
                ),
                flush=True,
            )
    with ROWS_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(all_rows[0]) if all_rows else [])
        if all_rows:
            writer.writeheader()
            writer.writerows(all_rows)

    aggregates: dict[str, Any] = {}
    for name in VARIANTS:
        variant_rows = [row for row in all_rows if row["variant"] == name]
        aggregates[name] = {
            "calibration": aggregate(variant_rows, CALIBRATION_MARKETS),
            "validation": aggregate(variant_rows, VALIDATION_MARKETS),
        }
    selected = max(
        ARRIVAL_SELECTION_ORDER,
        key=lambda name: (
            float(aggregates[name]["calibration"]["targetWindowShareReproduction"] or 0.0),
            -ARRIVAL_SELECTION_ORDER.index(name),
        ),
    )
    validation = aggregates[selected]["validation"]
    follower_validation = aggregates["FOLLOWER_RAW_RISK"]["validation"]
    improvement = float(validation["targetWindowShareReproduction"] or 0.0) - float(follower_validation["targetWindowShareReproduction"] or 0.0)
    selected_market_summaries = [summary for summary in market_summaries if summary["variant"] == selected and int(summary["marketId"]) in VALIDATION_MARKETS]
    clipped_ratio = max((float(summary["stripAudit"]["clippedShareOfPlacement"] or 0.0) for summary in selected_market_summaries), default=0.0)
    absolute_pass = bool(
        float(validation["targetWindowShareReproduction"] or 0.0) >= 0.80 - EPS
        and float(validation["fullParentRecallByTargetWindow"] or 0.0) >= 0.70 - EPS
        and float(validation["firstFillTargetSecondHitRate"] or 0.0) >= 0.60 - EPS
        and improvement >= 0.15 - EPS
        and (not VARIANTS[selected]["stripped"] or clipped_ratio <= 0.10 + EPS)
    )
    decision = (
        "KEEP_TARGET_REPLACEMENT_REPLAY_V1"
        if absolute_pass
        else "NEED_MORE_DATA_REPLAY_FIDELITY"
        if improvement >= 0.15 - EPS
        else "REJECT_CURRENT_TARGET_REPLAY_AS_UNCALIBRATED"
    )
    report = {
        "reportVersion": "TARGET_REPLAY_FIDELITY_GATE_V1",
        "researchOnly": True,
        "preregisteredContract": PREREG.name,
        "question": "Can Tape V1 plus HftBacktest reproduce known fully-filled Target Maker parents when clocks and historical Target depth are handled as replacement replay?",
        "criticalCorrection": {
            "previousForcedSmoke": "Used source-clock placement_first_ms directly as local submit time, while Tape V1 uses received_at_ms, then added 1092ms entry latency.",
            "thisAudit": "Joins the allocated public placement update to received_at_ms. Arrival-aligned variants submit 1092ms earlier so the exchange arrival matches the inferred public placement appearance.",
        },
        "cohort": {
            "markets": MARKETS,
            "calibrationMarkets": CALIBRATION_MARKETS,
            "chronologicalValidationMarkets": VALIDATION_MARKETS,
            "openedDevelopmentOnly": True,
            "officialHftForward": False,
        },
        "qualification": qualification,
        "executionSemantics": {
            "engine": "HftBacktest + Predict Execution Tape V1",
            "queueModels": ["risk", "log"],
            "entryLatencyMs": ENTRY_MS,
            "responseLatencyMs": RESPONSE_MS,
            "pollMs": POLL_MS,
            "tradeOffset": "mid",
            "partialFill": True,
            "dreamFill": False,
        },
        "marketSummaries": market_summaries,
        "aggregates": aggregates,
        "selection": {
            "selectedOnCalibration": selected,
            "validationShareReproductionImprovementOverFollower": improvement,
            "selectedValidationMaxClippedShareOfPlacement": clipped_ratio,
        },
        "lockedGate": {
            "validationShareReproductionAtLeast080": float(validation["targetWindowShareReproduction"] or 0.0) >= 0.80 - EPS,
            "validationFullParentRecallAtLeast070": float(validation["fullParentRecallByTargetWindow"] or 0.0) >= 0.70 - EPS,
            "validationFirstFillSecondHitAtLeast060": float(validation["firstFillTargetSecondHitRate"] or 0.0) >= 0.60 - EPS,
            "validationImprovementOverFollowerAtLeast015": improvement >= 0.15 - EPS,
            "selectedStrippingIntegrity": not VARIANTS[selected]["stripped"] or clipped_ratio <= 0.10 + EPS,
        },
        "decision": decision,
        "limitations": [
            "Filled-parent recall only; unfilled/canceled Target orders are not observable in this dataset, so fill precision is unmeasured.",
            "Anonymous L2 ownership and Target queue priority remain inferred, not Market-By-Order ground truth.",
            "Passing this gate would validate only this replacement replay representation, not strategy value or live deployability.",
        ],
        "artifacts": {"parentRowsCsv": str(ROWS_CSV.resolve())},
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(REPORT), "selected": selected, "validation": validation, "improvement": improvement, "decision": decision}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
