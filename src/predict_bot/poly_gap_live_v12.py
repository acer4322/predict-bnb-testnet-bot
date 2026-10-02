from __future__ import annotations

import os
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v11 import LegacyAwareExitPolyGapLiveEngine


STABLE_EXIT_CONFIRM_MS = max(
    100,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_STABLE_EXIT_CONFIRM_MS", "500")),
)
STABLE_EXIT_CONFIRM_SAMPLES = max(
    2,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_STABLE_EXIT_CONFIRM_SAMPLES", "3")),
)
STABLE_EXIT_MAX_SAMPLE_GAP_MS = max(
    STABLE_EXIT_CONFIRM_MS,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_STABLE_EXIT_MAX_SAMPLE_GAP_MS", "1250")),
)
STABLE_EXIT_EMERGENCY_UP_MID = min(
    0.95,
    max(0.55, float(os.environ.get("PREDICT_POLY_GAP_LIVE_STABLE_EXIT_EMERGENCY_UP_MID", "0.70"))),
)
STABLE_EXIT_EMERGENCY_DOWN_MID = max(
    0.05,
    min(0.45, float(os.environ.get("PREDICT_POLY_GAP_LIVE_STABLE_EXIT_EMERGENCY_DOWN_MID", "0.30"))),
)


class StableExitPolyGapLiveEngine(LegacyAwareExitPolyGapLiveEngine):
    """V12: reduce exit whipsaw while preserving the V11 execution safety chain.

    Entry behavior, max-loss protection, order reconciliation, rollover safety,
    and the deliberately wide 20% SELL execution tolerance remain unchanged.
    Only the *decision to start a normal SELL* changes:

    - the confident Polymarket direction must oppose the held side;
    - the opposite signal must be seen in at least three distinct fresh Poly
      receipts and persist for at least 500 ms;
    - a gap between candidate samples that is too large resets confirmation;
    - a very strong opposite probability (UP >= 0.70 against a DOWN position,
      or UP <= 0.30 against an UP position) is an emergency exit and bypasses
      the debounce window;
    - stale/missing/neutral/same-side Poly state never advances confirmation.

    This intentionally does not tighten SELL slippage yet. For the first stable
    live comparison, changing only the exit trigger keeps the causal difference
    small and avoids creating a new failure mode where an at-risk position cannot
    flatten because a price cap is too strict.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.exit_flip_candidate: dict[str, Any] | None = None
        self.exit_flip_confirmed_count = 0
        self.exit_emergency_count = 0
        self.exit_flip_reset_count = 0

    def _clear_exit_candidate(self, reason: str | None = None) -> None:
        candidate = self.exit_flip_candidate
        if candidate is None:
            return
        self.exit_flip_candidate = None
        self.exit_flip_reset_count += 1
        if reason:
            self._event(
                "INFO",
                "EXIT_FLIP_RESET",
                int(candidate["marketId"]),
                int(candidate["roundId"]),
                reason,
            )

    @staticmethod
    def _is_emergency_exit(active_side: str, up_mid: float | None) -> bool:
        if up_mid is None:
            return False
        if active_side == "UP":
            return up_mid <= STABLE_EXIT_EMERGENCY_DOWN_MID
        if active_side == "DOWN":
            return up_mid >= STABLE_EXIT_EMERGENCY_UP_MID
        return False

    def _observe_exit_flip(
        self,
        active: dict[str, Any],
        *,
        direction: str,
        up_mid: float | None,
        receipt_ms: int | None,
        now_ms: int,
    ) -> bool:
        """Return True only when a normal or emergency exit is confirmed."""

        round_id = int(active["id"])
        market_id = int(active["market_id"])
        active_side = str(active["side"])

        if self._is_emergency_exit(active_side, up_mid):
            self.exit_emergency_count += 1
            self._event(
                "WARN",
                "EXIT_EMERGENCY_CONFIRMED",
                market_id,
                round_id,
                (
                    f"strong opposite Poly signal bypassed debounce; held={active_side}; "
                    f"direction={direction}; upMid={up_mid}; thresholds="
                    f"{STABLE_EXIT_EMERGENCY_DOWN_MID:.3f}/{STABLE_EXIT_EMERGENCY_UP_MID:.3f}"
                ),
            )
            self.exit_flip_candidate = None
            return True

        # Distinct receipt timestamps are required so a fast local loop cannot
        # count the same Polymarket snapshot three times.
        if receipt_ms is None:
            self._clear_exit_candidate("Poly receipt timestamp unavailable; confirmation reset")
            return False

        candidate = self.exit_flip_candidate
        same_candidate = bool(
            candidate
            and int(candidate.get("roundId") or -1) == round_id
            and str(candidate.get("direction") or "") == direction
        )
        if not same_candidate:
            self.exit_flip_candidate = {
                "roundId": round_id,
                "marketId": market_id,
                "heldSide": active_side,
                "direction": direction,
                "firstSeenAtMs": now_ms,
                "lastSeenAtMs": now_ms,
                "lastReceiptMs": receipt_ms,
                "samples": 1,
                "upMid": up_mid,
            }
            self._event(
                "INFO",
                "EXIT_FLIP_CANDIDATE",
                market_id,
                round_id,
                (
                    f"opposite Poly signal started; held={active_side}; to={direction}; "
                    f"upMid={up_mid}; require {STABLE_EXIT_CONFIRM_SAMPLES} distinct samples "
                    f"and {STABLE_EXIT_CONFIRM_MS}ms"
                ),
            )
            return False

        assert candidate is not None
        last_receipt = int(candidate.get("lastReceiptMs") or 0)
        if receipt_ms <= last_receipt:
            candidate["lastSeenAtMs"] = now_ms
            candidate["upMid"] = up_mid
            return False

        if receipt_ms - last_receipt > STABLE_EXIT_MAX_SAMPLE_GAP_MS:
            self.exit_flip_candidate = {
                "roundId": round_id,
                "marketId": market_id,
                "heldSide": active_side,
                "direction": direction,
                "firstSeenAtMs": now_ms,
                "lastSeenAtMs": now_ms,
                "lastReceiptMs": receipt_ms,
                "samples": 1,
                "upMid": up_mid,
            }
            self.exit_flip_reset_count += 1
            self._event(
                "INFO",
                "EXIT_FLIP_RESET",
                market_id,
                round_id,
                (
                    f"opposite signal receipt gap exceeded {STABLE_EXIT_MAX_SAMPLE_GAP_MS}ms; "
                    "confirmation restarted"
                ),
            )
            return False

        candidate["samples"] = int(candidate.get("samples") or 1) + 1
        candidate["lastSeenAtMs"] = now_ms
        candidate["lastReceiptMs"] = receipt_ms
        candidate["upMid"] = up_mid
        elapsed_ms = now_ms - int(candidate["firstSeenAtMs"])

        if (
            int(candidate["samples"]) >= STABLE_EXIT_CONFIRM_SAMPLES
            and elapsed_ms >= STABLE_EXIT_CONFIRM_MS
        ):
            self.exit_flip_confirmed_count += 1
            self._event(
                "INFO",
                "EXIT_FLIP_CONFIRMED",
                market_id,
                round_id,
                (
                    f"opposite Poly signal confirmed for {elapsed_ms}ms across "
                    f"{candidate['samples']} distinct receipts; held={active_side}; "
                    f"to={direction}; upMid={up_mid}"
                ),
            )
            self.exit_flip_candidate = None
            return True
        return False

    def _tick(self) -> None:
        active = self._current_active_round()
        if active is None:
            self._clear_exit_candidate()
            return super()._tick()

        state = str(active.get("state") or "")
        if state != "OPEN":
            self._clear_exit_candidate()
            return super()._tick()

        # Preserve V2 rollover safety and base same-market settlement behavior.
        market = self._prime_market()
        if market is None or int(market.get("market_id") or 0) != int(active["market_id"]):
            self._clear_exit_candidate("market rolled while position remained open")
            self._settle_hold(active)
            still_active = self._current_active_round()
            if still_active is not None and int(still_active["id"]) == int(active["id"]):
                self.status = "WAITING_SETTLEMENT"
            return

        now_ms = base._now_ms()
        if now_ms >= int(market.get("end_ms") or 0):
            self._clear_exit_candidate("market ended before a stable exit confirmed")
            self._settle_hold(active)
            return

        poly = self._poly_state()
        if poly is None:
            self._clear_exit_candidate("fresh Polymarket state unavailable")
            self.status = "DEGRADED"
            return

        direction = poly.get("direction")
        self.last_poly_direction = str(direction) if direction else None
        active_side = str(active["side"])
        if direction not in {"UP", "DOWN"} or str(direction) == active_side:
            if self.exit_flip_candidate is not None:
                self._clear_exit_candidate(
                    "opposite direction did not persist; candidate cancelled"
                )
            self.status = "MANAGING_POSITION"
            return

        up_mid = base._finite(poly.get("upMid"))
        receipt_raw = poly.get("receivedTimestampMs")
        try:
            receipt_ms = int(receipt_raw) if receipt_raw is not None else None
        except (TypeError, ValueError):
            receipt_ms = None

        if self._observe_exit_flip(
            active,
            direction=str(direction),
            up_mid=up_mid,
            receipt_ms=receipt_ms,
            now_ms=now_ms,
        ):
            self._exit_round(active, now_ms)
            return

        candidate = self.exit_flip_candidate or {}
        self.status = "EXIT_FLIP_CONFIRMING"
        self.last_error = None

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V12"
        payload["stableExit"] = {
            "enabled": True,
            "normalConfirmMs": STABLE_EXIT_CONFIRM_MS,
            "normalConfirmDistinctSamples": STABLE_EXIT_CONFIRM_SAMPLES,
            "maxDistinctSampleGapMs": STABLE_EXIT_MAX_SAMPLE_GAP_MS,
            "emergencyUpMidAgainstDown": STABLE_EXIT_EMERGENCY_UP_MID,
            "emergencyUpMidAgainstUp": STABLE_EXIT_EMERGENCY_DOWN_MID,
            "candidate": dict(self.exit_flip_candidate) if self.exit_flip_candidate else None,
            "confirmedNormalExits": int(self.exit_flip_confirmed_count),
            "emergencyExits": int(self.exit_emergency_count),
            "candidateResets": int(self.exit_flip_reset_count),
            "requiresDistinctPolyReceiptTimestamps": True,
            "sellExecutionToleranceChangedFromV11": False,
            "exitSlippageBps": (payload.get("executionTuning") or {}).get("exitSlippageBps"),
            "stabilityPriority": (
                "debounce the signal first; keep V11 wide SELL execution and order reconciliation unchanged"
            ),
        }
        return payload


base.PolyGapLiveEngine = StableExitPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
