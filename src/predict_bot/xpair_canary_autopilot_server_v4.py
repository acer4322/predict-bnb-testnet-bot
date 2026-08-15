from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import xpair_canary_autopilot_server as base
from . import xpair_canary_autopilot_server_v2 as v2
from . import xpair_canary_autopilot_server_v3 as v3
from .core import BinancePredictionTradingClient
from .xpair_btc_eth_canary import (
    CanaryPlan,
    CanaryStore,
    _quote_details,
    place_pair,
)
from .xpair_btc_eth_paper import MarketRef, utc_iso

INCIDENT_CLEAR_VALUE = "I_RECONCILED_XPAIR_INCIDENT"
TRACKING_TIMEOUT_SECONDS = 45.0
FILL_MISMATCH_GRACE_SECONDS = 1.0
TERMINAL_NO_FILL_STATUSES = {
    "REJECTED",
    "CANCELED",
    "CANCELLED",
    "EXPIRED",
    "FAILED",
}


class SafetyLedger:
    """Single-row durable safety lock for the non-atomic XPAIR executor."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS xpair_live_safety(
                       id INTEGER PRIMARY KEY CHECK(id=1),
                       locked INTEGER NOT NULL DEFAULT 0,
                       lock_kind TEXT NOT NULL DEFAULT 'CLEAR',
                       status TEXT NOT NULL DEFAULT 'CLEAR',
                       reason TEXT,
                       market_key TEXT,
                       run_id INTEGER,
                       btc_order_id TEXT,
                       eth_order_id TEXT,
                       btc_status TEXT,
                       eth_status TEXT,
                       market_end_ms INTEGER,
                       created_at TEXT,
                       updated_at TEXT NOT NULL,
                       resolved_at TEXT
                   )"""
            )
            db.execute(
                """INSERT OR IGNORE INTO xpair_live_safety(
                       id, locked, lock_kind, status, updated_at
                   ) VALUES (1, 0, 'CLEAR', 'CLEAR', ?)""",
                (utc_iso(),),
            )
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def _row(self) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM xpair_live_safety WHERE id=1"
            ).fetchone()
        return dict(row) if row is not None else {}

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            row = self._row()
        return {
            "locked": bool(row.get("locked")),
            "lockKind": row.get("lock_kind") or "CLEAR",
            "status": row.get("status") or "CLEAR",
            "reason": row.get("reason"),
            "marketKey": row.get("market_key"),
            "runId": row.get("run_id"),
            "btcOrderId": row.get("btc_order_id"),
            "ethOrderId": row.get("eth_order_id"),
            "btcStatus": row.get("btc_status"),
            "ethStatus": row.get("eth_status"),
            "marketEndMs": row.get("market_end_ms"),
            "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"),
            "resolvedAt": row.get("resolved_at"),
            "manualClearPhrase": INCIDENT_CLEAR_VALUE,
        }

    def begin_tracking(
        self,
        *,
        market_key: str,
        run_id: int,
        btc_order_id: str,
        eth_order_id: str,
        market_end_ms: int,
    ) -> None:
        now = utc_iso()
        with self.lock, self._connect() as db:
            db.execute(
                """UPDATE xpair_live_safety SET
                       locked=1, lock_kind='TRACKING', status='SUBMITTED_TRACKING',
                       reason='both placement calls returned order IDs; monitoring until terminal',
                       market_key=?, run_id=?, btc_order_id=?, eth_order_id=?,
                       btc_status='SUBMITTED', eth_status='SUBMITTED',
                       market_end_ms=?, created_at=?, updated_at=?, resolved_at=NULL
                     WHERE id=1""",
                (
                    market_key,
                    int(run_id),
                    btc_order_id,
                    eth_order_id,
                    int(market_end_ms),
                    now,
                    now,
                ),
            )
            db.commit()

    def activate_incident(
        self,
        *,
        status: str,
        reason: str,
        market_key: str | None,
        run_id: int | None,
        btc_order_id: str | None = None,
        eth_order_id: str | None = None,
        btc_status: str | None = None,
        eth_status: str | None = None,
        market_end_ms: int | None = None,
    ) -> None:
        now = utc_iso()
        prior = self._row()
        created_at = prior.get("created_at") or now
        with self.lock, self._connect() as db:
            db.execute(
                """UPDATE xpair_live_safety SET
                       locked=1, lock_kind='INCIDENT', status=?, reason=?,
                       market_key=COALESCE(?, market_key),
                       run_id=COALESCE(?, run_id),
                       btc_order_id=COALESCE(?, btc_order_id),
                       eth_order_id=COALESCE(?, eth_order_id),
                       btc_status=COALESCE(?, btc_status),
                       eth_status=COALESCE(?, eth_status),
                       market_end_ms=COALESCE(?, market_end_ms),
                       created_at=?, updated_at=?, resolved_at=NULL
                     WHERE id=1""",
                (
                    status[:80],
                    reason[:1000],
                    market_key,
                    run_id,
                    btc_order_id,
                    eth_order_id,
                    btc_status,
                    eth_status,
                    market_end_ms,
                    created_at,
                    now,
                ),
            )
            db.commit()

    def update_tracking(
        self,
        *,
        btc_status: str,
        eth_status: str,
        status: str = "SUBMITTED_TRACKING",
        reason: str | None = None,
    ) -> None:
        with self.lock, self._connect() as db:
            db.execute(
                """UPDATE xpair_live_safety SET
                       status=?, reason=COALESCE(?, reason),
                       btc_status=?, eth_status=?, updated_at=?
                     WHERE id=1""",
                (status, reason, btc_status, eth_status, utc_iso()),
            )
            db.commit()

    def resolve_automatically(self, status: str, reason: str) -> None:
        now = utc_iso()
        with self.lock, self._connect() as db:
            db.execute(
                """UPDATE xpair_live_safety SET
                       locked=0, lock_kind='CLEAR', status=?, reason=?,
                       updated_at=?, resolved_at=?
                     WHERE id=1""",
                (status[:80], reason[:1000], now, now),
            )
            db.commit()

    def clear_by_operator(self, note: str = "") -> None:
        current = self.snapshot()
        if current["lockKind"] == "TRACKING":
            raise RuntimeError(
                "XPAIR orders are still under automatic tracking; wait for a terminal "
                "result or an incident before clearing"
            )
        now = utc_iso()
        reason = "operator confirmed manual reconciliation"
        if note.strip():
            reason += f": {note.strip()[:300]}"
        with self.lock, self._connect() as db:
            db.execute(
                """UPDATE xpair_live_safety SET
                       locked=0, lock_kind='CLEAR', status='OPERATOR_CLEARED',
                       reason=?, updated_at=?, resolved_at=?
                     WHERE id=1""",
                (reason, now, now),
            )
            db.commit()


