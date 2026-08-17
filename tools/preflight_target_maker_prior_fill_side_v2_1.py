from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BEHAVIOR = ROOT / "data" / "research" / "target_maker_direct_behavior_v1.csv"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_maker_prior_fill_side_v2_1_preflight.json"


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _read(path: Path) -> list[dict[str, Any]]:
    with path.expanduser().resolve().open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def _market_blocked(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[int, list[int]] = defaultdict(list)
    for row in rows:
        value = row.get(key)
        if value is None:
            continue
        grouped[int(row["market_id"])].append(int(value))
    rates = [sum(values) / len(values) for values in grouped.values() if values]
    return {
        "markets": len(rates),
        "meanRate": statistics.fmean(rates) if rates else None,
        "medianRate": statistics.median(rates) if rates else None,
        "minRate": min(rates) if rates else None,
        "maxRate": max(rates) if rates else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Seconds-only empirical preflight for prior Maker fill-side dependence")
    parser.add_argument("--behavior-dataset", type=Path, default=DEFAULT_BEHAVIOR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    rows = _read(args.behavior_dataset)
    required = {"market_id", "parent_id", "target_side", "placement_first_ms", "first_fill_ms", "label_side_up"}
    if not rows:
        raise SystemExit("behavior dataset is empty")
    missing = sorted(required - set(rows[0]))
    if missing:
        raise SystemExit("behavior dataset missing columns: " + ", ".join(missing))

    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    missing_fill = 0
    own_fill_before_placement = 0
    for row in rows:
        market_id = int(float(row["market_id"]))
        row["market_id"] = market_id
        row["placement_first_ms"] = int(float(row["placement_first_ms"]))
        fill = _finite(row.get("first_fill_ms"))
        row["first_fill_ms_numeric"] = int(fill) if fill is not None else None
        row["label_side_up"] = int(float(row["label_side_up"]))
        if fill is None:
            missing_fill += 1
        elif int(fill) < int(row["placement_first_ms"]):
            own_fill_before_placement += 1
        by_market[market_id].append(row)

    enriched: list[dict[str, Any]] = []
    for market_id, market_rows in by_market.items():
        placements = sorted(market_rows, key=lambda r: (int(r["placement_first_ms"]), str(r.get("parent_id") or "")))
        fill_events = sorted(
            [r for r in market_rows if r["first_fill_ms_numeric"] is not None],
            key=lambda r: (int(r["first_fill_ms_numeric"]), str(r.get("parent_id") or "")),
        )
        event_index = 0
        seen: set[str] = set()
        up_seen: set[str] = set()
        down_seen: set[str] = set()
        last_fill_side: str | None = None
        last_fill_ms: int | None = None

        for placement in placements:
            placement_ms = int(placement["placement_first_ms"])
            current_parent = str(placement.get("parent_id") or "")
            while event_index < len(fill_events) and int(fill_events[event_index]["first_fill_ms_numeric"]) < placement_ms:
                event = fill_events[event_index]
                parent_id = str(event.get("parent_id") or "")
                # Never allow the current parent to create its own prior state, even if inference timestamps are anomalous.
                if parent_id != current_parent and parent_id not in seen:
                    side = str(event.get("target_side") or "").upper()
                    seen.add(parent_id)
                    if side == "UP":
                        up_seen.add(parent_id)
                    elif side == "DOWN":
                        down_seen.add(parent_id)
                    last_fill_side = side if side in {"UP", "DOWN"} else last_fill_side
                    last_fill_ms = int(event["first_fill_ms_numeric"])
                event_index += 1

            current_side = "UP" if int(placement["label_side_up"]) == 1 else "DOWN"
            prior_count = len(seen)
            majority_side = "UP" if len(up_seen) > len(down_seen) else "DOWN" if len(down_seen) > len(up_seen) else None
            enriched.append(
                {
                    "market_id": market_id,
                    "prior_count": prior_count,
                    "prior_up_count": len(up_seen),
                    "prior_down_count": len(down_seen),
                    "current_side": current_side,
                    "last_side": last_fill_side,
                    "last_fill_age_ms": placement_ms - last_fill_ms if last_fill_ms is not None else None,
                    "same_as_last": int(current_side == last_fill_side) if last_fill_side in {"UP", "DOWN"} else None,
                    "opposite_last_correct": int(current_side != last_fill_side) if last_fill_side in {"UP", "DOWN"} else None,
                    "same_as_majority": int(current_side == majority_side) if majority_side else None,
                    "opposite_majority_correct": int(current_side != majority_side) if majority_side else None,
                }
            )

    with_prior = [r for r in enriched if r["prior_count"] > 0 and r["last_side"] in {"UP", "DOWN"}]
    majority_rows = [r for r in enriched if r["same_as_majority"] is not None]
    same_last = sum(int(r["same_as_last"]) for r in with_prior)
    same_majority = sum(int(r["same_as_majority"]) for r in majority_rows)

    buckets: dict[str, list[dict[str, Any]]] = {"1": [], "2_4": [], "5_9": [], "10_plus": []}
    for row in with_prior:
        n = int(row["prior_count"])
        key = "1" if n == 1 else "2_4" if n <= 4 else "5_9" if n <= 9 else "10_plus"
        buckets[key].append(row)

    payload = {
        "reportVersion": "TARGET_MAKER_PRIOR_FILL_SIDE_V2_1_EMPIRICAL_PREFLIGHT",
        "paperResearchOnly": True,
        "noModelTraining": True,
        "source": str(args.behavior_dataset.expanduser().resolve()),
        "rows": len(enriched),
        "markets": len(by_market),
        "missingFirstFillRows": missing_fill,
        "ownFirstFillBeforeOwnPlacementAnomalies": own_fill_before_placement,
        "rowsWithPriorMakerFillParent": len(with_prior),
        "rowsWithoutPriorMakerFillParent": len(enriched) - len(with_prior),
        "coverageRate": _rate(len(with_prior), len(enriched)),
        "rowLevel": {
            "sameAsLastFillSideRate": _rate(same_last, len(with_prior)),
            "oppositeLastFillSideAccuracy": _rate(len(with_prior) - same_last, len(with_prior)),
            "sameAsPriorMajoritySideRate": _rate(same_majority, len(majority_rows)),
            "oppositePriorMajoritySideAccuracy": _rate(len(majority_rows) - same_majority, len(majority_rows)),
            "majorityDefinedRows": len(majority_rows),
        },
        "marketBlocked": {
            "sameAsLastFillSide": _market_blocked(with_prior, "same_as_last"),
            "oppositeLastFillSide": _market_blocked(with_prior, "opposite_last_correct"),
            "sameAsPriorMajoritySide": _market_blocked(majority_rows, "same_as_majority"),
            "oppositePriorMajoritySide": _market_blocked(majority_rows, "opposite_majority_correct"),
        },
        "priorCountBuckets": {
            key: {
                "rows": len(group),
                "sameAsLastFillSideRate": _rate(sum(int(r["same_as_last"]) for r in group), len(group)),
                "oppositeLastFillSideAccuracy": _rate(sum(int(r["opposite_last_correct"]) for r in group), len(group)),
            }
            for key, group in buckets.items()
        },
        "interpretation": "This is a no-EBM evidence gate. A stable material deviation from 50% in row-level and market-blocked last-side/majority relations justifies a short OOF model. Near-50% means stop and seek other Maker state variables. first_fill_ms only gates whether a prior parent has started filling; no final shares are used, avoiding parent-size lookahead.",
    }

    report = args.report.expanduser().resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    print(f"Report: {report}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
