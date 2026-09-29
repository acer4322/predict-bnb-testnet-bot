from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import evaluate_target_replay_fidelity_gate_v1 as fidelity
from tools import hft_safe_floor_contingent_pair_smoke_v1 as pair_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1


OUT = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT / "target_actual_fill_role_correction_v1_preregistered.json"
REPORT = OUT / "target_actual_fill_role_correction_v1_report.json"
DECISIONS = OUT / "target_actual_fill_role_correction_v1_decisions.csv"
MARKET_ID = 1572594
VARIANTS = ("LITERAL_PATIENT", "ACTUAL_FILL_ROLE_WAIT")
EPS = 1e-9


def economic_role(side: str, up_shares: float, down_shares: float) -> str:
    signed_net = float(up_shares) - float(down_shares)
    if abs(signed_net) <= EPS:
        return "FOUNDATION"
    dominant = "UP" if signed_net > 0 else "DOWN"
    return "DIRECTIONAL_OPTION" if str(side).upper() == dominant else "REPAIR"


def target_reference_before(
    parents: list[dict[str, Any]], current: dict[str, Any]
) -> tuple[float, float]:
    current_received_ms = int(current["placement_received_ms"])
    up_shares = 0.0
    down_shares = 0.0
    for prior in parents:
        if str(prior["parent_id"]) == str(current["parent_id"]):
            continue
        # Wallet target fills are second-resolution. Require the entire final
        # fill second to precede the observed placement update, rather than
        # inventing an ordering inside the same second.
        if int(prior["last_target_ms"]) + 999 >= current_received_ms:
            continue
        side = str(prior["target_side"]).upper()
        if side == "UP":
            up_shares += float(prior["target_filled_shares"])
        elif side == "DOWN":
            down_shares += float(prior["target_filled_shares"])
    return up_shares, down_shares


def prepare_goals(parents: list[dict[str, Any]], first_ms: int, last_ms: int) -> list[dict[str, Any]]:
    goals: list[dict[str, Any]] = []
    for parent in parents:
        submit_ms = int(parent["placement_received_ms"]) - fidelity.ENTRY_MS
        if submit_ms < first_ms or submit_ms > last_ms:
            continue
        target_up_before, target_down_before = target_reference_before(parents, parent)
        side = str(parent["target_side"]).upper()
        goals.append(
            {
                **parent,
                "submit_ms": submit_ms,
                "target_up_before": target_up_before,
                "target_down_before": target_down_before,
                "target_signed_net_before": target_up_before - target_down_before,
                "intended_role": economic_role(side, target_up_before, target_down_before),
            }
        )
    return sorted(goals, key=lambda row: (int(row["submit_ms"]), str(row["parent_id"])))