SAFETY = SafetyLedger(base.DB_PATH)


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _status(order: dict[str, Any] | None) -> str:
    return str((order or {}).get("status") or "NOT_FOUND").upper()


def _numeric(order: dict[str, Any], *keys: str) -> float:
    for key in keys:
        try:
            value = float(order.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def _has_fill(order: dict[str, Any] | None) -> bool:
    if not isinstance(order, dict):
        return False
    if _status(order) in {"FILLED", "PARTIAL", "PARTIALLY_FILLED"}:
        return True
    return _numeric(
        order,
        "filledShareQty",
        "filledQuantity",
        "executedQty",
        "filledUsdtAmount",
    ) > 0


def classify_pair_orders(
    *,
    btc_order: dict[str, Any] | None,
    eth_order: dict[str, Any] | None,
    tracking_age_seconds: float,
    market_ended: bool,
) -> tuple[str, str, str]:
    """Return action, status and reason for a submitted XPAIR order pair."""
    btc_status = _status(btc_order)
    eth_status = _status(eth_order)
    btc_fill = _has_fill(btc_order)
    eth_fill = _has_fill(eth_order)

    if btc_status == "FILLED" and eth_status == "FILLED":
        return "RESOLVE", "FILLED_BOTH", "both XPAIR orders are fully filled"

    if btc_fill != eth_fill and tracking_age_seconds >= FILL_MISMATCH_GRACE_SECONDS:
        filled_symbol = "BTC" if btc_fill else "ETH"
        missing_symbol = "ETH" if btc_fill else "BTC"
        return (
            "INCIDENT",
            "ONE_SIDED_FILL_LOCK",
            f"{filled_symbol} has a fill while {missing_symbol} does not "
            f"(BTC={btc_status}, ETH={eth_status})",
        )

    if btc_fill and eth_fill:
        btc_qty = _numeric(
            btc_order or {}, "filledShareQty", "filledQuantity", "executedQty"
        )
        eth_qty = _numeric(
            eth_order or {}, "filledShareQty", "filledQuantity", "executedQty"
        )
        if btc_qty > 0 and eth_qty > 0:
            mismatch = abs(btc_qty - eth_qty) / min(btc_qty, eth_qty)
            if mismatch > 0.0025 and tracking_age_seconds >= FILL_MISMATCH_GRACE_SECONDS:
                return (
                    "INCIDENT",
                    "FILL_SHARE_MISMATCH_LOCK",
                    f"filled-share mismatch {mismatch:.3%} exceeds 0.25% "
                    f"(BTC={btc_qty:.8f}, ETH={eth_qty:.8f})",
                )
        if market_ended:
            return (
                "INCIDENT",
                "MARKET_ENDED_PARTIAL_PAIR_LOCK",
                f"market ended before both orders became fully filled "
                f"(BTC={btc_status}, ETH={eth_status})",
            )
        return "TRACK", "PARTIAL_PAIR_TRACKING", "both legs have partial fills"

    if (
        btc_status in TERMINAL_NO_FILL_STATUSES
        and eth_status in TERMINAL_NO_FILL_STATUSES
        and not btc_fill
        and not eth_fill
    ):
        return (
            "RESOLVE",
            "NO_FILL_BOTH_TERMINAL",
            f"both orders ended without fills (BTC={btc_status}, ETH={eth_status})",
        )

    if market_ended:
        return (
            "INCIDENT",
            "MARKET_ENDED_UNRESOLVED_LOCK",
            f"market ended with unresolved XPAIR orders "
            f"(BTC={btc_status}, ETH={eth_status})",
        )

    if tracking_age_seconds >= TRACKING_TIMEOUT_SECONDS and (
        btc_status == "NOT_FOUND" or eth_status == "NOT_FOUND"
    ):
        return (
            "INCIDENT",
            "ORDER_SYNC_UNRESOLVED_LOCK",
            f"one or both order IDs were not found after "
            f"{TRACKING_TIMEOUT_SECONDS:.0f}s (BTC={btc_status}, ETH={eth_status})",
        )

    return (
        "TRACK",
        "SUBMITTED_TRACKING",
        f"waiting for terminal order states (BTC={btc_status}, ETH={eth_status})",
    )


def _merge_run_details(
    *,
    run_id: int,
    status: str,
    message: str,
    orders: dict[str, dict[str, Any]],
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
            details["orders"] = orders
            db.execute(
                """UPDATE canary_runs SET status=?, details_json=?, message=?, updated_at=?
                     WHERE id=?""",
                (
                    status,
                    json.dumps(details, ensure_ascii=False, sort_keys=True, default=str),
                    message[:500],
                    utc_iso(),
                    int(run_id),
                ),
            )
            db.commit()
    except Exception as exc:
        base.STATE.log(f"SAFETY_LEDGER_RUN_UPDATE_FAILED {str(exc)[:300]}", "ERROR")


def execute_live_attempt(
    *,
    client: BinancePredictionTradingClient,
    store: CanaryStore,
    config: base.MonitorConfig,
    wallet_address: str,
    wallet_id: str,
    btc: MarketRef,
    eth: MarketRef,
    plan: CanaryPlan,
    quotes: list[dict[str, Any]],
    quoted_cost: Any,
    run_id: int,
) -> None:
    existing = SAFETY.snapshot()
    if existing["locked"]:
        base.STATE.disarm("persistent_safety_lock")
        raise RuntimeError(
            f"XPAIR safety lock is active: {existing['status']} — {existing['reason']}"
        )

    key = base.market_key(btc, eth)
    if not base.STATE.consume_arm(key):
        return
    base.STATE.set_phase("PLACE_ATTEMPTED")
    base.STATE.log(
        f"PLACE_ATTEMPTED market={key} variant={plan.variant} "
        f"cost/share={quoted_cost:.6f}",
        "WARN",
    )
    quote_details = _quote_details(plan, quotes)
    store.update(
        run_id,
        status="PLACE_ATTEMPTED",
        quoted_cost=quoted_cost,
        details=quote_details,
    )
    results = base.enrich_placements(
        plan,
        place_pair(
            client,
            wallet_address=wallet_address,
            wallet_id=wallet_id,
            account_type=config.account_type,
            slippage_bps=config.slippage_bps,
            plan=plan,
            quotes=quotes,
        ),
    )
    placement_details = {**quote_details, "placements": results}
    by_symbol = {
        str(item.get("symbol") or ""): item
        for item in results
        if isinstance(item, dict)
    }
    btc_result = by_symbol.get("BTC", {})
    eth_result = by_symbol.get("ETH", {})
    btc_order_id = str(btc_result.get("orderId") or "") or None
    eth_order_id = str(eth_result.get("orderId") or "") or None

    if not all(item.get("ok") for item in results):
        reason = "one or both placement responses were rejected or ambiguous"
        SAFETY.activate_incident(
            status="PLACEMENT_INCOMPLETE_MANUAL_RECONCILE",
            reason=reason,
            market_key=key,
            run_id=run_id,
            btc_order_id=btc_order_id,
            eth_order_id=eth_order_id,
            btc_status=str(btc_result.get("status") or "UNKNOWN").upper(),
            eth_status=str(eth_result.get("status") or "UNKNOWN").upper(),
            market_end_ms=min(btc.end_ms, eth.end_ms),
        )
        store.update(
            run_id,
            status="PLACEMENT_INCOMPLETE_MANUAL_RECONCILE",
            details=placement_details,
            message=reason,
        )
        base.STATE.disarm("placement_incomplete")
        base.STATE.set_phase("XPAIR_INCIDENT_LOCKED")
        base.STATE.log(
            "XPAIR_INCIDENT_LOCKED placement incomplete; manual reconciliation "
            "and explicit incident clear are required",
            "ERROR",
        )
        return

    assert btc_order_id is not None and eth_order_id is not None
    SAFETY.begin_tracking(
        market_key=key,
        run_id=run_id,
        btc_order_id=btc_order_id,
        eth_order_id=eth_order_id,
        market_end_ms=min(btc.end_ms, eth.end_ms),
    )
    store.update(
        run_id,
        status="SUBMITTED_TRACKING",
        details=placement_details,
        message="both order IDs received; durable background reconciliation active",
    )
    base.STATE.set_phase("LIVE_ORDER_TRACKING")
    base.STATE.log(
        f"SUBMITTED_TRACKING BTC={btc_order_id} ETH={eth_order_id}; "
        "new live arms are locked until reconciliation completes",
        "WARN",
    )


def order_watch_loop() -> None:
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        base.STATE.log("ORDER_WATCH_DISABLED missing Binance credentials", "ERROR")
        return
    client = BinancePredictionTradingClient(api_key, api_secret)
    wallet_address = ""
    try:
        wallets = client.wallets().get("wallets") or []
        if len(wallets) == 1:
            wallet_address = str(wallets[0].get("walletAddress") or "")
        if not wallet_address:
            base.STATE.log("ORDER_WATCH_DISABLED Prediction wallet unavailable", "ERROR")
            return

        while True:
            snapshot = SAFETY.snapshot()
            if not snapshot["locked"]:
                time.sleep(1.0)
                continue
            if snapshot["lockKind"] != "TRACKING":
                time.sleep(1.0)
                continue

            btc_order_id = str(snapshot.get("btcOrderId") or "")
            eth_order_id = str(snapshot.get("ethOrderId") or "")
            if not btc_order_id or not eth_order_id:
                SAFETY.activate_incident(
                    status="ORDER_IDENTIFIERS_MISSING_LOCK",
                    reason="tracking row is missing one or both exchange order IDs",
                    market_key=snapshot.get("marketKey"),
                    run_id=snapshot.get("runId"),
                )
                base.STATE.set_phase("XPAIR_INCIDENT_LOCKED")
                base.STATE.log("XPAIR_INCIDENT_LOCKED missing order identifiers", "ERROR")
                continue

            try:
                orders = client.order_history(wallet_address, limit=100).get("orders") or []
                found = {
                    str(item.get("orderId") or ""): dict(item)
                    for item in orders
                    if str(item.get("orderId") or "") in {btc_order_id, eth_order_id}
                }
            except Exception as exc:
                base.STATE.log(f"ORDER_WATCH_ERROR {str(exc)[:300]}", "ERROR")
                time.sleep(2.0)
                continue

            btc_order = found.get(btc_order_id)
            eth_order = found.get(eth_order_id)
            created_at = _parse_time(snapshot.get("createdAt"))
            age_seconds = (
                max(0.0, (datetime.now(timezone.utc) - created_at).total_seconds())
                if created_at is not None
                else TRACKING_TIMEOUT_SECONDS
            )
            market_end_ms = int(snapshot.get("marketEndMs") or 0)
            market_ended = market_end_ms > 0 and client.server_timestamp_ms() >= market_end_ms
            action, status, reason = classify_pair_orders(
                btc_order=btc_order,
                eth_order=eth_order,
                tracking_age_seconds=age_seconds,
                market_ended=market_ended,
            )
            btc_status = _status(btc_order)
            eth_status = _status(eth_order)
            run_id = int(snapshot.get("runId") or 0)
            order_payload = {
                btc_order_id: btc_order or {"status": "NOT_FOUND"},
                eth_order_id: eth_order or {"status": "NOT_FOUND"},
            }

            if action == "RESOLVE":
                SAFETY.update_tracking(
                    btc_status=btc_status,
                    eth_status=eth_status,
                    status=status,
                    reason=reason,
                )
                SAFETY.resolve_automatically(status, reason)
                if run_id > 0:
                    _merge_run_details(
                        run_id=run_id,
                        status=status,
                        message=reason,
                        orders=order_payload,
                    )
                base.STATE.set_phase(status)
                base.STATE.log(f"{status}: {reason}", "WARN")
            elif action == "INCIDENT":
                SAFETY.activate_incident(
                    status=status,
                    reason=reason,
                    market_key=snapshot.get("marketKey"),
                    run_id=run_id or None,
                    btc_order_id=btc_order_id,
                    eth_order_id=eth_order_id,
                    btc_status=btc_status,
                    eth_status=eth_status,
                    market_end_ms=market_end_ms or None,
                )
                if run_id > 0:
                    _merge_run_details(
                        run_id=run_id,
                        status=status,
                        message=reason,
                        orders=order_payload,
                    )
                base.STATE.disarm("xpair_incident")
                base.STATE.set_phase("XPAIR_INCIDENT_LOCKED")
                base.STATE.log(f"XPAIR_INCIDENT_LOCKED {status}: {reason}", "ERROR")
            else:
                SAFETY.update_tracking(
                    btc_status=btc_status,
                    eth_status=eth_status,
                    status=status,
                    reason=reason,
                )
                if run_id > 0:
                    _merge_run_details(
                        run_id=run_id,
                        status=status,
                        message=reason,
                        orders=order_payload,
                    )
                base.STATE.set_phase("LIVE_ORDER_TRACKING")

            time.sleep(1.0)
    finally:
        client.close()


_original_arm = base.AutopilotState.arm
_original_set_phase = base.AutopilotState.set_phase
_original_state_payload = base.state_payload
_PATCHED = False


def safe_arm(self: base.AutopilotState, payload: dict[str, Any]) -> base.MonitorConfig:
    safety = SAFETY.snapshot()
    if safety["locked"]:
        raise RuntimeError(
            f"XPAIR live arm is blocked by {safety['lockKind']} safety lock: "
            f"{safety['status']} — {safety['reason']}"
        )
    return _original_arm(self, payload)


def safe_set_phase(self: base.AutopilotState, phase: str) -> None:
    safety = SAFETY.snapshot()
    if safety["locked"]:
        forced = (
            "XPAIR_INCIDENT_LOCKED"
            if safety["lockKind"] == "INCIDENT"
            else "LIVE_ORDER_TRACKING"
        )
        _original_set_phase(self, forced)
        return
    _original_set_phase(self, phase)


def state_payload() -> dict[str, Any]:
    payload = _original_state_payload()
    payload["safety"] = SAFETY.snapshot()
    payload["policy"].update(
        {
            "persistentIncidentLock": True,
            "restartPreservesIncidentLock": True,
            "continuousOrderReconciliation": True,
            "oneSidedFillHardLock": True,
            "unknownPlacementHardLock": True,
            "manualIncidentClearRequired": True,
            "automaticCancel": False,
            "automaticUnwind": False,
        }
    )
    return payload


def install_safety_patches() -> None:
    global _PATCHED
    if _PATCHED:
        return
    base.AutopilotState.arm = safe_arm
    base.AutopilotState.set_phase = safe_set_phase
    base.execute_live_attempt = execute_live_attempt
    base.state_payload = state_payload
    _PATCHED = True
    safety = SAFETY.snapshot()
    if safety["locked"]:
        base.STATE.disarm("restored_persistent_safety_lock")
        base.STATE.set_phase(
            "XPAIR_INCIDENT_LOCKED"
            if safety["lockKind"] == "INCIDENT"
            else "LIVE_ORDER_TRACKING"
        )
        base.STATE.log(
            f"PERSISTENT_SAFETY_LOCK_RESTORED {safety['status']}: {safety['reason']}",
            "ERROR" if safety["lockKind"] == "INCIDENT" else "WARN",
        )


class Handler(base.Handler):
    server_version = "BTC5MLabXPairAutopilot/4.0"

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path != "/api/xpair-canary/incident/clear":
            super().do_POST()
            return
        if not base.origin_is_allowed(self.headers.get("Origin")):
            self.respond(403, {"error": "origin is not allowed"})
            return
        try:
            payload = self.read_json()
            if self.headers.get("X-BTC-Lab-XPair-Live") != "confirmed":
                raise ValueError("incident clear request is missing confirmation header")
            if str(payload.get("confirmation") or "") != INCIDENT_CLEAR_VALUE:
                raise ValueError("incident clear confirmation phrase is incorrect")
            SAFETY.clear_by_operator(str(payload.get("note") or ""))
            base.STATE.set_phase("MONITORING")
            base.STATE.log("XPAIR_INCIDENT_CLEARED_BY_OPERATOR", "WARN")
            self.respond(200, state_payload())
        except RuntimeError as exc:
            self.respond(409, {"error": str(exc), **state_payload()})
        except (ValueError, ArithmeticError) as exc:
            self.respond(400, {"error": str(exc), **state_payload()})
        except Exception as exc:
            self.respond(500, {"error": str(exc)[:500], **state_payload()})


def main() -> None:
    v3.install_patches()
    install_safety_patches()
    threading.Thread(
        target=v2.monitor_loop,
        name="xpair-autopilot-monitor-v4",
        daemon=True,
    ).start()
    threading.Thread(
        target=order_watch_loop,
        name="xpair-order-watch-v4",
        daemon=True,
    ).start()
    server = base.ThreadingHTTPServer((base.API_HOST, base.API_PORT), Handler)
    print(
        f"XPAIR autopilot v4 API listening on http://{base.API_HOST}:{base.API_PORT}; "
        "signed quotes are monitored continuously, live placement is one-shot, "
        "and non-atomic incidents are persistently locked"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        base.STATE.disarm("server_shutdown")
        server.server_close()


if __name__ == "__main__":
    main()
