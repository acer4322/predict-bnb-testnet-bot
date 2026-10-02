from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESEARCH = ROOT / "data" / "research"
OUT = RESEARCH / "execution_aware_fill_lifecycle_v0"
TARGET_DIR = RESEARCH / "target_maker_taker_coordination_big_v1"
TARGET_TRAIN = TARGET_DIR / "post_taker_maker_reentry_hazard_v0.csv"
TARGET_FORWARD = TARGET_DIR / "post_taker_maker_reentry_hazard_forward_states_v0.csv"
PREREG = OUT / "hft_target_transition_occupancy_preflight_v1_preregistered.json"
REPORT = OUT / "hft_target_transition_occupancy_preflight_v1_report.json"

HFT_FILES = {
    1573252: OUT / "hft_r2_cycle_option_program_dataset_v1_report.json",
    1574038: OUT / "hft_r2_cycle_option_program_ceiling2_v1_market1574038.json",
    1574352: OUT / "hft_r2_cycle_option_program_ceiling2_v1_market1574352.json",
    1574538: OUT / "hft_r2_cycle_ownstate_event_reentry_v1_market1574538.json",
    1574737: OUT / "hft_r2_cycle_ownstate_event_reentry_v1_market1574737.json",
}
TRAIN_MARKETS = [1573252, 1574038, 1574352]
HOLDOUT_MARKETS = [1574538, 1574737]
SEED = 20260823

FEATURES = [
    "seconds_fraction",
    "public_up_mid_centered",
    "aligned_combined_net_ratio",
    "aligned_maker_net_ratio",
    "combined_imbalance_ratio",
    "combined_paired_coverage",
    "maker_paired_coverage",
    "floor_per_gross",
    "maker_avg_pair_edge",
    "combined_avg_pair_edge",
    "maker_taker_net_same_sign",
    "up_spread_ticks",
    "down_spread_ticks",
    "pair_bid_edge",
    "delta_aligned_combined_net_ratio_per_s",
    "delta_combined_imbalance_ratio_per_s",
    "delta_combined_paired_coverage_per_s",
    "delta_floor_per_gross_per_s",
    "delta_log_combined_gross_per_s",
    "maker_increment_fraction",
    "taker_increment_fraction",
]


def finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def public_up_mid(state: dict[str, Any]) -> float:
    up_bid = finite(state.get("up_bid"), math.nan)
    up_ask = finite(state.get("up_ask"), math.nan)
    if math.isfinite(up_bid) and math.isfinite(up_ask):
        return (up_bid + up_ask) / 2.0
    if math.isfinite(up_bid):
        return up_bid
    if math.isfinite(up_ask):
        return up_ask
    return 0.5


def normalized_state(state: dict[str, Any]) -> dict[str, float]:
    up_mid = public_up_mid(state)
    preferred_sign = 1.0 if up_mid > 0.5 else -1.0 if up_mid < 0.5 else 0.0
    combined_gross = max(0.0, finite(state.get("combined_gross")))
    maker_gross = max(0.0, finite(state.get("maker_gross")))
    combined_scale = max(18.0, combined_gross)
    maker_scale = max(18.0, maker_gross)
    return {
        "seconds_fraction": min(1.0, max(0.0, finite(state.get("seconds_left")) / 300.0)),
        "public_up_mid_centered": up_mid - 0.5,
        "aligned_combined_net_ratio": preferred_sign * finite(state.get("combined_net")) / combined_scale,
        "aligned_maker_net_ratio": preferred_sign * finite(state.get("maker_net")) / maker_scale,
        "combined_imbalance_ratio": finite(state.get("combined_imbalance_ratio")),
        "combined_paired_coverage": finite(state.get("combined_paired_coverage")),
        "maker_paired_coverage": finite(state.get("maker_paired_coverage")),
        "floor_per_gross": finite(state.get("worst_case_floor")) / combined_scale,
        "maker_avg_pair_edge": finite(state.get("maker_avg_pair_edge")),
        "combined_avg_pair_edge": finite(state.get("combined_avg_pair_edge")),
        "maker_taker_net_same_sign": finite(state.get("maker_taker_net_same_sign")),
        "up_spread_ticks": min(20.0, max(0.0, finite(state.get("up_spread_ticks")))),
        "down_spread_ticks": min(20.0, max(0.0, finite(state.get("down_spread_ticks")))),
        "pair_bid_edge": finite(state.get("pair_bid_edge")),
        "combined_gross": combined_gross,
        "maker_gross": maker_gross,
        "taker_gross": max(0.0, finite(state.get("taker_gross"))),
    }


