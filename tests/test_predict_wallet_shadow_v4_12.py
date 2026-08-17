from predict_bot.predict_wallet_shadow_observer_v4_12 import WalletShadowObserver


def test_v4_12_registers_integrated_target_core_forward_cohort(tmp_path) -> None:
    observer = WalletShadowObserver(tmp_path / "shadow.db", tmp_path / "missing.db")
    try:
        row = observer.db.execute(
            "SELECT deployed_at_ms,excluded_market_id,policy_json "
            "FROM wallet_maker_inventory_shared_v1_meta WHERE cohort=?",
            ("TARGET_CORE_INTEGRATED_V1",),
        ).fetchone()
        assert row is not None
        assert int(row["deployed_at_ms"]) > 0
        assert row["excluded_market_id"] is None
        assert '"paperOnly":true' in row["policy_json"]
        assert '"targetCoreIntegrated":{"enabled":true' in row["policy_json"]
        assert '"hardCorridorShares":108.0' in row["policy_json"]
        assert '"principalUsdtRange":[1.0,15.0]' in row["policy_json"]

        performance = observer._shared_performance("TARGET_CORE_INTEGRATED_V1")
        assert performance["settledMarkets"] == 0
        assert performance["makerFills"] == 0
        assert performance["takerFills"] == 0

        health = observer.health_snapshot()
        assert health["version"] == "PREDICT_WALLET_SHADOW_V0_15_TARGET_CORE_INTEGRATED_PAPER"
        assert health["paperOnly"] is True
        assert health["liveOrdersAffected"] is False
    finally:
        for timer in observer.mirror_timers:
            timer.cancel()
        observer.http.close()
        observer.local_http.close()
        observer.db.close()
