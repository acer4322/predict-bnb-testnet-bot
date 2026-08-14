from __future__ import annotations

import threading
import time
from http.server import ThreadingHTTPServer
from typing import Any

from . import predict_wallet_maker_inventory_taker_strategy as shared_strategy
from . import predict_wallet_reconstructed_maker_strategy as reconstructed_strategy
from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v4_2 as v4_2
from . import predict_wallet_shadow_observer_v4_13 as v4_13


VERSION = "PREDICT_WALLET_SHADOW_V0_18_TARGET_CORE_TAKER_HEAVY_V2"
REPORT_REFRESH_MS = 60_000
REPORT_ERROR_RETRY_MS = 300_000
REPORT_BUILD_MIN_SECONDS_LEFT = 150.0
REPORT_BUILD_MAX_SECONDS_LEFT = 240.0
TARGET_CORE_V2_COHORT = "TARGET_CORE_INTEGRATED_V2_TAKER_HEAVY"
TARGET_CORE_V2_LEVELS_PER_SIDE = 5
TARGET_CORE_V2_SOFT_HEAVY_LEVELS = 2
TARGET_CORE_V2_HARD_REPAIR_LEVELS = 3
TARGET_CORE_V2_TAIL_LEVELS = 3


TARGET_CORE_V2_VARIANT: dict[str, Any] = {
    "cohort": TARGET_CORE_V2_COHORT,
    "label": "Target-core Taker-heavy allocation V2",
    "dynamicDepth": False,
    "fixedLevels": TARGET_CORE_V2_LEVELS_PER_SIDE,
    "reservationSkew": True,
    "targetCoreMaker": True,
    "targetCoreIntegrated": True,
    "targetCoreTakerHeavyV2": True,
}


