from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct
from predict_sdk import (
    ApprovalScope,
    BuildOrderInput,
    CancelOrdersOptions,
    ChainId,
    LimitHelperInput,
    Order,
    OrderBuilder,
    OrderBuilderOptions,
    Side,
    SignatureType,
)

from . import wallet_maker_clone_live as core
from . import wallet_maker_clone_live_v4 as v4base
from . import wallet_maker_clone_live_v8 as base


API_BASE = os.environ.get("PREDICT_FUN_API_BASE", "https://api.predict.fun").rstrip("/")
DIRECT_BOOK_POLL_SECONDS = max(
    1.0,
    float(os.environ.get("PREDICT_WALLET_MAKER_CLONE_PREDICT_BOOK_SECONDS", "1.0")),
)
DIRECT_ORDER_POLL_SECONDS = max(
    1.0,
    float(os.environ.get("PREDICT_WALLET_MAKER_CLONE_PREDICT_ORDER_SECONDS", "1.0")),
)
DIRECT_MARKET_REFRESH_SECONDS = max(
    5.0,
    float(os.environ.get("PREDICT_WALLET_MAKER_CLONE_PREDICT_MARKET_SECONDS", "15.0")),
)
WEI = 10**18
ET = ZoneInfo("America/New_York")
TITLE_RE = re.compile(
    r"^(Ethereum|BNB) Up or Down - (.+),\s*(\d{1,2})(?::(\d{2}))?(AM|PM)-"
    r"(\d{1,2})(?::(\d{2}))?(AM|PM) ET$"
)


class PredictDirectTransportError(RuntimeError):
    pass


class PredictDirectApiError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = int(status_code)


@dataclass
class DirectMarketWindow:
    start_ms: int
    end_ms: int


def _minute_of_day(hour: int, minute: int, ampm: str) -> int:
    value = int(hour) % 12
    if str(ampm).upper() == "PM":
        value += 12
    return value * 60 + int(minute)


def parse_exact_5m_window(title: Any, reference_ms: int | None = None) -> DirectMarketWindow | None:
    """Parse Predict ETH/BNB exact 5m titles into a UTC market window."""
    match = TITLE_RE.match(str(title or "").strip())
    if not match:
        return None
    start_minute_of_day = _minute_of_day(
        int(match.group(3)), int(match.group(4) or 0), match.group(5)
    )
    end_minute_of_day = _minute_of_day(
        int(match.group(6)), int(match.group(7) or 0), match.group(8)
    )
    duration = end_minute_of_day - start_minute_of_day
    if duration < 0:
        duration += 24 * 60
    if duration != 5:
        return None

    ref_ms = int(reference_ms if reference_ms is not None else core._now_ms())
    ref_dt = datetime.fromtimestamp(ref_ms / 1000.0, tz=timezone.utc)
    local_year = ref_dt.astimezone(ET).year
    date_text = match.group(2).strip()
    start_hour = start_minute_of_day // 60
    start_minute = start_minute_of_day % 60
    candidates: list[datetime] = []
    for year in (local_year - 1, local_year, local_year + 1):
        parsed_date: datetime | None = None
        for fmt in ("%B %d %Y", "%b %d %Y", "%B %d, %Y", "%b %d, %Y"):
            try:
                parsed_date = datetime.strptime(f"{date_text} {year}", fmt)
                break
            except ValueError:
                continue
        if parsed_date is None:
            continue
        local_start = datetime(
            parsed_date.year,
            parsed_date.month,
            parsed_date.day,
            start_hour,
            start_minute,
            tzinfo=ET,
        )
        candidates.append(local_start.astimezone(timezone.utc))
    if not candidates:
        return None
    chosen = min(candidates, key=lambda row: abs((row - ref_dt).total_seconds()))
    start_ms = int(chosen.timestamp() * 1000)
    return DirectMarketWindow(start_ms=start_ms, end_ms=start_ms + 300_000)


def _asset_matches_market(asset: str, market: dict[str, Any]) -> bool:
    asset = str(asset).upper()
    variant = market.get("variantData") if isinstance(market.get("variantData"), dict) else {}
    if str(variant.get("type") or "").upper() != "CRYPTO_UP_DOWN":
        return False
    symbol = str(variant.get("priceFeedSymbol") or "").upper()
    if asset == "ETH" and symbol and "ETH" not in symbol:
        return False
    if asset == "BNB" and symbol and "BNB" not in symbol:
        return False
    title = str(market.get("title") or market.get("question") or "")
    expected = "Ethereum Up or Down - " if asset == "ETH" else "BNB Up or Down - "
    return title.startswith(expected) and parse_exact_5m_window(title) is not None


def _outcome_map(market: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], str] | None:
    outcomes = market.get("outcomes") if isinstance(market.get("outcomes"), list) else []
    mapped: dict[str, dict[str, Any]] = {}
    yes_side: str | None = None
    for raw in outcomes:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").upper().strip()
        if name not in {"UP", "DOWN"}:
            continue
        token = str(raw.get("onChainId") or raw.get("tokenId") or "").strip()
        if not token:
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


