from __future__ import annotations

import sqlite3

from predict_bot import predict_wallet_maker_book_inference_collector as collector_module
from predict_bot.predict_wallet_maker_book_inference_collector import (
    MakerBookInferenceCollector,
    decode_json,
    encode_json,
    level_changes,
    native_level_for_target,
    normalize_levels,
    now_ms,
    parse_full_book,
)


def test_full_book_helpers_preserve_depth_and_down_mapping() -> None:
    levels = normalize_levels([[0.45, 10], [0.45, 2], {"price": 0.44, "size": 3}])
    assert levels == {0.44: 3.0, 0.45: 12.0}

    parsed = parse_full_book({
        "marketId": 101,
        "updateTimestampMs": 1_700_000_000_123,
        "orderCount": 5,
        "bids": [[0.45, 12], [0.44, 3]],
        "asks": [[0.55, 8]],
    })
    assert parsed is not None
    assert parsed["bids"][0.45] == 12
    assert parsed["asks"][0.55] == 8
    assert native_level_for_target("UP", 0.45, 2) == ("BID", 0.45)
    assert native_level_for_target("DOWN", 0.45, 2) == ("ASK", 0.55)

    changes = level_changes({0.45: 12, 0.44: 3}, {0.45: 5, 0.43: 4})
    by_price = {row["price"]: row for row in changes}
    assert by_price[0.45]["delta"] == -7
    assert by_price[0.44]["after"] == 0
    assert by_price[0.43]["before"] == 0
    assert decode_json(encode_json({"changes": changes}))["changes"] == changes


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


