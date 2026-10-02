from __future__ import annotations

import json
import threading
import time
from typing import Any

import httpx

from .core import (
    ApiError,
    ApiHttpError,
    ApiTransportError,
    BinancePredictionClient,
    BinancePredictionTradingClient,
)


TIME_SYNC_REFRESH_SECONDS = 30.0
TIME_SYNC_WALL_JUMP_MS = 250
SIGNED_TIMESTAMP_SAFETY_BIAS_MS = 100
TIMESTAMP_ERROR_CODE = -1021


def _ensure_time_sync_state(client: BinancePredictionClient) -> None:
    if not hasattr(client, "_time_sync_lock"):
        client._time_sync_lock = threading.RLock()  # type: ignore[attr-defined]
    if not hasattr(client, "_time_sync_last_mono"):
        client._time_sync_last_mono = None  # type: ignore[attr-defined]
    if not hasattr(client, "_time_sync_last_wall_ms"):
        client._time_sync_last_wall_ms = None  # type: ignore[attr-defined]
    if not hasattr(client, "_time_sync_last_server_ms"):
        client._time_sync_last_server_ms = None  # type: ignore[attr-defined]
    if not hasattr(client, "_time_sync_refreshes"):
        client._time_sync_refreshes = 0  # type: ignore[attr-defined]
    if not hasattr(client, "_time_sync_timestamp_error_recoveries"):
        client._time_sync_timestamp_error_recoveries = 0  # type: ignore[attr-defined]
    if not hasattr(client, "_time_sync_last_error_path"):
        client._time_sync_last_error_path = None  # type: ignore[attr-defined]
    if not hasattr(client, "_time_sync_last_error_at_ms"):
        client._time_sync_last_error_at_ms = None  # type: ignore[attr-defined]
    if not hasattr(client, "_time_sync_last_rtt_ms"):
        client._time_sync_last_rtt_ms = None  # type: ignore[attr-defined]


def _wall_clock_jump_detected(client: BinancePredictionClient, now_wall_ms: int, now_mono: float) -> bool:
    last_wall = getattr(client, "_time_sync_last_wall_ms", None)
    last_mono = getattr(client, "_time_sync_last_mono", None)
    if last_wall is None or last_mono is None:
        return False
    expected_wall = int(last_wall) + int(round((now_mono - float(last_mono)) * 1000.0))
    return abs(int(now_wall_ms) - expected_wall) > TIME_SYNC_WALL_JUMP_MS


def _sync_server_clock(client: BinancePredictionClient, *, force: bool = False) -> int:
    _ensure_time_sync_state(client)
    now_mono = time.monotonic()
    now_wall_ms = int(time.time() * 1000)
    last_mono = getattr(client, "_time_sync_last_mono", None)
    stale = (
        last_mono is None
        or now_mono - float(last_mono) >= TIME_SYNC_REFRESH_SECONDS
        or getattr(client, "_time_offset_ms", None) is None
    )
    jumped = _wall_clock_jump_detected(client, now_wall_ms, now_mono)
    if not force and not stale and not jumped:
        return int(getattr(client, "_time_offset_ms"))

    lock = getattr(client, "_time_sync_lock")
    with lock:
        # Re-check after waiting for another thread that may already have refreshed.
        now_mono = time.monotonic()
        now_wall_ms = int(time.time() * 1000)
        last_mono = getattr(client, "_time_sync_last_mono", None)
        stale = (
            last_mono is None
            or now_mono - float(last_mono) >= TIME_SYNC_REFRESH_SECONDS
            or getattr(client, "_time_offset_ms", None) is None
        )
        jumped = _wall_clock_jump_detected(client, now_wall_ms, now_mono)
        if not force and not stale and not jumped:
            return int(getattr(client, "_time_offset_ms"))

        before_ms = int(time.time() * 1000)
        before_mono = time.monotonic()
        payload = client.get("/api/v3/time")
        after_ms = int(time.time() * 1000)
        after_mono = time.monotonic()
        if not isinstance(payload, dict) or payload.get("serverTime") is None:
            raise ApiTransportError("Binance server time response unavailable")
        server_ms = int(payload["serverTime"])
        midpoint_ms = (before_ms + after_ms) // 2
        offset_ms = server_ms - midpoint_ms

        client._time_offset_ms = int(offset_ms)
        client._time_sync_last_mono = (before_mono + after_mono) / 2.0  # type: ignore[attr-defined]
        client._time_sync_last_wall_ms = midpoint_ms  # type: ignore[attr-defined]
        client._time_sync_last_server_ms = server_ms  # type: ignore[attr-defined]
        client._time_sync_last_rtt_ms = max(0.0, (after_mono - before_mono) * 1000.0)  # type: ignore[attr-defined]
        client._time_sync_refreshes = int(getattr(client, "_time_sync_refreshes", 0)) + 1  # type: ignore[attr-defined]
        return int(offset_ms)


