from __future__ import annotations

import hashlib
import hmac
import sqlite3
from pathlib import Path

import httpx

from predict_bot.core import BinancePredictionTradingClient
from predict_bot.strategy_target_compare_recorder_v1 import StrategyTargetCompareRecorder


class _FakeResponse:
    status_code = 200
    headers = {}
    text = '{"success":true}'

    def json(self):
        return {"success": True}


class _FakeHttp:
    def __init__(self):
        self.calls = []

    def post(self, path, *, content, headers):
        self.calls.append((path, content.decode("utf-8"), dict(headers)))
        return _FakeResponse()


def test_prediction_batch_cancel_uses_literal_bracket_keys():
    fake = _FakeHttp()
    client = BinancePredictionTradingClient("key", "secret", http_client=fake)  # type: ignore[arg-type]
    client._time_offset_ms = 0
    client.server_timestamp_ms = lambda: 1234567890  # type: ignore[method-assign]
    result = client.batch_cancel_orders_raw(wallet_address="wa", wallet_id="wi", order_ids=["123", "456"])
    assert result["success"] is True
    path, body, headers = fake.calls[-1]
    assert path == "/sapi/v1/w3w/wallet/prediction/trade/batch-cancel"
    canonical, signature = body.rsplit("&signature=", 1)
    assert canonical == (
        "walletAddress=wa&walletId=wi&cancelInfoList[0].orderId=123"
        "&cancelInfoList[1].orderId=456&recvWindow=5000&timestamp=1234567890"
    )
    assert "%5B" not in body and "%5D" not in body
    assert "vendor=" not in body
    assert signature == hmac.new(
        b"secret", canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"


def test_recorder_incremental_maker_fill_is_idempotent(tmp_path: Path):
    db = tmp_path / "rec.db"
    r = StrategyTargetCompareRecorder(db)
    try:
        r.record_order_placement(
            order_id="m1", strategy_version="V", market_id=1, placement_decision_id="d1",
            channel="MAKER", side="UP", quote_type="BID", price=0.42, shares=18,
            placed_at_ms=1000, placement_state={"x": 1},
        )
        assert r.record_order_fill_delta(
            order_id="m1", fill_id="m1:seq7", filled_at_ms=2000, fill_price=0.426,
            shares=11.54, fill_state={"engineEventSeq": 7}, terminal=False,
        ) is True
        assert r.record_order_fill_delta(
            order_id="m1", fill_id="m1:seq7", filled_at_ms=2000, fill_price=0.426,
            shares=11.54, fill_state={"engineEventSeq": 7}, terminal=False,
        ) is False
        assert r.record_order_fill_delta(
            order_id="m1", fill_id="m1:seq12", filled_at_ms=3000, fill_price=0.420,
            shares=6.28, fill_state={"engineEventSeq": 12}, terminal=True,
        ) is True
    finally:
        r.close()
    con = sqlite3.connect(db)
    try:
        fills = con.execute("select fill_id,shares from our_fills order by filled_at_ms").fetchall()
        assert fills == [("m1:seq7", 11.54), ("m1:seq12", 6.28)]
        status = con.execute("select status from our_orders where order_id='m1'").fetchone()[0]
        assert status == "FILLED"
    finally:
        con.close()


def test_active_intervention_hook_is_live_only_and_priority_is_before_passive_maker():
    from predict_bot import unified_controller_cap100_shadow_v1 as paper
    from predict_bot import unified_controller_cap100_echtgeld_v1 as live
    p = paper.UnifiedControllerCap100ShadowV1.__new__(paper.UnifiedControllerCap100ShadowV1)
    assert p._active_intervention_required() is False
    x = live.UnifiedControllerCap100EchtgeldV1.__new__(live.UnifiedControllerCap100EchtgeldV1)
    x.active_intervention_required = False
    x.active_intervention_reason = None
    x._on_active_intervention_required("UNRESOLVED_PASSIVE_REPAIR_15S", 123)
    assert x._active_intervention_required() is True
    assert x.active_intervention_reason == "UNRESOLVED_PASSIVE_REPAIR_15S"
    x._on_active_intervention_satisfied()
    assert x._active_intervention_required() is False

    source = Path(paper.__file__).read_text(encoding="utf-8")
    required = source.index('if active_required and now-self.last_taker_ms>=1000:')
    passive = source.index('elif passive_draw:', required)
    normal_maker = source.index('maker_suppressed=bool(self._active_intervention_required())', passive)
    assert required < passive < normal_maker
