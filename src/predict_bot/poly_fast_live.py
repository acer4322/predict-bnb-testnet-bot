from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# Multi-asset live modules resolve one bootstrap identity at import time. Each
# embedded engine captures its own asset/symbol immediately before __init__.
os.environ.setdefault("PREDICT_POLY_GAP_LIVE_ASSET", "ETH")
os.environ.setdefault("PREDICT_POLY_GAP_LIVE_SYMBOL", "ETHUSDT")
os.environ["PREDICT_POLY_GAP_LIVE_ENABLED"] = os.environ.get(
    "PREDICT_POLY_FAST_LIVE_ENABLED",
    os.environ.get("PREDICT_POLY_GAP_LIVE_ENABLED", "true"),
)

from . import multi_prediction_observer as observer_base
from . import poly_gap_multi_asset_live_v1 as multi_v1
from .multi_prediction_observer_v2 import LiveGradeMultiPredictionObserver
from .poly_gap_multi_asset_live_v3 import PinnedMultiAssetPolyGapLiveEngine

ROOT = Path(__file__).resolve().parents[2]
HOST = os.environ.get("PREDICT_POLY_FAST_LIVE_HOST", "127.0.0.1")
PORT = int(os.environ.get("PREDICT_POLY_FAST_LIVE_PORT", "8782"))
DB_PATH = Path(os.environ.get("PREDICT_POLY_FAST_OBSERVER_DB", ROOT / "data" / "poly_fast_observer.db"))
FAST_BINANCE_POLL_SECONDS = max(0.05, float(os.environ.get("PREDICT_POLY_FAST_BINANCE_POLL_SECONDS", "0.20")))
FAST_SAMPLE_SECONDS = max(0.05, float(os.environ.get("PREDICT_POLY_FAST_SAMPLE_SECONDS", "0.10")))
ASSETS = ("BTC", "ETH", "BNB")

# Fast Live intentionally reuses the hardened multi-asset V44/V3 execution
# lineage for BTC too. This keeps all three assets on the exact same isolated
# execution policy without requiring the legacy BTC 8766/8767 stack.
multi_v1.SUPPORTED_ASSETS.setdefault("BTC", "BTCUSDT")


