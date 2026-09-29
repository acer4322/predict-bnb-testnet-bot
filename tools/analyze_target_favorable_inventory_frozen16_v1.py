from __future__ import annotations

import bisect
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

import analyze_target_maker_directional_inventory_tolerance_v0 as directional
import analyze_target_maker_taker_inventory_lifecycle_v1 as lifecycle

ROOT = Path(__file__).resolve().parents[1]
RISK_CSV = ROOT / "data" / "research" / "target_maker_taker_repair_hazard_v2_risk.csv"
PUBLIC_CSV = ROOT / "data" / "research" / "target_taker_action_burst_hazard_v1.csv"
PAIRS_CSV = ROOT / "data" / "research" / "target_maker_taker_complete_set_v2_pairs.csv"
MODEL_PATH = ROOT / "data" / "research" / "target_taker_behavior_models_v1" / "side_up.joblib"
REPORT = ROOT / "data" / "research" / "target_favorable_inventory_frozen16_v1_report.json"
HORIZON_MS = 5000
JOIN_MAX_LAG_MS = 2000
EPS = 1e-12

FEATURES = [
    "seconds_left",
    "predict_up_mid",
    "predict_up_spread",
    "predict_down_spread",
    "spot_minus_strike_bps",
    "chainlink_minus_strike_bps",
    "direction_score",
    "spot_queue_imbalance",
    "spot_taker_imbalance_1s",
    "spot_return_1s_bps",
    "spot_return_3s_bps",
    "futures_queue_imbalance",
    "futures_taker_imbalance_1s",
    "futures_return_1s_bps",
    "futures_return_3s_bps",
    "signal_age_ms",
]


def _mean(xs: list[float]) -> float | None:
    return statistics.fmean(xs) if xs else None


def _market_blocked(rows: list[dict[str, Any]], key: str) -> float | None:
    by_market: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        by_market[int(row["market_id"])].append(float(row[key]))
    return _mean([statistics.fmean(v) for v in by_market.values() if v])


def _state(value: float) -> str:
    if value > EPS:
        return "FAVORABLE"
    if value < -EPS:
        return "UNFAVORABLE"
    return "BALANCED"


def _load_public() -> tuple[dict[int, list[dict[str, Any]]], dict[int, list[int]]]:
    rows: dict[int, list[dict[str, Any]]] = defaultdict(list)
    with PUBLIC_CSV.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            try:
                item = {
                    "market_id": int(float(raw["market_id"])),
                    "sampled_ms": int(float(raw["decision_sampled_at_ms"])),
                }
                complete = True
                for name in FEATURES:
                    text = str(raw.get(name, "")).strip()
                    if text == "":
                        complete = False
                        break
                    item[name] = float(text)
                if complete:
                    rows[item["market_id"]].append(item)
            except (TypeError, ValueError):
                continue
    times: dict[int, list[int]] = {}
    for mid, xs in rows.items():
        xs.sort(key=lambda r: int(r["sampled_ms"]))
        times[mid] = [int(r["sampled_ms"]) for r in xs]
    return rows, times


