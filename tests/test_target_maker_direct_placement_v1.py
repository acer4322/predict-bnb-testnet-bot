from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pandas as pd

from predict_bot.target_maker_direct_placement_v1 import build_datasets


def _maker_db(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE maker_book_inference_meta(
            cohort TEXT PRIMARY KEY,deployed_at_ms INTEGER,excluded_market_id INTEGER,target_wallet TEXT,last_target_rowid INTEGER,policy_json TEXT
        );
        INSERT INTO maker_book_inference_meta VALUES('TARGET_MAKER_BOOK_INFERENCE_V1',0,99,'0x0',0,'{}');
        CREATE TABLE maker_book_inference_markets(
            market_id INTEGER PRIMARY KEY,title TEXT,decimal_precision INTEGER,first_seen_ms INTEGER,window_end_ms INTEGER,status TEXT
        );
        INSERT INTO maker_book_inference_markets VALUES(1,'m1',2,1,300000,'ACTIVE');
        INSERT INTO maker_book_inference_markets VALUES(2,'m2',2,1,300000,'ACTIVE');
        CREATE TABLE maker_book_inference_v21_parent_lifecycles(
            parent_id TEXT PRIMARY KEY,market_id INTEGER,order_hash TEXT,target_side TEXT,native_book_side TEXT,
            target_price REAL,native_price REAL,first_target_ms INTEGER,last_target_ms INTEGER,
            placement_first_ms INTEGER,placement_last_ms INTEGER,resting_ms INTEGER,expected_parent_shares REAL,
            placement_coverage REAL,fill_allocation_coverage REAL,confidence REAL
        );
        INSERT INTO maker_book_inference_v21_parent_lifecycles VALUES(
            'p1',1,'h1','UP','BID',0.49,0.49,1700,1700,1500,1500,200,18,0.95,0.95,0.90
        );
        """
    )
    db.commit()
    db.close()


def _signal_db(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE wallet_taker_signal_meta(key TEXT PRIMARY KEY,value TEXT);
        INSERT INTO wallet_taker_signal_meta VALUES('deployed_at_ms','0');
        CREATE TABLE wallet_taker_signal_snapshots(
            timestamp_ns INTEGER PRIMARY KEY,sampled_at_ms INTEGER,market_id INTEGER,seconds_left REAL,
            predict_up_bid REAL,predict_up_ask REAL,predict_up_mid REAL,predict_down_bid REAL,predict_down_ask REAL,predict_down_mid REAL,
            spot_queue_imbalance REAL,spot_taker_imbalance_250ms REAL,spot_taker_imbalance_1s REAL,
            spot_return_250ms_bps REAL,spot_return_1s_bps REAL,spot_return_3s_bps REAL,spot_return_5s_bps REAL,
            futures_queue_imbalance REAL,futures_taker_imbalance_250ms REAL,futures_taker_imbalance_1s REAL,
            futures_return_250ms_bps REAL,futures_return_1s_bps REAL,futures_return_3s_bps REAL,futures_return_5s_bps REAL,
            perp_spot_basis_bps REAL,spot_minus_strike_bps REAL,chainlink_minus_strike_bps REAL,spot_minus_chainlink_bps REAL,direction_score REAL
        );
        """
    )
    def insert(ts: int, market: int, up_bid: float, direction: float) -> None:
        down_bid = 1.0 - up_bid - 0.02
        values = [
            ts * 1_000_000 + market,ts,market,100.0,
            up_bid,up_bid+0.02,up_bid+0.01,down_bid,down_bid+0.02,down_bid+0.01,
            0.1,0.1,0.1,0.2,0.3,0.4,0.5,-0.1,-0.1,-0.1,-0.2,-0.3,-0.4,-0.5,1.0,2.0,1.5,0.5,direction,
        ]
        db.execute("INSERT INTO wallet_taker_signal_snapshots VALUES(" + ",".join("?" for _ in values) + ")", values)
    insert(1000,1,0.48,0.2)
    insert(1400,1,0.50,0.2)
    insert(1600,1,0.51,-0.2)
    insert(1000,2,0.50,0.1)
    insert(2000,2,0.51,0.1)
    db.commit()
    db.close()


def test_builder_uses_strict_pre_placement_and_keeps_zero_placement_markets(tmp_path: Path) -> None:
    maker = tmp_path / "maker.db"
    signal = tmp_path / "signal.db"
    hazard = tmp_path / "hazard.csv"
    behavior = tmp_path / "behavior.csv"
    meta = tmp_path / "meta.json"
    _maker_db(maker)
    _signal_db(signal)

    report = build_datasets(
        maker_db_path=maker, signal_db_path=signal,
        hazard_output_path=hazard, behavior_output_path=behavior, meta_output_path=meta,
    )
    assert report["hazard"]["markets"] == 2
    assert report["hazard"]["zeroPlacementMarkets"] == 1
    rows = pd.read_csv(behavior)
    assert len(rows) == 1
    row = rows.iloc[0]
    assert int(row.signal_sampled_at_ms) == 1400
    assert int(row.label_side_up) == 1
    assert abs(float(row.placement_ticks_from_pre_best_bid) - 1.0) < 1e-9
    assert int(row.label_near_best_1tick) == 1
    hazard_rows = pd.read_csv(hazard)
    zero_market = hazard_rows[hazard_rows.market_id == 2]
    assert len(zero_market) == 2
    assert int(zero_market.label_next_inferred_placement_any_5s.sum()) == 0


def test_spanning_folds_include_latest_history() -> None:
    path = Path(__file__).resolve().parents[1] / "tools" / "train_target_maker_direct_placement_v1.py"
    spec = importlib.util.spec_from_file_location("maker_direct_trainer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    markets = list(range(1, 101))
    folds = module._spanning_folds(markets, min_train=20, test_markets=10, max_folds=4)
    assert len(folds) == 4
    assert folds[-1]["testMarkets"] == list(range(91, 101))
    assert folds[0]["testMarkets"][0] == 21
    assert folds[1]["testMarkets"][0] > folds[0]["testMarkets"][0]


def test_side_feature_sets_are_target_blind() -> None:
    path = Path(__file__).resolve().parents[1] / "tools" / "train_target_maker_direct_placement_v1.py"
    spec = importlib.util.spec_from_file_location("maker_direct_trainer_contract", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    forbidden = ("target_", "chosen_", "label_", "placement_", "parent_")
    for features in module.SIDE_FEATURE_SETS.values():
        assert all(not feature.startswith(forbidden) for feature in features)
