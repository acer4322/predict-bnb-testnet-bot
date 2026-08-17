from __future__ import annotations

import os
import threading
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from . import xpair_canary_autopilot_server as base
from . import xpair_canary_autopilot_server_v2 as v2
from . import xpair_canary_autopilot_server_v4 as v4
from . import xpair_canary_autopilot_server_v5 as v5

DEFAULT_MAX_PAIR_BUDGET_USDT = Decimal("10.00")
ABSOLUTE_MAX_PAIR_BUDGET_USDT = Decimal("100.00")
MIN_PAIR_BUDGET_USDT = Decimal("2.00")
DEFAULT_SELECTION = "BTC_DOWN_ETH_UP"
LIVE_PRODUCTION_SELECTIONS = {"BTC_DOWN_ETH_UP"}
EXPERIMENTAL_SELECTIONS = {"BTC_UP_ETH_DOWN", "CHEAPEST_ELIGIBLE"}
ARMED_DECISION_STATUSES = {
    "ARMED_NO_ELIGIBLE_VARIANT",
    "ARMED_QUOTE_REJECTED",
    "ARMED_WAITING_SAFE_WALLET",
}


def configured_max_pair_budget() -> Decimal:
    raw = os.environ.get(
        "XPAIR_MAX_PAIR_BUDGET_USDT",
        str(DEFAULT_MAX_PAIR_BUDGET_USDT),
    )
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        value = DEFAULT_MAX_PAIR_BUDGET_USDT
    if not value.is_finite():
        value = DEFAULT_MAX_PAIR_BUDGET_USDT
    return min(
        ABSOLUTE_MAX_PAIR_BUDGET_USDT,
        max(MIN_PAIR_BUDGET_USDT, value),
    )


def experimental_directions_enabled() -> bool:
    return os.environ.get("XPAIR_ALLOW_EXPERIMENTAL_DIRECTIONS", "").strip() == "1"


MAX_PAIR_BUDGET_USDT = configured_max_pair_budget()


def validate_monitor_config(self: base.MonitorConfig) -> None:
    if self.selection not in {
        "BTC_DOWN_ETH_UP",
        "BTC_UP_ETH_DOWN",
        "CHEAPEST_ELIGIBLE",
    }:
        raise ValueError("unsupported XPAIR selection")
    if not MIN_PAIR_BUDGET_USDT <= self.pair_budget_usdt <= MAX_PAIR_BUDGET_USDT:
        raise ValueError(
            f"pair budget must be between {MIN_PAIR_BUDGET_USDT:.2f} and "
            f"{MAX_PAIR_BUDGET_USDT:.2f} USDT"
        )
    if self.balance_buffer_usdt < 0:
        raise ValueError("balance buffer cannot be negative")
    if not Decimal("0") < self.max_total_cost < Decimal("2"):
        raise ValueError("max total cost must be between 0 and 2")
    if not Decimal("0") <= self.max_leg_reprice <= Decimal("0.05"):
        raise ValueError("max leg reprice must be between 0 and 0.05")
    if not 0 < self.entry_window_seconds < self.entry_seconds_left:
        raise ValueError("entry window must be positive and below entry time")
    if not 0 <= self.slippage_bps <= 500:
        raise ValueError("slippage bps must be between 0 and 500")
    if not 0.25 <= self.quote_interval_seconds <= 10:
        raise ValueError("quote interval must be between 0.25 and 10 seconds")


def validate_live_selection(selection: str) -> None:
    normalized = str(selection or "").strip().upper()
    if normalized in LIVE_PRODUCTION_SELECTIONS:
        return
    if normalized in EXPERIMENTAL_SELECTIONS and experimental_directions_enabled():
        return
    if normalized in EXPERIMENTAL_SELECTIONS:
        raise ValueError(
            f"live selection {normalized} is blocked by the empirical direction gate; "
            "BTC_UP_ETH_DOWN produced negative eligible-sample ROI, so live arming "
            "defaults to BTC_DOWN_ETH_UP. Set XPAIR_ALLOW_EXPERIMENTAL_DIRECTIONS=1 "
            "only for an intentional experimental live test"
        )
    raise ValueError("unsupported XPAIR live selection")


def expose_armed_decision_messages(payload: dict[str, Any]) -> dict[str, Any]:
    """Make persisted armed non-entry reasons visible in the existing table."""
    for run in payload.get("recentRuns") or []:
        if not isinstance(run, dict):
            continue
        canonical_status = str(run.get("status") or "")
        message = str(run.get("message") or "").strip()
        if canonical_status in ARMED_DECISION_STATUSES and message:
            run["canonical_status"] = canonical_status
            run["status"] = f"{canonical_status} — {message[:300]}"
    return payload


