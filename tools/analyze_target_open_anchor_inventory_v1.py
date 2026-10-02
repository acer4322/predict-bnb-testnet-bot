from __future__ import annotations

import bisect
import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

import analyze_target_favorable_inventory_frozen16_v1 as frozen
import analyze_target_maker_directional_inventory_tolerance_v0 as directional
import analyze_target_maker_taker_inventory_lifecycle_v1 as lifecycle

ROOT = Path(__file__).resolve().parents[1]
RISK_CSV = frozen.RISK_CSV
PAIRS_CSV = frozen.PAIRS_CSV
MODEL_PATH = frozen.MODEL_PATH
REPORT = ROOT / "data" / "research" / "target_open_anchor_inventory_v1_report.json"
EPS = 1e-12
HORIZON_MS = 5000
JOIN_MAX_LAG_MS = 2000


def _mean(xs: list[float]) -> float | None:
    return statistics.fmean(xs) if xs else None


def _state(x: float) -> str:
    if x > EPS:
        return "FAVORABLE"
    if x < -EPS:
        return "UNFAVORABLE"
    return "BALANCED"


def _market_blocked(rows: list[dict[str, Any]], key: str) -> float | None:
    grouped: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        grouped[int(row["market_id"])].append(float(row[key]))
    return _mean([statistics.fmean(v) for v in grouped.values() if v])


def _load_anchors(action_index: dict[int, tuple[list[dict[str, Any]], list[int]]]) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    pub_rows, pub_times = frozen._load_public()
    candidates: list[dict[str, Any]] = []
    misses = 0
    for mid, (actions, times) in action_index.items():
        if not actions or not times:
            continue
        first_ms = int(times[0])
        pts = pub_times.get(mid, [])
        pos = bisect.bisect_left(pts, first_ms) - 1
        if pos < 0:
            misses += 1
            continue
        pub = pub_rows[mid][pos]
        lag = first_ms - int(pub["sampled_ms"])
        if lag > JOIN_MAX_LAG_MS:
            misses += 1
            continue
        candidates.append({
            "market_id": mid,
            "first_taker_ms": first_ms,
            "first_taker_side": str(actions[0].get("side") or ""),
            "join_lag_ms": lag,
            **{name: float(pub[name]) for name in frozen.FEATURES},
        })

    bundle = joblib.load(MODEL_PATH)
    if [str(x) for x in bundle.get("features") or []] != frozen.FEATURES:
        raise RuntimeError("frozen16 feature contract mismatch")
    frame = pd.DataFrame([{name: r[name] for name in frozen.FEATURES} for r in candidates], columns=frozen.FEATURES)
    probs = bundle["model"].predict_proba(frame)
    classes = [int(v) for v in bundle["model"].classes_]
    up_idx = classes.index(1)
    anchors: dict[int, dict[str, Any]] = {}
    phase_counts: Counter[str] = Counter()
    for row, prob in zip(candidates, probs):
        p_up = float(prob[up_idx])
        score16 = 2.0 * p_up - 1.0
        p_predict = float(row["predict_up_mid"])
        score_predict = 2.0 * p_predict - 1.0
        seconds_left = float(row["seconds_left"])
        phase = "OPEN" if seconds_left > 180 else "MID" if seconds_left > 60 else "TAIL"
        phase_counts[phase] += 1
        anchors[int(row["market_id"])] = {
            **row,
            "p_up_frozen16": p_up,
            "score16": score16,
            "confidence16": max(p_up, 1.0 - p_up),
            "score_predict": score_predict,
            "confidence_predict": max(p_predict, 1.0 - p_predict),
            "anchor_phase": phase,
        }
    return anchors, {
        "anchorMarkets": len(anchors),
        "missingOrStaleMarkets": misses,
        "joinMaxLagMs": JOIN_MAX_LAG_MS,
        "anchorPhaseCounts": dict(phase_counts),
        "modelPath": str(MODEL_PATH),
    }


