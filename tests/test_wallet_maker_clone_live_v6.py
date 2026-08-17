from __future__ import annotations

import os
import time

os.environ.setdefault("PREDICT_WALLET_MAKER_CLONE_ASSET", "ETH")
os.environ.setdefault("PREDICT_WALLET_MAKER_CLONE_ENABLED", "false")

from predict_bot.wallet_maker_clone_live_v6 import (  # noqa: E402
    BASE_POTENTIAL_PROFIT_USDT,
    SequentialReplenishmentWalletMakerCloneEngine,
)


def _engine(tmp_path):
    return SequentialReplenishmentWalletMakerCloneEngine(tmp_path / "clone-v6.db")


def _close(engine: SequentialReplenishmentWalletMakerCloneEngine) -> None:
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


def _plan(side: str, token: str) -> dict:
    return {
        "side": side,
        "tokenId": token,
        "price": 0.50,
        "bestBid": 0.49,
        "bestAsk": 0.51,
        "targetProfit": 1.0,
        "plannedCost": 1.0,
        "plannedShares": 2.0,
    }


def test_v6_schema_allows_multiple_parent_generations_per_side(tmp_path) -> None:
    engine = _engine(tmp_path)
    try:
        pair = engine._new_pair(_market())
        first = engine._insert_order_plan(pair["id"], 123, _plan("UP", "up-token"))
        second = engine._insert_order_plan(pair["id"], 123, _plan("UP", "up-token"))
        rows = engine._pair_orders(pair["id"])
        assert first != second
        assert [row["generation"] for row in rows] == [1, 2]
    finally:
        _close(engine)


def test_generation_one_keeps_neutral_half_half_anchor(tmp_path) -> None:
    engine = _engine(tmp_path)
    try:
        engine.books = {
            "UP": {"bestBid": 0.01, "bestAsk": 0.99},
            "DOWN": {"bestBid": 0.01, "bestAsk": 0.99},
        }
        up = engine._plan_order("UP", _market())
        down = engine._plan_order("DOWN", _market())
        assert up is not None and down is not None
        assert up["blocked"] is False
        assert down["blocked"] is False
        assert up["price"] == 0.50
        assert down["price"] == 0.50
    finally:
        _close(engine)


def test_later_generation_uses_current_passive_best_bid(tmp_path) -> None:
    engine = _engine(tmp_path)
    try:
        engine.books = {
            "UP": {"bestBid": 0.49, "bestAsk": 0.51},
            "DOWN": {"bestBid": 0.49, "bestAsk": 0.51},
        }
        plan = engine._plan_replenishment("UP", _market())
        assert plan is not None
        assert plan["blocked"] is False
        assert plan["price"] == 0.49
        assert plan["pricingModel"] == "CURRENT_PASSIVE_BEST_BID_REPLENISH"
        assert plan["requestedPotentialProfit"] == BASE_POTENTIAL_PROFIT_USDT
    finally:
        _close(engine)


def test_terminal_timestamp_is_not_refreshed_by_repeated_reconciliation(tmp_path) -> None:
    engine = _engine(tmp_path)
    try:
        pair = engine._new_pair(_market())
        row_id = engine._insert_order_plan(pair["id"], 123, _plan("UP", "up-token"))
        engine._update_order(row_id, state="FILLED", filled_usdt_amount=1.0, filled_share_qty=2.0)
        first = engine._latest_side_order(pair["id"], "UP")
        assert first is not None
        terminal_at = int(first["terminal_at_ms"] or 0)
        assert terminal_at > 0

        time.sleep(0.01)
        engine._update_order(row_id, state="FILLED", filled_usdt_amount=1.0, filled_share_qty=2.0)
        second = engine._latest_side_order(pair["id"], "UP")
        assert second is not None
        assert int(second["terminal_at_ms"] or 0) == terminal_at
    finally:
        _close(engine)


def test_v6_locks_base_profit_and_auto_requote_off(tmp_path) -> None:
    engine = _engine(tmp_path)
    try:
        settings = engine._settings()
        assert settings["targetPotentialProfitUsdt"] == 1.0
        assert settings["autoRequote"] is False
        try:
            engine.update_settings({"targetPotentialProfitUsdt": 2.0})
        except ValueError as exc:
            assert "locks" in str(exc)
        else:
            raise AssertionError("V6 must reject non-$1 base potential-profit settings")
    finally:
        _close(engine)