def _install_target_core_v2() -> None:
    if not any(str(item.get("cohort")) == TARGET_CORE_V2_COHORT for item in shared_strategy.COHORTS):
        shared_strategy.COHORTS = (*shared_strategy.COHORTS, TARGET_CORE_V2_VARIANT)

    if not hasattr(shared_strategy, "_target_core_v2_original_depth_plan"):
        setattr(shared_strategy, "_target_core_v2_original_depth_plan", shared_strategy.depth_plan)
    if not hasattr(shared_strategy, "_target_core_v2_original_policy"):
        setattr(shared_strategy, "_target_core_v2_original_policy", shared_strategy.policy)

    original_depth_plan = getattr(shared_strategy, "_target_core_v2_original_depth_plan")
    original_policy = getattr(shared_strategy, "_target_core_v2_original_policy")

    def depth_plan(variant: dict[str, Any], snapshot: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
        plan = original_depth_plan(variant, snapshot, inventory)
        if str(variant.get("cohort")) != TARGET_CORE_V2_COHORT:
            return plan

        residual = inventory.get("makerResidualSide")
        absolute_delta = abs(float(inventory.get("makerDelta") or 0.0))
        seconds_left_raw = snapshot.get("seconds_left")
        try:
            seconds_left = float(seconds_left_raw) if seconds_left_raw is not None else None
        except (TypeError, ValueError, OverflowError):
            seconds_left = None

        # V2 is an allocation experiment, not a new signal model. Preserve the
        # 18-share parent unit and V1 corridor thresholds while cutting active
        # Maker quote slots by two thirds so the existing Taker economics can be
        # evaluated with materially lower Maker drag.
        plan.update({
            "upLevels": min(int(plan.get("upLevels") or 0), TARGET_CORE_V2_LEVELS_PER_SIDE),
            "downLevels": min(int(plan.get("downLevels") or 0), TARGET_CORE_V2_LEVELS_PER_SIDE),
            "baseLevels": TARGET_CORE_V2_LEVELS_PER_SIDE,
            "targetCoreTakerHeavyV2": True,
            "makerAllocationScaleVsV1": TARGET_CORE_V2_LEVELS_PER_SIDE / shared_strategy.TARGET_CORE_LEVELS_PER_SIDE,
        })

        if residual and absolute_delta >= shared_strategy.TARGET_CORE_HARD_DELTA_SHARES - 1e-9:
            if residual == "UP":
                plan["upLevels"] = 0
                plan["downLevels"] = min(int(plan.get("downLevels") or 0), TARGET_CORE_V2_HARD_REPAIR_LEVELS)
            else:
                plan["upLevels"] = min(int(plan.get("upLevels") or 0), TARGET_CORE_V2_HARD_REPAIR_LEVELS)
                plan["downLevels"] = 0
            plan["regime"] = str(plan.get("regime") or "TARGET_CORE_HARD_REPAIR") + "_TAKER_HEAVY_V2"
        elif residual and absolute_delta >= shared_strategy.TARGET_CORE_SOFT_DELTA_SHARES - 1e-9:
            if residual == "UP":
                plan["upLevels"] = min(int(plan.get("upLevels") or 0), TARGET_CORE_V2_SOFT_HEAVY_LEVELS)
                plan["downLevels"] = TARGET_CORE_V2_LEVELS_PER_SIDE
            else:
                plan["upLevels"] = TARGET_CORE_V2_LEVELS_PER_SIDE
                plan["downLevels"] = min(int(plan.get("downLevels") or 0), TARGET_CORE_V2_SOFT_HEAVY_LEVELS)
            plan["regime"] = str(plan.get("regime") or "TARGET_CORE_SOFT_TILT") + "_TAKER_HEAVY_V2"
        else:
            plan["upLevels"] = min(int(plan.get("upLevels") or 0), TARGET_CORE_V2_LEVELS_PER_SIDE)
            plan["downLevels"] = min(int(plan.get("downLevels") or 0), TARGET_CORE_V2_LEVELS_PER_SIDE)
            plan["regime"] = "TARGET_CORE_TAKER_HEAVY_V2_REDUCED_MAKER"

        if seconds_left is not None and seconds_left <= shared_strategy.TARGET_CORE_TAIL_SHALLOW_SECONDS:
            plan["upLevels"] = min(int(plan.get("upLevels") or 0), TARGET_CORE_V2_TAIL_LEVELS)
            plan["downLevels"] = min(int(plan.get("downLevels") or 0), TARGET_CORE_V2_TAIL_LEVELS)
            if not str(plan.get("regime") or "").endswith("_TAIL_SHALLOW"):
                plan["regime"] = str(plan.get("regime") or "") + "_TAIL_SHALLOW"
        return plan

    def policy(variant: dict[str, Any]) -> dict[str, Any]:
        payload = original_policy(variant)
        if str(variant.get("cohort")) != TARGET_CORE_V2_COHORT:
            return payload
        target = payload.get("targetCoreIntegrated")
        if isinstance(target, dict):
            target = dict(target)
            maker = dict(target.get("maker") or {})
            maker.update({
                "levelsPerSide": TARGET_CORE_V2_LEVELS_PER_SIDE,
                "softTiltHeavySideLevels": TARGET_CORE_V2_SOFT_HEAVY_LEVELS,
                "hardRepairMaximumLevels": TARGET_CORE_V2_HARD_REPAIR_LEVELS,
                "tailLevels": TARGET_CORE_V2_TAIL_LEVELS,
                "allocationScaleVsV1ByQuoteSlots": TARGET_CORE_V2_LEVELS_PER_SIDE / shared_strategy.TARGET_CORE_LEVELS_PER_SIDE,
            })
            taker = dict(target.get("taker") or {})
            taker.update({
                "signalLogicChangedVsV1": False,
                "sizingChangedVsV1": False,
                "allocationInterpretation": "same Taker signal/frequency/sizing; higher relative Taker share comes from lower Maker quote-slot exposure",
            })
            target["maker"] = maker
            target["taker"] = taker
            target["allocationExperiment"] = {
                "controlCohort": "TARGET_CORE_INTEGRATED_V1",
                "makerLevelsPerSideV1": shared_strategy.TARGET_CORE_LEVELS_PER_SIDE,
                "makerLevelsPerSideV2": TARGET_CORE_V2_LEVELS_PER_SIDE,
                "makerQuoteSlotReduction": 1.0 - TARGET_CORE_V2_LEVELS_PER_SIDE / shared_strategy.TARGET_CORE_LEVELS_PER_SIDE,
                "takerSignalSameAsV1": True,
                "takerSizingSameAsV1": True,
                "successCondition": "Taker PnL stays positive and exceeds absolute Maker loss often enough for total PnL and stress ROI to remain positive across a forward sample",
            }
            payload["targetCoreIntegrated"] = target
        maker_policy = payload.get("maker")
        if isinstance(maker_policy, dict):
            maker_policy = dict(maker_policy)
            maker_policy["depthMode"] = "5-level two-sided target-core grid; same 18-share unit and V1 inventory corridors"
            payload["maker"] = maker_policy
        return payload

    shared_strategy.depth_plan = depth_plan
    shared_strategy.policy = policy


_install_target_core_v2()


class WalletShadowObserver(v4_13.WalletShadowObserver):
    """V4.13 plus non-blocking reports and a lower-Maker target-core V2 A/B cohort."""

    def __init__(self, db_path=base.DB_PATH, simulation_db_path=v4_2.SIMULATION_DB_PATH) -> None:
        self._report_lock = threading.Lock()
        self._report_cache: dict[str, Any] | None = None
        self._report_thread: threading.Thread | None = None
        self._report_building = False
        self._report_build_count = 0
        self._report_last_started_ms: int | None = None
        self._report_last_completed_ms: int | None = None
        self._report_last_generation_ms: float | None = None
        self._report_error: str | None = None
        super().__init__(db_path, simulation_db_path)

    def _report_safe_window(self) -> tuple[bool, float | None]:
        snapshot = self.latest_public_signal_snapshot
        if not isinstance(snapshot, dict):
            return False, None
        try:
            seconds_left = float(snapshot.get("seconds_left"))
        except (TypeError, ValueError, OverflowError):
            return False, None
        return REPORT_BUILD_MIN_SECONDS_LEFT <= seconds_left <= REPORT_BUILD_MAX_SECONDS_LEFT, seconds_left

    def _report_diagnostics(self) -> dict[str, Any]:
        now_ms = base._now_ms()
        with self._report_lock:
            has_cache = self._report_cache is not None
            building = self._report_building
            completed = self._report_last_completed_ms
            started = self._report_last_started_ms
            generation_ms = self._report_last_generation_ms
            build_count = self._report_build_count
            error = self._report_error
        age_ms = now_ms - completed if completed is not None else None
        safe, seconds_left = self._report_safe_window()
        stale = age_ms is not None and age_ms >= REPORT_REFRESH_MS
        error_backoff = bool(error and started is not None and now_ms - started < REPORT_ERROR_RETRY_MS)
        if not has_cache:
            if building:
                status = "BUILDING_FIRST_REPORT"
            elif error_backoff:
                status = "ERROR_BACKOFF"
            elif error:
                status = "ERROR_WAITING_SAFE_WINDOW" if not safe else "ERROR_RETRY_READY"
            else:
                status = "WAITING_SAFE_WINDOW" if not safe else "BUILD_READY"
        elif building:
            status = "REFRESHING"
        elif error_backoff:
            status = "STALE_ERROR_BACKOFF" if stale else "READY_WITH_REPORT_ERROR"
        elif stale:
            status = "STALE_WAITING_SAFE_WINDOW" if not safe else "STALE_REFRESH_READY"
        else:
            status = "READY"
        return {
            "reportStatus": status,
            "reportAgeMs": age_ms,
            "lastReportStartedMs": started,
            "lastReportCompletedMs": completed,
            "lastReportGenerationMs": generation_ms,
            "reportBuildCount": build_count,
            "reportError": error,
            "reportRefreshMs": REPORT_REFRESH_MS,
            "reportErrorRetryMs": REPORT_ERROR_RETRY_MS,
            "reportBuildSafeWindowSecondsLeft": [REPORT_BUILD_MIN_SECONDS_LEFT, REPORT_BUILD_MAX_SECONDS_LEFT],
            "reportBuildSafeNow": safe,
            "secondsLeftAtCheck": seconds_left,
            "nonBlockingState": True,
        }

    def _build_full_report(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        return payload

    def _report_worker(self) -> None:
        started = time.perf_counter()
        try:
            payload = self._build_full_report()
            generation_ms = (time.perf_counter() - started) * 1_000.0
            completed_ms = base._now_ms()
            with self._report_lock:
                self._report_last_generation_ms = generation_ms
                self._report_last_completed_ms = completed_ms
                self._report_error = None
                self._report_cache = payload
        except Exception as exc:
            with self._report_lock:
                self._report_error = str(exc)[:500]
        finally:
            with self._report_lock:
                self._report_building = False

    def _maybe_start_report_build(self) -> None:
        now_ms = base._now_ms()
        with self._report_lock:
            if self._report_building:
                return
            if (
                self._report_error
                and self._report_last_started_ms is not None
                and now_ms - self._report_last_started_ms < REPORT_ERROR_RETRY_MS
            ):
                return
            if (
                self._report_cache is not None
                and self._report_last_completed_ms is not None
                and now_ms - self._report_last_completed_ms < REPORT_REFRESH_MS
            ):
                return
        safe, _ = self._report_safe_window()
        if not safe:
            return
        with self._report_lock:
            if self._report_building:
                return
            if (
                self._report_error
                and self._report_last_started_ms is not None
                and now_ms - self._report_last_started_ms < REPORT_ERROR_RETRY_MS
            ):
                return
            if (
                self._report_cache is not None
                and self._report_last_completed_ms is not None
                and now_ms - self._report_last_completed_ms < REPORT_REFRESH_MS
            ):
                return
            self._report_building = True
            self._report_build_count += 1
            self._report_last_started_ms = now_ms
            thread = threading.Thread(target=self._report_worker, name="wallet-shadow-full-report", daemon=True)
            self._report_thread = thread
        thread.start()

    def _lightweight_snapshot(self) -> dict[str, Any]:
        health = super().health_snapshot()
        return {
            "version": VERSION,
            "status": health.get("status"),
            "paperOnly": True,
            "liveOrdersAffected": False,
            "market": {"marketId": health.get("marketId")},
            "reportOnlyState": True,
            "message": "Historical research report is asynchronous; current strategy state remains available immediately.",
        }

    def _overlay_live_shared_states(self, payload: dict[str, Any]) -> None:
        existing_lab = payload.get("makerInventoryTakerSharedLab")
        lab = dict(existing_lab) if isinstance(existing_lab, dict) else {}
        cached_variants = {
            str(item.get("cohort")): item
            for item in lab.get("variants", [])
            if isinstance(item, dict) and item.get("cohort")
        }
        variants: list[dict[str, Any]] = []
        for variant in shared_strategy.COHORTS:
            cohort = str(variant["cohort"])
            state = self.shared_states.get(cohort)
            if not isinstance(state, dict):
                continue
            existing = cached_variants.get(cohort)
            item = dict(existing) if isinstance(existing, dict) else dict(variant)
            inventory = self._shared_inventory(state)
            item.update({
                **variant,
                "status": "ACTIVE" if state["active"] else "WAITING_NEXT_COMPLETE_MARKET",
                "deploymentBoundaryMs": state["deployedAtMs"],
                "excludedDeploymentMarketId": state["excludedMarketId"],
                "policy": shared_strategy.policy(variant),
                "current": {
                    "marketId": self.market_id,
                    "activeOrders": len(state["orders"]),
                    "upOrders": sum(order["side"] == "UP" for order in state["orders"].values()),
                    "downOrders": sum(order["side"] == "DOWN" for order in state["orders"].values()),
                    "depthPlan": state["depthPlan"],
                    "inventory": inventory,
                    "lastDecision": state["lastDecision"],
                    "cutoffApplied": state["cutoffApplied"],
                },
                "performance": dict(existing.get("performance") or {}) if isinstance(existing, dict) else {},
            })
            variants.append(item)
        lab.update({
            "paperOnly": True,
            "forwardOnly": True,
            "historicalBackfill": False,
            "targetEventsDriveStrategy": False,
            "liveOrdersAffected": False,
            "variants": variants,
        })
        payload["makerInventoryTakerSharedLab"] = lab

    def _overlay_live_reconstructed_state(self, payload: dict[str, Any]) -> None:
        state = self.reconstructed_state
        existing = payload.get("reconstructedMakerRulesLab")
        lab = dict(existing) if isinstance(existing, dict) else {
            "cohort": reconstructed_strategy.COHORT,
            "paperOnly": True,
            "forwardOnly": True,
            "historicalBackfill": False,
            "targetEventsDriveStrategy": False,
            "liveOrdersAffected": False,
            "policy": reconstructed_strategy.policy(),
            "performance": {},
        }
        active_orders = list(state["orders"].values())
        current_reserved = sum(float(order["price"]) * float(order["shares"]) for order in active_orders)
        lab["status"] = "ACTIVE" if state["active"] else "WAITING_NEXT_COMPLETE_MARKET"
        lab["current"] = {
            "marketId": self.market_id,
            "initializationStatus": state["initializationStatus"],
            "frozen": state["frozen"],
            "activeOrders": len(active_orders),
            "upOrders": sum(order["side"] == "UP" for order in active_orders),
            "downOrders": sum(order["side"] == "DOWN" for order in active_orders),
            "openingOrders": sum(order["origin"] == "OPENING_RAIL" for order in active_orders),
            "refillOrders": sum(order["origin"] == "REFILL" for order in active_orders),
            "currentReservedUsdt": current_reserved,
            "initialReservedUsdt": state["initialReservedUsdt"],
            "peakReservedUsdt": state["peakReservedUsdt"],
            "inventory": self._current_reconstructed_inventory(),
        }
        payload["reconstructedMakerRulesLab"] = lab

    def snapshot(self) -> dict[str, Any]:
        self._maybe_start_report_build()
        with self._report_lock:
            cached = self._report_cache
        payload = dict(cached) if cached is not None else self._lightweight_snapshot()
        payload["version"] = VERSION
        self._overlay_live_shared_states(payload)
        self._overlay_live_reconstructed_state(payload)
        diagnostics = self._report_diagnostics()
        previous_diagnostics = payload.get("observerDiagnostics")
        merged_diagnostics = dict(previous_diagnostics) if isinstance(previous_diagnostics, dict) else {}
        merged_diagnostics.update(diagnostics)
        payload["observerDiagnostics"] = merged_diagnostics
        payload["reportStatus"] = diagnostics["reportStatus"]
        payload["reportAgeMs"] = diagnostics["reportAgeMs"]
        return payload

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        payload["version"] = VERSION
        payload.update(self._report_diagnostics())
        payload["targetCoreV2Cohort"] = TARGET_CORE_V2_COHORT
        return payload


class _Handler(v4_13._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_14Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        f"{TARGET_CORE_V2_COHORT} active; non-blocking cached reports enabled; paperOnly=true; liveOrdersAffected=false",
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
