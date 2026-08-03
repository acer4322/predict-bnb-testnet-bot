from __future__ import annotations

import json
import math
import time
from functools import wraps
from typing import Any

from . import live_trading as _live


DECISION_SNAPSHOT_VERSION = "DECISION_SNAPSHOT_V1"
_DECISION_SNAPSHOT_STATUSES = {
    "BLOCKED_DRAWDOWN_CONTROL",
    "BLOCKED_STRATEGY_OBSERVER",
    "BLOCKED_FUTURES_LEAD_OBSERVER",
}
_DECISION_SNAPSHOT_KEYS = (
    "decisionSnapshotVersion",
    "decisionSnapshotStage",
    "decisionCapturedAt",
    "decisionCaptureStatus",
    "decisionSnapshotLookupMs",
    "decisionBookBlockStatus",
    "decisionBookErrorKind",
    "decisionBookMessage",
    "decisionPriceSource",
    "decisionLatestAsk",
    "decisionAskDelta",
    "decisionAskAbsDelta",
    "decisionMarketMatchesSignal",
    "decisionCurrentMarketMatchesSignal",
    "eventToDecisionSnapshotMs",
    "signalMarketId",
    "currentMarketId",
    "signalSide",
    "signalPrice",
    "signalAsk",
    "signalAskSize",
    "signalBookAgeMs",
    "latestLocalAsk",
    "latestLocalAskSize",
    "latestLocalBookAgeMs",
    "latestMarketId",
    "orientation",
    "bookSource",
    "freshnessBasis",
    "drawdownSignalSpotPrice",
    "drawdownSignalSpotAgeMs",
    "drawdownSignalSpotSource",
    "drawdownReferencePrice",
    "drawdownReferenceAgeMs",
    "drawdownReferenceSource",
    "drawdownTradeAgeMs",
    "drawdownBookAgeMs",
)


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _drawdown_diagnostics(signal: dict[str, Any]) -> dict[str, Any]:
    return {
        "drawdownSignalSpotPrice": _finite_float(
            signal.get("drawdown_signal_spot_price")
        ),
        "drawdownSignalSpotAgeMs": _finite_float(
            signal.get("drawdown_signal_spot_age_ms")
        ),
        "drawdownSignalSpotSource": signal.get("drawdown_signal_spot_source"),
        "drawdownReferencePrice": _finite_float(
            signal.get("drawdown_recheck_spot_price")
        ),
        "drawdownReferenceAgeMs": _finite_float(
            signal.get("drawdown_recheck_spot_age_ms")
        ),
        "drawdownReferenceSource": signal.get("drawdown_recheck_spot_source"),
        "drawdownTradeAgeMs": _finite_float(
            signal.get("drawdown_recheck_trade_age_ms")
        ),
        "drawdownBookAgeMs": _finite_float(
            signal.get("drawdown_recheck_book_age_ms")
        ),
    }


def build_blocked_decision_snapshot(
    engine: Any,
    signal: dict[str, Any],
    status: str,
) -> dict[str, Any] | None:
    """Capture a read-only current Prediction book at an early block decision.

    This helper does not alter a gate decision, request a signed quote, or place an
    order.  It only reuses the engine's existing fail-closed verified-book reader.
    """
    normalized_status = str(status or "").strip().upper()
    if normalized_status not in _DECISION_SNAPSHOT_STATUSES:
        return None

    capture_started = time.monotonic()
    signal_market_id = _integer(signal.get("market_id"))
    side = str(signal.get("side") or "").strip().upper()
    signal_price = _finite_float(signal.get("entry_price"))
    signal_ask = _finite_float(signal.get("signal_prediction_ask"))
    if signal_ask is None:
        signal_ask = signal_price

    try:
        reference = engine.current_market() or {}
    except Exception:
        reference = {}
    current_market_id = _integer(reference.get("market_id"))

    diagnostics: dict[str, Any] = {}
    checked_book: dict[str, Any] | None = None
    book_status: str | None = None
    book_error_kind: str | None = None
    book_message: str | None = None

    if signal_market_id is not None and side in {"UP", "DOWN"}:
        try:
            (
                checked_book,
                book_status,
                book_error_kind,
                book_message,
                raw_diagnostics,
            ) = engine._latest_prediction_book_check(
                market_id=signal_market_id,
                side=side,
            )
            if isinstance(raw_diagnostics, dict):
                diagnostics.update(raw_diagnostics)
        except Exception as exc:  # diagnostics must never disturb the block path
            book_status = "DIAGNOSTIC_LOOKUP_FAILED"
            book_error_kind = "DECISION_SNAPSHOT_LOOKUP_FAILED"
            book_message = str(exc)[:200]
    else:
        book_status = "DIAGNOSTIC_INPUT_INVALID"
        book_error_kind = "DECISION_SNAPSHOT_INPUT_INVALID"
        book_message = "signal market_id or side is invalid"

    latest_ask = None
    if isinstance(checked_book, dict):
        latest_ask = _finite_float(checked_book.get("latest_ask"))
    if latest_ask is None:
        latest_ask = _finite_float(diagnostics.get("latestLocalAsk"))

    comparison_price = signal_ask if signal_ask is not None else signal_price
    delta = (
        latest_ask - comparison_price
        if latest_ask is not None and comparison_price is not None
        else None
    )
    latest_market_id = _integer(diagnostics.get("latestMarketId"))

    event_to_snapshot_ms = None
    try:
        event_received = engine._signal_monotonic_seconds(
            signal, "market_event_received_monotonic_ns"
        )
        event_to_snapshot_ms = engine._elapsed_ms(
            event_received, time.monotonic()
        )
    except Exception:
        event_to_snapshot_ms = None

    diagnostics.update(
        {
            "decisionSnapshotVersion": DECISION_SNAPSHOT_VERSION,
            "decisionSnapshotStage": "PRE_LEDGER_EARLY_BLOCK",
            "decisionCapturedAt": _live.utc_iso(),
            "decisionCaptureStatus": (
                "AVAILABLE" if latest_ask is not None else "UNAVAILABLE"
            ),
            "decisionSnapshotLookupMs": max(
                0.0, (time.monotonic() - capture_started) * 1000.0
            ),
            "decisionBookBlockStatus": book_status,
            "decisionBookErrorKind": book_error_kind,
            "decisionBookMessage": book_message,
            "decisionPriceSource": (
                "CURRENT_VERIFIED_PREDICTION_BOOK"
                if latest_ask is not None
                else "UNAVAILABLE"
            ),
            "decisionLatestAsk": latest_ask,
            "decisionAskDelta": delta,
            "decisionAskAbsDelta": abs(delta) if delta is not None else None,
            "decisionMarketMatchesSignal": (
                latest_market_id == signal_market_id
                if latest_market_id is not None and signal_market_id is not None
                else None
            ),
            "decisionCurrentMarketMatchesSignal": (
                current_market_id == signal_market_id
                if current_market_id is not None and signal_market_id is not None
                else None
            ),
            "eventToDecisionSnapshotMs": event_to_snapshot_ms,
            "signalMarketId": signal_market_id,
            "currentMarketId": current_market_id,
            "signalSide": side or None,
            "signalPrice": signal_price,
            "signalAsk": signal_ask,
            "signalAskSize": _finite_float(
                signal.get("signal_prediction_ask_size")
            ),
            "signalBookAgeMs": _finite_float(
                signal.get("signal_prediction_book_age_ms")
            ),
        }
    )
    return diagnostics


