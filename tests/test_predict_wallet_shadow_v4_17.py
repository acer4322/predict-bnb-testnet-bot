from __future__ import annotations

from predict_bot import predict_wallet_reconstructed_maker_strategy_v3 as strategy_v3
from predict_bot.predict_wallet_shadow_observer_v4_17 import VERSION, WalletShadowObserver


def close_observer(observer: WalletShadowObserver) -> None:
    thread = getattr(observer, "_report_thread", None)
    if thread is not None and thread.is_alive():
        thread.join(timeout=2)
    for timer in getattr(observer, "mirror_timers", []):
        timer.cancel()
    observer.http.close()
    observer.local_http.close()
    observer.db.close()


def test_v4_17_registers_forward_only_lifecycle_cohort(tmp_path) -> None:
    observer = WalletShadowObserver(tmp_path / "shadow.db", tmp_path / "missing.db")
    try:
        with observer.db_lock:
            meta = observer.db.execute(
                "SELECT policy_json FROM wallet_reconstructed_maker_v1_meta WHERE cohort=?",
                (strategy_v3.COHORT,),
            ).fetchone()
            plan_table = observer.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wallet_reconstructed_maker_lifecycle_v3_plans'"
            ).fetchone()
        assert meta is not None
        assert plan_table is not None
        assert observer.lifecycle_v3_state["pendingPlans"] == {}
        health = observer.health_snapshot()
        assert health["version"] == VERSION
        assert health["reconstructedMakerLifecycleV3Cohort"] == strategy_v3.COHORT
        assert strategy_v3.policy()["targetEventsDriveStrategy"] is False
        assert strategy_v3.policy()["controlCohort"] == "TARGET_MAKER_RULES_GRID18_SOFTPOOL_V2"
    finally:
        close_observer(observer)
