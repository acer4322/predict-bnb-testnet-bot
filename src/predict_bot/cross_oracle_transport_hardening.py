from __future__ import annotations

import json
import ssl
import threading
import urllib.parse
from typing import Any

from . import cross_oracle_tls_fallback as tls


resilient = tls.resilient
cross = resilient.cross_oracle_module

_WS_ALLOWED_HOSTS = {
    "ws-live-data.polymarket.com",
    "ws-subscriptions-clob.polymarket.com",
}


def _gamma_list_lookup_404_safe(
    client: Any,
    *,
    slug: str,
) -> dict[str, Any] | None:
    """Treat Gamma list-query 404 as "not published", never as a TLS error.

    The exact-slug endpoint can return 404 during a rollover. The fallback query
    endpoint can also return 404 for the same not-yet-published slug. Both are
    application-level discovery states and must converge to WAITING_GAMMA.
    """

    response = client.get(
        tls._GAMMA_LIST_URL,
        params={"slug": slug, "limit": 5},
    )
    if int(response.status_code) == 404:
        return None
    response.raise_for_status()
    payload = response.json()
    rows = payload if isinstance(payload, list) else []
    for row in rows:
        if isinstance(row, dict) and str(row.get("slug") or "") == slug:
            return row
    return None


# _read_public_json resolves this helper dynamically from its module globals.
# Replacing it here fixes both strict-TLS and restricted-fallback discovery paths.
tls._gamma_list_lookup = _gamma_list_lookup_404_safe


def _self_signed_tls_error(error: Any) -> bool:
    text = str(error).lower()
    return (
        "certificate_verify_failed" in text
        and ("self-signed certificate" in text or "self signed certificate" in text)
    )


