from __future__ import annotations

import csv
import json
import random
import statistics
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PRIOR_FILES = [
    ROOT / "data/research/target_controller_hazard_v21_20260818_1136_1445_states.csv",
    ROOT / "data/research/target_controller_hazard_v21_20260818_1446_1555_states.csv",
]
FRESH = ROOT / "data/research/target_controller_hazard_v21_20260818_1556_1655_states.csv"
BLIND = ROOT / "data/research/target_deadline_recovery_failure_v1_blind.csv"
REVEAL = ROOT / "data/research/target_deadline_recovery_failure_v1_reveal.csv"
REPORT = ROOT / "data/research/target_deadline_recovery_failure_v1_report.json"
SEED = 202608181655
MIN_SINCE_TAKER_MS = 30_000
MIN_SECONDS_LEFT = 10.0
MAX_SECONDS_LEFT = 90.0
DEADLINE_SECONDS = 30.0  # independently rejected as sufficient in V7; used here only as a necessary urgency component.
EPS = 1e-9


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [dict(r) for r in csv.DictReader(handle)]


def _f(row: dict[str, Any], key: str) -> float | None:
    try:
        x = float(row.get(key, ""))
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def _eligible(row: dict[str, Any]) -> bool:
    since = _f(row, "time_since_last_taker_ms")
    sec = _f(row, "seconds_left")
    return since is not None and sec is not None and since >= MIN_SINCE_TAKER_MS and MIN_SECONDS_LEFT <= sec <= MAX_SECONDS_LEFT


def _unresolved_ratio(row: dict[str, Any]) -> float | None:
    gap = _f(row, "abs_payoff_gap")
    mx = _f(row, "max_gap_since_taker_reset")
    if gap is None or mx is None or mx <= EPS:
        return None
    return gap / mx


