from __future__ import annotations

import json
import os
import sqlite3
import threading
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Any, Sequence

from . import xpair_canary_autopilot_server as base
from .xpair_btc_eth_paper import utc_iso

MIN_ONE_WIN_HOLD_PNL_ENV = "XPAIR_MIN_ONE_WIN_HOLD_PNL_USDT"
DEFAULT_MIN_ONE_WIN_HOLD_PNL_USDT = Decimal("0.01")
MIN_SELL_QUOTE_CAPACITY = Decimal("0.95")
MAX_SHARE_MISMATCH = Decimal("0.0025")
MIN_QUOTE_EXPIRY_MS = 1_500
MIN_ORDER_USDT = Decimal("1.00")
RECOVERY_RECONCILE_SECONDS = 15.0
RECOVERY_POSITION_SYNC_ATTEMPTS = 5
RECOVERY_POSITION_SYNC_INTERVAL = 0.5
TERMINAL_NO_FILL = {
    "REJECTED",
    "CANCELED",
    "CANCELLED",
    "EXPIRED",
    "FAILED",
}
ACTIVE_EXIT_STATUSES = {
    "EVALUATING",
    "RECOVERY_BUY_QUOTING",
    "RECOVERY_BUY_PLACEMENT_IN_FLIGHT",
    "RECOVERY_BUY_TRACKING",
    "UNWIND_QUOTING",
    "UNWIND_PLACEMENT_IN_FLIGHT",
    "UNWIND_TRACKING",
}
TERMINAL_EXIT_STATUSES = {
    "HOLD_PROFITABLE_PAIR",
    "UNWOUND_UNPROFITABLE_PAIR",
    "UNWOUND_ONE_SIDED_FILL",
    "EXIT_GUARD_INCIDENT",
}


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _from_wei(value: Any) -> Decimal | None:
    parsed = _decimal(value)
    if parsed is None:
        return None
    return parsed / Decimal(10**18)


def _to_wei(value: Decimal) -> str:
    if value <= 0:
        raise ValueError("amount must be positive")
    return str(
        int(
            (value * Decimal(10**18)).to_integral_value(
                rounding=ROUND_DOWN
            )
        )
    )


def _configured_min_hold_pnl() -> Decimal:
    raw = os.environ.get(
        MIN_ONE_WIN_HOLD_PNL_ENV,
        str(DEFAULT_MIN_ONE_WIN_HOLD_PNL_USDT),
    )
    parsed = _decimal(raw)
    if parsed is None or parsed < 0:
        return DEFAULT_MIN_ONE_WIN_HOLD_PNL_USDT
    return min(parsed, Decimal("10"))


MIN_ONE_WIN_HOLD_PNL_USDT = _configured_min_hold_pnl()


def _status(order: dict[str, Any] | None) -> str:
    return str((order or {}).get("status") or "NOT_FOUND").upper()


