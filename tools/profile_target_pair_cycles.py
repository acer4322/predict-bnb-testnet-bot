from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FIVE_MIN_TITLE = re.compile(
    r"^(Bitcoin|BTC|Ethereum|ETH|BNB) Up or Down - .+,\s*(\d{1,2})(?::(\d{2}))?(AM|PM)-(\d{1,2})(?::(\d{2}))?(AM|PM) ET$"
)


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def minute_of_day(hour: int, minute: int, ampm: str) -> int:
    hour %= 12
    if ampm.upper() == "PM":
        hour += 12
    return hour * 60 + minute


def is_crypto_5m(title: Any) -> bool:
    text = str(title or "").strip()
    match = FIVE_MIN_TITLE.match(text)
    if not match:
        return False
    sh, sm, sap, eh, em, eap = (
        int(match.group(2)),
        int(match.group(3) or 0),
        match.group(4),
        int(match.group(5)),
        int(match.group(6) or 0),
        match.group(7),
    )
    start = minute_of_day(sh, sm, sap)
    end = minute_of_day(eh, em, eap)
    if end < start:
        end += 24 * 60
    return end - start == 5


# Backward-compatible alias for external callers/tests that imported the old helper.
def is_eth_bnb_5m(title: Any) -> bool:
    return is_crypto_5m(title)


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    rows = sorted(values)
    if len(rows) == 1:
        return rows[0]
    x = max(0.0, min(1.0, q)) * (len(rows) - 1)
    lo = int(x)
    hi = min(len(rows) - 1, lo + 1)
    weight = x - lo
    return rows[lo] * (1.0 - weight) + rows[hi] * weight


def event_time(row: dict[str, Any], field: str) -> datetime:
    parsed = parse_iso(row.get(field))
    if parsed is None:
        return datetime.max.replace(tzinfo=timezone.utc)
    return parsed


def run_lengths(events: list[dict[str, Any]]) -> list[int]:
    runs: list[int] = []
    previous = None
    current = 0
    for row in events:
        side = str(row.get("outcome") or "").upper()
        if side not in {"UP", "DOWN"}:
            continue
        if side == previous:
            current += 1
        else:
            if current:
                runs.append(current)
            previous = side
            current = 1
    if current:
        runs.append(current)
    return runs


