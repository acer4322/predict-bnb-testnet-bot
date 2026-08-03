from __future__ import annotations

import threading
from typing import Any
from urllib.parse import urlsplit

from . import xpair_canary_autopilot_server as base
from . import xpair_canary_autopilot_server_v2 as v2
from . import xpair_canary_autopilot_server_v4 as v4
from . import xpair_canary_autopilot_server_v5 as v5


def state_payload() -> dict[str, Any]:
    payload = v5.state_payload()
    policy = payload.setdefault("policy", {})
    policy.pop("liveConfirmationPhrase", None)
    policy["liveArmConfirmation"] = "dashboard_button_and_browser_dialog"
    policy["typedLiveConfirmationRequired"] = False
    return payload


def validate_button_arm_header(value: str | None) -> None:
    if value != "confirmed":
        raise ValueError("live arm request is missing dashboard confirmation header")


class Handler(v4.Handler):
    server_version = "BTC5MLabXPairAutopilot/6.0"

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
    v5.install_patches()
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
        f"XPAIR autopilot v6 API listening on http://{base.API_HOST}:{base.API_PORT}; "
        "the dashboard button and browser dialog arm one live attempt, while "
        "persistent incident protection remains enabled"
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
