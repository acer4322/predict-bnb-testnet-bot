from __future__ import annotations

import sqlite3

from predict_bot import predict_wallet_shadow_observer as base
from predict_bot.predict_wallet_shadow_observer_v4_2 import (
    COHORT,
    WalletShadowObserver,
    effective_taker_cost,
)


def _simulation_db(path) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE strategy_m_market_sequence(
            sequence_no INTEGER, market_id INTEGER, start_ms INTEGER
        );
        CREATE TABLE observations(
            id INTEGER PRIMARY KEY, market_id INTEGER, start_price REAL,
            spot_price REAL, seconds_left REAL, spot_age_ms REAL, timestamp TEXT
        );
        INSERT INTO strategy_m_market_sequence VALUES(1,7001,1000000);
        INSERT INTO observations VALUES(9,7001,100.0,101.0,9.5,20.0,'now');
        """
    )
    db.commit()
    db.close()


def test_forward_cohort_is_causal_fee_inclusive_and_one_entry(tmp_path, monkeypatch) -> None:
    sim = tmp_path / "simulation.db"
    _simulation_db(sim)
    monkeypatch.setattr(base, "_now_ms", lambda: 1_290_500)
    observer = WalletShadowObserver(tmp_path / "shadow.db", sim)
    try:
        observer.market_id = 123
        observer.bucket_start_sec = 1000
        observer._advance_spot_strike(
            {"secondsLeft": 9.5, "receivedTimestampMs": 1_290_490, "upAsk": 0.70, "downAsk": 0.30}
        )
        observer._advance_spot_strike(
            {"secondsLeft": 9.4, "receivedTimestampMs": 1_290_500, "upAsk": 0.71, "downAsk": 0.29}
        )
        rows = observer.db.execute(
            "SELECT * FROM wallet_spot_strike_forward_events WHERE cohort=?", (COHORT,)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["side"] == "UP"
        assert rows[0]["effective_unit_cost"] == effective_taker_cost(0.70)
        assert rows[0]["source_observation_id"] == 9
        decisions = observer.db.execute(
            "SELECT * FROM wallet_spot_strike_forward_decisions WHERE cohort=?", (COHORT,)
        ).fetchall()
        assert len(decisions) == 1
        assert decisions[0]["decision"] == "TRADE"
    finally:
        observer.stop()


def test_stale_predict_book_fails_closed(tmp_path, monkeypatch) -> None:
    sim = tmp_path / "simulation.db"
    _simulation_db(sim)
    monkeypatch.setattr(base, "_now_ms", lambda: 1_290_500)
    observer = WalletShadowObserver(tmp_path / "shadow.db", sim)
    try:
        observer.market_id = 123
        observer.bucket_start_sec = 1000
        observer._advance_spot_strike(
            {"secondsLeft": 9.5, "receivedTimestampMs": 1_280_000, "upAsk": 0.70, "downAsk": 0.30}
        )
        assert observer.forward_event is None
        assert observer.forward_last_block["reason"] == "STALE_PREDICT_BOOK"
        observer._advance_spot_strike(
            {"secondsLeft": 8.5, "receivedTimestampMs": 1_290_500, "upAsk": 0.70, "downAsk": 0.30}
        )
        assert observer.forward_event is None
        decisions = observer.db.execute(
            "SELECT * FROM wallet_spot_strike_forward_decisions WHERE cohort=?", (COHORT,)
        ).fetchall()
        assert len(decisions) == 1
    finally:
        observer.stop()


def test_late_restart_cannot_enter_after_decision_window(tmp_path, monkeypatch) -> None:
    sim = tmp_path / "simulation.db"
    _simulation_db(sim)
    monkeypatch.setattr(base, "_now_ms", lambda: 1_293_200)
    observer = WalletShadowObserver(tmp_path / "shadow.db", sim)
    try:
        observer.market_id = 123
        observer.bucket_start_sec = 1000
        observer._advance_spot_strike(
            {"secondsLeft": 6.8, "receivedTimestampMs": 1_293_190, "upAsk": 0.70, "downAsk": 0.30}
        )
        assert observer.forward_event is None
        assert observer.forward_decision["decision"] == "SKIP"
        assert observer.forward_decision["reason"] == "MISSED_DECISION_WINDOW"
    finally:
        observer.stop()


def test_official_predict_result_settles_net_roi(tmp_path, monkeypatch) -> None:
    sim = tmp_path / "simulation.db"
    _simulation_db(sim)
    monkeypatch.setattr(base, "_now_ms", lambda: 1_290_500)
    observer = WalletShadowObserver(tmp_path / "shadow.db", sim)
    try:
        observer.market_id = 123
        observer.bucket_start_sec = 1000
        observer._register_v1_market(123)
        observer._advance_spot_strike(
            {"secondsLeft": 9.5, "receivedTimestampMs": 1_290_490, "upAsk": 0.70, "downAsk": 0.30}
        )
        observer._store_market_result(123, {"title": "BTC"}, "UP")
        result = observer.db.execute(
            "SELECT * FROM wallet_spot_strike_forward_results WHERE cohort=?", (COHORT,)
        ).fetchone()
        assert result["status"] == "WIN"
        assert result["net_roi"] > 0.4
        performance = observer._forward_performance()
        assert performance["maxDrawdownUsdt"] == 0
        assert performance["longestLossStreak"] == 0
    finally:
        observer.stop()


def test_trade_decision_and_event_commit_together(tmp_path, monkeypatch) -> None:
    sim = tmp_path / "simulation.db"
    _simulation_db(sim)
    monkeypatch.setattr(base, "_now_ms", lambda: 1_290_500)
    observer = WalletShadowObserver(tmp_path / "shadow.db", sim)
    original_execute = observer.db.execute
    try:
        observer.market_id = 123
        observer.bucket_start_sec = 1000

        class FailingConnection:
            def execute(self, sql, parameters=()):
                if "INSERT OR IGNORE INTO wallet_spot_strike_forward_events" in sql:
                    raise sqlite3.OperationalError("injected event write failure")
                return original_execute(sql, parameters)

            def commit(self):
                real_db.commit()

            def rollback(self):
                real_db.rollback()

        real_db = observer.db
        observer.db = FailingConnection()
        try:
            observer._advance_spot_strike(
                {"secondsLeft": 9.5, "receivedTimestampMs": 1_290_490, "upAsk": 0.70, "downAsk": 0.30}
            )
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("injected write failure should propagate")
        finally:
            observer.db = real_db
        assert observer.forward_decision is None
        assert real_db.execute(
            "SELECT COUNT(*) FROM wallet_spot_strike_forward_decisions"
        ).fetchone()[0] == 0
        assert real_db.execute(
            "SELECT COUNT(*) FROM wallet_spot_strike_forward_events"
        ).fetchone()[0] == 0
    finally:
        observer.stop()
