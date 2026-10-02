from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "poly_gap_runtime_decision_v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _source_detail(snapshot: dict[str, Any]) -> dict[str, Any]:
    source = _dict(snapshot.get("sourceFreshnessV41"))
    current = _dict(source.get("current"))
    poly = _dict(snapshot.get("poly"))
    return {
        "policy": current.get("policy"),
        "sourceAgeMs": current.get("sourceAgeMs", poly.get("signalSourceAgeMs")),
        "quoteReceiptAgeMs": current.get(
            "quoteReceiptAgeMs", poly.get("signalQuoteReceiptAgeMs")
        ),
        "transportAgeMs": current.get(
            "transportAgeMs", poly.get("signalTransportAgeMs")
        ),
        "timestampSource": current.get(
            "timestampSource", poly.get("signalFreshnessTimestampSource")
        ),
    }


def build_runtime_decision(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy Vxx statuses into one diagnostic decision envelope.

    This is intentionally observational.  It does not decide whether an order is
    sent; existing V40+ guards remain authoritative.  The normalized envelope
    gives the Dashboard and logs one stable vocabulary while preserving rawStatus.
    """

    status = str(snapshot.get("status") or "UNKNOWN").strip().upper()
    error = _text(snapshot.get("lastError"))
    active = _dict(snapshot.get("activeRound"))
    active_state = str(active.get("state") or "").strip().upper()
    source_detail = _source_detail(snapshot)
    source_policy = str(source_detail.get("policy") or "").upper()

    state = "WAITING"
    source = "SYSTEM"
    code = status or "UNKNOWN"
    retryable = True
    affects_new_buy = False
    affects_sell_exit = False
    detail: dict[str, Any] = {}

    # Active reconciliation/position management outranks stale display labels.
    if active_state == "ENTRY_SYNC":
        state, source, code = "ACTIVE", "EXECUTION", "ENTRY_RECONCILIATION"
    elif active_state == "EXIT_SYNC":
        state, source, code = "ACTIVE", "EXECUTION", "EXIT_RECONCILIATION"
    elif active_state == "OPEN" or status == "MANAGING_POSITION":
        state, source, code = "ACTIVE", "POSITION", "MANAGING_POSITION"
    elif status == "MASTER_DISABLED":
        state, source, code, retryable = "STOPPED", "CONFIG", "MASTER_DISABLED", False
        affects_new_buy = True
    elif status in {"PAUSED", "MAX_LOSS_TRIPPED"}:
        state, source, code = "BLOCKED", "RISK", status
        affects_new_buy = True
    elif status in {"CONFIG_REQUIRED", "BLOCKED_PREFLIGHT"}:
        state, source, code = "UNAVAILABLE", "CONFIG", status
        affects_new_buy = True
    elif status in {"MARKET_HALTED", "HALTED"}:
        state, source, code, retryable = "HALTED", "EXECUTION", "MARKET_HALTED", False
        affects_new_buy = True
    elif status == "MARKET_MISMATCH":
        state, source, code = "BLOCKED", "MARKET_IDENTITY", "MARKET_MISMATCH"
        affects_new_buy = True
    elif status == "DEGRADED":
        state, source, code = "UNAVAILABLE", "DATA", "DEGRADED"
        affects_new_buy = True
    elif source_policy and source_policy != "ALLOW":
        state, source, code = "BLOCKED", "POLY_FEED", "POLY_SOURCE_STALE"
        affects_new_buy = True
        detail.update(source_detail)
    elif "SOURCE_STALE" in status or "QUOTE_SOURCE_STALE" in status:
        state, source, code = "BLOCKED", "POLY_FEED", "POLY_SOURCE_STALE"
        affects_new_buy = True
        detail.update(source_detail)
    elif status.startswith("REVERSAL_REENTRY_CONFIRM") or status in {
        "REVERSAL_REENTRY_BLOCKED_V40",
        "REVERSAL_REENTRY_WAITING_FRESH_CONFIRM_V40",
    }:
        state, source, code = "WAITING", "REVERSAL_CONFIRMATION", "REVERSAL_CONFIRMING"
        affects_new_buy = True
    elif "REVERSAL_REENTRY_BOOK_EDGE_BLOCKED" in status:
        state, source, code = "BLOCKED", "ENTRY_EDGE", "REVERSAL_BOOK_EDGE_BLOCKED"
        affects_new_buy = True
    elif "REVERSAL_REENTRY_SIGNED_EDGE_BLOCKED" in status:
        state, source, code = "BLOCKED", "ENTRY_EDGE", "REVERSAL_SIGNED_EDGE_BLOCKED"
        affects_new_buy = True
    elif status == "BLOCKED_ENTRY_FOK_DEPTH":
        state, source, code = "BLOCKED", "BINANCE_BOOK", "ENTRY_DEPTH_INSUFFICIENT"
        affects_new_buy = True
        detail.update(_dict(_dict(snapshot.get("executionDepthV32")).get("lastBuyDepth")))
    elif status in {"BLOCKED_ENTRY_PRICE", "ENTRY_PRICE_BLOCKED"}:
        state, source, code = "BLOCKED", "ENTRY_PRICE", "ENTRY_PRICE_BLOCKED"
        affects_new_buy = True
    elif status == "WAITING_BINANCE_BOOK":
        depth = _dict(_dict(snapshot.get("executionDepthV32")).get("lastBuyDepth"))
        reason = str(depth.get("reason") or "").upper()
        if reason == "NO_ASK_LEVELS":
            state, source, code = "UNAVAILABLE", "BINANCE_BOOK", "BINANCE_NO_ASK_LEVELS"
        else:
            state, source, code = "WAITING", "BINANCE_BOOK", "WAITING_BINANCE_BOOK"
        affects_new_buy = True
        detail.update(depth)
    elif status == "WAITING_DATA":
        state, source, code = "UNAVAILABLE", "DATA", "WAITING_DATA"
        affects_new_buy = True
    elif status == "ARMED_WAITING_GAP":
        state, source, code = "READY", "STRATEGY", "WAITING_FOR_EDGE"
    elif status.startswith("ENTRY_") and "BLOCK" in status:
        state, source, code = "BLOCKED", "ENTRY_POLICY", status
        affects_new_buy = True
    elif status.startswith("EXIT_"):
        state, source, code = "ACTIVE", "EXIT_EXECUTION", status
    else:
        state, source, code = "WAITING", "SYSTEM", status or "UNKNOWN"

    if not detail and source == "POLY_FEED":
        detail.update(source_detail)

    return {
        "schemaVersion": SCHEMA_VERSION,
        "state": state,
        "source": source,
        "code": code,
        "rawStatus": status,
        "reason": error,
        "retryable": retryable,
        "affectsNewBuy": affects_new_buy,
        "affectsSellExit": affects_sell_exit,
        "activeRoundId": active.get("id"),
        "activeRoundState": active_state or None,
        "detail": detail,
    }
