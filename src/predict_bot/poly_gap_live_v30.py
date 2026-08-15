from __future__ import annotations

from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v29 import ReversalHandoffPolyGapLiveEngine


class GuardVerifiedReversalHandoffPolyGapLiveEngine(ReversalHandoffPolyGapLiveEngine):
    """V30: keep V29 fast handoff but refresh the Paper new-exposure guard first.

    Normal V13+ flow refreshes the Paper guard only while FLAT. A V29 reversal
    handoff intentionally creates new exposure before the old round becomes flat,
    so it must explicitly verify the V27 lightweight local-DB guard rather than
    inherit a possibly stale last-good state from the earlier entry.
    """

    def _attempt_handoff_entry(
        self,
        *,
        parent: dict[str, Any],
        market: dict[str, Any],
        expected_direction: str,
    ) -> None:
        guard = self._refresh_chop_guard(force=True)
        market_id = int(parent["market_id"])
        parent_id = int(parent["id"])
        if guard.get("verified") is not True:
            self._last_reversal_handoff = {
                "marketId": market_id,
                "parentRoundId": parent_id,
                "status": "BLOCKED_PAPER_GUARD_UNVERIFIED",
                "blocked": True,
                "paperGuardSource": guard.get("source"),
                "paperGuardError": guard.get("error"),
                "checkedAtMs": base._now_ms(),
            }
            self._event(
                "WARN",
                "REVERSAL_HANDOFF_BLOCKED_PAPER_GUARD_UNVERIFIED",
                market_id,
                parent_id,
                str(guard.get("error") or guard.get("reason") or "Paper guard unverified")[:800],
            )
            return
        if guard.get("blocked") is True:
            self._last_reversal_handoff = {
                "marketId": market_id,
                "parentRoundId": parent_id,
                "status": "BLOCKED_PAPER_CHOP_GUARD",
                "blocked": True,
                "paperPersistentPaused": guard.get("persistentPaused"),
                "paperGuardSource": guard.get("source"),
                "paperGuardReason": guard.get("reason"),
                "checkedAtMs": base._now_ms(),
            }
            self._event(
                "WARN",
                "REVERSAL_HANDOFF_BLOCKED_PAPER_CHOP_GUARD",
                market_id,
                parent_id,
                str(guard.get("reason") or "Paper CHOP guard blocks new exposure")[:800],
            )
            return
        return super()._attempt_handoff_entry(
            parent=parent,
            market=market,
            expected_direction=expected_direction,
        )

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V30"
        handoff = payload.get("reversalHandoff")
        if isinstance(handoff, dict):
            handoff["freshPaperGuardRequiredBeforeHandoffBuy"] = True
            handoff["paperGuardSource"] = "V27_LOCAL_CROSS_ORACLE_DB_HEALTH_HEARTBEAT"
        return payload


base.PolyGapLiveEngine = GuardVerifiedReversalHandoffPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
