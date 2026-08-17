from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import pytest

from predict_bot.target_maker_ebm_dataset import OUTPUT_COLUMNS as V1_OUTPUT_COLUMNS, SIGNAL_COLUMNS
from predict_bot.target_maker_ebm_v3_dataset import build_v3_dataset


def _write_v1(path: Path) -> None:
    row = {column: "" for column in V1_OUTPUT_COLUMNS}
    row.update({
        "dataset_version": "V1",
        "decision_point": "AFTER_PARENT_FILL_BEFORE_NEXT_PARENT",
        "parent_id": "1:abc:UP:0.5",
        "market_id": 1,
        "order_hash": "abc",
        "target_side": "UP",
        "first_target_ms": 1000,
        "last_target_ms": 1500,
        "post_action": "REPRICE_1_3_TICKS_CONFIRMED_NEXT_PARENT",
        "post_action_delay_ms": 400,
        "post_action_native_price": 0.52,
        "target_price": 0.50,
        "native_price": 0.50,
        "target_fill_count": 3,
        "target_filled_shares": 18.0,
        "observed_filled_near_18": 1,
        "target_side_is_up": 1,
        "seconds_left": 120.0,
        "prior_maker_delta_shares": 18.0,
        "prior_maker_imbalance_ratio": 0.2,
        "side_aligned_prior_delta_shares": 18.0,
    })
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=V1_OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerow(row)


def _maker_db(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE maker_book_inference_v21_parent_lifecycles (
            parent_id TEXT PRIMARY KEY,
            native_book_side TEXT NOT NULL
        );
        CREATE TABLE maker_book_inference_target_events (
            leg_id TEXT PRIMARY KEY,
            order_hash TEXT,
            market_id INTEGER NOT NULL,
            target_event_ms INTEGER NOT NULL,
            side TEXT NOT NULL,
            target_price REAL NOT NULL,
            target_shares REAL NOT NULL,
            status TEXT NOT NULL
        );
        """
    )
    db.execute("INSERT INTO maker_book_inference_v21_parent_lifecycles VALUES (?,?)", ("1:abc:UP:0.5", "BID"))
    db.executemany(
        "INSERT INTO maker_book_inference_target_events VALUES (?,?,?,?,?,?,?,?)",
        [
            ("a", "abc", 1, 1000, "UP", 0.50, 6.0, "MATCHED"),
            ("b", "abc", 1, 1300, "UP", 0.50, 6.0, "MATCHED"),
            ("c", "abc", 1, 1500, "UP", 0.50, 6.0, "MATCHED"),
            ("future", "abc", 1, 1700, "UP", 0.50, 99.0, "MATCHED"),
        ],
    )
    db.commit()
    db.close()


def _signal_db(path: Path) -> None:
    db = sqlite3.connect(path)
    typed = []
    for column in SIGNAL_COLUMNS:
        if column == "sampled_at_ms":
            typed.append(f"{column} INTEGER NOT NULL")
        else:
            typed.append(f"{column} REAL")
    db.execute(
        f"CREATE TABLE wallet_taker_signal_snapshots (timestamp_ns INTEGER PRIMARY KEY, market_id INTEGER, {','.join(typed)})"
    )
    for sampled, spot, futures, up_mid, queue in [
        (500, 100.0, 100.0, 0.45, -0.4),
        (1000, 101.0, 101.5, 0.47, -0.2),
        (1250, 102.0, 102.0, 0.48, 0.0),
        (1500, 103.0, 104.0, 0.50, 0.3),
    ]:
        values = {column: None for column in SIGNAL_COLUMNS}
        values.update({
            "sampled_at_ms": sampled,
            "predict_up_bid": up_mid - 0.01,
            "predict_up_ask": up_mid + 0.01,
            "predict_up_mid": up_mid,
            "predict_down_bid": 1.0 - up_mid - 0.01,
            "predict_down_ask": 1.0 - up_mid + 0.01,
            "predict_down_mid": 1.0 - up_mid,
            "spot_price": spot,
            "futures_price": futures,
            "spot_queue_imbalance": queue,
            "futures_queue_imbalance": queue / 2,
            "spot_taker_imbalance_250ms": queue / 3,
            "futures_taker_imbalance_250ms": queue / 4,
            "direction_score": queue,
        })
        columns = ["timestamp_ns", "market_id", *SIGNAL_COLUMNS]
        payload = [sampled * 1_000_000, 1, *(values[column] for column in SIGNAL_COLUMNS)]
        db.execute(
            f"INSERT INTO wallet_taker_signal_snapshots ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            payload,
        )
    db.commit()
    db.close()


def test_v3_dataset_is_strict_asof_and_labels_signed_reprice(tmp_path: Path) -> None:
    v1 = tmp_path / "v1.csv"
    maker = tmp_path / "maker.db"
    signal = tmp_path / "signal.db"
    output = tmp_path / "v3.csv"
    meta = tmp_path / "v3.meta.json"
    _write_v1(v1)
    _maker_db(maker)
    _signal_db(signal)

    summary = build_v3_dataset(
        v1_dataset_path=v1,
        maker_db_path=maker,
        signal_db_path=signal,
        output_path=output,
        meta_output_path=meta,
        trajectory_snapshot_max_age_ms=100,
    )
    assert summary["rows"] == 1
    with output.open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))

    assert int(row["path_fill_leg_count"]) == 3
    assert float(row["path_fill_duration_ms"]) == pytest.approx(500.0)
    assert int(row["path_fill_burst_count_250ms"]) == 2
    assert float(row["path_shares_last_250ms"]) == pytest.approx(12.0)
    assert float(row["path_shares_last_500ms"]) == pytest.approx(18.0)
    # The synthetic future target leg at 1700 must not leak into the path.
    assert float(row["path_peak_leg_shares"]) == pytest.approx(6.0)

    assert float(row["traj_side_predict_mid_delta_250ms"]) == pytest.approx(0.02)
    assert float(row["traj_spot_price_return_500ms_bps"]) == pytest.approx((103 / 101 - 1) * 10_000)

    assert int(row["label_continue"]) == 1
    assert int(row["label_refill_given_continue"]) == 0
    assert int(row["label_reprice_toward_touch"]) == 1
    assert float(row["label_reprice_ticks_abs"]) == pytest.approx(2.0)
    assert float(row["label_reprice_signed_ticks_toward"]) == pytest.approx(2.0)
    assert int(row["label_continue_delay_le_250ms"]) == 0
    assert int(row["label_continue_delay_le_500ms"]) == 1