def _server_timestamp_ms_hardened(client: BinancePredictionClient) -> int:
    offset = _sync_server_clock(client)
    return int(time.time() * 1000) + int(offset)


def _signed_timestamp_ms(client: BinancePredictionClient) -> int:
    # A small past bias protects against midpoint/network asymmetry.  Binance's
    # recvWindow tolerates this tiny age, while a timestamp > serverTime + 1000 ms
    # is rejected outright.
    return _server_timestamp_ms_hardened(client) - SIGNED_TIMESTAMP_SAFETY_BIAS_MS


def _is_timestamp_error_response(response: Any) -> bool:
    if int(getattr(response, "status_code", 0) or 0) < 400:
        return False
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        try:
            if int(payload.get("code")) == TIMESTAMP_ERROR_CODE:
                return True
        except (TypeError, ValueError):
            pass
    text = str(getattr(response, "text", "") or "")
    return '"code":-1021' in text.replace(" ", "") or "Timestamp for this request" in text


def _record_timestamp_error(client: BinancePredictionClient, path: str) -> None:
    _ensure_time_sync_state(client)
    client._time_sync_timestamp_error_recoveries = int(  # type: ignore[attr-defined]
        getattr(client, "_time_sync_timestamp_error_recoveries", 0)
    ) + 1
    client._time_sync_last_error_path = str(path)  # type: ignore[attr-defined]
    client._time_sync_last_error_at_ms = int(time.time() * 1000)  # type: ignore[attr-defined]
    # Invalidate first so a failed resync can never leave the known-bad offset
    # looking healthy to the next request.
    client._time_offset_ms = None
    client._time_sync_last_mono = None  # type: ignore[attr-defined]


def _resync_after_timestamp_error(client: BinancePredictionClient, path: str) -> None:
    _record_timestamp_error(client, path)
    _sync_server_clock(client, force=True)


def _signed_get_once(
    client: BinancePredictionClient,
    path: str,
    params: dict[str, Any] | None,
) -> Any:
    signed = dict(params or {})
    signed["recvWindow"] = 5000
    signed["timestamp"] = _signed_timestamp_ms(client)
    query = client.sign_query(signed)
    try:
        return client.http_client.get(
            f"{path}?{query}",
            headers={
                "User-Agent": "binance-prediction-paper-bot/0.2",
                "X-MBX-APIKEY": client.api_key,
            },
        )
    except httpx.RequestError as exc:
        raise ApiTransportError(
            f"Request failed for {client.base_url}{path}: {type(exc).__name__}"
        ) from exc


