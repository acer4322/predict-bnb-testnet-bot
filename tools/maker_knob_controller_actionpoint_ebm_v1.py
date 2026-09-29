from __future__ import annotations

import importlib.util
import json
import statistics
import sys
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from interpret.glassbox import ExplainableBoostingRegressor

ROOT = Path(__file__).resolve().parents[1]
V0_PATH = ROOT / "tools" / "maker_knob_controller_counterfactual_ebm_v0.py"
spec = importlib.util.spec_from_file_location("knob_cf_v0", V0_PATH)
v0 = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = v0
spec.loader.exec_module(v0)
base = v0.base

VERSION = "MAKER_KNOB_CONTROLLER_ACTIONPOINT_EBM_V1"
REPORT = ROOT / "data" / "research" / "maker_knob_controller_actionpoint_ebm_v1_report.json"
DEPTH_DATASET = ROOT / "data" / "research" / "maker_knob_controller_depth_actionpoints_v1.csv"
DELAY_DATASET = ROOT / "data" / "research" / "maker_knob_controller_delay_actionpoints_v1.csv"
MODEL_DIR = ROOT / "data" / "research" / "maker_knob_controller_ebm_v1"
MAX_STATES_PER_MARKET = 14
DEPTH_MIN_GAP_MS = 7_000
DELAY_MIN_GAP_MS = 5_000
MIN_SECONDS_LEFT = 75.0
MAX_SECONDS_LEFT = 295.0
EPS = 1e-9


def make_model() -> ExplainableBoostingRegressor:
    return ExplainableBoostingRegressor(
        feature_names=v0.FEATURES,
        max_bins=64,
        max_interaction_bins=32,
        interactions=3,
        outer_bags=4,
        learning_rate=0.04,
        max_rounds=3000,
        early_stopping_rounds=80,
        min_samples_leaf=6,
        n_jobs=-2,
        random_state=20260819,
    )


def frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame([{f: r.get(f) for f in v0.FEATURES} for r in rows], columns=v0.FEATURES)


def collect() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[int, int]]:
    our = base.ro(base.DEFAULT_OUR_DB)
    try:
        snapshots = base.load_snapshots(our)
        seeds_by_market = base.load_seeds(our)
        models = base.maker_ebm.load_models()
        depth_rows: list[dict[str, Any]] = []
        delay_rows: list[dict[str, Any]] = []
        market_first_ms: dict[int, int] = {}

        for market_id in sorted(snapshots):
            items = snapshots[market_id]
            if not items:
                continue
            market_first_ms[market_id] = int(items[0]["decision_ms"])
            seeds = list(seeds_by_market.get(market_id, []))
            seed_idx = 0
            ref = v0.KnobSim(models, v0.KnobParams(1, 0))
            depth_n = 0; delay_n = 0
            last_depth_ms: int | None = None
            last_delay_ms: int | None = None

            for i, item in enumerate(items):
                now = int(item["decision_ms"])
                snap = dict(item["snapshot"])
                ns = int(base.num(snap.get("timestampNs")) or base.num(snap.get("timestamp_ns")) or now * 1_000_000)
                before = len(ref.maker_fills)
                filled = ref.fill_existing(snap, ns, now)
                new_fills = [dict(x) for x in ref.maker_fills[before:]]
                decision = ref.decide(snap, market_id, now)
                seed_idx, seed_filled = v0.apply_due_seeds(ref, seeds, seed_idx, now)
                sec = base.snapshot_value(snap, "seconds_left", "secondsLeft")
                open_mid = sec is not None and MIN_SECONDS_LEFT <= float(sec) <= MAX_SECONDS_LEFT

                # Delay action point: immediately after a Maker fill. Current snapshot
                # cannot refill; 0s vs 2s differs only on later decisions.
                delay_eligible = (
                    open_mid and filled > 0 and delay_n < MAX_STATES_PER_MARKET
                    and (last_delay_ms is None or now - last_delay_ms >= DELAY_MIN_GAP_MS)
                )
                if delay_eligible:
                    feats = v0.state_features(snap, ref, new_fills)
                    d0 = v0.branch_run(ref, v0.KnobParams(1, 0), items, i, seeds, seed_idx, market_id, ns, filled, seed_filled)
                    d2 = v0.branch_run(ref, v0.KnobParams(1, 2_000), items, i, seeds, seed_idx, market_id, ns, filled, seed_filled)
                    if d0 is not None and d2 is not None:
                        delay_rows.append({
                            "marketId": market_id, "decisionMs": now, **feats,
                            "delay0Utility": d0["utilityMtm10s"],
                            "delay2Utility": d2["utilityMtm10s"],
                            "delay2Minus0": d2["utilityMtm10s"] - d0["utilityMtm10s"],
                            "delay0PairEdgeDelta": d0["pairEdgeDelta10s"],
                            "delay2PairEdgeDelta": d2["pairEdgeDelta10s"],
                            "delay0AbsNetDelta": d0["absNetDelta10s"],
                            "delay2AbsNetDelta": d2["absNetDelta10s"],
                            "delay0FloorDelta": d0["worstCaseFloorDelta10s"],
                            "delay2FloorDelta": d2["worstCaseFloorDelta10s"],
                        })
                        delay_n += 1; last_delay_ms = now

                # Depth action point: no current fill/seed, controller wants at least
                # one side that is not currently resting, so a quote can actually be created now.
                desired_rows = list(decision.get("orders") or []) if decision.get("decision") == "QUOTE" else []
                active_sides = {o.side for o in ref.orders.values()}
                missing_desired = [r for r in desired_rows if str(r.get("side")) not in active_sides]
                depth_eligible = (
                    open_mid and filled == 0 and not seed_filled and bool(missing_desired)
                    and depth_n < MAX_STATES_PER_MARKET
                    and (last_depth_ms is None or now - last_depth_ms >= DEPTH_MIN_GAP_MS)
                )
                if depth_eligible:
                    feats = v0.state_features(snap, ref, [])
                    q1 = v0.branch_run(ref, v0.KnobParams(1, 0), items, i, seeds, seed_idx, market_id, ns, 0, False)
                    q2 = v0.branch_run(ref, v0.KnobParams(2, 0), items, i, seeds, seed_idx, market_id, ns, 0, False)
                    if q1 is not None and q2 is not None:
                        depth_rows.append({
                            "marketId": market_id, "decisionMs": now, **feats,
                            "depth1Utility": q1["utilityMtm10s"],
                            "depth2Utility": q2["utilityMtm10s"],
                            "depth2Minus1": q2["utilityMtm10s"] - q1["utilityMtm10s"],
                            "depth1PairEdgeDelta": q1["pairEdgeDelta10s"],
                            "depth2PairEdgeDelta": q2["pairEdgeDelta10s"],
                            "depth1AbsNetDelta": q1["absNetDelta10s"],
                            "depth2AbsNetDelta": q2["absNetDelta10s"],
                            "depth1FloorDelta": q1["worstCaseFloorDelta10s"],
                            "depth2FloorDelta": q2["worstCaseFloorDelta10s"],
                        })
                        depth_n += 1; last_depth_ms = now

                ref.apply_plan(decision, ns, now, allow_new=(filled == 0 and not seed_filled))

        return depth_rows, delay_rows, market_first_ms
    finally:
        our.close()


