from __future__ import annotations

import argparse
import json
import lzma
import math
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hftbacktest_execution_shift_audit_v0 as ex  # noqa: E402
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1  # noqa: E402


VERSION = "R21_ECHTGELD_MARKET_HFT_REPLAY_V1"
ENGINE_DB = ROOT / "data" / "echtgeld_engine_v1.db"
CONTROLLER_DB = ROOT / "data" / "strategy_r2_r21_echtgeld_v1.db"
NATIVE_HFT_DIR = ROOT / "data" / "hft_forward_paper_v1" / "markets"
OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
SOURCE_ID = "R2_R21_8789"
EPS = 1e-9


def _read_only(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _json(value: Any) -> dict[str, Any]:
    try:
        result = json.loads(str(value or "{}"))
    except Exception:
        return {}
    return result if isinstance(result, dict) else {}


def _decision_ms(client_order_id: str, fallback: int) -> int:
    parts = str(client_order_id).split(":")
    for value in reversed(parts):
        try:
            parsed = int(value)
        except ValueError:
            continue
        if parsed > 1_000_000_000_000:
            return parsed
    return int(fallback)


def _incident_for_order(inbox: dict[str, Any], order_marker: str, incident_type: str) -> dict[str, Any] | None:
    for item in inbox.get("incidents") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("incidentType")) == incident_type and order_marker in str(item.get("orderKey") or ""):
            return item
    return None


def _compact_decision(row: sqlite3.Row) -> dict[str, Any]:
    public = _json(row["public_state_json"])
    payload = _json(row["payload_json"])
    inbox = public.get("r21ExecutionIncidentInbox") or {}
    semantic = payload.get("r21SemanticCooperation") or {}
    return {
        "decisionMs": int(row["decision_ms"]),
        "secondsLeft": float(row["seconds_left"]),
        "desiredPortfolioAction": row["desired_portfolio_action"],
        "executionChoice": row["execution_choice"],
        "primaryReason": row["primary_reason"],
        "situationCode": semantic.get("situationCode"),
        "ownershipState": semantic.get("ownershipState"),
        "remainingObligation": semantic.get("remainingObligation"),
        "actualConfirmedInventory": inbox.get("actualConfirmedInventory"),
        "targetBySide": inbox.get("targetBySide"),
        "lastEventSeq": inbox.get("lastEventSeq"),
    }


