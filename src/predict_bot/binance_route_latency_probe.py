from __future__ import annotations

import argparse
import json
import math
import os
import platform
import socket
import ssl
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

# Reuse the production Prediction read-only client, but never call any trade or quote endpoint.
from . import binance_time_sync_hardening as _time_sync_hardening  # noqa: F401
from .core import BinancePredictionClient


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE_URL = os.environ.get("PREDICT_BINANCE_ROUTE_BASE_URL", "https://api.binance.com")
DEFAULT_MARKET_REFERENCE_URL = os.environ.get(
    "PREDICT_BINANCE_ROUTE_MARKET_REFERENCE_URL",
    "http://127.0.0.1:8766/api/binance-market-reference",
)
DEFAULT_INTERVAL_MS = max(250, int(os.environ.get("PREDICT_BINANCE_ROUTE_INTERVAL_MS", "1000")))
DEFAULT_HANDSHAKE_EVERY = max(1, int(os.environ.get("PREDICT_BINANCE_ROUTE_HANDSHAKE_EVERY", "5")))
DEFAULT_TIMEOUT_SECONDS = max(1.0, float(os.environ.get("PREDICT_BINANCE_ROUTE_TIMEOUT_SECONDS", "5")))
USER_AGENT = "BTC-5M-Lab-Binance-Route-Latency-Probe/1.0"


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lo = int(math.floor(position))
    hi = int(math.ceil(position))
    if lo == hi:
        return ordered[lo]
    weight = position - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def _stats(values: list[float]) -> dict[str, float | int | None]:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    return {
        "count": len(clean),
        "mean": statistics.fmean(clean) if clean else None,
        "median": statistics.median(clean) if clean else None,
        "p90": _percentile(clean, 0.90),
        "p95": _percentile(clean, 0.95),
        "p99": _percentile(clean, 0.99),
        "min": min(clean) if clean else None,
        "max": max(clean) if clean else None,
    }


def _credentials() -> tuple[str | None, str | None, str]:
    live_key = os.environ.get("BINANCE_LIVE_API_KEY")
    live_secret = os.environ.get("BINANCE_LIVE_API_SECRET")
    if live_key and live_secret:
        return live_key, live_secret, "BINANCE_LIVE_*"
    key = os.environ.get("BINANCE_API_KEY")
    secret = os.environ.get("BINANCE_API_SECRET")
    if key and secret:
        return key, secret, "BINANCE_API_*"
    return None, None, "UNAVAILABLE"


def _safe_label(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value.strip())
    return cleaned[:80] or "route"


