from predict_bot.predict_wallet_shadow_observer_v4_7 import WalletShadowObserver


def test_health_snapshot_is_lightweight_and_separate_from_full_state(tmp_path) -> None:
    observer = WalletShadowObserver(tmp_path / "shadow.db", tmp_path / "missing-simulation.db")
    try:
        observer.market_id = 123
        observer.api_key = "test"
        observer.last_poll_ms = 1_000
        observer.last_error = None
        observer.signal_collector_status = {"status": "LIVE", "sampleAgeMs": 25}
        health = observer.health_snapshot()
        assert health["version"].startswith("PREDICT_WALLET_SHADOW_")
        assert health["status"] == "LIVE"
        assert health["marketId"] == 123
        assert health["collector8777"]["status"] == "LIVE"
        assert "makerInventoryTakerSharedLab" not in health
        assert "target" not in health
    finally:
        observer.http.close()
        observer.db.close()
