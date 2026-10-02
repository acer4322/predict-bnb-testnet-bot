from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import audit_target_maker_8778_predict_book_coverage_v2_5b as bookmod

REPORT_VERSION = "TARGET_EXECUTION_COST_SPREAD_GATE_V0"
REGIME = "ORDINARY_2026_08_17"
SEED = 202608181758
BOOK_DB = ROOT / "data" / "wallet_maker_book_inference.db"
PRIOR_FILES = [
    ROOT / "data/research/target_controller_hazard_v21_20260818_1136_1445_states.csv",
    ROOT / "data/research/target_controller_hazard_v21_20260818_1446_1555_states.csv",
    ROOT / "data/research/target_controller_hazard_v21_20260818_1556_1655_states.csv",
]
FRESH_FILE = ROOT / "data/research/target_controller_hazard_v21_20260818_1656_1755_states.csv"
BLIND = ROOT / "data/research/target_execution_cost_spread_gate_v0_blind.csv"
REVEAL = ROOT / "data/research/target_execution_cost_spread_gate_v0_reveal.csv"
REPORT = ROOT / "data/research/target_execution_cost_spread_gate_v0_report.json"
LOG = ROOT / "data/research/target_execution_arbitration_research_log_v10.json"

SAFE_FIELDS = (
    "market_id", "regime", "sample_ms", "seconds_left", "time_since_last_taker_ms",
    "risk_deficit", "abs_payoff_gap", "worst_case_pnl", "payoff_gap",
    "maker_parents_since_last_taker", "max_gap_since_taker_reset",
)


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def _i(v: Any) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            row = {k: raw.get(k, "") for k in SAFE_FIELDS}
            if row.get("regime") != REGIME:
                continue
            mid, ms = _i(row.get("market_id")), _i(row.get("sample_ms"))
            sl, last = _f(row.get("seconds_left")), _f(row.get("time_since_last_taker_ms"))
            if mid is None or ms is None or sl is None or last is None:
                continue
            if last < 30000 or not (10 <= sl <= 150):
                continue
            row["market_id"] = mid
            row["sample_ms"] = ms
            row["seconds_left"] = sl
            row["time_since_last_taker_ms"] = last
            rows.append(row)
    rows.sort(key=lambda r: (int(r["market_id"]), int(r["sample_ms"])))
    return rows


