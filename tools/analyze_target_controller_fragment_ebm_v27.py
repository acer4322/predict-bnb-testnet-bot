from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
REPORT_VERSION = "TARGET_CONTROLLER_FRAGMENT_EBM_V27"
STRESS = "STRESS_2026_08_16"
DEFAULT_STATES = ROOT / "data" / "research" / "target_controller_hazard_v21_states.csv"
DEFAULT_DB = ROOT / "data" / "microstructure.db"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_controller_fragment_ebm_v27_report.json"
DEFAULT_ROWS = ROOT / "data" / "research" / "target_controller_fragment_ebm_v27_training_rows.csv"
DEFAULT_LOCAL = ROOT / "data" / "research" / "target_controller_fragment_ebm_v27_local_explanations.csv"
DEFAULT_START = "2026-08-16T05:40:05.009737+08:00"
DEFAULT_END = "2026-08-16T06:00:02.210840+08:00"
FRESHNESS_MS = 750
PREDICTION_FRESHNESS_MS = 2000
HISTORY_MS = (1000, 3000, 5000)
SUPPORTED_FUTURE_HORIZONS = (1, 3, 5)
RANDOM_STATE = 20260818

STATE_CORE = [
    "seconds_left",
    "risk_deficit", "abs_payoff_gap", "maker_abs_payoff_gap", "worst_case_pnl",
    "payoff_gap", "maker_payoff_gap",
    "risk_growth_1s", "risk_growth_3s", "risk_growth_5s",
    "gap_growth_1s", "gap_growth_3s", "gap_growth_5s",
    "maker_gap_growth_1s", "maker_gap_growth_3s", "maker_gap_growth_5s",
]
LIFECYCLE_DESCRIPTIVE = [
    "prior_maker_parents", "prior_taker_parents",
    "time_since_last_maker_ms", "time_since_last_taker_ms", "maker_streak_age_ms",
    "maker_parents_since_last_taker", "maker_shares_since_last_taker",
    "maker_notional_since_last_taker", "max_risk_since_taker_reset",
    "max_gap_since_taker_reset", "same_payoff_gap_sign_age_ms",
    "same_maker_gap_sign_age_ms",
]
CURRENT_PUBLIC = [
    "micro_spot_qi", "micro_futures_qi", "micro_book_pressure",
    "micro_qi_disagreement_abs", "micro_direction_score", "micro_abs_direction_score",
    "micro_spot_taker_250ms", "micro_futures_taker_250ms",
    "micro_spot_taker_1s", "micro_futures_taker_1s",
    "micro_prediction_up_mid", "micro_prediction_distance_05", "micro_prediction_event_age_ms",
]
HISTORY_PUBLIC: list[str] = []
for _h in HISTORY_MS:
    _s = _h // 1000
    HISTORY_PUBLIC.extend([
        f"micro_book_pressure_mean_{_s}s",
        f"micro_book_pressure_abs_mean_{_s}s",
        f"micro_book_pressure_mean_abs_{_s}s",
        f"micro_book_pressure_sign_consistency_{_s}s",
        f"micro_spot_return_{_s}s_bps", f"micro_futures_return_{_s}s_bps",
        f"micro_spot_range_{_s}s_bps", f"micro_futures_range_{_s}s_bps",
        f"micro_prediction_delta_{_s}s", f"micro_prediction_range_{_s}s",
        f"micro_prediction_pressure_aligned_delta_{_s}s",
    ])
PUBLIC_FEATURES = CURRENT_PUBLIC + HISTORY_PUBLIC
FEATURE_SPECS = {
    "CORE_STATE_PUBLIC": STATE_CORE + PUBLIC_FEATURES,
    "FULL_DESCRIPTIVE": STATE_CORE + PUBLIC_FEATURES + LIFECYCLE_DESCRIPTIVE,
}
TASK_LABEL_PREFIX = {
    "ACTION": "taker_within_{}s",
    "ADD": "add_within_{}s",
    "REPAIR": "repair_within_{}s",
}
SNAPSHOT_FIELDS = (
    "timestamp_ns", "market_id", "spot_price", "futures_price",
    "spot_queue_imbalance", "futures_queue_imbalance",
    "spot_taker_imbalance_250ms", "futures_taker_imbalance_250ms",
    "spot_taker_imbalance_1s", "futures_taker_imbalance_1s",
    "prediction_up_mid", "direction_score",
)


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _iso_ms(value: str) -> int:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return int(dt.timestamp() * 1000)


