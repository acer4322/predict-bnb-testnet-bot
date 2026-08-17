from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_poly_urgency_hazard_v1 as mod


def test_strict_past_poly_excludes_equal_timestamp() -> None:
    series = {
        "slug": (
            [1000, 2000],
            [
                {"source_ms": 1000, "up_mid": 0.60},
                {"source_ms": 2000, "up_mid": 0.70},
            ],
        )
    }
    assert mod._strict_asof(series, "slug", 1000) is None
    assert mod._strict_asof(series, "slug", 2000)["source_ms"] == 1000
    assert mod._strict_asof(series, "slug", 2001)["source_ms"] == 2000


def test_next_taker_hazard_ignores_intervening_maker_rows() -> None:
    index = mod._burst_index(
        [
            {
                "market_id": "7",
                "first_event_ms": "6000",
                "portfolio_effect": "RISK_REDUCING",
                "clean_side": "DOWN",
            }
        ]
    )
    burst = mod._next_burst(index, 7, 1000)
    assert burst is not None
    assert int(burst["first_event_ms"]) == 6000


def test_one_second_grid_keeps_latest_maker_state() -> None:
    rows = [
        {"market_id": "1", "maker_completed_ms": "1000"},
        {"market_id": "1", "maker_completed_ms": "1500"},
        {"market_id": "1", "maker_completed_ms": "2100"},
    ]
    result = mod._dedupe_grid(rows, 1000)
    assert [int(row["maker_completed_ms"]) for row in result] == [1500, 2100]


def test_poly_loader_requires_index_and_is_strict_past(tmp_path: Path) -> None:
    db_path = tmp_path / "cross.db"
    db = sqlite3.connect(db_path)
    db.executescript(
        """
        CREATE TABLE polymarket_events(
            id INTEGER PRIMARY KEY,
            market_slug TEXT NOT NULL,
            outcome TEXT,
            best_bid REAL,
            best_ask REAL,
            last_trade REAL,
            source_timestamp_ms INTEGER
        );
        CREATE INDEX idx_poly_events_source ON polymarket_events(source_timestamp_ms);
        CREATE TABLE poly_chop_guard_markets(
            market_id INTEGER PRIMARY KEY,
            poly_market_slug TEXT NOT NULL
        );
        """
    )
    db.execute(
        "INSERT INTO poly_chop_guard_markets VALUES (?,?)",
        (123, "btc-updown-5m-1"),
    )
    db.executemany(
        "INSERT INTO polymarket_events(market_slug,outcome,best_bid,best_ask,last_trade,source_timestamp_ms) "
        "VALUES (?,?,?,?,?,?)",
        [
            ("btc-updown-5m-1", "UP", 0.59, 0.61, None, 1000),
            ("btc-updown-5m-1", "DOWN", 0.39, 0.41, None, 1001),
            ("btc-updown-5m-1", "UP", 0.69, 0.71, None, 2000),
        ],
    )
    db.commit()
    db.close()

    before = db_path.read_bytes()
    ro = mod._open_ro(db_path)
    assert mod._require_index(ro, "polymarket_events", "source_timestamp_ms")["name"] == "idx_poly_events_source"
    assert mod._slug_mapping(ro, [123])[123] == "btc-updown-5m-1"
    series, audit = mod._load_poly_series(ro, 1000, 3000, 0)
    assert audit["eventRowsRead"] == 3
    state = mod._strict_asof(series, "btc-updown-5m-1", 2000)
    assert state is not None
    assert state["source_ms"] == 1001
    assert abs(float(state["up_mid"]) - 0.60) < 1e-9
    ro.close()
    assert db_path.read_bytes() == before


def test_fixed_strength_surface_orders_bins() -> None:
    rows = []
    for strength, hit in [(0.01, 0), (0.05, 0), (0.10, 1), (0.20, 1), (0.30, 1)]:
        rows.append(
            {
                "poly_strength": strength,
                "poly_up_mid": 0.5 + strength,
                "taker_within_1s": hit,
                "taker_within_3s": hit,
                "taker_within_5s": hit,
                "taker_within_15s": hit,
                "next_taker_effect": "RISK_REDUCING" if hit else "",
            }
        )
    surface = mod._surface(rows)
    assert [row["n"] for row in surface] == [1, 1, 1, 1, 1]
    assert surface[0]["takerWithin5sRate"] == 0.0
    assert surface[-1]["takerWithin5sRate"] == 1.0