def _book_features(updates: list[bookmod.BookUpdate], sample_ms: int) -> dict[str, float] | None:
    res = bookmod.replay_strict_pre(updates, int(sample_ms), "receivedStrict")
    if not res.reconstructable or res.best_bid is None or res.best_ask is None:
        return None
    bid, ask = _f(res.best_bid), _f(res.best_ask)
    if bid is None or ask is None or not (0 <= bid < ask <= 1):
        return None
    age = res.latest_age_ms
    if age is None or age < 0 or age > 2000:
        return None
    mid = (bid + ask) / 2.0
    return {
        "up_bid": bid,
        "up_ask": ask,
        "up_mid": mid,
        "spread": ask - bid,
        "taker_premium": (ask - bid) / 2.0,
        "book_age_ms": float(age),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        if fields:
            writer.writeheader()
            writer.writerows(rows)


def _quantile(values: list[float], q: float) -> float:
    xs = sorted(values)
    if not xs:
        raise RuntimeError("no values for quantile")
    if len(xs) == 1:
        return xs[0]
    pos = q * (len(xs) - 1)
    lo = int(math.floor(pos)); hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    w = pos - lo
    return xs[lo] * (1 - w) + xs[hi] * w


def _prior_threshold() -> tuple[float, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in PRIOR_FILES:
        rows.extend(_safe_rows(path))
    # 5-second unlabeled grid keeps reconstruction bounded while preserving time weighting.
    sampled = [r for r in rows if int(r["sample_ms"]) % 5000 == 0]
    mids = sorted({int(r["market_id"]) for r in sampled})
    conn = bookmod.connect(str(BOOK_DB))
    try:
        updates = bookmod.load_updates(conn, mids)
    finally:
        conn.close()
    premiums: list[float] = []
    valid = 0
    for row in sampled:
        feat = _book_features(updates.get(int(row["market_id"]), []), int(row["sample_ms"]))
        if feat is None:
            continue
        valid += 1
        premiums.append(float(feat["taker_premium"]))
    threshold = _quantile(premiums, 0.25)
    return threshold, {
        "eligibleRows": len(rows), "fiveSecondGridRows": len(sampled), "validBookRows": valid,
        "markets": len(mids), "premiumP25": threshold,
        "premiumMedian": statistics.median(premiums) if premiums else None,
        "premiumP75": _quantile(premiums, 0.75) if premiums else None,
    }


def blind() -> None:
    threshold, audit = _prior_threshold()
    rows = _safe_rows(FRESH_FILE)
    by: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        by.setdefault(int(row["market_id"]), []).append(row)
    mids = sorted(by)
    conn = bookmod.connect(str(BOOK_DB))
    try:
        updates = bookmod.load_updates(conn, mids)
    finally:
        conn.close()
    rng = random.Random(SEED)
    out: list[dict[str, Any]] = []
    for mid in mids:
        valid: list[tuple[dict[str, Any], dict[str, float]]] = []
        crossing: tuple[dict[str, Any], dict[str, float]] | None = None
        for row in by[mid]:
            feat = _book_features(updates.get(mid, []), int(row["sample_ms"]))
            if feat is None:
                continue
            valid.append((row, feat))
            if crossing is None and float(feat["taker_premium"]) <= threshold + 1e-12:
                crossing = (row, feat)
        if crossing is not None:
            row, feat = crossing
            pred = "REPAIR"
            reason = "CHEAP_IMMEDIACY_PREMIUM_CROSS"
        elif valid:
            row, feat = rng.choice(valid)
            pred = "PASSIVE"
            reason = "NO_CHEAP_PREMIUM_CROSS"
        else:
            continue
        gap = _f(row.get("payoff_gap")) or 0.0
        repair_side = "DOWN" if gap > 0 else "UP" if gap < 0 else "NONE"
        repair_ask = (1.0 - float(feat["up_bid"])) if repair_side == "DOWN" else float(feat["up_ask"]) if repair_side == "UP" else None
        out.append({
            "market_id": mid,
            "sample_ms": int(row["sample_ms"]),
            "seconds_left": float(row["seconds_left"]),
            "time_since_last_taker_ms": float(row["time_since_last_taker_ms"]),
            "risk_deficit": _f(row.get("risk_deficit")),
            "abs_payoff_gap": _f(row.get("abs_payoff_gap")),
            "payoff_gap": gap,
            "repair_side": repair_side,
            "repair_side_ask": repair_ask,
            "up_bid": feat["up_bid"], "up_ask": feat["up_ask"], "up_mid": feat["up_mid"],
            "spread": feat["spread"], "taker_premium": feat["taker_premium"], "book_age_ms": feat["book_age_ms"],
            "frozen_premium_p25": threshold,
            "blind_prediction": pred,
            "blind_reason": reason,
        })
    _write_csv(BLIND, out)
    sidecar = {
        "reportVersion": REPORT_VERSION + "_BLIND_BOUNDARY",
        "researchOnly": True, "futureLabelsRead": False, "randomSeed": SEED,
        "priorUnlabeledAudit": audit,
        "frozenRule": "fresh-intervention eligible (last Taker >=30s, 10<=seconds_left<=150) AND strict-past taker immediacy premium=(UP ask-UP bid)/2 <= prior-unlabeled P25 => REPAIR; otherwise PASSIVE",
        "freshMarkets": len(out), "predictions": dict(Counter(r["blind_prediction"] for r in out)),
        "blindFile": str(BLIND.relative_to(ROOT)),
    }
    (ROOT / "data/research/target_execution_cost_spread_gate_v0_blind_boundary.json").write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(sidecar, ensure_ascii=False, indent=2))


def reveal() -> None:
    if not BLIND.exists():
        raise SystemExit("blind file missing")
    blind_rows = list(csv.DictReader(BLIND.open(encoding="utf-8", newline="")))
    # Future labels are first read here, after blind artifacts are durable.
    lookup: dict[tuple[int, int], dict[str, str]] = {}
    with FRESH_FILE.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            mid, ms = _i(raw.get("market_id")), _i(raw.get("sample_ms"))
            if mid is not None and ms is not None:
                lookup[(mid, ms)] = raw
    out: list[dict[str, Any]] = []
    for b in blind_rows:
        mid, ms = int(float(b["market_id"])), int(float(b["sample_ms"]))
        s = lookup[(mid, ms)]
        actual_repair = int(float(s.get("repair_within_5s") or 0)) == 1
        actual_any = int(float(s.get("taker_within_5s") or 0)) == 1
        actual = "REPAIR" if actual_repair else "PASSIVE"
        row = dict(b)
        row.update({
            "target_repair_5s": actual,
            "target_any_taker_5s": int(actual_any),
            "target_purpose": s.get("next_taker_purpose", ""),
            "match": int(str(b["blind_prediction"]) == actual),
        })
        out.append(row)
    _write_csv(REVEAL, out)
    n = len(out)
    hits = sum(int(r["match"]) for r in out)
    pred = sum(r["blind_prediction"] == "REPAIR" for r in out)
    actual = sum(r["target_repair_5s"] == "REPAIR" for r in out)
    tp = sum(r["blind_prediction"] == "REPAIR" and r["target_repair_5s"] == "REPAIR" for r in out)
    baseline = sum(r["target_repair_5s"] == "PASSIVE" for r in out) / n if n else None
    precision = tp / pred if pred else None
    recall = tp / actual if actual else None
    target_premiums = [float(r["taker_premium"]) for r in out if r["target_repair_5s"] == "REPAIR"]
    passive_premiums = [float(r["taker_premium"]) for r in out if r["target_repair_5s"] == "PASSIVE"]
    acc = hits / n if n else None
    verdict = "KEEP" if n and acc is not None and baseline is not None and acc > baseline and tp > 0 and precision is not None and precision >= 0.5 else "OBSERVE" if tp > 0 else "REJECT"
    report = {
        "reportVersion": REPORT_VERSION,
        "researchOnly": True, "liveChanges": False, "parameterSweep": False, "modelFit": False,
        "topic": "Execution-cost / liquidity arbitration for fresh REPAIR",
        "hypothesis": "After >=30s without Taker, Target pays Taker for a desired repair when the strict-past executable book is unusually cheap in immediacy cost; half-spread is used as the minimal Taker premium proxy.",
        "method": {
            "priorFiles": [str(p.relative_to(ROOT)) for p in PRIOR_FILES],
            "freshFile": str(FRESH_FILE.relative_to(ROOT)),
            "bookDb": str(BOOK_DB.relative_to(ROOT)),
            "strictPastBookMode": "receivedStrict",
            "bookFreshnessMaxMs": 2000,
            "thresholdSource": "prior unlabeled 5-second-grid premium distribution",
            "thresholdQuantile": "P25",
            "blindWrittenBeforeReveal": True,
            "oneDecisionPerMarket": True,
        },
        "result": {
            "markets": n, "accuracy": acc, "alwaysPassiveAccuracy": baseline,
            "predictedRepair": pred, "targetRepair": actual, "truePositive": tp,
            "precision": precision, "recall": recall,
            "targetRepairPremiumMedian": statistics.median(target_premiums) if target_premiums else None,
            "passivePremiumMedian": statistics.median(passive_premiums) if passive_premiums else None,
        },
        "verdict": verdict,
        "interpretation": "A tight spread is retained only if it beats the strong passive baseline on fresh blind markets. Failure is treated as evidence that cheap immediacy cost alone is not sufficient, not as a reason to tune the P25 threshold.",
        "outputs": {"blind": str(BLIND.relative_to(ROOT)), "reveal": str(REVEAL.relative_to(ROOT)), "report": str(REPORT.relative_to(ROOT))},
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log = {
        "reportVersion": "TARGET_EXECUTION_ARBITRATION_RESEARCH_LOG_V10",
        "researchOnly": True, "liveChanges": False,
        "topic": "Execution-cost / liquidity opportunity as Taker escalation arbitration",
        "literatureReview": [
            "LOCKED_POSITIVE=>immediate Taker was previously rejected because Target usually waited/Maker-completed despite favorable completion economics.",
            "Deadline, gap re-expansion, long Maker runs, and deadline+recovery-failure gates have all failed as sufficient fresh Taker triggers.",
            "The remaining architectural distinction is desired portfolio adjustment versus whether paying spread for immediate Taker execution is worthwhile."
        ],
        "hypothesis": report["hypothesis"],
        "method": report["method"],
        "result": report["result"],
        "blindScore": {"markets": n, "accuracy": acc, "alwaysPassiveAccuracy": baseline},
        "KEEP": ["desired adjustment and execution method remain separate", "strong PASSIVE prior for fresh intervention"] + (["tight-spread Taker premium as an arbitration component"] if verdict == "KEEP" else []),
        "OBSERVE": ["tight-spread Taker premium may still be a component rather than a direct trigger"] if verdict != "REJECT" else [],
        "REJECT": ["cheap strict-past half-spread alone as a fresh REPAIR trigger"] if verdict == "REJECT" else [],
        "nextQuestion": "If cheap spread alone is rejected, test a two-stage arbitration interaction rather than another standalone threshold: desired-repair urgency state first, then ask whether tight spread selects TAKER versus MAKER/WAIT. Continue the frozen 242.385 USDT extreme-risk watch unchanged when fresh crossings occur.",
    }
    LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main() -> None:
    p = argparse.ArgumentParser(); p.add_argument("--phase", choices=["blind", "reveal"], required=True)
    args = p.parse_args()
    blind() if args.phase == "blind" else reveal()


if __name__ == "__main__":
    main()