def replay_variant(
    variant: str,
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
    cash = 0.0
    orders: dict[int, dict[str, Any]] = {}
    decisions: list[dict[str, Any]] = []
    order_num = 0
    submit_rejects = 0

    def harvest(now_ms: int) -> None:
        nonlocal cash
        for meta in orders.values():
            snapshot = ex.order_snapshot(bt, int(meta["order_num"]))
            cumulative = float(snapshot.get("cumExecQty") or 0.0)
            previous = float(meta.get("prev_cum") or 0.0)
            if cumulative > previous + EPS:
                delta = cumulative - previous
                outcome_price = pair_v1.outcome_fill_price(
                    str(meta["side"]), snapshot.get("execPrice"), float(meta["target_price"])
                )
                portfolio[str(meta["side"])] += delta
                cash -= delta * outcome_price
                meta["fill_shares"] += delta
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
                side = str(goal["target_side"]).upper()
                actual_up_before = float(portfolio["UP"])
                actual_down_before = float(portfolio["DOWN"])
                actual_role = economic_role(side, actual_up_before, actual_down_before)
                intended_role = str(goal["intended_role"])
                role_mismatch = actual_role != intended_role
                action = (
                    "WAIT"
                    if variant == "ACTUAL_FILL_ROLE_WAIT" and role_mismatch
                    else "ACT"
                )
                decision: dict[str, Any] = {
                    "marketId": MARKET_ID,
                    "variant": variant,
                    "parentId": goal["parent_id"],
                    "placementReceivedMs": int(goal["placement_received_ms"]),
                    "submitLocalMs": int(goal["submit_ms"]),
                    "targetSide": side,
                    "nativeBookSide": str(goal["native_book_side"]),
                    "nativePrice": round(float(goal["native_price"]), 2),
                    "goalShares": float(goal["expected_parent_shares"]),
                    "targetFilledShares": float(goal["target_filled_shares"]),
                    "targetUpBefore": float(goal["target_up_before"]),
                    "targetDownBefore": float(goal["target_down_before"]),
                    "targetSignedNetBefore": float(goal["target_signed_net_before"]),
                    "actualUpBefore": actual_up_before,
                    "actualDownBefore": actual_down_before,
                    "actualSignedNetBefore": actual_up_before - actual_down_before,
                    "intendedRole": intended_role,
                    "actualFillRole": actual_role,
                    "roleMismatch": int(role_mismatch),
                    "action": action,
                    "orderNum": "",
                    "submitRc": "",
                    "terminalFilledShares": 0.0,
                    "terminalFillNotional": 0.0,
                    "finalStatus": "WAIT" if action == "WAIT" else "SUBMITTED",
                    "lastFillExchangeMs": "",
                }
                if action == "ACT":
                    order_num += 1
                    native_price = round(float(goal["native_price"]), 2)
                    quantity = float(goal["expected_parent_shares"])
                    rc = fidelity.submit_native(
                        bt,
                        order_num,
                        str(goal["native_book_side"]).upper(),
                        native_price,
                        quantity,
                    )
                    submit_rejects += int(rc != 0)
                    orders[order_num] = {
                        "order_num": order_num,
                        "decision": decision,
                        "side": side,
                        "target_price": float(goal["target_price"]),
                        "prev_cum": 0.0,
                        "fill_shares": 0.0,
                        "fill_notional": 0.0,
                        "last_fill_exchange_ms": None,
                        "status": "SUBMITTED",
                    }
                    decision["orderNum"] = order_num
                    decision["submitRc"] = rc
                decisions.append(decision)
        harvest(last_ms)
    finally:
        for meta in orders.values():
            decision = meta["decision"]
            decision["terminalFilledShares"] = float(meta["fill_shares"])
            decision["terminalFillNotional"] = float(meta["fill_notional"])
            decision["finalStatus"] = str(meta["status"])
            decision["lastFillExchangeMs"] = (
                int(meta["last_fill_exchange_ms"])
                if meta["last_fill_exchange_ms"] is not None
                else ""
            )
        bt.close()

    target = {"UP": 0.0, "DOWN": 0.0}
    for goal in goals:
        target[str(goal["target_side"]).upper()] += float(goal["target_filled_shares"])
    target_gross = target["UP"] + target["DOWN"]
    target_pair = min(target.values())
    actual_pair = min(portfolio.values())
    portfolio_l1_gap = sum(abs(portfolio[side] - target[side]) for side in ("UP", "DOWN"))
    act_count = sum(row["action"] == "ACT" for row in decisions)
    wait_count = len(decisions) - act_count
    role_mismatch_goals = sum(int(row["roleMismatch"]) for row in decisions)
    semantic_mismatch_submits = sum(
        int(row["roleMismatch"] and row["action"] == "ACT") for row in decisions
    )
    floor = cash + actual_pair
    summary = {
        "variant": variant,
        "goals": len(decisions),
        "ACT": act_count,
        "WAIT": wait_count,
        "actRate": act_count / len(decisions) if decisions else None,
        "waitRate": wait_count / len(decisions) if decisions else None,
        "intendedRoleCounts": dict(Counter(str(row["intendedRole"]) for row in decisions)),
        "actualFillRoleCounts": dict(Counter(str(row["actualFillRole"]) for row in decisions)),
        "roleMismatchGoals": role_mismatch_goals,
        "semanticRoleMismatchSubmits": semantic_mismatch_submits,
        "submitRejects": submit_rejects,
        "targetShares": target,
        "hftActualFillShares": portfolio,
        "targetPairedShares": target_pair,
        "hftPairedShares": actual_pair,
        "pairedShareReachability": actual_pair / target_pair if target_pair > EPS else None,
        "targetAbsNetShares": abs(target["UP"] - target["DOWN"]),
        "hftAbsNetShares": abs(portfolio["UP"] - portfolio["DOWN"]),
        "signedNetGapShares": abs(
            (portfolio["UP"] - portfolio["DOWN"])
            - (target["UP"] - target["DOWN"])
        ),
        "portfolioL1GapShares": portfolio_l1_gap,
        "normalizedPortfolioL1Gap": portfolio_l1_gap / target_gross if target_gross > EPS else None,
        "cashAfterExecution": cash,
        "winnerFreeWorstCaseFloor": floor,
        "winnerFreeBestCaseAudit": cash + max(portfolio.values()),
        "meanFillPrice": (-cash / (portfolio["UP"] + portfolio["DOWN"]))
        if portfolio["UP"] + portfolio["DOWN"] > EPS
        else None,
    }
    return summary, decisions


def main() -> None:
    if not PREREG.exists():
        raise RuntimeError(f"missing preregistration: {PREREG}")
    raw_events, _, feed = tape_v1.build_archive_events(MARKET_ID, trade_offset="mid")
    first_ms = int(raw_events[0]["local_ts"] // 1_000_000)
    last_ms = int(feed["lastReceivedMs"])
    parents = fidelity.load_parents(MARKET_ID)
    goals = prepare_goals(parents, first_ms, last_ms)
    summaries: dict[str, dict[str, Any]] = {}
    all_decisions: list[dict[str, Any]] = []
    for variant in VARIANTS:
        summary, decisions = replay_variant(
            variant, raw_events, goals, first_ms, last_ms
        )
        summaries[variant] = summary
        all_decisions.extend(decisions)

    if all_decisions:
        with DECISIONS.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(all_decisions[0]))
            writer.writeheader()
            writer.writerows(all_decisions)

    literal = summaries["LITERAL_PATIENT"]
    corrected = summaries["ACTUAL_FILL_ROLE_WAIT"]
    l1_improvement = float(literal["normalizedPortfolioL1Gap"]) - float(
        corrected["normalizedPortfolioL1Gap"]
    )
    floor_improvement = float(corrected["winnerFreeWorstCaseFloor"]) - float(
        literal["winnerFreeWorstCaseFloor"]
    )
    paired_retention = (
        float(corrected["hftPairedShares"]) / float(literal["hftPairedShares"])
        if float(literal["hftPairedShares"]) > EPS
        else None
    )
    paired_reachability_loss = float(literal["pairedShareReachability"]) - float(
        corrected["pairedShareReachability"]
    )
    if (
        int(corrected["semanticRoleMismatchSubmits"]) == 0
        and (l1_improvement >= 0.05 - EPS or floor_improvement >= 1.0 - EPS)
        and paired_retention is not None
        and paired_retention >= 0.50 - EPS
    ):
        decision = "KEEP_ROLE_CORRECTION_SIGNAL"
    elif (
        paired_reachability_loss > 0.25 + EPS
        and l1_improvement <= EPS
        and floor_improvement <= EPS
    ):
        decision = "REJECT_CURRENT_ROLE_WAIT_CORRECTION"
    else:
        decision = "NEED_MORE_DATA"

    report = {
        "reportVersion": "TARGET_ACTUAL_FILL_ROLE_CORRECTION_V1",
        "researchOnly": True,
        "performanceClaim": False,
        "preregisteredContract": PREREG.name,
        "hypothesis": "Some literal Target Maker actions become the wrong economic role after follower fill divergence; formal WAIT on those role inversions can preserve the cycle manifold or winner-free floor.",
        "cohort": {
            "marketId": MARKET_ID,
            "openedDevelopmentOnly": True,
            "officialHftForward": False,
            "qualifiedObservableMakerGoals": len(goals),
        },
        "executionSemantics": {
            "engine": "HftBacktest + Predict Execution Tape V1",
            "queue": "risk",
            "entryLatencyMs": fidelity.ENTRY_MS,
            "responseLatencyMs": fidelity.RESPONSE_MS,
            "pollMs": fidelity.POLL_MS,
            "partialFill": True,
            "dreamFill": False,
            "orderType": "GTC passive exact Target native placement through Tape end",
            "stateFeedback": "Only confirmed HftBacktest fills update inventory and cash before a later decision.",
            "roleCorrection": "ACT only if strict-past Target role equals the role under follower actual-fill inventory; otherwise formal WAIT.",
        },
        "feed": {
            key: feed[key]
            for key in (
                "updates",
                "rawMatchRows",
                "normalizedTrades",
                "firstReceivedMs",
                "lastReceivedMs",
            )
        },
        "variants": summaries,
        "primaryComparison": {
            "normalizedL1GapImprovement": l1_improvement,
            "winnerFreeFloorImprovement": floor_improvement,
            "correctedPairedShareRetentionVsLiteral": paired_retention,
            "pairedShareReachabilityLoss": paired_reachability_loss,
        },
        "decision": decision,
        "oracleValueCeiling": "N/A: paired replay/invariant intervention, not an action-value sweep",
        "learnedPolicyRealizedValue": "N/A: deterministic preregistered role correction, no learned policy",
        "chronologicalUnseenOOS": "Not claimed; one previously opened diagnostic market only",
        "limitations": [
            "The 39 goals are high-confidence, fully-filled, single-placement observable Target Maker parents; hidden unfilled/cancelled orders are unavailable.",
            "Target roles are offline diagnostic labels from strictly completed prior qualified Target Maker fills, not runtime inputs for a deployable policy.",
            "Same-second Target fills are excluded from role state because wallet timestamps cannot safely order them within the second.",
            "No Target Taker intervention is included, so this does not reconstruct the complete Target control system.",
            "A KEEP decision identifies an actual-fill feedback mechanism worth generalizing; it does not establish unseen positive execution value.",
        ],
        "artifacts": {"decisions": str(DECISIONS.resolve())},
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "report": str(REPORT),
                "variants": summaries,
                "primaryComparison": report["primaryComparison"],
                "decision": decision,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
