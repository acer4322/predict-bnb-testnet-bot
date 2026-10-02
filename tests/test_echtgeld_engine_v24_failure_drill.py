from __future__ import annotations

from predict_bot import echtgeld_engine_v24 as v24


class FakeExecutor:
    def __init__(self, config) -> None:
        self.config = config
    def close(self) -> None:
        pass
    def available_balance_snapshot(self) -> dict:
        return {"status": "OK", "availableUsdt": 100.0}
    def execute(self, **_kwargs):
        raise AssertionError("failure drill must never use generic executor")


def make_engine(tmp_path):
    return v24.EchtgeldEngine(
        tmp_path / "echtgeld.db",
        executor_factory=FakeExecutor,
        start_worker=False,
        settlement_db_path=tmp_path / "none.db",
        target_official_db_path=tmp_path / "none2.db",
    )


def test_failure_drill_matrix_isolated_and_passes(tmp_path):
    engine = make_engine(tmp_path)
    try:
        before_orders = engine.db.execute("SELECT COUNT(*) FROM engine_cap100_orders").fetchone()[0]
        before_events = engine.db.execute("SELECT COUNT(*) FROM engine_cap100_events").fetchone()[0]
        armed_before = engine.armed

        report = engine.run_cap100_drill()

        assert report["status"] == "PASS"
        assert report["total"] == 9
        assert report["passed"] == 9
        assert report["failed"] == 0
        assert report["venueWrites"] == 0
        assert report["productionLedgerWrites"] == 0
        assert report["productionPnlAffected"] is False
        assert engine.armed is armed_before
        assert engine.db.execute("SELECT COUNT(*) FROM engine_cap100_orders").fetchone()[0] == before_orders
        assert engine.db.execute("SELECT COUNT(*) FROM engine_cap100_events").fetchone()[0] == before_events
    finally:
        engine.close()


def test_required_user_scenarios_are_present_and_have_expected_safety_outcomes(tmp_path):
    engine = make_engine(tmp_path)
    try:
        report = engine.run_cap100_drill()
        cases = {x["scenario"]: x for x in report["scenarios"]}

        assert cases["ORDER_SUBMISSION_REJECTED"]["final"]["venueWrite"] is False
        assert cases["VENUE_ORDER_FAILED_NO_FILL"]["final"]["inventoryDelta"] == 0
        assert cases["PARTIAL_FILL_INCREMENTAL"]["final"]["inventoryShares"] == 12
        assert cases["PARTIAL_FILL_INCREMENTAL"]["final"]["remainingRestingShares"] == 6
        assert cases["FAST_PRICE_RISE_SLIPPAGE_CAP"]["final"]["venueWrite"] is False
        assert cases["FAST_PRICE_RISE_SLIPPAGE_CAP"]["final"]["reason"] == "TAKER_PRICE_CAP"
    finally:
        engine.close()


def test_ambiguous_and_cancel_races_are_fail_closed(tmp_path):
    engine = make_engine(tmp_path)
    try:
        cases = {x["scenario"]: x for x in engine.run_cap100_drill()["scenarios"]}
        unknown = cases["TRANSPORT_UNKNOWN_RECOVERED_FILLED"]["final"]
        assert unknown["blindRetry"] is False
        assert unknown["inventoryShares"] == 18
        assert unknown["pnlEligible"] is True

        unresolved = cases["UNKNOWN_UNRESOLVED_MANUAL_REVIEW"]["final"]
        assert unresolved["entryFrozen"] is True
        assert unresolved["automaticRetry"] is False

        race = cases["CANCEL_FILL_RACE"]["final"]
        assert race["cancelAckTreatedAsTerminal"] is False
        assert race["inventoryShares"] == 18
    finally:
        engine.close()
