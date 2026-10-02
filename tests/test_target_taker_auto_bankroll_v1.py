from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from predict_bot import predict_wallet_target_taker_public_side_strategy_v1 as public_side
from predict_bot import target_taker_auto_bankroll_v1 as bankroll


def _row(
    *,
    market_id: int,
    status: str,
    pnl: float,
    stake: float = 1.0,
    confidence: float = 0.70,
) -> dict:
    return {
        "market_id": market_id,
        "settled_at_ms": market_id * 1000,
        "status": status,
        "stake_usdt": stake,
        "net_pnl_usdt": pnl,
        "signal_confidence": confidence,
    }


def test_fresh_wallet_starts_at_one_percent_and_one_usdt() -> None:
    sized = bankroll.size_trade([], signal_confidence=0.61)
    assert sized["equityBeforeUsdt"] == pytest.approx(100.0)
    assert sized["maturityState"] == "START"
    assert sized["finalRiskFraction"] == pytest.approx(0.01)
    assert sized["stakeUsdt"] == pytest.approx(1.0)


def test_stake_compounds_from_settled_equity_only() -> None:
    rows = [_row(market_id=1, status="WIN", pnl=1.0)]
    sized = bankroll.size_trade(rows, signal_confidence=0.61)
    assert sized["equityBeforeUsdt"] == pytest.approx(101.0)
    assert sized["stakeUsdt"] == pytest.approx(1.01)


def test_high_confidence_loss_is_operational_surprise_loss() -> None:
    assert bankroll.is_surprise_loss(
        _row(market_id=1, status="LOSS", pnl=-1.0, confidence=0.65)
    )
    assert not bankroll.is_surprise_loss(
        _row(market_id=2, status="LOSS", pnl=-1.0, confidence=0.6499)
    )
    assert not bankroll.is_surprise_loss(
        _row(market_id=3, status="WIN", pnl=1.0, confidence=0.90)
    )


def test_two_consecutive_surprise_losses_derisk_fast() -> None:
    rows = [
        _row(market_id=1, status="WIN", pnl=0.5, confidence=0.70),
        _row(market_id=2, status="LOSS", pnl=-1.0, confidence=0.72),
        _row(market_id=3, status="LOSS", pnl=-1.0, confidence=0.74),
    ]
    metrics = bankroll.performance(rows)
    assert metrics["recent5SurpriseLosses"] == 2
    assert metrics["consecutiveSurpriseLosses"] == 2
    assert bankroll.short_term_multiplier(metrics) == pytest.approx(0.30)


def test_drawdown_multiplier_steps_down_independently() -> None:
    assert bankroll.drawdown_multiplier(0.00) == pytest.approx(1.00)
    assert bankroll.drawdown_multiplier(0.03) == pytest.approx(0.80)
    assert bankroll.drawdown_multiplier(0.06) == pytest.approx(0.60)
    assert bankroll.drawdown_multiplier(0.10) == pytest.approx(0.35)
    assert bankroll.drawdown_multiplier(0.15) == pytest.approx(0.20)


def test_maturity_ceiling_rises_only_with_settled_evidence() -> None:
    twenty = [_row(market_id=i, status="WIN", pnl=0.5) for i in range(1, 21)]
    state, ceiling = bankroll.maturity(bankroll.performance(twenty))
    assert state == "PROVEN_1"
    assert ceiling == pytest.approx(0.015)

    forty = [_row(market_id=i, status="WIN", pnl=0.5) for i in range(1, 41)]
    state, ceiling = bankroll.maturity(bankroll.performance(forty))
    assert state == "PROVEN_2"
    assert ceiling == pytest.approx(0.02)

    eighty = [_row(market_id=i, status="WIN", pnl=0.5) for i in range(1, 81)]
    state, ceiling = bankroll.maturity(bankroll.performance(eighty))
    assert state == "STRONG"
    assert ceiling == pytest.approx(0.03)

    mature = [_row(market_id=i, status="WIN", pnl=0.5) for i in range(1, 151)]
    state, ceiling = bankroll.maturity(bankroll.performance(mature))
    assert state == "MATURE"
    assert ceiling == pytest.approx(0.04)


def test_signal_confidence_only_modulates_size_after_warmup() -> None:
    assert bankroll.signal_multiplier(0.60, 19) == pytest.approx(1.0)
    assert bankroll.signal_multiplier(0.60, 20) == pytest.approx(0.67)
    assert bankroll.signal_multiplier(0.66, 20) == pytest.approx(0.80)
    assert bankroll.signal_multiplier(0.72, 20) == pytest.approx(0.90)
    assert bankroll.signal_multiplier(0.80, 20) == pytest.approx(1.00)


