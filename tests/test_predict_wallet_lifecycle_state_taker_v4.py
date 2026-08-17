from __future__ import annotations

from predict_bot import predict_wallet_lifecycle_state_taker_strategy_v4 as strategy
from predict_bot.predict_wallet_shadow_observer_v4_18 import VERSION, WalletShadowObserver


def snapshot(**changes):
    row = {
        "market_id": 10,
        "timestamp_ns": 101,
        "sampled_at_ms": 10_000,
        "predict_receipt_age_ms": 50,
        "seconds_left": 180,
        "predict_up_bid": 0.50,
        "predict_up_ask": 0.52,
        "predict_down_bid": 0.50,
        "predict_down_ask": 0.52,
        "direction_score": 0.9,
        "futures_return_1s_bps": 1.0,
        "futures_taker_imbalance_1s": 0.9,
        "futures_queue_imbalance": 0.9,
        "spot_return_1s_bps": 1.0,
        "spot_taker_imbalance_1s": 0.9,
    }
    row.update(changes)
    return row


def eligibility(**changes):
    row = {"openedAtMs": 9_900, "expiresAtMs": 14_900, "openedSnapshotNs": 100}
    row.update(changes)
    return row


def close_observer(observer: WalletShadowObserver) -> None:
    thread = getattr(observer, "_report_thread", None)
    if thread is not None and thread.is_alive():
        thread.join(timeout=2)
    for timer in getattr(observer, "mirror_timers", []):
        timer.cancel()
    observer.http.close()
    observer.local_http.close()
    observer.db.close()


def test_thresholds_are_regime_specific_forward_heuristics() -> None:
    assert strategy.score_threshold(250) == (0.50, "EARLY_GT200")
    assert strategy.score_threshold(120) == (0.45, "MID_60_200")
    assert strategy.score_threshold(45) == (0.35, "LATE_LE60")
    assert "heuristic" in strategy.policy()["publicSignal"]["thresholdStatus"]


def test_same_fill_snapshot_cannot_take() -> None:
    inv = strategy.inventory_state(maker_up_shares=18, maker_down_shares=18)
    decision = strategy.decide(
        snapshot(timestamp_ns=100), inv, eligibility(openedSnapshotNs=100), expected_market_id=10, now_ms=10_050
    )
    assert decision["decision"] == "SKIP"
    assert decision["reason"] == "WAIT_NEXT_PUBLIC_SNAPSHOT_AFTER_FILL"


def test_public_signal_can_continue_heavy_side_without_inventory_hard_block() -> None:
    inv = strategy.inventory_state(maker_up_shares=36, maker_down_shares=18)
    decision = strategy.decide(snapshot(), inv, eligibility(), expected_market_id=10, now_ms=10_050)
    assert inv["heavySide"] == "UP"
    assert decision["side"] == "UP"
    assert decision["sameAsHeavySide"] is True
    assert decision["decision"] == "TRADE"
    assert decision["intent"] == "DIRECTIONAL_TAKER"
    assert decision["inventoryScale"] == 1.0


def test_repair_requires_public_signal_to_point_to_underweight_side() -> None:
    inv = strategy.inventory_state(maker_up_shares=36, maker_down_shares=18)
    decision = strategy.decide(
        snapshot(
            direction_score=-0.9,
            futures_return_1s_bps=-1.0,
            futures_taker_imbalance_1s=-0.9,
            futures_queue_imbalance=-0.9,
            spot_return_1s_bps=-1.0,
            spot_taker_imbalance_1s=-0.9,
        ),
        inv,
        eligibility(),
        expected_market_id=10,
        now_ms=10_050,
    )
    assert inv["repairSide"] == "DOWN"
    assert decision["side"] == "DOWN"
    assert decision["decision"] == "TRADE"
    assert decision["intent"] == "INVENTORY_REPAIR_TAKER"
    assert decision["inventoryScale"] == 1.25


def test_execution_reuses_existing_target_core_sizing() -> None:
    inv = strategy.inventory_state(maker_up_shares=18, maker_down_shares=18)
    snap = snapshot()
    decision = strategy.decide(snap, inv, eligibility(), expected_market_id=10, now_ms=10_050)
    fill = strategy.execution(decision, snap)
    assert fill is not None
    assert 1.0 <= fill["principalUsdt"] <= 15.0
    assert fill["feeUsdt"] > 0


def test_v4_18_registers_paper_only_overlay_schema(tmp_path) -> None:
    observer = WalletShadowObserver(tmp_path / "shadow.db", tmp_path / "missing.db")
    try:
        with observer.db_lock:
            meta = observer.db.execute(
                "SELECT policy_json FROM wallet_lifecycle_state_taker_v4_meta WHERE cohort=?",
                (strategy.COHORT,),
            ).fetchone()
            event_table = observer.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wallet_lifecycle_state_taker_v4_events'"
            ).fetchone()
        assert meta is not None
        assert event_table is not None
        health = observer.health_snapshot()
        assert health["version"] == VERSION
        assert health["lifecycleStateTakerV4Cohort"] == strategy.COHORT
        assert health["paperOnly"] is True
        assert health["liveOrdersAffected"] is False
    finally:
        close_observer(observer)