def _load_rows(action_index: dict[int, tuple[list[dict[str, Any]], list[int]]], anchors: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with RISK_CSV.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            if raw["lifecycle_state"] != "POST_FIRST_TAKER" or raw["phase"] not in {"MID", "TAIL"}:
                continue
            mid = int(float(raw["market_id"]))
            anchor = anchors.get(mid)
            if anchor is None:
                continue
            t = int(float(raw["sampled_ms"]))
            combined = float(raw["combined_delta"])
            actions, times = action_index.get(mid, ([], []))
            lo = bisect.bisect_right(times, t)
            hi = bisect.bisect_right(times, t + HORIZON_MS)
            future = actions[lo:hi]
            taker_net = 0.0
            for action in future:
                taker_net += float(lifecycle._directional_effect(str(action["side"]), str(action["quote_type"]), float(action["shares"])))
            maker_side = str(raw["maker_heavy_side"])
            heavy_won_text = str(raw.get("heavy_side_won", "")).strip()
            winner_up: int | None = None
            if heavy_won_text:
                hw = int(float(heavy_won_text))
                winner_up = hw if maker_side == "UP" else 1 - hw
            a16 = combined * float(anchor["score16"])
            ap = combined * float(anchor["score_predict"])
            sign16 = 1.0 if anchor["score16"] > 0 else -1.0 if anchor["score16"] < 0 else 0.0
            signp = 1.0 if anchor["score_predict"] > 0 else -1.0 if anchor["score_predict"] < 0 else 0.0
            rows.append({
                "market_id": mid,
                "regime": str(raw["regime"]),
                "phase": str(raw["phase"]),
                "sampled_ms": t,
                "combined_delta": combined,
                "hold_5s": int(not future),
                "taker_net_5s": taker_net,
                "alignment16": a16,
                "state16": _state(a16),
                "toward_anchor16_net_5s": taker_net * sign16,
                "alignment16_change_5s": taker_net * float(anchor["score16"]),
                "alignment_predict_anchor": ap,
                "state_predict_anchor": _state(ap),
                "toward_predict_anchor_net_5s": taker_net * signp,
                "alignment_predict_anchor_change_5s": taker_net * float(anchor["score_predict"]),
                "anchor_score16": float(anchor["score16"]),
                "anchor_confidence16": float(anchor["confidence16"]),
                "anchor_score_predict": float(anchor["score_predict"]),
                "anchor_confidence_predict": float(anchor["confidence_predict"]),
                "anchor_phase": str(anchor["anchor_phase"]),
                "first_taker_side": str(anchor["first_taker_side"]),
                "winner_up": winner_up,
            })
    return rows


def _summary(rows: list[dict[str, Any]], *, kind: str) -> dict[str, Any]:
    if not rows:
        return {"rows": 0, "markets": 0}
    if kind == "frozen16":
        toward = "toward_anchor16_net_5s"; change = "alignment16_change_5s"; align = "alignment16"
    else:
        toward = "toward_predict_anchor_net_5s"; change = "alignment_predict_anchor_change_5s"; align = "alignment_predict_anchor"
    return {
        "rows": len(rows),
        "markets": len({int(r["market_id"]) for r in rows}),
        "marketBlockedHold5s": _market_blocked(rows, "hold_5s"),
        "marketBlockedTowardAnchorNet5s": _market_blocked(rows, toward),
        "marketBlockedAlignmentChange5s": _market_blocked(rows, change),
        "marketBlockedMeanAlignment": _market_blocked(rows, align),
        "meanAbsCombinedDelta": _mean([abs(float(r["combined_delta"])) for r in rows]),
    }


def _within(rows: list[dict[str, Any]], state_key: str) -> dict[str, Any]:
    grouped: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        state = str(row[state_key])
        if state in {"FAVORABLE", "UNFAVORABLE"}:
            grouped[int(row["market_id"])][state].append(float(row["hold_5s"]))
    diffs: list[float] = []
    counts = Counter()
    for states in grouped.values():
        if not states["FAVORABLE"] or not states["UNFAVORABLE"]:
            continue
        d = statistics.fmean(states["FAVORABLE"]) - statistics.fmean(states["UNFAVORABLE"])
        diffs.append(d)
        counts["FAV_HIGHER" if d > EPS else "UNFAV_HIGHER" if d < -EPS else "TIE"] += 1
    return {
        "pairedMarkets": len(diffs),
        "meanHoldDifferenceFavorableMinusUnfavorable": _mean(diffs),
        "median": statistics.median(diffs) if diffs else None,
        "votes": dict(counts),
    }


def _anchor_accuracy(rows: list[dict[str, Any]], score_key: str) -> dict[str, Any]:
    seen: dict[int, tuple[float, int]] = {}
    for row in rows:
        if row["winner_up"] is None:
            continue
        seen.setdefault(int(row["market_id"]), (float(row[score_key]), int(row["winner_up"])))
    wins = [int((score >= 0) == bool(winner)) for score, winner in seen.values()]
    return {"markets": len(wins), "chosenSideSettlementWinRate": _mean([float(x) for x in wins])}


def _repair_economics(rows: list[dict[str, Any]], *, state_key: str, alignment_key: str) -> dict[str, Any]:
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
            state_row = index[mid][pos]
            if t - int(state_row["sampled_ms"]) > JOIN_MAX_LAG_MS:
                continue
            joined.append({
                "market_id": mid,
                "regime": str(raw["regime"]),
                "state": str(state_row[state_key]),
                "alignment": float(state_row[alignment_key]),
                "shares": float(raw["paired_shares"]),
                "pair_cost": float(raw["pair_cost"]),
                "raw_edge": float(raw["raw_locked_edge_usdt"]),
                "pair_class": str(raw["pair_class_raw"]),
            })
    def summarize(xs: list[dict[str, Any]]) -> dict[str, Any]:
        if not xs: return {"fragments": 0, "markets": 0}
        shares = sum(r["shares"] for r in xs)
        cls: dict[str, float] = defaultdict(float)
        for r in xs: cls[r["pair_class"]] += r["shares"]
        return {
            "fragments": len(xs), "markets": len({r["market_id"] for r in xs}), "pairedShares": shares,
            "shareWeightedPairCost": sum(r["pair_cost"]*r["shares"] for r in xs)/shares,
            "rawEdgeUsdt": sum(r["raw_edge"] for r in xs),
            "pairClassShareRates": {k:v/shares for k,v in cls.items()},
            "shareWeightedAlignment": sum(r["alignment"]*r["shares"] for r in xs)/shares,
        }
    out: dict[str, Any] = {}
    for regime in ("ORDINARY_PRE_SPECIAL","SPECIAL"):
        out[regime] = {s:summarize([r for r in joined if r["regime"]==regime and r["state"]==s]) for s in ("FAVORABLE","UNFAVORABLE","BALANCED")}
    out["join"]={"fragments":len(joined),"markets":len({r["market_id"] for r in joined})}
    return out


def main() -> int:
    print("TARGET_OPEN_ANCHOR_INVENTORY_V1", flush=True)
    action_index = directional._load_actions()
    anchors, anchor_cov = _load_anchors(action_index)
    rows = _load_rows(action_index, anchors)
    report: dict[str, Any] = {
        "reportVersion":"TARGET_OPEN_ANCHOR_INVENTORY_V1",
        "researchOnly":True,
        "liveChanges":False,
        "parameterSweep":False,
        "modelFit":False,
        "purpose":"Test whether the frozen16 side model works better as a once-per-market first-Taker directional anchor than as a continuously rescored MID/TAIL direction signal.",
        "definitions":{
            "frozen16Anchor":"latest complete strict-past frozen16 state before first observed Target Taker parent; score frozen once and carry forward",
            "alignment16":"combined_delta * frozen first-Taker score (2*p_up-1)",
            "predictAnchorControl":"same first-Taker timestamp but freeze raw Predict up-mid direction",
            "FAVORABLE":"alignment > 0",
            "UNFAVORABLE":"alignment < 0",
            "primarySlice":"POST_FIRST_TAKER + MID/TAIL"
        },
        "coverage":{**anchor_cov,"riskRows":len(rows),"riskMarkets":len({r['market_id'] for r in rows})},
        "regimes":{},
        "repairEconomics":{},
        "guardrails":[
            "Frozen16 was trained to imitate Target Taker side; this test treats its first-Taker score as a proxy anchor, not proof of Target private belief.",
            "No special data are fit or used to choose thresholds.",
            "The anchor is frozen after first Taker; no MID/TAIL rescoring is allowed in this test.",
        ]
    }
    for regime in ("ORDINARY_PRE_SPECIAL","SPECIAL"):
        xs=[r for r in rows if r["regime"]==regime]
        report["regimes"][regime]={
            "rows":len(xs),"markets":len({r['market_id'] for r in xs}),
            "FROZEN16_ANCHOR":{
                "byState":{s:_summary([r for r in xs if r["state16"]==s],kind="frozen16") for s in ("FAVORABLE","UNFAVORABLE","BALANCED")},
                "withinMarketHoldContrast":_within(xs,"state16"),
                "anchorSettlementAudit":_anchor_accuracy(xs,"anchor_score16"),
            },
            "PREDICT_OPEN_ANCHOR_CONTROL":{
                "byState":{s:_summary([r for r in xs if r["state_predict_anchor"]==s],kind="predict") for s in ("FAVORABLE","UNFAVORABLE","BALANCED")},
                "withinMarketHoldContrast":_within(xs,"state_predict_anchor"),
                "anchorSettlementAudit":_anchor_accuracy(xs,"anchor_score_predict"),
            },
        }
    report["repairEconomics"]["FROZEN16_ANCHOR"]=_repair_economics(rows,state_key="state16",alignment_key="alignment16")
    report["repairEconomics"]["PREDICT_OPEN_ANCHOR_CONTROL"]=_repair_economics(rows,state_key="state_predict_anchor",alignment_key="alignment_predict_anchor")
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"coverage":report["coverage"],"regimes":report["regimes"],"repairEconomics":report["repairEconomics"],"report":str(REPORT)},ensure_ascii=False,indent=2),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
