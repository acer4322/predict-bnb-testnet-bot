from __future__ import annotations

import importlib.util
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from interpret.glassbox import ExplainableBoostingRegressor

ROOT = Path(__file__).resolve().parents[1]
V0_PATH = ROOT / "tools" / "maker_knob_controller_counterfactual_ebm_v0.py"
spec = importlib.util.spec_from_file_location("knob_cf_v0_for_budget", V0_PATH)
v0 = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = v0
spec.loader.exec_module(v0)
base = v0.base
sens = v0.sens
dirv0 = v0.dirv0

VERSION = "MAKER_INVENTORY_TOLERANCE_CONTEXTUAL_EBM_V0"
REPORT = ROOT / "data" / "research" / "maker_inventory_tolerance_contextual_ebm_v0_report.json"
DATASET = ROOT / "data" / "research" / "maker_inventory_tolerance_contextual_ebm_v0_states.csv"
MODEL_DIR = ROOT / "data" / "research" / "maker_inventory_tolerance_ebm_v0"

LOW_BUDGET_SHARES = 18.0
HIGH_BUDGET_SHARES = 54.0
HORIZON_MS = 20_000
MIN_HORIZON_MS = 16_000
MIN_SECONDS_LEFT = 90.0
MAX_SECONDS_LEFT = 295.0
MAX_STATES_PER_MARKET = 12
MIN_STATE_GAP_MS = 6_000
EPS = 1e-9

FEATURES = list(v0.FEATURES) + [
    "headwind_dominant",
    "budget_fraction_18_to_54",
    "dominant_open_now",
    "minority_open_now",
]


@dataclass(frozen=True)
class BudgetParams:
    block_min_abs_net: float


class BudgetSim(sens.ParamSim):
    """Stable offset-1 directional controller with a configurable headwind inventory budget."""

    def __init__(self, models: dict[str, dict[str, Any]], params: BudgetParams) -> None:
        super().__init__(models, sens.Params(
            name=f"BUDGET_{int(params.block_min_abs_net)}",
            offset_ticks=1,
            headwind_block_min_abs_net=float(params.block_min_abs_net),
            max_order_age_ms=None,
        ))
        self.budget_params = params


def clone_sim(src: BudgetSim, params: BudgetParams) -> BudgetSim:
    dst = BudgetSim(src.models, params)
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
    dst.headwind_blocks = int(getattr(src, "headwind_blocks", 0))
    dst.tailwind_allows = int(getattr(src, "tailwind_allows", 0))
    return dst


def maker_net(sim: BudgetSim) -> float:
    up = sum(float(f["shares"]) for f in sim.maker_fills if str(f["side"]) == "UP")
    down = sum(float(f["shares"]) for f in sim.maker_fills if str(f["side"]) == "DOWN")
    return up - down


def dominant_side(sim: BudgetSim) -> str | None:
    net = maker_net(sim)
    return "UP" if net > EPS else "DOWN" if net < -EPS else None


def strict_headwind(snapshot: dict[str, Any], sim: BudgetSim) -> tuple[bool, str | None, str | None]:
    dom = dominant_side(sim)
    direction = dirv0.simple3(snapshot)
    return bool(dom and direction and dom != direction), dom, direction


def state_features(snapshot: dict[str, Any], sim: BudgetSim, new_fills: list[dict[str, Any]]) -> dict[str, float | None]:
    feats = v0.state_features(snapshot, sim, new_fills)
    net = float(feats.get("maker_net") or 0.0)
    abs_net = abs(net)
    headwind, dom, _ = strict_headwind(snapshot, sim)
    orders = list(sim.orders.values())
    minority = "DOWN" if dom == "UP" else "UP" if dom == "DOWN" else None
    frac = (abs_net - LOW_BUDGET_SHARES) / (HIGH_BUDGET_SHARES - LOW_BUDGET_SHARES)
    return {
        **feats,
        "headwind_dominant": 1.0 if headwind else 0.0,
        "budget_fraction_18_to_54": max(0.0, min(1.0, frac)),
        "dominant_open_now": 1.0 if dom and any(o.side == dom for o in orders) else 0.0,
        "minority_open_now": 1.0 if minority and any(o.side == minority for o in orders) else 0.0,
    }


def mtm(sim: BudgetSim, snapshot: dict[str, Any]) -> float | None:
    up, down = v0.mids(snapshot)
    if up is None or down is None:
        return None
    return float(sim.up_shares) * float(up) + float(sim.down_shares) * float(down) - float(sim.up_cost) - float(sim.down_cost)


def floor(sim: BudgetSim) -> float:
    total_cost = float(sim.up_cost) + float(sim.down_cost)
    return min(float(sim.up_shares) - total_cost, float(sim.down_shares) - total_cost)


