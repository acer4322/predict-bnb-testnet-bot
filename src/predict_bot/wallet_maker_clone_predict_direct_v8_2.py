from __future__ import annotations

from typing import Any, Callable

from . import wallet_maker_clone_live as core
from . import wallet_maker_clone_predict_direct_v8_1 as base


LIMIT_MARKET_ONLY_FIELDS = frozenset({
    "reservedBalancePolicy",
})


def sanitize_predict_limit_order_request(data: dict[str, Any]) -> dict[str, Any]:
    """Return a Predict LIMIT payload without MARKET-only request fields.

    Predict rejects ``reservedBalancePolicy`` on LIMIT orders with
    ``create_order_reserved_balance_policy_invalid``.  The V8.1 transport copied
    that field from the generic create-order schema even though it only applies
    to MARKET orders.  LIMIT orders also do not need zero-slippage metadata, so
    omit those no-op fields rather than depending on permissive server parsing.
    """

    payload = dict(data)
    if str(payload.get("strategy") or "").upper() != "LIMIT":
        return payload

    for key in LIMIT_MARKET_ONLY_FIELDS:
        payload.pop(key, None)

    # Predict's guide says slippage is applied only when slippageBps and
    # isMinAmountOut are submitted.  Native maker LIMIT orders in this engine
    # intentionally use exact signed amounts, so zero-slippage metadata is
    # unnecessary and is removed to keep the request LIMIT-specific.
    try:
        zero_slippage = float(payload.get("slippageBps") or 0) == 0.0
    except (TypeError, ValueError):
        zero_slippage = False
    if zero_slippage:
        payload.pop("slippageBps", None)
        if payload.get("isMinAmountOut") is False or payload.get("isMinAmountOut") is None:
            payload.pop("isMinAmountOut", None)

    return payload


class PredictDirectV82WalletMakerCloneEngine(base.PredictDirectV81WalletMakerCloneEngine):
    """Predict Direct V8.2: V8.1 discovery plus a strict LIMIT request payload.

    Strategy and safety behaviour are unchanged.  This version only fixes the
    native Predict REST submission envelope after production returned HTTP 400:
    reservedBalancePolicy is valid for MARKET orders only and must not be sent
    on this engine's post-only LIMIT orders.
    """

    VERSION = "WALLET_MAKER_CLONE_PREDICT_DIRECT_V8_2_LIMIT_PAYLOAD_FIXED"

    def _ensure_client(self) -> bool:
        if not super()._ensure_client():
            return False
        client = self.direct_client
        if client is None:
            return False

        if not bool(getattr(client, "_btc5m_limit_payload_v82", False)):
            original_create: Callable[[dict[str, Any]], dict[str, Any]] = client.create_order

            def create_limit_safe(data: dict[str, Any]) -> dict[str, Any]:
                return original_create(sanitize_predict_limit_order_request(data))

            # Install once before any paired placement threads are started.  Both
            # UP and DOWN submissions then share the same immutable sanitizer.
            client.create_order = create_limit_safe  # type: ignore[method-assign]
            setattr(client, "_btc5m_limit_payload_v82", True)
        return True

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = self.VERSION
        direct = payload.setdefault("predictDirect", {})
        direct.update(
            limitPayloadModel="POST_ONLY_LIMIT_NO_MARKET_ONLY_FIELDS",
            reservedBalancePolicyOnLimit=False,
            zeroSlippageMetadataOnLimit=False,
        )
        payload.setdefault("rules", {}).update(
            predictDirectLimitPayloadV82=True,
            reservedBalancePolicySentOnLimit=False,
        )
        return payload


def main() -> int:
    engine = PredictDirectV82WalletMakerCloneEngine()
    engine.start()
    handler = type("PredictDirectV82WalletMakerCloneHandler", (core._Handler,), {"engine": engine})
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
