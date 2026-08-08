from __future__ import annotations

import os
import time
from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v12 import StableExitPolyGapLiveEngine


PAPER_CHOP_GUARD_URL = os.environ.get(
    "PREDICT_POLY_GAP_LIVE_CHOP_GUARD_URL",
    "http://127.0.0.1:8768/state",
)
PAPER_CHOP_GUARD_POLL_MS = max(
    250,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_CHOP_GUARD_POLL_MS", "500")),
)
PAPER_CHOP_GUARD_MAX_AGE_MS = max(
    1_000,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_CHOP_GUARD_MAX_AGE_MS", "5000")),
)


class PaperChopGuardedPolyGapLiveEngine(StableExitPolyGapLiveEngine):
    """V13: Paper-owned choppy-market pause gate for new live entries.

    V12/V11 position management remains authoritative.  The Paper guard is read
    only when there is no active live round and the executor would otherwise be
    eligible to look for a new entry.  Therefore a guard pause, stale Paper API,
    or Paper process failure can never block EXIT/ENTRY reconciliation for money
    already at risk; it only suppresses new exposure.

    Fail-closed semantics are intentional: if the Paper guard cannot be verified
    as fresh, V13 does not open a new position.  Automatic resume occurs only
    after the Paper sidecar itself clears its persistent hysteresis state.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._next_chop_guard_poll_at = 0.0
        self.last_chop_guard: dict[str, Any] = {
            "verified": False,
            "paused": None,
            "reason": "not checked yet",
            "checkedAtMs": None,
            "evaluatedAtMs": None,
            "ageMs": None,
            "error": None,
        }
        self._last_chop_guard_event_key: tuple[Any, ...] | None = None

    def _emit_guard_transition(self, state: dict[str, Any]) -> None:
        key = (
            bool(state.get("verified")),
            state.get("paused"),
            str(state.get("reason") or state.get("error") or ""),
        )
        if key == self._last_chop_guard_event_key:
            return
        self._last_chop_guard_event_key = key
        if not state.get("verified"):
            event_type = "PAPER_CHOP_GUARD_UNVERIFIED"
            level = "WARN"
        elif state.get("paused"):
            event_type = "PAPER_CHOP_GUARD_PAUSED"
            level = "WARN"
        else:
            event_type = "PAPER_CHOP_GUARD_ARMED"
            level = "INFO"
        self._event(
            level,
            event_type,
            None,
            None,
            str(state.get("reason") or state.get("error") or event_type)[:800],
        )

    def _refresh_chop_guard(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not force and now < self._next_chop_guard_poll_at:
            return dict(self.last_chop_guard)
        self._next_chop_guard_poll_at = now + PAPER_CHOP_GUARD_POLL_MS / 1000.0
        checked_ms = base._now_ms()
        state: dict[str, Any] = {
            "verified": False,
            "paused": None,
            "reason": None,
            "checkedAtMs": checked_ms,
            "evaluatedAtMs": None,
            "ageMs": None,
            "error": None,
        }
        try:
            response = self.http.get(PAPER_CHOP_GUARD_URL)
            response.raise_for_status()
            payload = response.json()
            guard = payload.get("chopGuard") if isinstance(payload, dict) else None
            if not isinstance(guard, dict) or guard.get("enabled") is not True:
                raise RuntimeError("Paper sidecar did not expose an enabled chopGuard")
            evaluated_raw = guard.get("evaluatedAtMs")
            try:
                evaluated_ms = int(evaluated_raw)
            except (TypeError, ValueError):
                raise RuntimeError("Paper chopGuard evaluatedAtMs unavailable")
            age_ms = max(0, checked_ms - evaluated_ms)
            if evaluated_ms > checked_ms + PAPER_CHOP_GUARD_MAX_AGE_MS:
                raise RuntimeError(
                    f"Paper chopGuard timestamp is unexpectedly in the future: {evaluated_ms}"
                )
            if age_ms > PAPER_CHOP_GUARD_MAX_AGE_MS:
                raise RuntimeError(
                    f"Paper chopGuard stale: {age_ms}ms > {PAPER_CHOP_GUARD_MAX_AGE_MS}ms"
                )
            paused_raw = guard.get("paused")
            if not isinstance(paused_raw, bool):
                raise RuntimeError("Paper chopGuard paused state is not boolean")
            state.update(
                verified=True,
                paused=paused_raw,
                reason=str(guard.get("reason") or "Paper chop guard active")[:500],
                evaluatedAtMs=evaluated_ms,
                ageMs=age_ms,
                guard=guard,
            )
        except Exception as exc:
            state["error"] = str(exc)[:500]
            state["reason"] = "Paper chop guard could not be verified; new live entries fail closed"
        self.last_chop_guard = state
        self._emit_guard_transition(state)
        return dict(state)

    def _tick(self) -> None:
        active = self._current_active_round()
        if active is not None:
            # Never let the risk gate interfere with managing money already at risk.
            return super()._tick()

        # Preserve the more fundamental V12/V11 states without masking them behind
        # a Paper-guard status when live trading is intentionally disabled anyway.
        settings = self._settings()
        if (
            not base.MASTER_ENABLED
            or not settings.get("runtimeEnabled")
            or settings.get("lossTripped")
        ):
            return super()._tick()

        guard = self._refresh_chop_guard()
        if not guard.get("verified"):
            self.status = "BLOCKED_PAPER_CHOP_GUARD_UNVERIFIED"
            self.last_error = str(guard.get("error") or guard.get("reason") or "")[:500]
            return
        if guard.get("paused") is True:
            self.status = "BLOCKED_PAPER_CHOP_GUARD"
            self.last_error = str(guard.get("reason") or "Paper detected choppy markets")[:500]
            return

        return super()._tick()

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V13"
        guard = dict(self.last_chop_guard)
        nested = guard.pop("guard", None)
        payload["paperChopGuard"] = {
            **guard,
            "url": PAPER_CHOP_GUARD_URL,
            "pollMs": PAPER_CHOP_GUARD_POLL_MS,
            "maxAcceptedAgeMs": PAPER_CHOP_GUARD_MAX_AGE_MS,
            "paperState": nested,
            "failClosedForNewEntries": True,
            "existingPositionManagementNeverBlocked": True,
            "automaticResumeOwnedByPaper": True,
        }
        return payload


base.PolyGapLiveEngine = PaperChopGuardedPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
