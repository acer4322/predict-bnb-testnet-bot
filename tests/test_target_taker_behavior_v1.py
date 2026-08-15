from __future__ import annotations

import csv
import importlib.util
import sqlite3
from pathlib import Path

from predict_bot.target_maker_taker_link import TAKER_COHORT
from predict_bot.target_taker_behavior_v1 import (
    SIDE_MODEL_FEATURES,
    build_target_taker_behavior_dataset,
)


def _load_trainer():
    path = Path(__file__).resolve().parents[1] / "tools" / "train_target_taker_behavior_v1.py"
    spec = importlib.util.spec_from_file_location("target_taker_behavior_v1_trainer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_direct_taker_dataset_excludes_whole_event_second_and_side_leakage(tmp_path):
    shadow_path = tmp_path / "shadow.db"
    db = sqlite3.connect(shadow_path)
    db.execute(
        """CREATE TABLE wallet_target_taker_mirror_meta (
            cohort TEXT PRIMARY KEY, deployed_at_ms INTEGER, excluded_market_id INTEGER, policy_json TEXT
        )"""
    )
    db.execute(
        "INSERT INTO wallet_target_taker_mirror_meta VALUES (?,?,?,?)",
        (TAKER_COHORT, 0, None, "{}"),
    )
    db.execute(
        """CREATE TABLE wallet_target_taker_mirror_parents (
            cohort TEXT, parent_id TEXT, market_id INTEGER, order_hash TEXT, side TEXT,
            target_event_ms INTEGER, target_last_event_ms INTEGER, detected_at_ms INTEGER,
            detection_lag_ms INTEGER, target_average_price REAL,
            target_shares_at_detection REAL, target_latest_shares REAL, target_fill_legs INTEGER
        )"""
    )
    db.executemany(
        "INSERT INTO wallet_target_taker_mirror_parents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (TAKER_COHORT, "t1", 101, "o1", "UP", 12000, 12000, 12100, 100, 0.62, 8.0, 20.0, 2),
            (TAKER_COHORT, "t2", 101, "o2", "DOWN", 13000, 13000, 13100, 100, 0.39, 7.0, 14.0, 1),
        ],
    )
    db.commit()
    db.close()

    signal_path = tmp_path / "signals.db"
    db = sqlite3.connect(signal_path)
    db.execute("CREATE TABLE wallet_taker_signal_meta (key TEXT PRIMARY KEY,value TEXT)")
    db.execute("INSERT INTO wallet_taker_signal_meta VALUES ('deployed_at_ms','0')")
    db.execute(
        """CREATE TABLE wallet_taker_signal_snapshots (
            timestamp_ns INTEGER PRIMARY KEY, sampled_at_ms INTEGER, market_id INTEGER,
            seconds_left REAL, strike_price REAL,
            predict_up_bid REAL,predict_up_ask REAL,predict_up_mid REAL,
            predict_down_bid REAL,predict_down_ask REAL,predict_down_mid REAL,
            spot_price REAL,spot_microprice REAL,spot_queue_imbalance REAL,
            spot_taker_imbalance_250ms REAL,spot_taker_imbalance_1s REAL,
            spot_return_250ms_bps REAL,spot_return_1s_bps REAL,spot_return_3s_bps REAL,spot_return_5s_bps REAL,
            futures_price REAL,futures_microprice REAL,futures_queue_imbalance REAL,
            futures_taker_imbalance_250ms REAL,futures_taker_imbalance_1s REAL,
            futures_return_250ms_bps REAL,futures_return_1s_bps REAL,futures_return_3s_bps REAL,futures_return_5s_bps REAL,
            perp_spot_basis_bps REAL,spot_minus_strike_bps REAL,chainlink_price REAL,
            chainlink_minus_strike_bps REAL,spot_minus_chainlink_bps REAL,direction_score REAL
        )"""
    )

    def insert(sampled_at_ms: int, up_ask: float, direction: float):
        up_bid = up_ask - 0.01
        down_ask = 1.01 - up_ask
        down_bid = down_ask - 0.01
        values = (
            sampled_at_ms * 1_000_000, sampled_at_ms, 101, 200.0, 100000.0,
            up_bid, up_ask, (up_bid + up_ask) / 2,
            down_bid, down_ask, (down_bid + down_ask) / 2,
            100001.0, 100001.0, 0.2, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5,
            100002.0, 100002.0, 0.25, 0.12, 0.18, 0.25, 0.35, 0.45, 0.55,
            0.1, 0.2, 100000.5, 0.1, 0.1, direction,
        )
        db.execute(
            "INSERT INTO wallet_taker_signal_snapshots VALUES (" + ",".join("?" for _ in values) + ")",
            values,
        )

    insert(11990, 0.55, 0.4)   # legal strict-pre snapshot for t1
    insert(12010, 0.91, -0.9)  # forbidden: same reported event second for t1
    insert(12990, 0.61, -0.3)  # legal strict-pre snapshot for t2
    db.commit()
    db.close()

    output = tmp_path / "taker.csv"
    meta = tmp_path / "taker.meta.json"
    report = build_target_taker_behavior_dataset(
        shadow_db_path=shadow_path,
        signal_db_path=signal_path,
        output_path=output,
        meta_output_path=meta,
    )
    with output.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert report["rows"] == 2
    assert report["pairingRate"] == 1.0
    first = rows[0]
    assert int(first["event_bucket_start_ms"]) == 12000
    assert int(first["signal_sampled_at_ms"]) == 11990
    assert float(first["predict_up_ask"]) == 0.55
    assert int(first["label_side_up"]) == 1
    assert float(first["chosen_predict_ask"]) == 0.55
    assert abs(float(first["chosen_price_slippage_vs_pre_ask"]) - 0.07) < 1e-9
    assert float(first["target_log1p_latest_shares"]) > 0

    assert all(not name.startswith("side_aligned_") for name in SIDE_MODEL_FEATURES)
    assert "label_side_up" not in SIDE_MODEL_FEATURES
    assert all(not name.startswith("chosen_") for name in SIDE_MODEL_FEATURES)


