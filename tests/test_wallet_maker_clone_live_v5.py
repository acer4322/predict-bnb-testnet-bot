from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("PREDICT_WALLET_MAKER_CLONE_ASSET", "ETH")
os.environ.setdefault("PREDICT_WALLET_MAKER_CLONE_ENABLED", "false")

from predict_bot.wallet_maker_clone_live_v5 import NeutralAnchorWalletMakerCloneEngine  # noqa: E402


def _engine(tmp_path: Path) -> NeutralAnchorWalletMakerCloneEngine:
    return NeutralAnchorWalletMakerCloneEngine(tmp_path / "clone-v5.db")


def _close(engine: NeutralAnchorWalletMakerCloneEngine) -> None:
    engine.http.close()
    with engine.db_lock:
        engine.db.close()


def test_symmetric_floor_ceiling_shell_anchors_at_half(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        engine.books["UP"] = {"bestBid": 0.01, "bestAsk": 0.99}
        engine.books["DOWN"] = {"bestBid": 0.01, "bestAsk": 0.99}
        anchor = engine._complementary_anchor()
        assert anchor == {"UP": 0.5, "DOWN": 0.5}
        market = {"precision": 2, "up_token_id": "up", "down_token_id": "down"}
        up = engine._plan_order("UP", market)
        down = engine._plan_order("DOWN", market)
        assert up is not None and down is not None
        assert up["blocked"] is False and down["blocked"] is False
        assert up["price"] == 0.5
        assert down["price"] == 0.5
    finally:
        _close(engine)


def test_complementary_normal_books_follow_market_not_fixed_half(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        engine.books["UP"] = {"bestBid": 0.24, "bestAsk": 0.27}
        engine.books["DOWN"] = {"bestBid": 0.73, "bestAsk": 0.76}
        anchor = engine._complementary_anchor()
        assert anchor is not None
        assert abs(anchor["UP"] - 0.255) < 1e-12
        assert abs(anchor["DOWN"] - 0.745) < 1e-12
        market = {"precision": 2, "up_token_id": "up", "down_token_id": "down"}
        up = engine._plan_order("UP", market)
        down = engine._plan_order("DOWN", market)
        assert up is not None and down is not None
        assert up["price"] == 0.25
        assert down["price"] == 0.74
    finally:
        _close(engine)


def test_one_tick_market_stays_post_only(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        engine.books["UP"] = {"bestBid": 0.49, "bestAsk": 0.50}
        engine.books["DOWN"] = {"bestBid": 0.50, "bestAsk": 0.51}
        market = {"precision": 2, "up_token_id": "up", "down_token_id": "down"}
        up = engine._plan_order("UP", market)
        down = engine._plan_order("DOWN", market)
        assert up is not None and down is not None
        assert up["price"] == 0.49
        assert down["price"] == 0.50
        assert up["price"] < engine.books["UP"]["bestAsk"]
        assert down["price"] < engine.books["DOWN"]["bestAsk"]
    finally:
        _close(engine)
