"""Synthetic/unit coverage only. These tests never run market replay or live IO."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import sqlite3

import pytest

from tools.mvps_research_v1 import core, runner
from tools.mvps_research_v1.cli import load_completed, main, synthetic_demo


def fill(fid="f1", oid="o1", t=10, side="UP", qty="2", price=".4", cum=None, order_qty=None):
    return {"fillId": fid, "orderId": oid, "receiptMs": t, "placedMs": 0,
            "side": side, "qty": qty, "price": price,
            "cumQty": qty if cum is None else cum,
            "orderQty": qty if order_qty is None else order_qty,
            "priceAuthority": "EXCHANGE_SNAPSHOT"}


def test_root_is_repository():
    assert (runner.ROOT / "pyproject.toml").is_file()
    assert (runner.ROOT / runner.LEGACY_SOURCES[0]).is_file()


def test_partial_fill_fifo_and_residual_reconcile():
    emitted = []
    ledger = core.EconomicLedger(0, lambda kind, row: emitted.append((kind, row)))
    ledger.fill(fill(qty="1", order_qty="2"))
    ledger.fill(fill("f2", t=20, qty="1", cum="2", order_qty="2"))
    ledger.fill(fill("f3", "o2", 30, "DOWN", "1.5", ".5"))
    summary = ledger.summary(100)
    assert summary["fillEvents"] == 3
    assert summary["pairedGrossProfit"] == "0.15"
    assert summary["pairedQty"] == "1.5"
    assert Decimal(summary["residualCost"]) == Decimal(".2")
    assert Decimal(summary["endpointGross"]["UP"]) == Decimal(".45")
    assert Decimal(summary["endpointGross"]["DOWN"]) == Decimal("-.05")
    assert summary["pairLinks"] == 2
    assert summary["fillSideAlternations"] == 1
    assert all(row["kind"] == "PRIOR_FIFO_LOT_MATCH" for kind, row in emitted if kind == "allocations")
    assert ledger.order_cum["o1"] == 2


def test_same_clock_match_is_not_old_responsibility_payment():
    rows = []
    ledger = core.EconomicLedger(0, lambda kind, row: rows.append((kind, row)))
    ledger.fill(fill())
    ledger.fill(fill("f2", "o2", 10, "DOWN", "2", ".5"))
    allocation = [r for k, r in rows if k == "allocations"][0]
    assert allocation["kind"] == "SAME_CLOCK_PAIR"
    assert allocation["authority"] == "ECONOMIC_ACCOUNTING_ONLY"
    assert Decimal(ledger.summary(20)["absNetShareMs"]) == 0


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", None, True, "bogus"])
def test_nonfinite_and_invalid_numbers_rejected(value):
    with pytest.raises(ValueError):
        core.number(value)


@pytest.mark.parametrize("change", [
    {"qty": "0"}, {"qty": "-1"}, {"price": "1"}, {"side": "YES"},
    {"cumQty": "3"}, {"orderQty": "1"}, {"placedMs": 11},
    {"priceAuthority": "DREAM_FILL"},
])
def test_bad_fill_rejected(change):
    ledger = core.EconomicLedger(0)
    row = fill()
    row.update(change)
    with pytest.raises(ValueError):
        ledger.fill(row)


def test_duplicate_and_clock_regression_rejected():
    ledger = core.EconomicLedger(0)
    ledger.fill(fill())
    with pytest.raises(ValueError, match="duplicate"):
        ledger.fill(fill())
    with pytest.raises(ValueError, match="regressed"):
        ledger.fill(fill("f2", "o2", t=9))


def test_pending_exposure_integral_and_zero_fill_denominator():
    ledger = core.EconomicLedger(0)
    assert ledger.summary(100)["fillEvents"] == 0
    ledger = core.EconomicLedger(0)
    ledger.fill(fill(t=10))
    summary = ledger.summary(100)
    assert Decimal(summary["absNetShareMs"]) == 180
    assert Decimal(summary["receiptClockPeakAbsNet"]) == 2


def ready_run():
    rows = []
    for mid, start in [(1945866, 0), (1945869, 100)]:
        ledger = core.EconomicLedger(start)
        ledger.fill(fill(t=start + 10))
        ledger.fill(fill("f2", "o2", start + 20, "DOWN", "2", ".5"))
        row = ledger.summary(start + 100)
        row.update(marketId=mid, winnerPostHocOnly="UP", openOrderCountAtHorizon=0,
                   cashAndReservationsPeak="2")
        rows.append(row)
    contract = {"costScope": "ALL_COSTS_NOT_ALREADY_IN_BUY_NOTIONAL",
                "extraCostUpperByMarket": {str(r["marketId"]): ".01" for r in rows},
                "costProvenance": "SYNTHETIC_TEST_ONLY",
                "riskLimits": {"maxWorstEndpointLoss": "1", "maxReceiptPeakAbsNet": "3",
                               "maxTerminalSequenceDrawdown": "1", "maxCashPlusReservations": "3"},
                "riskProvenance": "SYNTHETIC_TEST_ONLY",
                "minActivityRetention": {k: ".8" for k in core.ACTIVITY},
                "minTradeCoverage": ".8", "activityProvenance": "SYNTHETIC_TEST_ONLY"}
    return {"researchOnly": True, "liveAuthority": False,
            "evidenceKind": "REALISTIC_HFT_REPLAY", "cohortKind": "consumed_development",
            "policyId": "unit_candidate", "marketIds": [r["marketId"] for r in rows],
            "markets": rows, "behaviorParityVerified": True, "evaluationContract": contract,
            "activityControl": {"marketIds": [r["marketId"] for r in rows],
                                "policyId": "unit_control", "provenance": "SYNTHETIC_TEST_ONLY",
                                "totals": {k: str(sum(core.number(r[k]) for r in rows)) for k in core.ACTIVITY}}}


def test_positive_absolute_screen_never_promotes_to_live():
    result = core.evaluate(ready_run())
    assert result["verdict"] == "DEVELOPMENT_SCREEN_PASS_NOT_PROMOTED"
    assert Decimal(result["netLowerAggregate"]) == Decimal(".38")
    assert not result["liveEligible"] and not result["forwardEligible"]


def test_unknown_cost_is_not_zero_and_unknown_budget_is_not_pass():
    run = ready_run()
    run["evaluationContract"]["extraCostUpperByMarket"] = None
    run["evaluationContract"]["riskLimits"] = {}
    result = core.evaluate(run)
    assert result["netLowerAggregate"] is None
    assert "FULL_NET_COST_UNRESOLVED" in result["issues"]
    assert "RISK_BUDGET_UNSPECIFIED" in result["issues"]


def test_synthetic_positive_case_cannot_be_profit_evidence():
    run = ready_run()
    run["evidenceKind"] = "SYNTHETIC_FIXTURE_NOT_HFT"
    result = core.evaluate(run)
    assert "SYNTHETIC_NOT_ECONOMIC_EVIDENCE" in result["issues"]
    assert result["verdict"] == "NOT_PASSED"


def test_activity_collapse_fails_even_with_positive_profit():
    run = ready_run()
    run["activityControl"]["totals"]["fillEvents"] = "100"
    assert "FAIL_ACTIVITY_COLLAPSE:fillEvents" in core.evaluate(run)["issues"]


def test_zero_activity_control_denominator_not_waived():
    run = ready_run()
    run["activityControl"]["totals"]["pairLinks"] = "0"
    assert "ACTIVITY_DENOMINATOR_ZERO:pairLinks" in core.evaluate(run)["issues"]


def test_all_markets_required_and_no_self_activity_control():
    run = ready_run()
    run["markets"].pop()
    with pytest.raises(ValueError, match="markets"):
        core.evaluate(run)
    run = ready_run()
    run["activityControl"]["policyId"] = run["policyId"]
    with pytest.raises(ValueError, match="distinct"):
        core.evaluate(run)


def test_execution_price_fallback_blocks_positive_claim():
    run = ready_run()
    run["markets"][0]["priceFallbackFillCount"] = 1
    assert "EXECUTION_PRICE_FALLBACK_UNRESOLVED" in core.evaluate(run)["issues"]


def test_nonterminal_orders_and_missing_reservation_risk_block_promotion():
    run = ready_run()
    run["markets"][0]["openOrderCountAtHorizon"] = 1
    assert "NONTERMINAL_ORDERS_AT_HORIZON" in core.evaluate(run)["issues"]
    del run["markets"][0]["cashAndReservationsPeak"]
    assert "PHYSICAL_LIFECYCLE_RISK_UNRESOLVED" in core.evaluate(run)["issues"]


def test_changed_order_identity_not_accepted_as_partial_fill():
    ledger = core.EconomicLedger(0)
    ledger.fill(fill(qty="1", order_qty="2"))
    with pytest.raises(ValueError, match="immutable"):
        ledger.fill(fill("f2", qty="1", cum="2", order_qty="2", side="DOWN"))


def test_risk_budget_checks_reserved_capital_not_only_filled_cost():
    run = ready_run()
    run["markets"][0]["cashAndReservationsPeak"] = "100"
    assert "RISK_LIMIT_EXCEEDED:maxCashPlusReservations" in core.evaluate(run)["issues"]


def test_live_flag_rejected_by_evaluator():
    run = ready_run()
    run["liveAuthority"] = True
    with pytest.raises(ValueError, match="research-only"):
        core.evaluate(run)


def test_negative_cost_upper_bound_rejected():
    run = ready_run()
    run["evaluationContract"]["extraCostUpperByMarket"]["1945866"] = "-.1"
    with pytest.raises(ValueError, match="negative"):
        core.evaluate(run)


def test_one_best_market_cannot_hide_net_losing_rest():
    run = ready_run()
    run["markets"][0]["endpointGross"]["UP"] = "10"
    run["markets"][1]["endpointGross"]["UP"] = "-1"
    result = core.evaluate(run)
    assert core.number(result["netLowerAggregate"]) > 0
    assert "OUTLIER_DOMINATED" in result["issues"]


def test_drawdown_uses_settlement_chronology_not_input_order():
    run = ready_run()
    run["markets"][0]["endpointGross"]["UP"] = "-.5"
    run["markets"][0]["endMs"] = 1000
    run["markets"][1]["endpointGross"]["UP"] = "2"
    result = core.evaluate(run)
    assert core.number(result["terminalSequenceDrawdown"]) == Decimal(".51")


def test_cannot_select_only_a_known_winning_anchor():
    p = plan()
    p["marketIds"] = [1945898]
    p["tapes"] = {"1945898": {"path": "1945898.json.xz", "sha256": "unused"}}
    with pytest.raises(ValueError, match="fixed prefix"):
        runner.validate_plan(p)


def test_favorable_payoff_decline_is_not_a_standalone_gate():
    run = ready_run()
    run["activityControl"]["winnerGroupPnl"] = "999999"
    assert core.evaluate(run)["verdict"] == "DEVELOPMENT_SCREEN_PASS_NOT_PROMOTED"


def test_offline_guard_blocks_connection_without_making_one():
    with runner.offline_boundary():
        with pytest.raises(PermissionError):
            runner._audit("socket.connect", ())


def plan():
    p = runner.draft_plan()
    p["marketIds"] = [1945866]
    p["tapes"] = {"1945866": {"path": "1945866.json.xz", "sha256": "test"}}
    return p


def auth(p, budget=2):
    return {"allowReplay": True, "planSha256": core.digest(p), "maxBE": budget,
            "budgetPool": "MVPS_RESEARCH_V1_NEW_SYNTHETIC_UNIT_TEST",
            "authorizationRef": "UNIT_TEST_NOT_MARKET_AUTHORIZATION"}


@pytest.mark.parametrize("change", [
    {"liveAuthority": True}, {"cohortKind": "Reserve"}, {"marketIds": [1946298]},
    {"marketIds": [1945866, 1945866]}, {"policyId": "python:arbitrary"},
])
def test_plan_blocks_locked_cohorts_and_dynamic_policy(change):
    p = plan()
    p.update(change)
    with pytest.raises(ValueError):
        runner.validate_plan(p)


def test_authorization_is_new_plan_bound_and_budgeted():
    p = plan()
    a = auth(p)
    assert runner.validate_authorization(p, a) == 2
    for change in ({"allowReplay": False}, {"maxBE": 0}, {"maxBE": True},
                   {"planSha256": "wrong"}, {"budgetPool": "OLD_ITT_REMAINING_2_BE"}):
        bad = {**a, **change}
        with pytest.raises(ValueError):
            runner.validate_authorization(p, bad)


def test_refusal_occurs_before_tape_io_and_backend_import(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("must not import backend without authorization")
    monkeypatch.setattr(runner, "load_backend", forbidden)
    with pytest.raises(ValueError, match="authorization"):
        runner.replay(plan(), {"allowReplay": False}, tmp_path / "run")
    assert not (tmp_path / "run").exists()


def test_failed_be_is_not_refunded_or_reset_by_authorization_edit(tmp_path):
    a = auth(plan())
    with pytest.raises(RuntimeError):
        with runner.BudgetJournal(tmp_path, a) as budget:
            budget.reserve(1945866, "A")
            raise RuntimeError("synthetic failure")
    a["authorizationRef"] = "EDITED_REFERENCE"
    with runner.BudgetJournal(tmp_path, a) as budget:
        assert budget.used == 1
        budget.reserve(1945866, "B")
        with pytest.raises(ValueError, match="exhausted"):
            budget.reserve(1945866, "AUTO_RETRY")


def test_concurrent_budget_lock_fails_closed(tmp_path):
    a = auth(plan())
    with runner.BudgetJournal(tmp_path, a):
        with pytest.raises(FileExistsError):
            with runner.BudgetJournal(tmp_path, a):
                pass


def test_offline_guard_blocks_database_and_releases_scope():
    with runner.offline_boundary():
        with pytest.raises(PermissionError):
            sqlite3.connect(":memory:")
    connection = sqlite3.connect(":memory:")
    connection.close()


def test_strict_json_and_no_overwrite(tmp_path):
    path = tmp_path / "x.json"
    core.write_json_new(path, {"x": 1})
    with pytest.raises(FileExistsError):
        core.write_json_new(path, {"x": 2})
    assert core.read_json(path) == {"x": 1}


def test_demo_and_complete_artifact_tamper_detection(tmp_path):
    target = tmp_path / "demo"
    result = synthetic_demo(target)
    assert result["verdict"] == "NOT_PASSED"
    saved = load_completed(target)
    assert saved["consumedBE"] == 0
    trace = target / "FIXTURE_A/fills.jsonl"
    with trace.open("a", encoding="utf-8") as stream:
        stream.write("{}\n")
    with pytest.raises(ValueError, match="trace"):
        load_completed(target)


def test_cli_has_no_implicit_execution(tmp_path, capsys):
    assert main(["template", "--output", str(tmp_path / "draft.json")]) == 0
    assert core.read_json(tmp_path / "draft.json")["liveAuthority"] is False
    assert main(["template", "--output", str(tmp_path / "draft.json")]) == 2
    assert "STOP:" in capsys.readouterr().err


class FakeNative:
    """Deterministic semantic stub, NOT a market or fill model."""
    seen_winners = []

    def __init__(self, tape, slots, pair, floor, serial):
        self.meta = {"firstReceivedMs": 0, "lastReceivedMs": 100}
        self.orders, self.slot_key, self.slot_history, self.veto = {}, {}, [], {}
        self.inv = {"UP": 0., "DOWN": 0.}
        self.cost, self.fills, self.submits, self.n, self.snap_calls = 0., 0, 0, 1, 0
        self.t = 0

    def snap(self, o):
        self.snap_calls += 1
        cum = (1 if self.t == 10 else 2 if self.t >= 20 else 0) if o["side"] == "UP" else (2 if self.t >= 30 else 0)
        return {"cumExecQty": cum, "status": "FILLED" if cum == 2 else "NEW",
                "execPrice": o["price"] if o["side"] == "UP" else 1 - o["price"]}

    def record_fill(self, t, side, q, p):
        self.inv[side] += q
        self.cost += q * p

    def process(self, t):
        self.t = t
        for o in self.orders.values():
            s = self.snap(o)
            inc = s["cumExecQty"] - o["cum"]
            if inc > 0:
                p = s["execPrice"] if o["side"] == "UP" else 1 - s["execPrice"]
                self.record_fill(t, o["side"], inc, p)
                self.fills += 1
                o["cum"] = s["cumExecQty"]
            o["status"] = s["status"]

    def _refresh_slots(self, t):
        for sid, key in list(self.slot_key.items()):
            if self.orders[key]["status"] == "FILLED":
                self.slot_history.append({"event": "SLOT_RELEASE", "t": t, "key": key})
                del self.slot_key[sid]

    def _open_free_slots(self, t, qv, end):
        if self.slot_key or self.n > 2:
            return
        side = "UP" if self.n == 1 else "DOWN"
        key = f"{side}_{self.n}"
        self.orders[key] = {"n": self.n, "side": side, "qty": 2., "price": .4 if side == "UP" else .5,
                            "cum": 0., "placed": t, "status": "NEW"}
        self.n += 1
        self.submits += 1
        self.slot_key[1] = key
        self.slot_history.append({"event": "SLOT_SUBMIT", "t": t, "key": key})

    def run_ladder(self, winner):
        self.seen_winners.append(winner)
        for t in (0, 10, 20, 30, 100):
            self.process(t)
            self._refresh_slots(t)
            self._open_free_slots(t, {"imb": 1.0}, 1000)
        return {"upQty": self.inv["UP"], "downQty": self.inv["DOWN"],
                "buyNotional": self.cost, "fillEvents": self.fills, "submits": self.submits}

    def close(self):
        pass


def test_observer_does_not_add_snapshots_or_change_behavior(tmp_path):
    native = FakeNative(None, 1, True, False, False)
    nresult = native.run_ladder("__UNSCORED__")
    sink = runner.TraceSink(tmp_path / "observed")
    observed = runner.instrumented_class(FakeNative)(None, 1, sink)
    oresult = observed.run_ladder("__UNSCORED__")
    sink.close()
    assert observed.snap_calls == native.snap_calls
    assert core.digest(runner.behavior_state(observed, oresult)) == core.digest(runner.behavior_state(native, nresult))
    summary = observed.research_ledger.summary(100)
    runner.reconcile(summary, oresult)
    assert summary["fillEvents"] == 3
    assert Decimal(summary["pairedGrossProfit"]) == Decimal(".2")
    assert sink.counts["fills"] == 3
    assert all(x == "__UNSCORED__" for x in FakeNative.seen_winners)


def test_complete_replay_orchestration_with_fake_backend_only(tmp_path, monkeypatch):
    p = plan()
    settlements = tmp_path / "settlements.json"
    core.write_json_new(settlements, {"1945866": "UP"})
    p["settlements"] = {"path": str(settlements), "sha256": core.file_digest(settlements)}
    a = auth(p)
    original = runner.validate_plan
    monkeypatch.setattr(runner, "validate_plan", lambda value, **kwargs: original(value))
    monkeypatch.setattr(runner, "load_backend", lambda _: FakeNative)
    monkeypatch.setattr(runner, "RUN_ROOT", tmp_path / "runs")
    out = tmp_path / "runs/test"
    result = runner.replay(p, a, out)
    assert result["behaviorParityVerified"]
    assert result["budgetPoolReservedBE"] == 2
    assert (out / "COMPLETED.json").is_file()
    assert load_completed(out)["markets"][0]["winnerPostHocOnly"] == "UP"
    assert not core.evaluate(result)["liveEligible"]
    with pytest.raises(ValueError, match="insufficient"):
        runner.replay(p, a, tmp_path / "runs/second")
    assert (tmp_path / "runs/second/FAILED.json").is_file()
    assert not (tmp_path / "runs/second/COMPLETED.json").exists()
