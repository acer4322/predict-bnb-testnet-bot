from __future__ import annotations

import os
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v34 import (
    ExitCommitmentTelemetryPolyGapLiveEngine,
    SELL_TELEMETRY_MAX_AGE_MS,
)


EXIT_COMMITMENT_RETRY_WITHOUT_FRESH_POLY = (
    os.environ.get("PREDICT_POLY_GAP_LIVE_EXIT_COMMITMENT_RETRY_WITHOUT_FRESH_POLY", "1")
    .strip()
    .lower()
    not in {"0", "false", "no", "off"}
)


class DurableExitCommitmentPolyGapLiveEngine(ExitCommitmentTelemetryPolyGapLiveEngine):
    """V35: make a confirmed reversal an execution commitment until explicitly cancelled.

    V34 still allowed a neutral Poly state to expire the short commitment window and
    then wait indefinitely for another explicit opposite reading. Echtgeld evidence
    showed NO_FILL -> next SELL gaps of 6-105 seconds. V35 removes that expiry path:
    after the original V12 reversal has already been confirmed, a terminal FOK
    NO_FILL remains an execution problem. The executor retries after the existing
    cooldown unless a *fresh explicit* Poly direction returns to the held side.

    V34 also refreshed the SELL book before V8 read position shares. That signed
    position request could consume ~200-400ms, making a nominally fresh book stale
    before get_quote. V35 refreshes the Bid ladder immediately after the position
    read and rewrites the already-started SELL attempt with that near-quote snapshot.
    No extra request is inserted between signed quote response and place-order.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v35_exit_context: dict[str, Any] | None = None
        self._v35_committed_retries_without_poly = 0
        self._v35_committed_neutral_retries = 0
        self._v35_committed_opposite_retries = 0
        self._v35_commitment_cancels = 0
        self._v35_near_quote_book_updates = 0
        super().__init__(*args, **kwargs)

    def _rewrite_latest_sell_attempt_depth(
        self, round_id: int, depth: dict[str, Any]
    ) -> None:
        attempt_id = self._latest_attempt_id(int(round_id), "SELL")
        if attempt_id is None:
            return
        with self.db_lock:
            self.db.execute(
                """UPDATE poly_gap_live_execution_attempts SET
                       book_observed_at_ms=?,best_price=?,best_size=?,price_boundary=?,
                       visible_amount=?,required_amount=?,coverage_ratio=?,expected_vwap=?,
                       expected_worst_price=?,book_rtt_ms=?
                     WHERE id=?""",
                (
                    int(depth.get("observedAtMs") or 0) or None,
                    base._finite(depth.get("bid")),
                    base._finite(depth.get("bidSize")),
                    base._finite(depth.get("minPriceWithinExitTolerance")),
                    base._finite(depth.get("visibleShares")),
                    base._finite(depth.get("requiredShares")),
                    base._finite(depth.get("coverageRatio")),
                    base._finite(depth.get("expectedFillVwap")),
                    base._finite(depth.get("expectedWorstPrice")),
                    base._finite(depth.get("bookRttMs")),
                    int(attempt_id),
                ),
            )
            self.db.commit()

    def _position_shares(self, token_id: str) -> float | None:
        # V8 calls this immediately before it starts the signed SELL quote. Refresh
        # the book *after* this potentially slow position request so book->quote
        # age describes the actual execution path rather than a pre-position read.
        shares = super()._position_shares(token_id)
        context = self._v35_exit_context
        if (
            isinstance(context, dict)
            and str(context.get("token_id") or "") == str(token_id)
            and shares is not None
            and shares > 1e-9
        ):
            depth = self._refresh_sell_depth_for_attempt(context)
            if isinstance(depth, dict):
                self._rewrite_latest_sell_attempt_depth(int(context["id"]), depth)
                self._v35_near_quote_book_updates += 1
        return shares

    def _exit_round(self, row: dict[str, Any], signal_ms: int) -> None:
        existing_order_id = str(row.get("exit_order_id") or "").strip()
        if existing_order_id:
            # Existing SELL reconciliation is not a fresh execution attempt.
            return super()._exit_round(row, signal_ms)

        # Skip V34's pre-position book refresh. V32 still creates the execution
        # attempt; our _position_shares override then replaces its depth fields
        # with a snapshot taken immediately before the quote starts.
        self._v35_exit_context = dict(row)
        before_sequence = self._place_sequence()
        try:
            super(ExitCommitmentTelemetryPolyGapLiveEngine, self)._exit_round(
                row, signal_ms
            )
        finally:
            self._v35_exit_context = None
        self._persist_place_timing(
            round_id=int(row["id"]),
            action="SELL",
            before_sequence=before_sequence,
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
        # Reuse V32/V34's configured no-fill cooldown from the snapshot/rules path.
        from .poly_gap_live_v32 import EXIT_NO_FILL_RETRY_COOLDOWN_MS

        remaining = EXIT_NO_FILL_RETRY_COOLDOWN_MS - max(0, now_ms - updated_ms)
        if remaining > 0:
            self.status = "EXIT_COMMITMENT_RETRY_COOLDOWN"
            self.last_error = f"confirmed reversal SELL no-fill; committed retry in {remaining}ms"
            return True

        market = self._prime_market()
        if (
            not isinstance(market, dict)
            or int(market.get("market_id") or 0) != int(active["market_id"])
            or now_ms >= int(market.get("end_ms") or 0)
        ):
            return False

        # Arm before reading Poly. V34 armed only after _poly_state succeeded,
        # which meant a temporary feed gap could prevent commitment altogether.
        commitment = self._commitment_for(active, now_ms)
        poly = self._poly_state()
        direction = str((poly or {}).get("direction") or "").upper()
        held_side = str(active.get("side") or "").upper()

        if direction == held_side and held_side in {"UP", "DOWN"}:
            self._exit_commitments.pop(int(active["id"]), None)
            self._set_exit_intent(int(active["id"]), "")
            self._update_round(int(active["id"]), error_kind=None, error_message=None)
            self._v35_commitment_cancels += 1
            self._event(
                "INFO",
                "EXIT_COMMITMENT_CANCELLED_HELD_SIDE_V35",
                int(active["market_id"]),
                int(active["id"]),
                "fresh Poly explicitly returned to the held side; committed SELL retry cancelled",
            )
            return False

        if not isinstance(poly, dict) and not EXIT_COMMITMENT_RETRY_WITHOUT_FRESH_POLY:
            self.status = "EXIT_COMMITMENT_WAITING_FRESH_POLY"
            self.last_error = "confirmed reversal SELL no-fill; configured to wait for fresh Poly"
            return True

        if not isinstance(poly, dict):
            policy = "RETRY_COMMITTED_WITHOUT_FRESH_POLY"
            self._v35_committed_retries_without_poly += 1
        elif direction in {"UP", "DOWN"}:
            policy = "RETRY_OPPOSITE"
            self._v35_committed_opposite_retries += 1
        else:
            policy = "RETRY_NEUTRAL_COMMITTED"
            self._v35_committed_neutral_retries += 1

        retry_key = (int(active["id"]), updated_ms)
        if self._last_fast_retry_key != retry_key:
            self._last_fast_retry_key = retry_key
            self._fast_exit_retries += 1
            self._event(
                "WARN",
                "EXIT_COMMITMENT_RETRY_V35",
                int(active["market_id"]),
                int(active["id"]),
                (
                    f"confirmed reversal SELL FOK no-fill; policy={policy}; "
                    f"direction={direction or 'UNAVAILABLE/NEUTRAL'}; retrying execution without "
                    "repeating signal debounce or waiting for a neutral commitment expiry"
                ),
            )

        signal_ms = int(active.get("exit_signal_at_ms") or commitment.get("startedAtMs") or now_ms)
        self._exit_round(active, signal_ms)
        return True

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V35"
        payload["exitCommitmentV35"] = {
            "enabled": True,
            "commitmentExpiresOnNeutral": False,
            "explicitFreshHeldSideCancels": True,
            "retryWithoutFreshPoly": EXIT_COMMITMENT_RETRY_WITHOUT_FRESH_POLY,
            "neutralRetries": int(self._v35_committed_neutral_retries),
            "oppositeRetries": int(self._v35_committed_opposite_retries),
            "retriesWithoutFreshPoly": int(self._v35_committed_retries_without_poly),
            "heldSideCancels": int(self._v35_commitment_cancels),
            "active": dict(
                self._exit_commitments.get(
                    int((self._current_active_round() or {}).get("id") or -1), {}
                )
            ),
        }
        payload["executionTimingV35"] = {
            "sellBookRefreshAfterPositionRead": True,
            "sellBookRefreshBeforeQuote": True,
            "extraBookRequestBetweenQuoteAndPlace": False,
            "nearQuoteBookUpdates": int(self._v35_near_quote_book_updates),
            "targetBookAgeAtQuoteStartMs": SELL_TELEMETRY_MAX_AGE_MS,
            "exactPlaceRttInheritedFromV34": True,
        }
        rules = payload.setdefault("rules", {})
        rules.update(
            {
                "exitCommitmentNeutralExpiryRemovedV35": True,
                "exitCommitmentRetryWithoutFreshPoly": EXIT_COMMITMENT_RETRY_WITHOUT_FRESH_POLY,
                "sellBookRefreshAfterPositionReadV35": True,
            }
        )
        return payload


base.PolyGapLiveEngine = DurableExitCommitmentPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
