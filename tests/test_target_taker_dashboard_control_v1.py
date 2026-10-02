from __future__ import annotations

import sqlite3
import threading

import pytest

from predict_bot import predict_wallet_shadow_observer_v4_21 as observer_v4_21
from predict_bot import predict_wallet_target_taker_public_side_strategy_v1 as public_side
from predict_bot import target_taker_live_execution_v4 as live


def test_binance_balance_parser_prefers_selected_account() -> None:
    payload = {
        "paymentOptions": [
            {"asset": "USDT", "accountType": "FUNDING", "availableBalance": "7.5"},
            {"asset": "USDT", "accountType": "SPOT", "availableBalance": "12.25"},
        ]
    }
    assert live.extract_binance_available_usdt(payload, account_type="SPOT") == pytest.approx(12.25)
    assert live.extract_binance_available_usdt(payload, account_type="FUNDING") == pytest.approx(7.5)


def test_binance_balance_parser_converts_explicit_wei() -> None:
    payload = {"data": {"asset": "USDT", "accountType": "SPOT", "balanceWei": "2500000000000000000"}}
    assert live.extract_binance_available_usdt(payload, account_type="SPOT") == pytest.approx(2.5)


class _FakeBuilder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def balance_of(self, token: str, address: str | None = None) -> int:
        self.calls.append((token, address))
        return 42 * 10**18


class _FakePredictClient:
    def close(self) -> None:
        pass


def test_predict_balance_snapshot_uses_sdk_balance_of(monkeypatch: pytest.MonkeyPatch) -> None:
    config = live.TargetTakerLiveConfig(mode="paper", venue="predictfun", notional_usdt=1.0)
    executor = live.TargetTakerLiveExecutor(config)
    builder = _FakeBuilder()
    executor._predict_client = _FakePredictClient()
    executor._predict_builder = builder
    monkeypatch.setenv("PREDICT_FUN_ACCOUNT_ADDRESS", "0xabc")
    result = executor.available_balance_snapshot()
    assert result["status"] == "OK"
    assert result["availableUsdt"] == pytest.approx(42.0)
    assert builder.calls == [("USDT", "0xabc")]


class _ClosableExecutor:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_runtime_update_changes_future_config_without_persisted_enablement() -> None:
    observer = observer_v4_21.WalletShadowObserver.__new__(observer_v4_21.WalletShadowObserver)
    observer.target_taker_live_runtime_lock = threading.RLock()
    observer.target_taker_live_startup_config = live.TargetTakerLiveConfig(
        mode="paper", venue="predictfun", notional_usdt=1.0
    )
    observer.target_taker_live_config = observer.target_taker_live_startup_config
    old = _ClosableExecutor()
    observer.target_taker_live_executor = old
    observer.target_taker_balance_cache = {"cacheKey": "predictfun:1"}
    observer.target_taker_live_settings_updated_at_ms = None

    # Avoid the full snapshot dependency; this unit only verifies the mutation.
    observer._target_taker_live_control_snapshot = lambda include_recent=True: {
        **observer.target_taker_live_config.snapshot(),
        "runtimeEnabled": observer.target_taker_live_config.mode == "live",
    }
    result = observer.update_target_taker_live_settings(
        {
            "runtimeEnabled": True,
            "venue": "binance",
            "notionalUsdt": 2.5,
            "maxPriceDrift": 0.01,
            "cohort": "SIDE_ONLY",
        }
    )
    assert old.closed is True
    assert observer.target_taker_live_config.mode == "live"
    assert observer.target_taker_live_config.venue == "binance"
    assert observer.target_taker_live_config.notional_usdt == pytest.approx(2.5)
    assert observer.target_taker_live_config.cohort == public_side.SIDE_ONLY_COHORT
    assert observer.target_taker_balance_cache is None
    assert result["runtimeEnabled"] is True


def test_balance_is_cached_once_per_market_and_venue() -> None:
    class FakeExecutor:
        def __init__(self) -> None:
            self.calls = 0

        def available_balance_snapshot(self) -> dict:
            self.calls += 1
            return {"status": "OK", "venue": "predictfun", "availableUsdt": 10.0, "asOfMs": 1}

    observer = observer_v4_21.WalletShadowObserver.__new__(observer_v4_21.WalletShadowObserver)
    observer.market_id = 123
    observer.target_taker_live_runtime_lock = threading.RLock()
    observer.target_taker_live_config = live.TargetTakerLiveConfig(mode="paper", venue="predictfun")
    executor = FakeExecutor()
    observer.target_taker_live_executor = executor
    observer.target_taker_balance_cache = None

    first = observer._target_taker_balance()
    second = observer._target_taker_balance()
    assert first["availableUsdt"] == pytest.approx(10.0)
    assert second["cacheKey"] == "predictfun:123"
    assert executor.calls == 1

    observer.market_id = 124
    observer._target_taker_balance()
    assert executor.calls == 2


def test_live_performance_joins_official_winner_to_submitted_fok_amounts() -> None:
    observer = observer_v4_21.WalletShadowObserver.__new__(observer_v4_21.WalletShadowObserver)
    observer.db_lock = threading.RLock()
    observer.db = sqlite3.connect(":memory:")
    observer.db.row_factory = sqlite3.Row
    observer.db.executescript(
        """
        CREATE TABLE wallet_target_taker_public_side_v1_live_orders (
            cohort TEXT NOT NULL, market_id INTEGER NOT NULL, venue TEXT, mode TEXT,
            signal_id TEXT, side TEXT, target_notional_usdt REAL, signal_ask REAL,
            status TEXT, attempted_at_ms INTEGER, completed_at_ms INTEGER,
            execution_price REAL, shares REAL, submitted_usdt REAL,
            vendor_order_id TEXT, error_message TEXT,
            PRIMARY KEY(cohort,market_id)
        );
        CREATE TABLE wallet_target_taker_public_side_v1_results (
            cohort TEXT NOT NULL, market_id INTEGER NOT NULL, winner TEXT,
            resolved_at_ms INTEGER, PRIMARY KEY(cohort,market_id)
        );
        """
    )
    cohort = public_side.SIDE_ONLY_COHORT
    observer.db.execute(
        "INSERT INTO wallet_target_taker_public_side_v1_live_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cohort, 1, "predictfun", "live", "sig1", "UP", 1.0, 0.5, "SUBMITTED", 1, 2, 0.5, 2.0, 1.0, "o1", None),
    )
    observer.db.execute(
        "INSERT INTO wallet_target_taker_public_side_v1_live_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cohort, 2, "predictfun", "live", "sig2", "UP", 1.0, 0.5, "SUBMITTED", 3, 4, 0.5, 2.0, 1.0, "o2", None),
    )
    observer.db.execute(
        "INSERT INTO wallet_target_taker_public_side_v1_results VALUES (?,?,?,?)",
        (cohort, 1, "UP", 10),
    )
    observer.db.execute(
        "INSERT INTO wallet_target_taker_public_side_v1_results VALUES (?,?,?,?)",
        (cohort, 2, "DOWN", 20),
    )
    observer.db.commit()

    perf = observer._target_taker_live_performance()
    assert perf["settledCounted"] == 2
    assert perf["wins"] == 1
    assert perf["losses"] == 1
    assert perf["netPnlUsdt"] == pytest.approx(0.0)
    assert perf["netRoi"] == pytest.approx(0.0)
    assert perf["longestLossStreak"] == 1
    observer.db.close()
