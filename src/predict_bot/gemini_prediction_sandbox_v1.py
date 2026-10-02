from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
import websocket

VERSION = "GEMINI_PREDICTION_SANDBOX_V1"
DEFAULT_REST_URL = "https://api.sandbox.gemini.com"
DEFAULT_WS_URL = "wss://ws.sandbox.gemini.com"
BTC5M_SERIES = "BTC05M"


def number(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def parse_iso_ms(value: Any) -> int | None:
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def stable_market_id(ticker: str) -> int:
    """Deterministic numeric ID for BTC05MYYMMDDHHMM tickers."""
    digits = "".join(re.findall(r"\d", str(ticker)))
    if len(digits) >= 10:
        return int(digits[-10:])
    # Deterministic non-random fallback that still fits SQLite INTEGER.
    return int.from_bytes(hashlib.sha256(str(ticker).encode("utf-8")).digest()[:7], "big")


def _host_is_sandbox(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "api.sandbox.gemini.com" or host == "ws.sandbox.gemini.com" or host.endswith(".sandbox.gemini.com")


def assert_sandbox_url(url: str) -> None:
    if not _host_is_sandbox(url):
        raise RuntimeError(f"Gemini V1 is sandbox-only; refusing non-sandbox URL: {url}")


def _contract_up(event: dict[str, Any]) -> dict[str, Any] | None:
    for item in event.get("contracts") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("ticker") or "").upper() == "UP":
            return item
        if str(item.get("label") or "").strip().upper() == "UP":
            return item
    contracts = [x for x in (event.get("contracts") or []) if isinstance(x, dict)]
    return contracts[0] if len(contracts) == 1 else None


def _event_window(event: dict[str, Any]) -> tuple[int | None, int | None]:
    return parse_iso_ms(event.get("startTime") or event.get("effectiveDate")), parse_iso_ms(event.get("expiryDate"))


def select_btc5m_event(
    events: list[dict[str, Any]],
    *,
    now_ms: int | None = None,
    allow_upcoming: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], str] | None:
    at = int(now_ms if now_ms is not None else time.time() * 1000)
    current: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    future: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if str(event.get("series") or "").upper() != BTC5M_SERIES:
            continue
        if str(event.get("status") or "active").lower() not in {"active", "open"}:
            continue
        contract = _contract_up(event)
        if contract is None or not contract.get("instrumentSymbol"):
            continue
        start_ms, end_ms = _event_window(event)
        if start_ms is None or end_ms is None or end_ms <= start_ms:
            continue
        if start_ms <= at < end_ms:
            current.append((start_ms, event, contract))
        elif at < start_ms:
            future.append((start_ms, event, contract))
    if current:
        _, event, contract = max(current, key=lambda row: row[0])
        return event, contract, "ACTIVE"
    if allow_upcoming and future:
        _, event, contract = min(future, key=lambda row: row[0])
        return event, contract, "UPCOMING"
    return None


def _strike_value(contract: dict[str, Any]) -> float | None:
    strike = contract.get("strike")
    if isinstance(strike, dict):
        return number(strike.get("value") or strike.get("price") or strike.get("strikePrice"))
    return number(strike)


def contract_rules(contract: dict[str, Any]) -> dict[str, float | str | None]:
    return {
        "instrumentSymbol": str(contract.get("instrumentSymbol") or ""),
        "quantityIncrement": number(contract.get("quantityIncrement")) or 1.0,
        "quantityMinimum": number(contract.get("quantityMinimum")) or 1.0,
        "priceIncrement": number(contract.get("priceIncrement")) or 0.01,
        "priceMinimum": number(contract.get("priceMinimum")) or 0.01,
        "strikePrice": _strike_value(contract),
    }


def _bid_ask(prices: dict[str, Any], outcome: str) -> tuple[float | None, float | None]:
    key = outcome.lower()
    buy = prices.get("buy") if isinstance(prices.get("buy"), dict) else {}
    sell = prices.get("sell") if isinstance(prices.get("sell"), dict) else {}
    # Gemini event payload semantics: BUY is what a buyer pays (ask), SELL is
    # what a seller receives (bid). For YES, bestBid/bestAsk are fallbacks.
    bid = number(sell.get(key))
    ask = number(buy.get(key))
    if outcome.upper() == "YES":
        bid = bid if bid is not None else number(prices.get("bestBid"))
        ask = ask if ask is not None else number(prices.get("bestAsk"))
    return bid, ask


