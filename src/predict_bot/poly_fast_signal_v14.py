from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from . import multi_prediction_observer as observer_base
from . import poly_fast_live as fast_base
from . import poly_fast_signal_v13 as v13

ASSETS = v13.ASSETS
HOST = v13.HOST
PORT = v13.PORT
ROOT = v13.ROOT
DB_PATH = v13.DB_PATH


class ActiveCurrentBinanceObserver(v13.BinanceFreshnessDiagnosticObserver):
    """V13 telemetry plus strict current-market Binance discovery.

    The old rollover path could temporarily discover only the next 5m topic,
    cache that future market, and then stop rediscovering because its end time
    was far in the future.  That left the signal path stuck in WAITING_BOOK /
    bucket mismatch.  This wrapper only caches an actually active 5m market and
    keeps retrying every market-loop pass while no exact current market exists.

    It also preserves per-side orderbook request errors when only one side
    succeeds, instead of clearing the useful error at the end of a partial poll.
    No admission, direction, price, TP, lifecycle, gateway or Echtgeld behavior
    is changed.
    """

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
                start_ms = int(market.get("startMs") or 0)
                end_ms = int(market.get("endMs") or 0)
                if (
                    end_ms <= now_ms + 750
                    or start_ms <= 0
                    or start_ms > now_ms + 1_500
                ):
                    need.append(asset)
        if not need:
            return

        active_candidates: dict[str, list[dict[str, Any]]] = {asset: [] for asset in need}
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
                if (
                    start_ms <= now_ms < end_ms
                    and 0 < end_ms - start_ms <= 301_000
                ):
                    active_candidates[asset].append(topic)
            if all(active_candidates[asset] for asset in need) or not response.get("hasMore"):
                break
            offset += int(response.get("limit") or 100)

        for asset in need:
            rows = active_candidates[asset]
            if not rows:
                with self.lock:
                    b = self.assets[asset]["binance"]
                    b["status"] = "WAITING_MARKET"
                    b["error"] = (
                        f"no exact active {observer_base.ASSETS[asset]['symbol']} 5m CRYPTO_UP_DOWN market yet; retrying"
                    )
                continue

            summary = min(rows, key=lambda row: abs(int(row.get("startDate") or 0) - now_ms))
            try:
                selected = observer_base.select_binary_market(summary)
            except Exception as exc:
                with self.lock:
                    b = self.assets[asset]["binance"]
                    b["status"] = "WAITING_MARKET"
                    b["error"] = str(exc)[:300]
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
                if previous_id != cache["marketId"]:
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

    def _poll_binance_books_fast(self) -> None:
        if not self._ensure_binance_client():
            return
        client = self.binance_client
        assert client is not None
        jobs: list[tuple[str, str, int, str]] = []
        with self.lock:
            for asset in ASSETS:
                market = observer_base._record(self.assets[asset]["binance"].get("market"))
                market_id = int(market.get("marketId") or 0)
                for outcome, key in (("UP", "upTokenId"), ("DOWN", "downTokenId")):
                    token = str(market.get(key) or "")
                    if market_id > 0 and token:
                        jobs.append((asset, outcome, market_id, token))
        if not jobs:
            return

        results: dict[str, dict[str, Any]] = {asset: {} for asset in ASSETS}
        errors: dict[str, dict[str, str]] = {asset: {} for asset in ASSETS}
        with ThreadPoolExecutor(max_workers=min(6, len(jobs)), thread_name_prefix="poly-fast-book") as executor:
            futures = {}
            for asset, outcome, market_id, token in jobs:
                started = time.monotonic()
                futures[executor.submit(client.orderbook, market_id, token)] = (asset, outcome, started)
            for future in as_completed(futures):
                asset, outcome, started = futures[future]
                try:
                    book = future.result()
                except observer_base.ApiHttpError as exc:
                    if exc.status_code == 429:
                        retry = max(1.0, float(client.retry_after_seconds or 1.0))
                        self.binance_backoff_until = time.monotonic() + retry
                    errors[asset][outcome] = str(exc)[:300]
                    continue
                except Exception as exc:
                    errors[asset][outcome] = f"{type(exc).__name__}: {str(exc)[:260]}"
                    continue
                if not isinstance(book, dict):
                    errors[asset][outcome] = "orderbook response was not an object"
                    continue
                bid, _ = observer_base._best_level(book, "bid")
                ask, _ = observer_base._best_level(book, "ask")
                update_ms = observer_base._timestamp_ms(book.get("updateTimestampMs") or book.get("timestamp"))
                results[asset][outcome] = {
                    "bid": bid,
                    "ask": ask,
                    "rttMs": max(0.0, (time.monotonic() - started) * 1000.0),
                    "updateTimestampMs": update_ms,
                }

        now_ms = observer_base._now_ms()
        with self.lock:
            for asset in ASSETS:
                outcome_rows = results[asset]
                side_errors = errors[asset]
                if not outcome_rows and not side_errors:
                    continue
                b = self.assets[asset]["binance"]
                up = observer_base._record(outcome_rows.get("UP"))
                down = observer_base._record(outcome_rows.get("DOWN"))
                if up:
                    b["upBid"], b["upAsk"] = up.get("bid"), up.get("ask")
                    b["upSourceTimestampMs"] = up.get("updateTimestampMs")
                    b["upBookRttMs"] = up.get("rttMs")
                if down:
                    b["downBid"], b["downAsk"] = down.get("bid"), down.get("ask")
                    b["downSourceTimestampMs"] = down.get("updateTimestampMs")
                    b["downBookRttMs"] = down.get("rttMs")

                rtts = [observer_base._finite(row.get("rttMs")) for row in (up, down) if row]
                rtts = [value for value in rtts if value is not None]
                timestamps = [observer_base._finite(row.get("updateTimestampMs")) for row in (up, down) if row]
                timestamps = [value for value in timestamps if value is not None]
                if outcome_rows:
                    b["observedAtMs"] = now_ms
                    b["bookRttMs"] = max(rtts) if rtts else None
                    b["bookAgeMs"] = max(0.0, now_ms - min(timestamps)) if timestamps else None

                missing = [side for side in ("UP", "DOWN") if side not in outcome_rows]
                if b.get("upAsk") is not None and b.get("downAsk") is not None and not missing:
                    b["status"] = "LIVE"
                    b["error"] = None
                else:
                    b["status"] = "WAITING_BOOK"
                    detail = "; ".join(
                        f"{side}={side_errors.get(side, 'no usable book')}" for side in missing
                    )
                    b["error"] = f"partial Binance book poll; {detail}"[:400]


