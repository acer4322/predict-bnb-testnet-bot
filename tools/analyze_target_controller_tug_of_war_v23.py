from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
REPORT_VERSION = "TARGET_CONTROLLER_TUG_OF_WAR_V23"
STRESS = "STRESS_2026_08_16"
ORDINARY = "ORDINARY_2026_08_17"
REGIMES = (STRESS, ORDINARY)
HORIZONS_MS = (1000, 3000, 5000)
DEFAULT_BURSTS = ROOT / "data" / "research" / "target_controller_hazard_v21_directional_bursts.csv"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_controller_tug_of_war_v23_report.json"
DEFAULT_PATTERNS = ROOT / "data" / "research" / "target_controller_tug_of_war_v23_patterns.csv"

PATTERN_FIELDS = [
    "regime", "market_id", "segment_id",
    "a_burst_id", "b_burst_id", "c_burst_id",
    "a_direction", "b_direction", "c_direction",
    "a_to_b_ms", "b_to_c_ms",
    "a_shares", "b_shares", "c_shares",
    "b_to_a_share_ratio", "c_to_a_share_ratio",
    "a_pre_risk", "a_post_risk", "b_pre_risk", "b_post_risk", "c_pre_risk", "c_post_risk",
    "a_risk_delta", "b_risk_delta", "c_risk_delta",
    "a_pre_gap", "a_post_gap", "b_pre_gap", "b_post_gap", "c_pre_gap", "c_post_gap",
    "a_gap_delta", "b_gap_delta", "c_gap_delta",
]


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _opposite(side: str) -> str:
    return "DOWN" if side == "UP" else "UP" if side == "DOWN" else "UNKNOWN"


def exposure_direction(row: dict[str, Any]) -> str:
    """Convert token side + buy/sell quote type into signed payoff exposure direction.

    BUY/BID UP -> UP exposure; BUY/BID DOWN -> DOWN exposure.
    SELL/ASK UP -> DOWN exposure; SELL/ASK DOWN -> UP exposure.
    Same-side bursts containing both BID and ASK parents are ambiguous and excluded
    from strict direction-pattern tests rather than guessed.
    """
    side = str(row.get("side") or "").upper()
    bids = _int(row.get("bid_parent_count"))
    asks = _int(row.get("ask_parent_count"))
    if side not in {"UP", "DOWN"}:
        return "UNKNOWN"
    if bids > 0 and asks == 0:
        return side
    if asks > 0 and bids == 0:
        return _opposite(side)
    return "UNKNOWN"


def _distribution(values: Iterable[float]) -> dict[str, Any]:
    xs = sorted(float(x) for x in values if math.isfinite(float(x)))
    if not xs:
        return {"n": 0}
    def q(p: float) -> float:
        if len(xs) == 1:
            return xs[0]
        pos = p * (len(xs) - 1)
        lo = int(math.floor(pos))
        hi = int(math.ceil(pos))
        if lo == hi:
            return xs[lo]
        frac = pos - lo
        return xs[lo] * (1.0 - frac) + xs[hi] * frac
    return {
        "n": len(xs), "min": xs[0], "p10": q(0.10), "p25": q(0.25),
        "median": q(0.50), "p75": q(0.75), "p90": q(0.90), "max": xs[-1],
        "mean": statistics.fmean(xs),
    }


def _load(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    rows: list[dict[str, Any]] = []
    with resolved.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "market_id", "segment_id", "regime", "burst_id", "first_event_ms", "last_event_ms",
            "side", "bid_parent_count", "ask_parent_count", "shares", "purpose",
            "pre_risk_deficit", "post_risk_deficit", "pre_abs_payoff_gap", "post_abs_payoff_gap",
        }
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise RuntimeError("directional bursts CSV missing columns: " + ", ".join(missing))
        for raw in reader:
            regime = str(raw.get("regime") or "")
            if regime not in REGIMES:
                continue
            row = dict(raw)
            row["market_id"] = _int(row.get("market_id"))
            row["segment_id"] = _int(row.get("segment_id"))
            row["first_event_ms"] = _int(row.get("first_event_ms"))
            row["last_event_ms"] = _int(row.get("last_event_ms"))
            row["shares"] = float(row.get("shares") or 0.0)
            row["purpose"] = str(row.get("purpose") or "").upper()
            row["exposure_direction"] = exposure_direction(row)
            rows.append(row)
    rows.sort(key=lambda r: (r["regime"], r["market_id"], r["segment_id"], r["first_event_ms"], str(r["burst_id"])))
    directions = Counter(str(r["exposure_direction"]) for r in rows)
    return rows, {
        "path": str(resolved), "rows": len(rows), "markets": len({(r['regime'], r['market_id']) for r in rows}),
        "exposureDirectionCounts": dict(directions),
        "strictDirectionUsableRate": (directions.get("UP", 0) + directions.get("DOWN", 0)) / len(rows) if rows else 0.0,
    }