def _first_positive(payload: dict[str, Any] | None, keys: Sequence[str]) -> Decimal | None:
    if not isinstance(payload, dict):
        return None
    candidates = [payload]
    for nested_key in ("position", "data", "result"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            candidates.append(nested)
    for item in candidates:
        for key in keys:
            value = _decimal(item.get(key))
            if value is not None and value > 0:
                return value
    return None


def _filled_shares(order: dict[str, Any] | None, quote: dict[str, Any] | None) -> Decimal:
    actual = _first_positive(
        order,
        (
            "filledShareQty",
            "filledQuantity",
            "executedQty",
            "shareQty",
            "quantity",
        ),
    )
    if actual is not None:
        return actual
    quoted = _from_wei((quote or {}).get("amountOut"))
    return quoted if quoted is not None and quoted > 0 else Decimal("0")


def _filled_cash(order: dict[str, Any] | None, quote: dict[str, Any] | None) -> Decimal:
    actual = _first_positive(
        order,
        (
            "filledUsdtAmount",
            "filledAmount",
            "executedAmount",
            "cumulativeQuoteQty",
            "cumQuote",
            "quoteQty",
        ),
    )
    if actual is not None:
        return actual
    quoted = _from_wei((quote or {}).get("amountIn"))
    return quoted if quoted is not None and quoted > 0 else Decimal("0")


def _actual_or_estimated_fee(
    order: dict[str, Any] | None,
    *,
    shares: Decimal,
    cash: Decimal,
    fee_bps: int,
    quote: dict[str, Any] | None,
) -> Decimal:
    actual = _first_positive(
        order,
        ("feeAmount", "fee", "tradeFee", "commission"),
    )
    if actual is not None:
        return actual
    average = _decimal((order or {}).get("averagePrice"))
    if average is None:
        average = _decimal((order or {}).get("avgPrice"))
    if average is None:
        average = _decimal((quote or {}).get("averagePrice"))
    if average is None and shares > 0:
        average = cash / shares
    if average is None or not Decimal("0") < average < Decimal("1"):
        return Decimal("0")
    return (
        shares
        * min(average, Decimal("1") - average)
        * Decimal(int(fee_bps))
        / Decimal(10_000)
    )


def _order_has_fill(order: dict[str, Any] | None) -> bool:
    if not isinstance(order, dict):
        return False
    return _status(order) in {"FILLED", "PARTIAL", "PARTIALLY_FILLED"} or (
        _first_positive(
            order,
            ("filledShareQty", "filledQuantity", "executedQty", "filledUsdtAmount"),
        )
        is not None
    )


class ExitLedger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS xpair_exit_guard(
                       run_id INTEGER PRIMARY KEY,
                       market_key TEXT NOT NULL,
                       trigger TEXT NOT NULL,
                       status TEXT NOT NULL,
                       reason TEXT,
                       one_win_pnl_usdt REAL,
                       total_cost_usdt REAL,
                       minimum_one_win_payout_usdt REAL,
                       recovery_symbol TEXT,
                       recovery_order_id TEXT,
                       btc_sell_order_id TEXT,
                       eth_sell_order_id TEXT,
                       details_json TEXT NOT NULL DEFAULT '{}',
                       created_at TEXT NOT NULL,
                       updated_at TEXT NOT NULL,
                       resolved_at TEXT
                   )"""
            )
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def get(self, run_id: int) -> dict[str, Any] | None:
        with self.lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM xpair_exit_guard WHERE run_id=?",
                (int(run_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    def latest(self) -> dict[str, Any] | None:
        with self.lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM xpair_exit_guard ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row is not None else None

    def claim(self, *, run_id: int, market_key: str, trigger: str) -> bool:
        now = utc_iso()
        with self.lock, self._connect() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO xpair_exit_guard(
                       run_id, market_key, trigger, status, reason,
                       created_at, updated_at
                   ) VALUES (?,?,?,'EVALUATING','post-fill guard claimed',?,?)""",
                (int(run_id), market_key, trigger, now, now),
            )
            db.commit()
        return bool(cursor.rowcount)

    def update(
        self,
        run_id: int,
        *,
        status: str,
        reason: str,
        one_win_pnl: Decimal | None = None,
        total_cost: Decimal | None = None,
        minimum_payout: Decimal | None = None,
        recovery_symbol: str | None = None,
        recovery_order_id: str | None = None,
        btc_sell_order_id: str | None = None,
        eth_sell_order_id: str | None = None,
        details: dict[str, Any] | None = None,
        resolved: bool = False,
    ) -> None:
        now = utc_iso()
        encoded = (
            json.dumps(details, ensure_ascii=False, sort_keys=True, default=str)
            if details is not None
            else None
        )
        with self.lock, self._connect() as db:
            db.execute(
                """UPDATE xpair_exit_guard SET
                       status=?, reason=?,
                       one_win_pnl_usdt=COALESCE(?, one_win_pnl_usdt),
                       total_cost_usdt=COALESCE(?, total_cost_usdt),
                       minimum_one_win_payout_usdt=COALESCE(?, minimum_one_win_payout_usdt),
                       recovery_symbol=COALESCE(?, recovery_symbol),
                       recovery_order_id=COALESCE(?, recovery_order_id),
                       btc_sell_order_id=COALESCE(?, btc_sell_order_id),
                       eth_sell_order_id=COALESCE(?, eth_sell_order_id),
                       details_json=COALESCE(?, details_json),
                       updated_at=?,
                       resolved_at=CASE WHEN ? THEN ? ELSE resolved_at END
                     WHERE run_id=?""",
                (
                    status[:80],
                    reason[:1000],
                    float(one_win_pnl) if one_win_pnl is not None else None,
                    float(total_cost) if total_cost is not None else None,
                    float(minimum_payout) if minimum_payout is not None else None,
                    recovery_symbol,
                    recovery_order_id,
                    btc_sell_order_id,
                    eth_sell_order_id,
                    encoded,
                    now,
                    1 if resolved else 0,
                    now,
                    int(run_id),
                ),
            )
            db.commit()

    def snapshot(self) -> dict[str, Any] | None:
        row = self.latest()
        if row is None:
            return None
        try:
            details = json.loads(str(row.get("details_json") or "{}"))
        except json.JSONDecodeError:
            details = {}
        return {
            "runId": row.get("run_id"),
            "marketKey": row.get("market_key"),
            "trigger": row.get("trigger"),
            "status": row.get("status"),
            "reason": row.get("reason"),
            "oneWinPnlUsdt": row.get("one_win_pnl_usdt"),
            "totalCostUsdt": row.get("total_cost_usdt"),
            "minimumOneWinPayoutUsdt": row.get("minimum_one_win_payout_usdt"),
            "recoverySymbol": row.get("recovery_symbol"),
            "recoveryOrderId": row.get("recovery_order_id"),
            "btcSellOrderId": row.get("btc_sell_order_id"),
            "ethSellOrderId": row.get("eth_sell_order_id"),
            "details": details,
            "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"),
            "resolvedAt": row.get("resolved_at"),
        }


