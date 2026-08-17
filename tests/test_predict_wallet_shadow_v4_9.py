from predict_bot.predict_wallet_shadow_observer_v4_9 import WalletShadowObserver


def test_v4_9_registers_isolated_pooled_forward_cohort(tmp_path) -> None:
    observer = WalletShadowObserver(tmp_path / "shadow.db", tmp_path / "missing.db")
    try:
        row = observer.db.execute(
            "SELECT deployed_at_ms,excluded_market_id,policy_json "
            "FROM wallet_maker_inventory_shared_v1_meta WHERE cohort=?",
            ("RECENTERED_POOLED_INVENTORY_V1",),
        ).fetchone()
        assert row is not None
        assert int(row["deployed_at_ms"]) > 0
        assert row["excluded_market_id"] is None
        assert '"paperOnly":true' in row["policy_json"]
        assert '"pooledInventory"' in row["policy_json"]

        performance = observer._shared_performance("RECENTERED_POOLED_INVENTORY_V1")
        assert performance["settledMarkets"] == 0
        assert performance["finalMakerPairedCoverageMedian"] is None

        health = observer.health_snapshot()
        assert health["version"] == "PREDICT_WALLET_SHADOW_V0_12_RECENTERED_POOLED_INVENTORY_PAPER"
        assert health["paperOnly"] is True
        assert health["liveOrdersAffected"] is False
    finally:
        observer.http.close()
        observer.db.close()