class FastEmbeddedObserver(LiveGradeMultiPredictionObserver):
    """Single-process BTC/ETH/BNB Poly + Binance observer with RAM trajectory."""

    def start(self) -> None:
        threading.Thread(target=self._fast_market_loop, name="poly-fast-markets", daemon=True).start()
        threading.Thread(target=self._fast_binance_loop, name="poly-fast-binance", daemon=True).start()
        threading.Thread(target=self._memory_sample_loop, name="poly-fast-samples", daemon=True).start()

    def _refresh_btc_local(self) -> None:
        # Never touch 8766/8767. BTC is discovered and polled directly below.
        return

    def _load_trajectory_locked(self, asset: str, bucket: int) -> None:
        return

    def _fast_market_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                poly_changed = False
                for asset in ASSETS:
                    slug, bucket = observer_base.current_slug(asset)
                    with self.lock:
                        current = observer_base._record(self.assets[asset]["poly"].get("market"))
                    if int(current.get("bucketStartSec") or 0) == bucket:
                        continue
                    candidate, method = self._discover_poly_market(asset, slug, bucket)
                    if candidate is None:
                        with self.lock:
                            poly = self.assets[asset]["poly"]
                            poly["status"] = "WAITING_MARKET"
                            poly["error"] = f"no strict current {asset} 5m UP/DOWN event for {slug}"
                        continue
                    with self.lock:
                        state = self.assets[asset]
                        state["bucketStartSec"] = bucket
                        state["windowEndMs"] = (bucket + 300) * 1000
                        state["trajectory"].clear()
                        state["poly"] = {
                            "status": "CONNECTING",
                            "error": None,
                            "discoveryMethod": method,
                            "market": candidate,
                            "up": {"bestBid": None, "bestAsk": None, "quoteSourceTimestampMs": None, "quoteReceivedTimestampMs": None},
                            "down": {"bestBid": None, "bestAsk": None, "quoteSourceTimestampMs": None, "quoteReceivedTimestampMs": None},
                        }
                    poly_changed = True
                if poly_changed:
                    self._restart_poly_ws()
                    self._prime_poly_books()
                self._refresh_binance_markets_fast()
            except Exception as exc:
                self.last_error = f"fast market loop: {str(exc)[:400]}"
            self.stop_event.wait(0.50)

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

        candidates: dict[str, list[dict[str, Any]]] = {asset: [] for asset in need}
        offset = 0
        for _page in range(3):
            response = client.list_markets(offset=offset, limit=100)
            topics = response.get("marketTopics", []) if isinstance(response, dict) else []
            if not isinstance(topics, list):
                topics = []
            for topic in topics:
                if not isinstance(topic, dict):
                    continue
                symbol = str(topic.get("symbol") or "")
                asset = next((name for name in need if observer_base.ASSETS[name]["symbol"] == symbol), None)
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
                    self.assets[asset]["binance"]["error"] = f"no current {observer_base.ASSETS[asset]['symbol']} 5m CRYPTO_UP_DOWN market"
                continue
            active = [row for row in rows if int(row.get("startDate") or 0) <= now_ms < int(row.get("endDate") or 0)]
            summary = min(active or rows, key=lambda row: abs(int(row.get("startDate") or 0) - now_ms))
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
                    b.update(upBid=None, upAsk=None, downBid=None, downAsk=None, observedAtMs=None, bookRttMs=None, bookAgeMs=None)

    def _restart_poly_ws(self) -> None:
        if observer_base.websocket is None:
            with self.lock:
                for asset in ASSETS:
                    self.assets[asset]["poly"]["status"] = "ERROR"
                    self.assets[asset]["poly"]["error"] = "websocket-client is not installed"
            return
        token_map: dict[str, tuple[str, str]] = {}
        with self.lock:
            for asset in ASSETS:
                market = observer_base._record(self.assets[asset]["poly"].get("market"))
                up = str(market.get("upTokenId") or "")
                down = str(market.get("downTokenId") or "")
                if up:
                    token_map[up] = (asset, "UP")
                if down:
                    token_map[down] = (asset, "DOWN")
        tokens = tuple(sorted(token_map))
        if not tokens or tokens == self.last_poly_restart_tokens:
            return
        self.last_poly_restart_tokens = tokens
        self.poly_token_map = token_map
        self.poly_generation += 1
        generation = self.poly_generation
        try:
            if self.poly_ws is not None:
                self.poly_ws.close()
        except Exception:
            pass
        threading.Thread(target=self._poly_ws_loop, args=(generation,), name=f"poly-fast-ws-{generation}", daemon=True).start()

    def _poly_open(self, ws: Any, generation: int) -> None:
        if generation != self.poly_generation:
            ws.close()
            return
        tokens = list(self.last_poly_restart_tokens)
        ws.send(json.dumps({"assets_ids": tokens, "type": "market", "custom_feature_enabled": True}, separators=(",", ":")))
        with self.lock:
            for asset in ASSETS:
                poly = self.assets[asset]["poly"]
                poly["status"] = "LIVE"
                poly["error"] = None
                for side_key in ("up", "down"):
                    side = poly.get(side_key)
                    if isinstance(side, dict):
                        side["wsSession"] = int(generation)
        threading.Thread(target=self._poly_heartbeat, args=(ws, generation), daemon=True).start()

    def _fast_binance_loop(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                if time.monotonic() >= self.binance_backoff_until:
                    self._poll_binance_books_fast()
            except Exception as exc:
                self.last_error = f"fast Binance books: {str(exc)[:400]}"
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(0.01, FAST_BINANCE_POLL_SECONDS - elapsed))

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
                    with self.lock:
                        self.assets[asset]["binance"]["status"] = "ERROR"
                        self.assets[asset]["binance"]["error"] = str(exc)[:300]
                    continue
                except Exception as exc:
                    with self.lock:
                        self.assets[asset]["binance"]["status"] = "ERROR"
                        self.assets[asset]["binance"]["error"] = str(exc)[:300]
                    continue
                if not isinstance(book, dict):
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
            for asset, outcome_rows in results.items():
                if not outcome_rows:
                    continue
                b = self.assets[asset]["binance"]
                up = observer_base._record(outcome_rows.get("UP"))
                down = observer_base._record(outcome_rows.get("DOWN"))
                if up:
                    b["upBid"], b["upAsk"] = up.get("bid"), up.get("ask")
                if down:
                    b["downBid"], b["downAsk"] = down.get("bid"), down.get("ask")
                rtts = [observer_base._finite(row.get("rttMs")) for row in (up, down) if row]
                rtts = [value for value in rtts if value is not None]
                timestamps = [observer_base._finite(row.get("updateTimestampMs")) for row in (up, down) if row]
                timestamps = [value for value in timestamps if value is not None]
                b["observedAtMs"] = now_ms
                b["bookRttMs"] = max(rtts) if rtts else None
                b["bookAgeMs"] = max(0.0, now_ms - min(timestamps)) if timestamps else None
                b["status"] = "LIVE" if b.get("upAsk") is not None and b.get("downAsk") is not None else "WAITING_BOOK"
                b["error"] = None

    def _memory_sample_loop(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                self._sample_once()
            except Exception as exc:
                self.last_error = f"fast sample loop: {str(exc)[:400]}"
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(0.01, FAST_SAMPLE_SECONDS - elapsed))

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_FAST_EMBEDDED_OBSERVER_V2"
        payload["fastPath"] = {
            "enabled": True,
            "assets": list(ASSETS),
            "btcLocalDependencies": False,
            "httpHopToStrategy": False,
            "trajectoryPersistence": False,
            "binancePollMs": int(FAST_BINANCE_POLL_SECONDS * 1000),
            "sampleMs": int(FAST_SAMPLE_SECONDS * 1000),
        }
        return payload