def _gap_ms(left: dict[str, Any], right: dict[str, Any]) -> int:
    return int(right["first_event_ms"]) - int(left["last_event_ms"])


def _is_purpose_triplet(a: dict[str, Any], b: dict[str, Any], c: dict[str, Any], limit_ms: int) -> bool:
    return (
        a["purpose"] == "ADD" and b["purpose"] == "REPAIR" and c["purpose"] == "ADD"
        and 0 <= _gap_ms(a, b) <= limit_ms
        and 0 <= _gap_ms(b, c) <= limit_ms
    )


def _is_strict_tug(a: dict[str, Any], b: dict[str, Any], c: dict[str, Any], limit_ms: int) -> bool:
    if not _is_purpose_triplet(a, b, c, limit_ms):
        return False
    da, db, dc = a["exposure_direction"], b["exposure_direction"], c["exposure_direction"]
    return da in {"UP", "DOWN"} and db == _opposite(da) and dc == da


def _is_strict_pair(a: dict[str, Any], b: dict[str, Any], limit_ms: int) -> bool:
    if a["purpose"] != "ADD" or b["purpose"] != "REPAIR" or not (0 <= _gap_ms(a, b) <= limit_ms):
        return False
    da, db = a["exposure_direction"], b["exposure_direction"]
    return da in {"UP", "DOWN"} and db == _opposite(da)


def _pattern_row(a: dict[str, Any], b: dict[str, Any], c: dict[str, Any]) -> dict[str, Any]:
    def n(row: dict[str, Any], key: str) -> float:
        return float(row.get(key) or 0.0)
    a_sh, b_sh, c_sh = n(a, "shares"), n(b, "shares"), n(c, "shares")
    a_pre_r, a_post_r = n(a, "pre_risk_deficit"), n(a, "post_risk_deficit")
    b_pre_r, b_post_r = n(b, "pre_risk_deficit"), n(b, "post_risk_deficit")
    c_pre_r, c_post_r = n(c, "pre_risk_deficit"), n(c, "post_risk_deficit")
    a_pre_g, a_post_g = n(a, "pre_abs_payoff_gap"), n(a, "post_abs_payoff_gap")
    b_pre_g, b_post_g = n(b, "pre_abs_payoff_gap"), n(b, "post_abs_payoff_gap")
    c_pre_g, c_post_g = n(c, "pre_abs_payoff_gap"), n(c, "post_abs_payoff_gap")
    return {
        "regime": a["regime"], "market_id": a["market_id"], "segment_id": a["segment_id"],
        "a_burst_id": a["burst_id"], "b_burst_id": b["burst_id"], "c_burst_id": c["burst_id"],
        "a_direction": a["exposure_direction"], "b_direction": b["exposure_direction"], "c_direction": c["exposure_direction"],
        "a_to_b_ms": _gap_ms(a, b), "b_to_c_ms": _gap_ms(b, c),
        "a_shares": a_sh, "b_shares": b_sh, "c_shares": c_sh,
        "b_to_a_share_ratio": b_sh / a_sh if a_sh > 0 else "",
        "c_to_a_share_ratio": c_sh / a_sh if a_sh > 0 else "",
        "a_pre_risk": a_pre_r, "a_post_risk": a_post_r, "b_pre_risk": b_pre_r, "b_post_risk": b_post_r,
        "c_pre_risk": c_pre_r, "c_post_risk": c_post_r,
        "a_risk_delta": a_post_r - a_pre_r, "b_risk_delta": b_post_r - b_pre_r, "c_risk_delta": c_post_r - c_pre_r,
        "a_pre_gap": a_pre_g, "a_post_gap": a_post_g, "b_pre_gap": b_pre_g, "b_post_gap": b_post_g,
        "c_pre_gap": c_pre_g, "c_post_gap": c_post_g,
        "a_gap_delta": a_post_g - a_pre_g, "b_gap_delta": b_post_g - b_pre_g, "c_gap_delta": c_post_g - c_pre_g,
    }


