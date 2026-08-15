from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

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

from .core import BinancePredictionTradingClient, binance_top_of_book
from .predict_wallet_target_taker_public_side_strategy_v1 import MAX_ASK, VERSION as STRATEGY_VERSION
from .wallet_maker_clone_predict_direct_v8 import (
    WEI,
    PredictDirectRestClient,
    PredictDirectTransportError,
    _outcome_map,
    _signed_order_payload,
    derive_outcome_books,
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


class TargetTakerLiveError(RuntimeError):
    pass


class TargetTakerLiveAmbiguousError(TargetTakerLiveError):
    """A write may have reached the venue; caller must not retry blindly."""


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
        return cls(mode=mode, venue=venue, notional_usdt=notional, cohort=cohort, max_price_drift=drift)

    def snapshot(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "venue": self.venue,
            "notionalUsdt": self.notional_usdt,
            "cohort": self.cohort,
            "maxPriceDrift": self.max_price_drift,
            "strategy": STRATEGY_VERSION,
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


def _token_id(outcome: dict[str, Any]) -> str | None:
    for key in ("tokenId", "onChainId", "token_id", "id"):
        value = outcome.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
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


def _safe_error(exc: Exception) -> str:
    # The clients deliberately avoid signed URLs in their errors. Keep this
    # truncated too so private credentials can never be persisted by accident.
    return f"{type(exc).__name__}: {str(exc)[:500]}"


class TargetTakerLiveExecutor:
    """Fail-closed one-shot executor for TARGET_TAKER_PUBLIC_SIDE_V1_EBM_FORWARD.

    Signal generation remains owned by the frozen EBM observer. This class only
    converts an already-approved TRADE decision into one venue order. It has no
    retry loop: ambiguous write outcomes must be reconciled manually/externally.
    """

    def __init__(self, config: TargetTakerLiveConfig | None = None) -> None:
        self.config = config or TargetTakerLiveConfig.from_env()
        self._predict_client: PredictDirectRestClient | None = None
        self._predict_builder: OrderBuilder | None = None
        self._predict_approvals_market_id: int | None = None
        self._predict_approvals_ok: bool | None = None
        self._binance_client: BinancePredictionTradingClient | None = None
        self.last_result: dict[str, Any] | None = None

    def close(self) -> None:
        if self._predict_client is not None:
            try:
                self._predict_client.close()
            except Exception:
                pass
        if self._binance_client is not None:
            try:
                self._binance_client.close()
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
                    market_id=int(market_id), side=side, signal_ask=signal_ask,
                    snapshot=snapshot, signal_id=signal_id,
                )
            elif self.config.venue == "binance":
                result = self._execute_binance(
                    source_market_id=int(market_id), side=side, signal_ask=signal_ask,
                    snapshot=snapshot, signal_id=signal_id,
                )
            else:  # guarded by config validation
                result = {"status": "UNSUPPORTED_VENUE", "venue": self.config.venue}
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
            api_key, jwt=static_jwt, auth_signer=auth_signer, sign_auth_message=sign_message
        )
        client.authenticate(force=False)
        self._predict_client = client
        self._predict_builder = builder
        return client, builder

    def _predict_market(self, client: PredictDirectRestClient, market_id: int) -> dict[str, Any]:
        for row in client.list_markets(first=100, max_pages=5):
            if _market_id(row) == int(market_id):
                return row
        raise TargetTakerLiveError(f"Predict.fun market {market_id} was not found in current CRYPTO_UP_DOWN markets")

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

        # Aggressive FOK LIMIT at the freshly re-read best ask gives taker
        # semantics while retaining a deterministic price ceiling.
        price_wei = int(round(latest_ask * WEI))
        shares = self.config.notional_usdt / latest_ask
        quantity_wei = max(1, int(math.floor(shares * WEI)))
        amounts = builder.get_limit_order_amounts(
            LimitHelperInput(side=Side.BUY, price_per_share_wei=price_wei, quantity_wei=quantity_wei)
        )
        maker_usdt = float(amounts.maker_amount) / WEI
        if maker_usdt > self.config.notional_usdt + 0.000001:
            raise TargetTakerLiveError(
                f"Predict.fun rounded maker amount {maker_usdt:.8f} exceeds configured notional {self.config.notional_usdt:.8f}"
            )
        window_end_ms = int(_finite(snapshot.get("window_end_ms") or snapshot.get("windowEndMs")) or 0)
        expires_ms = window_end_ms if window_end_ms > int(time.time() * 1000) else int(time.time() * 1000) + 60_000
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
        data = _data(placed)
        order_id = str(data.get("orderId") or "").strip()
        returned_hash = str(data.get("orderHash") or order_hash or "").strip()
        if not order_id or not returned_hash:
            raise TargetTakerLiveAmbiguousError("Predict.fun accepted response lacked orderId/orderHash")
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
            raise TargetTakerLiveError("Binance live execution requires BINANCE_API_KEY and BINANCE_API_SECRET")
        self._binance_client = BinancePredictionTradingClient(api_key, api_secret)
        return self._binance_client

    def _binance_wallet(self) -> tuple[str, str, str]:
        address = str(os.environ.get(BINANCE_WALLET_ADDRESS_ENV) or "").strip()
        wallet_id = str(os.environ.get(BINANCE_WALLET_ID_ENV) or "").strip()
        account_type = str(os.environ.get(BINANCE_ACCOUNT_TYPE_ENV) or "MPC").strip().upper()
        if not address or not wallet_id:
            raise TargetTakerLiveError(
                f"Binance Target Taker requires {BINANCE_WALLET_ADDRESS_ENV} and {BINANCE_WALLET_ID_ENV}; "
                "wallet selection is intentionally never guessed for real-money orders"
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
        summary = client.find_market_summary(symbol, max_pages=2)
        if not isinstance(summary, dict):
            raise TargetTakerLiveError(f"Binance current {symbol} prediction market is unavailable")
        expected_start = int(_finite(snapshot.get("bucket_start_sec") or snapshot.get("bucketStartSec")) or 0) * 1000
        expected_end = int(_finite(snapshot.get("window_end_ms") or snapshot.get("windowEndMs")) or 0)
        venue_start = int(_finite(summary.get("startDate")) or 0)
        venue_end = int(_finite(summary.get("endDate")) or 0)
        if expected_start and venue_start and abs(expected_start - venue_start) > 2_000:
            raise TargetTakerLiveError("Binance market window does not match the EBM signal window")
        if expected_end and venue_end and abs(expected_end - venue_end) > 2_000:
            raise TargetTakerLiveError("Binance market end does not match the EBM signal window")

        selected = _dict(summary.get("_selectedMarket"))
        market = _dict(selected.get("market"))
        outcome = _dict(selected.get("up" if side == "UP" else "down"))
        venue_market_id = _market_id(market)
        topic_id = int(_finite(summary.get("marketTopicId")) or 0)
        token_id = _token_id(outcome)
        if venue_market_id <= 0 or topic_id <= 0 or not token_id:
            raise TargetTakerLiveError("Binance prediction market/outcome mapping is incomplete")

        up = _dict(selected.get("up"))
        down = _dict(selected.get("down"))
        up_token = _token_id(up)
        down_token = _token_id(down)
        if not up_token or not down_token:
            raise TargetTakerLiveError("Binance UP/DOWN token mapping is incomplete")
        now_ms = int(time.time() * 1000)
        up_book = client.orderbook(venue_market_id, up_token)
        down_book = client.orderbook(venue_market_id, down_token)
        top = binance_top_of_book(up_book, down_book, now_ms)
        latest_ask = top.up_ask if side == "UP" else top.down_ask
        if latest_ask is None or not 0 < latest_ask <= MAX_ASK:
            raise TargetTakerLiveError("Binance latest ask is unavailable or outside strategy cap")
        if top.book_age_ms is not None and top.book_age_ms > 2_000:
            raise TargetTakerLiveError(f"Binance order book is stale: {top.book_age_ms:.0f}ms")
        maximum_price = min(MAX_ASK, signal_ask + self.config.max_price_drift)
        if latest_ask > maximum_price + 1e-12:
            raise TargetTakerLiveError(
                f"Binance ask moved from {signal_ask:.6f} to {latest_ask:.6f}, cap={maximum_price:.6f}"
            )

        amount_wei = str(int((Decimal(str(self.config.notional_usdt)) * Decimal(WEI)).to_integral_value()))
        fee_rate_bps = int(market.get("feeRateBps") or summary.get("feeRateBps") or 0)
        quote_started = time.monotonic()
        quote = client.get_quote(
            wallet_address=wallet_address,
            token_id=token_id,
            amount_in_wei=amount_wei,
            price_limit=None,
            slippage_bps=0,
            fee_rate_bps=fee_rate_bps,
            funding_source="MPC",
            side="BUY",
            order_type="MARKET",
        )
        quote_data = _data(quote)
        quote_id = str(quote_data.get("quoteId") or "").strip()
        quote_price = _first_number(quote, "averagePrice", "avgPrice", "price")
        if not quote_id:
            raise TargetTakerLiveError("Binance quote response contained no quoteId")
        if quote_price is None or quote_price > maximum_price + 1e-12:
            raise TargetTakerLiveError(
                f"Binance quote average price {quote_price} exceeds execution cap {maximum_price:.6f}"
            )
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
            # ApiTransportError is deliberately ambiguous for signed POSTs. A
            # network exception after submission must never trigger auto-retry.
            from .core import ApiTransportError
            if isinstance(exc, ApiTransportError):
                raise TargetTakerLiveAmbiguousError(str(exc)) from exc
            raise
        order_id = _first_text(placed, "orderId", "id")
        if not order_id:
            raise TargetTakerLiveAmbiguousError("Binance placement response contained no orderId")
        placed_data = _data(placed)
        shares = _first_number(placed, "filledShareQty", "makerShareQty", "amountOut")
        submitted = _first_number(placed, "filledUsdtAmount", "makerUsdtAmount")
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
            "shares": shares,
            "submittedUsdt": submitted if submitted is not None else self.config.notional_usdt,
            "vendorOrderId": order_id,
            "exchangeStatus": str(placed_data.get("status") or "SUBMITTED"),
            "quoteRttMs": (time.monotonic() - quote_started) * 1000.0,
            "fillOrKill": True,
        }
