from __future__ import annotations

import importlib.util
import json
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from interpret.glassbox import ExplainableBoostingRegressor

ROOT = Path(__file__).resolve().parents[1]
SENS = ROOT / "tools" / "backtest_mature_mm_parameter_sensitivity_v1.py"
spec = importlib.util.spec_from_file_location("maker_knob_sens_v1", SENS)
sens = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = sens
spec.loader.exec_module(sens)
base = sens.base
dirv0 = sens.dirv0

VERSION = "MAKER_KNOB_CONTROLLER_COUNTERFACTUAL_EBM_V0"
REPORT = ROOT / "data" / "research" / "maker_knob_controller_counterfactual_ebm_v0_report.json"
DATASET = ROOT / "data" / "research" / "maker_knob_controller_counterfactual_ebm_v0_states.csv"
MODEL_DIR = ROOT / "data" / "research" / "maker_knob_controller_ebm_v0"
HORIZON_MS = 10_000
MIN_HORIZON_MS = 8_000
MAX_STATES_PER_MARKET = 8
MIN_STATE_GAP_MS = 12_000
MIN_SECONDS_LEFT = 75.0
MAX_SECONDS_LEFT = 295.0

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
    "simple3_up",
    "maker_net",
    "maker_abs_net",
    "maker_gross",
    "maker_imbalance_ratio",
    "maker_paired_coverage",
    "dominant_up",
    "dominant_aligned_simple3",
    "open_order_count",
    "open_up",
    "open_down",
    "filled_up_this_state",
    "filled_down_this_state",
]


@dataclass(frozen=True)
class KnobParams:
    offset_ticks: int = 1
    filled_order_delay_ms: int = 0


class KnobSim(sens.ParamSim):
    """Stable-directional reference plus controllable depth/refill-delay knobs."""

    def __init__(self, models: dict[str, dict[str, Any]], knobs: KnobParams) -> None:
        super().__init__(models, sens.Params("KNOB", knobs.offset_ticks, 18.0, None))
        self.knobs = knobs
        self.last_fill_ms_by_side: dict[str, int] = {}
        self.current_seconds_left: float | None = None
        self.delay_suppressed_plans = 0

    def decide(self, snapshot: dict[str, Any], market_id: int, now_ms: int) -> dict[str, Any]:
        self.current_seconds_left = base.snapshot_value(snapshot, "seconds_left", "secondsLeft")
        return super().decide(snapshot, market_id, now_ms)

    def fill_existing(self, snapshot: dict[str, Any], snapshot_ns: int, now_ms: int) -> int:
        before = len(self.maker_fills)
        n = super().fill_existing(snapshot, snapshot_ns, now_ms)
        for f in self.maker_fills[before:]:
            side = str(f.get("side") or "")
            if side in {"UP", "DOWN"}:
                self.last_fill_ms_by_side[side] = int(f.get("at_ms") or now_ms)
        return n

    def apply_plan(self, decision: dict[str, Any], snapshot_ns: int, now_ms: int, allow_new: bool) -> None:
        d = dict(decision)
        rows = list(d.get("orders") or []) if d.get("decision") == "QUOTE" else []
        delay = int(self.knobs.filled_order_delay_ms or 0)
        open_mid = self.current_seconds_left is not None and self.current_seconds_left > base.OPEN_MID_MIN_SECONDS_LEFT
        if open_mid and delay > 0 and rows:
            kept: list[dict[str, Any]] = []
            for row in rows:
                side = str(row.get("side") or "")
                last_fill = self.last_fill_ms_by_side.get(side)
                if last_fill is not None and now_ms - last_fill < delay:
                    self.delay_suppressed_plans += 1
                    continue
                kept.append(row)
            rows = kept
            d["orders"] = rows
            d["decision"] = "QUOTE" if rows else "IDLE"
            if not rows:
                d["reason"] = "FILLED_ORDER_DELAY"
        super().apply_plan(d, snapshot_ns, now_ms, allow_new)


