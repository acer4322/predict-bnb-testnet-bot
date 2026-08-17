from __future__ import annotations

import os

import pytest

os.environ.setdefault("PREDICT_WALLET_MAKER_CLONE_ASSET", "ETH")
os.environ.setdefault("PREDICT_WALLET_MAKER_CLONE_ENABLED", "false")

from predict_bot.wallet_maker_clone_live_v8 import (  # noqa: E402
    DEFAULT_MAXIMUM_ENTRY_COUNT,
    DEFAULT_MAXIMUM_LOSS_USDT,
    HARD_CANCEL_SECONDS,
    NO_NEW_ENTRY_SECONDS,
    BoundedRiskPairedWalletMakerCloneEngine,
)


def _engine(tmp_path):
    return BoundedRiskPairedWalletMakerCloneEngine(tmp_path / "clone-v8.db")


def _close(engine: BoundedRiskPairedWalletMakerCloneEngine) -> None:
    engine.http.close()
    with engine.db_lock:
        engine.db.commit()
        engine.db.close()


def _market(market_id: int = 123) -> dict:
    return {
        "asset": "ETH",
        "symbol": "ETHUSDT",
        "market_id": market_id,
        "topic_id": 456,
        "end_ms": 2_000_000_000_000,
        "up_token_id": "up-token",
        "down_token_id": "down-token",
        "fee_rate_bps": 200,
        "precision": 2,
    }


def _plan(side: str, token: str, *, cost: float = 1.0, shares: float = 2.0) -> dict:
    return {
        "side": side,
        "tokenId": token,
        "price": 0.50,
        "bestBid": 0.49,
        "bestAsk": 0.51,
        "targetProfit": 1.0,
        "plannedCost": cost,
        "plannedShares": shares,
    }


def test_v8_defaults_are_bounded_and_auto_requote_stays_off(tmp_path) -> None:
    engine = _engine(tmp_path)
    try:
        settings = engine._settings()
        assert engine.VERSION == "WALLET_MAKER_CLONE_LIVE_V8_BOUNDED_PAIRED_RISK"
        assert settings["targetPotentialProfitUsdt"] == 1.0
        assert settings["maximumEntryCount"] == DEFAULT_MAXIMUM_ENTRY_COUNT
        assert settings["maximumLossUsdt"] == DEFAULT_MAXIMUM_LOSS_USDT
        assert settings["riskStopLatched"] is False
        assert settings["autoRequote"] is False
        assert NO_NEW_ENTRY_SECONDS == 60.0
        assert HARD_CANCEL_SECONDS == 30.0
    finally:
        _close(engine)


def test_v8_target_profit_entry_count_and_loss_cap_are_configurable(tmp_path) -> None:
    engine = _engine(tmp_path)
    try:
        engine.update_settings(
            {
                "targetPotentialProfitUsdt": 2.5,
                "maximumEntryCount": 4,
                "maximumLossUsdt": 7.5,
            }
        )
        settings = engine._settings()
        assert settings["targetPotentialProfitUsdt"] == 2.5
        assert settings["maximumEntryCount"] == 4
        assert settings["maximumLossUsdt"] == 7.5
        assert settings["autoRequote"] is False
    finally:
        _close(engine)


def test_v8_replenishment_uses_configured_potential_profit(tmp_path) -> None:
    engine = _engine(tmp_path)
    try:
        engine.update_settings({"targetPotentialProfitUsdt": 2.0})
        engine.books = {
            "UP": {"bestBid": 0.50, "bestAsk": 0.51},
            "DOWN": {"bestBid": 0.50, "bestAsk": 0.51},
        }
        plan = engine._plan_replenishment("UP", _market())
        assert plan is not None
        assert plan["blocked"] is False
        assert plan["price"] == 0.50
        assert plan["requestedPotentialProfit"] == 2.0
        assert plan["targetProfit"] == pytest.approx(2.0)
        assert plan["plannedCost"] == pytest.approx(2.0)
        assert plan["plannedShares"] == pytest.approx(4.0)
    finally:
        _close(engine)


