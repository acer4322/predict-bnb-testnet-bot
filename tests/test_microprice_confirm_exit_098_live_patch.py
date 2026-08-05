from __future__ import annotations

import sqlite3
import threading
from decimal import Decimal
from types import SimpleNamespace

import pytest

from predict_bot import microprice_confirm_exit_098_live_patch as exit_patch
from predict_bot import microprice_confirm_exit_098_live_v3_patch as exit_v3
from predict_bot.microprice_confirm_optimization_shadows import (
    EXIT_098_STRATEGY,
)


def _candidate_engine(status: str, *, exit_status: str | None = None):
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE live_orders (
            id INTEGER PRIMARY KEY,
            strategy TEXT,
            market_id INTEGER,
            side TEXT,
            status TEXT,
            token_id TEXT,
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
               id, strategy, market_id, side, status,
               token_id, filled_share_qty
           ) VALUES (1, ?, 101, 'UP', ?, 'up-token', 2.5)""",
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
        assert candidates[0]["token_id"] == "up-token"

    for status in ("PARTIAL", "PARTIALLY_FILLED", "OPEN", "PENDING"):
        engine = _candidate_engine(status)
        assert exit_patch._live_exit_candidates(engine, 101) == []


def test_exit_candidates_do_not_duplicate_active_exit() -> None:
    engine = _candidate_engine("FILLED", exit_status="SUBMITTED")
    assert exit_patch._live_exit_candidates(engine, 101) == []


def test_best_bid_parser_accepts_object_array_and_nested_books() -> None:
    assert exit_v3._best_bid_any(
        {"bids": [{"price": "0.98", "size": "2"}]}
    ) == Decimal("0.98")
    assert exit_v3._best_bid_any(
        {"bids": [["0.99", "3"], ["0.98", "4"]]}
    ) == Decimal("0.99")
    best, capacity = exit_v3._best_bid_and_capacity(
        {"data": {"bids": [["0.99", "3"], ["0.98", "4"], ["0.97", "9"]]}},
        minimum_price=Decimal("0.98"),
    )
    assert best == Decimal("0.99")
    assert capacity == Decimal("7")


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
    def __init__(self, bid: str = "0.98", quote_average: str = "0.98") -> None:
        self.bid = bid
        self.quote_average = quote_average
        self.quote_calls: list[dict] = []
        self.place_calls: list[dict] = []

    def server_timestamp_ms(self) -> int:
        return 1_000_000

    def position_by_token(self, _wallet_address: str, _token_id: str):
        return {"position": {"shares": "2.5"}}

    def orderbook(self, _market_id: int, _token_id: str):
        # Binance levels may be arrays rather than objects.
        return {"bids": [[self.bid, "100"]]}

    def get_quote(self, **kwargs):
        self.quote_calls.append(kwargs)
        return {
            "quoteId": "exit-quote",
            "tokenId": kwargs["token_id"],
            "side": "SELL",
            "orderType": "LIMIT",
            "amountIn": kwargs["amount_in_wei"],
            "amountOut": "2450000000000000000",
            "averagePrice": self.quote_average,
            "expireAt": 2_000_000,
        }

    def place_limit_order(self, **kwargs):
        self.place_calls.append(kwargs)
        return {"orderId": "exit-order-1"}


class FakeLiveEngine:
    def __init__(self, *, bid: str = "0.98", quote_average: str = "0.98") -> None:
        self.ledger = FakeLedger()
        self.client = FakeClient(bid=bid, quote_average=quote_average)
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
    def _quote_is_safe(quote, *, price_limit, **_kwargs):
        average = Decimal(str(quote["averagePrice"]))
        if average < price_limit:
            return False, "quote average is below target"
        return True, ""


def test_target_submit_requires_real_bid_at_or_above_098() -> None:
    engine = FakeLiveEngine(bid="0.97")
    with pytest.raises(ValueError, match="below the 0.98 target"):
        exit_patch._submit_live_target_exit(engine, 1)
    assert engine.ledger.begin_calls == []
    assert engine.client.quote_calls == []


def test_target_submit_rejects_quote_average_below_098() -> None:
    engine = FakeLiveEngine(bid="0.98", quote_average="0.979")
    with pytest.raises(ValueError, match="below target"):
        exit_patch._submit_live_target_exit(engine, 1)
    assert engine.client.place_calls == []
    assert engine.ledger.updates[-1][1]["status"] == "REJECTED"


def test_target_submit_places_limit_098_from_array_orderbook() -> None:
    engine = FakeLiveEngine(bid="0.99", quote_average="0.985")

    exit_patch._submit_live_target_exit(engine, 1)

    assert exit_patch.EXIT_098_LIVE_PATCH_VERSION.endswith("_V3")
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


def test_dedicated_monitor_schedules_only_after_target(monkeypatch) -> None:
    db_engine = _candidate_engine("FILLED")
    scheduled: list[tuple] = []

    live_engine = SimpleNamespace(
        ledger=db_engine.ledger,
        client=FakeClient(bid="0.97"),
        wallet_address="0xabc",
        lock=threading.RLock(),
        current_market=lambda: {"market_id": 101, "end_ms": 2_000_000},
    )
    monkeypatch.setattr(
        exit_patch._EXIT_WORKER,
        "submit",
        lambda *args: scheduled.append(args),
    )
    exit_patch._EXIT_IN_FLIGHT.clear()
    exit_patch._EXIT_RETRY_AFTER.clear()

    assert exit_v3._monitor_once(live_engine) is True
    assert scheduled == []

    live_engine.client.bid = "0.98"
    assert exit_v3._monitor_once(live_engine) is True
    assert len(scheduled) == 1
    assert scheduled[0][2] == 1
