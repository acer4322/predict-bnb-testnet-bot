from __future__ import annotations

from typing import Any

from . import binance_time_sync_hardening as time_sync
from . import poly_gap_live as base
from .poly_gap_live_v15 import PrefetchedBinanceMarketPolyGapLiveEngine


class TimeSyncHardenedPolyGapLiveEngine(PrefetchedBinanceMarketPolyGapLiveEngine):
    """V16: V15 plus self-healing Binance signed-request clock synchronization.

    This changes transport timing only.  All V11-V15 entry, exit, market binding,
    CHOP, order reconciliation and exact-market prefetch rules remain unchanged.
    """

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V16"
        with self.lock:
            execution = self.client
            metadata = self.metadata_client
        payload["binanceTimeSync"] = {
            "execution": time_sync.time_sync_snapshot(execution),
            "metadata": time_sync.time_sync_snapshot(metadata),
            "periodicRefresh": True,
            "wallClockJumpDetection": True,
            "signedGet1021AutoRetryOnce": True,
            "signedPost1021NeverBlindRetried": True,
            "manualRestartShouldNotBeRequired": True,
        }
        return payload


base.PolyGapLiveEngine = TimeSyncHardenedPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