def load_live_truth(market_id: int) -> dict[str, Any]:
    engine = _read_only(ENGINE_DB)
    controller = _read_only(CONTROLLER_DB)
    try:
        engine_orders = [
            dict(row)
            for row in engine.execute(
                """SELECT * FROM engine_cap100_orders
                    WHERE source_market_id=? ORDER BY created_at_ms,client_order_id""",
                (int(market_id),),
            )
        ]
        engine_events = [
            dict(row)
            for row in engine.execute(
                """SELECT * FROM engine_cap100_events
                    WHERE source_market_id=? ORDER BY occurred_at_ms,seq""",
                (int(market_id),),
            )
        ]
        controller_orders = [
            dict(row)
            for row in controller.execute(
                """SELECT * FROM our_orders WHERE market_id=?
                    ORDER BY placed_at_ms,order_id""",
                (int(market_id),),
            )
        ]
        decisions = controller.execute(
            """SELECT decision_id,decision_ms,seconds_left,desired_portfolio_action,
                      execution_choice,primary_reason,public_state_json,payload_json
                 FROM our_decisions WHERE market_id=? ORDER BY decision_ms,decision_id""",
            (int(market_id),),
        ).fetchall()
        settlement = engine.execute(
            "SELECT winner,resolved_at_ms,source FROM engine_settlements WHERE market_id=? LIMIT 1",
            (int(market_id),),
        ).fetchone()
    finally:
        engine.close()
        controller.close()

    controller_by_id = {str(row["order_id"]): row for row in controller_orders}
    decision_by_ms = {int(row["decision_ms"]): row for row in decisions}
    fills_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in engine_events:
        if str(event.get("event_type")) == "FILL_DELTA":
            fills_by_id[str(event.get("client_order_id") or "")].append(event)

    orders: list[dict[str, Any]] = []
    policy_mismatches: list[dict[str, Any]] = []
    for index, row in enumerate(engine_orders, start=1):
        order_id = str(row["client_order_id"])
        role = str(row["role"]).upper()
        decision_ms = _decision_ms(order_id, int(row["created_at_ms"]))
        local = controller_by_id.get(order_id)
        if role == "MAKER":
            if local is None:
                policy_mismatches.append({"orderId": order_id, "reason": "ENGINE_MAKER_WITHOUT_CONTROLLER_ORDER"})
            else:
                for key, engine_value, local_value in (
                    ("side", str(row["side"]), str(local["side"])),
                    ("price", float(row["requested_price"]), float(local["price"])),
                    ("shares", float(row["requested_shares"]), float(local["shares"])),
                ):
                    if engine_value != local_value:
                        policy_mismatches.append(
                            {"orderId": order_id, "field": key, "engine": engine_value, "controller": local_value}
                        )
        decision = decision_by_ms.get(decision_ms)
        if role == "TAKER" and (
            decision is None or str(decision["desired_portfolio_action"]) != "ACTIVE_INTERVENTION_REQUIRED"
        ):
            policy_mismatches.append({"orderId": order_id, "reason": "TAKER_WITHOUT_FROZEN_R2_ACTIVE_DECISION"})

        fill_path = fills_by_id.get(order_id, [])
        orders.append(
            {
                "num": index,
                "clientOrderId": order_id,
                "role": role,
                "side": str(row["side"]).upper(),
                "price": float(row["requested_price"]),
                "shares": float(row["requested_shares"]),
                "decisionMs": decision_ms,
                "liveCreatedMs": int(row["created_at_ms"]),
                "livePlaceCompletedMs": row["place_completed_at_ms"],
                "liveCompletedMs": row["completed_at_ms"],
                "liveState": row["state"],
                "liveExchangeStatus": row["exchange_status"],
                "liveErrorKind": row["error_kind"],
                "liveFilledShares": float(row["filled_share_qty"] or 0.0),
                "liveFilledUsdt": float(row["filled_usdt_amount"] or 0.0),
                "liveAvgFillPrice": row["avg_fill_price"],
                "liveFirstFillMs": int(fill_path[0]["occurred_at_ms"]) if fill_path else None,
                "liveLastFillMs": int(fill_path[-1]["occurred_at_ms"]) if fill_path else None,
                "reachedLiveVenue": bool(row["place_completed_at_ms"]),
            }
        )

    maker_reject = next(
        (
            event
            for event in engine_events
            if event.get("event_type") == "ORDER_REJECTED"
            and event.get("role") == "MAKER"
            and event.get("state") == "REJECTED"
        ),
        None,
    )
    taker_order = next((order for order in orders if order["role"] == "TAKER"), None)
    maker_marker = ":MAKER:DOWN:34:"
    taker_marker = ":TAKER:UP:"
    maker_visible: sqlite3.Row | None = None
    taker_delay: sqlite3.Row | None = None
    taker_stall: sqlite3.Row | None = None
    taker_terminal_visible: sqlite3.Row | None = None
    for decision in decisions:
        inbox = (_json(decision["public_state_json"]).get("r21ExecutionIncidentInbox") or {})
        if maker_visible is None and _incident_for_order(inbox, maker_marker, "SUBMIT_REJECT_CONFIRMED"):
            maker_visible = decision
        if taker_delay is None and _incident_for_order(inbox, taker_marker, "LIVE_NO_FILL_DELAY"):
            taker_delay = decision
        if taker_stall is None and _incident_for_order(inbox, taker_marker, "LIVE_NO_FILL_STALL"):
            taker_stall = decision
        for terminal_type in ("ACTIVE_CHILD_TERMINAL_NO_FILL_CONFIRMED", "SUBMIT_REJECT_CONFIRMED"):
            if _incident_for_order(inbox, taker_marker, terminal_type):
                taker_terminal_visible = decision
                break

    first_formal_repair = next(
        (
            row
            for row in decisions
            if maker_reject is not None
            and int(row["decision_ms"]) > int(maker_reject["occurred_at_ms"])
            and str(row["desired_portfolio_action"]) == "PASSIVE_REPAIR"
        ),
        None,
    )
    order_decision_times = {int(order["decisionMs"]) for order in orders}
    first_formal_repair_action = next(
        (
            row
            for row in decisions
            if maker_reject is not None
            and int(row["decision_ms"]) > int(maker_reject["occurred_at_ms"])
            and str(row["desired_portfolio_action"]) == "PASSIVE_REPAIR"
            and int(row["decision_ms"]) in order_decision_times
        ),
        None,
    )
    post_reject_orders = [
        {
            "delayMs": int(order["decisionMs"]) - int(maker_reject["occurred_at_ms"]),
            "role": order["role"],
            "side": order["side"],
            "price": order["price"],
        }
        for order in orders
        if maker_reject is not None and int(order["decisionMs"]) > int(maker_reject["occurred_at_ms"])
    ][:8]

    wait_count = sum(str(row["execution_choice"]) == "WAIT" for row in decisions)
    action_count = len(decisions) - wait_count
    side_counts = Counter(order["side"] for order in orders if order["role"] == "MAKER")
    live_up = sum(order["liveFilledShares"] for order in orders if order["side"] == "UP")
    live_down = sum(order["liveFilledShares"] for order in orders if order["side"] == "DOWN")
    live_cost = sum(order["liveFilledUsdt"] for order in orders)
    placement_latencies = [
        int(order["livePlaceCompletedMs"]) - int(order["decisionMs"])
        for order in orders
        if order.get("livePlaceCompletedMs") is not None
    ]
    maker_placement_latencies = [
        int(order["livePlaceCompletedMs"]) - int(order["decisionMs"])
        for order in orders
        if order["role"] == "MAKER" and order.get("livePlaceCompletedMs") is not None
    ]
    winner = str(settlement["winner"]).upper() if settlement else None
    live_pnl = None
    if winner in {"UP", "DOWN"}:
        live_pnl = (live_up if winner == "UP" else live_down) - live_cost

    return {
        "orders": orders,
        "policyAudit": {
            "sourceIds": sorted({str(row.get("source_id")) for row in engine_orders}),
            "strategies": sorted({str(row.get("strategy")) for row in engine_orders}),
            "engineOrders": len(engine_orders),
            "controllerMakerOrders": len(controller_orders),
            "makerOrders": sum(order["role"] == "MAKER" for order in orders),
            "takerOrders": sum(order["role"] == "TAKER" for order in orders),
            "makerSideCounts": dict(side_counts),
            "makerSideSequence": [order["side"] for order in orders if order["role"] == "MAKER"],
            "firstMaker": next(
                (
                    {key: order[key] for key in ("side", "price", "decisionMs")}
                    for order in orders
                    if order["role"] == "MAKER"
                ),
                None,
            ),
            "firstTaker": next(
                (
                    {key: order[key] for key in ("side", "price", "decisionMs")}
                    for order in orders
                    if order["role"] == "TAKER"
                ),
                None,
            ),
            "allExactlyTenShares": all(abs(order["shares"] - 10.0) <= EPS for order in orders),
            "policyMismatches": policy_mismatches,
            "exactControllerEngineMapping": not policy_mismatches,
            "decisions": len(decisions),
            "waitDecisions": wait_count,
            "actionDecisions": action_count,
            "waitRate": wait_count / len(decisions) if decisions else None,
            "actionRate": action_count / len(decisions) if decisions else None,
            "note": "One rolled-back Maker reject and the active-intervention hook create venue attempts even when the final recorded executionChoice is WAIT; the durable order ledger is authoritative for write attempts.",
        },
        "r21Audit": {
            "makerReject": maker_reject,
            "makerRejectFirstVisibleDecision": _compact_decision(maker_visible) if maker_visible else None,
            "makerRejectDeliveryLagMs": (
                int(maker_visible["decision_ms"]) - int(maker_reject["occurred_at_ms"])
                if maker_visible is not None and maker_reject is not None
                else None
            ),
            "firstFormalPassiveRepairDecision": _compact_decision(first_formal_repair) if first_formal_repair else None,
            "firstFormalPassiveRepairLagMs": (
                int(first_formal_repair["decision_ms"]) - int(maker_reject["occurred_at_ms"])
                if first_formal_repair is not None and maker_reject is not None
                else None
            ),
            "firstExecutedPassiveRepairDecision": _compact_decision(first_formal_repair_action) if first_formal_repair_action else None,
            "firstExecutedPassiveRepairLagMs": (
                int(first_formal_repair_action["decision_ms"]) - int(maker_reject["occurred_at_ms"])
                if first_formal_repair_action is not None and maker_reject is not None
                else None
            ),
            "postRejectOrderAttempts": post_reject_orders,
            "taker": taker_order,
            "takerNoFillDelayFirstVisibleDecision": _compact_decision(taker_delay) if taker_delay else None,
            "takerNoFillStallFirstVisibleDecision": _compact_decision(taker_stall) if taker_stall else None,
            "takerTerminalFailureVisibleToR2Decision": taker_terminal_visible is not None,
            "takerTerminalFailureDecision": _compact_decision(taker_terminal_visible) if taker_terminal_visible else None,
            "lastR2DecisionMs": int(decisions[-1]["decision_ms"]) if decisions else None,
            "terminalFailureAfterLastDecisionMs": (
                int(taker_order["liveCompletedMs"]) - int(decisions[-1]["decision_ms"])
                if taker_order and taker_order.get("liveCompletedMs") and decisions
                else None
            ),
            "informationOnly": True,
            "causalBoundary": "This proves detection and delivery into Frozen R2's recorded input. It does not prove that a later R2 action was caused by R2.1 without a matched inbox-removed counterfactual.",
        },
        "liveExecution": {
            "attempts": len(orders),
            "venueReached": sum(order["reachedLiveVenue"] for order in orders),
            "filledOrders": sum(order["liveFilledShares"] > EPS for order in orders),
            "rejectedOrders": sum(str(order["liveState"]) == "REJECTED" for order in orders),
            "filledShares": {"UP": live_up, "DOWN": live_down},
            "filledCostUsdt": live_cost,
            "winnerAuditOnly": winner,
            "settledPnlUsdtAuditOnly": live_pnl,
            "decisionToVenueResponseLatencyMs": {
                "count": len(placement_latencies),
                "min": min(placement_latencies) if placement_latencies else None,
                "median": statistics.median(placement_latencies) if placement_latencies else None,
                "max": max(placement_latencies) if placement_latencies else None,
                "makerMedian": statistics.median(maker_placement_latencies) if maker_placement_latencies else None,
            },
        },
    }


