from __future__ import annotations

from predict_bot import predict_wallet_shadow_observer as base
from predict_bot.predict_wallet_shadow_observer_v4_3 import WalletShadowObserver
from predict_bot.predict_wallet_shadow_v0_capital_s1 import (
    CapitalS1State,
    cap100_fill,
    market_economics,
    min1_exec_cap100_fill,
    planned_market_budget,
    reduce_source_event,
    time20_unlocked_fraction,
)


def source_event(event_id: str, event_type: str, side: str, shares: float, price: float) -> base.ShadowEvent:
    return base.ShadowEvent(
        id=event_id,
        market_id=2,
        at_ms=base._now_ms() + len(event_id),
        event_type=event_type,
        role="MAKER" if event_type == "MAKER_FILL_PROXY" else "TAKER",
        side=side,
        price=price,
        shares=shares,
        inference="TEST",
        reason="test",
        core_side=side,
        core_source="test",
        maker_up_shares=0,
        maker_down_shares=0,
        taker_up_shares=0,
        taker_down_shares=0,
    )


def test_locked_policy_scales_without_suppressing_maker_frequency() -> None:
    state = CapitalS1State()
    state, maker = reduce_source_event(
        state, event_type="MAKER_FILL_PROXY", side="UP", original_shares=18
    )
    assert maker["accepted"] is True
    assert maker["scaledShares"] == 1

    state, first = reduce_source_event(state, event_type="TAKER_INTENT", side="UP", original_shares=180)
    state, flip = reduce_source_event(state, event_type="TAKER_INTENT", side="DOWN", original_shares=72)
    state, blocked = reduce_source_event(state, event_type="TAKER_INTENT", side="UP", original_shares=18)
    state, later_maker = reduce_source_event(
        state, event_type="MAKER_FILL_PROXY", side="DOWN", original_shares=18
    )
    assert first["scaledShares"] == 2
    assert flip["accepted"] is True and flip["scaledShares"] == 2
    assert blocked["accepted"] is False
    assert state.taker_blocked is True
    assert later_maker["accepted"] is True and later_maker["scaledShares"] == 1


def test_economics_reports_fee_and_slippage_pressure() -> None:
    result = market_economics(
        [
            {"accepted": 1, "role": "MAKER", "side": "UP", "price": 0.4, "scaled_shares": 1},
            {"accepted": 1, "role": "TAKER", "side": "UP", "price": 0.5, "scaled_shares": 2},
        ],
        "UP",
    )
    assert result["costUsdt"] == 1.4
    assert result["grossPnlUsdt"] == 1.6
    assert result["takerFeeUsdt"] == 0.02
    assert result["stress1PnlUsdt"] < result["feeNetPnlUsdt"]
    assert result["stress2PnlUsdt"] < result["stress1PnlUsdt"]


def test_cap100_shrinks_final_fill_and_blocks_later_fills() -> None:
    partial = cap100_fill(used_capital_usdt=99, role="TAKER", price=0.5, requested_shares=10)
    assert partial["truncated"] is True
    assert partial["blocked"] is False
    assert abs(partial["capitalCostUsdt"] - 1) < 1e-9
    assert partial["executedShares"] < 2
    blocked = cap100_fill(used_capital_usdt=100, role="MAKER", price=0.4, requested_shares=1)
    assert blocked["truncated"] is True
    assert blocked["blocked"] is True
    assert blocked["executedShares"] == 0


def test_min1_exec_enforces_order_floor_and_fee_inclusive_cap() -> None:
    cheap = min1_exec_cap100_fill(used_capital_usdt=0, role="MAKER", price=0.01, desired_shares=1)
    assert cheap["minimumUplift"] is True
    assert cheap["executedShares"] == 100
    assert cheap["principalCostUsdt"] == 1
    taker = min1_exec_cap100_fill(used_capital_usdt=98.98, role="TAKER", price=0.5, desired_shares=2)
    assert abs(taker["capitalCostUsdt"] - 1.02) < 1e-9
    assert taker["remainingAfterUsdt"] < 1e-9
    blocked = min1_exec_cap100_fill(used_capital_usdt=99.5, role="MAKER", price=0.5, desired_shares=1)
    assert blocked["blocked"] is True
    assert blocked["reason"] == "INSUFFICIENT_CAPITAL_FOR_MINIMUM_ORDER"


def test_growth_budget_scales_with_wallet_without_permanent_cap() -> None:
    assert planned_market_budget(100) == 20
    assert planned_market_budget(1000) == 200
    assert planned_market_budget(4) == 1
    assert planned_market_budget(0.99) == 0


def test_time20_unlocks_market_budget_in_five_cumulative_minutes() -> None:
    assert time20_unlocked_fraction(None) == 0
    assert time20_unlocked_fraction(300) == 0.2
    assert time20_unlocked_fraction(241) == 0.2
    assert time20_unlocked_fraction(240) == 0.4
    assert time20_unlocked_fraction(181) == 0.4
    assert time20_unlocked_fraction(180) == 0.6
    assert time20_unlocked_fraction(120) == 0.8
    assert time20_unlocked_fraction(60) == 1.0
    assert time20_unlocked_fraction(0) == 1.0