def _join_risk_public() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    public_rows, public_times = _load_public()
    source: list[dict[str, Any]] = []
    candidates = 0
    with RISK_CSV.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            if raw["lifecycle_state"] != "POST_FIRST_TAKER" or raw["phase"] not in {"MID", "TAIL"}:
                continue
            candidates += 1
            mid = int(float(raw["market_id"]))
            sampled_ms = int(float(raw["sampled_ms"]))
            ts = public_times.get(mid, [])
            pos = bisect.bisect_left(ts, sampled_ms) - 1
            if pos < 0:
                continue
            pub = public_rows[mid][pos]
            lag = sampled_ms - int(pub["sampled_ms"])
            if lag > JOIN_MAX_LAG_MS:
                continue
            maker_delta = float(raw["maker_delta"])
            if abs(maker_delta) <= EPS:
                continue
            heavy_side = str(raw["maker_heavy_side"])
            heavy_won_raw = str(raw.get("heavy_side_won", "")).strip()
            winner_up: int | None = None
            if heavy_won_raw:
                heavy_won = int(float(heavy_won_raw))
                winner_up = heavy_won if heavy_side == "UP" else 1 - heavy_won
            item: dict[str, Any] = {
                "market_id": mid,
                "regime": str(raw["regime"]),
                "phase": str(raw["phase"]),
                "sampled_ms": sampled_ms,
                "join_lag_ms": lag,
                "maker_delta": maker_delta,
                "combined_delta": float(raw["combined_delta"]),
                "predict_up_mid": float(raw["predict_up_mid"]),
                "winner_up": winner_up,
                "repair_taker_5s": int(float(raw["repair_taker_5s"])),
                "repair_shares_5s": float(raw["repair_shares_5s"]),
            }
            for name in FEATURES:
                item[name] = float(pub[name])
            source.append(item)

    bundle = joblib.load(MODEL_PATH)
    model_features = [str(v) for v in bundle.get("features") or []]
    if model_features != FEATURES:
        raise RuntimeError(f"frozen16 contract mismatch: {model_features}")
    frame = pd.DataFrame([{name: r[name] for name in FEATURES} for r in source], columns=FEATURES)
    probs = model_bundle_probs = bundle["model"].predict_proba(frame)
    classes = [int(v) for v in bundle["model"].classes_]
    up_idx = classes.index(1)
    for row, prob in zip(source, probs):
        p_up = float(prob[up_idx])
        score16 = 2.0 * p_up - 1.0
        score_predict = 2.0 * float(row["predict_up_mid"]) - 1.0
        row["p_up_frozen16"] = p_up
        row["score16"] = score16
        row["score_predict"] = score_predict
        row["alignment16"] = float(row["combined_delta"]) * score16
        row["alignment_predict"] = float(row["combined_delta"]) * score_predict
        row["state16"] = _state(float(row["alignment16"]))
        row["state_predict"] = _state(float(row["alignment_predict"]))
        row["confidence16"] = max(p_up, 1.0 - p_up)
        if row["winner_up"] is None:
            row["signal16_won"] = None
        else:
            chosen_up = p_up >= 0.5
            row["signal16_won"] = int(chosen_up == bool(row["winner_up"]))

    return source, {
        "candidateRiskRows": candidates,
        "joinedRows": len(source),
        "joinCoverage": len(source) / candidates if candidates else None,
        "joinMaxLagMs": JOIN_MAX_LAG_MS,
        "modelPath": str(MODEL_PATH),
        "modelReportVersion": bundle.get("reportVersion"),
        "features": FEATURES,
    }


def _add_future_flow(rows: list[dict[str, Any]]) -> None:
    actions = directional._load_actions()
    for row in rows:
        mid = int(row["market_id"])
        t = int(row["sampled_ms"])
        market_actions, times = actions.get(mid, ([], []))
        lo = bisect.bisect_right(times, t)
        hi = bisect.bisect_right(times, t + HORIZON_MS)
        future = market_actions[lo:hi]
        taker_net = 0.0
        for action in future:
            taker_net += float(lifecycle._directional_effect(
                str(action["side"]), str(action["quote_type"]), float(action["shares"])
            ))
        row["hold_5s"] = int(not future)
        row["taker_net_5s"] = taker_net
        sign16 = 1.0 if row["score16"] > 0 else -1.0 if row["score16"] < 0 else 0.0
        row["toward_signal16_net_5s"] = taker_net * sign16
        row["alignment16_change_5s"] = taker_net * float(row["score16"])
        sign_predict = 1.0 if row["score_predict"] > 0 else -1.0 if row["score_predict"] < 0 else 0.0
        row["toward_predict_net_5s"] = taker_net * sign_predict
        row["alignment_predict_change_5s"] = taker_net * float(row["score_predict"])


