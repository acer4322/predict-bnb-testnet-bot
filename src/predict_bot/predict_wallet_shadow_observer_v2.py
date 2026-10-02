from __future__ import annotations

from http.server import ThreadingHTTPServer
from typing import Any

from . import predict_wallet_shadow_observer as base

WEI = 10**18
VERSION = "PREDICT_WALLET_SHADOW_V0_2"


def dec_wei(value: Any) -> float | None:
    try:
        result = float(value) / WEI
    except (TypeError, ValueError, OverflowError):
        return None
    return result if base.math.isfinite(result) else None


def normalize_match_leg(
    raw: Any,
    *,
    wallet: str,
    role: str,
    maker_index: int | None = None,
) -> dict[str, Any] | None:
    """Decode Predict MatchData with the repository's canonical 1e18 scaling."""

    row = base._record(raw)
    market = base._record(row.get("market"))
    market_id = base._positive_int(market.get("id"))
    event_ms = base._iso_ms(row.get("executedAt"))
    if market_id is None or event_ms is None:
        return None

    if role == "TAKER":
        participant = base._record(row.get("taker"))
    else:
        makers = base._rows(row.get("makers"))
        if maker_index is None or maker_index < 0 or maker_index >= len(makers):
            return None
        participant = makers[maker_index]

    signer = str(participant.get("signer") or "").strip().lower()
    if signer != wallet.lower():
        return None

    order_hash = str(participant.get("hash") or "").strip() or None
    amount = dec_wei(participant.get("amount"))
    price = dec_wei(participant.get("price"))
    if amount is None:
        amount = dec_wei(row.get("amountFilled"))
    if price is None:
        price = dec_wei(row.get("priceExecuted"))
    if amount is None or amount < 0:
        amount = 0.0
    if price is not None and not 0 <= price <= 1:
        price = None

    side = base._side(participant.get("outcome"))
    quote_type = base._quote_type(participant.get("quoteType"))
    transaction_hash = str(row.get("transactionHash") or "").strip()
    settlement_id = str(row.get("settlementId") or "").strip()
    leg_id = ":".join(
        [
            role,
            order_hash or "NO_ORDER",
            transaction_hash or "NO_TX",
            settlement_id or "NO_SETTLEMENT",
            str(maker_index if maker_index is not None else "TAKER"),
            side,
            quote_type,
            f"{amount:.12f}",
            f"{price:.12f}" if price is not None else "NO_PRICE",
            str(event_ms),
        ]
    )
    parent_id = f"{role}:{order_hash or leg_id}"
    return {
        "legId": leg_id,
        "parentId": parent_id,
        "role": role,
        "marketId": market_id,
        "title": str(market.get("title") or market.get("question") or "") or None,
        "side": side,
        "quoteType": quote_type,
        "orderHash": order_hash,
        "eventMs": event_ms,
        "price": price,
        "shares": float(amount),
    }


class WalletShadowObserver(base.WalletShadowObserver):
    """Wire-format-hardened observer; all strategy behavior remains V0."""

    def _ingest_role(self, market_id: int, role: str) -> None:
        pages = base.INITIAL_BACKFILL_PAGES if role not in self.initialized_roles else 1
        after: str | None = None
        for _ in range(pages):
            payload = self._fetch_matches_page(market_id, role, after)
            data = base._rows(payload.get("data"))
            for raw in data:
                participant_count = 1 if role == "TAKER" else len(base._rows(raw.get("makers")))
                for index in range(participant_count):
                    leg = normalize_match_leg(
                        raw,
                        wallet=self.wallet,
                        role=role,
                        maker_index=None if role == "TAKER" else index,
                    )
                    if leg is None or int(leg["marketId"]) != market_id:
                        continue
                    leg_id = str(leg["legId"])
                    if leg_id in self.seen_legs:
                        continue
                    self.seen_legs.add(leg_id)
                    parent_id = str(leg["parentId"])
                    self.parents[parent_id] = base.aggregate_parent(self.parents.get(parent_id), leg)
                    self._persist_target_leg(leg, raw)
            cursor = str(payload.get("cursor") or "").strip() or None
            if not cursor or not data:
                break
            after = cursor
        self.initialized_roles.add(role)

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        payload["wireFormat"] = {
            "amount": "uint256 / 1e18",
            "price": "uint256 / 1e18",
            "source": "same convention as profile_predict_wallet_fair_value_execution_v2",
        }

        target = sorted(self.parents.values(), key=lambda item: item.first_event_ms, reverse=True)
        first_shadow_ms = min(
            (event.at_ms for event in self.shadow_events),
            default=None,
        )
        eligible = [
            item
            for item in target
            if first_shadow_ms is not None and item.first_event_ms >= first_shadow_ms
        ]
        payload["similarity"] = base.similarity(eligible, self.shadow_events)
        payload["similarityWindow"] = {
            "shadowStartedAtMs": first_shadow_ms,
            "eligibleTargetParents": len(eligible),
            "preShadowTargetParentsExcluded": len(target) - len(eligible),
            "rule": "target parent firstEventMs >= first causal shadow event",
        }
        return payload


class _Handler(base._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV2Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        f"target={observer.wallet}; apiKeyConfigured={bool(observer.api_key)}; db={base.DB_PATH}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        observer.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