class PolyFastSignalRuntimeV14(v13.PolyFastSignalRuntimeV13):
    def __init__(self) -> None:
        self.observer = ActiveCurrentBinanceObserver(db_path=DB_PATH)
        self.engines = {
            asset: v13.v12.PostRejectDiagnosticEvaluator(
                self.observer,
                asset=asset,
                db_path=ROOT / "data" / f"poly_fast_signal_{asset.lower()}.db",
            )
            for asset in ASSETS
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_FAST_SIGNAL_V14_ACTIVE_BINANCE_MARKET_RETRY"
        payload["architecture"].update(
            binanceCurrentMarketOnly=True,
            binanceFutureMarketCacheForbidden=True,
            binancePartialBookErrorsPreserved=True,
        )
        return payload

    def diagnostics(self) -> dict[str, Any]:
        payload = super().diagnostics()
        payload["strategyVersion"] = self.snapshot().get("version")
        payload["behavior"]["note"] = (
            "V1.2 freshness telemetry plus strict active-current Binance market discovery; "
            "future 5m topics are never cached and partial-side book errors remain visible. "
            "Trading rules are unchanged."
        )
        return payload


class _Handler(v13._Handler):
    runtime: PolyFastSignalRuntimeV14


def main() -> int:
    runtime = PolyFastSignalRuntimeV14()
    runtime.start()
    handler = type("PolyFastSignalV14Handler", (_Handler,), {"runtime": runtime})
    server = fast_base.ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Poly Fast Signal V14 listening on http://{HOST}:{PORT}/state; "
        "Binance=current-active-only retry; partial book errors preserved; "
        "V13 freshness diagnostics + V12 trading rules unchanged",
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
