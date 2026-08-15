import sqlite3

from tools.analyze_wallet_shadow_recent import aggregate_report, market_report, recent_market_ids


def build_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE wallet_shadow_target_events(
            leg_id TEXT PRIMARY KEY, wallet TEXT, market_id INTEGER, role TEXT,
            side TEXT, quote_type TEXT, order_hash TEXT, event_ms INTEGER,
            price REAL, shares REAL, raw_json TEXT
        );
        CREATE TABLE wallet_shadow_events(
            id TEXT PRIMARY KEY, wallet TEXT, market_id INTEGER, at_ms INTEGER,
            event_type TEXT, role TEXT, side TEXT, price REAL, shares REAL,
            core_side TEXT, core_source TEXT, reason TEXT, payload_json TEXT
        );
        CREATE TABLE wallet_shadow_market_results(
            wallet TEXT, market_id INTEGER, title TEXT, winner TEXT,
            resolved_at_ms INTEGER, traded INTEGER, status TEXT,
            fill_count INTEGER, cost_usdt REAL, payout_usdt REAL,
            gross_pnl_usdt REAL, gross_roi REAL, maker_cost_usdt REAL,
            maker_pnl_usdt REAL, taker_cost_usdt REAL, taker_pnl_usdt REAL
        );
        """
    )
    return conn


def test_recent_analysis_compares_taker_cadence_and_residual():
    conn = build_db()
    conn.execute(
        "INSERT INTO wallet_shadow_target_events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("m1", "w", 100, "MAKER", "UP", "BID", "mh", 1000, 0.40, 18, "{}"),
    )
    conn.execute(
        "INSERT INTO wallet_shadow_target_events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("t1", "w", 100, "TAKER", "DOWN", "BID", "th1", 2000, 0.61, 18, "{}"),
    )
    conn.execute(
        "INSERT INTO wallet_shadow_target_events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("t2", "w", 100, "TAKER", "DOWN", "BID", "th2", 3000, 0.63, 18, "{}"),
    )
    conn.execute(
        "INSERT INTO wallet_shadow_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("s1", "w", 100, 2500, "TAKER_INTENT", "TAKER", "DOWN", 0.62, 36, "DOWN", "test", "x", "{}"),
    )
    conn.commit()

    assert recent_market_ids(conn, 10) == [100]
    report = market_report(conn, 100)
    comp = report["takerComparison"]
    assert comp["targetParents"] == 2
    assert comp["shadowIntents"] == 1
    assert comp["eventCountRatioShadowToTarget"] == 0.5
    assert comp["residualSideMatch"] is True
    summary = aggregate_report([report])
    assert summary["targetTakerParents"] == 2
    assert summary["shadowTakerIntents"] == 1
