from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from . import multi_prediction_observer as observer_base
from . import poly_fast_live as fast_base
from . import poly_fast_signal_v12 as v12

ASSETS = v12.ASSETS
HOST = v12.HOST
PORT = v12.PORT
ROOT = v12.ROOT
DB_PATH = v12.DB_PATH

_ACTIVE_PHASES = {"OPEN", "ENTRY_AMBIGUOUS", "EXIT_AMBIGUOUS", "EXITING", "ACTIVE"}


class BinanceFreshnessDiagnosticObserver(v12.v11.v10.v9.v8.v4.v3.SelfContainedFastObserver):
    """V12 observer plus source/receipt freshness telemetry only.

    Trading inputs, polling cadence, request fan-out and book values are unchanged.
    The additional timestamps let diagnostics distinguish transport latency from a
    Binance outcome book whose exchange-side update timestamp simply has not moved.
    """

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
                b["observedAtMs"] = now_ms
                b["bookRttMs"] = max(rtts) if rtts else None
                # Compatibility only: this remains the worst exchange-side source age.
                b["bookAgeMs"] = max(0.0, now_ms - min(timestamps)) if timestamps else None
                b["status"] = "LIVE" if b.get("upAsk") is not None and b.get("downAsk") is not None else "WAITING_BOOK"
                b["error"] = None


