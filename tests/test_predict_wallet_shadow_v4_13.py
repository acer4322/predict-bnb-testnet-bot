from predict_bot.predict_wallet_reconstructed_maker_strategy import COHORT
from predict_bot.predict_wallet_shadow_observer_v4_13 import VERSION, WalletShadowObserver


def close_observer(observer: WalletShadowObserver) -> None:
    for timer in observer.mirror_timers:
        timer.cancel()
    observer.http.close()
    observer.local_http.close()
    observer.db.close()


def test_v4_13_registers_reconstructed_maker_forward_cohort(tmp_path) -> None:
    observer = WalletShadowObserver(tmp_path / "shadow.db", tmp_path / "missing.db")
    try:
        row = observer.db.execute(
            "SELECT deployed_at_ms,excluded_market_id,policy_json "
            "FROM wallet_reconstructed_maker_v1_meta WHERE cohort=?",
            (COHORT,),
        ).fetchone()
        assert row is not None
        assert int(row["deployed_at_ms"]) > 0
        assert row["excluded_market_id"] is None
        assert '"paperOnly":true' in row["policy_json"]
        assert '"historicalBackfill":false' in row["policy_json"]
        assert '"targetEventsDriveStrategy":false' in row["policy_json"]
        assert '"liveOrdersAffected":false' in row["policy_json"]

        observer._reset_market(501, None, "deployment market")
        assert observer.reconstructed_state["active"] is False
        assert observer.reconstructed_state["excludedMarketId"] == 501
        observer._reset_market(502, None, "first complete forward market")
        assert observer.reconstructed_state["active"] is True

        performance = observer._reconstructed_performance()
        assert performance["settledMarkets"] == 0
        assert performance["fills"] == 0

        snapshot = observer.snapshot()
        lab = snapshot["reconstructedMakerRulesLab"]
        assert lab["cohort"] == COHORT
        assert lab["paperOnly"] is True
        assert lab["forwardOnly"] is True
        assert lab["targetEventsDriveStrategy"] is False
        assert lab["liveOrdersAffected"] is False

        health = observer.health_snapshot()
        assert health["version"] == VERSION
        assert health["paperOnly"] is True
        assert health["liveOrdersAffected"] is False
    finally:
        close_observer(observer)
