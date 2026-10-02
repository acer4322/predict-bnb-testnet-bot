from __future__ import annotations

from typing import Any

from . import multi_prediction_observer as observer_base
from . import poly_fast_live as fast_base
from . import poly_fast_signal_v4 as v4
from . import poly_fast_signal_v6 as v6
from . import poly_fast_signal_v7 as v7

ASSETS = v7.ASSETS
HOST = v7.HOST
PORT = v7.PORT
ROOT = v7.ROOT
DB_PATH = v7.DB_PATH


class BucketAlignedGapSignalEvaluator(v7.GapDirectionLifecycleSignalEvaluator):
    """V7 GAP lifecycle with a final strict current-bucket alignment gate.

    Entry signals are allowed only when the current wall-clock 5m bucket, the
    discovered Polymarket bucket, and the embedded Binance market start/end all
    refer to the same five-minute window. This is deliberately redundant with
    upstream discovery/reset logic so a transient stale book at rollover cannot
    become an Echtgeld intent.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.bucket_alignment_checks = 0
        self.bucket_alignment_blocks = 0
        self.last_bucket_alignment: dict[str, Any] | None = None

    def _bucket_alignment(self) -> dict[str, Any]:
        self.bucket_alignment_checks += 1
        now_ms = observer_base._now_ms()
        current_bucket_sec = (now_ms // 300_000) * 300
        expected_start_ms = current_bucket_sec * 1000
        expected_end_ms = expected_start_ms + 300_000

        with self._embedded_observer.lock:
            state = self._embedded_observer._asset_snapshot(self.asset)
        poly = observer_base._record(state.get("poly"))
        poly_market = observer_base._record(poly.get("market"))
        binance = observer_base._record(state.get("binance"))
        binance_market = observer_base._record(binance.get("market"))

        state_bucket_sec = int(state.get("bucketStartSec") or 0)
        poly_bucket_sec = int(poly_market.get("bucketStartSec") or 0)
        binance_start_ms = int(binance_market.get("startMs") or 0)
        binance_end_ms = int(binance_market.get("endMs") or 0)
        poly_status = str(poly.get("status") or "").upper()
        binance_status = str(binance.get("status") or "").upper()

        aligned = (
            state_bucket_sec == current_bucket_sec
            and poly_bucket_sec == current_bucket_sec
            and binance_start_ms == expected_start_ms
            and binance_end_ms == expected_end_ms
            and poly_status == "LIVE"
            and binance_status == "LIVE"
        )
        result = {
            "aligned": bool(aligned),
            "currentBucketSec": int(current_bucket_sec),
            "stateBucketSec": state_bucket_sec or None,
            "polyBucketSec": poly_bucket_sec or None,
            "binanceStartMs": binance_start_ms or None,
            "binanceEndMs": binance_end_ms or None,
            "expectedBinanceStartMs": int(expected_start_ms),
            "expectedBinanceEndMs": int(expected_end_ms),
            "polyStatus": poly_status or None,
            "binanceStatus": binance_status or None,
            "policy": "CURRENT_BUCKET_EQ_POLY_BUCKET_EQ_BINANCE_WINDOW_AND_BOTH_LIVE",
        }
        if not aligned:
            self.bucket_alignment_blocks += 1
        self.last_bucket_alignment = dict(result)
        return result

    def _evaluate_and_forward(self) -> None:
        if self.entry_mode == v6.MODE_PINNED:
            # The stricter rollover guard applies to every mode before the
            # inherited detector can emit an intent.
            alignment = self._bucket_alignment()
            if not alignment.get("aligned"):
                self._last_pinned_evaluation = {
                    "allowed": False,
                    "state": "WAITING_BUCKET_ALIGNMENT",
                    "reason": "Poly/Binance/current 5m buckets are not strictly aligned",
                    "bucketAlignment": alignment,
                }
                return
            return super()._evaluate_and_forward()

        alignment = self._bucket_alignment()
        if not alignment.get("aligned"):
            self.last_gap_evaluation = {
                "allowed": False,
                "state": "WAITING_BUCKET_ALIGNMENT",
                "strategy": v6.GAP_STRATEGY,
                "reason": "Poly/Binance/current 5m buckets are not strictly aligned",
                "bucketAlignment": alignment,
            }
            return

        market = self._prime_market(force=False)
        if not isinstance(market, dict):
            return
        poly_state = self._gap_poly_state()
        active = self._active_round()
        if isinstance(active, dict):
            self._maybe_exit(active, poly_state)
            return

        evaluation = self._gap_evaluation(market, poly_state)
        evaluation["polyUpMid"] = poly_state.get("polyUpMid")
        evaluation["polyDownMid"] = poly_state.get("polyDownMid")
        evaluation["polyUpThreshold"] = float(v7.POLY_UP_THRESHOLD)
        evaluation["polyDownThreshold"] = float(v7.POLY_DOWN_THRESHOLD)
        evaluation["directionSource"] = "8792_EMBEDDED_POLY_BOOK"
        evaluation["bucketAlignment"] = alignment
        self.last_gap_evaluation = dict(evaluation)
        if evaluation.get("allowed") is True:
            self._forward_gap_entry(market, evaluation)

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["bucketAlignmentPreflight"] = {
            "enabled": True,
            "strict": True,
            "checks": int(self.bucket_alignment_checks),
            "blocks": int(self.bucket_alignment_blocks),
            "last": self.last_bucket_alignment,
            "blockState": "WAITING_BUCKET_ALIGNMENT",
        }
        gap = payload.get("gapEntry")
        if isinstance(gap, dict):
            gap["requiresStrictBucketAlignment"] = True
        return payload


class PolyFastSignalRuntimeV8(v7.PolyFastSignalRuntimeV7):
    def __init__(self) -> None:
        self.observer = v4.v3.SelfContainedFastObserver(db_path=DB_PATH)
        self.engines = {
            asset: BucketAlignedGapSignalEvaluator(
                self.observer,
                asset=asset,
                db_path=ROOT / "data" / f"poly_fast_signal_{asset.lower()}.db",
            )
            for asset in ASSETS
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_FAST_SIGNAL_V8_BUCKET_ALIGNMENT"
        payload["architecture"].update(
            strictBucketAlignment=True,
            bucketAlignmentPolicy="CURRENT==POLY==BINANCE_START_END",
            rolloverStaleBookFailClosed=True,
        )
        return payload


class _Handler(fast_base._Handler):
    runtime: PolyFastSignalRuntimeV8


def main() -> int:
    runtime = PolyFastSignalRuntimeV8()
    runtime.start()
    handler = type("PolyFastSignalV8Handler", (_Handler,), {"runtime": runtime})
    server = fast_base.ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Poly Fast Signal V8 listening on http://{HOST}:{PORT}/state; "
        f"entry={v6._entry_mode()}; strict-bucket-alignment=true; Echtgeld=8781-only",
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
