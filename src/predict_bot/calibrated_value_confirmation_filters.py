from __future__ import annotations

from functools import wraps
from typing import Any

from . import m_realtime as _realtime
from .calibrated_value_confirmation_variants import (
    CALIBRATED_VALUE_CONFIRMATION_VERSION,
    FINAL_MIN_NET_EDGE,
    INITIAL_MIN_NET_EDGE,
    MAX_BOOK_AGE_MS,
    MAX_BOOK_SKEW_MS,
    MAX_CONFIRMATION_MS,
    MAX_FOLLOW_ASK,
    MIN_CHOSEN_MIDPOINT_MOVE,
    MIN_CONFIRMATION_MS,
    MIN_CONFIRMATIONS,
    MIN_RETAINED_EDGE_RATIO,
    SOURCE_STRATEGY,
    STAKE_USDT_PER_VARIANT,
    WINDOW_MAX_SECONDS_LEFT,
    WINDOW_MIN_SECONDS_LEFT,
    CalibratedValueConfirmationTracker,
    _candidate,
    _direct_context_is_safe,
    _execution,
    _finite,
)

RANGE12_STRATEGY = "R_CALIBRATED_VALUE_CONFIRM_RANGE12"
LOWTAIL_STRATEGY = "R_CALIBRATED_VALUE_LOWTAIL_CONFIRM"
FILTER_VERSION = "CALIBRATED_VALUE_CONFIRM_FILTERS_V1"
FILTER_STRATEGIES = (RANGE12_STRATEGY, LOWTAIL_STRATEGY)

RANGE12_SCORE_MIN = 1
RANGE12_SCORE_MAX = 2
RANGE12_CROSSOVERS_MAX = 2

LOWTAIL_ENTRY_MIN = 0.10
LOWTAIL_ENTRY_MAX = 0.221
LOWTAIL_CONFIRMATIONS = 3
LOWTAIL_CONFIRMATION_MS = 250.0
LOWTAIL_CONFIRMATION_MAX_MS = 1500.0
LOWTAIL_BOOK_AGE_MAX_MS = 300.0
LOWTAIL_BOOK_SKEW_MAX_MS = 100.0
LOWTAIL_RETAINED_EDGE_MIN = 0.75

_ACTIVE_TRACKER: CalibratedValueConfirmationTracker | None = None


def _int(value: Any) -> int | None:
    number = _finite(value)
    return int(number) if number is not None else None


def _observer(context: dict[str, Any], market_id: int) -> dict[str, Any] | None:
    gate = context.get("m01o_observer_gate")
    if not isinstance(gate, dict):
        gates = context.get("m01o_observer_gates")
        gate = gates.get("F2") if isinstance(gates, dict) else None
    if not isinstance(gate, dict):
        return None
    gate_market = _int(gate.get("currentMarketId"))
    if gate_market is not None and gate_market != market_id:
        return None
    score = _int(gate.get("currentRangeScore"))
    crossovers = _int(gate.get("currentEffectiveCrossovers"))
    trend = gate.get("currentTrendVeto")
    if score is None or crossovers is None:
        return None
    return {
        "currentMarketId": gate_market,
        "currentRangeScore": score,
        "currentEffectiveCrossovers": crossovers,
        "currentTrendVeto": trend if isinstance(trend, bool) else None,
        "currentMedianEr60s": _finite(gate.get("currentMedianEr60s")),
        "currentBothSidesTouched": gate.get("currentBothSidesTouched"),
        "currentPhase": gate.get("currentPhase"),
        "historicalState": gate.get("historicalState"),
        "historicalSampleCount": _int(gate.get("historicalSampleCount")),
    }


def _safe_book(snapshot: dict[str, Any], age_max: float, skew_max: float) -> bool:
    age = _finite(snapshot.get("book_age_ms"))
    skew = _finite(snapshot.get("book_skew_ms"))
    return bool(
        age is not None
        and skew is not None
        and 0 <= age <= age_max
        and 0 <= skew <= skew_max
    )


