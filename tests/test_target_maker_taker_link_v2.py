from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import pytest

from predict_bot.target_maker_ebm_v3_dataset import OUTPUT_COLUMNS
from predict_bot.target_maker_taker_link_v2 import analyze_link_v2


def _maker_dataset(path: Path) -> None:
    rows = []
    for parent_id, anchor, duration in [("p1", 10_500, 100.0), ("p2", 20_500, 1500.0)]:
        row = {column: "" for column in OUTPUT_COLUMNS}
        row.update({
            "dataset_version": "V3",
            "parent_id": parent_id,
            "market_id": 1,
            "target_side": "UP",
            "last_target_ms": anchor,
            "post_action": "REPRICE_1_3_TICKS_CONFIRMED_NEXT_PARENT",
            "observed_filled_near_18": 1,
            "prior_maker_delta_shares": 18.0,
            "prior_maker_imbalance_ratio": 0.2,
            "seconds_left": 120.0,
            "target_price": 0.5,
            "path_fill_duration_ms": duration,
        })
        rows.append(row)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _maker_db(path: Path) -> None:
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE maker_book_inference_v21_allocations (
            allocation_id TEXT PRIMARY KEY,market_id INTEGER,allocation_kind TEXT,parent_id TEXT,
            target_leg_id TEXT,update_id INTEGER,source_ms INTEGER,native_book_side TEXT,native_price REAL,
            public_delta_quantity REAL,allocated_quantity REAL,score REAL,evidence_json TEXT
        )"""
    )
    rows = [
        ("a1", 1, "TARGET_FILL_DECREASE", "p1", "l1", 1, 10_400, "BID", 0.5, 18, 9, 1, "{}"),
        ("a2", 1, "TARGET_FILL_DECREASE", "p1", "l2", 2, 10_500, "BID", 0.5, 18, 9, 1, "{}"),
        ("a3", 1, "TARGET_FILL_DECREASE", "p2", "l3", 3, 19_000, "BID", 0.5, 18, 9, 1, "{}"),
        ("a4", 1, "TARGET_FILL_DECREASE", "p2", "l4", 4, 20_500, "BID", 0.5, 18, 9, 1, "{}"),
    ]
    db.executemany("INSERT INTO maker_book_inference_v21_allocations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.commit()
    db.close()


def _shadow_db(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE wallet_target_taker_mirror_meta (
            cohort TEXT PRIMARY KEY,deployed_at_ms INTEGER NOT NULL,excluded_market_id INTEGER,policy_json TEXT NOT NULL
        );
        CREATE TABLE wallet_target_taker_mirror_parents (
            cohort TEXT NOT NULL,parent_id TEXT NOT NULL,market_id INTEGER NOT NULL,order_hash TEXT,side TEXT NOT NULL,
            target_event_ms INTEGER NOT NULL,target_last_event_ms INTEGER NOT NULL,detected_at_ms INTEGER NOT NULL,
            detection_lag_ms INTEGER NOT NULL,target_average_price REAL,target_shares_at_detection REAL NOT NULL,
            target_latest_shares REAL NOT NULL,target_fill_legs INTEGER NOT NULL,strict_pre_signal_ms INTEGER,
            strict_pre_signal_lead_ms INTEGER,strict_pre_signal_json TEXT,PRIMARY KEY(cohort,parent_id)
        );
        """
    )
    cohort = "TARGET_TAKER_MIRROR_AUDIT_V1"
    db.execute("INSERT INTO wallet_target_taker_mirror_meta VALUES (?,?,?,?)", (cohort, 500, None, "{}"))
    rows = [
        (cohort, "t_same", 1, "h0", "DOWN", 10_000, 10_000, 10_100, 100, 0.5, 2, 2, 1, None, None, None),
        (cohort, "t_pre", 1, "h1", "UP", 9_000, 9_000, 9_100, 100, 0.5, 2, 2, 1, None, None, None),
        (cohort, "t_post", 1, "h2", "DOWN", 11_000, 11_000, 11_100, 100, 0.5, 3, 3, 1, None, None, None),
        (cohort, "t_slow_post", 1, "h3", "DOWN", 21_000, 21_000, 21_100, 100, 0.5, 4, 4, 1, None, None, None),
    ]
    db.executemany("INSERT INTO wallet_target_taker_mirror_parents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.commit()
    db.close()


def test_link_v2_uses_second_buckets_and_highres_fill_duration(tmp_path: Path) -> None:
    maker_csv = tmp_path / "maker.csv"
    maker_db = tmp_path / "maker.db"
    shadow_db = tmp_path / "shadow.db"
    report = tmp_path / "report.json"
    _maker_dataset(maker_csv)
    _maker_db(maker_db)
    _shadow_db(shadow_db)

    result = analyze_link_v2(
        maker_dataset_path=maker_csv,
        maker_db_path=maker_db,
        shadow_db_path=shadow_db,
        report_path=report,
        bootstrap_samples=100,
    )

    assert result["makerAnchorsWithHighResolution8778FillEvidence"] == 2
    assert result["highResolutionMakerAnchorCoverage"] == pytest.approx(1.0)
    assert result["timestampDiagnostics"]["exactSecondBoundaryRate"] == pytest.approx(1.0)
    assert result["timestampDiagnostics"]["subsecondOrderingUsable"] is False
    assert result["sameSecondAmbiguous"]["anchorsWithAnyTaker"] == 1

    one = result["bucketWindows"]["1"]
    assert one["prior"]["anchorsWithAnyTaker"] == 1
    assert one["post"]["anchorsWithAnyTaker"] == 2
    assert one["post"]["inventoryBalancingEventRate"] == pytest.approx(1.0)

    hazards = {row["fillDurationBin"]: row for row in result["slowFillTakerHazard"]["rows"]}
    assert hazards["LE_250MS"]["makerAnchors"] == 1
    assert hazards["1000_1500MS"]["makerAnchors"] == 1
    assert hazards["1000_1500MS"]["nextFullSecond"]["post"]["anyTakerRate"] == pytest.approx(1.0)
    assert report.exists()
