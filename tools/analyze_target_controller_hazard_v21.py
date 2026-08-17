from __future__ import annotations

import argparse
import bisect
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import analyze_target_controller_complete_history_v2_compat as compat

v2 = compat.v2
core = v2.core
ROOT = Path(__file__).resolve().parents[1]
REPORT_VERSION = "TARGET_CONTROLLER_HAZARD_V21_OFFICIAL_FIXED_GRID"
DEFAULT_DB = ROOT / "data" / "target_wallet_official_v1.db"
CORRECTION = {"RISK_REDUCING", "TAIL_IMPROVING", "GAP_REDUCING_EXPENSIVE"}
HORIZONS = (1, 3, 5, 15)
EPS = 1e-9

DEFAULTS = {
    "stress_start": "2026-08-16T05:20:00+08:00",
    "stress_end": "2026-08-16T11:35:00+08:00",
    "ordinary_start": "2026-08-17T05:20:00+08:00",
    "ordinary_end": "2026-08-17T11:35:00+08:00",
}

DIRECTIONAL_FIELDS = [
    "market_id", "segment_id", "regime", "burst_id", "first_event_ms", "last_event_ms",
    "duration_ms", "parent_count", "side", "bid_parent_count", "ask_parent_count",
    "shares", "notional_usdt", "vwap", "min_price", "max_price",
    "pre_risk_deficit", "pre_abs_payoff_gap", "pre_maker_abs_payoff_gap",
    "post_risk_deficit", "post_abs_payoff_gap", "delta_worst_case_pnl",
    "risk_deficit_reduction", "abs_gap_reduction", "portfolio_effect", "purpose",
]
EXECUTION_FIELDS = ["regime", *v2.BURST_FIELDS]
STATE_FIELDS = [
    "market_id", "segment_id", "regime", "sample_ms", "seconds_left",
    "risk_deficit", "abs_payoff_gap", "maker_abs_payoff_gap", "worst_case_pnl",
    "payoff_gap", "maker_payoff_gap", "prior_maker_parents", "prior_taker_parents",
    "time_since_last_maker_ms", "time_since_last_taker_ms", "maker_streak_age_ms",
    "maker_parents_since_last_taker", "maker_shares_since_last_taker",
    "maker_notional_since_last_taker", "max_risk_since_taker_reset",
    "max_gap_since_taker_reset", "same_payoff_gap_sign_age_ms",
    "same_maker_gap_sign_age_ms", "risk_growth_1s", "risk_growth_3s", "risk_growth_5s",
    "gap_growth_1s", "gap_growth_3s", "gap_growth_5s",
    "maker_gap_growth_1s", "maker_gap_growth_3s", "maker_gap_growth_5s",
    "next_taker_delay_ms", "next_taker_purpose", "next_taker_side",
    *[f"{kind}_within_{h}s" for h in HORIZONS for kind in ("taker", "repair", "add")],
]
SURFACE_FEATURES = (
    "risk_deficit", "abs_payoff_gap", "maker_abs_payoff_gap",
    "time_since_last_taker_ms", "maker_streak_age_ms",
    "maker_shares_since_last_taker", "maker_notional_since_last_taker",
    "max_risk_since_taker_reset", "max_gap_since_taker_reset",
    "same_payoff_gap_sign_age_ms", "same_maker_gap_sign_age_ms",
    "risk_growth_5s", "gap_growth_5s", "maker_gap_growth_5s",
)


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _sign(value: float) -> int:
    return 1 if value > EPS else -1 if value < -EPS else 0


def _regime(at_ms: int, windows: dict[str, tuple[int, int]]) -> str | None:
    for name, (start, end) in windows.items():
        if start <= at_ms < end:
            return name
    return None


def _purpose(effect: str) -> str:
    return "REPAIR" if effect in CORRECTION else "ADD" if effect == "EXPOSURE_ADD" else "NEUTRAL"