def _ws_host_allowed(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    return (
        parsed.scheme == "wss"
        and (parsed.hostname or "").lower() in _WS_ALLOWED_HOSTS
    )


def _ws_sslopt(fallback: bool, url: str) -> dict[str, Any] | None:
    if not fallback:
        return None
    if not _ws_host_allowed(url):
        raise RuntimeError(
            "restricted websocket TLS fallback refused non-public Polymarket host"
        )
    return {
        "cert_reqs": ssl.CERT_NONE,
        "check_hostname": False,
    }


def _ensure_ws_tls_state(collector: Any) -> None:
    if not hasattr(collector, "_chainlink_ws_tls_fallback"):
        collector._chainlink_ws_tls_fallback = False
    if not hasattr(collector, "_polymarket_ws_tls_fallback"):
        collector._polymarket_ws_tls_fallback = False
    if not hasattr(collector, "_ws_tls_fallback_count"):
        collector._ws_tls_fallback_count = 0
    if not hasattr(collector, "_last_ws_tls_fallback_stream"):
        collector._last_ws_tls_fallback_stream = None


_original_chainlink_error = resilient.ResilientCrossOracleCollector._chainlink_error
_original_polymarket_error = resilient.ResilientCrossOracleCollector._polymarket_error
_original_snapshot = resilient.ResilientCrossOracleCollector.snapshot


def _chainlink_error_hardened(self: Any, ws: Any, error: Any) -> None:
    _ensure_ws_tls_state(self)
    if _self_signed_tls_error(error):
        with self.lock:
            self._chainlink_ws_tls_fallback = True
            self._ws_tls_fallback_count += 1
            self._last_ws_tls_fallback_stream = "chainlink"
            self.chainlink["status"] = "RECONNECTING_TLS_FALLBACK"
            self.chainlink["error"] = None
        try:
            ws.close()
        except Exception:
            pass
        return
    _original_chainlink_error(self, ws, error)


def _polymarket_error_hardened(
    self: Any,
    ws: Any,
    error: Any,
    generation: int,
) -> None:
    _ensure_ws_tls_state(self)
    if generation == self.polymarket_generation and _self_signed_tls_error(error):
        with self.lock:
            self._polymarket_ws_tls_fallback = True
            self._ws_tls_fallback_count += 1
            self._last_ws_tls_fallback_stream = "polymarket"
            self.polymarket["status"] = "RECONNECTING_TLS_FALLBACK"
            self.polymarket["error"] = None
            self.reconnect_count += 1
        target_slug, _ = cross.current_btc_5m_slug()
        self._open_gap(
            "POLYMARKET_WS_TLS_FALLBACK",
            "strict WSS verification saw a self-signed certificate; retrying public read-only Polymarket WSS with restricted fallback",
            target_slug,
        )
        try:
            ws.close()
        except Exception:
            pass
        return
    _original_polymarket_error(self, ws, error, generation)


def _chainlink_supervisor_hardened(self: Any) -> None:
    _ensure_ws_tls_state(self)
    websocket_module = cross.websocket
    if websocket_module is None:
        return
    while not self.stop_event.is_set():
        with self.lock:
            self.chainlink["status"] = "CONNECTING"
            self.chainlink["error"] = None
        ws = websocket_module.WebSocketApp(
            cross.CHAINLINK_WS_URL,
            on_open=self._chainlink_open,
            on_message=self._chainlink_message,
            on_error=self._chainlink_error,
            on_close=self._chainlink_close,
        )
        self.chainlink_ws = ws
        try:
            sslopt = _ws_sslopt(
                bool(self._chainlink_ws_tls_fallback),
                cross.CHAINLINK_WS_URL,
            )
            if sslopt is None:
                ws.run_forever()
            else:
                ws.run_forever(sslopt=sslopt)
        except Exception as exc:  # pragma: no cover - network dependent.
            self._chainlink_error(ws, exc)
        if self.stop_event.wait(0.5):
            return


def _polymarket_stream_loop_hardened(self: Any, generation: int) -> None:
    _ensure_ws_tls_state(self)
    websocket_module = cross.websocket
    if websocket_module is None:
        return
    while not self.stop_event.is_set() and generation == self.polymarket_generation:
        ws = websocket_module.WebSocketApp(
            cross.POLYMARKET_WS_URL,
            on_open=lambda app: self._polymarket_open(app, generation),
            on_message=lambda app, raw: self._polymarket_message(app, raw, generation),
            on_error=lambda app, error: self._polymarket_error(app, error, generation),
            on_close=lambda app, code, message: self._polymarket_close(
                app, code, message, generation
            ),
        )
        self.polymarket_ws = ws
        try:
            sslopt = _ws_sslopt(
                bool(self._polymarket_ws_tls_fallback),
                cross.POLYMARKET_WS_URL,
            )
            if sslopt is None:
                ws.run_forever()
            else:
                ws.run_forever(sslopt=sslopt)
        except Exception as exc:  # pragma: no cover - network dependent.
            self._polymarket_error(ws, exc, generation)
        if self.stop_event.wait(0.5):
            return


def _snapshot_hardened(self: Any) -> dict[str, Any]:
    _ensure_ws_tls_state(self)
    payload = _original_snapshot(self)
    transport = {
        "strictFirst": True,
        "fallbackPublicReadOnlyOnly": True,
        "allowedHosts": sorted(_WS_ALLOWED_HOSTS),
        "chainlinkFallbackActive": bool(self._chainlink_ws_tls_fallback),
        "polymarketFallbackActive": bool(self._polymarket_ws_tls_fallback),
        "fallbackCount": int(self._ws_tls_fallback_count),
        "lastFallbackStream": self._last_ws_tls_fallback_stream,
    }
    continuity = payload.get("continuity")
    if isinstance(continuity, dict):
        continuity["websocketTls"] = transport
    polymarket = payload.get("polymarket")
    if isinstance(polymarket, dict):
        poly_continuity = polymarket.get("continuity")
        if isinstance(poly_continuity, dict):
            poly_continuity["websocketTls"] = transport
    payload["websocketTls"] = transport
    return payload


resilient.ResilientCrossOracleCollector._chainlink_error = _chainlink_error_hardened
resilient.ResilientCrossOracleCollector._polymarket_error = _polymarket_error_hardened
resilient.ResilientCrossOracleCollector._chainlink_supervisor = _chainlink_supervisor_hardened
resilient.ResilientCrossOracleCollector._polymarket_stream_loop = _polymarket_stream_loop_hardened
resilient.ResilientCrossOracleCollector.snapshot = _snapshot_hardened


def main() -> int:
    return tls.main()


if __name__ == "__main__":
    raise SystemExit(main())
