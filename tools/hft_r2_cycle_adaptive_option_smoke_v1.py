from __future__ import annotations

import copy
import math
import time
from pathlib import Path
from typing import Any

from tools import hft_r2_cycle_preserving_execution_smoke_v1 as smoke

base = smoke.base
EPS = smoke.EPS


def run_adaptive_smoke(market_id: int, maintain_mode: str, repair_mode: str) -> dict[str, Any]:
    allowed = {"wait", "offset0", "offset1", "offset2"}
    if maintain_mode not in allowed or repair_mode not in allowed:
        raise ValueError("unsupported mode")
    traces: list[dict[str, Any]] = []
    logic_state: dict[str, Any] = {"desiredPortfolioAction": None}
    original_new_controller = base.new_controller
    original_joblib_load = base.joblib.load
    original_passive_quote = base.r2_passive_quote

    def traced_new_controller(adapter: Any) -> Any:
        controller = original_new_controller(adapter)
        original_step = controller._step
        def traced_step(snapshot: dict[str, Any]) -> Any:
            at_ms = int(snapshot["sampledAtMs"])
            result = original_step(snapshot)
            decision = copy.deepcopy(controller.last_decision)
            if decision is not None and int(decision.get("decisionMs") or -1) == at_ms:
                logic_state["desiredPortfolioAction"] = decision.get("desiredPortfolioAction")
            traces.append({"atMs": at_ms, "decision": decision if decision is not None and int(decision.get("decisionMs") or -1) == at_ms else None})
            return result
        controller._step = traced_step
        return controller

    def fixed_load(path: Any, *args: Any, **kwargs: Any) -> Any:
        if Path(path).resolve() == base.MODEL_PATH.resolve():
            return smoke._fixed_keep_artifact()
        return original_joblib_load(path, *args, **kwargs)

    def adaptive_quote(book: dict[str, dict[float, float]], side: str, opposite_price: float | None) -> float | None:
        desired = logic_state.get("desiredPortfolioAction")
        if desired == "PASSIVE_MAINTAIN":
            mode = maintain_mode
        elif desired == "PASSIVE_REPAIR":
            mode = repair_mode
        else:
            mode = "wait"
        if mode == "wait":
            return None
        book_features = base.mod.outcome_book(book, None)
        if not book_features:
            return None
        offset_ticks = int(mode[-1])
        bid = float(book_features["up_bid"] if side == "UP" else book_features["down_bid"])
        tick = int(math.floor((bid + 1e-9) / base.mod.GRID)) - offset_ticks
        tick = max(int(round(base.mod.MIN_PRICE / base.mod.GRID)), tick)
        price = round(tick * base.mod.GRID, 2)
        if opposite_price is not None:
            while price + float(opposite_price) > base.mod.MAX_PAIR_PRICE_SUM + EPS:
                tick -= 1
                if tick < int(round(base.mod.MIN_PRICE / base.mod.GRID)):
                    return None
                price = round(tick * base.mod.GRID, 2)
        return price

    started = time.perf_counter()
    base.new_controller = traced_new_controller
    base.joblib.load = fixed_load
    base.r2_passive_quote = adaptive_quote
    try:
        row = base.run_market(int(market_id))
    finally:
        base.new_controller = original_new_controller
        base.joblib.load = original_joblib_load
        base.r2_passive_quote = original_passive_quote

    decisions = [t["decision"] for t in traces if isinstance(t.get("decision"), dict)]
    desired_sequence = [str(d.get("desiredPortfolioAction") or "NONE") for d in decisions]
    transitions = sum(desired_sequence[i] != desired_sequence[i-1] for i in range(1, len(desired_sequence)))
    return {
        "marketId": int(market_id),
        "maintainMode": maintain_mode,
        "repairMode": repair_mode,
        "runtimeSeconds": time.perf_counter() - started,
        "realizedPnl": float(row["actualExecution"]["realizedPnl"]),
        "worstCaseFloor": float(row["actualExecution"]["finalPortfolio"]["worst_case_floor"]),
        "finalAbsTrackingError": float(row["actualExecution"]["finalAbsTrackingError"]),
        "combinedFinalAbsNet": float(row["actualExecution"]["combinedFinalAbsNet"]),
        "makerFilledShares": float(row["actualExecution"]["makerFilledShares"]),
        "takerFilledShares": float(row["actualExecution"]["takerFilledShares"]),
        "optionTransitions": transitions,
        "desiredPortfolioActionCounts": smoke._action_counts(decisions, "desiredPortfolioAction"),
    }
