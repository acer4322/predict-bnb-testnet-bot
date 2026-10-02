from __future__ import annotations

import json
import math
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
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners


OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT_DIR / "hft_taker_seed_passive_conversion_smoke5_v1_preregistered.json"
REPORT = OUT_DIR / "hft_taker_seed_passive_conversion_smoke5_v1_report.json"
TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
MARKETS = [1541829, 1554238, 1555937, 1556296, 1556493]
SIDES = ("UP", "DOWN")
REPAIR_OFFSETS = (0, 1)
QTY = 18.0
GRID = float(mod.GRID)
EPS = 1e-9
POLL_MS = 250
SEED_CONFIRM_TIMEOUT_MS = 2200
TERMINAL = {"FILLED", "REJECTED", "EXPIRED", "CANCELED"}


def outcome_bid(bt: Any, side: str) -> float | None:
    book = pair_v1.hft_book(bt)
    if side == "UP":
        return max(book["bids"]) if book["bids"] else None
    return 1.0 - min(book["asks"]) if book["asks"] else None


def submit_limit(bt: Any, order_num: int, side: str, price: float, quantity: float) -> int:
    native_side, native_price = ex.native_order(side, price)
    if native_side == "BUY":
        return int(bt.submit_buy_order(0, order_num, native_price, quantity, ex.hbt.GTC, ex.LIMIT, False))
    return int(bt.submit_sell_order(0, order_num, native_price, quantity, ex.hbt.GTC, ex.LIMIT, False))


def request_cancel(bt: Any, order_num: int, meta: dict[str, Any], now_ms: int, lifecycle: list[dict[str, Any]], reason: str) -> None:
    if meta.get("cancelRequested"):
        return
    snapshot = ex.order_snapshot(bt, order_num)
    if str(snapshot.get("status")) not in {"NEW", "PARTIALLY_FILLED"}:
        return
    order = bt.orders(0).get(order_num)
    if order is not None and bool(order.cancellable):
        bt.cancel(0, order_num, False)
        meta["cancelRequested"] = True
        lifecycle.append({"atMs": now_ms, "action": "CANCEL_REQUESTED", "orderNum": order_num, "reason": reason})


