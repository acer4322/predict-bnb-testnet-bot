from __future__ import annotations

import bisect
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import analyze_target_maker_directional_inventory_tolerance_v0 as directional
import analyze_target_maker_taker_inventory_lifecycle_v1 as lifecycle

ROOT = Path(__file__).resolve().parents[1]
RISK_CSV = ROOT / "data" / "research" / "target_maker_taker_repair_hazard_v2_risk.csv"
PAIRS_CSV = ROOT / "data" / "research" / "target_maker_taker_complete_set_v2_pairs.csv"
REPORT = ROOT / "data" / "research" / "target_favorable_inventory_architecture_v1_report.json"
HORIZON_MS = 5000
EPS = 1e-9


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _market_blocked(rows: list[dict[str, Any]], key: str) -> float | None:
    grouped: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        grouped[int(row["market_id"])].append(float(row[key]))
    values = [statistics.fmean(xs) for xs in grouped.values() if xs]
    return _mean(values)


def _residual_bucket(ratio: float) -> str:
    if ratio <= 0.0:
        return "OVERHEDGED_LE_0"
    if ratio < 0.25:
        return "RESIDUAL_0_025"
    if ratio < 0.75:
        return "RESIDUAL_025_075"
    if ratio <= 1.25:
        return "RESIDUAL_075_125"
    return "AMPLIFIED_GT_125"


def _prob_bucket(p: float) -> str:
    if p < 0.20:
        return "LT_020"
    if p < 0.40:
        return "020_040"
    if p < 0.60:
        return "040_060"
    if p < 0.80:
        return "060_080"
    return "GE_080"


def _state(alignment: float) -> str:
    if alignment > EPS:
        return "FAVORABLE"
    if alignment < -EPS:
        return "UNFAVORABLE"
    return "BALANCED"


def _load_state_rows() -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]], dict[int, list[int]]]:
    action_index = directional._load_actions()
    rows: list[dict[str, Any]] = []
    risk_index: dict[int, list[dict[str, Any]]] = defaultdict(list)

    with RISK_CSV.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            if str(raw["lifecycle_state"]) != "POST_FIRST_TAKER" or str(raw["phase"]) not in {"MID", "TAIL"}:
                continue
            maker_delta = float(raw["maker_delta"])
            if abs(maker_delta) <= EPS:
                continue
            combined_delta = float(raw["combined_delta"])
            p = float(raw["heavy_win_probability"])
            sign = 1.0 if maker_delta > 0 else -1.0
            oriented_combined = sign * combined_delta
            residual_ratio = oriented_combined / abs(maker_delta)
            alignment = oriented_combined * (2.0 * p - 1.0)
            market_id = int(float(raw["market_id"]))
            sampled_ms = int(float(raw["sampled_ms"]))

            actions, times = action_index.get(market_id, ([], []))
            lo = bisect.bisect_right(times, sampled_ms)
            hi = bisect.bisect_right(times, sampled_ms + HORIZON_MS)
            future = actions[lo:hi]
            oriented_taker_net = 0.0
            against = 0
            with_heavy = 0
            for action in future:
                effect = float(lifecycle._directional_effect(
                    str(action["side"]), str(action["quote_type"]), float(action["shares"])
                ))
                oriented = sign * effect
                oriented_taker_net += oriented
                if oriented < -EPS:
                    against = 1
                elif oriented > EPS:
                    with_heavy = 1

            market_direction = 1.0 if p > 0.5 else -1.0 if p < 0.5 else 0.0
            market_aligned_taker_net = oriented_taker_net * market_direction
            post_alignment = (oriented_combined + oriented_taker_net) * (2.0 * p - 1.0)
            row = {
                "market_id": market_id,
                "regime": str(raw["regime"]),
                "phase": str(raw["phase"]),
                "sampled_ms": sampled_ms,
                "maker_abs_delta": abs(maker_delta),
                "oriented_combined_delta": oriented_combined,
                "residual_ratio": residual_ratio,
                "residual_bucket": _residual_bucket(residual_ratio),
                "heavy_win_probability": p,
                "probability_bucket": _prob_bucket(p),
                "heavy_side_won": int(float(raw["heavy_side_won"])) if str(raw["heavy_side_won"]).strip() else None,
                "alignment": alignment,
                "alignment_abs": abs(alignment),
                "state": _state(alignment),
                "hold_5s": int(not future),
                "against_5s": against,
                "with_heavy_5s": with_heavy,
                "oriented_taker_net_5s": oriented_taker_net,
                "market_aligned_taker_net_5s": market_aligned_taker_net,
                "alignment_change_5s": post_alignment - alignment,
                "repair_taker_5s": int(float(raw["repair_taker_5s"])),
                "repair_shares_5s": float(raw["repair_shares_5s"]),
            }
            rows.append(row)
            risk_index[market_id].append(row)

    risk_times: dict[int, list[int]] = {}
    for market_id, market_rows in risk_index.items():
        market_rows.sort(key=lambda row: int(row["sampled_ms"]))
        risk_times[market_id] = [int(row["sampled_ms"]) for row in market_rows]
    return rows, risk_index, risk_times


