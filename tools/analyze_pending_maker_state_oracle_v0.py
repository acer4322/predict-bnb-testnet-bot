from __future__ import annotations

import importlib.util
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/research/target_maker_taker_coordination_big_v1"
HANDOFF_CSV = OUT / "post_taker_handoff_states_v1.csv"
BOOK_DB = ROOT / "data/wallet_maker_book_inference.db"
AUG_CSV = OUT / "post_taker_handoff_pending_oracle_v0.csv"
REPORT = OUT / "pending_maker_state_oracle_v0_report.json"
ARTIFACT = OUT / "handoff_full_plus_pending_oracle_v0.joblib"
BASELINE_REPORT = OUT / "report_handoff_full.json"

# High-confidence retrospective parent placement/fill evidence only.
MIN_PLACEMENT_COVERAGE = 0.85
MIN_FILL_COVERAGE = 0.70
MIN_CONFIDENCE = 0.75
GRID = 0.01


def load_coord_module():
    path = ROOT / "tools/train_target_maker_taker_coordination_big_v1.py"
    spec = importlib.util.spec_from_file_location("coord_big_v1", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("pragma query_only=on")
    return con


def parent_key(market_id: int, order_hash: str | None, leg_id: str, side: str, target_price: float) -> str:
    identity = str(order_hash or leg_id or "NO_ORDER")
    return f"{market_id}:{identity}:{side}:{float(target_price):.12g}"


def load_parent_state() -> tuple[dict[int, list[dict]], dict[str, list[tuple[int, float]]]]:
    con = ro(BOOK_DB)
    try:
        parents: dict[int, list[dict]] = defaultdict(list)
        sql = """
        SELECT parent_id,market_id,target_side,target_price,first_target_ms,last_target_ms,
               target_filled_shares,expected_parent_shares,placement_allocated_shares,
               placement_coverage,placement_first_ms,placement_last_ms,resting_ms,
               fill_allocation_coverage,placement_supports_18,confidence,post_action,
               post_action_delay_ms,post_action_native_price
          FROM maker_book_inference_v21_parent_lifecycles
         WHERE placement_last_ms IS NOT NULL
           AND placement_coverage >= ?
           AND fill_allocation_coverage >= ?
           AND confidence >= ?
        """
        for row in con.execute(sql, (MIN_PLACEMENT_COVERAGE, MIN_FILL_COVERAGE, MIN_CONFIDENCE)):
            parents[int(row["market_id"])].append(dict(row))

        fills: dict[str, list[tuple[int, float]]] = defaultdict(list)
        q = """
        SELECT market_id,order_hash,leg_id,side,target_price,target_shares,target_event_ms
          FROM maker_book_inference_target_events
         WHERE status='MATCHED'
        """
        for row in con.execute(q):
            key = parent_key(int(row["market_id"]), row["order_hash"], str(row["leg_id"]), str(row["side"]), float(row["target_price"]))
            fills[key].append((int(row["target_event_ms"]), float(row["target_shares"])))
        for key in fills:
            fills[key].sort()
        for m in parents:
            parents[m].sort(key=lambda r: (int(r["placement_last_ms"]), int(r["last_target_ms"]), str(r["parent_id"])))
        return parents, fills
    finally:
        con.close()


def finite(x, default=math.nan) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def oracle_features(row: pd.Series, parents_by_market: dict[int, list[dict]], fills_by_parent: dict[str, list[tuple[int, float]]]) -> dict[str, float]:
    market = int(row["market_id"])
    cp = int(row["checkpoint_ms"])
    current_bid = {"UP": finite(row.get("up_bid")), "DOWN": finite(row.get("down_bid"))}
    intervention_side = str(row.get("intervention_side") or "")

    active: list[dict] = []
    for p in parents_by_market.get(market, []):
        placement_last = int(p["placement_last_ms"])
        # Critical anti-leak rule: placement evidence must already exist before the
        # Taker-completion checkpoint. Future Target fills are used only to
        # retrospectively confirm that this pre-existing anonymous placement was Target.
        if placement_last > cp:
            break
        if int(p["last_target_ms"]) <= cp:
            continue
        key = str(p["parent_id"])
        prior_filled = sum(sh for ts, sh in fills_by_parent.get(key, []) if ts <= cp)
        expected = float(p["expected_parent_shares"] or 0.0)
        remaining = max(0.0, expected - prior_filled)
        if remaining <= 1e-9:
            continue
        side = str(p["target_side"])
        bid = current_bid.get(side, math.nan)
        price = float(p["target_price"])
        depth_ticks = ((bid - price) / GRID) if math.isfinite(bid) else math.nan
        active.append({
            "side": side,
            "remaining": remaining,
            "age_ms": float(cp - placement_last),
            "depth_ticks": depth_ticks,
            "confidence": float(p["confidence"]),
            "placement_coverage": float(p["placement_coverage"]),
            "prior_filled": prior_filled,
            "multi_partial": float(prior_filled > 1e-9),
        })

    out: dict[str, float] = {}
    for side in ("UP", "DOWN"):
        xs = [p for p in active if p["side"] == side]
        ages = [p["age_ms"] for p in xs]
        depths = [p["depth_ticks"] for p in xs if math.isfinite(p["depth_ticks"])]
        out[f"oracle_pending_{side.lower()}_count"] = float(len(xs))
        out[f"oracle_pending_{side.lower()}_shares"] = float(sum(p["remaining"] for p in xs))
        out[f"oracle_pending_{side.lower()}_oldest_age_ms"] = float(max(ages)) if ages else math.nan
        out[f"oracle_pending_{side.lower()}_youngest_age_ms"] = float(min(ages)) if ages else math.nan
        out[f"oracle_pending_{side.lower()}_nearest_depth_ticks"] = float(min(depths, key=lambda v: abs(v))) if depths else math.nan
        out[f"oracle_pending_{side.lower()}_mean_confidence"] = float(np.mean([p["confidence"] for p in xs])) if xs else math.nan
        out[f"oracle_pending_{side.lower()}_partial_count"] = float(sum(p["multi_partial"] for p in xs))

    up = out["oracle_pending_up_shares"]
    down = out["oracle_pending_down_shares"]
    out["oracle_pending_any"] = float(bool(active))
    out["oracle_pending_both"] = float(up > 0 and down > 0)
    out["oracle_pending_net"] = float(up - down)
    out["oracle_pending_abs_net"] = float(abs(up - down))
    out["oracle_pending_gross"] = float(up + down)
    out["oracle_pending_same_taker_shares"] = float(up if intervention_side == "UP" else down if intervention_side == "DOWN" else 0.0)
    out["oracle_pending_opp_taker_shares"] = float(down if intervention_side == "UP" else up if intervention_side == "DOWN" else 0.0)
    out["oracle_pending_same_minus_opp"] = out["oracle_pending_same_taker_shares"] - out["oracle_pending_opp_taker_shares"]
    out["oracle_pending_parent_count"] = float(len(active))
    return out


ORACLE_FEATURES = [
    "oracle_pending_up_count","oracle_pending_up_shares","oracle_pending_up_oldest_age_ms","oracle_pending_up_youngest_age_ms","oracle_pending_up_nearest_depth_ticks","oracle_pending_up_mean_confidence","oracle_pending_up_partial_count",
    "oracle_pending_down_count","oracle_pending_down_shares","oracle_pending_down_oldest_age_ms","oracle_pending_down_youngest_age_ms","oracle_pending_down_nearest_depth_ticks","oracle_pending_down_mean_confidence","oracle_pending_down_partial_count",
    "oracle_pending_any","oracle_pending_both","oracle_pending_net","oracle_pending_abs_net","oracle_pending_gross","oracle_pending_same_taker_shares","oracle_pending_opp_taker_shares","oracle_pending_same_minus_opp","oracle_pending_parent_count",
]


def summarize_oracle(df: pd.DataFrame) -> dict:
    any_mask = df["oracle_pending_any"] > 0.5
    both_mask = df["oracle_pending_both"] > 0.5
    by_label = {}
    for label, g in df.groupby("label_handoff"):
        by_label[str(label)] = {
            "n": int(len(g)),
            "pendingAnyRate": float((g["oracle_pending_any"] > 0.5).mean()),
            "pendingBothRate": float((g["oracle_pending_both"] > 0.5).mean()),
            "pendingSameTakerSharesMean": float(g["oracle_pending_same_taker_shares"].mean()),
            "pendingOppTakerSharesMean": float(g["oracle_pending_opp_taker_shares"].mean()),
        }
    return {
        "rows": int(len(df)),
        "rowsWithPending": int(any_mask.sum()),
        "pendingRate": float(any_mask.mean()),
        "rowsWithBoth": int(both_mask.sum()),
        "bothRate": float(both_mask.mean()),
        "byHandoffLabel": by_label,
    }


def main() -> int:
    coord = load_coord_module()
    df = pd.read_csv(HANDOFF_CSV)
    parents, fills = load_parent_state()
    oracle_rows = [oracle_features(row, parents, fills) for _, row in df.iterrows()]
    odf = pd.DataFrame(oracle_rows)
    aug = pd.concat([df.reset_index(drop=True), odf], axis=1)
    aug.to_csv(AUG_CSV, index=False)

    split = coord.split_markets(aug)
    parts = {k: aug[aug.market_id.astype(int).isin(v)].copy() for k, v in split.items()}
    base_features = list(coord.HANDOFF_FEATURE_SETS["FULL"])
    features = base_features + ORACLE_FEATURES

    model = coord.ebm(features)
    model.fit(coord.numeric(parts["train"], features), parts["train"]["label_handoff"].astype(str).tolist())
    classes = [str(x) for x in model.classes_]
    joblib.dump({"version":"PENDING_MAKER_STATE_ORACLE_V0","teacherOnlyOracle":True,"features":features,"classes":classes,"model":model}, ARTIFACT)

    metrics = {}
    for k, part in parts.items():
        X = coord.numeric(part, features)
        metrics[k] = coord.multi_metrics(part["label_handoff"].astype(str), model.predict(X), model.predict_proba(X), classes)

    baseline = json.loads(BASELINE_REPORT.read_text(encoding="utf-8")) if BASELINE_REPORT.exists() else None
    comparison = {}
    if baseline:
        for k in ("validation", "test"):
            comparison[k] = {
                "baselineBalancedAccuracy": float(baseline[k]["balancedAccuracy"]),
                "oracleBalancedAccuracy": float(metrics[k]["balancedAccuracy"]),
                "balancedAccuracyLift": float(metrics[k]["balancedAccuracy"] - baseline[k]["balancedAccuracy"]),
                "baselineMacroF1": float(baseline[k]["macroF1"]),
                "oracleMacroF1": float(metrics[k]["macroF1"]),
                "macroF1Lift": float(metrics[k]["macroF1"] - baseline[k]["macroF1"]),
            }

    report = {
        "reportVersion": "PENDING_MAKER_STATE_ORACLE_V0",
        "researchOnly": True,
        "teacherOnlyOracle": True,
        "runtimeDeployable": False,
        "question": "Does retrospective knowledge of Target Maker parents that were already placed before Taker completion and later confirmed by Target fills materially explain post-Taker Maker handoff?",
        "antiLeakGuard": "Only parents with placement_last_ms <= checkpoint are eligible. Future Target fills may confirm ownership/survival of that pre-existing placement, but placements created after checkpoint are never exposed to the oracle.",
        "confidenceFilters": {"placementCoverageMin":MIN_PLACEMENT_COVERAGE,"fillCoverageMin":MIN_FILL_COVERAGE,"confidenceMin":MIN_CONFIDENCE},
        "coverage": summarize_oracle(aug),
        "splitMarkets": {k: len(v) for k, v in split.items()},
        "featuresAdded": ORACLE_FEATURES,
        "metrics": metrics,
        "comparisonToHistoricalFull": comparison,
        "topTerms": coord.top_terms(model, 30),
        "artifact": str(ARTIFACT),
        "augmentedDataset": str(AUG_CSV),
        "interpretationBoundary": "This is an upper-bound hidden-state value test, not a deployable model. A lift justifies training a public/own-state pending-order belief student; no lift rejects that direction.",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
