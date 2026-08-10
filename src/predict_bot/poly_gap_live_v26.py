from __future__ import annotations

import os
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v25 import RollingPerformancePolyGapLiveEngine


LIVE_SAME_MARKET_BREAKER_EXIT_COUNT = max(
    2,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_SAME_MARKET_BREAKER_EXIT_COUNT", "2")),
)


class LiveExitCountBreakerPolyGapLiveEngine(RollingPerformancePolyGapLiveEngine):
    """V26: let the first real flip exit re-arm; stop after the second one.

    V13 originally used the Paper market-wide confirmed-reversal count for the
    immediate same-market breaker. That means a reversal observed before the
    first real-money entry could combine with the first real flip exit and block
    the rest of the market. V26 separates those responsibilities:

    - Paper currentMarketChoppy stays visible as a diagnostic only;
    - Paper persistentPaused still blocks new exposure exactly as before;
    - the immediate same-market Live breaker is owned by the real-money ledger;
    - a completed reversal exit means state=CLOSED with exit_signal_at_ms set;
    - the first completed reversal exit may re-arm into the new direction;
    - after the second completed reversal exit in the same Binance 5m market,
      no more NEW BUY rounds are allowed for that market;
    - existing positions, SELL reconciliation and settlement are never blocked.

    The count is derived from the persisted Live ledger, so process restart does
    not reset or bypass the same-market breaker.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._same_market_breaker_event_market_id: int | None = None
        super().__init__(*args, **kwargs)

    def _completed_reversal_exits_for_market(self, market_id: int) -> int:
        with self.db_lock:
            row = self.db.execute(
                """SELECT COUNT(*) AS n
                     FROM poly_gap_live_rounds
                    WHERE market_id=?
                      AND state='CLOSED'
                      AND exit_signal_at_ms IS NOT NULL""",
                (int(market_id),),
            ).fetchone()
        return int(row["n"] if row else 0)

    def _live_same_market_breaker(self, market_id: int) -> dict[str, Any]:
        count = self._completed_reversal_exits_for_market(int(market_id))
        blocked = count >= LIVE_SAME_MARKET_BREAKER_EXIT_COUNT
        return {
            "marketId": int(market_id),
            "completedReversalExits": count,
            "threshold": LIVE_SAME_MARKET_BREAKER_EXIT_COUNT,
            "remainingBeforeBlock": max(0, LIVE_SAME_MARKET_BREAKER_EXIT_COUNT - count),
            "blocked": blocked,
            "firstReversalExitMayRearm": True,
            "paperCurrentMarketReversalsDoNotTriggerImmediateBlock": True,
            "persistentPaperChopGuardStillApplies": True,
        }

    def _emit_guard_transition(self, state: dict[str, Any]) -> None:
        """Make Paper same-market CHOP diagnostic-only while preserving persistence.

        V13 calls this hook after assigning ``last_chop_guard = state``. Mutating
        the dictionary here therefore changes the exact state V13 subsequently
        uses in its entry gate without copying or bypassing any later safety
        layers in the V14-V25 inheritance chain.
        """
        if state.get("verified") is True:
            persistent = bool(state.get("persistentPaused"))
            current_choppy = bool(state.get("currentMarketChoppy"))
            state["paperRequestedBlockBeforeV26"] = bool(state.get("blocked"))
            state["paperCurrentMarketChoppyDiagnosticOnly"] = current_choppy
            state["liveImmediateBreakerOwner"] = "LIVE_COMPLETED_REVERSAL_EXITS"
            state["blocked"] = persistent
            if current_choppy and not persistent:
                state["reason"] = (
                    "Paper detected current-market CHOP, but V26 treats that as diagnostic only; "
                    f"same-market Live blocking requires {LIVE_SAME_MARKET_BREAKER_EXIT_COUNT} "
                    "completed real-money reversal exits"
                )
        return super()._emit_guard_transition(state)

    def _entry_allowed(self) -> bool:
        if not super()._entry_allowed():
            return False
        market = self.market_cache
        if not isinstance(market, dict):
            return True
        try:
            market_id = int(market.get("market_id") or 0)
        except (TypeError, ValueError):
            return True
        if market_id <= 0:
            return True
        return not self._live_same_market_breaker(market_id)["blocked"]

    def _tick(self) -> None:
        # Never put the breaker in front of money already at risk. The entire
        # V25/V24/V13/V12 position-management chain remains authoritative.
        if self._current_active_round() is not None:
            return super()._tick()

        settings = self._settings()
        if (
            base.MASTER_ENABLED
            and settings.get("runtimeEnabled")
            and not settings.get("lossTripped")
        ):
            market = self._prime_market()
            if isinstance(market, dict):
                try:
                    market_id = int(market.get("market_id") or 0)
                except (TypeError, ValueError):
                    market_id = 0
                if market_id > 0:
                    breaker = self._live_same_market_breaker(market_id)
                    if self._same_market_breaker_event_market_id != market_id and not breaker["blocked"]:
                        self._same_market_breaker_event_market_id = None
                    if breaker["blocked"]:
                        self.status = "BLOCKED_LIVE_CURRENT_MARKET_REVERSAL_EXITS"
                        self.last_error = (
                            f"current market already completed {breaker['completedReversalExits']} "
                            f"real-money reversal exits; threshold={breaker['threshold']}; "
                            "no more new BUY rounds this market"
                        )
                        if self._same_market_breaker_event_market_id != market_id:
                            self._same_market_breaker_event_market_id = market_id
                            self._event(
                                "WARN",
                                "LIVE_SAME_MARKET_REVERSAL_EXIT_BREAKER",
                                market_id,
                                None,
                                self.last_error,
                            )
                        return

        return super()._tick()

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        market = payload.get("market")
        market_id = 0
        if isinstance(market, dict):
            try:
                market_id = int(market.get("market_id") or 0)
            except (TypeError, ValueError):
                market_id = 0
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V26"
        payload["sameMarketLiveExitBreaker"] = (
            self._live_same_market_breaker(market_id)
            if market_id > 0
            else {
                "marketId": None,
                "completedReversalExits": 0,
                "threshold": LIVE_SAME_MARKET_BREAKER_EXIT_COUNT,
                "remainingBeforeBlock": LIVE_SAME_MARKET_BREAKER_EXIT_COUNT,
                "blocked": False,
                "firstReversalExitMayRearm": True,
                "paperCurrentMarketReversalsDoNotTriggerImmediateBlock": True,
                "persistentPaperChopGuardStillApplies": True,
            }
        )
        return payload


base.PolyGapLiveEngine = LiveExitCountBreakerPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