def abs_net(sim: BudgetSim) -> float:
    return abs(maker_net(sim))


def apply_due_seeds(sim: BudgetSim, seeds: list[dict[str, Any]], seed_idx: int, now_ms: int) -> tuple[int, bool]:
    used = False
    while seed_idx < len(seeds) and int(seeds[seed_idx]["filled_at_ms"]) <= now_ms:
        sim.apply_seed(seeds[seed_idx])
        seed_idx += 1
        used = True
    return seed_idx, used


def branch_run(
    src: BudgetSim,
    params: BudgetParams,
    items: list[dict[str, Any]],
    start_idx: int,
    seeds: list[dict[str, Any]],
    seed_idx: int,
    market_id: int,
    current_snapshot_ns: int,
    current_filled: int,
    current_seed_filled: bool,
) -> dict[str, Any] | None:
    sim = clone_sim(src, params)
    start_item = items[start_idx]
    start_now = int(start_item["decision_ms"])
    start_snap = dict(start_item["snapshot"])
    start_mtm = mtm(sim, start_snap)
    if start_mtm is None:
        return None
    start_pair = base.fifo_pair([dict(x) for x in sim.maker_fills])
    start_floor = floor(sim)
    start_abs_net = abs_net(sim)
    start_fills = len(sim.maker_fills)

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
        "mtmUtility20s": float(end_mtm - start_mtm),
        "pairEdgeDelta20s": float(end_pair["lockedEdgeUsdt"] - start_pair["lockedEdgeUsdt"]),
        "absNetDelta20s": float(abs_net(sim) - start_abs_net),
        "floorDelta20s": float(floor(sim) - start_floor),
        "makerFills20s": int(len(sim.maker_fills) - start_fills),
        "horizonMs": int(end_ms - start_now),
        "endAbsNet": float(abs_net(sim)),
    }


def collect() -> tuple[list[dict[str, Any]], dict[int, int]]:
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
            ref = BudgetSim(models, BudgetParams(LOW_BUDGET_SHARES))
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
                headwind, dom, direction = strict_headwind(snap, ref)
                net = maker_net(ref)
                eligible = (
                    sec is not None
                    and MIN_SECONDS_LEFT <= float(sec) <= MAX_SECONDS_LEFT
                    and headwind
                    and LOW_BUDGET_SHARES - EPS <= abs(net) < HIGH_BUDGET_SHARES - EPS
                    and sampled < MAX_STATES_PER_MARKET
                    and (last_state_ms is None or now - last_state_ms >= MIN_STATE_GAP_MS)
                )

                if eligible:
                    feats = state_features(snap, ref, new_fills)
                    low = branch_run(ref, BudgetParams(LOW_BUDGET_SHARES), items, i, seeds, seed_idx, market_id, ns, filled, seed_filled)
                    high = branch_run(ref, BudgetParams(HIGH_BUDGET_SHARES), items, i, seeds, seed_idx, market_id, ns, filled, seed_filled)
                    if low is not None and high is not None:
                        rows.append({
                            "marketId": market_id,
                            "decisionMs": now,
                            "dominantSide": dom,
                            "simple3Direction": direction,
                            **feats,
                            "lowMtmUtility": low["mtmUtility20s"],
                            "highMtmUtility": high["mtmUtility20s"],
                            "highMinusLowMtm": high["mtmUtility20s"] - low["mtmUtility20s"],
                            "lowPairEdgeDelta": low["pairEdgeDelta20s"],
                            "highPairEdgeDelta": high["pairEdgeDelta20s"],
                            "highMinusLowPairEdge": high["pairEdgeDelta20s"] - low["pairEdgeDelta20s"],
                            "lowAbsNetDelta": low["absNetDelta20s"],
                            "highAbsNetDelta": high["absNetDelta20s"],
                            "highMinusLowAbsNet": high["absNetDelta20s"] - low["absNetDelta20s"],
                            "lowFloorDelta": low["floorDelta20s"],
                            "highFloorDelta": high["floorDelta20s"],
                            "highMinusLowFloor": high["floorDelta20s"] - low["floorDelta20s"],
                            "lowMakerFills": low["makerFills20s"],
                            "highMakerFills": high["makerFills20s"],
                            "lowEndAbsNet": low["endAbsNet"],
                            "highEndAbsNet": high["endAbsNet"],
                            "horizonMs": low["horizonMs"],
                        })
                        sampled += 1
                        last_state_ms = now

                # Continue canonical low-budget reference path after branch extraction.
                ref.apply_plan(decision, ns, now, allow_new=(filled == 0 and not seed_filled))

        return rows, market_first_ms
    finally:
        our.close()


