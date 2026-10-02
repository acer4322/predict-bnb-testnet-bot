from __future__ import annotations

import os
import time
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v16 import TimeSyncHardenedPolyGapLiveEngine


SETTLEMENT_RECOVERY_INTERVAL_MS = max(
    250,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_SETTLEMENT_RECOVERY_INTERVAL_MS", "1000")),
)
ROUND_END_INFERENCE_SAFETY_MS = max(
    0,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_ROUND_END_INFERENCE_SAFETY_MS", "2000")),
)


class SettlementRecoveryPolyGapLiveEngine(TimeSyncHardenedPolyGapLiveEngine):
    """V17: decouple expired hold settlement from the current Binance market cache.

    V15 made ``market_cache`` exact-current and promotes the next Binance market at
    the boundary.  The base V1 settlement gate, however, only settled an active
    round when ``active.market_id == market_cache.market_id``.  If an API error
    delayed settlement across rollover, the cache already pointed at the new
    market and the expired previous round could remain active forever.

    V17 gives every live round its own market-end identity and treats official
    settlement as background accounting once execution risk is gone:

    - new rounds persist their exact Binance ``end_ms``;
    - legacy rounds recover end time from Binance topic detail or, only as a
      conservative fallback, from their five-minute entry bucket plus a safety
      delay;
    - an expired OPEN/fully-reconciled round with no unresolved order moves to
      ``WAITING_SETTLEMENT`` and no longer occupies the one-active-risk slot;
    - ``WAITING_SETTLEMENT`` rows are retried in the background until the shared
      simulation DB has an OFFICIAL winner;
    - ENTRY_SYNC / EXIT_SYNC / any row carrying an unresolved order id remains
      fail-closed and must reconcile before the next live exposure is allowed.

    No proxy winner is used for real-money PnL.  Only OFFICIAL settlement can turn
    ``WAITING_SETTLEMENT`` into ``SETTLED``.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._ensure_settlement_recovery_schema()
        self._next_pending_settlement_scan_mono = 0.0
        self._round_end_lookup_after_mono: dict[int, float] = {}
        self.settlement_recovery_stats: dict[str, Any] = {
            "expiredDetached": 0,
            "backgroundSettled": 0,
            "executionReconcileAttempts": 0,
            "legacyEndRecoveredFromBinance": 0,
            "legacyEndInferredFromEntryBucket": 0,
            "lastDetachedRoundId": None,
            "lastSettledRoundId": None,
            "lastBlockedRoundId": None,
            "lastBlockedReason": None,
        }

    def _ensure_settlement_recovery_schema(self) -> None:
        with self.db_lock:
            columns = {
                str(row[1])
                for row in self.db.execute("PRAGMA table_info(poly_gap_live_rounds)").fetchall()
            }
            if "market_end_ms" not in columns:
                self.db.execute(
                    "ALTER TABLE poly_gap_live_rounds ADD COLUMN market_end_ms INTEGER"
                )
            self.db.execute(
                "CREATE INDEX IF NOT EXISTS idx_poly_gap_live_rounds_waiting_settlement "
                "ON poly_gap_live_rounds(state, market_end_ms, id)"
            )
            self.db.commit()

    def _round_state(self, round_id: int) -> dict[str, Any] | None:
        with self.db_lock:
            row = self.db.execute(
                "SELECT * FROM poly_gap_live_rounds WHERE id=?",
                (int(round_id),),
            ).fetchone()
        return dict(row) if row else None

    def _persist_round_end(self, round_id: int, end_ms: int) -> None:
        if int(end_ms) <= 0:
            return
        with self.db_lock:
            self.db.execute(
                "UPDATE poly_gap_live_rounds SET market_end_ms=?, updated_at_ms=? WHERE id=?",
                (int(end_ms), base._now_ms(), int(round_id)),
            )
            self.db.commit()

    def _insert_round(self, **kwargs: Any) -> dict[str, Any]:
        row = super()._insert_round(**kwargs)
        market = kwargs.get("market")
        if isinstance(market, dict):
            try:
                end_ms = int(market.get("end_ms") or 0)
            except (TypeError, ValueError):
                end_ms = 0
            if end_ms > 0:
                self._persist_round_end(int(row["id"]), end_ms)
                refreshed = self._round_state(int(row["id"]))
                if refreshed is not None:
                    return refreshed
        return row

    @staticmethod
    def _plausible_round_end(row: dict[str, Any], end_ms: int) -> bool:
        reference = int(
            row.get("entry_signal_at_ms")
            or row.get("created_at_ms")
            or 0
        )
        if reference <= 0 or end_ms <= 0:
            return False
        # A five-minute Prediction round can be entered at any point in its own
        # window.  The end must be after the entry and no more than roughly one
        # window away (plus a small publication skew allowance).
        return reference < end_ms <= reference + 305_000

    def _recover_round_end_from_binance(self, row: dict[str, Any]) -> int | None:
        round_id = int(row["id"])
        now_mono = time.monotonic()
        if now_mono < self._round_end_lookup_after_mono.get(round_id, 0.0):
            return None
        self._round_end_lookup_after_mono[round_id] = now_mono + 5.0

        if not self._ensure_clients():
            return None
        with self.lock:
            metadata = self.metadata_client
        if metadata is None:
            return None
        try:
            detail = metadata.market_detail(int(row["topic_id"]))
        except Exception as exc:
            self.settlement_recovery_stats["lastBlockedReason"] = (
                f"round-end metadata lookup failed: {str(exc)[:240]}"
            )
            return None
        end_ms = base._first_number(detail, ("endDate", "end_ms", "endMs"))
        if end_ms is None:
            return None
        normalized = int(end_ms)
        if not self._plausible_round_end(row, normalized):
            return None
        self._persist_round_end(round_id, normalized)
        self.settlement_recovery_stats["legacyEndRecoveredFromBinance"] = int(
            self.settlement_recovery_stats["legacyEndRecoveredFromBinance"]
        ) + 1
        return normalized

    def _round_end_ms(self, row: dict[str, Any]) -> int | None:
        try:
            persisted = int(row.get("market_end_ms") or 0)
        except (TypeError, ValueError):
            persisted = 0
        if persisted > 0:
            return persisted

        with self.lock:
            current = dict(self.market_cache or {})
        try:
            if int(current.get("market_id") or -1) == int(row["market_id"]):
                cached_end = int(current.get("end_ms") or 0)
            else:
                cached_end = 0
        except (TypeError, ValueError):
            cached_end = 0
        if cached_end > 0 and self._plausible_round_end(row, cached_end):
            self._persist_round_end(int(row["id"]), cached_end)
            return cached_end

        recovered = self._recover_round_end_from_binance(row)
        if recovered is not None:
            return recovered

        reference = int(
            row.get("entry_signal_at_ms")
            or row.get("created_at_ms")
            or 0
        )
        if reference <= 0:
            return None
        inferred = ((reference // 300_000) + 1) * 300_000
        if not self._plausible_round_end(row, inferred):
            return None
        self._persist_round_end(int(row["id"]), inferred)
        self.settlement_recovery_stats["legacyEndInferredFromEntryBucket"] = int(
            self.settlement_recovery_stats["legacyEndInferredFromEntryBucket"]
        ) + 1
        return inferred

    def _detach_expired_hold(self, row: dict[str, Any], end_ms: int) -> None:
        round_id = int(row["id"])
        refreshed = self._update_round(
            round_id,
            state="WAITING_SETTLEMENT",
            error_kind=None,
            error_message=None,
        )
        # ``_update_round`` does not know the V17-only column; persist it separately.
        self._persist_round_end(round_id, end_ms)
        self.settlement_recovery_stats["expiredDetached"] = int(
            self.settlement_recovery_stats["expiredDetached"]
        ) + 1
        self.settlement_recovery_stats["lastDetachedRoundId"] = round_id
        self._event(
            "INFO",
            "ROUND_EXPIRED_WAITING_OFFICIAL_SETTLEMENT",
            int(row["market_id"]),
            round_id,
            (
                "execution risk is resolved; official settlement moved to background "
                "and no longer blocks the next market"
            ),
        )
        # Try once immediately.  If 8766 still has only PENDING/proxy settlement,
        # the row simply remains WAITING_SETTLEMENT for the background scanner.
        self._settle_hold(refreshed or self._round_state(round_id) or row)

    def _recover_pending_settlements(self) -> None:
        now_mono = time.monotonic()
        if now_mono < self._next_pending_settlement_scan_mono:
            return
        self._next_pending_settlement_scan_mono = (
            now_mono + SETTLEMENT_RECOVERY_INTERVAL_MS / 1000.0
        )
        with self.db_lock:
            rows = [
                dict(row)
                for row in self.db.execute(
                    """SELECT * FROM poly_gap_live_rounds
                       WHERE state='WAITING_SETTLEMENT'
                       ORDER BY id ASC LIMIT 20"""
                ).fetchall()
            ]
        for row in rows:
            round_id = int(row["id"])
            before = self._round_state(round_id)
            self._settle_hold(row)
            after = self._round_state(round_id)
            if (
                before is not None
                and after is not None
                and str(before.get("state")) == "WAITING_SETTLEMENT"
                and str(after.get("state")) == "SETTLED"
            ):
                self.settlement_recovery_stats["backgroundSettled"] = int(
                    self.settlement_recovery_stats["backgroundSettled"]
                ) + 1
                self.settlement_recovery_stats["lastSettledRoundId"] = round_id

    def _handle_expired_active(self, row: dict[str, Any], end_ms: int) -> bool:
        now_ms = base._now_ms()
        if now_ms < int(end_ms) + ROUND_END_INFERENCE_SAFETY_MS:
            return False

        state = str(row.get("state") or "")
        exit_order_id = str(row.get("exit_order_id") or "").strip()
        entry_order_id = str(row.get("entry_order_id") or "").strip()

        # Entry execution must still be reconciled before the row can be treated
        # as a known held position or no-fill.
        if state == "ENTRY_SYNC":
            self.settlement_recovery_stats["executionReconcileAttempts"] = int(
                self.settlement_recovery_stats["executionReconcileAttempts"]
            ) + 1
            self.settlement_recovery_stats["lastBlockedRoundId"] = int(row["id"])
            self.settlement_recovery_stats["lastBlockedReason"] = (
                f"expired round still reconciling BUY order {entry_order_id or 'unknown'}"
            )
            self._sync_entry(row)
            return True

        # Never detach an unresolved SELL.  V10/V11 order reconciliation is the
        # authority for deciding FILLED vs definite NO_FILL.
        if exit_order_id:
            self.settlement_recovery_stats["executionReconcileAttempts"] = int(
                self.settlement_recovery_stats["executionReconcileAttempts"]
            ) + 1
            self.settlement_recovery_stats["lastBlockedRoundId"] = int(row["id"])
            self.settlement_recovery_stats["lastBlockedReason"] = (
                f"expired round still reconciling SELL order {exit_order_id}"
            )
            self._settle_hold(row)
            return True

        # A previously reconciled NO_FILL may leave the state as EXIT_SYNC with
        # the order id cleared while official settlement is still unavailable.
        if state in {"OPEN", "EXIT_SYNC"}:
            self._detach_expired_hold(row, end_ms)
            return True

        # Preserve the inherited fail-closed restart handling for synchronous
        # quote/write states and any future state we do not explicitly recognize.
        return False

    def _tick(self) -> None:
        self._recover_pending_settlements()

        active = self._current_active_round()
        if active is not None:
            end_ms = self._round_end_ms(active)
            if end_ms is not None and self._handle_expired_active(active, end_ms):
                # If settlement became terminal immediately, the next loop can arm
                # the current market.  If it moved to WAITING_SETTLEMENT, it is no
                # longer returned by _current_active_round and likewise will not
                # block the next loop.
                if self._current_active_round() is None:
                    self.status = "SETTLEMENT_PENDING_BACKGROUND"
                return

        super()._tick()

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V17"
        with self.db_lock:
            pending_rows = [
                dict(row)
                for row in self.db.execute(
                    """SELECT id,market_id,round_no,side,market_end_ms,updated_at_ms
                       FROM poly_gap_live_rounds
                       WHERE state='WAITING_SETTLEMENT'
                       ORDER BY id DESC LIMIT 20"""
                ).fetchall()
            ]
        payload["settlementRecovery"] = {
            **self.settlement_recovery_stats,
            "pendingCount": len(pending_rows),
            "pendingRounds": pending_rows,
            "scanIntervalMs": SETTLEMENT_RECOVERY_INTERVAL_MS,
            "roundEndInferenceSafetyMs": ROUND_END_INFERENCE_SAFETY_MS,
            "waitingSettlementBlocksNewMarket": False,
            "unresolvedExecutionStillBlocksNewMarket": True,
            "officialWinnerRequiredForLivePnl": True,
            "proxySettlementNeverUsedForLivePnl": True,
            "roundEndBoundToOriginalRound": True,
        }
        return payload


base.PolyGapLiveEngine = SettlementRecoveryPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
