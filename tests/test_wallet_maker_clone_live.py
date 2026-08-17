from __future__ import annotations

import os
from pathlib import Path
from typing import Any

os.environ.setdefault("PREDICT_WALLET_MAKER_CLONE_ASSET", "ETH")
os.environ.setdefault("PREDICT_WALLET_MAKER_CLONE_ENABLED", "false")

from predict_bot.wallet_maker_clone_live import (  # noqa: E402
    ClonePredictionClient,
    WalletMakerCloneEngine,
    _order_rows,
)


class _FakeResponse:
    status_code = 200
    headers: dict[str, str] = {}
    text = '{"success":true}'

    @staticmethod
    def json() -> dict[str, Any]:
        return {"success": True}


class _FakeHttp:
    def __init__(self) -> None:
        self.content: bytes | None = None
        self.path: str | None = None
        self.headers: dict[str, str] | None = None

    def post(self, path: str, *, content: bytes, headers: dict[str, str]) -> _FakeResponse:
        self.path = path
        self.content = content
        self.headers = headers
        return _FakeResponse()


def _engine(tmp_path: Path) -> WalletMakerCloneEngine:
    return WalletMakerCloneEngine(tmp_path / "clone.db")


def _close_engine(engine: WalletMakerCloneEngine) -> None:
    engine.http.close()
    with engine.db_lock:
        engine.db.close()


def test_order_rows_recursively_finds_order_ids() -> None:
    payload = {
        "data": {
            "orders": [
                {"orderId": "a", "status": "NEW"},
                {"orderId": "b", "status": "FILLED"},
            ]
        }
    }
    rows = _order_rows(payload)
    assert {row["orderId"] for row in rows} == {"a", "b"}


def test_profit_normalized_sizing_uses_best_bid(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        engine._set_setting("target_profit_usdt", "1")
        engine._set_setting("minimum_order_usdt", "0.01")
        engine._set_setting("maximum_order_usdt", "100")
        engine._set_setting("bid_offset_ticks", "0")
        engine.books["UP"] = {"bestBid": 0.80, "bestAsk": 0.82}
        market = {"precision": 2, "up_token_id": "up", "down_token_id": "down"}
        plan = engine._plan_order("UP", market)
        assert plan is not None and plan["blocked"] is False
        assert plan["price"] == 0.80
        assert abs(float(plan["plannedShares"]) - 5.0) < 1e-9
        assert abs(float(plan["plannedCost"]) - 4.0) < 1e-9
        assert abs(float(plan["targetProfit"]) - 1.0) < 1e-9
    finally:
        _close_engine(engine)


def test_soft_post_only_blocks_crossed_buy(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        engine.books["UP"] = {"bestBid": 0.50, "bestAsk": 0.50}
        market = {"precision": 2, "up_token_id": "up", "down_token_id": "down"}
        plan = engine._plan_order("UP", market)
        assert plan is not None
        assert plan["blocked"] is True
        assert "post-only" in str(plan["reason"])
    finally:
        _close_engine(engine)


def test_partial_fill_normalization() -> None:
    engine = object.__new__(WalletMakerCloneEngine)
    update = engine._normalize_order_update(
        {
            "orderId": "123",
            "status": "PARTIALLY_FILLED",
            "makerUsdtAmount": "4.0",
            "makerShareQty": "5.0",
            "filledUsdtAmount": "2.0",
            "filledShareQty": "2.5",
            "fillPercentage": "50",
        }
    )
    assert update["state"] == "PARTIAL_FILL"
    assert update["fill_percentage"] == 0.5
    assert update["filled_share_qty"] == 2.5


def test_batch_cancel_preserves_raw_bracket_keys() -> None:
    client = ClonePredictionClient("key", "secret")
    fake = _FakeHttp()
    real_http = client.http_client
    client.http_client = fake  # type: ignore[assignment]
    client.server_timestamp_ms = lambda: 1234567890  # type: ignore[method-assign]
    try:
        payload = client.batch_cancel_orders_raw(
            wallet_address="0xabc",
            wallet_id="wallet-1",
            order_ids=["order-a", "order-b"],
        )
        assert payload["success"] is True
        body = (fake.content or b"").decode("utf-8")
        assert "cancelInfoList[0].orderId=order-a" in body
        assert "cancelInfoList[1].orderId=order-b" in body
        assert "%5B" not in body and "%5D" not in body
        assert "walletAddress=0xabc" in body
        assert "walletId=wallet-1" in body
        assert "signature=" in body
    finally:
        client.http_client = real_http
        client.close()
