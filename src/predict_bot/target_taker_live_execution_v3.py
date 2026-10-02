from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any, Callable

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct
from predict_sdk import (
    ApprovalScope,
    BuildOrderInput,
    ChainId,
    LimitHelperInput,
    OrderBuilder,
    OrderBuilderOptions,
    Side,
)

from .binance_exact_market import find_exact_market_summary, live_cache_from_summary
from .core import (
    ApiHttpError,
    ApiTransportError,
    BinancePredictionTradingClient,
    binance_top_of_book,
)
from .predict_wallet_target_taker_public_side_strategy_v1 import (
    MAX_ASK,
    VERSION as STRATEGY_VERSION,
)


MODE_ENV = "PREDICT_TARGET_TAKER_LIVE_MODE"
VENUE_ENV = "PREDICT_TARGET_TAKER_LIVE_VENUE"
NOTIONAL_ENV = "PREDICT_TARGET_TAKER_LIVE_NOTIONAL_USDT"
COHORT_ENV = "PREDICT_TARGET_TAKER_LIVE_COHORT"
MAX_PRICE_DRIFT_ENV = "PREDICT_TARGET_TAKER_LIVE_MAX_PRICE_DRIFT"
BINANCE_SYMBOL_ENV = "PREDICT_TARGET_TAKER_BINANCE_SYMBOL"
BINANCE_WALLET_ADDRESS_ENV = "PREDICT_TARGET_TAKER_BINANCE_WALLET_ADDRESS"
BINANCE_WALLET_ID_ENV = "PREDICT_TARGET_TAKER_BINANCE_WALLET_ID"
BINANCE_ACCOUNT_TYPE_ENV = "PREDICT_TARGET_TAKER_BINANCE_ACCOUNT_TYPE"

DEFAULT_MODE = "paper"
DEFAULT_VENUE = "predictfun"
DEFAULT_NOTIONAL_USDT = 1.0
DEFAULT_MAX_PRICE_DRIFT = 0.02
DEFAULT_COHORT = "TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY"
SUPPORTED_VENUES = {"predictfun", "binance"}
SUPPORTED_MODES = {"paper", "live", "off"}
WEI = 10**18
PREDICT_API_BASE = "https://api.predict.fun"


class TargetTakerLiveError(RuntimeError):
    pass


class TargetTakerLiveAmbiguousError(TargetTakerLiveError):
    """The venue may have accepted the write; never retry automatically."""


class PredictDirectTransportError(TargetTakerLiveError):
    pass


