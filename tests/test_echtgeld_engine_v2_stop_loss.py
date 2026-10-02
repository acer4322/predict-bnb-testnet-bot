from __future__ import annotations

from pathlib import Path

import pytest

from predict_bot import echtgeld_engine_v1 as v1
from predict_bot import echtgeld_engine_v2 as v2


class _FakeExecutor:
    def __init__(self, config) -> None:
        self.config = config
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def available_balance_snapshot(self) -> dict:
        return {"status": "OK", "availableUsdt": 100.0, "source": "test"}

    def execute(self, **_kwargs) -> dict:
        raise AssertionError("stop-loss unit tests must never touch live execution")


def _engine(tmp_path: Path) -> v2.EchtgeldEngine:
    return v2.EchtgeldEngine(
        tmp_path / "echtgeld.db",
        executor_factory=_FakeExecutor,
        start_worker=False,
        settlement_db_path=tmp_path / "missing-settlement.db",
    )


def test_stop_loss_setting_is_durable_across_restart(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    state = engine.update_settings({"stopLossUsdt": 5.25})
    assert state["riskControl"]["enabled"] is True
    assert state["riskControl"]["stopLossUsdt"] == pytest.approx(5.25)
    assert state["config"]["stopLossUsdt"] == pytest.approx(5.25)
    engine.close()

    restarted = _engine(tmp_path)
    try:
        assert restarted.stop_loss_usdt == pytest.approx(5.25)
        assert restarted.health()["stopLossEnabled"] is True
        assert restarted.health()["stopLossUsdt"] == pytest.approx(5.25)
    finally:
        restarted.close()


def test_breached_stop_loss_auto_pauses_armed_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine(tmp_path)
    try:
        engine.stop_loss_usdt = 5.0
        engine._persist_risk_control()
        engine.armed = True
        monkeypatch.setattr(
            engine,
            "_performance_snapshot",
            lambda **_kwargs: {"netPnlUsdt": -5.01},
        )

        risk = engine._enforce_stop_loss(force_sync=True)

        assert risk["tripped"] is True
        assert engine.armed is False
        assert engine.stop_loss_last_triggered_net_pnl_usdt == pytest.approx(-5.01)
        events = engine.events(20)
        assert any(row["event_type"] == "AUTO_PAUSED_STOP_LOSS" for row in events)
    finally:
        engine.close()


def test_resume_is_blocked_while_current_pnl_is_beyond_stop_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine(tmp_path)
    try:
        engine.stop_loss_usdt = 3.0
        engine._persist_risk_control()
        monkeypatch.setattr(
            engine,
            "_performance_snapshot",
            lambda **_kwargs: {"netPnlUsdt": -3.0},
        )

        with pytest.raises(v1.EchtgeldEngineError, match="Cannot resume Echtgeld"):
            engine.resume()
        assert engine.armed is False
    finally:
        engine.close()


def test_zero_stop_loss_is_true_off_switch_without_performance_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine(tmp_path)
    try:
        engine.stop_loss_usdt = 0.0

        def forbidden_performance(**_kwargs):
            raise AssertionError("disabled stop loss must not read settlement/PnL on the live guard path")

        monkeypatch.setattr(engine, "_performance_snapshot", forbidden_performance)
        risk = engine._enforce_stop_loss(force_sync=True)

        assert risk["enabled"] is False
        assert risk["tripped"] is False
        assert risk["stopLossUsdt"] == 0.0
    finally:
        engine.close()


def test_operator_can_disable_tripped_guard_while_paused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine(tmp_path)
    try:
        engine.stop_loss_usdt = 2.0
        engine._persist_risk_control()
        monkeypatch.setattr(
            engine,
            "_performance_snapshot",
            lambda **_kwargs: {"netPnlUsdt": -4.0},
        )
        with pytest.raises(v1.EchtgeldEngineError):
            engine.resume()

        # update_settings is allowed only while PAUSED. Zero means the guard is
        # intentionally disabled, so resume must no longer consult PnL.
        engine.update_settings({"stopLossUsdt": 0})
        state = engine.resume()
        assert state["armed"] is True
        assert state["riskControl"]["enabled"] is False
    finally:
        engine.close()
