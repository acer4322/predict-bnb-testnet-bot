from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import analyze_target_maker_taker_inventory_lifecycle_v1 as lifecycle

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC_DATASET = ROOT / "data" / "research" / "target_taker_action_burst_hazard_v1.csv"
DEFAULT_SIGNAL_DBS = [
    ROOT / "data" / "wallet_taker_signals.db",
    ROOT / "data" / "public_research_archive_v1.db",
]
DEFAULT_SETTLEMENT_DB = ROOT / "data" / "research" / "target_maker_survival_settlements_v3.db"
DEFAULT_RISK_CSV = ROOT / "data" / "research" / "target_maker_taker_repair_hazard_v2_risk.csv"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_maker_heavy_survival_v3_report.json"
DEFAULT_OOF = ROOT / "data" / "research" / "target_maker_heavy_survival_v3_scores.csv"
REPORT_VERSION = "TARGET_MAKER_HEAVY_SURVIVAL_V3_ORDINARY_WALKFORWARD_SPECIAL_AUDIT"
DEFAULT_SPECIAL_START = "2026-08-16T12:00:00+08:00"
EPS = 1e-9

PREDICT_CONTEXT = [
    "seconds_left",
    "predict_up_mid",
    "predict_up_spread",
    "predict_down_spread",
]
LEAN_SPOT = PREDICT_CONTEXT + [
    "spot_minus_strike_bps",
    "direction_score",
    "spot_queue_imbalance",
    "spot_taker_imbalance_1s",
    "spot_return_1s_bps",
    "spot_return_3s_bps",
]
FULL_PUBLIC = LEAN_SPOT + [
    "chainlink_minus_strike_bps",
    "futures_queue_imbalance",
    "futures_taker_imbalance_1s",
    "futures_return_1s_bps",
    "futures_return_3s_bps",
]
FEATURE_SETS = {
    "PREDICT_CONTEXT": PREDICT_CONTEXT,
    "LEAN_SPOT": LEAN_SPOT,
    "FULL_PUBLIC": FULL_PUBLIC,
}

SCORE_FIELDS = [
    "market_id",
    "sampled_ms",
    "regime",
    "fold",
    "feature_set",
    "label_up",
    "predict_up_mid",
    "model_up_probability",
]


