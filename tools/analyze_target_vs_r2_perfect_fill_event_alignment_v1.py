from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import analyze_strategy_target_cross_compare_v1 as cross_v1
from tools.hftbacktest_r2_execution_school_v0 import load_reference_paper


OUT = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT / "target_vs_r2_perfect_fill_event_alignment_v1_preregistered.json"
REPORT = OUT / "target_vs_r2_perfect_fill_event_alignment_v1_report.json"
MATCH_ROWS = OUT / "target_vs_r2_perfect_fill_event_alignment_v1_primary_matches.csv"
MARKETS = [1569361, 1571387, 1572594]
PRIMARY_MARKET = 1572594
WINDOWS_MS = [1000, 3000, 5000, 10000]
PRIMARY_WINDOW_MS = 5000
EPS = 1e-9


def r2_actions(market_id: int) -> list[cross_v1.Action]:
    paper = load_reference_paper(market_id)
    actions: list[cross_v1.Action] = []
    for index, row in enumerate(paper["orders"]):
        if str(row.get("status") or "").upper() != "FILLED" or row.get("filled_at_ms") is None:
            continue
        actions.append(
            cross_v1.Action(
                action_id=f"R2:{market_id}:MAKER:{index}",
                owner="R2",
                channel="MAKER",
                market_id=market_id,
                event_ms=int(row["filled_at_ms"]),
                side=str(row["side"]).upper(),
                quote_type="BID",
                price=float(row["price"]),
                shares=float(row["shares"]),
                source="FROZEN_R2_OPTIMISTIC_PAPER",
                factors={"placedAtMs": int(row["placed_at_ms"])},
            )
        )
    for index, row in enumerate(paper["takers"]):
        if row.get("filled_at_ms") is None:
            continue
        actions.append(
            cross_v1.Action(
                action_id=f"R2:{market_id}:TAKER:{index}",
                owner="R2",
                channel="TAKER",
                market_id=market_id,
                event_ms=int(row["filled_at_ms"]),
                side=str(row["side"]).upper(),
                quote_type="BID",
                price=float(row["price"]),
                shares=float(row["shares"]),
                source="FROZEN_R2_OPTIMISTIC_PAPER",
            )
        )
    return sorted(actions, key=lambda row: (row.event_ms, row.channel, row.action_id))


def group_match(
    left: list[cross_v1.Action],
    right: list[cross_v1.Action],
    window_ms: int,
    signature: Callable[[cross_v1.Action], Any],
) -> list[tuple[cross_v1.Action, cross_v1.Action]]:
    left_groups: dict[Any, list[cross_v1.Action]] = defaultdict(list)
    right_groups: dict[Any, list[cross_v1.Action]] = defaultdict(list)
    for row in left:
        left_groups[signature(row)].append(row)
    for row in right:
        right_groups[signature(row)].append(row)
    pairs: list[tuple[cross_v1.Action, cross_v1.Action]] = []
    for key in sorted(set(left_groups) & set(right_groups), key=str):
        lhs = sorted(left_groups[key], key=lambda row: (row.event_ms, row.action_id))
        rhs = sorted(right_groups[key], key=lambda row: (row.event_ms, row.action_id))
        i = j = 0
        while i < len(lhs) and j < len(rhs):
            if rhs[j].event_ms < lhs[i].event_ms - window_ms:
                j += 1
            elif lhs[i].event_ms < rhs[j].event_ms - window_ms:
                i += 1
            else:
                pairs.append((lhs[i], rhs[j]))
                i += 1
                j += 1
    return sorted(pairs, key=lambda pair: (pair[0].event_ms, pair[1].event_ms))


