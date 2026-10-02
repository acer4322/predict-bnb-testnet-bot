from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("PREDICT_WALLET_MAKER_CLONE_ASSET", "ETH")
os.environ.setdefault("PREDICT_WALLET_MAKER_CLONE_ENABLED", "false")

from predict_bot.wallet_maker_clone_live_v3 import (  # noqa: E402
    BINANCE_PREDICTION_MIN_ORDER_USDT,
    MinimumOrderSafeWalletMakerCloneEngine,
)


def _engine(tmp_path: Path) -> MinimumOrderSafeWalletMakerCloneEngine:
    return MinimumOrderSafeWalletMakerCloneEngine(tmp_path / "clone-v3.db")


def _close_engine(engine: MinimumOrderSafeWalletMakerCloneEngine) -> None:
    engine.http.close()
    with engine.db_lock:
        engine.db.close()


def test_v3_reports_hard_one_dollar_venue_floor(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        settings = engine._settings()
        assert settings["minimumOrderUsdt"] == BINANCE_PREDICTION_MIN_ORDER_USDT
        assert settings["venueMinimumOrderUsdt"] == BINANCE_PREDICTION_MIN_ORDER_USDT
        snapshot = engine.snapshot()
        assert snapshot["rules"]["minimumOrderHardFloorV3"] is True
    finally:
        _close_engine(engine)


def test_v3_rejects_minimum_setting_below_one_dollar(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        with pytest.raises(ValueError, match="at least 1.00 USDT"):
            engine.update_settings({"minimumOrderUsdt": 0.99})
    finally:
        _close_engine(engine)


def test_v3_plan_never_falls_below_one_dollar(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        # Simulate a stale/corrupt pre-V3 database value. The final planner must
        # still enforce the Binance venue minimum as defense in depth.
        engine._set_setting("minimum_order_usdt", "0.01")
        engine._set_setting("maximum_order_usdt", "100")
        engine._set_setting("target_profit_usdt", "0.10")
        engine.books["DOWN"] = {"bestBid": 0.10, "bestAsk": 0.12}
        market = {"precision": 2, "up_token_id": "up", "down_token_id": "down"}
        plan = engine._plan_order("DOWN", market)
        assert plan is not None and plan["blocked"] is False
        assert float(plan["plannedCost"]) >= 1.0
        assert abs(float(plan["plannedShares"]) - 10.0) < 1e-9
    finally:
        _close_engine(engine)