def split_markets(market_first_ms: dict[int, int], used_markets: set[int]) -> dict[str, list[int]]:
    markets = sorted(used_markets, key=lambda m: market_first_ms.get(m, 0))
    n = len(markets); a = max(1, int(n * 0.70)); b = min(n, max(a + 1, int(n * 0.85)))
    return {"train": markets[:a], "validation": markets[a:b], "test": markets[b:]}


def stats(xs: list[float]) -> dict[str, Any]:
    return base.stats([float(x) for x in xs])


def top_terms(model: ExplainableBoostingRegressor, n: int = 12) -> list[dict[str, Any]]:
    imps = list(model.term_importances()); names = list(model.term_names_)
    idx = sorted(range(len(imps)), key=lambda i: float(imps[i]), reverse=True)[:n]
    return [{"term": str(names[i]), "importance": float(imps[i])} for i in idx]


def evaluate_binary(
    model: ExplainableBoostingRegressor,
    rows: list[dict[str, Any]],
    u0_key: str,
    u1_key: str,
    action0: str,
    action1: str,
    fixed_train_action: int,
) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    pred = model.predict(frame(rows))
    dynamic: list[float] = []; fixed: list[float] = []; oracle: list[float] = []
    chosen = []; correct = 0; effective = 0
    for r, p in zip(rows, pred):
        u0 = float(r[u0_key]); u1 = float(r[u1_key]); delta = u1 - u0
        a = 1 if float(p) > 0 else 0
        chosen.append(a); dynamic.append(u1 if a else u0); fixed.append(u1 if fixed_train_action else u0); oracle.append(max(u0, u1))
        if abs(delta) > EPS:
            effective += 1
            if (a == 1) == (delta > 0): correct += 1
    return {
        "n": len(rows),
        "effectiveStates": effective,
        "effectiveRate": effective / len(rows),
        "signAccuracyOnEffective": correct / effective if effective else None,
        "chosenCounts": {action0: sum(a == 0 for a in chosen), action1: sum(a == 1 for a in chosen)},
        "fixedActionSelectedFromTrain": action1 if fixed_train_action else action0,
        "dynamicUtility": stats(dynamic),
        "fixedTrainSelectedUtility": stats(fixed),
        "oracleUtility": stats(oracle),
        "dynamicMeanGainVsTrainSelectedFixed": statistics.mean(dynamic) - statistics.mean(fixed),
        "dynamicSumGainVsTrainSelectedFixed": sum(dynamic) - sum(fixed),
        "meanRegretVsOracle": statistics.mean([o-d for o,d in zip(oracle,dynamic)]),
    }


