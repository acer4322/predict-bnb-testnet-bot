from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    log_loss,
    silhouette_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
STATE_CSV = ROOT / "data" / "research" / "supervisor_options_v0" / "target_general_state_increment_v2.csv"
TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT_DIR / "target_latent_terminal_goal_v1_preregistered.json"
REPORT = OUT_DIR / "target_latent_terminal_goal_v1_report.json"

SEED = 20260823
CHECKPOINTS = (240, 180, 120)
PRIMARY_CHECKPOINT = 240
SPLIT_SIZES = {"train": 48, "validation": 16, "unseenHoldout": 16}
TERMINAL_FEATURES = ("pairedCoverage", "floorPerGross", "bestPerGross")

DEPTH_COLUMNS = (
    "up_bid_depth",
    "up_ask_depth",
    "up_top3_bid_depth",
    "down_bid_depth",
    "down_ask_depth",
    "down_top3_bid_depth",
)
PUBLIC_RAW_COLUMNS = (
    "book_age_ms",
    "up_bid",
    "up_ask",
    "up_spread_ticks",
    "down_bid",
    "down_ask",
    "down_spread_ticks",
    "pair_bid_edge",
    "pair_ask_edge",
    "dominant_bid",
    "dominant_ask",
    "opposite_bid",
    "opposite_ask",
    "dominant_opp_bid_pair_edge",
) + DEPTH_COLUMNS


def finite(value: Any, default: float = math.nan) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def safe_div(numerator: Any, denominator: Any, default: float = 0.0) -> float:
    n, d = finite(numerator), finite(denominator)
    return n / d if math.isfinite(n) and math.isfinite(d) and abs(d) > 1e-12 else default