class PolyFastSignalRuntimeV13(v12.PolyFastSignalRuntimeV12):
    def __init__(self) -> None:
        self.observer = BinanceFreshnessDiagnosticObserver(db_path=DB_PATH)
        self.engines = {
            asset: v12.PostRejectDiagnosticEvaluator(
                self.observer,
                asset=asset,
                db_path=ROOT / "data" / f"poly_fast_signal_{asset.lower()}.db",
            )
            for asset in ASSETS
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_FAST_SIGNAL_V13_BINANCE_FRESHNESS_DIAGNOSTIC"
        payload["architecture"].update(
            binanceFreshnessDiagnostic=True,
            binanceFreshnessDiagnosticObservationOnly=True,
        )
        return payload

    @staticmethod
    def _age(now_ms: int, timestamp: Any) -> float | None:
        value = v12._number(timestamp)
        if value is None:
            return None
        return max(0.0, float(now_ms) - value)

    def diagnostics(self) -> dict[str, Any]:
        payload = super().diagnostics()
        snapshot = self.snapshot()
        observer = v12._record(snapshot.get("observer"))
        observer_assets = v12._record(observer.get("assets"))
        now_ms = int(payload.get("asOfMs") or observer_base._now_ms())
        rows = v12._record(payload.get("assets"))

        for asset in ASSETS:
            row = v12._record(rows.get(asset))
            market = v12._record(row.get("market"))
            evaluator = v12._record(row.get("evaluator"))
            observed = v12._record(observer_assets.get(asset))
            binance = v12._record(observed.get("binance"))

            receipt_age = self._age(now_ms, binance.get("observedAtMs"))
            source_age = v12._number(binance.get("bookAgeMs"))
            rtt = v12._number(binance.get("bookRttMs"))
            up_source_age = self._age(now_ms, binance.get("upSourceTimestampMs"))
            down_source_age = self._age(now_ms, binance.get("downSourceTimestampMs"))
            up_rtt = v12._number(binance.get("upBookRttMs"))
            down_rtt = v12._number(binance.get("downBookRttMs"))
            selected_side = str(evaluator.get("direction") or "").upper()
            selected_source_age = (
                up_source_age if selected_side == "UP" else down_source_age if selected_side == "DOWN" else None
            )

            market["binanceBookAgeMs"] = source_age
            market["binanceReceiptAgeMs"] = receipt_age
            market["binanceSourceAgeMs"] = source_age
            market["binanceUpSourceAgeMs"] = up_source_age
            market["binanceDownSourceAgeMs"] = down_source_age
            market["binanceBookRttMs"] = rtt
            market["binanceUpBookRttMs"] = up_rtt
            market["binanceDownBookRttMs"] = down_rtt
            market["binanceSelectedSide"] = selected_side or None
            market["binanceSelectedSideSourceAgeMs"] = selected_source_age
            market["binanceBookAgeMeaning"] = "WORST_SIDE_EXCHANGE_SOURCE_AGE_NOT_TRANSPORT_AGE"

            # V12 treated worst-side exchange source age as transport staleness.
            # If both feeds are LIVE and actual receipt freshness is good, downgrade
            # that false positive without changing any trading/admission behavior.
            if (
                str(row.get("status") or "") == "STALE_MARKET_DATA"
                and str(market.get("polyStatus") or "").upper() == "LIVE"
                and str(market.get("binanceStatus") or "").upper() == "LIVE"
                and (v12._number(market.get("polyReceiptAgeMs")) or 0.0) <= 2_500
                and receipt_age is not None
                and receipt_age <= 2_500
                and source_age is not None
                and source_age > 2_500
            ):
                lifecycle = v12._record(row.get("lifecycle"))
                phase = str(lifecycle.get("phase") or "").upper()
                if asset == "BNB":
                    row["status"] = "ENTRY_DISABLED"
                elif market.get("bucketAligned") is False:
                    row["status"] = "MARKET_MISMATCH"
                elif phase in _ACTIVE_PHASES:
                    row["status"] = "ACTIVE_POSITION"
                else:
                    row["status"] = "WAITING_SIGNAL"
                reasons = [
                    str(reason)
                    for reason in (row.get("reasons") or [])
                    if not str(reason).startswith("book age polyReceipt=")
                ]
                reasons.append(
                    f"Binance source book unchanged worstSide={source_age}ms while receiptAge={receipt_age}ms rtt={rtt}ms; not transport stale"
                )
                row["reasons"] = reasons

        severity = {
            "HEALTHY": 0,
            "WAITING_SIGNAL": 0,
            "ENTRY_DISABLED": 0,
            "ACTIVE_POSITION": 0,
            "DEGRADED": 1,
            "STALE_MARKET_DATA": 2,
            "MARKET_MISMATCH": 3,
            "STALE_STRATEGY_LOOP": 3,
            "SIGNAL_PIPELINE_BROKEN": 4,
            "EXECUTION_PIPELINE_BROKEN": 4,
        }
        overall = "HEALTHY"
        warnings: list[dict[str, Any]] = []
        for asset in ASSETS:
            row = v12._record(rows.get(asset))
            status = str(row.get("status") or "UNKNOWN")
            if severity.get(status, 1) > severity.get(overall, 0):
                overall = status
            if severity.get(status, 0) > 0:
                warnings.append({"asset": asset, "status": status, "reasons": row.get("reasons") or []})
        active_assets = [asset for asset in ASSETS if asset != "BNB"]
        normal = {"HEALTHY", "WAITING_SIGNAL", "ACTIVE_POSITION", "ENTRY_DISABLED"}
        if all(str(rows.get(asset, {}).get("status")) in normal for asset in active_assets):
            overall = "HEALTHY"

        payload["version"] = "STRATEGY_HEALTH_DIAGNOSTICS_V1_2"
        payload["strategyVersion"] = snapshot.get("version")
        payload["status"] = overall
        payload["ok"] = overall in normal
        payload["warnings"] = warnings
        payload["behavior"] = {
            "status": "COUNTERS_ONLY",
            "note": "V1.2 separates Binance transport receipt freshness, HTTP RTT and exchange-side source age; no historical baseline or trading rule changes.",
        }
        return payload


class _Handler(fast_base._Handler):
    runtime: PolyFastSignalRuntimeV13

    def do_GET(self) -> None:  # noqa: N802
        parsed = fast_base.urlparse(self.path)
        if parsed.path in {"/diagnostics", "/api/diagnostics"}:
            self._send(200, {"ok": True, "diagnostics": self.runtime.diagnostics()})
            return
        super().do_GET()


def main() -> int:
    runtime = PolyFastSignalRuntimeV13()
    runtime.start()
    handler = type("PolyFastSignalV13Handler", (_Handler,), {"runtime": runtime})
    server = fast_base.ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Poly Fast Signal V13 listening on http://{HOST}:{PORT}/state; "
        "Binance freshness diagnostics=receipt/source/per-side/RTT observation-only; "
        "V12 trading rules unchanged",
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