EXIT = ExitLedger(base.DB_PATH)


def _load_run(run_id: int) -> dict[str, Any]:
    with sqlite3.connect(base.DB_PATH, timeout=5.0) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        row = db.execute(
            "SELECT * FROM canary_runs WHERE id=?", (int(run_id),)
        ).fetchone()
    if row is None:
        raise RuntimeError(f"canary run {run_id} was not found")
    result = dict(row)
    try:
        result["details"] = json.loads(str(result.get("details_json") or "{}"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"canary run {run_id} details are invalid JSON") from exc
    return result


def _run_context(run_id: int) -> dict[str, Any]:
    row = _load_run(run_id)
    details = row["details"]
    plan = details.get("plan") or {}
    legs = {
        str(item.get("symbol") or "").upper(): dict(item)
        for item in (plan.get("legs") or [])
        if isinstance(item, dict)
    }
    quotes = {
        str(item.get("symbol") or "").upper(): dict(item)
        for item in (details.get("quotes") or [])
        if isinstance(item, dict)
    }
    if set(legs) != {"BTC", "ETH"}:
        raise RuntimeError("run details do not contain BTC and ETH plan legs")
    if set(quotes) != {"BTC", "ETH"}:
        raise RuntimeError("run details do not contain BTC and ETH signed quotes")
    return {
        "row": row,
        "details": details,
        "plan": plan,
        "legs": legs,
        "quotes": quotes,
    }


def evaluate_one_win_profitability(
    *,
    orders: dict[str, dict[str, Any]],
    context: dict[str, Any],
) -> dict[str, Any]:
    legs = context["legs"]
    quotes = context["quotes"]
    leg_values: dict[str, dict[str, Decimal]] = {}
    for symbol in ("BTC", "ETH"):
        order = orders.get(symbol)
        quote = quotes[symbol]
        shares = _filled_shares(order, quote)
        cash = _filled_cash(order, quote)
        if shares <= 0 or cash <= 0:
            raise RuntimeError(
                f"{symbol} filled shares/cost are unavailable after FILLED status"
            )
        fee_bps = int(legs[symbol].get("fee_bps") or 0)
        fee = _actual_or_estimated_fee(
            order,
            shares=shares,
            cash=cash,
            fee_bps=fee_bps,
            quote=quote,
        )
        leg_values[symbol] = {
            "shares": shares,
            "cash": cash,
            "fee": fee,
            "total": cash + fee,
        }
    minimum_payout = min(
        leg_values["BTC"]["shares"], leg_values["ETH"]["shares"]
    )
    total_cost = leg_values["BTC"]["total"] + leg_values["ETH"]["total"]
    one_win_pnl = minimum_payout - total_cost
    mismatch = (
        abs(leg_values["BTC"]["shares"] - leg_values["ETH"]["shares"])
        / minimum_payout
        if minimum_payout > 0
        else Decimal("1")
    )
    return {
        "oneWinPnl": one_win_pnl,
        "minimumPayout": minimum_payout,
        "totalCost": total_cost,
        "shareMismatch": mismatch,
        "legs": leg_values,
        "hold": (
            one_win_pnl >= MIN_ONE_WIN_HOLD_PNL_USDT
            and mismatch <= MAX_SHARE_MISMATCH
        ),
    }


def _merge_recovery_details(
    run_id: int,
    *,
    status: str,
    message: str,
    recovery: dict[str, Any],
) -> None:
    try:
        with sqlite3.connect(base.DB_PATH, timeout=5.0) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=5000")
            row = db.execute(
                "SELECT details_json FROM canary_runs WHERE id=?", (int(run_id),)
            ).fetchone()
            try:
                details = json.loads(str(row["details_json"] or "{}")) if row else {}
            except json.JSONDecodeError:
                details = {}
            details["recovery"] = recovery
            db.execute(
                """UPDATE canary_runs SET status=?, message=?, details_json=?, updated_at=?
                     WHERE id=?""",
                (
                    status[:80],
                    message[:500],
                    json.dumps(details, ensure_ascii=False, sort_keys=True, default=str),
                    utc_iso(),
                    int(run_id),
                ),
            )
            db.commit()
    except Exception as exc:
        base.STATE.log(
            f"EXIT_GUARD_RUN_UPDATE_FAILED {type(exc).__name__}: {str(exc)[:300]}",
            "ERROR",
        )
