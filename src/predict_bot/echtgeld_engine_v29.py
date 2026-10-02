from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer
from typing import Any

from . import binance_time_sync_hardening as _binance_time_sync_hardening
from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v26 as v26
from . import echtgeld_engine_v28 as v28
from .core import BINANCE_API, BinancePredictionClient, BinancePredictionTradingClient


VERSION = "ECHTGELD_ENGINE_V2_4310_NEXT_COMPLETE_MARKET_ONCE_V1_R2_R21_SEMANTIC_V2_BINANCE_TIME_SYNC_V1"
HOST = v28.HOST
PORT = v28.PORT


class EchtgeldEngine(v28.EchtgeldEngine):
    """V28 plus an engine-owned one-complete-market live session.

    The ordinary RESUME behavior is unchanged: it waits for the next complete
    market and then continues across later markets.  The opt-in one-market
    control uses the same V26 activation anchor, admits only the first market
    after that anchor, and pauses/cancels at the following market rollover.
    Admission is enforced here as well as by controller heartbeats so a stale
    browser or a controller timing race cannot open the second market.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.single_market_lock = threading.RLock()
        self.single_market_requested = False
        self.single_market_status = "IDLE"
        self.single_market_target_id: int | None = None
        self.single_market_target_bucket_start_sec: int | None = None
        self.single_market_target_window_end_ms: int | None = None
        self.single_market_last_completed_id: int | None = None
        self.single_market_last_stop_reason: str | None = None
        self.single_market_completion_thread: threading.Thread | None = None
        super().__init__(*args, **kwargs)

    def _reset_single_market_run(self, *, status: str = "IDLE", reason: str | None = None) -> None:
        with self.single_market_lock:
            self.single_market_requested = False
            self.single_market_status = status
            self.single_market_target_id = None
            self.single_market_target_bucket_start_sec = None
            self.single_market_target_window_end_ms = None
            self.single_market_last_stop_reason = reason

    def resume(self) -> dict[str, Any]:
        # The existing button keeps its original continuous-after-next-market
        # contract.  A prior completed/cancelled one-market request never leaks
        # into an ordinary RESUME.
        self._reset_single_market_run()
        state = super().resume()
        state["singleMarketRun"] = self.single_market_run_state()
        return state

    def resume_next_market_once(self) -> dict[str, Any]:
        with self.single_market_lock:
            self.single_market_requested = True
            self.single_market_status = "WAITING_NEXT_MARKET"
            self.single_market_target_id = None
            self.single_market_target_bucket_start_sec = None
            self.single_market_target_window_end_ms = None
            self.single_market_last_stop_reason = None
        try:
            # Deliberately bypass this class's continuous RESUME wrapper while
            # retaining V28 settlement preflight and the V26 next-market gate.
            state = super().resume()
        except Exception:
            self._reset_single_market_run(status="ARM_FAILED", reason="RESUME_PREFLIGHT_FAILED")
            raise
        self._cap100_event(
            "NEXT_MARKET_ONCE_ARMED",
            detail="one-market session armed; current market blocked and only the next complete market may enter",
            payload={"activationMarketId": self.activation_anchor_market_id},
        )
        state["singleMarketRun"] = self.single_market_run_state()
        return state

    def pause(self, reason: str = "operator") -> dict[str, Any]:
        with self.single_market_lock:
            was_requested = self.single_market_requested
            target = self.single_market_target_id
        state = super().pause(reason)
        if was_requested:
            with self.single_market_lock:
                self.single_market_requested = False
                self.single_market_status = "CANCELED"
                self.single_market_last_stop_reason = str(reason)
                self.single_market_target_id = target
        state["singleMarketRun"] = self.single_market_run_state()
        return state

    def _activate_single_market(
        self,
        market_id: int,
        bucket_start_sec: int | None,
        window_end_ms: int | None,
        *,
        source: str,
    ) -> None:
        with self.single_market_lock:
            if not self.single_market_requested or self.single_market_target_id is not None:
                return
            self.single_market_target_id = int(market_id)
            self.single_market_target_bucket_start_sec = int(bucket_start_sec) if bucket_start_sec else None
            self.single_market_target_window_end_ms = int(window_end_ms) if window_end_ms else None
            self.single_market_status = "RUNNING"
        # Heartbeat activation can happen before the first order.  Open the
        # existing V26 gate for exactly this market so a no-order market is still
        # represented as the one requested session.
        with self.next_market_lock:
            if self.waiting_next_market:
                self.waiting_next_market = False
                self.live_market_id = int(market_id)
                self.live_bucket_start_sec = int(bucket_start_sec) if bucket_start_sec else None
        self._cap100_event(
            "NEXT_MARKET_ONCE_ACTIVATED",
            detail=f"one-market session activated market={market_id} from {source}",
            payload={
                "activationMarketId": self.activation_anchor_market_id,
                "liveMarketId": int(market_id),
                "liveBucketStartSec": int(bucket_start_sec) if bucket_start_sec else None,
                "windowEndMs": int(window_end_ms) if window_end_ms else None,
            },
        )

    def _begin_single_market_completion(self, observed_market_id: int, *, reason: str) -> int | None:
        with self.single_market_lock:
            if not self.single_market_requested:
                return None
            completed = self.single_market_target_id
            self.single_market_requested = False
            self.single_market_status = "COMPLETING"
            self.single_market_last_completed_id = completed
            self.single_market_last_stop_reason = reason
            return completed

    def _finish_single_market_completion(
        self,
        completed: int | None,
        observed_market_id: int,
        *,
        reason: str,
    ) -> dict[str, Any]:
        # Pause is engine-owned and invokes the existing Maker cancel-all path.
        # Call the parent directly so the operator-cancel status wrapper above
        # does not relabel an automatic completion.
        state = super().pause(reason)
        with self.single_market_lock:
            self.single_market_status = "COMPLETED"
        self._cap100_event(
            "NEXT_MARKET_ONCE_COMPLETED",
            detail=f"one-market session completed market={completed}; observed rollover={observed_market_id}; engine paused",
            payload={
                "completedMarketId": completed,
                "observedMarketId": int(observed_market_id),
                "reason": reason,
            },
        )
        state["singleMarketRun"] = self.single_market_run_state()
        return state

    def _complete_single_market(self, observed_market_id: int, *, reason: str) -> dict[str, Any]:
        completed = self._begin_single_market_completion(observed_market_id, reason=reason)
        if completed is None:
            return self.state()
        return self._finish_single_market_completion(
            completed,
            observed_market_id,
            reason=reason,
        )

    def _complete_single_market_after_entry_rejection(
        self,
        observed_market_id: int,
        *,
        reason: str,
    ) -> None:
        """Fence the second market immediately, then pause/cancel off-request.

        Pause may wait on reconciliation or the serialized venue lock. The order
        HTTP request must still receive a deterministic rejection before the
        controller timeout; otherwise an expected policy block becomes a false
        UNKNOWN_SUBMISSION/orphan in 8789.
        """
        completed = self._begin_single_market_completion(observed_market_id, reason=reason)
        if completed is None:
            return

        def finish() -> None:
            try:
                self._finish_single_market_completion(
                    completed,
                    observed_market_id,
                    reason=reason,
                )
            except Exception as exc:
                with self.single_market_lock:
                    self.single_market_status = "COMPLETION_FAILED"
                    self.single_market_last_stop_reason = f"{reason}:{type(exc).__name__}"

        thread = threading.Thread(
            target=finish,
            name="echtgeld-single-market-completion",
            daemon=True,
        )
        with self.single_market_lock:
            self.single_market_completion_thread = thread
        thread.start()

    def cap100_heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = super().cap100_heartbeat(payload)
        market_id = int(v1._finite(payload.get("marketId")) or 0)
        bucket = int(v1._finite(payload.get("bucketStartSec")) or 0) or None
        window_end = int(v1._finite(payload.get("windowEndMs")) or 0) or None
        with self.single_market_lock:
            requested = self.single_market_requested
            target = self.single_market_target_id
        if requested and self.armed and market_id > 0:
            if target is None:
                with self.next_market_lock:
                    anchor = self.activation_anchor_market_id
                if market_id != anchor:
                    self._activate_single_market(market_id, bucket, window_end, source="heartbeat")
            elif market_id != target:
                self._complete_single_market(
                    market_id,
                    reason=f"NEXT_MARKET_ONCE_COMPLETE:{target}->{market_id}",
                )
        result["armed"] = bool(self.armed)
        result["nextMarketArmGate"] = self.next_market_arm_state()
        result["singleMarketRun"] = self.single_market_run_state()
        return result

    def _enforce_single_market_run_gate(self, payload: dict[str, Any]) -> None:
        source = str(payload.get("entrySource") or "").strip().upper()
        if source not in v26.CAP100_ALLOWED_SOURCES or not self.armed:
            return
        market_id = int(v1._finite(payload.get("marketId")) or 0)
        bucket = int(v1._finite(payload.get("bucketStartSec")) or 0) or None
        window_end = int(v1._finite(payload.get("windowEndMs")) or 0) or None
        with self.single_market_lock:
            requested = self.single_market_requested
            status = self.single_market_status
            target = self.single_market_target_id
            target_bucket = self.single_market_target_bucket_start_sec
        if status in {"COMPLETING", "COMPLETED", "COMPLETION_FAILED"} and target is not None:
            raise v1.EchtgeldEngineError(
                f"one-market session is {status}; all new entries are fenced after market {target}"
            )
        if not requested:
            return
        if market_id <= 0:
            raise v1.EchtgeldEngineError("one-market run requires marketId")
        if target is None:
            with self.next_market_lock:
                anchor = self.activation_anchor_market_id
            if market_id != anchor:
                self._activate_single_market(market_id, bucket, window_end, source="entry")
            return
        if market_id == target:
            return
        # A delayed request from the old anchor remains a stale-order rejection,
        # not evidence that the selected live market has completed.
        if bucket is not None and target_bucket is not None and bucket <= target_bucket:
            raise v1.EchtgeldEngineError(
                f"one-market stale/out-of-order request blocked: request={market_id}, live={target}"
            )
        reason = f"NEXT_MARKET_ONCE_SECOND_MARKET_BLOCKED:{target}->{market_id}"
        self._complete_single_market_after_entry_rejection(
            market_id,
            reason=reason,
        )
        raise v1.EchtgeldEngineError(
            f"one-market session completed after market {target}; second-market entry {market_id} blocked and engine paused"
        )

    def submit_cap100_maker(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._enforce_single_market_run_gate(payload)
        return super().submit_cap100_maker(payload)

    def submit_cap100_taker(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._enforce_single_market_run_gate(payload)
        return super().submit_cap100_taker(payload)

    def single_market_run_state(self) -> dict[str, Any]:
        with self.single_market_lock:
            return {
                "enabled": True,
                "requested": bool(self.single_market_requested),
                "status": self.single_market_status,
                "targetMarketId": self.single_market_target_id,
                "targetBucketStartSec": self.single_market_target_bucket_start_sec,
                "targetWindowEndMs": self.single_market_target_window_end_ms,
                "lastCompletedMarketId": self.single_market_last_completed_id,
                "lastStopReason": self.single_market_last_stop_reason,
                "waitsNextCompleteMarket": True,
                "secondMarketEntryBlockedEngineSide": True,
                "autoPauseCancelsRestingMaker": True,
                "completionFenceFailFast": True,
            }

    def cap100_state(self) -> dict[str, Any]:
        payload = super().cap100_state()
        payload["singleMarketRun"] = self.single_market_run_state()
        # Binance Prediction currently exposes Predict.fun as the upstream
        # market vendor in order payloads.  Keep those two identities separate:
        # all authenticated reads/writes use Binance SAPI, while PREDICT_FUN is
        # provenance for the market carried inside Binance's response.
        payload["executionVenue"] = "BINANCE_PREDICTION"
        payload["executionApiBase"] = BINANCE_API
        payload["marketVendor"] = "PREDICT_FUN"
        payload["venueContract"] = "BINANCE_PREDICTION_SAPI_WITH_PREDICT_FUN_MARKET_VENDOR"
        source = str(payload.get("sourceId") or "")
        payload["displayName"] = (
            "R2 + R2.1 V3.3"
            if source == v26.R2_R21_SOURCE
            else "CAP100 Frozen Controller"
            if source == v26.CAP100_SOURCE
            else source or "Strategy Execution"
        )
        payload["legacyTransportName"] = "cap100"
        return payload

    def _cap100_order_projection(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = super()._cap100_order_projection(limit)
        for row in rows:
            row["venue"] = "binance"
            row["executionVenue"] = "BINANCE_PREDICTION"
            row["executionApiBase"] = BINANCE_API
            row["marketVendor"] = "PREDICT_FUN"
            result = row.get("result")
            if isinstance(result, dict):
                result["executionVenue"] = "BINANCE_PREDICTION"
                result["marketVendor"] = "PREDICT_FUN"
            context = row.get("context")
            if isinstance(context, dict):
                context["executionVenue"] = "BINANCE_PREDICTION"
                context["executionApiBase"] = BINANCE_API
                context["marketVendor"] = "PREDICT_FUN"
        return rows

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION
        payload["singleMarketRun"] = self.single_market_run_state()
        payload["strategyExecution"] = self.cap100_state()
        payload["executionAdapterLegacyAlias"] = "cap100Execution"
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["singleMarketRun"] = self.single_market_run_state()
        payload["strategyExecutionAdapter"] = True
        payload["strategyExecutionSource"] = self.cap100_state().get("sourceId")
        payload["strategyExecutionVenue"] = "BINANCE_PREDICTION"
        payload["strategyExecutionApiBase"] = BINANCE_API
        payload["strategyMarketVendor"] = "PREDICT_FUN"
        payload["cap100SingleNextMarketRun"] = True
        payload["cap100SingleRunSecondMarketBlockedEngineSide"] = True
        payload["cap100SingleRunAutoPauseCancelsMaker"] = True
        payload["singleMarketCompletionFenceFailFast"] = True
        payload["binancePredictionTimeSyncHardened"] = bool(
            BinancePredictionClient.server_timestamp_ms.__module__
            == _binance_time_sync_hardening.__name__
            and BinancePredictionClient.signed_get.__module__
            == _binance_time_sync_hardening.__name__
            and BinancePredictionTradingClient.signed_post.__module__
            == _binance_time_sync_hardening.__name__
        )
        payload["binancePredictionTimeSync"] = _binance_time_sync_hardening.time_sync_snapshot(
            self._poly_client
        )
        return payload

    def close(self) -> None:
        with self.single_market_lock:
            thread = self.single_market_completion_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        super().close()


class _Handler(v28._Handler):
    engine: EchtgeldEngine

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/control/run-next-market-once":
            return super().do_POST()
        try:
            self._body()
            self._send(200, {"ok": True, "state": self.engine.resume_next_market_once()})
        except v1.EchtgeldEngineError as exc:
            self._send(400, {"ok": False, "error": str(exc)[:500]})
        except Exception as exc:
            self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:500]}"})


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV29Handler", (_Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; ordinary RESUME waits next market; opt-in one-market run auto-pauses at rollover",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown(); server.server_close(); engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
