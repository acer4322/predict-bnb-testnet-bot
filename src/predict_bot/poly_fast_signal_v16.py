from __future__ import annotations

from typing import Any

from . import multi_prediction_observer as observer_base
from . import poly_fast_live as fast_base
from . import poly_fast_signal_v14 as v14
from . import poly_fast_signal_v15 as v15

ASSETS = v15.ASSETS
HOST = v15.HOST
PORT = v15.PORT
ROOT = v15.ROOT
DB_PATH = v15.DB_PATH

WAITING_BOOK_REVALIDATE_MS = 1500


class SelfHealingBinanceObserver(v14.ActiveCurrentBinanceObserver):
    """V14 strict-current Binance observer with read-only token revalidation.

    A current topic can occasionally remain WAITING_BOOK even while HTTP polling
    continues successfully, for example when one outcome returns no usable Ask
    or the list-markets token metadata is temporarily stale around rollover.

    This layer does not relax admission. Both outcome Asks are still required for
    LIVE exactly as before. If WAITING_BOOK persists, it only re-reads the current
    topic detail and refreshes the token ids for the same current market.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._waiting_book_since_ms: dict[str, int | None] = {asset: None for asset in ASSETS}
        self._last_detail_revalidate_ms: dict[str, int] = {asset: 0 for asset in ASSETS}
        self._detail_revalidate_count: dict[str, int] = {asset: 0 for asset in ASSETS}
        self._detail_revalidate_last_error: dict[str, str | None] = {asset: None for asset in ASSETS}
        self._detail_revalidate_last_result: dict[str, dict[str, Any] | None] = {asset: None for asset in ASSETS}

    @staticmethod
    def _detail_tokens(payload: Any, market_id: int) -> tuple[str, str] | None:
        if not isinstance(payload, dict):
            return None
        markets = payload.get("markets")
        if not isinstance(markets, list):
            return None
        selected: dict[str, Any] | None = None
        for raw in markets:
            if not isinstance(raw, dict):
                continue
            try:
                candidate_id = int(raw.get("marketId") or raw.get("id") or 0)
            except (TypeError, ValueError):
                candidate_id = 0
            if candidate_id == market_id:
                selected = raw
                break
        if selected is None:
            return None
        outcomes = selected.get("outcomes")
        if not isinstance(outcomes, list):
            return None
        up_token = ""
        down_token = ""
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                continue
            name = str(outcome.get("name") or outcome.get("outcome") or "").strip().upper()
            token = str(outcome.get("tokenId") or outcome.get("token_id") or "").strip()
            if name == "UP" and token:
                up_token = token
            elif name == "DOWN" and token:
                down_token = token
        return (up_token, down_token) if up_token and down_token else None

    def _poll_binance_books_fast(self) -> None:
        super()._poll_binance_books_fast()

        now_ms = observer_base._now_ms()
        candidates: list[tuple[str, int, int]] = []
        with self.lock:
            for asset in ASSETS:
                b = self.assets[asset]["binance"]
                status = str(b.get("status") or "").upper()
                up_ask = observer_base._finite(b.get("upAsk"))
                down_ask = observer_base._finite(b.get("downAsk"))
                missing_asks = [
                    side for side, ask in (("UP", up_ask), ("DOWN", down_ask)) if ask is None
                ]
                b["missingAskSides"] = missing_asks

                if status == "LIVE" and not missing_asks:
                    self._waiting_book_since_ms[asset] = None
                    b["waitingBookDurationMs"] = 0
                    continue
                if status != "WAITING_BOOK":
                    self._waiting_book_since_ms[asset] = None
                    b["waitingBookDurationMs"] = None
                    continue

                since = self._waiting_book_since_ms.get(asset)
                if since is None:
                    since = now_ms
                    self._waiting_book_since_ms[asset] = since
                duration = max(0, now_ms - since)
                b["waitingBookDurationMs"] = duration
                if missing_asks:
                    b["error"] = (
                        f"Binance WAITING_BOOK; missing usable Ask side(s): {','.join(missing_asks)}"
                    )

                if duration < WAITING_BOOK_REVALIDATE_MS:
                    continue
                if now_ms - self._last_detail_revalidate_ms.get(asset, 0) < WAITING_BOOK_REVALIDATE_MS:
                    continue
                market = observer_base._record(b.get("market"))
                topic_id = int(market.get("topicId") or 0)
                market_id = int(market.get("marketId") or 0)
                if topic_id > 0 and market_id > 0:
                    self._last_detail_revalidate_ms[asset] = now_ms
                    candidates.append((asset, topic_id, market_id))

        if not candidates or not self._ensure_binance_client():
            return
        client = self.binance_client
        assert client is not None

        for asset, topic_id, market_id in candidates:
            self._detail_revalidate_count[asset] += 1
            try:
                detail = client.market_detail(topic_id)
                tokens = self._detail_tokens(detail, market_id)
                if tokens is None:
                    raise ValueError("current market detail did not contain usable UP/DOWN token ids")
                up_token, down_token = tokens
                with self.lock:
                    b = self.assets[asset]["binance"]
                    market = observer_base._record(b.get("market"))
                    if (
                        int(market.get("topicId") or 0) != topic_id
                        or int(market.get("marketId") or 0) != market_id
                    ):
                        continue
                    old_up = str(market.get("upTokenId") or "")
                    old_down = str(market.get("downTokenId") or "")
                    changed = old_up != up_token or old_down != down_token
                    market["upTokenId"] = up_token
                    market["downTokenId"] = down_token
                    b["market"] = market
                    if changed:
                        b.update(
                            upBid=None,
                            upAsk=None,
                            downBid=None,
                            downAsk=None,
                            observedAtMs=None,
                            bookRttMs=None,
                            bookAgeMs=None,
                            upSourceTimestampMs=None,
                            downSourceTimestampMs=None,
                            upBookRttMs=None,
                            downBookRttMs=None,
                        )
                    self._detail_revalidate_last_result[asset] = {
                        "atMs": now_ms,
                        "topicId": topic_id,
                        "marketId": market_id,
                        "tokensChanged": changed,
                        "missingAskSides": list(b.get("missingAskSides") or []),
                    }
                    self._detail_revalidate_last_error[asset] = None
                    b["bookDetailRevalidation"] = dict(self._detail_revalidate_last_result[asset] or {})
            except Exception as exc:
                message = f"{type(exc).__name__}: {str(exc)[:280]}"
                self._detail_revalidate_last_error[asset] = message
                with self.lock:
                    b = self.assets[asset]["binance"]
                    b["bookDetailRevalidation"] = {
                        "atMs": now_ms,
                        "topicId": topic_id,
                        "marketId": market_id,
                        "error": message,
                    }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload.setdefault("fastPath", {}).update(
            waitingBookDetailRevalidation=True,
            waitingBookRevalidateMs=WAITING_BOOK_REVALIDATE_MS,
            waitingBookAdmissionRelaxed=False,
        )
        return payload


class PolyFastSignalRuntimeV16(v15.PolyFastSignalRuntimeV15):
    def __init__(self) -> None:
        self.observer = SelfHealingBinanceObserver(db_path=DB_PATH)
        self.engines = {
            asset: v15.EmbeddedPolyStateEvaluator(
                self.observer,
                asset=asset,
                db_path=ROOT / "data" / f"poly_fast_signal_{asset.lower()}.db",
            )
            for asset in ASSETS
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_FAST_SIGNAL_V16_BINANCE_BOOK_SELF_HEAL"
        payload["architecture"].update(
            binancePersistentWaitingBookRevalidation=True,
            binanceWaitingBookAdmissionRelaxed=False,
        )
        return payload

    def diagnostics(self) -> dict[str, Any]:
        payload = super().diagnostics()
        payload["strategyVersion"] = self.snapshot().get("version")
        payload["behavior"]["note"] = (
            "V1.2 diagnostics; embedded Poly state; strict current Binance market; persistent WAITING_BOOK "
            "revalidates current topic detail/token ids without relaxing the two-sided book admission rule."
        )
        return payload


class _Handler(v15._Handler):
    runtime: PolyFastSignalRuntimeV16


def main() -> int:
    runtime = PolyFastSignalRuntimeV16()
    runtime.start()
    handler = type("PolyFastSignalV16Handler", (_Handler,), {"runtime": runtime})
    server = fast_base.ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Poly Fast Signal V16 listening on http://{HOST}:{PORT}/state; "
        "Poly state=embedded; Binance persistent WAITING_BOOK=current-topic detail revalidation; "
        "two-sided book admission remains fail-closed; V12 trading rules unchanged",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.10)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