class PredictDirectApiError(TargetTakerLiveError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = int(status_code)


@dataclass(frozen=True)
class TargetTakerLiveConfig:
    mode: str = DEFAULT_MODE
    venue: str = DEFAULT_VENUE
    notional_usdt: float = DEFAULT_NOTIONAL_USDT
    cohort: str = DEFAULT_COHORT
    max_price_drift: float = DEFAULT_MAX_PRICE_DRIFT

    @classmethod
    def from_env(cls) -> "TargetTakerLiveConfig":
        mode = str(os.environ.get(MODE_ENV, DEFAULT_MODE)).strip().lower()
        venue = str(os.environ.get(VENUE_ENV, DEFAULT_VENUE)).strip().lower()
        cohort = str(os.environ.get(COHORT_ENV, DEFAULT_COHORT)).strip()
        try:
            notional = float(os.environ.get(NOTIONAL_ENV, str(DEFAULT_NOTIONAL_USDT)))
            drift = float(os.environ.get(MAX_PRICE_DRIFT_ENV, str(DEFAULT_MAX_PRICE_DRIFT)))
        except (TypeError, ValueError) as exc:
            raise TargetTakerLiveError("Target Taker live numeric configuration is invalid") from exc
        if mode not in SUPPORTED_MODES:
            raise TargetTakerLiveError(f"unsupported Target Taker mode: {mode}")
        if venue not in SUPPORTED_VENUES:
            raise TargetTakerLiveError(f"unsupported Target Taker venue: {venue}")
        if not math.isfinite(notional) or not 0 < notional <= 100:
            raise TargetTakerLiveError("Target Taker notional must be within (0, 100] USDT")
        if not math.isfinite(drift) or not 0 <= drift <= 0.10:
            raise TargetTakerLiveError("Target Taker max price drift must be within [0, 0.10]")
        if not cohort:
            raise TargetTakerLiveError("Target Taker live cohort may not be empty")
        return cls(
            mode=mode,
            venue=venue,
            notional_usdt=notional,
            cohort=cohort,
            max_price_drift=drift,
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "venue": self.venue,
            "notionalUsdt": self.notional_usdt,
            "cohort": self.cohort,
            "maxPriceDrift": self.max_price_drift,
            "strategy": STRATEGY_VERSION,
            "executorVersion": "V3_DECOUPLED",
        }


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("data")
    return value if isinstance(value, dict) else payload


def _first_text(payload: dict[str, Any], *keys: str) -> str | None:
    data = _data(payload)
    for key in keys:
        value = data.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _first_number(payload: dict[str, Any], *keys: str) -> float | None:
    data = _data(payload)
    for key in keys:
        value = _finite(data.get(key))
        if value is not None:
            return value
    return None


def _market_id(market: dict[str, Any]) -> int:
    for key in ("id", "marketId", "market_id"):
        try:
            value = int(market.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def _token_id(outcome: dict[str, Any]) -> str | None:
    for key in ("tokenId", "onChainId", "token_id", "id"):
        value = outcome.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _outcome_map(market: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], str] | None:
    rows = market.get("outcomes") if isinstance(market.get("outcomes"), list) else []
    mapped: dict[str, dict[str, Any]] = {}
    yes_side: str | None = None
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip().upper()
        if name not in {"UP", "DOWN"} or not _token_id(raw):
            continue
        mapped[name] = raw
        try:
            if int(raw.get("indexSet") or 0) == 1:
                yes_side = name
        except (TypeError, ValueError):
            pass
    if set(mapped) != {"UP", "DOWN"} or yes_side not in {"UP", "DOWN"}:
        return None
    return mapped, str(yes_side)


def _best_level(data: dict[str, Any], side: str) -> tuple[float | None, float | None]:
    rows = data.get("bids" if side == "bid" else "asks")
    if not isinstance(rows, list) or not rows:
        return None, None
    row = rows[0]
    if not isinstance(row, (list, tuple)) or not row:
        return None, None
    price = _finite(row[0])
    size = _finite(row[1]) if len(row) > 1 else None
    return price, size


def derive_outcome_books(
    orderbook: dict[str, Any], *, yes_outcome: str, precision: int
) -> dict[str, dict[str, Any]]:
    data = _data(orderbook)
    yes_bid, yes_bid_size = _best_level(data, "bid")
    yes_ask, yes_ask_size = _best_level(data, "ask")
    if yes_bid is None or yes_ask is None:
        return {"UP": {}, "DOWN": {}}
    decimals = max(1, int(precision))
    no_bid = round(1.0 - yes_ask, decimals)
    no_ask = round(1.0 - yes_bid, decimals)
    yes_book = {
        "bestBid": yes_bid,
        "bestBidSize": yes_bid_size,
        "bestAsk": yes_ask,
        "bestAskSize": yes_ask_size,
    }
    no_book = {
        "bestBid": no_bid,
        "bestBidSize": yes_ask_size,
        "bestAsk": no_ask,
        "bestAskSize": yes_bid_size,
    }
    return {"UP": yes_book, "DOWN": no_book} if yes_outcome.upper() == "UP" else {"UP": no_book, "DOWN": yes_book}


def _signed_order_payload(signed: Any, order_hash: str) -> dict[str, Any]:
    side_value = signed.side.value if hasattr(signed.side, "value") else int(signed.side)
    signature_type = (
        signed.signature_type.value
        if hasattr(signed.signature_type, "value")
        else int(signed.signature_type)
    )
    return {
        "hash": str(order_hash),
        "salt": str(signed.salt),
        "maker": str(signed.maker),
        "signer": str(signed.signer),
        "taker": str(signed.taker),
        "tokenId": str(signed.token_id),
        "makerAmount": str(signed.maker_amount),
        "takerAmount": str(signed.taker_amount),
        "expiration": int(signed.expiration),
        "nonce": str(signed.nonce),
        "feeRateBps": str(signed.fee_rate_bps),
        "side": int(side_value),
        "signatureType": int(signature_type),
        "signature": str(signed.signature),
    }


def _safe_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:500]}"