def _format_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat(timespec="milliseconds")


def _mean(values: Iterable[float | None]) -> float | None:
    xs = [x for x in values if x is not None and math.isfinite(x)]
    return statistics.fmean(xs) if xs else None


def _load_states(path: Path, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    required = {
        "market_id", "segment_id", "regime", "sample_ms",
        *STATE_CORE, *LIFECYCLE_DESCRIPTIVE,
        *[TASK_LABEL_PREFIX[t].format(h) for t in TASK_LABEL_PREFIX for h in SUPPORTED_FUTURE_HORIZONS],
    }
    rows: list[dict[str, Any]] = []
    with path.expanduser().resolve().open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise RuntimeError("V2.1 states CSV missing columns: " + ", ".join(missing))
        for raw in reader:
            if str(raw.get("regime") or "") != STRESS:
                continue
            sample_ms = _int(raw.get("sample_ms"))
            if not (start_ms <= sample_ms <= end_ms):
                continue
            row = dict(raw)
            row["market_id"] = _int(row.get("market_id"))
            row["segment_id"] = _int(row.get("segment_id"))
            row["sample_ms"] = sample_ms
            for field in STATE_CORE + LIFECYCLE_DESCRIPTIVE:
                row[field] = _num(row.get(field))
            for task in TASK_LABEL_PREFIX:
                for h in SUPPORTED_FUTURE_HORIZONS:
                    label = TASK_LABEL_PREFIX[task].format(h)
                    row[label] = _int(row.get(label))
            rows.append(row)
    rows.sort(key=lambda r: (r["sample_ms"], r["market_id"], r["segment_id"]))
    return rows


def _open_ro(path: Path) -> sqlite3.Connection:
    uri = path.expanduser().resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _load_micro(path: Path, start_ms: int, end_ms: int) -> tuple[list[dict[str, Any]], dict[int, list[int]]]:
    lower_ns = (start_ms - max(HISTORY_MS) - 1500) * 1_000_000
    upper_ns = end_ms * 1_000_000
    cols = ", ".join(SNAPSHOT_FIELDS)
    with _open_ro(path) as conn:
        snapshots = [dict(r) for r in conn.execute(
            f"SELECT {cols} FROM microstructure_snapshots "
            "WHERE timestamp_ns >= ? AND timestamp_ns <= ? ORDER BY timestamp_ns",
            (lower_ns, upper_ns),
        )]
        prediction_events: dict[int, list[int]] = defaultdict(list)
        for r in conn.execute(
            "SELECT received_wall_ns, market_id FROM microstructure_events "
            "WHERE received_wall_ns >= ? AND received_wall_ns <= ? "
            "AND source = 'prediction' AND stream = 'orderbook' "
            "AND COALESCE(feature_eligible, 1) = 1 ORDER BY received_wall_ns",
            (lower_ns, upper_ns),
        ):
            prediction_events[_int(r["market_id"])].append(_int(r["received_wall_ns"]))
    return snapshots, dict(prediction_events)


def strict_past_snapshot(
    snapshots: list[dict[str, Any]], timestamps: list[int], sample_ms: int, freshness_ms: int = FRESHNESS_MS
) -> tuple[dict[str, Any] | None, str]:
    sample_ns = int(sample_ms) * 1_000_000
    pos = bisect.bisect_left(timestamps, sample_ns) - 1
    if pos < 0:
        return None, "NO_STRICT_PAST"
    age_ms = (sample_ns - timestamps[pos]) / 1_000_000.0
    if age_ms > freshness_ms:
        return None, "STALE"
    out = dict(snapshots[pos])
    out["snapshot_age_ms"] = age_ms
    return out, "OK"


def _prediction_age_ms(prediction_events: dict[int, list[int]], market_id: int, sample_ms: int) -> float | None:
    xs = prediction_events.get(int(market_id), [])
    if not xs:
        return None
    sample_ns = int(sample_ms) * 1_000_000
    pos = bisect.bisect_left(xs, sample_ns) - 1
    if pos < 0:
        return None
    return (sample_ns - xs[pos]) / 1_000_000.0


def _price_stats(rows: list[dict[str, Any]], field: str) -> tuple[float | None, float | None]:
    vals = [_num(r.get(field)) for r in rows]
    xs = [x for x in vals if x is not None and x > 0]
    if len(xs) < 2:
        return None, None
    ret = (xs[-1] / xs[0] - 1.0) * 10000.0
    rng = (max(xs) - min(xs)) / xs[0] * 10000.0
    return ret, rng


def _history_features(
    snapshots: list[dict[str, Any]], timestamps: list[int], sample_ms: int, micro_market_id: int, history_ms: int
) -> dict[str, float | None]:
    sample_ns = int(sample_ms) * 1_000_000
    start_ns = (int(sample_ms) - int(history_ms)) * 1_000_000
    lo = bisect.bisect_left(timestamps, start_ns)
    hi = bisect.bisect_left(timestamps, sample_ns)
    rows = [r for r in snapshots[lo:hi] if _int(r.get("market_id")) == int(micro_market_id)]
    suffix = f"{history_ms // 1000}s"
    names = {
        f"micro_book_pressure_mean_{suffix}", f"micro_book_pressure_abs_mean_{suffix}",
        f"micro_book_pressure_mean_abs_{suffix}", f"micro_book_pressure_sign_consistency_{suffix}",
        f"micro_spot_return_{suffix}_bps", f"micro_futures_return_{suffix}_bps",
        f"micro_spot_range_{suffix}_bps", f"micro_futures_range_{suffix}_bps",
        f"micro_prediction_delta_{suffix}", f"micro_prediction_range_{suffix}",
        f"micro_prediction_pressure_aligned_delta_{suffix}",
    }
    blank = {name: None for name in names}
    if len(rows) < 2:
        return blank
    first_ns, last_ns = _int(rows[0].get("timestamp_ns")), _int(rows[-1].get("timestamp_ns"))
    coverage = max(0.0, (last_ns - first_ns) / (history_ms * 1_000_000.0))
    if coverage < 0.65:
        return blank

    pressures: list[float] = []
    for row in rows:
        p = _mean([_num(row.get("spot_queue_imbalance")), _num(row.get("futures_queue_imbalance"))])
        if p is not None:
            pressures.append(p)
    pressure_mean = statistics.fmean(pressures) if pressures else None
    pressure_abs_mean = abs(pressure_mean) if pressure_mean is not None else None
    pressure_mean_abs = statistics.fmean(abs(x) for x in pressures) if pressures else None
    signs = [1 if x > 0 else -1 if x < 0 else 0 for x in pressures]
    consistency = abs(statistics.fmean(signs)) if signs else None

    spot_ret, spot_range = _price_stats(rows, "spot_price")
    fut_ret, fut_range = _price_stats(rows, "futures_price")
    pred = [_num(r.get("prediction_up_mid")) for r in rows]
    pred = [x for x in pred if x is not None]
    pred_delta = pred[-1] - pred[0] if len(pred) >= 2 else None
    pred_range = max(pred) - min(pred) if len(pred) >= 2 else None
    aligned_delta = None
    if pressure_mean is not None and pred_delta is not None:
        aligned_delta = pred_delta * (1 if pressure_mean > 0 else -1 if pressure_mean < 0 else 0)

    return {
        f"micro_book_pressure_mean_{suffix}": pressure_mean,
        f"micro_book_pressure_abs_mean_{suffix}": pressure_abs_mean,
        f"micro_book_pressure_mean_abs_{suffix}": pressure_mean_abs,
        f"micro_book_pressure_sign_consistency_{suffix}": consistency,
        f"micro_spot_return_{suffix}_bps": spot_ret,
        f"micro_futures_return_{suffix}_bps": fut_ret,
        f"micro_spot_range_{suffix}_bps": spot_range,
        f"micro_futures_range_{suffix}_bps": fut_range,
        f"micro_prediction_delta_{suffix}": pred_delta,
        f"micro_prediction_range_{suffix}": pred_range,
        f"micro_prediction_pressure_aligned_delta_{suffix}": aligned_delta,
    }


def build_training_rows(
    states: list[dict[str, Any]], snapshots: list[dict[str, Any]], prediction_events: dict[int, list[int]]
) -> tuple[list[dict[str, Any]], Counter]:
    timestamps = [_int(r.get("timestamp_ns")) for r in snapshots]
    out: list[dict[str, Any]] = []
    audit: Counter = Counter()
    for state in states:
        snap, status = strict_past_snapshot(snapshots, timestamps, state["sample_ms"])
        audit[status] += 1
        if snap is None:
            continue
        row = dict(state)
        row["join_status"] = status
        row["micro_market_id"] = _int(snap.get("market_id"))
        row["snapshot_age_ms"] = _num(snap.get("snapshot_age_ms"))
        spot_qi = _num(snap.get("spot_queue_imbalance"))
        fut_qi = _num(snap.get("futures_queue_imbalance"))
        pressure = _mean([spot_qi, fut_qi])
        row.update(
            micro_spot_qi=spot_qi,
            micro_futures_qi=fut_qi,
            micro_book_pressure=pressure,
            micro_qi_disagreement_abs=(abs(spot_qi - fut_qi) if spot_qi is not None and fut_qi is not None else None),
            micro_direction_score=_num(snap.get("direction_score")),
            micro_abs_direction_score=(abs(_num(snap.get("direction_score"))) if _num(snap.get("direction_score")) is not None else None),
            micro_spot_taker_250ms=_num(snap.get("spot_taker_imbalance_250ms")),
            micro_futures_taker_250ms=_num(snap.get("futures_taker_imbalance_250ms")),
            micro_spot_taker_1s=_num(snap.get("spot_taker_imbalance_1s")),
            micro_futures_taker_1s=_num(snap.get("futures_taker_imbalance_1s")),
        )
        pred_age = _prediction_age_ms(prediction_events, row["micro_market_id"], row["sample_ms"])
        row["micro_prediction_event_age_ms"] = pred_age
        pred_mid = _num(snap.get("prediction_up_mid"))
        if pred_age is None or pred_age > PREDICTION_FRESHNESS_MS:
            pred_mid = None
            audit["predictionStaleOrMissing"] += 1
        else:
            audit["predictionFresh"] += 1
        row["micro_prediction_up_mid"] = pred_mid
        row["micro_prediction_distance_05"] = abs(pred_mid - 0.5) if pred_mid is not None else None
        for history_ms in HISTORY_MS:
            row.update(_history_features(snapshots, timestamps, row["sample_ms"], row["micro_market_id"], history_ms))
        out.append(row)
    return out, audit


def feature_specs() -> dict[str, list[str]]:
    return {name: list(fields) for name, fields in FEATURE_SPECS.items()}


def build_leave_one_market_out(rows: list[dict[str, Any]]) -> list[tuple[int, list[int], list[int]]]:
    groups: dict[int, list[int]] = defaultdict(list)
    for idx, row in enumerate(rows):
        groups[int(row["market_id"])].append(idx)
    folds: list[tuple[int, list[int], list[int]]] = []
    all_idx = set(range(len(rows)))
    for market_id in sorted(groups):
        test = sorted(groups[market_id])
        train = sorted(all_idx - set(test))
        if train and test:
            folds.append((market_id, train, test))
    return folds


def _feature_family(term: str) -> str:
    families: set[str] = set()
    for name in STATE_CORE:
        if name in term:
            families.add("TIME" if name == "seconds_left" else "RISK_STATE")
    for name in PUBLIC_FEATURES:
        if name in term:
            families.add("PUBLIC_MARKET")
    for name in LIFECYCLE_DESCRIPTIVE:
        if name in term:
            families.add("LIFECYCLE")
    return "+".join(sorted(families)) if families else "OTHER"


def _safe_metric(y: list[int], p: list[float]) -> dict[str, float | None]:
    from sklearn.metrics import log_loss, roc_auc_score

    result: dict[str, float | None] = {"auc": None, "logLoss": None}
    if not y:
        return result
    try:
        result["logLoss"] = float(log_loss(y, p, labels=[0, 1]))
    except ValueError:
        pass
    if len(set(y)) == 2:
        result["auc"] = float(roc_auc_score(y, p))
    return result


def _matrix(rows: list[dict[str, Any]], features: list[str], indices: list[int]) -> tuple[Any, Any]:
    import numpy as np

    x = np.array([
        [float(rows[i][f]) if rows[i].get(f) is not None else np.nan for f in features]
        for i in indices
    ], dtype=float)
    return x


def _new_model(features: list[str]) -> Any:
    from interpret.glassbox import ExplainableBoostingClassifier

    return ExplainableBoostingClassifier(
        feature_names=features,
        interactions=8,
        max_bins=16,
        max_interaction_bins=16,
        learning_rate=0.05,
        max_rounds=300,
        outer_bags=4,
        inner_bags=0,
        min_samples_leaf=5,
        max_leaves=3,
        validation_size=0.15,
        n_jobs=1,
        random_state=RANDOM_STATE,
    )


def _term_summary(model: Any, top_n: int = 20) -> tuple[list[dict[str, Any]], dict[str, float]]:
    names = list(model.term_names_)
    values = [float(x) for x in model.term_importances()]
    rows = [
        {"term": name, "importance": imp, "family": _feature_family(name), "interaction": " & " in name}
        for name, imp in zip(names, values)
    ]
    rows.sort(key=lambda r: r["importance"], reverse=True)
    family = defaultdict(float)
    total = sum(r["importance"] for r in rows)
    for row in rows:
        family[row["family"]] += row["importance"]
    shares = {k: (v / total if total > 0 else 0.0) for k, v in sorted(family.items())}
    return rows[:top_n], shares


def _local_rows(
    model: Any, x: Any, source_rows: list[dict[str, Any]], indices: list[int], task: str, horizon: int, holdout: int
) -> list[dict[str, Any]]:
    probs = model.predict_proba(x)[:, 1]
    terms = None
    try:
        terms = model.eval_terms(x)
    except Exception:
        terms = None
    names = list(model.term_names_)
    out: list[dict[str, Any]] = []
    for j, idx in enumerate(indices):
        src = source_rows[idx]
        label_name = TASK_LABEL_PREFIX[task].format(horizon)
        row = {
            "task": task,
            "horizon_s": horizon,
            "holdout_target_market_id": holdout,
            "target_market_id": src["market_id"],
            "micro_market_id": src.get("micro_market_id"),
            "sample_ms": src["sample_ms"],
            "sample_utc": _format_ms(src["sample_ms"]),
            "label": src[label_name],
            "oof_probability": float(probs[j]),
            "next_taker_delay_ms": src.get("next_taker_delay_ms"),
            "next_taker_purpose": src.get("next_taker_purpose"),
            "next_taker_side": src.get("next_taker_side"),
        }
        if terms is not None:
            contrib = [(names[k], float(terms[j][k])) for k in range(len(names))]
            positive = sorted((x for x in contrib if x[1] > 0), key=lambda x: x[1], reverse=True)[:5]
            negative = sorted((x for x in contrib if x[1] < 0), key=lambda x: x[1])[:5]
            row["top_positive_terms"] = json.dumps(positive, ensure_ascii=False)
            row["top_negative_terms"] = json.dumps(negative, ensure_ascii=False)
        out.append(row)
    return out


def fit_one(
    rows: list[dict[str, Any]], features: list[str], task: str, horizon: int, spec_name: str,
    collect_local: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    label = TASK_LABEL_PREFIX[task].format(horizon)
    folds = build_leave_one_market_out(rows)
    fold_reports: list[dict[str, Any]] = []
    fold_top_terms: Counter = Counter()
    fold_importances: defaultdict[str, list[float]] = defaultdict(list)
    oof_y: list[int] = []
    oof_p: list[float] = []
    locals_out: list[dict[str, Any]] = []

    for holdout, train_idx, test_idx in folds:
        y_train = [int(rows[i][label]) for i in train_idx]
        y_test = [int(rows[i][label]) for i in test_idx]
        fold = {
            "holdoutTargetMarketId": holdout,
            "trainRows": len(train_idx), "testRows": len(test_idx),
            "trainPositives": sum(y_train), "testPositives": sum(y_test),
        }
        if len(set(y_train)) < 2:
            fold["status"] = "SKIP_SINGLE_CLASS_TRAIN"
            fold_reports.append(fold)
            continue
        x_train = _matrix(rows, features, train_idx)
        x_test = _matrix(rows, features, test_idx)
        model = _new_model(features)
        model.fit(x_train, y_train)
        p = [float(v) for v in model.predict_proba(x_test)[:, 1]]
        fold.update(status="OK", **_safe_metric(y_test, p))
        terms, _ = _term_summary(model, top_n=10)
        fold["topTerms"] = terms
        for rank, item in enumerate(terms, 1):
            fold_top_terms[item["term"]] += 1
            fold_importances[item["term"]].append(float(item["importance"]))
        fold_reports.append(fold)
        oof_y.extend(y_test)
        oof_p.extend(p)
        if collect_local:
            locals_out.extend(_local_rows(model, x_test, rows, test_idx, task, horizon, holdout))

    full_y = [int(r[label]) for r in rows]
    full_summary: dict[str, Any] = {"status": "SKIP_SINGLE_CLASS"}
    if len(set(full_y)) == 2:
        model = _new_model(features)
        model.fit(_matrix(rows, features, list(range(len(rows)))), full_y)
        terms, families = _term_summary(model, top_n=25)
        full_summary = {
            "status": "OK",
            "topTerms": terms,
            "topInteractions": [r for r in terms if r["interaction"]][:10],
            "familyImportanceShare": families,
        }

    stability = []
    for term, count in fold_top_terms.most_common(30):
        stability.append({
            "term": term,
            "top10FoldCount": count,
            "meanImportanceWhenTop10": statistics.fmean(fold_importances[term]),
            "family": _feature_family(term),
        })
    return {
        "task": task,
        "horizonS": horizon,
        "featureSpec": spec_name,
        "rows": len(rows),
        "positives": sum(full_y),
        "prevalence": sum(full_y) / len(full_y) if full_y else None,
        "folds": fold_reports,
        "oof": _safe_metric(oof_y, oof_p),
        "oofRows": len(oof_y),
        "stability": stability,
        "fullFitDescriptive": full_summary,
    }, locals_out


def _coverage(rows: list[dict[str, Any]], fields: list[str]) -> dict[str, float]:
    if not rows:
        return {}
    return {field: sum(r.get(field) is not None for r in rows) / len(rows) for field in fields}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        resolved.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with resolved.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _parse_horizons(text: str) -> list[int]:
    values = sorted({_int(x.strip()) for x in text.split(",") if x.strip()})
    invalid = [x for x in values if x not in SUPPORTED_FUTURE_HORIZONS]
    if invalid or not values:
        raise ValueError("--horizons must be a comma list drawn from 1,3,5")
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description="Fragment EBM anatomy for Target controller action/add/repair hazard.")
    parser.add_argument("--states", type=Path, default=DEFAULT_STATES)
    parser.add_argument("--micro-db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--horizons", default="3", help="Default 3 for laptop-friendly first pass; optionally 1,3,5.")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--rows", type=Path, default=DEFAULT_ROWS)
    parser.add_argument("--local", type=Path, default=DEFAULT_LOCAL)
    args = parser.parse_args()

    start_ms, end_ms = _iso_ms(args.start), _iso_ms(args.end)
    horizons = _parse_horizons(args.horizons)
    states = _load_states(args.states, start_ms, end_ms)
    snapshots, prediction_events = _load_micro(args.micro_db, start_ms, end_ms)
    rows, join_audit = build_training_rows(states, snapshots, prediction_events)
    if len(rows) < 50:
        raise RuntimeError(f"too few fresh fragment rows after strict-past join: {len(rows)}")

    specs = feature_specs()
    models: dict[str, Any] = {}
    local_rows: list[dict[str, Any]] = []
    for horizon in horizons:
        for task in ("ACTION", "ADD", "REPAIR"):
            for spec_name, features in specs.items():
                key = f"{task}_{horizon}S__{spec_name}"
                result, local = fit_one(
                    rows, features, task, horizon, spec_name,
                    collect_local=(horizon == 3 and spec_name == "CORE_STATE_PUBLIC"),
                )
                models[key] = result
                local_rows.extend(local)
                print(
                    key,
                    "n=", result["rows"],
                    "pos=", result["positives"],
                    "OOF AUC=", result["oof"].get("auc"),
                    "logloss=", result["oof"].get("logLoss"),
                )

    market_counts = Counter(int(r["market_id"]) for r in rows)
    report = {
        "version": REPORT_VERSION,
        "policy": {
            "window": {"start": args.start, "end": args.end},
            "strictPast": "micro snapshot timestamp < fixed-grid sample; equality excluded",
            "microFreshnessMs": FRESHNESS_MS,
            "predictionFreshnessGateMs": PREDICTION_FRESHNESS_MS,
            "validation": "leave-one-Target-market-out; no random split",
            "defaultMainHorizonS": 3,
            "requestedHorizonsS": horizons,
            "featureSpecs": {
                "CORE_STATE_PUBLIC": "current portfolio/risk geometry + strict-past public microstructure; excludes reset/since-last-Taker lifecycle controls",
                "FULL_DESCRIPTIVE": "CORE plus policy/lifecycle state; descriptive only because several fields are post-treatment",
            },
            "localExplanation": "OOF only: each second explained by a model that did not train on that Target market",
            "guardrails": [
                "Future labels and next_taker_* fields are never model features.",
                "Microstructure history never crosses the current microstructure market_id.",
                "Prediction mid is nulled unless a strict-past Prediction orderbook event is <=2s old.",
                "Full-data EBM term importance is descriptive; cross-market OOF metrics/stability are the validation evidence.",
                "No Echtgeld/live strategy changes.",
            ],
        },
        "source": {
            "states": str(args.states.expanduser().resolve()),
            "microDb": str(args.micro_db.expanduser().resolve()),
            "fixedGridRowsInWindow": len(states),
            "microSnapshotsLoaded": len(snapshots),
            "freshTrainingRows": len(rows),
            "joinAudit": dict(join_audit),
            "targetMarketRows": {str(k): v for k, v in sorted(market_counts.items())},
        },
        "featureCoverage": {
            name: _coverage(rows, fields) for name, fields in specs.items()
        },
        "models": models,
        "interpretation": {
            "desiredPattern": "ADD importance concentrated in PUBLIC_MARKET / public-risk interactions while REPAIR concentrates in RISK_STATE would support distinct controller families.",
            "warning": "EBM anatomy is associational and local to this special fragment; it does not identify Target's proprietary hidden score or prove causality.",
        },
    }

    args.report.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    args.report.expanduser().resolve().write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_csv(args.rows, rows)
    _write_csv(args.local, sorted(local_rows, key=lambda r: (r["sample_ms"], r["task"])))
    print("report:", args.report)
    print("rows:", args.rows)
    print("local OOF explanations:", args.local, "rows=", len(local_rows))


if __name__ == "__main__":
    main()