def derive_outcome_books(
    orderbook: dict[str, Any], *, yes_outcome: str, precision: int
) -> dict[str, dict[str, Any]]:
    """Convert Predict's Yes-only book into UP and DOWN top-of-book views."""
    data = orderbook.get("data") if isinstance(orderbook.get("data"), dict) else orderbook
    if not isinstance(data, dict):
        return {"UP": {}, "DOWN": {}}
    yes_bid, yes_bid_size = core._best_level(data, "bid")
    yes_ask, yes_ask_size = core._best_level(data, "ask")
    if yes_bid is None or yes_ask is None:
        return {"UP": {}, "DOWN": {}}
    decimals = max(1, int(precision))
    no_bid = round(1.0 - float(yes_ask), decimals)
    no_ask = round(1.0 - float(yes_bid), decimals)
    yes_book = {
        "bestBid": float(yes_bid),
        "bestBidSize": yes_bid_size,
        "bestAsk": float(yes_ask),
        "bestAskSize": yes_ask_size,
    }
    no_book = {
        "bestBid": no_bid,
        "bestBidSize": yes_ask_size,
        "bestAsk": no_ask,
        "bestAskSize": yes_bid_size,
    }
    if str(yes_outcome).upper() == "UP":
        return {"UP": yes_book, "DOWN": no_book}
    return {"UP": no_book, "DOWN": yes_book}


def _wei_float(value: Any) -> float:
    try:
        return max(0.0, float(value) / WEI)
    except (TypeError, ValueError):
        return 0.0


def normalize_predict_order_update(row: dict[str, Any]) -> dict[str, Any]:
    contract = row.get("order") if isinstance(row.get("order"), dict) else {}
    status = str(row.get("status") or "UNKNOWN").upper()
    amount_wei = max(0, int(float(row.get("amount") or contract.get("takerAmount") or 0)))
    filled_wei = max(0, int(float(row.get("amountFilled") or 0)))
    maker_amount_wei = max(0, int(float(contract.get("makerAmount") or 0)))
    total_shares = amount_wei / WEI
    filled_shares = min(total_shares, filled_wei / WEI) if total_shares > 0 else filled_wei / WEI
    fill_percentage = min(1.0, filled_shares / total_shares) if total_shares > 0 else 0.0
    filled_cost = (
        (maker_amount_wei / WEI) * fill_percentage
        if maker_amount_wei > 0 and fill_percentage > 0
        else 0.0
    )
    if status == "FILLED" or fill_percentage >= 1.0 - 1e-9:
        local_state = "FILLED"
    elif status in {"CANCELED", "CANCELLED", "EXPIRED"}:
        local_state = "CANCELED"
    elif status in {"REJECTED", "FAILED"}:
        local_state = "REJECTED"
    elif filled_shares > 0:
        local_state = "PARTIAL_FILL"
    else:
        local_state = "RESTING"
    return {
        "order_status": status,
        "maker_usdt_amount": maker_amount_wei / WEI,
        "maker_share_qty": total_shares,
        "filled_usdt_amount": filled_cost,
        "filled_share_qty": filled_shares,
        "fill_percentage": fill_percentage,
        "last_reconciled_at_ms": core._now_ms(),
        "raw_status_json": json.dumps(row, separators=(",", ":"), allow_nan=False, default=str),
        "state": local_state,
    }


def _sdk_order_from_remote(row: dict[str, Any]) -> Order | None:
    raw = row.get("order") if isinstance(row.get("order"), dict) else None
    if not isinstance(raw, dict):
        return None
    try:
        return Order(
            salt=str(raw["salt"]),
            maker=str(raw["maker"]),
            signer=str(raw["signer"]),
            taker=str(raw["taker"]),
            token_id=str(raw["tokenId"]),
            maker_amount=str(raw["makerAmount"]),
            taker_amount=str(raw["takerAmount"]),
            expiration=str(raw["expiration"]),
            nonce=str(raw["nonce"]),
            fee_rate_bps=str(raw["feeRateBps"]),
            side=Side(int(raw["side"])),
            signature_type=SignatureType(int(raw["signatureType"])),
        )
    except Exception:
        return None


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