def decision_snapshot_from_payload(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    if not isinstance(payload, dict):
        return None
    if payload.get("decisionSnapshotVersion") != DECISION_SNAPSHOT_VERSION:
        return None
    return {
        key: payload.get(key)
        for key in _DECISION_SNAPSHOT_KEYS
        if key in payload
    }


def install_decision_snapshot_diagnostics() -> None:
    """Install idempotent, read-only diagnostics on live blocked decisions."""
    engine_cls = _live.LiveM0WEngine
    ledger_cls = _live.LiveLedger

    original_record = engine_cls._record_blocked_signal
    if not getattr(original_record, "_decision_snapshot_diagnostics", False):

        @wraps(original_record)
        def record_with_snapshot(
            self: Any,
            signal: dict[str, Any],
            status: str,
            message: str,
            *,
            error_kind: str = "LOCAL_BLOCK",
            diagnostics: dict[str, Any] | None = None,
        ) -> int | None:
            normalized_status = str(status or "").strip().upper()
            merged = dict(diagnostics) if isinstance(diagnostics, dict) else {}
            if normalized_status == "BLOCKED_DRAWDOWN_CONTROL":
                for key, value in _drawdown_diagnostics(signal).items():
                    merged.setdefault(key, value)
            snapshot = build_blocked_decision_snapshot(
                self, signal, normalized_status
            )
            if snapshot is not None:
                merged = {**snapshot, **merged}
            return original_record(
                self,
                signal,
                status,
                message,
                error_kind=error_kind,
                diagnostics=(merged or None),
            )

        record_with_snapshot._decision_snapshot_diagnostics = True  # type: ignore[attr-defined]
        engine_cls._record_blocked_signal = record_with_snapshot

    original_recent_orders = ledger_cls.recent_orders
    if not getattr(original_recent_orders, "_decision_snapshot_diagnostics", False):

        @wraps(original_recent_orders)
        def recent_orders_with_snapshot(
            self: Any, limit: int = 100
        ) -> list[dict[str, Any]]:
            items = original_recent_orders(self, limit)
            ids = [
                int(item["id"])
                for item in items
                if item.get("id") is not None
            ]
            if not ids:
                return items
            placeholders = ",".join("?" for _ in ids)
            try:
                with self.lock:
                    rows = self.db.execute(
                        f"SELECT id, response_json FROM live_orders "
                        f"WHERE id IN ({placeholders})",
                        tuple(ids),
                    ).fetchall()
            except Exception:
                return items
            snapshots = {
                int(row["id"]): decision_snapshot_from_payload(
                    row["response_json"]
                )
                for row in rows
            }
            for item in items:
                snapshot = snapshots.get(int(item["id"]))
                if snapshot is not None:
                    item["decision_snapshot"] = snapshot
            return items

        recent_orders_with_snapshot._decision_snapshot_diagnostics = True  # type: ignore[attr-defined]
        ledger_cls.recent_orders = recent_orders_with_snapshot