def clone_sim(src: KnobSim, knobs: KnobParams) -> KnobSim:
    dst = KnobSim(src.models, knobs)
    dst.orders = {k: type(v)(**vars(v)) for k, v in src.orders.items()}
    dst.last_closed = dict(src.last_closed)
    dst.up_shares = float(src.up_shares)
    dst.down_shares = float(src.down_shares)
    dst.up_cost = float(src.up_cost)
    dst.down_cost = float(src.down_cost)
    dst.placements = int(src.placements)
    dst.cancels = int(src.cancels)
    dst.maker_fills = [dict(x) for x in src.maker_fills]
    dst.seed_fills = [dict(x) for x in src.seed_fills]
    dst.last_fill_ms_by_side = dict(src.last_fill_ms_by_side)
    dst.current_seconds_left = src.current_seconds_left
    dst.headwind_blocks = int(getattr(src, "headwind_blocks", 0))
    dst.tailwind_allows = int(getattr(src, "tailwind_allows", 0))
    return dst


def mids(snapshot: dict[str, Any]) -> tuple[float | None, float | None]:
    up = base.snapshot_value(snapshot, "predict_up_mid", "predictUpMid")
    down = base.snapshot_value(snapshot, "predict_down_mid", "predictDownMid")
    if down is None and up is not None:
        # only a fallback for valuation; normal recorder has both token mids
        down = 1.0 - float(up)
    return up, down


def mtm(sim: KnobSim, snapshot: dict[str, Any]) -> float | None:
    up_mid, down_mid = mids(snapshot)
    if up_mid is None or down_mid is None:
        return None
    return (
        float(sim.up_shares) * float(up_mid)
        + float(sim.down_shares) * float(down_mid)
        - float(sim.up_cost)
        - float(sim.down_cost)
    )


def worst_case_floor(sim: KnobSim) -> float:
    total_cost = float(sim.up_cost) + float(sim.down_cost)
    return min(float(sim.up_shares) - total_cost, float(sim.down_shares) - total_cost)


def maker_abs_net(sim: KnobSim) -> float:
    up = sum(float(f["shares"]) for f in sim.maker_fills if str(f["side"]) == "UP")
    down = sum(float(f["shares"]) for f in sim.maker_fills if str(f["side"]) == "DOWN")
    return abs(up - down)


def state_features(snapshot: dict[str, Any], sim: KnobSim, new_fills: list[dict[str, Any]]) -> dict[str, float | None]:
    pub = base.maker_ebm.public_feature_row(snapshot)
    inv = sim.inventory()
    maker_up = sum(float(f["shares"]) for f in sim.maker_fills if str(f["side"]) == "UP")
    maker_down = sum(float(f["shares"]) for f in sim.maker_fills if str(f["side"]) == "DOWN")
    net = maker_up - maker_down
    gross = maker_up + maker_down
    dom = "UP" if net > 1e-9 else "DOWN" if net < -1e-9 else None
    dr = dirv0.simple3(snapshot)
    orders = list(sim.orders.values())
    return {
        **{k: pub.get(k) for k in base.maker_ebm.HAZARD_FEATURES},
        "simple3_up": 1.0 if dr == "UP" else 0.0 if dr == "DOWN" else None,
        "maker_net": net,
        "maker_abs_net": abs(net),
        "maker_gross": gross,
        "maker_imbalance_ratio": abs(net) / gross if gross > 1e-9 else 0.0,
        "maker_paired_coverage": float(inv.get("pairedCoverage") or 0.0),
        "dominant_up": 1.0 if dom == "UP" else 0.0 if dom == "DOWN" else None,
        "dominant_aligned_simple3": 1.0 if dom and dr and dom == dr else 0.0 if dom and dr else None,
        "open_order_count": float(len(orders)),
        "open_up": 1.0 if any(o.side == "UP" for o in orders) else 0.0,
        "open_down": 1.0 if any(o.side == "DOWN" for o in orders) else 0.0,
        "filled_up_this_state": float(sum(1 for f in new_fills if str(f.get("side")) == "UP")),
        "filled_down_this_state": float(sum(1 for f in new_fills if str(f.get("side")) == "DOWN")),
    }


def apply_due_seeds(sim: KnobSim, seeds: list[dict[str, Any]], seed_idx: int, now_ms: int) -> tuple[int, bool]:
    used = False
    while seed_idx < len(seeds) and int(seeds[seed_idx]["filled_at_ms"]) <= now_ms:
        sim.apply_seed(seeds[seed_idx])
        seed_idx += 1
        used = True
    return seed_idx, used


