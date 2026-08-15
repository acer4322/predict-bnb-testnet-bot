from __future__ import annotations

import pytest

from predict_bot import target_taker_live_execution_v1 as live


def _decision(side: str = "UP", ask: float = 0.42) -> dict:
    return {"decision": "TRADE", "side": side, "ask": ask}


def _snapshot(market_id: int = 123) -> dict:
    return {
        "market_id": market_id,
        "bucket_start_sec": 1_800_000_000,
        "window_end_ms": 1_800_000_300_000,
    }


def test_config_defaults_are_paper_one_usdt(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        live.MODE_ENV,
        live.VENUE_ENV,
        live.NOTIONAL_ENV,
        live.COHORT_ENV,
        live.MAX_PRICE_DRIFT_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    config = live.TargetTakerLiveConfig.from_env()
    assert config.mode == "paper"
    assert config.venue == "predictfun"
    assert config.notional_usdt == pytest.approx(1.0)
    assert config.cohort == live.DEFAULT_COHORT


def test_invalid_live_configuration_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(live.MODE_ENV, "LIVE_NOW")
    with pytest.raises(live.TargetTakerLiveError):
        live.TargetTakerLiveConfig.from_env()

    monkeypatch.setenv(live.MODE_ENV, "live")
    monkeypatch.setenv(live.NOTIONAL_ENV, "0")
    with pytest.raises(live.TargetTakerLiveError):
        live.TargetTakerLiveConfig.from_env()


def test_paper_mode_never_dispatches_to_venue(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = live.TargetTakerLiveExecutor(
        live.TargetTakerLiveConfig(mode="paper", venue="predictfun", notional_usdt=1.0)
    )

    def forbidden(**_kwargs):  # pragma: no cover - assertion path
        raise AssertionError("paper mode reached live venue")

    monkeypatch.setattr(executor, "_execute_predictfun", forbidden)
    result = executor.execute(
        cohort=live.DEFAULT_COHORT,
        market_id=123,
        decision=_decision(),
        snapshot=_snapshot(),
        signal_id="signal-1",
    )
    assert result["status"] == "NOT_ARMED"


def test_only_explicitly_armed_cohort_can_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = live.TargetTakerLiveExecutor(
        live.TargetTakerLiveConfig(mode="live", venue="predictfun", notional_usdt=1.0)
    )

    def forbidden(**_kwargs):  # pragma: no cover - assertion path
        raise AssertionError("unarmed cohort reached live venue")

    monkeypatch.setattr(executor, "_execute_predictfun", forbidden)
    result = executor.execute(
        cohort="TARGET_TAKER_PUBLIC_SIDE_V1_HAZARD_SIDE",
        market_id=123,
        decision=_decision(),
        snapshot=_snapshot(),
        signal_id="signal-2",
    )
    assert result["status"] == "COHORT_NOT_ARMED"


def test_market_mismatch_blocks_before_venue(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = live.TargetTakerLiveExecutor(
        live.TargetTakerLiveConfig(mode="live", venue="predictfun", notional_usdt=1.0)
    )

    def forbidden(**_kwargs):  # pragma: no cover - assertion path
        raise AssertionError("market mismatch reached live venue")

    monkeypatch.setattr(executor, "_execute_predictfun", forbidden)
    result = executor.execute(
        cohort=live.DEFAULT_COHORT,
        market_id=123,
        decision=_decision(),
        snapshot=_snapshot(124),
        signal_id="signal-3",
    )
    assert result["status"] == "MARKET_MISMATCH"


def test_live_predict_dispatch_preserves_one_usdt_config(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = live.TargetTakerLiveExecutor(
        live.TargetTakerLiveConfig(
            mode="live", venue="predictfun", notional_usdt=1.0, max_price_drift=0.02
        )
    )
    seen = {}

    def fake_predict(**kwargs):
        seen.update(kwargs)
        return {
            "status": "SUBMITTED",
            "venue": "predictfun",
            "executionPrice": 0.42,
            "submittedUsdt": 1.0,
        }

    monkeypatch.setattr(executor, "_execute_predictfun", fake_predict)
    result = executor.execute(
        cohort=live.DEFAULT_COHORT,
        market_id=123,
        decision=_decision(),
        snapshot=_snapshot(),
        signal_id="signal-4",
    )
    assert seen["market_id"] == 123
    assert seen["side"] == "UP"
    assert result["notionalUsdt"] == pytest.approx(1.0)
    assert result["submittedUsdt"] == pytest.approx(1.0)


def test_live_binance_dispatch_is_separate(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = live.TargetTakerLiveExecutor(
        live.TargetTakerLiveConfig(mode="live", venue="binance", notional_usdt=1.0)
    )
    seen = {}

    def fake_binance(**kwargs):
        seen.update(kwargs)
        return {"status": "SUBMITTED", "venue": "binance", "executionPrice": 0.41}

    monkeypatch.setattr(executor, "_execute_binance", fake_binance)
    result = executor.execute(
        cohort=live.DEFAULT_COHORT,
        market_id=123,
        decision=_decision(side="DOWN", ask=0.41),
        snapshot=_snapshot(),
        signal_id="signal-5",
    )
    assert seen["source_market_id"] == 123
    assert seen["side"] == "DOWN"
    assert result["venue"] == "binance"
