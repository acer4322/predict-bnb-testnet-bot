from __future__ import annotations

import math
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v26 import LIVE_SAME_MARKET_BREAKER_EXIT_COUNT
from .poly_gap_live_v30 import GuardVerifiedReversalHandoffPolyGapLiveEngine


SETTING_KEY = "same_market_reversal_exit_threshold"
MIN_THRESHOLD = 1
MAX_THRESHOLD = 20
DEFAULT_THRESHOLD = max(
    MIN_THRESHOLD,
    min(MAX_THRESHOLD, int(LIVE_SAME_MARKET_BREAKER_EXIT_COUNT)),
)


class RuntimeReversalBreakerPolyGapLiveEngine(
    GuardVerifiedReversalHandoffPolyGapLiveEngine
):
    """V31: make the completed-reversal same-market breaker runtime editable.

    The setting is persisted in the existing 8769 settings table. It gates only
    NEW BUY exposure, including V29/V30 reversal handoff BUYs. Existing positions,
    reversal SELLs, reconciliation and settlement remain unaffected.
    """

    def _ensure_defaults(self) -> None:
        super()._ensure_defaults()
        now = base._now_ms()
        with self.db_lock:
            self.db.execute(
                """INSERT OR IGNORE INTO poly_gap_live_settings(key,value,updated_at_ms)
                   VALUES(?,?,?)""",
                (SETTING_KEY, str(DEFAULT_THRESHOLD), now),
            )
            self.db.commit()

    def _same_market_reversal_exit_threshold(self) -> int:
        try:
            value = int(float(self._setting(SETTING_KEY, str(DEFAULT_THRESHOLD))))
        except (TypeError, ValueError):
            value = DEFAULT_THRESHOLD
        return max(MIN_THRESHOLD, min(MAX_THRESHOLD, value))

    def _settings(self) -> dict[str, Any]:
        settings = super()._settings()
        settings["sameMarketReversalExitThreshold"] = (
            self._same_market_reversal_exit_threshold()
        )
        return settings

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        forwarded = dict(values)
        raw_threshold = forwarded.pop("sameMarketReversalExitThreshold", None)
        threshold: int | None = None

        if raw_threshold is not None:
            try:
                numeric = float(raw_threshold)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "sameMarketReversalExitThreshold must be an integer between 1 and 20"
                ) from exc
            if not math.isfinite(numeric) or not numeric.is_integer():
                raise ValueError(
                    "sameMarketReversalExitThreshold must be an integer between 1 and 20"
                )
            threshold = int(numeric)
            if not MIN_THRESHOLD <= threshold <= MAX_THRESHOLD:
                raise ValueError(
                    "sameMarketReversalExitThreshold must be between 1 and 20"
                )

        if forwarded:
            super().update_settings(forwarded)
        elif threshold is None:
            return self.snapshot()

        if threshold is not None:
            previous = self._same_market_reversal_exit_threshold()
            self._set_setting(SETTING_KEY, str(threshold))
            # Let a lowered threshold announce a block immediately on the next
            # flat tick for the current market.
            self._same_market_breaker_event_market_id = None
            self._event(
                "WARN" if threshold < previous else "INFO",
                "LIVE_SAME_MARKET_REVERSAL_EXIT_THRESHOLD_CHANGED",
                None,
                None,
                (
                    "same-market completed reversal-exit breaker threshold changed "
                    f"{previous} -> {threshold}"
                ),
            )
        return self.snapshot()

    def _live_same_market_breaker(self, market_id: int) -> dict[str, Any]:
        count = self._completed_reversal_exits_for_market(int(market_id))
        threshold = self._same_market_reversal_exit_threshold()
        return {
            "marketId": int(market_id),
            "completedReversalExits": count,
            "threshold": threshold,
            "remainingBeforeBlock": max(0, threshold - count),
            "blocked": count >= threshold,
            "thresholdRuntimeEditable": True,
            "thresholdMinimum": MIN_THRESHOLD,
            "thresholdMaximum": MAX_THRESHOLD,
            "firstReversalExitMayRearm": threshold > 1,
            "paperCurrentMarketReversalsDoNotTriggerImmediateBlock": True,
            "persistentPaperChopGuardStillApplies": True,
        }

    def _projected_reversal_breaker(self, market_id: int) -> dict[str, Any]:
        completed = self._completed_reversal_exits_for_market(int(market_id))
        projected = completed + 1
        threshold = self._same_market_reversal_exit_threshold()
        return {
            "marketId": int(market_id),
            "completedBeforeCurrentExit": completed,
            "projectedIfCurrentExitCompletes": projected,
            "threshold": threshold,
            "handoffBlocked": projected >= threshold,
            "thresholdRuntimeEditable": True,
        }

    def _emit_guard_transition(self, state: dict[str, Any]) -> None:
        result = super()._emit_guard_transition(state)
        if state.get("verified") is True:
            persistent = bool(state.get("persistentPaused"))
            current_choppy = bool(state.get("currentMarketChoppy"))
            if current_choppy and not persistent:
                threshold = self._same_market_reversal_exit_threshold()
                state["reason"] = (
                    "Paper detected current-market CHOP, but it remains diagnostic only; "
                    f"same-market Live blocking requires {threshold} completed real-money "
                    "reversal exits"
                )
        return result

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V31"
        threshold = self._same_market_reversal_exit_threshold()
        verification = payload.get("paperGuardVerificationV27")
        if isinstance(verification, dict):
            verification["sameMarketImmediateBreaker"] = (
                f"{threshold}_COMPLETED_LIVE_REVERSAL_EXITS_RUNTIME_EDITABLE"
            )
        handoff = payload.get("reversalHandoff")
        if isinstance(handoff, dict):
            handoff["sameMarketBreakerThreshold"] = threshold
            handoff["sameMarketBreakerThresholdRuntimeEditable"] = True
        payload["sameMarketLiveExitBreakerControlV31"] = {
            "runtimeEditable": True,
            "persisted": True,
            "threshold": threshold,
            "minimum": MIN_THRESHOLD,
            "maximum": MAX_THRESHOLD,
            "affectsNewBuyOnly": True,
            "reversalHandoffBuyUsesSameThreshold": True,
            "existingPositionExitUnaffected": True,
        }
        return payload


base.PolyGapLiveEngine = RuntimeReversalBreakerPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
