from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import backfill_target_taker_official_settlements_v1 as mod


def test_parse_official_detail_uses_start_and_end_prices():
    payload = {"data": {"variantData": {"startPrice": "100", "endPrice": "101"}}}
    parsed = mod._parse_official_detail(payload)
    assert parsed is not None
    assert parsed["official_winner"] == "UP"
    assert parsed["start_price"] == 100.0
    assert parsed["official_end_price"] == 101.0

    down = mod._parse_official_detail({"variantData": {"startPrice": 100, "endPrice": 99}})
    assert down is not None and down["official_winner"] == "DOWN"


def test_parse_official_detail_rejects_unsettled_market():
    assert mod._parse_official_detail({"data": {"variantData": {"startPrice": "100", "endPrice": None}}}) is None


def test_simulation_lookup_supports_topic_id_fallback(tmp_path: Path):
    path = tmp_path / "simulation.db"
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE market_settlements(
            market_id INTEGER PRIMARY KEY,
            topic_id INTEGER,
            start_price REAL,
            official_winner TEXT,
            official_end_price REAL,
            status TEXT NOT NULL
        )"""
    )
    db.execute(
        "INSERT INTO market_settlements VALUES(999,123,100,'UP',101,'OFFICIAL')"
    )
    db.commit()
    db.close()

    ro = mod._connect_ro(path)
    assert ro is not None
    try:
        direct = mod._simulation_lookup(ro, 999)
        topic = mod._simulation_lookup(ro, 123)
    finally:
        ro.close()
    assert direct is not None and direct["official_winner"] == "UP"
    assert topic is not None and topic["official_winner"] == "UP"
    assert topic["source"] == "SIMULATION_DB_TOPIC_ID"