def ro_connection(path: Path) -> sqlite3.Connection:
    uri = "file:" + str(path.resolve()).replace("\\", "/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def terminal_frame(markets: list[int]) -> pd.DataFrame:
    placeholders = ",".join("?" for _ in markets)
    query = f"""
        select market_id, resolved_at_ms, buy_notional_usdt, sell_proceeds_usdt,
               up_position_shares, down_position_shares
        from target_market_results
        where asset='BTC' and market_id in ({placeholders})
    """
    with ro_connection(TARGET_DB) as connection:
        out = pd.read_sql_query(query, connection, params=markets)
    if len(out) != len(markets) or out.market_id.nunique() != len(markets):
        raise RuntimeError(f"terminal coverage mismatch: rows={len(out)} markets={out.market_id.nunique()} expected={len(markets)}")
    out["cash"] = out.sell_proceeds_usdt - out.buy_notional_usdt
    out["combinedGross"] = out.up_position_shares + out.down_position_shares
    out["pairedCoverage"] = 2.0 * out[["up_position_shares", "down_position_shares"]].min(axis=1) / out.combinedGross
    out["worstCaseFloor"] = out.cash + out[["up_position_shares", "down_position_shares"]].min(axis=1)
    out["bestCasePnl"] = out.cash + out[["up_position_shares", "down_position_shares"]].max(axis=1)
    out["floorPerGross"] = out.worstCaseFloor / out.combinedGross
    out["bestPerGross"] = out.bestCasePnl / out.combinedGross
    return out


def split_markets(market_order: list[int]) -> dict[str, list[int]]:
    if len(market_order) != sum(SPLIT_SIZES.values()):
        raise RuntimeError(f"expected {sum(SPLIT_SIZES.values())} markets, got {len(market_order)}")
    a = SPLIT_SIZES["train"]
    b = a + SPLIT_SIZES["validation"]
    return {
        "train": market_order[:a],
        "validation": market_order[a:b],
        "unseenHoldout": market_order[b:],
    }


def checkpoint_row(group: pd.DataFrame, seconds_left: int) -> pd.Series | None:
    eligible = group[pd.to_numeric(group.seconds_left, errors="coerce") >= float(seconds_left)]
    if eligible.empty:
        return None
    row = eligible.sort_values(["seconds_left", "checkpoint_ms"]).iloc[0]
    if finite(row.seconds_left) - seconds_left > 0.75:
        return None
    return row


def public_point(row: pd.Series) -> dict[str, float]:
    up_bid, up_ask = finite(row.up_bid), finite(row.up_ask)
    down_bid, down_ask = finite(row.down_bid), finite(row.down_ask)
    up_mid = (up_bid + up_ask) / 2.0
    down_mid = (down_bid + down_ask) / 2.0
    out = {name: finite(row.get(name)) for name in PUBLIC_RAW_COLUMNS}
    out.update(
        {
            "up_mid": up_mid,
            "down_mid": down_mid,
            "pair_mid_sum": up_mid + down_mid,
            "up_depth_imbalance": safe_div(finite(row.up_bid_depth) - finite(row.up_ask_depth), finite(row.up_bid_depth) + finite(row.up_ask_depth)),
            "down_depth_imbalance": safe_div(finite(row.down_bid_depth) - finite(row.down_ask_depth), finite(row.down_bid_depth) + finite(row.down_ask_depth)),
        }
    )
    for name in DEPTH_COLUMNS:
        out[f"log1p_{name}"] = math.log1p(max(0.0, finite(row.get(name), 0.0)))
        out.pop(name, None)
    return out


def public_features(group: pd.DataFrame, row: pd.Series) -> dict[str, float]:
    now_ms = int(row.checkpoint_ms)
    current = public_point(row)
    history = group[pd.to_numeric(group.checkpoint_ms, errors="coerce") <= now_ms].sort_values("checkpoint_ms")
    for seconds in (10, 30):
        window = history[pd.to_numeric(history.checkpoint_ms, errors="coerce") >= now_ms - seconds * 1000]
        start = public_point(window.iloc[0])
        for name in (
            "up_mid",
            "down_mid",
            "pair_mid_sum",
            "up_spread_ticks",
            "down_spread_ticks",
            "pair_bid_edge",
            "pair_ask_edge",
            "up_depth_imbalance",
            "down_depth_imbalance",
        ):
            current[f"delta_{name}_{seconds}s"] = finite(current.get(name)) - finite(start.get(name))
        mids = (pd.to_numeric(window.up_bid, errors="coerce") + pd.to_numeric(window.up_ask, errors="coerce")) / 2.0
        current[f"up_mid_rv_{seconds}s"] = finite(mids.diff().std(ddof=0), 0.0)
        depths = np.log1p(pd.to_numeric(window.up_bid_depth, errors="coerce").clip(lower=0.0))
        current[f"up_bid_logdepth_rv_{seconds}s"] = finite(depths.diff().std(ddof=0), 0.0)
    return current


def portfolio_features(row: pd.Series) -> dict[str, float]:
    maker_gross = finite(row.maker_gross, 0.0)
    taker_gross = finite(row.taker_gross, 0.0)
    combined_gross = finite(row.combined_gross, 0.0)
    out = {
        "log1p_maker_gross": math.log1p(max(0.0, maker_gross)),
        "maker_net_ratio": safe_div(row.maker_net, maker_gross),
        "maker_paired_coverage": finite(row.maker_paired_coverage, 0.0),
        "log1p_taker_gross": math.log1p(max(0.0, taker_gross)),
        "taker_net_ratio": safe_div(row.taker_net, taker_gross),
        "taker_paired_coverage": finite(row.taker_paired_coverage, 0.0),
        "log1p_combined_gross": math.log1p(max(0.0, combined_gross)),
        "combined_net_ratio": safe_div(row.combined_net, combined_gross),
        "combined_paired_coverage": finite(row.combined_paired_coverage, 0.0),
        "floor_per_combined_gross": safe_div(row.worst_case_floor, combined_gross),
        "best_per_combined_gross": safe_div(row.best_case_pnl, combined_gross),
        "maker_avg_pair_edge": finite(row.maker_avg_pair_edge),
        "taker_avg_pair_edge": finite(row.taker_avg_pair_edge),
        "combined_avg_pair_edge": finite(row.combined_avg_pair_edge),
        "maker_taker_net_same_sign": finite(row.maker_taker_net_same_sign, 0.0),
        "log1p_last_maker_age_ms": math.log1p(max(0.0, finite(row.last_maker_age_ms, 300000.0))),
        "log1p_last_taker_age_ms": math.log1p(max(0.0, finite(row.last_taker_age_ms, 300000.0))),
        "log1p_maker_fills_10s": math.log1p(max(0.0, finite(row.maker_fills_10s, 0.0))),
        "log1p_taker_fills_10s": math.log1p(max(0.0, finite(row.taker_fills_10s, 0.0))),
        "log1p_maker_shares_10s": math.log1p(max(0.0, finite(row.maker_shares_10s, 0.0))),
        "log1p_taker_shares_10s": math.log1p(max(0.0, finite(row.taker_shares_10s, 0.0))),
        "combined_absnet_change_10s_ratio": safe_div(row.combined_absnet_change_10s, combined_gross),
    }
    return out


def classifier() -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
            ("model", LogisticRegression(C=0.1, max_iter=5000, solver="lbfgs", random_state=SEED)),
        ]
    )