def same_second_semantic_count(
    left: list[cross_v1.Action], right: list[cross_v1.Action]
) -> int:
    lhs = Counter((row.event_ms // 1000, row.channel, row.side) for row in left)
    rhs = Counter((row.event_ms // 1000, row.channel, row.side) for row in right)
    return sum(min(count, rhs.get(key, 0)) for key, count in lhs.items())


def safe_f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or precision + recall <= EPS:
        return 0.0 if precision is not None and recall is not None else None
    return 2.0 * precision * recall / (precision + recall)


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    lo = int(math.floor(position))
    hi = int(math.ceil(position))
    if lo == hi:
        return ordered[lo]
    weight = position - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def metrics(
    r2: list[cross_v1.Action],
    target: list[cross_v1.Action],
    window_ms: int,
) -> tuple[dict[str, Any], list[tuple[cross_v1.Action, cross_v1.Action]]]:
    time_pairs = group_match(r2, target, window_ms, lambda _row: "ALL")
    role_pairs = group_match(r2, target, window_ms, lambda row: row.channel)
    side_pairs = group_match(r2, target, window_ms, lambda row: row.side)
    semantic_pairs = group_match(
        r2, target, window_ms, lambda row: (row.channel, row.side)
    )
    precision = len(semantic_pairs) / len(r2) if r2 else None
    recall = len(semantic_pairs) / len(target) if target else None
    abs_dt = [abs(float(a.event_ms - b.event_ms)) for a, b in semantic_pairs]
    price_error = [abs(float(a.price - b.price)) for a, b in semantic_pairs]
    share_error = [abs(float(a.shares - b.shares)) for a, b in semantic_pairs]
    strict_action = sum(
        abs(float(a.price - b.price)) <= 0.010000001
        and abs(float(a.shares - b.shares)) <= 0.05
        for a, b in semantic_pairs
    )
    by_channel = Counter(a.channel for a, _ in semantic_pairs)
    return (
        {
            "windowMs": window_ms,
            "r2Events": len(r2),
            "targetEvents": len(target),
            "timeOnlyPairs": len(time_pairs),
            "rolePairs": len(role_pairs),
            "sidePairs": len(side_pairs),
            "semanticRoleSidePairs": len(semantic_pairs),
            "semanticPrecisionOnR2": precision,
            "semanticRecallOnTarget": recall,
            "semanticF1": safe_f1(precision, recall),
            "sameSecondSemanticPairs": same_second_semantic_count(r2, target),
            "sameSecondPrecisionOnR2": same_second_semantic_count(r2, target) / len(r2)
            if r2
            else None,
            "semanticPairsByChannel": dict(by_channel),
            "strictPrice1TickAndSharesPairsAmongSemantic": strict_action,
            "strictActionPrecisionOnR2": strict_action / len(r2) if r2 else None,
            "medianAbsTimingErrorMs": percentile(abs_dt, 0.5),
            "p90AbsTimingErrorMs": percentile(abs_dt, 0.9),
            "medianAbsPriceError": percentile(price_error, 0.5),
            "medianAbsShareError": percentile(share_error, 0.5),
        },
        semantic_pairs,
    )


def aggregate_metrics(
    all_r2: dict[int, list[cross_v1.Action]],
    all_target: dict[int, list[cross_v1.Action]],
    window_ms: int,
) -> dict[str, Any]:
    per_market = {
        str(market_id): metrics(all_r2[market_id], all_target[market_id], window_ms)[0]
        for market_id in MARKETS
    }
    r2_total = sum(row["r2Events"] for row in per_market.values())
    target_total = sum(row["targetEvents"] for row in per_market.values())
    semantic = sum(row["semanticRoleSidePairs"] for row in per_market.values())
    precision = semantic / r2_total if r2_total else None
    recall = semantic / target_total if target_total else None
    return {
        "windowMs": window_ms,
        "aggregate": {
            "markets": len(MARKETS),
            "r2Events": r2_total,
            "targetEvents": target_total,
            "semanticRoleSidePairs": semantic,
            "semanticPrecisionOnR2": precision,
            "semanticRecallOnTarget": recall,
            "semanticF1": safe_f1(precision, recall),
            "sameSecondSemanticPairs": sum(
                row["sameSecondSemanticPairs"] for row in per_market.values()
            ),
        },
        "markets": per_market,
    }


def event_counts(rows: list[cross_v1.Action]) -> dict[str, int]:
    return dict(Counter(f"{row.channel}_{row.side}" for row in rows))


def main() -> None:
    if not PREREG.exists():
        raise RuntimeError(f"missing preregistration: {PREREG}")
    target_connection = cross_v1._connect_ro(cross_v1.DEFAULT_OFFICIAL_DB)
    try:
        target_raw_rows = [
            row
            for row in cross_v1._official_target_actions(
                target_connection, set(MARKETS)
            )
            if row.quote_type == "BID"
        ]
    finally:
        target_connection.close()

    r2_by_market = {market_id: r2_actions(market_id) for market_id in MARKETS}
    target_raw_by_market = {
        market_id: [row for row in target_raw_rows if row.market_id == market_id]
        for market_id in MARKETS
    }
    target_burst_rows = cross_v1._coalesce_target_actions(target_raw_rows)
    target_burst_by_market = {
        market_id: [row for row in target_burst_rows if row.market_id == market_id]
        for market_id in MARKETS
    }

    raw_windows = {
        str(window): aggregate_metrics(r2_by_market, target_raw_by_market, window)
        for window in WINDOWS_MS
    }
    burst_windows = {
        str(window): aggregate_metrics(r2_by_market, target_burst_by_market, window)
        for window in WINDOWS_MS
    }
    primary_metrics, primary_pairs = metrics(
        r2_by_market[PRIMARY_MARKET],
        target_burst_by_market[PRIMARY_MARKET],
        PRIMARY_WINDOW_MS,
    )
    match_rows: list[dict[str, Any]] = []
    for r2, target in primary_pairs:
        match_rows.append(
            {
                "marketId": PRIMARY_MARKET,
                "r2ActionId": r2.action_id,
                "r2EventMs": r2.event_ms,
                "r2Role": r2.channel,
                "r2Side": r2.side,
                "r2Price": r2.price,
                "r2Shares": r2.shares,
                "targetActionId": target.action_id,
                "targetEventMs": target.event_ms,
                "targetRole": target.channel,
                "targetSide": target.side,
                "targetPrice": target.price,
                "targetShares": target.shares,
                "deltaMsTargetMinusR2": target.event_ms - r2.event_ms,
                "absPriceError": abs(target.price - r2.price),
                "absShareError": abs(target.shares - r2.shares),
            }
        )
    if match_rows:
        with MATCH_ROWS.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(match_rows[0]))
            writer.writeheader()
            writer.writerows(match_rows)

    primary_f1 = float(primary_metrics["semanticF1"] or 0.0)
    if primary_f1 >= 0.50 - EPS:
        decision = "HIGH_EVENT_ALIGNMENT"
    elif primary_f1 >= 0.25 - EPS:
        decision = "PARTIAL_MACRO_NOT_EVENT_ALIGNMENT"
    else:
        decision = "LOW_EVENT_ALIGNMENT"
    report = {
        "reportVersion": "TARGET_VS_R2_PERFECT_FILL_EVENT_ALIGNMENT_V1",
        "researchOnly": True,
        "performanceClaim": False,
        "preregisteredContract": PREREG.name,
        "question": "Ignoring HFT, how much do Frozen R2 optimistic-PAPER fill events overlap official Target fill-parent events in the same markets?",
        "cohort": {
            "markets": MARKETS,
            "primaryMarket": PRIMARY_MARKET,
            "openedDevelopmentOnly": True,
            "officialHftForward": False,
        },
        "semantics": {
            "execution": "No HftBacktest. Frozen R2 optimistic PAPER fill ledger is treated as the perfect-execution trajectory.",
            "r2EventTime": "Maker/Taker filled_at_ms",
            "targetEventTime": "Official BID parent first_event_ms",
            "primaryTargetUnit": "1s-idle / 3s-cap same-role same-side Target action burst",
            "primaryMatch": "One-to-one identical role+side within 5 seconds",
            "priceAndSize": "Not required for semantic overlap; separately audited",
        },
        "primaryMarketBurst5s": primary_metrics,
        "eventCountsByMarket": {
            str(market_id): {
                "r2": event_counts(r2_by_market[market_id]),
                "targetRawParents": event_counts(target_raw_by_market[market_id]),
                "targetBursts": event_counts(target_burst_by_market[market_id]),
            }
            for market_id in MARKETS
        },
        "burstWindows": burst_windows,
        "rawParentWindows": raw_windows,
        "decision": decision,
        "WAIT_ACT": "N/A: observed perfect-PAPER fill-event comparison, not a policy run",
        "oracleValueCeiling": "N/A",
        "learnedPolicyRealizedValue": "N/A",
        "chronologicalUnseenOOS": "N/A: descriptive same-market alignment only",
        "limitations": [
            "Target private unfilled/cancelled orders are unobservable; only executed BID parents are compared.",
            "R2 perfect-PAPER fills and Target real fills are different execution ecologies; this deliberately removes HFT but does not create shared private state.",
            "A low event F1 can coexist with similar macro portfolio behavior, and a high event F1 would not prove equal economics.",
            "The 5-second window is the existing project diagnostic convention; 1/3/10-second results are included to expose sensitivity rather than select a favorable window.",
        ],
        "artifacts": {"primarySemanticMatches": str(MATCH_ROWS.resolve())},
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "report": str(REPORT),
                "primaryMarketBurst5s": primary_metrics,
                "aggregateBurst5s": burst_windows[str(PRIMARY_WINDOW_MS)]["aggregate"],
                "decision": decision,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