def _signed_get_hardened(
    client: BinancePredictionClient,
    path: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = _signed_get_once(client, path, params)
    client._capture_rate_limits(response)

    if _is_timestamp_error_response(response):
        # GET is read-only/idempotent.  Refresh time and retry exactly once.
        try:
            _resync_after_timestamp_error(client, path)
        except Exception:
            client._raise_http_error(response, path=path)
        response = _signed_get_once(client, path, params)
        client._capture_rate_limits(response)

    if response.status_code >= 400:
        client._raise_http_error(response, path=path)
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise ApiTransportError(
            f"Invalid JSON response from {client.base_url}{path}"
        ) from exc
    if not isinstance(payload, dict):
        raise ApiTransportError(
            f"Unexpected response type from {client.base_url}{path}"
        )
    return payload


def _signed_post_hardened(
    client: BinancePredictionTradingClient,
    path: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    signed = dict(params or {})
    signed["recvWindow"] = 5000
    signed["timestamp"] = _signed_timestamp_ms(client)
    encoded = client.sign_query(signed)
    try:
        response = client.http_client.post(
            path,
            content=encoded.encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "binance-prediction-live-m0w/0.1",
                "X-MBX-APIKEY": client.api_key,
            },
        )
    except httpx.RequestError as exc:
        # Preserve the existing at-most-once rule for writes.
        raise ApiTransportError(
            f"Request failed for {client.base_url}{path}: {type(exc).__name__}"
        ) from exc

    client._capture_rate_limits(response)
    if response.status_code >= 400:
        if _is_timestamp_error_response(response):
            # Binance explicitly rejected this write before normal processing.
            # Resync the clock for the next strategy cycle, but DO NOT resend this
            # POST inside the transport layer.
            try:
                _resync_after_timestamp_error(client, path)
            except Exception:
                pass
        client._raise_http_error(response, path=path)

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise ApiTransportError(
            f"Invalid JSON response from {client.base_url}{path}"
        ) from exc
    if not isinstance(payload, dict):
        raise ApiTransportError(
            f"Unexpected response type from {client.base_url}{path}"
        )
    if payload.get("success") is False:
        raise ApiError(f"API rejected {client.base_url}{path}: {payload}")
    return payload


def time_sync_snapshot(client: BinancePredictionClient | None) -> dict[str, Any]:
    if client is None:
        return {
            "available": False,
            "refreshSeconds": TIME_SYNC_REFRESH_SECONDS,
            "wallJumpThresholdMs": TIME_SYNC_WALL_JUMP_MS,
            "signedTimestampSafetyBiasMs": SIGNED_TIMESTAMP_SAFETY_BIAS_MS,
        }
    _ensure_time_sync_state(client)
    last_mono = getattr(client, "_time_sync_last_mono", None)
    age_ms = (
        max(0.0, (time.monotonic() - float(last_mono)) * 1000.0)
        if last_mono is not None
        else None
    )
    return {
        "available": True,
        "offsetMs": getattr(client, "_time_offset_ms", None),
        "lastSyncAgeMs": age_ms,
        "lastServerTimeMs": getattr(client, "_time_sync_last_server_ms", None),
        "lastRttMs": getattr(client, "_time_sync_last_rtt_ms", None),
        "refreshes": int(getattr(client, "_time_sync_refreshes", 0)),
        "timestampErrorRecoveries": int(
            getattr(client, "_time_sync_timestamp_error_recoveries", 0)
        ),
        "lastTimestampErrorPath": getattr(client, "_time_sync_last_error_path", None),
        "lastTimestampErrorAtMs": getattr(client, "_time_sync_last_error_at_ms", None),
        "refreshSeconds": TIME_SYNC_REFRESH_SECONDS,
        "wallJumpThresholdMs": TIME_SYNC_WALL_JUMP_MS,
        "signedTimestampSafetyBiasMs": SIGNED_TIMESTAMP_SAFETY_BIAS_MS,
        "signedGet1021AutoRetryOnce": True,
        "signedPost1021AutoRetry": False,
    }


# Apply process-wide to all existing/future instances.  Existing instances resolve
# methods on the class, so importing this module after construction is still safe.
BinancePredictionClient.server_timestamp_ms = _server_timestamp_ms_hardened
BinancePredictionClient.signed_get = _signed_get_hardened
BinancePredictionTradingClient.signed_post = _signed_post_hardened