def state_payload() -> dict[str, Any]:
    payload = expose_armed_decision_messages(v5.state_payload())
    payload["defaults"]["maximumPairBudgetUsdt"] = float(MAX_PAIR_BUDGET_USDT)
    payload["defaults"]["bookMonitorIntervalSeconds"] = float(
        base.STATE.config.interval_seconds
    )
    payload.setdefault("runtime", {})["bookAnalysis"] = v2.latest_book_analysis()
    policy = payload.setdefault("policy", {})
    policy.pop("liveConfirmationPhrase", None)
    policy["liveArmConfirmation"] = "dashboard_button_and_browser_dialog"
    policy["typedLiveConfirmationRequired"] = False
    policy["maximumPairBudgetUsdt"] = float(MAX_PAIR_BUDGET_USDT)
    policy["maximumPairBudgetEnvironment"] = "XPAIR_MAX_PAIR_BUDGET_USDT"
    policy["armedDecisionLedger"] = True
    policy["armedDecisionReasonsVisibleInHistory"] = True
    policy["defaultSelection"] = DEFAULT_SELECTION
    policy["automaticDirectionSelection"] = False
    policy["liveDirectionGate"] = True
    policy["liveAllowedSelections"] = sorted(LIVE_PRODUCTION_SELECTIONS)
    policy["experimentalSelections"] = sorted(EXPERIMENTAL_SELECTIONS)
    policy["experimentalDirectionEnvironment"] = (
        "XPAIR_ALLOW_EXPERIMENTAL_DIRECTIONS"
    )
    policy["experimentalDirectionsEnabled"] = experimental_directions_enabled()
    policy["continuousBookMonitoring"] = True
    policy["bookMonitoringModel"] = (
        "PAIR_ARB_STYLE_INDEPENDENT_OUTCOME_BOOK_EVALUATION"
    )
    policy["bookMonitoringIntervalSeconds"] = float(
        base.STATE.config.interval_seconds
    )
    policy["signedQuotesRestrictedToEntryWindow"] = True
    policy["livePlacementRestrictedToEntryWindow"] = True
    policy["entryWindowSecondsLeft"] = {
        "from": float(base.STATE.config.entry_seconds_left),
        "to": float(
            base.STATE.config.entry_seconds_left
            - base.STATE.config.entry_window_seconds
        ),
    }
    return payload


def validate_button_arm_header(value: str | None) -> None:
    if value != "confirmed":
        raise ValueError("live arm request is missing dashboard confirmation header")


class Handler(v4.Handler):
    server_version = "BTC5MLabXPairAutopilot/6.5"

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path != "/api/xpair-canary/arm":
            super().do_POST()
            return
        if not base.origin_is_allowed(self.headers.get("Origin")):
            self.respond(403, {"error": "origin is not allowed"})
            return
        try:
            payload = self.read_json()
            validate_button_arm_header(
                self.headers.get("X-BTC-Lab-XPair-Live")
            )
            validate_live_selection(
                str(payload.get("selection") or base.STATE.config.selection)
            )
            base.STATE.arm(payload)
            self.respond(200, state_payload())
        except RuntimeError as exc:
            self.respond(409, {"error": str(exc), **state_payload()})
        except (ValueError, ArithmeticError) as exc:
            self.respond(400, {"error": str(exc), **state_payload()})
        except Exception as exc:
            self.respond(500, {"error": str(exc)[:500], **state_payload()})


def install_patches() -> None:
    base.MAX_PAIR_BUDGET_USDT = MAX_PAIR_BUDGET_USDT
    v5.install_patches()
    # v3 adds the 1 USDT-per-leg rule but inherits a legacy 3 USDT validation
    # message. Install the final validator after v5 so edited budgets and error
    # text use the same dynamic limit shown by the dashboard.
    base.MonitorConfig.validate = validate_monitor_config
    base.state_payload = state_payload
    v4.state_payload = state_payload
    # The two opposite-direction variants are not empirically symmetric after
    # the entry filters. Start every new process on the direction that retained
    # positive eligible-sample ROI. Experimental directions remain available
    # for background monitoring, but live arming needs an explicit env opt-in.
    with base.STATE.lock:
        base.STATE.config = replace(
            base.STATE.config,
            selection=DEFAULT_SELECTION,
        )


def main() -> None:
    install_patches()
    threading.Thread(
        target=v2.monitor_loop,
        name="xpair-autopilot-monitor-v6",
        daemon=True,
    ).start()
    threading.Thread(
        target=v5.resilient_order_watch_loop,
        name="xpair-order-watch-v6",
        daemon=True,
    ).start()
    threading.Thread(
        target=v5.incident_order_audit_loop,
        name="xpair-incident-audit-v6",
        daemon=True,
    ).start()
    server = base.ThreadingHTTPServer((base.API_HOST, base.API_PORT), Handler)
    print(
        f"XPAIR autopilot v6.5 API listening on http://{base.API_HOST}:{base.API_PORT}; "
        f"PAIR_ARB-style book evaluation runs every "
        f"{base.STATE.config.interval_seconds:.2f}s, signed quotes and placement "
        f"remain limited to the {base.STATE.config.entry_seconds_left:.0f}-"
        f"{base.STATE.config.entry_seconds_left - base.STATE.config.entry_window_seconds:.0f}s "
        "window, live direction defaults to BTC_DOWN_ETH_UP, and persistent "
        "incident protection remains active"
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
