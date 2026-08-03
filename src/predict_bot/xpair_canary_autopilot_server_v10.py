from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any

from . import xpair_canary_autopilot_server as base
from . import xpair_canary_autopilot_server_v2 as v2
from . import xpair_canary_autopilot_server_v4 as v4
from . import xpair_canary_autopilot_server_v5 as v5
from . import xpair_canary_autopilot_server_v7 as v7
from . import xpair_canary_autopilot_server_v8 as v8
from . import xpair_canary_autopilot_server_v9 as v9
from .core import BinancePredictionTradingClient
from .xpair_btc_eth_canary_cedefi import preflight_wallet
from .xpair_dashboard_paper_sim import paper_simulation_loop

EXECUTION_START_GUARD_SECONDS = 5.0
EXECUTION_MIN_SECONDS_LEFT = 20.0
FIVE_MINUTE_MARKET_SECONDS = 300.0
EXECUTION_ENTRY_SECONDS_LEFT = FIVE_MINUTE_MARKET_SECONDS - EXECUTION_START_GUARD_SECONDS
EXECUTION_WINDOW_SECONDS = EXECUTION_ENTRY_SECONDS_LEFT - EXECUTION_MIN_SECONDS_LEFT
LIVE_SELECTION = "BTC_DOWN_ETH_UP"

_ORIGINAL_FROM_PAYLOAD = base.MonitorConfig.from_payload.__func__
_V9_STATE_PAYLOAD = v9.state_payload
_PATCHED = False


def normalize_first_eligible_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Force the production live direction and the safe market lifetime.

    The dashboard paper experiment evaluates both opposite variants independently.
    That must never leak into the live executor configuration. Legacy browsers or
    stale form state may still submit BTC_UP_ETH_DOWN/CHEAPEST_ELIGIBLE, so the
    server normalizes every config and arm payload to the production direction.
    """
    normalized = dict(payload)
    normalized["selection"] = LIVE_SELECTION
    normalized["entrySecondsLeft"] = EXECUTION_ENTRY_SECONDS_LEFT
    normalized["entryWindowSeconds"] = EXECUTION_WINDOW_SECONDS
    return normalized


def first_eligible_from_payload(
    cls: type[base.MonitorConfig],
    payload: dict[str, Any],
    current: base.MonitorConfig,
) -> base.MonitorConfig:
    candidate = _ORIGINAL_FROM_PAYLOAD(
        cls,
        normalize_first_eligible_payload(payload),
        current,
    )
    return replace(
        candidate,
        selection=LIVE_SELECTION,
        entry_seconds_left=EXECUTION_ENTRY_SECONDS_LEFT,
        entry_window_seconds=EXECUTION_WINDOW_SECONDS,
    )


def execution_allowed(seconds_left: float) -> bool:
    return EXECUTION_MIN_SECONDS_LEFT <= float(seconds_left) <= EXECUTION_ENTRY_SECONDS_LEFT


def strict_live_preflight_allow_settled_positions(
    client: BinancePredictionTradingClient,
    config: base.MonitorConfig,
) -> tuple[str, str, Any]:
    """Allow filled positions waiting for settlement, but never working orders.

    Available balance is still checked after other positions reserve their funds.
    XPAIR's own durable tracking/incident lock remains a separate hard gate.
    """
    return preflight_wallet(
        client,
        account_type=config.account_type,
        required_balance=config.required_balance,
        allow_existing_positions=True,
        allow_existing_orders=False,
    )


def state_payload() -> dict[str, Any]:
    payload = _V9_STATE_PAYLOAD()
    defaults = payload.setdefault("defaults", {})
    defaults["selection"] = LIVE_SELECTION
    defaults["entrySecondsLeft"] = EXECUTION_ENTRY_SECONDS_LEFT
    defaults["entryWindowSeconds"] = EXECUTION_WINDOW_SECONDS
    policy = payload.setdefault("policy", {})
    policy.update(
        {
            "executionMode": "FIRST_ELIGIBLE_CONTINUOUS",
            "firstEligibleLiveExecution": True,
            "targetSecondsControlsIgnored": True,
            "executionStartGuardSeconds": EXECUTION_START_GUARD_SECONDS,
            "executionMinimumSecondsLeft": EXECUTION_MIN_SECONDS_LEFT,
            "signedQuotesRestrictedToEntryWindow": False,
            "livePlacementRestrictedToEntryWindow": False,
            "signedQuotesRestrictedToSafeMarketLifetime": True,
            "livePlacementRestrictedToSafeMarketLifetime": True,
            "quoteOnEveryEligibleCooldown": True,
            "quoteCooldownSeconds": float(base.STATE.config.quote_interval_seconds),
            "stopQuotingMarketAfterLiveAttempt": True,
            "oneLiveAttemptPerArm": True,
            "liveSelectionFixed": True,
            "liveFixedSelection": LIVE_SELECTION,
            "paperSimulationTestsBothDirections": True,
            "paperDirectionsDoNotAffectLiveSelection": True,
            "existingFilledPositionsAllowed": True,
            "existingWorkingOrdersBlocked": True,
            "availableBalanceStillRequired": True,
            "persistentXpairSafetyLockStillRequired": True,
        }
    )
    return payload


class Handler(v9.Handler):
    server_version = "BTC5MLabXPairAutopilot/10.2"


def install_patches() -> None:
    global _PATCHED
    if _PATCHED:
        return
    v9.install_patches()
    base.MonitorConfig.from_payload = classmethod(first_eligible_from_payload)
    base.strict_live_preflight = strict_live_preflight_allow_settled_positions
    with base.STATE.lock:
        base.STATE.config = replace(
            base.STATE.config,
            selection=LIVE_SELECTION,
            entry_seconds_left=EXECUTION_ENTRY_SECONDS_LEFT,
            entry_window_seconds=EXECUTION_WINDOW_SECONDS,
        )
    base.state_payload = state_payload
    v4.state_payload = state_payload
    _PATCHED = True


def main() -> None:
    install_patches()
    threading.Thread(
        target=v2.monitor_loop,
        name="xpair-autopilot-monitor-v10",
        daemon=True,
    ).start()
    threading.Thread(
        target=v5.resilient_order_watch_loop,
        name="xpair-order-watch-v10",
        daemon=True,
    ).start()
    threading.Thread(
        target=v5.incident_order_audit_loop,
        name="xpair-incident-audit-v10",
        daemon=True,
    ).start()
    threading.Thread(
        target=v7.exit_guard_loop,
        name="xpair-exit-guard-v10",
        daemon=True,
    ).start()
    threading.Thread(
        target=v8.placement_incident_exit_loop,
        name="xpair-placement-incident-exit-v10",
        daemon=True,
    ).start()
    threading.Thread(
        target=paper_simulation_loop,
        name="xpair-dashboard-paper-v10",
        daemon=True,
    ).start()
    server = base.ThreadingHTTPServer((base.API_HOST, base.API_PORT), Handler)
    print(
        "XPAIR autopilot v10.2 API listening on "
        f"http://{base.API_HOST}:{base.API_PORT}; live direction is fixed to "
        f"{LIVE_SELECTION}, filled Prediction positions waiting for settlement are "
        "allowed when available balance is sufficient, working orders remain blocked, "
        "and armed live execution requests signed quotes on the first eligible "
        f"book state from {EXECUTION_START_GUARD_SECONDS:.0f}s after market start "
        f"until {EXECUTION_MIN_SECONDS_LEFT:.0f}s before settlement"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        base.STATE.disarm("server_shutdown")
        server.server_close()


if __name__ == "__main__":
    main()