def _exists(store: Any, strategy: str, market_id: int) -> bool:
    try:
        return store.db.execute(
            "SELECT 1 FROM trades WHERE strategy=? AND market_id=? LIMIT 1",
            (strategy, market_id),
        ).fetchone() is not None
    except Exception:
        return False


def _ensure(tracker: CalibratedValueConfirmationTracker) -> dict[str, Any]:
    state = getattr(tracker, "_cv_filter_state", None)
    if isinstance(state, dict):
        return state
    state = {
        "marketId": None,
        "lastSequence": None,
        "initial": None,
        "rangeSamples": [],
        "lowtailInitial": None,
        "lowtailSamples": [],
        "rangeOpened": set(),
        "lowtailOpened": set(),
        "rangeTerminal": set(),
        "lowtailTerminal": set(),
        "rangeCount": 0,
        "lowtailCount": 0,
        "lastDecisions": {RANGE12_STRATEGY: None, LOWTAIL_STRATEGY: None},
    }
    tracker._cv_filter_state = state
    return state


def _reset(state: dict[str, Any], market_id: int) -> None:
    if state["marketId"] == market_id:
        return
    state["marketId"] = market_id
    state["lastSequence"] = None
    state["initial"] = None
    state["rangeSamples"] = []
    state["lowtailInitial"] = None
    state["lowtailSamples"] = []


