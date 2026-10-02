from __future__ import annotations

import threading
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v43 import QuoteTimestampRaceFixedPolyGapLiveEngine
from .poly_gap_runtime_decision import build_runtime_decision


DUPLICATE_INFLIGHT_STATUS = "EXECUTION_DUPLICATE_INFLIGHT_BLOCKED_V44"


class IntegratedDecisionAndIdempotencyPolyGapLiveEngine(
    QuoteTimestampRaceFixedPolyGapLiveEngine
):
    """V44: consolidate diagnostics and execution idempotency without policy drift.

    This version deliberately does not change any strategy threshold or order
    policy.  V40 immediate reversal SELL / cautious re-entry, V41/V43 source-age
    safety and V42 TAKE_PROFIT same-market lock remain authoritative.

    V44 adds two infrastructure layers inspired by mature trading systems:
      1. one normalized runtimeDecisionV44 envelope for Dashboard/log consumers;
      2. an in-memory per-round/action in-flight lock, while upgrading V32's
         already durable execution-attempt ledger with a canonical UNIQUE
         action_key.  Existing no-fill retries remain legal because every retry
         receives a new V32 attempt_no.  Existing exchange reconciliation remains
         the authority after submission/ambiguity.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v44_execution_lock = threading.RLock()
        self._v44_inflight_actions: set[tuple[int, str]] = set()
        self._v44_duplicate_inflight_blocks = 0
        self._v44_last_action_key: str | None = None
        super().__init__(*args, **kwargs)

    def _create_schema(self) -> None:
        super()._create_schema()
        with self.db_lock:
            columns = {
                str(row["name"])
                for row in self.db.execute(
                    "PRAGMA table_info(poly_gap_live_execution_attempts)"
                ).fetchall()
            }
            if "action_key" not in columns:
                self.db.execute(
                    "ALTER TABLE poly_gap_live_execution_attempts ADD COLUMN action_key TEXT"
                )
            # V32 already guarantees UNIQUE(round_id, action, attempt_no).  Backfill
            # the equivalent stable key so external tools no longer need to infer
            # identity from version-specific columns.
            self.db.execute(
                """UPDATE poly_gap_live_execution_attempts
                      SET action_key = CAST(market_id AS TEXT) || ':' ||
                                       CAST(round_id AS TEXT) || ':' ||
                                       action || ':' || CAST(attempt_no AS TEXT)
                    WHERE action_key IS NULL OR action_key=''"""
            )
            self.db.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_poly_gap_live_execution_action_key_v44
                     ON poly_gap_live_execution_attempts(action_key)"""
            )
            self.db.commit()

    def _begin_attempt(
        self,
        row: dict[str, Any],
        *,
        action: str,
        depth: dict[str, Any] | None,
    ) -> int:
        attempt_id = super()._begin_attempt(row, action=action, depth=depth)
        with self.db_lock:
            attempt = self.db.execute(
                """SELECT market_id,round_id,action,attempt_no
                     FROM poly_gap_live_execution_attempts WHERE id=?""",
                (int(attempt_id),),
            ).fetchone()
            if attempt is None:
                return attempt_id
            action_key = (
                f"{int(attempt['market_id'])}:{int(attempt['round_id'])}:"
                f"{str(attempt['action'])}:{int(attempt['attempt_no'])}"
            )
            self.db.execute(
                "UPDATE poly_gap_live_execution_attempts SET action_key=? WHERE id=?",
                (action_key, int(attempt_id)),
            )
            self.db.commit()
        self._v44_last_action_key = action_key
        return attempt_id

    def _enter_execution(self, row: dict[str, Any], action: str) -> bool:
        key = (int(row["id"]), str(action).upper())
        with self._v44_execution_lock:
            if key in self._v44_inflight_actions:
                self._v44_duplicate_inflight_blocks += 1
                self.status = DUPLICATE_INFLIGHT_STATUS
                self.last_error = (
                    f"duplicate {key[1]} execution call suppressed while round {key[0]} "
                    "already has an in-flight execution path"
                )
                self._event(
                    "WARN",
                    DUPLICATE_INFLIGHT_STATUS,
                    int(row.get("market_id") or 0) or None,
                    int(row["id"]),
                    self.last_error,
                )
                return False
            self._v44_inflight_actions.add(key)
            return True

    def _leave_execution(self, row: dict[str, Any], action: str) -> None:
        key = (int(row["id"]), str(action).upper())
        with self._v44_execution_lock:
            self._v44_inflight_actions.discard(key)

    def _open_round(self, row: dict[str, Any], poly: dict[str, Any]) -> None:
        # This lock is intentionally around the logical BUY path, not around an
        # entire market lifetime. A definitive V36 NO_FILL later enters again as a
        # new call and receives a new V32 attempt_no/actionKey.
        if not self._enter_execution(row, "BUY"):
            return
        try:
            return super()._open_round(row, poly)
        finally:
            self._leave_execution(row, "BUY")

    def _exit_round(self, row: dict[str, Any], signal_ms: int) -> None:
        # Reconciliation of an existing exit order is still delegated unchanged;
        # the short in-flight lock only prevents two concurrent SELL placement
        # paths from entering the inherited state machine for the same round.
        if not self._enter_execution(row, "SELL"):
            return
        try:
            return super()._exit_round(row, signal_ms)
        finally:
            self._leave_execution(row, "SELL")

    def _recent_action_keys(self, limit: int = 8) -> list[dict[str, Any]]:
        with self.db_lock:
            rows = self.db.execute(
                """SELECT action_key,round_id,market_id,action,attempt_no,outcome,
                          order_status,order_id,created_at_ms
                     FROM poly_gap_live_execution_attempts
                    ORDER BY id DESC LIMIT ?""",
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V44"

        with self._v44_execution_lock:
            inflight = [
                {"roundId": round_id, "action": action}
                for round_id, action in sorted(self._v44_inflight_actions)
            ]
        payload["executionIdempotencyV44"] = {
            "enabled": True,
            "inMemoryPerRoundActionLock": True,
            "inFlight": inflight,
            "duplicateInFlightBlocks": int(self._v44_duplicate_inflight_blocks),
            "sqliteAttemptUniqueConstraintInheritedV32": True,
            "sqliteCanonicalActionKeyUnique": True,
            "actionKeyFormat": "marketId:roundId:BUY|SELL:attemptNo",
            "lastActionKey": self._v44_last_action_key,
            "recentActions": self._recent_action_keys(),
            "definitiveNoFillCreatesNewAttempt": True,
            "v36FastRetryPreserved": True,
            "exchangeReconciliationInherited": True,
            "ambiguousBlindRetryAllowed": False,
        }

        # Build this last so it can observe every inherited Vxx diagnostic field.
        payload["runtimeDecisionV44"] = build_runtime_decision(payload)
        payload.setdefault("rules", {}).update(
            unifiedRuntimeDecisionV44=True,
            executionInFlightDedupV44=True,
            executionCanonicalActionKeyV44=True,
            executionAttemptUniquenessInheritedV32=True,
            exchangeReconciliationStillAuthoritativeV44=True,
            v36FastRetrySemanticsChangedV44=False,
            v40ImmediateReversalSellPreservedByV44=True,
            v40CautiousReentryPreservedByV44=True,
            v42TakeProfitSameMarketLockPreservedByV44=True,
            v43QuoteFreshnessPreservedByV44=True,
        )
        return payload


base.PolyGapLiveEngine = IntegratedDecisionAndIdempotencyPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
