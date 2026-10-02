from __future__ import annotations

from pathlib import Path

import pytest

from predict_bot.poly_gap_live_v23 import DelayedEntryPolyGapLiveEngine
from predict_bot.poly_gap_live_v24 import TieredLossGuardPolyGapLiveEngine


def _insert_settled(engine: TieredLossGuardPolyGapLiveEngine, *, pnl: float, market_id: int) -> int:
    now = 1_800_000_000_000 + market_id
    with engine.db_lock:
        cursor = engine.db.execute(
            """INSERT INTO poly_gap_live_rounds(
                   market_id,topic_id,round_no,side,token_id,state,stake_usdt,
                   pnl_usdt,created_at_ms,updated_at_ms,market_end_ms
               ) VALUES(?,?,?,?,?,'SETTLED',?,?,?,?,?)""",
            (
                market_id,
                market_id + 1000,
                1,
                "UP",
                f"token-{market_id}",
                1.0,
                float(pnl),
                now,
                now,
                now + 300_000,
            ),
        )
        engine.db.commit()
        return int(cursor.lastrowid)


def _configure(engine: TieredLossGuardPolyGapLiveEngine) -> None:
    engine.update_settings(
        {
            "stakeUsdt": 1.0,
            "reduceLossEnabled": True,
            "reduceLossUsdt": 3.0,
            "reducedStakeUsdt": 0.25,
            "maximumLossEnabled": True,
            "maximumLossUsdt": 10.0,
        }
    )


def test_v24_builds_on_v23() -> None:
    assert issubclass(TieredLossGuardPolyGapLiveEngine, DelayedEntryPolyGapLiveEngine)


def test_reduction_threshold_latches_and_does_not_bounce_after_recovery(tmp_path: Path) -> None:
    engine = TieredLossGuardPolyGapLiveEngine(tmp_path / "reduce.db")
    try:
        _configure(engine)
        _insert_settled(engine, pnl=-3.5, market_id=101)
        engine._check_max_loss()

        reduced = engine._loss_state()
        assert reduced["currentLossUsdt"] == pytest.approx(3.5)
        assert reduced["reductionTripped"] is True
        assert reduced["tripped"] is False
        assert reduced["phase"] == "REDUCED"
        assert reduced["effectiveStakeUsdt"] == pytest.approx(0.25)

        _insert_settled(engine, pnl=2.5, market_id=102)
        recovered = engine._loss_state()
        assert recovered["currentLossUsdt"] == pytest.approx(1.0)
        assert recovered["reductionTripped"] is True
        assert recovered["phase"] == "REDUCED"
        assert recovered["effectiveStakeUsdt"] == pytest.approx(0.25)
    finally:
        engine.stop()


def test_new_round_uses_reduced_stake_after_stage_one(tmp_path: Path) -> None:
    engine = TieredLossGuardPolyGapLiveEngine(tmp_path / "stake.db")
    try:
        _configure(engine)
        _insert_settled(engine, pnl=-3.1, market_id=201)
        engine._check_max_loss()

        row = engine._insert_round(
            market={
                "market_id": 202,
                "topic_id": 1202,
                "start_ms": 1_800_000_300_000,
                "end_ms": 1_800_000_600_000,
                "up_token_id": "up-202",
                "down_token_id": "down-202",
                "fee_rate_bps": 200,
            },
            side="UP",
            token_id="up-202",
            stake=1.0,
            poly_selected=0.70,
            ask=0.60,
            edge=0.10,
        )

        assert float(row["stake_usdt"]) == pytest.approx(0.25)
        assert int(row["market_end_ms"]) == 1_800_000_600_000
    finally:
        engine.stop()


def test_hard_stop_remains_second_stage_and_pauses_new_entries(tmp_path: Path) -> None:
    engine = TieredLossGuardPolyGapLiveEngine(tmp_path / "stop.db")
    try:
        _configure(engine)
        engine._set_setting("runtime_enabled", "1")
        _insert_settled(engine, pnl=-10.25, market_id=301)
        engine._check_max_loss()

        state = engine._loss_state()
        assert state["reductionTripped"] is True
        assert state["tripped"] is True
        assert state["phase"] == "STOPPED"
        assert engine._setting("runtime_enabled", "1") == "0"
    finally:
        engine.stop()


def test_loss_reset_clears_both_stage_latches_and_counter(tmp_path: Path) -> None:
    engine = TieredLossGuardPolyGapLiveEngine(tmp_path / "reset.db")
    try:
        _configure(engine)
        _insert_settled(engine, pnl=-10.25, market_id=401)
        engine._check_max_loss()
        assert engine._loss_state()["phase"] == "STOPPED"

        engine.update_settings({"resetLoss": True})
        state = engine._loss_state()
        assert state["currentLossUsdt"] == pytest.approx(0.0)
        assert state["reductionTripped"] is False
        assert state["tripped"] is False
        assert state["phase"] == "NORMAL"
    finally:
        engine.stop()


def test_startup_reconstructs_reduction_latch_from_persistent_loss(tmp_path: Path) -> None:
    db_path = tmp_path / "restart.db"
    first = TieredLossGuardPolyGapLiveEngine(db_path)
    try:
        _configure(first)
        _insert_settled(first, pnl=-3.5, market_id=501)
        # Simulate upgrading/restarting before an old process had a chance to run
        # the new reduction check.
        first._set_setting("loss_reduced", "0")
    finally:
        first.stop()

    restarted = TieredLossGuardPolyGapLiveEngine(db_path)
    try:
        state = restarted._loss_state()
        assert state["currentLossUsdt"] == pytest.approx(3.5)
        assert state["reductionTripped"] is True
        assert state["effectiveStakeUsdt"] == pytest.approx(0.25)
    finally:
        restarted.stop()


def test_tiered_settings_require_smaller_stake_and_earlier_threshold(tmp_path: Path) -> None:
    engine = TieredLossGuardPolyGapLiveEngine(tmp_path / "validation.db")
    try:
        with pytest.raises(ValueError, match="reducedStakeUsdt"):
            engine.update_settings(
                {
                    "stakeUsdt": 1.0,
                    "reduceLossEnabled": True,
                    "reduceLossUsdt": 3.0,
                    "reducedStakeUsdt": 1.5,
                    "maximumLossEnabled": True,
                    "maximumLossUsdt": 10.0,
                }
            )
        with pytest.raises(ValueError, match="reduceLossUsdt"):
            engine.update_settings(
                {
                    "stakeUsdt": 1.0,
                    "reduceLossEnabled": True,
                    "reduceLossUsdt": 10.0,
                    "reducedStakeUsdt": 0.25,
                    "maximumLossEnabled": True,
                    "maximumLossUsdt": 10.0,
                }
            )
    finally:
        engine.stop()


def test_snapshot_and_supervisor_expose_v24(tmp_path: Path) -> None:
    engine = TieredLossGuardPolyGapLiveEngine(tmp_path / "snapshot.db")
    try:
        state = engine.snapshot()
        assert state["version"] == "POLY_GAP_DEDICATED_LIVE_V24"
        assert state["tieredLossGuard"]["stage1"] == "REDUCE_NEW_ROUND_STAKE"
        assert state["tieredLossGuard"]["stage2"] == "STOP_NEW_ROUNDS"
        assert state["tieredLossGuard"]["openPositionExitManagementUnaffected"] is True
    finally:
        engine.stop()

    supervisor = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "predict_bot"
        / "supervisor.py"
    ).read_text(encoding="utf-8")
    assert "predict_bot.poly_gap_live_v23" in supervisor
    assert "predict_bot.poly_gap_live_v24" in supervisor