def split_markets(rows: list[dict[str, Any]], market_first_ms: dict[int, int]) -> dict[str, list[int]]:
    markets = sorted({int(r["marketId"]) for r in rows}, key=lambda m: market_first_ms.get(m, 0))
    n = len(markets)
    a = max(1, int(n * 0.70))
    b = min(n, max(a + 1, int(n * 0.85)))
    return {"train": markets[:a], "validation": markets[a:b], "test": markets[b:]}


def frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame([{f: r.get(f) for f in FEATURES} for r in rows], columns=FEATURES)


def make_model() -> ExplainableBoostingRegressor:
    return ExplainableBoostingRegressor(
        feature_names=FEATURES,
        max_bins=64,
        max_interaction_bins=32,
        interactions=4,
        outer_bags=4,
        learning_rate=0.04,
        max_rounds=3500,
        early_stopping_rounds=80,
        min_samples_leaf=6,
        n_jobs=-2,
        random_state=20260819,
    )


def stat(xs: list[float]) -> dict[str, Any]:
    return base.stats([float(x) for x in xs])


def top_terms(model: ExplainableBoostingRegressor, n: int = 12) -> list[dict[str, Any]]:
    imps = list(model.term_importances())
    names = list(model.term_names_)
    idx = sorted(range(len(imps)), key=lambda i: float(imps[i]), reverse=True)[:n]
    return [{"term": str(names[i]), "importance": float(imps[i])} for i in idx]


def train_model(train_rows: list[dict[str, Any]]) -> tuple[ExplainableBoostingRegressor, list[dict[str, Any]]]:
    effective = [r for r in train_rows if abs(float(r["highMinusLowMtm"])) > EPS]
    if len(effective) < 30:
        raise RuntimeError(f"too few effective train states: {len(effective)}")
    model = make_model()
    model.fit(frame(effective), [float(r["highMinusLowMtm"]) for r in effective])
    return model, effective


def evaluate(model: ExplainableBoostingRegressor, rows: list[dict[str, Any]], fixed_high: bool) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    preds = model.predict(frame(rows))
    dynamic_mtm: list[float] = []
    fixed_mtm: list[float] = []
    oracle_mtm: list[float] = []
    dynamic_pair: list[float] = []
    fixed_pair: list[float] = []
    dynamic_absnet: list[float] = []
    fixed_absnet: list[float] = []
    dynamic_floor: list[float] = []
    fixed_floor: list[float] = []
    chosen_high = 0
    effective = 0
    correct = 0

    for r, p in zip(rows, preds):
        choose_high = float(p) > 0
        chosen_high += int(choose_high)
        low_u = float(r["lowMtmUtility"]); high_u = float(r["highMtmUtility"])
        delta = high_u - low_u
        if abs(delta) > EPS:
            effective += 1
            correct += int(choose_high == (delta > 0))
        dynamic_mtm.append(high_u if choose_high else low_u)
        fixed_mtm.append(high_u if fixed_high else low_u)
        oracle_mtm.append(max(low_u, high_u))
        dynamic_pair.append(float(r["highPairEdgeDelta"] if choose_high else r["lowPairEdgeDelta"]))
        fixed_pair.append(float(r["highPairEdgeDelta"] if fixed_high else r["lowPairEdgeDelta"]))
        dynamic_absnet.append(float(r["highAbsNetDelta"] if choose_high else r["lowAbsNetDelta"]))
        fixed_absnet.append(float(r["highAbsNetDelta"] if fixed_high else r["lowAbsNetDelta"]))
        dynamic_floor.append(float(r["highFloorDelta"] if choose_high else r["lowFloorDelta"]))
        fixed_floor.append(float(r["highFloorDelta"] if fixed_high else r["lowFloorDelta"]))

    return {
        "n": len(rows),
        "effectiveStates": effective,
        "effectiveRate": effective / len(rows),
        "signAccuracyOnEffective": correct / effective if effective else None,
        "chosenCounts": {"LOW18": len(rows) - chosen_high, "HIGH54": chosen_high},
        "fixedActionSelectedFromTrain": "HIGH54" if fixed_high else "LOW18",
        "mtm": {
            "dynamic": stat(dynamic_mtm),
            "fixedTrainSelected": stat(fixed_mtm),
            "oracle": stat(oracle_mtm),
            "dynamicMeanGainVsFixed": statistics.mean(dynamic_mtm) - statistics.mean(fixed_mtm),
            "dynamicSumGainVsFixed": sum(dynamic_mtm) - sum(fixed_mtm),
            "meanRegretVsOracle": statistics.mean([o-d for o, d in zip(oracle_mtm, dynamic_mtm)]),
        },
        "riskDiagnostics": {
            "pairEdgeDeltaDynamic": stat(dynamic_pair),
            "pairEdgeDeltaFixed": stat(fixed_pair),
            "absNetDeltaDynamic": stat(dynamic_absnet),
            "absNetDeltaFixed": stat(fixed_absnet),
            "floorDeltaDynamic": stat(dynamic_floor),
            "floorDeltaFixed": stat(fixed_floor),
        },
    }