def _analyze_regime(rows: list[dict[str, Any]], limit_ms: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    groups: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(int(row["market_id"]), int(row["segment_id"]))].append(row)
    for group in groups.values():
        group.sort(key=lambda r: (int(r["first_event_ms"]), str(r["burst_id"])))

    add_anchors = 0
    strict_pairs = 0
    purpose_pairs = 0
    strict_triplets = 0
    purpose_triplets = 0
    patterns: list[dict[str, Any]] = []
    markets_with_pattern: set[int] = set()

    for group in groups.values():
        for i, a in enumerate(group):
            if a["purpose"] != "ADD" or a["exposure_direction"] not in {"UP", "DOWN"}:
                continue
            add_anchors += 1
            if i + 1 >= len(group):
                continue
            b = group[i + 1]
            if b["purpose"] == "REPAIR" and 0 <= _gap_ms(a, b) <= limit_ms:
                purpose_pairs += 1
            if not _is_strict_pair(a, b, limit_ms):
                continue
            strict_pairs += 1
            if i + 2 >= len(group):
                continue
            c = group[i + 2]
            if _is_purpose_triplet(a, b, c, limit_ms):
                purpose_triplets += 1
            if _is_strict_tug(a, b, c, limit_ms):
                strict_triplets += 1
                markets_with_pattern.add(int(a["market_id"]))
                patterns.append(_pattern_row(a, b, c))

    pairs_den = strict_pairs
    result = {
        "bursts": len(rows), "markets": len({int(r['market_id']) for r in rows}),
        "addAnchorsStrictDirection": add_anchors,
        "adjacentAddToRepairWithinWindow": purpose_pairs,
        "strictOppositeRepairPairs": strict_pairs,
        "strictOppositeRepairPairRatePerAdd": strict_pairs / add_anchors if add_anchors else None,
        "purposeAddRepairAddTriplets": purpose_triplets,
        "strictTugOfWarTriplets": strict_triplets,
        "strictTugRatePerAdd": strict_triplets / add_anchors if add_anchors else None,
        "sameDirectionAddContinuationGivenStrictPair": strict_triplets / pairs_den if pairs_den else None,
        "strictShareAmongPurposeTriplets": strict_triplets / purpose_triplets if purpose_triplets else None,
        "marketsWithStrictTug": len(markets_with_pattern),
        "marketPatternRate": len(markets_with_pattern) / len({int(r['market_id']) for r in rows}) if rows else None,
        "patternTimingMs": {
            "addToRepair": _distribution(float(p["a_to_b_ms"]) for p in patterns),
            "repairToAdd": _distribution(float(p["b_to_c_ms"]) for p in patterns),
        },
        "patternSizing": {
            "aShares": _distribution(float(p["a_shares"]) for p in patterns),
            "bShares": _distribution(float(p["b_shares"]) for p in patterns),
            "cShares": _distribution(float(p["c_shares"]) for p in patterns),
            "bToAShareRatio": _distribution(float(p["b_to_a_share_ratio"]) for p in patterns if p["b_to_a_share_ratio"] != ""),
            "cToAShareRatio": _distribution(float(p["c_to_a_share_ratio"]) for p in patterns if p["c_to_a_share_ratio"] != ""),
        },
        "patternRiskTrajectory": {
            "aRiskDelta": _distribution(float(p["a_risk_delta"]) for p in patterns),
            "bRiskDelta": _distribution(float(p["b_risk_delta"]) for p in patterns),
            "cRiskDelta": _distribution(float(p["c_risk_delta"]) for p in patterns),
            "aGapDelta": _distribution(float(p["a_gap_delta"]) for p in patterns),
            "bGapDelta": _distribution(float(p["b_gap_delta"]) for p in patterns),
            "cGapDelta": _distribution(float(p["c_gap_delta"]) for p in patterns),
        },
    }
    return result, patterns


