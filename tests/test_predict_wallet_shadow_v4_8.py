from predict_bot.predict_wallet_shadow_observer_v4_8 import WalletShadowObserver
from predict_bot.predict_wallet_wide_maker_flow_strategy import COHORTS


def test_v4_8_schema_and_health_are_paper_only(tmp_path) -> None:
    observer = WalletShadowObserver(tmp_path / "shadow.db", tmp_path / "missing.db")
    try:
        tables = {
            row[0] for row in observer.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'wallet_wide_maker_flow_v1_%'"
            )
        }
        assert "wallet_wide_maker_flow_v1_orders" in tables
        assert "wallet_wide_maker_flow_v1_results" in tables
        health = observer.health_snapshot()
        assert health["version"] == "PREDICT_WALLET_SHADOW_V0_11_WIDE_MAKER_FLOW_TAIL_PAPER"
        assert health["paperOnly"] is True
        assert health["liveOrdersAffected"] is False
    finally:
        observer.http.close()
        observer.db.close()


def test_v4_8_result_settlement_writes_all_ledger_columns(tmp_path) -> None:
    observer = WalletShadowObserver(tmp_path / "shadow.db", tmp_path / "missing.db")
    cohort = COHORTS[0]["cohort"]
    try:
        observer.db.execute(
            """INSERT INTO wallet_wide_maker_flow_v1_markets
               (cohort,market_id,title,started_at_ms,initialization_status,initialized_at_ms,
                initial_reserved_usdt,peak_reserved_usdt)
               VALUES (?,?,?,?,?,?,?,?)""",
            (cohort, 42, "BTC test", 1, "INITIALIZED", 2, 12.5, 15.0),
        )
        observer.db.commit()

        observer._store_market_result(42, {"title": "BTC test"}, "UP")

        row = observer.db.execute(
            "SELECT status,total_cost_usdt,net_pnl_usdt,initial_reserved_usdt,peak_reserved_usdt "
            "FROM wallet_wide_maker_flow_v1_results WHERE cohort=? AND market_id=?",
            (cohort, 42),
        ).fetchone()
        assert tuple(row) == ("NO_TRADE", 0.0, 0.0, 12.5, 15.0)
    finally:
        observer.http.close()
        observer.db.close()