def _state_summary(rows: list[dict[str, Any]], *, prefix: str) -> dict[str, Any]:
    if not rows:
        return {"rows": 0, "markets": 0}
    if prefix == "signal16":
        toward_key = "toward_signal16_net_5s"
        change_key = "alignment16_change_5s"
        alignment_key = "alignment16"
    elif prefix == "predict":
        toward_key = "toward_predict_net_5s"
        change_key = "alignment_predict_change_5s"
        alignment_key = "alignment_predict"
    else:
        raise ValueError(prefix)
    return {
        "rows": len(rows),
        "markets": len({int(r["market_id"]) for r in rows}),
        "marketBlockedHold5s": _market_blocked(rows, "hold_5s"),
        "marketBlockedTowardSignalNet5s": _market_blocked(rows, toward_key),
        "marketBlockedAlignmentChange5s": _market_blocked(rows, change_key),
        "marketBlockedMeanAlignment": _market_blocked(rows, alignment_key),
        "meanCombinedAbsDelta": _mean([abs(float(r["combined_delta"])) for r in rows]),
    }


def _within_market(rows: list[dict[str, Any]], state_key: str) -> dict[str, Any]:
    grouped: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        state = str(row[state_key])
        if state in {"FAVORABLE", "UNFAVORABLE"}:
            grouped[int(row["market_id"])][state].append(float(row["hold_5s"]))
    diffs: list[float] = []
    fav_higher = unfav_higher = ties = 0
    for state_rows in grouped.values():
        if not state_rows["FAVORABLE"] or not state_rows["UNFAVORABLE"]:
            continue
        diff = statistics.fmean(state_rows["FAVORABLE"]) - statistics.fmean(state_rows["UNFAVORABLE"])
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


def _signal_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [r for r in rows if r["signal16_won"] is not None]
    if not valid:
        return {}
    by_market: dict[int, list[float]] = defaultdict(list)
    for r in valid:
        by_market[int(r["market_id"])].append(float(r["signal16_won"]))
    result: dict[str, Any] = {
        "rows": len(valid),
        "markets": len(by_market),
        "rawChosenSideWinRate": _mean([float(r["signal16_won"]) for r in valid]),
        "marketBlockedChosenSideWinRate": _mean([statistics.fmean(v) for v in by_market.values()]),
        "note": "frozen16 output is a class-weighted ranking score, not a calibrated probability",
    }
    buckets = {
        "050_055": (0.50, 0.55),
        "055_060": (0.55, 0.60),
        "060_070": (0.60, 0.70),
        "GE_070": (0.70, 1.01),
    }
    result["byConfidenceDescriptive"] = {}
    for name, (lo, hi) in buckets.items():
        xs = [r for r in valid if lo <= float(r["confidence16"]) < hi]
        result["byConfidenceDescriptive"][name] = {
            "rows": len(xs),
            "markets": len({int(r["market_id"]) for r in xs}),
            "rawWinRate": _mean([float(r["signal16_won"]) for r in xs]),
        }
    return result


def _repair_economics(rows: list[dict[str, Any]], state_key: str, alignment_key: str) -> dict[str, Any]:
    index: dict[int, list[dict[str, Any]]] = defaultdict(list)
    times: dict[int, list[int]] = {}
    for row in rows:
        index[int(row["market_id"])].append(row)
    for mid, xs in index.items():
        xs.sort(key=lambda r: int(r["sampled_ms"]))
        times[mid] = [int(r["sampled_ms"]) for r in xs]

    joined: list[dict[str, Any]] = []
    with PAIRS_CSV.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            if int(float(raw["repair_against_maker_heavy"])) != 1:
                continue
            mid = int(float(raw["market_id"]))
            t = int(float(raw["taker_first_event_ms"]))
            ts = times.get(mid, [])
            pos = bisect.bisect_left(ts, t) - 1
            if pos < 0:
                continue
            row = index[mid][pos]
            lag = t - int(row["sampled_ms"])
            if lag > JOIN_MAX_LAG_MS:
                continue
            joined.append({
                "market_id": mid,
                "regime": str(raw["regime"]),
                "state": str(row[state_key]),
                "alignment": float(row[alignment_key]),
                "shares": float(raw["paired_shares"]),
                "pair_cost": float(raw["pair_cost"]),
                "raw_edge": float(raw["raw_locked_edge_usdt"]),
                "pair_class": str(raw["pair_class_raw"]),
            })

    def summarize(xs: list[dict[str, Any]]) -> dict[str, Any]:
        if not xs:
            return {"fragments": 0, "markets": 0}
        shares = sum(float(r["shares"]) for r in xs)
        cls: dict[str, float] = defaultdict(float)
        for r in xs:
            cls[str(r["pair_class"])] += float(r["shares"])
        return {
            "fragments": len(xs),
            "markets": len({int(r["market_id"]) for r in xs}),
            "pairedShares": shares,
            "shareWeightedPairCost": sum(float(r["pair_cost"]) * float(r["shares"]) for r in xs) / shares,
            "rawEdgeUsdt": sum(float(r["raw_edge"]) for r in xs),
            "pairClassShareRates": {k: v / shares for k, v in cls.items()},
            "shareWeightedAlignment": sum(float(r["alignment"]) * float(r["shares"]) for r in xs) / shares,
        }

    out: dict[str, Any] = {}
    for regime in ("ORDINARY_PRE_SPECIAL", "SPECIAL"):
        out[regime] = {
            state: summarize([r for r in joined if r["regime"] == regime and r["state"] == state])
            for state in ("FAVORABLE", "UNFAVORABLE", "BALANCED")
        }
    out["join"] = {"fragments": len(joined), "markets": len({int(r["market_id"]) for r in joined})}
    return out


