from __future__ import annotations

import os
from typing import Any

from . import poly_gap_live as base
from . import poly_gap_live_v15 as v15
from .poly_gap_live_v17 import SettlementRecoveryPolyGapLiveEngine


# V15 intentionally removed nearest-market fallback, but its default one-page
# exact lookup was too narrow: market/list is globally END_DATE sorted and the
# exact BTC 5m topic can legitimately sit beyond the first 100 rows.  Keep the
# strict exact-window validator and only widen how far we search.
V18_EXACT_MAX_PAGES = max(
    2,
    min(
        5,
        int(os.environ.get("PREDICT_POLY_GAP_LIVE_BINANCE_EXACT_MAX_PAGES", "5")),
    ),
)
V18_CURRENT_RETRY_MS = max(
    750,
    int(os.environ.get("PREDICT_POLY_GAP_LIVE_BINANCE_CURRENT_RETRY_MS", "1000")),
)

# Methods on the inherited V15 class resolve these module globals at call time.
# Updating them here therefore hardens V17/V18 without duplicating any execution
# or reconciliation logic.
v15.BINANCE_EXACT_MAX_PAGES = V18_EXACT_MAX_PAGES
v15.BINANCE_CURRENT_RETRY_MS = V18_CURRENT_RETRY_MS


class DeepExactBinanceDiscoveryPolyGapLiveEngine(SettlementRecoveryPolyGapLiveEngine):
    """V18: V17 plus deeper exact-current Binance Prediction discovery.

    No market identity rule is relaxed.  The executor still accepts only the
    exact BTCUSDT CRYPTO_UP_DOWN five-minute start/end window with explicit UP
    and DOWN token IDs.  V18 merely searches additional market/list pages before
    concluding that the exact current topic is unavailable.
    """

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V18"
        payload["binanceExactDiscovery"] = {
            "maxPages": int(v15.BINANCE_EXACT_MAX_PAGES),
            "rowsPerPage": 100,
            "maxRowsScannedPerAttempt": int(v15.BINANCE_EXACT_MAX_PAGES) * 100,
            "currentRetryMs": int(v15.BINANCE_CURRENT_RETRY_MS),
            "strictExactWindowOnly": True,
            "explicitUpDownTokensRequired": True,
            "nearestMarketFallback": False,
            "reason": "avoid false WAITING_BINANCE_CURRENT_MARKET when exact BTC 5m topic is beyond page 1",
        }
        return payload


base.PolyGapLiveEngine = DeepExactBinanceDiscoveryPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