def _quantile(values: list[float], p: float) -> float:
    xs = sorted(values)
    if not xs:
        raise ValueError("empty quantile")
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    w = k - lo
    return xs[lo] * (1 - w) + xs[hi] * w


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def main() -> int:
    # Freeze thresholds from PRIOR UNLABELED state distributions only.
    prior: list[dict[str, str]] = []
    for path in PRIOR_FILES:
        prior.extend(r for r in _read(path) if _eligible(r))
    ratios = [x for r in prior if (x := _unresolved_ratio(r)) is not None]
    maker_counts = [x for r in prior if (x := _f(r, "maker_parents_since_last_taker")) is not None]
    unresolved_cut = _quantile(ratios, 0.75)
    maker_cut = _quantile(maker_counts, 0.50)

    fresh = [r for r in _read(FRESH) if _eligible(r)]
    by_market: dict[int, list[dict[str, str]]] = {}
    for r in fresh:
        try:
            mid = int(float(r["market_id"]))
            t = int(float(r["sample_ms"]))
        except (TypeError, ValueError):
            continue
        r = dict(r)
        r["_mid"] = str(mid)
        r["_t"] = str(t)
        by_market.setdefault(mid, []).append(r)
    for rows in by_market.values():
        rows.sort(key=lambda r: int(r["_t"]))

    rng = random.Random(SEED)
    selected: list[dict[str, Any]] = []
    for mid in sorted(by_market):
        rows = by_market[mid]
        crossing: dict[str, str] | None = None
        for r in rows:
            ratio = _unresolved_ratio(r)
            makers = _f(r, "maker_parents_since_last_taker")
            sec = _f(r, "seconds_left")
            if ratio is None or makers is None or sec is None:
                continue
            if sec <= DEADLINE_SECONDS and ratio >= unresolved_cut and makers >= maker_cut:
                crossing = r
                break
        chosen = crossing if crossing is not None else rng.choice(rows)
        ratio = _unresolved_ratio(chosen)
        makers = _f(chosen, "maker_parents_since_last_taker")
        sec = _f(chosen, "seconds_left")
        gate = bool(crossing is chosen)
        selected.append({
            "market_id": mid,
            "sample_ms": int(chosen["_t"]),
            "seconds_left": sec,
            "time_since_last_taker_ms": _f(chosen, "time_since_last_taker_ms"),
            "maker_parents_since_last_taker": makers,
            "abs_payoff_gap": _f(chosen, "abs_payoff_gap"),
            "max_gap_since_taker_reset": _f(chosen, "max_gap_since_taker_reset"),
            "unresolved_ratio": ratio,
            "risk_deficit": _f(chosen, "risk_deficit"),
            "deadline_component": int(sec is not None and sec <= DEADLINE_SECONDS),
            "maker_activity_component": int(makers is not None and makers >= maker_cut),
            "unresolved_component": int(ratio is not None and ratio >= unresolved_cut),
            "prediction": "REPAIR" if gate else "PASSIVE",
            "selection": "FIRST_GATE_CROSSING" if gate else "SEEDED_CONTROL",
        })

    blind_fields = list(selected[0].keys()) if selected else ["market_id"]
    _write_csv(BLIND, selected, blind_fields)  # Critical boundary: blind artifact exists before labels are consulted below.

    # Reveal only after blind decisions are durable.
    fresh_lookup = {(int(float(r["market_id"])), int(float(r["sample_ms"]))): r for r in fresh}
    reveal: list[dict[str, Any]] = []
    for b in selected:
        raw = fresh_lookup[(int(b["market_id"]), int(b["sample_ms"]))]
        repair5 = int(float(raw.get("repair_within_5s") or 0))
        taker5 = int(float(raw.get("taker_within_5s") or 0))
        add5 = int(float(raw.get("add_within_5s") or 0))
        target = "REPAIR" if repair5 else "OTHER_TAKER" if taker5 else "PASSIVE"
        reveal.append({**b, "target_repair_5s": repair5, "target_taker_5s": taker5, "target_add_5s": add5, "target": target})
    _write_csv(REVEAL, reveal, list(reveal[0].keys()) if reveal else blind_fields)

    predicted = [r for r in reveal if r["prediction"] == "REPAIR"]
    target_pos = [r for r in reveal if r["target_repair_5s"] == 1]
    tp = sum(r["prediction"] == "REPAIR" and r["target_repair_5s"] == 1 for r in reveal)
    correct = sum((r["prediction"] == "REPAIR") == (r["target_repair_5s"] == 1) for r in reveal)
    baseline = sum(r["target_repair_5s"] == 0 for r in reveal)

    report = {
        "reportVersion": "TARGET_DEADLINE_RECOVERY_FAILURE_V1",
        "researchOnly": True,
        "liveChanges": False,
        "parameterSweep": False,
        "modelFit": False,
        "hypothesis": "After >=30s without Taker activity, deadline proximity becomes a fresh REPAIR trigger only when substantial Maker activity has occurred but the directional payoff gap remains unresolved near its since-Taker maximum.",
        "priorUnlabeledFreeze": {
            "files": [str(p.relative_to(ROOT)) for p in PRIOR_FILES],
            "eligibleRows": len(prior),
            "unresolvedRatioP75": unresolved_cut,
            "makerParentsMedian": maker_cut,
            "deadlineSeconds": DEADLINE_SECONDS,
            "note": "Deadline <=30s was previously rejected as sufficient; here it is only one necessary component. Thresholds are not fitted to fresh labels."
        },
        "frozenRule": f"time_since_last_taker>=30s AND seconds_left<=30 AND maker_parents_since_last_taker>={maker_cut:g} AND abs_gap/max_gap_since_taker_reset>={unresolved_cut:.6f} => REPAIR within 5s; otherwise PASSIVE",
        "freshSource": str(FRESH.relative_to(ROOT)),
        "blindBoundary": {"blindWrittenBeforeReveal": True, "oneRuntimeRealizableDecisionPerMarket": True, "randomSeed": SEED},
        "result": {
            "markets": len(reveal),
            "eligibleRows": len(fresh),
            "predictedRepair": len(predicted),
            "targetRepair": len(target_pos),
            "truePositive": tp,
            "accuracy": correct / len(reveal) if reveal else None,
            "alwaysPassiveAccuracy": baseline / len(reveal) if reveal else None,
            "precision": tp / len(predicted) if predicted else None,
            "recall": tp / len(target_pos) if target_pos else None,
            "gateMarkets": [int(r["market_id"]) for r in predicted],
            "targetRepairMarkets": [int(r["market_id"]) for r in target_pos],
        },
        "outputs": {"blind": str(BLIND.relative_to(ROOT)), "reveal": str(REVEAL.relative_to(ROOT)), "report": str(REPORT.relative_to(ROOT))},
    }
    tmp = REPORT.with_suffix(REPORT.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(REPORT)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
