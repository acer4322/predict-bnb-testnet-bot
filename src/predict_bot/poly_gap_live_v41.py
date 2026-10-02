from __future__ import annotations

import os
import threading
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v40 import ImmediateExitCautiousReentryPolyGapLiveEngine


MAX_POLY_SIGNAL_SOURCE_AGE_MS = max(
    100.0,
    float(os.environ.get("PREDICT_POLY_GAP_LIVE_MAX_SOURCE_AGE_MS", "750")),
)
MAX_POLY_SIGNAL_QUOTE_RECEIPT_AGE_MS = max(
    100.0,
    float(
        os.environ.get(
            "PREDICT_POLY_GAP_LIVE_MAX_QUOTE_RECEIPT_AGE_MS",
            str(base.MAX_POLY_AGE_MS),
        )
    ),
)
POLY_SOURCE_FUTURE_TOLERANCE_MS = max(
    0.0,
    float(os.environ.get("PREDICT_POLY_GAP_LIVE_SOURCE_FUTURE_TOLERANCE_MS", "250")),
)
QUOTE_EVENT_TYPES = {"book", "price_change", "best_bid_ask"}


class _PolySourceFreshnessBlocked(RuntimeError):
    pass


def source_freshness_policy(
    *,
    source_age_ms: int | float | None,
    quote_receipt_age_ms: int | float | None,
    max_source_age_ms: int | float = MAX_POLY_SIGNAL_SOURCE_AGE_MS,
    max_quote_receipt_age_ms: int | float = MAX_POLY_SIGNAL_QUOTE_RECEIPT_AGE_MS,
    future_tolerance_ms: int | float = POLY_SOURCE_FUTURE_TOLERANCE_MS,
) -> str:
    source_age = base._finite(source_age_ms)
    receipt_age = base._finite(quote_receipt_age_ms)
    if source_age is None or receipt_age is None:
        return "BLOCK_UNAVAILABLE"
    tolerance = max(0.0, float(future_tolerance_ms))
    if source_age < -tolerance or receipt_age < -tolerance:
        return "BLOCK_CLOCK_SKEW"
    if source_age > max(0.0, float(max_source_age_ms)):
        return "BLOCK_SOURCE_STALE"
    if receipt_age > max(0.0, float(max_quote_receipt_age_ms)):
        return "BLOCK_QUOTE_RECEIPT_STALE"
    return "ALLOW"


