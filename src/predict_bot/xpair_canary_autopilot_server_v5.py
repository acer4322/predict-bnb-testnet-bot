from __future__ import annotations

import os
import threading
import time
from typing import Any

from . import xpair_canary_autopilot_server as base
from . import xpair_canary_autopilot_server_v2 as v2
from . import xpair_canary_autopilot_server_v3 as v3
from . import xpair_canary_autopilot_server_v4 as v4
from .core import BinancePredictionTradingClient


def state_payload() -> dict[str, Any]:
    payload = v3.state_payload()
    payload["safety"] = v4.SAFETY.snapshot()
    payload["policy"].update(
        {
            "persistentIncidentLock": True,
            "restartPreservesIncidentLock": True,
            "continuousOrderReconciliation": True,
            "incidentOrderAuditContinues": True,
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


def incident_order_audit_loop() -> None:
    """Keep known exchange statuses fresh without auto-clearing an incident."""
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        return
    client = BinancePredictionTradingClient(api_key, api_secret)
    wallet_address = ""
    last_status_key = ""
    try:
        while True:
            if not wallet_address:
                try:
                    wallets = client.wallets().get("wallets") or []
                    if len(wallets) == 1:
                        wallet_address = str(wallets[0].get("walletAddress") or "")
                except Exception as exc:
                    base.STATE.log(
                        f"INCIDENT_AUDIT_WALLET_RETRY {str(exc)[:300]}", "ERROR"
                    )
                if not wallet_address:
                    time.sleep(5.0)
                    continue

            snapshot = v4.SAFETY.snapshot()
            if not snapshot["locked"] or snapshot["lockKind"] != "INCIDENT":
                time.sleep(1.0)
                continue
            known_ids = {
                str(value)
                for value in (
                    snapshot.get("btcOrderId"),
                    snapshot.get("ethOrderId"),
                )
                if value
            }
            if not known_ids:
                time.sleep(1.0)
                continue
            try:
                orders = client.order_history(wallet_address, limit=100).get("orders") or []
                found = {
                    str(item.get("orderId") or ""): dict(item)
                    for item in orders
                    if str(item.get("orderId") or "") in known_ids
                }
            except Exception as exc:
                base.STATE.log(f"INCIDENT_ORDER_AUDIT_ERROR {str(exc)[:300]}", "ERROR")
                time.sleep(2.0)
                continue

            current = v4.SAFETY.snapshot()
            if (
                not current["locked"]
                or current["lockKind"] != "INCIDENT"
                or current.get("marketKey") != snapshot.get("marketKey")
            ):
                continue
            btc_id = str(snapshot.get("btcOrderId") or "")
            eth_id = str(snapshot.get("ethOrderId") or "")
            btc_order = found.get(btc_id) if btc_id else None
            eth_order = found.get(eth_id) if eth_id else None
            btc_status = (
                v4._status(btc_order)
                if btc_id
                else str(snapshot.get("btcStatus") or "NO_ORDER_ID")
            )
            eth_status = (
                v4._status(eth_order)
                if eth_id
                else str(snapshot.get("ethStatus") or "NO_ORDER_ID")
            )
            v4.SAFETY.update_tracking(
                btc_status=btc_status,
                eth_status=eth_status,
                status=str(snapshot.get("status") or "XPAIR_INCIDENT_LOCKED"),
                reason=str(snapshot.get("reason") or "manual reconciliation required"),
            )
            run_id = int(snapshot.get("runId") or 0)
            if run_id > 0:
                order_payload: dict[str, dict[str, Any]] = {}
                if btc_id:
                    order_payload[btc_id] = btc_order or {"status": "NOT_FOUND"}
                if eth_id:
                    order_payload[eth_id] = eth_order or {"status": "NOT_FOUND"}
                v4._merge_run_details(
                    run_id=run_id,
                    status=str(snapshot.get("status") or "XPAIR_INCIDENT_LOCKED"),
                    message=(
                        f"incident audit BTC={btc_status} ETH={eth_status}; "
                        "manual clear still required"
                    ),
                    orders=order_payload,
                )
            status_key = f"{snapshot.get('marketKey')}:{btc_status}:{eth_status}"
            if status_key != last_status_key:
                base.STATE.log(
                    f"INCIDENT_ORDER_AUDIT BTC={btc_status} ETH={eth_status}; "
                    "lock remains active",
                    "WARN",
                )
                last_status_key = status_key
            time.sleep(1.0)
    finally:
        client.close()


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
    threading.Thread(
        target=incident_order_audit_loop,
        name="xpair-incident-audit-v5",
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
