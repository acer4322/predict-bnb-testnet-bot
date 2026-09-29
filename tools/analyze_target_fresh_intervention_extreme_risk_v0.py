from __future__ import annotations

import argparse
import csv
import json
import math
import random
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORT_VERSION = "TARGET_FRESH_INTERVENTION_EXTREME_RISK_V0"


def _read(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _eligible(row: dict[str, str]) -> bool:
    if not str(row.get("regime") or "").startswith("ORDINARY"):
        return False
    since = _float(row.get("time_since_last_taker_ms"))
    left = _float(row.get("seconds_left"))
    risk = _float(row.get("risk_deficit"))
    return since is not None and since >= 30_000 and left is not None and 10 <= left <= 150 and risk is not None


def _quantile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("empty prior distribution")
    pos = p * (len(ordered) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    w = pos - lo
    return ordered[lo] * (1.0 - w) + ordered[hi] * w


def _threshold(priors: list[str]) -> tuple[float, int, int]:
    values: list[float] = []
    markets: set[tuple[str, int]] = set()
    for prior in priors:
        for row in _read(prior):
            if not _eligible(row):
                continue
            risk = _float(row.get("risk_deficit"))
            if risk is None:
                continue
            values.append(risk)
            markets.add((str(prior), int(float(row["market_id"]))))
    # Conventional extreme-tail definition fixed before looking at fresh labels.
    return _quantile(values, 0.95), len(values), len(markets)


def _blind_rows(states_path: str, threshold: float, seed: int) -> list[dict[str, Any]]:
    source = [row for row in _read(states_path) if _eligible(row)]
    by_market: dict[int, list[dict[str, str]]] = {}
    for row in source:
        by_market.setdefault(int(float(row["market_id"])), []).append(row)
    rng = random.Random(seed)
    output: list[dict[str, Any]] = []
    for market_id in sorted(by_market):
        rows = sorted(by_market[market_id], key=lambda r: int(float(r["sample_ms"])))
        crossings = [r for r in rows if float(r["risk_deficit"]) >= threshold]
        if crossings:
            chosen = crossings[0]  # runtime-realizable first threshold crossing; no future maximum.
            prediction = "REPAIR"
            selection = "FIRST_EXTREME_RISK_CROSSING"
        else:
            chosen = rows[rng.randrange(len(rows))]
            prediction = "PASSIVE"
            selection = "SEEDED_CONTROL_STATE"
        output.append({
            "market_id": market_id,
            "sample_ms": int(float(chosen["sample_ms"])),
            "seconds_left": float(chosen["seconds_left"]),
            "time_since_last_taker_ms": float(chosen["time_since_last_taker_ms"]),
            "maker_parents_since_last_taker": int(float(chosen["maker_parents_since_last_taker"])),
            "risk_deficit": float(chosen["risk_deficit"]),
            "abs_payoff_gap": float(chosen["abs_payoff_gap"]),
            "worst_case_pnl": float(chosen["worst_case_pnl"]),
            "risk_growth_5s": chosen.get("risk_growth_5s", ""),
            "selection": selection,
            "prediction": prediction,
        })
    return output


def _write_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys()) if rows else ["market_id"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prior", action="append", required=True)
    parser.add_argument("--states", required=True)
    parser.add_argument("--mode", choices=("blind", "reveal"), required=True)
    parser.add_argument("--blind", required=True)
    parser.add_argument("--reveal")
    parser.add_argument("--report")
    parser.add_argument("--seed", type=int, default=202608181557)
    args = parser.parse_args()

    threshold, prior_rows, prior_markets = _threshold(args.prior)
    if args.mode == "blind":
        rows = _blind_rows(args.states, threshold, args.seed)
        _write_csv(args.blind, rows)
        print(json.dumps({
            "reportVersion": REPORT_VERSION,
            "priorTailQuantile": 0.95,
            "riskThreshold": threshold,
            "priorRows": prior_rows,
            "priorMarkets": prior_markets,
            "freshMarkets": len(rows),
            "predictedRepair": sum(r["prediction"] == "REPAIR" for r in rows),
            "blind": args.blind,
        }, ensure_ascii=False, indent=2))
        return 0

    blind_path = Path(args.blind)
    if not blind_path.exists():
        raise SystemExit("blind file missing")
    blind = _read(blind_path)
    labels = {(int(float(r["market_id"])), int(float(r["sample_ms"]))): r for r in _read(args.states)}
    revealed: list[dict[str, Any]] = []
    for row in blind:
        key = (int(float(row["market_id"])), int(float(row["sample_ms"])))
        label = labels.get(key)
        if label is None:
            raise SystemExit(f"missing label row {key}")
        repair = int(float(label.get("repair_within_5s") or 0))
        any_taker = int(float(label.get("taker_within_5s") or 0))
        add = int(float(label.get("add_within_5s") or 0))
        target = "REPAIR" if repair else "PASSIVE"
        item = dict(row)
        item.update({
            "target": target,
            "correct": int(str(row["prediction"]) == target),
            "target_taker_within_5s": any_taker,
            "target_repair_within_5s": repair,
            "target_add_within_5s": add,
            "next_taker_delay_ms": label.get("next_taker_delay_ms", ""),
            "next_taker_purpose": label.get("next_taker_purpose", ""),
        })
        revealed.append(item)
    if args.reveal:
        _write_csv(args.reveal, revealed)

    n = len(revealed)
    predicted = sum(r["prediction"] == "REPAIR" for r in revealed)
    actual = sum(r["target"] == "REPAIR" for r in revealed)
    tp = sum(r["prediction"] == "REPAIR" and r["target"] == "REPAIR" for r in revealed)
    fp = sum(r["prediction"] == "REPAIR" and r["target"] != "REPAIR" for r in revealed)
    fn = sum(r["prediction"] != "REPAIR" and r["target"] == "REPAIR" for r in revealed)
    accuracy = sum(int(r["correct"]) for r in revealed) / n if n else None
    baseline = sum(r["target"] == "PASSIVE" for r in revealed) / n if n else None
    report = {
        "reportVersion": REPORT_VERSION,
        "researchOnly": True,
        "liveChanges": False,
        "parameterSweep": False,
        "modelFit": False,
        "priorTailQuantile": 0.95,
        "riskThreshold": threshold,
        "thresholdSource": "95th percentile of eligible strict-past risk_deficit states from prior cohorts; labels not used",
        "eligibility": "time_since_last_taker_ms>=30000 and 10<=seconds_left<=150",
        "selection": "first strict-past crossing of fixed risk threshold per market; otherwise one seeded control state",
        "frozenRule": "risk_deficit >= prior P95 at first crossing => REPAIR within 5s; else PASSIVE",
        "markets": n,
        "accuracy": accuracy,
        "alwaysPassiveAccuracy": baseline,
        "predictedRepair": predicted,
        "targetRepair": actual,
        "truePositive": tp,
        "falsePositive": fp,
        "falseNegative": fn,
        "precision": tp / predicted if predicted else None,
        "recall": tp / actual if actual else None,
        "targetAnyTaker": sum(int(r["target_taker_within_5s"]) for r in revealed),
        "targetAdd": sum(int(r["target_add_within_5s"]) for r in revealed),
    }
    if args.report:
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
