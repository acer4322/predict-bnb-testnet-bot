from __future__ import annotations

from typing import Any

from . import wallet_maker_clone_live as base


class SafeWalletMakerCloneEngine(base.WalletMakerCloneEngine):
    """V2 safety patch for the dual-sided real-money clone.

    Normal-live interlock availability is fail-closed, and any incomplete or
    ambiguous two-leg submission immediately disables further clone placements.
    """

    VERSION = "WALLET_MAKER_CLONE_LIVE_V2"

    def _normal_live_conflict(self) -> dict[str, Any]:
        result = super()._normal_live_conflict()
        if result.get("runtimeEnabled") is None:
            result["blocked"] = True
            result["reason"] = f"FAIL_CLOSED: {result.get('reason') or 'normal live state unavailable'}"
            self.last_interlock = dict(result)
        return result

    def _place_pair(self, pair: dict[str, Any], market: dict[str, Any]) -> None:
        super()._place_pair(pair, market)
        with self.db_lock:
            refreshed = self.db.execute(
                "SELECT state FROM wallet_maker_clone_pairs WHERE id=?", (int(pair["id"]),)
            ).fetchone()
        state = str(refreshed["state"] if refreshed else "")
        if state in {"AMBIGUOUS", "PARTIAL_SUBMISSION", "CANCELING"}:
            self._set_setting("runtime_enabled", "0")
            self.status = "AMBIGUOUS_MANUAL_RECONCILIATION"
            self._event(
                "ERROR",
                "CLONE_AUTO_PAUSED_AFTER_INCOMPLETE_PAIR",
                int(market.get("market_id") or 0) or None,
                int(pair["id"]),
                f"pair state={state}; further placements disabled until operator review",
            )

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = self.VERSION
        payload.setdefault("rules", {}).update(
            normalLiveInterlockFailClosed=True,
            incompletePairAutoPause=True,
        )
        return payload


base.WalletMakerCloneEngine = SafeWalletMakerCloneEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
