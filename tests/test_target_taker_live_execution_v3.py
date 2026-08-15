from __future__ import annotations

import pytest

from predict_bot import target_taker_live_execution_v3 as live


def _decision(side: str = "UP", ask: float = 0.42) -> dict:
    return {"decision": "TRADE", "side": side, "ask": ask}


def _snapshot(market_id: int = 123) -> dict:
    return {
        "market_id": market_id,
        "bucket_start_sec": 1_800_000_000,
        "window_end_ms": 1_800_000_300_000,
    }


def test_config_defaults_are_paper_predictfun_one_usdt(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert config.snapshot()["executorVersion"] == "V3_DECOUPLED"


def test_invalid_configuration_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(live.MODE_ENV, "LIVE_NOW")
    with pytest.raises(live.TargetTakerLiveError):
        live.TargetTakerLiveConfig.from_env()

    monkeypatch.setenv(live.MODE_ENV, "live")
    monkeypatch.setenv(live.NOTIONAL_ENV, "0")
    with pytest.raises(live.TargetTakerLiveError):
        live.TargetTakerLiveConfig.from_env()

    monkeypatch.setenv(live.NOTIONAL_ENV, "1")
    monkeypatch.setenv(live.MAX_PRICE_DRIFT_ENV, "0.11")
    with pytest.raises(live.TargetTakerLiveError):
        live.TargetTakerLiveConfig.from_env()


def test_paper_mode_never_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = live.TargetTakerLiveExecutor(
        live.TargetTakerLiveConfig(mode="paper", venue="predictfun", notional_usdt=1.0)
    )

    def forbidden(**_kwargs):
        raise AssertionError("paper mode reached live venue")

    monkeypatch.setattr(executor, "_execute_predictfun", forbidden)
    result = executor.execute(
        cohort=live.DEFAULT_COHORT,
        market_id=123,
        decision=_decision(),
        snapshot=_snapshot(),
        signal_id="signal-paper",
    )
    assert result["status"] == "NOT_ARMED"


def test_only_explicit_live_cohort_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = live.TargetTakerLiveExecutor(
        live.TargetTakerLiveConfig(mode="live", venue="predictfun", notional_usdt=1.0)
    )

    def forbidden(**_kwargs):
        raise AssertionError("unarmed cohort reached live venue")

    monkeypatch.setattr(executor, "_execute_predictfun", forbidden)
    result = executor.execute(
        cohort="TARGET_TAKER_PUBLIC_SIDE_V1_HAZARD_SIDE",
        market_id=123,
        decision=_decision(),
        snapshot=_snapshot(),
        signal_id="signal-cohort",
    )
    assert result["status"] == "COHORT_NOT_ARMED"


def test_market_mismatch_blocks_before_venue(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = live.TargetTakerLiveExecutor(
        live.TargetTakerLiveConfig(mode="live", venue="predictfun", notional_usdt=1.0)
    )

    def forbidden(**_kwargs):
        raise AssertionError("market mismatch reached live venue")

    monkeypatch.setattr(executor, "_execute_predictfun", forbidden)
    result = executor.execute(
        cohort=live.DEFAULT_COHORT,
        market_id=123,
        decision=_decision(),
        snapshot=_snapshot(124),
        signal_id="signal-market",
    )
    assert result["status"] == "MARKET_MISMATCH"


def test_predict_dispatch_keeps_one_usdt_config(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = live.TargetTakerLiveExecutor(
        live.TargetTakerLiveConfig(mode="live", venue="predictfun", notional_usdt=1.0)
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
        signal_id="signal-predict",
    )
    assert seen["market_id"] == 123
    assert seen["side"] == "UP"
    assert result["notionalUsdt"] == pytest.approx(1.0)
    assert result["submittedUsdt"] == pytest.approx(1.0)


def test_binance_dispatch_is_separate(monkeypatch: pytest.MonkeyPatch) -> None:
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
        signal_id="signal-binance",
    )
    assert seen["source_market_id"] == 123
    assert seen["side"] == "DOWN"
    assert result["venue"] == "binance"


def test_binance_account_type_defaults_to_spot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(live.BINANCE_WALLET_ADDRESS_ENV, "0xabc")
    monkeypatch.setenv(live.BINANCE_WALLET_ID_ENV, "wallet-1")
    monkeypatch.delenv(live.BINANCE_ACCOUNT_TYPE_ENV, raising=False)
    assert live.TargetTakerLiveExecutor._binance_wallet() == ("0xabc", "wallet-1", "SPOT")


def test_binance_account_type_accepts_funding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(live.BINANCE_WALLET_ADDRESS_ENV, "0xabc")
    monkeypatch.setenv(live.BINANCE_WALLET_ID_ENV, "wallet-1")
    monkeypatch.setenv(live.BINANCE_ACCOUNT_TYPE_ENV, "funding")
    assert live.TargetTakerLiveExecutor._binance_wallet()[2] == "FUNDING"


def test_binance_account_type_rejects_mpc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(live.BINANCE_WALLET_ADDRESS_ENV, "0xabc")
    monkeypatch.setenv(live.BINANCE_WALLET_ID_ENV, "wallet-1")
    monkeypatch.setenv(live.BINANCE_ACCOUNT_TYPE_ENV, "MPC")
    with pytest.raises(live.TargetTakerLiveError, match="SPOT or FUNDING"):
        live.TargetTakerLiveExecutor._binance_wallet()


class _FakePredictClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[tuple[int, int]] = []

    def list_markets(self, *, first: int, max_pages: int) -> list[dict]:
        self.calls.append((first, max_pages))
        return list(self.rows)


def test_predict_market_requires_exact_id() -> None:
    client = _FakePredictClient([{"id": 6827000}, {"id": 6827001}])
    market = live.TargetTakerLiveExecutor._predict_market(client, 6827001)
    assert market["id"] == 6827001
    assert client.calls == [(100, 5)]


def test_predict_market_blocks_when_exact_id_missing() -> None:
    client = _FakePredictClient([{"id": 6827000}, {"id": 6827002}])
    with pytest.raises(live.TargetTakerLiveError, match="exact market 6827001"):
        live.TargetTakerLiveExecutor._predict_market(client, 6827001)


def test_predict_yes_up_book_orientation() -> None:
    books = live.derive_outcome_books(
        {"data": {"bids": [["0.40", "10"]], "asks": [["0.42", "8"]]}},
        yes_outcome="UP",
        precision=2,
    )
    assert books["UP"]["bestAsk"] == pytest.approx(0.42)
    assert books["DOWN"]["bestAsk"] == pytest.approx(0.60)


def test_predict_yes_down_book_orientation() -> None:
    books = live.derive_outcome_books(
        {"data": {"bids": [["0.40", "10"]], "asks": [["0.42", "8"]]}},
        yes_outcome="DOWN",
        precision=2,
    )
    assert books["DOWN"]["bestAsk"] == pytest.approx(0.42)
    assert books["UP"]["bestAsk"] == pytest.approx(0.60)
