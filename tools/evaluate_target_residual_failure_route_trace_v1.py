from __future__ import annotations

import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.predict_bot import unified_controller_paper_v2 as mod
from tools import hft_safe_floor_contingent_pair_smoke_v1 as pair_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools.hft_target_forced_action_memory_smoke3_v1 import submit_order


OUT = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT / "target_residual_failure_route_trace_v1_preregistered.json"
PATIENT_ROWS = OUT / "target_observable_maker_cycle_reachability_v1_patient_parents.csv"
TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
REPORT = OUT / "target_residual_failure_route_trace_v1_report.json"
ROWS = OUT / "target_residual_failure_route_trace_v1_routes.csv"
MARKET_ID = 1572594
ENTRY_MS = 1092
RESPONSE_MS = 273
POLL_MS = 250
HORIZON_MS = 5000
EPS = 1e-9


def ro(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def failed_parents() -> list[dict[str, Any]]:
    with PATIENT_ROWS.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [
        row
        for row in rows
        if int(row["marketId"]) == MARKET_ID and float(row["hftTerminalFilled"] or 0.0) < EPS
    ]


def target_trace(failures: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    connection = ro(TARGET_DB)
    try:
        events = [
            dict(row)
            for row in connection.execute(
                """SELECT event_ms,observed_at_ms,role,side,quote_type,order_hash,price,shares,leg_id
                     FROM wallet_shadow_target_events
                    WHERE market_id=? AND quote_type='BID'
                    ORDER BY event_ms,id""",
                (MARKET_ID,),
            )
        ]
    finally:
        connection.close()
    output: dict[str, dict[str, Any]] = {}
    for failure in failures:
        parent_id = str(failure["parentId"])
        order_hash = parent_id.split(":")[1]
        placement_ms = int(failure["placementReceivedMs"])
        fill_ms = int(failure["targetFirstFillSecondMs"])
        up = down = 0.0
        for event in events:
            if int(event["event_ms"]) >= fill_ms:
                break
            if event["side"] == "UP":
                up += float(event["shares"])
            elif event["side"] == "DOWN":
                down += float(event["shares"])
        exact_fill = sum(float(event["shares"]) for event in events if event["order_hash"] == order_hash)
        signed_before = up - down
        signed_after = signed_before - exact_fill
        effect = (
            "REPAIR_TOWARD_PAIR"
            if abs(signed_after) < abs(signed_before) - EPS
            else "DIRECTIONAL_ADD"
            if abs(signed_after) > abs(signed_before) + EPS
            else "NEUTRAL"
        )
        later = [event for event in events if int(event["event_ms"]) > fill_ms]
        next_opposite = next((event for event in later if event["role"] == "MAKER" and event["side"] == "UP"), None)
        next_same = next((event for event in later if event["role"] == "MAKER" and event["side"] == "DOWN"), None)
        next_taker = next((event for event in later if event["role"] == "TAKER"), None)
        output[parent_id] = {
            "orderHash": order_hash,
            "placementReceivedMs": placement_ms,
            "targetFillMs": fill_ms,
            "targetFillShares": exact_fill,
            "targetInventoryBeforeFill": {"up": up, "down": down, "signedNet": signed_before},
            "targetSignedNetAfterFill": signed_after,
            "targetCycleRole": effect,
            "nextSameSideMakerDelayMs": int(next_same["event_ms"]) - fill_ms if next_same else None,
            "nextOppositeMakerDelayMs": int(next_opposite["event_ms"]) - fill_ms if next_opposite else None,
            "nextOppositeMaker": {
                "eventMs": int(next_opposite["event_ms"]),
                "price": float(next_opposite["price"]),
                "shares": float(next_opposite["shares"]),
                "orderHash": str(next_opposite["order_hash"]),
            }
            if next_opposite
            else None,
            "nextTakerDelayMs": int(next_taker["event_ms"]) - fill_ms if next_taker else None,
        }
    return output


def run_route(events: Any, feed: dict[str, Any], failure: dict[str, Any], route: str) -> dict[str, Any]:
    placement_ms = int(failure["placementReceivedMs"])
    submit_ms = placement_ms if route == "REACTIVE_TAKER" else placement_ms - ENTRY_MS
    first_ms = int(events[0]["local_ts"] // 1_000_000)
    last_ms = int(feed["lastReceivedMs"])
    if submit_ms < first_ms or submit_ms > last_ms:
        return {"route": route, "error": "SUBMIT_OUTSIDE_TAPE"}
    bt = ex.new_bt(events, entry_latency_ms=ENTRY_MS, response_latency_ms=RESPONSE_MS, queue_model="risk")
    ex.initialize_bt(bt)
    order_num = 1
    try:
        if not ex.advance_to(bt, submit_ms):
            return {"route": route, "error": "ADVANCE_TO_SUBMIT_FAILED"}
        ask = pair_v1.outcome_ask(bt, "DOWN")
        if ask is None:
            return {"route": route, "error": "NO_STRICT_PAST_DOWN_ASK"}
        submitted_price = min(0.99, round(float(ask) + 2.0 * float(mod.GRID), 2))
        rc = submit_order(bt, order_num, "DOWN", submitted_price, 18.0)
        first_fill_ms: int | None = None
        filled_5s = 0.0
        deadline = min(last_ms, submit_ms + HORIZON_MS)
        now_ms = submit_ms
        while now_ms < deadline:
            now_ms = min(deadline, now_ms + POLL_MS)
            if not ex.advance_to(bt, now_ms):
                break
            snapshot = ex.order_snapshot(bt, order_num)
            cumulative = float(snapshot.get("cumExecQty") or 0.0)
            if cumulative > EPS and first_fill_ms is None:
                first_fill_ms = int((snapshot.get("exchangeTs") or now_ms * 1_000_000) // 1_000_000)
            filled_5s = cumulative
        if deadline < last_ms:
            ex.advance_to(bt, last_ms)
        terminal = ex.order_snapshot(bt, order_num)
        terminal_filled = float(terminal.get("cumExecQty") or 0.0)
        target_passive_price = 1.0 - float(failure["nativePrice"])
        executed_price = (
            pair_v1.outcome_fill_price("DOWN", terminal.get("execPrice"), submitted_price)
            if terminal_filled > EPS
            else None
        )
        if terminal_filled > EPS and first_fill_ms is None:
            first_fill_ms = int((terminal.get("exchangeTs") or last_ms * 1_000_000) // 1_000_000)
        return {
            "route": route,
            "submitLocalMs": submit_ms,
            "exchangeArrivalMs": submit_ms + ENTRY_MS,
            "strictPastDownAsk": float(ask),
            "submittedPrice": submitted_price,
            "submitRc": int(rc),
            "filledShares5s": filled_5s,
            "terminalFilledShares": terminal_filled,
            "targetPassivePrice": target_passive_price,
            "executedPrice": executed_price,
            "executionPremiumPerShareVsTargetPassive": executed_price - target_passive_price
            if executed_price is not None
            else None,
            "takerFeeUsdtAudit": mod.taker_fee(terminal_filled, executed_price, mod.FEE_BPS)
            if executed_price is not None
            else 0.0,
            "firstFillExchangeMs": first_fill_ms,
            "firstFillFromSubmitMs": first_fill_ms - submit_ms if first_fill_ms is not None else None,
            "finalStatus": str(terminal.get("status") or "NONE"),
        }
    finally:
        bt.close()


def classify(routes: list[dict[str, Any]]) -> str:
    by = {str(row["route"]): row for row in routes}
    reactive = float(by["REACTIVE_TAKER"].get("filledShares5s") or 0.0)
    prepositioned = float(by["PREPOSITIONED_TAKER_CEILING"].get("filledShares5s") or 0.0)
    terminal = max(float(row.get("terminalFilledShares") or 0.0) for row in routes)
    if reactive >= 17.95:
        return "LEGAL_REACTIVE_ACTIVE_ROUTE"
    if prepositioned >= 17.95:
        return "PREPOSITIONING_REQUIRED"
    if terminal < 17.95:
        return "NO_BOUNDED_ACTIVE_ROUTE"
    return "LATE_ACTIVE_ROUTE_ONLY"


def main() -> None:
    if not PREREG.exists():
        raise RuntimeError(f"missing preregistration: {PREREG}")
    failures = failed_parents()
    traces = target_trace(failures)
    events, _, feed = tape_v1.build_archive_events(MARKET_ID, trade_offset="mid")
    rows: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    for failure in failures:
        parent_id = str(failure["parentId"])
        routes = [
            run_route(events, feed, failure, route)
            for route in ("REACTIVE_TAKER", "PREPOSITIONED_TAKER_CEILING")
        ]
        classification = classify(routes)
        trace = traces[parent_id]
        cases.append(
            {
                "parentId": parent_id,
                "targetSide": failure["targetSide"],
                "targetPrice": 1.0 - float(failure["nativePrice"]),
                "targetTrace": trace,
                "routes": routes,
                "classification": classification,
            }
        )
        for route in routes:
            rows.append(
                {
                    "parentId": parent_id,
                    "targetCycleRole": trace["targetCycleRole"],
                    "targetFillMs": trace["targetFillMs"],
                    "classification": classification,
                    **route,
                }
            )
    if rows:
        with ROWS.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    counts: dict[str, int] = {}
    roles: dict[str, int] = {}
    for case in cases:
        counts[case["classification"]] = counts.get(case["classification"], 0) + 1
        role = str(case["targetTrace"]["targetCycleRole"])
        roles[role] = roles.get(role, 0) + 1
    report = {
        "reportVersion": "TARGET_RESIDUAL_FAILURE_ROUTE_TRACE_V1",
        "researchOnly": True,
        "performanceClaim": False,
        "preregisteredContract": PREREG.name,
        "cohort": {"marketId": MARKET_ID, "failedParents": len(cases), "openedDevelopmentOnly": True},
        "executionSemantics": {
            "engine": "HftBacktest + Predict Execution Tape V1",
            "queue": "risk",
            "entryLatencyMs": ENTRY_MS,
            "responseLatencyMs": RESPONSE_MS,
            "pollMs": POLL_MS,
            "takerLimit": "strict-past outcome ask +2 ticks capped 0.99",
            "partialFill": True,
            "dreamFill": False,
        },
        "targetCycleRoleCounts": roles,
        "routeClassificationCounts": counts,
        "cases": cases,
        "waitActOracleValue": "N/A: mechanical route reachability and offline responsibility trace only",
        "limitations": [
            "The bounded Taker is a diagnostic route ceiling, not evidence that paying the spread preserves Target economics.",
            "Target cycle roles use official fills available only to the offline teacher; a runtime policy must infer goals from strict-past own/public state.",
            "Target-private unfilled/cancelled orders and exact queue position remain unavailable.",
            "Single opened market; no generalization or performance claim.",
        ],
        "artifacts": {"routeRows": str(ROWS.resolve())},
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "report": str(REPORT),
                "targetCycleRoleCounts": roles,
                "routeClassificationCounts": counts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
