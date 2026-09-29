from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from pathlib import Path


EPS = 1e-9
MARKET_IDS = (1916869, 1917324, 1912961)
BURST_WINDOWS_MS = (1000, 2000, 3000, 5000)


def phase_name(phase: float) -> str:
    if phase < 1.0 / 3.0:
        return "INITIAL"
    if phase < 2.0 / 3.0:
        return "MIDDLE"
    return "LATE"


def active_containing(route: str | None) -> bool:
    return str(route or "").upper() in {"ACTIVE", "MIXED"}


def passive_containing(route: str | None) -> bool:
    return str(route or "").upper() in {"PASSIVE", "MIXED"}


def median(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    pos = (len(ordered) - 1) * fraction
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - pos) + ordered[hi] * (pos - lo)


def linear_slope(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    xm = statistics.fmean(xs)
    ym = statistics.fmean(ys)
    den = sum((x - xm) ** 2 for x in xs)
    if den <= EPS:
        return 0.0
    return sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / den


def compact_raw_row(row: sqlite3.Row) -> dict:
    return {
        "eventMs": int(row["event_ms"]),
        "role": str(row["role"]),
        "side": str(row["side"]),
        "price": float(row["price"]),
        "shares": float(row["shares"]),
        "parentKey": f'{row["role"]}:{row["order_hash"]}:{row["side"]}:BID',
    }


def max_window(rows: list[dict], window_ms: int, end_ms: int, initial_only: bool) -> dict:
    eligible = [
        row
        for row in rows
        if not initial_only or (float(row["eventMs"] - (end_ms - 300000)) / 300000.0) < 1.0 / 3.0
    ]
    best = {"distinctParents": 0, "distinctSidePrices": 0, "rows": []}
    for start in sorted({int(row["eventMs"]) for row in eligible}):
        picked = [row for row in eligible if start <= int(row["eventMs"]) <= start + window_ms]
        parents = {row["parentKey"] for row in picked}
        prices = {(row["side"], round(float(row["price"]), 9)) for row in picked}
        score = (len(parents), len(prices), sum(float(row["shares"]) for row in picked))
        prior = (best["distinctParents"], best["distinctSidePrices"], best.get("shares", 0.0))
        if score > prior:
            best = {
                "startMs": start,
                "endMs": start + window_ms,
                "secondsLeftAtStart": (end_ms - start) / 1000.0,
                "normalizedPhaseAtStart": (start - (end_ms - 300000)) / 300000.0,
                "distinctParents": len(parents),
                "distinctSidePrices": len(prices),
                "shares": sum(float(row["shares"]) for row in picked),
                "rows": picked,
            }
    return best


def analyze_market(trace: dict, raw_rows: list[dict], end_ms: int) -> dict:
    market_id = int(trace["marketId"])
    events = list(trace.get("events") or [])
    births: dict[int, int] = {}
    completions: dict[int, int] = {}
    payments: dict[int, list[dict]] = defaultdict(list)
    for event in events:
        rid_value = (event.get("responsibility") or {}).get("responsibilityId")
        rid = int(rid_value) if rid_value is not None else None
        event_type = str(event.get("eventType") or "")
        if rid is not None and event_type == "RESPONSIBILITY_BIRTH":
            births.setdefault(rid, int(event["timeMs"]))
        elif rid is not None and event_type == "RESPONSIBILITY_COMPLETED":
            completions.setdefault(rid, int(event["timeMs"]))
        elif rid is not None and event_type == "REPAIR_PAYMENT":
            payments[rid].append(event)

    def open_count(t: int, include_completion_clock: bool = False) -> int:
        total = 0
        for rid, born in births.items():
            completed = completions.get(rid)
            if born <= t and (completed is None or (completed >= t if include_completion_clock else completed > t)):
                total += 1
        return total

    window_start = end_ms - 300000
    samples = []
    for t in range(window_start, end_ms + 1, 1000):
        phase = (t - window_start) / 300000.0
        samples.append({"t": t, "phase": phase, "phaseName": phase_name(phase), "open": open_count(t)})

    phase_stats = {}
    for label in ("INITIAL", "MIDDLE", "LATE"):
        phase_samples = [row for row in samples if row["phaseName"] == label]
        birth_count = sum(phase_name((born - window_start) / 300000.0) == label for born in births.values())
        completion_count = sum(phase_name((done - window_start) / 300000.0) == label for done in completions.values())
        phase_payments = [
            event
            for rows in payments.values()
            for event in rows
            if phase_name(float(event.get("normalizedPhase") or 0.0)) == label
        ]
        active_qty = sum(
            float((event.get("allocation") or {}).get("repairAllocation") or 0.0)
            for event in phase_payments
            if active_containing((event.get("execution") or {}).get("route"))
        )
        passive_qty = sum(
            float((event.get("allocation") or {}).get("repairAllocation") or 0.0)
            for event in phase_payments
            if str((event.get("execution") or {}).get("route") or "").upper() == "PASSIVE"
        )
        phase_stats[label] = {
            "meanOpenResponsibilities": statistics.fmean(row["open"] for row in phase_samples) if phase_samples else 0.0,
            "medianOpenResponsibilities": median([row["open"] for row in phase_samples]),
            "maxOpenResponsibilities": max((row["open"] for row in phase_samples), default=0),
            "births": birth_count,
            "completions": completion_count,
            "netSlotChange": birth_count - completion_count,
            "completionsPer30NormalizedSeconds": completion_count / (100.0 / 30.0),
            "activeContainingRepairQty": active_qty,
            "purePassiveRepairQty": passive_qty,
        }

    lifecycle_by_birth_phase = {}
    for label in ("INITIAL", "MIDDLE", "LATE"):
        durations = [
            (completions[rid] - born) / 1000.0
            for rid, born in births.items()
            if rid in completions and phase_name((born - window_start) / 300000.0) == label
        ]
        lifecycle_by_birth_phase[label] = {
            "completedResponsibilities": len(durations),
            "medianLifetimeSeconds": median(durations),
            "p75LifetimeSeconds": quantile(durations, 0.75),
            "meanLifetimeSeconds": statistics.fmean(durations) if durations else None,
        }

    exact_groups: dict[int, list[dict]] = defaultdict(list)
    for row in raw_rows:
        exact_groups[int(row["eventMs"])].append(row)

    def exact_best(initial_only: bool) -> dict:
        candidates = []
        for t, rows_at_t in exact_groups.items():
            phase = (t - window_start) / 300000.0
            if initial_only and phase >= 1.0 / 3.0:
                continue
            parents = {row["parentKey"] for row in rows_at_t}
            prices = {(row["side"], round(float(row["price"]), 9)) for row in rows_at_t}
            candidates.append(
                {
                    "eventMs": t,
                    "secondsLeft": (end_ms - t) / 1000.0,
                    "normalizedPhase": phase,
                    "distinctParents": len(parents),
                    "distinctSidePrices": len(prices),
                    "shares": sum(float(row["shares"]) for row in rows_at_t),
                    "rows": rows_at_t,
                }
            )
        return max(candidates, key=lambda row: (row["distinctParents"], row["distinctSidePrices"], row["shares"]), default={})

    sequence_counts = Counter()
    sequence_rows = []
    active_events = []
    for rid, rows in sorted(payments.items()):
        ordered = sorted(rows, key=lambda row: (int(row["timeMs"]), int(row.get("sequence") or 0)))
        routes = [str((row.get("execution") or {}).get("route") or "UNKNOWN").upper() for row in ordered]
        complete = rid in completions
        first_active = bool(ordered and active_containing(routes[0]))
        final_active = bool(complete and ordered and active_containing(routes[-1]))
        prior_passive = bool(len(ordered) > 1 and any(passive_containing(route) for route in routes[:-1]))
        first_passive = bool(ordered and passive_containing(routes[0]))
        final_passive = bool(complete and ordered and passive_containing(routes[-1]))
        if final_active and prior_passive:
            category = "PASSIVE_BEFORE_ACTIVE_FINAL"
        elif first_active and final_passive and len(ordered) > 1:
            category = "ACTIVE_FIRST_PASSIVE_FINAL"
        elif all(active_containing(route) for route in routes):
            category = "ACTIVE_ONLY"
        elif all(route == "PASSIVE" for route in routes):
            category = "PASSIVE_ONLY"
        else:
            category = "OTHER_MIXED_SEQUENCE"
        sequence_counts[category] += 1
        sequence_rows.append(
            {
                "responsibilityId": rid,
                "bornAt": births.get(rid),
                "completedAt": completions.get(rid),
                "routes": routes,
                "paymentCount": len(ordered),
                "category": category,
                "firstRepairPhase": float(ordered[0].get("normalizedPhase") or 0.0) if ordered else None,
                "finalRepairPhase": float(ordered[-1].get("normalizedPhase") or 0.0) if ordered else None,
            }
        )
        for index, event in enumerate(ordered):
            route = str((event.get("execution") or {}).get("route") or "UNKNOWN").upper()
            if not active_containing(route):
                continue
            debt_before = float((event.get("managerDebt") or {}).get("before") or 0.0)
            repair_qty = float((event.get("allocation") or {}).get("repairAllocation") or 0.0)
            debt_after = float((event.get("managerDebt") or {}).get("after") or 0.0)
            t = int(event["timeMs"])
            active_events.append(
                {
                    "responsibilityId": rid,
                    "timeMs": t,
                    "secondsLeft": float(event.get("secondsLeft") or 0.0),
                    "normalizedPhase": float(event.get("normalizedPhase") or 0.0),
                    "route": route,
                    "paymentIndex": index + 1,
                    "paymentCountForResponsibility": len(ordered),
                    "hadPriorPassivePayment": any(passive_containing(prior) for prior in routes[:index]),
                    "isTerminalPayment": complete and index == len(ordered) - 1,
                    "openResponsibilitiesBeforeClock": open_count(t, include_completion_clock=True),
                    "repairQty": repair_qty,
                    "debtBefore": debt_before,
                    "debtAfter": debt_after,
                    "fractionOfPrePaymentDebt": repair_qty / debt_before if debt_before > EPS else None,
                }
            )

    first_active = min(active_events, key=lambda row: row["timeMs"], default=None)
    active_final_after_passive = [
        row for row in active_events if row["isTerminalPayment"] and row["hadPriorPassivePayment"]
    ]
    active_first_then_passive_final = [
        row for row in sequence_rows if row["category"] == "ACTIVE_FIRST_PASSIVE_FINAL"
    ]
    initial_samples_after_first_fill = []
    if raw_rows:
        first_fill = min(int(row["eventMs"]) for row in raw_rows)
        initial_samples_after_first_fill = [row["open"] for row in samples if first_fill <= row["t"] <= first_fill + 60000]

    return {
        "marketId": market_id,
        "sourceSummary": trace.get("summary"),
        "realizedResponsibilitySlots": {
            "maxConcurrentOpenWholeMarket": max((row["open"] for row in samples), default=0),
            "maxConcurrentOpenInitialPhase": max((row["open"] for row in samples if row["phaseName"] == "INITIAL"), default=0),
            "medianOpenFirst60sAfterFirstFill": median(initial_samples_after_first_fill),
            "p75OpenFirst60sAfterFirstFill": quantile(initial_samples_after_first_fill, 0.75),
            "occupancySlopePerFullNormalizedMarket": linear_slope(
                [row["phase"] for row in samples], [float(row["open"]) for row in samples]
            ),
            "phaseStats": phase_stats,
            "lifecycleByBirthPhase": lifecycle_by_birth_phase,
        },
        "realizedExecutionSlotLowerBound": {
            "exactClockInitial": exact_best(True),
            "exactClockWholeMarket": exact_best(False),
            "burstInitial": {str(ms): max_window(raw_rows, ms, end_ms, True) for ms in BURST_WINDOWS_MS},
            "burstWholeMarket": {str(ms): max_window(raw_rows, ms, end_ms, False) for ms in BURST_WINDOWS_MS},
        },
        "repairRouteSequence": {
            "responsibilitiesWithRepair": len(sequence_rows),
            "counts": dict(sequence_counts),
            "activeFinalAfterPriorPassiveCount": len(active_final_after_passive),
            "activeFirstThenPassiveFinalCount": len(active_first_then_passive_final),
            "rows": sequence_rows,
        },
        "activeRepair": {
            "eventCount": len(active_events),
            "repairQty": sum(row["repairQty"] for row in active_events),
            "firstEvent": first_active,
            "terminalAfterPriorPassiveEvents": active_final_after_passive,
            "terminalAfterPriorPassiveQty": sum(row["repairQty"] for row in active_final_after_passive),
            "eventsWithMultipleOpenResponsibilities": sum(row["openResponsibilitiesBeforeClock"] > 1 for row in active_events),
            "events": active_events,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-dir", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    trace_dir = Path(args.trace_dir)
    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    markets = []
    try:
        for market_id in MARKET_IDS:
            trace = json.loads((trace_dir / f"TARGET_SEMANTIC_TRACE_{market_id}_V1.json").read_text(encoding="utf-8"))
            end_row = connection.execute(
                "select window_end_ms from eth_markets where market_id=?", (market_id,)
            ).fetchone()
            if end_row is None:
                raise RuntimeError(f"missing market end for {market_id}")
            raw = [
                compact_raw_row(row)
                for row in connection.execute(
                    "select event_ms,role,side,order_hash,price,shares from eth_events where market_id=? order by event_ms,id",
                    (market_id,),
                )
            ]
            markets.append(analyze_market(trace, raw, int(end_row["window_end_ms"])))
    finally:
        connection.close()

    pooled_counts = Counter()
    pooled_active = []
    for market in markets:
        pooled_counts.update(market["repairRouteSequence"]["counts"])
        pooled_active.extend(market["activeRepair"]["events"])
    initial_open_lower_bounds = [
        int(market["realizedResponsibilitySlots"]["maxConcurrentOpenInitialPhase"]) for market in markets
    ]
    early_exact_lower_bounds = [
        int(market["realizedExecutionSlotLowerBound"]["exactClockInitial"].get("distinctParents", 0))
        for market in markets
    ]
    early_burst_2s = [
        int(market["realizedExecutionSlotLowerBound"]["burstInitial"]["2000"].get("distinctParents", 0))
        for market in markets
    ]
    early_means = [market["realizedResponsibilitySlots"]["phaseStats"]["INITIAL"]["meanOpenResponsibilities"] for market in markets]
    middle_means = [market["realizedResponsibilitySlots"]["phaseStats"]["MIDDLE"]["meanOpenResponsibilities"] for market in markets]
    late_means = [market["realizedResponsibilitySlots"]["phaseStats"]["LATE"]["meanOpenResponsibilities"] for market in markets]
    slopes = [market["realizedResponsibilitySlots"]["occupancySlopePerFullNormalizedMarket"] for market in markets]
    active_final_after_passive = [
        row for row in pooled_active if row["isTerminalPayment"] and row["hadPriorPassivePayment"]
    ]
    first_active_phases = [
        market["activeRepair"]["firstEvent"]["normalizedPhase"]
        for market in markets
        if market["activeRepair"]["firstEvent"] is not None
    ]
    occupancy_decreases_all = all(late < early for early, late in zip(early_means, late_means))
    total_sequence_count = sum(pooled_counts.values())
    completion_rates = {
        label: statistics.fmean(
            market["realizedResponsibilitySlots"]["phaseStats"][label]["completionsPer30NormalizedSeconds"]
            for market in markets
        )
        for label in ("INITIAL", "MIDDLE", "LATE")
    }
    residual_finish_qty = [row["repairQty"] for row in active_final_after_passive]
    output = {
        "version": "TARGET_MULTISLOT_CYCLE_INFERENCE_V1",
        "date": "2026-09-05",
        "researchOnly": True,
        "evidenceClass": "POST_MARKET_REALIZED_SLOT_LOWER_BOUND",
        "marketIds": list(MARKET_IDS),
        "verdict": {
            "multiSlotAtStart": "SUPPORTED_AS_REALIZED_LOWER_BOUND" if max(initial_open_lower_bounds + early_burst_2s) >= 2 else "NOT_OBSERVED",
            "slotCountMonotonicallyDecreasesWithTime": "SUPPORTED" if occupancy_decreases_all and all(slope < 0 for slope in slopes) else "REJECTED_AS_UNIVERSAL_MONOTONIC_RULE",
            "activeOnlyAfterSlotReduction": "REJECTED" if first_active_phases and min(first_active_phases) < 1.0 / 3.0 else "NOT_REJECTED",
            "activeFinishesPassiveResidual": "SUPPORTED_AS_ONE_RECURRING_MODE" if active_final_after_passive else "NOT_OBSERVED",
            "activeFinishesPassiveResidualIsExclusiveMode": "REJECTED" if pooled_counts.get("ACTIVE_FIRST_PASSIVE_FINAL", 0) or pooled_counts.get("ACTIVE_ONLY", 0) else "NOT_REJECTED",
        },
        "pooled": {
            "initialMaxConcurrentResponsibilityLowerBounds": initial_open_lower_bounds,
            "initialExactClockParentLowerBounds": early_exact_lower_bounds,
            "initialTwoSecondBurstParentLowerBounds": early_burst_2s,
            "initialFiveSecondBurstParentLowerBounds": [
                int(market["realizedExecutionSlotLowerBound"]["burstInitial"]["5000"].get("distinctParents", 0))
                for market in markets
            ],
            "meanOpenResponsibilitiesByPhase": {
                "INITIAL": statistics.fmean(early_means),
                "MIDDLE": statistics.fmean(middle_means),
                "LATE": statistics.fmean(late_means),
            },
            "occupancySlopes": slopes,
            "repairRouteSequenceCounts": dict(pooled_counts),
            "repairRouteSequenceShares": {
                key: value / total_sequence_count if total_sequence_count else 0.0
                for key, value in pooled_counts.items()
            },
            "meanCompletionsPer30NormalizedSecondsByPhase": completion_rates,
            "activeRepairEvents": len(pooled_active),
            "activeRepairQty": sum(row["repairQty"] for row in pooled_active),
            "activeRepairEventsWithMultipleOpenResponsibilities": sum(row["openResponsibilitiesBeforeClock"] > 1 for row in pooled_active),
            "activeRepairMultiOpenShare": (
                sum(row["openResponsibilitiesBeforeClock"] > 1 for row in pooled_active) / len(pooled_active)
                if pooled_active
                else 0.0
            ),
            "activeFinalAfterPriorPassiveEvents": len(active_final_after_passive),
            "activeFinalAfterPriorPassiveQty": sum(row["repairQty"] for row in active_final_after_passive),
            "activeFinalAfterPriorPassiveShareOfResponsibilities": (
                len(active_final_after_passive) / total_sequence_count if total_sequence_count else 0.0
            ),
            "activeFinalAfterPriorPassiveResidualQtyMedian": median(residual_finish_qty),
            "activeFinalAfterPriorPassiveResidualQtyP75": quantile(residual_finish_qty, 0.75),
            "firstActiveRepairPhases": first_active_phases,
        },
        "markets": markets,
        "interpretationBoundary": [
            "confirmed fills reveal only a lower bound on simultaneously configured slots",
            "canonical FIFO responsibilities are economic debt slots, not direct exchange-order slots",
            "exact-clock and burst parent counts show independent realized executions, not unseen resting orders",
            "Target evidence is not used by OUR runtime",
            "no fixed-second threshold is inferred for OUR",
        ],
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "verdict": output["verdict"], "pooled": output["pooled"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