class EmbeddedPinnedEngine(PinnedMultiAssetPolyGapLiveEngine):
    def __init__(self, observer: FastEmbeddedObserver, *, asset: str, db_path: Path) -> None:
        asset = str(asset).upper()
        if asset not in ASSETS:
            raise ValueError(f"unsupported fast-live asset: {asset}")
        self._embedded_observer = observer
        multi_v1.ASSET = asset
        multi_v1.SYMBOL = multi_v1.SUPPORTED_ASSETS[asset]
        super().__init__(db_path=db_path)

    def _asset_observer_state(self) -> dict[str, Any] | None:
        try:
            with self._embedded_observer.lock:
                row = self._embedded_observer._asset_snapshot(self.asset)
        except Exception as exc:
            self.last_error = f"{self.asset} embedded observer state: {str(exc)[:300]}"
            return None
        self._asset_observer_version = "MULTI_PREDICTION_OBSERVER_V2"
        self._last_asset_observer_state = row
        return row

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["polyFastLive"] = {
            "enabled": True,
            "port": PORT,
            "observerTransport": "IN_PROCESS_MEMORY",
            "localhostHttpSignalHop": False,
            "externalDependencies": ["Binance Prediction", "Polymarket"],
            "researchServicesRequired": False,
        }
        return payload


class PolyFastLiveRuntime:
    def __init__(self) -> None:
        self.observer = FastEmbeddedObserver(db_path=DB_PATH)
        self.engines: dict[str, EmbeddedPinnedEngine] = {}
        for asset in ASSETS:
            self.engines[asset] = EmbeddedPinnedEngine(
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
            "version": "POLY_FAST_LIVE_V2",
            "realMoney": True,
            "host": HOST,
            "port": PORT,
            "architecture": {
                "processes": 1,
                "assets": list(ASSETS),
                "observerTransport": "IN_PROCESS_MEMORY",
                "btcCoreRequired": False,
                "multiAssetSupervisorRequired": False,
                "predictFunRequired": False,
                "makerResearchRequired": False,
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


class _Handler(BaseHTTPRequestHandler):
    runtime: PolyFastLiveRuntime

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, allow_nan=False, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path not in {"/state", "/health", "/api/state"}:
            self._send(404, {"ok": False, "error": "not found"})
            return
        payload = self.runtime.snapshot()
        asset = str((parse_qs(parsed.query).get("asset") or [""])[0]).upper()
        if asset:
            row = payload["assets"].get(asset)
            if row is None:
                self._send(400, {"ok": False, "error": "asset must be BTC, ETH or BNB"})
                return
            self._send(200, {"ok": True, "state": row, "fastPath": payload["architecture"]})
            return
        self._send(200, {"ok": True, "state": payload})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path not in {"/settings", "/api/settings"}:
            self._send(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 64_000:
                raise ValueError("invalid request body length")
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("settings body must be a JSON object")
            query = parse_qs(parsed.query)
            asset = str(body.pop("asset", (query.get("asset") or [""])[0])).upper()
            self._send(200, {"ok": True, "state": self.runtime.update_settings(asset, body)})
        except ValueError as exc:
            self._send(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send(500, {"ok": False, "error": str(exc)[:500]})

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def main() -> int:
    runtime = PolyFastLiveRuntime()
    runtime.start()
    handler = type("PolyFastLiveHandler", (_Handler,), {"runtime": runtime})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Poly Fast Live V2 listening on http://{HOST}:{PORT}/state; assets={','.join(ASSETS)}; "
        f"in-process observer; Binance poll={FAST_BINANCE_POLL_SECONDS:.3f}s",
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