def train_one(rows: list[dict[str, Any]], delta_key: str) -> tuple[ExplainableBoostingRegressor, list[dict[str, Any]]]:
    effective = [r for r in rows if abs(float(r[delta_key])) > EPS]
    if len(effective) < 30:
        raise RuntimeError(f"too few effective training states for {delta_key}: {len(effective)}")
    model = make_model(); model.fit(frame(effective), [float(r[delta_key]) for r in effective])
    return model, effective


def main() -> int:
    depth_rows, delay_rows, market_first_ms = collect()
    used = {int(r["marketId"]) for r in depth_rows} | {int(r["marketId"]) for r in delay_rows}
    split = split_markets(market_first_ms, used)
    sets = {k: set(v) for k,v in split.items()}

    def rs(rows: list[dict[str, Any]], part: str) -> list[dict[str, Any]]:
        return [r for r in rows if int(r["marketId"]) in sets[part]]

    depth_train = rs(depth_rows,"train"); delay_train = rs(delay_rows,"train")
    depth_model, depth_eff_train = train_one(depth_train, "depth2Minus1")
    delay_model, delay_eff_train = train_one(delay_train, "delay2Minus0")

    depth_train_means = [statistics.mean([float(r[k]) for r in depth_train]) for k in ("depth1Utility","depth2Utility")]
    delay_train_means = [statistics.mean([float(r[k]) for r in delay_train]) for k in ("delay0Utility","delay2Utility")]
    depth_fixed = 1 if depth_train_means[1] > depth_train_means[0] else 0
    delay_fixed = 1 if delay_train_means[1] > delay_train_means[0] else 0

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    depth_art = MODEL_DIR / "depth2_minus1_actionpoint_10s.joblib"
    delay_art = MODEL_DIR / "delay2s_minus0_actionpoint_10s.joblib"
    meta = {"reportVersion": VERSION,"researchOnly":True,"runtimePromotionAllowed":False,"features":v0.FEATURES,"winnerUsed":False,"targetEventsUsed":False,"horizonMs":v0.HORIZON_MS}
    joblib.dump({**meta,"task":"depth2_minus1","model":depth_model},depth_art)
    joblib.dump({**meta,"task":"delay2s_minus0","model":delay_model},delay_art)
    DEPTH_DATASET.parent.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(depth_rows).sort_values(["decisionMs","marketId"]).to_csv(DEPTH_DATASET,index=False)
    pd.DataFrame(delay_rows).sort_values(["decisionMs","marketId"]).to_csv(DELAY_DATASET,index=False)

    report: dict[str,Any] = {
        "reportVersion": VERSION,"researchOnly":True,"liveTradingChanges":False,
        "purpose":"Second contextual-knob V0 using true action points and non-tie training only; tests whether EBM beats a fixed action chosen strictly from training markets.",
        "recorderCadenceContext":"median our_decisions interval ~1.018s, so 1s filled-order delay is near replay resolution; V1 tests 0 vs 2s.",
        "split":{"method":"chronological market split 70/15/15","trainMarkets":split["train"],"validationMarkets":split["validation"],"testMarkets":split["test"]},
        "depth":{"states":len(depth_rows),"effectiveTrainStates":len(depth_eff_train),"dataset":str(DEPTH_DATASET),"artifact":str(depth_art),"fixedTrainMeans":{"1tick":depth_train_means[0],"2tick":depth_train_means[1]},"topTerms":top_terms(depth_model)},
        "delay":{"states":len(delay_rows),"effectiveTrainStates":len(delay_eff_train),"dataset":str(DELAY_DATASET),"artifact":str(delay_art),"fixedTrainMeans":{"0ms":delay_train_means[0],"2000ms":delay_train_means[1]},"topTerms":top_terms(delay_model)},
        "results":{},
        "guard":[
            "No model hyperparameter sweep and no action-threshold tuning.",
            "Training excludes exact counterfactual ties because they contain no knob-choice information; evaluation includes all states.",
            "Fixed comparison action is chosen on training markets only, never from validation/test.",
            "No winner, settlement, Target event, or future/full-market regime is used as a feature.",
            "10s future public snapshots are used only to construct counterfactual labels under the same paper fill proxy.",
            "Do not modify frozen 8785 R1 from this experiment."
        ]
    }
    for part in ("train","validation","test"):
        report["results"][part] = {
            "depth": evaluate_binary(depth_model,rs(depth_rows,part),"depth1Utility","depth2Utility","1tick","2tick",depth_fixed),
            "delay": evaluate_binary(delay_model,rs(delay_rows,part),"delay0Utility","delay2Utility","0ms","2000ms",delay_fixed),
        }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
        "coverage":{"depthStates":len(depth_rows),"delayStates":len(delay_rows),"depthEffectiveTrain":len(depth_eff_train),"delayEffectiveTrain":len(delay_eff_train)},
        "fixedTrainMeans":{"depth":report["depth"]["fixedTrainMeans"],"delay":report["delay"]["fixedTrainMeans"]},
        "validation":report["results"]["validation"],"test":report["results"]["test"],
        "topTerms":{"depth":report["depth"]["topTerms"][:8],"delay":report["delay"]["topTerms"][:8]},
        "report":str(REPORT)},ensure_ascii=False,indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
