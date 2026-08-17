from __future__ import annotations

import csv
import sqlite3

from predict_bot.target_maker_taker_link import TAKER_COHORT
from predict_bot.target_maker_taker_state_link_v3 import build_state_link_dataset


def test_state_link_v3_uses_post_fill_inventory_and_excludes_same_second(tmp_path):
    maker_dataset = tmp_path / "maker_v3.csv"
    with maker_dataset.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "parent_id",
                "market_id",
                "order_hash",
                "target_side",
                "native_book_side",
                "last_target_ms",
                "target_filled_shares",
                "prior_maker_up_shares",
                "prior_maker_down_shares",
                "prior_maker_delta_shares",
                "prior_maker_imbalance_ratio",
                "prior_maker_paired_coverage",
                "path_fill_duration_ms",
                "seconds_left",
                "target_price",
                "target_side_is_up",
                "side_aligned_direction_score",
                "post_action",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "parent_id": "p1",
                "market_id": 101,
                "order_hash": "o1",
                "target_side": "DOWN",
                "native_book_side": "BID",
                "last_target_ms": 11600,
                "target_filled_shares": 18,
                "prior_maker_up_shares": 36,
                "prior_maker_down_shares": 0,
                "prior_maker_delta_shares": 36,
                "prior_maker_imbalance_ratio": 1,
                "prior_maker_paired_coverage": 0,
                "path_fill_duration_ms": 9999,
                "seconds_left": 180,
                "target_price": 0.42,
                "target_side_is_up": 0,
                "side_aligned_direction_score": -0.3,
                "post_action": "REPRICE_1_3_TICKS_CONFIRMED_NEXT_PARENT",
            }
        )

    maker_db = tmp_path / "maker.db"
    db = sqlite3.connect(maker_db)
    db.execute(
        """CREATE TABLE maker_book_inference_v21_allocations (
            parent_id TEXT, market_id INTEGER, source_ms INTEGER,
            allocated_quantity REAL, allocation_kind TEXT
        )"""
    )
    db.executemany(
        "INSERT INTO maker_book_inference_v21_allocations VALUES (?,?,?,?,?)",
        [
            ("p1", 101, 10000, 6.0, "TARGET_FILL_DECREASE"),
            ("p1", 101, 11600, 12.0, "TARGET_FILL_DECREASE"),
        ],
    )
    db.commit()
    db.close()

    shadow_db = tmp_path / "shadow.db"
    db = sqlite3.connect(shadow_db)
    db.execute(
        """CREATE TABLE wallet_target_taker_mirror_meta (
            cohort TEXT PRIMARY KEY, deployed_at_ms INTEGER, excluded_market_id INTEGER
        )"""
    )
    db.execute(
        "INSERT INTO wallet_target_taker_mirror_meta VALUES (?,?,?)",
        (TAKER_COHORT, 0, None),
    )
    db.execute(
        """CREATE TABLE wallet_target_taker_mirror_parents (
            parent_id TEXT, market_id INTEGER, order_hash TEXT, side TEXT,
            target_event_ms INTEGER, target_last_event_ms INTEGER,
            target_latest_shares REAL, target_fill_legs INTEGER, detection_lag_ms REAL,
            cohort TEXT
        )"""
    )
    db.executemany(
        """INSERT INTO wallet_target_taker_mirror_parents
           (parent_id,market_id,order_hash,side,target_event_ms,target_last_event_ms,
            target_latest_shares,target_fill_legs,detection_lag_ms,cohort)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        [
            ("t_same", 101, "ts", "UP", 11000, 11000, 9.0, 1, 0.0, TAKER_COHORT),
            ("t_next", 101, "tn", "DOWN", 12000, 12000, 11.0, 1, 0.0, TAKER_COHORT),
            ("t_next2", 101, "tn2", "UP", 13000, 13000, 7.0, 1, 0.0, TAKER_COHORT),
        ],
    )
    db.commit()
    db.close()

    output = tmp_path / "state.csv"
    meta = tmp_path / "state.meta.json"
    report = build_state_link_dataset(
        maker_dataset_path=maker_dataset,
        maker_db_path=maker_db,
        shadow_db_path=shadow_db,
        output_path=output,
        meta_output_path=meta,
    )

    with output.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert report["rows"] == 1
    assert report["highResolutionMakerAnchorCoverage"] == 1.0
    assert len(rows) == 1
    row = rows[0]

    assert float(row["highres_fill_duration_ms"]) == 1600.0
    assert int(float(row["fill_duration_ge_1500ms"])) == 1

    # Prior UP 36 plus this DOWN fill 18 => post-fill UP 36 / DOWN 18, still UP-heavy.
    assert float(row["post_fill_maker_up_shares"]) == 36.0
    assert float(row["post_fill_maker_down_shares"]) == 18.0
    assert float(row["post_fill_maker_delta_shares"]) == 18.0
    assert abs(float(row["post_fill_maker_imbalance_ratio"]) - (1.0 / 3.0)) < 1e-9

    # Same-second Taker is metadata only. The next full second contains only t_next.
    assert int(row["same_second_taker_parents_ambiguous"]) == 1
    assert int(row["next_taker_parent_count_1s"]) == 1
    assert int(row["label_next_taker_any_1s"]) == 1
    assert int(row["label_next_taker_inventory_balancing_1s"]) == 1
    assert int(row["label_next_taker_balancing_given_taker_1s"]) == 1
    assert int(row["label_next_taker_same_maker_side_1s"]) == 1