def _select(rows: list[dict[str, Any]], cutoff_ms: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    out, seen = [], set()
    audit = Counter()
    for row in rows:
        if int(row["event_ms"]) < cutoff_ms:
            audit["beforeCutoverExcluded"] += 1
            continue
        wallet = str(row.get("wallet") or "").lower()
        if wallet and wallet != v2.TARGET_WALLET:
            audit["wrongWalletExcluded"] += 1
            continue
        key = (str(row["leg_id"]), int(row["market_id"]))
        if key in seen:
            audit["duplicatesRemoved"] += 1
            continue
        seen.add(key)
        out.append(row)
    out.sort(key=lambda r: (int(r["event_ms"]), int(r["market_id"]), str(r["leg_id"])))
    audit["selectedRows"] = len(out)
    return out, dict(audit)


def _directional_burst_groups(
    parents: list[dict[str, Any]], idle_gap_ms: int, cap_ms: int
) -> list[list[dict[str, Any]]]:
    groups, current = [], []
    onset = prev_end = 0
    side = ""
    for parent in parents:
        if str(parent["role"]).upper() != "TAKER":
            if current:
                groups.append(current)
                current, side = [], ""
            continue
        first, last = int(parent["first_event_ms"]), int(parent["last_event_ms"])
        current_side = str(parent["side"]).upper()
        if (
            current
            and current_side == side
            and first - prev_end <= idle_gap_ms
            and first - onset <= cap_ms
        ):
            current.append(parent)
        else:
            if current:
                groups.append(current)
            current, onset, side = [parent], first, current_side
        prev_end = last
    if current:
        groups.append(current)
    return groups


def _directional_bursts(
    market_id: int,
    segment_id: int,
    parents: list[dict[str, Any]],
    idle_gap_ms: int,
    cap_ms: int,
    windows: dict[str, tuple[int, int]],
) -> list[dict[str, Any]]:
    parents = sorted(
        parents, key=lambda r: (int(r["first_event_ms"]), int(r["last_event_ms"]), str(r["parent_id"]))
    )
    state = core.PortfolioState()
    before: dict[str, Any] = {}
    for parent in parents:
        before[str(parent["parent_id"])] = state.copy()
        core._apply_leg(
            state, parent["role"], parent["side"], parent["quote_type"],
            float(parent["shares"]), float(parent["average_price"])
        )

    out = []
    for i, group in enumerate(_directional_burst_groups(parents, idle_gap_ms, cap_ms), 1):
        first, last = group[0], group[-1]
        regime = _regime(int(first["first_event_ms"]), windows)
        if regime is None:
            continue
        pre_state = before[str(first["parent_id"])].copy()
        post_state = pre_state.copy()
        for parent in group:
            core._apply_leg(
                post_state, parent["role"], parent["side"], parent["quote_type"],
                float(parent["shares"]), float(parent["average_price"])
            )
        pre, post = core._portfolio_metrics(pre_state), core._portfolio_metrics(post_state)
        effect = core._portfolio_effect(pre, post)
        shares = sum(float(p["shares"]) for p in group)
        notional = sum(float(p["notional_usdt"]) for p in group)
        prices = [float(p["average_price"]) for p in group]
        out.append({
            "market_id": market_id, "segment_id": segment_id, "regime": regime,
            "burst_id": f"{market_id}:{segment_id}:D{i}",
            "first_event_ms": int(first["first_event_ms"]),
            "last_event_ms": int(last["last_event_ms"]),
            "duration_ms": int(last["last_event_ms"]) - int(first["first_event_ms"]),
            "parent_count": len(group), "side": str(first["side"]).upper(),
            "bid_parent_count": sum(str(p["quote_type"]).upper() == "BID" for p in group),
            "ask_parent_count": sum(str(p["quote_type"]).upper() == "ASK" for p in group),
            "shares": shares, "notional_usdt": notional,
            "vwap": notional / shares if shares > EPS else 0.0,
            "min_price": min(prices), "max_price": max(prices),
            "pre_risk_deficit": pre["risk_deficit"],
            "pre_abs_payoff_gap": pre["abs_payoff_gap"],
            "pre_maker_abs_payoff_gap": pre["maker_abs_payoff_gap"],
            "post_risk_deficit": post["risk_deficit"],
            "post_abs_payoff_gap": post["abs_payoff_gap"],
            "delta_worst_case_pnl": post["worst_case_pnl"] - pre["worst_case_pnl"],
            "risk_deficit_reduction": pre["risk_deficit"] - post["risk_deficit"],
            "abs_gap_reduction": pre["abs_payoff_gap"] - post["abs_payoff_gap"],
            "portfolio_effect": effect, "purpose": _purpose(effect),
        })
    return out


def _labels(sample_ms: int, bursts: list[dict[str, Any]], starts: list[int]) -> dict[str, Any]:
    pos = bisect.bisect_right(starts, sample_ms)
    nxt = bursts[pos] if pos < len(bursts) else None
    row = {
        "next_taker_delay_ms": "" if nxt is None else int(nxt["first_event_ms"]) - sample_ms,
        "next_taker_purpose": "" if nxt is None else nxt["purpose"],
        "next_taker_side": "" if nxt is None else nxt["side"],
    }
    for seconds in HORIZONS:
        subset = []
        limit = sample_ms + seconds * 1000
        cursor = pos
        while cursor < len(bursts) and int(bursts[cursor]["first_event_ms"]) <= limit:
            subset.append(bursts[cursor])
            cursor += 1
        row[f"taker_within_{seconds}s"] = int(bool(subset))
        row[f"repair_within_{seconds}s"] = int(any(b["purpose"] == "REPAIR" for b in subset))
        row[f"add_within_{seconds}s"] = int(any(b["purpose"] == "ADD" for b in subset))
    return row


def _fixed_grid(
    market_id: int,
    segment_id: int,
    parents: list[dict[str, Any]],
    bursts: list[dict[str, Any]],
    windows: dict[str, tuple[int, int]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    first_ms = min(int(p["first_event_ms"]) for p in parents)
    bucket_start = (first_ms // 300_000) * 300_000
    bucket_end = bucket_start + 300_000
    if any(
        not (bucket_start <= int(p["first_event_ms"]) < bucket_end + 2_000)
        or int(p["last_event_ms"]) >= bucket_end + 2_000
        for p in parents
    ):
        return [], {"invalidBucket": 1}

    completions = sorted(
        parents, key=lambda r: (int(r["last_event_ms"]), int(r["first_event_ms"]), str(r["parent_id"]))
    )
    bursts = sorted(bursts, key=lambda r: (int(r["first_event_ms"]), str(r["burst_id"])))
    starts = [int(r["first_event_ms"]) for r in bursts]
    state, pos = core.PortfolioState(), 0
    last_maker = last_taker = first_maker_after_taker = None
    maker_count = taker_count = maker_since = 0
    maker_shares = maker_notional = max_risk = max_gap = 0.0
    payoff_sign = maker_sign = 0
    payoff_sign_since = maker_sign_since = bucket_start
    history: dict[int, dict[str, float]] = {}
    out, audit = [], Counter()

    for sample_ms in range(bucket_start + 1000, bucket_end, 1000):
        audit["samplesConsidered"] += 1
        while pos < len(completions) and int(completions[pos]["last_event_ms"]) < sample_ms:
            parent = completions[pos]
            core._apply_leg(
                state, parent["role"], parent["side"], parent["quote_type"],
                float(parent["shares"]), float(parent["average_price"])
            )
            met = core._portfolio_metrics(state)
            completed = int(parent["last_event_ms"])
            if str(parent["role"]).upper() == "MAKER":
                maker_count += 1
                last_maker = completed
                if maker_since == 0:
                    first_maker_after_taker = completed
                maker_since += 1
                maker_shares += float(parent["shares"])
                maker_notional += float(parent["notional_usdt"])
            else:
                taker_count += 1
                last_taker = completed
                first_maker_after_taker = None
                maker_since, maker_shares, maker_notional = 0, 0.0, 0.0
                max_risk, max_gap = float(met["risk_deficit"]), float(met["abs_payoff_gap"])
            max_risk = max(max_risk, float(met["risk_deficit"]))
            max_gap = max(max_gap, float(met["abs_payoff_gap"]))
            ps, ms = _sign(float(met["payoff_gap"])), _sign(float(met["maker_payoff_gap"]))
            if ps != payoff_sign:
                payoff_sign, payoff_sign_since = ps, completed
            if ms != maker_sign:
                maker_sign, maker_sign_since = ms, completed
            pos += 1

        met = core._portfolio_metrics(state)
        history[sample_ms] = met
        regime = _regime(sample_ms, windows)
        if regime is None:
            audit["outsideWindows"] += 1
            continue
        if maker_count == 0:
            audit["beforeFirstMaker"] += 1
            continue
        if any(int(p["first_event_ms"]) <= sample_ms <= int(p["last_event_ms"]) for p in parents):
            audit["insideActiveParent"] += 1
            continue
        if any(int(b["first_event_ms"]) <= sample_ms <= int(b["last_event_ms"]) for b in bursts):
            audit["insideExistingBurst"] += 1
            continue

        def growth(feature: str, seconds: int) -> Any:
            prior = history.get(sample_ms - seconds * 1000)
            return "" if prior is None else float(met[feature]) - float(prior[feature])

        row = {
            "market_id": market_id, "segment_id": segment_id, "regime": regime,
            "sample_ms": sample_ms, "seconds_left": (bucket_end - sample_ms) / 1000.0,
            "risk_deficit": met["risk_deficit"], "abs_payoff_gap": met["abs_payoff_gap"],
            "maker_abs_payoff_gap": met["maker_abs_payoff_gap"], "worst_case_pnl": met["worst_case_pnl"],
            "payoff_gap": met["payoff_gap"], "maker_payoff_gap": met["maker_payoff_gap"],
            "prior_maker_parents": maker_count, "prior_taker_parents": taker_count,
            "time_since_last_maker_ms": "" if last_maker is None else sample_ms - last_maker,
            "time_since_last_taker_ms": "" if last_taker is None else sample_ms - last_taker,
            "maker_streak_age_ms": "" if first_maker_after_taker is None else sample_ms - first_maker_after_taker,
            "maker_parents_since_last_taker": maker_since,
            "maker_shares_since_last_taker": maker_shares,
            "maker_notional_since_last_taker": maker_notional,
            "max_risk_since_taker_reset": max(max_risk, float(met["risk_deficit"])),
            "max_gap_since_taker_reset": max(max_gap, float(met["abs_payoff_gap"])),
            "same_payoff_gap_sign_age_ms": max(0, sample_ms - payoff_sign_since),
            "same_maker_gap_sign_age_ms": max(0, sample_ms - maker_sign_since),
        }
        for seconds in (1, 3, 5):
            row[f"risk_growth_{seconds}s"] = growth("risk_deficit", seconds)
            row[f"gap_growth_{seconds}s"] = growth("abs_payoff_gap", seconds)
            row[f"maker_gap_growth_{seconds}s"] = growth("maker_abs_payoff_gap", seconds)
        row.update(_labels(sample_ms, bursts, starts))
        out.append(row)
        audit["emitted"] += 1
    return out, dict(audit)


def _rate(rows: list[dict[str, Any]], key: str) -> float | None:
    return None if not rows else sum(int(r.get(key) or 0) for r in rows) / len(rows)


def _surface(rows: list[dict[str, Any]], feature: str) -> list[dict[str, Any]]:
    ordered = sorted(
        [(value, row) for row in rows for value in [_num(row.get(feature))] if value is not None],
        key=lambda pair: pair[0],
    )
    out = []
    for i in range(5):
        lo, hi = len(ordered) * i // 5, len(ordered) * (i + 1) // 5
        pairs = ordered[lo:hi]
        if not pairs:
            continue
        chunk = [row for _, row in pairs]
        out.append({
            "bin": i + 1, "rows": len(chunk), "min": pairs[0][0], "max": pairs[-1][0],
            "median": statistics.median(value for value, _ in pairs),
            **{
                f"{kind}Within{h}s": _rate(chunk, f"{kind}_within_{h}s")
                for h in (5, 15) for kind in ("taker", "repair", "add")
            },
        })
    return out


def _hazard_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"states": 0}
    return {
        "states": len(rows), "markets": len({int(r["market_id"]) for r in rows}),
        "hazard": {
            f"{h}s": {kind: _rate(rows, f"{kind}_within_{h}s") for kind in ("taker", "repair", "add")}
            for h in HORIZONS
        },
        "distributions": {
            feature: core._distribution(
                value for row in rows for value in [_num(row.get(feature))] if value is not None
            )
            for feature in SURFACE_FEATURES
        },
        "surfaces": {feature: _surface(rows, feature) for feature in SURFACE_FEATURES},
    }


def _directional_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"bursts": 0}
    repair = [r for r in rows if r["purpose"] == "REPAIR"]
    risk = [float(r["pre_risk_deficit"]) for r in repair]
    gap = [float(r["pre_abs_payoff_gap"]) for r in repair]
    shares = [float(r["shares"]) for r in repair]
    purposes = Counter(r["purpose"] for r in rows)
    return {
        "bursts": len(rows), "markets": len({int(r["market_id"]) for r in rows}),
        "parents": sum(int(r["parent_count"]) for r in rows),
        "parentsPerBurst": sum(int(r["parent_count"]) for r in rows) / len(rows),
        "purposes": dict(purposes), "purposeRates": {k: v / len(rows) for k, v in purposes.items()},
        "repairSizing": {
            "bursts": len(repair),
            "sharesVsPreRiskDeficit": core._ols(risk, shares),
            "sharesVsPreAbsPayoffGap": core._ols(gap, shares),
            "preRiskDeficit": core._distribution(risk),
            "preAbsPayoffGap": core._distribution(gap),
            "shares": core._distribution(shares),
        },
    }


def _execution_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"bursts": 0}
    return {
        "bursts": len(rows), "markets": len({int(r["market_id"]) for r in rows}),
        "parentsPerBurst": sum(int(r["parent_count"]) for r in rows) / len(rows),
        "mixedSideRate": sum(int(r["mixed_sides"]) for r in rows) / len(rows),
    }


def _ratio(a: Any, b: Any) -> float | None:
    x, y = _num(a), _num(b)
    return None if x is None or y is None or y <= EPS else x / y


def main() -> int:
    parser = argparse.ArgumentParser(description="OFFICIAL-only Target controller V2.1 hazard replay")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--asset", default="BTC")
    parser.add_argument("--cutover", default=v2.DEFAULT_CUTOVER)
    parser.add_argument("--gap-minutes", type=float, default=30.0)
    parser.add_argument("--idle-gap-ms", type=int, default=1000)
    parser.add_argument("--burst-cap-ms", type=int, default=3000)
    for key, value in DEFAULTS.items():
        parser.add_argument("--" + key.replace("_", "-"), default=value)
    parser.add_argument("--report", type=Path, default=ROOT / "data/research/target_controller_hazard_v21_report.json")
    parser.add_argument("--directional-bursts", type=Path, default=ROOT / "data/research/target_controller_hazard_v21_directional_bursts.csv")
    parser.add_argument("--execution-bursts", type=Path, default=ROOT / "data/research/target_controller_hazard_v21_execution_bursts.csv")
    parser.add_argument("--states", type=Path, default=ROOT / "data/research/target_controller_hazard_v21_states.csv")
    args = parser.parse_args()

    windows = {
        "STRESS_2026_08_16": (v2._parse_ms(args.stress_start), v2._parse_ms(args.stress_end)),
        "ORDINARY_2026_08_17": (v2._parse_ms(args.ordinary_start), v2._parse_ms(args.ordinary_end)),
    }
    if any(end <= start for start, end in windows.values()):
        raise SystemExit("window end must be after start")

    raw, source_audit = compat._load_source_compat(args.db, "OFFICIAL", str(args.asset).upper())
    events, selection_audit = _select(raw, v2._parse_ms(args.cutover))
    if not events:
        raise SystemExit("no OFFICIAL Target events after cutover")
    mapping, hard_gaps = v2._assign_segments(events, max(1, int(args.gap_minutes * 60_000)))
    parents = v2._build_parents(events)
    events_by_market, parents_by_market = defaultdict(list), defaultdict(list)
    for row in events:
        events_by_market[int(row["market_id"])].append(row)
    for row in parents:
        parents_by_market[int(row["market_id"])].append(row)

    directional, execution, states, invalid = [], [], [], []
    grid_audit = Counter()
    for market_id in sorted(events_by_market):
        market_events = events_by_market[market_id]
        valid, segment_id, source, reason = v2._market_segment(market_events, mapping)
        if not valid or segment_id is None:
            if any(_regime(int(r["event_ms"]), windows) for r in market_events):
                invalid.append({"marketId": market_id, "source": source, "reason": reason})
            continue
        market_parents = parents_by_market[market_id]
        if not any(_regime(int(p["first_event_ms"]), windows) for p in market_parents):
            continue
        drows = _directional_bursts(
            market_id, int(segment_id), market_parents,
            max(0, args.idle_gap_ms), max(0, args.burst_cap_ms), windows
        )
        directional.extend(drows)
        erows, _, _ = v2._replay(
            market_id, market_parents, int(segment_id), "OFFICIAL",
            max(0, args.idle_gap_ms), max(0, args.burst_cap_ms)
        )
        for row in erows:
            regime = _regime(int(row["first_event_ms"]), windows)
            if regime:
                item = dict(row)
                item["regime"] = regime
                execution.append(item)
        srows, audit = _fixed_grid(market_id, int(segment_id), market_parents, drows, windows)
        states.extend(srows)
        grid_audit.update(audit)

    directional.sort(key=lambda r: (int(r["first_event_ms"]), int(r["market_id"]), r["burst_id"]))
    execution.sort(key=lambda r: (int(r["first_event_ms"]), int(r["market_id"]), r["burst_id"]))
    states.sort(key=lambda r: (int(r["sample_ms"]), int(r["market_id"])))
    v2._write_csv(args.directional_bursts, directional, DIRECTIONAL_FIELDS)
    v2._write_csv(args.execution_bursts, execution, EXECUTION_FIELDS)
    v2._write_csv(args.states, states, STATE_FIELDS)

    by_d, by_e, by_s = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in directional:
        by_d[row["regime"]].append(row)
    for row in execution:
        by_e[row["regime"]].append(row)
    for row in states:
        by_s[row["regime"]].append(row)
    regimes = {
        name: {
            "fixedGridHazard": _hazard_summary(by_s[name]),
            "directionalBursts": _directional_summary(by_d[name]),
            "executionBursts": _execution_summary(by_e[name]),
        }
        for name in windows
    }
    stress, ordinary = regimes["STRESS_2026_08_16"], regimes["ORDINARY_2026_08_17"]
    contrast = {
        "hazardRatioStressVsOrdinary": {
            f"{h}s": {
                kind: _ratio(
                    stress["fixedGridHazard"].get("hazard", {}).get(f"{h}s", {}).get(kind),
                    ordinary["fixedGridHazard"].get("hazard", {}).get(f"{h}s", {}).get(kind),
                )
                for kind in ("taker", "repair", "add")
            }
            for h in HORIZONS
        },
        "executionMixedSideRateRatio": _ratio(
            stress["executionBursts"].get("mixedSideRate"),
            ordinary["executionBursts"].get("mixedSideRate"),
        ),
        "directionalRepairSizing": {
            "stress": stress["directionalBursts"].get("repairSizing"),
            "ordinary": ordinary["directionalBursts"].get("repairSizing"),
        },
    }
    report = {
        "reportVersion": REPORT_VERSION, "generatedAt": datetime.now(timezone.utc).isoformat(),
        "paperResearchOnly": True, "noModelFit": True, "noStrategyPromotion": True,
        "sourcePolicy": {
            "collector": "OFFICIAL_ONLY", "db": str(args.db), "cutover": args.cutover,
            "asset": str(args.asset).upper(),
            "strictPastStateRule": "Apply only parents with last_event_ms < sample_ms; exclude samples inside active parents or already-started directional Taker bursts.",
            "fixedGridRule": "One market-second sample after first strict-past Maker; infer 5m bucket from Target event timestamp floor.",
            "directionalBurstRule": f"Maker or side flip breaks; idle <= {args.idle_gap_ms}ms; onset cap <= {args.burst_cap_ms}ms.",
            "purposeRule": "REPAIR=RISK_REDUCING|TAIL_IMPROVING|GAP_REDUCING_EXPENSIVE; ADD=EXPOSURE_ADD.",
        },
        "windows": {
            name: {
                "startTaipei": v2._fmt(start), "endTaipei": v2._fmt(end),
                "minutes": (end - start) / 60_000.0,
            }
            for name, (start, end) in windows.items()
        },
        "sourceAudit": source_audit, "selectionAudit": selection_audit,
        "coverage": {
            "officialFillLegs": len(events), "officialParents": len(parents),
            "officialMarkets": len(events_by_market), "invalidTargetWindowMarkets": invalid,
            "detectedHardGaps": hard_gaps, "directionalBursts": len(directional),
            "executionBursts": len(execution), "fixedGridStates": len(states),
            "gridAudit": dict(grid_audit),
        },
        "regimes": regimes, "stressVsOrdinary": contrast,
        "guardrails": [
            "Same OFFICIAL collector and equal 05:20-11:35 Taipei windows only.",
            "Early Taker seed before first Maker is outside this Maker->Taker hazard sample.",
            "This diagnoses controller structure only; no EBM fit or strategy promotion.",
        ],
        "outputs": {
            "report": str(args.report), "directionalBurstsCsv": str(args.directional_bursts),
            "executionBurstsCsv": str(args.execution_bursts), "statesCsv": str(args.states),
        },
    }
    v2._write_json(args.report, report)
    print(REPORT_VERSION)
    print(
        f"legs={len(events):,} parents={len(parents):,} markets={len(events_by_market):,} "
        f"directional={len(directional):,} execution={len(execution):,} states={len(states):,}"
    )
    for name in windows:
        h, d, e = regimes[name]["fixedGridHazard"], regimes[name]["directionalBursts"], regimes[name]["executionBursts"]
        print(f"{name}: states={h.get('states',0):,} directional={d.get('bursts',0):,} execution={e.get('bursts',0):,} mixed={e.get('mixedSideRate')}")
    print(f"report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