def _state_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"rows": 0, "markets": 0}
    return {
        "rows": len(rows),
        "markets": len({int(row["market_id"]) for row in rows}),
        "marketBlockedHold5s": _market_blocked(rows, "hold_5s"),
        "marketBlockedAgainst5s": _market_blocked(rows, "against_5s"),
        "marketBlockedWithHeavy5s": _market_blocked(rows, "with_heavy_5s"),
        "marketBlockedMarketAlignedTakerNet5s": _market_blocked(rows, "market_aligned_taker_net_5s"),
        "marketBlockedAlignmentChange5s": _market_blocked(rows, "alignment_change_5s"),
        "marketBlockedMeanAlignment": _market_blocked(rows, "alignment"),
        "meanMakerAbsDelta": _mean([float(row["maker_abs_delta"]) for row in rows]),
        "meanAbsAlignment": _mean([float(row["alignment_abs"]) for row in rows]),
    }


def _within_market_hold(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row["state"] in {"FAVORABLE", "UNFAVORABLE"}:
            grouped[int(row["market_id"])][str(row["state"])].append(float(row["hold_5s"]))
    diffs: list[float] = []
    fav_higher = 0
    unfav_higher = 0
    ties = 0
    for states in grouped.values():
        if not states["FAVORABLE"] or not states["UNFAVORABLE"]:
            continue
        fav = statistics.fmean(states["FAVORABLE"])
        unfav = statistics.fmean(states["UNFAVORABLE"])
        diff = fav - unfav
        diffs.append(diff)
        if diff > EPS:
            fav_higher += 1
        elif diff < -EPS:
            unfav_higher += 1
        else:
            ties += 1
    return {
        "pairedMarkets": len(diffs),
        "meanHoldDifferenceFavorableMinusUnfavorable": _mean(diffs),
        "medianHoldDifferenceFavorableMinusUnfavorable": statistics.median(diffs) if diffs else None,
        "favorableHigherMarkets": fav_higher,
        "unfavorableHigherMarkets": unfav_higher,
        "ties": ties,
    }


def _alignment_strength(rows: list[dict[str, Any]]) -> dict[str, Any]:
    active = [row for row in rows if row["state"] in {"FAVORABLE", "UNFAVORABLE"}]
    values = sorted(float(row["alignment_abs"]) for row in active)
    if not values:
        return {}
    q1 = values[len(values) // 3]
    q2 = values[(2 * len(values)) // 3]
    result: dict[str, Any] = {"descriptiveTercileCuts": [q1, q2], "note": "descriptive only; not a promoted threshold"}
    for state in ("FAVORABLE", "UNFAVORABLE"):
        result[state] = {}
        for name, lo, hi in (("LOW", -1.0, q1), ("MID", q1, q2), ("HIGH", q2, float("inf"))):
            subset = [
                row for row in active
                if row["state"] == state and float(row["alignment_abs"]) > lo and float(row["alignment_abs"]) <= hi
            ]
            result[state][name] = _state_summary(subset)
    return result


def _residual_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    names = ("OVERHEDGED_LE_0", "RESIDUAL_0_025", "RESIDUAL_025_075", "RESIDUAL_075_125", "AMPLIFIED_GT_125")
    return {name: _state_summary([row for row in rows if row["residual_bucket"] == name]) for name in names}


def _probability_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for name in ("LT_020", "020_040", "040_060", "060_080", "GE_080"):
        subset = [row for row in rows if row["probability_bucket"] == name]
        wins = [int(row["heavy_side_won"]) for row in subset if row["heavy_side_won"] is not None]
        output[name] = {
            **_state_summary(subset),
            "heavySideWinRateAudit": _mean([float(v) for v in wins]),
            "rawRepair5sRate": _mean([float(row["repair_taker_5s"]) for row in subset]),
            "meanRepairSharesGivenRepair": _mean([
                float(row["repair_shares_5s"]) for row in subset if float(row["repair_shares_5s"]) > EPS
            ]),
        }
    return output


def _repair_economics(risk_index: dict[int, list[dict[str, Any]]], risk_times: dict[int, list[int]]) -> dict[str, Any]:
    joined: list[dict[str, Any]] = []
    with PAIRS_CSV.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            if int(float(raw["repair_against_maker_heavy"])) != 1:
                continue
            market_id = int(float(raw["market_id"]))
            taker_ms = int(float(raw["taker_first_event_ms"]))
            times = risk_times.get(market_id, [])
            pos = bisect.bisect_left(times, taker_ms) - 1
            if pos < 0:
                continue
            state_row = risk_index[market_id][pos]
            lag_ms = taker_ms - int(state_row["sampled_ms"])
            if lag_ms > 2000:
                continue
            joined.append({
                "regime": str(raw["regime"]),
                "market_id": market_id,
                "state": str(state_row["state"]),
                "alignment": float(state_row["alignment"]),
                "shares": float(raw["paired_shares"]),
                "pair_cost": float(raw["pair_cost"]),
                "raw_edge_usdt": float(raw["raw_locked_edge_usdt"]),
                "pair_class": str(raw["pair_class_raw"]),
            })

    def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {"fragments": 0, "markets": 0}
        shares = sum(float(row["shares"]) for row in rows)
        classes: dict[str, float] = defaultdict(float)
        for row in rows:
            classes[str(row["pair_class"])] += float(row["shares"])
        return {
            "fragments": len(rows),
            "markets": len({int(row["market_id"]) for row in rows}),
            "pairedShares": shares,
            "shareWeightedPairCost": sum(float(row["pair_cost"]) * float(row["shares"]) for row in rows) / shares,
            "rawEdgeUsdt": sum(float(row["raw_edge_usdt"]) for row in rows),
            "pairClassShareRates": {key: value / shares for key, value in classes.items()},
            "shareWeightedAlignment": sum(float(row["alignment"]) * float(row["shares"]) for row in rows) / shares,
        }

    output: dict[str, Any] = {}
    for regime in ("ORDINARY_PRE_SPECIAL", "SPECIAL"):
        output[regime] = {
            state: summarize([row for row in joined if row["regime"] == regime and row["state"] == state])
            for state in ("FAVORABLE", "UNFAVORABLE", "BALANCED")
        }
    output["join"] = {
        "fragments": len(joined),
        "markets": len({int(row["market_id"]) for row in joined}),
        "asofMaxLagMs": 2000,
    }
    return output


def main() -> int:
    print("TARGET_FAVORABLE_INVENTORY_ARCHITECTURE_V1", flush=True)
    rows, risk_index, risk_times = _load_state_rows()
    report: dict[str, Any] = {
        "reportVersion": "TARGET_FAVORABLE_INVENTORY_ARCHITECTURE_V1",
        "researchOnly": True,
        "liveChanges": False,
        "modelFit": False,
        "parameterSweep": False,
        "question": "Can FAVORABLE/UNFAVORABLE inventory be explained as a whole-portfolio controller state rather than a Maker-only imbalance label?",
        "definitions": {
            "orientedCombinedDelta": "sign(Maker delta) * (Maker + prior Taker combined delta)",
            "wholePortfolioAlignment": "orientedCombinedDelta * (2 * public heavy-side win probability - 1)",
            "FAVORABLE": "wholePortfolioAlignment > 0",
            "UNFAVORABLE": "wholePortfolioAlignment < 0",
            "BALANCED": "wholePortfolioAlignment == 0",
            "interpretation": "positive means the total observed portfolio residual points toward the currently higher-probability outcome; negative means it points against it",
            "primarySlice": "POST_FIRST_TAKER + MID/TAIL; ordinary vs frozen 2026-08-16 12:00+ special cohort",
        },
        "coverage": {},
        "regimes": {},
        "repairEconomics": {},
        "guardrails": [
            "Public Predict midpoint is a proxy for Target directional value, not proof of its private signal.",
            "FAVORABLE/UNFAVORABLE here is an architecture hypothesis, not a promoted live rule.",
            "Repeated one-second rows are serially correlated; market-blocked behavior and within-market contrasts are reported.",
            "Repair economics uses observed Maker/Taker FIFO pair attribution and a strict-past risk snapshot within 2 seconds; it is not proof the wallet internally linked those exact lots.",
            "The special cohort in this dataset is the frozen 2026-08-16 12:00+ cohort, not the separate pre-noon 03:40-11:35 stress window.",
        ],
    }

    for regime in ("ORDINARY_PRE_SPECIAL", "SPECIAL"):
        subset = [row for row in rows if row["regime"] == regime]
        report["regimes"][regime] = {
            "overall": _state_summary(subset),
            "byState": {state: _state_summary([row for row in subset if row["state"] == state]) for state in ("FAVORABLE", "UNFAVORABLE", "BALANCED")},
            "withinMarketHoldContrast": _within_market_hold(subset),
            "alignmentStrength": _alignment_strength(subset),
            "byWholePortfolioResidual": _residual_summary(subset),
            "probabilityAudit": _probability_audit(subset),
        }

    report["coverage"] = {
        "rows": len(rows),
        "markets": len({int(row["market_id"]) for row in rows}),
        "ordinaryRows": sum(row["regime"] == "ORDINARY_PRE_SPECIAL" for row in rows),
        "ordinaryMarkets": len({int(row["market_id"]) for row in rows if row["regime"] == "ORDINARY_PRE_SPECIAL"}),
        "specialRows": sum(row["regime"] == "SPECIAL" for row in rows),
        "specialMarkets": len({int(row["market_id"]) for row in rows if row["regime"] == "SPECIAL"}),
    }
    report["repairEconomics"] = _repair_economics(risk_index, risk_times)

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "coverage": report["coverage"],
        "ordinaryState": report["regimes"]["ORDINARY_PRE_SPECIAL"]["byState"],
        "ordinaryWithinMarket": report["regimes"]["ORDINARY_PRE_SPECIAL"]["withinMarketHoldContrast"],
        "specialState": report["regimes"]["SPECIAL"]["byState"],
        "specialWithinMarket": report["regimes"]["SPECIAL"]["withinMarketHoldContrast"],
        "repairEconomics": report["repairEconomics"],
        "report": str(REPORT),
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
