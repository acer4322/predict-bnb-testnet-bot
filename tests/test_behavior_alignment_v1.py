from __future__ import annotations

import sqlite3

from tools.behavior_alignment.capability_matrix import build_capability_matrix, valid_status
from tools.behavior_alignment.diff_engine import compare_market
from tools.behavior_alignment.economic_cycle_metrics import economic_cycle_metrics
from tools.behavior_alignment.schema import (
    canonical_event,
    trace_document,
    validate_trace_document,
)
from tools.behavior_alignment.target_trace import build_target_trace


def _insert_leg(connection, row_id, time_ms, side, shares, price, role="MAKER"):
    connection.execute(
        """
        insert into eth_events
          (id, leg_id, market_id, role, side, order_hash, transaction_hash,
           settlement_id, event_ms, observed_at_ms, price, shares)
        values (?, ?, 7, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row_id,
            f"leg-{row_id}",
            role,
            side,
            f"order-{row_id}",
            f"tx-{row_id}",
            f"settlement-{row_id}",
            time_ms,
            time_ms + 5,
            price,
            shares,
        ),
    )


def test_target_trace_atomic_fifo_partial_multi_payment_and_composite(tmp_path):
    db = tmp_path / "target.db"
    connection = sqlite3.connect(db)
    connection.execute(
        """
        create table eth_events (
          id integer, leg_id text, market_id integer, role text, side text,
          order_hash text, transaction_hash text, settlement_id text,
          event_ms integer, observed_at_ms integer, price real, shares real
        )
        """
    )
    # Birth 5 UP debt; pay 2 DOWN; later pay the remaining 3 and birth 2 DOWN.
    _insert_leg(connection, 1, 300_010, "UP", 5.0, 0.40)
    _insert_leg(connection, 2, 300_020, "DOWN", 2.0, 0.50)
    _insert_leg(connection, 3, 300_030, "DOWN", 5.0, 0.45, role="TAKER")
    connection.commit()
    connection.close()

    trace = build_target_trace(db, 7)
    assert validate_trace_document(trace) == []
    metrics = economic_cycle_metrics(trace)
    assert metrics["responsibilitiesBorn"] == 2
    assert metrics["responsibilitiesCompleted"] == 1
    assert metrics["multiPaymentCompletedResponsibilities"] == 1
    assert metrics["compositeRepairThenExpandClocks"] == 1
    assert abs(metrics["terminalOutstandingDebt"] - 2.0) < 1e-9
    payments = [event for event in trace["events"] if event["eventType"] == "REPAIR_PAYMENT"]
    assert [event["allocation"]["repairAllocation"] for event in payments] == [2.0, 3.0]


def test_schema_rejects_physical_allocation_nonconservation():
    event = canonical_event(
        source="OUR",
        evidence_class="AUTHORITATIVE_EVENT",
        market_id=9,
        sequence=0,
        event_type="PHYSICAL_FILL_ALLOCATED",
        responsibility={"role": "REPAIR"},
        manager_debt={"before": 2.0, "after": 1.0},
        allocation={
            "physicalFillQty": 2.0,
            "repairAllocation": 1.0,
            "overflowAllocation": 0.5,
        },
    )
    document = trace_document(
        source="OUR", market_id=9, events=[event], provenance={"test": True}
    )
    assert any("physical conservation" in error for error in validate_trace_document(document))


def test_diff_localizes_late_live_debt_block_to_router():
    our_event = canonical_event(
        source="OUR",
        evidence_class="AUTHORITATIVE_EVENT",
        market_id=11,
        sequence=0,
        event_type="ROUTER_DECISION",
        time_ms=150_000,
        seconds_left=150.0,
        normalized_phase=0.5,
        responsibility={"role": "REPAIR", "responsibilityId": 1},
        manager_debt={"before": 1.5, "after": 1.5},
        decision={
            "module": "RepairExecutionRouter",
            "allow": False,
            "reason": "LATE_NO_NEW_ACTIVE_EXPOSURE",
            "terminalBlocker": "LATE_NO_NEW_ACTIVE_EXPOSURE",
        },
    )
    our = trace_document(source="OUR", market_id=11, events=[our_event], provenance={})
    target = trace_document(source="TARGET", market_id=11, events=[], provenance={})
    diff = compare_market(our, target)
    gap = next(gap for gap in diff["gaps"] if gap["capability"] == "Late Existing-debt Repair Payment")
    assert gap["primaryModule"] == "RepairExecutionRouter"
    assert gap["evidence"]["ourLateBlocksWithLiveDebt"] == 1


def test_capability_matrix_enforces_controlled_status_vocabulary():
    assert valid_status("IMPLEMENTED_CORRECT")
    assert valid_status("BLOCKED_BY_REPAIR_EXECUTION_ROUTER")
    row = {
        "capability": "x",
        "status": "MADE_UP",
        "targetEvidence": [],
        "ourEvidence": [],
        "ourImplementation": [],
        "primaryModule": "x",
        "blocker": None,
        "nextMinimalTest": "x",
    }
    try:
        build_capability_matrix([row], evidence_cutoff="2026-09-04")
    except ValueError as error:
        assert "invalid status" in str(error)
    else:
        raise AssertionError("invalid matrix status was accepted")
