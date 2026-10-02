from __future__ import annotations

import json
import math
import os
import threading
import time
from http.server import ThreadingHTTPServer
from typing import Any

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v6 as v6
from . import echtgeld_engine_v22 as v22
from .binance_exact_market import find_exact_market_summary, live_cache_from_summary
from .core import ApiTransportError, binance_top_of_book
from .target_taker_live_execution_v3 import WEI, _finite, _first_number, _first_text

VERSION = "ECHTGELD_ENGINE_V2_4310_CAP100_EXECUTION_ADAPTER_V1"
HOST = v22.HOST
PORT = v22.PORT
CAP100_SOURCE = "CAP100_8787"
R2_R21_SOURCE = "R2_R21_8789"
CAP100_ALLOWED_SOURCES = {CAP100_SOURCE, R2_R21_SOURCE}
CAP100_STRATEGY = "PROMOTED_OWNSTATE_V4_R2_CAP100_KEEP18"
R2_R21_STRATEGY = "R2_R21_V351_AUTONOMOUS_CHILD_REASSESS_HEARTBEAT_FIX_10SHARE_NO_NOTIONAL_CAP"
CAP100_STRATEGY_BY_SOURCE = {
    CAP100_SOURCE: CAP100_STRATEGY,
    R2_R21_SOURCE: R2_R21_STRATEGY,
}
CAP100_COHORT = "CAP100_REAL_EXECUTION"
CAP100_RECONCILE_SECONDS = 0.50
CAP100_HEARTBEAT_TIMEOUT_MS = 8_000
CAP100_UNKNOWN_MATCH_WINDOW_MS = 8_000
CAP100_UNKNOWN_HARD_AGE_MS = 30_000
CAP100_SLIPPAGE_BPS = 1
CAP100_MIN_NOTIONAL_USDT = 1.0
R2_R21_TAKER_CONFIRM_TIMEOUT_MS = 2_200
CAP100_ACCOUNT_TYPE = str(os.environ.get("PREDICT_TARGET_TAKER_BINANCE_ACCOUNT_TYPE") or "SPOT").strip().upper()