def branch_run(
    src: KnobSim,
    knobs: KnobParams,
    items: list[dict[str, Any]],
    start_idx: int,
    seeds: list[dict[str, Any]],
    seed_idx: int,
    market_id: int,
    current_snapshot_ns: int,
    current_filled: int,
    current_seed_filled: bool,
) -> dict[str, Any] | None:
    sim = clone_sim(src, knobs)
    start_item = items[start_idx]
    start_now = int(start_item["decision_ms"])
    start_snap = dict(start_item["snapshot"])
    start_mtm = mtm(sim, start_snap)
    if start_mtm is None:
        return None
    start_pair = base.fifo_pair([dict(x) for x in sim.maker_fills])
    start_floor = worst_case_floor(sim)
    start_abs_net = maker_abs_net(sim)
    start_fill_count = len(sim.maker_fills)

    # Apply the current post-fill decision. New placement is deliberately blocked
    # on the same snapshot, matching the existing replay contract.
    d0 = sim.decide(start_snap, market_id, start_now)
    sim.apply_plan(d0, current_snapshot_ns, start_now, allow_new=(current_filled == 0 and not current_seed_filled))

    end_snap = start_snap
    end_ms = start_now
    local_seed_idx = seed_idx
    for j in range(start_idx + 1, len(items)):
        item = items[j]
        now = int(item["decision_ms"])
        if now - start_now > HORIZON_MS:
            break
        snap = dict(item["snapshot"])
        ns = int(base.num(snap.get("timestampNs")) or base.num(snap.get("timestamp_ns")) or now * 1_000_000)
        filled = sim.fill_existing(snap, ns, now)
        decision = sim.decide(snap, market_id, now)
        local_seed_idx, seed_filled = apply_due_seeds(sim, seeds, local_seed_idx, now)
        sim.apply_plan(decision, ns, now, allow_new=(filled == 0 and not seed_filled))
        end_snap = snap
        end_ms = now

    if end_ms - start_now < MIN_HORIZON_MS:
        return None
    end_mtm = mtm(sim, end_snap)
    if end_mtm is None:
        return None
    end_pair = base.fifo_pair([dict(x) for x in sim.maker_fills])
    return {
        "utilityMtm10s": float(end_mtm - start_mtm),
        "pairEdgeDelta10s": float(end_pair["lockedEdgeUsdt"] - start_pair["lockedEdgeUsdt"]),
        "absNetDelta10s": float(maker_abs_net(sim) - start_abs_net),
        "worstCaseFloorDelta10s": float(worst_case_floor(sim) - start_floor),
        "makerFills10s": int(len(sim.maker_fills) - start_fill_count),
        "horizonMs": int(end_ms - start_now),
        "delaySuppressedPlans": int(sim.delay_suppressed_plans),
    }