class PredictDirectRestClient:
    """Small, asset-neutral Predict.fun transport used only by Target Taker."""

    def __init__(
        self,
        api_key: str,
        *,
        jwt: str | None,
        auth_signer: str,
        sign_auth_message: Callable[[str], str],
    ) -> None:
        self.api_key = str(api_key)
        self.jwt = str(jwt or "").strip() or None
        self.auth_signer = str(auth_signer)
        self.sign_auth_message = sign_auth_message
        self.auth_lock = threading.RLock()
        self.http = httpx.Client(
            base_url=PREDICT_API_BASE,
            timeout=httpx.Timeout(5.0, connect=2.0),
            verify=True,
            trust_env=False,
            follow_redirects=True,
            headers={"Accept": "application/json", "User-Agent": "BTC-5M-Lab-Target-Taker-Live-V3/1.0"},
        )

    def close(self) -> None:
        self.http.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        auth: bool = False,
        retry_auth: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["x-api-key"] = self.api_key
        if auth:
            if not self.jwt:
                self.authenticate()
            assert self.jwt is not None
            headers["Authorization"] = f"Bearer {self.jwt}"
        try:
            response = self.http.request(method, path, headers=headers, **kwargs)
        except httpx.RequestError as exc:
            raise PredictDirectTransportError(
                f"{method} {path}: {type(exc).__name__}: {exc}"
            ) from exc
        if response.status_code == 401 and auth and retry_auth:
            self.authenticate(force=True)
            return self._request(method, path, auth=True, retry_auth=False, **kwargs)
        try:
            payload = response.json()
        except Exception:
            payload = {"raw": response.text[:500]}
        if response.status_code >= 400:
            raise PredictDirectApiError(
                response.status_code,
                f"Predict {method} {path} HTTP {response.status_code}: {str(payload)[:500]}",
            )
        if not isinstance(payload, dict):
            raise PredictDirectTransportError(f"Predict {method} {path} returned non-object JSON")
        if payload.get("success") is False:
            raise PredictDirectApiError(
                response.status_code,
                f"Predict rejected {method} {path}: {str(payload)[:500]}",
            )
        return payload

    def authenticate(self, *, force: bool = False) -> str:
        with self.auth_lock:
            if self.jwt and not force:
                return self.jwt
            message_payload = self._request("GET", "/v1/auth/message")
            message = str(_data(message_payload).get("message") or "")
            if not message:
                raise PredictDirectTransportError("Predict auth message response contained no message")
            token_payload = self._request(
                "POST",
                "/v1/auth",
                json={
                    "signer": self.auth_signer,
                    "signature": self.sign_auth_message(message),
                    "message": message,
                },
            )
            token = str(_data(token_payload).get("token") or "").strip()
            if not token:
                raise PredictDirectTransportError("Predict auth response contained no JWT token")
            self.jwt = token
            return token

    def list_markets(self, *, first: int = 100, max_pages: int = 5) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        after: str | None = None
        seen: set[str] = set()
        for _ in range(max(1, int(max_pages))):
            params: dict[str, Any] = {
                "first": max(1, min(100, int(first))),
                "marketVariant": "CRYPTO_UP_DOWN",
            }
            if after:
                params["after"] = after
            payload = self._request("GET", "/v1/markets", params=params)
            rows = payload.get("data") if isinstance(payload.get("data"), list) else []
            output.extend(row for row in rows if isinstance(row, dict))
            cursor = str(payload.get("cursor") or "").strip()
            if not cursor or cursor in seen or not rows:
                break
            seen.add(cursor)
            after = cursor
        return output

    def orderbook(self, market_id: int) -> dict[str, Any]:
        return self._request("GET", f"/v1/markets/{int(market_id)}/orderbook")

    def create_order(self, data: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/orders", auth=True, json={"data": data})


class TargetTakerLiveExecutor:
    """Fail-closed one-shot executor for TARGET_TAKER_PUBLIC_SIDE_V1_EBM_FORWARD."""

    def __init__(self, config: TargetTakerLiveConfig | None = None) -> None:
        self.config = config or TargetTakerLiveConfig.from_env()
        self._predict_client: PredictDirectRestClient | None = None
        self._predict_builder: OrderBuilder | None = None
        self._predict_approvals_market_id: int | None = None
        self._predict_approvals_ok: bool | None = None
        self._binance_client: BinancePredictionTradingClient | None = None
        self.last_result: dict[str, Any] | None = None

    def close(self) -> None:
        for client in (self._predict_client, self._binance_client):
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def execute(
        self,
        *,
        cohort: str,
        market_id: int,
        decision: dict[str, Any],
        snapshot: dict[str, Any],
        signal_id: str,
    ) -> dict[str, Any]:
        if self.config.mode != "live":
            return self._remember({"status": "NOT_ARMED", "venue": self.config.venue})
        if cohort != self.config.cohort:
            return self._remember({"status": "COHORT_NOT_ARMED", "venue": self.config.venue})
        if str(decision.get("decision") or "") != "TRADE":
            return self._remember({"status": "DECISION_NOT_TRADE", "venue": self.config.venue})
        side = str(decision.get("side") or "").upper()
        signal_ask = _finite(decision.get("ask"))
        if side not in {"UP", "DOWN"} or signal_ask is None or not 0 < signal_ask <= MAX_ASK:
            return self._remember({"status": "INVALID_SIGNAL", "venue": self.config.venue})
        snapshot_market_id = int(_finite(snapshot.get("market_id") or snapshot.get("marketId")) or 0)
        if snapshot_market_id != int(market_id):
            return self._remember({"status": "MARKET_MISMATCH", "venue": self.config.venue})
        try:
            if self.config.venue == "predictfun":
                result = self._execute_predictfun(
                    market_id=int(market_id),
                    side=side,
                    signal_ask=signal_ask,
                    snapshot=snapshot,
                    signal_id=signal_id,
                )
            else:
                result = self._execute_binance(
                    source_market_id=int(market_id),
                    side=side,
                    signal_ask=signal_ask,
                    snapshot=snapshot,
                    signal_id=signal_id,
                )
        except TargetTakerLiveAmbiguousError as exc:
            result = {"status": "AMBIGUOUS", "venue": self.config.venue, "error": _safe_error(exc)}
        except Exception as exc:
            result = {"status": "REJECTED", "venue": self.config.venue, "error": _safe_error(exc)}
        return self._remember(result)

    def _remember(self, result: dict[str, Any]) -> dict[str, Any]:
        safe = {
            **result,
            "strategy": STRATEGY_VERSION,
            "notionalUsdt": self.config.notional_usdt,
            "completedAtMs": int(time.time() * 1000),
        }
        self.last_result = safe
        return safe

    def _ensure_predict(self) -> tuple[PredictDirectRestClient, OrderBuilder]:
        if self._predict_client is not None and self._predict_builder is not None:
            return self._predict_client, self._predict_builder
        api_key = str(os.environ.get("PREDICT_FUN_API_KEY") or "").strip()
        private_key = str(
            os.environ.get("PREDICT_FUN_PRIVATE_KEY")
            or os.environ.get("PREDICT_FUN_PRIVY_PRIVATE_KEY")
            or ""
        ).strip()
        predict_account = str(os.environ.get("PREDICT_FUN_ACCOUNT_ADDRESS") or "").strip()
        static_jwt = str(os.environ.get("PREDICT_FUN_JWT") or "").strip() or None
        if not api_key or not private_key:
            raise TargetTakerLiveError(
                "Predict.fun live execution requires PREDICT_FUN_API_KEY and PREDICT_FUN_PRIVATE_KEY "
                "(or PREDICT_FUN_PRIVY_PRIVATE_KEY)"
            )
        account = Account.from_key(private_key)
        builder = OrderBuilder.make(
            ChainId.BNB_MAINNET,
            private_key,
            OrderBuilderOptions(predict_account=predict_account or None, log_level="WARN"),
        )
        auth_signer = predict_account or account.address

        def sign_message(message: str) -> str:
            if predict_account:
                return builder.sign_predict_account_message(message)
            return account.sign_message(encode_defunct(text=message)).signature.hex()

        client = PredictDirectRestClient(
            api_key,
            jwt=static_jwt,
            auth_signer=auth_signer,
            sign_auth_message=sign_message,
        )
        client.authenticate(force=False)
        self._predict_client = client
        self._predict_builder = builder
        return client, builder

    @staticmethod
    def _predict_market(client: PredictDirectRestClient, market_id: int) -> dict[str, Any]:
        # Predict's documented list API is the canonical metadata source. Search
        # a bounded set and require exact ID equality; a miss blocks the order.
        for row in client.list_markets(first=100, max_pages=5):
            if _market_id(row) == int(market_id):
                return row
        raise TargetTakerLiveError(
            f"Predict.fun exact market {int(market_id)} was not found in the bounded CRYPTO_UP_DOWN listing"
        )

    def _check_predict_approvals(self, builder: OrderBuilder, market: dict[str, Any]) -> None:
        market_id = _market_id(market)
        if market_id == self._predict_approvals_market_id and self._predict_approvals_ok is True:
            return
        steps = builder.get_approval_steps(
            ApprovalScope(
                operation="TRADE",
                is_neg_risk=bool(market.get("isNegRisk")),
                is_yield_bearing=bool(market.get("isYieldBearing")),
                side=Side.BUY,
            )
        )
        checks = builder.check_approvals(steps)
        self._predict_approvals_market_id = market_id
        self._predict_approvals_ok = all(bool(check.satisfied) for check in checks)
        if self._predict_approvals_ok is not True:
            raise TargetTakerLiveError("Predict.fun BUY approvals are not confirmed")

    def _execute_predictfun(
        self,
        *,
        market_id: int,
        side: str,
        signal_ask: float,
        snapshot: dict[str, Any],
        signal_id: str,
    ) -> dict[str, Any]:
        client, builder = self._ensure_predict()
        market = self._predict_market(client, market_id)
        if str(market.get("tradingStatus") or "").upper() not in {"OPEN", "TRADING"}:
            raise TargetTakerLiveError("Predict.fun market is not open")
        mapped = _outcome_map(market)
        if mapped is None:
            raise TargetTakerLiveError("Predict.fun UP/DOWN outcome mapping is ambiguous")
        outcome_map, yes_outcome = mapped
        token_id = _token_id(outcome_map[side])
        if not token_id:
            raise TargetTakerLiveError(f"Predict.fun {side} token ID is missing")
        self._check_predict_approvals(builder, market)

        raw_book = client.orderbook(market_id)
        precision = max(1, int(market.get("decimalPrecision") or 2))
        books = derive_outcome_books(raw_book, yes_outcome=yes_outcome, precision=precision)
        latest_ask = _finite(_dict(books.get(side)).get("bestAsk"))
        if latest_ask is None or not 0 < latest_ask <= MAX_ASK:
            raise TargetTakerLiveError("Predict.fun latest ask is unavailable or outside strategy cap")
        maximum_price = min(MAX_ASK, signal_ask + self.config.max_price_drift)
        if latest_ask > maximum_price + 1e-12:
            raise TargetTakerLiveError(
                f"Predict.fun ask moved from {signal_ask:.6f} to {latest_ask:.6f}, cap={maximum_price:.6f}"
            )

        price_wei = int(round(latest_ask * WEI))
        shares = Decimal(str(self.config.notional_usdt)) / Decimal(str(latest_ask))
        quantity_wei = max(
            1,
            int((shares * Decimal(WEI)).to_integral_value(rounding=ROUND_DOWN)),
        )
        amounts = builder.get_limit_order_amounts(
            LimitHelperInput(
                side=Side.BUY,
                price_per_share_wei=price_wei,
                quantity_wei=quantity_wei,
            )
        )
        maker_usdt = float(amounts.maker_amount) / WEI
        if maker_usdt > self.config.notional_usdt + 0.000001:
            raise TargetTakerLiveError(
                f"Predict.fun rounded maker amount {maker_usdt:.8f} exceeds configured notional "
                f"{self.config.notional_usdt:.8f}"
            )
        window_end_ms = int(_finite(snapshot.get("window_end_ms") or snapshot.get("windowEndMs")) or 0)
        now_ms = int(time.time() * 1000)
        expires_ms = window_end_ms if window_end_ms > now_ms else now_ms + 60_000
        order = builder.build_order(
            "LIMIT",
            BuildOrderInput(
                side=Side.BUY,
                token_id=token_id,
                maker_amount=str(amounts.maker_amount),
                taker_amount=str(amounts.taker_amount),
                fee_rate_bps=str(int(market.get("feeRateBps") or 0)),
                expires_at=datetime.fromtimestamp(expires_ms / 1000.0, tz=timezone.utc),
            ),
        )
        typed = builder.build_typed_data(
            order,
            is_neg_risk=bool(market.get("isNegRisk")),
            is_yield_bearing=bool(market.get("isYieldBearing")),
        )
        signed = builder.sign_typed_data_order(typed)
        order_hash = builder.build_typed_data_hash(typed)
        request_data = {
            "pricePerShare": str(amounts.price_per_share),
            "strategy": "LIMIT",
            "slippageBps": "0",
            "isFillOrKill": True,
            "isPostOnly": False,
            "reservedBalancePolicy": "REJECT_MARKET_ORDER",
            "isMinAmountOut": True,
            "selfTradePrevention": "CANCEL_MAKER",
            "order": _signed_order_payload(signed, order_hash),
        }
        try:
            placed = client.create_order(request_data)
        except PredictDirectTransportError as exc:
            raise TargetTakerLiveAmbiguousError(str(exc)) from exc
        except PredictDirectApiError as exc:
            if exc.status_code >= 500:
                raise TargetTakerLiveAmbiguousError(str(exc)) from exc
            raise
        data = _data(placed)
        order_id = str(data.get("orderId") or "").strip()
        returned_hash = str(data.get("orderHash") or order_hash or "").strip()
        if not order_id or not returned_hash:
            raise TargetTakerLiveAmbiguousError(
                "Predict.fun accepted response lacked orderId/orderHash"
            )
        return {
            "status": "SUBMITTED",
            "venue": "predictfun",
            "sourceMarketId": market_id,
            "venueMarketId": market_id,
            "side": side,
            "signalId": signal_id,
            "signalAsk": signal_ask,
            "executionPrice": latest_ask,
            "shares": float(amounts.taker_amount) / WEI,
            "submittedUsdt": maker_usdt,
            "vendorOrderId": order_id,
            "vendorOrderHash": returned_hash,
            "fillOrKill": True,
            "postOnly": False,
        }

    def _ensure_binance(self) -> BinancePredictionTradingClient:
        if self._binance_client is not None:
            return self._binance_client
        api_key = str(os.environ.get("BINANCE_API_KEY") or "").strip()
        api_secret = str(os.environ.get("BINANCE_API_SECRET") or "").strip()
        if not api_key or not api_secret:
            raise TargetTakerLiveError(
                "Binance live execution requires BINANCE_API_KEY and BINANCE_API_SECRET"
            )
        self._binance_client = BinancePredictionTradingClient(api_key, api_secret)
        return self._binance_client

    @staticmethod
    def _binance_wallet() -> tuple[str, str, str]:
        address = str(os.environ.get(BINANCE_WALLET_ADDRESS_ENV) or "").strip()
        wallet_id = str(os.environ.get(BINANCE_WALLET_ID_ENV) or "").strip()
        account_type = str(os.environ.get(BINANCE_ACCOUNT_TYPE_ENV) or "SPOT").strip().upper()
        if not address or not wallet_id:
            raise TargetTakerLiveError(
                f"Binance Target Taker requires {BINANCE_WALLET_ADDRESS_ENV} and {BINANCE_WALLET_ID_ENV}; "
                "wallet selection is intentionally never guessed for real-money orders"
            )
        if account_type not in {"SPOT", "FUNDING"}:
            raise TargetTakerLiveError(
                f"{BINANCE_ACCOUNT_TYPE_ENV} must be SPOT or FUNDING; MPC is a fundingSource, not accountType"
            )
        return address, wallet_id, account_type

    def _execute_binance(
        self,
        *,
        source_market_id: int,
        side: str,
        signal_ask: float,
        snapshot: dict[str, Any],
        signal_id: str,
    ) -> dict[str, Any]:
        client = self._ensure_binance()
        wallet_address, wallet_id, account_type = self._binance_wallet()
        symbol = str(os.environ.get(BINANCE_SYMBOL_ENV, "BTCUSDT")).strip().upper()
        expected_start_ms = int(
            _finite(snapshot.get("bucket_start_sec") or snapshot.get("bucketStartSec")) or 0
        ) * 1000
        expected_end_ms = int(
            _finite(snapshot.get("window_end_ms") or snapshot.get("windowEndMs")) or 0
        )
        if expected_start_ms <= 0 or expected_end_ms <= expected_start_ms:
            raise TargetTakerLiveError("Target Taker snapshot has no valid five-minute window")
        summary = find_exact_market_summary(
            client,
            symbol=symbol,
            target_start_ms=expected_start_ms,
            max_pages=2,
        )
        if not isinstance(summary, dict):
            raise TargetTakerLiveError(
                f"Binance exact {symbol} prediction market was not found for the EBM signal window"
            )
        cache = live_cache_from_summary(summary)
        if not isinstance(cache, dict):
            raise TargetTakerLiveError("Binance exact market metadata is incomplete")
        if abs(int(cache["end_ms"]) - expected_end_ms) > 2_000:
            raise TargetTakerLiveError("Binance market end does not match the EBM signal window")

        venue_market_id = int(cache["market_id"])
        topic_id = int(cache["topic_id"])
        up_token = str(cache["up_token_id"])
        down_token = str(cache["down_token_id"])
        token_id = up_token if side == "UP" else down_token
        now_ms = int(time.time() * 1000)
        up_book = client.orderbook(venue_market_id, up_token)
        down_book = client.orderbook(venue_market_id, down_token)
        top = binance_top_of_book(up_book, down_book, now_ms)
        latest_ask = top.up_ask if side == "UP" else top.down_ask
        if latest_ask is None or not 0 < latest_ask <= MAX_ASK:
            raise TargetTakerLiveError("Binance latest ask is unavailable or outside strategy cap")
        if top.book_age_ms is None or top.book_age_ms > 2_000:
            raise TargetTakerLiveError(
                "Binance order book has no valid fresh timestamp"
                if top.book_age_ms is None
                else f"Binance order book is stale: {top.book_age_ms:.0f}ms"
            )
        maximum_price = min(MAX_ASK, signal_ask + self.config.max_price_drift)
        if latest_ask > maximum_price + 1e-12:
            raise TargetTakerLiveError(
                f"Binance ask moved from {signal_ask:.6f} to {latest_ask:.6f}, cap={maximum_price:.6f}"
            )

        amount_in_wei = int(
            (Decimal(str(self.config.notional_usdt)) * Decimal(WEI)).to_integral_value()
        )
        quote_started = time.monotonic()
        quote = client.get_quote(
            wallet_address=wallet_address,
            token_id=token_id,
            amount_in_wei=str(amount_in_wei),
            price_limit=None,
            slippage_bps=0,
            fee_rate_bps=int(cache["fee_rate_bps"]),
            funding_source="MPC",
            side="BUY",
            order_type="MARKET",
        )
        quote_id = str(quote.get("quoteId") or "").strip()
        quote_amount_in = _finite(quote.get("amountIn"))
        quote_amount_out = _finite(quote.get("amountOut"))
        quote_price = _finite(quote.get("averagePrice"))
        if not quote_id:
            raise TargetTakerLiveError("Binance quote response contained no quoteId")
        if quote_amount_in is None or quote_amount_in <= 0 or quote_amount_in > amount_in_wei:
            raise TargetTakerLiveError("Binance quote exceeds or invalidates the configured notional hard cap")
        if quote_amount_out is None or quote_amount_out <= 0:
            raise TargetTakerLiveError("Binance quote returned no positive outcome amount")
        if quote_price is None or quote_price > maximum_price + 1e-12:
            raise TargetTakerLiveError(
                f"Binance quote average price {quote_price} exceeds execution cap {maximum_price:.6f}"
            )
        if str(quote.get("orderType") or "MARKET").upper() != "MARKET":
            raise TargetTakerLiveError("Binance quote changed the requested order type")
        if str(quote.get("side") or "BUY").upper() != "BUY":
            raise TargetTakerLiveError("Binance quote changed the requested order side")
        if quote.get("tokenId") is not None and str(quote.get("tokenId")) != token_id:
            raise TargetTakerLiveError("Binance quote token does not match selected EBM side")
        expiry = int(_finite(quote.get("expireAt")) or 0)
        if expiry and expiry <= client.server_timestamp_ms() + 250:
            raise TargetTakerLiveError("Binance quote expired before placement")

        try:
            placed = client.place_market_order(
                wallet_address=wallet_address,
                wallet_id=wallet_id,
                quote_id=quote_id,
                slippage_bps=0,
                account_type=account_type,
                funding_source="MPC",
            )
        except Exception as exc:
            if isinstance(exc, ApiTransportError) or (
                isinstance(exc, ApiHttpError) and exc.status_code >= 500
            ):
                raise TargetTakerLiveAmbiguousError(str(exc)) from exc
            raise
        order_id = _first_text(placed, "orderId", "id")
        if not order_id:
            raise TargetTakerLiveAmbiguousError(
                "Binance placement response contained no orderId"
            )
        placed_data = _data(placed)
        filled_usdt = _first_number(placed, "filledUsdtAmount", "makerUsdtAmount")
        filled_shares = _first_number(placed, "filledShareQty", "makerShareQty")
        return {
            "status": "SUBMITTED",
            "venue": "binance",
            "sourceMarketId": source_market_id,
            "venueMarketId": venue_market_id,
            "venueTopicId": topic_id,
            "side": side,
            "signalId": signal_id,
            "signalAsk": signal_ask,
            "executionPrice": quote_price,
            "shares": (
                filled_shares
                if filled_shares is not None
                else quote_amount_out / WEI
            ),
            "submittedUsdt": (
                filled_usdt
                if filled_usdt is not None
                else quote_amount_in / WEI
            ),
            "vendorOrderId": order_id,
            "exchangeStatus": str(placed_data.get("status") or "SUBMITTED"),
            "quoteRttMs": (time.monotonic() - quote_started) * 1000.0,
            "fillOrKill": True,
        }
