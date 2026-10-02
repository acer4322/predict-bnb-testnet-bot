from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from predict_bot import echtgeld_engine_v2 as engine_v2
from predict_bot import predict_wallet_target_taker_public_side_strategy_v1 as public_side
from predict_bot import target_taker_live_execution_v3 as live_v3
from predict_bot import target_taker_live_execution_v5 as live_v5


class _BalanceClient:
    def __init__(self, wallet_address: str, wallet_id: str, *, payment_payload=None, payment_error=None):
        self.wallet_address = wallet_address
        self.wallet_id = wallet_id
        self.payment_payload = payment_payload
        self.payment_error = payment_error

    def wallets(self):
        return {"wallets": [{"walletAddress": self.wallet_address, "walletId": self.wallet_id}]}

    def payment_option_balances(self):
        if self.payment_error is not None:
            raise self.payment_error
        return self.payment_payload


def _balance_executor(monkeypatch: pytest.MonkeyPatch, client: _BalanceClient):
    monkeypatch.setenv(live_v3.BINANCE_WALLET_ADDRESS_ENV, client.wallet_address)
    monkeypatch.setenv(live_v3.BINANCE_WALLET_ID_ENV, client.wallet_id)
    monkeypatch.setenv(live_v3.BINANCE_ACCOUNT_TYPE_ENV, "SPOT")
    executor = live_v5.TargetTakerLiveExecutor(
        live_v5.TargetTakerLiveConfig(mode="paper", venue="binance", notional_usdt=1.5)
    )
    monkeypatch.setattr(executor, "_ensure_binance", lambda: client)
    monkeypatch.setattr(executor, "_binance_prediction_wallet_usdt_balance", lambda _wallet: 12.5)
    return executor


def test_4310_payment_options_prefers_cedefi_and_keeps_mpc_safety_balance(monkeypatch: pytest.MonkeyPatch) -> None:
    wallet = "0x1234567890abcdef1234567890abcdef12345678"
    client = _BalanceClient(
        wallet,
        "wallet-1",
        payment_payload={
            "items": [
                {"accountType": "SPOT", "availableBalanceDisplay": "0", "enabled": True},
                {"accountType": "FUNDING", "availableBalanceDisplay": "0", "enabled": True},
                {"accountType": "CeDeFi", "availableBalanceDisplay": "81.75", "enabled": True},
            ]
        },
    )
    executor = _balance_executor(monkeypatch, client)
    try:
        snapshot = executor.available_balance_snapshot()
    finally:
        executor.close()

    assert snapshot["status"] == "OK"
    assert snapshot["availableUsdt"] == pytest.approx(81.75)
    assert snapshot["accountType"] == "CeDeFi"
    assert snapshot["source"] == "binance_prediction.payment-options"
    assert snapshot["mpcWalletAvailableUsdt"] == pytest.approx(12.5)
    assert snapshot["predictionWalletAddress"] == wallet
    assert len(snapshot["paymentOptions"]) == 3


def test_payment_options_failure_does_not_hide_mpc_safety_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    wallet = "0x1234567890abcdef1234567890abcdef12345678"
    client = _BalanceClient(wallet, "wallet-1", payment_error=RuntimeError("payment endpoint unavailable"))
    executor = _balance_executor(monkeypatch, client)
    try:
        snapshot = executor.available_balance_snapshot()
    finally:
        executor.close()

    assert snapshot["availableUsdt"] == pytest.approx(12.5)
    assert snapshot["paymentSourceStatus"] == "UNAVAILABLE"
    assert snapshot["mpcWalletAvailableUsdt"] == pytest.approx(12.5)
    assert "payment endpoint unavailable" in snapshot["paymentSourceError"]


class _FakeExecutor:
    def __init__(self, config):
        self.config = config

    def close(self):
        return None

    def execute(self, **_kwargs):
        raise AssertionError("PnL read test must not execute a live order")

    def available_balance_snapshot(self):
        return {"status": "OK", "venue": self.config.venue, "availableUsdt": 81.75}