class PredictDirectRestClient:
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
        self.http = httpx.Client(
            base_url=API_BASE,
            timeout=httpx.Timeout(5.0, connect=2.0),
            verify=True,
            trust_env=False,
            follow_redirects=True,
            headers={"Accept": "application/json", "User-Agent": "BTC-5M-Lab-Predict-Direct-V8/1.0"},
        )
        self.auth_lock = threading.RLock()

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
            raise PredictDirectTransportError(f"{method} {path}: {type(exc).__name__}: {exc}") from exc
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
            raise PredictDirectApiError(response.status_code, f"Predict rejected {method} {path}: {str(payload)[:500]}")
        return payload

    def authenticate(self, *, force: bool = False) -> str:
        with self.auth_lock:
            if self.jwt and not force:
                return self.jwt
            message_payload = self._request("GET", "/v1/auth/message", auth=False)
            data = message_payload.get("data") if isinstance(message_payload.get("data"), dict) else {}
            message = str(data.get("message") or "")
            if not message:
                raise PredictDirectTransportError("Predict auth message response contained no message")
            signature = self.sign_auth_message(message)
            token_payload = self._request(
                "POST",
                "/v1/auth",
                auth=False,
                json={"signer": self.auth_signer, "signature": signature, "message": message},
            )
            token_data = token_payload.get("data") if isinstance(token_payload.get("data"), dict) else {}
            token = str(token_data.get("token") or "").strip()
            if not token:
                raise PredictDirectTransportError("Predict auth response contained no JWT token")
            self.jwt = token
            return token

    def list_markets(self, *, first: int = 100, max_pages: int = 5) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        after: str | None = None
        seen: set[str] = set()
        for _ in range(max(1, int(max_pages))):
            params: dict[str, Any] = {"first": max(1, min(100, int(first))), "marketVariant": "CRYPTO_UP_DOWN"}
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

    def list_orders(self, *, status: str = "OPEN", first: int = 100) -> list[dict[str, Any]]:
        payload = self._request(
            "GET",
            "/v1/orders",
            auth=True,
            params={"first": max(1, min(100, int(first))), "status": str(status)},
        )
        return [row for row in (payload.get("data") or []) if isinstance(row, dict)]

    def order_by_hash(self, order_hash: str) -> dict[str, Any] | None:
        try:
            payload = self._request("GET", f"/v1/orders/{str(order_hash)}", auth=True)
        except PredictDirectApiError as exc:
            if exc.status_code == 404:
                return None
            raise
        data = payload.get("data") if isinstance(payload.get("data"), dict) else None
        return data if isinstance(data, dict) else None

    def remove_orders(self, order_ids: list[str]) -> dict[str, Any]:
        ids = [str(value) for value in order_ids if str(value)]
        if not ids:
            return {"success": True, "removed": [], "noop": []}
        return self._request("POST", "/v1/orders/remove", auth=True, json={"data": {"ids": ids}})


