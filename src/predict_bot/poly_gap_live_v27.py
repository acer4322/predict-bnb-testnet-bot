from __future__ import annotations

import os
import sqlite3
import time
from typing import Any

from . import poly_gap_live as base
from .cross_oracle_strategy_server import DB_PATH as CROSS_ORACLE_DB_PATH
from .cross_oracle_strategy_chop_guard_v2 import IMMEDIATE_CHOP_BREAKER_REVERSALS
from .poly_gap_live_v25 import RollingPerformancePolyGapLiveEngine
from .poly_gap_live_v26 import LiveExitCountBreakerPolyGapLiveEngine


PAPER_GUARD_DB_POLL_MS = max(
    250,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_CHOP_GUARD_DB_POLL_MS", "500")),
)
PAPER_GUARD_DB_MAX_HEARTBEAT_AGE_MS = max(
    1_000,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_CHOP_GUARD_DB_MAX_AGE_MS", "5000")),
)
PAPER_GUARD_DB_READ_TIMEOUT_SECONDS = max(
    0.05,
    float(os.environ.get("PREDICT_POLY_GAP_LIVE_CHOP_GUARD_DB_READ_TIMEOUT_SECONDS", "0.25")),
)
PAPER_GUARD_LAST_GOOD_GRACE_MS = max(
    0,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_CHOP_GUARD_LAST_GOOD_GRACE_MS", "2000")),
)