def main() -> int:
    print("TARGET_FAVORABLE_INVENTORY_FROZEN16_V1", flush=True)
    rows, coverage = _join_risk_public()
    _add_future_flow(rows)
    report: dict[str, Any] = {
        "reportVersion": "TARGET_FAVORABLE_INVENTORY_FROZEN16_V1",
        "researchOnly": True,
        "liveChanges": False,
        "parameterSweep": False,
        "modelFit": False,
        "purpose": "Use the already-frozen TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY 16-feature EBM only as a directional proxy inside the whole-portfolio controller architecture.",
        "definitions": {
            "frozen16Alignment": "combined_delta * (2 * frozen16_EBM_p_up - 1)",
            "predictControlAlignment": "combined_delta * (2 * public_predict_up_mid - 1)",
            "FAVORABLE": "alignment > 0",
            "UNFAVORABLE": "alignment < 0",
            "primarySlice": "POST_FIRST_TAKER + MID/TAIL",
            "strictPastJoin": "latest complete frozen16 public row strictly before risk snapshot, lag <= 2000ms",
        },
        "coverage": coverage,
        "regimes": {},
        "repairEconomics": {},
        "guardrails": [
            "Frozen16 was trained to imitate Target Taker side, not to predict settlement outcome or reveal Target private directional logic.",
            "Its class-weighted output is used only as a signed direction/confidence score; no probability calibration claim is made.",
            "Special cohort is audit-only; no fitting or threshold selection uses SPECIAL.",
            "Repeated one-second snapshots are serially correlated; market-blocked and within-market contrasts are emphasized.",
        ],
    }
    for regime in ("ORDINARY_PRE_SPECIAL", "SPECIAL"):
        subset = [r for r in rows if r["regime"] == regime]
        payload: dict[str, Any] = {"rows": len(subset), "markets": len({int(r["market_id"]) for r in subset})}
        for label, state_key, prefix in (
            ("FROZEN16", "state16", "signal16"),
            ("PREDICT_CONTROL", "state_predict", "predict"),
        ):
            payload[label] = {
                "byState": {
                    state: _state_summary([r for r in subset if r[state_key] == state], prefix=prefix)
                    for state in ("FAVORABLE", "UNFAVORABLE", "BALANCED")
                },
                "withinMarketHoldContrast": _within_market(subset, state_key),
            }
        payload["frozen16SignalAudit"] = _signal_audit(subset)
        report["regimes"][regime] = payload

    report["repairEconomics"]["FROZEN16"] = _repair_economics(rows, "state16", "alignment16")
    report["repairEconomics"]["PREDICT_CONTROL"] = _repair_economics(rows, "state_predict", "alignment_predict")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    out = {"coverage": coverage, "regimes": {}, "repairEconomics": report["repairEconomics"], "report": str(REPORT)}
    for regime in ("ORDINARY_PRE_SPECIAL", "SPECIAL"):
        out["regimes"][regime] = report["regimes"][regime]
    print(json.dumps(out, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
