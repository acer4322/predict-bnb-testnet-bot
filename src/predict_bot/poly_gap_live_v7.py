from __future__ import annotations

import os
import time
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v6 import ExitPriorityPolyGapLiveEngine


ENTRY_RETRY_COOLDOWN_MS = max(
    100,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_ENTRY_RETRY_COOLDOWN_MS", "500")),
)
PLACE_REJECT_RETRY_COOLDOWN_MS = max(
    ENTRY_RETRY_COOLDOWN_MS,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_PLACE_REJECT_RETRY_COOLDOWN_MS", "1000")),
)
NO_DEPTH_RETRY_COOLDOWN_MS = max(
    100,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_NO_DEPTH_RETRY_COOLDOWN_MS", "250")),
)
RATE_LIMIT_RETRY_COOLDOWN_MS = max(
    PLACE_REJECT_RETRY_COOLDOWN_MS,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_RATE_LIMIT_RETRY_COOLDOWN_MS", "5000")),
)
BALANCE_RETRY_COOLDOWN_MS = max(
    RATE_LIMIT_RETRY_COOLDOWN_MS,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_BALANCE_RETRY_COOLDOWN_MS", "10000")),
)


class ReArmingScalpPolyGapLiveEngine(ExitPriorityPolyGapLiveEngine):
    """V7: make the dedicated GAP executor a repeatable scalp cycle.

    V3 intentionally latched one persistent gap generation to prevent accidental
    quote/order spam. That was safe, but too sticky for the dedicated live path:
    a definite BUY failure could suppress the rest of the five-minute market.

    V7 keeps all hard safety behavior (one active round, flat-before-rearm,
    ambiguous placement halts, maximum-loss guard) while changing only the
    *definite failure* lifecycle:

    - quote/edge/place rejections re-arm after a bounded cooldown;
    - lack of visible first-level depth is retryable instead of market-long latched;
    - a confirmed flat exit clears the entry latch immediately, so the opposite
      Poly direction can form the next round in the same market;
    - rate-limit and balance failures use slower backoff instead of hot-looping;
    - ambiguous placement and unconfirmed positions remain fail-closed.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.entry_retry_not_before: dict[tuple[int, str], float] = {}
        self.entry_retry_reason: dict[tuple[int, str], str] = {}
        self.last_rearm_at_ms: int | None = None
        self.last_rearm_reason: str | None = None

    def _schedule_retry(self, key: tuple[int, str], cooldown_ms: int, reason: str) -> None:
        self.entry_signal_latch = None
        self.entry_retry_not_before[key] = time.monotonic() + max(0, cooldown_ms) / 1000.0
        self.entry_retry_reason[key] = reason
        self.last_rearm_at_ms = base._now_ms()
        self.last_rearm_reason = reason
        self._event(
            "INFO",
            "ENTRY_REARM_SCHEDULED",
            int(key[0]),
            None,
            f"{key[1]} retry in {cooldown_ms}ms after {reason}",
        )

    def _clear_retry(self, key: tuple[int, str] | None = None) -> None:
        if key is None:
            self.entry_retry_not_before.clear()
            self.entry_retry_reason.clear()
            return
        self.entry_retry_not_before.pop(key, None)
        self.entry_retry_reason.pop(key, None)

    def _retry_remaining_ms(self, key: tuple[int, str]) -> int:
        deadline = self.entry_retry_not_before.get(key)
        if deadline is None:
            return 0
        return max(0, int(round((deadline - time.monotonic()) * 1000.0)))

    def _round_state(self, round_id: int) -> dict[str, Any] | None:
        with self.db_lock:
            row = self.db.execute(
                "SELECT * FROM poly_gap_live_rounds WHERE id=?",
                (int(round_id),),
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _failure_cooldown_ms(row: dict[str, Any]) -> int:
        reason = str(row.get("error_kind") or row.get("close_reason") or "")
        message = str(row.get("error_message") or "").lower()
        combined = f"{reason.lower()} {message}"
        if "429" in combined or "rate limit" in combined or "too many request" in combined:
            return RATE_LIMIT_RETRY_COOLDOWN_MS
        if "insufficient" in combined or "balance" in combined:
            return BALANCE_RETRY_COOLDOWN_MS
        if reason in {"ENTRY_PLACE_REJECTED", "PREFLIGHT"}:
            return PLACE_REJECT_RETRY_COOLDOWN_MS
        return ENTRY_RETRY_COOLDOWN_MS

    def _finish_round(self, row: dict[str, Any], pnl: float, proceeds: float, reason: str) -> None:
        super()._finish_round(row, pnl, proceeds, reason)
        # The wallet is confirmed flat before _finish_round is used for a normal
        # exit. The old entry signal must not suppress the next scalp cycle.
        self.entry_signal_latch = None
        self._clear_retry()
        self.last_rearm_at_ms = base._now_ms()
        self.last_rearm_reason = f"confirmed flat after {reason}"
        self._event(
            "INFO",
            "SCALP_REARMED_FLAT",
            int(row["market_id"]),
            int(row["id"]),
            "wallet flat; same-market next round is armed",
        )

    def _settle_hold(self, row: dict[str, Any]) -> None:
        before = self._round_state(int(row["id"]))
        super()._settle_hold(row)
        after = self._round_state(int(row["id"]))
        if before and after and str(before.get("state")) != str(after.get("state")) and str(after.get("state")) == "SETTLED":
            self.entry_signal_latch = None
            self._clear_retry()
            self.last_rearm_at_ms = base._now_ms()
            self.last_rearm_reason = "official settlement completed"

    def _tick(self) -> None:
        active = self._current_active_round()
        if active is not None:
            # Keep V6 exit-priority behavior unchanged for positions already at risk.
            return super()._tick()

        settings = self._settings()
        if not base.MASTER_ENABLED:
            self.status = "MASTER_DISABLED"
            return
        if not settings["runtimeEnabled"] or settings["lossTripped"]:
            self.status = "MAX_LOSS_TRIPPED" if settings["lossTripped"] else "PAUSED"
            return
        if self._refresh_general_live_conflict():
            self.status = "BLOCKED_GENERAL_LIVE_CONFLICT"
            self.last_error = self.general_live_conflict_detail
            return
        if not self._ensure_clients():
            return

        market = self._prime_market()
        poly = self._poly_state()
        if market is None or poly is None:
            self.status = "WAITING_DATA"
            return

        market_id = int(market["market_id"])
        if self.halted_market_id == market_id:
            self.status = "MARKET_HALTED"
            return

        poly_end = int(poly.get("windowEndMs") or 0)
        if poly_end <= 0 or abs(poly_end - int(market["end_ms"])) > base.MAX_MARKET_END_SKEW_MS:
            self.status = "MARKET_MISMATCH"
            self.last_error = f"Poly/Binance end-time mismatch: {poly_end} vs {market['end_ms']}"
            return

        direction = poly.get("direction")
        self.last_poly_direction = str(direction) if direction else None
        if direction not in {"UP", "DOWN"}:
            self.entry_signal_latch = None
            self._clear_retry()
            self.status = "ARMED_WAITING_GAP"
            return

        side = str(direction)
        selected_mid = base._finite(poly.get("selectedMid"))
        if selected_mid is None:
            self.status = "WAITING_POLY_PRICE"
            return

        ask, ask_size, _book_rtt_ms = self._direct_book(market, side)
        if ask is None:
            self.status = "WAITING_BINANCE_BOOK"
            return

        edge = selected_mid - ask
        key = (market_id, side)
        if edge + 1e-12 < base.SCALP_MIN_EDGE:
            if self.entry_signal_latch == key:
                self.entry_signal_latch = None
            self._clear_retry(key)
            self.status = "ARMED_WAITING_GAP"
            return

        remaining_ms = self._retry_remaining_ms(key)
        if remaining_ms > 0:
            self.status = "GAP_RETRY_COOLDOWN"
            return
        self._clear_retry(key)

        # If a successful order is active it would have been caught above. At this
        # point a same-key latch is only a stale latch from an older lifecycle;
        # clear it rather than suppressing the market indefinitely.
        if self.entry_signal_latch == key:
            self.entry_signal_latch = None

        stake = float(settings["stakeUsdt"])
        if ask_size is not None and ask_size > 0 and ask_size * ask < min(stake, 0.01):
            self._schedule_retry(key, NO_DEPTH_RETRY_COOLDOWN_MS, "NO_VISIBLE_DEPTH")
            self.status = "GAP_WAITING_DEPTH_RETRY"
            return

        token_id = str(market[f"{side.lower()}_token_id"])
        self.entry_signal_latch = key
        row = self._insert_round(
            market=market,
            side=side,
            token_id=token_id,
            stake=stake,
            poly_selected=selected_mid,
            ask=ask,
            edge=edge,
        )
        self._open_round(row, poly)

        refreshed = self._round_state(int(row["id"]))
        if refreshed is None:
            return
        state = str(refreshed.get("state") or "")
        reason = str(refreshed.get("error_kind") or refreshed.get("close_reason") or state or "ENTRY_FAILURE")

        if state in {"REJECTED", "FAILED"}:
            # Definite failures are safe to retry. Placement ambiguity is never
            # represented by these states; it remains AMBIGUOUS + market halt.
            self._schedule_retry(key, self._failure_cooldown_ms(refreshed), reason)
            self.status = "ENTRY_RETRY_COOLDOWN"
        elif state == "AMBIGUOUS":
            self.status = "MARKET_HALTED"
        elif state == "ENTRY_SYNC":
            # Real BUY was submitted. Keep one-active-round semantics until the
            # wallet position is positively reconciled.
            self.status = "ENTRY_SYNC"

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V7"

        current_market = (payload.get("market") or {}).get("market_id")
        current_direction = (payload.get("poly") or {}).get("direction")
        current_key: tuple[int, str] | None = None
        try:
            if current_market is not None and current_direction in {"UP", "DOWN"}:
                current_key = (int(current_market), str(current_direction))
        except (TypeError, ValueError):
            current_key = None

        payload["scalpCycle"] = {
            "repeatSameMarketRounds": True,
            "definiteEntryFailureRetries": True,
            "entryRetryCooldownMs": ENTRY_RETRY_COOLDOWN_MS,
            "placeRejectRetryCooldownMs": PLACE_REJECT_RETRY_COOLDOWN_MS,
            "noDepthRetryCooldownMs": NO_DEPTH_RETRY_COOLDOWN_MS,
            "rateLimitRetryCooldownMs": RATE_LIMIT_RETRY_COOLDOWN_MS,
            "balanceRetryCooldownMs": BALANCE_RETRY_COOLDOWN_MS,
            "retryRemainingMs": self._retry_remaining_ms(current_key) if current_key else 0,
            "lastRearmAtMs": self.last_rearm_at_ms,
            "lastRearmReason": self.last_rearm_reason,
            "rearmImmediatelyAfterConfirmedFlat": True,
            "ambiguousPlacementStillHaltsMarket": True,
            "unconfirmedPositionStillHaltsMarket": True,
            "oneActiveRoundAtATime": True,
        }
        payload["signalGeneration"]["rearm"] = (
            "definite entry failure after cooldown, confirmed-flat exit, edge reset, "
            "Poly direction change, or market rollover"
        )
        return payload


base.PolyGapLiveEngine = ReArmingScalpPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
