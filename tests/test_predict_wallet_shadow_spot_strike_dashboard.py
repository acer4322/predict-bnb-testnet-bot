from __future__ import annotations

from predict_bot import predict_wallet_shadow_observer as base
from predict_bot.predict_wallet_shadow_observer_v4_2 import COHORT
from predict_bot.predict_wallet_shadow_observer_v4_3 import WalletShadowObserver


def test_missing_simulation_db_degrades_only_spot_strike(tmp_path) -> None:
    observer = WalletShadowObserver(tmp_path / "shadow.db", tmp_path / "missing-simulation.db")
    try:
        assert observer.simulation_db is None
        snapshot = observer.snapshot()
        assert snapshot["spotStrikeForward"]["availability"]["simulationDbAvailable"] is False
        assert snapshot["spotStrikeForward"]["paperOnly"] is True
    finally:
        observer.stop()


def test_spot_strike_tables_follow_wallet_retention(tmp_path, monkeypatch) -> None:
    now = 2_000_000_000
    monkeypatch.setattr(base, "_now_ms", lambda: now)
    observer = WalletShadowObserver(tmp_path / "shadow.db", tmp_path / "missing-simulation.db")
    try:
        observer.retention_ms = 1_000
        old = now - 10_000
        fresh = now
        with observer.db_lock:
            for market_id, at_ms in ((1, old), (2, fresh)):
                observer.db.execute(
                    """INSERT INTO wallet_spot_strike_forward_decisions(
                           cohort,market_id,market_bucket,decision_at_ms,seconds_left,decision,reason,
                           side,observed_ask,start_price,spot_price,displacement_bps,spot_age_ms,
                           prediction_receipt_age_ms,source_market_id,source_observation_id,payload_json
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (COHORT, market_id, 100, at_ms, 9.5, "TRADE", "test", "UP", 0.7,
                     100.0, 101.0, 100.0, 10.0, 10.0, 99, market_id, "{}"),
                )
                observer.db.execute(
                    """INSERT INTO wallet_spot_strike_forward_events(
                           cohort,market_id,market_bucket,decision_at_ms,seconds_left,side,
                           observed_ask,effective_unit_cost,stake_usdt,shares,start_price,spot_price,
                           displacement_bps,spot_age_ms,prediction_receipt_age_ms,source_market_id,
                           source_observation_id,payload_json
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (COHORT, market_id, 100, at_ms, 9.5, "UP", 0.7, 0.704, 1.0, 1.42,
                     100.0, 101.0, 100.0, 10.0, 10.0, 99, market_id, "{}"),
                )
                observer.db.execute(
                    """INSERT INTO wallet_spot_strike_forward_results(
                           cohort,market_id,market_bucket,winner,resolved_at_ms,side,observed_ask,
                           stake_usdt,shares,payout_usdt,net_pnl_usdt,net_roi,status
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (COHORT, market_id, 100, "UP", at_ms, "UP", 0.7, 1.0, 1.42, 1.42, 0.42, 0.42, "WIN"),
                )
            observer.db.commit()

        observer._cleanup_retention(force=True)

        with observer.db_lock:
            decision_ids = {row[0] for row in observer.db.execute(
                "SELECT market_id FROM wallet_spot_strike_forward_decisions WHERE cohort=?", (COHORT,)
            )}
            event_ids = {row[0] for row in observer.db.execute(
                "SELECT market_id FROM wallet_spot_strike_forward_events WHERE cohort=?", (COHORT,)
            )}
            result_ids = {row[0] for row in observer.db.execute(
                "SELECT market_id FROM wallet_spot_strike_forward_results WHERE cohort=?", (COHORT,)
            )}
        assert decision_ids == {2}
        assert event_ids == {2}
        assert result_ids == {2}
    finally:
        observer.stop()


def test_spot_strike_target_similarity_is_diagnostic_only(tmp_path, monkeypatch) -> None:
    now = 3_000_000_000
    monkeypatch.setattr(base, "_now_ms", lambda: now)
    observer = WalletShadowObserver(tmp_path / "shadow.db", tmp_path / "missing-simulation.db")
    try:
        decision_at = now - 1_000
        with observer.db_lock:
            observer.db.execute(
                """INSERT INTO wallet_spot_strike_forward_decisions(
                       cohort,market_id,market_bucket,decision_at_ms,seconds_left,decision,reason,
                       side,observed_ask,start_price,spot_price,displacement_bps,spot_age_ms,
                       prediction_receipt_age_ms,source_market_id,source_observation_id,payload_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (COHORT, 77, 100, decision_at, 9.5, "TRADE", "test", "UP", 0.7,
                 100.0, 101.0, 100.0, 10.0, 10.0, 99, 1, "{}"),
            )
            observer.db.execute(
                """INSERT INTO wallet_shadow_target_events(
                       leg_id,wallet,market_id,role,side,quote_type,order_hash,event_ms,price,shares,raw_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                ("target-before", observer.wallet, 77, "TAKER", "UP", "BID", "a",
                 decision_at - 100, 0.6, 12.0, "{}"),
            )
            observer.db.execute(
                """INSERT INTO wallet_shadow_target_events(
                       leg_id,wallet,market_id,role,side,quote_type,order_hash,event_ms,price,shares,raw_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                ("target-after", observer.wallet, 77, "TAKER", "DOWN", "BID", "b",
                 decision_at + 100, 0.4, 2.0, "{}"),
            )
            observer.db.commit()

        similarity = observer._forward_target_similarity()
        assert similarity["atDecisionComparable"] == 1
        assert similarity["atDecisionMatchRate"] == 1.0
        assert similarity["finalMatchRate"] == 1.0
        assert "never drive" in similarity["note"]
    finally:
        observer.stop()