TERMINAL_STATES = {"FILLED", "CANCELED", "REJECTED", "EXPIRED", "FAILED"}
ACTIVE_STATES = {
    "PLANNED", "QUOTING", "QUOTE_READY", "PLACING", "RESTING", "PARTIAL_FILL",
    "CANCEL_PENDING", "CANCEL_UNKNOWN", "UNKNOWN_SUBMISSION",
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _order_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("data", "rows", "orders", "list"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
            if isinstance(value, dict):
                for child in ("rows", "orders", "list"):
                    rows = value.get(child)
                    if isinstance(rows, list):
                        return [x for x in rows if isinstance(x, dict)]
    return []


def _txt(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _num(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _finite(row.get(key))
        if value is not None:
            return float(value)
    return None


class EchtgeldEngine(v22.EchtgeldEngine):
    """V22 plus a CAP100-specific real execution state machine.

    CAP100 orders do not use the old one-shot generic Echtgeld queue.  Maker and
    Taker writes have their own durable ledger, cumulative fill reconciliation and
    incremental fill event feed.  CAP100 inventory must be rebuilt from these
    venue-confirmed fill deltas, never from the paper queue-clear proxy.

    Any uncertain placement is fail-closed: it freezes further CAP100 entry writes
    until order history can prove whether the venue received the order.  Operator
    pause, stop-loss trip and 8787 heartbeat loss also cancel all known resting
    Maker orders; cancel acceptance is never treated as proof of cancellation.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.cap100_lock = threading.RLock()
        self.cap100_last_heartbeat_ms: int | None = None
        self.cap100_reconcile_last_ms: int | None = None
        self.cap100_last_error: str | None = None
        self.cap100_reconcile_thread: threading.Thread | None = None
        super().__init__(*args, **kwargs)
        # A restarted engine is PAUSED and V22 clears the selected source. Any
        # durable CAP100 Maker that was still non-terminal before the restart is
        # nevertheless a live venue risk, so cancel it internally without waiting
        # for an operator to re-select a source. Terminal proof still comes only
        # from reconciliation.
        try:
            self._cap100_cancel_all("ENGINE_RESTART")
        except Exception as exc:
            self.cap100_last_error = f"startup cancel-all: {type(exc).__name__}: {str(exc)[:350]}"
        if kwargs.get("start_worker", True):
            self.cap100_reconcile_thread = threading.Thread(
                target=self._cap100_reconcile_loop,
                name="echtgeld-cap100-reconcile",
                daemon=True,
            )
            self.cap100_reconcile_thread.start()

    def _setup_schema(self) -> None:
        super()._setup_schema()
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS engine_cap100_orders (
                    client_order_id TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_market_id INTEGER NOT NULL,
                    venue_market_id INTEGER,
                    bucket_start_sec INTEGER NOT NULL,
                    window_end_ms INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    token_id TEXT,
                    fee_rate_bps INTEGER,
                    requested_price REAL,
                    requested_shares REAL NOT NULL,
                    requested_cost_usdt REAL,
                    quote_id TEXT,
                    order_id TEXT,
                    vendor_order_id TEXT,
                    state TEXT NOT NULL,
                    exchange_status TEXT,
                    fill_percentage REAL,
                    filled_share_qty REAL NOT NULL DEFAULT 0,
                    filled_usdt_amount REAL NOT NULL DEFAULT 0,
                    avg_fill_price REAL,
                    created_at_ms INTEGER NOT NULL,
                    place_started_at_ms INTEGER,
                    place_completed_at_ms INTEGER,
                    last_reconciled_at_ms INTEGER,
                    cancel_requested_at_ms INTEGER,
                    completed_at_ms INTEGER,
                    error_kind TEXT,
                    error_message TEXT,
                    raw_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_cap100_orders_state
                    ON engine_cap100_orders(state,created_at_ms);
                CREATE INDEX IF NOT EXISTS idx_cap100_orders_market
                    ON engine_cap100_orders(source_market_id,created_at_ms);
                CREATE TABLE IF NOT EXISTS engine_cap100_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at_ms INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    client_order_id TEXT,
                    source_market_id INTEGER,
                    role TEXT,
                    side TEXT,
                    state TEXT,
                    delta_shares REAL,
                    delta_usdt REAL,
                    fill_price REAL,
                    detail TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_cap100_events_time
                    ON engine_cap100_events(occurred_at_ms DESC);
                """
            )
            self.db.commit()

    def _cap100_event(
        self,
        event_type: str,
        *,
        row: dict[str, Any] | None = None,
        delta_shares: float | None = None,
        delta_usdt: float | None = None,
        fill_price: float | None = None,
        detail: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        r = row or {}
        with self.db_lock:
            self.db.execute(
                """INSERT INTO engine_cap100_events(
                       occurred_at_ms,event_type,client_order_id,source_market_id,role,side,state,
                       delta_shares,delta_usdt,fill_price,detail,payload_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _now_ms(), str(event_type)[:80], r.get("client_order_id"),
                    int(r.get("source_market_id") or 0) or None, r.get("role"), r.get("side"),
                    r.get("state"), delta_shares, delta_usdt, fill_price, str(detail)[:500],
                    json.dumps(payload or {}, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()

    def cap100_events(self, after_seq: int = 0, limit: int = 300) -> list[dict[str, Any]]:
        with self.db_lock:
            rows = self.db.execute(
                """SELECT e.*,o.source_id AS source_id,o.requested_shares AS requested_shares,
                          o.requested_price AS requested_price,
                          o.filled_share_qty AS filled_share_qty,o.created_at_ms AS created_at_ms,
                          o.cancel_requested_at_ms AS cancel_requested_at_ms,
                          o.last_reconciled_at_ms AS last_reconciled_at_ms,
                          o.error_kind AS error_kind
                     FROM engine_cap100_events e
                     LEFT JOIN engine_cap100_orders o ON o.client_order_id=e.client_order_id
                    WHERE e.seq>? ORDER BY e.seq ASC LIMIT ?""",
                (max(0, int(after_seq)), max(1, min(1000, int(limit)))),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            try:
                row["payload"] = json.loads(str(row.pop("payload_json") or "{}"))
            except Exception:
                row["payload"] = {}
            out.append(row)
        return out

    def _cap100_rows(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM engine_cap100_orders"
        params: tuple[Any, ...] = ()
        if active_only:
            placeholders = ",".join("?" for _ in ACTIVE_STATES)
            sql += f" WHERE state IN ({placeholders})"
            params = tuple(sorted(ACTIVE_STATES))
        sql += " ORDER BY created_at_ms ASC"
        with self.db_lock:
            return [dict(r) for r in self.db.execute(sql, params).fetchall()]

    def _cap100_unknown_write(self) -> dict[str, Any] | None:
        with self.db_lock:
            row = self.db.execute(
                """SELECT * FROM engine_cap100_orders
                    WHERE state IN ('UNKNOWN_SUBMISSION','CANCEL_UNKNOWN')
                    ORDER BY created_at_ms ASC LIMIT 1"""
            ).fetchone()
        return dict(row) if row else None

    def _cap100_gate(self, payload: dict[str, Any], *, require_armed: bool = True) -> None:
        supplied = str(payload.get("entrySource") or "").strip().upper()
        if supplied not in CAP100_ALLOWED_SOURCES:
            raise v1.EchtgeldEngineError(f"CAP100 execution requires entrySource in {sorted(CAP100_ALLOWED_SOURCES)}")
        allowed, message, _selected = self._check_entry_source(payload)
        if not allowed:
            raise v1.EchtgeldEngineError(message)
        if require_armed and not self.armed:
            raise v1.EchtgeldEngineError("Echtgeld Engine is PAUSED")
        uncertain = self._cap100_unknown_write()
        if require_armed and uncertain is not None:
            raise v1.EchtgeldEngineError(
                "CAP100 entry writes are frozen by unresolved venue-write uncertainty: "
                f"{uncertain['client_order_id']} {uncertain['state']}"
            )

    def cap100_heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._cap100_gate(payload, require_armed=False)
        self.cap100_last_heartbeat_ms = _now_ms()
        source_id = str(payload.get("entrySource") or "").strip().upper()
        return {
            "ok": True,
            "sourceId": source_id,
            "armed": bool(self.armed),
            "selectedSourceId": self._selected_entry_source(),
            "asOfMs": self.cap100_last_heartbeat_ms,
        }

    def _cap100_market(self, payload: dict[str, Any]) -> dict[str, Any]:
        bucket_start_sec = int(_finite(payload.get("bucketStartSec")) or 0)
        window_end_ms = int(_finite(payload.get("windowEndMs")) or 0)
        source_market_id = int(_finite(payload.get("marketId")) or 0)
        asset = str(payload.get("asset") or "BTC").strip().upper()
        if asset != "BTC":
            raise v1.EchtgeldEngineError("CAP100 V1 live adapter is BTC-only")
        if source_market_id <= 0 or bucket_start_sec <= 0 or window_end_ms <= bucket_start_sec * 1000:
            raise v1.EchtgeldEngineError("CAP100 marketId/bucketStartSec/windowEndMs are required")
        client, _wallet = self._poly_ensure_client()
        summary = find_exact_market_summary(
            client,
            symbol="BTCUSDT",
            target_start_ms=bucket_start_sec * 1000,
            max_pages=2,
        )
        if not isinstance(summary, dict):
            raise v1.EchtgeldEngineError("Binance exact BTCUSDT prediction market not found for CAP100 window")
        cache = live_cache_from_summary(summary)
        if not isinstance(cache, dict):
            raise v1.EchtgeldEngineError("Binance CAP100 exact market metadata incomplete")
        if abs(int(cache["end_ms"]) - window_end_ms) > 2_000:
            raise v1.EchtgeldEngineError("Binance CAP100 market end does not match source window")
        now = int(client.server_timestamp_ms())
        if not int(cache["start_ms"]) <= now < int(cache["end_ms"]):
            raise v1.EchtgeldEngineError("Binance CAP100 market is outside its live window")
        return {**cache, "source_market_id": source_market_id, "bucket_start_sec": bucket_start_sec}

    @staticmethod
    def _validate_cap100_order(payload: dict[str, Any]) -> tuple[str, str, float, float]:
        cid = str(payload.get("clientOrderId") or "").strip()
        side = str(payload.get("side") or "").strip().upper()
        shares = float(_finite(payload.get("shares")) or 0.0)
        price = float(_finite(payload.get("price")) or 0.0)
        if not cid or len(cid) > 240:
            raise v1.EchtgeldEngineError("CAP100 clientOrderId is required and <=240 chars")
        if side not in {"UP", "DOWN"}:
            raise v1.EchtgeldEngineError("CAP100 side must be UP or DOWN")
        if not 0 < shares <= 200:
            raise v1.EchtgeldEngineError("CAP100 shares must be within (0,200]")
        if not 0 < price < 1:
            raise v1.EchtgeldEngineError("CAP100 price must be within (0,1)")
        return cid, side, shares, price

    def _cap100_insert(self, payload: dict[str, Any], role: str, market: dict[str, Any], cid: str, side: str, shares: float, price: float) -> dict[str, Any]:
        token = str(market["up_token_id"] if side == "UP" else market["down_token_id"])
        source_id = str(payload.get("entrySource") or "").strip().upper()
        strategy = CAP100_STRATEGY_BY_SOURCE.get(source_id)
        if strategy is None:
            raise v1.EchtgeldEngineError(f"Unsupported CAP100 source attribution: {source_id or 'MISSING'}")
        now = _now_ms()
        with self.db_lock:
            existing = self.db.execute("SELECT * FROM engine_cap100_orders WHERE client_order_id=?", (cid,)).fetchone()
            if existing is not None:
                return dict(existing)
            self.db.execute(
                """INSERT INTO engine_cap100_orders(
                       client_order_id,role,strategy,source_id,source_market_id,venue_market_id,
                       bucket_start_sec,window_end_ms,side,token_id,fee_rate_bps,requested_price,
                       requested_shares,requested_cost_usdt,state,created_at_ms,raw_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'PLANNED',?,?)""",
                (
                    cid, role, strategy, source_id, int(market["source_market_id"]),
                    int(market["market_id"]), int(market["bucket_start_sec"]), int(market["end_ms"]),
                    side, token, int(market.get("fee_rate_bps") or 200), price, shares, price * shares,
                    now, json.dumps(payload, separators=(",", ":"), default=str),
                ),
            )
            self.db.commit()
            row = self.db.execute("SELECT * FROM engine_cap100_orders WHERE client_order_id=?", (cid,)).fetchone()
        result = dict(row)
        self._cap100_event("ORDER_PLANNED", row=result, detail=f"{role} {side} {shares:g} @ {price:.4f}")
        return result

    def _cap100_update(self, cid: str, **values: Any) -> dict[str, Any]:
        allowed = {
            "quote_id", "order_id", "vendor_order_id", "state", "exchange_status", "fill_percentage",
            "filled_share_qty", "filled_usdt_amount", "avg_fill_price", "place_started_at_ms",
            "place_completed_at_ms", "last_reconciled_at_ms", "cancel_requested_at_ms", "completed_at_ms",
            "error_kind", "error_message", "raw_json",
        }
        updates = {k: v for k, v in values.items() if k in allowed}
        if updates:
            with self.db_lock:
                assignments = ",".join(f"{k}=?" for k in updates)
                self.db.execute(f"UPDATE engine_cap100_orders SET {assignments} WHERE client_order_id=?", (*updates.values(), cid))
                self.db.commit()
                row = self.db.execute("SELECT * FROM engine_cap100_orders WHERE client_order_id=?", (cid,)).fetchone()
        else:
            with self.db_lock:
                row = self.db.execute("SELECT * FROM engine_cap100_orders WHERE client_order_id=?", (cid,)).fetchone()
        if row is None:
            raise v1.EchtgeldEngineError("CAP100 order not found")
        return dict(row)

    def submit_cap100_maker(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.cap100_lock:
            return self._submit_cap100_maker_locked(payload)

    def _submit_cap100_maker_locked(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._cap100_gate(payload)
        cid, side, shares, price = self._validate_cap100_order(payload)
        market = self._cap100_market(payload)
        row = self._cap100_insert(payload, "MAKER", market, cid, side, shares, price)
        if str(row.get("state")) != "PLANNED":
            return {"ok": True, "idempotent": True, "order": row}
        notional = price * shares
        if notional < CAP100_MIN_NOTIONAL_USDT - 1e-9:
            row = self._cap100_update(
                cid,
                state="REJECTED",
                completed_at_ms=_now_ms(),
                error_kind="LIMIT_MIN_NOTIONAL",
                error_message=(
                    f"CAP100 maker notional {notional:.4f} < {CAP100_MIN_NOTIONAL_USDT:.1f} "
                    "USDT minimum; rejected before quote and never upsized"
                ),
            )
            self._cap100_event("ORDER_REJECTED", row=row, detail=str(row.get("error_message") or ""))
            return {"ok": False, "accepted": False, "preVenueRejected": True, "order": row}
        client, wallet = self._poly_ensure_client()
        token = str(row["token_id"])
        # Hard soft-post-only check directly against the venue immediately before quote/place.
        up_book = client.orderbook(int(market["market_id"]), str(market["up_token_id"]))
        down_book = client.orderbook(int(market["market_id"]), str(market["down_token_id"]))
        top = binance_top_of_book(up_book, down_book, int(client.server_timestamp_ms()))
        latest_ask = top.up_ask if side == "UP" else top.down_ask
        if latest_ask is None or price + 1e-12 >= float(latest_ask):
            row = self._cap100_update(cid, state="REJECTED", completed_at_ms=_now_ms(), error_kind="SOFT_POST_ONLY_BLOCK", error_message=f"price {price:.4f} >= venue ask {latest_ask}")
            self._cap100_event("ORDER_REJECTED", row=row, detail=str(row.get("error_message") or ""))
            return {"ok": False, "accepted": False, "order": row}
        amount_wei = str(int(round(price * shares * WEI)))
        self._cap100_update(cid, state="QUOTING")
        quote = client.get_quote(
            wallet_address=wallet["walletAddress"], token_id=token, amount_in_wei=amount_wei,
            price_limit=f"{price:.6f}", slippage_bps=CAP100_SLIPPAGE_BPS,
            fee_rate_bps=int(row.get("fee_rate_bps") or 200), funding_source="MPC",
            side="BUY", order_type="LIMIT",
        )
        quote_id = str(quote.get("quoteId") or "").strip()
        if not quote_id:
            row = self._cap100_update(cid, state="REJECTED", completed_at_ms=_now_ms(), error_kind="QUOTE_MISSING_ID", error_message="LIMIT quote returned no quoteId", raw_json=json.dumps(quote, default=str))
            self._cap100_event("ORDER_REJECTED", row=row, detail="quote missing id")
            return {"ok": False, "accepted": False, "order": row}
        self._cap100_update(cid, state="QUOTE_READY", quote_id=quote_id)
        # Re-check source/global arm after quote latency, immediately before venue write.
        self._cap100_gate(payload)
        place_started = _now_ms()
        self._cap100_update(cid, state="PLACING", place_started_at_ms=place_started)
        try:
            placed = client.place_limit_order(
                wallet_address=wallet["walletAddress"], wallet_id=wallet["walletId"], quote_id=quote_id,
                price_limit=f"{price:.6f}", slippage_bps=CAP100_SLIPPAGE_BPS,
                account_type=CAP100_ACCOUNT_TYPE, funding_source="MPC",
            )
        except ApiTransportError as exc:
            row = self._cap100_update(cid, state="UNKNOWN_SUBMISSION", place_completed_at_ms=_now_ms(), error_kind="PLACE_TRANSPORT_UNKNOWN", error_message=str(exc)[:500])
            self._cap100_event("UNKNOWN_SUBMISSION", row=row, detail="venue write uncertain; CAP100 entries frozen until reconciliation")
            return {"ok": False, "accepted": True, "uncertain": True, "order": row}
        order_id = _txt(placed, "orderId", "order_id", "id")
        vendor_id = _txt(placed, "vendorOrderId", "vendor_order_id")
        if not order_id:
            row = self._cap100_update(cid, state="UNKNOWN_SUBMISSION", place_completed_at_ms=_now_ms(), vendor_order_id=vendor_id or None, error_kind="PLACE_MISSING_ORDER_ID", error_message="place response returned no orderId", raw_json=json.dumps(placed, default=str))
            self._cap100_event("UNKNOWN_SUBMISSION", row=row, detail="response missing order id; CAP100 entries frozen")
            return {"ok": False, "accepted": True, "uncertain": True, "order": row}
        row = self._cap100_update(cid, state="RESTING", order_id=order_id, vendor_order_id=vendor_id or None, exchange_status=_txt(placed, "status", "orderStatus") or "NEW", place_completed_at_ms=_now_ms(), error_kind=None, error_message=None, raw_json=json.dumps(placed, default=str))
        self._cap100_event("ORDER_RESTING", row=row, detail=f"venue order {order_id}")
        return {"ok": True, "accepted": True, "order": row}

    def submit_cap100_taker(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.cap100_lock:
            return self._submit_cap100_taker_locked(payload)

    def _submit_cap100_taker_locked(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._cap100_gate(payload)
        cid, side, shares, signal_price = self._validate_cap100_order(payload)
        market = self._cap100_market(payload)
        row = self._cap100_insert(payload, "TAKER", market, cid, side, shares, signal_price)
        if str(row.get("state")) != "PLANNED":
            return {"ok": True, "idempotent": True, "order": row}
        client, wallet = self._poly_ensure_client()
        token = str(row["token_id"])
        up_book = client.orderbook(int(market["market_id"]), str(market["up_token_id"]))
        down_book = client.orderbook(int(market["market_id"]), str(market["down_token_id"]))
        top = binance_top_of_book(up_book, down_book, int(client.server_timestamp_ms()))
        latest_ask = top.up_ask if side == "UP" else top.down_ask
        if latest_ask is None or not 0 < float(latest_ask) < 1:
            row = self._cap100_update(cid, state="REJECTED", completed_at_ms=_now_ms(), error_kind="ASK_UNAVAILABLE", error_message="venue ask unavailable")
            self._cap100_event("ORDER_REJECTED", row=row, detail="taker ask unavailable")
            return {"ok": False, "accepted": False, "order": row}
        max_price = float(_finite(payload.get("maxPrice")) or signal_price)
        if float(latest_ask) > max_price + 1e-12:
            row = self._cap100_update(cid, state="REJECTED", completed_at_ms=_now_ms(), error_kind="TAKER_PRICE_CAP", error_message=f"venue ask {latest_ask:.4f} > maxPrice {max_price:.4f}")
            self._cap100_event("ORDER_REJECTED", row=row, detail=str(row.get("error_message") or ""))
            return {"ok": False, "accepted": False, "order": row}
        notional = float(latest_ask) * shares
        if notional < CAP100_MIN_NOTIONAL_USDT - 1e-9:
            row = self._cap100_update(cid, state="REJECTED", completed_at_ms=_now_ms(), error_kind="MARKET_MIN_NOTIONAL", error_message=f"CAP100 taker notional {notional:.4f} < {CAP100_MIN_NOTIONAL_USDT:.1f} USDT minimum; never upsize silently")
            self._cap100_event("ORDER_REJECTED", row=row, detail=str(row.get("error_message") or ""))
            return {"ok": False, "accepted": False, "order": row}
        amount_wei = str(int(round(notional * WEI)))
        self._cap100_update(cid, state="QUOTING")
        quote = client.get_quote(
            wallet_address=wallet["walletAddress"], token_id=token, amount_in_wei=amount_wei,
            price_limit=None, slippage_bps=CAP100_SLIPPAGE_BPS,
            fee_rate_bps=int(row.get("fee_rate_bps") or 200), funding_source="MPC",
            side="BUY", order_type="MARKET",
        )
        quote_id = str(quote.get("quoteId") or "").strip()
        if not quote_id:
            row = self._cap100_update(cid, state="REJECTED", completed_at_ms=_now_ms(), error_kind="QUOTE_MISSING_ID", error_message="MARKET quote returned no quoteId")
            self._cap100_event("ORDER_REJECTED", row=row, detail="market quote missing id")
            return {"ok": False, "accepted": False, "order": row}
        self._cap100_update(cid, state="QUOTE_READY", quote_id=quote_id)
        self._cap100_gate(payload)
        self._cap100_update(cid, state="PLACING", place_started_at_ms=_now_ms())
        try:
            placed = client.place_market_order(
                wallet_address=wallet["walletAddress"], wallet_id=wallet["walletId"], quote_id=quote_id,
                slippage_bps=CAP100_SLIPPAGE_BPS, account_type=CAP100_ACCOUNT_TYPE, funding_source="MPC",
            )
        except ApiTransportError as exc:
            row = self._cap100_update(cid, state="UNKNOWN_SUBMISSION", place_completed_at_ms=_now_ms(), error_kind="PLACE_TRANSPORT_UNKNOWN", error_message=str(exc)[:500])
            self._cap100_event("UNKNOWN_SUBMISSION", row=row, detail="taker venue write uncertain; entries frozen")
            return {"ok": False, "accepted": True, "uncertain": True, "order": row}
        order_id = _txt(placed, "orderId", "order_id", "id")
        vendor_id = _txt(placed, "vendorOrderId", "vendor_order_id")
        if not order_id:
            row = self._cap100_update(cid, state="UNKNOWN_SUBMISSION", place_completed_at_ms=_now_ms(), vendor_order_id=vendor_id or None, error_kind="PLACE_MISSING_ORDER_ID", error_message="market place response returned no orderId", raw_json=json.dumps(placed, default=str))
            self._cap100_event("UNKNOWN_SUBMISSION", row=row, detail="taker response missing order id; entries frozen")
            return {"ok": False, "accepted": True, "uncertain": True, "order": row}
        row = self._cap100_update(cid, state="RESTING", order_id=order_id, vendor_order_id=vendor_id or None, exchange_status=_txt(placed, "status", "orderStatus") or "SUBMITTED", place_completed_at_ms=_now_ms(), raw_json=json.dumps(placed, default=str))
        self._cap100_event("ORDER_ACCEPTED", row=row, detail=f"taker venue order {order_id}; awaiting fill reconciliation")
        return {"ok": True, "accepted": True, "order": row}

    def cancel_cap100_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.cap100_lock:
            return self._cancel_cap100_order_locked(payload)

    def _cancel_cap100_order_locked(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._cap100_gate(payload, require_armed=False)
        cid = str(payload.get("clientOrderId") or "").strip()
        with self.db_lock:
            raw = self.db.execute("SELECT * FROM engine_cap100_orders WHERE client_order_id=?", (cid,)).fetchone()
        if raw is None:
            raise v1.EchtgeldEngineError("CAP100 order not found")
        return self._cancel_cap100_row_locked(dict(raw), reason=str(payload.get("reason") or "CONTROLLER_REQUEST"))

    def _cancel_cap100_row_locked(self, row: dict[str, Any], *, reason: str) -> dict[str, Any]:
        """Request venue cancellation while retaining ownership until terminal proof.

        Internal lifecycle/risk paths use this after selecting an exact durable row,
        so they do not depend on the operator entry-source gate.  A cancel response
        is only an acknowledgement; reconciliation remains the terminal authority.
        """
        cid = str(row.get("client_order_id") or "").strip()
        if str(row.get("state")) in TERMINAL_STATES:
            return {"ok": True, "terminal": True, "order": row}
        if str(row.get("state")) == "CANCEL_PENDING":
            # A controller rollover and an engine auto-pause may request the same
            # risk-reducing cancel concurrently.  The first accepted request owns
            # reconciliation; never send a duplicate venue cancel or reinterpret
            # its response as new uncertainty.
            return {"ok": True, "accepted": True, "alreadyPending": True, "order": row}
        order_id = str(row.get("order_id") or "").strip()
        if not order_id:
            # An unknown placement cannot safely be cancelled by guessing an ID.
            row = self._cap100_update(cid, state="CANCEL_UNKNOWN", cancel_requested_at_ms=_now_ms(), error_kind="CANCEL_NO_CONFIRMED_ORDER_ID", error_message="cancel requested while placement identity is unresolved")
            self._cap100_event("CANCEL_UNKNOWN", row=row, detail=f"reason={reason}; no confirmed venue order id; reconciliation required")
            return {"ok": False, "uncertain": True, "order": row}
        client, wallet = self._poly_ensure_client()
        row = self._cap100_update(cid, state="CANCEL_PENDING", cancel_requested_at_ms=_now_ms())
        try:
            # Never mark terminal merely because this request was accepted.
            client.batch_cancel_orders_raw(
                wallet_address=wallet["walletAddress"], wallet_id=wallet["walletId"], order_ids=[order_id]
            )
        except ApiTransportError as exc:
            row = self._cap100_update(cid, state="CANCEL_UNKNOWN", error_kind="CANCEL_TRANSPORT_UNKNOWN", error_message=str(exc)[:500])
            self._cap100_event("CANCEL_UNKNOWN", row=row, detail=f"reason={reason}; cancel transport uncertain; keep treating order as live")
            return {"ok": False, "uncertain": True, "order": row}
        except Exception as exc:
            row = self._cap100_update(cid, state="CANCEL_UNKNOWN", error_kind="CANCEL_REQUEST_ERROR", error_message=str(exc)[:500])
            self._cap100_event("CANCEL_UNKNOWN", row=row, detail=f"reason={reason}; cancel request failed with uncertain venue state: {str(exc)[:400]}")
            return {"ok": False, "uncertain": True, "order": row}
        self._cap100_event("CANCEL_REQUEST_ACCEPTED", row=row, detail=f"reason={reason}; awaiting venue terminal confirmation")
        return {"ok": True, "accepted": True, "order": row}

    def _cancel_expired_r2_r21_takers(self) -> int:
        """Cancel an unfinished R2+R2.1 Taker after its fixed confirm horizon.

        The controller and HftBacktest contract both use 2200ms confirmation.  The
        durable venue owner enforces it here so controller/source-snapshot stalls or
        restarts cannot leave a zero/partial-fill Taker live until market expiry.
        Ownership is not released here; only a later reconciled terminal event does
        that.
        """
        now = _now_ms()
        requested = 0
        with self.cap100_lock:
            for row in self._cap100_rows(active_only=True):
                if str(row.get("source_id") or "").upper() != R2_R21_SOURCE:
                    continue
                if str(row.get("role") or "").upper() != "TAKER":
                    continue
                if str(row.get("state") or "").upper() not in {"RESTING", "PARTIAL_FILL"}:
                    continue
                accepted_at = int(row.get("place_completed_at_ms") or row.get("place_started_at_ms") or 0)
                if accepted_at <= 0 or now - accepted_at < R2_R21_TAKER_CONFIRM_TIMEOUT_MS:
                    continue
                result = self._cancel_cap100_row_locked(
                    row,
                    reason=f"R2_R21_TAKER_CONFIRM_TIMEOUT_{R2_R21_TAKER_CONFIRM_TIMEOUT_MS}MS",
                )
                if bool(result.get("accepted")) and not bool(result.get("alreadyPending")):
                    requested += 1
        return requested

    def _match_unknown(self, row: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any] | None:
        # Strong fingerprint only. Multiple candidates => remain uncertain/frozen.
        expected_token = str(row.get("token_id") or "")
        expected_price = float(row.get("requested_price") or 0.0)
        expected_time = int(row.get("place_started_at_ms") or row.get("created_at_ms") or 0)
        matches: list[dict[str, Any]] = []
        for remote in history:
            token = _txt(remote, "tokenId", "token_id")
            if token and token != expected_token:
                continue
            side = _txt(remote, "side").upper()
            if side and side != "BUY":
                continue
            order_type = _txt(remote, "orderType", "order_type", "type").upper()
            if order_type and order_type != ("LIMIT" if row.get("role") == "MAKER" else "MARKET"):
                continue
            px = _num(remote, "price", "priceLimit", "averagePrice", "avgPrice")
            if row.get("role") == "MAKER" and px is not None and abs(px - expected_price) > 0.011:
                continue
            ts = _num(remote, "createTime", "createdAt", "createdAtMs", "time", "timestamp")
            if ts is not None:
                tms = int(ts)
                if tms < 10_000_000_000:
                    tms *= 1000
                if expected_time > 0 and abs(tms - expected_time) > CAP100_UNKNOWN_MATCH_WINDOW_MS:
                    continue
            matches.append(remote)
        return matches[0] if len(matches) == 1 else None

    def _apply_remote(self, row: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
        cid = str(row["client_order_id"])
        old_shares = float(row.get("filled_share_qty") or 0.0)
        old_usdt = float(row.get("filled_usdt_amount") or 0.0)
        status = _txt(remote, "status", "orderStatus", "order_status").upper() or str(row.get("exchange_status") or "UNKNOWN")
        fill_pct = _num(remote, "fillPercentage", "fillPct")
        if fill_pct is not None and fill_pct > 1.0:
            fill_pct /= 100.0
        filled_shares = _num(remote, "filledShareQty", "filledShares", "executedQty", "shareQty")
        filled_usdt = _num(remote, "filledUsdtAmount", "filledUsdt", "executedUsdt", "quoteQty")
        if filled_shares is None:
            filled_shares = old_shares
        if filled_usdt is None:
            filled_usdt = old_usdt
        filled_shares = max(old_shares, float(filled_shares or 0.0))
        filled_usdt = max(old_usdt, float(filled_usdt or 0.0))
        order_id = _txt(remote, "orderId", "order_id", "id") or str(row.get("order_id") or "")
        vendor_id = _txt(remote, "vendorOrderId", "vendor_order_id") or str(row.get("vendor_order_id") or "")
        if status == "FILLED" or (fill_pct is not None and fill_pct >= 1 - 1e-9):
            state = "FILLED"
        elif status in {"CANCELED", "CANCELLED", "EXPIRED"}:
            state = "CANCELED"
        elif status in {"FAILED", "REJECTED"} and filled_shares <= 1e-9:
            state = "REJECTED"
        elif filled_shares > 1e-9 or status in {"PARTIALLY_FILLED", "PARTIAL_FILLED"}:
            state = "PARTIAL_FILL"
        else:
            state = "RESTING"
        prior_state = str(row.get("state") or "").upper()
        if prior_state in {"CANCEL_PENDING", "CANCEL_UNKNOWN"} and state in {"RESTING", "PARTIAL_FILL"}:
            # A non-terminal history row after a cancel request proves only that
            # the child may still be live. It must not roll ownership backward to
            # RESTING/PARTIAL_FILL, which would permit duplicate cancellation or
            # release a fail-closed CANCEL_UNKNOWN quarantine.
            state = prior_state
        avg = (filled_usdt / filled_shares) if filled_shares > 1e-9 and filled_usdt > 0 else _num(remote, "averagePrice", "avgPrice", "price")
        updated = self._cap100_update(
            cid, state=state, exchange_status=status, fill_percentage=fill_pct,
            filled_share_qty=filled_shares, filled_usdt_amount=filled_usdt,
            avg_fill_price=avg, order_id=order_id or None, vendor_order_id=vendor_id or None,
            last_reconciled_at_ms=_now_ms(), completed_at_ms=_now_ms() if state in TERMINAL_STATES else None,
            error_kind=row.get("error_kind") if state in {"UNKNOWN_SUBMISSION", "CANCEL_UNKNOWN"} else None,
            error_message=row.get("error_message") if state in {"UNKNOWN_SUBMISSION", "CANCEL_UNKNOWN"} else None,
            raw_json=json.dumps(remote, separators=(",", ":"), default=str),
        )
        delta_shares = max(0.0, filled_shares - old_shares)
        delta_usdt = max(0.0, filled_usdt - old_usdt)
        if delta_shares > 1e-9:
            fill_price = delta_usdt / delta_shares if delta_usdt > 1e-9 else float(avg or row.get("requested_price") or 0.0)
            self._cap100_event("FILL_DELTA", row=updated, delta_shares=delta_shares, delta_usdt=delta_usdt, fill_price=fill_price, detail=f"venue-confirmed cumulative fill {filled_shares:g} shares")
        if state != str(row.get("state")):
            self._cap100_event(f"ORDER_{state}", row=updated, detail=f"{row.get('state')}->{state}; exchange={status}")
        return updated

    def _cap100_reconcile_once(self) -> None:
        rows = self._cap100_rows(active_only=True)
        if not rows:
            self.cap100_reconcile_last_ms = _now_ms()
            return
        client, wallet = self._poly_ensure_client()
        history_payload = client.order_history(wallet["walletAddress"], limit=100)
        history_rows = _order_rows(history_payload)
        by_id = {
            _txt(r, "orderId", "order_id", "id"): r
            for r in history_rows if _txt(r, "orderId", "order_id", "id")
        }
        active_rows: list[dict[str, Any]] = []
        try:
            active_rows = _order_rows(client.active_orders(wallet["walletAddress"], limit=100))
        except Exception:
            active_rows = []
        for r in active_rows:
            oid = _txt(r, "orderId", "order_id", "id")
            if oid:
                by_id[oid] = r
        for row in rows:
            remote = None
            oid = str(row.get("order_id") or "")
            if oid:
                remote = by_id.get(oid)
            elif str(row.get("state")) in {"UNKNOWN_SUBMISSION", "CANCEL_UNKNOWN"}:
                remote = self._match_unknown(row, history_rows + active_rows)
            if remote is not None:
                self._apply_remote(row, remote)
            elif str(row.get("state")) == "UNKNOWN_SUBMISSION" and _now_ms() - int(row.get("place_started_at_ms") or row.get("created_at_ms") or 0) > CAP100_UNKNOWN_HARD_AGE_MS:
                # Deliberately remain UNKNOWN and frozen. Absence from a bounded history
                # page is not proof that the venue did not accept the order.
                if str(row.get("error_kind") or "") != "UNKNOWN_UNRESOLVED_30S":
                    updated = self._cap100_update(str(row["client_order_id"]), error_kind="UNKNOWN_UNRESOLVED_30S", error_message="No unique venue-history match after 30s; remains fail-closed, manual review required")
                    self._cap100_event("UNKNOWN_REQUIRES_REVIEW", row=updated, detail=str(updated.get("error_message") or ""))
        self._cancel_expired_r2_r21_takers()
        self.cap100_reconcile_last_ms = _now_ms()
        self.cap100_last_error = None

    def _cap100_cancel_all(self, reason: str) -> None:
        with self.cap100_lock:
            self._cap100_cancel_all_locked(reason)

    def _cap100_cancel_all_locked(self, reason: str) -> None:
        # Internal risk-reduction path. It deliberately bypasses the entry-source
        # gate so PAUSE/stop-loss/heartbeat-loss/restart can always cancel known
        # resting Maker risk even after V22 has cleared source selection.
        rows = [r for r in self._cap100_rows(active_only=True) if str(r.get("role")) == "MAKER"]
        if not rows:
            return
        client, wallet = self._poly_ensure_client()
        for row in rows:
            cid = str(row["client_order_id"])
            state = str(row.get("state") or "")
            if state in TERMINAL_STATES:
                continue
            if state == "CANCEL_PENDING":
                continue
            order_id = str(row.get("order_id") or "").strip()
            if not order_id:
                updated = self._cap100_update(
                    cid, state="CANCEL_UNKNOWN", cancel_requested_at_ms=_now_ms(),
                    error_kind="CANCEL_NO_CONFIRMED_ORDER_ID",
                    error_message=f"internal cancel-all ({reason}) requested while placement identity is unresolved",
                )
                self._cap100_event("CANCEL_UNKNOWN", row=updated, detail=f"reason={reason}; no confirmed venue order id")
                continue
            updated = self._cap100_update(cid, state="CANCEL_PENDING", cancel_requested_at_ms=_now_ms())
            try:
                client.batch_cancel_orders_raw(
                    wallet_address=wallet["walletAddress"], wallet_id=wallet["walletId"], order_ids=[order_id]
                )
                self._cap100_event("CANCEL_REQUEST_ACCEPTED", row=updated, detail=f"internal reason={reason}; awaiting venue terminal confirmation")
            except ApiTransportError as exc:
                updated = self._cap100_update(cid, state="CANCEL_UNKNOWN", error_kind="CANCEL_TRANSPORT_UNKNOWN", error_message=str(exc)[:500])
                self._cap100_event("CANCEL_UNKNOWN", row=updated, detail=f"internal reason={reason}; cancel transport uncertain")
            except Exception as exc:
                updated = self._cap100_update(cid, state="CANCEL_UNKNOWN", error_kind="CANCEL_REQUEST_ERROR", error_message=str(exc)[:500])
                self._cap100_event("CANCEL_UNKNOWN", row=updated, detail=f"internal reason={reason}; uncertain cancel failure: {str(exc)[:300]}")
        self._cap100_event("CANCEL_ALL_REQUESTED", detail=f"reason={reason}; count={len(rows)}")

    def _cap100_reconcile_loop(self) -> None:
        while not self.stop_event.wait(CAP100_RECONCILE_SECONDS):
            try:
                self._cap100_reconcile_once()
            except Exception as exc:
                self.cap100_last_error = f"{type(exc).__name__}: {str(exc)[:400]}"
            try:
                if self.armed and self._selected_entry_source() in CAP100_ALLOWED_SOURCES:
                    hb = self.cap100_last_heartbeat_ms
                    if hb is None or _now_ms() - hb > CAP100_HEARTBEAT_TIMEOUT_MS:
                        # Fail-safe controller death: pause first, then cancel known Maker risk.
                        source_id = self._selected_entry_source() or "CAP100_CONTROLLER"
                        self.pause(f"{source_id} heartbeat lost")
                        self._cap100_event(
                            "CONTROLLER_HEARTBEAT_LOST",
                            detail=f"{source_id} heartbeat stale; engine paused and Maker cancel-all requested",
                            payload={"entrySource": source_id},
                        )
            except Exception as exc:
                self.cap100_last_error = f"heartbeat fail-safe: {type(exc).__name__}: {str(exc)[:350]}"

    def pause(self, reason: str = "operator") -> dict[str, Any]:
        with self.cap100_lock:
            state = super().pause(reason)
            try:
                self._cap100_cancel_all_locked(f"PAUSE:{reason}")
            except Exception as exc:
                self.cap100_last_error = f"pause cancel-all: {type(exc).__name__}: {str(exc)[:350]}"
            return state

    def _auto_pause_stop_loss(self, *, net_pnl: float, threshold: float) -> bool:
        with self.cap100_lock:
            changed = super()._auto_pause_stop_loss(net_pnl=net_pnl, threshold=threshold)
            if changed:
                try:
                    self._cap100_cancel_all_locked("STOP_LOSS")
                except Exception as exc:
                    self.cap100_last_error = f"stop-loss cancel-all: {type(exc).__name__}: {str(exc)[:350]}"
            return changed

    def _target_taker_markets(self) -> dict[int, set[str]]:
        result = super()._target_taker_markets()
        with self.db_lock:
            rows = self.db.execute(
                "SELECT DISTINCT source_market_id FROM engine_cap100_orders WHERE filled_share_qty>0"
            ).fetchall()
        for row in rows:
            result.setdefault(int(row["source_market_id"]), set()).add(CAP100_COHORT)
        return result

    def _cap100_performance(self) -> dict[str, Any]:
        settlements = self._settlement_map()
        rows = self._cap100_rows(active_only=False)
        pnl = stake = 0.0
        settled_orders = wins = losses = 0
        market_pnl: dict[int, float] = {}
        for row in rows:
            shares = float(row.get("filled_share_qty") or 0.0)
            cost = float(row.get("filled_usdt_amount") or 0.0)
            if shares <= 1e-9:
                continue
            if cost <= 1e-9:
                px = float(row.get("avg_fill_price") or row.get("requested_price") or 0.0)
                cost = px * shares
            settlement = settlements.get((CAP100_COHORT, int(row.get("source_market_id") or 0)))
            winner = str((settlement or {}).get("winner") or "").upper()
            if winner not in {"UP", "DOWN"}:
                continue
            payout = shares if str(row.get("side")) == winner else 0.0
            row_pnl = payout - cost
            stake += cost
            pnl += row_pnl
            settled_orders += 1
            wins += int(row_pnl > 1e-9)
            losses += int(row_pnl < -1e-9)
            mid = int(row.get("source_market_id") or 0)
            market_pnl[mid] = market_pnl.get(mid, 0.0) + row_pnl
        return {
            "orders": len(rows),
            "filledOrdersSettled": settled_orders,
            "wins": wins,
            "losses": losses,
            "stakeUsdt": stake,
            "netPnlUsdt": pnl,
            "winRate": wins / (wins + losses) if wins + losses else None,
            "settledMarkets": len(market_pnl),
            "marketNetPnl": [{"marketId": k, "netPnlUsdt": v} for k, v in sorted(market_pnl.items())[-50:]],
            "basis": "venue-confirmed cumulative strategy fills joined to official SETTLED UP/DOWN winner; partial fills count only confirmed shares/cost",
        }

    def _performance_snapshot(self, *, force_sync: bool = False) -> dict[str, Any]:
        base = super()._performance_snapshot(force_sync=force_sync)
        cap = self._cap100_performance()
        cap_pnl = float(cap.get("netPnlUsdt") or 0.0)
        base["cap100NetPnlUsdt"] = cap_pnl
        base["netPnlUsdt"] = float(base.get("netPnlUsdt") or 0.0) + cap_pnl
        base["cap100"] = cap
        base["accountingBasis"] = str(base.get("accountingBasis") or "") + " + CAP100 venue-confirmed fill ledger."
        return base

    def cap100_state(self) -> dict[str, Any]:
        active = self._cap100_rows(active_only=True)
        unknown = self._cap100_unknown_write()
        with self.db_lock:
            recent = [dict(r) for r in self.db.execute("SELECT * FROM engine_cap100_orders ORDER BY created_at_ms DESC LIMIT 80").fetchall()]
            seq = self.db.execute("SELECT MAX(seq) AS seq FROM engine_cap100_events").fetchone()
        now = _now_ms()
        selected_source = self._selected_entry_source()
        effective_source = selected_source if selected_source in CAP100_ALLOWED_SOURCES else CAP100_SOURCE
        return {
            "sourceId": effective_source,
            "strategy": CAP100_STRATEGY_BY_SOURCE[effective_source],
            "armed": bool(self.armed),
            "selected": self._selected_entry_source() in CAP100_ALLOWED_SOURCES,
            "selectedSourceId": selected_source,
            "allowedSources": sorted(CAP100_ALLOWED_SOURCES),
            "entryWriteFrozen": unknown is not None,
            "unknownWrite": unknown,
            "heartbeatAgeMs": now - self.cap100_last_heartbeat_ms if self.cap100_last_heartbeat_ms else None,
            "heartbeatTimeoutMs": CAP100_HEARTBEAT_TIMEOUT_MS,
            "lastReconcileAgeMs": now - self.cap100_reconcile_last_ms if self.cap100_reconcile_last_ms else None,
            "lastError": self.cap100_last_error,
            "activeOrders": active,
            "recentOrders": recent,
            "latestEventSeq": int(seq["seq"] or 0) if seq else 0,
            "performance": self._cap100_performance(),
            "safety": {
                "paperFillProxyForbidden": True,
                "fillSource": "VENUE_ORDER_HISTORY_CUMULATIVE_DELTA",
                "unknownSubmissionFreezesEntries": True,
                "cancelAckIsNotTerminal": True,
                "pauseCancelsMaker": True,
                "stopLossCancelsMaker": True,
                "heartbeatLossPausesAndCancelsMaker": True,
                "partialFillAccounting": True,
                "idempotentClientOrderId": True,
                "preVenueArmAndSourceRecheck": True,
                "r2R21TakerConfirmTimeoutMs": R2_R21_TAKER_CONFIRM_TIMEOUT_MS,
                "r2R21TakerTimeoutCancelWaitsTerminal": True,
            },
        }

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION
        payload["cap100Execution"] = self.cap100_state()
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        cap = self.cap100_state()
        payload["version"] = VERSION
        payload["cap100ExecutionAdapter"] = True
        payload["cap100VenueFillFeedback"] = True
        payload["cap100UnknownSubmissionFreezesEntries"] = True
        payload["cap100PauseCancelsRestingMaker"] = True
        payload["cap100StopLossCancelsRestingMaker"] = True
        payload["cap100HeartbeatFailSafe"] = True
        payload["cap100AllowedSources"] = sorted(CAP100_ALLOWED_SOURCES)
        payload["r2R21SemanticCooperationSource"] = R2_R21_SOURCE
        payload["cap100DynamicSourceAttribution"] = True
        payload["cap100EventSourceProjection"] = True
        payload["cap100MakerMinNotionalPreflight"] = True
        payload["r2R21TakerConfirmTimeoutMs"] = R2_R21_TAKER_CONFIRM_TIMEOUT_MS
        payload["r2R21TakerTimeoutCancelWaitsTerminal"] = True
        payload["cap100EntryWriteFrozen"] = bool(cap["entryWriteFrozen"])
        payload["cap100ReconcileAlive"] = bool(self.cap100_reconcile_thread and self.cap100_reconcile_thread.is_alive()) if self.cap100_reconcile_thread is not None else True
        return payload

    def close(self) -> None:
        self.stop_event.set()
        if self.cap100_reconcile_thread is not None and self.cap100_reconcile_thread.is_alive():
            self.cap100_reconcile_thread.join(timeout=2.0)
        super().close()


class _Handler(v22._Handler):
    engine: EchtgeldEngine

    def do_GET(self) -> None:  # noqa: N802
        from urllib.parse import parse_qs
        path, _, query = self.path.partition("?")
        if path == "/cap100/state":
            self._send(200, {"ok": True, "cap100": self.engine.cap100_state()})
            return
        if path == "/cap100/events":
            args = parse_qs(query)
            after = int((args.get("afterSeq") or ["0"])[0] or 0)
            limit = int((args.get("limit") or ["300"])[0] or 300)
            self._send(200, {"ok": True, "events": self.engine.cap100_events(after, limit)})
            return
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path not in {"/cap100/heartbeat", "/cap100/maker", "/cap100/taker", "/cap100/cancel"}:
            return super().do_POST()
        try:
            payload = self._body()
            if path == "/cap100/heartbeat":
                result = self.engine.cap100_heartbeat(payload)
            elif path == "/cap100/maker":
                result = self.engine.submit_cap100_maker(payload)
            elif path == "/cap100/taker":
                result = self.engine.submit_cap100_taker(payload)
            else:
                result = self.engine.cancel_cap100_order(payload)
            self._send(200, result)
        except v1.EchtgeldEngineError as exc:
            self._send(400, {"ok": False, "error": str(exc)[:500]})
        except Exception as exc:
            self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:500]}"})


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV23Handler", (_Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; CAP100 source={CAP100_SOURCE}; "
        "venue-confirmed fill feedback; uncertain writes freeze entries; pause/stop-loss/heartbeat-loss cancel Maker",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown(); server.server_close(); engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
