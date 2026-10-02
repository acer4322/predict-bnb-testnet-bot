from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.predict_bot import unified_controller_paper_v2 as mod
from tools import evaluate_target_actual_fill_role_correction_v1 as role_v1
from tools import evaluate_target_replay_fidelity_gate_v1 as fidelity
from tools import hft_safe_floor_contingent_pair_smoke_v1 as pair_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools.hft_target_forced_action_memory_smoke3_v1 import submit_order


OUT = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT / "target_residual_taker_substitution_attribution_v1_preregistered.json"
PATIENT_ROWS = OUT / "target_observable_maker_cycle_reachability_v1_patient_parents.csv"
REPORT = OUT / "target_residual_taker_substitution_attribution_v1_report.json"
ROWS = OUT / "target_residual_taker_substitution_attribution_v1_orders.csv"
MARKET_ID = 1572594
EPS = 1e-9


def failed_parent_ids() -> list[str]:
    with PATIENT_ROWS.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [
        str(row["parentId"])
        for row in rows
        if int(row["marketId"]) == MARKET_ID
        and float(row["hftTerminalFilled"] or 0.0) < EPS
    ]


def replay(
    name: str,
    replacement_ids: set[str],
    events: Any,
    goals: list[dict[str, Any]],
    first_ms: int,
    last_ms: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    bt = ex.new_bt(
        events,
        entry_latency_ms=fidelity.ENTRY_MS,
        response_latency_ms=fidelity.RESPONSE_MS,
        queue_model="risk",
    )
    ex.initialize_bt(bt)
    by_submit: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for goal in goals:
        by_submit[int(goal["submit_ms"])].append(goal)
    poll_start = ((first_ms + fidelity.POLL_MS - 1) // fidelity.POLL_MS) * fidelity.POLL_MS
    timeline = sorted(
        set(range(poll_start, last_ms + 1, fidelity.POLL_MS))
        | set(by_submit)
        | {last_ms}
    )
    portfolio = {"UP": 0.0, "DOWN": 0.0}
    cash_before_fees = 0.0
    orders: dict[int, dict[str, Any]] = {}
    order_num = 0

    def harvest(now_ms: int) -> None:
        nonlocal cash_before_fees
        for meta in orders.values():
            snapshot = ex.order_snapshot(bt, int(meta["order_num"]))
            cumulative = float(snapshot.get("cumExecQty") or 0.0)
            previous = float(meta.get("prev_cum") or 0.0)
            if cumulative > previous + EPS:
                delta = cumulative - previous
                outcome_price = pair_v1.outcome_fill_price(
                    str(meta["side"]), snapshot.get("execPrice"), float(meta["submitted_price"])
                )
                portfolio[str(meta["side"])] += delta
                cash_before_fees -= delta * outcome_price
                meta["filled_shares"] += delta
                meta["fill_notional"] += delta * outcome_price
                meta["prev_cum"] = cumulative
                meta["last_fill_exchange_ms"] = int(
                    (snapshot.get("exchangeTs") or now_ms * 1_000_000) // 1_000_000
                )
            meta["status"] = str(snapshot.get("status") or "NONE")

    try:
        for now_ms in timeline:
            if not ex.advance_to(bt, int(now_ms)):
                break
            harvest(int(now_ms))
            for goal in by_submit.get(int(now_ms), []):
                parent_id = str(goal["parent_id"])
                side = str(goal["target_side"]).upper()
                route = "TAKER_SUBSTITUTE" if parent_id in replacement_ids else "PASSIVE_TARGET"
                strict_past_ask: float | None = None
                if route == "TAKER_SUBSTITUTE":
                    strict_past_ask = pair_v1.outcome_ask(bt, side)
                    if strict_past_ask is None:
                        raise RuntimeError(f"no strict-past {side} ask for {parent_id}")
                    submitted_price = min(
                        0.99,
                        round(float(strict_past_ask) + 2.0 * float(mod.GRID), 2),
                    )
                else:
                    submitted_price = float(goal["target_price"])

                order_num += 1
                quantity = float(goal["expected_parent_shares"])
                if route == "TAKER_SUBSTITUTE":
                    rc = submit_order(bt, order_num, side, submitted_price, quantity)
                else:
                    rc = fidelity.submit_native(
                        bt,
                        order_num,
                        str(goal["native_book_side"]).upper(),
                        round(float(goal["native_price"]), 2),
                        quantity,
                    )
                orders[order_num] = {
                    "order_num": order_num,
                    "parent_id": parent_id,
                    "side": side,
                    "route": route,
                    "submit_local_ms": int(goal["submit_ms"]),
                    "placement_received_ms": int(goal["placement_received_ms"]),
                    "target_passive_price": float(goal["target_price"]),
                    "strict_past_ask": strict_past_ask,
                    "submitted_price": submitted_price,
                    "quantity": quantity,
                    "submit_rc": int(rc),
                    "prev_cum": 0.0,
                    "filled_shares": 0.0,
                    "fill_notional": 0.0,
                    "last_fill_exchange_ms": None,
                    "status": "SUBMITTED",
                }
        harvest(last_ms)
    finally:
        bt.close()

    rows: list[dict[str, Any]] = []
    taker_fees = 0.0
    for meta in orders.values():
        filled = float(meta["filled_shares"])
        average_price = float(meta["fill_notional"]) / filled if filled > EPS else None
        fee = (
            mod.taker_fee(filled, average_price, mod.FEE_BPS)
            if meta["route"] == "TAKER_SUBSTITUTE" and average_price is not None
            else 0.0
        )
        taker_fees += fee
        rows.append(
            {
                "variant": name,
                "parentId": meta["parent_id"],
                "side": meta["side"],
                "route": meta["route"],
                "submitLocalMs": meta["submit_local_ms"],
                "placementReceivedMs": meta["placement_received_ms"],
                "targetPassivePrice": meta["target_passive_price"],
                "strictPastAsk": meta["strict_past_ask"] if meta["strict_past_ask"] is not None else "",
                "submittedPrice": meta["submitted_price"],
                "quantity": meta["quantity"],
                "submitRc": meta["submit_rc"],
                "terminalFilledShares": filled,
                "averageFillPrice": average_price if average_price is not None else "",
                "executionPremiumPerShareVsTargetPassive": (
                    average_price - float(meta["target_passive_price"])
                    if average_price is not None
                    else ""
                ),
                "takerFeeAudit": fee,
                "lastFillExchangeMs": meta["last_fill_exchange_ms"]
                if meta["last_fill_exchange_ms"] is not None
                else "",
                "finalStatus": meta["status"],
            }
        )

    target = {"UP": 0.0, "DOWN": 0.0}
    for goal in goals:
        target[str(goal["target_side"]).upper()] += float(goal["target_filled_shares"])
    target_gross = target["UP"] + target["DOWN"]
    target_pair = min(target.values())
    actual_pair = min(portfolio.values())
    l1_gap = sum(abs(portfolio[side] - target[side]) for side in ("UP", "DOWN"))
    cash_after_fees = cash_before_fees - taker_fees
    replacement_rows = [row for row in rows if row["route"] == "TAKER_SUBSTITUTE"]
    summary = {
        "variant": name,
        "replacedParents": len(replacement_ids),
        "replacementActualFilledShares": sum(
            float(row["terminalFilledShares"]) for row in replacement_rows
        ),
        "replacementFullFillCount": sum(
            float(row["terminalFilledShares"]) >= float(row["quantity"]) - 0.05
            for row in replacement_rows
        ),
        "submitRejects": sum(int(row["submitRc"]) != 0 for row in rows),
        "targetShares": target,
        "hftActualFillShares": portfolio,
        "targetPairedShares": target_pair,
        "hftPairedShares": actual_pair,
        "pairedShareReachability": actual_pair / target_pair if target_pair > EPS else None,
        "portfolioL1GapShares": l1_gap,
        "normalizedPortfolioL1Gap": l1_gap / target_gross if target_gross > EPS else None,
        "signedNetGapShares": abs(
            (portfolio["UP"] - portfolio["DOWN"])
            - (target["UP"] - target["DOWN"])
        ),
        "cashBeforeFees": cash_before_fees,
        "takerFees": taker_fees,
        "cashAfterFees": cash_after_fees,
        "winnerFreeWorstCaseFloorAfterFees": cash_after_fees + actual_pair,
        "winnerFreeBestCaseAuditAfterFees": cash_after_fees + max(portfolio.values()),
    }
    return summary, rows


def main() -> None:
    if not PREREG.exists():
        raise RuntimeError(f"missing preregistration: {PREREG}")
    failed_ids = failed_parent_ids()
    if len(failed_ids) != 5:
        raise RuntimeError(f"expected five frozen residual parents, got {len(failed_ids)}")
    events, _, feed = tape_v1.build_archive_events(MARKET_ID, trade_offset="mid")
    first_ms = int(events[0]["local_ts"] // 1_000_000)
    last_ms = int(feed["lastReceivedMs"])
    goals = role_v1.prepare_goals(
        fidelity.load_parents(MARKET_ID), first_ms, last_ms
    )
    interventions: list[tuple[str, set[str]]] = [("LITERAL_PASSIVE_ALL", set())]
    interventions.extend(
        (f"REPLACE_ONE_{index + 1}", {parent_id})
        for index, parent_id in enumerate(failed_ids)
    )
    interventions.append(("REPLACE_ALL_RESIDUAL5", set(failed_ids)))

    summaries: dict[str, dict[str, Any]] = {}
    all_rows: list[dict[str, Any]] = []
    for name, replacement_ids in interventions:
        summary, rows = replay(
            name, replacement_ids, events, goals, first_ms, last_ms
        )
        summary["replacementParentIds"] = sorted(replacement_ids)
        summaries[name] = summary
        all_rows.extend(rows)
    if all_rows:
        with ROWS.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(all_rows[0]))
            writer.writeheader()
            writer.writerows(all_rows)

    base = summaries["LITERAL_PASSIVE_ALL"]
    joint = summaries["REPLACE_ALL_RESIDUAL5"]
    per_parent: list[dict[str, Any]] = []
    for index, parent_id in enumerate(failed_ids):
        single = summaries[f"REPLACE_ONE_{index + 1}"]
        replacement_row = next(
            row
            for row in all_rows
            if row["variant"] == f"REPLACE_ONE_{index + 1}"
            and row["parentId"] == parent_id
        )
        per_parent.append(
            {
                "parentId": parent_id,
                "targetSide": replacement_row["side"],
                "targetPassivePrice": replacement_row["targetPassivePrice"],
                "actualTakerFillPrice": replacement_row["averageFillPrice"],
                "actualTakerFilledShares": replacement_row["terminalFilledShares"],
                "takerFee": replacement_row["takerFeeAudit"],
                "normalizedL1GapImprovementVsBase": float(base["normalizedPortfolioL1Gap"])
                - float(single["normalizedPortfolioL1Gap"]),
                "floorDeltaVsBase": float(single["winnerFreeWorstCaseFloorAfterFees"])
                - float(base["winnerFreeWorstCaseFloorAfterFees"]),
                "pairedSharesDeltaVsBase": float(single["hftPairedShares"])
                - float(base["hftPairedShares"]),
            }
        )

    joint_l1_improvement = float(base["normalizedPortfolioL1Gap"]) - float(
        joint["normalizedPortfolioL1Gap"]
    )
    joint_floor_delta = float(joint["winnerFreeWorstCaseFloorAfterFees"]) - float(
        base["winnerFreeWorstCaseFloorAfterFees"]
    )
    if joint_l1_improvement >= 0.10 - EPS and joint_floor_delta >= 1.0 - EPS:
        decision = "KEEP_BOUNDED_ACTIVE_COMPLETION"
    elif joint_l1_improvement >= 0.10 - EPS and joint_floor_delta < -1.0 + EPS:
        decision = "KEEP_MANIFOLD_ATTRIBUTION_REJECT_UNCONDITIONAL_COMPLETION"
    elif joint_l1_improvement < 0.05 - EPS:
        decision = "REJECT_CAUSAL_COMPLETION_HYPOTHESIS"
    else:
        decision = "NEED_MORE_DATA"

    report = {
        "reportVersion": "TARGET_RESIDUAL_TAKER_SUBSTITUTION_ATTRIBUTION_V1",
        "researchOnly": True,
        "performanceClaim": False,
        "preregisteredContract": PREREG.name,
        "hypothesis": "The five passive residual failures causally explain the observable manifold gap; full-cycle passive-to-Taker substitutions quantify the marginal benefit and economic cost.",
        "cohort": {
            "marketId": MARKET_ID,
            "openedDevelopmentOnly": True,
            "officialHftForward": False,
            "qualifiedObservableMakerGoals": len(goals),
            "oracleSelectedResidualParents": len(failed_ids),
        },
        "executionSemantics": {
            "engine": "HftBacktest + Predict Execution Tape V1",
            "queue": "risk",
            "entryLatencyMs": fidelity.ENTRY_MS,
            "responseLatencyMs": fidelity.RESPONSE_MS,
            "pollMs": fidelity.POLL_MS,
            "partialFill": True,
            "dreamFill": False,
            "passive": "Exact Target native placement, GTC through Tape end",
            "substitution": "Same arrival-aligned decision time; strict-past outcome ask +2 ticks capped 0.99; GTC marketable limit",
            "fees": "Configured Predict Taker fee on actual substituted fills",
        },
        "variants": summaries,
        "perParentAttribution": per_parent,
        "jointComparison": {
            "normalizedL1GapImprovementVsBase": joint_l1_improvement,
            "winnerFreeFloorDeltaVsBase": joint_floor_delta,
            "pairedSharesDeltaVsBase": float(joint["hftPairedShares"])
            - float(base["hftPairedShares"]),
            "takerFees": joint["takerFees"],
        },
        "decision": decision,
        "WAIT_ACT": {
            "base": {"WAIT": 0, "ACT": len(goals), "actRate": 1.0},
            "joint": {"WAIT": 0, "ACT": len(goals), "actRate": 1.0},
            "note": "Offline route substitution attribution; no learned gate or WAIT decision was evaluated.",
        },
        "oracleValueCeiling": "Joint replacement is an opened-outcome component-attribution ceiling, not a policy-value oracle.",
        "learnedPolicyRealizedValue": "N/A: no learned policy",
        "chronologicalUnseenOOS": "Not claimed; one opened diagnostic market",
        "limitations": [
            "Residual parents are selected using the already revealed base replay result and cannot be runtime inputs.",
            "This substitutes Taker at the original arrival-aligned decision and therefore measures a route ceiling, not a causal detection policy.",
            "Only the observable 39 fully-filled Maker goals are replayed; hidden Target orders, full initial inventory, and Target Takers are absent.",
            "Winner-free floor on this isolated observable subset is diagnostic and is not the Target's complete portfolio PnL.",
        ],
        "artifacts": {"orderRows": str(ROWS.resolve())},
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "report": str(REPORT),
                "base": base,
                "joint": joint,
                "perParentAttribution": per_parent,
                "jointComparison": report["jointComparison"],
                "decision": decision,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
