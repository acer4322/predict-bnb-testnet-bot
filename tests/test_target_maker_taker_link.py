from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import pytest

from predict_bot.target_maker_ebm_v3_dataset import OUTPUT_COLUMNS
from predict_bot.target_maker_taker_link import analyze_link


def _maker_dataset(path: Path) -> None:
    rows = []
    for anchor, action in [
        (1000, "REPRICE_1_3_TICKS_CONFIRMED_NEXT_PARENT"),
        (2000, "SAME_PRICE_REFILL_CONFIRMED_NEXT_PARENT"),
    ]:
        row = {column: "" for column in OUTPUT_COLUMNS}
        row.update({
            "dataset_version": "V3",
            "parent_id": f"p{anchor}",
            "market_id": 1,
            "target_side": "UP",
            "last_target_ms": anchor,
            "post_action": action,
            "observed_filled_near_18": 1,
            "prior_maker_delta_shares": 18.0,
            "prior_maker_imbalance_ratio": 0.2,
            "seconds_left": 120.0,
            "target_price": 0.5,
            "path_fill_duration_ms": 100.0,
            "label_reprice_toward_touch": 1 if "REPRICE" in action else "",
        })
        rows.append(row)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _shadow_db(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE wallet_target_taker_mirror_meta (
            cohort TEXT PRIMARY KEY,
            deployed_at_ms INTEGER NOT NULL,
            excluded_market_id INTEGER,
            policy_json TEXT NOT NULL
        );
        CREATE TABLE wallet_target_taker_mirror_parents (
            cohort TEXT NOT NULL,
            parent_id TEXT NOT NULL,
            market_id INTEGER NOT NULL,
            order_hash TEXT,
            side TEXT NOT NULL,
            target_event_ms INTEGER NOT NULL,
            target_last_event_ms INTEGER NOT NULL,
            detected_at_ms INTEGER NOT NULL,
            detection_lag_ms INTEGER NOT NULL,
            target_average_price REAL,
            target_shares_at_detection REAL NOT NULL,
            target_latest_shares REAL NOT NULL,
            target_fill_legs INTEGER NOT NULL,
            strict_pre_signal_ms INTEGER,
            strict_pre_signal_lead_ms INTEGER,
            strict_pre_signal_json TEXT,
            PRIMARY KEY(cohort,parent_id)
        );
        """
    )
    cohort = "TARGET_TAKER_MIRROR_AUDIT_V1"
    db.execute("INSERT INTO wallet_target_taker_mirror_meta VALUES (?,?,?,?)", (cohort, 500, None, "{}"))
    rows = [
        (cohort, "t1", 1, "h1", "DOWN", 1100, 1100, 1110, 10, 0.5, 5.0, 5.0, 1, None, None, None),
        (cohort, "t2", 1, "h2", "UP", 1900, 1900, 1910, 10, 0.5, 4.0, 4.0, 1, None, None, None),
        (cohort, "t3", 1, "h3", "DOWN", 2100, 2100, 2110, 10, 0.5, 6.0, 6.0, 1, None, None, None),
    ]
    db.executemany("INSERT INTO wallet_target_taker_mirror_parents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.commit()
    db.close()


def test_maker_taker_link_measures_symmetric_pre_post_windows(tmp_path: Path) -> None:
    maker = tmp_path / "maker_v3.csv"
    shadow = tmp_path / "shadow.db"
    report = tmp_path / "link.json"
    _maker_dataset(maker)
    _shadow_db(shadow)

    result = analyze_link(
        maker_dataset_path=maker,
        shadow_db_path=shadow,
        report_path=report,
        min_regime_rows=10,
        bootstrap_samples=100,
    )

    assert result["eligibleMakerAnchors"] == 2
    window = result["windows"]["250"]
    assert window["post"]["anyTakerRate"] == pytest.approx(1.0)
    assert window["pre"]["anyTakerRate"] == pytest.approx(0.5)
    assert window["rawPostMinusPreAnyRate"] == pytest.approx(0.5)
    assert window["post"]["inventoryBalancingEventRate"] == pytest.approx(1.0)
    assert window["post"]["sameMakerSideEventRate"] == pytest.approx(0.0)
    assert result["causalClaim"] is False
    assert report.exists()
