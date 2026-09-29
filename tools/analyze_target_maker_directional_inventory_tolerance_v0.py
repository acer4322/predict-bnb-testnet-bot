from __future__ import annotations

import bisect
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import analyze_target_maker_taker_inventory_lifecycle_v1 as lifecycle
import analyze_target_maker_taker_inventory_lifecycle_v1_1 as lifecycle_v11

ROOT = Path(__file__).resolve().parents[1]
RISK_CSV = ROOT / "data" / "research" / "target_maker_taker_repair_hazard_v2_risk.csv"
TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
PUBLIC_DATASET = ROOT / "data" / "research" / "target_taker_action_burst_hazard_v1.csv"
REPORT = ROOT / "data" / "research" / "target_maker_directional_inventory_tolerance_v0_report.json"
SPECIAL_START = "2026-08-16T12:00:00+08:00"
HORIZON_MS = 5000
EPS = 1e-9


def _finite(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def _bucket(p: float) -> str:
    if p < 0.40:
        return "LOW_LT_040"
    if p < 0.60:
        return "MID_040_060"
    if p < 0.80:
        return "HIGH_060_080"
    return "VERY_HIGH_GE_080"


def _mean(xs: list[float]) -> float | None:
    return statistics.fmean(xs) if xs else None


def _market_blocked(rows: list[dict[str, Any]], key: str) -> float | None:
    by_market: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        by_market[int(r["market_id"])].append(float(r[key]))
    return _mean([statistics.fmean(v) for v in by_market.values()])


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"rows": 0, "markets": 0}
    any_rows = [r for r in rows if int(r["any_taker_5s"]) == 1]
    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_market[int(r["market_id"])].append(r)
    market_votes = {"WITH_GREATER": 0, "AGAINST_GREATER": 0, "EQUAL": 0}
    oriented_signs = {"POSITIVE_WITH": 0, "NEGATIVE_AGAINST": 0, "ZERO": 0}
    for mrows in by_market.values():
        wr = statistics.fmean(float(r["with_5s"]) for r in mrows)
        ar = statistics.fmean(float(r["against_5s"]) for r in mrows)
        on = statistics.fmean(float(r["oriented_net_effect_shares_5s"]) for r in mrows)
        if wr > ar + EPS:
            market_votes["WITH_GREATER"] += 1
        elif ar > wr + EPS:
            market_votes["AGAINST_GREATER"] += 1
        else:
            market_votes["EQUAL"] += 1
        if on > EPS:
            oriented_signs["POSITIVE_WITH"] += 1
        elif on < -EPS:
            oriented_signs["NEGATIVE_AGAINST"] += 1
        else:
            oriented_signs["ZERO"] += 1
    return {
        "rows": len(rows),
        "markets": len({int(r["market_id"]) for r in rows}),
        "meanMakerAbsDelta": _mean([float(r["maker_abs_delta"]) for r in rows]),
        "anyTaker5sRate": _mean([float(r["any_taker_5s"]) for r in rows]),
        "againstMakerHeavy5sRate": _mean([float(r["against_5s"]) for r in rows]),
        "withMakerHeavy5sRate": _mean([float(r["with_5s"]) for r in rows]),
        "hold5sRate": _mean([float(r["hold_5s"]) for r in rows]),
        "marketBlockedAgainst5sRate": _market_blocked(rows, "against_5s"),
        "marketBlockedWith5sRate": _market_blocked(rows, "with_5s"),
        "marketBlockedHold5sRate": _market_blocked(rows, "hold_5s"),
        "marketBlockedOrientedNetEffectShares5s": _market_blocked(rows, "oriented_net_effect_shares_5s"),
        "marketVoteWithVsAgainst": market_votes,
        "marketOrientedNetSign": oriented_signs,
        "meanOrientedNetEffectShares5s": _mean([float(r["oriented_net_effect_shares_5s"]) for r in rows]),
        "meanAgainstShares5s": _mean([float(r["against_shares_5s"]) for r in rows]),
        "meanWithShares5s": _mean([float(r["with_shares_5s"]) for r in rows]),
        "givenAnyTaker": {
            "rows": len(any_rows),
            "againstRate": _mean([float(r["against_5s"]) for r in any_rows]),
            "withRate": _mean([float(r["with_5s"]) for r in any_rows]),
            "meanOrientedNetEffectShares": _mean([float(r["oriented_net_effect_shares_5s"]) for r in any_rows]),
        },
    }


