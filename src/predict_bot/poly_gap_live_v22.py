from __future__ import annotations

from typing import Any

from . import poly_gap_live as base
from .binance_exact_market import bucket_start_ms
from .poly_gap_live_v21 import LightweightSharedMarketPolyGapLiveEngine


class PreflightOrderedSharedMarketPolyGapLiveEngine(LightweightSharedMarketPolyGapLiveEngine):
    """V22: resolve shared market identity before Binance execution preflight.

    The base live loop initializes the signed execution/wallet clients before it
    asks ``_prime_market`` for the current Prediction market.  V21's lightweight
    8766 reference therefore never ran when execution preflight itself failed or
    timed out.  V15's outer status wrapper then replaced the real
    ``BLOCKED_PREFLIGHT`` reason with ``WAITING_BINANCE_CURRENT_MARKET``, making a
    wallet/time/API problem look like missing market metadata.

    V22 deliberately separates those concerns:

    - when flat, read and validate the tiny 8766 exact-current market reference
      *before* any signed execution-client requirement;
    - seed ``market_cache`` from that reference without touching Binance trading
      credentials or market/list;
    - then let the inherited execution preflight initialize wallet/order clients;
    - if execution preflight fails, keep the market identity visible and expose
      ``BLOCKED_EXECUTION_PREFLIGHT`` plus the original error instead of masking
      it as market discovery;
    - all V14+ Poly binding, direct book checks, signed quote/order safeguards,
      settlement recovery and no-nearest-window rules remain unchanged.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.shared_market_preload_attempts = 0
        self.shared_market_preload_hits = 0
        self.shared_market_preload_last_at_ms: int | None = None
        self.shared_market_preload_last_market_id: int | None = None
        self.execution_preflight_attempts = 0
        self.execution_preflight_failures = 0
        self.execution_preflight_last_at_ms: int | None = None
        self.execution_preflight_last_ok_at_ms: int | None = None
        self.execution_preflight_last_error: str | None = None
        self.execution_preflight_ready = False

    def _clients_ready(self) -> bool:
        with self.lock:
            return bool(
                self.client is not None
                and self.metadata_client is not None
                and self.wallet_address
                and self.wallet_id
            )

    def _ensure_clients(self) -> bool:
        if self._clients_ready():
            self.execution_preflight_ready = True
            return True

        self.execution_preflight_attempts += 1
        self.execution_preflight_last_at_ms = base._now_ms()
        ok = super()._ensure_clients()
        self.execution_preflight_ready = bool(ok and self._clients_ready())
        if self.execution_preflight_ready:
            self.execution_preflight_last_ok_at_ms = base._now_ms()
            self.execution_preflight_last_error = None
            return True

        self.execution_preflight_failures += 1
        self.execution_preflight_last_error = str(
            self.last_error or "Binance execution client preflight unavailable"
        )[:500]
        return False

    def _preload_shared_market_identity(self) -> dict[str, Any] | None:
        """Populate current market identity without requiring signed Binance APIs."""

        # Use the local wall clock only for choosing the five-minute bucket.  This
        # path must not call metadata.server_timestamp_ms(), otherwise a broken
        # execution preflight could once again prevent the 8766 reference read.
        target_start_ms = bucket_start_ms(base._now_ms())
        with self.lock:
            cached = dict(self.market_cache or {})
        if self._cache_matches_start(cached, target_start_ms):
            return cached

        self.shared_market_preload_attempts += 1
        self.shared_market_preload_last_at_ms = base._now_ms()
        local_cache = self._local_current_cache(target_start_ms)
        if local_cache is None:
            return None

        with self.lock:
            self.market_cache = dict(local_cache)
        self.shared_market_preload_hits += 1
        self.shared_market_preload_last_market_id = int(local_cache["market_id"])
        return dict(local_cache)

    def _tick(self) -> None:
        # Only the flat/new-entry path needs current-market discovery.  Active
        # execution/reconciliation keeps its inherited round-bound identity.
        if self._current_active_round() is None:
            self._preload_shared_market_identity()

        super()._tick()

        # V15 historically overwrote a failed client preflight with
        # WAITING_BINANCE_CURRENT_MARKET whenever market_cache was empty.  Restore
        # the true blocker after the inherited tick so diagnostics cannot lie.
        if not self.execution_preflight_ready and self.execution_preflight_last_error:
            settings = self._settings()
            if base.MASTER_ENABLED and settings["runtimeEnabled"] and not settings["lossTripped"]:
                self.status = "BLOCKED_EXECUTION_PREFLIGHT"
                self.last_error = self.execution_preflight_last_error

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V22"
        payload["marketIdentityPreflight"] = {
            "shared8766BeforeExecutionClient": True,
            "attempts": int(self.shared_market_preload_attempts),
            "hits": int(self.shared_market_preload_hits),
            "lastAtMs": self.shared_market_preload_last_at_ms,
            "lastMarketId": self.shared_market_preload_last_market_id,
            "doesNotRequireSignedBinanceClient": True,
            "usesLocalWallClockOnlyForBucketSelection": True,
            "exactFiveMinuteReferenceStillRequired": True,
        }
        payload["executionPreflight"] = {
            "ready": bool(self.execution_preflight_ready),
            "attempts": int(self.execution_preflight_attempts),
            "failures": int(self.execution_preflight_failures),
            "lastAtMs": self.execution_preflight_last_at_ms,
            "lastOkAtMs": self.execution_preflight_last_ok_at_ms,
            "lastError": self.execution_preflight_last_error,
            "marketDiscoveryFailureCannotMaskThis": True,
            "failedStatus": "BLOCKED_EXECUTION_PREFLIGHT",
        }
        shared = payload.get("binanceSharedMarketReference")
        if isinstance(shared, dict):
            shared["invokedBeforeExecutionPreflight"] = True
            shared["zeroAttemptsWhileFlatIsUnexpected"] = True
        return payload


base.PolyGapLiveEngine = PreflightOrderedSharedMarketPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
