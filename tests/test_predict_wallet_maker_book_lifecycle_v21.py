from __future__ import annotations

import json

from predict_bot import predict_wallet_maker_book_inference_collector as base
from predict_bot.predict_wallet_maker_book_inference_collector_v2_1 import (
    MakerBookConsumableLifecycleCollector,
    VERSION,
)


def _activate(collector: MakerBookConsumableLifecycleCollector, market_id: int = 101) -> None:
    assert collector._activate_market(100, title="deployment", precision=2, window_end_ms=None) is False
    assert collector._activate_market(market_id, title="forward", precision=2, window_end_ms=None) is True


def _insert_target(
    collector: MakerBookConsumableLifecycleCollector,
    *,
    leg_id: str,
    order_hash: str,
    event_ms: int,
    matched_ms: int,
    shares: float,
    price: float = 0.45,
) -> None:
    with collector.db_lock:
        collector.db.execute(
            """INSERT INTO maker_book_inference_target_events(
                   leg_id,target_rowid,wallet,market_id,order_hash,target_event_ms,target_observed_ms,
                   side,target_price,target_shares,native_book_side,native_price,status,
                   matched_update_id,matched_source_ms,matched_received_ms,before_size,after_size,
                   observed_decrease,event_delay_ms,match_confidence,confidence_label,evidence_json
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                leg_id, 1, base.TARGET_WALLET, 101, order_hash, event_ms, event_ms,
                "UP", price, shares, "BID", price, "MATCHED", None, matched_ms, matched_ms,
                shares, 0.0, shares, abs(matched_ms - event_ms), 0.9, "HIGH", "{}",
            ),
        )
        collector.db.commit()


def test_v21_one_public_placement_cannot_create_two_18_share_parents(tmp_path) -> None:
    collector = MakerBookConsumableLifecycleCollector(tmp_path / "maker.db", tmp_path / "missing-target.db")
    try:
        _activate(collector)
        t0 = base.now_ms() - 20_000
        collector.process_book_payload(101, {
            "marketId": 101, "updateTimestampMs": t0,
            "orderCount": 1, "bids": [[0.44, 5]], "asks": [[0.55, 5]],
        }, received_ms=t0)
        collector.process_book_payload(101, {
            "marketId": 101, "updateTimestampMs": t0 + 100,
            "orderCount": 2, "bids": [[0.45, 18], [0.44, 5]], "asks": [[0.55, 5]],
        }, received_ms=t0 + 100)
        collector.process_book_payload(101, {
            "marketId": 101, "updateTimestampMs": t0 + 1000,
            "orderCount": 1, "bids": [[0.44, 5]], "asks": [[0.55, 5]],
        }, received_ms=t0 + 1000)
        collector.process_book_payload(101, {
            "marketId": 101, "updateTimestampMs": t0 + 2000,
            "orderCount": 2, "bids": [[0.45, 18], [0.44, 5]], "asks": [[0.55, 5]],
        }, received_ms=t0 + 2000)
        collector.process_book_payload(101, {
            "marketId": 101, "updateTimestampMs": t0 + 3000,
            "orderCount": 1, "bids": [[0.44, 5]], "asks": [[0.55, 5]],
        }, received_ms=t0 + 3000)
        _insert_target(collector, leg_id="a", order_hash="order-a", event_ms=t0 + 900, matched_ms=t0 + 1000, shares=18)
        _insert_target(collector, leg_id="b", order_hash="order-b", event_ms=t0 + 2900, matched_ms=t0 + 3000, shares=18)

        assert collector._reconcile_market(101) is True
        rows = [dict(row) for row in collector.db.execute(
            "SELECT parent_id,placement_allocated_shares FROM maker_book_inference_v21_parent_lifecycles ORDER BY parent_id"
        )]
        assert len(rows) == 2
        # There were two +18 public placements in this synthetic book, therefore
        # exactly 36 shares are available across both parents, never >36 through reuse.
        assert sum(float(row["placement_allocated_shares"]) for row in rows) <= 36.0 + 1e-9
        allocations = collector.db.execute(
            """SELECT update_id,native_price,SUM(allocated_quantity) used,MAX(public_delta_quantity) capacity
                 FROM maker_book_inference_v21_allocations
                WHERE allocation_kind='PARENT_PLACEMENT'
                GROUP BY update_id,native_price"""
        ).fetchall()
        assert allocations
        assert all(float(row[2]) <= float(row[3]) + 1e-9 for row in allocations)
    finally:
        collector.stop()


def test_v21_groups_partial_fills_by_order_hash_into_one_parent(tmp_path) -> None:
    collector = MakerBookConsumableLifecycleCollector(tmp_path / "maker.db", tmp_path / "missing-target.db")
    try:
        _activate(collector)
        t0 = base.now_ms() - 20_000
        collector.process_book_payload(101, {
            "marketId": 101, "updateTimestampMs": t0,
            "orderCount": 1, "bids": [[0.44, 5]], "asks": [[0.55, 5]],
        }, received_ms=t0)
        collector.process_book_payload(101, {
            "marketId": 101, "updateTimestampMs": t0 + 100,
            "orderCount": 2, "bids": [[0.45, 18], [0.44, 5]], "asks": [[0.55, 5]],
        }, received_ms=t0 + 100)
        collector.process_book_payload(101, {
            "marketId": 101, "updateTimestampMs": t0 + 1000,
            "orderCount": 2, "bids": [[0.45, 12], [0.44, 5]], "asks": [[0.55, 5]],
        }, received_ms=t0 + 1000)
        collector.process_book_payload(101, {
            "marketId": 101, "updateTimestampMs": t0 + 2000,
            "orderCount": 1, "bids": [[0.44, 5]], "asks": [[0.55, 5]],
        }, received_ms=t0 + 2000)
        _insert_target(collector, leg_id="p1", order_hash="parent-18", event_ms=t0 + 900, matched_ms=t0 + 1000, shares=6)
        _insert_target(collector, leg_id="p2", order_hash="parent-18", event_ms=t0 + 1900, matched_ms=t0 + 2000, shares=12)

        assert collector._reconcile_market(101) is True
        row = dict(collector.db.execute(
            "SELECT * FROM maker_book_inference_v21_parent_lifecycles"
        ).fetchone())
        assert row["target_fill_count"] == 2
        assert abs(float(row["target_filled_shares"]) - 18.0) < 1e-9
        assert row["multi_fill_parent"] == 1
        assert row["observed_filled_near_18"] == 1
        assert float(row["placement_allocated_shares"]) <= 18.0 + 1e-9
        assert float(row["fill_allocation_coverage"]) >= 0.99

        state = collector.snapshot()
        assert state["version"] == VERSION
        lifecycle = state["lifecycleInference"]
        assert lifecycle["consumableQuantityAllocation"] is True
        assert lifecycle["parents"] == 1
        assert lifecycle["multiFillParentRate"] == 1.0
        assert state["lifecycleInferenceV2Retired"]["status"] == "RETIRED_MANY_TO_ONE_EVIDENCE_REUSE"
    finally:
        collector.stop()
