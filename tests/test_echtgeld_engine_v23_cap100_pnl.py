from __future__ import annotations

import sqlite3

import pytest

from predict_bot import echtgeld_engine_v23 as v23


class FakeExecutor:
    def __init__(self, config) -> None:
        self.config = config
    def close(self) -> None:
        pass
    def available_balance_snapshot(self) -> dict:
        return {"status": "OK", "availableUsdt": 100.0}
    def execute(self, **_kwargs):
        raise AssertionError


def test_cap100_confirmed_fill_enters_combined_pnl_and_stoploss(tmp_path):
    official = tmp_path / "official.db"
    db = sqlite3.connect(official)
    db.execute("CREATE TABLE target_markets(market_id INTEGER PRIMARY KEY,status TEXT,winner TEXT,resolved_at_ms INTEGER)")
    db.execute("INSERT INTO target_markets VALUES(1510001,'SETTLED','DOWN',3000)")
    db.commit(); db.close()

    engine = v23.EchtgeldEngine(
        tmp_path / "echtgeld.db", executor_factory=FakeExecutor, start_worker=False,
        settlement_db_path=tmp_path / "retired.db", target_official_db_path=official,
    )
    try:
        with engine.db_lock:
            engine.db.execute(
                """INSERT INTO engine_cap100_orders(
                       client_order_id,role,strategy,source_id,source_market_id,venue_market_id,
                       bucket_start_sec,window_end_ms,side,token_id,fee_rate_bps,requested_price,
                       requested_shares,requested_cost_usdt,state,filled_share_qty,filled_usdt_amount,
                       avg_fill_price,created_at_ms,completed_at_ms,raw_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "M1","MAKER",v23.CAP100_STRATEGY,v23.CAP100_SOURCE,1510001,7001,
                    1000,1300000,"UP","TOKEN",200,0.4,18.0,7.2,"FILLED",18.0,7.2,0.4,1100,1200,"{}",
                ),
            )
            engine.db.commit()
        perf = engine._performance_snapshot(force_sync=True)
        assert perf["cap100NetPnlUsdt"] == pytest.approx(-7.2)
        assert perf["netPnlUsdt"] == pytest.approx(-7.2)
        assert perf["cap100"]["settledMarkets"] == 1
        engine.stop_loss_usdt = 5.0
        risk = engine._risk_snapshot(perf)
        assert risk["tripped"] is True
        assert risk["currentNetPnlUsdt"] == pytest.approx(-7.2)
    finally:
        engine.close()
