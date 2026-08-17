from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_controller_complete_history_v2_compat as compat


def _create_db(path: Path, *, with_asset: bool) -> None:
    db = sqlite3.connect(path)
    try:
        asset_col = ", asset TEXT" if with_asset else ""
        db.execute(
            f"""
            CREATE TABLE wallet_shadow_target_events (
                leg_id TEXT NOT NULL,
                wallet TEXT,
                market_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                side TEXT NOT NULL,
                quote_type TEXT NOT NULL,
                order_hash TEXT,
                event_ms INTEGER NOT NULL,
                price REAL NOT NULL,
                shares REAL NOT NULL
                {asset_col}
            )
            """
        )
        if with_asset:
            db.executemany(
                """
                INSERT INTO wallet_shadow_target_events
                    (leg_id,wallet,market_id,role,side,quote_type,order_hash,event_ms,price,shares,asset)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    ("btc1", compat.v2.TARGET_WALLET, 1, "TAKER", "UP", "BID", "o1", 1000, 0.4, 2.0, "BTC"),
                    ("eth1", compat.v2.TARGET_WALLET, 2, "TAKER", "DOWN", "BID", "o2", 2000, 0.3, 1.0, "ETH"),
                ],
            )
        else:
            db.execute(
                """
                INSERT INTO wallet_shadow_target_events
                    (leg_id,wallet,market_id,role,side,quote_type,order_hash,event_ms,price,shares)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                ("legacy1", compat.v2.TARGET_WALLET, 7, "MAKER", "DOWN", "BID", "legacy-order", 900, 0.55, 3.0),
            )
        db.commit()
    finally:
        db.close()


def test_legacy_schema_without_asset_injects_cli_asset(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    _create_db(path, with_asset=False)

    rows, audit = compat._load_source_compat(path, "LEGACY", "BTC")

    assert len(rows) == 1
    assert rows[0]["asset"] == "BTC"
    assert rows[0]["source_version"] == "LEGACY"
    assert audit["assetColumnPresent"] is False
    assert audit["assetFilterApplied"] is False
    assert audit["assetInjectedFromCli"] is True


def test_official_schema_with_asset_keeps_asset_filter(tmp_path: Path) -> None:
    path = tmp_path / "official.db"
    _create_db(path, with_asset=True)

    rows, audit = compat._load_source_compat(path, "OFFICIAL", "BTC")

    assert [row["leg_id"] for row in rows] == ["btc1"]
    assert rows[0]["asset"] == "BTC"
    assert audit["assetColumnPresent"] is True
    assert audit["assetFilterApplied"] is True
    assert audit["assetInjectedFromCli"] is False


def test_decision_surface_allows_equal_feature_values_without_dict_comparison() -> None:
    rows = [
        {"risk_deficit": 10.0, "next_actor": "TAKER", "market_id": 1},
        {"risk_deficit": 10.0, "next_actor": "MAKER", "market_id": 2},
        {"risk_deficit": 20.0, "next_actor": "TAKER", "market_id": 3},
        {"risk_deficit": 20.0, "next_actor": "MAKER", "market_id": 4},
        {"risk_deficit": 30.0, "next_actor": "TAKER", "market_id": 5},
    ]
    surface = compat._surface_compat(rows, "risk_deficit", bins=2)
    assert len(surface) == 2
    assert sum(int(bin_row["rows"]) for bin_row in surface) == len(rows)
