from __future__ import annotations

import csv
import hashlib
import hmac
import json
import math
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


BINANCE_API = "https://api.binance.com"
BINANCE_USDM_FUTURES_API = "https://fapi.binance.com"


class ApiError(RuntimeError):
    pass


class ApiHttpError(ApiError):
    """A deterministic HTTP response from Binance.

    The status code is retained so the live executor can distinguish a normal
    rejection (4xx) from a potentially ambiguous server-side result (5xx)
    without ever logging signed URLs or credentials.
    """

    def __init__(self, message: str, *, status_code: int, detail: str = "") -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.detail = detail


class ApiTransportError(ApiError):
    """The request did not produce a trustworthy HTTP response."""

    pass


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JsonClient:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode() if body is not None else None
        request_headers = {"Accept": "application/json", "User-Agent": "predict-testnet-research-bot/0.1"}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)
        req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        safe_url = f"{self.base_url}{path}"
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise ApiHttpError(
                f"HTTP {exc.code} from {safe_url}: {detail[:500]}",
                status_code=exc.code,
                detail=detail[:500],
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ApiTransportError(f"Request failed for {safe_url}: {exc}") from exc
        if isinstance(payload, dict) and payload.get("success") is False:
            raise ApiError(f"API rejected {safe_url}: {payload}")
        return payload

    def get(self, path: str, query: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, query=query)


class BinancePredictionClient(JsonClient):
    """Signed, read-only client for Binance Prediction Trading market data."""

    def __init__(self, api_key: str, api_secret: str) -> None:
        super().__init__(BINANCE_API)
        self.api_key = api_key
        self.api_secret = api_secret
        self._time_offset_ms: int | None = None
        self.last_rate_limits: dict[str, str] = {}
        self.retry_after_seconds: float | None = None

    def server_timestamp_ms(self) -> int:
        local_ms = int(time.time() * 1000)
        if self._time_offset_ms is None:
            server_ms = int(self.get("/api/v3/time")["serverTime"])
            self._time_offset_ms = server_ms - local_ms
        return int(time.time() * 1000) + self._time_offset_ms

    def sign_query(self, params: dict[str, Any]) -> str:
        # ``batch-redeem`` requires repeated tokenIds query fields.  ``doseq``
        # preserves the existing scalar encoding while signing lists as
        # tokenIds=a&tokenIds=b, exactly matching Binance's wire format.
        canonical = urllib.parse.urlencode(params, doseq=True)
        signature = hmac.new(
            self.api_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return f"{canonical}&signature={signature}"

    def signed_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        signed = dict(params or {})
        signed["recvWindow"] = 5000
        signed["timestamp"] = self.server_timestamp_ms()
        query = self.sign_query(signed)
        url = f"{self.base_url}{path}?{query}"
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "binance-prediction-paper-bot/0.2",
                "X-MBX-APIKEY": self.api_key,
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                self.retry_after_seconds = None
                self.last_rate_limits = {
                    key.lower(): value
                    for key, value in response.headers.items()
                    if key.lower().startswith(("x-mbx-used-weight", "x-sapi-used-ip-weight"))
                }
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            retry_after = exc.headers.get("Retry-After")
            try:
                self.retry_after_seconds = float(retry_after) if retry_after else None
            except ValueError:
                self.retry_after_seconds = None
            # Never include the signed URL, API key, or signature in errors.
            raise ApiHttpError(
                f"HTTP {exc.code} from {self.base_url}{path}: {detail[:500]}",
                status_code=exc.code,
                detail=detail[:500],
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ApiTransportError(
                f"Request failed for {self.base_url}{path}: {exc}"
            ) from exc

    def list_markets(self, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        return self.signed_get(
            "/sapi/v1/w3w/wallet/prediction/market/list",
            {"sortBy": "END_DATE", "orderBy": "ASC", "offset": offset, "limit": limit},
        )

    def market_detail(self, topic_id: int) -> dict[str, Any]:
        return self.signed_get(
            "/sapi/v1/w3w/wallet/prediction/market/detail", {"marketTopicId": topic_id}
        )

    def orderbook(self, market_id: int, token_id: str) -> dict[str, Any]:
        return self.signed_get(
            "/sapi/v1/w3w/wallet/prediction/order-book",
            {"vendor": "predict_fun", "marketId": market_id, "tokenId": token_id},
        )

    def find_market_summary(
        self,
        symbol: str,
        now: datetime | None = None,
        *,
        max_pages: int = 5,
    ) -> dict[str, Any] | None:
        """Return a usable list-market record without a detail round trip.

        Binance's list response already carries startPrice, the binary market,
        outcomes and token IDs.  The rollover hot path uses one page so it can
        discover a newly-created five-minute market before the +1s M7 cohort's
        execution grace expires.
        """
        now = now or utc_now()
        now_ms = int(now.timestamp() * 1000)
        candidates: list[dict[str, Any]] = []
        offset = 0
        page_count = 0
        while offset < 500 and page_count < max(1, int(max_pages)):
            response = self.list_markets(offset=offset)
            page_count += 1
            topics = response.get("marketTopics", [])
            for topic in topics:
                start_ms = int(topic.get("startDate") or 0)
                end_ms = int(topic.get("endDate") or 0)
                if (
                    topic.get("chartType") == "CRYPTO_UP_DOWN"
                    and topic.get("symbol") == symbol
                    and end_ms > now_ms
                    and 0 < end_ms - start_ms <= 301_000
                ):
                    candidates.append(topic)
            if candidates or not response.get("hasMore"):
                break
            offset += int(response.get("limit") or 100)
        if not candidates:
            return None
        active = [t for t in candidates if int(t["startDate"]) <= now_ms < int(t["endDate"])]
        summary = dict(
            min(active or candidates, key=lambda t: abs(int(t["startDate"]) - now_ms))
        )
        summary["_selectedMarket"] = select_binary_market(summary)
        return summary

    def find_market(self, symbol: str, now: datetime | None = None) -> dict[str, Any] | None:
        summary = self.find_market_summary(symbol, now, max_pages=5)
        if summary is None:
            return None
        detail = self.market_detail(int(summary["marketTopicId"]))
        detail["_selectedMarket"] = select_binary_market(detail)
        return detail


class BinancePredictionTradingClient(BinancePredictionClient):
    """Explicit live-trading client for Binance Prediction Trading.

    Keeping write methods off ``BinancePredictionClient`` makes it impossible
    for the collector and microstructure observer to place orders by accident.
    Only the separately armed live executor instantiates this subclass.
    """

    def signed_post(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        signed = dict(params or {})
        signed["recvWindow"] = 5000
        signed["timestamp"] = self.server_timestamp_ms()
        encoded = self.sign_query(signed)
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=encoded.encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "binance-prediction-live-m0w/0.1",
                "X-MBX-APIKEY": self.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                self.retry_after_seconds = None
                self.last_rate_limits = {
                    key.lower(): value
                    for key, value in response.headers.items()
                    if key.lower().startswith(
                        ("x-mbx-used-weight", "x-sapi-used-ip-weight")
                    )
                }
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            retry_after = exc.headers.get("Retry-After")
            try:
                self.retry_after_seconds = float(retry_after) if retry_after else None
            except ValueError:
                self.retry_after_seconds = None
            raise ApiHttpError(
                f"HTTP {exc.code} from {self.base_url}{path}: {detail[:500]}",
                status_code=exc.code,
                detail=detail[:500],
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            # For a placement request this is deliberately treated as
            # ambiguous; callers must reconcile instead of blindly retrying.
            raise ApiTransportError(
                f"Request failed for {self.base_url}{path}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise ApiTransportError(
                f"Unexpected response type from {self.base_url}{path}"
            )
        if payload.get("success") is False:
            raise ApiError(f"API rejected {self.base_url}{path}: {payload}")
        return payload

    def wallets(self) -> dict[str, Any]:
        return self.signed_get("/sapi/v1/w3w/wallet/prediction/wallet/list")

    def quota_status(self) -> dict[str, Any]:
        return self.signed_get(
            "/sapi/v1/w3w/wallet/prediction/quota/limit/status"
        )

    def payment_option_balances(self) -> dict[str, Any]:
        return self.signed_get(
            "/sapi/v1/w3w/wallet/prediction/balance/payment-options"
        )

    def portfolio(
        self, wallet_address: str, **filters: Any
    ) -> dict[str, Any]:
        return self.signed_get(
            "/sapi/v1/w3w/wallet/prediction/pnl/portfolio",
            {"walletAddress": wallet_address, **filters},
        )

    def positions(
        self,
        wallet_address: str,
        *,
        tab: str = "ONGOING",
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        return self.signed_get(
            "/sapi/v1/w3w/wallet/prediction/position/list",
            {
                "walletAddress": wallet_address,
                "tab": str(tab).upper(),
                "offset": max(0, int(offset)),
                "limit": max(1, min(100, int(limit))),
            },
        )

    def position_by_token(
        self, wallet_address: str, token_id: str
    ) -> dict[str, Any]:
        return self.signed_get(
            "/sapi/v1/w3w/wallet/prediction/position/token",
            {"walletAddress": wallet_address, "tokenId": str(token_id)},
        )

    def batch_redeem(
        self,
        *,
        wallet_address: str,
        wallet_id: str,
        token_ids: list[str],
        chain_id: str = "56",
    ) -> dict[str, Any]:
        normalized = [str(token_id) for token_id in token_ids if str(token_id)]
        if not normalized:
            raise ValueError("batch redeem requires at least one token ID")
        return self.signed_post(
            "/sapi/v1/w3w/wallet/prediction/batch-redeem",
            {
                "walletAddress": wallet_address,
                "walletId": wallet_id,
                "tokenIds": normalized,
                "chainId": str(chain_id or "56"),
            },
        )

    def redeem_status(
        self, wallet_address: str, tx_hash: str
    ) -> dict[str, Any]:
        return self.signed_get(
            "/sapi/v1/w3w/wallet/prediction/redeem/status",
            {"walletAddress": wallet_address, "txHash": str(tx_hash)},
        )

    def get_quote(
        self,
        *,
        wallet_address: str,
        token_id: str,
        amount_in_wei: str,
        price_limit: str | None,
        slippage_bps: int,
        fee_rate_bps: int,
        funding_source: str = "MPC",
        side: str = "BUY",
        order_type: str = "LIMIT",
    ) -> dict[str, Any]:
        normalized_side = str(side).upper()
        normalized_order_type = str(order_type).upper()
        if normalized_side not in {"BUY", "SELL"}:
            raise ValueError("prediction quote side must be BUY or SELL")
        if normalized_order_type not in {"LIMIT", "MARKET"}:
            raise ValueError("prediction quote order type must be LIMIT or MARKET")
        params: dict[str, Any] = {
            "walletAddress": wallet_address,
            "tokenId": token_id,
            "side": normalized_side,
            "amountIn": amount_in_wei,
            "orderType": normalized_order_type,
            "slippageBps": int(slippage_bps),
            "chainId": "56",
            "feeRateBps": int(fee_rate_bps),
            "fundingSource": funding_source,
        }
        if price_limit is not None:
            params["priceLimit"] = price_limit
        return self.signed_post(
            "/sapi/v1/w3w/wallet/prediction/trade/get-quote",
            params,
        )

    def place_limit_order(
        self,
        *,
        wallet_address: str,
        wallet_id: str,
        quote_id: str,
        price_limit: str,
        slippage_bps: int,
        account_type: str,
        funding_source: str = "MPC",
    ) -> dict[str, Any]:
        return self._place_order(
            wallet_address=wallet_address,
            wallet_id=wallet_id,
            quote_id=quote_id,
            order_type="LIMIT",
            price_limit=price_limit,
            slippage_bps=slippage_bps,
            account_type=account_type,
            funding_source=funding_source,
        )

    def place_market_order(
        self,
        *,
        wallet_address: str,
        wallet_id: str,
        quote_id: str,
        slippage_bps: int,
        account_type: str,
        funding_source: str = "MPC",
    ) -> dict[str, Any]:
        return self._place_order(
            wallet_address=wallet_address,
            wallet_id=wallet_id,
            quote_id=quote_id,
            order_type="MARKET",
            price_limit=None,
            slippage_bps=slippage_bps,
            account_type=account_type,
            funding_source=funding_source,
        )

    def _place_order(
        self,
        *,
        wallet_address: str,
        wallet_id: str,
        quote_id: str,
        order_type: str,
        price_limit: str | None,
        slippage_bps: int,
        account_type: str,
        funding_source: str,
    ) -> dict[str, Any]:
        normalized_order_type = str(order_type).upper()
        if normalized_order_type not in {"LIMIT", "MARKET"}:
            raise ValueError("prediction order type must be LIMIT or MARKET")
        if normalized_order_type == "LIMIT" and not price_limit:
            raise ValueError("prediction LIMIT order requires price_limit")
        params: dict[str, Any] = {
            "walletAddress": wallet_address,
            "walletId": wallet_id,
            "quoteId": quote_id,
            "timeInForce": "GTC" if normalized_order_type == "LIMIT" else "FOK",
            "accountType": account_type,
            "orderType": normalized_order_type,
            "slippageBps": int(slippage_bps),
            "fundingSource": funding_source,
        }
        if normalized_order_type == "LIMIT":
            params["priceLimit"] = str(price_limit)
        return self.signed_post(
            "/sapi/v1/w3w/wallet/prediction/trade/place-order-bundle",
            params,
        )

    def active_orders(
        self, wallet_address: str, *, market_id: int | None = None, limit: int = 100
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "walletAddress": wallet_address,
            "offset": 0,
            "limit": max(1, min(100, int(limit))),
        }
        if market_id is not None:
            params["marketId"] = int(market_id)
        return self.signed_get(
            "/sapi/v1/w3w/wallet/prediction/order/list", params
        )

    def order_history(
        self, wallet_address: str, *, limit: int = 100
    ) -> dict[str, Any]:
        return self.signed_get(
            "/sapi/v1/w3w/wallet/prediction/order/history",
            {
                "walletAddress": wallet_address,
                "offset": 0,
                "limit": max(1, min(100, int(limit))),
            },
        )


def select_binary_market(detail: dict[str, Any]) -> dict[str, Any]:
    markets = [m for m in detail.get("markets", []) if m.get("tradingStatus") == "OPEN"]
    if not markets:
        markets = detail.get("markets", [])
    if not markets:
        raise ApiError("Binance market detail has no binary market")
    market = markets[0]
    outcomes = market.get("outcomes", [])
    if len(outcomes) != 2:
        raise ApiError("Expected exactly two outcomes for a CRYPTO_UP_DOWN market")
    positive = next(
        (o for o in outcomes if str(o.get("name", "")).upper() in {"UP", "YES"}), outcomes[0]
    )
    negative = next((o for o in outcomes if o is not positive), outcomes[1])
    return {"market": market, "up": positive, "down": negative}


class BinanceClient(JsonClient):
    def __init__(self) -> None:
        super().__init__(BINANCE_API)

    def price(self, symbol: str) -> float:
        payload = self.get("/api/v3/ticker/price", {"symbol": symbol})
        return float(payload["price"])


class BinanceUsdmFuturesClient(JsonClient):
    """Public USD-M futures client used only for last-trade observation."""

    def __init__(self) -> None:
        super().__init__(BINANCE_USDM_FUTURES_API)

    def latest_agg_trade(self, symbol: str) -> dict[str, float | int]:
        payload = self.get("/fapi/v1/aggTrades", {"symbol": symbol, "limit": 1})
        if not isinstance(payload, list) or not payload:
            raise ApiError("Binance USD-M aggTrades returned no trades")
        trade = payload[-1]
        return {
            "price": float(trade["p"]),
            "timestamp_ms": int(trade["T"]),
            "aggregate_trade_id": int(trade["a"]),
        }


@dataclass(frozen=True)
class TopOfBook:
    up_ask: float | None
    up_bid: float | None
    down_ask: float | None
    down_bid: float | None
    up_ask_size: float | None = None
    up_bid_size: float | None = None
    down_ask_size: float | None = None
    down_bid_size: float | None = None
    book_skew_ms: float | None = None
    up_book_timestamp_ms: float | None = None
    down_book_timestamp_ms: float | None = None
    book_age_ms: float | None = None


def top_of_book(book: dict[str, Any], precision: int) -> TopOfBook:
    asks = book.get("asks") or []
    bids = book.get("bids") or []
    up_ask = float(asks[0][0]) if asks else None
    up_bid = float(bids[0][0]) if bids else None
    down_ask = round(1.0 - up_bid, precision) if up_bid is not None else None
    down_bid = round(1.0 - up_ask, precision) if up_ask is not None else None
    return TopOfBook(up_ask, up_bid, down_ask, down_bid)


def _best_level(levels: list[Any], *, ask: bool) -> tuple[float | None, float | None]:
    if not levels:
        return None, None
    selected = (min if ask else max)(
        levels, key=lambda level: float(level["price"] if isinstance(level, dict) else level[0])
    )
    price = float(selected["price"] if isinstance(selected, dict) else selected[0])
    raw_size = (
        selected.get("size", selected.get("quantity"))
        if isinstance(selected, dict)
        else (selected[1] if len(selected) > 1 else None)
    )
    return price, (float(raw_size) if raw_size is not None else None)


def binance_top_of_book(
    up_book: dict[str, Any],
    down_book: dict[str, Any],
    current_timestamp_ms: int | float | None = None,
) -> TopOfBook:
    up_ask, up_ask_size = _best_level(up_book.get("asks") or [], ask=True)
    up_bid, up_bid_size = _best_level(up_book.get("bids") or [], ask=False)
    down_ask, down_ask_size = _best_level(down_book.get("asks") or [], ask=True)
    down_bid, down_bid_size = _best_level(down_book.get("bids") or [], ask=False)
    try:
        up_timestamp = float(up_book.get("updateTimestampMs", up_book.get("timestamp")))
        down_timestamp = float(down_book.get("updateTimestampMs", down_book.get("timestamp")))
        book_skew_ms = abs(up_timestamp - down_timestamp)
        book_age_ms = (
            max(0.0, float(current_timestamp_ms) - min(up_timestamp, down_timestamp))
            if current_timestamp_ms is not None
            else None
        )
    except (KeyError, TypeError, ValueError):
        up_timestamp = down_timestamp = book_skew_ms = book_age_ms = None
    return TopOfBook(
        up_ask=up_ask, up_bid=up_bid, down_ask=down_ask, down_bid=down_bid,
        up_ask_size=up_ask_size, up_bid_size=up_bid_size,
        down_ask_size=down_ask_size, down_bid_size=down_bid_size,
        book_skew_ms=book_skew_ms,
        up_book_timestamp_ms=up_timestamp, down_book_timestamp_ms=down_timestamp,
        book_age_ms=book_age_ms,
    )


class ProbabilityModel:
    def __init__(self, max_samples: int = 120, fallback_sigma_per_sqrt_second: float = 0.00012) -> None:
        self.samples: deque[tuple[float, float]] = deque(maxlen=max_samples)
        self.fallback_sigma = fallback_sigma_per_sqrt_second

    def observe(self, timestamp: float, price: float) -> None:
        self.samples.append((timestamp, price))

    def sigma_per_sqrt_second(self) -> float:
        rates: list[float] = []
        for (t0, p0), (t1, p1) in zip(self.samples, list(self.samples)[1:]):
            dt = t1 - t0
            if dt > 0 and p0 > 0 and p1 > 0:
                rates.append(math.log(p1 / p0) / math.sqrt(dt))
        if len(rates) < 5:
            return self.fallback_sigma
        return max(statistics.pstdev(rates), 0.00002)

    def up_probability(self, current: float, start: float, seconds_left: float) -> float:
        if seconds_left <= 0:
            return 1.0 if current > start else (0.0 if current < start else 0.5)
        sigma = self.sigma_per_sqrt_second()
        z = math.log(current / start) / (sigma * math.sqrt(seconds_left))
        return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


@dataclass(frozen=True)
class Decision:
    action: str
    probability: float
    cost: float | None
    edge: float | None
    reason: str


def taker_fee(shares: float, price: float, fee_rate_bps: int | float) -> float:
    """Predict taker fee for a binary share at the executed price."""
    if shares <= 0 or not 0 <= price <= 1:
        return 0.0
    return shares * min(price, 1.0 - price) * float(fee_rate_bps) / 10_000


def effective_taker_cost(price: float, fee_rate_bps: int | float) -> float:
    """Cash cost per share, including the price-dependent Predict taker fee."""
    return price + taker_fee(1.0, price, fee_rate_bps)


def decide(q_up: float, book: TopOfBook, fee_rate_bps: int, min_edge: float) -> Decision:
    choices: list[tuple[str, float, float]] = []
    if book.up_ask is not None:
        choices.append(("PAPER_BUY_UP", q_up, effective_taker_cost(book.up_ask, fee_rate_bps)))
    if book.down_ask is not None:
        choices.append(("PAPER_BUY_DOWN", 1.0 - q_up, effective_taker_cost(book.down_ask, fee_rate_bps)))
    if not choices:
        return Decision("NO_TRADE", q_up, None, None, "NO_LIQUIDITY")
    action, probability, cost = max(choices, key=lambda item: item[1] - item[2])
    edge = probability - cost
    if edge < min_edge:
        return Decision("NO_TRADE", q_up, cost, edge, "EDGE_TOO_SMALL")
    return Decision(action, q_up, cost, edge, "PAPER_ONLY")


def enforce_one_trade(decision: Decision, market_id: int, traded_market_ids: set[int]) -> Decision:
    if not decision.action.startswith("PAPER_BUY"):
        return decision
    if market_id in traded_market_ids:
        return Decision("NO_TRADE", decision.probability, decision.cost, decision.edge, "ALREADY_TRADED")
    traded_market_ids.add(market_id)
    return decision


CSV_FIELDS = [
    "timestamp", "market_id", "title", "start_price", "spot_price", "seconds_left",
    "sigma", "q_up", "up_ask", "up_bid", "down_ask", "down_bid", "action",
    "effective_cost", "edge", "reason",
]


def append_csv(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field) for field in CSV_FIELDS})


def run_bot(
    symbol: str,
    duration: int,
    interval: float,
    min_edge: float,
    output: Path,
    api_key: str,
    api_secret: str,
) -> None:
    prediction = BinancePredictionClient(api_key, api_secret)
    spot = BinanceClient()
    model = ProbabilityModel()
    stop_at = time.monotonic() + duration
    current_market_id: int | None = None
    traded_market_ids: set[int] = set()
    market: dict[str, Any] | None = None
    while time.monotonic() < stop_at:
        now = utc_now()
        now_ms = int(now.timestamp() * 1000)
        if market is None or int(market.get("endDate") or 0) <= now_ms:
            market = prediction.find_market(symbol, now)
            if market is None:
                print(f"{now.isoformat()} WAITING no current/nearby {symbol} 5m market", flush=True)
                time.sleep(interval)
                continue
            selected = market["_selectedMarket"]
            new_market_id = int(selected["market"]["marketId"])
            if current_market_id != new_market_id:
                current_market_id = new_market_id
                print(
                    f"TOPIC {market['marketTopicId']} MARKET {current_market_id} | {market['title']}",
                    flush=True,
                )
        start_ms = int(market["startDate"])
        end_ms = int(market["endDate"])
        if now_ms < start_ms:
            print(f"{now.isoformat()} WAITING market starts in {(start_ms-now_ms)/1000:.1f}s", flush=True)
            time.sleep(interval)
            continue
        selected = market["_selectedMarket"]
        start_price = market.get("variantData", {}).get("startPrice")
        if not start_price:
            market = prediction.market_detail(int(market["marketTopicId"]))
            market["_selectedMarket"] = select_binary_market(market)
            selected = market["_selectedMarket"]
            start_price = market.get("variantData", {}).get("startPrice")
        if not start_price:
            print(f"{now.isoformat()} WAITING Binance Prediction startPrice", flush=True)
            time.sleep(interval)
            continue
        with ThreadPoolExecutor(max_workers=3) as pool:
            spot_future = pool.submit(spot.price, symbol)
            up_future = pool.submit(
                prediction.orderbook, current_market_id, str(selected["up"]["tokenId"])
            )
            down_future = pool.submit(
                prediction.orderbook, current_market_id, str(selected["down"]["tokenId"])
            )
            spot_price = spot_future.result()
            up_book = up_future.result()
            down_book = down_future.result()
        model.observe(time.time(), spot_price)
        seconds_left = max(0.0, (end_ms - now_ms) / 1000)
        q_up = model.up_probability(spot_price, float(start_price), seconds_left)
        book = binance_top_of_book(up_book, down_book)
        decision = decide(q_up, book, int(market.get("feeRateBps", 0)), min_edge)
        decision = enforce_one_trade(decision, current_market_id, traded_market_ids)
        row = {
            "timestamp": now.isoformat(), "market_id": current_market_id, "title": market["title"],
            "start_price": start_price, "spot_price": spot_price, "seconds_left": round(seconds_left, 3),
            "sigma": model.sigma_per_sqrt_second(), "q_up": q_up, "up_ask": book.up_ask,
            "up_bid": book.up_bid, "down_ask": book.down_ask, "down_bid": book.down_bid,
            "action": decision.action, "effective_cost": decision.cost, "edge": decision.edge,
            "reason": decision.reason,
        }
        append_csv(output, row)
        print(
            f"{now.isoformat()} id={current_market_id} left={seconds_left:6.1f}s "
            f"start={float(start_price):.3f} spot={spot_price:.3f} q_up={q_up:.3f} "
            f"ask={book.up_ask} action={decision.action} reason={decision.reason}",
            flush=True,
        )
        time.sleep(interval)