def test_min1_forward_recomputes_inventory_and_never_exceeds_cap(tmp_path) -> None:
    observer = WalletShadowObserver(tmp_path / "min1.db", tmp_path / "missing-simulation.db")
    try:
        observer._reset_market(1, 100, "deployment")
        observer._reset_market(2, 200, "complete")
        maker = source_event("min1-cheap", "MAKER_FILL_PROXY", "UP", 18, 0.01)
        observer._advance_min1([maker], {"upAsk": 0.01, "downAsk": 0.99}, {"side": "DOWN"})
        assert observer.min1_maker_up_shares == 100
        assert observer.min1_taker_down_shares == 2
        for index in range(150):
            item = source_event(f"min1-{index}", "MAKER_FILL_PROXY", "UP", 18, 0.5)
            observer._advance_min1([item], {"upAsk": 0.5, "downAsk": 0.5}, {"side": "DOWN"})
        assert observer.min1_used_capital_usdt <= 100 + 1e-8
        with observer.db_lock:
            rows = [dict(row) for row in observer.db.execute(
                "SELECT principal_cost_usdt,executed_shares,blocked FROM wallet_shadow_min1_events"
            )]
        assert rows
        assert all(float(item["principal_cost_usdt"]) >= 1 - 1e-8 for item in rows if float(item["executed_shares"]) > 0)
        assert any(bool(item["blocked"]) for item in rows)
    finally:
        observer.stop()


def test_time20_forward_cannot_borrow_later_minute_budget(tmp_path) -> None:
    observer = WalletShadowObserver(tmp_path / "time20.db", tmp_path / "missing-simulation.db")
    try:
        observer._reset_market(1, 100, "deployment")
        observer._reset_market(2, 200, "first complete market")
        assert observer.time20_active_market is True
        assert observer.time20_market_budget_usdt == 20

        first_minute = [
            source_event(f"time20-first-{index}", "MAKER_FILL_PROXY", "UP", 18, 0.5)
            for index in range(10)
        ]
        observer._advance_time20(
            first_minute,
            {"secondsLeft": 299, "upAsk": 0.5, "downAsk": 0.5},
            {"side": "UP"},
        )
        assert observer.time20_unlocked_fraction == 0.2
        assert abs(observer.time20_used_capital_usdt - 4.0) < 1e-8

        second_minute = [
            source_event(f"time20-second-{index}", "MAKER_FILL_PROXY", "UP", 18, 0.5)
            for index in range(10)
        ]
        observer._advance_time20(
            second_minute,
            {"secondsLeft": 239, "upAsk": 0.5, "downAsk": 0.5},
            {"side": "UP"},
        )
        assert observer.time20_unlocked_fraction == 0.4
        assert abs(observer.time20_used_capital_usdt - 8.0) < 1e-8

        with observer.db_lock:
            rows = [dict(item) for item in observer.db.execute(
                """SELECT principal_cost_usdt,executed_shares,blocked
                     FROM wallet_shadow_growth_events WHERE cohort=?""",
                ("MIN1_WALLET_GROWTH_TIME20",),
            )]
        assert sum(1 for item in rows if float(item["executed_shares"]) > 0) == 8
        assert all(
            float(item["principal_cost_usdt"]) >= 1 - 1e-8
            for item in rows
            if float(item["executed_shares"]) > 0
        )
        assert any(bool(item["blocked"]) for item in rows)
    finally:
        observer.stop()


def test_batched_reserve_accumulates_maker_floor_and_sizes_taker_from_residual(tmp_path) -> None:
    observer = WalletShadowObserver(tmp_path / "batched.db", tmp_path / "missing-simulation.db")
    try:
        observer._reset_market(1, 100, "deployment")
        observer._reset_market(2, 200, "first complete market")
        assert observer.batched_active_market is True
        assert observer.batched_market_budget_usdt == 20

        first_nine = [
            source_event(f"batched-{index}", "MAKER_FILL_PROXY", "UP", 18, 0.1)
            for index in range(9)
        ]
        observer._advance_batched(first_nine, {"upAsk": 0.1, "downAsk": 0.5}, {"side": "DOWN"})
        assert observer.batched_maker_capital_usdt == 0
        assert observer.batched_pending_maker_shares["UP"] == 9
        observer._reset_batched_market(2, "first complete market")
        assert observer.batched_pending_maker_shares["UP"] == 9

        tenth = source_event("batched-9", "MAKER_FILL_PROXY", "UP", 18, 0.1)
        observer._advance_batched([tenth], {"upAsk": 0.1, "downAsk": 0.5}, {"side": "DOWN"})
        assert abs(observer.batched_maker_capital_usdt - 1.0) < 1e-8
        assert observer.batched_pending_maker_shares["UP"] == 0
        observer._advance_batched([], {"upAsk": 0.1, "downAsk": 0.5}, {"side": "DOWN"})
        assert observer.batched_core_stability_count >= 2
        assert abs(observer.batched_taker_down_shares - 10.0) < 1e-8
        assert abs(observer.batched_taker_capital_usdt - 5.1) < 1e-8

        with observer.db_lock:
            rows = [dict(item) for item in observer.db.execute(
                """SELECT role,principal_cost_usdt,executed_shares
                     FROM wallet_shadow_growth_events WHERE cohort=? ORDER BY at_ms,id""",
                ("MIN1_BATCHED_MAKER_TAKER_RESERVE",),
            )]
        assert sorted(item["role"] for item in rows) == ["MAKER", "TAKER"]
        assert all(float(item["principal_cost_usdt"]) >= 1 - 1e-8 for item in rows)
    finally:
        observer.stop()


