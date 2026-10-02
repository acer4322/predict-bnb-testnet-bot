from __future__ import annotations

import bisect
import csv
import json
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TRANSITIONS = ROOT / "data/research/target_controller_complete_history_v2_transitions.csv"
RISK = ROOT / "data/research/target_maker_taker_repair_hazard_v2_risk.csv"
PUBLIC = ROOT / "data/research/target_taker_action_burst_hazard_v1.csv"
BLIND_CSV = ROOT / "data/research/target_architecture_blind_random_v0_decisions.csv"
REVEAL_CSV = ROOT / "data/research/target_architecture_blind_random_v0_reveal.csv"
REPORT = ROOT / "data/research/target_architecture_blind_random_v0_report.json"
VERSION = "TARGET_ARCHITECTURE_BLIND_RANDOM_V0"
SEED = 20260818
SAMPLE_PER_REGIME = 15
ASOF_MAX_LAG_MS = 2000
EPS = 1e-9


def _float(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def _int(v: Any) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def _asof(index: dict[int, list[tuple[int, dict[str, str]]]], mid: int, ts: int) -> tuple[int, dict[str, str]] | None:
    rows = index.get(mid, [])
    if not rows:
        return None
    times = [x[0] for x in rows]
    pos = bisect.bisect_left(times, ts) - 1
    if pos < 0:
        return None
    out = rows[pos]
    if ts - out[0] > ASOF_MAX_LAG_MS:
        return None
    return out


def _simple3(pub: dict[str, str]) -> tuple[int, int, str]:
    pm = _float(pub.get("predict_up_mid"))
    spot = _float(pub.get("spot_minus_strike_bps"))
    chain = _float(pub.get("chainlink_minus_strike_bps"))
    if None in (pm, spot, chain):
        return 0, 0, "BALANCED"
    signs = [
        1 if pm > 0.5 else -1 if pm < 0.5 else 0,
        1 if spot > 0 else -1 if spot < 0 else 0,
        1 if chain > 0 else -1 if chain < 0 else 0,
    ]
    votes = sum(signs)
    direction = 1 if votes > 0 else -1 if votes < 0 else 0
    return direction, abs(votes), "UP" if direction > 0 else "DOWN" if direction < 0 else "BALANCED"


def _risk_level(ratio: float) -> str:
    if ratio >= 0.75:
        return "OVERRIDE"
    if ratio >= 0.50:
        return "WARNING"
    return "NORMAL"


def _target_class(row: dict[str, str], horizon_ms: int) -> str:
    if str(row.get("next_actor") or "") != "TAKER":
        return "HOLD"
    delay = _int(row.get("next_delay_ms"))
    if delay is None or delay > horizon_ms:
        return "HOLD"
    effect = str(row.get("next_effect") or "")
    if effect in {"RISK_REDUCING", "TAIL_IMPROVING", "GAP_REDUCING_EXPENSIVE"}:
        return "RISK_REDUCE"
    if effect == "EXPOSURE_ADD":
        return "ADD"
    return "OTHER_ACTIVE"


def _mean(xs: list[float]) -> float | None:
    return statistics.fmean(xs) if xs else None


def main() -> int:
    transitions = _read(TRANSITIONS)
    risks = _read(RISK)
    publics = _read(PUBLIC)

    risk_index: dict[int, list[tuple[int, dict[str, str]]]] = defaultdict(list)
    regime_by_market: dict[int, str] = {}
    for row in risks:
        mid = _int(row.get("market_id"))
        ts = _int(row.get("sampled_ms"))
        if mid is None or ts is None:
            continue
        risk_index[mid].append((ts, row))
        regime_by_market[mid] = str(row.get("regime") or "")
    for rows in risk_index.values():
        rows.sort(key=lambda x: x[0])

    public_index: dict[int, list[tuple[int, dict[str, str]]]] = defaultdict(list)
    for row in publics:
        mid = _int(row.get("market_id"))
        ts = _int(row.get("decision_sampled_at_ms"))
        if mid is None or ts is None:
            continue
        public_index[mid].append((ts, row))
    for rows in public_index.values():
        rows.sort(key=lambda x: x[0])

    transitions.sort(key=lambda r: (_int(r.get("market_id")) or 0, _int(r.get("maker_completed_ms")) or 0))
    max_gap_so_far: dict[int, float] = defaultdict(float)
    first_eligible: dict[int, dict[str, Any]] = {}

    for row in transitions:
        mid = _int(row.get("market_id"))
        ts = _int(row.get("maker_completed_ms"))
        gap = _float(row.get("abs_payoff_gap"))
        if mid is None or ts is None or gap is None:
            continue
        max_gap_so_far[mid] = max(max_gap_so_far[mid], abs(gap))
        if mid in first_eligible or mid not in regime_by_market:
            continue
        risk_match = _asof(risk_index, mid, ts)
        public_match = _asof(public_index, mid, ts)
        if risk_match is None or public_match is None:
            continue
        _, risk_row = risk_match
        public_ts, pub = public_match
        if str(risk_row.get("lifecycle_state") or "") != "POST_FIRST_TAKER":
            continue
        if str(risk_row.get("phase") or "") not in {"MID", "TAIL"}:
            continue
        if abs(gap) <= EPS:
            continue
        direction, strength, direction_name = _simple3(pub)
        if direction == 0:
            continue
        payoff_gap = _float(row.get("abs_payoff_gap"))
        signed_gap_risk = _float(risk_row.get("combined_delta"))
        # abs_payoff_gap gives size; sign comes from combined_delta in strict-past risk snapshot.
        if payoff_gap is None or signed_gap_risk is None or abs(signed_gap_risk) <= EPS:
            continue
        signed_gap = payoff_gap if signed_gap_risk > 0 else -payoff_gap
        state = "FAVORABLE" if signed_gap * direction > 0 else "UNFAVORABLE"
        risk_deficit = max(0.0, _float(row.get("risk_deficit")) or 0.0)
        exposure_scale = max(max_gap_so_far[mid], EPS)
        risk_ratio = risk_deficit / exposure_scale
        risk_level = _risk_level(risk_ratio)

        repair_side = "DOWN" if signed_gap > 0 else "UP"
        add_side = "UP" if direction > 0 else "DOWN"
        repair_ask = _float(pub.get("predict_down_ask" if repair_side == "DOWN" else "predict_up_ask"))
        add_ask = _float(pub.get("predict_up_ask" if add_side == "UP" else "predict_down_ask"))
        worst = _float(row.get("worst_case_pnl")) or 0.0
        locked_post_floor = None
        locked_positive = False
        if repair_ask is not None and 0 < repair_ask < 1.0:
            locked_post_floor = worst + abs(payoff_gap) * (1.0 - repair_ask)
            locked_positive = locked_post_floor >= -EPS

        action = "HOLD"
        reason = "DEFAULT_HOLD"
        if locked_positive:
            action = "RISK_REDUCE"
            reason = "LOCKED_POSITIVE_COMPLETION"
        elif risk_level == "OVERRIDE":
            action = "RISK_REDUCE"
            reason = "RISK_OVERRIDE"
        elif risk_level == "WARNING" and state == "UNFAVORABLE":
            action = "RISK_REDUCE"
            reason = "WARNING_UNFAVORABLE"
        elif risk_level == "NORMAL" and state == "UNFAVORABLE" and strength == 3:
            action = "RISK_REDUCE"
            reason = "STRONG_UNFAVORABLE"
        elif (
            risk_level == "NORMAL"
            and state == "FAVORABLE"
            and strength == 3
            and risk_ratio < 0.25
            and add_ask is not None
            and 0 < add_ask <= 0.60
        ):
            action = "ADD"
            reason = "STRONG_FAVORABLE_CHEAP_ADD"

        first_eligible[mid] = {
            "market_id": mid,
            "regime": regime_by_market[mid],
            "decision_ms": ts,
            "phase": str(risk_row.get("phase") or ""),
            "public_asof_ms": public_ts,
            "public_lag_ms": ts - public_ts,
            "simple3_direction": direction_name,
            "simple3_strength": strength,
            "portfolio_state": state,
            "payoff_gap": signed_gap,
            "abs_payoff_gap": abs(payoff_gap),
            "worst_case_pnl": worst,
            "risk_deficit": risk_deficit,
            "exposure_scale_max_gap_so_far": exposure_scale,
            "risk_ratio": risk_ratio,
            "risk_level": risk_level,
            "repair_side": repair_side,
            "repair_ask": repair_ask,
            "locked_post_floor": locked_post_floor,
            "locked_positive": int(locked_positive),
            "add_side": add_side,
            "add_ask": add_ask,
            "blind_action": action,
            "blind_reason": reason,
            "maker_parent_id": str(row.get("maker_parent_id") or ""),
        }

    rng = random.Random(SEED)
    sampled: list[dict[str, Any]] = []
    for regime in ("ORDINARY_PRE_SPECIAL", "SPECIAL"):
        candidates = [r for r in first_eligible.values() if r["regime"] == regime]
        candidates.sort(key=lambda r: int(r["market_id"]))
        take = min(SAMPLE_PER_REGIME, len(candidates))
        sampled.extend(rng.sample(candidates, take))
    sampled.sort(key=lambda r: (str(r["regime"]), int(r["market_id"])))

    # Freeze blind choices to disk BEFORE revealing Target future actions.
    _write_csv(BLIND_CSV, sampled)

    transition_by_key = {
        (_int(r.get("market_id")), str(r.get("maker_parent_id") or "")): r
        for r in transitions
    }
    revealed: list[dict[str, Any]] = []
    for blind in sampled:
        key = (int(blind["market_id"]), str(blind["maker_parent_id"]))
        target = transition_by_key[key]
        t5 = _target_class(target, 5000)
        t15 = _target_class(target, 15000)
        row = dict(blind)
        row.update({
            "target_5s": t5,
            "target_15s": t15,
            "target_next_actor": str(target.get("next_actor") or ""),
            "target_next_effect": str(target.get("next_effect") or ""),
            "target_next_delay_ms": target.get("next_delay_ms") or "",
            "match_5s": int(str(blind["blind_action"]) == t5),
            "match_15s": int(str(blind["blind_action"]) == t15),
            "active_match_5s": int((blind["blind_action"] != "HOLD") == (t5 != "HOLD")),
            "active_match_15s": int((blind["blind_action"] != "HOLD") == (t15 != "HOLD")),
        })
        revealed.append(row)
    _write_csv(REVEAL_CSV, revealed)

    def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {"markets": 0}
        confusion5 = Counter((str(r["blind_action"]), str(r["target_5s"])) for r in rows)
        misses = Counter()
        for r in rows:
            if int(r["match_5s"]) == 1:
                continue
            misses[f"{r['blind_action']}->{r['target_5s']}"] += 1
        return {
            "markets": len(rows),
            "exactActionAccuracy5s": _mean([float(r["match_5s"]) for r in rows]),
            "exactActionAccuracy15s": _mean([float(r["match_15s"]) for r in rows]),
            "activeVsHoldAccuracy5s": _mean([float(r["active_match_5s"]) for r in rows]),
            "activeVsHoldAccuracy15s": _mean([float(r["active_match_15s"]) for r in rows]),
            "blindActionCounts": dict(Counter(str(r["blind_action"]) for r in rows)),
            "target5sCounts": dict(Counter(str(r["target_5s"]) for r in rows)),
            "target15sCounts": dict(Counter(str(r["target_15s"]) for r in rows)),
            "missPatterns5s": dict(misses),
            "confusion5s": {f"{a}->{b}": n for (a, b), n in confusion5.items()},
        }

    report = {
        "reportVersion": VERSION,
        "researchOnly": True,
        "liveChanges": False,
        "profitabilityClaim": False,
        "randomSeed": SEED,
        "samplePerRegime": SAMPLE_PER_REGIME,
        "question": "On randomly held-out markets, how close is the current coarse controller architecture to Target's next active intervention decision?",
        "blindBoundary": {
            "oneDecisionPointPerMarket": True,
            "point": "first eligible strict-past POST_FIRST_TAKER MID/TAIL Maker-completion state",
            "targetFutureHiddenUntilBlindCsvWritten": True,
            "publicAsOfMaxLagMs": ASOF_MAX_LAG_MS,
            "note": "This diagnoses controller mapping from an observed historical portfolio state; it is not yet a full counterfactual from market open because prior Target Taker inventory is part of the starting state.",
        },
        "frozenPolicy": {
            "direction": "SIMPLE3 majority: Predict mid, spot-vs-strike sign, Chainlink-vs-strike sign; strength 3=all agree",
            "riskRatio": "risk_deficit / max(abs_payoff_gap seen so far in this market)",
            "riskBands": {"NORMAL": "<0.50", "WARNING": "0.50-<0.75", "OVERRIDE": ">=0.75"},
            "lockedPositive": "full gap-reducing buy at current opposite ask yields post worst-case PnL >= 0",
            "rules": [
                "LOCKED_POSITIVE => RISK_REDUCE",
                "OVERRIDE => RISK_REDUCE",
                "WARNING + UNFAVORABLE => RISK_REDUCE",
                "NORMAL + STRONG UNFAVORABLE => RISK_REDUCE",
                "NORMAL + STRONG FAVORABLE + risk<0.25 + aligned ask<=0.60 => ADD",
                "otherwise HOLD",
            ],
        },
        "eligibleMarkets": dict(Counter(r["regime"] for r in first_eligible.values())),
        "sampledMarkets": dict(Counter(r["regime"] for r in sampled)),
        "overall": summary(revealed),
        "byRegime": {
            regime: summary([r for r in revealed if r["regime"] == regime])
            for regime in ("ORDINARY_PRE_SPECIAL", "SPECIAL")
        },
        "byRiskLevel": {
            level: summary([r for r in revealed if r["risk_level"] == level])
            for level in ("NORMAL", "WARNING", "OVERRIDE")
        },
        "byPortfolioState": {
            state: summary([r for r in revealed if r["portfolio_state"] == state])
            for state in ("FAVORABLE", "UNFAVORABLE")
        },
        "outputs": {
            "blindDecisions": str(BLIND_CSV),
            "reveal": str(REVEAL_CSV),
            "report": str(REPORT),
        },
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