def run_seed_option(market_id: int, seed_side: str, repair_offset: int) -> dict[str, Any]:
    checkpoint = pair_v1.selected_checkpoint(market_id)
    checkpoint_ms = int(checkpoint["sampledAtMs"])
    events, _, feed = tape_v1.build_archive_events(market_id, trade_offset="mid")
    bt = ex.new_bt(events, entry_latency_ms=1092, response_latency_ms=273, queue_model="risk")
    ex.initialize_bt(bt)
    opposite = "DOWN" if seed_side == "UP" else "UP"
    orders: dict[int, dict[str, Any]] = {}
    fills: list[dict[str, Any]] = []
    lifecycle: list[dict[str, Any]] = []
    violations: list[str] = []
    up = down = cash = taker_fees = 0.0
    repair_submitted = False
    seed_terminal_observed = False
    internal_wait_reason: str | None = None
    entry_state: dict[str, Any] = {}

    def apply_fill(meta: dict[str, Any], quantity: float, price: float, at_ms: int) -> None:
        nonlocal up, down, cash, taker_fees
        fee = mod.taker_fee(quantity, price, mod.FEE_BPS) if meta["role"] == "TAKER" else 0.0
        if meta["side"] == "UP":
            up += quantity
        else:
            down += quantity
        cash -= price * quantity + fee
        taker_fees += fee
        fills.append({"atMs": at_ms, "role": meta["role"], "side": meta["side"], "price": price, "shares": quantity, "feeUsdt": fee})

    def harvest(now_ms: int) -> None:
        for order_num, meta in list(orders.items()):
            snapshot = ex.order_snapshot(bt, order_num)
            cumulative = float(snapshot.get("cumExecQty") or 0.0)
            previous = float(meta.get("prevCum") or 0.0)
            if cumulative > previous + EPS:
                quantity = cumulative - previous
                price = pair_v1.outcome_fill_price(meta["side"], snapshot.get("execPrice"), float(meta["price"]))
                exchange_ms = int((snapshot.get("exchangeTs") or now_ms * 1_000_000) // 1_000_000)
                apply_fill(meta, quantity, price, exchange_ms)
                meta["prevCum"] = cumulative
                lifecycle.append({"atMs": now_ms, "action": "ACTUAL_FILL", "orderNum": order_num, "role": meta["role"], "side": meta["side"], "deltaShares": quantity})
            meta["lastStatus"] = snapshot.get("status")

    try:
        ex.advance_to(bt, checkpoint_ms)
        seed_ask = pair_v1.outcome_ask(bt, seed_side)
        repair_bid = outcome_bid(bt, opposite)
        if seed_ask is None or repair_bid is None:
            internal_wait_reason = "MISSING_ENTRY_BOOK"
            seed_limit = None
            initial_repair_price = None
            projected_floor_at_limits = None
        else:
            seed_limit = min(0.99, round(float(seed_ask) + 2.0 * GRID, 2))
            initial_repair_price = max(0.01, round(float(repair_bid) - repair_offset * GRID, 2))
            projected_floor_at_limits = (
                QTY
                - QTY * seed_limit
                - mod.taker_fee(QTY, seed_limit, mod.FEE_BPS)
                - QTY * initial_repair_price
            )
            if projected_floor_at_limits <= EPS:
                internal_wait_reason = "NONPOSITIVE_ENTRY_PAIR_FLOOR"

        up_proxy = round(float(checkpoint["predictUpBid"]), 2)
        down_proxy = round(float(checkpoint["predictDownBid"]), 2)
        entry_state = pair_v1.strict_past_entry_state(bt, events, checkpoint, checkpoint_ms, up_proxy, down_proxy)
        entry_state.update(
            {
                "seedSide": seed_side,
                "seedAsk": seed_ask,
                "seedLimit": seed_limit,
                "repairSide": opposite,
                "initialRepairBestBid": repair_bid,
                "initialRepairPrice": initial_repair_price,
                "repairOffset": repair_offset,
                "projectedFloorAtEntryLimits": projected_floor_at_limits,
            }
        )
        if internal_wait_reason is not None:
            lifecycle.append({"atMs": checkpoint_ms, "action": "FORMAL_WAIT", "reason": internal_wait_reason})
        else:
            rc = submit_limit(bt, 1, seed_side, float(seed_limit), QTY)
            orders[1] = {
                "side": seed_side,
                "role": "TAKER",
                "price": float(seed_limit),
                "qty": QTY,
                "prevCum": 0.0,
                "cancelRequested": False,
                "submittedAtMs": checkpoint_ms,
                "submitRc": rc,
            }
            lifecycle.append({"atMs": checkpoint_ms, "action": "TAKER_SEED_SUBMITTED", "side": seed_side, "limitPrice": seed_limit, "quantity": QTY, "submitRc": rc})

        last_ms = int(feed["lastReceivedMs"])
        now_ms = checkpoint_ms
        while internal_wait_reason is None and now_ms < last_ms:
            now_ms = min(last_ms, now_ms + POLL_MS)
            if not ex.advance_to(bt, now_ms):
                break
            harvest(now_ms)
            seed_snapshot = ex.order_snapshot(bt, 1)
            seed_status = str(seed_snapshot.get("status"))
            if now_ms - checkpoint_ms >= SEED_CONFIRM_TIMEOUT_MS and seed_status in {"NEW", "PARTIALLY_FILLED"}:
                request_cancel(bt, 1, orders[1], now_ms, lifecycle, "BOUNDED_TAKER_SEED_TIMEOUT")
            seed_status = str(ex.order_snapshot(bt, 1).get("status"))
            if not seed_terminal_observed and seed_status in TERMINAL:
                seed_terminal_observed = True
                seed_filled = float(orders[1].get("prevCum") or 0.0)
                lifecycle.append({"atMs": now_ms, "action": "TAKER_SEED_TERMINAL_ACK", "status": seed_status, "filledShares": seed_filled})
                if seed_filled > EPS:
                    current_bid = outcome_bid(bt, opposite)
                    if current_bid is None:
                        lifecycle.append({"atMs": now_ms, "action": "PASSIVE_REPAIR_ABORTED", "reason": "MISSING_REPAIR_BOOK"})
                    else:
                        raw_price = max(0.01, round(float(current_bid) - repair_offset * GRID, 2))
                        max_safe_price = (cash + seed_filled) / seed_filled
                        safe_cap = math.floor((max_safe_price - EPS) / GRID) * GRID
                        repair_price = round(min(raw_price, safe_cap), 2)
                        if repair_price < 0.01:
                            lifecycle.append({"atMs": now_ms, "action": "PASSIVE_REPAIR_ABORTED", "reason": "NO_POSITIVE_FLOOR_QUOTE", "maxSafePrice": max_safe_price})
                        else:
                            rc = ex.submit_native(bt, 2, opposite, repair_price, seed_filled)
                            orders[2] = {
                                "side": opposite,
                                "role": "MAKER",
                                "price": repair_price,
                                "qty": seed_filled,
                                "prevCum": 0.0,
                                "cancelRequested": False,
                                "submittedAtMs": now_ms,
                                "submitRc": int(rc),
                            }
                            repair_submitted = True
                            lifecycle.append({"atMs": now_ms, "action": "PASSIVE_REPAIR_SUBMITTED", "side": opposite, "price": repair_price, "quantity": seed_filled, "submitRc": int(rc), "maxSafePrice": max_safe_price})
            if repair_submitted:
                repair_snapshot = ex.order_snapshot(bt, 2)
                if str(repair_snapshot.get("status")) == "FILLED":
                    lifecycle.append({"atMs": now_ms, "action": "PROTECTED_CONVERSION_COMPLETED"})
                    break

        harvest(last_ms)
        if 1 in orders:
            request_cancel(bt, 1, orders[1], last_ms, lifecycle, "TAPE_END")
        if 2 in orders:
            request_cancel(bt, 2, orders[2], last_ms, lifecycle, "TAPE_END")
        if 1 in orders and str(ex.order_snapshot(bt, 1).get("status")) not in TERMINAL:
            violations.append("TAKER_SEED_NOT_TERMINAL_BEFORE_TAPE_END")
        if repair_submitted and not seed_terminal_observed:
            violations.append("PASSIVE_REPAIR_BEFORE_SEED_TERMINAL_ACK")
    finally:
        bt.close()

    seed_filled = sum(float(fill["shares"]) for fill in fills if fill["role"] == "TAKER")
    repair_filled = sum(float(fill["shares"]) for fill in fills if fill["role"] == "MAKER")
    floor = cash + min(up, down)
    best = cash + max(up, down)
    winner = winners([market_id]).get(market_id)
    realized_pnl = cash + (up if winner == "UP" else down if winner == "DOWN" else 0.0)
    if floor > EPS:
        terminal_state = "SAFE_POSITIVE_FLOOR"
    elif not fills:
        terminal_state = "NO_FILL_WAIT_EQUIVALENT"
    elif best > EPS:
        terminal_state = "DIRECTIONAL_OPTIONALITY"
    else:
        terminal_state = "DOMINATED_NEGATIVE_BEST"
    return {
        "marketId": market_id,
        "action": f"TAKER_SEED_{seed_side}__PASSIVE_REPAIR_OFFSET{repair_offset}",
        "seedSide": seed_side,
        "repairSide": opposite,
        "repairOffset": repair_offset,
        "checkpointMs": checkpoint_ms,
        "secondsLeft": float(checkpoint["secondsLeft"]),
        "strictPastEntryState": entry_state,
        "internalFormalWait": internal_wait_reason is not None,
        "internalWaitReason": internal_wait_reason,
        "actualExecution": {
            "seedTakerFilledShares": seed_filled,
            "passiveRepairFilledShares": repair_filled,
            "upShares": up,
            "downShares": down,
            "cash": cash,
            "takerFeesUsdt": taker_fees,
            "worstCaseFloor": floor,
            "bestCasePnl": best,
            "realizedPnlAudit": realized_pnl,
            "terminalState": terminal_state,
        },
        "fills": fills,
        "lifecycle": lifecycle,
        "cycleInvariantViolations": violations,
        "cycleInvariantViolationCount": len(violations),
    }


def target_program_audit() -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"file:{TARGET_DB.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        output: list[dict[str, Any]] = []
        for market_id in MARKETS:
            rows = [
                dict(row)
                for row in connection.execute(
                    """SELECT role,COUNT(*) parents,SUM(shares) shares,
                              MIN(first_event_ms) first_ms,MAX(last_event_ms) last_ms
                         FROM target_parent_orders WHERE market_id=? GROUP BY role ORDER BY role""",
                    (market_id,),
                )
            ]
            first = min(rows, key=lambda row: int(row["first_ms"]))["role"] if rows else None
            output.append({"marketId": market_id, "firstObservedRole": first, "roles": rows})
        return output
    finally:
        connection.close()


def main() -> None:
    if not PREREG.exists():
        raise RuntimeError(f"missing preregistration: {PREREG}")
    rows: list[dict[str, Any]] = []
    market_summaries: list[dict[str, Any]] = []
    expanded_oracle_value = 0.0
    existing_oracle_value = 0.0
    seed_oracle_value = 0.0
    seed_oracle_markets = 0
    expanded_actions: dict[str, int] = {}

    for index, market_id in enumerate(MARKETS, 1):
        pair = pair_v1.run_offset(market_id, 1)
        seed_rows = [run_seed_option(market_id, side, offset) for side in SIDES for offset in REPAIR_OFFSETS]
        rows.extend(seed_rows)
        pair_floor = (
            float(pair["actualExecution"]["worstCaseFloor"])
            if int(pair["cycleInvariantViolationCount"]) == 0
            else -math.inf
        )
        valid_seed = [row for row in seed_rows if int(row["cycleInvariantViolationCount"]) == 0]
        best_seed = max(valid_seed, key=lambda row: float(row["actualExecution"]["worstCaseFloor"])) if valid_seed else None
        best_seed_floor = float(best_seed["actualExecution"]["worstCaseFloor"]) if best_seed is not None else -math.inf
        existing_floor = max(0.0, pair_floor)
        seed_floor = max(0.0, best_seed_floor)
        candidates = [("WAIT", 0.0), ("CONTINGENT_PAIR_OFFSET1", pair_floor)]
        candidates.extend((str(row["action"]), float(row["actualExecution"]["worstCaseFloor"])) for row in valid_seed)
        action, expanded_floor = max(candidates, key=lambda item: item[1])
        expanded_floor = max(0.0, expanded_floor)
        existing_oracle_value += existing_floor
        seed_oracle_value += seed_floor
        expanded_oracle_value += expanded_floor
        seed_oracle_markets += int(seed_floor > EPS)
        expanded_actions[action] = expanded_actions.get(action, 0) + 1
        market_summaries.append(
            {
                "marketId": market_id,
                "pairOffset1Floor": pair_floor,
                "seedOracleAction": "WAIT" if seed_floor <= EPS else str(best_seed["action"]),
                "seedOracleFloor": seed_floor,
                "expandedOracleAction": action,
                "expandedOracleFloor": expanded_floor,
                "incrementalFloorVsWaitPlusPair": expanded_floor - existing_floor,
                "pairCycleInvariantViolations": pair["cycleInvariantViolations"],
            }
        )
        print(json.dumps({"progress": index, **market_summaries[-1]}, ensure_ascii=False), flush=True)

    incremental = expanded_oracle_value - existing_oracle_value
    oracle_seed_violations = sum(
        int(row["cycleInvariantViolationCount"])
        for summary in market_summaries
        if str(summary["seedOracleAction"]) != "WAIT"
        for row in rows
        if int(row["marketId"]) == int(summary["marketId"]) and str(row["action"]) == str(summary["seedOracleAction"])
    )
    keep = seed_oracle_markets >= 3 and incremental >= 2.0 - EPS and oracle_seed_violations == 0
    need_more = seed_oracle_markets > 0 and incremental > EPS and not keep
    decision = (
        "KEEP_TAKER_SEED_CONVERSION_FOR_CHRONOLOGICAL_PILOT"
        if keep
        else "NEED_MORE_DATA_TAKER_SEED_ACTION_FAMILY"
        if need_more
        else "REJECT_TAKER_SEED_PASSIVE_CONVERSION_ACTION_FAMILY"
    )
    report = {
        "reportVersion": "HFT_TAKER_SEED_PASSIVE_CONVERSION_SMOKE5_V1",
        "researchOnly": True,
        "preregisteredContract": PREREG.name,
        "hypothesis": "A bounded Taker-first seed followed only after terminal seed ACK by actual-fill-sized passive opposite-side conversion can add a full-cycle safe-floor option that is absent from the Maker-first protected-pair family.",
        "dedupBoundary": {
            "oldOpenSeed": "Recorded OUR OPEN_SEED fills and optimistic public-book/paper continuation; not performance-grade HftBacktest action generation.",
            "oldTakerWork": "Isolated Taker terminal actions or post-Taker SAME/OPP classifiers; no Taker-first HftBacktest seed with passive terminal-floor conversion.",
            "thisTest": "Flat start, generated Taker seed, confirmed actual fills, terminal seed ACK, passive opposite repair, winner-free terminal floor and formal WAIT.",
        },
        "cohort": {
            "markets": MARKETS,
            "selection": "Earliest five chronological pre-official COMPLETE_FORWARD_V1 Target-plus-Tape overlap markets; selected without Target role/order outcomes.",
            "openedDevelopmentOnly": True,
            "chronologicalUnseenOos": False,
            "officialHftForward": False,
            "special20260816": False,
            "supervisorFinal75To99": False,
        },
        "runtimeInputs": {
            "strictPastPublicBook": True,
            "actualFillOwnState": True,
            "targetTimingSideActionOrInventory": False,
            "r2OrPaperIntent": False,
            "winnerSettlementOrPnl": False,
        },
        "executionSemantics": {
            "engine": "HftBacktest + Predict Execution Tape V1",
            "queue": "risk",
            "entryLatencyMs": 1092,
            "responseLatencyMs": 273,
            "ownStatePollMs": POLL_MS,
            "partialFill": True,
            "seedConfirmTimeoutMs": SEED_CONFIRM_TIMEOUT_MS,
            "seedTerminalAckBeforeRepair": True,
            "takerFee": True,
            "dreamFill": False,
        },
        "actionSpace": [
            "WAIT",
            "CONTINGENT_PAIR_OFFSET1",
            *[f"TAKER_SEED_{side}__PASSIVE_REPAIR_OFFSET{offset}" for side in SIDES for offset in REPAIR_OFFSETS],
        ],
        "targetProgramAuditOnly": target_program_audit(),
        "actionAttemptAudit": {
            "seedRows": len(rows),
            "internalFormalWaitRows": sum(bool(row["internalFormalWait"]) for row in rows),
            "takerSeedFillRows": sum(float(row["actualExecution"]["seedTakerFilledShares"]) > EPS for row in rows),
            "passiveRepairFillRows": sum(float(row["actualExecution"]["passiveRepairFilledShares"]) > EPS for row in rows),
            "positiveFloorRows": sum(float(row["actualExecution"]["worstCaseFloor"]) > EPS for row in rows),
            "negativeFloorRows": sum(float(row["actualExecution"]["worstCaseFloor"]) < -EPS for row in rows),
        },
        "oracle": {
            "waitValue": 0.0,
            "existingWaitPlusPairOffset1Value": existing_oracle_value,
            "seedOnlyWaitInclusiveValue": seed_oracle_value,
            "seedOnlyActMarkets": seed_oracle_markets,
            "seedOnlyActRate": seed_oracle_markets / len(MARKETS),
            "expandedWaitPairSeedValue": expanded_oracle_value,
            "expandedActionCounts": expanded_actions,
            "incrementalValueVsWaitPlusPair": incremental,
            "oracleSelectedSeedViolations": oracle_seed_violations,
        },
        "learnedPolicy": {
            "waitActRate": "N/A action-family ceiling only",
            "realizedValue": "N/A no policy fit",
            "chronologicalUnseenOos": "N/A opened development smoke",
        },
        "lockedDecisionGate": {
            "minimumPositiveSeedOracleMarkets": 3,
            "minimumIncrementalFloorVsWaitPlusPairUsdt": 2.0,
            "oracleSelectedSeedViolationsRequired": 0,
        },
        "marketSummaries": market_summaries,
        "rows": rows,
        "decision": decision,
        "nextStep": (
            "Freeze this action family and run one small chronological real-Tape policy-identifiability pilot; do not infer side from Target actions."
            if keep
            else "Do not tune seed timeout, size, side rule or passive offsets on these markets."
        ),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(REPORT), "decision": decision, "oracle": report["oracle"], "actionAttemptAudit": report["actionAttemptAudit"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