def _shared() -> Any:
    path = ROOT / "tools" / "train_target_taker_behavior_v1.py"
    spec = importlib.util.spec_from_file_location("target_survival_shared", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load shared trainer: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _connect_ro(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    db = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=10.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    return db


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _mid(
    up_mid: Any,
    down_mid: Any,
    up_bid: Any = None,
    up_ask: Any = None,
    down_bid: Any = None,
    down_ask: Any = None,
) -> tuple[float | None, float | None]:
    up = _finite(up_mid)
    down = _finite(down_mid)
    ub, ua = _finite(up_bid), _finite(up_ask)
    db, da = _finite(down_bid), _finite(down_ask)
    if up is None and ub is not None and ua is not None:
        up = (ub + ua) / 2.0
    if down is None and db is not None and da is not None:
        down = (db + da) / 2.0
    if up is None and down is not None:
        up = 1.0 - down
    if down is None and up is not None:
        down = 1.0 - up
    if up is not None and not 0.0 <= up <= 1.0:
        up = None
    if down is not None and not 0.0 <= down <= 1.0:
        down = None
    return up, down


def _prob_bucket(value: float | None) -> str:
    if value is None:
        return "UNKNOWN"
    if value < 0.20:
        return "LT_020"
    if value < 0.40:
        return "020_040"
    if value < 0.60:
        return "040_060"
    if value < 0.80:
        return "060_080"
    return "GE_080"


def _heavy_probability(up_probability: float, heavy_side: str) -> float:
    side = str(heavy_side or "").upper()
    if side == "UP":
        return float(up_probability)
    if side == "DOWN":
        return 1.0 - float(up_probability)
    raise ValueError(f"unsupported heavy side: {heavy_side!r}")


def _brier(y: list[int], p: list[float]) -> float | None:
    if not y or len(y) != len(p):
        return None
    return sum((float(a) - float(b)) ** 2 for a, b in zip(y, p)) / len(y)


def _logloss(y: list[int], p: list[float]) -> float | None:
    if not y or len(y) != len(p):
        return None
    total = 0.0
    for actual, probability in zip(y, p):
        q = min(1.0 - 1e-7, max(1e-7, float(probability)))
        total += -(int(actual) * math.log(q) + (1 - int(actual)) * math.log(1.0 - q))
    return total / len(y)


def _market_blocked_brier(rows: list[dict[str, Any]], probability_key: str) -> float | None:
    grouped: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        actual = int(row["heavy_side_won"])
        probability = float(row[probability_key])
        grouped[int(row["market_id"])].append((actual - probability) ** 2)
    if not grouped:
        return None
    return statistics.fmean(statistics.fmean(values) for values in grouped.values())


def _market_blocked_repair_rate(rows: list[dict[str, Any]]) -> float | None:
    grouped: dict[int, list[int]] = defaultdict(list)
    for row in rows:
        grouped[int(row["market_id"])].append(int(row["repair_taker_5s"]))
    if not grouped:
        return None
    return statistics.fmean(statistics.fmean(values) for values in grouped.values())


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp = resolved.with_suffix(resolved.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(resolved)


def _write_scores(path: Path, rows: list[dict[str, Any]]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp = resolved.with_suffix(resolved.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SCORE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(resolved)


class FullSignalArchive:
    def __init__(self, paths: Iterable[Path]) -> None:
        self.sources: list[dict[str, Any]] = []
        for priority, raw in enumerate(paths):
            path = Path(raw).expanduser().resolve()
            if not path.exists():
                continue
            db = _connect_ro(path)
            table = "wallet_taker_signal_snapshots"
            cols = _columns(db, table) if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone() else set()
            if not {"market_id", "sampled_at_ms", "seconds_left"}.issubset(cols):
                db.close()
                continue
            if not (
                {"predict_up_mid", "predict_down_mid"}.issubset(cols)
                or {"predict_up_bid", "predict_up_ask", "predict_down_bid", "predict_down_ask"}.issubset(cols)
            ):
                db.close()
                continue
            self.sources.append(
                {
                    "path": path,
                    "db": db,
                    "priority": priority,
                    "columns": cols,
                }
            )

    def close(self) -> None:
        for source in self.sources:
            source["db"].close()

    def rows(self, market_ids: set[int]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        merged: dict[tuple[int, int], dict[str, Any]] = {}
        raw_rows = 0
        source_counts: dict[str, int] = defaultdict(int)
        desired = {
            "predict_up_mid", "predict_down_mid", "predict_up_bid", "predict_up_ask",
            "predict_down_bid", "predict_down_ask", "spot_minus_strike_bps",
            "chainlink_minus_strike_bps", "direction_score", "spot_queue_imbalance",
            "spot_taker_imbalance_1s", "spot_return_1s_bps", "spot_return_3s_bps",
            "futures_queue_imbalance", "futures_taker_imbalance_1s",
            "futures_return_1s_bps", "futures_return_3s_bps",
        }
        if not market_ids:
            return [], {"configuredSources": [], "rawRowsRead": 0, "oneSecondRows": 0}
        ordered_ids = sorted(market_ids)
        chunk_size = 300
        for source in self.sources:
            cols: set[str] = source["columns"]
            select_optional = sorted(desired & cols)
            for start in range(0, len(ordered_ids), chunk_size):
                chunk = ordered_ids[start : start + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                select = ["market_id", "sampled_at_ms", "seconds_left", *select_optional]
                query = (
                    f"SELECT {','.join(select)} FROM wallet_taker_signal_snapshots "
                    f"WHERE market_id IN ({placeholders}) ORDER BY market_id,sampled_at_ms"
                )
                for raw_row in source["db"].execute(query, tuple(chunk)):
                    raw_rows += 1
                    row = dict(raw_row)
                    market_id = int(row["market_id"])
                    sampled_ms = int(row["sampled_at_ms"])
                    second_key = sampled_ms // 1000
                    up_mid, down_mid = _mid(
                        row.get("predict_up_mid"), row.get("predict_down_mid"),
                        row.get("predict_up_bid"), row.get("predict_up_ask"),
                        row.get("predict_down_bid"), row.get("predict_down_ask"),
                    )
                    if up_mid is None or down_mid is None:
                        continue
                    up_bid, up_ask = _finite(row.get("predict_up_bid")), _finite(row.get("predict_up_ask"))
                    down_bid, down_ask = _finite(row.get("predict_down_bid")), _finite(row.get("predict_down_ask"))
                    candidate: dict[str, Any] = {
                        "market_id": market_id,
                        "sampled_ms": sampled_ms,
                        "seconds_left": _finite(row.get("seconds_left")),
                        "predict_up_mid": up_mid,
                        "predict_down_mid": down_mid,
                        "predict_up_spread": (
                            up_ask - up_bid if up_ask is not None and up_bid is not None else None
                        ),
                        "predict_down_spread": (
                            down_ask - down_bid if down_ask is not None and down_bid is not None else None
                        ),
                        "source": str(source["path"]),
                        "source_priority": int(source["priority"]),
                    }
                    for name in desired:
                        if name.startswith("predict_"):
                            continue
                        candidate[name] = _finite(row.get(name))
                    key = (market_id, second_key)
                    previous = merged.get(key)
                    if previous is None or (
                        sampled_ms, int(source["priority"])
                    ) >= (
                        int(previous["sampled_ms"]), int(previous["source_priority"])
                    ):
                        merged[key] = candidate
        rows = sorted(
            merged.values(), key=lambda row: (int(row["market_id"]), int(row["sampled_ms"]))
        )
        for row in rows:
            source_counts[str(row["source"])] += 1
        return rows, {
            "configuredSources": [str(source["path"]) for source in self.sources],
            "rawRowsRead": raw_rows,
            "oneSecondRows": len(rows),
            "markets": len({int(row["market_id"]) for row in rows}),
            "selectedSourceRows": dict(source_counts),
        }


def _load_settlements(path: Path) -> dict[int, str]:
    db = _connect_ro(path)
    try:
        cols = _columns(db, "market_settlements")
        if not {"market_id", "status", "official_winner"}.issubset(cols):
            raise RuntimeError("settlement DB missing required market_settlements columns")
        result: dict[int, str] = {}
        for row in db.execute(
            "SELECT market_id,status,official_winner FROM market_settlements"
        ):
            winner = str(row["official_winner"] or "").upper()
            if str(row["status"] or "").upper() == "OFFICIAL" and winner in {"UP", "DOWN"}:
                result[int(row["market_id"])] = winner
        return result
    finally:
        db.close()


def _make_frame(
    deps: dict[str, Any],
    signal_rows: list[dict[str, Any]],
    cohort: dict[int, str],
    settlements: dict[int, str],
) -> Any:
    pd = deps["pd"]
    rows: list[dict[str, Any]] = []
    for row in signal_rows:
        market_id = int(row["market_id"])
        regime = cohort.get(market_id)
        winner = settlements.get(market_id)
        if regime not in {"ORDINARY_PRE_SPECIAL", "SPECIAL"} or winner not in {"UP", "DOWN"}:
            continue
        item = dict(row)
        item["regime"] = regime
        item["label_up"] = int(winner == "UP")
        rows.append(item)
    frame = pd.DataFrame(rows)
    if len(frame):
        frame = frame.sort_values(["sampled_ms", "market_id"], kind="mergesort").reset_index(drop=True)
    return frame


def _market_order(frame: Any) -> list[int]:
    rows = (
        frame.groupby("market_id", as_index=False)["sampled_ms"]
        .min()
        .sort_values(["sampled_ms", "market_id"])
    )
    return [int(value) for value in rows["market_id"].tolist()]


def _numeric(pd: Any, frame: Any, features: list[str]) -> Any:
    return frame[features].apply(pd.to_numeric, errors="coerce")


def _metric_summary(values: list[float]) -> dict[str, Any]:
    finite = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not finite:
        return {"count": 0}
    return {
        "count": len(finite),
        "min": min(finite),
        "median": statistics.median(finite),
        "max": max(finite),
        "mean": statistics.fmean(finite),
    }


def _fit_walkforward(
    *,
    shared: Any,
    deps: dict[str, Any],
    ordinary_all: Any,
    ordinary_fit: Any,
    features: list[str],
    min_train_markets: int,
    test_markets: int,
    max_folds: int,
    seed: int,
    interactions: int,
    max_rounds: int,
    outer_bags: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], Any, Any]:
    pd, np = deps["pd"], deps["np"]
    markets = _market_order(ordinary_fit)
    folds = shared._walk_forward_folds(
        markets,
        min_train_markets=min_train_markets,
        test_markets=test_markets,
        max_folds=max_folds,
    )
    fold_reports: list[dict[str, Any]] = []
    score_rows: list[dict[str, Any]] = []
    all_y: list[int] = []
    all_model: list[float] = []
    all_predict: list[float] = []

    for fold_index, fold in enumerate(folds, 1):
        train = ordinary_fit[ordinary_fit["market_id"].isin(fold["trainMarkets"])].copy()
        calibration = ordinary_fit[
            ordinary_fit["market_id"].isin(fold["calibrationMarkets"])
        ].copy()
        test = ordinary_all[ordinary_all["market_id"].isin(fold["testMarkets"])].copy()
        y_train = train["label_up"].astype(int)
        y_cal = calibration["label_up"].astype(int)
        y_test = test["label_up"].astype(int)
        if set(int(v) for v in y_train.unique()) != {0, 1} or len(test) < 20:
            fold_reports.append({
                "fold": fold_index,
                "status": "INSUFFICIENT_DATA_OR_CLASSES",
                "trainRows": int(len(train)),
                "testRows": int(len(test)),
            })
            continue
        model = shared._fit_classifier(
            deps,
            _numeric(pd, train, features),
            y_train,
            interactions=min(max(0, interactions), max(0, len(features) // 2)),
            max_rounds=max_rounds,
            outer_bags=outer_bags,
            seed=seed + fold_index,
        )
        raw_cal = (
            model.predict_proba(_numeric(pd, calibration, features))[:, 1]
            if len(calibration)
            else np.array([])
        )
        calibrator = shared._calibrate(deps, y_cal, raw_cal)
        raw_test = model.predict_proba(_numeric(pd, test, features))[:, 1]
        model_probability = shared._apply_calibration(deps, calibrator, raw_test)
        predict_probability = pd.to_numeric(test["predict_up_mid"], errors="coerce").to_numpy()
        valid = np.isfinite(predict_probability)
        model_metrics = shared._classification_metrics(
            deps, y_test.to_numpy(), model_probability
        )
        predict_metrics = shared._classification_metrics(
            deps, y_test.to_numpy()[valid], predict_probability[valid]
        )
        fold_reports.append({
            "fold": fold_index,
            "status": "OK",
            "trainMarkets": len(fold["trainMarkets"]),
            "calibrationMarkets": len(fold["calibrationMarkets"]),
            "testMarkets": len(fold["testMarkets"]),
            "trainRows": int(len(train)),
            "calibrationRows": int(len(calibration)),
            "testRows": int(len(test)),
            "model": model_metrics,
            "predictMidBaseline": predict_metrics,
            "logLossLiftVsPredictMid": (
                float(predict_metrics["logLoss"] - model_metrics["logLoss"])
                if predict_metrics.get("logLoss") is not None and model_metrics.get("logLoss") is not None
                else None
            ),
            "brierLiftVsPredictMid": (
                float(predict_metrics["brier"] - model_metrics["brier"])
                if predict_metrics.get("brier") is not None and model_metrics.get("brier") is not None
                else None
            ),
            "termImportances": shared._term_summary(model)[:20],
        })
        for (_, row), probability in zip(test.iterrows(), model_probability):
            score_rows.append({
                "market_id": int(row["market_id"]),
                "sampled_ms": int(row["sampled_ms"]),
                "regime": "ORDINARY_PRE_SPECIAL",
                "fold": fold_index,
                "label_up": int(row["label_up"]),
                "predict_up_mid": float(row["predict_up_mid"]),
                "model_up_probability": float(probability),
            })
        all_y.extend(int(v) for v in y_test.tolist())
        all_model.extend(float(v) for v in model_probability.tolist())
        all_predict.extend(float(v) for v in predict_probability.tolist())

    aggregate_model = shared._classification_metrics(
        deps, np.asarray(all_y, dtype=int), np.asarray(all_model, dtype=float)
    ) if all_y else {}
    aggregate_predict = shared._classification_metrics(
        deps, np.asarray(all_y, dtype=int), np.asarray(all_predict, dtype=float)
    ) if all_y else {}
    report = {
        "markets": len(markets),
        "foldsRequested": len(folds),
        "folds": fold_reports,
        "aggregateOOF": {
            "model": aggregate_model,
            "predictMidBaseline": aggregate_predict,
            "logLossLiftVsPredictMid": (
                aggregate_predict.get("logLoss", 0) - aggregate_model.get("logLoss", 0)
                if aggregate_model and aggregate_predict else None
            ),
            "brierLiftVsPredictMid": (
                aggregate_predict.get("brier", 0) - aggregate_model.get("brier", 0)
                if aggregate_model and aggregate_predict else None
            ),
        },
        "foldAuc": _metric_summary([
            item.get("model", {}).get("rocAuc")
            for item in fold_reports if item.get("status") == "OK"
        ]),
        "foldLogLossLiftVsPredictMid": _metric_summary([
            item.get("logLossLiftVsPredictMid")
            for item in fold_reports if item.get("status") == "OK"
        ]),
    }

    calibration_count = max(12, int(round(len(markets) * 0.15)))
    calibration_count = min(calibration_count, max(1, len(markets) - 30))
    final_train_markets = markets[:-calibration_count]
    final_cal_markets = markets[-calibration_count:]
    train = ordinary_fit[ordinary_fit["market_id"].isin(final_train_markets)].copy()
    calibration = ordinary_fit[ordinary_fit["market_id"].isin(final_cal_markets)].copy()
    model = shared._fit_classifier(
        deps,
        _numeric(pd, train, features),
        train["label_up"].astype(int),
        interactions=min(max(0, interactions), max(0, len(features) // 2)),
        max_rounds=max_rounds,
        outer_bags=outer_bags,
        seed=seed + 1000,
    )
    raw_cal = model.predict_proba(_numeric(pd, calibration, features))[:, 1]
    calibrator = shared._calibrate(deps, calibration["label_up"].astype(int), raw_cal)
    return report, score_rows, model, calibrator


def _score_special(
    *,
    shared: Any,
    deps: dict[str, Any],
    special: Any,
    features: list[str],
    model: Any,
    calibrator: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    pd, np = deps["pd"], deps["np"]
    if not len(special):
        return {"rows": 0}, []
    raw = model.predict_proba(_numeric(pd, special, features))[:, 1]
    probability = shared._apply_calibration(deps, calibrator, raw)
    y = special["label_up"].astype(int).to_numpy()
    predict = pd.to_numeric(special["predict_up_mid"], errors="coerce").to_numpy()
    valid = np.isfinite(predict)
    model_metrics = shared._classification_metrics(deps, y, probability)
    predict_metrics = shared._classification_metrics(deps, y[valid], predict[valid])
    rows: list[dict[str, Any]] = []
    for (_, row), score in zip(special.iterrows(), probability):
        rows.append({
            "market_id": int(row["market_id"]),
            "sampled_ms": int(row["sampled_ms"]),
            "regime": "SPECIAL",
            "fold": "SPECIAL_AUDIT",
            "label_up": int(row["label_up"]),
            "predict_up_mid": float(row["predict_up_mid"]),
            "model_up_probability": float(score),
        })
    return {
        "rows": int(len(special)),
        "markets": int(special["market_id"].nunique()),
        "model": model_metrics,
        "predictMidBaseline": predict_metrics,
        "logLossLiftVsPredictMid": predict_metrics["logLoss"] - model_metrics["logLoss"],
        "brierLiftVsPredictMid": predict_metrics["brier"] - model_metrics["brier"],
        "termImportances": shared._term_summary(model)[:20],
    }, rows


def _load_risk_rows(path: Path) -> list[dict[str, Any]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    rows: list[dict[str, Any]] = []
    with resolved.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "market_id", "sampled_ms", "regime", "lifecycle_state", "maker_heavy_side",
            "heavy_win_probability", "heavy_side_won", "repair_taker_5s", "repair_shares_5s",
        }
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise RuntimeError("risk CSV missing: " + ", ".join(sorted(missing)))
        for raw in reader:
            try:
                rows.append({
                    "market_id": int(float(raw["market_id"])),
                    "sampled_ms": int(float(raw["sampled_ms"])),
                    "regime": str(raw["regime"]),
                    "phase": str(raw.get("phase") or ""),
                    "lifecycle_state": str(raw["lifecycle_state"]),
                    "maker_heavy_side": str(raw["maker_heavy_side"]).upper(),
                    "maker_abs_delta": float(raw.get("maker_abs_delta") or 0.0),
                    "predict_heavy_probability": float(raw["heavy_win_probability"]),
                    "heavy_side_won": int(float(raw["heavy_side_won"])),
                    "repair_taker_5s": int(float(raw["repair_taker_5s"])),
                    "repair_shares_5s": float(raw["repair_shares_5s"]),
                })
            except (TypeError, ValueError):
                continue
    return rows


def _repair_bucket_summary(rows: list[dict[str, Any]], probability_key: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for bucket in ("LT_020", "020_040", "040_060", "060_080", "GE_080"):
        subset = [row for row in rows if _prob_bucket(float(row[probability_key])) == bucket]
        repairs = [row for row in subset if int(row["repair_taker_5s"]) == 1]
        result[bucket] = {
            "rows": len(subset),
            "markets": len({int(row["market_id"]) for row in subset}),
            "heavySideWinRateAudit": (
                statistics.fmean(int(row["heavy_side_won"]) for row in subset) if subset else None
            ),
            "repairRate5s": (
                statistics.fmean(int(row["repair_taker_5s"]) for row in subset) if subset else None
            ),
            "marketBlockedMeanRepairRate5s": _market_blocked_repair_rate(subset),
            "meanRepairSharesGivenRepair": (
                statistics.fmean(float(row["repair_shares_5s"]) for row in repairs) if repairs else None
            ),
        }
    return result


def _repair_audit(
    risk_rows: list[dict[str, Any]],
    score_rows_by_set: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for feature_set, score_rows in score_rows_by_set.items():
        index = {
            (int(row["market_id"]), int(row["sampled_ms"]) // 1000): float(row["model_up_probability"])
            for row in score_rows
        }
        feature_result: dict[str, Any] = {}
        for regime in ("ORDINARY_PRE_SPECIAL", "SPECIAL"):
            joined: list[dict[str, Any]] = []
            source = [
                row for row in risk_rows
                if row["regime"] == regime and row["lifecycle_state"] == "POST_FIRST_TAKER"
            ]
            for row in source:
                score = index.get((int(row["market_id"]), int(row["sampled_ms"]) // 1000))
                if score is None:
                    continue
                item = dict(row)
                item["model_heavy_probability"] = _heavy_probability(
                    score, str(row["maker_heavy_side"])
                )
                joined.append(item)
            y = [int(row["heavy_side_won"]) for row in joined]
            p_model = [float(row["model_heavy_probability"]) for row in joined]
            p_predict = [float(row["predict_heavy_probability"]) for row in joined]
            feature_result[regime] = {
                "sourceRiskRows": len(source),
                "joinedRows": len(joined),
                "joinCoverage": len(joined) / len(source) if source else None,
                "markets": len({int(row["market_id"]) for row in joined}),
                "heavyOutcomeCalibration": {
                    "modelBrier": _brier(y, p_model),
                    "predictMidBrier": _brier(y, p_predict),
                    "modelLogLoss": _logloss(y, p_model),
                    "predictMidLogLoss": _logloss(y, p_predict),
                    "marketBlockedModelBrier": _market_blocked_brier(joined, "model_heavy_probability"),
                    "marketBlockedPredictMidBrier": _market_blocked_brier(joined, "predict_heavy_probability"),
                },
                "repairByModelHeavyProbability": _repair_bucket_summary(
                    joined, "model_heavy_probability"
                ),
                "repairByPredictHeavyProbability": _repair_bucket_summary(
                    joined, "predict_heavy_probability"
                ),
            }
        result[feature_set] = feature_result
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Train target-blind public P(UP wins) EBM models on ordinary markets only, "
            "audit untouched special markets, then orient scores to Target Maker-heavy side "
            "and compare them with repair hazard."
        )
    )
    parser.add_argument("--public-dataset", type=Path, default=DEFAULT_PUBLIC_DATASET)
    parser.add_argument("--settlement-db", type=Path, default=DEFAULT_SETTLEMENT_DB)
    parser.add_argument("--risk-csv", type=Path, default=DEFAULT_RISK_CSV)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--scores-csv", type=Path, default=DEFAULT_OOF)
    parser.add_argument("--signal-db", type=Path, action="append", default=None)
    parser.add_argument("--special-start", default=DEFAULT_SPECIAL_START)
    parser.add_argument("--fit-stride-seconds", type=int, default=2)
    parser.add_argument("--min-train-markets", type=int, default=180)
    parser.add_argument("--test-markets", type=int, default=50)
    parser.add_argument("--max-folds", type=int, default=7)
    parser.add_argument("--interactions", type=int, default=6)
    parser.add_argument("--max-rounds", type=int, default=500)
    parser.add_argument("--outer-bags", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20260817)
    args = parser.parse_args()

    shared = _shared()
    deps = shared._imports()
    pd = deps["pd"]
    special_start_ms = lifecycle._epoch_ms(args.special_start)

    print(REPORT_VERSION, flush=True)
    print("[1/6] Resolve frozen ordinary/special cohorts + official outcomes...", flush=True)
    cohort, _, public_meta = lifecycle._load_public_cohorts(
        args.public_dataset, special_start_ms=special_start_ms
    )
    settlements = _load_settlements(args.settlement_db)
    cohort_ids = set(cohort)
    settled_cohort = cohort_ids & set(settlements)
    print(
        f"      cohortMarkets={len(cohort_ids):,} settled={len(settled_cohort):,} "
        f"coverage={len(settled_cohort)/len(cohort_ids):.1%}",
        flush=True,
    )

    print("[2/6] Load one-second target-blind public features...", flush=True)
    signal_paths = args.signal_db if args.signal_db else DEFAULT_SIGNAL_DBS
    archive = FullSignalArchive(signal_paths)
    try:
        signal_rows, signal_meta = archive.rows(settled_cohort)
    finally:
        archive.close()
    frame = _make_frame(deps, signal_rows, cohort, settlements)
    if not len(frame):
        raise SystemExit("no public feature rows with official outcomes")
    ordinary_all = frame[frame["regime"] == "ORDINARY_PRE_SPECIAL"].copy()
    special = frame[frame["regime"] == "SPECIAL"].copy()
    stride = max(1, int(args.fit_stride_seconds))
    ordinary_fit = ordinary_all[(ordinary_all["sampled_ms"] // 1000) % stride == 0].copy()
    print(
        f"      ordinary={ordinary_all['market_id'].nunique():,} markets/{len(ordinary_all):,} rows "
        f"fitRows={len(ordinary_fit):,}; special={special['market_id'].nunique():,} markets/{len(special):,} rows",
        flush=True,
    )

    print("[3/6] Ordinary chronological walk-forward...", flush=True)
    feature_reports: dict[str, Any] = {}
    score_rows_by_set: dict[str, list[dict[str, Any]]] = {}
    for feature_set, features in FEATURE_SETS.items():
        missing = [name for name in features if name not in ordinary_all.columns]
        if missing:
            feature_reports[feature_set] = {"status": "MISSING_FEATURES", "missing": missing}
            score_rows_by_set[feature_set] = []
            continue
        wf, ordinary_scores, final_model, final_calibrator = _fit_walkforward(
            shared=shared,
            deps=deps,
            ordinary_all=ordinary_all,
            ordinary_fit=ordinary_fit,
            features=features,
            min_train_markets=max(40, int(args.min_train_markets)),
            test_markets=max(10, int(args.test_markets)),
            max_folds=max(1, int(args.max_folds)),
            seed=int(args.seed),
            interactions=max(0, int(args.interactions)),
            max_rounds=max(100, int(args.max_rounds)),
            outer_bags=max(2, int(args.outer_bags)),
        )
        special_report, special_scores = _score_special(
            shared=shared,
            deps=deps,
            special=special,
            features=features,
            model=final_model,
            calibrator=final_calibrator,
        )
        for row in ordinary_scores + special_scores:
            row["feature_set"] = feature_set
        feature_reports[feature_set] = {
            "status": "OK",
            "features": features,
            "ordinaryWalkForward": wf,
            "specialUntouchedAudit": special_report,
        }
        score_rows_by_set[feature_set] = ordinary_scores + special_scores
        print(
            f"      {feature_set}: ordinary AUC={wf['aggregateOOF']['model'].get('rocAuc')} "
            f"special AUC={special_report.get('model', {}).get('rocAuc')} "
            f"special brierLift={special_report.get('brierLiftVsPredictMid')}",
            flush=True,
        )

    all_scores = [row for rows in score_rows_by_set.values() for row in rows]
    _write_scores(args.scores_csv, all_scores)

    print("[4/6] Join model survival scores back to Maker repair risk-set...", flush=True)
    risk_rows = _load_risk_rows(args.risk_csv)
    repair_audit = _repair_audit(risk_rows, score_rows_by_set)

    print("[5/6] Build report...", flush=True)
    report = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "purpose": (
            "Estimate target-blind public outcome survival on ordinary markets, stress-test it on untouched special markets, "
            "then ask whether Target Maker repair behavior is better organized by this survival estimate than raw Predict midpoint."
        ),
        "modelTarget": "P(UP wins); heavy-side survival is P(UP) for UP-heavy and 1-P(UP) for DOWN-heavy.",
        "leakageBoundary": (
            "No Target Maker size, Taker action, future Target event, or special-regime row enters survival-model fitting. "
            "Target Maker-heavy side is used only after scoring to orient P(UP) into P(current heavy side wins)."
        ),
        "featureSets": FEATURE_SETS,
        "baseline": "raw target-blind Predict midpoint",
        "fitPolicy": {
            "ordinaryOnly": True,
            "chronologicalMarketWalkForward": True,
            "fitStrideSeconds": stride,
            "specialNeverUsedForFitOrCalibration": True,
            "metricsPriority": "log-loss and Brier for probability quality; ROC AUC is secondary ranking evidence",
        },
        "coverage": {
            "publicResearch": public_meta,
            "cohortMarkets": len(cohort_ids),
            "settledCohortMarkets": len(settled_cohort),
            "settlementCoverage": len(settled_cohort) / len(cohort_ids) if cohort_ids else None,
            "signalArchive": signal_meta,
            "ordinaryMarkets": int(ordinary_all["market_id"].nunique()),
            "ordinaryRows": int(len(ordinary_all)),
            "ordinaryFitRows": int(len(ordinary_fit)),
            "specialMarkets": int(special["market_id"].nunique()),
            "specialRows": int(len(special)),
            "riskRows": len(risk_rows),
        },
        "models": feature_reports,
        "repairAudit": repair_audit,
        "outputs": {
            "scoresCsv": str(args.scores_csv.expanduser().resolve()),
        },
        "interpretationGuardrails": [
            "Special markets are an untouched stress audit, not a tuning set.",
            "Repeated one-second rows from the same market are serially correlated; repair audit therefore reports market-blocked Brier and repair rates.",
            "A better survival probability does not by itself prove the Target uses the same model; it is only a public-state proxy for the risk the wallet may be responding to.",
            "Repair hazard need not be monotonic in loss probability because complementary-outcome insurance becomes more expensive as the heavy side becomes less likely to win.",
            "No probability threshold discovered in this report may be promoted without a new ordinary holdout.",
        ],
    }
    _write_json(args.report, report)

    print("[6/6] Done", flush=True)
    print(f"Report: {args.report.expanduser().resolve()}", flush=True)
    print(f"Scores: {args.scores_csv.expanduser().resolve()}", flush=True)
    print("No survival cutoff or repair rule was promoted.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