class PredictDirectV8WalletMakerCloneEngine(base.BoundedRiskPairedWalletMakerCloneEngine):
    """V8 strategy/risk rules with native Predict order signing and post-only LIMIT execution.

    This is a separate execution venue, not a replacement for the Binance Prediction
    V8 path. It preserves V8's paired-cycle, max-entry, max-loss, T-60 and T-30
    controls while replacing quote/place/reconcile/cancel with Predict-native APIs.

    Cancellation is deliberately two-stage: remove the order from Predict's off-chain
    book first for speed, then submit an on-chain SDK cancellation. A REST remove alone
    is never considered terminal because Predict documents that it does not invalidate
    the signed order on-chain.
    """

    VERSION = "WALLET_MAKER_CLONE_PREDICT_DIRECT_V8_BOUNDED_PAIRED_RISK"

    def __init__(self, db_path: Path = core.DB_PATH) -> None:
        self.builder: OrderBuilder | None = None
        self.direct_client: PredictDirectRestClient | None = None
        self.predict_account: str | None = None
        self.signer_address: str | None = None
        self.last_market_refresh = 0.0
        self.last_approval_market_id: int | None = None
        self.approvals_ok: bool | None = None
        self.approval_details: list[dict[str, Any]] = []
        self.last_final_reconcile = 0.0
        super().__init__(db_path)

    def _create_schema(self) -> None:
        super()._create_schema()
        with self.db_lock:
            columns = {
                str(row[1])
                for row in self.db.execute("PRAGMA table_info(wallet_maker_clone_orders)").fetchall()
            }
            if "direct_removed_from_book" not in columns:
                self.db.execute(
                    "ALTER TABLE wallet_maker_clone_orders ADD COLUMN direct_removed_from_book INTEGER NOT NULL DEFAULT 0"
                )
            if "direct_cancel_onchain_confirmed" not in columns:
                self.db.execute(
                    "ALTER TABLE wallet_maker_clone_orders ADD COLUMN direct_cancel_onchain_confirmed INTEGER NOT NULL DEFAULT 0"
                )
            if "direct_reward_earning_rate" not in columns:
                self.db.execute(
                    "ALTER TABLE wallet_maker_clone_orders ADD COLUMN direct_reward_earning_rate REAL"
                )
            self.db.commit()

    def _update_order(self, order_row_id: int, **values: Any) -> None:
        direct_keys = {
            "direct_removed_from_book",
            "direct_cancel_onchain_confirmed",
            "direct_reward_earning_rate",
        }
        direct_values = {key: values.pop(key) for key in list(values) if key in direct_keys}
        super()._update_order(order_row_id, **values)
        if direct_values:
            assignments = ",".join(f"{key}=?" for key in direct_values)
            with self.db_lock:
                self.db.execute(
                    f"UPDATE wallet_maker_clone_orders SET {assignments},updated_at_ms=? WHERE id=?",
                    (*direct_values.values(), core._now_ms(), int(order_row_id)),
                )
                self.db.commit()

    def _settings(self) -> dict[str, Any]:
        settings = super()._settings()
        # Keep the existing $1 local floor for a clean A/B comparison with Binance
        # V8. It is a strategy comparison bound here, not a claim about Predict's
        # native venue minimum.
        settings["venueMinimumOrderUsdt"] = None
        settings["comparisonMinimumOrderUsdt"] = float(settings.get("minimumOrderUsdt") or 1.0)
        return settings

    def _ensure_client(self) -> bool:
        with self.lock:
            if self.direct_client is not None and self.builder is not None and self.wallet_address:
                self.client = self.direct_client  # type: ignore[assignment]
                return True

        api_key = str(os.environ.get("PREDICT_FUN_API_KEY") or "").strip()
        private_key = str(
            os.environ.get("PREDICT_FUN_PRIVATE_KEY")
            or os.environ.get("PREDICT_FUN_PRIVY_PRIVATE_KEY")
            or ""
        ).strip()
        predict_account = str(os.environ.get("PREDICT_FUN_ACCOUNT_ADDRESS") or "").strip()
        static_jwt = str(os.environ.get("PREDICT_FUN_JWT") or "").strip() or None
        if not api_key or not private_key:
            self.status = "CONFIG_REQUIRED"
            self.last_error = (
                "Predict Direct requires PREDICT_FUN_API_KEY and PREDICT_FUN_PRIVATE_KEY "
                "(or PREDICT_FUN_PRIVY_PRIVATE_KEY); PREDICT_FUN_ACCOUNT_ADDRESS is also required "
                "when trading through the Predict web-app Smart Wallet"
            )
            return False

        try:
            account = Account.from_key(private_key)
            options = OrderBuilderOptions(predict_account=predict_account or None, log_level="WARN")
            builder = OrderBuilder.make(ChainId.BNB_MAINNET, private_key, options)
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
            with self.lock:
                self.builder = builder
                self.direct_client = client
                self.client = client  # type: ignore[assignment]
                self.predict_account = predict_account or None
                self.signer_address = account.address
                self.wallet_address = auth_signer
                self.wallet_id = "PREDICT_DIRECT"
            self.credential_source = "PREDICT_FUN_API_KEY+PRIVATE_KEY"
            self.last_error = None
            return True
        except Exception as exc:
            self.status = "BLOCKED_PREFLIGHT"
            self.last_error = f"Predict Direct preflight: {str(exc)[:500]}"
            return False

    def _prime_market(self) -> dict[str, Any] | None:
        if not self._ensure_client():
            return None
        now_ms = core._now_ms()
        cached = self.market if isinstance(self.market, dict) else None
        if cached:
            start_ms = int(cached.get("start_ms") or (int(cached.get("end_ms") or 0) - 300_000))
            end_ms = int(cached.get("end_ms") or 0)
            if start_ms <= now_ms < end_ms and time.monotonic() - self.last_market_refresh < DIRECT_MARKET_REFRESH_SECONDS:
                return dict(cached)

        client = self.direct_client
        assert client is not None
        self.last_market_refresh = time.monotonic()
        candidates: list[tuple[float, dict[str, Any], DirectMarketWindow, dict[str, dict[str, Any]], str]] = []
        for raw in client.list_markets(first=100, max_pages=5):
            if str(raw.get("tradingStatus") or "").upper() != "OPEN":
                continue
            if not _asset_matches_market(core.ASSET, raw):
                continue
            title = raw.get("title") or raw.get("question")
            window = parse_exact_5m_window(title, now_ms)
            outcomes = _outcome_map(raw)
            if window is None or outcomes is None:
                continue
            if not (window.start_ms - 5_000 <= now_ms < window.end_ms):
                continue
            outcome_map, yes_outcome = outcomes
            center = (window.start_ms + window.end_ms) / 2.0
            candidates.append((abs(now_ms - center), raw, window, outcome_map, yes_outcome))
        if not candidates:
            self.last_error = f"current Predict Direct {core.ASSET} exact 5m market unavailable"
            return None
        candidates.sort(key=lambda item: item[0])
        _, selected, window, outcome_map, yes_outcome = candidates[0]
        up = outcome_map["UP"]
        down = outcome_map["DOWN"]
        market = {
            "asset": core.ASSET,
            "symbol": core.SYMBOL,
            "market_id": int(selected.get("id") or 0),
            "topic_id": int(selected.get("id") or 0),
            "start_ms": int(window.start_ms),
            "end_ms": int(window.end_ms),
            "up_token_id": str(up.get("onChainId") or up.get("tokenId") or ""),
            "down_token_id": str(down.get("onChainId") or down.get("tokenId") or ""),
            "yes_outcome": yes_outcome,
            "fee_rate_bps": int(selected.get("feeRateBps") or 0),
            "precision": int(selected.get("decimalPrecision") or 2),
            "is_neg_risk": bool(selected.get("isNegRisk")),
            "is_yield_bearing": bool(selected.get("isYieldBearing")),
            "condition_id": str(selected.get("conditionId") or ""),
            "market_title": str(selected.get("title") or selected.get("question") or ""),
        }
        if market["market_id"] <= 0 or not market["up_token_id"] or not market["down_token_id"]:
            self.last_error = "Predict Direct market outcome metadata incomplete"
            return None
        self.market = dict(market)
        self._refresh_approval_state(market)
        return market

    def _refresh_approval_state(self, market: dict[str, Any]) -> None:
        market_id = int(market.get("market_id") or 0)
        if market_id <= 0 or market_id == self.last_approval_market_id:
            return
        builder = self.builder
        if builder is None:
            self.approvals_ok = False
            return
        try:
            steps = builder.get_approval_steps(
                ApprovalScope(
                    operation="TRADE",
                    is_neg_risk=bool(market.get("is_neg_risk")),
                    is_yield_bearing=bool(market.get("is_yield_bearing")),
                    side=Side.BUY,
                )
            )
            checks = builder.check_approvals(steps)
            details = [
                {
                    "id": check.step.id,
                    "label": check.step.label,
                    "satisfied": bool(check.satisfied),
                }
                for check in checks
            ]
            self.approvals_ok = all(row["satisfied"] for row in details)
            self.approval_details = details
            self.last_approval_market_id = market_id
            if not self.approvals_ok:
                self.last_error = "Predict Direct BUY approvals missing; run Predict SDK approvals before Echtgeld resume"
        except Exception as exc:
            self.approvals_ok = False
            self.approval_details = [{"id": "CHECK_FAILED", "label": str(exc)[:300], "satisfied": False}]
            self.last_approval_market_id = market_id
            self.last_error = f"Predict Direct approval check failed: {str(exc)[:400]}"

    def _poll_books(self, market: dict[str, Any]) -> None:
        if time.monotonic() - self.last_book_poll < DIRECT_BOOK_POLL_SECONDS:
            return
        self.last_book_poll = time.monotonic()
        client = self.direct_client
        if client is None:
            return
        observed = core._now_ms()
        try:
            raw = client.orderbook(int(market["market_id"]))
            books = derive_outcome_books(
                raw,
                yes_outcome=str(market["yes_outcome"]),
                precision=int(market.get("precision") or 2),
            )
            for side in ("UP", "DOWN"):
                books.setdefault(side, {})["observedAtMs"] = observed
                books[side]["venue"] = "PREDICT_DIRECT"
            self.books.update(books)
        except Exception as exc:
            self.last_error = f"Predict Direct orderbook: {str(exc)[:400]}"

    def _plan_order(self, side: str, market: dict[str, Any]) -> dict[str, Any] | None:
        if self.approvals_ok is not True:
            book = self.books.get(side) or {}
            return {
                "side": side,
                "blocked": True,
                "reason": "Predict Direct BUY approvals are not confirmed",
                "price": core._finite(book.get("bestBid")),
                "bestBid": core._finite(book.get("bestBid")),
                "bestAsk": core._finite(book.get("bestAsk")),
            }
        return super()._plan_order(side, market)

    def _plan_replenishment(self, side: str, market: dict[str, Any]) -> dict[str, Any] | None:
        if self.approvals_ok is not True:
            book = self.books.get(side) or {}
            return {
                "side": side,
                "blocked": True,
                "reason": "Predict Direct BUY approvals are not confirmed",
                "price": core._finite(book.get("bestBid")),
                "bestBid": core._finite(book.get("bestBid")),
                "bestAsk": core._finite(book.get("bestAsk")),
            }
        return super()._plan_replenishment(side, market)

    def _place_one(self, order_row_id: int, plan: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
        client = self.direct_client
        builder = self.builder
        if client is None or builder is None:
            return {"ok": False, "completedAtMs": core._now_ms(), "error": "Predict Direct client unavailable"}
        if self.approvals_ok is not True:
            return {"ok": False, "completedAtMs": core._now_ms(), "error": "Predict Direct approvals unavailable"}

        side = str(plan["side"])
        p0_ms = core._now_ms()
        p0 = time.monotonic()
        self._update_order(order_row_id, state="PLACING", place_started_at_ms=p0_ms)
        try:
            latest_raw = client.orderbook(int(market["market_id"]))
            latest_books = derive_outcome_books(
                latest_raw,
                yes_outcome=str(market["yes_outcome"]),
                precision=int(market.get("precision") or 2),
            )
            latest_ask = core._finite((latest_books.get(side) or {}).get("bestAsk"))
            if latest_ask is None or float(plan["price"]) + 1e-12 >= latest_ask:
                raise ValueError(
                    f"native post-only precheck blocked {side} price {plan['price']} >= ask {latest_ask}"
                )

            price_wei = int(round(float(plan["price"]) * WEI))
            quantity_wei = max(1, int(round(float(plan["plannedShares"]) * WEI)))
            amounts = builder.get_limit_order_amounts(
                LimitHelperInput(
                    side=Side.BUY,
                    price_per_share_wei=price_wei,
                    quantity_wei=quantity_wei,
                )
            )
            expires_at = datetime.fromtimestamp(int(market["end_ms"]) / 1000.0, tz=timezone.utc)
            order = builder.build_order(
                "LIMIT",
                BuildOrderInput(
                    side=Side.BUY,
                    token_id=str(plan["tokenId"]),
                    maker_amount=str(amounts.maker_amount),
                    taker_amount=str(amounts.taker_amount),
                    fee_rate_bps=str(int(market.get("fee_rate_bps") or 0)),
                    expires_at=expires_at,
                ),
            )
            typed = builder.build_typed_data(
                order,
                is_neg_risk=bool(market.get("is_neg_risk")),
                is_yield_bearing=bool(market.get("is_yield_bearing")),
            )
            signed = builder.sign_typed_data_order(typed)
            order_hash = builder.build_typed_data_hash(typed)
            request_data = {
                "pricePerShare": str(amounts.price_per_share),
                "strategy": "LIMIT",
                "slippageBps": "0",
                "isFillOrKill": False,
                "isPostOnly": True,
                "reservedBalancePolicy": "REJECT_MARKET_ORDER",
                "isMinAmountOut": False,
                "selfTradePrevention": "CANCEL_MAKER",
                "order": _signed_order_payload(signed, order_hash),
            }
            placed = client.create_order(request_data)
        except PredictDirectTransportError as exc:
            completed = core._now_ms()
            self._update_order(
                order_row_id,
                state="AMBIGUOUS",
                place_completed_at_ms=completed,
                place_rtt_ms=(time.monotonic() - p0) * 1000.0,
                error_kind="PREDICT_DIRECT_PLACE_AMBIGUOUS",
                error_message=str(exc)[:500],
            )
            return {"ok": False, "ambiguous": True, "completedAtMs": completed, "error": str(exc)[:500]}
        except Exception as exc:
            completed = core._now_ms()
            self._update_order(
                order_row_id,
                state="REJECTED",
                place_completed_at_ms=completed,
                place_rtt_ms=(time.monotonic() - p0) * 1000.0,
                error_kind="PREDICT_DIRECT_PLACE_REJECTED",
                error_message=str(exc)[:500],
            )
            return {"ok": False, "completedAtMs": completed, "error": str(exc)[:500]}

        completed = core._now_ms()
        data = placed.get("data") if isinstance(placed.get("data"), dict) else {}
        order_id = str(data.get("orderId") or "").strip()
        returned_hash = str(data.get("orderHash") or order_hash or "").strip()
        if not order_id or not returned_hash:
            self._update_order(
                order_row_id,
                state="AMBIGUOUS",
                place_completed_at_ms=completed,
                place_rtt_ms=(time.monotonic() - p0) * 1000.0,
                error_kind="PREDICT_DIRECT_PLACE_MISSING_ID",
                error_message=f"Predict response missing orderId/orderHash: {str(placed)[:400]}",
            )
            return {"ok": False, "ambiguous": True, "completedAtMs": completed, "error": "missing Predict order id/hash"}

        self._update_order(
            order_row_id,
            state="RESTING",
            place_completed_at_ms=completed,
            place_rtt_ms=(time.monotonic() - p0) * 1000.0,
            order_id=order_id,
            vendor_order_id=returned_hash,
            order_status="OPEN",
            maker_usdt_amount=float(amounts.maker_amount) / WEI,
            maker_share_qty=float(amounts.taker_amount) / WEI,
            raw_status_json=json.dumps(placed, separators=(",", ":"), allow_nan=False, default=str),
            error_kind=None,
            error_message=None,
        )
        return {
            "ok": True,
            "completedAtMs": completed,
            "orderId": order_id,
            "orderHash": returned_hash,
            "postOnly": True,
        }

    def _remote_maps(self, orders: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        client = self.direct_client
        if client is None:
            return {}, {}
        open_rows = client.list_orders(status="OPEN", first=100)
        by_id = {str(row.get("id") or ""): row for row in open_rows if row.get("id")}
        by_hash = {
            str((row.get("order") or {}).get("hash") or ""): row
            for row in open_rows
            if isinstance(row.get("order"), dict) and (row.get("order") or {}).get("hash")
        }
        for local in orders:
            oid = str(local.get("order_id") or "")
            order_hash = str(local.get("vendor_order_id") or "")
            if not oid and not order_hash:
                continue
            if oid in by_id or (order_hash and order_hash in by_hash):
                continue
            if order_hash:
                remote = client.order_by_hash(order_hash)
                if remote is not None:
                    if remote.get("id"):
                        by_id[str(remote.get("id"))] = remote
                    raw_order = remote.get("order") if isinstance(remote.get("order"), dict) else {}
                    remote_hash = str(raw_order.get("hash") or order_hash)
                    by_hash[remote_hash] = remote
        return by_id, by_hash

    def _apply_remote_update(
        self,
        local: dict[str, Any],
        remote: dict[str, Any],
        *,
        cancellation_context: bool,
    ) -> dict[str, Any]:
        update = normalize_predict_order_update(remote)
        update["vendor_order_id"] = str((remote.get("order") or {}).get("hash") or local.get("vendor_order_id") or "")
        update["direct_reward_earning_rate"] = core._finite(remote.get("rewardEarningRate"))
        if cancellation_context and update["state"] == "CANCELED":
            expiration = int(float(((remote.get("order") or {}).get("expiration") or 0)))
            expired_onchain = expiration > 0 and int(time.time()) >= expiration
            confirmed = bool(int(local.get("direct_cancel_onchain_confirmed") or 0))
            if not confirmed and not expired_onchain:
                update["state"] = "CANCEL_PENDING"
        self._update_order(int(local["id"]), **update)
        return update

    def _reconcile_pair(self, pair: dict[str, Any], market: dict[str, Any]) -> None:
        if time.monotonic() - self.last_order_poll < DIRECT_ORDER_POLL_SECONDS:
            return
        self.last_order_poll = time.monotonic()
        orders = self._pair_orders(int(pair["id"]))
        live = [
            row
            for row in orders
            if row.get("order_id")
            and str(row.get("state") or "") not in {"FILLED", "CANCELED", "REJECTED"}
        ]
        if not live or self.direct_client is None:
            return
        try:
            by_id, by_hash = self._remote_maps(live)
        except Exception as exc:
            self.last_error = f"Predict Direct order reconciliation: {str(exc)[:400]}"
            return

        for local in live:
            oid = str(local.get("order_id") or "")
            order_hash = str(local.get("vendor_order_id") or "")
            remote = by_id.get(oid) or by_hash.get(order_hash)
            if remote is None:
                continue
            previous = str(local.get("state") or "")
            update = self._apply_remote_update(local, remote, cancellation_context=previous == "CANCEL_PENDING")
            if str(update.get("state")) != previous:
                self._event(
                    "INFO",
                    f"PREDICT_DIRECT_ORDER_{update.get('state')}",
                    int(pair["market_id"]),
                    int(pair["id"]),
                    f"{local['side']} generation={local.get('generation')} {previous}->{update.get('state')}; filledShares={update.get('filled_share_qty')}",
                )

        refreshed = self._pair_orders(int(pair["id"]))
        latest = {}
        for row in refreshed:
            latest[str(row.get("side"))] = row
        states = {side: str((latest.get(side) or {}).get("state") or "") for side in ("UP", "DOWN")}
        if all(states.get(side) == "FILLED" for side in ("UP", "DOWN")):
            pair_state = "BOTH_FILLED"
        elif any(states.get(side) == "FILLED" for side in ("UP", "DOWN")):
            pair_state = "ONE_FILLED"
        elif any(states.get(side) == "PARTIAL_FILL" for side in ("UP", "DOWN")):
            pair_state = "PARTIAL_FILL"
        else:
            pair_state = "BOTH_RESTING"
        self._set_pair(int(pair["id"]), state=pair_state)

    def _cancel_pair(self, pair_id: int, reason: str) -> bool:
        client = self.direct_client
        builder = self.builder
        if client is None or builder is None:
            return False
        orders = self._pair_orders(int(pair_id))
        cancellable = [
            row
            for row in orders
            if row.get("order_id")
            and str(row.get("state") or "") not in {"FILLED", "CANCELED", "REJECTED"}
        ]
        if not cancellable:
            return True
        pair = self._current_pair() or {}
        market_id = int(pair.get("market_id") or 0) or None
        self._set_pair(int(pair_id), state="CANCEL_PENDING", close_reason=reason)
        self._event(
            "WARN",
            "PREDICT_DIRECT_CANCEL_REQUESTED_V8",
            market_id,
            int(pair_id),
            f"reason={reason}; ids={[row.get('order_id') for row in cancellable]}",
        )

        to_remove = [
            str(row["order_id"])
            for row in cancellable
            if not bool(int(row.get("direct_removed_from_book") or 0))
        ]
        if to_remove:
            try:
                client.remove_orders(to_remove)
                for row in cancellable:
                    if str(row.get("order_id")) in to_remove:
                        self._update_order(int(row["id"]), direct_removed_from_book=1, state="CANCEL_PENDING")
            except Exception as exc:
                self.last_error = f"Predict Direct fast remove failed: {str(exc)[:400]}"
                self._event("ERROR", "PREDICT_DIRECT_FAST_REMOVE_FAILED", market_id, int(pair_id), self.last_error)

        need_onchain = [
            row
            for row in self._pair_orders(int(pair_id))
            if row.get("order_id")
            and str(row.get("state") or "") not in {"FILLED", "CANCELED", "REJECTED"}
            and not bool(int(row.get("direct_cancel_onchain_confirmed") or 0))
        ]
        if not need_onchain:
            return True

        remote_rows: list[tuple[dict[str, Any], dict[str, Any], Order]] = []
        for local in need_onchain:
            order_hash = str(local.get("vendor_order_id") or "")
            if not order_hash:
                self.last_error = "Predict Direct cannot on-chain cancel an order without its orderHash"
                return False
            try:
                remote = client.order_by_hash(order_hash)
            except Exception as exc:
                self.last_error = f"Predict Direct cancel lookup: {str(exc)[:400]}"
                return False
            if remote is None:
                self.last_error = f"Predict Direct cancel lookup missing hash={order_hash}"
                return False
            normalized = normalize_predict_order_update(remote)
            if normalized["state"] == "FILLED":
                self._apply_remote_update(local, remote, cancellation_context=False)
                continue
            sdk_order = _sdk_order_from_remote(remote)
            if sdk_order is None:
                self.last_error = f"Predict Direct could not reconstruct SDK order hash={order_hash}"
                return False
            remote_rows.append((local, remote, sdk_order))

        grouped: dict[tuple[bool, bool], list[tuple[dict[str, Any], Order]]] = defaultdict(list)
        for local, remote, sdk_order in remote_rows:
            grouped[(bool(remote.get("isNegRisk")), bool(remote.get("isYieldBearing")))].append((local, sdk_order))

        all_ok = True
        for (is_neg_risk, is_yield_bearing), group in grouped.items():
            try:
                tx = builder.cancel_orders(
                    [sdk_order for _, sdk_order in group],
                    CancelOrdersOptions(
                        is_neg_risk=is_neg_risk,
                        is_yield_bearing=is_yield_bearing,
                    ),
                )
                if not bool(tx.success):
                    raise RuntimeError(f"SDK cancel transaction failed: {tx}")
                for local, _ in group:
                    self._update_order(
                        int(local["id"]),
                        state="CANCEL_PENDING",
                        order_status="ONCHAIN_CANCEL_CONFIRMED_AWAITING_API",
                        direct_cancel_onchain_confirmed=1,
                    )
            except Exception as exc:
                all_ok = False
                self.last_error = f"Predict Direct on-chain cancel failed: {str(exc)[:500]}"
                self._event("ERROR", "PREDICT_DIRECT_ONCHAIN_CANCEL_FAILED", market_id, int(pair_id), self.last_error)
        return all_ok

    def _final_reconcile_pair(self, pair: dict[str, Any]) -> bool:
        pair_id = int(pair["id"])
        pending_unconfirmed = [
            row
            for row in self._pair_orders(pair_id)
            if row.get("order_id")
            and str(row.get("state") or "") not in {"FILLED", "CANCELED", "REJECTED"}
            and not bool(int(row.get("direct_cancel_onchain_confirmed") or 0))
        ]
        if pending_unconfirmed:
            self._cancel_pair(pair_id, str(pair.get("close_reason") or "FINAL_RECONCILIATION"))

        if time.monotonic() - self.last_final_reconcile < DIRECT_ORDER_POLL_SECONDS:
            return False
        self.last_final_reconcile = time.monotonic()
        orders = self._pair_orders(pair_id)
        tracked = [row for row in orders if row.get("order_id")]
        if not tracked:
            return True
        try:
            by_id, by_hash = self._remote_maps(tracked)
        except Exception as exc:
            self.last_error = f"Predict Direct final reconciliation: {str(exc)[:400]}"
            return False

        terminal = True
        for local in tracked:
            oid = str(local.get("order_id") or "")
            order_hash = str(local.get("vendor_order_id") or "")
            remote = by_id.get(oid) or by_hash.get(order_hash)
            if remote is None:
                terminal = False
                continue
            update = self._apply_remote_update(local, remote, cancellation_context=True)
            if str(update.get("state") or "") not in {"FILLED", "CANCELED", "REJECTED"}:
                terminal = False

        refreshed = self._pair_orders(pair_id)
        if any(
            row.get("order_id") and str(row.get("state") or "") not in {"FILLED", "CANCELED", "REJECTED"}
            for row in refreshed
        ):
            terminal = False
        if terminal:
            fills = [
                {
                    "side": row.get("side"),
                    "generation": row.get("generation"),
                    "state": row.get("state"),
                    "filledUsdt": row.get("filled_usdt_amount"),
                    "filledShares": row.get("filled_share_qty"),
                    "onchainCancelConfirmed": bool(int(row.get("direct_cancel_onchain_confirmed") or 0)),
                }
                for row in refreshed
            ]
            self._set_pair(pair_id, state="CANCELED")
            self._event(
                "INFO",
                "PREDICT_DIRECT_CANCEL_TERMINAL_RECONCILED_V8",
                int(pair.get("market_id") or 0) or None,
                pair_id,
                f"final Predict states={fills}",
            )
        return terminal

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = self.VERSION
        payload["executionVenue"] = "PREDICT_DIRECT"
        payload["executionPath"] = "PREDICT_NATIVE_SIGNED_LIMIT_POST_ONLY"
        payload["predictDirect"] = {
            "apiBase": API_BASE,
            "accountMode": "PREDICT_ACCOUNT" if self.predict_account else "EOA",
            "predictAccount": self.predict_account,
            "signerAddress": self.signer_address,
            "jwtAvailable": bool(self.direct_client and self.direct_client.jwt),
            "approvalsOk": self.approvals_ok,
            "approvalDetails": list(self.approval_details),
            "nativePostOnly": True,
            "fastOffBookRemove": True,
            "onChainCancelRequired": True,
        }
        payload.setdefault("rules", {}).update(
            predictDirectV8=True,
            nativePredictPostOnly=True,
            predictRestRemoveAloneNeverTerminal=True,
            predictOnchainCancelViaOfficialSdk=True,
            signedLimitExpiresAtMarketEnd=True,
            predictDirectKeepsBinanceV8OneDollarFloorForABComparison=True,
        )
        return payload

    def stop(self) -> None:
        super().stop()
        # super().stop() calls close() on self.client. Keep this explicit only for
        # defensive cases where initialization failed before self.client was wired.
        client = self.direct_client
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def main() -> int:
    engine = PredictDirectV8WalletMakerCloneEngine()
    engine.start()
    handler = type("PredictDirectV8WalletMakerCloneHandler", (core._Handler,), {"engine": engine})
    server = core.ThreadingHTTPServer((core.HOST, core.PORT), handler)
    print(
        f"{engine.VERSION} {core.ASSET} listening on http://{core.HOST}:{core.PORT}/state; "
        f"venue=PREDICT_DIRECT; masterEnabled={core.MASTER_ENABLED}; db={core.DB_PATH}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        engine.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