def nearest_opposite_gap(
    row: dict[str, Any],
    market_rows: list[dict[str, Any]],
    field: str,
) -> float | None:
    side = str(row.get("outcome") or "").upper()
    when = parse_iso(row.get(field))
    if when is None or side not in {"UP", "DOWN"}:
        return None
    gaps: list[float] = []
    for other in market_rows:
        if other is row or str(other.get("outcome") or "").upper() == side:
            continue
        other_when = parse_iso(other.get(field))
        if other_when is not None:
            gaps.append(abs((other_when - when).total_seconds()))
    return min(gaps) if gaps else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Test whether target BTC/ETH/BNB 5m makerHash fills look like strict paired cycles "
            "or softer inventory-balanced replenishment. Read-only; match history cannot see unfilled/cancelled orders."
        )
    )
    parser.add_argument("--input", default="data/target_maker_hash_profile.json")
    parser.add_argument("--output", default="data/target_pair_cycle_profile.json")
    args = parser.parse_args()

    source = Path(args.input)
    report = json.loads(source.read_text(encoding="utf-8"))
    rows = report.get("parentOrdersByMakerHash") if isinstance(report, dict) else None
    if not isinstance(rows, list):
        raise SystemExit("input does not contain parentOrdersByMakerHash")

    filtered = [
        row
        for row in rows
        if isinstance(row, dict)
        and is_crypto_5m(row.get("marketTitle"))
        and str(row.get("outcome") or "").upper() in {"UP", "DOWN"}
    ]
    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in filtered:
        by_market[int(row.get("marketId") or 0)].append(row)

    first_runs: list[int] = []
    last_runs: list[int] = []
    triple_run_markets_first = 0
    triple_run_markets_last = 0
    adjacent_completion_pairs = 0
    adjacent_completion_pairs_opposite = 0
    opposite_gap_first: list[float] = []
    opposite_gap_last: list[float] = []
    inventory_decisions = Counter()
    inventory_abs_before: list[float] = []
    inventory_abs_after: list[float] = []
    examples_triple: list[dict[str, Any]] = []
    examples_inventory_increase: list[dict[str, Any]] = []

    for market_id, market_rows in by_market.items():
        first_sorted = sorted(market_rows, key=lambda row: (event_time(row, "firstExecutedAt"), str(row.get("makerHash"))))
        last_sorted = sorted(market_rows, key=lambda row: (event_time(row, "lastExecutedAt"), str(row.get("makerHash"))))

        fruns = run_lengths(first_sorted)
        lruns = run_lengths(last_sorted)
        first_runs.extend(fruns)
        last_runs.extend(lruns)
        if any(length >= 3 for length in fruns):
            triple_run_markets_first += 1
        if any(length >= 3 for length in lruns):
            triple_run_markets_last += 1
            if len(examples_triple) < 25:
                examples_triple.append(
                    {
                        "marketId": market_id,
                        "marketTitle": market_rows[0].get("marketTitle"),
                        "completionSequence": [
                            {
                                "side": str(row.get("outcome") or "").upper(),
                                "time": row.get("lastExecutedAt"),
                                "shares": float(row.get("totalShares") or 0.0),
                                "price": float(row.get("price") or 0.0),
                                "makerHash": row.get("makerHash"),
                            }
                            for row in last_sorted
                        ],
                    }
                )

        # Strict one-UP + one-DOWN generations imply completion events should be
        # largely pairable into opposite-side adjacent pairs. This is only a
        # proxy because matches do not expose true terminal/cancel timestamps.
        for index in range(0, len(last_sorted) - 1, 2):
            left = str(last_sorted[index].get("outcome") or "").upper()
            right = str(last_sorted[index + 1].get("outcome") or "").upper()
            adjacent_completion_pairs += 1
            if left != right:
                adjacent_completion_pairs_opposite += 1

        for row in market_rows:
            gap_first = nearest_opposite_gap(row, market_rows, "firstExecutedAt")
            gap_last = nearest_opposite_gap(row, market_rows, "lastExecutedAt")
            if gap_first is not None:
                opposite_gap_first.append(gap_first)
            if gap_last is not None:
                opposite_gap_last.append(gap_last)

        # Softer inventory-control test. At each observed parent completion, use
        # cumulative observed filled shares only. If UP inventory exceeds DOWN,
        # a new DOWN fill reduces residual; another UP fill increases it.
        up_shares = 0.0
        down_shares = 0.0
        for row in last_sorted:
            side = str(row.get("outcome") or "").upper()
            shares = float(row.get("totalShares") or 0.0)
            before = up_shares - down_shares
            abs_before = abs(before)
            if side == "UP":
                up_shares += shares
            else:
                down_shares += shares
            after = up_shares - down_shares
            abs_after = abs(after)
            inventory_abs_before.append(abs_before)
            inventory_abs_after.append(abs_after)

            if abs_before < 1e-9:
                inventory_decisions["from_balanced"] += 1
            elif abs_after + 1e-9 < abs_before:
                inventory_decisions["reduced_observed_share_imbalance"] += 1
            elif abs_after > abs_before + 1e-9:
                inventory_decisions["increased_observed_share_imbalance"] += 1
                if len(examples_inventory_increase) < 25:
                    examples_inventory_increase.append(
                        {
                            "marketId": market_id,
                            "marketTitle": row.get("marketTitle"),
                            "side": side,
                            "lastExecutedAt": row.get("lastExecutedAt"),
                            "shares": shares,
                            "price": float(row.get("price") or 0.0),
                            "imbalanceBefore": before,
                            "imbalanceAfter": after,
                            "makerHash": row.get("makerHash"),
                        }
                    )
            else:
                inventory_decisions["unchanged"] += 1

    def gap_summary(values: list[float]) -> dict[str, Any]:
        return {
            "medianSeconds": statistics.median(values) if values else None,
            "p10Seconds": percentile(values, 0.10),
            "p90Seconds": percentile(values, 0.90),
            "within1sShare": sum(value <= 1.0 for value in values) / len(values) if values else None,
            "within3sShare": sum(value <= 3.0 for value in values) / len(values) if values else None,
            "within5sShare": sum(value <= 5.0 for value in values) / len(values) if values else None,
            "within15sShare": sum(value <= 15.0 for value in values) / len(values) if values else None,
            "within30sShare": sum(value <= 30.0 for value in values) / len(values) if values else None,
        }

    actionable = (
        inventory_decisions["reduced_observed_share_imbalance"]
        + inventory_decisions["increased_observed_share_imbalance"]
    )
    summary = {
        "source": str(source),
        "filter": "BTC/ETH/BNB exact 5-minute target maker parents only",
        "markets": len(by_market),
        "parentOrdersApprox": len(filtered),
        "strictPairedCycleProxies": {
            "adjacentCompletionPairs": adjacent_completion_pairs,
            "adjacentCompletionPairsOpposite": adjacent_completion_pairs_opposite,
            "oppositePairShare": (
                adjacent_completion_pairs_opposite / adjacent_completion_pairs
                if adjacent_completion_pairs
                else None
            ),
            "marketsWithThreeOrMoreSameSideFirstFillRun": triple_run_markets_first,
            "shareMarketsWithThreeOrMoreSameSideFirstFillRun": (
                triple_run_markets_first / len(by_market) if by_market else None
            ),
            "marketsWithThreeOrMoreSameSideCompletionRun": triple_run_markets_last,
            "shareMarketsWithThreeOrMoreSameSideCompletionRun": (
                triple_run_markets_last / len(by_market) if by_market else None
            ),
            "firstFillRunLengthMedian": statistics.median(first_runs) if first_runs else None,
            "firstFillRunLengthP90": percentile([float(x) for x in first_runs], 0.90),
            "completionRunLengthMedian": statistics.median(last_runs) if last_runs else None,
            "completionRunLengthP90": percentile([float(x) for x in last_runs], 0.90),
        },
        "oppositeSideTiming": {
            "nearestOppositeParentFirstFill": gap_summary(opposite_gap_first),
            "nearestOppositeParentLastFill": gap_summary(opposite_gap_last),
        },
        "observedShareInventoryResponse": {
            **dict(inventory_decisions),
            "imbalanceReducingShareAmongDirectionalDecisions": (
                inventory_decisions["reduced_observed_share_imbalance"] / actionable
                if actionable
                else None
            ),
            "medianAbsoluteImbalanceBeforeObservedParentCompletion": (
                statistics.median(inventory_abs_before) if inventory_abs_before else None
            ),
            "medianAbsoluteImbalanceAfterObservedParentCompletion": (
                statistics.median(inventory_abs_after) if inventory_abs_after else None
            ),
        },
    }

    output = {
        "summary": summary,
        "examplesThreePlusSameSideCompletionRun": examples_triple,
        "examplesObservedShareImbalanceIncreased": examples_inventory_increase,
        "interpretation": {
            "strictCycleSupport": (
                "High opposite-pair share, very few 3+ same-side completion runs, and tight opposite-side timing are compatible with strict paired generations."
            ),
            "strictCycleAgainst": (
                "Many 3+ same-side completion runs are hard to reconcile with a rule requiring both sides of generation N to fill before generation N+1, unless substantial unobserved cancel/replace behavior exists."
            ),
            "inventoryGateSupport": (
                "A high imbalance-reducing share suggests a softer inventory-aware controller that preferentially replenishes the lagging outcome rather than a strict generation lockstep."
            ),
        },
        "caveats": [
            "Public matches expose only filled maker amounts, not original order size, resting time, cancellation, or true terminal order state.",
            "lastExecutedAt is the last observed fill for a makerHash, not proof that the parent order was completely filled at that moment.",
            "Therefore this profiler can reject some overly strict models but cannot prove exact placement logic from matches alone.",
        ],
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"wrote {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())