def _base_sample(
    initial: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    return {
        "sequence": str(initial.get("sequence") or ""),
        "received_monotonic_ns": int(initial.get("received_monotonic_ns") or 0),
        "side": str(initial.get("side") or ""),
        "edge": float(initial.get("edge") or 0.0),
        "probability": float(initial.get("probability") or 0.0),
        "entry": float(initial.get("entry") or 0.0),
        "midpoint": float(initial.get("midpoint") or 0.0),
        "book_age_ms": _finite(snapshot.get("book_age_ms")),
        "book_skew_ms": _finite(snapshot.get("book_skew_ms")),
    }


def _initialize(
    tracker: CalibratedValueConfirmationTracker,
    snapshot: dict[str, Any],
) -> None:
    state = _ensure(tracker)
    if state["initial"] is not None:
        return
    initial = getattr(tracker, "initial", None)
    if not isinstance(initial, dict):
        return
    if int(getattr(tracker, "market_id", -1) or -1) != int(snapshot["market_id"]):
        return
    state["initial"] = dict(initial)
    state["lastSequence"] = str(initial.get("sequence") or "")
    sample = _base_sample(initial, snapshot)
    state["rangeSamples"] = [sample]
    initial_entry = float(initial.get("entry") or 0.0)
    if (
        initial_entry <= LOWTAIL_ENTRY_MAX
        and float(initial.get("edge") or 0.0) >= INITIAL_MIN_NET_EDGE
        and _safe_book(
            snapshot,
            LOWTAIL_BOOK_AGE_MAX_MS,
            LOWTAIL_BOOK_SKEW_MAX_MS,
        )
    ):
        state["lowtailInitial"] = dict(initial)
        state["lowtailSamples"] = [sample]
    else:
        state["lowtailTerminal"].add(int(snapshot["market_id"]))
        state["lastDecisions"][LOWTAIL_STRATEGY] = {
            "status": "NOT_ELIGIBLE",
            "reason": "initial event was not a strict low-price candidate",
            "initialEntry": initial_entry,
        }


def _sample(
    sequence: str,
    received_ns: int,
    candidate: dict[str, float | str],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "received_monotonic_ns": received_ns,
        "side": str(candidate["side"]),
        "edge": float(candidate["edge"]),
        "probability": float(candidate["probability"]),
        "entry": float(candidate["entry"]),
        "midpoint": float(candidate["midpoint"]),
        "book_age_ms": _finite(snapshot.get("book_age_ms")),
        "book_skew_ms": _finite(snapshot.get("book_skew_ms")),
    }


def _open(
    tracker: CalibratedValueConfirmationTracker,
    *,
    strategy: str,
    mode: str,
    snapshot: dict[str, Any],
    context: dict[str, Any],
    fee_bps: int,
    candidate: dict[str, float | str],
    initial: dict[str, Any],
    samples: list[dict[str, Any]],
    duration_ms: float,
    midpoint_delta: float,
    retained: float,
    observer: dict[str, Any],
    filter_rule: dict[str, Any],
) -> list[dict[str, Any]]:
    market_id = int(snapshot["market_id"])
    if _exists(tracker.store, strategy, market_id):
        return []
    execution = _execution(snapshot, str(candidate["side"]), maximum_ask=MAX_FOLLOW_ASK)
    if execution is None:
        return []
    side = str(candidate["side"])
    diagnostics = {
        **tracker._common_diagnostics(
            snapshot=snapshot,
            context=context,
            fee_bps=fee_bps,
            pair_id=f"{FILTER_VERSION}:{market_id}:{strategy}",
        ),
        "strategy_version": FILTER_VERSION,
        "base_confirmation_version": CALIBRATED_VALUE_CONFIRMATION_VERSION,
        "variant_mode": mode,
        "selected_side": side,
        "model_probability": float(candidate["probability"]),
        "model_edge": float(candidate["edge"]),
        "up_probability": float(candidate["up_probability"]),
        "raw_top_ask": float(execution["ask"]),
        "raw_top_bid": float(execution["bid"]),
        "visible_ask_size": float(execution["ask_size"]),
        "requested_shares": float(execution["requested_shares"]),
        "initial_side": initial.get("side"),
        "initial_edge": initial.get("edge"),
        "initial_entry": initial.get("entry"),
        "initial_midpoint": initial.get("midpoint"),
        "final_edge": float(candidate["edge"]),
        "final_entry": float(candidate["entry"]),
        "final_midpoint": float(candidate["midpoint"]),
        "confirmation_count": len(samples),
        "confirmation_duration_ms": duration_ms,
        "chosen_midpoint_delta": midpoint_delta,
        "retained_edge_ratio": retained,
        "observer_features": observer,
        "filter_rule": filter_rule,
        "samples": [dict(item) for item in samples],
    }
    tracker.store.open_trade(
        strategy=strategy,
        topic_id=int(snapshot["topic_id"]),
        market_id=market_id,
        side=side,
        entry=float(execution["entry"]),
        target=None,
        stake=STAKE_USDT_PER_VARIANT,
        fee_rate_bps=fee_bps,
        note=f"{strategy} forward-only paper confirmation filter; never live-forwarded",
        strategy_version=FILTER_VERSION,
        model_probability=float(candidate["probability"]),
        model_edge=float(candidate["edge"]),
        model_sigma=None,
        diagnostics=diagnostics,
    )
    return [{
        "strategy": strategy,
        "topic_id": int(snapshot["topic_id"]),
        "market_id": market_id,
        "side": side,
        "entry_price": float(execution["entry"]),
        "raw_top_ask": float(execution["ask"]),
        "stake": STAKE_USDT_PER_VARIANT,
        "seconds_left": float(snapshot["seconds_left"]),
        "book_age_ms": snapshot.get("book_age_ms"),
        "fee_bps": fee_bps,
        "signal_timestamp": str(snapshot.get("timestamp") or ""),
        "paper_only": True,
        "live_orders_affected": False,
        "market_data_integrity_ok": True,
        "research_signal": float(candidate["edge"]),
        "calibrated_value_confirmation_variant": True,
        "variant_mode": mode,
    }]


def _metrics(initial: dict[str, Any], candidate: dict[str, float | str], received_ns: int) -> tuple[float, float, float]:
    duration_ms = max(
        0.0,
        (received_ns - int(initial.get("received_monotonic_ns") or 0)) / 1_000_000,
    )
    midpoint_delta = float(candidate["midpoint"]) - float(initial.get("midpoint") or 0.0)
    initial_edge = float(initial.get("edge") or 0.0)
    retained = float(candidate["edge"]) / initial_edge if initial_edge > 0 else 0.0
    return duration_ms, midpoint_delta, retained


def _process_filters(
    tracker: CalibratedValueConfirmationTracker,
    snapshot: dict[str, Any],
    fee_bps: int,
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    state = _ensure(tracker)
    initial = state["initial"]
    if not isinstance(initial, dict) or not _direct_context_is_safe(tracker.engine, context):
        return []
    try:
        market_id = int(snapshot["market_id"])
        seconds_left = float(snapshot["seconds_left"])
        received_ns = int(
            context.get("received_monotonic_ns")
            or context.get("signal_received_monotonic_ns")
            or snapshot.get("received_monotonic_ns")
            or 0
        )
    except (KeyError, TypeError, ValueError):
        return []
    if received_ns <= 0 or not WINDOW_MIN_SECONDS_LEFT <= seconds_left <= WINDOW_MAX_SECONDS_LEFT:
        return []
    if not _safe_book(snapshot, MAX_BOOK_AGE_MS, MAX_BOOK_SKEW_MS):
        return []
    sequence = str(context.get("signal_event_sequence") or snapshot.get("signal_event_sequence") or "")
    if not sequence or sequence == state["lastSequence"]:
        return []
    state["lastSequence"] = sequence
    candidate = _candidate(snapshot, fee_bps)
    if candidate is None or str(candidate["side"]) != str(initial.get("side")):
        state["rangeTerminal"].add(market_id)
        state["lowtailTerminal"].add(market_id)
        return []
    observer = _observer(context, market_id)
    opened: list[dict[str, Any]] = []

    if market_id not in state["rangeOpened"] and market_id not in state["rangeTerminal"]:
        state["rangeSamples"].append(_sample(sequence, received_ns, candidate, snapshot))
        state["rangeSamples"] = state["rangeSamples"][-12:]
        duration, midpoint_delta, retained = _metrics(initial, candidate, received_ns)
        if duration > MAX_CONFIRMATION_MS:
            state["rangeTerminal"].add(market_id)
        elif (
            len(state["rangeSamples"]) >= MIN_CONFIRMATIONS
            and duration >= MIN_CONFIRMATION_MS
            and midpoint_delta >= MIN_CHOSEN_MIDPOINT_MOVE
        ):
            final_edge = float(candidate["edge"])
            if final_edge < FINAL_MIN_NET_EDGE or retained < MIN_RETAINED_EDGE_RATIO:
                state["rangeTerminal"].add(market_id)
            elif (
                observer is not None
                and RANGE12_SCORE_MIN <= int(observer["currentRangeScore"]) <= RANGE12_SCORE_MAX
                and int(observer["currentEffectiveCrossovers"]) <= RANGE12_CROSSOVERS_MAX
            ):
                result = _open(
                    tracker,
                    strategy=RANGE12_STRATEGY,
                    mode="FOLLOW_CONFIRMED_REPRICING_RANGE12",
                    snapshot=snapshot,
                    context=context,
                    fee_bps=fee_bps,
                    candidate=candidate,
                    initial=initial,
                    samples=state["rangeSamples"],
                    duration_ms=duration,
                    midpoint_delta=midpoint_delta,
                    retained=retained,
                    observer=observer,
                    filter_rule={
                        "currentRangeScore": [RANGE12_SCORE_MIN, RANGE12_SCORE_MAX],
                        "maximumEffectiveCrossovers": RANGE12_CROSSOVERS_MAX,
                        "inheritsConfirmV2": True,
                    },
                )
                if result:
                    state["rangeOpened"].add(market_id)
                    state["rangeCount"] += 1
                    state["lastDecisions"][RANGE12_STRATEGY] = {
                        "status": "OPENED",
                        "marketId": market_id,
                        "rangeScore": observer["currentRangeScore"],
                        "effectiveCrossovers": observer["currentEffectiveCrossovers"],
                    }
                    opened.extend(result)

    lowtail_initial = state["lowtailInitial"]
    if (
        isinstance(lowtail_initial, dict)
        and market_id not in state["lowtailOpened"]
        and market_id not in state["lowtailTerminal"]
    ):
        duration, midpoint_delta, retained = _metrics(lowtail_initial, candidate, received_ns)
        if duration > LOWTAIL_CONFIRMATION_MAX_MS:
            state["lowtailTerminal"].add(market_id)
        elif _safe_book(snapshot, LOWTAIL_BOOK_AGE_MAX_MS, LOWTAIL_BOOK_SKEW_MAX_MS):
            state["lowtailSamples"].append(_sample(sequence, received_ns, candidate, snapshot))
            state["lowtailSamples"] = state["lowtailSamples"][-12:]
            final_entry = float(candidate["entry"])
            if (
                len(state["lowtailSamples"]) >= LOWTAIL_CONFIRMATIONS
                and duration >= LOWTAIL_CONFIRMATION_MS
                and midpoint_delta >= MIN_CHOSEN_MIDPOINT_MOVE
                and float(candidate["edge"]) >= FINAL_MIN_NET_EDGE
                and retained >= LOWTAIL_RETAINED_EDGE_MIN
                and LOWTAIL_ENTRY_MIN <= final_entry <= LOWTAIL_ENTRY_MAX
                and observer is not None
                and observer.get("currentTrendVeto") is False
            ):
                result = _open(
                    tracker,
                    strategy=LOWTAIL_STRATEGY,
                    mode="FOLLOW_STRICT_LOWTAIL_CONFIRMATION",
                    snapshot=snapshot,
                    context=context,
                    fee_bps=fee_bps,
                    candidate=candidate,
                    initial=lowtail_initial,
                    samples=state["lowtailSamples"],
                    duration_ms=duration,
                    midpoint_delta=midpoint_delta,
                    retained=retained,
                    observer=observer,
                    filter_rule={
                        "entryRange": [LOWTAIL_ENTRY_MIN, LOWTAIL_ENTRY_MAX],
                        "minimumInitialNetEdge": INITIAL_MIN_NET_EDGE,
                        "minimumFinalNetEdge": FINAL_MIN_NET_EDGE,
                        "minimumConfirmations": LOWTAIL_CONFIRMATIONS,
                        "minimumConfirmationMs": LOWTAIL_CONFIRMATION_MS,
                        "maximumConfirmationMs": LOWTAIL_CONFIRMATION_MAX_MS,
                        "maximumBookAgeMs": LOWTAIL_BOOK_AGE_MAX_MS,
                        "maximumBookSkewMs": LOWTAIL_BOOK_SKEW_MAX_MS,
                        "minimumRetainedEdgeRatio": LOWTAIL_RETAINED_EDGE_MIN,
                        "requiresTrendVetoFalse": True,
                    },
                )
                if result:
                    state["lowtailOpened"].add(market_id)
                    state["lowtailCount"] += 1
                    state["lastDecisions"][LOWTAIL_STRATEGY] = {
                        "status": "OPENED",
                        "marketId": market_id,
                        "finalEntry": final_entry,
                        "confirmationCount": len(state["lowtailSamples"]),
                    }
                    opened.extend(result)
    return opened


def _runtime(tracker: CalibratedValueConfirmationTracker | None) -> dict[str, Any]:
    if not isinstance(tracker, CalibratedValueConfirmationTracker):
        return {"version": FILTER_VERSION, "status": "UNAVAILABLE", "rules": {}}
    state = _ensure(tracker)
    return {
        "version": FILTER_VERSION,
        "paperOnly": True,
        "liveOrdersAffected": False,
        "forwardOnly": True,
        "range12Opened": state["rangeCount"],
        "lowtailOpened": state["lowtailCount"],
        "lastDecisions": dict(state["lastDecisions"]),
        "rules": {
            RANGE12_STRATEGY: {
                "currentRangeScore": [RANGE12_SCORE_MIN, RANGE12_SCORE_MAX],
                "maximumEffectiveCrossovers": RANGE12_CROSSOVERS_MAX,
                "inheritsConfirmV2": True,
            },
            LOWTAIL_STRATEGY: {
                "entryRange": [LOWTAIL_ENTRY_MIN, LOWTAIL_ENTRY_MAX],
                "minimumConfirmations": LOWTAIL_CONFIRMATIONS,
                "minimumConfirmationMs": LOWTAIL_CONFIRMATION_MS,
                "maximumConfirmationMs": LOWTAIL_CONFIRMATION_MAX_MS,
                "maximumBookAgeMs": LOWTAIL_BOOK_AGE_MAX_MS,
                "maximumBookSkewMs": LOWTAIL_BOOK_SKEW_MAX_MS,
                "minimumRetainedEdgeRatio": LOWTAIL_RETAINED_EDGE_MIN,
                "requiresTrendVetoFalse": True,
            },
        },
    }


def _stats(store: Any) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    placeholders = ",".join("?" for _ in FILTER_STRATEGIES)
    try:
        rows = [dict(row) for row in store.db.execute(
            f"""SELECT id, strategy, market_id, side, status, entry_price,
                       stake, pnl, opened_at, closed_at
                  FROM trades WHERE strategy IN ({placeholders}) ORDER BY id""",
            FILTER_STRATEGIES,
        ).fetchall()]
    except Exception:
        rows = []
    modes = {
        RANGE12_STRATEGY: "FOLLOW_CONFIRMED_REPRICING_RANGE12",
        LOWTAIL_STRATEGY: "FOLLOW_STRICT_LOWTAIL_CONFIRMATION",
    }
    result: dict[str, dict[str, Any]] = {}
    for strategy in FILTER_STRATEGIES:
        selected = [row for row in rows if row.get("strategy") == strategy]
        settled = [row for row in selected if row.get("status") in {"SETTLED_WIN", "SETTLED_LOSS"}]
        wins = sum(row.get("status") == "SETTLED_WIN" for row in settled)
        result[strategy] = {
            "trades": len(selected),
            "open": sum(row.get("status") == "OPEN" for row in selected),
            "settled": len(settled),
            "wins": int(wins),
            "losses": len(settled) - int(wins),
            "winRate": wins / len(settled) if settled else None,
            "realizedPnl": sum(float(row.get("pnl") or 0.0) for row in settled),
            "averageEntryPrice": (
                sum(float(row["entry_price"]) for row in selected) / len(selected)
                if selected else None
            ),
            "mode": modes[strategy],
        }
    return result, rows


def _inject(payload: dict[str, Any], store: Any) -> dict[str, Any]:
    stats, rows = _stats(store)
    runtime = _runtime(_ACTIVE_TRACKER)
    research = payload.get("researchForward")
    if isinstance(research, dict):
        experiment = research.get("calibratedValueConfirmationExperiment")
        if isinstance(experiment, dict):
            if isinstance(experiment.get("strategies"), dict):
                experiment["strategies"].update(stats)
            experiment["filterExtensionVersion"] = FILTER_VERSION
            experiment["filterRuntime"] = runtime
            experiment["range12Markets"] = stats[RANGE12_STRATEGY]["trades"]
            experiment["lowtailMarkets"] = stats[LOWTAIL_STRATEGY]["trades"]
            by_market: dict[int, dict[str, dict[str, Any]]] = {}
            for row in rows:
                by_market.setdefault(int(row["market_id"]), {})[str(row["strategy"])] = row
            recent = experiment.get("recentCohorts")
            if isinstance(recent, list):
                for cohort in recent:
                    if not isinstance(cohort, dict):
                        continue
                    market_id = _int(cohort.get("marketId"))
                    items = by_market.get(market_id or -1, {})
                    cohort["range12"] = items.get(RANGE12_STRATEGY)
                    cohort["lowtail"] = items.get(LOWTAIL_STRATEGY)
        strategies = research.get("strategies")
        if isinstance(strategies, dict):
            rules = runtime.get("rules", {}) if isinstance(runtime, dict) else {}
            for strategy, item in stats.items():
                strategies[strategy] = {
                    "enabled": True,
                    "stakeUsdt": STAKE_USDT_PER_VARIANT,
                    "selectedBacktestParameters": {
                        **(rules.get(strategy, {}) if isinstance(rules, dict) else {}),
                        "sourceStrategy": SOURCE_STRATEGY,
                        "sourceConfirmation": "R_CALIBRATED_VALUE_CONFIRM_V2",
                        "forwardOnly": True,
                    },
                    "chronologicalValidation": {
                        "status": "ANALYZABLE" if item["settled"] >= 30 else "COLLECTING",
                        "samples": item["trades"],
                        "settled": item["settled"],
                        "wins": item["wins"],
                        "losses": item["losses"],
                        "realizedPnl": item["realizedPnl"],
                        "minimum": 30,
                        "fixedCohort": True,
                    },
                    "paperOnly": True,
                    "liveOrdersAffected": False,
                }
    summaries = payload.get("summaries")
    if isinstance(summaries, dict):
        for strategy, item in stats.items():
            summaries[strategy] = {
                "trades": item["trades"],
                "open": item["open"],
                "wins": item["wins"],
                "losses": item["losses"],
                "realized_pnl": item["realizedPnl"],
            }
    return payload


def _patch_tracker() -> None:
    cls = CalibratedValueConfirmationTracker
    original_process = cls.process
    if not getattr(original_process, "_cv_filter_patch_v1", False):
        @wraps(original_process)
        def process(
            self: CalibratedValueConfirmationTracker,
            snapshot: dict[str, Any],
            fee_bps: int,
            context: dict[str, Any],
        ) -> list[dict[str, Any]]:
            state = _ensure(self)
            try:
                market_id = int(snapshot["market_id"])
            except (KeyError, TypeError, ValueError):
                return original_process(self, snapshot, fee_bps, context)
            _reset(state, market_id)
            extra: list[dict[str, Any]] = []
            if (
                int(getattr(self, "market_id", -1) or -1) == market_id
                and isinstance(getattr(self, "initial", None), dict)
            ):
                if state["initial"] is None:
                    _initialize(self, snapshot)
                extra = _process_filters(self, snapshot, int(fee_bps), dict(context or {}))
            base = original_process(self, snapshot, int(fee_bps), context)
            if state["initial"] is None:
                _initialize(self, snapshot)
            return [*(base or []), *extra]
        process._cv_filter_patch_v1 = True  # type: ignore[attr-defined]
        cls.process = process

    original_state = cls.state
    if not getattr(original_state, "_cv_filter_patch_v1", False):
        @wraps(original_state)
        def state(self: CalibratedValueConfirmationTracker) -> dict[str, Any]:
            payload = original_state(self)
            payload["filterExtension"] = _runtime(self)
            strategies = payload.get("strategies")
            if isinstance(strategies, list):
                for strategy in FILTER_STRATEGIES:
                    if strategy not in strategies:
                        strategies.append(strategy)
            return payload
        state._cv_filter_patch_v1 = True  # type: ignore[attr-defined]
        cls.state = state


def _wrap_dashboard(store_class: type[Any]) -> None:
    original = getattr(store_class, "dashboard", None)
    if not callable(original) or getattr(original, "_cv_filter_dashboard_v1", False):
        return
    @wraps(original)
    def dashboard(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = original(self, *args, **kwargs)
        return _inject(payload, self) if isinstance(payload, dict) else payload
    dashboard._cv_filter_dashboard_v1 = True  # type: ignore[attr-defined]
    store_class.dashboard = dashboard


def install_calibrated_value_confirmation_filters() -> None:
    _patch_tracker()
    engine_class = _realtime.MSeriesRealtimeEngine
    original_init = engine_class.__init__
    if getattr(original_init, "_cv_filter_patch_v1", False):
        return
    @wraps(original_init)
    def init(self: Any, *args: Any, **kwargs: Any) -> None:
        global _ACTIVE_TRACKER
        original_init(self, *args, **kwargs)
        tracker = getattr(self, "calibrated_value_confirmation_tracker", None)
        if isinstance(tracker, CalibratedValueConfirmationTracker):
            _ensure(tracker)
            _ACTIVE_TRACKER = tracker
        store = getattr(self, "store", None)
        if store is not None:
            _wrap_dashboard(type(store))
    init._cv_filter_patch_v1 = True  # type: ignore[attr-defined]
    engine_class.__init__ = init