def _make_settlement_db(path: Path) -> None:
    db = sqlite3.connect(path)
    try:
        db.execute(
            """CREATE TABLE wallet_target_taker_public_side_v1_results(
                   cohort TEXT NOT NULL, market_id INTEGER NOT NULL, winner TEXT NOT NULL,
                   resolved_at_ms INTEGER NOT NULL, PRIMARY KEY(cohort,market_id)
               )"""
        )
        db.executemany(
            "INSERT INTO wallet_target_taker_public_side_v1_results VALUES (?,?,?,?)",
            [
                (public_side.SIDE_ONLY_COHORT, 700001, "UP", 1000),
                (public_side.SIDE_ONLY_COHORT, 700002, "UP", 2000),
            ],
        )
        db.commit()
    finally:
        db.close()


def _insert_order(engine, *, intent_id: str, market_id: int, side: str, cost: float, shares: float, attempted: int) -> None:
    engine.db.execute(
        """INSERT INTO engine_orders(
               dedupe_key,intent_id,strategy,cohort,market_id,venue,side,signal_ask,
               target_notional_usdt,status,attempted_at_ms,completed_at_ms,execution_price,
               shares,submitted_usdt,vendor_order_id,result_json,context_json
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            f"dedupe-{market_id}", intent_id, public_side.VERSION, public_side.SIDE_ONLY_COHORT,
            market_id, "binance", side, 0.60, cost, "SUBMITTED", attempted, attempted + 10,
            cost / shares, shares, cost, f"order-{market_id}", json.dumps({"status": "SUBMITTED"}), "{}",
        ),
    )
    engine.db.commit()


def test_engine_v2_recreates_old_live_pnl_from_actual_fills_and_official_winner(tmp_path: Path) -> None:
    settlement_db = tmp_path / "shadow.db"
    _make_settlement_db(settlement_db)
    engine = engine_v2.EchtgeldEngine(
        tmp_path / "engine.db",
        executor_factory=_FakeExecutor,
        start_worker=False,
        settlement_db_path=settlement_db,
    )
    try:
        _insert_order(engine, intent_id="win", market_id=700001, side="UP", cost=1.5, shares=2.5, attempted=100)
        _insert_order(engine, intent_id="loss", market_id=700002, side="DOWN", cost=1.5, shares=3.0, attempted=200)
        perf = engine.performance()
        assert perf["attempts"] == 2
        assert perf["submitted"] == 2
        assert perf["settledCounted"] == 2
        assert perf["wins"] == 1
        assert perf["losses"] == 1
        assert perf["winRate"] == pytest.approx(0.5)
        assert perf["stakeUsdt"] == pytest.approx(3.0)
        assert perf["netPnlUsdt"] == pytest.approx(-0.5)
        assert perf["netRoi"] == pytest.approx(-0.5 / 3.0)
        assert perf["maxDrawdownUsdt"] == pytest.approx(1.5)
        assert perf["currentLossStreak"] == 1
        assert perf["longestLossStreak"] == 1
        assert perf["settlementSync"]["status"] == "OK"

        rows = list(reversed(engine.orders(10)))
        assert rows[0]["resultStatus"] == "WIN"
        assert rows[0]["netPnlUsdt"] == pytest.approx(1.0)
        assert rows[1]["resultStatus"] == "LOSS"
        assert rows[1]["netPnlUsdt"] == pytest.approx(-1.5)
    finally:
        engine.close()


def test_settlement_sync_failure_is_observational_only(tmp_path: Path) -> None:
    engine = engine_v2.EchtgeldEngine(
        tmp_path / "engine.db",
        executor_factory=_FakeExecutor,
        start_worker=False,
        settlement_db_path=tmp_path / "missing-shadow.db",
    )
    try:
        sync = engine._sync_settlements(force=True)
        assert sync["status"] == "UNAVAILABLE"
        assert engine.armed is False
        assert engine.performance()["settledCounted"] == 0
    finally:
        engine.close()
