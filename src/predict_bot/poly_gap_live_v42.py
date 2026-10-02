from __future__ import annotations

from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v41 import PolySourceFreshnessGuardPolyGapLiveEngine


TAKE_PROFIT_MARKET_LOCK_REASON = "TAKE_PROFIT"
TAKE_PROFIT_MARKET_LOCK_STATUS = "BLOCKED_TAKE_PROFIT_MARKET_LOCK_V42"


def take_profit_reentry_policy(*, locked: bool) -> str:
    return "BLOCK_TAKE_PROFIT_MARKET" if bool(locked) else "ALLOW"


class TakeProfitMarketLockPolyGapLiveEngine(PolySourceFreshnessGuardPolyGapLiveEngine):
    """V42: a take-profit trigger permanently closes that five-minute market to new BUYs.

    V42 changes only the post-take-profit re-entry policy. V40/V41 remain
    authoritative for position management and entry quality:

    - V40 first fresh/confident opposite Poly direction still starts SELL
      immediately and ordinary POLY_DIRECTION_FLIP exits may still use V40's
      cautious 2s / >=0.075 reversal re-entry path;
    - V41 source-age freshness still blocks stale-source BUYs and never blocks
      SELL;
    - when V28's held-side Bid price control persists exit_intent=TAKE_PROFIT,
      V42 immediately persists a market-level lock before the SELL outcome is
      known;
    - while that market lock exists, no new normal BUY, fast-retry BUY,
      reversal re-entry BUY, or new Shotgun generation may be submitted;
    - the lock never blocks SELL/reconciliation/settlement for money already at
      risk and survives process restarts;
    - existing resting Shotgun GTC orders are intentionally not cancelled or
      modified, preserving the established no-auto-cancel policy.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v42_take_profit_entry_blocks = 0
        self._v42_last_block_event_market_id: int | None = None
        self._v42_blocked_this_tick = False
        self._v42_last_lock: dict[str, Any] | None = None
        super().__init__(*args, **kwargs)

    def _create_schema(self) -> None:
        super()._create_schema()
        with self.db_lock:
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS poly_gap_live_take_profit_market_locks (
                       market_id INTEGER PRIMARY KEY,
                       source_round_id INTEGER,
                       triggered_at_ms INTEGER NOT NULL,
                       trigger_bid REAL,
                       take_profit_price REAL,
                       reason TEXT NOT NULL
                   )"""
            )
            # Backfill any V28+ take-profit that already happened before V42 was
            # installed. This makes an in-progress current market safe across an
            # upgrade/restart and is harmless for old completed markets.
            self.db.execute(
                """INSERT OR IGNORE INTO poly_gap_live_take_profit_market_locks(
                       market_id, source_round_id, triggered_at_ms,
                       trigger_bid, take_profit_price, reason
                   )
                   SELECT market_id,
                          MIN(id),
                          MIN(COALESCE(exit_signal_at_ms, updated_at_ms, created_at_ms)),
                          NULL,
                          NULL,
                          'TAKE_PROFIT'
                     FROM poly_gap_live_rounds
                    WHERE exit_intent='TAKE_PROFIT' OR close_reason='TAKE_PROFIT'
                    GROUP BY market_id"""
            )
            self.db.commit()

    def _take_profit_market_lock(self, market_id: int) -> dict[str, Any] | None:
        with self.db_lock:
            row = self.db.execute(
                """SELECT market_id, source_round_id, triggered_at_ms,
                          trigger_bid, take_profit_price, reason
                     FROM poly_gap_live_take_profit_market_locks
                    WHERE market_id=?""",
                (int(market_id),),
            ).fetchone()
        if row is None:
            return None
        return {
            "marketId": int(row["market_id"]),
            "sourceRoundId": (
                int(row["source_round_id"])
                if row["source_round_id"] is not None
                else None
            ),
            "triggeredAtMs": int(row["triggered_at_ms"]),
            "triggerBid": base._finite(row["trigger_bid"]),
            "takeProfitPrice": base._finite(row["take_profit_price"]),
            "reason": str(row["reason"] or TAKE_PROFIT_MARKET_LOCK_REASON),
        }

    def _is_take_profit_locked_market(self, market_id: int) -> bool:
        return self._take_profit_market_lock(int(market_id)) is not None

    def _lock_take_profit_market(self, market_id: int, source_round_id: int) -> None:
        market_id = int(market_id)
        round_id = int(source_round_id)
        if market_id <= 0 or round_id <= 0:
            return

        book = getattr(self, "_last_take_profit_book", None)
        trigger_bid: float | None = None
        if isinstance(book, dict):
            try:
                same_market = int(book.get("marketId") or 0) == market_id
                same_round = int(book.get("roundId") or 0) == round_id
            except (TypeError, ValueError):
                same_market = False
                same_round = False
            if same_market and same_round:
                trigger_bid = base._finite(book.get("bid"))

        try:
            take_profit_price = base._finite(self._settings().get("takeProfitPrice"))
        except Exception:
            take_profit_price = None
        now_ms = base._now_ms()

        with self.db_lock:
            cursor = self.db.execute(
                """INSERT OR IGNORE INTO poly_gap_live_take_profit_market_locks(
                       market_id, source_round_id, triggered_at_ms,
                       trigger_bid, take_profit_price, reason
                   ) VALUES(?,?,?,?,?,?)""",
                (
                    market_id,
                    round_id,
                    now_ms,
                    trigger_bid,
                    take_profit_price,
                    TAKE_PROFIT_MARKET_LOCK_REASON,
                ),
            )
            inserted = int(cursor.rowcount or 0) > 0
            self.db.commit()

        lock = self._take_profit_market_lock(market_id)
        self._v42_last_lock = dict(lock or {}) or None

        # If a prior normal reversal had armed V40's cautious re-entry timer,
        # TAKE_PROFIT wins: this market is now closed to new exposure.
        guard = getattr(self, "_v40_reentry_guard", None)
        if isinstance(guard, dict):
            try:
                if int(guard.get("marketId") or 0) == market_id:
                    self._v40_reentry_guard = None
            except (TypeError, ValueError):
                pass

        if inserted:
            self._event(
                "WARN",
                "TAKE_PROFIT_MARKET_LOCK_ARMED_V42",
                market_id,
                round_id,
                (
                    "take-profit price control triggered; this Binance 5m market is "
                    "permanently closed to new BUY/re-entry attempts until market rollover"
                ),
            )

    def _set_exit_intent(self, round_id: int, intent: str) -> None:
        super()._set_exit_intent(round_id, intent)
        if str(intent or "").strip().upper() != TAKE_PROFIT_MARKET_LOCK_REASON:
            return
        row = self._round_state(int(round_id))
        if not isinstance(row, dict):
            return
        try:
            market_id = int(row.get("market_id") or 0)
        except (TypeError, ValueError):
            market_id = 0
        if market_id > 0:
            self._lock_take_profit_market(market_id, int(round_id))

    def _mark_take_profit_entry_block(
        self,
        market_id: int,
        *,
        round_id: int | None = None,
        stage: str,
    ) -> None:
        market_id = int(market_id)
        lock = self._take_profit_market_lock(market_id)
        if lock is None:
            return
        self._v42_blocked_this_tick = True
        self._v42_take_profit_entry_blocks += 1
        self.status = TAKE_PROFIT_MARKET_LOCK_STATUS
        self.last_error = (
            f"market {market_id} is locked after TAKE_PROFIT trigger from round "
            f"{lock.get('sourceRoundId')}; no new BUY this five-minute market; stage={stage}"
        )
        if self._v42_last_block_event_market_id != market_id:
            self._v42_last_block_event_market_id = market_id
            self._event(
                "INFO",
                "TAKE_PROFIT_MARKET_REENTRY_BLOCKED_V42",
                market_id,
                round_id,
                self.last_error,
            )

    def _entry_allowed(self) -> bool:
        if not super()._entry_allowed():
            return False
        market = self.market_cache
        if not isinstance(market, dict):
            return True
        try:
            market_id = int(market.get("market_id") or 0)
        except (TypeError, ValueError):
            market_id = 0
        if market_id <= 0 or not self._is_take_profit_locked_market(market_id):
            return True
        self._mark_take_profit_entry_block(market_id, stage="ENTRY_ALLOWED")
        return False

    def _direct_book(
        self, market: dict[str, Any], side: str
    ) -> tuple[float | None, float | None, float]:
        try:
            market_id = int(market.get("market_id") or 0)
        except (TypeError, ValueError):
            market_id = 0
        if market_id > 0 and self._is_take_profit_locked_market(market_id):
            self._entry_price_blocked_this_tick = False
            self._entry_depth_blocked_this_tick = False
            self._mark_take_profit_entry_block(
                market_id,
                stage=f"BEFORE_BINANCE_BOOK_{str(side or '').upper()}",
            )
            return None, None, 0.0
        return super()._direct_book(market, side)

    def _open_round(self, row: dict[str, Any], poly: dict[str, Any]) -> None:
        try:
            market_id = int(row.get("market_id") or 0)
            round_id = int(row.get("id") or 0)
        except (TypeError, ValueError):
            market_id = 0
            round_id = 0
        if market_id > 0 and self._is_take_profit_locked_market(market_id):
            message = (
                f"BUY rejected: market {market_id} already triggered TAKE_PROFIT; "
                "same-market re-entry is disabled by V42"
            )
            if round_id > 0:
                self._update_round(
                    round_id,
                    state="REJECTED",
                    close_reason="TAKE_PROFIT_MARKET_REENTRY_BLOCKED_V42",
                    error_kind="TAKE_PROFIT_MARKET_REENTRY_BLOCKED_V42",
                    error_message=message,
                )
            self._mark_take_profit_entry_block(
                market_id,
                round_id=round_id or None,
                stage="BEFORE_BUY_QUOTE",
            )
            return
        return super()._open_round(row, poly)

    def _refresh_reentry_confirmation(self) -> str:
        guard = getattr(self, "_v40_reentry_guard", None)
        if isinstance(guard, dict):
            try:
                market_id = int(guard.get("marketId") or 0)
            except (TypeError, ValueError):
                market_id = 0
            if market_id > 0 and self._is_take_profit_locked_market(market_id):
                self._v40_reentry_guard = None
                self._mark_take_profit_entry_block(
                    market_id,
                    round_id=(
                        int(guard.get("sourceRoundId") or 0) or None
                    ),
                    stage="REVERSAL_REENTRY_CONFIRM",
                )
                return "BLOCKED_TAKE_PROFIT_MARKET_V42"
        return super()._refresh_reentry_confirmation()

    def _tick(self) -> None:
        self._v42_blocked_this_tick = False
        result = super()._tick()
        if self._v42_blocked_this_tick and self._current_active_round() is None:
            self.status = TAKE_PROFIT_MARKET_LOCK_STATUS
        return result

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        market = payload.get("market")
        market_id = 0
        if isinstance(market, dict):
            try:
                market_id = int(market.get("market_id") or 0)
            except (TypeError, ValueError):
                market_id = 0
        current_lock = (
            self._take_profit_market_lock(market_id) if market_id > 0 else None
        )
        with self.db_lock:
            recent = [
                {
                    "marketId": int(row["market_id"]),
                    "sourceRoundId": (
                        int(row["source_round_id"])
                        if row["source_round_id"] is not None
                        else None
                    ),
                    "triggeredAtMs": int(row["triggered_at_ms"]),
                    "triggerBid": base._finite(row["trigger_bid"]),
                    "takeProfitPrice": base._finite(row["take_profit_price"]),
                    "reason": str(row["reason"] or TAKE_PROFIT_MARKET_LOCK_REASON),
                }
                for row in self.db.execute(
                    """SELECT market_id,source_round_id,triggered_at_ms,
                              trigger_bid,take_profit_price,reason
                         FROM poly_gap_live_take_profit_market_locks
                        ORDER BY triggered_at_ms DESC, market_id DESC LIMIT 20"""
                ).fetchall()
            ]

        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V42"
        payload["takeProfitMarketLockV42"] = {
            "enabled": True,
            "triggerIntent": TAKE_PROFIT_MARKET_LOCK_REASON,
            "lockAtTriggerBeforeSellOutcome": True,
            "currentMarketLocked": current_lock is not None,
            "currentLock": current_lock,
            "lastLock": dict(self._v42_last_lock or {}) or None,
            "recentLocks": recent,
            "entryBlocks": int(self._v42_take_profit_entry_blocks),
            "sameMarketNormalBuyBlocked": True,
            "sameMarketFastRetryBuyBlocked": True,
            "sameMarketReversalReentryBlocked": True,
            "sameMarketNewShotgunGenerationBlocked": True,
            "ordinaryPolyReversalReentryStillUsesV40": True,
            "sellExitBlocked": False,
            "settlementBlocked": False,
            "persistentAcrossRestart": True,
            "shotgunCancellationChanged": False,
            "existingRestingShotgunGtcLeftUntouched": True,
        }
        payload.setdefault("rules", {}).update(
            takeProfitLocksSameMarketV42=True,
            takeProfitLockOccursAtTriggerV42=True,
            takeProfitLockBlocksNewBuyOnlyV42=True,
            takeProfitLockDoesNotBlockSellV42=True,
            ordinaryPolyReversalCautiousReentryV40PreservedByV42=True,
            sourceFreshnessV41PreservedByV42=True,
            shotgunCancellationChangedByV42=False,
        )
        return payload


base.PolyGapLiveEngine = TakeProfitMarketLockPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