def test_minimum_risk_floor_prevents_zeroing_the_same_trade_stream() -> None:
    rows = [
        *[_row(market_id=i, status="WIN", pnl=0.05, confidence=0.61) for i in range(1, 17)],
        _row(market_id=17, status="LOSS", pnl=-5.0, confidence=0.80),
        _row(market_id=18, status="LOSS", pnl=-5.0, confidence=0.80),
        _row(market_id=19, status="LOSS", pnl=-5.0, confidence=0.80),
        _row(market_id=20, status="LOSS", pnl=-5.0, confidence=0.80),
    ]
    sized = bankroll.size_trade(rows, signal_confidence=0.60)
    assert sized["shortTermMultiplier"] == pytest.approx(0.10)
    assert sized["drawdownMultiplier"] == pytest.approx(0.20)
    assert sized["finalRiskFraction"] == pytest.approx(bankroll.MIN_RISK_FRACTION)
    assert sized["stakeUsdt"] > 0


def test_policy_keeps_target_wallet_observations_out_of_sizing() -> None:
    policy = bankroll.policy()
    assert policy["paperOnly"] is True
    assert policy["sameTradeSignalsAsControl"] is True
    assert policy["targetEventsDriveSizing"] is False
    assert policy["initialEquityUsdt"] == pytest.approx(100.0)


class _DummyObserverBase:
    def __init__(self) -> None:
        self.db_lock = threading.RLock()
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.market_id = 123
        self.public_side_states = {bankroll.SOURCE_COHORT: {"event": None}}
        self.db.execute(
            """CREATE TABLE wallet_target_taker_public_side_v1_results(
                   cohort TEXT NOT NULL, market_id INTEGER NOT NULL, winner TEXT NOT NULL,
                   resolved_at_ms INTEGER NOT NULL, PRIMARY KEY(cohort,market_id))"""
        )
        self.db.commit()

    def _execute_public_side(
        self,
        cohort: str,
        decision: dict,
        hazard: dict | None,
        *,
        snapshot_ns: int,
        now_ms: int,
    ) -> None:
        del hazard
        state = self.public_side_states[cohort]
        if state["event"] is not None:
            return
        ask = float(decision["ask"])
        confidence = float(decision["signal"]["selectedProbability"])
        state["event"] = {
            "id": f"{cohort}:{self.market_id}:TAKER:{snapshot_ns}",
            "cohort": cohort,
            "marketId": self.market_id,
            "decisionAtMs": now_ms,
            "side": decision["side"],
            "ask": ask,
            "effectiveUnitCost": public_side.effective_unit_cost(ask),
            "sideSelectedProbability": confidence,
        }

    def _store_market_result(self, market_id: int, market: dict, winner: str) -> None:
        del market
        resolved_at_ms = int(time.time() * 1000)
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO wallet_target_taker_public_side_v1_results VALUES (?,?,?,?)",
                (bankroll.SOURCE_COHORT, market_id, winner, resolved_at_ms),
            )

    def snapshot(self) -> dict:
        return {}

    def health_snapshot(self) -> dict:
        return {}


class _DummyObserver(bankroll.AutoBankrollMixin, _DummyObserverBase):
    pass


def test_mixin_mirrors_same_event_and_settles_wallet_lifecycle() -> None:
    observer = _DummyObserver()
    now_ms = int(time.time() * 1000) + 1
    observer._execute_public_side(
        bankroll.SOURCE_COHORT,
        {
            "decision": "TRADE",
            "side": "UP",
            "ask": 0.50,
            "signal": {"selectedProbability": 0.70},
        },
        None,
        snapshot_ns=123456,
        now_ms=now_ms,
    )
    trade = observer.db.execute(
        "SELECT * FROM wallet_target_taker_auto_bankroll_v1_trades WHERE market_id=123"
    ).fetchone()
    assert trade is not None
    assert trade["status"] == "OPEN"
    assert trade["stake_usdt"] == pytest.approx(1.0)
    assert trade["equity_before_usdt"] == pytest.approx(100.0)

    observer._store_market_result(123, {}, "DOWN")
    settled = observer.db.execute(
        "SELECT * FROM wallet_target_taker_auto_bankroll_v1_trades WHERE market_id=123"
    ).fetchone()
    assert settled["status"] == "LOSS"
    assert settled["net_pnl_usdt"] == pytest.approx(-1.0)
    assert settled["equity_after_usdt"] == pytest.approx(99.0)
    assert settled["surprise_loss"] == 1

    snapshot = observer.snapshot()["targetTakerAutoBankrollV1"]
    assert snapshot["equityUsdt"] == pytest.approx(99.0)
    assert snapshot["performance"]["settledTrades"] == 1
    observer.db.close()