def test_collector_excludes_deployment_market_and_matches_forward_maker_fill(tmp_path) -> None:
    target_db = tmp_path / "target.db"
    _target_db(target_db)
    collector = MakerBookInferenceCollector(tmp_path / "inference.db", target_db)
    try:
        assert collector._activate_market(100, title="deployment", precision=2, window_end_ms=None) is False
        assert collector._meta()["excluded_market_id"] == 100
        assert collector._activate_market(101, title="forward", precision=2, window_end_ms=None) is True

        base = now_ms() - 6_000
        with collector.db_lock:
            collector.db.execute(
                "UPDATE maker_book_inference_meta SET deployed_at_ms=? WHERE cohort='TARGET_MAKER_BOOK_INFERENCE_V1'",
                (base - 1_000,),
            )
            collector.db.commit()

        first = collector.process_book_payload(101, {
            "marketId": 101,
            "updateTimestampMs": base,
            "orderCount": 3,
            "bids": [[0.45, 12], [0.44, 3]],
            "asks": [[0.55, 8]],
        }, received_ms=base + 20)
        second = collector.process_book_payload(101, {
            "marketId": 101,
            "updateTimestampMs": base + 200,
            "orderCount": 2,
            "bids": [[0.45, 5], [0.44, 3]],
            "asks": [[0.55, 8]],
        }, received_ms=base + 230)
        assert first is not None
        assert second is not None

        con = sqlite3.connect(target_db)
        con.execute(
            """INSERT INTO wallet_shadow_target_events(
                   leg_id,wallet,market_id,role,side,quote_type,order_hash,event_ms,price,shares,raw_json
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "leg-1", collector_module.TARGET_WALLET, 101, "MAKER", "UP", "BID", "order-1",
                base + 150, 0.45, 6.0, "{}",
            ),
        )
        con.execute(
            "INSERT INTO wallet_shadow_target_event_context(leg_id,observed_at_ms) VALUES (?,?)",
            ("leg-1", base + 250),
        )
        con.commit()
        con.close()

        collector._ingest_new_target_events()
        collector._match_pending_events()
        row = collector.db.execute(
            "SELECT * FROM maker_book_inference_target_events WHERE order_hash='order-1'"
        ).fetchone()
        assert row is not None
        assert row["status"] == "MATCHED"
        assert row["native_book_side"] == "BID"
        assert row["native_price"] == 0.45
        assert row["observed_decrease"] == 7
        assert row["confidence_label"] == "HIGH"

        state = collector.snapshot()
        assert state["forwardOnly"] is True
        assert state["historicalBackfill"] is False
        assert state["targetEventsDriveStrategy"] is False
        assert state["liveOrdersAffected"] is False
        assert state["targetInference"]["matched"] == 1
    finally:
        collector.stop()


def test_eth_cohort_keeps_an_independent_forward_boundary(tmp_path, monkeypatch) -> None:
    target_db = tmp_path / "target.db"
    _target_db(target_db)
    monkeypatch.setattr(collector_module, "ASSET", "ETH")
    monkeypatch.setattr(collector_module, "COHORT", "TARGET_MAKER_BOOK_INFERENCE_ETH_5M_V1")
    monkeypatch.setattr(collector_module, "VERSION", "TARGET_MAKER_BOOK_INFERENCE_ETH_5M_V1_FORWARD_ONLY")
    collector = MakerBookInferenceCollector(tmp_path / "eth.db", target_db)
    try:
        assert collector.asset == "ETH"
        assert collector.cohort == "TARGET_MAKER_BOOK_INFERENCE_ETH_5M_V1"
        assert collector._activate_market(201, title="ETH deployment", precision=2, window_end_ms=None) is False
        assert collector._activate_market(202, title="ETH forward", precision=2, window_end_ms=None) is True
        state = collector.snapshot()
        assert state["asset"] == "ETH"
        assert state["timeframe"] == "5M"
        assert state["excludedDeploymentMarketId"] == 201
        assert state["current"]["marketId"] == 202
    finally:
        collector.stop()


def test_eth_direct_target_legs_are_forward_only_and_deduplicated(tmp_path, monkeypatch) -> None:
    target_db = tmp_path / "unused-target.db"
    _target_db(target_db)
    monkeypatch.setattr(collector_module, "ASSET", "ETH")
    monkeypatch.setattr(collector_module, "COHORT", "TARGET_MAKER_BOOK_INFERENCE_ETH_5M_V1")
    monkeypatch.setattr(collector_module, "VERSION", "TARGET_MAKER_BOOK_INFERENCE_ETH_5M_V1_FORWARD_ONLY")
    collector = MakerBookInferenceCollector(tmp_path / "eth.db", target_db)
    try:
        assert collector._activate_market(301, title="excluded", precision=2, window_end_ms=None) is False
        assert collector._activate_market(302, title="forward", precision=2, window_end_ms=None) is True
        base = now_ms() - 6_000
        with collector.db_lock:
            collector.db.execute(
                "UPDATE maker_book_inference_meta SET deployed_at_ms=? WHERE cohort=?",
                (base - 1_000, collector.cohort),
            )
            collector.db.commit()
        collector.process_book_payload(302, {
            "marketId": 302,
            "updateTimestampMs": base,
            "orderCount": 2,
            "bids": [[0.45, 12]],
            "asks": [[0.55, 8]],
        }, received_ms=base + 20)
        collector.process_book_payload(302, {
            "marketId": 302,
            "updateTimestampMs": base + 200,
            "orderCount": 2,
            "bids": [[0.45, 5]],
            "asks": [[0.55, 8]],
        }, received_ms=base + 230)
        leg = {
            "legId": "eth-leg-1", "parentId": "MAKER:eth-order-1", "role": "MAKER",
            "marketId": 302, "side": "UP", "quoteType": "BID", "orderHash": "eth-order-1",
            "eventMs": base + 150, "price": 0.45, "shares": 6.0,
        }
        assert collector._persist_direct_target_legs([leg], observed_ms=base + 250) == 1
        assert collector._persist_direct_target_legs([leg], observed_ms=base + 300) == 0
        taker_leg = {
            "legId": "eth-taker-leg-1", "parentId": "TAKER:eth-taker-order-1", "role": "TAKER",
            "marketId": 302, "side": "DOWN", "quoteType": "BID", "orderHash": "eth-taker-order-1",
            "eventMs": base + 300, "price": 0.35, "shares": 4.0,
        }
        assert collector._persist_direct_target_legs([taker_leg], observed_ms=base + 350) == 1
        assert collector._persist_direct_target_legs([taker_leg], observed_ms=base + 400) == 0
        collector._match_pending_events()
        row = collector.db.execute(
            "SELECT * FROM maker_book_inference_target_events WHERE order_hash='eth-order-1'"
        ).fetchone()
        assert row is not None
        assert row["target_shares"] == 6.0
        assert row["status"] == "MATCHED"
        assert row["observed_decrease"] == 7.0
        assert collector.db.execute("SELECT COUNT(*) FROM maker_book_inference_source_legs").fetchone()[0] == 1
        state = collector.snapshot()
        assert state["targetActivity"]["total_events"] == 2
        assert state["targetActivity"]["maker_events"] == 1
        assert state["targetActivity"]["taker_events"] == 1
        assert state["targetInference"]["target_events"] == 1
    finally:
        collector.stop()
