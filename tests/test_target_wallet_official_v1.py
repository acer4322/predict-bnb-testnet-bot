from __future__ import annotations

import sqlite3
from pathlib import Path

from predict_bot import target_wallet_official_v1 as official


def _raw_match(*, wallet: str, market_id: int = 123, role: str = "MAKER") -> dict:
    participant = {
        "signer": wallet,
        "hash": "0xorder",
        "amount": str(18 * official.WEI),
        "price": str(int(0.42 * official.WEI)),
        "outcome": "UP",
        "quoteType": "BID",
    }
    return {
        "market": {"id": market_id, "title": "BTC 5M test"},
        "executedAt": "2026-08-16T00:00:01Z",
        "transactionHash": "0xtx",
        "settlementId": "settlement-1",
        "taker": participant if role == "TAKER" else {},
        "makers": [participant] if role == "MAKER" else [],
    }


def test_normalize_match_leg_preserves_predict_1e18_scaling() -> None:
    raw = _raw_match(wallet=official.TARGET_WALLET)
    leg = official.normalize_match_leg(
        raw,
        wallet=official.TARGET_WALLET,
        role="MAKER",
        maker_index=0,
    )
    assert leg is not None
    assert leg["marketId"] == 123
    assert leg["role"] == "MAKER"
    assert leg["side"] == "UP"
    assert leg["quoteType"] == "BID"
    assert abs(leg["shares"] - 18.0) < 1e-9
    assert abs(leg["price"] - 0.42) < 1e-9


def test_official_db_keeps_8778_compatibility_tables(tmp_path: Path) -> None:
    path = tmp_path / "official.db"
    collector = official.TargetWalletOfficialCollector(path)
    try:
        con = sqlite3.connect(path)
        try:
            event_columns = {row[1] for row in con.execute("PRAGMA table_info(wallet_shadow_target_events)")}
            context_columns = {row[1] for row in con.execute("PRAGMA table_info(wallet_shadow_target_event_context)")}
        finally:
            con.close()
        assert {
            "leg_id", "wallet", "market_id", "order_hash", "event_ms",
            "role", "quote_type", "side", "price", "shares",
        }.issubset(event_columns)
        assert {"leg_id", "observed_at_ms"}.issubset(context_columns)
    finally:
        collector.stop()


def test_persisted_fill_updates_parent_and_compatibility_context(tmp_path: Path) -> None:
    path = tmp_path / "official.db"
    collector = official.TargetWalletOfficialCollector(path)
    raw = _raw_match(wallet=collector.wallet)
    leg = official.normalize_match_leg(raw, wallet=collector.wallet, role="MAKER", maker_index=0)
    assert leg is not None
    try:
        assert collector._persist_leg("BTC", leg, raw, observed_at_ms=1_765_843_202_000)
        assert not collector._persist_leg("BTC", leg, raw, observed_at_ms=1_765_843_203_000)
        with collector.db_lock:
            event = collector.db.execute(
                "SELECT rowid,asset,role,side,quote_type,price,shares FROM wallet_shadow_target_events"
            ).fetchone()
            context = collector.db.execute(
                "SELECT observed_at_ms FROM wallet_shadow_target_event_context WHERE leg_id=?",
                (leg["legId"],),
            ).fetchone()
            parent = collector.db.execute(
                "SELECT asset,role,side,average_price,shares,fill_legs FROM target_parent_orders"
            ).fetchone()
        assert event is not None and event[0] > 0
        assert tuple(event[1:5]) == ("BTC", "MAKER", "UP", "BID")
        assert abs(event[5] - 0.42) < 1e-9
        assert abs(event[6] - 18.0) < 1e-9
        assert context is not None and context[0] == 1_765_843_202_000
        assert parent is not None
        assert tuple(parent[:3]) == ("BTC", "MAKER", "UP")
        assert abs(parent[3] - 0.42) < 1e-9
        assert abs(parent[4] - 18.0) < 1e-9
        assert parent[5] == 1
    finally:
        collector.stop()


def test_filled_cashflow_accounting_handles_bid_and_ask() -> None:
    result = official.TargetWalletOfficialCollector._account_rows(
        [
            {"role": "MAKER", "side": "UP", "quote_type": "BID", "price": 0.40, "shares": 10.0},
            {"role": "MAKER", "side": "UP", "quote_type": "ASK", "price": 0.70, "shares": 4.0},
            {"role": "TAKER", "side": "DOWN", "quote_type": "BID", "price": 0.20, "shares": 3.0},
        ],
        "UP",
    )
    assert abs(float(result["buyNotionalUsdt"]) - 4.6) < 1e-9
    assert abs(float(result["sellProceedsUsdt"]) - 2.8) < 1e-9
    assert abs(float(result["payoutUsdt"]) - 6.0) < 1e-9
    assert abs(float(result["netPnlUsdt"]) - 4.2) < 1e-9


def test_8777_is_zero_work_retired_stub() -> None:
    from predict_bot import predict_wallet_taker_signal_collector as retired

    payload = retired.state()
    assert payload["retired"] is True
    assert payload["dataCollectionEnabled"] is False
    assert payload["databaseWritesEnabled"] is False
    assert payload["websocketEnabled"] is False
    assert payload["strategyLogic"] is False
    assert payload["liveOrdersAffected"] is False