def _mid(bid: float | None, ask: float | None) -> float | None:
    return (bid + ask) / 2.0 if bid is not None and ask is not None else None


def event_snapshot(
    event: dict[str, Any],
    contract: dict[str, Any],
    *,
    observed_at_ms: int | None = None,
    phase: str = "ACTIVE",
) -> dict[str, Any]:
    at_ms = int(observed_at_ms if observed_at_ms is not None else time.time() * 1000)
    start_ms, end_ms = _event_window(event)
    prices = contract.get("prices") if isinstance(contract.get("prices"), dict) else {}
    up_bid, up_ask = _bid_ask(prices, "YES")
    down_bid, down_ask = _bid_ask(prices, "NO")
    ticker = str(event.get("ticker") or contract.get("ticker") or "")
    rules = contract_rules(contract)
    return {
        "timestampNs": time.time_ns(),
        "sampledAtMs": at_ms,
        "marketId": stable_market_id(ticker),
        "bucketStartSec": (start_ms // 1000) if start_ms is not None else None,
        "windowStartMs": start_ms,
        "windowEndMs": end_ms,
        "secondsLeft": max(0.0, (end_ms - at_ms) / 1000.0) if end_ms is not None else None,
        "strikePrice": rules["strikePrice"],
        "predictUpBid": up_bid,
        "predictUpAsk": up_ask,
        "predictUpMid": _mid(up_bid, up_ask),
        "predictDownBid": down_bid,
        "predictDownAsk": down_ask,
        "predictDownMid": _mid(down_bid, down_ask),
        # REST events do not expose a matching exchange source timestamp.
        "predictSourceAgeMs": None,
        "predictReceiptAgeMs": 0.0,
        "venue": "GEMINI_PM_SANDBOX",
        "externalVenue": True,
        "geminiPhase": phase,
        "geminiEventId": event.get("id"),
        "geminiEventTicker": ticker,
        "geminiEventTitle": event.get("title"),
        "geminiSeries": event.get("series"),
        "geminiInstrumentSymbol": rules["instrumentSymbol"],
        "geminiContractStatus": contract.get("status"),
        "geminiMarketState": contract.get("marketState"),
        "geminiSource": contract.get("source"),
        "geminiLastTradePrice": number(prices.get("lastTradePrice")),
        "geminiQuantityIncrement": rules["quantityIncrement"],
        "geminiQuantityMinimum": rules["quantityMinimum"],
        "geminiPriceIncrement": rules["priceIncrement"],
        "geminiPriceMinimum": rules["priceMinimum"],
    }


def merge_public_baseline(gemini: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    """Preserve public BTC microstructure while replacing venue-specific PM state."""
    out = dict(baseline or {})
    baseline_market_id = out.get("marketId")
    for key, value in gemini.items():
        out[key] = value
    out["baselineMarketId"] = baseline_market_id
    out["baselineVenue"] = "PROJECT_PUBLIC_8783" if baseline else None

    # Never carry a Predict.fun strike geometry into Gemini.
    strike = number(gemini.get("strikePrice"))
    spot = number(out.get("spotPrice"))
    chain = number(out.get("chainlinkPrice"))
    if strike is not None and strike > 0:
        out["spotMinusStrikeBps"] = (spot / strike - 1.0) * 10_000 if spot and spot > 0 else None
        out["chainlinkMinusStrikeBps"] = (chain / strike - 1.0) * 10_000 if chain and chain > 0 else None
        out["geminiStrikeMappingStatus"] = "GEMINI_STRIKE_APPLIED"
    else:
        out["spotMinusStrikeBps"] = None
        out["chainlinkMinusStrikeBps"] = None
        out["geminiStrikeMappingStatus"] = "STRIKE_UNAVAILABLE_NO_CROSS_VENUE_REUSE"
    return out


@dataclass(frozen=True)
class PreparedOrder:
    request_id: str
    client_order_id: str
    symbol: str
    channel: str
    side: str
    outcome: str
    price: str
    quantity: str
    time_in_force: str
    message: dict[str, Any]


def _is_increment(value: float, increment: float, *, eps: float = 1e-8) -> bool:
    if increment <= 0:
        return False
    q = value / increment
    return abs(q - round(q)) <= eps


def prepare_order(
    contract: dict[str, Any],
    *,
    channel: str,
    side: str,
    price: float,
    quantity: float,
    client_order_id: str | None = None,
    request_id: str | None = None,
) -> PreparedOrder:
    rules = contract_rules(contract)
    symbol = str(rules["instrumentSymbol"] or "")
    if not symbol:
        raise ValueError("contract has no instrumentSymbol")
    channel_u = str(channel).upper()
    side_u = str(side).upper()
    if channel_u not in {"MAKER", "TAKER"}:
        raise ValueError("channel must be MAKER or TAKER")
    if side_u not in {"UP", "DOWN"}:
        raise ValueError("side must be UP or DOWN")
    px = float(price)
    qty = float(quantity)
    pmin = float(rules["priceMinimum"] or 0.01)
    pinc = float(rules["priceIncrement"] or 0.01)
    qmin = float(rules["quantityMinimum"] or 1.0)
    qinc = float(rules["quantityIncrement"] or 1.0)
    if not (pmin <= px < 1.0):
        raise ValueError(f"price {px} outside [{pmin}, 1)")
    if qty < qmin:
        raise ValueError(f"quantity {qty} below minimum {qmin}")
    if not _is_increment(px, pinc):
        raise ValueError(f"price {px} is not aligned to increment {pinc}")
    if not _is_increment(qty, qinc):
        raise ValueError(f"quantity {qty} is not aligned to increment {qinc}")

    now = int(time.time() * 1000)
    cid = client_order_id or f"BTC5MLAB-GEMINI-{now}"
    rid = request_id or f"order-{now}"
    tif = "MOC" if channel_u == "MAKER" else "IOC"
    outcome = "YES" if side_u == "UP" else "NO"
    msg = {
        "id": rid,
        "method": "order.place",
        "params": {
            "symbol": symbol,
            "side": "BUY",
            "type": "LIMIT",
            "timeInForce": tif,
            "price": f"{px:.8f}".rstrip("0").rstrip("."),
            "quantity": f"{qty:.8f}".rstrip("0").rstrip("."),
            "eventOutcome": outcome,
            "clientOrderId": cid,
        },
    }
    return PreparedOrder(
        request_id=rid,
        client_order_id=cid,
        symbol=symbol,
        channel=channel_u,
        side=side_u,
        outcome=outcome,
        price=msg["params"]["price"],
        quantity=msg["params"]["quantity"],
        time_in_force=tif,
        message=msg,
    )


class GeminiPredictionSandboxClient:
    def __init__(
        self,
        *,
        rest_url: str | None = None,
        ws_url: str | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.rest_url = (rest_url or os.environ.get("GEMINI_PM_SANDBOX_REST_URL") or DEFAULT_REST_URL).rstrip("/")
        self.ws_url = ws_url or os.environ.get("GEMINI_PM_SANDBOX_WS_URL") or DEFAULT_WS_URL
        assert_sandbox_url(self.rest_url)
        assert_sandbox_url(self.ws_url)
        self.timeout_seconds = max(0.5, float(timeout_seconds))
        self.http = httpx.Client(timeout=self.timeout_seconds, trust_env=False)
        self._events_cache: list[dict[str, Any]] = []
        self._events_cache_at_ms = 0
        self._last_rest_nonce = 0

    @property
    def order_enabled(self) -> bool:
        return str(os.environ.get("GEMINI_PM_SANDBOX_ORDER_ENABLED", "false")).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _windows_user_env(name: str) -> str | None:
        if os.name != "nt":
            return None
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                value, _ = winreg.QueryValueEx(key, name)
            return str(value) if value else None
        except Exception:
            return None

    def _credentials(self) -> tuple[str, str]:
        api_key = (
            os.environ.get("GEMINI_PM_SANDBOX_API_KEY")
            or os.environ.get("gemini_sandbox_APIKEY")
            or self._windows_user_env("gemini_sandbox_APIKEY")
        )
        api_secret = (
            os.environ.get("GEMINI_PM_SANDBOX_API_SECRET")
            or os.environ.get("gemini_sandbox_APISecret")
            or self._windows_user_env("gemini_sandbox_APISecret")
        )
        if not api_key or not api_secret:
            raise RuntimeError("missing Gemini Sandbox API credentials")
        return api_key, api_secret

    def credential_metadata(self) -> dict[str, Any]:
        try:
            api_key, api_secret = self._credentials()
        except Exception:
            return {
                "present": False,
                "accountScoped": False,
                "keyLength": 0,
                "secretLength": 0,
            }
        return {
            "present": True,
            "accountScoped": str(api_key).startswith("account-"),
            "keyLength": len(api_key),
            "secretLength": len(api_secret),
        }

    def _rest_auth_headers(self, request_path: str, extra: dict[str, Any] | None = None) -> dict[str, str]:
        api_key, api_secret = self._credentials()
        now_nonce = int(time.time())
        nonce = max(now_nonce, int(self._last_rest_nonce) + 1)
        self._last_rest_nonce = nonce
        payload_obj = {
            "request": str(request_path),
            "nonce": nonce,
        }
        if extra:
            payload_obj.update(extra)
        raw = json.dumps(payload_obj, separators=(",", ":")).encode("utf-8")
        payload = base64.b64encode(raw).decode("ascii")
        signature = hmac.new(
            api_secret.encode("utf-8"),
            payload.encode("ascii"),
            hashlib.sha384,
        ).hexdigest()
        return {
            "X-GEMINI-APIKEY": api_key,
            "X-GEMINI-PAYLOAD": payload,
            "X-GEMINI-SIGNATURE": signature,
            "Content-Type": "text/plain",
        }

    def _master_accounts(self) -> list[str]:
        path = "/v1/account/list"
        response = self.http.post(
            f"{self.rest_url}{path}",
            headers=self._rest_auth_headers(path),
            content=b"",
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload if isinstance(payload, list) else payload.get("accounts") if isinstance(payload, dict) else []
        out: list[str] = []
        for row in rows or []:
            if isinstance(row, dict) and row.get("account"):
                out.append(str(row["account"]))
        return out

    def account_scope_probe(self) -> dict[str, Any]:
        path = "/v1/account/list"
        response = self.http.post(
            f"{self.rest_url}{path}",
            headers=self._rest_auth_headers(path),
            content=b"",
        )
        result: dict[str, Any] = {
            "httpStatus": int(response.status_code),
            "masterListSucceeded": int(response.status_code) == 200,
        }
        try:
            payload = response.json()
        except Exception:
            payload = None
        if isinstance(payload, list):
            result["accountCount"] = len(payload)
            result["hasAccounts"] = bool(payload)
        elif isinstance(payload, dict):
            accounts = payload.get("accounts")
            if isinstance(accounts, list):
                result["accountCount"] = len(accounts)
                result["hasAccounts"] = bool(accounts)
            result["reason"] = payload.get("reason") or payload.get("error")
            result["message"] = payload.get("message")
        return result

    def terms_status(self) -> dict[str, Any]:
        path = "/v1/prediction-markets/terms/status"

        def attempt(extra: dict[str, Any] | None, mode: str) -> dict[str, Any]:
            response = self.http.get(
                f"{self.rest_url}{path}",
                headers=self._rest_auth_headers(path, extra),
            )
            result: dict[str, Any] = {
                "httpStatus": int(response.status_code),
                "authenticated": int(response.status_code) != 401,
                "accountTargetingMode": mode,
            }
            try:
                payload = response.json()
            except Exception:
                payload = None
            if isinstance(payload, dict):
                result["hasAcceptedLatest"] = payload.get("hasAcceptedLatest")
                result["acceptedVersion"] = payload.get("acceptedVersion")
                result["latestVersion"] = payload.get("latestVersion")
                result["error"] = payload.get("error") or payload.get("message")
            elif response.status_code >= 400:
                result["error"] = f"HTTP_{response.status_code}"
            return result

        direct = attempt(None, "NONE")
        if direct.get("httpStatus") == 200:
            return direct
        try:
            accounts = self._master_accounts()
        except Exception:
            return direct
        if not accounts:
            return direct
        single = attempt({"account": accounts[0]}, "ACCOUNT")
        if single.get("httpStatus") == 200:
            return single
        plural = attempt({"accounts": accounts}, "ACCOUNTS")
        return plural if plural.get("httpStatus") == 200 else single

    def close(self) -> None:
        self.http.close()

    def _get_with_retry(self, url: str, *, params: dict[str, Any] | None = None, attempts: int = 2) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(max(1, int(attempts))):
            try:
                response = self.http.get(url, params=params)
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt + 1 >= max(1, int(attempts)):
                    break
                time.sleep(0.15 * (attempt + 1))
        assert last_error is not None
        raise last_error

    def list_events(self, *, cache_seconds: float = 30.0) -> list[dict[str, Any]]:
        at_ms = int(time.time() * 1000)
        if self._events_cache and at_ms - self._events_cache_at_ms < max(0.0, cache_seconds) * 1000:
            return [dict(row) for row in self._events_cache]
        response = self._get_with_retry(
            f"{self.rest_url}/v1/prediction-markets/events",
            params={"status": "active", "limit": 500},
            attempts=2,
        )
        payload = response.json()
        rows = payload.get("data") if isinstance(payload, dict) else None
        self._events_cache = [dict(row) for row in (rows or []) if isinstance(row, dict)]
        self._events_cache_at_ms = at_ms
        return [dict(row) for row in self._events_cache]

    def get_event(self, event_ticker: str) -> dict[str, Any]:
        ticker = str(event_ticker or "").strip()
        if not ticker:
            raise ValueError("event ticker is required")
        response = self._get_with_retry(
            f"{self.rest_url}/v1/prediction-markets/events/{ticker}",
            attempts=2,
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Gemini Get Event returned non-object")
        return dict(payload)

    def recently_settled_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        response = self.http.get(
            f"{self.rest_url}/v1/prediction-markets/events/recently-settled",
            params={"category": "Crypto", "limit": max(1, min(500, int(limit)))},
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") if isinstance(payload, dict) else None
        return [dict(row) for row in (rows or []) if isinstance(row, dict)]

    def current_btc5m(
        self,
        *,
        now_ms: int | None = None,
        allow_upcoming: bool = True,
        hydrate_detail: bool = True,
    ):
        selected = select_btc5m_event(self.list_events(), now_ms=now_ms, allow_upcoming=allow_upcoming)
        if selected is None or not hydrate_detail:
            return selected
        event, contract, phase = selected
        ticker = str(event.get("ticker") or "")
        if not ticker:
            return selected
        try:
            detail = self.get_event(ticker)
        except httpx.HTTPStatusError as exc:
            # Gemini Sandbox can advertise the next BTC05M event a few seconds
            # before its Get Event detail becomes available. Treat that 404 as
            # a normal rollover warm-up, not a venue outage.
            if exc.response.status_code == 404:
                return selected
            raise
        detail_contract = _contract_up(detail)
        if detail_contract is None:
            return selected
        return detail, detail_contract, phase

    @staticmethod
    def auth_headers(api_key: str, api_secret: str, *, nonce: str | None = None) -> dict[str, str]:
        nonce_value = nonce or str(int(time.time()))
        payload = base64.b64encode(nonce_value.encode("utf-8")).decode("ascii")
        signature = hmac.new(api_secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha384).hexdigest()
        return {
            "X-GEMINI-APIKEY": api_key,
            "X-GEMINI-NONCE": nonce_value,
            "X-GEMINI-PAYLOAD": payload,
            "X-GEMINI-SIGNATURE": signature,
        }

    def submit_order_rest(self, prepared: PreparedOrder) -> dict[str, Any]:
        if not self.order_enabled:
            raise PermissionError("Gemini sandbox order submission is disabled; set GEMINI_PM_SANDBOX_ORDER_ENABLED=true locally")
        assert_sandbox_url(self.rest_url)
        terms = self.terms_status()
        if terms.get("httpStatus") != 200 or terms.get("hasAcceptedLatest") is not True:
            raise RuntimeError(f"prediction market terms are not accepted: {terms}")

        extra: dict[str, Any] = {
            "symbol": prepared.symbol,
            "orderType": "limit",
            "side": "buy",
            "quantity": prepared.quantity,
            "price": prepared.price,
            "outcome": prepared.outcome.lower(),
            "timeInForce": "immediate-or-cancel" if prepared.channel == "TAKER" else "good-til-cancel",
            "clientOrderId": prepared.client_order_id,
        }
        if prepared.channel == "MAKER":
            extra["makerOrCancel"] = True

        meta = self.credential_metadata()
        account_targeted = False
        if not meta.get("accountScoped"):
            accounts = self._master_accounts()
            if not accounts:
                raise RuntimeError("Master key has no target account")
            extra["account"] = accounts[0]
            account_targeted = True

        path = "/v1/prediction-markets/order"
        response = self.http.post(
            f"{self.rest_url}{path}",
            headers=self._rest_auth_headers(path, extra),
            content=b"",
        )
        try:
            payload = response.json()
        except Exception:
            payload = {"raw": response.text[:1000]}
        return {
            "ok": int(response.status_code) in {200, 201},
            "transport": "REST",
            "sandboxOnly": True,
            "httpStatus": int(response.status_code),
            "accountTargeted": account_targeted,
            "response": payload,
            "prepared": {
                "symbol": prepared.symbol,
                "channel": prepared.channel,
                "side": prepared.side,
                "outcome": prepared.outcome,
                "price": prepared.price,
                "quantity": prepared.quantity,
                "timeInForce": extra["timeInForce"],
                "makerOrCancel": bool(extra.get("makerOrCancel", False)),
                "clientOrderId": prepared.client_order_id,
            },
        }

    def submit_order(self, prepared: PreparedOrder, *, timeout_seconds: float = 6.0) -> dict[str, Any]:
        if not self.order_enabled:
            raise PermissionError("Gemini sandbox order submission is disabled; set GEMINI_PM_SANDBOX_ORDER_ENABLED=true locally")
        assert_sandbox_url(self.ws_url)
        api_key = os.environ.get("GEMINI_PM_SANDBOX_API_KEY") or os.environ.get("gemini_sandbox_APIKEY")
        api_secret = os.environ.get("GEMINI_PM_SANDBOX_API_SECRET") or os.environ.get("gemini_sandbox_APISecret")
        if not api_key or not api_secret:
            raise RuntimeError("missing Gemini Sandbox API credentials")
        headers = self.auth_headers(api_key, api_secret)
        header_lines = [f"{key}: {value}" for key, value in headers.items()]
        ws = websocket.create_connection(self.ws_url, header=header_lines, timeout=max(1.0, timeout_seconds))
        messages: list[dict[str, Any]] = []
        try:
            ws.send(json.dumps({"id": "subscribe-orders", "method": "subscribe", "params": ["orders@account"]}))
            ws.send(json.dumps(prepared.message))
            deadline = time.monotonic() + max(1.0, timeout_seconds)
            ack: dict[str, Any] | None = None
            update: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                try:
                    raw = ws.recv()
                except Exception:
                    break
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                messages.append(msg)
                if str(msg.get("id") or "") == prepared.request_id:
                    ack = msg
                if str(msg.get("c") or "") == prepared.client_order_id:
                    update = msg
                    if str(msg.get("X") or "").upper() in {
                        "OPEN", "FILLED", "PARTIALLY_FILLED", "CANCELED", "REJECTED"
                    }:
                        break
            return {
                "ok": bool(ack is not None or update is not None),
                "sandboxOnly": True,
                "prepared": prepared.message,
                "ack": ack,
                "orderUpdate": update,
                "messages": messages[-20:],
            }
        finally:
            try:
                ws.close()
            except Exception:
                pass
