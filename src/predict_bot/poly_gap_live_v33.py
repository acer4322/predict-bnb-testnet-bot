from __future__ import annotations

import sqlite3
from typing import Any

from . import poly_gap_live as base
from .cross_oracle_strategy_server import DB_PATH as CROSS_ORACLE_DB_PATH
from .poly_gap_live_v32 import FokDepthAndExitRetryPolyGapLiveEngine


class PersistedPaperPauseFallbackPolyGapLiveEngine(FokDepthAndExitRetryPolyGapLiveEngine):
    """V33: let the runtime Live reversal count own same-market blocking.

    V27 intentionally failed closed whenever the Paper health heartbeat could not
    be verified.  That made a transient/stale Paper heartbeat block NEW live BUYs
    before V31's dashboard-configurable completed-reversal threshold was even
    consulted.

    V33 keeps the important persistent Paper pause, but separates it from Paper
    liveness:

    - a healthy V27 guard is unchanged;
    - if only heartbeat verification fails, read the persisted
      poly_chop_guard_state row directly;
    - persisted paused=1 still blocks new exposure immediately;
    - persisted paused=0 is allowed in a DEGRADED mode and execution proceeds to
      the normal Live same-market completed-reversal breaker;
    - if the persisted state itself cannot be read, fail closed exactly as before.

    Existing positions, SELL/exit reconciliation, V32 FOK execution handling and
    V31 runtime reversal threshold remain unchanged.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._paper_guard_degraded_fallback_count = 0
        self._last_persisted_pause_fallback: dict[str, Any] | None = None
        super().__init__(*args, **kwargs)

    @staticmethod
    def _read_persisted_pause_state() -> dict[str, Any]:
        db = sqlite3.connect(
            f"file:{CROSS_ORACLE_DB_PATH}?mode=ro",
            uri=True,
            timeout=0.25,
        )
        db.row_factory = sqlite3.Row
        try:
            row = db.execute(
                "SELECT paused,changed_at_ms,reason FROM poly_chop_guard_state WHERE id=1"
            ).fetchone()
            if row is None:
                raise RuntimeError("poly_chop_guard_state row is unavailable")
            return dict(row)
        finally:
            db.close()

    @staticmethod
    def _heartbeat_only_failure(state: dict[str, Any]) -> bool:
        text = " ".join(
            str(value or "")
            for value in (state.get("error"), state.get("reason"))
        ).lower()
        heartbeat_markers = (
            "heartbeat",
            "health heartbeat",
            "timestamp is unavailable",
            "timestamp unavailable",
            "unexpectedly in the future",
        )
        return any(marker in text for marker in heartbeat_markers)

    def _refresh_chop_guard(self, *, force: bool = False) -> dict[str, Any]:
        state = super()._refresh_chop_guard(force=force)
        if state.get("verified") is True:
            self._last_persisted_pause_fallback = None
            return state

        # Do not turn arbitrary DB/schema/I/O failures into fail-open behavior.
        # Only downgrade failures attributable to the Paper health heartbeat.
        if not self._heartbeat_only_failure(state):
            return state

        checked_ms = base._now_ms()
        try:
            persisted = self._read_persisted_pause_state()
            paused = bool(int(persisted.get("paused") or 0))
            fallback = {
                "verified": True,
                "blocked": paused,
                "persistentPaused": paused,
                "currentMarketChoppy": None,
                "reason": (
                    str(persisted.get("reason") or "persisted Paper CHOP pause")
                    if paused
                    else (
                        "Paper health heartbeat is degraded, but persisted Paper pause is OFF; "
                        "same-market new-entry blocking is delegated to the runtime Live completed-reversal threshold"
                    )
                ),
                "checkedAtMs": checked_ms,
                "evaluatedAtMs": None,
                "ageMs": None,
                "error": state.get("error"),
                "source": "PERSISTED_PAPER_PAUSE_FALLBACK_V33",
                "dbPath": str(CROSS_ORACLE_DB_PATH),
                "degraded": True,
                "heartbeatVerified": False,
                "persistedPauseReadable": True,
                "paperState": {
                    "paused": paused,
                    "stateChangedAtMs": int(persisted.get("changed_at_ms") or 0),
                    "reason": persisted.get("reason"),
                },
                "paperCurrentMarketChoppyDiagnosticOnly": True,
                "paperCurrentMarketReversalsDoNotTriggerImmediateBlock": True,
                "liveImmediateBreakerOwner": "LIVE_COMPLETED_REVERSAL_EXITS_RUNTIME_THRESHOLD",
                "full8768SnapshotRequiredForEntryGate": False,
            }
        except Exception as exc:
            # Persisted state is the authoritative safety fallback. If even that
            # cannot be read, keep the original fail-closed V27 result.
            failed = dict(state)
            existing_error = str(failed.get("error") or "")
            failed["error"] = (
                f"{existing_error}; persisted pause fallback failed: {str(exc)[:300]}"
            ).strip("; ")[:500]
            failed["source"] = "PERSISTED_PAPER_PAUSE_FALLBACK_FAILED_V33"
            failed["persistedPauseReadable"] = False
            return failed

        self._paper_guard_degraded_fallback_count += 1
        self._last_persisted_pause_fallback = dict(fallback)
        self.last_chop_guard = dict(fallback)
        self._emit_guard_transition(fallback)
        return dict(fallback)

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V33"
        threshold = self._same_market_reversal_exit_threshold()
        payload["paperGuardFallbackV33"] = {
            "currentMarketImmediateBlockOwner": "LIVE_COMPLETED_REVERSAL_EXITS",
            "sameMarketReversalExitThreshold": threshold,
            "staleHeartbeatAloneBlocksNewBuy": False,
            "persistedPaperPauseStillBlocks": True,
            "persistedPauseUnreadableStillFailClosed": True,
            "degradedFallbackCount": int(self._paper_guard_degraded_fallback_count),
            "lastFallback": dict(self._last_persisted_pause_fallback or {}),
        }
        verification = payload.get("paperGuardVerificationV27")
        if isinstance(verification, dict):
            verification["staleHeartbeatAloneBlocksNewBuy"] = False
            verification["persistedPauseFallbackV33"] = True
            verification["sameMarketImmediateBreaker"] = (
                f"{threshold}_COMPLETED_LIVE_REVERSAL_EXITS_RUNTIME_EDITABLE"
            )
        return payload


base.PolyGapLiveEngine = PersistedPaperPauseFallbackPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