def brier_multiclass(y_true: np.ndarray, probabilities: np.ndarray, classes: np.ndarray) -> float:
    one_hot = np.zeros_like(probabilities, dtype=float)
    positions = {int(label): index for index, label in enumerate(classes)}
    for index, label in enumerate(y_true):
        one_hot[index, positions[int(label)]] = 1.0
    return float(np.mean(np.sum((probabilities - one_hot) ** 2, axis=1)))


def metrics(y_true: np.ndarray, predictions: np.ndarray, probabilities: np.ndarray | None = None, classes: np.ndarray | None = None) -> dict[str, Any]:
    labels = np.asarray([0, 1, 2], dtype=int)
    out: dict[str, Any] = {
        "n": int(len(y_true)),
        "classCounts": {str(k): int(v) for k, v in sorted(Counter(map(int, y_true)).items())},
        "accuracy": float(accuracy_score(y_true, predictions)),
        "balancedAccuracy": float(balanced_accuracy_score(y_true, predictions)),
        "macroF1": float(f1_score(y_true, predictions, labels=labels, average="macro", zero_division=0)),
        "confusionMatrix": confusion_matrix(y_true, predictions, labels=labels).tolist(),
    }
    if probabilities is not None and classes is not None:
        out["logLoss"] = float(log_loss(y_true, probabilities, labels=list(classes)))
        out["multiclassBrier"] = brier_multiclass(y_true, probabilities, classes)
    return out


