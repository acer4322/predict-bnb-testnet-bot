from __future__ import annotations

import threading
from typing import Any

from . import xpair_canary_autopilot_server as base
from . import xpair_canary_autopilot_server_v2 as v2
from . import xpair_canary_autopilot_server_v4 as v4
from . import xpair_canary_autopilot_server_v5 as v5
from . import xpair_canary_autopilot_server_v7 as v7
from . import xpair_canary_autopilot_server_v8 as v8
from .xpair_dashboard_paper_sim import (
    PAPER_MIN_SECONDS_AFTER_START,
    PAPER_MIN_SECONDS_LEFT,
    PAPER_VARIANTS,
    paper_dashboard_payload,
    paper_simulation_loop,
)

_V8_STATE_PAYLOAD = v8.state_payload


def state_payload() -> dict[str, Any]:
    payload = _V8_STATE_PAYLOAD()
    payload["paperSimulation"] = paper_dashboard_payload()
    policy = payload.setdefault("policy", {})
    policy.update(
        {
            "dashboardPaperSimulation": True,
            "paperSimulationIndependentOfLiveArm": True,
            "paperSimulationDualVariant": True,
            "paperSimulationVariants": list(PAPER_VARIANTS),
            "paperSimulationOneEntryPerVariantPerAlignedMarket": True,
            "paperSimulationEntryRule": (
                "FIRST_ELIGIBLE_ONCE_PER_VARIANT_PER_ALIGNED_MARKET"
            ),
            "paperSimulationMinimumSecondsAfterStart": PAPER_MIN_SECONDS_AFTER_START,
            "paperSimulationMinimumSecondsLeft": PAPER_MIN_SECONDS_LEFT,
            "paperSimulationUsesSignedQuote": False,
            "paperSimulationSettlementUsesMarketEndPrice": True,
            "paperSimulationSeparateComparisonLedger": True,
        }
    )
    return payload


class Handler(v8.Handler):
    server_version = "BTC5MLabXPairAutopilot/9.1"


def install_patches() -> None:
    v8.install_patches()
    base.state_payload = state_payload
    v4.state_payload = state_payload


def main() -> None:
    install_patches()
    threading.Thread(
        target=v2.monitor_loop,
        name="xpair-autopilot-monitor-v9",
        daemon=True,
    ).start()
    threading.Thread(
        target=v5.resilient_order_watch_loop,
        name="xpair-order-watch-v9",
        daemon=True,
    ).start()
    threading.Thread(
        target=v5.incident_order_audit_loop,
        name="xpair-incident-audit-v9",
        daemon=True,
    ).start()
    threading.Thread(
        target=v7.exit_guard_loop,
        name="xpair-exit-guard-v9",
        daemon=True,
    ).start()
    threading.Thread(
        target=v8.placement_incident_exit_loop,
        name="xpair-placement-incident-exit-v9",
        daemon=True,
    ).start()
    threading.Thread(
        target=paper_simulation_loop,
        name="xpair-dashboard-paper-v9",
        daemon=True,
    ).start()
    server = base.ThreadingHTTPServer((base.API_HOST, base.API_PORT), Handler)
    print(
        "XPAIR autopilot v9.1 API listening on "
        f"http://{base.API_HOST}:{base.API_PORT}; dashboard paper simulation tracks "
        "BTC_DOWN_ETH_UP and BTC_UP_ETH_DOWN independently, once per variant per "
        f"aligned market, from {PAPER_MIN_SECONDS_AFTER_START:.0f}s after start until "
        f"{PAPER_MIN_SECONDS_LEFT:.0f}s before settlement"
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