def test_forward_boundary_excludes_deployment_market_and_persists_next(tmp_path) -> None:
    observer = WalletShadowObserver(tmp_path / "shadow.db", tmp_path / "missing-simulation.db")
    try:
        observer._reset_market(1, 100, "deployment market")
        assert observer.capital_s1_active_market is False
        observer._advance_capital_s1([source_event("excluded", "MAKER_FILL_PROXY", "UP", 18, 0.4)])

        observer._reset_market(2, 200, "first complete market")
        assert observer.capital_s1_active_market is True
        events = [
            source_event("maker", "MAKER_FILL_PROXY", "UP", 18, 0.4),
            source_event("taker-up", "TAKER_INTENT", "UP", 180, 0.5),
            source_event("taker-down", "TAKER_INTENT", "DOWN", 72, 0.5),
            source_event("taker-up-2", "TAKER_INTENT", "UP", 18, 0.5),
        ]
        observer._advance_capital_s1(events)
        observer._advance_capital_s1(events)

        with observer.db_lock:
            rows = [dict(row) for row in observer.db.execute(
                "SELECT source_event_id,accepted,scaled_shares FROM wallet_shadow_capital_s1_events ORDER BY source_at_ms,id"
            )]
        assert len(rows) == 4
        by_id = {item["source_event_id"]: item for item in rows}
        assert by_id["maker"]["scaled_shares"] == 1
        assert by_id["taker-up"]["scaled_shares"] == 2
        assert by_id["taker-down"]["accepted"] == 1
        assert by_id["taker-up-2"]["accepted"] == 0

        observer._store_market_result(2, {"title": "first complete market"}, "UP")
        performance = observer._capital_s1_performance()
        assert performance["settledMarkets"] == 1
        assert performance["sourceEvents"] == 4
        assert performance["acceptedEvents"] == 3
        assert performance["maximumCostPerMarketUsdt"] > 0
        cap100 = observer._cap100_performance()
        assert cap100["settledMarkets"] == 1
        assert cap100["maximumCapitalPerMarketUsdt"] <= 100
    finally:
        observer.stop()


def test_snapshot_labels_capital_s1_as_isolated_paper_forward(tmp_path) -> None:
    observer = WalletShadowObserver(tmp_path / "shadow.db", tmp_path / "missing-simulation.db")
    try:
        snapshot = observer.snapshot()["capitalS1"]
        assert snapshot["paperOnly"] is True
        assert snapshot["forwardOnly"] is True
        assert snapshot["liveOrdersAffected"] is False
        assert snapshot["historicalBackfill"] is False
        assert snapshot["config"]["takerEffectiveCapShares"] == 2
        cap100 = observer.snapshot()["capitalS1Cap100Stress"]
        assert cap100["paperOnly"] is True
        assert cap100["config"]["maximumCapitalPerMarketUsdt"] == 100
        min1 = observer.snapshot()["min1ExecCap100"]
        assert min1["config"]["minimumOrderPrincipalUsdt"] == 1
        assert min1["config"]["maximumCapitalPerMarketUsdt"] == 100
        growth = observer.snapshot()["min1WalletGrowth"]
        assert growth["config"]["initialWalletUsdt"] == 100
        assert growth["config"]["permanentWalletCap"] is None
        assert growth["config"]["marketBudgetFractionOfAvailableCash"] == 0.2
        time20 = observer.snapshot()["min1WalletGrowthTime20"]
        assert time20["paperOnly"] is True
        assert time20["liveOrdersAffected"] is False
        assert time20["config"]["initialWalletUsdt"] == 100
        assert time20["config"]["permanentWalletCap"] is None
        assert time20["config"]["timeTranches"] == 5
        assert time20["config"]["trancheFraction"] == 0.2
        assert time20["config"]["carryUnusedForwardWithinMarket"] is True
        assert time20["config"]["futureTrancheBorrowing"] is False
        batched = observer.snapshot()["min1BatchedMakerTakerReserve"]
        assert batched["paperOnly"] is True
        assert batched["forwardOnly"] is True
        assert batched["historicalBackfill"] is False
        assert batched["liveOrdersAffected"] is False
        assert batched["config"]["sourceMakerUnitShares"] == 18
        assert batched["config"]["researchMakerSignalShares"] == 1
        assert batched["config"]["makerBudgetFraction"] == 0.2
        assert batched["config"]["takerReserveFraction"] == 0.8
        assert batched["config"]["coreStabilityConfirmations"] == 2
    finally:
        observer.stop()
