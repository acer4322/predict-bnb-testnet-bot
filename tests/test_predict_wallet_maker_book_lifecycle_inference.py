from __future__ import annotations

import sqlite3

from predict_bot import predict_wallet_maker_book_inference_collector as base
from predict_bot.predict_wallet_maker_book_inference_collector_v2 import (
    MakerBookLifecycleInferenceCollector,
    VERSION,
)


def _target_db(path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE wallet_shadow_target_events (
            leg_id TEXT PRIMARY KEY,wallet TEXT,market_id INTEGER,role TEXT,side TEXT,
            quote_type TEXT,order_hash TEXT,event_ms INTEGER,price REAL,shares REAL,raw_json TEXT
        );
        CREATE TABLE wallet_shadow_target_event_context (
            leg_id TEXT PRIMARY KEY,observed_at_ms INTEGER
        );
        """
    )
    con.commit()
    con.close()


def _activate(collector: MakerBookLifecycleInferenceCollector, *, base_ms: int) -> None:
    assert collector._activate_market(100, title="deployment", precision=2, window_end_ms=None) is False
    assert collector._activate_market(101, title="forward", precision=2, window_end_ms=None) is True
    with collector.db_lock:
        collector.db.execute(
            "UPDATE maker_book_inference_meta SET deployed_at_ms=? WHERE cohort=?",
            (base_ms - 1000, collector.cohort),
        )
        collector.db.commit()


def test_lifecycle_infers_placement_resting_and_same_price_refill(tmp_path) -> None:
    target_db = tmp_path / "target.db"
    _target_db(target_db)
    collector = MakerBookLifecycleInferenceCollector(tmp_path / "inference.db", target_db)
    try:
        base_ms = base.now_ms() - 12_000
        _activate(collector, base_ms=base_ms)

        collector.process_book_payload(101, {
            "marketId": 101, "updateTimestampMs": base_ms,
            "orderCount": 2, "bids": [[0.44, 10]], "asks": [[0.55, 8]],
        }, received_ms=base_ms + 10)
        collector.process_book_payload(101, {
            "marketId": 101, "updateTimestampMs": base_ms + 100,
            "orderCount": 3, "bids": [[0.45, 18], [0.44, 10]], "asks": [[0.55, 8]],
        }, received_ms=base_ms + 110)
        collector.process_book_payload(101, {
            "marketId": 101, "updateTimestampMs": base_ms + 1200,
            "orderCount": 3, "bids": [[0.45, 12], [0.44, 10]], "asks": [[0.55, 8]],
        }, received_ms=base_ms + 1210)
        collector.process_book_payload(101, {
            "marketId": 101, "updateTimestampMs": base_ms + 2000,
            "orderCount": 3, "bids": [[0.45, 18], [0.44, 10]], "asks": [[0.55, 8]],
        }, received_ms=base_ms + 2010)
        collector.process_book_payload(101, {
            "marketId": 101, "updateTimestampMs": base_ms + 7000,
            "orderCount": 3, "bids": [[0.45, 18], [0.44, 11]], "asks": [[0.55, 8]],
        }, received_ms=base_ms + 7010)

        con = sqlite3.connect(target_db)
        con.execute(
            """INSERT INTO wallet_shadow_target_events(
                   leg_id,wallet,market_id,role,side,quote_type,order_hash,event_ms,price,shares,raw_json
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            ("leg-1", base.TARGET_WALLET, 101, "MAKER", "UP", "BID", "order-1",
             base_ms + 1150, 0.45, 6.0, "{}"),
        )
        con.execute(
            "INSERT INTO wallet_shadow_target_event_context(leg_id,observed_at_ms) VALUES (?,?)",
            ("leg-1", base_ms + 1250),
        )
        con.commit()
        con.close()

        collector._ingest_new_target_events()
        collector._match_pending_events()
        row = collector.db.execute(
            "SELECT * FROM maker_book_inference_lifecycles WHERE target_leg_id LIKE 'order-1:%' OR target_leg_id='order-1'"
        ).fetchone()
        if row is None:
            row = collector.db.execute("SELECT * FROM maker_book_inference_lifecycles LIMIT 1").fetchone()
        assert row is not None
        assert row["placement_source_ms"] == base_ms + 100
        assert row["resting_ms"] == 1100
        assert row["post_action"] == "SAME_PRICE_REFILL"
        assert row["completion_type"] == "AGGREGATE_LEVEL_REMAINS"
        assert row["placement_confidence"] > 0

        state = collector.snapshot()
        assert state["version"] == VERSION
        lifecycle = state["lifecycleInference"]
        assert lifecycle["lifecycles"] >= 1
        assert lifecycle["samePriceRefillRate"] > 0
        assert "lower bound" in lifecycle["capitalLowerBound"]["interpretation"]
        assert "anonymous" in lifecycle["identityBoundary"]
    finally:
        collector.stop()


def test_cancel_candidate_reprice_is_speculative_not_high_confidence(tmp_path) -> None:
    target_db = tmp_path / "target.db"
    _target_db(target_db)
    collector = MakerBookLifecycleInferenceCollector(tmp_path / "inference.db", target_db)
    try:
        base_ms = base.now_ms() - 12_000
        _activate(collector, base_ms=base_ms)
        collector.process_book_payload(101, {
            "marketId": 101, "updateTimestampMs": base_ms,
            "orderCount": 2, "bids": [[0.44, 5]], "asks": [[0.55, 8]],
        }, received_ms=base_ms + 10)
        collector.process_book_payload(101, {
            "marketId": 101, "updateTimestampMs": base_ms + 100,
            "orderCount": 3, "bids": [[0.45, 18], [0.44, 5]], "asks": [[0.55, 8]],
        }, received_ms=base_ms + 110)
        collector.process_book_payload(101, {
            "marketId": 101, "updateTimestampMs": base_ms + 2000,
            "orderCount": 2, "bids": [[0.44, 5]], "asks": [[0.55, 8]],
        }, received_ms=base_ms + 2010)
        collector.process_book_payload(101, {
            "marketId": 101, "updateTimestampMs": base_ms + 2500,
            "orderCount": 2, "bids": [[0.44, 23]], "asks": [[0.55, 8]],
        }, received_ms=base_ms + 2510)
        collector.process_book_payload(101, {
            "marketId": 101, "updateTimestampMs": base_ms + 6000,
            "orderCount": 2, "bids": [[0.44, 24]], "asks": [[0.55, 8]],
        }, received_ms=base_ms + 6010)

        assert collector._infer_cancels() >= 1
        row = collector.db.execute(
            "SELECT * FROM maker_book_inference_cancel_candidates ORDER BY cancel_source_ms LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row["post_action"] == "REPRICE_1_3_TICKS"
        assert row["likely_reason"] == "REPRICE_1_3_TICKS"
        assert row["confidence_label"] in {"LOW_SPECULATIVE", "MEDIUM_SPECULATIVE"}
        assert float(row["confidence"]) <= 0.74
    finally:
        collector.stop()


def test_pressure_requires_multi_feature_agreement() -> None:
    result = MakerBookLifecycleInferenceCollector._pressure({
        "direction_score": 0.8,
        "spot_return_3s_bps": 2.0,
        "futures_return_3s_bps": 2.2,
        "spot_taker_imbalance_1s": 0.8,
        "futures_taker_imbalance_1s": 0.9,
        "spot_queue_imbalance": 0.0,
        "futures_queue_imbalance": 0.0,
    })
    assert result["side"] == "UP"
    assert result["upVotes"] >= 3
