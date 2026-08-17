from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_taker_ordinary_paper_pnl_v1 as mod


def _signal_db(path: Path, rows: list[tuple[int, int, float, float]]) -> None:
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE wallet_taker_signal_snapshots(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id INTEGER NOT NULL,
            sampled_at_ms INTEGER NOT NULL,
            predict_up_ask REAL,
            predict_down_ask REAL,
            predict_up_bid REAL,
            predict_down_bid REAL
        )"""
    )
    db.executemany(
        "INSERT INTO wallet_taker_signal_snapshots(market_id,sampled_at_ms,predict_up_ask,predict_down_ask,predict_up_bid,predict_down_bid) VALUES(?,?,?,?,?,?)",
        [(market, sampled, up, down, max(0.0, up - 0.01), max(0.0, down - 0.01)) for market, sampled, up, down in rows],
    )
    db.commit()
    db.close()


def _settlement_db(path: Path) -> None:
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE market_settlements(
            market_id INTEGER PRIMARY KEY,
            status TEXT NOT NULL,
            official_winner TEXT
        )"""
    )
    db.executemany(
        "INSERT INTO market_settlements(market_id,status,official_winner) VALUES(?,?,?)",
        [(1, "OFFICIAL", "UP"), (2, "PENDING", "DOWN")],
    )
    db.commit()
    db.close()


def test_signal_lookup_is_strict_asof_and_later_source_wins_tie(tmp_path: Path):
    legacy = tmp_path / "legacy.db"
    newer = tmp_path / "newer.db"
    _signal_db(legacy, [(1, 1000, 0.40, 0.60), (1, 3000, 0.90, 0.10)])
    _signal_db(newer, [(1, 1000, 0.41, 0.59), (1, 2500, 0.70, 0.30)])
    lookup = mod.SignalLookup([legacy, newer])
    try:
        row = lookup.asof(1, 2000, 1500)
    finally:
        lookup.close()
    assert row is not None
    assert row["sampled_at_ms"] == 1000
    assert row["predict_up_ask"] == 0.41
    assert row["lagMs"] == 1000


def test_settlement_lookup_requires_official(tmp_path: Path):
    path = tmp_path / "simulation.db"
    _settlement_db(path)
    lookup = mod.SettlementLookup(path)
    try:
        assert lookup.official(1) == "UP"
        assert lookup.official(2) is None
        assert lookup.official(999) is None
    finally:
        lookup.close()


def test_trade_result_uses_predict_fee_formula_and_hold_to_settlement():
    row = {
        "entryAsk": 0.50,
        "predicted_side": "UP",
        "officialWinner": "UP",
        "sampled_ms": 1000,
        "market_id": 1,
    }
    result = mod._trade_result(row, slippage_bps=0, fee_bps=200, stake=1.0)
    assert result is not None
    assert abs(result["shares"] - 2.0) < 1e-12
    assert abs(result["entryFee"] - 0.02) < 1e-12
    assert abs(result["grossPnl"] - 1.0) < 1e-12
    assert abs(result["netPnl"] - 0.98) < 1e-12

    loser = dict(row, officialWinner="DOWN")
    result2 = mod._trade_result(loser, slippage_bps=0, fee_bps=200, stake=1.0)
    assert result2 is not None
    assert abs(result2["netPnl"] + 1.02) < 1e-12


def test_metrics_reports_directional_settlement_pnl():
    base = {
        "entryAsk": 0.50,
        "sampled_ms": 1000,
        "market_id": 1,
        "fold": 1,
        "phase": "MID",
        "predicted_side": "UP",
    }
    win = mod._trade_result(dict(base, officialWinner="UP"), slippage_bps=0, fee_bps=200, stake=1.0)
    loss = mod._trade_result(dict(base, sampled_ms=2000, market_id=2, officialWinner="DOWN"), slippage_bps=0, fee_bps=200, stake=1.0)
    assert win is not None and loss is not None
    metrics = mod._metrics([win, loss])
    assert metrics["trades"] == 2
    assert metrics["wins"] == 1
    assert metrics["losses"] == 1
    assert abs(metrics["netPnl"] + 0.04) < 1e-12
    assert metrics["maxDrawdownUsdt"] >= 1.02 - 1e-12