def _market_reference(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    row = payload.get("marketReference")
    if not isinstance(row, dict):
        return None
    try:
        market_id = int(row.get("market_id") or row.get("marketId") or 0)
    except (TypeError, ValueError):
        return None
    up = str(row.get("up_token_id") or row.get("upTokenId") or "")
    down = str(row.get("down_token_id") or row.get("downTokenId") or "")
    if market_id <= 0 or not up or not down or up == down:
        return None
    return {"marketId": market_id, "upTokenId": up, "downTokenId": down}


def _resolve_once(host: str, port: int) -> tuple[list[tuple[int, str]], float]:
    started = time.perf_counter()
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    seen: set[tuple[int, str]] = set()
    rows: list[tuple[int, str]] = []
    # Prefer IPv4 for deterministic VPS/VPN comparisons, but retain IPv6 if it is all we have.
    for family, _socktype, _proto, _canonname, sockaddr in infos:
        ip = str(sockaddr[0])
        key = (family, ip)
        if key not in seen:
            seen.add(key)
            rows.append(key)
    rows.sort(key=lambda item: 0 if item[0] == socket.AF_INET else 1)
    return rows, elapsed_ms


def _cold_tls_sample(host: str, port: int, timeout: float) -> dict[str, Any]:
    addresses, dns_ms = _resolve_once(host, port)
    if not addresses:
        raise RuntimeError(f"DNS returned no address for {host}")
    family, ip = addresses[0]
    sockaddr: tuple[Any, ...] = (ip, port) if family == socket.AF_INET else (ip, port, 0, 0)
    raw = socket.socket(family, socket.SOCK_STREAM)
    raw.settimeout(timeout)
    connect_started = time.perf_counter()
    try:
        raw.connect(sockaddr)
        tcp_ms = (time.perf_counter() - connect_started) * 1000.0
        context = ssl.create_default_context()
        tls_started = time.perf_counter()
        with context.wrap_socket(raw, server_hostname=host) as wrapped:
            tls_ms = (time.perf_counter() - tls_started) * 1000.0
            cipher = wrapped.cipher()
            return {
                "dnsMs": dns_ms,
                "tcpConnectMs": tcp_ms,
                "tlsHandshakeMs": tls_ms,
                "tcpTlsMs": tcp_ms + tls_ms,
                "peerIp": ip,
                "tlsVersion": wrapped.version(),
                "cipher": cipher[0] if cipher else None,
            }
    except Exception:
        raw.close()
        raise


def _trace_route(host: str) -> dict[str, Any]:
    is_windows = platform.system().lower().startswith("win")
    command = ["tracert", "-d", "-h", "20", "-w", "700", host] if is_windows else [
        "traceroute", "-n", "-m", "20", "-w", "1", host
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
            check=False,
        )
        return {
            "command": command,
            "returnCode": completed.returncode,
            "stdout": completed.stdout[-12000:],
            "stderr": completed.stderr[-3000:],
        }
    except Exception as exc:
        return {"command": command, "error": f"{type(exc).__name__}: {exc}"}


def _metric_delta(current: dict[str, Any], baseline: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    def pick(payload: dict[str, Any]) -> float | None:
        value: Any = payload
        for key in path:
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        return _finite(value)

    now = pick(current)
    before = pick(baseline)
    delta = now - before if now is not None and before is not None else None
    pct = (delta / before * 100.0) if delta is not None and before not in (None, 0.0) else None
    return {"baselineMs": before, "currentMs": now, "deltaMs": delta, "deltaPct": pct}


def compare_summaries(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    return {
        "baselineLabel": baseline.get("label"),
        "currentLabel": current.get("label"),
        "lowerIsBetter": True,
        "median": {
            "coldTcpTls": _metric_delta(current, baseline, ("network", "coldTcpTlsMs", "median")),
            "publicServerTimeHttp": _metric_delta(current, baseline, ("http", "publicServerTimeRttMs", "median")),
            "predictionOrderbook": _metric_delta(current, baseline, ("http", "predictionOrderbookRttMs", "median")),
        },
        "p95": {
            "coldTcpTls": _metric_delta(current, baseline, ("network", "coldTcpTlsMs", "p95")),
            "publicServerTimeHttp": _metric_delta(current, baseline, ("http", "publicServerTimeRttMs", "p95")),
            "predictionOrderbook": _metric_delta(current, baseline, ("http", "predictionOrderbookRttMs", "p95")),
        },
    }


class BinanceRouteLatencyProbe:
    """Passive route benchmark for Binance and Binance Prediction REST.

    No quote, order, cancel, or account-changing endpoint is called. The signed
    Prediction request is only the current market order-book GET. This makes it
    suitable for comparing direct ISP, WARP, VPN, WireGuard, or VPS routes.
    """

    def __init__(
        self,
        *,
        label: str,
        base_url: str,
        market_reference_url: str,
        interval_ms: int,
        handshake_every: int,
        timeout_seconds: float,
        output_path: Path,
        fixed_market_id: int | None = None,
        fixed_token_id: str | None = None,
        trace: bool = False,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("base URL must be an https:// URL")
        self.label = label
        self.base_url = base_url.rstrip("/")
        self.host = parsed.hostname
        self.port = int(parsed.port or 443)
        self.market_reference_url = market_reference_url
        self.interval_seconds = max(0.25, interval_ms / 1000.0)
        self.handshake_every = max(1, int(handshake_every))
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.output_path = output_path
        self.fixed_market_id = int(fixed_market_id) if fixed_market_id else None
        self.fixed_token_id = str(fixed_token_id) if fixed_token_id else None
        self.trace_enabled = trace
        self.started_at_ms = _now_ms()

        self.http = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(
                connect=self.timeout_seconds,
                read=self.timeout_seconds,
                write=self.timeout_seconds,
                pool=self.timeout_seconds,
            ),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4, keepalive_expiry=30.0),
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            transport=httpx.HTTPTransport(retries=0),
        )
        self.local_http = httpx.Client(timeout=2.0, transport=httpx.HTTPTransport(retries=0))

        key, secret, credential_source = _credentials()
        self.credential_source = credential_source
        self.prediction_client: BinancePredictionClient | None = None
        if key and secret:
            self.prediction_client = BinancePredictionClient(key, secret, http_client=self.http)
            # Keep error messages and the one-time timestamp sync on the same tested host.
            self.prediction_client.base_url = self.base_url

        self.dns_ms: list[float] = []
        self.tcp_ms: list[float] = []
        self.tls_ms: list[float] = []
        self.tcp_tls_ms: list[float] = []
        self.public_http_ms: list[float] = []
        self.prediction_ms: list[float] = []
        self.peer_ips: set[str] = set()
        self.resolved_ips: set[str] = set()
        self.network_errors = 0
        self.public_http_errors = 0
        self.prediction_errors = 0
        self.samples = 0
        self.market_switches = 0
        self.current_market_id: int | None = self.fixed_market_id
        self.current_tokens: list[str] = [self.fixed_token_id] if self.fixed_token_id else []
        self.last_error: str | None = None
        self.last_rate_limits: dict[str, str] = {}
        self.trace_result: dict[str, Any] | None = None

    def close(self) -> None:
        if self.prediction_client is not None:
            self.prediction_client.close()
        self.local_http.close()
        self.http.close()

    def _refresh_market(self) -> tuple[int, list[str]] | None:
        if self.fixed_market_id and self.fixed_token_id:
            return self.fixed_market_id, [self.fixed_token_id]
        try:
            response = self.local_http.get(self.market_reference_url)
            response.raise_for_status()
            reference = _market_reference(response.json())
        except Exception as exc:
            self.last_error = f"market-reference: {type(exc).__name__}: {exc}"
            return None
        if reference is None:
            self.last_error = "market-reference: current Prediction market unavailable"
            return None
        market_id = int(reference["marketId"])
        tokens = [str(reference["upTokenId"]), str(reference["downTokenId"])]
        if market_id != self.current_market_id:
            if self.current_market_id is not None:
                self.market_switches += 1
            self.current_market_id = market_id
        self.current_tokens = tokens
        return market_id, tokens

    def _warm_prediction_time_sync(self) -> None:
        if self.prediction_client is None:
            return
        try:
            self.prediction_client.server_timestamp_ms()
        except Exception as exc:
            self.last_error = f"prediction-time-sync: {type(exc).__name__}: {exc}"

    def _network_sample(self) -> None:
        try:
            sample = _cold_tls_sample(self.host, self.port, self.timeout_seconds)
            self.dns_ms.append(float(sample["dnsMs"]))
            self.tcp_ms.append(float(sample["tcpConnectMs"]))
            self.tls_ms.append(float(sample["tlsHandshakeMs"]))
            self.tcp_tls_ms.append(float(sample["tcpTlsMs"]))
            self.peer_ips.add(str(sample["peerIp"]))
            addresses, _ = _resolve_once(self.host, self.port)
            self.resolved_ips.update(ip for _family, ip in addresses)
        except Exception as exc:
            self.network_errors += 1
            self.last_error = f"cold-tls: {type(exc).__name__}: {exc}"

    def _public_http_sample(self) -> None:
        started = time.perf_counter()
        try:
            response = self.http.get("/api/v3/time")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("serverTime") is None:
                raise RuntimeError("unexpected /api/v3/time response")
            self.public_http_ms.append((time.perf_counter() - started) * 1000.0)
        except Exception as exc:
            self.public_http_errors += 1
            self.last_error = f"public-http: {type(exc).__name__}: {exc}"

    def _prediction_sample(self, token_index: int) -> None:
        if self.prediction_client is None:
            return
        reference = self._refresh_market()
        if reference is None:
            self.prediction_errors += 1
            return
        market_id, tokens = reference
        token_id = tokens[token_index % len(tokens)]
        started = time.perf_counter()
        try:
            payload = self.prediction_client.orderbook(market_id, token_id)
            if not isinstance(payload, dict):
                raise RuntimeError("unexpected Prediction order-book response")
            self.prediction_ms.append((time.perf_counter() - started) * 1000.0)
            self.last_rate_limits = dict(self.prediction_client.last_rate_limits)
        except Exception as exc:
            self.prediction_errors += 1
            self.last_error = f"prediction-orderbook: {type(exc).__name__}: {exc}"

    def summary(self) -> dict[str, Any]:
        public = _stats(self.public_http_ms)
        prediction = _stats(self.prediction_ms)
        public_median = _finite(public.get("median"))
        prediction_median = _finite(prediction.get("median"))
        return {
            "version": "binance_route_latency_probe_v1",
            "label": self.label,
            "safety": {
                "passiveOnly": True,
                "ordersPossible": False,
                "quoteEndpointsCalled": False,
                "tradeEndpointsCalled": False,
                "predictionEndpoint": "signed read-only order-book GET only",
            },
            "baseUrl": self.base_url,
            "host": self.host,
            "startedAtMs": self.started_at_ms,
            "generatedAtMs": _now_ms(),
            "runtime": {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "label": self.label,
            },
            "parameters": {
                "intervalMs": int(self.interval_seconds * 1000),
                "coldHandshakeEverySamples": self.handshake_every,
                "timeoutSeconds": self.timeout_seconds,
                "marketReferenceUrl": self.market_reference_url,
            },
            "network": {
                "dnsLookupMs": _stats(self.dns_ms),
                "tcpConnectMs": _stats(self.tcp_ms),
                "tlsHandshakeMs": _stats(self.tls_ms),
                "coldTcpTlsMs": _stats(self.tcp_tls_ms),
                "resolvedIps": sorted(self.resolved_ips),
                "connectedPeerIps": sorted(self.peer_ips),
                "errors": self.network_errors,
            },
            "http": {
                "publicServerTimeRttMs": public,
                "predictionOrderbookRttMs": prediction,
                "predictionMinusPublicMedianMs": (
                    prediction_median - public_median
                    if prediction_median is not None and public_median is not None
                    else None
                ),
                "interpretation": (
                    "predictionMinusPublicMedianMs is only a route/backend comparison hint; it is not "
                    "a pure measurement of Binance server processing time"
                ),
                "publicErrors": self.public_http_errors,
                "predictionErrors": self.prediction_errors,
                "credentialSource": self.credential_source,
                "lastRateLimits": self.last_rate_limits,
            },
            "prediction": {
                "marketId": self.current_market_id,
                "marketSwitches": self.market_switches,
                "tokenCount": len(self.current_tokens),
            },
            "samples": self.samples,
            "lastError": self.last_error,
            "traceRoute": self.trace_result,
        }

    def run(self, seconds: float, report_seconds: float) -> dict[str, Any]:
        self._warm_prediction_time_sync()
        if self.trace_enabled:
            self.trace_result = _trace_route(self.host)
        deadline = time.monotonic() + max(1.0, seconds)
        next_report = time.monotonic() + max(2.0, report_seconds)
        token_index = 0
        while time.monotonic() < deadline:
            iteration_started = time.monotonic()
            if self.samples % self.handshake_every == 0:
                self._network_sample()
            self._public_http_sample()
            self._prediction_sample(token_index)
            token_index += 1
            self.samples += 1
            if time.monotonic() >= next_report:
                print(json.dumps(self.summary(), ensure_ascii=False, separators=(",", ":")), flush=True)
                next_report = time.monotonic() + max(2.0, report_seconds)
            remaining = self.interval_seconds - (time.monotonic() - iteration_started)
            if remaining > 0:
                time.sleep(remaining)
        payload = self.summary()
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Passive Direct/WARP/VPN/VPS route latency benchmark for Binance Prediction"
    )
    parser.add_argument("--label", default="DIRECT", help="route label, e.g. DIRECT, WARP, TOKYO_VPS")
    parser.add_argument("--minutes", type=float, default=10.0)
    parser.add_argument("--interval-ms", type=int, default=DEFAULT_INTERVAL_MS)
    parser.add_argument("--handshake-every", type=int, default=DEFAULT_HANDSHAKE_EVERY)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--report-seconds", type=float, default=10.0)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--market-reference-url", default=DEFAULT_MARKET_REFERENCE_URL)
    parser.add_argument("--market-id", type=int)
    parser.add_argument("--token-id")
    parser.add_argument("--trace", action="store_true", help="capture one tracert/traceroute in the JSON")
    parser.add_argument("--compare-to", help="previous probe JSON to compare against")
    parser.add_argument("--output")
    args = parser.parse_args()

    if bool(args.market_id) != bool(args.token_id):
        parser.error("--market-id and --token-id must be supplied together")

    label = _safe_label(args.label)
    output = Path(args.output) if args.output else ROOT / "data" / f"binance_route_latency_{label}.json"
    probe = BinanceRouteLatencyProbe(
        label=label,
        base_url=args.base_url,
        market_reference_url=args.market_reference_url,
        interval_ms=args.interval_ms,
        handshake_every=args.handshake_every,
        timeout_seconds=args.timeout,
        output_path=output,
        fixed_market_id=args.market_id,
        fixed_token_id=args.token_id,
        trace=args.trace,
    )
    try:
        payload = probe.run(max(1.0, args.minutes * 60.0), args.report_seconds)
    finally:
        probe.close()

    if args.compare_to:
        baseline_path = Path(args.compare_to)
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        payload["comparison"] = compare_summaries(payload, baseline)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    print(f"saved: {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
