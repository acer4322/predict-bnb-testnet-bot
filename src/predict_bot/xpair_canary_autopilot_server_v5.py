from __future__ import annotations

import threading
import time
from typing import Any

from . import xpair_canary_autopilot_server as base
from . import xpair_canary_autopilot_server_v2 as v2
from . import xpair_canary_autopilot_server_v3 as v3
from . import xpair_canary_autopilot_server_v4 as v4


def state_payload() -> dict[str, Any]:
    payload = v3.state_payload()
    payload["safety"] = v4.SAFETY.snapshot()
    payload["policy"].update(
        {
            "persistentIncidentLock": True,
            "restartPreservesIncidentLock": True,
            "continuousOrderReconciliation": True,
            "oneSidedFillHardLock": True,
            "unknownPlacementHardLock": True,
            "manualIncidentClearRequired": True,
            "automaticCancel": False,
            "automaticUnwind": False,
        }
    )
    return payload


def resilient_order_watch_loop() -> None:
    while True:
        try:
            v4.order_watch_loop()
        except Exception as exc:
            base.STATE.log(
                f"ORDER_WATCH_RESTARTING {type(exc).__name__}: {str(exc)[:400]}",
                "ERROR",
            )
        time.sleep(5.0)


def install_patches() -> None:
    v3.install_patches()
    v4.install_safety_patches()
    base.state_payload = state_payload
    v4.state_payload = state_payload


def main() -> None:
    install_patches()
    threading.Thread(
        target=v2.monitor_loop,
        name="xpair-autopilot-monitor-v5",
        daemon=True,
    ).start()
    threading.Thread(
        target=resilient_order_watch_loop,
        name="xpair-order-watch-v5",
        daemon=True,
    ).start()
    server = base.ThreadingHTTPServer((base.API_HOST, base.API_PORT), v4.Handler)
    print(
        f"XPAIR autopilot v5 API listening on http://{base.API_HOST}:{base.API_PORT}; "
        "signed quotes are monitored continuously, live placement is one-shot, "
        "and incidents persist across restarts"
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
