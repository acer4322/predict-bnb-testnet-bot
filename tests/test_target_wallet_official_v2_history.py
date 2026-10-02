from __future__ import annotations

import sqlite3
import threading

from predict_bot import target_wallet_official_v1 as v1
from predict_bot import target_wallet_official_v2 as v2


WALLET = "0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03"


def create_legacy(path) -> None:
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE wallet_shadow_target_market_results(
               wallet TEXT,market_id INTEGER,title TEXT,winner TEXT,resolved_at_ms INTEGER,
               historical_reconstruction INTEGER,accounting_mode TEXT,status TEXT,event_count INTEGER,
               maker_event_count INTEGER,taker_event_count INTEGER,buy_notional_usdt REAL,
               sell_proceeds_usdt REAL,collateral_fees_usdt REAL,payout_usdt REAL,net_pnl_usdt REAL,
               net_roi REAL,maker_notional_usdt REAL,maker_pnl_usdt REAL,taker_notional_usdt REAL,
               taker_pnl_usdt REAL,gross_up_shares REAL,gross_down_shares REAL,share_fees_up REAL,
               share_fees_down REAL,net_up_shares REAL,net_down_shares REAL,share_conviction_side TEXT,
               capital_conviction_side TEXT,share_direction_correct INTEGER,capital_direction_correct INTEGER
           )"""
    )
    rows = [
        (WALLET, 100, "legacy overlap", "UP", 1_000, 1, "OLD", "WIN", 2, 1, 1, 5.0, 0.0, 0.0, 7.0, 2.0, 0.4, 2.5, 1.0, 2.5, 1.0, 7.0, 0.0, 0.0, 0.0, 7.0, 0.0, "UP", "UP", 1, 1),
        (WALLET, 101, "legacy only", "DOWN", 2_000, 1, "OLD", "WIN", 3, 2, 1, 4.0, 0.0, 0.0, 6.0, 2.0, 0.5, 2.0, 1.0, 2.0, 1.0, 0.0, 6.0, 0.0, 0.0, 0.0, 6.0, "DOWN", "DOWN", 1, 1),
    ]
    db.executemany("INSERT INTO wallet_shadow_target_market_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.commit()
    db.close()


def create_current(path) -> sqlite3.Connection:
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute(
        """CREATE TABLE target_market_results(
               market_id INTEGER,asset TEXT,title TEXT,winner TEXT,resolved_at_ms INTEGER,
               fill_count INTEGER,parent_count INTEGER,buy_notional_usdt REAL,sell_proceeds_usdt REAL,
               payout_usdt REAL,net_pnl_usdt REAL,net_roi REAL,maker_net_pnl_usdt REAL,
               taker_net_pnl_usdt REAL,up_position_shares REAL,down_position_shares REAL,
               accounting_version TEXT
           )"""
    )
    db.execute(
        "INSERT INTO target_market_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (100, "BTC", "current override", "DOWN", 3_000, 4, 2, 6.0, 0.0, 0.0, -6.0, -1.0, -2.0, -4.0, 5.0, 1.0, "CURRENT"),
    )
    db.commit()
    return db


def test_historical_merge_is_read_only_and_current_market_wins(monkeypatch, tmp_path) -> None:
    legacy_path = tmp_path / "predict_wallet_shadow.db"
    current_path = tmp_path / "target_wallet_official_v1.db"
    create_legacy(legacy_path)
    db = create_current(current_path)

    monkeypatch.setattr(v1, "LEGACY_DB_PATH", legacy_path)
    instance = v2.TargetWalletOfficialCollector.__new__(v2.TargetWalletOfficialCollector)
    instance.wallet = WALLET
    instance.db_path = current_path
    instance.db = db
    instance.db_lock = threading.RLock()
    instance._history_lock = threading.RLock()
    instance._history_cache = None
    instance._history_cache_at_ms = 0

    history = instance._historical_performance()

    assert history["settledMarkets"] == 2
    assert history["wins"] == 1
    assert history["losses"] == 1
    assert history["legacyMarkets"] == 1
    assert history["currentOfficialMarkets"] == 1
    assert history["sources"]["legacy"]["mode"] == "READ_ONLY"
    assert history["sources"]["legacy"]["rows"] == 2
    by_id = {int(row["market_id"]): row for row in history["recentMarkets"]}
    assert by_id[100]["source"] == "TARGET_WALLET_OFFICIAL_V2"
    assert by_id[100]["title"] == "current override"
    assert by_id[100]["status"] == "LOSS"
    assert by_id[101]["source"] == "LEGACY_WALLET_SHADOW_TARGET_ACCOUNTING"

    # Reading historical continuity must not mutate the legacy database.
    check = sqlite3.connect(legacy_path)
    assert check.execute("SELECT COUNT(*) FROM wallet_shadow_target_market_results").fetchone()[0] == 2
    check.close()
    db.close()
