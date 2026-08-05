from __future__ import annotations

import sqlite3
import threading
from decimal import Decimal
from types import SimpleNamespace

from predict_bot import microprice_confirm_exit_098_live_patch as exit_patch
from predict_bot.microprice_confirm_optimization_shadows import (
    EXIT_098_STRATEGY,
)


def _candidate_engine(status: str, *, exit_status: str | None = None):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE live_orders (
            id INTEGER PRIMARY KEY,
            strategy TEXT,
            market_id INTEGER,
            side TEXT,
            status TEXT,
            filled_share_qty REAL
        );
        CREATE TABLE live_strategy_settlements (
            order_local_id INTEGER PRIMARY KEY
        );
        CREATE TABLE live_manual_exits (
            order_local_id INTEGER PRIMARY KEY,
            status TEXT
        );
        """
    )
    db.execute(
        """INSERT INTO live_orders(
               id, strategy, market_id, side, status, filled_share_qty
           ) VALUES (1, ?, 101, 'UP', ?, 2.5)""",
        (EXIT_098_STRATEGY, status),
    )
    if exit_status is not None:
        db.execute(
            "INSERT INTO live_manual_exits(order_local_id, status) VALUES (1, ?)",
            (exit_status,),
        )
    db.commit()
    return SimpleNamespace(
        ledger=SimpleNamespace(db=db, lock=threading.RLock())
    )


def test_exit_candidates_include_only_stable_position_quantities() -> None:
    for status in ("FILLED", "CANCELED", "EXPIRED", "REJECTED", "FAILED"):
        engine = _candidate_engine(status)
        candidates = exit_patch._live_exit_candidates(engine, 101)
        assert [row["id"] for row in candidates] == [1]

    for status in ("PARTIAL", "PARTIALLY_FILLED", "OPEN", "PENDING"):
        engine = _candidate_engine(status)
        assert exit_patch._live_exit_candidates(engine, 101) == []


def test_exit_candidates_do_not_duplicate_active_protection_order() -> None:
    engine = _candidate_engine("FILLED", exit_status="SUBMITTED")
    assert exit_patch._live_exit_candidates(engine, 101) == []


class FakeLedger:
    def __init__(self) -> None:
        self.order = {
            "id": 1,
            "strategy": EXIT_098_STRATEGY,
            "market_id": 101,
            "side": "UP",
            "status": "FILLED",
            "filled_share_qty": "2.5",
            "token_id": "up-token",
        }
        self.begin_calls: list[dict] = []
        self.updates: list[tuple[int, dict]] = []
        self.events: list[tuple] = []

    def order_for_manual_exit(self, local_id: int):
        return dict(self.order) if local_id == 1 else None

    def begin_manual_exit(self, **kwargs):
        self.begin_calls.append(kwargs)
        return {"id": 7}

    def update_manual_exit(self, exit_id: int, **values):
        self.updates.append((exit_id, values))

    def record_event(self, *args):
        self.events.append(args)


class FakeClient:
    def __init__(self) -> None:
        self.quote_calls: list[dict] = []
        self.place_calls: list[dict] = []

    def server_timestamp_ms(self) -> int:
        return 1_000_000

    def position_by_token(self, _wallet_address: str, _token_id: str):
        return {"position": {"shares": "2.5"}}

    def orderbook(self, _market_id: int, _token_id: str):
        # The target has not traded yet.  V2 must still submit a resting GTC
        # protection order instead of waiting and racing a later 0.98 touch.
        return {"bids": [{"price": "0.60", "size": "100"}]}

    def get_quote(self, **kwargs):
        self.quote_calls.append(kwargs)
        return {
            "quoteId": "exit-quote",
            "tokenId": kwargs["token_id"],
            "side": "SELL",
            "orderType": "LIMIT",
            "amountIn": kwargs["amount_in_wei"],
            "amountOut": "2450000000000000000",
            "averagePrice": "0.98",
            "expireAt": 2_000_000,
        }

    def place_limit_order(self, **kwargs):
        self.place_calls.append(kwargs)
        return {"orderId": "exit-order-1"}


class FakeLiveEngine:
    def __init__(self) -> None:
        self.ledger = FakeLedger()
        self.client = FakeClient()
        self.wallet_address = "0xabc"
        self.wallet_id = "wallet-1"
        self.account_type = "CeDeFi"
        self.lock = threading.RLock()
        self.manual_exit_lock = threading.Lock()
        self.next_order_sync = 99.0

    def current_market(self):
        return {"market_id": 101, "end_ms": 2_000_000, "fee_bps": 200}

    @staticmethod
    def _position_record(payload):
        return payload.get("position")

    @staticmethod
    def _position_shares(record):
        return Decimal(str(record["shares"])) if record else None

    @staticmethod
    def _best_bid(book):
        bids = book.get("bids") or []
        return Decimal(str(bids[0]["price"])) if bids else None

    @staticmethod
    def _quote_is_safe(*_args, **_kwargs):
        return True, ""


def test_filled_entry_places_resting_gtc_exit_before_bid_reaches_target() -> None:
    engine = FakeLiveEngine()

    exit_patch._submit_live_target_exit(engine, 1)

    assert exit_patch.EXIT_098_LIVE_PATCH_VERSION.endswith("_V2")
    assert engine.ledger.begin_calls[0]["order_type"] == "LIMIT"
    assert engine.ledger.begin_calls[0]["price_limit"] == Decimal("0.98")
    assert engine.ledger.begin_calls[0]["sell_shares"] == Decimal("2.5")
    assert engine.client.quote_calls[0]["side"] == "SELL"
    assert engine.client.quote_calls[0]["price_limit"] == "0.98"
    assert engine.client.place_calls[0]["price_limit"] == "0.98"
    assert engine.client.place_calls[0]["account_type"] == "CeDeFi"
    assert engine.ledger.updates[-1][1]["status"] == "SUBMITTED"
    assert engine.ledger.updates[-1][1]["order_id"] == "exit-order-1"
    assert engine.next_order_sync == 0.0
