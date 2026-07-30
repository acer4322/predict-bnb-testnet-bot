import sqlite3
from pathlib import Path

import pytest

from predict_bot.backtest import (
    M01BacktestSpec,
    deterministic_m0_side,
    run_m01_backtests,
    run_observer_replay,
    run_t180_advanced_exit_sweep,
    run_t180_take_profit_sweep,
)


def make_database(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE observations(
            id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, topic_id INTEGER NOT NULL,
            market_id INTEGER NOT NULL, start_price REAL NOT NULL,
            spot_price REAL NOT NULL, seconds_left REAL NOT NULL,
            up_ask REAL, up_bid REAL, down_ask REAL, down_bid REAL,
            up_ask_size REAL, down_ask_size REAL,
            book_skew_ms REAL, book_age_ms REAL
        );
        CREATE TABLE market_settlements(
            market_id INTEGER PRIMARY KEY, status TEXT NOT NULL, official_winner TEXT
        );
        """
    )
    for market_id, seconds_left, winner in ((101, 180.001, "UP"), (102, 180.0, "DOWN"), (103, 100.0, "UP")):
        side = deterministic_m0_side(20260717, market_id)
        values = {
            "up_ask": 0.55,
            "up_bid": 0.54,
            "down_ask": 0.46,
            "down_bid": 0.45,
        }
        values[f"{side.lower()}_ask"] = 0.30
        values[f"{side.lower()}_bid"] = 0.29
        db.execute(
            """INSERT INTO observations VALUES(
                   ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
               )""",
            (
                market_id,
                f"2026-01-01T00:{market_id - 101:02d}:00+00:00",
                1000 + market_id,
                market_id,
                100.0,
                100.01,
                seconds_left,
                values["up_ask"],
                values["up_bid"],
                values["down_ask"],
                values["down_bid"],
                100.0,
                100.0,
                0.0,
                0.0,
            ),
        )
        db.execute(
            "INSERT INTO market_settlements VALUES (?, 'OFFICIAL', ?)",
            (market_id, winner),
        )
    db.commit()
    db.close()


def test_replay_compares_baseline_with_strict_t180_boundary(tmp_path: Path):
    path = tmp_path / "history.db"
    make_database(path)
    report = run_m01_backtests(
        path,
        [
            M01BacktestSpec(name="baseline"),
            M01BacktestSpec(name="t180", min_seconds_left_exclusive=180),
        ],
    )

    assert report["candidateMarkets"] == 3
    assert report["summaries"]["baseline"]["overall"]["trades"] == 3
    assert report["summaries"]["t180"]["overall"]["trades"] == 1
    assert [trade["market_id"] for trade in report["trades"] if trade["strategy"] == "t180"] == [101]
    assert sum(report["summaries"]["baseline"]["overall"][key] for key in ("wins", "losses")) == 3
    assert report["splitPolicy"] == "chronological_60_20_20"


def test_backtest_rejects_empty_or_invalid_windows(tmp_path: Path):
    path = tmp_path / "history.db"
    make_database(path)
    with pytest.raises(ValueError, match="at least one"):
        run_m01_backtests(path, [])
    with pytest.raises(ValueError, match="time window is empty"):
        run_m01_backtests(
            path,
            [M01BacktestSpec(name="bad", min_seconds_left_exclusive=180, max_seconds_left_inclusive=180)],
        )


def test_t180_take_profit_sweep_uses_later_bid_and_full_visible_depth(
    tmp_path: Path,
):
    path = tmp_path / "take-profit.db"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE observations(
            id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, topic_id INTEGER NOT NULL,
            market_id INTEGER NOT NULL, start_price REAL NOT NULL,
            spot_price REAL NOT NULL, seconds_left REAL NOT NULL,
            up_ask REAL, up_bid REAL, down_ask REAL, down_bid REAL,
            up_ask_size REAL, up_bid_size REAL, down_ask_size REAL, down_bid_size REAL,
            book_skew_ms REAL, book_age_ms REAL
        );
        CREATE TABLE market_settlements(
            market_id INTEGER PRIMARY KEY, status TEXT NOT NULL, official_winner TEXT
        );
        """
    )
    market_id = 901
    side = deterministic_m0_side(20260717, market_id)
    winner = "DOWN" if side == "UP" else "UP"
    entry = {
        "up_ask": .70, "up_bid": .69, "down_ask": .70, "down_bid": .69,
        "up_ask_size": 100.0, "up_bid_size": 100.0,
        "down_ask_size": 100.0, "down_bid_size": 100.0,
    }
    entry[f"{side.lower()}_ask"] = .30
    entry[f"{side.lower()}_bid"] = .29
    target = dict(entry)
    target[f"{side.lower()}_ask"] = .46
    target[f"{side.lower()}_bid"] = .45
    for observation_id, seconds_left, values in (
        (1, 250.0, entry),
        (2, 240.0, target),
    ):
        db.execute(
            """INSERT INTO observations VALUES(
                   ?, ?, 1001, ?, 100, 100, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0
               )""",
            (
                observation_id,
                f"2026-01-01T00:00:{observation_id:02d}+00:00",
                market_id,
                seconds_left,
                values["up_ask"], values["up_bid"],
                values["down_ask"], values["down_bid"],
                values["up_ask_size"], values["up_bid_size"],
                values["down_ask_size"], values["down_bid_size"],
            ),
        )
    db.execute(
        "INSERT INTO market_settlements VALUES (?, 'OFFICIAL', ?)",
        (market_id, winner),
    )
    db.commit()
    db.close()

    report = run_t180_take_profit_sweep(path, targets=[.40, .60])

    assert report["selection"]["selectedTarget"] == pytest.approx(.40)
    assert report["summaries"]["0.40"]["overall"]["targetExits"] == 1
    assert report["summaries"]["0.40"]["overall"]["realizedPnl"] > 0
    assert report["summaries"]["0.60"]["overall"]["targetExits"] == 0
    assert report["summaries"]["0.60"]["overall"]["realizedPnl"] < 0
    assert report["trades"][0]["exit_price"] == pytest.approx(.40)
    assert report["trades"][0]["observed_exit_bid"] == pytest.approx(.45)


