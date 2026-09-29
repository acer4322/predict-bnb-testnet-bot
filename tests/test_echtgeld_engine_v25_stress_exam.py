from __future__ import annotations

from predict_bot import echtgeld_engine_v25 as v25


class FakeExecutor:
    def __init__(self, config) -> None:
        self.config = config
    def close(self) -> None:
        pass
    def available_balance_snapshot(self) -> dict:
        return {"status": "OK", "availableUsdt": 100.0}
    def execute(self, **_kwargs):
        raise AssertionError("stress exam must never execute a generic order")


def make_engine(tmp_path):
    return v25.EchtgeldEngine(
        tmp_path / "echtgeld.db",
        executor_factory=FakeExecutor,
        start_worker=False,
        settlement_db_path=tmp_path / "none.db",
        target_official_db_path=tmp_path / "none2.db",
    )


def test_stress_exam_is_reproducible_and_isolated(tmp_path):
    engine = make_engine(tmp_path)
    try:
        before_orders = len(engine._cap100_rows(active_only=False))
        before_pnl = engine._cap100_performance()["netPnlUsdt"]
        a = engine.run_cap100_stress_exam(markets=250, seed=12345)
        b = engine.run_cap100_stress_exam(markets=250, seed=12345)
        assert a["status"] == "PASS"
        assert a["safetyGrade"] == "A"
        assert a["venueWrites"] == 0
        assert a["productionLedgerWrites"] == 0
        assert a["economics"] == b["economics"]
        assert a["faultCounts"] == b["faultCounts"]
        assert len(engine._cap100_rows(active_only=False)) == before_orders
        assert engine._cap100_performance()["netPnlUsdt"] == before_pnl
    finally:
        engine.close()


def test_stress_exam_reports_required_risk_metrics(tmp_path):
    engine = make_engine(tmp_path)
    try:
        report = engine.run_cap100_stress_exam(markets=500, seed=20260820)
        econ = report["economics"]
        safety = report["safety"]
        assert report["markets"] == 500
        assert econ["maxSingleMarketLossUsdt"] <= 0
        assert econ["maxSingleMarketProfitUsdt"] >= 0
        assert econ["maxDrawdownUsdt"] >= 0
        assert safety["maxObservedSpendUsdt"] <= 100.0 + 1e-9
        assert safety["violationCount"] == 0
        assert sum(report["faultCounts"].values()) == 500
        assert report["admissionRecommendation"] == "ELIGIBLE_FOR_CONTROLLED_LIVE_CANARY"
    finally:
        engine.close()
