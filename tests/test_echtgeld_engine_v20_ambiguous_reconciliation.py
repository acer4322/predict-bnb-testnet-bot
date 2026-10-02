from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from predict_bot import echtgeld_engine_v2 as v2
from predict_bot import echtgeld_engine_v20 as v20


class _FakeHistoryClient:
    def __init__(self, orders: list[dict]):
        self.orders = orders
        self.calls = 0

    def order_history(self, wallet_address: str, *, limit: int = 100):
        self.calls += 1
        assert wallet_address == "0xwallet"
        assert limit == 100
        return {"orders": list(self.orders)}


def _engine(tmp_path: Path, remote_orders: list[dict]):
    engine = object.__new__(v20.EchtgeldEngine)
    engine.db = sqlite3.connect(tmp_path / "engine.db", check_same_thread=False)
    engine.db.row_factory = sqlite3.Row
    engine.db_lock = threading.RLock()
    engine.last_result = None
    engine.generic_ambiguous_checks = 0
    engine.generic_ambiguous_history_reads = 0
    engine.generic_ambiguous_filled = 0
    engine.generic_ambiguous_no_fill = 0
    engine.generic_ambiguous_pending = 0
    engine.generic_ambiguous_last_at_ms = None
    engine.generic_ambiguous_last_error = None
    engine.generic_ambiguous_last_result = None
    engine._record_event = lambda *args, **kwargs: None
    engine._enforce_stop_loss = lambda **kwargs: {"tripped": False}
    client = _FakeHistoryClient(remote_orders)
    engine._poly_ensure_client = lambda: (client, {"walletAddress": "0xwallet", "walletId": "wallet-id"})
    engine.db.executescript(
        """
        CREATE TABLE engine_intents (
            intent_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            error_message TEXT
        );
        CREATE TABLE engine_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dedupe_key TEXT NOT NULL,
            intent_id TEXT NOT NULL,
            strategy TEXT NOT NULL,
            cohort TEXT NOT NULL,
            market_id INTEGER NOT NULL,
            venue TEXT NOT NULL,
            side TEXT NOT NULL,
            signal_ask REAL NOT NULL,
            target_notional_usdt REAL NOT NULL,
            status TEXT NOT NULL,
            attempted_at_ms INTEGER NOT NULL,
            completed_at_ms INTEGER,
            execution_price REAL,
            shares REAL,
            submitted_usdt REAL,
            vendor_order_id TEXT,
            vendor_order_hash TEXT,
            error_message TEXT,
            result_json TEXT NOT NULL,
            context_json TEXT NOT NULL
        );
        """
    )
    engine.db.execute(
        "INSERT INTO engine_intents(intent_id,status,error_message) VALUES('intent-1','AMBIGUOUS','old')"
    )
    engine.db.execute(
        """INSERT INTO engine_orders(
               dedupe_key,intent_id,strategy,cohort,market_id,venue,side,signal_ask,
               target_notional_usdt,status,attempted_at_ms,completed_at_ms,execution_price,
               shares,submitted_usdt,vendor_order_id,vendor_order_hash,error_message,result_json,context_json
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "dedupe-1",
            "intent-1",
            "TARGET_TAKER_PUBLIC_SIDE_V1_EBM_FORWARD",
            "TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY",
            123,
            "binance",
            "UP",
            0.62,
            1.0,
            "AMBIGUOUS",
            1000,
            2000,
            0.63,
            None,
            None,
            "order-1",
            None,
            "bounded reconciliation timed out",
            json.dumps({"status": "AMBIGUOUS", "vendorOrderId": "order-1"}),
            "{}",
        ),
    )
    engine.db.commit()
    return engine, client


def test_late_filled_order_promotes_and_becomes_countable_pnl(tmp_path: Path) -> None:
    engine, client = _engine(
        tmp_path,
        [
            {
                "orderId": "order-1",
                "vendorOrderId": "hash-1",
                "status": "FILLED",
                "filledUsdtAmount": "0.98",
                "filledShareQty": "1.57",
                "fillPercentage": "1",
                "price": "0.62",
            }
        ],
    )

    changed = engine._reconcile_ambiguous_order_history_once()

    assert changed == 1
    assert client.calls == 1
    row = dict(engine.db.execute("SELECT * FROM engine_orders WHERE id=1").fetchone())
    intent = dict(engine.db.execute("SELECT * FROM engine_intents WHERE intent_id='intent-1'").fetchone())
    assert row["status"] == "SUBMITTED"
    assert row["shares"] == 1.57
    assert row["submitted_usdt"] == 0.98
    assert row["execution_price"] == 0.62
    assert row["vendor_order_hash"] == "hash-1"
    assert row["error_message"] is None
    assert "error" not in json.loads(row["result_json"])
    assert intent["status"] == "SUBMITTED"
    computed = v2.EchtgeldEngine._settled_pnl(
        row,
        {"winner": "UP", "resolved_at_ms": 3000},
    )
    assert computed["resultStatus"] == "WIN"
    assert abs(float(computed["netPnlUsdt"]) - 0.59) < 1e-12
    engine.db.close()


def test_terminal_no_fill_promotes_to_rejected_without_fake_exposure(tmp_path: Path) -> None:
    engine, _ = _engine(
        tmp_path,
        [{"orderId": "order-1", "vendorOrderId": "hash-2", "status": "EXPIRED"}],
    )

    changed = engine._reconcile_ambiguous_order_history_once()

    assert changed == 1
    row = dict(engine.db.execute("SELECT * FROM engine_orders WHERE id=1").fetchone())
    intent = dict(engine.db.execute("SELECT * FROM engine_intents WHERE intent_id='intent-1'").fetchone())
    assert row["status"] == "REJECTED"
    assert row["shares"] is None
    assert row["submitted_usdt"] is None
    assert intent["status"] == "REJECTED"
    engine.db.close()


def test_pending_order_stays_ambiguous_and_is_never_retried(tmp_path: Path) -> None:
    engine, client = _engine(
        tmp_path,
        [{"orderId": "order-1", "vendorOrderId": "hash-3", "status": "SUBMITTED"}],
    )

    changed = engine._reconcile_ambiguous_order_history_once()

    assert changed == 0
    assert client.calls == 1
    row = dict(engine.db.execute("SELECT * FROM engine_orders WHERE id=1").fetchone())
    assert row["status"] == "AMBIGUOUS"
    result = json.loads(row["result_json"])
    assert result["exchangeStatus"] == "SUBMITTED"
    assert result["delayedOrderHistoryReconciliation"] is True
    # The fake client intentionally exposes no quote/place methods; reconciliation
    # succeeding proves the repair path is read-only.
    engine.db.close()


class _PagedHistoryClient(_FakeHistoryClient):
    def __init__(self):
        super().__init__([])
        self.offsets: list[int] = []

    def signed_get(self, path: str, params: dict):
        assert path.endswith("/order/history")
        offset = int(params["offset"])
        self.offsets.append(offset)
        if offset == 100:
            return {
                "orders": [
                    {
                        "orderId": "order-1",
                        "vendorOrderId": "old-hash",
                        "status": "FAILED",
                        "filledUsdtAmount": "0",
                        "filledShareQty": "0",
                        "fillPercentage": "0",
                    }
                ]
            }
        return {"orders": []}


def test_old_ambiguous_can_use_bounded_deep_history_page(tmp_path: Path) -> None:
    engine, _ = _engine(tmp_path, [])
    client = _PagedHistoryClient()
    engine._poly_ensure_client = lambda: (client, {"walletAddress": "0xwallet", "walletId": "wallet-id"})

    changed = engine._reconcile_ambiguous_order_history_once()

    assert changed == 1
    assert client.offsets == [100]
    row = dict(engine.db.execute("SELECT * FROM engine_orders WHERE id=1").fetchone())
    assert row["status"] == "REJECTED"
    assert row["submitted_usdt"] is None
    assert row["shares"] is None
    engine.db.close()
