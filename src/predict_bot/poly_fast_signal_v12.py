from __future__ import annotations

from typing import Any

from . import multi_prediction_observer as observer_base
from . import poly_fast_live as fast_base
from . import poly_fast_signal_v11 as v11

ASSETS = v11.ASSETS
HOST = v11.HOST
PORT = v11.PORT
ROOT = v11.ROOT
DB_PATH = v11.DB_PATH


class PostRejectDiagnosticEvaluator(v11.TakeProfitSignalEvaluator):
    """V11 plus diagnostics for what happens after a safe rejected-entry rearm.

    This wrapper is intentionally observation-only. It does not change admission,
    retry, take-profit, reversal-exit, lifecycle, or venue-write behavior.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._post_reject_active = False
        self._post_reject_market_id: int | None = None
        self._post_reject_rearmed_at_ms: int | None = None
        self._post_reject_rearm_count_seen = int(self.safe_reject_rearms)
        self._post_reject_evaluations = 0
        self._post_reject_entry_ready = 0
        self._post_reject_new_intents = 0
        self._post_reject_blocked_by: dict[str, int] = {}
        self._post_reject_last_evaluation: dict[str, Any] | None = None
        self._post_reject_last_new_intent: dict[str, Any] | None = None
        self._last_gap_evaluation_at_ms: int | None = None
        self._last_gap_evaluation_market_id: int | None = None
        self._last_gateway_market_id: int | None = None

    def _pending_entry_allows_new_attempt(self) -> bool:
        before = int(self.safe_reject_rearms)
        allowed = super()._pending_entry_allows_new_attempt()
        after = int(self.safe_reject_rearms)
        if after > before:
            terminal = self.last_entry_terminal if isinstance(self.last_entry_terminal, dict) else {}
            intent_id = str(terminal.get("intentId") or "")
            market_id = None
            parts = intent_id.split(":")
            if len(parts) >= 4:
                try:
                    market_id = int(parts[2])
                except Exception:
                    market_id = None
            self._post_reject_active = True
            self._post_reject_market_id = market_id
            self._post_reject_rearmed_at_ms = observer_base._now_ms()
            self._post_reject_rearm_count_seen = after
            self._post_reject_evaluations = 0
            self._post_reject_entry_ready = 0
            self._post_reject_new_intents = 0
            self._post_reject_blocked_by = {}
            self._post_reject_last_evaluation = None
            self._post_reject_last_new_intent = None
        return allowed

    def _gap_evaluation(self, market: dict[str, Any], poly_state: dict[str, Any]) -> dict[str, Any]:
        result = super()._gap_evaluation(market, poly_state)
        market_id = int(market.get("market_id") or 0)
        evaluated_at_ms = observer_base._now_ms()
        self._last_gap_evaluation_at_ms = evaluated_at_ms
        self._last_gap_evaluation_market_id = market_id or None
        if self._post_reject_active:
            if self._post_reject_market_id is None:
                self._post_reject_market_id = market_id or None
            if self._post_reject_market_id == market_id:
                self._post_reject_evaluations += 1
                state = str(result.get("state") or "UNKNOWN")
                if result.get("allowed") is True or state == "ENTRY_READY":
                    self._post_reject_entry_ready += 1
                else:
                    self._post_reject_blocked_by[state] = int(self._post_reject_blocked_by.get(state, 0)) + 1
                self._post_reject_last_evaluation = {
                    "atMs": evaluated_at_ms,
                    "marketId": market_id,
                    "state": state,
                    "allowed": bool(result.get("allowed") is True),
                    "reason": result.get("reason"),
                    "direction": result.get("direction"),
                    "polySelected": result.get("polySelected"),
                    "binanceSelectedAsk": result.get("binanceSelectedAsk"),
                    "edge": result.get("edge"),
                    "elapsedSeconds": result.get("elapsedSeconds"),
                }
            elif market_id > 0 and self._post_reject_market_id is not None and market_id != self._post_reject_market_id:
                # Keep the completed diagnostic snapshot visible after rollover,
                # but stop attributing new-market evaluations to the old reject.
                self._post_reject_active = False
        return result

    def _forward_gap_entry(self, market: dict[str, Any], evaluation: dict[str, Any]) -> None:
        before_attempts = int(self.gateway_attempts)
        super()._forward_gap_entry(market, evaluation)
        if int(self.gateway_attempts) > before_attempts:
            market_id = int(market.get("market_id") or 0)
            self._last_gateway_market_id = market_id or None
            if self._post_reject_active and self._post_reject_market_id == market_id:
                self._post_reject_new_intents += 1
                result = self.last_gateway_result if isinstance(self.last_gateway_result, dict) else {}
                self._post_reject_last_new_intent = {
                    "atMs": observer_base._now_ms(),
                    "marketId": market_id,
                    "status": result.get("status"),
                    "queued": result.get("queued"),
                    "intentId": result.get("intentId") or result.get("signalId"),
                    "roundId": result.get("roundId"),
                }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        gap = payload.get("gapEntry")
        if isinstance(gap, dict):
            gap["lastEvaluationAtMs"] = self._last_gap_evaluation_at_ms
            gap["lastEvaluationMarketId"] = self._last_gap_evaluation_market_id
        gateway = payload.get("polyFastSignalGateway")
        if isinstance(gateway, dict):
            gateway["lastMarketId"] = self._last_gateway_market_id
        payload["postRejectRearm"] = {
            "enabled": True,
            "observationOnly": True,
            "active": bool(self._post_reject_active),
            "lastRejectedMarketId": self._post_reject_market_id,
            "rearmedAtMs": self._post_reject_rearmed_at_ms,
            "safeRearmCountAtStart": int(self._post_reject_rearm_count_seen),
            "evaluationsAfterRearm": int(self._post_reject_evaluations),
            "entryReadyAfterRearm": int(self._post_reject_entry_ready),
            "newIntentAfterRearm": int(self._post_reject_new_intents),
            "blockedAfterRearmBy": dict(sorted(self._post_reject_blocked_by.items())),
            "lastEvaluationAfterRearm": self._post_reject_last_evaluation,
            "lastNewIntentAfterRearm": self._post_reject_last_new_intent,
        }
        return payload


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _first_record(parent: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = parent.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _first_value(parent: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in parent and parent.get(key) is not None:
            return parent.get(key)
    return None


def _same_market(left: Any, right: Any) -> bool | None:
    left_number = _number(left)
    right_number = _number(right)
    if left_number is None or right_number is None:
        return None
    return int(left_number) == int(right_number)


def _intent_market_id(intent_id: Any) -> int | None:
    parts = str(intent_id or "").split(":")
    if len(parts) < 3:
        return None
    try:
        value = int(parts[2])
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


class PolyFastSignalRuntimeV12(v11.PolyFastSignalRuntimeV11):
    def __init__(self) -> None:
        self.observer = v11.v10.v9.v8.v4.v3.SelfContainedFastObserver(db_path=DB_PATH)
        self.engines = {
            asset: PostRejectDiagnosticEvaluator(
                self.observer,
                asset=asset,
                db_path=ROOT / "data" / f"poly_fast_signal_{asset.lower()}.db",
            )
            for asset in ASSETS
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_FAST_SIGNAL_V12_POST_REJECT_DIAGNOSTIC"
        payload["architecture"].update(postRejectRearmDiagnostic=True, postRejectDiagnosticObservationOnly=True)
        return payload

    def diagnostics(self) -> dict[str, Any]:
        """Return a compact, observation-only health summary.

        Deliberately excludes trajectories, raw books, event ledgers and order
        history so this endpoint is small enough to paste into a bug report.
        Missing fields remain UNKNOWN rather than being guessed.
        """
        snapshot = self.snapshot()
        observer = _record(snapshot.get("observer"))
        observer_assets = _record(observer.get("assets"))
        engine_assets = _record(snapshot.get("assets"))
        now_ms = observer_base._now_ms()
        asset_rows: dict[str, Any] = {}
        warnings: list[dict[str, Any]] = []
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

        for asset in ASSETS:
            engine = _record(engine_assets.get(asset))
            observed = _record(observer_assets.get(asset))
            poly = _first_record(observed, "poly", "polymarket")
            binance = _record(observed.get("binance"))
            comparison = _record(observed.get("comparison"))
            poly_market = _record(poly.get("market"))
            binance_market = _record(binance.get("market"))
            gap = _first_record(engine, "gapEntry", "gap_entry")
            gateway = _first_record(engine, "polyFastSignalGateway", "gateway", "signalGateway")
            alignment = _first_record(engine, "bucketAlignmentPreflight", "bucketAlignment")
            alignment_last = _record(alignment.get("last"))
            lifecycle = _first_record(engine, "signalGeneration", "polyFastLifecycle", "lifecycle")
            active_round = _first_record(lifecycle, "activeRound", "active_round")
            post_reject = _record(engine.get("postRejectRearm"))
            take_profit = _record(engine.get("takeProfit"))
            last_eval = _first_record(gap, "lastEvaluation", "last")
            last_gateway = _first_record(gateway, "lastResult", "last")

            market_id = _first_value(binance_market, "marketId", "market_id")
            poly_bucket = _first_value(poly_market, "bucketStartSec", "bucket_start_sec")
            if poly_bucket is None:
                poly_bucket = _first_value(observed, "bucketStartSec", "bucket_start_sec")
            binance_start = _first_value(binance_market, "startMs", "start_ms")
            binance_end = _first_value(binance_market, "endMs", "end_ms")
            poly_status = str(poly.get("status") or "UNKNOWN").upper()
            binance_status = str(binance.get("status") or "UNKNOWN").upper()
            poly_receipt_age = _number(
                _first_value(comparison, "polyReceiptAgeMs", "poly_receipt_age_ms")
            )
            if poly_receipt_age is None:
                poly_receipt_age = _number(_first_value(poly, "bookAgeMs", "receiptAgeMs", "ageMs"))
            poly_source_age = _number(
                _first_value(comparison, "polySourceAgeMs", "poly_source_age_ms")
            )
            binance_age = _number(_first_value(binance, "bookAgeMs", "receiptAgeMs", "ageMs"))
            aligned_value = alignment_last.get("aligned")
            if aligned_value is None:
                aligned_value = alignment.get("aligned")
            aligned = aligned_value is True

            lifecycle_market_id = _first_value(active_round, "marketId", "market_id")
            lifecycle_market_match = _same_market(lifecycle_market_id, market_id)
            phase = str(
                _first_value(active_round, "phase", "status")
                or _first_value(lifecycle, "phase", "status")
                or ""
            ).upper()
            executable_exit = _first_value(active_round, "executableExit", "executable_exit")
            token_repair_reason = _first_value(active_round, "tokenRepairReason", "token_repair_reason")
            lifecycle_warning = "TOKEN_REPAIR_REQUIRED" if phase == "TOKEN_REPAIR_REQUIRED" else None

            eval_market_id = _first_value(
                gap,
                "lastEvaluationMarketId",
                "last_evaluation_market_id",
            )
            eval_market_match = _same_market(eval_market_id, market_id)
            eval_at_ms = _first_value(
                gap,
                "lastEvaluationAtMs",
                "last_evaluation_at_ms",
            )
            if eval_at_ms is None:
                eval_at_ms = _first_value(last_eval, "atMs", "evaluatedAtMs", "sampledAtMs")
            blocking = str(last_eval.get("state") or last_eval.get("reason") or "UNKNOWN")

            last_intent_id = _first_value(last_gateway, "intentId", "signalId")
            gateway_market_id = _first_value(gateway, "lastMarketId", "last_market_id")
            if gateway_market_id is None:
                gateway_market_id = _first_value(last_gateway, "marketId", "market_id")
            if gateway_market_id is None:
                gateway_market_id = _intent_market_id(last_intent_id)
            gateway_market_match = _same_market(gateway_market_id, market_id)
            gateway_at_ms = _first_value(
                gateway,
                "lastAttemptMs",
                "last_attempt_ms",
                "lastAtMs",
                "last_at_ms",
            )
            gateway_status = str(last_gateway.get("status") or "UNKNOWN").upper()
            disabled_entry = asset == "BNB"

            status = "WAITING_SIGNAL"
            reasons: list[str] = []
            if disabled_entry:
                status = "ENTRY_DISABLED"
                reasons.append("BNB new entry intentionally disabled")
                if lifecycle_warning:
                    reasons.append(
                        f"historical lifecycle warning={lifecycle_warning}; "
                        "new BNB entry remains intentionally disabled"
                    )
            elif phase == "TOKEN_REPAIR_REQUIRED":
                status = "EXECUTION_PIPELINE_BROKEN"
                reasons.append(
                    "8781 lifecycle blocks new signal evaluation until persisted Poly token identity is repaired"
                )
                if lifecycle_market_match is False:
                    reasons.append(
                        f"blocked by old lifecycle market={lifecycle_market_id} while current market={market_id}"
                    )
                if token_repair_reason:
                    reasons.append(str(token_repair_reason))
            elif poly_status not in {"LIVE"} or binance_status not in {"LIVE"}:
                status = "STALE_MARKET_DATA"
                reasons.append(f"market feeds poly={poly_status} binance={binance_status}")
            elif (poly_receipt_age is not None and poly_receipt_age > 2_500) or (
                binance_age is not None and binance_age > 2_500
            ):
                status = "STALE_MARKET_DATA"
                reasons.append(
                    f"book age polyReceipt={poly_receipt_age}ms binance={binance_age}ms"
                )
            elif aligned_value is False:
                status = "MARKET_MISMATCH"
                reasons.append("strict current/Poly/Binance bucket alignment is false")
            elif phase in {"OPEN", "ENTRY_AMBIGUOUS", "EXIT_AMBIGUOUS", "EXITING", "ACTIVE"}:
                status = "ACTIVE_POSITION"
                reasons.append(f"active lifecycle phase={phase}")
            elif post_reject.get("active") is True:
                evaluations = int(_number(post_reject.get("evaluationsAfterRearm")) or 0)
                ready = int(_number(post_reject.get("entryReadyAfterRearm")) or 0)
                new_intent = int(_number(post_reject.get("newIntentAfterRearm")) or 0)
                if evaluations >= 25 and ready > 0 and new_intent == 0:
                    status = "SIGNAL_PIPELINE_BROKEN"
                    reasons.append("post-reject evaluator reached ENTRY_READY but no new intent was emitted")
                else:
                    reasons.append("post-reject rearm is active and being observed")
            elif (
                blocking in {"ENTRY_READY"}
                and eval_market_match is not False
                and gateway_market_match is not True
            ):
                status = "DEGRADED"
                reasons.append("latest current-market evaluator says ENTRY_READY but gateway has no current-market result yet")

            if (
                gateway_status in {"ERROR", "FAILED", "UNAVAILABLE"}
                and gateway_market_match is True
                and status not in {"ENTRY_DISABLED", "ACTIVE_POSITION"}
            ):
                status = "EXECUTION_PIPELINE_BROKEN"
                reasons.append(f"current-market 8781 gateway status={gateway_status}")

            asset_rows[asset] = {
                "status": status,
                "marketId": market_id,
                "secondsLeft": _first_value(observed, "secondsLeft", "seconds_left"),
                "market": {
                    "polyStatus": poly_status,
                    "binanceStatus": binance_status,
                    "polyBucketSec": poly_bucket,
                    "binanceStartMs": binance_start,
                    "binanceEndMs": binance_end,
                    "polyBookAgeMs": poly_receipt_age,
                    "polyReceiptAgeMs": poly_receipt_age,
                    "polySourceAgeMs": poly_source_age,
                    "binanceBookAgeMs": binance_age,
                    "bucketAligned": aligned if aligned_value is not None else None,
                },
                "lifecycle": {
                    "phase": phase or "FLAT_OR_UNKNOWN",
                    "roundId": _first_value(active_round, "roundId", "round_id"),
                    "marketId": lifecycle_market_id,
                    "currentMarketMatch": lifecycle_market_match,
                    "executableExit": executable_exit,
                    "tokenRepairReason": token_repair_reason,
                    "warning": lifecycle_warning,
                },
                "evaluator": {
                    "checks": _first_value(gap, "checks"),
                    "triggers": _first_value(gap, "triggers"),
                    "blockingReason": blocking,
                    "lastAtMs": eval_at_ms,
                    "lastMarketId": eval_market_id,
                    "currentMarketMatch": eval_market_match,
                    "direction": last_eval.get("direction"),
                    "edge": last_eval.get("edge"),
                    "binanceSelectedAsk": last_eval.get("binanceSelectedAsk"),
                },
                "gateway": {
                    "scope": "RUNTIME_TOTAL",
                    "attempts": _first_value(gateway, "attempts"),
                    "queued": _first_value(gateway, "queued"),
                    "lastStatus": gateway_status,
                    "lastIntentId": last_intent_id,
                    "lastAtMs": gateway_at_ms,
                    "lastMarketId": gateway_market_id,
                    "currentMarketMatch": gateway_market_match,
                },
                "postRejectRearm": {
                    "active": bool(post_reject.get("active") is True),
                    "evaluationsAfterRearm": post_reject.get("evaluationsAfterRearm"),
                    "entryReadyAfterRearm": post_reject.get("entryReadyAfterRearm"),
                    "newIntentAfterRearm": post_reject.get("newIntentAfterRearm"),
                    "blockedAfterRearmBy": post_reject.get("blockedAfterRearmBy") or {},
                },
                "takeProfit": {
                    "enabled": bool(take_profit.get("enabled") is True),
                    "price": take_profit.get("price"),
                    "checks": take_profit.get("checks"),
                    "triggers": take_profit.get("triggers"),
                },
                "reasons": reasons,
            }
            if severity.get(status, 1) > severity.get(overall, 0):
                overall = status
            if severity.get(status, 0) > 0:
                warnings.append({"asset": asset, "status": status, "reasons": reasons})

        active_assets = [asset for asset in ASSETS if asset != "BNB"]
        normal = {"HEALTHY", "WAITING_SIGNAL", "ACTIVE_POSITION", "ENTRY_DISABLED"}
        if all(str(asset_rows.get(asset, {}).get("status")) in normal for asset in active_assets):
            overall = "HEALTHY"

        return {
            "version": "STRATEGY_HEALTH_DIAGNOSTICS_V1_1",
            "strategyVersion": snapshot.get("version"),
            "status": overall,
            "ok": overall in normal,
            "asOfMs": now_ms,
            "purpose": "compact observation-only health summary; safe to paste instead of full /state",
            "runtime": {
                "port": PORT,
                "observerVersion": observer.get("version"),
                "observerLastError": observer.get("lastError") or observer.get("last_error"),
                "entryAssets": ["BTC", "ETH"],
                "disabledEntryAssets": ["BNB"],
            },
            "assets": asset_rows,
            "behavior": {
                "status": "COUNTERS_ONLY",
                "note": "V1.1 reports current counters and scoped freshness/lifecycle diagnostics only; it does not invent a historical baseline.",
            },
            "warnings": warnings,
            "fullStateRequired": False,
        }


class _Handler(fast_base._Handler):
    runtime: PolyFastSignalRuntimeV12

    def do_GET(self) -> None:  # noqa: N802
        parsed = fast_base.urlparse(self.path)
        if parsed.path in {"/diagnostics", "/api/diagnostics"}:
            self._send(200, {"ok": True, "diagnostics": self.runtime.diagnostics()})
            return
        super().do_GET()


def main() -> int:
    runtime = PolyFastSignalRuntimeV12()
    runtime.start()
    handler = type("PolyFastSignalV12Handler", (_Handler,), {"runtime": runtime})
    server = fast_base.ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Poly Fast Signal V12 listening on http://{HOST}:{PORT}/state; "
        "diagnostics=/diagnostics compact observation-only; "
        "post-reject diagnostic=enabled observation-only; V11 trading rules unchanged",
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
