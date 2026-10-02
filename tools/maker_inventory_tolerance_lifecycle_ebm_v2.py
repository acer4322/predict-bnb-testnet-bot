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
V0_PATH = ROOT / "tools" / "maker_inventory_tolerance_contextual_ebm_v0.py"
spec = importlib.util.spec_from_file_location("budget_v0_lifecycle", V0_PATH)
v0 = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = v0
spec.loader.exec_module(v0)
base = v0.base

VERSION = "MAKER_INVENTORY_TOLERANCE_LIFECYCLE_EBM_V2"
REPORT = ROOT / "data" / "research" / "maker_inventory_tolerance_lifecycle_ebm_v2_report.json"
DATASET = ROOT / "data" / "research" / "maker_inventory_tolerance_lifecycle_ebm_v2_states.csv"
MODEL_DIR = ROOT / "data" / "research" / "maker_inventory_tolerance_ebm_v2"
EPS = 1e-9

LIFECYCLE_FEATURES = [
    "last_maker_fill_age_ms",
    "last_dominant_fill_age_ms",
    "last_minority_fill_age_ms",
    "last_fill_side_is_dominant",
    "same_side_fill_streak",
    "dominant_fill_count_5s",
    "minority_fill_count_5s",
    "dominant_fill_count_10s",
    "minority_fill_count_10s",
    "maker_abs_net_change_5s",
    "maker_abs_net_change_10s",
    "last_resolved_markout1s_ticks",
    "last_resolved_markout_side_is_dominant",
    "dominant_markout1s_mean_ticks_10s",
    "minority_markout1s_mean_ticks_10s",
    "dominant_toxic_fill_count_10s",
    "dominant_favorable_fill_count_10s",
]
FEATURES = list(v0.FEATURES) + LIFECYCLE_FEATURES


class LifecycleBudgetSim(v0.BudgetSim):
    def __init__(self, models: dict[str, dict[str, Any]], params: v0.BudgetParams) -> None:
        super().__init__(models, params)
        self.fill_history: list[dict[str, Any]] = []

    @staticmethod
    def _side_mid(snapshot: dict[str, Any], side: str) -> float | None:
        return base.snapshot_value(snapshot, f"predict_{side.lower()}_mid", f"predict{side.title()}Mid")

    def _resolve_markouts(self, snapshot: dict[str, Any], now_ms: int) -> None:
        for rec in self.fill_history:
            if rec.get("markout1s_ticks") is not None:
                continue
            fill_ms = int(rec["at_ms"])
            if now_ms - fill_ms < 1_000:
                continue
            mid_now = self._side_mid(snapshot, str(rec["side"]))
            mid_fill = rec.get("mid_at_fill")
            if mid_now is None or mid_fill is None:
                continue
            rec["markout1s_ticks"] = (float(mid_now) - float(mid_fill)) / float(base.maker_ebm.GRID)
            rec["markout_resolved_at_ms"] = now_ms

    def fill_existing(self, snapshot: dict[str, Any], snapshot_ns: int, now_ms: int) -> int:
        # Resolve prior fills first; this only uses the current strict-past snapshot.
        self._resolve_markouts(snapshot, now_ms)
        before = len(self.maker_fills)
        n = super().fill_existing(snapshot, snapshot_ns, now_ms)
        for f in self.maker_fills[before:]:
            side = str(f.get("side") or "")
            self.fill_history.append({
                "side": side,
                "shares": float(f.get("shares") or 0.0),
                "at_ms": int(f.get("at_ms") or now_ms),
                "mid_at_fill": self._side_mid(snapshot, side),
                "markout1s_ticks": None,
                "markout_resolved_at_ms": None,
            })
        return n


