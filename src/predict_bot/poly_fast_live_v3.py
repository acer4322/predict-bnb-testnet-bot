from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from . import multi_prediction_observer as observer_base
from . import poly_fast_live as fast_v2


ASSETS = fast_v2.ASSETS
HOST = fast_v2.HOST
PORT = fast_v2.PORT
ROOT = fast_v2.ROOT
DB_PATH = fast_v2.DB_PATH
FAST_BINANCE_POLL_SECONDS = fast_v2.FAST_BINANCE_POLL_SECONDS


class SelfContainedFastObserver(fast_v2.FastEmbeddedObserver):
    """8792 observer with BTC/ETH/BNB owned entirely by this process."""

    def _refresh_binance_markets_fast(self) -> None:
        if not self._ensure_binance_client():
            return
        client = self.binance_client
        assert client is not None
        now_ms = observer_base._now_ms()
        need: list[str] = []
        with self.lock:
            for asset in ASSETS:
                market = observer_base._record(self.assets[asset]["binance"].get("market"))
                if int(market.get("endMs") or 0) <= now_ms + 750:
                    need.append(asset)
        if not need:
            return

        # Five pages matches the hardened live exact-discovery depth.  The old
        # fast observer stopped at three pages and could miss a valid current
        # BTC/ETH/BNB topic even though credentials were healthy.
        candidates: dict[str, list[dict[str, Any]]] = {asset: [] for asset in need}
        offset = 0
        for _page in range(5):
            response = client.list_markets(offset=offset, limit=100)
            topics = response.get("marketTopics", []) if isinstance(response, dict) else []
            if not isinstance(topics, list):
                topics = []
            for topic in topics:
                if not isinstance(topic, dict):
                    continue
                symbol = str(topic.get("symbol") or "")
                asset = next(
                    (name for name in need if observer_base.ASSETS[name]["symbol"] == symbol),
                    None,
                )
                if asset is None or topic.get("chartType") != "CRYPTO_UP_DOWN":
                    continue
                start_ms = int(topic.get("startDate") or 0)
                end_ms = int(topic.get("endDate") or 0)
                if end_ms > now_ms and 0 < end_ms - start_ms <= 301_000:
                    candidates[asset].append(topic)
            if all(candidates[asset] for asset in need) or not response.get("hasMore"):
                break
            offset += int(response.get("limit") or 100)

        for asset in need:
            rows = candidates[asset]
            if not rows:
                with self.lock:
                    self.assets[asset]["binance"]["status"] = "WAITING_MARKET"
                    self.assets[asset]["binance"]["error"] = (
                        f"no current {observer_base.ASSETS[asset]['symbol']} 5m CRYPTO_UP_DOWN market in first 500 rows"
                    )
                continue
            active = [
                row
                for row in rows
                if int(row.get("startDate") or 0) <= now_ms < int(row.get("endDate") or 0)
            ]
            summary = min(
                active or rows,
                key=lambda row: abs(int(row.get("startDate") or 0) - now_ms),
            )
            try:
                selected = observer_base.select_binary_market(summary)
            except Exception as exc:
                with self.lock:
                    self.assets[asset]["binance"]["status"] = "WAITING_MARKET"
                    self.assets[asset]["binance"]["error"] = str(exc)[:300]
                continue
            market = observer_base._record(selected.get("market"))
            up = observer_base._record(selected.get("up"))
            down = observer_base._record(selected.get("down"))
            cache = {
                "topicId": int(summary.get("marketTopicId") or 0),
                "marketId": int(market.get("marketId") or 0),
                "startMs": int(summary.get("startDate") or 0),
                "endMs": int(summary.get("endDate") or 0),
                "feeRateBps": int(summary.get("feeRateBps") or market.get("feeRateBps") or 200),
                "upTokenId": str(up.get("tokenId") or ""),
                "downTokenId": str(down.get("tokenId") or ""),
            }
            with self.lock:
                b = self.assets[asset]["binance"]
                previous_id = int(observer_base._record(b.get("market")).get("marketId") or 0)
                b["market"] = cache
                b["status"] = "WAITING_BOOK"
                b["error"] = None
                if previous_id and previous_id != cache["marketId"]:
                    b.update(
                        upBid=None,
                        upAsk=None,
                        downBid=None,
                        downAsk=None,
                        observedAtMs=None,
                        bookRttMs=None,
                        bookAgeMs=None,
                    )

    def _poly_error(self, error: Any, generation: int) -> None:
        if generation != self.poly_generation:
            return
        with self.lock:
            for asset in ASSETS:
                self.assets[asset]["poly"]["status"] = "RECONNECTING"
                self.assets[asset]["poly"]["error"] = str(error)[:300]

    def _poly_close(self, code: Any, message: Any, generation: int) -> None:
        if generation != self.poly_generation or self.stop_event.is_set():
            return
        self._poly_error(f"close {code}: {message}", generation)

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_FAST_EMBEDDED_OBSERVER_V3"
        payload.setdefault("fastPath", {}).update(
            binanceMarketSearchRows=500,
            polyWsErrorsCoverAllAssets=True,
            external8766Reads=False,
            external8770Reads=False,
        )
        return payload