def _ratio(a: Any, b: Any) -> float | None:
    x, y = _num(a), _num(b)
    return x / y if x is not None and y is not None and y > 0 else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp = resolved.with_suffix(resolved.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(resolved)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp = resolved.with_suffix(resolved.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PATTERN_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(resolved)


def main() -> int:
    parser = argparse.ArgumentParser(description="Test ADD -> opposite REPAIR -> same-direction ADD Target controller tug-of-war sequences")
    parser.add_argument("--bursts", type=Path, default=DEFAULT_BURSTS)
    parser.add_argument("--max-gap-ms", type=int, default=5000)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--patterns", type=Path, default=DEFAULT_PATTERNS)
    args = parser.parse_args()

    rows, audit = _load(args.bursts)
    limit_ms = max(0, int(args.max_gap_ms))
    by_regime = {name: [r for r in rows if r["regime"] == name] for name in REGIMES}

    regimes: dict[str, Any] = {}
    pattern_rows: list[dict[str, Any]] = []
    for name in REGIMES:
        summary, patterns = _analyze_regime(by_regime[name], limit_ms)
        regimes[name] = summary
        pattern_rows.extend(patterns)

    horizon_sensitivity: dict[str, Any] = {}
    for horizon in HORIZONS_MS:
        per = {}
        for name in REGIMES:
            summary, _ = _analyze_regime(by_regime[name], horizon)
            per[name] = {
                "strictPairRatePerAdd": summary["strictOppositeRepairPairRatePerAdd"],
                "strictTugRatePerAdd": summary["strictTugRatePerAdd"],
                "continuationGivenPair": summary["sameDirectionAddContinuationGivenStrictPair"],
            }
        horizon_sensitivity[f"{horizon // 1000}s"] = {
            **per,
            "stressVsOrdinaryTugRateRatio": _ratio(per[STRESS]["strictTugRatePerAdd"], per[ORDINARY]["strictTugRatePerAdd"]),
            "stressVsOrdinaryPairRateRatio": _ratio(per[STRESS]["strictPairRatePerAdd"], per[ORDINARY]["strictPairRatePerAdd"]),
        }

    stress, ordinary = regimes[STRESS], regimes[ORDINARY]
    report = {
        "reportVersion": REPORT_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "paperResearchOnly": True, "noModelFit": True, "noStrategyPromotion": True,
        "hypothesis": "A high opportunity/greed controller can keep desired exposure pointed in one direction while a risk controller briefly repairs the opposite way, producing ADD -> opposite REPAIR -> same-direction ADD sequences.",
        "inputAudit": audit,
        "rules": {
            "source": "V2.1 directional bursts CSV only; no DB replay",
            "adjacency": "A, B, C must be consecutive directional bursts in the same market and segment",
            "maxGapMsEachLeg": limit_ms,
            "strictPattern": "A=ADD direction X; B=REPAIR exposure direction opposite X; C=ADD exposure direction X",
            "exposureDirection": "BID buys token side; ASK sells token side and therefore flips signed payoff exposure direction; mixed BID+ASK directional bursts are UNKNOWN and excluded from strict pattern tests.",
        },
        "regimes": regimes,
        "stressVsOrdinary": {
            "strictOppositeRepairPairRateRatio": _ratio(stress["strictOppositeRepairPairRatePerAdd"], ordinary["strictOppositeRepairPairRatePerAdd"]),
            "strictTugRatePerAddRatio": _ratio(stress["strictTugRatePerAdd"], ordinary["strictTugRatePerAdd"]),
            "continuationGivenPairRatio": _ratio(stress["sameDirectionAddContinuationGivenStrictPair"], ordinary["sameDirectionAddContinuationGivenStrictPair"]),
            "marketPatternRateRatio": _ratio(stress["marketPatternRate"], ordinary["marketPatternRate"]),
        },
        "horizonSensitivity": horizon_sensitivity,
        "interpretationGuide": {
            "strongSupport": "Stress has materially higher strict tug rate per ADD and/or continuation-after-pair, with A tending to raise risk/gap, B tending to reduce it, and C tending to rebuild exposure.",
            "riskOnlyAlternative": "ADD->opposite REPAIR pairs increase but same-direction ADD continuation does not; this looks more like defensive repair than persistent greed/opportunity pressure.",
            "noSupport": "Strict patterns are equally rare in stress and ordinary after using consecutive bursts and signed exposure direction.",
        },
        "outputs": {"report": str(args.report), "patternsCsv": str(args.patterns)},
    }
    _write_json(args.report, report)
    _write_csv(args.patterns, pattern_rows)

    print(REPORT_VERSION)
    print(f"rows={len(rows):,} strict_direction_usable={audit['strictDirectionUsableRate']:.3%} max_gap_ms={limit_ms}")
    for name in REGIMES:
        r = regimes[name]
        print(
            f"{name}: add={r['addAnchorsStrictDirection']:,} pairs={r['strictOppositeRepairPairs']:,} "
            f"tugs={r['strictTugOfWarTriplets']:,} tug/add={r['strictTugRatePerAdd']} continuation={r['sameDirectionAddContinuationGivenStrictPair']}"
        )
    print(f"report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