def make_dataset() -> tuple[list[dict[str, Any]], dict[int, int]]:
    our = base.ro(base.DEFAULT_OUR_DB)
    try:
        snapshots = base.load_snapshots(our)
        seeds_by_market = base.load_seeds(our)
        models = base.maker_ebm.load_models()
        rows: list[dict[str, Any]] = []
        market_first_ms: dict[int, int] = {}

        for market_id in sorted(snapshots):
            items = snapshots[market_id]
            if not items:
                continue
            market_first_ms[market_id] = int(items[0]["decision_ms"])
            seeds = list(seeds_by_market.get(market_id, []))
            seed_idx = 0
            ref = KnobSim(models, KnobParams(1, 0))
            sampled = 0
            last_state_ms: int | None = None

            for i, item in enumerate(items):
                now = int(item["decision_ms"])
                snap = dict(item["snapshot"])
                ns = int(base.num(snap.get("timestampNs")) or base.num(snap.get("timestamp_ns")) or now * 1_000_000)
                before = len(ref.maker_fills)
                filled = ref.fill_existing(snap, ns, now)
                new_fills = [dict(x) for x in ref.maker_fills[before:]]
                decision = ref.decide(snap, market_id, now)
                seed_idx, seed_filled = apply_due_seeds(ref, seeds, seed_idx, now)

                sec = base.snapshot_value(snap, "seconds_left", "secondsLeft")
                eligible = (
                    filled > 0
                    and sec is not None
                    and MIN_SECONDS_LEFT <= float(sec) <= MAX_SECONDS_LEFT
                    and sampled < MAX_STATES_PER_MARKET
                    and (last_state_ms is None or now - last_state_ms >= MIN_STATE_GAP_MS)
                )

                if eligible:
                    feats = state_features(snap, ref, new_fills)
                    depth1 = branch_run(ref, KnobParams(1, 0), items, i, seeds, seed_idx, market_id, ns, filled, seed_filled)
                    depth2 = branch_run(ref, KnobParams(2, 0), items, i, seeds, seed_idx, market_id, ns, filled, seed_filled)
                    delay0 = depth1
                    delay1 = branch_run(ref, KnobParams(1, 1_000), items, i, seeds, seed_idx, market_id, ns, filled, seed_filled)
                    delay2 = branch_run(ref, KnobParams(1, 2_000), items, i, seeds, seed_idx, market_id, ns, filled, seed_filled)
                    if all(x is not None for x in (depth1, depth2, delay0, delay1, delay2)):
                        rows.append({
                            "marketId": market_id,
                            "decisionMs": now,
                            **feats,
                            "depth1Utility": depth1["utilityMtm10s"],
                            "depth2Utility": depth2["utilityMtm10s"],
                            "depth2Minus1": depth2["utilityMtm10s"] - depth1["utilityMtm10s"],
                            "depth1PairEdgeDelta": depth1["pairEdgeDelta10s"],
                            "depth2PairEdgeDelta": depth2["pairEdgeDelta10s"],
                            "depth1AbsNetDelta": depth1["absNetDelta10s"],
                            "depth2AbsNetDelta": depth2["absNetDelta10s"],
                            "delay0Utility": delay0["utilityMtm10s"],
                            "delay1Utility": delay1["utilityMtm10s"],
                            "delay2Utility": delay2["utilityMtm10s"],
                            "delay1Minus0": delay1["utilityMtm10s"] - delay0["utilityMtm10s"],
                            "delay2Minus0": delay2["utilityMtm10s"] - delay0["utilityMtm10s"],
                            "delay0PairEdgeDelta": delay0["pairEdgeDelta10s"],
                            "delay1PairEdgeDelta": delay1["pairEdgeDelta10s"],
                            "delay2PairEdgeDelta": delay2["pairEdgeDelta10s"],
                            "delay0AbsNetDelta": delay0["absNetDelta10s"],
                            "delay1AbsNetDelta": delay1["absNetDelta10s"],
                            "delay2AbsNetDelta": delay2["absNetDelta10s"],
                            "horizonMs": depth1["horizonMs"],
                        })
                        sampled += 1
                        last_state_ms = now

                # Continue the reference path after branch extraction.
                ref.apply_plan(decision, ns, now, allow_new=(filled == 0 and not seed_filled))

        return rows, market_first_ms
    finally:
        our.close()


def split_markets(rows: list[dict[str, Any]], market_first_ms: dict[int, int]) -> dict[str, list[int]]:
    markets = sorted({int(r["marketId"]) for r in rows}, key=lambda m: market_first_ms.get(m, 0))
    n = len(markets)
    a = max(1, int(n * 0.70))
    b = max(a + 1, int(n * 0.85)) if n >= 4 else n
    b = min(b, n)
    return {"train": markets[:a], "validation": markets[a:b], "test": markets[b:]}


def make_ebm() -> ExplainableBoostingRegressor:
    return ExplainableBoostingRegressor(
        feature_names=FEATURES,
        max_bins=64,
        max_interaction_bins=32,
        interactions=3,
        outer_bags=4,
        learning_rate=0.04,
        max_rounds=3000,
        early_stopping_rounds=80,
        min_samples_leaf=8,
        n_jobs=-2,
        random_state=20260819,
    )


def frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame([{f: r.get(f) for f in FEATURES} for r in rows], columns=FEATURES)


def stat(xs: list[float]) -> dict[str, Any]:
    return base.stats([float(x) for x in xs])


def top_terms(model: ExplainableBoostingRegressor, n: int = 12) -> list[dict[str, Any]]:
    imps = list(model.term_importances())
    names = list(model.term_names_)
    order = sorted(range(len(imps)), key=lambda i: float(imps[i]), reverse=True)[:n]
    return [{"term": str(names[i]), "importance": float(imps[i])} for i in order]