def test_taker_behavior_ebm_classifier_and_regressor_smoke():
    trainer = _load_trainer()
    deps = trainer._imports()
    pd = deps["pd"]
    np = deps["np"]

    rows = 180
    direction = np.linspace(-1.0, 1.0, rows)
    seconds = np.tile(np.array([30.0, 120.0, 240.0]), rows // 3)
    X_side = pd.DataFrame({"direction_score": direction, "seconds_left": seconds})
    y_side = pd.Series((direction > 0).astype(int))
    classifier = trainer._fit_classifier(
        deps, X_side, y_side, interactions=1, max_rounds=200, outer_bags=2, seed=7
    )
    side_probability = classifier.predict_proba(X_side)[:, 1]
    metrics = trainer._classification_metrics(deps, y_side, side_probability)
    assert metrics["rocAuc"] is not None and metrics["rocAuc"] > 0.9

    X_size = pd.DataFrame(
        {
            "chosen_predict_ask": np.linspace(0.2, 0.8, rows),
            "seconds_left": seconds,
        }
    )
    y_size = pd.Series(np.log1p(5.0 + 20.0 * X_size["chosen_predict_ask"].to_numpy()))
    regressor = trainer._fit_regressor(
        deps, X_size, y_size, interactions=1, max_rounds=200, outer_bags=2, seed=8
    )
    prediction = regressor.predict(X_size)
    assert deps["mean_absolute_error"](y_size, prediction) < 0.25

    folds = trainer._walk_forward_folds(
        list(range(30)), min_train_markets=20, test_markets=5, max_folds=2
    )
    assert len(folds) == 2
    assert set(folds[0]["trainMarkets"]).isdisjoint(folds[0]["testMarkets"])
