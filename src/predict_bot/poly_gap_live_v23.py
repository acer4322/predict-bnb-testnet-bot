from __future__ import annotations

import math
from typing import Any

from . import poly_gap_live as base
from .binance_exact_market import bucket_start_ms
from .poly_gap_live_v22 import PreflightOrderedSharedMarketPolyGapLiveEngine


MIN_ENTRY_DELAY_SECONDS = 0.0
MAX_ENTRY_DELAY_SECONDS = 240.0
try:
    _configured_default_delay = float(
        base.os.environ.get("PREDICT_POLY_GAP_LIVE_ENTRY_DELAY_SECONDS", "10")
    )
except ValueError:
    _configured_default_delay = 10.0
DEFAULT_ENTRY_DELAY_SECONDS = max(
    MIN_ENTRY_DELAY_SECONDS,
    min(MAX_ENTRY_DELAY_SECONDS, _configured_default_delay),
)
_BASE_SETTING_KEYS = {
    "runtimeEnabled",
    "stakeUsdt",
    "maximumLossEnabled",
    "maximumLossUsdt",
    "resetLoss",
}


class DelayedEntryPolyGapLiveEngine(PreflightOrderedSharedMarketPolyGapLiveEngine):
    """V23: configurable no-entry window after each five-minute market opens.

    The delay applies only to NEW BUY entries and is measured from the exact
    Binance five-minute market start. Existing OPEN/ENTRY_SYNC/EXIT_SYNC rounds
    continue to be managed immediately, so the safety exit path is never delayed.

    This is deliberately a market-open delay, not a cooldown after every exit.
    Multiple same-market rounds remain possible after the configured opening
    delay has elapsed. A value of zero disables the market-open delay.
    """

    def _ensure_defaults(self) -> None:
        super()._ensure_defaults()
        self._set_default_setting(
            "entry_delay_seconds",
            f"{DEFAULT_ENTRY_DELAY_SECONDS:.3f}",
        )

    def _set_default_setting(self, key: str, value: str) -> None:
        now = base._now_ms()
        with self.db_lock:
            self.db.execute(
                "INSERT OR IGNORE INTO poly_gap_live_settings(key,value,updated_at_ms) VALUES(?,?,?)",
                (key, value, now),
            )
            self.db.commit()

    def _settings(self) -> dict[str, Any]:
        settings = super()._settings()
        raw = self._setting("entry_delay_seconds", str(DEFAULT_ENTRY_DELAY_SECONDS))
        try:
            delay = float(raw)
        except (TypeError, ValueError):
            delay = DEFAULT_ENTRY_DELAY_SECONDS
        if not math.isfinite(delay):
            delay = DEFAULT_ENTRY_DELAY_SECONDS
        settings["entryDelaySeconds"] = max(
            MIN_ENTRY_DELAY_SECONDS,
            min(MAX_ENTRY_DELAY_SECONDS, delay),
        )
        return settings

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = _BASE_SETTING_KEYS | {"entryDelaySeconds"}
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError("unsupported settings: " + ", ".join(unknown))

        delay: float | None = None
        if "entryDelaySeconds" in values:
            try:
                delay = float(values["entryDelaySeconds"])
            except (TypeError, ValueError) as exc:
                raise ValueError("entryDelaySeconds must be a number") from exc
            if (
                not math.isfinite(delay)
                or not MIN_ENTRY_DELAY_SECONDS <= delay <= MAX_ENTRY_DELAY_SECONDS
            ):
                raise ValueError(
                    f"entryDelaySeconds must be between {MIN_ENTRY_DELAY_SECONDS:g} and "
                    f"{MAX_ENTRY_DELAY_SECONDS:g} seconds"
                )

        forwarded = {key: value for key, value in values.items() if key != "entryDelaySeconds"}
        state = super().update_settings(forwarded)
        if delay is not None:
            self._set_setting("entry_delay_seconds", f"{delay:.3f}")
            self._event(
                "INFO",
                "ENTRY_DELAY_UPDATED",
                None,
                None,
                f"new-entry delay set to {delay:.3f}s after each five-minute market open",
            )
            state = self.snapshot()
        return state

    def _entry_delay_state(self) -> dict[str, Any]:
        settings = self._settings()
        delay_seconds = float(settings["entryDelaySeconds"])
        delay_ms = int(round(delay_seconds * 1000.0))
        now_ms = base._now_ms()
        target_start_ms = bucket_start_ms(now_ms)
        with self.lock:
            market = dict(self.market_cache or {})
        market_start_ms = None
        market_id = None
        if market:
            try:
                market_start_ms = int(
                    market.get("start_ms")
                    or (int(market.get("end_ms") or 0) - 300_000)
                )
                market_id = int(market.get("market_id") or 0) or None
            except (TypeError, ValueError):
                market_start_ms = None
                market_id = None
        identity_current = bool(
            market_start_ms is not None
            and abs(int(market_start_ms) - int(target_start_ms)) <= 1500
        )
        elapsed_ms = (
            max(0, now_ms - int(market_start_ms))
            if identity_current and market_start_ms is not None
            else None
        )
        remaining_ms = (
            max(0, delay_ms - int(elapsed_ms))
            if elapsed_ms is not None
            else None
        )
        return {
            "enabled": delay_seconds > 0,
            "configuredSeconds": delay_seconds,
            "minimumAllowedSeconds": MIN_ENTRY_DELAY_SECONDS,
            "maximumAllowedSeconds": MAX_ENTRY_DELAY_SECONDS,
            "marketId": market_id,
            "marketStartMs": market_start_ms,
            "currentBucketStartMs": target_start_ms,
            "identityCurrent": identity_current,
            "elapsedMs": elapsed_ms,
            "remainingMs": remaining_ms,
            "ready": bool(identity_current and remaining_ms == 0),
            "appliesToNewBuyOnly": True,
            "activePositionExitNeverDelayed": True,
            "sameMarketReentryAfterDelayStillAllowed": True,
        }

    def _tick(self) -> None:
        # Never delay position management, entry reconciliation, exit quotes or
        # settlement/reconciliation of an already-active round.
        if self._current_active_round() is not None:
            return super()._tick()

        # Resolve the exact shared 8766 market identity before evaluating the
        # delay. This path does not require signed Binance execution APIs.
        self._preload_shared_market_identity()

        settings = self._settings()
        if (
            not base.MASTER_ENABLED
            or not settings["runtimeEnabled"]
            or settings["lossTripped"]
        ):
            return super()._tick()

        delay = self._entry_delay_state()
        if delay["identityCurrent"] and not delay["ready"]:
            # Warm the signed execution client during the delay so crossing the
            # boundary does not incur avoidable wallet/time-sync startup lag.
            if not self._ensure_clients():
                self.status = "BLOCKED_EXECUTION_PREFLIGHT"
                if self.execution_preflight_last_error:
                    self.last_error = self.execution_preflight_last_error
                return
            self.status = "WAITING_ENTRY_DELAY"
            self.last_error = None
            return

        return super()._tick()

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V23"
        payload["entryDelay"] = self._entry_delay_state()
        rules = payload.get("rules")
        if isinstance(rules, dict):
            rules["newEntryDelayConfigurable"] = True
            rules["newEntryDelayMinimumSeconds"] = MIN_ENTRY_DELAY_SECONDS
            rules["newEntryDelayMaximumSeconds"] = MAX_ENTRY_DELAY_SECONDS
            rules["newEntryDelayAppliesFromMarketStart"] = True
            rules["newEntryDelayDoesNotAffectExits"] = True
        return payload


base.PolyGapLiveEngine = DelayedEntryPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