def _load_actions() -> dict[int, tuple[list[dict[str, Any]], list[int]]]:
    db = lifecycle._connect_ro(TARGET_DB)
    try:
        events, _ = lifecycle._load_official_events(db, asset="BTC")
        parents = lifecycle._build_parents(events)
        market_results = lifecycle._load_market_results(db, "BTC")
    finally:
        db.close()
    cohort, phase_index, _ = lifecycle._load_public_cohorts(
        PUBLIC_DATASET, special_start_ms=lifecycle._epoch_ms(SPECIAL_START)
    )
    actions, _ = lifecycle_v11._replay(
        events,
        parents,
        cohort=cohort,
        phase_index=phase_index,
        market_results=market_results,
        max_phase_lag_ms=2000,
    )
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for a in actions:
        out[int(a["market_id"])].append(a)
    indexed: dict[int, tuple[list[dict[str, Any]], list[int]]] = {}
    for mid, xs in out.items():
        xs.sort(key=lambda r: (int(r["first_event_ms"]), int(r["last_event_ms"]), str(r["parent_id"])))
        indexed[mid] = (xs, [int(r["first_event_ms"]) for r in xs])
    return indexed


def main() -> int:
    print("TARGET_MAKER_DIRECTIONAL_INVENTORY_TOLERANCE_V0", flush=True)
    print("[1/3] Load deduped Target Taker parents...", flush=True)
    action_index = _load_actions()

    print("[2/3] Orient future 5s Taker flow to current Maker-heavy side...", flush=True)
    rows: list[dict[str, Any]] = []
    with RISK_CSV.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            delta = _finite(raw.get("maker_delta"))
            prob = _finite(raw.get("heavy_win_probability"))
            if delta is None or prob is None or abs(delta) <= EPS:
                continue
            mid = int(float(raw["market_id"]))
            sampled = int(float(raw["sampled_ms"]))
            actions, times = action_index.get(mid, ([], []))
            lo = bisect.bisect_right(times, sampled)
            hi = bisect.bisect_right(times, sampled + HORIZON_MS)
            future = actions[lo:hi]
            sign = 1.0 if delta > 0 else -1.0
            against_shares = 0.0
            with_shares = 0.0
            oriented_net = 0.0
            against = 0
            with_heavy = 0
            for a in future:
                shares = float(a["shares"])
                effect = float(lifecycle._directional_effect(str(a["side"]), str(a["quote_type"]), shares))
                oriented = sign * effect
                oriented_net += oriented
                if oriented < -EPS:
                    against = 1
                    against_shares += abs(effect)
                elif oriented > EPS:
                    with_heavy = 1
                    with_shares += abs(effect)
            rows.append({
                "market_id": mid,
                "regime": str(raw["regime"]),
                "phase": str(raw["phase"]),
                "lifecycle_state": str(raw["lifecycle_state"]),
                "maker_abs_delta": abs(delta),
                "heavy_win_probability": prob,
                "bucket": _bucket(prob),
                "any_taker_5s": int(bool(future)),
                "against_5s": against,
                "with_5s": with_heavy,
                "hold_5s": int(not future),
                "against_shares_5s": against_shares,
                "with_shares_5s": with_shares,
                "oriented_net_effect_shares_5s": oriented_net,
            })

    primary = [
        r for r in rows
        if r["regime"] == "ORDINARY_PRE_SPECIAL"
        and r["lifecycle_state"] == "POST_FIRST_TAKER"
        and r["phase"] in {"MID", "TAIL"}
    ]
    special = [
        r for r in rows
        if r["regime"] == "SPECIAL"
        and r["lifecycle_state"] == "POST_FIRST_TAKER"
        and r["phase"] in {"MID", "TAIL"}
    ]

    def bundle(xs: list[dict[str, Any]]) -> dict[str, Any]:
        by_bucket = {b: _summary([r for r in xs if r["bucket"] == b]) for b in (
            "LOW_LT_040", "MID_040_060", "HIGH_060_080", "VERY_HIGH_GE_080"
        )}
        low = [r for r in xs if float(r["heavy_win_probability"]) < 0.40]
        high = [r for r in xs if float(r["heavy_win_probability"]) >= 0.60]
        return {
            "overall": _summary(xs),
            "byHeavyWinProbability": by_bucket,
            "fixedLowVsHigh": {
                "LOW_LT_040": _summary(low),
                "HIGH_GE_060": _summary(high),
            },
        }

    report = {
        "reportVersion": "TARGET_MAKER_DIRECTIONAL_INVENTORY_TOLERANCE_V0",
        "researchOnly": True,
        "liveChanges": False,
        "parameterSweep": False,
        "hypothesis": "Maker-heavy inventory aligned with public market direction is tolerated more, and may sometimes receive same-direction Taker flow instead of being neutralized.",
        "definitions": {
            "makerHeavy": "sign(maker_up_position - maker_down_position) at target-blind risk snapshot",
            "marketAlignmentProxy": "heavy_win_probability = public Predict midpoint of Maker-heavy outcome",
            "AGAINST": "future Taker directional effect within 5s has opposite sign to current Maker-heavy delta",
            "WITH": "future Taker directional effect within 5s has same sign as current Maker-heavy delta",
            "HOLD": "no future Taker parent within 5s",
            "orientedNetEffect": "sum future Taker directional effect * sign(current Maker-heavy delta); positive leans further with Maker-heavy side, negative offsets it",
            "primarySlice": "ORDINARY_PRE_SPECIAL + POST_FIRST_TAKER + MID/TAIL",
            "fixedBuckets": ["<0.40", "0.40-0.60", "0.60-0.80", ">=0.80"],
        },
        "coverage": {
            "allRiskRowsWithMakerImbalance": len(rows),
            "ordinaryPrimaryRows": len(primary),
            "ordinaryPrimaryMarkets": len({int(r["market_id"]) for r in primary}),
            "specialAuditRows": len(special),
            "specialAuditMarkets": len({int(r["market_id"]) for r in special}),
        },
        "ordinaryPrimary": bundle(primary),
        "specialAudit": bundle(special),
        "guardrails": [
            "Public Predict midpoint is a proxy for market direction, not proof of Target private belief.",
            "Rows are serially correlated; market-blocked rates are reported alongside raw rates.",
            "AGAINST/WITH classify Taker flow relative to the Maker-heavy side at snapshot time; they are actuator-direction labels, not semantic OPEN/ADD/REPAIR/INSURANCE ground truth.",
            "Special regime is audit-only and is not used to choose thresholds.",
        ],
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[3/3] Result", flush=True)
    for label, xs in (("ORDINARY", primary), ("SPECIAL", special)):
        b = bundle(xs)["byHeavyWinProbability"]
        print(label, flush=True)
        for k, v in b.items():
            print(k, "rows=", v.get("rows"), "blocked_against=", v.get("marketBlockedAgainst5sRate"), "blocked_with=", v.get("marketBlockedWith5sRate"), "blocked_hold=", v.get("marketBlockedHold5sRate"), "blocked_oriented_net=", v.get("marketBlockedOrientedNetEffectShares5s"), flush=True)
    print(f"report={REPORT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