def _submit(bt: Any, order: dict[str, Any]) -> int:
    if order["role"] == "MAKER":
        return ex.submit_native(bt, int(order["num"]), str(order["side"]), float(order["price"]), float(order["shares"]))
    native_side, native_price = ex.native_order(str(order["side"]), float(order["price"]))
    if native_side == "BUY":
        return int(bt.submit_buy_order(0, int(order["num"]), native_price, float(order["shares"]), ex.hbt.GTC, ex.LIMIT, False))
    return int(bt.submit_sell_order(0, int(order["num"]), native_price, float(order["shares"]), ex.hbt.GTC, ex.LIMIT, False))


def _hft_fill_price(order: dict[str, Any], snapshot: dict[str, Any]) -> float | None:
    native = snapshot.get("execPrice")
    if native is None:
        return None
    return float(native) if order["side"] == "UP" else 1.0 - float(native)


def replay_variant(
    orders: list[dict[str, Any]],
    feed_events: Any,
    update_times: list[int],
    *,
    name: str,
    entry_latency_ms: int,
    response_latency_ms: int,
    queue_model: str,
    winner: str | None,
    taker_confirm_ms: int = 2200,
) -> dict[str, Any]:
    selected = [dict(order) for order in orders]
    bt = ex.new_bt(
        feed_events,
        entry_latency_ms=entry_latency_ms,
        response_latency_ms=response_latency_ms,
        queue_model=queue_model,
    )
    ex.initialize_bt(bt)
    by_time: dict[int, list[dict[str, Any]]] = defaultdict(list)
    taker_confirm_by_time: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for order in selected:
        by_time[int(order["decisionMs"])].append(order)
        if order["role"] == "TAKER":
            taker_confirm_by_time[int(order["decisionMs"]) + int(taker_confirm_ms)].append(order)
    timeline = sorted(set(update_times + list(by_time) + list(taker_confirm_by_time)))
    submitted: set[int] = set()
    previous: dict[int, dict[str, Any]] = {}
    transitions: dict[int, list[dict[str, Any]]] = defaultdict(list)

    def observe(at_ms: int) -> None:
        for num in sorted(submitted):
            snapshot = ex.order_snapshot(bt, num)
            old = previous.get(num)
            if old is None or snapshot.get("status") != old.get("status") or abs(
                float(snapshot.get("cumExecQty") or 0.0) - float(old.get("cumExecQty") or 0.0)
            ) > EPS:
                transitions[num].append({"atMs": int(at_ms), **snapshot})
            previous[num] = snapshot

    try:
        feed_exhausted = False
        for at_ms in timeline:
            if not feed_exhausted and int(bt.current_timestamp) <= int(at_ms) * 1_000_000:
                if not ex.advance_to(bt, int(at_ms)):
                    feed_exhausted = True
            observe(at_ms)
            for order in by_time.get(at_ms, []):
                if feed_exhausted:
                    order["hftSubmitRc"] = 1
                    continue
                order["hftSubmitRc"] = _submit(bt, order)
                submitted.add(int(order["num"]))
                observe(at_ms)
            for order in taker_confirm_by_time.get(at_ms, []):
                num = int(order["num"])
                confirmation = ex.order_snapshot(bt, num)
                order["hftTakerConfirm"] = {"atMs": int(at_ms), **confirmation}
                current = bt.orders(0).get(num)
                if (
                    float(confirmation.get("cumExecQty") or 0.0) <= EPS
                    and current is not None
                    and int(current.status) in {int(ex.NEW), int(ex.PARTIALLY_FILLED)}
                    and bool(current.cancellable)
                ):
                    bt.cancel(0, num, False)
                    order["hftCancelRequestedAtConfirm"] = True
                else:
                    order["hftCancelRequestedAtConfirm"] = False
        while True:
            rc = int(bt.wait_next_feed(True, 10_000_000_000))
            if rc == 1:
                break
            observe(int(bt.current_timestamp // 1_000_000))
        observe(int(bt.current_timestamp // 1_000_000))
        for order in selected:
            num = int(order["num"])
            final = ex.order_snapshot(bt, num) if num in submitted else ex.order_snapshot(bt, -num)
            path = transitions[num]
            effective = order.get("hftTakerConfirm") if order["role"] == "TAKER" else final
            if not isinstance(effective, dict):
                effective = final
            effective_cutoff = int(order["decisionMs"]) + int(taker_confirm_ms) if order["role"] == "TAKER" else None
            filled = [
                row
                for row in path
                if float(row.get("cumExecQty") or 0.0) > EPS
                and (effective_cutoff is None or int(row["atMs"]) <= effective_cutoff)
            ]
            order["hftTransitions"] = path
            order["hftFinal"] = final
            order["hftEffectiveFinal"] = effective
            order["hftFilledShares"] = float(effective.get("cumExecQty") or 0.0)
            order["hftFillPrice"] = _hft_fill_price(order, effective)
            order["hftFirstFillMs"] = int(filled[0]["atMs"]) if filled else None
            order["filledShareErrorHftMinusLive"] = order["hftFilledShares"] - float(order["liveFilledShares"])
            order["firstFillErrorMsHftMinusLive"] = (
                int(order["hftFirstFillMs"]) - int(order["liveFirstFillMs"])
                if order["hftFirstFillMs"] is not None and order["liveFirstFillMs"] is not None
                else None
            )
    finally:
        bt.close()

    up = sum(order["hftFilledShares"] for order in selected if order["side"] == "UP")
    down = sum(order["hftFilledShares"] for order in selected if order["side"] == "DOWN")
    maker_up = sum(order["hftFilledShares"] for order in selected if order["role"] == "MAKER" and order["side"] == "UP")
    maker_down = sum(order["hftFilledShares"] for order in selected if order["role"] == "MAKER" and order["side"] == "DOWN")
    cost = sum(
        order["hftFilledShares"] * float(order["hftFillPrice"] or 0.0)
        for order in selected
    )
    taker_fee = sum(
        order["hftFilledShares"] * float(order["hftFillPrice"] or 0.0) * 0.02
        for order in selected
        if order["role"] == "TAKER"
    )
    pnl = None
    if winner in {"UP", "DOWN"}:
        pnl = (up if winner == "UP" else down) - cost - taker_fee
    both_filled_timing = [
        abs(int(order["firstFillErrorMsHftMinusLive"]))
        for order in selected
        if order.get("firstFillErrorMsHftMinusLive") is not None
    ]
    fill_agreement = sum(
        (float(order["liveFilledShares"]) > EPS) == (float(order["hftFilledShares"]) > EPS)
        for order in selected
    )
    return {
        "variant": name,
        "config": {
            "entryLatencyMs": entry_latency_ms,
            "responseLatencyMs": response_latency_ms,
            "queueModel": queue_model,
            "tradeOffset": "mid",
            "makerTimeInForce": "GTX",
            "takerTimeInForce": "GTC_MARKETABLE_LIMIT",
            "takerConfirmMs": int(taker_confirm_ms),
            "takerNoFillHandling": "cancel requested after confirmation horizon; fills after the horizon are not credited",
        },
        "summary": {
            "orders": len(selected),
            "makerOrders": sum(order["role"] == "MAKER" for order in selected),
            "takerOrders": sum(order["role"] == "TAKER" for order in selected),
            "hftAnyFillOrders": sum(order["hftFilledShares"] > EPS for order in selected),
            "hftFullFillOrders": sum(order["hftFilledShares"] >= order["shares"] - EPS for order in selected),
            "hftRejects": sum(str(order["hftEffectiveFinal"].get("status")) == "REJECTED" for order in selected),
            "liveVsHftFillStateAgreementOrders": fill_agreement,
            "liveVsHftFillStateAgreementRate": fill_agreement / len(selected) if selected else None,
            "medianAbsFirstFillErrorMs": statistics.median(both_filled_timing) if both_filled_timing else None,
            "filledShares": {"UP": up, "DOWN": down, "makerUP": maker_up, "makerDOWN": maker_down},
            "filledCostUsdt": cost,
            "takerFeesUsdt": taker_fee,
            "winnerAuditOnly": winner,
            "settledPnlUsdtAuditOnly": pnl,
        },
        "orders": selected,
    }


def native_hft_summary(market_id: int, live_maker_sequence: list[str]) -> dict[str, Any]:
    path = NATIVE_HFT_DIR / f"{int(market_id)}_r2_hft_closed_loop_v1.json.xz"
    with lzma.open(path, "rt", encoding="utf-8") as handle:
        report = json.load(handle)
    metadata = report.get("orderMeta") or {}
    rows = list(metadata.values()) if isinstance(metadata, dict) else list(metadata)
    rows.sort(key=lambda row: (int(row.get("placedAtMs") or 0), int(row.get("orderNum") or 0)))
    hft_sequence = [str(row.get("side")) for row in rows]
    prefix = min(len(live_maker_sequence), len(hft_sequence))
    decisions = report.get("decisionRows") or []
    wait = sum(str(row.get("executionChoice")) == "WAIT" for row in decisions)
    return {
        "artifact": str(path),
        "student": report.get("student"),
        "studentScale": report.get("studentScale"),
        "config": report.get("config"),
        "decisions": len(decisions),
        "waitDecisions": wait,
        "actionDecisions": len(decisions) - wait,
        "waitRate": wait / len(decisions) if decisions else None,
        "makerPlacements": len(rows),
        "makerSideCounts": dict(Counter(hft_sequence)),
        "makerSideSequence": hft_sequence,
        "sameSideAtSharedOrdinal": sum(
            live_maker_sequence[index] == hft_sequence[index] for index in range(prefix)
        ),
        "sharedOrdinalCount": prefix,
        "firstMaker": (
            {key: rows[0].get(key) for key in ("side", "price", "placedAtMs", "reason")} if rows else None
        ),
        "takerAttempts": report.get("takerAttempts") or [],
        "studentRollout": report.get("studentRollout"),
        "interpretationBoundary": "This is a separate closed-loop R2-on-HFT trajectory using the original 18-share scale. It diagnoses state/trajectory divergence and is not a size-matched execution replay of the 10-share live run.",
    }


def run(market_id: int, entry_latency_ms: int, response_latency_ms: int, queue_model: str) -> dict[str, Any]:
    live = load_live_truth(market_id)
    events, update_times, feed = tape_v1.build_archive_events(market_id, trade_offset="mid")
    winner = live["liveExecution"].get("winnerAuditOnly")
    all_attempts = replay_variant(
        live["orders"],
        events,
        update_times,
        name="ALL_CONTROLLER_ATTEMPTS",
        entry_latency_ms=entry_latency_ms,
        response_latency_ms=response_latency_ms,
        queue_model=queue_model,
        winner=winner,
    )
    reached = [order for order in live["orders"] if order["reachedLiveVenue"]]
    venue_reached = replay_variant(
        reached,
        events,
        update_times,
        name="LIVE_VENUE_REACHED_ONLY",
        entry_latency_ms=entry_latency_ms,
        response_latency_ms=response_latency_ms,
        queue_model=queue_model,
        winner=winner,
    )
    live_sequence = list(live["policyAudit"]["makerSideSequence"])
    native = native_hft_summary(market_id, live_sequence)
    primary_summary = venue_reached["summary"]
    live_execution = live["liveExecution"]
    return {
        "version": VERSION,
        "researchOnly": True,
        "liveTradingChanges": False,
        "marketId": int(market_id),
        "hypothesis": "The exact observed 10-share R2+R2.1 order-attempt sequence should retain broadly similar fill direction, count and timing when replayed through performance-grade HftBacktest execution semantics on the same market.",
        "dedupBoundary": {
            "nativeHftArtifact": "Already reruns R2 closed-loop at the original 18-share scale; it does not force the observed Echtgeld action sequence.",
            "oracleReplay": "Injects actual venue fills and therefore cannot validate HFT queue/latency reproduction.",
            "oldLiveCalibration": "Market 1513668 CAP100 only; this is the first R2_R21_8789 market 1658035 audit.",
        },
        "executionSemantics": {
            "engine": "HftBacktest partial-fill exchange + risk-adverse queue",
            "feed": "Predict Execution Tape V1 receipt-aligned L2 + normalized true matches",
            "entryLatencyMs": entry_latency_ms,
            "responseLatencyMs": response_latency_ms,
            "tradeOffset": "mid",
            "takerConfirmMs": 2200,
            "dreamFillAllowed": False,
            "actualFillsInjected": False,
            "strictPastRuntimeInput": True,
            "winnerUse": "audit-only after replay",
        },
        "feed": feed,
        "live": {key: value for key, value in live.items() if key != "orders"},
        "fixedObservedActionReplays": [all_attempts, venue_reached],
        "nativeClosedLoopHft": native,
        "oracleValueCeiling": None,
        "learnedPolicyRealizedValue": None,
        "conclusions": {
            "livePolicyMapping": "KEEP_EXACT_R2_R21_CONTROLLER_TO_ENGINE_MAPPING",
            "makerRejectTransport": "KEEP_R21_MAKER_REJECT_DETECTION_AND_R2_INPUT_DELIVERY",
            "takerLiveStallTransport": "KEEP_R21_NONTERMINAL_TAKER_DELAY_STALL_DELIVERY_NO_DUPLICATE_ACTION",
            "takerTerminalTransport": "NEED_FIX_POST_MARKET_TERMINAL_FAILURE_NOT_PRESENT_IN_ANY_R2_DECISION",
            "fixedObservedActionReplay": "REJECT_EXACT_REPRODUCTION_BUT_KEEP_AS_APPROXIMATE_EXECUTION_DIAGNOSTIC",
            "nativeClosedLoopReplay": "REJECT_NATIVE_HFT_AS_BEHAVIORAL_REPRODUCTION_OF_THIS_LIVE_RUN",
            "primaryReplayDifferences": {
                "fillStateAgreementOrders": primary_summary["liveVsHftFillStateAgreementOrders"],
                "orders": primary_summary["orders"],
                "upShareErrorHftMinusLive": float(primary_summary["filledShares"]["UP"]) - float(live_execution["filledShares"]["UP"]),
                "downShareErrorHftMinusLive": float(primary_summary["filledShares"]["DOWN"]) - float(live_execution["filledShares"]["DOWN"]),
                "settledPnlErrorHftMinusLiveAuditOnly": (
                    float(primary_summary["settledPnlUsdtAuditOnly"]) - float(live_execution["settledPnlUsdtAuditOnly"])
                    if primary_summary.get("settledPnlUsdtAuditOnly") is not None
                    and live_execution.get("settledPnlUsdtAuditOnly") is not None
                    else None
                ),
            },
            "mechanism": "With the observed actions fixed, HFT reproduces most fills and the Taker no-fill, but its Predict venue rejects/expires the first DOWN 0.39 Maker that Binance accepted. Native closed-loop HFT already diverges before that fill (its first Maker has a different time and side), then the different execution feedback compounds the trajectory into a different action count and opposite Taker side.",
        },
        "interpretationBoundary": "The real orders executed on Binance Prediction, while the performance-grade HFT tape is Predict.fun. Differences therefore measure total venue-data plus queue/latency simulator divergence, not simulator error alone. Fixed observed-action replay also suppresses closed-loop action changes after counterfactual fills; nativeClosedLoopHft is reported separately for that trajectory effect.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-id", type=int, default=1658035)
    parser.add_argument("--entry-latency-ms", type=int, default=1092)
    parser.add_argument("--response-latency-ms", type=int, default=273)
    parser.add_argument("--queue-model", choices=("risk", "log"), default="risk")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(args.market_id, args.entry_latency_ms, args.response_latency_ms, args.queue_model)
    output = args.output or OUT_DIR / f"r21_echtgeld_behavior_hft_replay_{args.market_id}_v1_report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "path": str(output),
                "policyAudit": report["live"]["policyAudit"],
                "r21Audit": report["live"]["r21Audit"],
                "liveExecution": report["live"]["liveExecution"],
                "hftSummaries": [item["summary"] for item in report["fixedObservedActionReplays"]],
                "nativeClosedLoop": {
                    key: report["nativeClosedLoopHft"].get(key)
                    for key in ("decisions", "waitDecisions", "actionDecisions", "makerPlacements", "makerSideCounts", "sameSideAtSharedOrdinal", "sharedOrdinalCount", "firstMaker", "takerAttempts")
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
