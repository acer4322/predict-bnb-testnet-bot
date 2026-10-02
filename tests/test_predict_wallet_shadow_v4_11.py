import sqlite3

from predict_bot import predict_wallet_shadow_observer_v4_11 as module
from predict_bot.predict_wallet_shadow_observer_v4_11 import WalletShadowObserver


def test_v4_11_registers_forward_only_target_taker_mirror(tmp_path) -> None:
    observer = WalletShadowObserver(tmp_path / "shadow.db", tmp_path / "missing.db")
    try:
        row = observer.db.execute(
            "SELECT deployed_at_ms,excluded_market_id,policy_json "
            "FROM wallet_target_taker_mirror_meta WHERE cohort='TARGET_TAKER_MIRROR_AUDIT_V1'"
        ).fetchone()
        assert row is not None
        assert int(row["deployed_at_ms"]) > 0
        assert row["excluded_market_id"] is None
        assert '"paperOnly":true' in row["policy_json"]
        assert '"forwardOnly":true' in row["policy_json"]
        assert '"horizonsMsAfterFirstDetection":[0,250,500,1000,2000]' in row["policy_json"]

        performance = observer._mirror_performance()
        assert performance["targetParents"] == 0
        assert len(performance["matrix"]) == 20

        health = observer.health_snapshot()
        assert health["version"] == "PREDICT_WALLET_SHADOW_V0_14_TARGET_TAKER_MIRROR_AUDIT_PAPER"
        assert health["paperOnly"] is True
        assert health["liveOrdersAffected"] is False
    finally:
        for timer in observer.mirror_timers:
            timer.cancel()
        observer.http.close()
        observer.local_http.close()
        observer.db.close()


def test_strict_pre_signal_never_uses_post_event_sample(tmp_path, monkeypatch) -> None:
    signal_db = tmp_path / "signals.db"
    db = sqlite3.connect(signal_db)
    db.execute(
        "CREATE TABLE wallet_taker_signal_snapshots("
        "market_id INTEGER,sampled_at_ms INTEGER,direction_score REAL)"
    )
    db.executemany(
        "INSERT INTO wallet_taker_signal_snapshots VALUES (?,?,?)",
        [(7, 9_999, -0.4), (7, 10_001, 0.9)],
    )
    db.commit()
    db.close()
    monkeypatch.setattr(module.signal_collector, "DB_PATH", signal_db)

    sampled, lead, context = WalletShadowObserver._strict_pre_signal(7, 10_000)
    assert sampled == 9_999
    assert lead == 1
    assert context is not None
    assert context["direction_score"] == -0.4