def transition_features(current: dict[str, Any], nxt: dict[str, Any], dt_seconds: float) -> dict[str, float]:
    if not math.isfinite(dt_seconds) or dt_seconds <= 0.0:
        raise ValueError("transition dt must be positive")
    a = normalized_state(current)
    b = normalized_state(nxt)
    dt = max(0.1, dt_seconds)
    delta_maker = max(0.0, b["maker_gross"] - a["maker_gross"])
    delta_taker = max(0.0, b["taker_gross"] - a["taker_gross"])
    delta_total = delta_maker + delta_taker
    row = {name: a[name] for name in FEATURES[:14]}
    row.update(
        {
            "delta_aligned_combined_net_ratio_per_s": (b["aligned_combined_net_ratio"] - a["aligned_combined_net_ratio"]) / dt,
            "delta_combined_imbalance_ratio_per_s": (b["combined_imbalance_ratio"] - a["combined_imbalance_ratio"]) / dt,
            "delta_combined_paired_coverage_per_s": (b["combined_paired_coverage"] - a["combined_paired_coverage"]) / dt,
            "delta_floor_per_gross_per_s": (b["floor_per_gross"] - a["floor_per_gross"]) / dt,
            "delta_log_combined_gross_per_s": (math.log1p(b["combined_gross"]) - math.log1p(a["combined_gross"])) / dt,
            "maker_increment_fraction": delta_maker / delta_total if delta_total > 1e-12 else 0.0,
            "taker_increment_fraction": delta_taker / delta_total if delta_total > 1e-12 else 0.0,
        }
    )
    return {name: finite(row[name]) for name in FEATURES}


def target_transition_frame(path: Path) -> pd.DataFrame:
    usecols = [
        "market_id", "checkpoint_ms", "seconds_left", "maker_gross", "maker_net",
        "maker_paired_coverage", "taker_gross", "combined_gross", "combined_net",
        "combined_imbalance_ratio", "combined_paired_coverage", "worst_case_floor",
        "maker_avg_pair_edge", "combined_avg_pair_edge", "maker_taker_net_same_sign",
        "up_bid", "up_ask", "up_spread_ticks", "down_spread_ticks", "pair_bid_edge",
    ]
    raw = pd.read_csv(path, usecols=usecols)
    raw = raw.drop_duplicates(["market_id", "checkpoint_ms"], keep="last")
    raw = raw.sort_values(["market_id", "checkpoint_ms"], kind="stable")
    rows: list[dict[str, Any]] = []
    for market_id, group in raw.groupby("market_id", sort=False):
        records = group.to_dict("records")
        for current, nxt in zip(records, records[1:]):
            dt = (finite(nxt["checkpoint_ms"]) - finite(current["checkpoint_ms"])) / 1000.0
            if dt < 0.25 or dt > 2.5:
                continue
            row = transition_features(current, nxt, dt)
            row["market_id"] = int(market_id)
            rows.append(row)
    return pd.DataFrame(rows)


def hft_state(transition: dict[str, Any]) -> dict[str, Any]:
    portfolio = dict(transition.get("actualPortfolio") or {})
    book = dict(transition.get("outcomeBook") or {})
    public = dict(transition.get("publicState") or {})
    state = {**portfolio, **book}
    state["seconds_left"] = public.get("secondsLeft")
    return state


def hft_program_rows(path: Path) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    market_id = int(payload["marketId"])
    transition_rows: list[dict[str, Any]] = []
    program_rows: list[dict[str, Any]] = []
    for program in payload.get("programs") or []:
        if int(program.get("cycleInvariantViolationCount") or 0) != 0:
            continue
        program_id = str(program["programId"])
        states = list(program.get("strictPastOptionTransitions") or [])
        count = 0
        for current, nxt in zip(states, states[1:]):
            dt = (finite(nxt.get("atMs")) - finite(current.get("atMs"))) / 1000.0
            if dt < 0.1 or dt > 120.0:
                continue
            row = transition_features(hft_state(current), hft_state(nxt), dt)
            row.update({"market_id": market_id, "program_id": program_id})
            transition_rows.append(row)
            count += 1
        program_rows.append(
            {
                "marketId": market_id,
                "programId": program_id,
                "transitionCount": count,
                "finalWorstCaseFloor": finite(program.get("finalWorstCaseFloor")),
                "realizedPnlAudit": finite(program.get("realizedPnl")),
                "makerFilledShares": finite(program.get("makerFilledShares")),
                "takerFilledShares": finite(program.get("takerFilledShares")),
            }
        )
    return pd.DataFrame(transition_rows), program_rows