def test_v8_entry_count_counts_parent_generations_conservatively(tmp_path) -> None:
    engine = _engine(tmp_path)
    try:
        pair = engine._new_pair(_market())
        engine._insert_order_plan(pair["id"], 123, _plan("UP", "up-token"))
        engine._insert_order_plan(pair["id"], 123, _plan("DOWN", "down-token"))
        assert engine._entry_count(pair["id"]) == 1

        engine._insert_order_plan(pair["id"], 123, _plan("UP", "up-token"))
        assert engine._entry_count(pair["id"]) == 2
    finally:
        _close(engine)


def test_v8_worst_case_loss_uses_confirmed_fills_not_planned_size(tmp_path) -> None:
    engine = _engine(tmp_path)
    try:
        pair = engine._new_pair(_market())
        up_id = engine._insert_order_plan(pair["id"], 123, _plan("UP", "up-token", cost=5.0, shares=10.0))
        engine._insert_order_plan(pair["id"], 123, _plan("DOWN", "down-token", cost=5.0, shares=10.0))

        # Only 1 USDT / 2 UP shares is venue-confirmed. The unfilled planned
        # quantity must not be treated as already-held inventory.
        engine._update_order(
            up_id,
            state="FILLED",
            filled_usdt_amount=1.0,
            filled_share_qty=2.0,
        )
        exposure = engine._pair_exposure(pair["id"])
        assert exposure["totalCostUsdt"] == pytest.approx(1.0)
        assert exposure["upShares"] == pytest.approx(2.0)
        assert exposure["downShares"] == pytest.approx(0.0)
        assert exposure["pnlIfUpUsdt"] == pytest.approx(1.0)
        assert exposure["pnlIfDownUsdt"] == pytest.approx(-1.0)
        assert exposure["worstCasePnlUsdt"] == pytest.approx(-1.0)
        assert exposure["worstCaseLossUsdt"] == pytest.approx(1.0)
    finally:
        _close(engine)


def test_v8_risk_stop_latches_and_requires_explicit_reset(tmp_path) -> None:
    engine = _engine(tmp_path)
    try:
        pair = engine._new_pair(_market())
        up_id = engine._insert_order_plan(pair["id"], 123, _plan("UP", "up-token"))
        engine._update_order(
            up_id,
            state="FILLED",
            filled_usdt_amount=1.0,
            filled_share_qty=2.0,
        )
        exposure = engine._pair_exposure(pair["id"])
        engine._latch_maximum_loss_stop(pair, exposure, maximum_loss=1.0)

        settings = engine._settings()
        assert settings["runtimeEnabled"] is False
        assert settings["riskStopLatched"] is True
        assert "worst-case settlement loss" in settings["riskStopReason"]
        assert engine.status == "MAXIMUM_LOSS_STOPPED"

        with pytest.raises(ValueError, match="risk stop is latched"):
            engine.update_settings({"runtimeEnabled": True})

        engine.update_settings({"resetRiskStop": True})
        settings = engine._settings()
        assert settings["runtimeEnabled"] is False
        assert settings["riskStopLatched"] is False
        assert settings["riskStopReason"] == ""
    finally:
        _close(engine)


def test_v8_rejects_invalid_risk_and_entry_settings(tmp_path) -> None:
    engine = _engine(tmp_path)
    try:
        with pytest.raises(ValueError, match="maximumEntryCount"):
            engine.update_settings({"maximumEntryCount": 0})
        with pytest.raises(ValueError, match="maximumEntryCount"):
            engine.update_settings({"maximumEntryCount": 2.5})
        with pytest.raises(ValueError, match="maximumLossUsdt"):
            engine.update_settings({"maximumLossUsdt": 0})
        with pytest.raises(ValueError, match="Auto Requote"):
            engine.update_settings({"autoRequote": True})
    finally:
        _close(engine)
