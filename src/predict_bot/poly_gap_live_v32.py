from __future__ import annotations

import os
import time
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v8 import (
    ENTRY_MAX_DETERIORATION_BPS,
    EXIT_SLIPPAGE_BPS,
)
from .poly_gap_live_v31 import RuntimeReversalBreakerPolyGapLiveEngine


ENTRY_DEPTH_BUFFER_RATIO = max(
    1.0,
    float(os.environ.get("PREDICT_POLY_GAP_LIVE_ENTRY_DEPTH_BUFFER_RATIO", "1.15")),
)
EXIT_NO_FILL_RETRY_COOLDOWN_MS = max(
    100,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_EXIT_NO_FILL_RETRY_COOLDOWN_MS", "100")),
)
EXIT_DEPTH_TOLERANCE_BPS = max(
    0,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_EXIT_DEPTH_TOLERANCE_BPS", str(EXIT_SLIPPAGE_BPS))),
)
ENTRY_DEPTH_EVENT_MIN_INTERVAL_MS = max(
    250,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_ENTRY_DEPTH_EVENT_MIN_INTERVAL_MS", "1000")),
)


def _book_levels(book: dict[str, Any], key: str) -> list[tuple[float, float]]:
    raw = book.get(key)
    if not isinstance(raw, list):
        return []
    parsed: list[tuple[float, float]] = []
    for level in raw:
        price: float | None
        size: float | None
        if isinstance(level, dict):
            price = base._finite(level.get("price"))
            size = base._finite(level.get("size", level.get("quantity")))
        elif isinstance(level, (list, tuple)) and level:
            price = base._finite(level[0])
            size = base._finite(level[1]) if len(level) > 1 else None
        else:
            continue
        if price is None or size is None or not (0 < price < 1) or size <= 0:
            continue
        parsed.append((price, size))
    parsed.sort(key=lambda row: row[0], reverse=(key == "bids"))
    return parsed


def _buy_depth(
    levels: list[tuple[float, float]],
    *,
    stake_usdt: float,
    max_price: float,
    buffer_ratio: float,
) -> dict[str, Any]:
    eligible = [(price, size) for price, size in levels if price <= max_price + 1e-12]
    visible_notional = sum(price * size for price, size in eligible)
    coverage = visible_notional / stake_usdt if stake_usdt > 0 else 0.0

    remaining = max(0.0, stake_usdt)
    spent = 0.0
    shares = 0.0
    worst_price: float | None = None
    for price, size in eligible:
        level_notional = price * size
        take_notional = min(remaining, level_notional)
        if take_notional <= 0:
            break
        spent += take_notional
        shares += take_notional / price
        remaining -= take_notional
        worst_price = price
        if remaining <= 1e-9:
            break
    expected_vwap = spent / shares if shares > 0 else None
    required_visible = stake_usdt * buffer_ratio
    return {
        "visibleNotionalUsdt": visible_notional,
        "requiredStakeUsdt": stake_usdt,
        "requiredBufferedNotionalUsdt": required_visible,
        "coverageRatio": coverage,
        "bufferRatio": buffer_ratio,
        "expectedFillVwap": expected_vwap,
        "expectedWorstPrice": worst_price,
        "eligibleLevels": len(eligible),
        "sufficient": visible_notional + 1e-12 >= required_visible,
    }


def _sell_depth(
    levels: list[tuple[float, float]],
    *,
    required_shares: float,
    min_price: float,
) -> dict[str, Any]:
    eligible = [(price, size) for price, size in levels if price + 1e-12 >= min_price]
    visible_shares = sum(size for _price, size in eligible)
    visible_notional = sum(price * size for price, size in eligible)
    coverage = visible_shares / required_shares if required_shares > 0 else None

    remaining = max(0.0, required_shares)
    filled = 0.0
    proceeds = 0.0
    worst_price: float | None = None
    for price, size in eligible:
        take = min(remaining, size)
        if take <= 0:
            break
        filled += take
        proceeds += take * price
        remaining -= take
        worst_price = price
        if remaining <= 1e-9:
            break
    expected_vwap = proceeds / filled if filled > 0 else None
    return {
        "visibleShares": visible_shares,
        "visibleNotionalUsdt": visible_notional,
        "requiredShares": required_shares,
        "coverageRatio": coverage,
        "expectedFillVwap": expected_vwap,
        "expectedWorstPrice": worst_price,
        "eligibleLevels": len(eligible),
        "sufficient": bool(required_shares > 0 and visible_shares + 1e-9 >= required_shares),
    }


