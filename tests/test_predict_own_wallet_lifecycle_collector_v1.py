from __future__ import annotations

import sqlite3
import tempfile
import unittest
import zlib
from pathlib import Path

from predict_bot.predict_own_wallet_lifecycle_collector_v1 import (
    LifecycleStore,
    OwnWalletLifecycleCollector,
    ReadOnlyPredictAuthClient,
    event_key,
)


class OwnWalletLifecycleCollectorV1Tests(unittest.TestCase):
    def test_read_only_client_has_a_hard_endpoint_allowlist(self) -> None:
        client = object.__new__(ReadOnlyPredictAuthClient)
        with self.assertRaises(PermissionError):
            client.request("POST", "/v1/orders")
        with self.assertRaises(PermissionError):
            client.request("DELETE", "/v1/orders/123")

    def test_event_key_is_canonical(self) -> None:
        self.assertEqual(event_key({"b": 2, "a": 1}), event_key({"a": 1, "b": 2}))

    def test_event_is_normalized_and_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lifecycle.db"
            store = LifecycleStore(path)
            try:
                store.start_session("s1", "0xabc")
                payload = {
                    "type": "orderTransactionSuccess",
                    "orderId": "123",
                    "orderHash": "0xhash",
                    "walletAddress": "0xabc",
                    "timestamp": 1000,
                    "settlementId": "settlement-1",
                    "isMaker": True,
                    "details": {
                        "marketId": 42,
                        "outcomeIndex": 0,
                        "outcome": "YES",
                        "quoteType": "BID",
                        "quantity": "18",
                        "quantityFilled": "5",
                        "price": "0.40",
                        "value": "7.2",
                        "valueFilled": "2.0",
                        "strategyType": "LIMIT",
                    },
                    "fill": {
                        "executedPriceWei": "400000000000000000",
                        "executedSizeWei": "5000000000000000000",
                        "executedValueWei": "2000000000000000000",
                    },
                    "fee": {"amountWei": "10", "type": "SHARES"},
                }
                self.assertTrue(store.store_event("s1", payload, 1200))
                self.assertFalse(store.store_event("s1", payload, 1300))
                row = store.db.execute("SELECT * FROM own_wallet_lifecycle_events_v1").fetchone()
                self.assertEqual(row["event_type"], "orderTransactionSuccess")
                self.assertEqual(row["market_id"], 42)
                self.assertEqual(row["source_receive_latency_ms"], 200)
                self.assertEqual(row["quantity_filled"], 5.0)
                self.assertEqual(row["is_maker"], 1)
                self.assertIn(b"orderTransactionSuccess", zlib.decompress(row["raw_json_z"]))
            finally:
                store.close()

    def test_empty_open_snapshot_is_durable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lifecycle.db"
            store = LifecycleStore(path)
            try:
                store.start_session("s1", "0xabc")
                store.store_open_snapshot("s1", [], rest_status="OK")
                row = store.db.execute("SELECT * FROM own_wallet_open_snapshot_runs_v1").fetchone()
                self.assertEqual(row["open_order_count"], 0)
                self.assertEqual(row["rest_status"], "OK")
            finally:
                store.close()

    def test_successful_subscription_clears_recovered_auth_error(self) -> None:
        class Store:
            subscribed: list[str] = []

            def mark_subscribed(self, session_id: str) -> None:
                self.subscribed.append(session_id)

        collector = object.__new__(OwnWalletLifecycleCollector)
        collector.last_message_at_ms = None
        collector.subscribed_at_ms = None
        collector.status = "REAUTHENTICATING"
        collector.last_error = "wallet subscription JWT rejected"
        collector.store = Store()
        accepted = collector._handle_message(
            object(),
            {"type": "R", "requestId": 1, "success": True},
            "session-new-jwt",
        )
        self.assertTrue(accepted)
        self.assertEqual(collector.status, "LIVE")
        self.assertIsNone(collector.last_error)
        self.assertEqual(collector.store.subscribed, ["session-new-jwt"])


if __name__ == "__main__":
    unittest.main()