def evaluate_depth(model: ExplainableBoostingRegressor, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    pred = model.predict(frame(rows))
    chosen: list[float] = []
    action: list[int] = []
    oracle: list[float] = []
    correct = 0
    non_tie = 0
    for r, p in zip(rows, pred):
        a = 2 if float(p) > 0 else 1
        u1 = float(r["depth1Utility"]); u2 = float(r["depth2Utility"])
        chosen.append(u2 if a == 2 else u1)
        action.append(a)
        oracle.append(max(u1, u2))
        d = u2 - u1
        if abs(d) > 1e-9:
            non_tie += 1
            if (a == 2) == (d > 0):
                correct += 1
    fixed1 = [float(r["depth1Utility"]) for r in rows]
    fixed2 = [float(r["depth2Utility"]) for r in rows]
    best_fixed_mean = max(statistics.mean(fixed1), statistics.mean(fixed2))
    return {
        "n": len(rows),
        "chosenActionCounts": {"1tick": sum(a == 1 for a in action), "2tick": sum(a == 2 for a in action)},
        "choiceAccuracyNonTie": correct / non_tie if non_tie else None,
        "nonTieStates": non_tie,
        "dynamicUtility": stat(chosen),
        "fixed1Utility": stat(fixed1),
        "fixed2Utility": stat(fixed2),
        "oracleUtility": stat(oracle),
        "dynamicMeanGainVsBestFixed": statistics.mean(chosen) - best_fixed_mean,
        "dynamicSumGainVsBestFixed": sum(chosen) - max(sum(fixed1), sum(fixed2)),
        "meanRegretVsOracle": statistics.mean([o - c for o, c in zip(oracle, chosen)]),
    }


def evaluate_delay(
    model1: ExplainableBoostingRegressor,
    model2: ExplainableBoostingRegressor,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    p1 = model1.predict(frame(rows)); p2 = model2.predict(frame(rows))
    chosen: list[float] = []
    actions: list[int] = []
    oracle: list[float] = []
    correct = 0
    for r, a1, a2 in zip(rows, p1, p2):
        pred_vals = [0.0, float(a1), float(a2)]
        a = max(range(3), key=lambda i: pred_vals[i])
        us = [float(r["delay0Utility"]), float(r["delay1Utility"]), float(r["delay2Utility"])]
        oa = max(range(3), key=lambda i: us[i])
        chosen.append(us[a]); actions.append(a); oracle.append(us[oa])
        if a == oa:
            correct += 1
    fixed = [[float(r[f"delay{i}Utility"]) for r in rows] for i in range(3)]
    fixed_means = [statistics.mean(x) for x in fixed]
    best_i = max(range(3), key=lambda i: fixed_means[i])
    return {
        "n": len(rows),
        "chosenActionCounts": {"0ms": sum(a == 0 for a in actions), "1000ms": sum(a == 1 for a in actions), "2000ms": sum(a == 2 for a in actions)},
        "exactOracleActionAccuracy": correct / len(rows),
        "dynamicUtility": stat(chosen),
        "fixed0Utility": stat(fixed[0]),
        "fixed1000Utility": stat(fixed[1]),
        "fixed2000Utility": stat(fixed[2]),
        "bestFixedActionOnSplit": [0, 1000, 2000][best_i],
        "oracleUtility": stat(oracle),
        "dynamicMeanGainVsBestFixed": statistics.mean(chosen) - fixed_means[best_i],
        "dynamicSumGainVsBestFixed": sum(chosen) - sum(fixed[best_i]),
        "meanRegretVsOracle": statistics.mean([o - c for o, c in zip(oracle, chosen)]),
    }


def main() -> int:
    rows, market_first_ms = make_dataset()
    if len(rows) < 80:
        raise RuntimeError(f"too few counterfactual states: {len(rows)}")

    split = split_markets(rows, market_first_ms)
    train_set = set(split["train"]); val_set = set(split["validation"]); test_set = set(split["test"])
    train_rows = [r for r in rows if int(r["marketId"]) in train_set]
    val_rows = [r for r in rows if int(r["marketId"]) in val_set]
    test_rows = [r for r in rows if int(r["marketId"]) in test_set]

    X_train = frame(train_rows)
    depth_model = make_ebm(); depth_model.fit(X_train, [float(r["depth2Minus1"]) for r in train_rows])
    delay1_model = make_ebm(); delay1_model.fit(X_train, [float(r["delay1Minus0"]) for r in train_rows])
    delay2_model = make_ebm(); delay2_model.fit(X_train, [float(r["delay2Minus0"]) for r in train_rows])

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "depth": MODEL_DIR / "depth_2minus1_10s.joblib",
        "delay1": MODEL_DIR / "delay1000_minus0_10s.joblib",
        "delay2": MODEL_DIR / "delay2000_minus0_10s.joblib",
    }
    common_meta = {
        "reportVersion": VERSION,
        "researchOnly": True,
        "runtimePromotionAllowed": False,
        "featureContract": FEATURES,
        "labelHorizonMs": HORIZON_MS,
        "label": "10s counterfactual mark-to-mid utility delta; no winner/Target future label",
        "trainingMarkets": split["train"],
    }
    joblib.dump({**common_meta, "task": "depth_2_minus_1", "model": depth_model}, artifacts["depth"])
    joblib.dump({**common_meta, "task": "delay1000_minus_0", "model": delay1_model}, artifacts["delay1"])
    joblib.dump({**common_meta, "task": "delay2000_minus_0", "model": delay2_model}, artifacts["delay2"])

    DATASET.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).sort_values(["decisionMs", "marketId"]).to_csv(DATASET, index=False)

    def split_report(rs: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "states": len(rs),
            "markets": len({int(r["marketId"]) for r in rs}),
            "depth": evaluate_depth(depth_model, rs),
            "delay": evaluate_delay(delay1_model, delay2_model, rs),
        }

    report = {
        "reportVersion": VERSION,
        "researchOnly": True,
        "liveTradingChanges": False,
        "purpose": "Minimal contextual Maker-controller V0: learn WHEN depth 1 vs 2 ticks and refill delay 0/1/2s are locally preferable from strict-past state.",
        "dataset": {
            "states": len(rows),
            "markets": len({int(r["marketId"]) for r in rows}),
            "branchPoints": "reference stable-directional Maker fill events, max 8/market, >=12s apart, seconds_left 75..295",
            "horizonMs": HORIZON_MS,
            "minimumObservedHorizonMs": MIN_HORIZON_MS,
            "utility": "change in mark-to-mid portfolio value over ~10s from same post-fill state; comparisons between actions share identical public future path",
            "secondaryDiagnostics": ["pairEdgeDelta10s", "absNetDelta10s", "worstCaseFloorDelta10s", "makerFills10s"],
            "winnerUsed": False,
            "targetEventsUsed": False,
            "file": str(DATASET),
        },
        "split": {
            "method": "chronological by market first decision timestamp; 70% train / 15% validation / 15% final test",
            "trainMarkets": split["train"],
            "validationMarkets": split["validation"],
            "testMarkets": split["test"],
        },
        "features": FEATURES,
        "models": {
            "depth": {"artifact": str(artifacts["depth"]), "target": "utility(depth2)-utility(depth1)", "topTerms": top_terms(depth_model)},
            "delay1000": {"artifact": str(artifacts["delay1"]), "target": "utility(delay1000)-utility(delay0)", "topTerms": top_terms(delay1_model)},
            "delay2000": {"artifact": str(artifacts["delay2"]), "target": "utility(delay2000)-utility(delay0)", "topTerms": top_terms(delay2_model)},
        },
        "train": split_report(train_rows),
        "validation": split_report(val_rows),
        "test": split_report(test_rows),
        "decisionRule": {
            "depth": "choose 2 ticks iff predicted utility(2)-utility(1)>0, else 1 tick",
            "delay": "predict utility delta for 1s and 2s vs 0; choose argmax of [0, pred_delta_1s, pred_delta_2s]",
        },
        "guard": [
            "No EBM hyperparameter sweep was performed.",
            "No Target event, winner, settlement result, or full-market future regime is a feature or training label.",
            "Future public snapshots are used only to construct the short-horizon counterfactual training label.",
            "This is paper replay under the existing ask-touch fill proxy; positive held-out lift is evidence for contextual knob selection, not live profitability.",
            "Do not modify frozen 8785 STABLE_DIRECTIONAL_TOLERANCE_V1:R1 from this reused historical experiment.",
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "dataset": report["dataset"],
        "splitCounts": {k: {"markets": report[k]["markets"], "states": report[k]["states"]} for k in ("train", "validation", "test")},
        "validation": report["validation"],
        "test": report["test"],
        "topTerms": {k: report["models"][k]["topTerms"][:8] for k in report["models"]},
        "report": str(REPORT),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