def lifecycle_features(sim: LifecycleBudgetSim, now_ms: int) -> dict[str, float | None]:
    hist = list(sim.fill_history)
    dom = v0.dominant_side(sim)
    minority = "DOWN" if dom == "UP" else "UP" if dom == "DOWN" else None

    def age_of_last(side: str | None = None) -> float | None:
        for r in reversed(hist):
            if side is None or str(r["side"]) == side:
                return float(max(0, now_ms - int(r["at_ms"])))
        return None

    last_side = str(hist[-1]["side"]) if hist else None
    streak = 0
    if last_side:
        for r in reversed(hist):
            if str(r["side"]) != last_side:
                break
            streak += 1

    def recent(window_ms: int) -> list[dict[str, Any]]:
        return [r for r in hist if 0 <= now_ms - int(r["at_ms"]) <= window_ms]

    r5 = recent(5_000)
    r10 = recent(10_000)

    def signed_delta(rows: list[dict[str, Any]]) -> float:
        return sum((1.0 if str(r["side"]) == "UP" else -1.0) * float(r["shares"]) for r in rows)

    current_net = v0.maker_net(sim)
    prior5 = current_net - signed_delta(r5)
    prior10 = current_net - signed_delta(r10)

    resolved = [r for r in hist if r.get("markout1s_ticks") is not None and int(r.get("markout_resolved_at_ms") or 0) <= now_ms]
    last_resolved = resolved[-1] if resolved else None

    def markouts(rows: list[dict[str, Any]], side: str | None) -> list[float]:
        if not side:
            return []
        return [float(r["markout1s_ticks"]) for r in rows
                if str(r["side"]) == side and r.get("markout1s_ticks") is not None]

    dom10 = markouts(r10, dom)
    min10 = markouts(r10, minority)

    return {
        "last_maker_fill_age_ms": age_of_last(None),
        "last_dominant_fill_age_ms": age_of_last(dom),
        "last_minority_fill_age_ms": age_of_last(minority),
        "last_fill_side_is_dominant": 1.0 if last_side and dom and last_side == dom else 0.0 if last_side and dom else None,
        "same_side_fill_streak": float(streak),
        "dominant_fill_count_5s": float(sum(1 for r in r5 if dom and str(r["side"]) == dom)),
        "minority_fill_count_5s": float(sum(1 for r in r5 if minority and str(r["side"]) == minority)),
        "dominant_fill_count_10s": float(sum(1 for r in r10 if dom and str(r["side"]) == dom)),
        "minority_fill_count_10s": float(sum(1 for r in r10 if minority and str(r["side"]) == minority)),
        "maker_abs_net_change_5s": abs(current_net) - abs(prior5),
        "maker_abs_net_change_10s": abs(current_net) - abs(prior10),
        "last_resolved_markout1s_ticks": float(last_resolved["markout1s_ticks"]) if last_resolved else None,
        "last_resolved_markout_side_is_dominant": (
            1.0 if last_resolved and dom and str(last_resolved["side"]) == dom
            else 0.0 if last_resolved and dom else None
        ),
        "dominant_markout1s_mean_ticks_10s": statistics.mean(dom10) if dom10 else None,
        "minority_markout1s_mean_ticks_10s": statistics.mean(min10) if min10 else None,
        "dominant_toxic_fill_count_10s": float(sum(x <= -1.0 for x in dom10)),
        "dominant_favorable_fill_count_10s": float(sum(x >= 1.0 for x in dom10)),
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
            ref = LifecycleBudgetSim(models, v0.BudgetParams(v0.LOW_BUDGET_SHARES))
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
                seed_idx, seed_filled = v0.apply_due_seeds(ref, seeds, seed_idx, now)

                sec = base.snapshot_value(snap, "seconds_left", "secondsLeft")
                headwind, dom, direction = v0.strict_headwind(snap, ref)
                net = v0.maker_net(ref)
                eligible = (
                    sec is not None
                    and v0.MIN_SECONDS_LEFT <= float(sec) <= v0.MAX_SECONDS_LEFT
                    and headwind
                    and v0.LOW_BUDGET_SHARES - EPS <= abs(net) < v0.HIGH_BUDGET_SHARES - EPS
                    and sampled < v0.MAX_STATES_PER_MARKET
                    and (last_state_ms is None or now - last_state_ms >= v0.MIN_STATE_GAP_MS)
                )

                if eligible:
                    feats = v0.state_features(snap, ref, new_fills)
                    life = lifecycle_features(ref, now)
                    low = v0.branch_run(ref, v0.BudgetParams(v0.LOW_BUDGET_SHARES), items, i, seeds, seed_idx, market_id, ns, filled, seed_filled)
                    high = v0.branch_run(ref, v0.BudgetParams(v0.HIGH_BUDGET_SHARES), items, i, seeds, seed_idx, market_id, ns, filled, seed_filled)
                    if low is not None and high is not None:
                        rows.append({
                            "marketId": market_id,
                            "decisionMs": now,
                            "dominantSide": dom,
                            "simple3Direction": direction,
                            **{k: feats.get(k) for k in v0.FEATURES},
                            **life,
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
                            "horizonMs": low["horizonMs"],
                        })
                        sampled += 1
                        last_state_ms = now

                ref.apply_plan(decision, ns, now, allow_new=(filled == 0 and not seed_filled))

        return rows, market_first_ms
    finally:
        our.close()


def frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame([{f: r.get(f) for f in FEATURES} for r in rows], columns=FEATURES)


def make_model() -> ExplainableBoostingRegressor:
    # Keep the same V0 capacity/hyperparameters; only the feature contract changes.
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


def top_terms(model: ExplainableBoostingRegressor, n: int = 16) -> list[dict[str, Any]]:
    imps = list(model.term_importances())
    names = list(model.term_names_)
    idx = sorted(range(len(imps)), key=lambda i: float(imps[i]), reverse=True)[:n]
    return [{"term": str(names[i]), "importance": float(imps[i])} for i in idx]


def evaluate(model: ExplainableBoostingRegressor, rows: list[dict[str, Any]], fixed_high: bool) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    pred = model.predict(frame(rows))
    dynamic: list[float] = []
    fixed: list[float] = []
    oracle: list[float] = []
    chosen_high = 0
    effective = 0
    correct = 0
    safe_true = 0
    safe_selected = 0
    unsafe_selected = 0

    for r, p in zip(rows, pred):
        choose_high = float(p) > 0
        chosen_high += int(choose_high)
        lo = float(r["lowMtmUtility"]); hi = float(r["highMtmUtility"])
        d = hi - lo
        is_safe = d > EPS and float(r["highMinusLowFloor"]) >= -EPS and float(r["highMinusLowAbsNet"]) <= EPS
        safe_true += int(is_safe)
        if choose_high:
            safe_selected += int(is_safe)
            unsafe_selected += int(not is_safe)
        if abs(d) > EPS:
            effective += 1
            correct += int(choose_high == (d > 0))
        dynamic.append(hi if choose_high else lo)
        fixed.append(hi if fixed_high else lo)
        oracle.append(max(lo, hi))

    return {
        "n": len(rows),
        "effectiveStates": effective,
        "signAccuracyOnEffective": correct / effective if effective else None,
        "chosenCounts": {"LOW18": len(rows)-chosen_high, "HIGH54": chosen_high},
        "trueSafeBeneficialHighStates": safe_true,
        "capturedSafeBeneficialHighStates": safe_selected,
        "unsafeHighSelections": unsafe_selected,
        "precisionOfHighSelection": safe_selected / chosen_high if chosen_high else None,
        "recallOfSafeHigh": safe_selected / safe_true if safe_true else None,
        "dynamicUtility": stat(dynamic),
        "fixedTrainSelectedUtility": stat(fixed),
        "oracleUtility": stat(oracle),
        "dynamicMeanGainVsFixed": statistics.mean(dynamic) - statistics.mean(fixed),
        "dynamicSumGainVsFixed": sum(dynamic) - sum(fixed),
    }


def main() -> int:
    rows, market_first_ms = collect()
    split = v0.split_markets(rows, market_first_ms)
    sets = {k: set(v) for k, v in split.items()}
    def part(name: str) -> list[dict[str, Any]]:
        return [r for r in rows if int(r["marketId"]) in sets[name]]

    train = part("train")
    effective = [r for r in train if abs(float(r["highMinusLowMtm"])) > EPS]
    model = make_model()
    model.fit(frame(effective), [float(r["highMinusLowMtm"]) for r in effective])

    low_mean = statistics.mean(float(r["lowMtmUtility"]) for r in train)
    high_mean = statistics.mean(float(r["highMtmUtility"]) for r in train)
    fixed_high = high_mean > low_mean

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    artifact = MODEL_DIR / "high54_minus_low18_mtm20s_lifecycle.joblib"
    joblib.dump({
        "reportVersion": VERSION,
        "researchOnly": True,
        "runtimePromotionAllowed": False,
        "task": "inventory_budget_high54_minus_low18_mtm20s_with_strict_past_lifecycle",
        "features": FEATURES,
        "winnerUsed": False,
        "targetEventsUsed": False,
        "horizonMs": v0.HORIZON_MS,
        "model": model,
    }, artifact)

    DATASET.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).sort_values(["decisionMs", "marketId"]).to_csv(DATASET, index=False)

    results = {name: evaluate(model, part(name), fixed_high) for name in ("train", "validation", "test")}
    report = {
        "reportVersion": VERSION,
        "researchOnly": True,
        "liveTradingChanges": False,
        "purpose": "Retest LOW18 vs HIGH54 inventory-budget permission with strict-past Maker lifecycle/fill-quality memory added; same actions, horizon and EBM capacity as V0.",
        "dataset": {
            "states": len(rows),
            "markets": len({int(r["marketId"]) for r in rows}),
            "actionPoint": "same as V0: strict-past headwind dominant, 18<=Maker-only abs net<54, seconds_left 90..295",
            "horizonMs": v0.HORIZON_MS,
            "winnerUsed": False,
            "targetEventsUsed": False,
            "file": str(DATASET),
        },
        "addedLifecycleFeatures": LIFECYCLE_FEATURES,
        "strictPastGuard": "1s fill markout is resolved only when a later public snapshot >=1s after that fill has already arrived; no future markout is exposed before resolution.",
        "training": {
            "trainStates": len(train),
            "effectiveTrainStates": len(effective),
            "fixedTrainMeans": {"LOW18": low_mean, "HIGH54": high_mean},
            "fixedActionSelectedFromTrain": "HIGH54" if fixed_high else "LOW18",
            "artifact": str(artifact),
            "topTerms": top_terms(model),
        },
        "results": results,
        "guard": [
            "No change to LOW18/HIGH54 actions, 20s horizon, EBM hyperparameters, chronological split method, or decision threshold.",
            "No winner, Target event, settlement, or future/full-market regime feature.",
            "This reused historical cohort is for testing whether lifecycle memory adds information; do not promote or tune from it.",
            "8785 frozen R1 is not modified."
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
