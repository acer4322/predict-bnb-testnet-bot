from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import pytest

from predict_bot.target_maker_ebm_dataset import build_dataset


def _maker_db(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE maker_book_inference_v21_parent_lifecycles (
            parent_id TEXT PRIMARY KEY,
            market_id INTEGER NOT NULL,
            order_hash TEXT,
            target_side TEXT NOT NULL,
            native_book_side TEXT NOT NULL,
            target_price REAL NOT NULL,
            native_price REAL NOT NULL,
            first_target_ms INTEGER NOT NULL,
            last_target_ms INTEGER NOT NULL,
            target_fill_count INTEGER NOT NULL,
            target_filled_shares REAL NOT NULL,
            expected_parent_shares REAL NOT NULL,
            allocated_fill_shares REAL NOT NULL,
            fill_allocation_coverage REAL NOT NULL,
            placement_allocated_shares REAL NOT NULL,
            placement_coverage REAL NOT NULL,
            placement_first_ms INTEGER,
            placement_last_ms INTEGER,
            resting_ms INTEGER,
            post_action TEXT NOT NULL,
            post_action_delay_ms INTEGER,
            post_action_native_price REAL,
            multi_fill_parent INTEGER NOT NULL,
            observed_filled_near_18 INTEGER NOT NULL,
            placement_supports_18 INTEGER NOT NULL,
            confidence REAL NOT NULL
        );
        """
    )
    db.executemany(
        "INSERT INTO maker_book_inference_v21_parent_lifecycles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                "1:a:UP:0.60", 1, "a", "UP", "BID", 0.60, 0.60,
                1900, 2000, 1, 18.0, 18.0, 18.0, 1.0, 18.0, 1.0,
                1700, 1800, 200, "REPRICE_1_3_TICKS_CONFIRMED_NEXT_PARENT", 500, 0.61,
                0, 1, 1, 0.90,
            ),
            (
                "1:b:DOWN:0.40", 1, "b", "DOWN", "ASK", 0.40, 0.60,
                2900, 3000, 2, 18.0, 18.0, 18.0, 1.0, 18.0, 1.0,
                2600, 2700, 300, "SAME_PRICE_REFILL_CONFIRMED_NEXT_PARENT", 400, 0.60,
                1, 1, 1, 0.92,
            ),
        ],
    )
    db.commit()
    db.close()


def _signal_db(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE wallet_taker_signal_snapshots (
            timestamp_ns INTEGER PRIMARY KEY,
            sampled_at_ms INTEGER NOT NULL,
            market_id INTEGER,
            seconds_left REAL,
            strike_price REAL,
            predict_up_bid REAL,
            predict_up_ask REAL,
            predict_up_mid REAL,
            predict_down_bid REAL,
            predict_down_ask REAL,
            predict_down_mid REAL,
            spot_price REAL,
            spot_microprice REAL,
            spot_queue_imbalance REAL,
            spot_taker_imbalance_250ms REAL,
            spot_taker_imbalance_1s REAL,
            spot_return_250ms_bps REAL,
            spot_return_1s_bps REAL,
            spot_return_3s_bps REAL,
            spot_return_5s_bps REAL,
            futures_price REAL,
            futures_microprice REAL,
            futures_queue_imbalance REAL,
            futures_taker_imbalance_250ms REAL,
            futures_taker_imbalance_1s REAL,
            futures_return_250ms_bps REAL,
            futures_return_1s_bps REAL,
            futures_return_3s_bps REAL,
            futures_return_5s_bps REAL,
            perp_spot_basis_bps REAL,
            spot_minus_strike_bps REAL,
            chainlink_minus_strike_bps REAL,
            spot_minus_chainlink_bps REAL,
            direction_score REAL
        );
        """
    )
    rows = []
    for sampled_at_ms, direction_score in [(1900, 0.10), (2050, 0.90), (2900, -0.25)]:
        rows.append(
            (
                sampled_at_ms * 1_000_000, sampled_at_ms, 1, 100.0, 63000.0,
                0.59, 0.60, 0.595, 0.40, 0.41, 0.405,
                63010.0, 63010.1, 0.2, 0.1, 0.3, 0.0, 0.2, 0.4, 0.5,
                63008.0, 63008.1, -0.3, -0.1, -0.2, 0.0, -0.1, -0.2, -0.3,
                -0.5, 1.5, -1.0, 2.5, direction_score,
            )
        )
    db.executemany(
        "INSERT INTO wallet_taker_signal_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    db.commit()
    db.close()


def test_build_dataset_uses_strict_asof_signal_and_prior_inventory(tmp_path: Path) -> None:
    maker_db = tmp_path / "maker.db"
    signal_db = tmp_path / "signal.db"
    output = tmp_path / "dataset.csv"
    meta = tmp_path / "dataset.meta.json"
    _maker_db(maker_db)
    _signal_db(signal_db)

    summary = build_dataset(
        maker_db_path=maker_db,
        signal_db_path=signal_db,
        output_path=output,
        meta_output_path=meta,
        max_signal_age_ms=500,
    )

    assert summary["rows"] == 2
    assert summary["markets"] == 1
    with output.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    first, second = rows
    assert int(first["signal_sampled_at_ms"]) == 1900
    assert float(first["direction_score"]) == pytest.approx(0.10)
    assert int(first["label_reprice_1_3_ticks"]) == 1
    assert int(first["label_same_price_refill"]) == 0
    assert float(first["side_aligned_direction_score"]) == pytest.approx(0.10)
    assert first["prior_maker_paired_coverage"] == ""

    assert int(second["signal_sampled_at_ms"]) == 2900
    assert int(second["label_reprice_1_3_ticks"]) == 0
    assert int(second["label_same_price_refill"]) == 1
    assert float(second["prior_maker_up_shares"]) == pytest.approx(18.0)
    assert float(second["prior_maker_down_shares"]) == pytest.approx(0.0)
    assert float(second["prior_maker_delta_shares"]) == pytest.approx(18.0)
    assert float(second["side_aligned_prior_delta_shares"]) == pytest.approx(-18.0)
    assert float(second["side_aligned_direction_score"]) == pytest.approx(0.25)
