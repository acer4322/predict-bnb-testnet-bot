from __future__ import annotations

import os
import threading
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


def state_payload() -> dict[str, Any]:
    payload = v5.state_payload()
    payload["defaults"]["maximumPairBudgetUsdt"] = float(MAX_PAIR_BUDGET_USDT)
    policy = payload.setdefault("policy", {})
    policy.pop("liveConfirmationPhrase", None)
    policy["liveArmConfirmation"] = "dashboard_button_and_browser_dialog"
    policy["typedLiveConfirmationRequired"] = False
    policy["maximumPairBudgetUsdt"] = float(MAX_PAIR_BUDGET_USDT)
    policy["maximumPairBudgetEnvironment"] = "XPAIR_MAX_PAIR_BUDGET_USDT"
    return payload


def validate_button_arm_header(value: str | None) -> None:
    if value != "confirmed":
        raise ValueError("live arm request is missing dashboard confirmation header")


class Handler(v4.Handler):
    server_version = "BTC5MLabXPairAutopilot/6.1"

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
        f"XPAIR autopilot v6.1 API listening on http://{base.API_HOST}:{base.API_PORT}; "
        f"pair budgets from {MIN_PAIR_BUDGET_USDT:.2f} to "
        f"{MAX_PAIR_BUDGET_USDT:.2f} USDT are enabled, while persistent "
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
