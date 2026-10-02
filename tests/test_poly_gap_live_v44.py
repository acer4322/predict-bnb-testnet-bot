from __future__ import annotations

from predict_bot import poly_gap_live as base
from predict_bot.poly_gap_live_v43 import QuoteTimestampRaceFixedPolyGapLiveEngine
from predict_bot.poly_gap_live_v44 import (
    DUPLICATE_INFLIGHT_STATUS,
    IntegratedDecisionAndIdempotencyPolyGapLiveEngine,
)
from predict_bot.poly_gap_runtime_decision import build_runtime_decision


def _market(market_id: int) -> dict[str, object]:
    now_ms = base._now_ms()
    return {
        "market_id": market_id,
        "topic_id": market_id + 1000,
        "up_token_id": f"up-{market_id}",
        "down_token_id": f"down-{market_id}",
        "fee_rate_bps": 200,
        "start_ms": now_ms - 1_000,
        "end_ms": now_ms + 299_000,
    }


def _insert_round(
    engine: IntegratedDecisionAndIdempotencyPolyGapLiveEngine,
    market_id: int,
) -> dict[str, object]:
    market = _market(market_id)
    return engine._insert_round(
        market=market,
        side="UP",
        token_id=str(market["up_token_id"]),
        stake=1.0,
        poly_selected=0.90,
        ask=0.50,
        edge=0.40,
    )


def test_decision_source_stale_is_not_mislabeled_as_binance_book() -> None:
    decision = build_runtime_decision(
        {
            "status": "WAITING_BINANCE_BOOK",
            "lastError": "Poly source freshness blocked BUY",
            "sourceFreshnessV41": {
                "current": {
                    "policy": "BLOCK_SOURCE_STALE",
                    "sourceAgeMs": 901,
                    "quoteReceiptAgeMs": 8,
                    "transportAgeMs": 893,
                    "timestampSource": "COLLECTOR_QUOTE_TIMESTAMP_V3",
                }
            },
        }
    )
    assert decision["state"] == "BLOCKED"
    assert decision["source"] == "POLY_FEED"
    assert decision["code"] == "POLY_SOURCE_STALE"
    assert decision["rawStatus"] == "WAITING_BINANCE_BOOK"
    assert decision["affectsNewBuy"] is True
    assert decision["affectsSellExit"] is False


def test_decision_active_position_outranks_entry_source_block() -> None:
    decision = build_runtime_decision(
        {
            "status": "ENTRY_POLY_QUOTE_SOURCE_STALE_V43",
            "activeRound": {"id": 12, "state": "OPEN"},
            "sourceFreshnessV41": {"current": {"policy": "BLOCK_SOURCE_STALE"}},
        }
    )
    assert decision["state"] == "ACTIVE"
    assert decision["source"] == "POSITION"
    assert decision["code"] == "MANAGING_POSITION"
    assert decision["activeRoundId"] == 12


def test_decision_distinguishes_empty_binance_book() -> None:
    decision = build_runtime_decision(
        {
            "status": "WAITING_BINANCE_BOOK",
            "executionDepthV32": {
                "lastBuyDepth": {"available": False, "reason": "NO_ASK_LEVELS"}
            },
        }
    )
    assert decision["state"] == "UNAVAILABLE"
    assert decision["source"] == "BINANCE_BOOK"
    assert decision["code"] == "BINANCE_NO_ASK_LEVELS"


def test_v44_preserves_v43_lineage() -> None:
    assert issubclass(
        IntegratedDecisionAndIdempotencyPolyGapLiveEngine,
        QuoteTimestampRaceFixedPolyGapLiveEngine,
    )


def test_v44_assigns_unique_canonical_action_keys(tmp_path) -> None:
    db_path = tmp_path / "poly_gap_v44_action_keys.db"
    engine = IntegratedDecisionAndIdempotencyPolyGapLiveEngine(db_path)
    try:
        row = _insert_round(engine, 4444)
        round_id = int(row["id"])

        first_id = engine._begin_attempt(row, action="BUY", depth=None)
        second_id = engine._begin_attempt(row, action="BUY", depth=None)

        with engine.db_lock:
            first = engine.db.execute(
                "SELECT action_key,attempt_no FROM poly_gap_live_execution_attempts WHERE id=?",
                (first_id,),
            ).fetchone()
            second = engine.db.execute(
                "SELECT action_key,attempt_no FROM poly_gap_live_execution_attempts WHERE id=?",
                (second_id,),
            ).fetchone()
        assert first is not None and second is not None
        assert first["attempt_no"] == 1
        assert second["attempt_no"] == 2
        assert first["action_key"] == f"4444:{round_id}:BUY:1"
        assert second["action_key"] == f"4444:{round_id}:BUY:2"
        assert first["action_key"] != second["action_key"]
    finally:
        engine.stop()


def test_v44_blocks_concurrent_duplicate_logical_buy_without_touching_round(tmp_path) -> None:
    db_path = tmp_path / "poly_gap_v44_inflight.db"
    engine = IntegratedDecisionAndIdempotencyPolyGapLiveEngine(db_path)
    try:
        row = _insert_round(engine, 4545)
        round_id = int(row["id"])
        key = (round_id, "BUY")
        with engine._v44_execution_lock:
            engine._v44_inflight_actions.add(key)
        try:
            engine._open_round(row, {})
        finally:
            with engine._v44_execution_lock:
                engine._v44_inflight_actions.discard(key)

        refreshed = engine._round_state(round_id)
        assert refreshed is not None
        assert refreshed["state"] == row["state"]
        assert engine.status == DUPLICATE_INFLIGHT_STATUS
        assert engine._v44_duplicate_inflight_blocks == 1
    finally:
        engine.stop()
