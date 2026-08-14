from __future__ import annotations

import csv
import sqlite3

from predict_bot.target_eth_taker_behavior_v1 import (
    ETH_COHORT,
    build_target_eth_taker_dataset,
)
from predict_bot.target_eth_taker_hazard_v1 import build_target_eth_taker_hazard_dataset


def _target_db(path):
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE maker_book_inference_meta(
            cohort TEXT PRIMARY KEY,deployed_at_ms INTEGER,excluded_market_id INTEGER,
            target_wallet TEXT,last_target_rowid INTEGER,policy_json TEXT
        )"""
    )
    db.execute(
        "INSERT INTO maker_book_inference_meta VALUES (?,?,?,?,?,?)",
        (ETH_COHORT, 0, None, "0xabc", 0, "{}"),
    )
    db.execute(
        """CREATE TABLE maker_book_inference_wallet_events(
            source_leg_id TEXT PRIMARY KEY,wallet TEXT,market_id INTEGER,role TEXT,
            quote_type TEXT,side TEXT,order_hash TEXT,event_ms INTEGER,observed_at_ms INTEGER,
            price REAL,shares REAL
        )"""
    )
    db.executemany(
        "INSERT INTO maker_book_inference_wallet_events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("maker1", "0xabc", 101, "MAKER", "BID", "UP", "m1", 10500, 10600, 0.40, 18.0),
            ("t1a", "0xabc", 101, "TAKER", "BID", "UP", "t1", 12000, 12100, 0.61, 4.0),
            ("t1b", "0xabc", 101, "TAKER", "BID", "UP", "t1", 12000, 12200, 0.62, 6.0),
            ("ask", "0xabc", 101, "TAKER", "ASK", "DOWN", "x", 13000, 13100, 0.40, 9.0),
        ],
    )
    db.execute(
        """CREATE TABLE maker_book_inference_markets(
            market_id INTEGER PRIMARY KEY,title TEXT,decimal_precision INTEGER,
            first_seen_ms INTEGER,window_end_ms INTEGER,status TEXT
        )"""
    )
    db.execute("INSERT INTO maker_book_inference_markets VALUES (101,'ETH',2,1000,300000,'RECORDING')")
    db.execute(
        """CREATE TABLE maker_book_inference_updates(
            id INTEGER PRIMARY KEY,market_id INTEGER,source_timestamp_ms INTEGER,
            received_at_ms INTEGER,order_count INTEGER,is_checkpoint INTEGER,
            native_bids_z BLOB,native_asks_z BLOB,changes_z BLOB,
            bid_level_count INTEGER,ask_level_count INTEGER
        )"""
    )
    db.execute("INSERT INTO maker_book_inference_updates VALUES (1,101,10000,10000,1,1,NULL,NULL,X'00',1,1)")
    db.commit()
    db.close()


def _predict_db(path):
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE predict_fun_trajectory(
            asset TEXT,market_id INTEGER,market_bucket INTEGER,sample_bucket_ms INTEGER,
            sampled_at_ms INTEGER,seconds_left REAL,up_mid REAL,down_mid REAL,
            up_bid REAL,up_ask REAL,down_bid REAL,down_ask REAL,
            source_age_ms REAL,receipt_age_ms REAL,
            PRIMARY KEY(asset,market_id,sample_bucket_ms)
        )"""
    )
    rows = []
    for sampled, up_mid in [(9000, 0.48), (10000, 0.50), (11000, 0.56), (12000, 0.70), (13000, 0.45)]:
        rows.append(
            (
                "ETH", 101, 0, sampled, sampled, 300 - sampled / 1000,
                up_mid, 1 - up_mid, up_mid - 0.01, up_mid + 0.01,
                1 - up_mid - 0.01, 1 - up_mid + 0.01, 10.0, 20.0,
            )
        )
    db.executemany("INSERT INTO predict_fun_trajectory VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.commit()
    db.close()


def test_eth_behavior_excludes_entire_target_second_and_aggregates_parent(tmp_path):
    target = tmp_path / "eth_target.db"
    predict = tmp_path / "predict.db"
    _target_db(target)
    _predict_db(predict)
    output = tmp_path / "behavior.csv"
    meta = tmp_path / "behavior.meta.json"

    report = build_target_eth_taker_dataset(
        target_db_path=target,
        predict_db_path=predict,
        signal_source="predict",
        output_path=output,
        meta_output_path=meta,
        max_signal_age_ms=2500,
    )
    with output.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert report["eligibleTargetTakerParents"] == 1
    assert report["rows"] == 1
    assert report["ethTargetActivity"]["MAKER_BID"]["legs"] == 1
    assert report["ethTargetActivity"]["TAKER_BID"]["legs"] == 2
    row = rows[0]
    assert int(row["signal_sampled_at_ms"]) == 11000
    assert float(row["predict_up_mid"]) == 0.56
    assert float(row["target_latest_shares"]) == 10.0
    assert int(row["target_fill_legs"]) == 2
    assert int(row["label_side_up"]) == 1
    # The 12s public row is in the target event second and must never be a side feature.
    assert float(row["predict_up_mid"]) != 0.70


def test_eth_uniform_clock_hazard_excludes_same_second_target(tmp_path):
    target = tmp_path / "eth_target.db"
    predict = tmp_path / "predict.db"
    _target_db(target)
    _predict_db(predict)
    output = tmp_path / "hazard.csv"
    meta = tmp_path / "hazard.meta.json"

    report = build_target_eth_taker_hazard_dataset(
        target_db_path=target,
        predict_db_path=predict,
        output_path=output,
        meta_output_path=meta,
    )
    with output.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    by_bucket = {int(row["decision_second_bucket_ms"]): row for row in rows}

    assert report["markets"] == 1
    # At 11s, the target Taker begins in the next full second (12s).
    assert int(by_bucket[11000]["label_next_taker_any_1s"]) == 1
    # At 12s, that same event is ambiguous/same-second metadata, not a future label.
    assert int(by_bucket[12000]["same_second_taker_parents_ambiguous"]) == 1
    assert int(by_bucket[12000]["label_next_taker_any_1s"]) == 0
