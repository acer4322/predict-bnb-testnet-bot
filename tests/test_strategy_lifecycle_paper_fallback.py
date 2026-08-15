from __future__ import annotations

import sqlite3
from pathlib import Path

from predict_bot.strategy_lifecycle_guard import build_strategy_lifecycle_summary
from predict_bot.strategy_lifecycle_paper_fallback import (
    build_paper_strategy_lifecycle_summary,
    merge_live_and_paper_lifecycle,
)


def _paper_db(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE config(key TEXT PRIMARY KEY, value REAL NOT NULL);
        CREATE TABLE trades(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL,
            market_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            stake REAL NOT NULL,
            pnl REAL,
            opened_at TEXT NOT NULL,
            closed_at TEXT
        );
        CREATE TABLE strategy_pair_arb_trades(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL,
            market_id INTEGER NOT NULL,
            opened_at TEXT NOT NULL,
            shares REAL NOT NULL,
            total_cost REAL NOT NULL,
            locked_pnl REAL NOT NULL
        );
        """
    )
    return db


def _live_row(strategy: str, market_id: int, pnl: float) -> dict[str, object]:
    return {
        "strategy": strategy,
        "market_id": market_id,
        "cost_usdt": 5.0,
        "pnl_usdt": pnl,
        "settled_at": f"2026-08-08T00:{market_id % 60:02d}:00+00:00",
    }


def test_paper_summary_reads_realized_directional_exit_and_locked_pair_rows(tmp_path: Path):
    path = tmp_path / "simulation.db"
    db = _paper_db(path)
    db.executemany(
        "INSERT INTO config(key, value) VALUES (?, ?)",
        [
            ("strategy_r_microprice_enabled", 1),
            ("strategy_pair_arb_010_enabled", 1),
        ],
    )
    db.executemany(
        """INSERT INTO trades(strategy, market_id, status, stake, pnl, opened_at, closed_at)
           VALUES ('R_MICROPRICE', ?, 'SETTLED_WIN', 5, 1, ?, ?)""",
        [
            (
                market_id,
                f"2026-08-08T00:{market_id:02d}:00+00:00",
                f"2026-08-08T00:{market_id:02d}:30+00:00",
            )
            for market_id in range(1, 4)
        ],
    )
    db.execute(
        """INSERT INTO trades(strategy, market_id, status, stake, pnl, opened_at, closed_at)
           VALUES ('R_MICROPRICE_CONFIRM_EXIT_098', 40, 'TARGET_FILLED', 5, 2.5,
                   '2026-08-08T00:40:00+00:00', '2026-08-08T00:41:00+00:00')"""
    )
    db.execute(
        """INSERT INTO strategy_pair_arb_trades(
               strategy, market_id, opened_at, shares, total_cost, locked_pnl
           ) VALUES ('PAIR_ARB_010', 50, '2026-08-08T01:00:00+00:00', 2, 1.8, 0.2)"""
    )
    db.commit()
    db.close()

    summary = build_paper_strategy_lifecycle_summary(
        path,
        [
            "R_MICROPRICE",
            "R_MICROPRICE_CONFIRM_EXIT_098",
            "PAIR_ARB_010",
        ],
    )
    rows = {row["strategy"]: row for row in summary["strategies"]}

    assert summary["status"] == "READY"
    assert rows["R_MICROPRICE"]["settledMarkets"] == 3
    assert rows["R_MICROPRICE"]["drawdown"]["lifetimePnlUsdt"] == 3.0
    assert rows["R_MICROPRICE_CONFIRM_EXIT_098"]["settledMarkets"] == 1
    assert rows["R_MICROPRICE_CONFIRM_EXIT_098"]["drawdown"]["lifetimePnlUsdt"] == 2.5
    assert rows["PAIR_ARB_010"]["settledMarkets"] == 1
    assert rows["PAIR_ARB_010"]["drawdown"]["lifetimePnlUsdt"] == 0.2
    assert summary["collectorEnabledByStrategy"] == {
        "R_MICROPRICE": True,
        "R_MICROPRICE_CONFIRM_EXIT_098": None,
        "PAIR_ARB_010": True,
    }
    assert summary["paperTradeSampleBasis"] == "realized terminal paper trades with pnl"


def test_lifecycle_uses_paper_only_when_live_has_zero_settled_samples(tmp_path: Path):
    path = tmp_path / "simulation.db"
    db = _paper_db(path)
    db.execute(
        "INSERT INTO config(key, value) VALUES ('strategy_r_microprice_enabled', 1)"
    )
    db.executemany(
        """INSERT INTO trades(strategy, market_id, status, stake, pnl, opened_at, closed_at)
           VALUES ('R_MICROPRICE', ?, 'SETTLED_WIN', 5, 1, ?, ?)""",
        [
            (
                market_id,
                f"2026-08-{1 + market_id // 24:02d}T{market_id % 24:02d}:00:00+00:00",
                f"2026-08-{1 + market_id // 24:02d}T{market_id % 24:02d}:05:00+00:00",
            )
            for market_id in range(1, 21)
        ],
    )
    db.commit()
    db.close()

    live = build_strategy_lifecycle_summary([], ["R_MICROPRICE"])
    paper = build_paper_strategy_lifecycle_summary(path, ["R_MICROPRICE"])
    merged = merge_live_and_paper_lifecycle(
        live,
        paper,
        ["R_MICROPRICE"],
        ["R_MICROPRICE"],
    )
    row = merged["strategies"][0]

    assert row["dataSource"] == "PAPER_FALLBACK"
    assert row["paperFallbackActive"] is True
    assert row["settledMarkets"] == 20
    assert row["status"] == "ACTIVE"
    assert row["live"]["settledMarkets"] == 0
    assert row["paper"]["settledMarkets"] == 20
    assert merged["paperFallbackStrategies"] == ["R_MICROPRICE"]
    assert merged["paperFallbackCount"] == 1


def test_any_live_settlement_switches_primary_basis_to_live_without_mixing():
    supported = ["R_MICROPRICE"]
    live = build_strategy_lifecycle_summary(
        [_live_row("R_MICROPRICE", 1, -2.0)],
        supported,
    )
    paper = build_strategy_lifecycle_summary(
        [_live_row("R_MICROPRICE", market_id, 1.0) for market_id in range(2, 22)],
        supported,
    )
    paper.update(
        {
            "status": "READY",
            "collectorEnabledByStrategy": {"R_MICROPRICE": True},
            "paperTradeSampleBasis": "realized terminal paper trades with pnl",
            "pairSampleBasis": "locked paper pair result at simulated fill",
        }
    )

    merged = merge_live_and_paper_lifecycle(live, paper, supported)
    row = merged["strategies"][0]

    assert row["dataSource"] == "LIVE"
    assert row["paperFallbackActive"] is False
    assert row["settledMarkets"] == 1
    assert row["drawdown"]["lifetimePnlUsdt"] == -2.0
    assert row["paper"]["settledMarkets"] == 20
    assert row["paper"]["drawdown"]["lifetimePnlUsdt"] == 20.0
    assert merged["paperFallbackCount"] == 0


def test_missing_simulation_db_fails_open_for_live_monitor(tmp_path: Path):
    missing = tmp_path / "missing.db"
    paper = build_paper_strategy_lifecycle_summary(
        missing,
        ["R_MICROPRICE"],
    )
    live = build_strategy_lifecycle_summary(
        [_live_row("R_MICROPRICE", 1, 1.0)],
        ["R_MICROPRICE"],
    )

    merged = merge_live_and_paper_lifecycle(
        live,
        paper,
        ["R_MICROPRICE"],
    )

    assert paper["status"] == "UNAVAILABLE"
    assert merged["strategies"][0]["dataSource"] == "LIVE"
    assert merged["paperStatus"] == "UNAVAILABLE"