def test_t180_advanced_exit_sweep_models_partial_and_trailing_fills(
    tmp_path: Path,
):
    path = tmp_path / "advanced-exit.db"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE observations(
            id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, topic_id INTEGER NOT NULL,
            market_id INTEGER NOT NULL, start_price REAL NOT NULL,
            spot_price REAL NOT NULL, seconds_left REAL NOT NULL,
            up_ask REAL, up_bid REAL, down_ask REAL, down_bid REAL,
            up_ask_size REAL, up_bid_size REAL, down_ask_size REAL, down_bid_size REAL,
            book_skew_ms REAL, book_age_ms REAL
        );
        CREATE TABLE market_settlements(
            market_id INTEGER PRIMARY KEY, status TEXT NOT NULL, official_winner TEXT
        );
        """
    )
    market_id = 902
    side = deterministic_m0_side(20260717, market_id)
    winner = "DOWN" if side == "UP" else "UP"
    for observation_id, seconds_left, held_ask, held_bid in (
        (1, 250.0, .30, .29),
        (2, 220.0, .91, .90),
        (3, 200.0, .81, .80),
    ):
        values = {
            "up_ask": .70, "up_bid": .69,
            "down_ask": .70, "down_bid": .69,
        }
        values[f"{side.lower()}_ask"] = held_ask
        values[f"{side.lower()}_bid"] = held_bid
        db.execute(
            """INSERT INTO observations VALUES(
                   ?, ?, 1002, ?, 100, 100, ?, ?, ?, ?, ?,
                   100, 100, 100, 100, 0, 0
               )""",
            (
                observation_id,
                f"2026-01-01T00:00:{observation_id:02d}+00:00",
                market_id, seconds_left,
                values["up_ask"], values["up_bid"],
                values["down_ask"], values["down_bid"],
            ),
        )
    db.execute(
        "INSERT INTO market_settlements VALUES (?, 'OFFICIAL', ?)",
        (market_id, winner),
    )
    db.commit()
    db.close()

    report = run_t180_advanced_exit_sweep(path)
    partial = report["summaries"]["PARTIAL_TP_0.85_P50"]["overall"]
    trailing = report["summaries"]["TRAIL_ARM_0.80_D10"]["overall"]

    assert partial["exitFills"] == 1
    assert partial["averageExitedFraction"] == pytest.approx(.50)
    assert trailing["exitFills"] == 1
    assert trailing["realizedPnl"] > partial["realizedPnl"]
    assert report["selection"]["selectedVariant"].startswith("TRAIL_ARM_")


def test_observer_replay_is_causal_and_warms_up_before_all_profiles(tmp_path: Path):
    path = tmp_path / "observer-history.db"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE observations(
            id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, topic_id INTEGER NOT NULL,
            market_id INTEGER NOT NULL, start_price REAL NOT NULL,
            spot_price REAL NOT NULL, seconds_left REAL NOT NULL,
            up_ask REAL, up_bid REAL, down_ask REAL, down_bid REAL,
            up_ask_size REAL, down_ask_size REAL,
            book_skew_ms REAL, book_age_ms REAL
        );
        CREATE TABLE market_settlements(
            market_id INTEGER PRIMARY KEY, status TEXT NOT NULL, official_winner TEXT
        );
        """
    )
    for index in range(8):
        market_id = 200 + index
        db.execute(
            "INSERT INTO observations VALUES (?, ?, ?, ?, 100, 100.01, 250, .30, .29, .30, .29, 100, 100, 0, 0)",
            (index + 1, f"2026-01-01T00:{index * 5:02d}:50+00:00", 5000 + market_id, market_id),
        )
        db.execute(
            "INSERT INTO market_settlements VALUES (?, 'OFFICIAL', ?)",
            (market_id, "UP" if index % 2 == 0 else "DOWN"),
        )
    db.commit()
    db.close()

    report = run_observer_replay(path)
    assert report["observer"]["causalSettlement"] is True
    assert report["summaries"]["M01"]["overall"]["trades"] == 8
    for strategy in ("M01O", "M01O_F1", "M01O_LIVE"):
        profile_trades = [trade for trade in report["trades"] if trade["strategy"] == strategy]
        assert [trade["market_id"] for trade in profile_trades] == [206, 207]
    assert report["observer"]["blockCategories"]["F2"]["NOT_READY"] >= 6