class SelfContainedPinnedEngine(fast_v2.EmbeddedPinnedEngine):
    """Live V3/V44 executor using 8792 memory for signal + market identity."""

    def _prime_market(self, force: bool = False) -> dict[str, Any] | None:
        now_ms = observer_base._now_ms()
        expected_start_ms = (now_ms // 300_000) * 300_000
        try:
            with self._embedded_observer.lock:
                b = observer_base._record(
                    self._embedded_observer.assets[self.asset].get("binance")
                )
                market = dict(observer_base._record(b.get("market")))
        except Exception as exc:
            self.last_error = f"{self.asset} embedded Binance market: {str(exc)[:300]}"
            return None

        market_id = int(market.get("marketId") or 0)
        topic_id = int(market.get("topicId") or 0)
        start_ms = int(market.get("startMs") or 0)
        end_ms = int(market.get("endMs") or 0)
        up_token = str(market.get("upTokenId") or "")
        down_token = str(market.get("downTokenId") or "")
        if (
            market_id <= 0
            or topic_id <= 0
            or not up_token
            or not down_token
            or abs(start_ms - expected_start_ms) > 1_500
            or abs(end_ms - (expected_start_ms + 300_000)) > 1_500
        ):
            self.last_error = (
                f"{self.asset} embedded Binance exact current 5m market unavailable "
                f"for start={expected_start_ms}"
            )
            return None

        cache = {
            "asset": self.asset,
            "symbol": self.symbol,
            "market_id": market_id,
            "topic_id": topic_id,
            "up_token_id": up_token,
            "down_token_id": down_token,
            "fee_rate_bps": int(market.get("feeRateBps") or 200),
            "start_ms": start_ms,
            "end_ms": end_ms,
        }
        with self.lock:
            self.market_cache = dict(cache)
        if self.halted_market_id is not None and self.halted_market_id != market_id:
            self.halted_market_id = None
            self.halted_reason = None
        self.last_error = None
        return dict(cache)

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["polyFastLive"].update(
            version="V3_SELF_CONTAINED",
            signalSource="8792_IN_PROCESS_MEMORY",
            marketIdentitySource="8792_EMBEDDED_BINANCE_MARKET",
            external8766Required=False,
            external8770Required=False,
            orderExecution="DEDICATED_SIGNED_BINANCE_MARKET_FOK",
        )
        # Inherited V19/V3 telemetry describes the legacy standalone deployment.
        # Keep it visible for compatibility, but explicitly mark it bypassed here.
        shared = payload.get("binanceSharedMarketReference")
        if isinstance(shared, dict):
            shared["enabled"] = False
            shared["bypassedByPolyFastV3"] = True
            shared["url"] = None
        binding = payload.get("marketIdentityPreflight")
        if isinstance(binding, dict):
            binding["shared8766BeforeExecutionClient"] = False
            binding["polyFastEmbeddedIdentity"] = True
        isolation = payload.get("assetIsolationV1")
        if isinstance(isolation, dict):
            isolation["source"] = "8792_IN_PROCESS_MEMORY"
        pinned = payload.get("pinnedDivergence")
        if isinstance(pinned, dict):
            pinned["observerUrl"] = None
            pinned["observerTransport"] = "IN_PROCESS_MEMORY"
        return payload


class PolyFastLiveRuntimeV3:
    def __init__(self) -> None:
        self.observer = SelfContainedFastObserver(db_path=DB_PATH)
        self.engines: dict[str, SelfContainedPinnedEngine] = {}
        for asset in ASSETS:
            self.engines[asset] = SelfContainedPinnedEngine(
                self.observer,
                asset=asset,
                db_path=ROOT / "data" / f"poly_fast_live_{asset.lower()}.db",
            )

    def start(self) -> None:
        self.observer.start()
        for engine in self.engines.values():
            engine.start()

    def stop(self) -> None:
        for engine in self.engines.values():
            try:
                engine.stop()
            except Exception:
                pass
        self.observer.stop()

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": "POLY_FAST_LIVE_V3",
            "realMoney": True,
            "host": HOST,
            "port": PORT,
            "architecture": {
                "processes": 1,
                "assets": list(ASSETS),
                "observerTransport": "IN_PROCESS_MEMORY",
                "marketIdentityTransport": "IN_PROCESS_MEMORY",
                "btcCoreRequired": False,
                "multiAssetSupervisorRequired": False,
                "predictFunRequired": False,
                "makerResearchRequired": False,
                "port8766Required": False,
                "port8770Required": False,
                "port8781Required": False,
                "executionLivesInside8792": True,
            },
            "observer": self.observer.snapshot(),
            "assets": {asset: engine.snapshot() for asset, engine in self.engines.items()},
        }

    def update_settings(self, asset: str, values: dict[str, Any]) -> dict[str, Any]:
        key = str(asset).upper()
        engine = self.engines.get(key)
        if engine is None:
            raise ValueError("asset must be BTC, ETH or BNB")
        return engine.update_settings(values)


class _Handler(fast_v2._Handler):
    runtime: PolyFastLiveRuntimeV3


def main() -> int:
    runtime = PolyFastLiveRuntimeV3()
    runtime.start()
    handler = type("PolyFastLiveV3Handler", (_Handler,), {"runtime": runtime})
    server = fast_v2.ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Poly Fast Live V3 listening on http://{HOST}:{PORT}/state; "
        f"assets={','.join(ASSETS)}; self-contained signal+market identity; "
        f"Binance poll={FAST_BINANCE_POLL_SECONDS:.3f}s",
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