class PolySourceFreshnessGuardPolyGapLiveEngine(
    ImmediateExitCautiousReentryPolyGapLiveEngine
):
    """V41: reject stale-source BUYs without weakening V40 reversal exits.

    V40 remains authoritative for position management:
      - the first fresh/confident opposite direction still starts SELL immediately;
      - the old 500ms/3-receipt exit debounce stays disabled;
      - reversal re-entry still requires 2s continuous confirmation plus the
        inherited fresh-book and signed-quote >=0.075 executable-edge gates.

    V41 only tightens entry safety. The existing 750ms receipt-age guard can miss
    a live socket that is draining old Polymarket events. V41 therefore tracks the
    source timestamp of the UP quote events that actually drive `upMid` and blocks
    new BUY/re-entry work when that quote source is stale. A later
    `last_trade_price` event cannot refresh this quote timestamp.

    Source freshness never blocks SELL. Existing resting Shotgun GTC orders are
    not cancelled or modified.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v41_guard_lock = threading.RLock()
        self._v41_quote_identity: tuple[str, int] | None = None
        self._v41_quote_source_timestamp_ms: int | None = None
        self._v41_quote_received_timestamp_ms: int | None = None
        self._v41_last_check: dict[str, Any] | None = None
        self._v41_blocks = 0
        self._v41_pre_book_blocks = 0
        self._v41_pre_quote_blocks = 0
        self._v41_pre_place_blocks = 0
        self._v41_reentry_source_resets = 0
        self._v41_partial_shotgun_blocks = 0
        self._v41_source_blocked_this_tick = False
        super().__init__(*args, **kwargs)

    @staticmethod
    def _int_ms(value: Any) -> int | None:
        number = base._finite(value)
        if number is None or number <= 0:
            return None
        return int(number)

    def _poly_state(self) -> dict[str, Any] | None:
        """Preserve the inherited receipt guard while exposing quote-source age.

        This intentionally mirrors the base reader instead of making a second
        localhost request. V40/V39/V36 calls dynamically dispatch here, so every
        normal entry, fast retry and cautious re-entry sees the same source-age
        metadata without adding another network hop.
        """
        try:
            response = self.http.get(base.CROSS_ORACLE_URL)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self.last_error = f"Poly state: {str(exc)[:300]}"
            return None
        if not isinstance(payload, dict):
            return None
        continuity = payload.get("continuity")
        if isinstance(continuity, dict) and continuity.get("gapActive") is True:
            self.last_error = "Polymarket continuity gap active"
            return None
        poly = payload.get("polymarket")
        if not isinstance(poly, dict) or str(poly.get("status")) != "LIVE":
            self.last_error = "Polymarket feed is not LIVE"
            return None
        receipt_age = base._finite(poly.get("ageMs"))
        if receipt_age is None or receipt_age > base.MAX_POLY_AGE_MS:
            self.last_error = f"Polymarket quote stale: {receipt_age}ms"
            return None
        up = poly.get("up")
        down = poly.get("down")
        market = poly.get("market")
        if not isinstance(up, dict) or not isinstance(down, dict) or not isinstance(market, dict):
            return None
        up_mid = base.probability_mid(up.get("bestBid"), up.get("bestAsk"))
        if up_mid is None:
            return None
        direction = base.probability_direction(up_mid)

        now_ms = base._now_ms()
        slug = str(market.get("slug") or "")
        try:
            ws_session = int(up.get("wsSession") or 0)
        except (TypeError, ValueError):
            ws_session = 0
        identity = (slug, ws_session)
        event_type = str(up.get("lastEventType") or "").strip().lower()
        with self._v41_guard_lock:
            if self._v41_quote_identity != identity:
                self._v41_quote_identity = identity
                self._v41_quote_source_timestamp_ms = None
                self._v41_quote_received_timestamp_ms = None
            # Only quote-changing/order-book events are allowed to refresh the
            # source timestamp used by the BUY guard. A fresh last trade must not
            # make an old best-bid/ask pair look fresh.
            if event_type in QUOTE_EVENT_TYPES:
                source_ms = self._int_ms(up.get("sourceTimestampMs"))
                received_ms = self._int_ms(up.get("receivedTimestampMs"))
                if source_ms is not None and received_ms is not None:
                    self._v41_quote_source_timestamp_ms = source_ms
                    self._v41_quote_received_timestamp_ms = received_ms
            signal_source_ms = self._v41_quote_source_timestamp_ms
            signal_received_ms = self._v41_quote_received_timestamp_ms

        source_age_ms = now_ms - signal_source_ms if signal_source_ms is not None else None
        quote_receipt_age_ms = (
            now_ms - signal_received_ms if signal_received_ms is not None else None
        )
        transport_age_ms = (
            signal_received_ms - signal_source_ms
            if signal_source_ms is not None and signal_received_ms is not None
            else None
        )
        result = {
            "upMid": up_mid,
            "direction": direction,
            "selectedMid": base.selected_probability(up_mid, direction) if direction else None,
            "ageMs": receipt_age,
            "slug": market.get("slug"),
            "windowEndMs": int(market.get("windowEndMs") or 0),
            "receivedTimestampMs": poly.get("receivedTimestampMs"),
            "sourceTimestampMs": poly.get("sourceTimestampMs"),
            "gapGeneration": (
                (continuity or {}).get("gapGeneration")
                if isinstance(continuity, dict)
                else None
            ),
            "signalSourceTimestampMs": signal_source_ms,
            "signalQuoteReceivedTimestampMs": signal_received_ms,
            "signalSourceAgeMs": source_age_ms,
            "signalQuoteReceiptAgeMs": quote_receipt_age_ms,
            "signalTransportAgeMs": transport_age_ms,
            "signalLastObservedEventType": event_type or None,
            "signalQuoteEventTypes": sorted(QUOTE_EVENT_TYPES),
        }
        self.last_poly = result
        return result

    def _source_policy(self, poly: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
        row = poly if isinstance(poly, dict) else self.last_poly
        row = row if isinstance(row, dict) else {}
        now_ms = base._now_ms()
        source_ms = self._int_ms(row.get("signalSourceTimestampMs"))
        received_ms = self._int_ms(row.get("signalQuoteReceivedTimestampMs"))
        source_age_ms = now_ms - source_ms if source_ms is not None else None
        quote_receipt_age_ms = now_ms - received_ms if received_ms is not None else None
        transport_age_ms = (
            received_ms - source_ms
            if source_ms is not None and received_ms is not None
            else None
        )
        policy = source_freshness_policy(
            source_age_ms=source_age_ms,
            quote_receipt_age_ms=quote_receipt_age_ms,
        )
        detail = {
            "policy": policy,
            "checkedAtMs": now_ms,
            "signalSourceTimestampMs": source_ms,
            "signalQuoteReceivedTimestampMs": received_ms,
            "sourceAgeMs": source_age_ms,
            "quoteReceiptAgeMs": quote_receipt_age_ms,
            "transportAgeMs": transport_age_ms,
            "maxSourceAgeMs": MAX_POLY_SIGNAL_SOURCE_AGE_MS,
            "maxQuoteReceiptAgeMs": MAX_POLY_SIGNAL_QUOTE_RECEIPT_AGE_MS,
            "futureToleranceMs": POLY_SOURCE_FUTURE_TOLERANCE_MS,
            "lastObservedEventType": row.get("signalLastObservedEventType"),
        }
        return policy, detail

    def _remember_check(self, stage: str, policy: str, detail: dict[str, Any]) -> None:
        with self._v41_guard_lock:
            self._v41_last_check = {"stage": stage, **detail, "policy": policy}

    def _block_message(self, stage: str, policy: str, detail: dict[str, Any]) -> str:
        return (
            f"Poly source freshness blocked BUY at {stage}: policy={policy}; "
            f"sourceAgeMs={detail.get('sourceAgeMs')}; "
            f"quoteReceiptAgeMs={detail.get('quoteReceiptAgeMs')}; "
            f"transportAgeMs={detail.get('transportAgeMs')}; "
            f"maxSourceAgeMs={MAX_POLY_SIGNAL_SOURCE_AGE_MS:.0f}; "
            f"maxQuoteReceiptAgeMs={MAX_POLY_SIGNAL_QUOTE_RECEIPT_AGE_MS:.0f}"
        )

    def _direct_book(
        self, market: dict[str, Any], side: str
    ) -> tuple[float | None, float | None, float]:
        policy, detail = self._source_policy()
        self._remember_check("BEFORE_BINANCE_BOOK", policy, detail)
        if policy != "ALLOW":
            self._v41_source_blocked_this_tick = True
            self._v41_blocks += 1
            self._v41_pre_book_blocks += 1
            self._entry_price_blocked_this_tick = False
            self._entry_depth_blocked_this_tick = False
            self.status = "ENTRY_POLY_SOURCE_STALE_V41"
            self.last_error = self._block_message("BEFORE_BINANCE_BOOK", policy, detail)
            return None, None, 0.0
        return super()._direct_book(market, side)

    def _reject_entry_for_source(
        self,
        row: dict[str, Any],
        *,
        stage: str,
        policy: str,
        detail: dict[str, Any],
    ) -> None:
        round_id = int(row["id"])
        market_id = int(row["market_id"])
        message = self._block_message(stage, policy, detail)
        self._update_round(
            round_id,
            state="REJECTED",
            close_reason="ENTRY_POLY_SOURCE_STALE_V41",
            error_kind="ENTRY_POLY_SOURCE_STALE_V41",
            error_message=message,
        )
        self._v41_blocks += 1
        if stage == "BEFORE_BUY_QUOTE":
            self._v41_pre_quote_blocks += 1
        else:
            self._v41_pre_place_blocks += 1
        self.status = "ENTRY_POLY_SOURCE_STALE_V41"
        self.last_error = message
        self._remember_check(stage, policy, detail)
        self._event(
            "INFO",
            "ENTRY_POLY_SOURCE_STALE_V41",
            market_id,
            round_id,
            message,
        )

    def _open_round(self, row: dict[str, Any], poly: dict[str, Any]) -> None:
        policy, detail = self._source_policy(poly)
        self._remember_check("BEFORE_BUY_QUOTE", policy, detail)
        if policy != "ALLOW":
            self._reject_entry_for_source(
                row,
                stage="BEFORE_BUY_QUOTE",
                policy=policy,
                detail=detail,
            )
            return

        with self.lock:
            client = self.client
        if client is None:
            return super()._open_round(row, poly)

        original_market = getattr(client, "place_market_order", None)
        original_limit = getattr(client, "place_limit_order", None)
        blocked: list[tuple[str, str, dict[str, Any]]] = []

        def check_before_place(order_kind: str) -> None:
            current_policy, current_detail = self._source_policy()
            self._remember_check(f"BEFORE_{order_kind}_PLACE", current_policy, current_detail)
            if current_policy == "ALLOW":
                return
            with self._v41_guard_lock:
                blocked.append((order_kind, current_policy, current_detail))
            raise _PolySourceFreshnessBlocked(
                self._block_message(f"BEFORE_{order_kind}_PLACE", current_policy, current_detail)
            )

        def guarded_market(*args: Any, **kwargs: Any) -> dict[str, Any]:
            check_before_place("MARKET_BUY")
            assert callable(original_market)
            return original_market(*args, **kwargs)

        def guarded_limit(*args: Any, **kwargs: Any) -> dict[str, Any]:
            check_before_place("LIMIT_BUY")
            assert callable(original_limit)
            return original_limit(*args, **kwargs)

        if callable(original_market):
            client.place_market_order = guarded_market  # type: ignore[method-assign]
        if callable(original_limit):
            client.place_limit_order = guarded_limit  # type: ignore[method-assign]
        try:
            super()._open_round(row, poly)
        finally:
            if callable(original_market):
                client.place_market_order = original_market  # type: ignore[method-assign]
            if callable(original_limit):
                client.place_limit_order = original_limit  # type: ignore[method-assign]

        if not blocked:
            return

        self._v41_blocks += len(blocked)
        self._v41_pre_place_blocks += len(blocked)
        last_kind, last_policy, last_detail = blocked[-1]
        message = self._block_message(
            f"BEFORE_{last_kind}_PLACE",
            last_policy,
            last_detail,
        )
        refreshed = self._round_state(int(row["id"])) or row
        submitted_shotgun = []
        submitted_levels = getattr(self, "_submitted_levels", None)
        if callable(submitted_levels):
            try:
                submitted_shotgun = list(submitted_levels(int(row["id"])))
            except Exception:
                submitted_shotgun = []
        has_submitted_order = bool(str(refreshed.get("entry_order_id") or "").strip())
        if not has_submitted_order and not submitted_shotgun:
            self._update_round(
                int(row["id"]),
                state="REJECTED",
                close_reason="ENTRY_POLY_SOURCE_STALE_V41",
                error_kind="ENTRY_POLY_SOURCE_STALE_V41",
                error_message=message,
            )
            self._event(
                "INFO",
                "ENTRY_POLY_SOURCE_STALE_V41",
                int(row["market_id"]),
                int(row["id"]),
                message,
            )
        else:
            # Some Shotgun LIMITs may already have been submitted by sibling
            # workers before source age crossed the threshold. Do not cancel or
            # mutate those resting GTC orders; only later placements are blocked.
            self._v41_partial_shotgun_blocks += 1
            self._event(
                "WARN",
                "SHOTGUN_SOURCE_STALE_PARTIAL_BLOCK_V41",
                int(row["market_id"]),
                int(row["id"]),
                (
                    f"{message}; alreadySubmittedLevels={len(submitted_shotgun)}; "
                    "existing resting GTC orders intentionally left untouched"
                ),
            )
        self.status = "ENTRY_POLY_SOURCE_STALE_V41"
        self.last_error = message

    def _refresh_reentry_confirmation(self) -> str:
        result = super()._refresh_reentry_confirmation()
        guard = self._v40_reentry_guard
        if not isinstance(guard, dict):
            return result
        policy, detail = self._source_policy()
        self._remember_check("REVERSAL_REENTRY_CONFIRM", policy, detail)
        if policy == "ALLOW":
            return result
        prior_reason = guard.get("lastResetReason")
        prior_started = guard.get("confirmationStartedAtMs")
        direction = str((self.last_poly or {}).get("direction") or "").upper() or None
        reason = f"RESET_SOURCE_{policy}_V41"
        self._reset_reentry_confirmation(reason, direction)
        if prior_reason != reason or prior_started is not None:
            self._v41_reentry_source_resets += 1
        self.status = "REVERSAL_REENTRY_WAITING_FRESH_SOURCE_V41"
        self.last_error = self._block_message("REVERSAL_REENTRY_CONFIRM", policy, detail)
        return reason

    def _tick(self) -> None:
        self._v41_source_blocked_this_tick = False
        result = super()._tick()
        if self._v41_source_blocked_this_tick:
            self.status = "ENTRY_POLY_SOURCE_STALE_V41"
        return result

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        policy, detail = self._source_policy()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V41"
        payload["sourceFreshnessV41"] = {
            "enabled": True,
            "entryOnly": True,
            "sellExitBlockedBySourceAge": False,
            "maxSignalSourceAgeMs": MAX_POLY_SIGNAL_SOURCE_AGE_MS,
            "maxSignalQuoteReceiptAgeMs": MAX_POLY_SIGNAL_QUOTE_RECEIPT_AGE_MS,
            "sourceFutureToleranceMs": POLY_SOURCE_FUTURE_TOLERANCE_MS,
            "quoteEventTypes": sorted(QUOTE_EVENT_TYPES),
            "lastTradeDoesNotRefreshQuoteSourceAge": True,
            "currentPolicy": policy,
            "current": detail,
            "lastCheck": dict(self._v41_last_check or {}),
            "blocks": int(self._v41_blocks),
            "preBinanceBookBlocks": int(self._v41_pre_book_blocks),
            "preBuyQuoteBlocks": int(self._v41_pre_quote_blocks),
            "prePlaceBlocks": int(self._v41_pre_place_blocks),
            "reversalReentrySourceResets": int(self._v41_reentry_source_resets),
            "partialShotgunPlacementBlocks": int(self._v41_partial_shotgun_blocks),
            "v40ImmediateReversalSellPreserved": True,
            "v40CautiousReentryPreserved": True,
            "v40ReentryConfirmMsPreserved": True,
            "v40ReentryExecutableEdgePreserved": True,
            "shotgunCancellationChanged": False,
        }
        payload.setdefault("rules", {}).update(
            polySignalSourceFreshnessGuardV41=True,
            polySignalSourceMaxAgeMs=MAX_POLY_SIGNAL_SOURCE_AGE_MS,
            polySignalQuoteReceiptMaxAgeMs=MAX_POLY_SIGNAL_QUOTE_RECEIPT_AGE_MS,
            polySignalSourceGuardEntryOnly=True,
            polySignalSourceGuardDoesNotBlockSell=True,
            reversalExitImmediateV40PreservedByV41=True,
            reversalCautiousReentryV40PreservedByV41=True,
            shotgunCancellationChangedByV41=False,
        )
        return payload


base.PolyGapLiveEngine = PolySourceFreshnessGuardPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