def main() -> int:
    rows, market_first_ms = collect()
    if len(rows) < 80:
        raise RuntimeError(f"too few inventory-budget action states: {len(rows)}")
    split = split_markets(rows, market_first_ms)
    sets = {k: set(v) for k, v in split.items()}
    def part(name: str) -> list[dict[str, Any]]:
        return [r for r in rows if int(r["marketId"]) in sets[name]]

    train_rows = part("train")
    model, effective_train = train_model(train_rows)
    train_low_mean = statistics.mean(float(r["lowMtmUtility"]) for r in train_rows)
    train_high_mean = statistics.mean(float(r["highMtmUtility"]) for r in train_rows)
    fixed_high = train_high_mean > train_low_mean

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    artifact = MODEL_DIR / "high54_minus_low18_mtm20s.joblib"
    joblib.dump({
        "reportVersion": VERSION,
        "researchOnly": True,
        "runtimePromotionAllowed": False,
        "task": "inventory_budget_high54_minus_low18_mtm20s",
        "features": FEATURES,
        "winnerUsed": False,
        "targetEventsUsed": False,
        "horizonMs": HORIZON_MS,
        "model": model,
    }, artifact)

    DATASET.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).sort_values(["decisionMs", "marketId"]).to_csv(DATASET, index=False)

    results = {name: evaluate(model, part(name), fixed_high) for name in ("train", "validation", "test")}
    report = {
        "reportVersion": VERSION,
        "researchOnly": True,
        "liveTradingChanges": False,
        "purpose": "Minimal contextual inventory-tolerance model: at true headwind-dominant states with 18<=|Maker net|<54, learn whether to block dominant now (LOW18) or tolerate until 54 shares (HIGH54).",
        "dataset": {
            "states": len(rows),
            "markets": len({int(r["marketId"]) for r in rows}),
            "actionPoint": "strict-past SIMPLE3 headwind dominant, 18<=Maker-only abs net<54, seconds_left 90..295",
            "horizonMs": HORIZON_MS,
            "minimumObservedHorizonMs": MIN_HORIZON_MS,
            "referencePath": "LOW18 stable-directional path; counterfactual branches start from the exact same current portfolio/order/public state",
            "winnerUsed": False,
            "targetEventsUsed": False,
            "file": str(DATASET),
        },
        "actions": {
            "LOW18": "block headwind dominant from abs Maker net >=18",
            "HIGH54": "continue allowing headwind dominant until abs Maker net >=54",
        },
        "label": {
            "primary": "20s counterfactual mark-to-mid utility delta HIGH54-LOW18",
            "secondaryAuditOnly": ["pair edge delta", "abs-net delta", "worst-case floor delta", "Maker fills"],
            "note": "No arbitrary weighted composite utility is used in V0; risk metrics are audited separately.",
        },
        "split": {
            "method": "chronological market split 70/15/15",
            "trainMarkets": split["train"],
            "validationMarkets": split["validation"],
            "testMarkets": split["test"],
        },
        "training": {
            "trainStates": len(train_rows),
            "effectiveTrainStates": len(effective_train),
            "fixedTrainMeans": {"LOW18": train_low_mean, "HIGH54": train_high_mean},
            "fixedActionSelectedFromTrain": "HIGH54" if fixed_high else "LOW18",
            "artifact": str(artifact),
            "topTerms": top_terms(model),
        },
        "results": results,
        "guard": [
            "No threshold sweep and no EBM hyperparameter sweep.",
            "Only genuine headwind inventory-budget states are sampled; no no-op state dilution by construction.",
            "No winner, settlement result, Target event, or full-market future regime is a feature or training label.",
            "Future public snapshots are used only to construct same-path 20s counterfactual labels under the existing paper fill proxy.",
            "The reference path historically used LOW18, so HIGH54 is a local counterfactual from the same observed current state, not a full-history counterfactual policy rollout.",
            "Do not modify frozen 8785 STABLE_DIRECTIONAL_TOLERANCE_V1:R1 from this reused historical model experiment.",
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "dataset": report["dataset"],
        "training": report["training"],
        "validation": results["validation"],
        "test": results["test"],
        "report": str(REPORT),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
