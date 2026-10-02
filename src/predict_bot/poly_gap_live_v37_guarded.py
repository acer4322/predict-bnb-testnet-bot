from __future__ import annotations

from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v37 import ShotgunEntryPolyGapLiveEngine


SHOTGUN_ZERO_SHARE_EXIT_RECHECK_MS = 500


class GuardedShotgunEntryPolyGapLiveEngine(ShotgunEntryPolyGapLiveEngine):
    """V37 guard: avoid hammering position reads while a GTC ladder has zero fills."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v37_zero_share_exit_retry_at_ms: dict[int, int] = {}
        self._v37_zero_share_exit_throttles = 0
        super().__init__(*args, **kwargs)

    def _exit_round(self, row: dict[str, Any], signal_ms: int) -> None:
        round_id = int(row["id"])
        if self._is_shotgun_round(round_id):
            now_ms = base._now_ms()
            retry_at = int(self._v37_zero_share_exit_retry_at_ms.get(round_id, 0))
            if now_ms < retry_at:
                self._v37_zero_share_exit_throttles += 1
                self.status = "SHOTGUN_ZERO_SHARE_EXIT_RECHECK_COOLDOWN_V37"
                return

        super()._exit_round(row, signal_ms)

        if not self._is_shotgun_round(round_id):
            return
        refreshed = self._round_state(round_id)
        if not isinstance(refreshed, dict):
            return
        shares = base._finite(refreshed.get("shares"))
        if str(refreshed.get("state") or "") == "OPEN" and (shares is None or shares <= 1e-9):
            self._v37_zero_share_exit_retry_at_ms[round_id] = (
                base._now_ms() + SHOTGUN_ZERO_SHARE_EXIT_RECHECK_MS
            )
            self.status = "SHOTGUN_RESTING_WAITING_FILL_V37"
        else:
            self._v37_zero_share_exit_retry_at_ms.pop(round_id, None)

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        shotgun = payload.setdefault("shotgunEntryV37", {})
        shotgun.update(
            {
                "zeroShareExitRecheckMs": SHOTGUN_ZERO_SHARE_EXIT_RECHECK_MS,
                "zeroShareExitThrottleHits": int(self._v37_zero_share_exit_throttles),
            }
        )
        payload.setdefault("rules", {})["shotgunZeroShareExitRecheckMs"] = (
            SHOTGUN_ZERO_SHARE_EXIT_RECHECK_MS
        )
        return payload


base.PolyGapLiveEngine = GuardedShotgunEntryPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