class LocalDbPaperGuardPolyGapLiveEngine(LiveExitCountBreakerPolyGapLiveEngine):
    """V27: verify persistent Paper CHOP state from the local SQLite heartbeat.

    V13 used the shared 8769 httpx client to fetch the entire 8768 /state payload
    before every flat-state re-entry decision.  That request inherits the live
    executor's short transport timeout and the Paper endpoint builds a relatively
    large snapshot.  A single transient localhost timeout therefore changed a
    perfectly healthy flat executor into BLOCKED_PAPER_CHOP_GUARD_UNVERIFIED.

    V27 makes the gate depend on the source of truth that already persists the
    regime state:

    - poly_chop_guard_state.paused controls the persistent cross-market pause;
    - the latest poly_chop_guard_markets.last_seen_at_ms is the Paper liveness
      heartbeat and must remain fresh;
    - current-market Paper reversals are retained as diagnostics only (V26);
    - the immediate same-market breaker remains the Echtgeld completed reversal
      exit counter introduced by V26;
    - a short last-good grace absorbs a single SQLite read race, but never extends
      beyond a stale Paper heartbeat.

    Existing positions and exit/reconciliation paths remain untouched because the
    guard is still consulted only while flat and considering NEW exposure.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._next_local_guard_poll_at = 0.0
        self._last_good_local_guard: dict[str, Any] | None = None
        self._last_good_local_guard_at_ms: int | None = None
        super().__init__(*args, **kwargs)

    @staticmethod
    def _read_local_guard_rows() -> tuple[dict[str, Any], dict[str, Any] | None]:
        db = sqlite3.connect(
            f"file:{CROSS_ORACLE_DB_PATH}?mode=ro",
            uri=True,
            timeout=PAPER_GUARD_DB_READ_TIMEOUT_SECONDS,
        )
        db.row_factory = sqlite3.Row
        try:
            state_row = db.execute(
                "SELECT paused,changed_at_ms,reason FROM poly_chop_guard_state WHERE id=1"
            ).fetchone()
            if state_row is None:
                raise RuntimeError("poly_chop_guard_state row is unavailable")
            market_row = db.execute(
                """SELECT market_id,status,confirmed_reversals,distinct_receipts,
                          evaluable,last_seen_at_ms,finalized_at_ms,reason
                     FROM poly_chop_guard_markets
                    ORDER BY last_seen_at_ms DESC, market_id DESC
                    LIMIT 1"""
            ).fetchone()
            return dict(state_row), (dict(market_row) if market_row is not None else None)
        finally:
            db.close()

    def _local_guard_snapshot(self, checked_ms: int) -> dict[str, Any]:
        state_row, market_row = self._read_local_guard_rows()
        if market_row is None:
            raise RuntimeError("Paper CHOP heartbeat market row is unavailable")

        try:
            heartbeat_ms = int(market_row.get("last_seen_at_ms") or 0)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Paper CHOP heartbeat timestamp is unavailable") from exc
        if heartbeat_ms <= 0:
            raise RuntimeError("Paper CHOP heartbeat timestamp is unavailable")

        heartbeat_age_ms = max(0, int(checked_ms) - heartbeat_ms)
        if heartbeat_ms > checked_ms + PAPER_GUARD_DB_MAX_HEARTBEAT_AGE_MS:
            raise RuntimeError(
                f"Paper CHOP DB heartbeat is unexpectedly in the future: {heartbeat_ms}"
            )
        if heartbeat_age_ms > PAPER_GUARD_DB_MAX_HEARTBEAT_AGE_MS:
            raise RuntimeError(
                "Paper CHOP DB heartbeat stale: "
                f"{heartbeat_age_ms}ms > {PAPER_GUARD_DB_MAX_HEARTBEAT_AGE_MS}ms"
            )

        persistent_paused = bool(int(state_row.get("paused") or 0))
        market_status = str(market_row.get("status") or "")
        market_evaluable = bool(int(market_row.get("evaluable") or 0))
        current_reversals = int(market_row.get("confirmed_reversals") or 0)
        current_choppy = bool(
            market_status == "ACTIVE"
            and market_evaluable
            and current_reversals >= IMMEDIATE_CHOP_BREAKER_REVERSALS
        )

        return {
            "verified": True,
            "blocked": persistent_paused,
            "persistentPaused": persistent_paused,
            "currentMarketChoppy": current_choppy,
            "reason": (
                str(state_row.get("reason") or "persistent Paper CHOP pause")
                if persistent_paused
                else "local Paper CHOP DB heartbeat verified; new exposure allowed"
            ),
            "checkedAtMs": int(checked_ms),
            "evaluatedAtMs": int(heartbeat_ms),
            "ageMs": int(heartbeat_age_ms),
            "error": None,
            "source": "LOCAL_CROSS_ORACLE_DB_HEARTBEAT_V27",
            "dbPath": str(CROSS_ORACLE_DB_PATH),
            "paperState": {
                "paused": persistent_paused,
                "stateChangedAtMs": int(state_row.get("changed_at_ms") or 0),
                "reason": state_row.get("reason"),
                "currentMarket": {
                    "marketId": int(market_row.get("market_id") or 0),
                    "status": market_status,
                    "reversals": current_reversals,
                    "distinctReceipts": int(market_row.get("distinct_receipts") or 0),
                    "evaluable": market_evaluable,
                    "lastSeenAtMs": heartbeat_ms,
                    "reason": market_row.get("reason"),
                },
            },
            "paperCurrentMarketChoppyDiagnosticOnly": current_choppy,
            "paperCurrentMarketReversalsDoNotTriggerImmediateBlock": True,
            "liveImmediateBreakerOwner": "LIVE_COMPLETED_REVERSAL_EXITS",
            "full8768SnapshotRequiredForEntryGate": False,
        }

    def _refresh_chop_guard(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not force and now < self._next_local_guard_poll_at:
            return dict(self.last_chop_guard)
        self._next_local_guard_poll_at = now + PAPER_GUARD_DB_POLL_MS / 1000.0
        checked_ms = base._now_ms()

        try:
            state = self._local_guard_snapshot(checked_ms)
            self._last_good_local_guard = dict(state)
            self._last_good_local_guard_at_ms = checked_ms
        except Exception as exc:
            cached = self._last_good_local_guard
            cached_at = self._last_good_local_guard_at_ms
            cache_age = checked_ms - int(cached_at or 0)
            cached_heartbeat_age = None
            if isinstance(cached, dict):
                try:
                    cached_heartbeat_age = checked_ms - int(cached.get("evaluatedAtMs") or 0)
                except (TypeError, ValueError):
                    cached_heartbeat_age = None
            can_grace = bool(
                cached
                and cached_at is not None
                and cache_age <= PAPER_GUARD_LAST_GOOD_GRACE_MS
                and cached_heartbeat_age is not None
                and cached_heartbeat_age <= PAPER_GUARD_DB_MAX_HEARTBEAT_AGE_MS
            )
            if can_grace:
                state = dict(cached or {})
                state.update(
                    checkedAtMs=checked_ms,
                    ageMs=max(0, int(cached_heartbeat_age or 0)),
                    error=str(exc)[:500],
                    source="LOCAL_CROSS_ORACLE_DB_LAST_GOOD_GRACE_V27",
                    usingLastGoodGrace=True,
                    reason=(
                        "transient local Paper CHOP DB read failed; using still-fresh "
                        "last verified persistent state"
                    ),
                )
            else:
                state = {
                    "verified": False,
                    "blocked": None,
                    "persistentPaused": None,
                    "currentMarketChoppy": None,
                    "reason": (
                        "local Paper CHOP DB heartbeat could not be verified; "
                        "new live entries fail closed"
                    ),
                    "checkedAtMs": checked_ms,
                    "evaluatedAtMs": None,
                    "ageMs": None,
                    "error": str(exc)[:500],
                    "source": "LOCAL_CROSS_ORACLE_DB_UNVERIFIED_V27",
                    "dbPath": str(CROSS_ORACLE_DB_PATH),
                    "usingLastGoodGrace": False,
                }

        self.last_chop_guard = state
        self._emit_guard_transition(state)
        return dict(state)

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V27"
        payload["paperGuardVerificationV27"] = {
            "gateSource": "LOCAL_CROSS_ORACLE_DB_HEARTBEAT",
            "dbPath": str(CROSS_ORACLE_DB_PATH),
            "pollMs": PAPER_GUARD_DB_POLL_MS,
            "maxHeartbeatAgeMs": PAPER_GUARD_DB_MAX_HEARTBEAT_AGE_MS,
            "lastGoodGraceMs": PAPER_GUARD_LAST_GOOD_GRACE_MS,
            "full8768SnapshotRequiredForEntryGate": False,
            "persistentPauseStillFailClosedWhenHeartbeatStale": True,
            "paperCurrentMarketChopDiagnosticOnly": True,
            "sameMarketImmediateBreaker": "2_COMPLETED_LIVE_REVERSAL_EXITS",
        }
        return payload


# Preserve V25 rolling stats / V24 tiered loss / V23 execution safety through
# the V26 class, replacing only the concrete live engine launched by base.main().
base.PolyGapLiveEngine = LocalDbPaperGuardPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
