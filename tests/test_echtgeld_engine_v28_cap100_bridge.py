from __future__ import annotations

import pytest

from predict_bot import echtgeld_engine_v23 as v23
from predict_bot import echtgeld_engine_v27 as v27
from predict_bot import echtgeld_engine_v28 as v28


class FakeExecutor:
    def __init__(self, config) -> None:
        self.config = config

    def close(self) -> None:
        pass

    def available_balance_snapshot(self) -> dict:
        return {"status": "OK", "availableUsdt": 100.0}


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self.payload


class FakeMarketHttp:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.urls: list[str] = []

    def get(self, url: str) -> FakeResponse:
        self.urls.append(url)
        return FakeResponse(self.payload)

    def close(self) -> None:
        pass


def make_engine(tmp_path, monkeypatch) -> v28.EchtgeldEngine:
    monkeypatch.setattr(v27, "REPLAY_DB", tmp_path / "replay.db")
    monkeypatch.setattr(v27, "CONTROLLER_DB", tmp_path / "controller.db")
    return v28.EchtgeldEngine(
        tmp_path / "engine.db",
        executor_factory=FakeExecutor,
        start_worker=False,
        settlement_db_path=tmp_path / "retired.db",
        target_official_db_path=tmp_path / "official.db",
    )


def insert_cap100_fill(engine: v28.EchtgeldEngine, *, market_id: int = 1510001) -> None:
    with engine.db_lock:
        engine.db.execute(
            """INSERT INTO engine_cap100_orders(
                   client_order_id,role,strategy,source_id,source_market_id,venue_market_id,
                   bucket_start_sec,window_end_ms,side,token_id,fee_rate_bps,requested_price,
                   requested_shares,requested_cost_usdt,state,filled_share_qty,filled_usdt_amount,
                   avg_fill_price,created_at_ms,completed_at_ms,raw_json
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "CAP-M1", "MAKER", v23.CAP100_STRATEGY, v23.CAP100_SOURCE, market_id, 7001,
                1, 2, "UP", "TOKEN", 200, 0.4, 18.0, 7.2, "FILLED",
                18.0, 7.2, 0.4, 10, 20, "{}",
            ),
        )
        engine.db.commit()


def test_strict_winner_accepts_explicit_official_evidence_only() -> None:
    assert v28._strict_official_winner({
        "outcomes": [
            {"name": "Up", "status": "WON"},
            {"name": "Down", "status": "LOST"},
        ]
    }) == "UP"
    assert v28._strict_official_winner({
        "resolution": {"status": "SETTLED", "outcome": "Down"}
    }) == "DOWN"


def test_strict_winner_never_infers_from_start_end_prices() -> None:
    assert v28._strict_official_winner({
        "variantData": {"startPrice": 100.0, "endPrice": 101.0}
    }) is None


def test_cap100_direct_settlement_enters_combined_pnl_stoploss_and_ui_projection(tmp_path, monkeypatch) -> None:
    engine = make_engine(tmp_path, monkeypatch)
    try:
        insert_cap100_fill(engine)
        engine.cap100_settlement_http.close()
        engine.cap100_settlement_http = FakeMarketHttp({
            "data": {
                "id": 1510001,
                "status": "SETTLED",
                "outcomes": [
                    {"name": "Up", "status": "LOST"},
                    {"name": "Down", "status": "WON"},
                ],
            }
        })

        perf = engine._performance_snapshot(force_sync=True)
        assert perf["cap100NetPnlUsdt"] == pytest.approx(-7.2)
        assert perf["netPnlUsdt"] == pytest.approx(-7.2)
        assert perf["cap100"]["settledMarkets"] == 1

        engine.stop_loss_usdt = 5.0
        risk = engine._risk_snapshot(perf)
        assert risk["tripped"] is True
        assert risk["currentNetPnlUsdt"] == pytest.approx(-7.2)

        projected = engine._cap100_order_projection(10)
        assert projected[0]["cap100"] is True
        assert projected[0]["winner"] == "DOWN"
        assert projected[0]["resultStatus"] == "LOSS"
        assert projected[0]["netPnlUsdt"] == pytest.approx(-7.2)

        messages = engine._cap100_permanent_trade_messages()
        assert any(item.get("cap100") is True and item.get("event_type") == "CAP100_SETTLED_ORDER" for item in messages)
    finally:
        engine.close()


def test_r2_r21_projection_uses_actual_source_not_cap100_label(tmp_path, monkeypatch) -> None:
    engine = make_engine(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(engine, "_sync_cap100_settlements", lambda **_kwargs: {})
        with engine.db_lock:
            engine.db.execute(
                """INSERT INTO engine_cap100_orders(
                       client_order_id,role,strategy,source_id,source_market_id,venue_market_id,
                       bucket_start_sec,window_end_ms,side,token_id,fee_rate_bps,requested_price,
                       requested_shares,requested_cost_usdt,state,filled_share_qty,filled_usdt_amount,
                       avg_fill_price,created_at_ms,completed_at_ms,raw_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "R21-M1", "MAKER", v23.R2_R21_STRATEGY, v23.R2_R21_SOURCE, 1510002, 7002,
                    1, 10**15, "DOWN", "TOKEN", 200, 0.3, 10.0, 3.0, "FILLED",
                    10.0, 3.0, 0.3, 10, 20, "{}",
                ),
            )
            engine.db.commit()

        projected = engine._cap100_order_projection(10)
        assert projected[0]["entry_source"] == v23.R2_R21_SOURCE
        assert projected[0]["cap100"] is False
        assert projected[0]["sharedExecutionAdapter"] is True

        message = engine._cap100_permanent_trade_messages()[0]
        assert message["entry_source"] == v23.R2_R21_SOURCE
        assert message["event_type"] == "R2_R21_FILLED_ORDER"
        assert "R2+R2.1 [R2_R21_8789]" in message["message"]
        assert message["cap100"] is False
    finally:
        engine.close()