def balanced_target_sample(frame: pd.DataFrame, maximum: int) -> pd.DataFrame:
    capped = (
        frame.groupby("market_id", group_keys=False, sort=False)
        .apply(lambda group: group.sample(n=min(10, len(group)), random_state=SEED), include_groups=False)
        .reset_index(drop=True)
    )
    if len(capped) > maximum:
        capped = capped.sample(n=maximum, random_state=SEED).reset_index(drop=True)
    return capped


def clipped_logit(probabilities: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(probabilities, dtype=float), 1e-4, 1.0 - 1e-4)
    return np.clip(np.log(p / (1.0 - p)), -5.0, 5.0)


def safe_spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3 or len(set(x)) < 2 or len(set(y)) < 2:
        return None
    value = float(spearmanr(x, y).statistic)
    return value if math.isfinite(value) else None


def ranking_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [row for row in rows if row["programId"] != "WAIT_ALL" and row.get("meanTransitionLogDensityRatio") is not None]
    scores = [float(row["meanTransitionLogDensityRatio"]) for row in scored]
    floors = [float(row["finalWorstCaseFloor"]) for row in scored]
    ordered = sorted(scored, key=lambda row: float(row["meanTransitionLogDensityRatio"]))
    quartile = max(1, len(ordered) // 4)
    bottom = ordered[:quartile]
    top = ordered[-quartile:]
    return {
        "programs": len(scored),
        "floorSpearman": safe_spearman(scores, floors),
        "bottomQuartileMeanFloor": float(np.mean([row["finalWorstCaseFloor"] for row in bottom])) if bottom else None,
        "topQuartileMeanFloor": float(np.mean([row["finalWorstCaseFloor"] for row in top])) if top else None,
        "topScoreProgram": max(scored, key=lambda row: float(row["meanTransitionLogDensityRatio"]))["programId"] if scored else None,
        "topScoreProgramFloor": max(scored, key=lambda row: float(row["meanTransitionLogDensityRatio"]))["finalWorstCaseFloor"] if scored else None,
        "floorOracleProgram": max(scored, key=lambda row: float(row["finalWorstCaseFloor"]))["programId"] if scored else None,
        "floorOracleValue": max((float(row["finalWorstCaseFloor"]) for row in scored), default=None),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=REPORT)
    args = parser.parse_args()

    if not PREREG.exists():
        raise FileNotFoundError(PREREG)

    target_train_all = target_transition_frame(TARGET_TRAIN)
    target_forward = target_transition_frame(TARGET_FORWARD)

    hft_frames: dict[int, pd.DataFrame] = {}
    programs: dict[int, list[dict[str, Any]]] = {}
    for market_id, path in HFT_FILES.items():
        frame, program_rows = hft_program_rows(path)
        hft_frames[market_id] = frame
        programs[market_id] = program_rows

    hft_train = pd.concat([hft_frames[market_id] for market_id in TRAIN_MARKETS], ignore_index=True)
    hft_holdout = pd.concat([hft_frames[market_id] for market_id in HOLDOUT_MARKETS], ignore_index=True)
    target_train = balanced_target_sample(target_train_all, maximum=max(1, 5 * len(hft_train)))

    x_train = pd.concat([target_train[FEATURES], hft_train[FEATURES]], ignore_index=True)
    y_train = np.concatenate([np.ones(len(target_train), dtype=int), np.zeros(len(hft_train), dtype=int)])
    model = Pipeline(
        [
            ("scale", StandardScaler()),
            (
                "classifier",
                LogisticRegression(C=0.1, class_weight="balanced", max_iter=2000, random_state=SEED),
            ),
        ]
    )
    model.fit(x_train, y_train)

    target_forward_eval = balanced_target_sample(target_forward, maximum=max(1, 5 * len(hft_holdout)))
    x_eval = pd.concat([target_forward_eval[FEATURES], hft_holdout[FEATURES]], ignore_index=True)
    y_eval = np.concatenate([np.ones(len(target_forward_eval), dtype=int), np.zeros(len(hft_holdout), dtype=int)])
    eval_probability = model.predict_proba(x_eval)[:, 1]
    domain_auc = float(roc_auc_score(y_eval, eval_probability))

    program_scores: list[dict[str, Any]] = []
    for market_id in HOLDOUT_MARKETS:
        frame = hft_frames[market_id].copy()
        if len(frame):
            frame["transitionLogDensityRatio"] = clipped_logit(model.predict_proba(frame[FEATURES])[:, 1])
        by_program = frame.groupby("program_id")["transitionLogDensityRatio"].agg(["mean", "median"]).to_dict("index") if len(frame) else {}
        for row in programs[market_id]:
            stats = by_program.get(row["programId"])
            enriched = dict(row)
            enriched["meanTransitionLogDensityRatio"] = float(stats["mean"]) if stats else None
            enriched["medianTransitionLogDensityRatio"] = float(stats["median"]) if stats else None
            program_scores.append(enriched)

    by_market: dict[str, Any] = {}
    for market_id in HOLDOUT_MARKETS:
        by_market[str(market_id)] = ranking_summary([row for row in program_scores if row["marketId"] == market_id])
    pooled = ranking_summary(program_scores)

    economic_conditions = {
        "domainAucAtLeast065": domain_auc >= 0.65,
        "pooledFloorSpearmanAtLeast030": pooled["floorSpearman"] is not None and float(pooled["floorSpearman"]) >= 0.30,
        "bothMarketFloorSpearmanPositive": all(
            by_market[str(market_id)]["floorSpearman"] is not None
            and float(by_market[str(market_id)]["floorSpearman"]) > 0.0
            for market_id in HOLDOUT_MARKETS
        ),
        "bothMarketTopQuartileFloorAboveBottom": all(
            float(by_market[str(market_id)]["topQuartileMeanFloor"])
            > float(by_market[str(market_id)]["bottomQuartileMeanFloor"])
            for market_id in HOLDOUT_MARKETS
        ),
    }
    keep = all(economic_conditions.values())
    decision = "KEEP_FOR_SMALL_OFFLINE_ACTION_INFERENCE" if keep else "REJECT_TARGET_TRANSITION_OCCUPANCY_AS_ECONOMIC_SURROGATE"

    target_forward_scores = clipped_logit(model.predict_proba(target_forward_eval[FEATURES])[:, 1])
    hft_holdout_scores = clipped_logit(model.predict_proba(hft_holdout[FEATURES])[:, 1])
    report = {
        "version": "HFT_TARGET_TRANSITION_OCCUPANCY_PREFLIGHT_V1",
        "researchOnly": True,
        "preregistration": PREREG.name,
        "hypothesis": "Observation-only Target transition occupancy can rank HftBacktest full-cycle program floor without Target action labels.",
        "data": {
            "targetTrainTransitionsAll": len(target_train_all),
            "targetTrainTransitionsSampled": len(target_train),
            "targetForwardTransitionsAll": len(target_forward),
            "targetForwardTransitionsSampled": len(target_forward_eval),
            "hftTrainMarkets": TRAIN_MARKETS,
            "hftTrainTransitions": len(hft_train),
            "hftHoldoutMarkets": HOLDOUT_MARKETS,
            "hftHoldoutTransitions": len(hft_holdout),
        },
        "model": {
            "type": "StandardScaler + class-balanced LogisticRegression",
            "features": FEATURES,
            "regularizationC": 0.1,
            "randomSeed": SEED,
        },
        "domainValidation": {
            "chronologicalTargetForwardVsHftHoldoutAuc": domain_auc,
            "targetForwardMeanLogDensityRatio": float(np.mean(target_forward_scores)),
            "hftHoldoutMeanLogDensityRatio": float(np.mean(hft_holdout_scores)),
        },
        "economicRanking": {
            "primaryMetric": "terminal worst-case portfolio floor",
            "pooled": pooled,
            "byMarket": by_market,
            "programScores": program_scores,
            "conditions": economic_conditions,
        },
        "waitBoundary": "WAIT remains value zero and cannot be overridden by occupancy similarity. Occupancy score is tested only as an ACT-program ranking surrogate.",
        "learnedPolicyRealizedValue": None,
        "chronologicalUnseenOos": False,
        "winnerRuntimeInput": False,
        "targetFutureActionInput": False,
        "officialHftForwardTuning": False,
        "decision": decision,
        "interpretation": (
            "Proceed only to a separately preregistered offline action-inference pilot; this preflight is not policy value."
            if keep
            else "The density ratio may distinguish Target from HFT transitions, but it does not satisfy the locked economic-ranking contract. Do not install or train a full occupancy-matching RL policy from this representation."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"decision": decision, "domainValidation": report["domainValidation"], "pooled": pooled, "byMarket": by_market, "path": str(args.output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