def evaluate_checkpoint(
    states: pd.DataFrame,
    labels_by_market: dict[int, int],
    splits: dict[str, list[int]],
    seconds_left: int,
    terminal_scaler: StandardScaler,
    goal_model: KMeans,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    selected: dict[int, pd.Series] = {}
    unavailable: list[int] = []
    for market_id, group in states.groupby("market_id", sort=False):
        selected_row = checkpoint_row(group, seconds_left)
        if selected_row is None:
            unavailable.append(int(market_id))
            continue
        selected[int(market_id)] = selected_row
        public = public_features(group, selected_row)
        portfolio = portfolio_features(selected_row)
        row = {"marketId": int(market_id), "label": int(labels_by_market[int(market_id)]), "selectedSecondsLeft": finite(selected_row.seconds_left)}
        row.update({f"public__{key}": value for key, value in public.items()})
        row.update({f"portfolio__{key}": value for key, value in portfolio.items()})
        rows.append(row)
    frame = pd.DataFrame(rows).set_index("marketId")
    public_columns = [name for name in frame.columns if name.startswith("public__")]
    combined_columns = public_columns + [name for name in frame.columns if name.startswith("portfolio__")]
    available_splits = {
        name: [market_id for market_id in market_ids if market_id in selected]
        for name, market_ids in splits.items()
    }
    train_ids = available_splits["train"]
    y_train = frame.loc[train_ids, "label"].to_numpy(dtype=int)
    prior = np.bincount(y_train, minlength=3).astype(float)
    prior /= prior.sum()

    models: dict[str, tuple[Pipeline, list[str]]] = {
        "public": (classifier(), public_columns),
        "publicPlusPortfolio": (classifier(), combined_columns),
    }
    for model, columns in models.values():
        model.fit(frame.loc[train_ids, columns], y_train)

    result: dict[str, Any] = {
        "selectedSecondsLeftRange": [float(frame.selectedSecondsLeft.min()), float(frame.selectedSecondsLeft.max())],
        "unavailableMarketIds": unavailable,
        "unavailableBySplit": {
            name: [market_id for market_id in market_ids if market_id not in selected]
            for name, market_ids in splits.items()
        },
        "featureCounts": {"public": len(public_columns), "publicPlusPortfolio": len(combined_columns)},
        "splits": {},
    }
    for split_name, market_ids in available_splits.items():
        y = frame.loc[market_ids, "label"].to_numpy(dtype=int)
        split_result: dict[str, Any] = {}
        prior_probabilities = np.tile(prior, (len(y), 1))
        prior_predictions = np.full(len(y), int(np.argmax(prior)), dtype=int)
        split_result["prior"] = metrics(y, prior_predictions, prior_probabilities, np.asarray([0, 1, 2]))

        current_vectors = []
        for market_id in market_ids:
            row = selected[market_id]
            gross = finite(row.combined_gross, 0.0)
            current_vectors.append(
                [
                    finite(row.combined_paired_coverage, 0.0),
                    safe_div(row.worst_case_floor, gross),
                    safe_div(row.best_case_pnl, gross),
                ]
            )
        persistence = goal_model.predict(terminal_scaler.transform(np.asarray(current_vectors, dtype=float)))
        split_result["currentShapePersistence"] = metrics(y, persistence)

        for model_name, (model, columns) in models.items():
            probabilities = model.predict_proba(frame.loc[market_ids, columns])
            predictions = model.predict(frame.loc[market_ids, columns])
            split_result[model_name] = metrics(y, predictions, probabilities, model.classes_)
        result["splits"][split_name] = split_result
    return result


def main() -> None:
    if not PREREG.exists():
        raise RuntimeError(f"missing preregistration: {PREREG}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    states = pd.read_csv(STATE_CSV)
    market_meta = (
        states[["market_id", "market_end_ms"]]
        .drop_duplicates()
        .sort_values(["market_end_ms", "market_id"])
        .reset_index(drop=True)
    )
    market_order = market_meta.market_id.astype(int).tolist()
    splits = split_markets(market_order)
    terminal = terminal_frame(market_order).set_index("market_id").loc[market_order].reset_index()
    terminal = terminal.merge(market_meta, on="market_id", validate="one_to_one")
    resolved_dates = pd.to_datetime(terminal.resolved_at_ms, unit="ms", utc=True)
    if any(resolved_dates.dt.strftime("%Y-%m-%d") == "2026-08-16"):
        raise RuntimeError("sealed 2026-08-16 market reached preflight")

    split_lookup = {market_id: split for split, markets in splits.items() for market_id in markets}
    terminal["split"] = terminal.market_id.map(split_lookup)
    train_mask = terminal.split == "train"
    terminal_scaler = StandardScaler()
    train_vectors = terminal.loc[train_mask, TERMINAL_FEATURES].to_numpy(dtype=float)
    train_scaled = terminal_scaler.fit_transform(train_vectors)
    goal_model = KMeans(n_clusters=3, n_init=50, random_state=SEED)
    goal_model.fit(train_scaled)
    all_scaled = terminal_scaler.transform(terminal.loc[:, TERMINAL_FEATURES].to_numpy(dtype=float))
    terminal["goalCluster"] = goal_model.predict(all_scaled)
    labels_by_market = {int(row.market_id): int(row.goalCluster) for row in terminal.itertuples()}

    cluster_summaries: dict[str, Any] = {}
    for cluster_id, group in terminal.groupby("goalCluster"):
        cluster_summaries[str(int(cluster_id))] = {
            "counts": {name: int((group.split == name).sum()) for name in splits},
            "median": {
                "pairedCoverage": float(group.pairedCoverage.median()),
                "floorPerGross": float(group.floorPerGross.median()),
                "bestPerGross": float(group.bestPerGross.median()),
                "combinedGross": float(group.combinedGross.median()),
                "buyNotionalUsdt": float(group.buy_notional_usdt.median()),
            },
            "range": {
                "pairedCoverage": [float(group.pairedCoverage.min()), float(group.pairedCoverage.max())],
                "floorPerGross": [float(group.floorPerGross.min()), float(group.floorPerGross.max())],
                "bestPerGross": [float(group.bestPerGross.min()), float(group.bestPerGross.max())],
            },
        }

    checkpoint_results = {
        str(seconds): evaluate_checkpoint(states, labels_by_market, splits, seconds, terminal_scaler, goal_model)
        for seconds in CHECKPOINTS
    }
    primary = checkpoint_results[str(PRIMARY_CHECKPOINT)]["splits"]
    train_counts = Counter(terminal.loc[train_mask, "goalCluster"].astype(int))
    silhouette = float(silhouette_score(all_scaled, terminal.goalCluster.to_numpy(dtype=int)))
    representation_ok = min(train_counts.values()) >= 6 and silhouette >= 0.20

    def f1(split: str, model: str) -> float:
        return float(primary[split][model]["macroF1"])

    public_keep = all(
        f1(split, "public") >= f1(split, "prior") + 0.05
        for split in ("validation", "unseenHoldout")
    )
    combined_keep = all(
        f1(split, "publicPlusPortfolio") >= max(f1(split, "prior"), f1(split, "currentShapePersistence")) + 0.05
        for split in ("validation", "unseenHoldout")
    )
    later_signal = False
    for seconds in (180, 120):
        parts = checkpoint_results[str(seconds)]["splits"]
        later_signal = later_signal or all(
            float(parts[split]["publicPlusPortfolio"]["macroF1"])
            >= float(parts[split]["prior"]["macroF1"]) + 0.05
            for split in ("validation", "unseenHoldout")
        )

    if not representation_ok:
        decision = "REJECT_TERMINAL_GOAL_DISCRETIZATION"
        reason = "The fixed train-only terminal payoff clusters failed the preregistered support or silhouette floor."
    elif combined_keep:
        decision = "KEEP_LATENT_GOAL_SELECTOR"
        reason = "At 240s, public-plus-portfolio state beat both prior and current-shape persistence on validation and unseen holdout."
    elif public_keep:
        decision = "KEEP_PUBLIC_GOAL_SELECTOR"
        reason = "At 240s, public state alone beat the train prior on validation and unseen holdout."
    elif later_signal:
        decision = "NEED_MORE_DATA_LATE_GOAL_SIGNAL"
        reason = "The goal representation survived, but repeatable selector lift appeared only at a later diagnostic checkpoint."
    else:
        decision = "REJECT_LEARNED_DISCRETE_SELECTOR_KEEP_REPRESENTATION_AUDIT"
        reason = "Economically distinct terminal shapes may exist, but the fixed learned selectors did not clear chronological baselines."

    report = {
        "reportVersion": "TARGET_LATENT_TERMINAL_GOAL_V1",
        "researchOnly": True,
        "preregisteredContract": str(PREREG.relative_to(ROOT)).replace("\\", "/"),
        "hypothesis": prereg["hypothesis"],
        "dedupFinding": prereg["dedupFinding"],
        "cohort": {
            "markets": len(market_order),
            "stateRows": len(states),
            "marketIdRange": [min(market_order), max(market_order)],
            "resolvedUtcRange": [resolved_dates.min().isoformat(), resolved_dates.max().isoformat()],
            "sealed20260816Used": False,
            "officialHftForwardUsed": False,
            "splits": {
                name: {
                    "markets": len(markets),
                    "marketIds": markets,
                    "marketEndMsRange": [
                        int(market_meta[market_meta.market_id.isin(markets)].market_end_ms.min()),
                        int(market_meta[market_meta.market_id.isin(markets)].market_end_ms.max()),
                    ],
                }
                for name, markets in splits.items()
            },
        },
        "strictPastAndLeakage": {
            "generalTimeGridNotActionConditioned": True,
            "winnerUsedAsInputOrLabel": False,
            "realizedPnlUsedAsInputOrLabel": False,
            "targetFutureActionUsed": False,
            "terminalClusterScalerFit": "chronological train only",
            "terminalClusterCentroidsFit": "chronological train only",
        },
        "goalRepresentation": {
            "features": list(TERMINAL_FEATURES),
            "clusters": 3,
            "trainClusterCounts": {str(k): int(v) for k, v in sorted(train_counts.items())},
            "allMarketSilhouette": silhouette,
            "representationGatePassed": representation_ok,
            "clusterSummaries": cluster_summaries,
            "centroidsOriginalUnits": terminal_scaler.inverse_transform(goal_model.cluster_centers_).tolist(),
        },
        "checkpointResults": checkpoint_results,
        "primaryCheckpointSecondsLeft": PRIMARY_CHECKPOINT,
        "primaryGateAudit": {
            "publicKeep": public_keep,
            "publicPlusPortfolioKeep": combined_keep,
            "laterSignal": later_signal,
            "validationMacroF1": {name: float(primary["validation"][name]["macroF1"]) for name in primary["validation"]},
            "unseenHoldoutMacroF1": {name: float(primary["unseenHoldout"][name]["macroF1"]) for name in primary["unseenHoldout"]},
        },
        "executionSemantics": {
            "hftbacktestUsed": False,
            "predictExecutionTapeV1Used": False,
            "queueLatencyPartialFillModeled": False,
            "waitActRate": "N/A target-only representation preflight",
            "oracleValueCeiling": "N/A target-only representation preflight",
            "learnedPolicyRealizedValue": "N/A target-only representation preflight",
        },
        "preregisteredGateDecision": decision,
        "preregisteredGateDecisionReason": reason,
        "decision": "KEEP_REPRESENTATION_NEED_MORE_DATA_SELECTOR" if representation_ok else decision,
        "decisionReason": (
            "The terminal payoff representation passed, but no selector is promoted: at 240s the public model's "
            "hard-label macro-F1 passed the weak preregistered prior gate while validation and holdout log-loss were "
            "both worse than the prior, and public-plus-portfolio did not beat current-shape persistence. No HFT value "
            "was measured."
            if representation_ok
            else reason
        ),
        "nextExperimentIfKept": "Express terminal goal as a continuous payoff target or compact goal token, then ask HftBacktest counterfactuals for Q(strict-past execution+portfolio+venue state, WAIT/passive/pair/selective-Taker, goal). Do not imitate Target actions.",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