class FokDepthAndExitRetryPolyGapLiveEngine(RuntimeReversalBreakerPolyGapLiveEngine):
    """V32: preflight BUY FOK depth and separate SELL execution retry from signal debounce.

    New BUYs are blocked unless the Binance Prediction Ask ladder shows at least
    115% of the intended USDT stake inside the already-existing V28 execution
    price envelope. No extra HTTP request is added between signed quote and order
    placement: the same direct order-book request that triggers the entry is
    parsed more completely.

    SELLs remain exit-priority and are never blocked by visible depth. While an
    OPEN position is managed, V28 already polls the held-side order book for the
    take-profit Bid. V32 parses that same response into an exit-depth snapshot so
    diagnostics add no extra network round trip.

    Once a POLY_DIRECTION_FLIP has passed V12's normal 500ms/3-receipt debounce,
    an explicit MARKET/FOK NO_FILL is treated as an execution failure, not a new
    signal decision. After a short cooldown, a fresh still-opposite Poly signal
    can retry the SELL without repeating the full debounce. If Poly has reverted
    to the held side, the persisted retry intent is cleared instead.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._last_entry_depth_preflight: dict[str, Any] | None = None
        self._last_exit_depth_snapshot: dict[str, Any] | None = None
        self._entry_depth_blocked_this_tick = False
        self._entry_depth_blocks = 0
        self._entry_no_fills = 0
        self._exit_no_fills = 0
        self._fast_exit_retries = 0
        self._last_entry_depth_event_at_ms = 0
        self._last_fast_retry_key: tuple[int, int] | None = None
        super().__init__(*args, **kwargs)

    def _create_schema(self) -> None:
        super()._create_schema()
        with self.db_lock:
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS poly_gap_live_execution_attempts (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       round_id INTEGER NOT NULL,
                       market_id INTEGER NOT NULL,
                       side TEXT NOT NULL,
                       action TEXT NOT NULL,
                       attempt_no INTEGER NOT NULL,
                       created_at_ms INTEGER NOT NULL,
                       book_observed_at_ms INTEGER,
                       best_price REAL,
                       best_size REAL,
                       price_boundary REAL,
                       visible_amount REAL,
                       required_amount REAL,
                       coverage_ratio REAL,
                       expected_vwap REAL,
                       expected_worst_price REAL,
                       book_rtt_ms REAL,
                       quote_started_at_ms INTEGER,
                       quote_completed_at_ms INTEGER,
                       quote_rtt_ms REAL,
                       quote_average REAL,
                       quote_expire_at_ms INTEGER,
                       order_response_at_ms INTEGER,
                       quote_response_to_order_response_ms INTEGER,
                       order_id TEXT,
                       outcome TEXT,
                       order_status TEXT,
                       message TEXT,
                       UNIQUE(round_id, action, attempt_no)
                   )"""
            )
            self.db.execute(
                """CREATE INDEX IF NOT EXISTS idx_poly_gap_live_execution_attempts_round
                     ON poly_gap_live_execution_attempts(round_id, action, attempt_no)"""
            )
            self.db.commit()

    def _effective_entry_stake(self) -> float:
        try:
            risk = self._loss_state()
            value = base._finite(risk.get("effectiveStakeUsdt"))
            if value is not None and value > 0:
                return value
        except Exception:
            pass
        settings = self._settings()
        return max(0.01, float(settings.get("stakeUsdt") or base.DEFAULT_STAKE_USDT))

    def _direct_book(
        self, market: dict[str, Any], side: str
    ) -> tuple[float | None, float | None, float]:
        # Replace the inherited direct-book read rather than doing a second read:
        # depth parsing must not lengthen quote -> placement latency.
        now = time.monotonic()
        wait = base.BINANCE_BOOK_MIN_INTERVAL - (now - self.last_binance_book_at)
        if wait > 0:
            self.stop_event.wait(wait)
        with self.lock:
            client = self.client
        if client is None:
            return None, None, 0.0

        token_id = str(market[f"{side.lower()}_token_id"])
        started = time.monotonic()
        book = client.orderbook(int(market["market_id"]), token_id)
        rtt_ms = max(0.0, (time.monotonic() - started) * 1000.0)
        self.last_binance_book_at = time.monotonic()
        observed_ms = base._now_ms()

        levels = _book_levels(book, "asks")
        ask = levels[0][0] if levels else None
        ask_size = levels[0][1] if levels else None
        self._entry_price_blocked_this_tick = False
        self._entry_depth_blocked_this_tick = False

        max_entry = float(self._settings()["maxEntryPrice"])
        if ask is None:
            self._last_entry_depth_preflight = {
                "marketId": int(market["market_id"]),
                "side": str(side),
                "observedAtMs": observed_ms,
                "bookRttMs": rtt_ms,
                "available": False,
                "reason": "NO_ASK_LEVELS",
            }
            return None, None, rtt_ms

        deterioration_cap = min(
            1.0,
            ask * (1.0 + ENTRY_MAX_DETERIORATION_BPS / 10_000.0),
        )
        effective_cap = min(deterioration_cap, max_entry)
        stake = self._effective_entry_stake()
        depth = _buy_depth(
            levels,
            stake_usdt=stake,
            max_price=effective_cap,
            buffer_ratio=ENTRY_DEPTH_BUFFER_RATIO,
        )
        snapshot = {
            "marketId": int(market["market_id"]),
            "side": str(side),
            "observedAtMs": observed_ms,
            "bookRttMs": rtt_ms,
            "available": True,
            "bestAsk": ask,
            "bestAskSize": ask_size,
            "configuredMaxEntryPrice": max_entry,
            "deteriorationCap": deterioration_cap,
            "effectivePriceCap": effective_cap,
            **depth,
        }
        self._last_entry_depth_preflight = snapshot
        self.last_binance = {
            "marketId": int(market["market_id"]),
            "side": side,
            "ask": ask,
            "askSize": ask_size,
            "bookRttMs": rtt_ms,
            "observedAtMs": observed_ms,
            "entryDepthCoverageRatio": depth["coverageRatio"],
            "entryVisibleDepthUsdt": depth["visibleNotionalUsdt"],
        }

        if ask + 1e-12 >= max_entry:
            self._entry_price_blocked_this_tick = True
            self._last_entry_price_guard = {
                "marketId": int(market["market_id"]),
                "side": str(side),
                "ask": ask,
                "maxEntryPrice": max_entry,
                "blocked": True,
                "checkedAtMs": observed_ms,
            }
            return None, ask_size, rtt_ms

        self._last_entry_price_guard = {
            "marketId": int(market["market_id"]),
            "side": str(side),
            "ask": ask,
            "maxEntryPrice": max_entry,
            "blocked": False,
            "checkedAtMs": observed_ms,
        }

        if not bool(depth["sufficient"]):
            self._entry_depth_blocked_this_tick = True
            self._entry_depth_blocks += 1
            if observed_ms - self._last_entry_depth_event_at_ms >= ENTRY_DEPTH_EVENT_MIN_INTERVAL_MS:
                self._last_entry_depth_event_at_ms = observed_ms
                self._event(
                    "INFO",
                    "ENTRY_FOK_DEPTH_PREFLIGHT_BLOCKED",
                    int(market["market_id"]),
                    None,
                    (
                        f"{side} BUY FOK not sent: visible depth within {effective_cap:.6f} is "
                        f"{float(depth['visibleNotionalUsdt']):.4f} USDT, need "
                        f"{float(depth['requiredBufferedNotionalUsdt']):.4f} USDT "
                        f"({ENTRY_DEPTH_BUFFER_RATIO:.2f}x of {stake:.4f} stake); "
                        f"coverage={float(depth['coverageRatio']):.3f}x"
                    ),
                )
            return None, ask_size, rtt_ms
        return ask, ask_size, rtt_ms

    def _held_side_take_profit_bid(
        self, market: dict[str, Any], active: dict[str, Any]
    ) -> tuple[float | None, float | None, float | None]:
        # Same request cadence as V28, but parse all Bid levels for diagnostics.
        now = time.monotonic()
        if now - self.last_binance_book_at < base.BINANCE_BOOK_MIN_INTERVAL:
            cached = self._last_take_profit_book or {}
            if (
                int(cached.get("marketId") or 0) == int(active["market_id"])
                and str(cached.get("side") or "") == str(active["side"])
            ):
                return (
                    base._finite(cached.get("bid")),
                    base._finite(cached.get("bidSize")),
                    base._finite(cached.get("bookRttMs")),
                )
            return None, None, None

        with self.lock:
            client = self.client
        if client is None:
            return None, None, None
        token_id = str(
            active.get("token_id")
            or market.get(f"{str(active['side']).lower()}_token_id")
            or ""
        )
        if not token_id:
            return None, None, None

        started = time.monotonic()
        try:
            book = client.orderbook(int(active["market_id"]), token_id)
        except Exception as exc:
            self.last_binance_book_at = time.monotonic()
            self._last_take_profit_error = str(exc)[:300]
            return None, None, None
        rtt_ms = max(0.0, (time.monotonic() - started) * 1000.0)
        self.last_binance_book_at = time.monotonic()
        observed_ms = base._now_ms()

        levels = _book_levels(book, "bids")
        bid = levels[0][0] if levels else None
        bid_size = levels[0][1] if levels else None
        required_shares = base._finite(active.get("shares")) or 0.0
        min_price = (
            max(0.0, bid * (1.0 - EXIT_DEPTH_TOLERANCE_BPS / 10_000.0))
            if bid is not None
            else 0.0
        )
        depth = _sell_depth(
            levels,
            required_shares=required_shares,
            min_price=min_price,
        )
        snapshot = {
            "marketId": int(active["market_id"]),
            "roundId": int(active["id"]),
            "side": str(active["side"]),
            "bid": bid,
            "bidSize": bid_size,
            "bookRttMs": rtt_ms,
            "observedAtMs": observed_ms,
            "minPriceWithinExitTolerance": min_price,
            "exitDepthToleranceBps": EXIT_DEPTH_TOLERANCE_BPS,
            **depth,
        }
        self._last_exit_depth_snapshot = snapshot
        self._last_take_profit_book = snapshot
        self._last_take_profit_error = None
        with self.lock:
            existing = dict(self.last_binance or {})
            if (
                int(existing.get("marketId") or 0) != int(active["market_id"])
                or str(existing.get("side") or "") != str(active["side"])
            ):
                existing = {
                    "marketId": int(active["market_id"]),
                    "side": str(active["side"]),
                }
            existing.update(snapshot)
            self.last_binance = existing
        return bid, bid_size, rtt_ms

    def _begin_attempt(
        self,
        row: dict[str, Any],
        *,
        action: str,
        depth: dict[str, Any] | None,
    ) -> int:
        round_id = int(row["id"])
        with self.db_lock:
            existing = self.db.execute(
                """SELECT COALESCE(MAX(attempt_no),0) AS n
                     FROM poly_gap_live_execution_attempts
                    WHERE round_id=? AND action=?""",
                (round_id, action),
            ).fetchone()
            attempt_no = int(existing["n"] if existing else 0) + 1
            d = depth or {}
            best_price = base._finite(d.get("bestAsk" if action == "BUY" else "bid"))
            best_size = base._finite(d.get("bestAskSize" if action == "BUY" else "bidSize"))
            boundary = base._finite(
                d.get("effectivePriceCap" if action == "BUY" else "minPriceWithinExitTolerance")
            )
            visible_amount = base._finite(
                d.get("visibleNotionalUsdt" if action == "BUY" else "visibleShares")
            )
            required_amount = base._finite(
                d.get("requiredStakeUsdt" if action == "BUY" else "requiredShares")
            )
            cursor = self.db.execute(
                """INSERT INTO poly_gap_live_execution_attempts(
                       round_id,market_id,side,action,attempt_no,created_at_ms,
                       book_observed_at_ms,best_price,best_size,price_boundary,
                       visible_amount,required_amount,coverage_ratio,expected_vwap,
                       expected_worst_price,book_rtt_ms,outcome
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    round_id,
                    int(row["market_id"]),
                    str(row["side"]),
                    action,
                    attempt_no,
                    base._now_ms(),
                    int(d.get("observedAtMs") or 0) or None,
                    best_price,
                    best_size,
                    boundary,
                    visible_amount,
                    required_amount,
                    base._finite(d.get("coverageRatio")),
                    base._finite(d.get("expectedFillVwap")),
                    base._finite(d.get("expectedWorstPrice")),
                    base._finite(d.get("bookRttMs")),
                    "STARTED",
                ),
            )
            self.db.commit()
            return int(cursor.lastrowid)

    def _finish_attempt(
        self,
        attempt_id: int,
        row: dict[str, Any],
        *,
        outcome: str,
        order_status: str | None = None,
        message: str | None = None,
    ) -> None:
        action_row = self.db.execute(
            "SELECT action FROM poly_gap_live_execution_attempts WHERE id=?",
            (int(attempt_id),),
        ).fetchone()
        action = str(action_row["action"] if action_row else "")
        prefix = "entry" if action == "BUY" else "exit"
        quote_started = row.get(f"{prefix}_quote_started_at_ms")
        quote_completed = row.get(f"{prefix}_quote_completed_at_ms")
        order_response = row.get(f"{prefix}_placed_at_ms")
        quote_to_order = None
        try:
            if quote_completed is not None and order_response is not None:
                quote_to_order = max(0, int(order_response) - int(quote_completed))
        except (TypeError, ValueError):
            quote_to_order = None
        with self.db_lock:
            self.db.execute(
                """UPDATE poly_gap_live_execution_attempts SET
                       quote_started_at_ms=?,quote_completed_at_ms=?,quote_rtt_ms=?,
                       quote_average=?,quote_expire_at_ms=?,order_response_at_ms=?,
                       quote_response_to_order_response_ms=?,order_id=?,outcome=?,
                       order_status=?,message=?
                     WHERE id=?""",
                (
                    quote_started,
                    quote_completed,
                    row.get(f"{prefix}_quote_rtt_ms"),
                    row.get(f"{prefix}_quote_average"),
                    row.get(f"{prefix}_quote_expire_at_ms") if action == "BUY" else None,
                    order_response,
                    quote_to_order,
                    row.get(f"{prefix}_order_id"),
                    str(outcome),
                    order_status,
                    message,
                    int(attempt_id),
                ),
            )
            self.db.commit()

    def _latest_attempt_id(self, round_id: int, action: str) -> int | None:
        with self.db_lock:
            row = self.db.execute(
                """SELECT id FROM poly_gap_live_execution_attempts
                    WHERE round_id=? AND action=? ORDER BY attempt_no DESC LIMIT 1""",
                (int(round_id), action),
            ).fetchone()
        return int(row["id"]) if row else None

    def _open_round(self, row: dict[str, Any], poly: dict[str, Any]) -> None:
        attempt_id = self._begin_attempt(
            row,
            action="BUY",
            depth=self._last_entry_depth_preflight,
        )
        super()._open_round(row, poly)
        refreshed = self._round_state(int(row["id"])) or row
        state = str(refreshed.get("state") or "")
        if state == "ENTRY_SYNC":
            outcome = "SUBMITTED"
        elif state == "AMBIGUOUS":
            outcome = "AMBIGUOUS"
        elif state in {"REJECTED", "FAILED"}:
            outcome = str(refreshed.get("error_kind") or refreshed.get("close_reason") or state)
        else:
            outcome = state or "UNKNOWN"
        self._finish_attempt(
            attempt_id,
            refreshed,
            outcome=outcome,
            message=str(refreshed.get("error_message") or "") or None,
        )

    def _exit_round(self, row: dict[str, Any], signal_ms: int) -> None:
        # Reconciliation of an already-submitted order is not a new execution attempt.
        if str(row.get("exit_order_id") or "").strip():
            return super()._exit_round(row, signal_ms)
        attempt_id = self._begin_attempt(
            row,
            action="SELL",
            depth=self._last_exit_depth_snapshot,
        )
        super()._exit_round(row, signal_ms)
        refreshed = self._round_state(int(row["id"])) or row
        state = str(refreshed.get("state") or "")
        if state == "EXIT_SYNC":
            outcome = "SUBMITTED"
        elif state == "AMBIGUOUS":
            outcome = "AMBIGUOUS"
        elif state == "OPEN":
            outcome = str(refreshed.get("error_kind") or "RETURNED_OPEN")
        else:
            outcome = state or "UNKNOWN"
        self._finish_attempt(
            attempt_id,
            refreshed,
            outcome=outcome,
            message=str(refreshed.get("error_message") or "") or None,
        )

    def _sync_entry(self, row: dict[str, Any]) -> None:
        super()._sync_entry(row)
        refreshed = self._round_state(int(row["id"])) or row
        if str(refreshed.get("error_kind") or "") == "ENTRY_ORDER_NOT_FILLED":
            self._entry_no_fills += 1
            attempt_id = self._latest_attempt_id(int(row["id"]), "BUY")
            if attempt_id is not None:
                status = None
                reconcile = self.last_entry_order_reconcile or {}
                if int(reconcile.get("roundId") or -1) == int(row["id"]):
                    status = str(reconcile.get("status") or "") or None
                self._finish_attempt(
                    attempt_id,
                    refreshed,
                    outcome="NO_FILL",
                    order_status=status,
                    message=str(refreshed.get("error_message") or "") or None,
                )

    def _sync_exit(self, row: dict[str, Any]) -> None:
        before_state = str(row.get("state") or "")
        super()._sync_exit(row)
        refreshed = self._round_state(int(row["id"])) or row
        error_kind = str(refreshed.get("error_kind") or "")
        attempt_id = self._latest_attempt_id(int(row["id"]), "SELL")
        if before_state == "EXIT_SYNC" and error_kind == "EXIT_ORDER_NOT_FILLED":
            self._exit_no_fills += 1
            status = None
            reconcile = self.last_exit_order_reconcile or {}
            if int(reconcile.get("roundId") or -1) == int(row["id"]):
                status = str(reconcile.get("status") or "") or None
            if attempt_id is not None:
                self._finish_attempt(
                    attempt_id,
                    refreshed,
                    outcome="NO_FILL",
                    order_status=status,
                    message=str(refreshed.get("error_message") or "") or None,
                )
        elif attempt_id is not None and str(refreshed.get("state") or "") == "CLOSED":
            self._finish_attempt(
                attempt_id,
                refreshed,
                outcome="FILLED",
                order_status=str((self.last_exit_order_reconcile or {}).get("status") or "") or None,
                message=str(refreshed.get("close_reason") or "") or None,
            )

    def _fast_exit_retry_if_needed(self) -> bool:
        active = self._current_active_round()
        if not isinstance(active, dict) or str(active.get("state") or "") != "OPEN":
            return False
        if str(active.get("error_kind") or "") != "EXIT_ORDER_NOT_FILLED":
            return False
        if self._exit_intent(int(active["id"])) != "POLY_DIRECTION_FLIP":
            return False

        now_ms = base._now_ms()
        updated_ms = int(active.get("updated_at_ms") or 0)
        remaining = EXIT_NO_FILL_RETRY_COOLDOWN_MS - max(0, now_ms - updated_ms)
        if remaining > 0:
            self.status = "EXIT_NO_FILL_RETRY_COOLDOWN"
            self.last_error = f"confirmed reversal SELL no-fill; execution retry in {remaining}ms"
            return True

        market = self._prime_market()
        if (
            not isinstance(market, dict)
            or int(market.get("market_id") or 0) != int(active["market_id"])
            or now_ms >= int(market.get("end_ms") or 0)
        ):
            return False

        poly = self._poly_state()
        if not isinstance(poly, dict):
            self.status = "EXIT_NO_FILL_WAITING_FRESH_POLY"
            self.last_error = "confirmed reversal SELL no-fill; waiting for fresh Poly before execution retry"
            return True
        direction = str(poly.get("direction") or "")
        held_side = str(active.get("side") or "")
        if direction == held_side:
            self._set_exit_intent(int(active["id"]), "")
            self._update_round(int(active["id"]), error_kind=None, error_message=None)
            self._event(
                "INFO",
                "EXIT_NO_FILL_FAST_RETRY_CANCELLED",
                int(active["market_id"]),
                int(active["id"]),
                "Poly reverted to the held side after a SELL no-fill; fast execution retry cancelled",
            )
            return False
        if direction not in {"UP", "DOWN"}:
            self.status = "EXIT_NO_FILL_WAITING_OPPOSITE_POLY"
            self.last_error = "confirmed reversal SELL no-fill; Poly is neutral, waiting before retry"
            return True

        retry_key = (int(active["id"]), updated_ms)
        if self._last_fast_retry_key != retry_key:
            self._last_fast_retry_key = retry_key
            self._fast_exit_retries += 1
            self._event(
                "WARN",
                "EXIT_NO_FILL_FAST_RETRY",
                int(active["market_id"]),
                int(active["id"]),
                (
                    f"previous reversal SELL FOK had no fill; Poly still opposes held {held_side}; "
                    f"retrying execution after {EXIT_NO_FILL_RETRY_COOLDOWN_MS}ms without repeating "
                    "the V12 500ms/3-sample signal debounce"
                ),
            )
        signal_ms = int(active.get("exit_signal_at_ms") or now_ms)
        self._exit_round(active, signal_ms)
        return True

    def _tick(self) -> None:
        self._entry_depth_blocked_this_tick = False
        if self._fast_exit_retry_if_needed():
            return
        result = super()._tick()
        if self._entry_depth_blocked_this_tick and self._current_active_round() is None:
            depth = self._last_entry_depth_preflight or {}
            self.status = "BLOCKED_ENTRY_FOK_DEPTH"
            self.last_error = (
                f"FOK depth {base._finite(depth.get('visibleNotionalUsdt')) or 0:.4f} USDT "
                f"< buffered requirement {base._finite(depth.get('requiredBufferedNotionalUsdt')) or 0:.4f} USDT"
            )
        return result

    def _latest_attempt(self, action: str) -> dict[str, Any] | None:
        with self.db_lock:
            row = self.db.execute(
                """SELECT * FROM poly_gap_live_execution_attempts
                    WHERE action=? ORDER BY id DESC LIMIT 1""",
                (str(action),),
            ).fetchone()
        return dict(row) if row else None

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V32"
        payload["executionDepthV32"] = {
            "buyFokDepthPreflightEnabled": True,
            "buyDepthBufferRatio": ENTRY_DEPTH_BUFFER_RATIO,
            "buyDepthAddsExtraRestRequest": False,
            "lastBuyDepth": dict(self._last_entry_depth_preflight or {}),
            "entryDepthBlocks": int(self._entry_depth_blocks),
            "entryNoFillsObserved": int(self._entry_no_fills),
            "sellDepthBlocksExit": False,
            "sellDepthToleranceBps": EXIT_DEPTH_TOLERANCE_BPS,
            "lastSellDepth": dict(self._last_exit_depth_snapshot or {}),
            "exitNoFillsObserved": int(self._exit_no_fills),
            "exitNoFillRetryCooldownMs": EXIT_NO_FILL_RETRY_COOLDOWN_MS,
            "fastExitRetries": int(self._fast_exit_retries),
            "confirmedReversalNoFillRepeatsSignalDebounce": False,
            "lastBuyAttempt": self._latest_attempt("BUY"),
            "lastSellAttempt": self._latest_attempt("SELL"),
        }
        rules = payload.setdefault("rules", {})
        rules.update(
            {
                "entryFokDepthBufferRatio": ENTRY_DEPTH_BUFFER_RATIO,
                "entryFokDepthUsesExistingDirectBook": True,
                "sellDepthDiagnosticOnly": True,
                "sellNoFillExecutionRetryMs": EXIT_NO_FILL_RETRY_COOLDOWN_MS,
                "sellNoFillRequiresFreshOppositePoly": True,
                "sellNoFillReDebounceRequired": False,
            }
        )
        return payload


base.PolyGapLiveEngine = FokDepthAndExitRetryPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
