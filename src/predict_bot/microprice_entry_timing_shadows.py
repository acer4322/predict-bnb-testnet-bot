from __future__ import annotations

import math
from functools import wraps
from typing import Any

from . import m_realtime as _realtime
from . import microprice_variants as _variants
from .research_strategy_registry_patch import register_shadow_strategy


SOURCE_STRATEGY = "R_MICROPRICE"
WAIT_PRICE_STRATEGY = "R_MICROPRICE_WAIT_045"
TIME_LADDER_STRATEGY = "R_MICROPRICE_TIME_LADDER"
ENTRY_TIMING_STRATEGIES = (WAIT_PRICE_STRATEGY, TIME_LADDER_STRATEGY)
ENTRY_TIMING_VERSION = "MICROPRICE_ENTRY_TIMING_V1"

SIGNAL_THRESHOLD = 0.20
WINDOW_MAX_SECONDS_LEFT = 180.0
WINDOW_MIN_SECONDS_LEFT = 30.0
WAIT_PRICE_MAX_ASK = 0.45
TIME_LADDER = (
    (120.0, 180.0, 0.40),
    (60.0, 120.0, 0.45),
    (30.0, 60.0, 0.50),
)
SLIPPAGE_BPS = 50.0
MAX_SPREAD = 0.03
MAX_BOOK_AGE_MS = 2_000.0
MAX_BOOK_SKEW_MS = 500.0
DEFAULT_STAKE_USDT = 5.0


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def price_cap_for_strategy(strategy: str, seconds_left: float) -> float | None:
    if not WINDOW_MIN_SECONDS_LEFT < seconds_left <= WINDOW_MAX_SECONDS_LEFT:
        return None
    if strategy == WAIT_PRICE_STRATEGY:
        return WAIT_PRICE_MAX_ASK
    if strategy != TIME_LADDER_STRATEGY:
        return None
    for lower, upper, cap in TIME_LADDER:
        if lower < seconds_left <= upper:
            return cap
    return None


def _trade_exists(store: Any, strategy: str, market_id: int) -> bool:
    try:
        return store.db.execute(
            "SELECT 1 FROM trades WHERE strategy=? AND market_id=? LIMIT 1",
            (strategy, int(market_id)),
        ).fetchone() is not None
    except Exception:
        return False


def _execution_candidate(
    snapshot: dict[str, Any],
    *,
    side: str,
    stake: float,
    max_ask: float,
) -> tuple[dict[str, float] | None, str]:
    prefix = side.lower()
    ask = _finite(snapshot.get(f"{prefix}_ask"))
    bid = _finite(snapshot.get(f"{prefix}_bid"))
    ask_size = _finite(snapshot.get(f"{prefix}_ask_size"))
    book_age_ms = _finite(snapshot.get("book_age_ms"))
    book_skew_ms = _finite(snapshot.get("book_skew_ms"))
    if ask is None or bid is None or ask_size is None:
        return None, "BOOK_UNAVAILABLE"
    if book_age_ms is None or not 0 <= book_age_ms <= MAX_BOOK_AGE_MS:
        return None, "BOOK_STALE"
    if book_skew_ms is None or not 0 <= book_skew_ms <= MAX_BOOK_SKEW_MS:
        return None, "BOOK_SKEW"
    if not 0 < ask < 1 or not 0 <= bid <= ask or ask_size <= 0:
        return None, "BOOK_INVALID"
    if ask - bid > MAX_SPREAD:
        return None, "SPREAD_TOO_WIDE"
    if ask > max_ask:
        return None, "WAIT_BETTER_PRICE"
    entry = ask * (1.0 + SLIPPAGE_BPS / 10_000.0)
    if not 0 < entry < 1:
        return None, "ENTRY_INVALID"
    requested_shares = stake / entry
    if ask_size + 1e-12 < requested_shares:
        return None, "INSUFFICIENT_DEPTH"
    return {
        "raw_ask": ask,
        "bid": bid,
        "visible_ask_size": ask_size,
        "entry": entry,
        "requested_shares": requested_shares,
        "book_age_ms": book_age_ms,
        "book_skew_ms": book_skew_ms,
        "spread": ask - bid,
        "price_cap": max_ask,
    }, "ALLOW"


class MicropriceEntryTimingTracker:
    def __init__(self, engine: Any, store: Any) -> None:
        self.engine = engine
        self.store = store
        self.market_id: int | None = None
        self.anchor_side: str | None = None
        self.anchor_score: float | None = None
        self.anchor_seconds_left: float | None = None
        self.opened: set[tuple[str, int]] = set()
        self.expired: set[tuple[str, int]] = set()
        self.last_event_sequence: str | None = None
        self.anchors = 0
        self.wait_price_events = 0
        self.signal_not_retained_events = 0
        self.opened_counts = {strategy: 0 for strategy in ENTRY_TIMING_STRATEGIES}
        self.expired_counts = {strategy: 0 for strategy in ENTRY_TIMING_STRATEGIES}
        self.last_decision: dict[str, Any] | None = None

    def _reset_market(self, market_id: int) -> None:
        if self.market_id == market_id:
            return
        self.market_id = market_id
        self.anchor_side = None
        self.anchor_score = None
        self.anchor_seconds_left = None
        self.last_event_sequence = None

    def _stake(self) -> float:
        try:
            value = float(
                self.store.config().get(
                    "strategy_r_microprice_stake",
                    DEFAULT_STAKE_USDT,
                )
            )
        except Exception:
            value = DEFAULT_STAKE_USDT
        return value if math.isfinite(value) and value > 0 else DEFAULT_STAKE_USDT

    def _mark_expired(self, market_id: int) -> None:
        if self.anchor_side is None:
            return
        for strategy in ENTRY_TIMING_STRATEGIES:
            key = (strategy, market_id)
            if key in self.opened or key in self.expired:
                continue
            if _trade_exists(self.store, strategy, market_id):
                self.opened.add(key)
                continue
            self.expired.add(key)
            self.expired_counts[strategy] += 1
        self.last_decision = {
            "marketId": market_id,
            "status": "EXPIRED",
            "reason": "price condition not reached before 30-second cutoff",
            "anchorSide": self.anchor_side,
            "anchorScore": self.anchor_score,
            "anchorSecondsLeft": self.anchor_seconds_left,
            "version": ENTRY_TIMING_VERSION,
        }

    def process(
        self,
        snapshot: dict[str, Any],
        fee_bps: int,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        try:
            cfg = self.store.config()
        except Exception:
            cfg = {}
        if not bool(cfg.get("strategy_r_microprice_enabled", True)):
            return []
        if not _variants._direct_context_is_safe(self.engine, context):
            return []
        try:
            market_id = int(snapshot["market_id"])
            seconds_left = float(snapshot["seconds_left"])
        except (KeyError, TypeError, ValueError):
            return []
        self._reset_market(market_id)

        sequence = str(
            context.get("signal_event_sequence")
            or snapshot.get("signal_event_sequence")
            or ""
        )
        if not sequence or sequence == self.last_event_sequence:
            return []
        self.last_event_sequence = sequence

        if seconds_left <= WINDOW_MIN_SECONDS_LEFT:
            self._mark_expired(market_id)
            return []
        if seconds_left > WINDOW_MAX_SECONDS_LEFT:
            return []

        score = _variants.microprice_score(snapshot)
        if score is None or abs(score) < SIGNAL_THRESHOLD:
            if self.anchor_side is not None:
                self.signal_not_retained_events += 1
                self.last_decision = {
                    "marketId": market_id,
                    "status": "WAIT_SIGNAL_RETAIN",
                    "reason": "anchored Microprice direction is not currently above threshold",
                    "anchorSide": self.anchor_side,
                    "currentScore": score,
                    "secondsLeft": seconds_left,
                    "version": ENTRY_TIMING_VERSION,
                }
            return []

        current_side = "UP" if score > 0 else "DOWN"
        if self.anchor_side is None:
            self.anchor_side = current_side
            self.anchor_score = float(score)
            self.anchor_seconds_left = seconds_left
            self.anchors += 1
        elif current_side != self.anchor_side:
            self.signal_not_retained_events += 1
            self.last_decision = {
                "marketId": market_id,
                "status": "WAIT_SIGNAL_RETAIN",
                "reason": "current Microprice signal opposes the frozen first signal",
                "anchorSide": self.anchor_side,
                "anchorScore": self.anchor_score,
                "currentSide": current_side,
                "currentScore": score,
                "secondsLeft": seconds_left,
                "version": ENTRY_TIMING_VERSION,
            }
            return []

        stake = self._stake()
        opened: list[dict[str, Any]] = []
        for strategy in ENTRY_TIMING_STRATEGIES:
            key = (strategy, market_id)
            if key in self.opened or _trade_exists(self.store, strategy, market_id):
                self.opened.add(key)
                continue
            cap = price_cap_for_strategy(strategy, seconds_left)
            if cap is None:
                continue
            execution, reason = _execution_candidate(
                snapshot,
                side=self.anchor_side,
                stake=stake,
                max_ask=cap,
            )
            if execution is None:
                if reason == "WAIT_BETTER_PRICE":
                    self.wait_price_events += 1
                self.last_decision = {
                    "marketId": market_id,
                    "strategy": strategy,
                    "status": "WAITING",
                    "reason": reason,
                    "anchorSide": self.anchor_side,
                    "anchorScore": self.anchor_score,
                    "currentScore": score,
                    "secondsLeft": seconds_left,
                    "priceCap": cap,
                    "version": ENTRY_TIMING_VERSION,
                }
                continue

            diagnostics = {
                "paper_only": True,
                "live_orders_affected": False,
                "shadow_only": True,
                "forward_only": True,
                "entry_timing_version": ENTRY_TIMING_VERSION,
                "source_strategy": SOURCE_STRATEGY,
                "variant_mode": (
                    "WAIT_FOR_RAW_ASK_LE_045"
                    if strategy == WAIT_PRICE_STRATEGY
                    else "TIME_DEPENDENT_RAW_ASK_CAP"
                ),
                "signal_threshold": SIGNAL_THRESHOLD,
                "frozen_first_signal_side": True,
                "anchor_side": self.anchor_side,
                "anchor_score": self.anchor_score,
                "anchor_seconds_left": self.anchor_seconds_left,
                "current_score": float(score),
                "seconds_left": seconds_left,
                "raw_top_ask": execution["raw_ask"],
                "raw_top_bid": execution["bid"],
                "simulated_entry_after_slippage": execution["entry"],
                "visible_ask_size": execution["visible_ask_size"],
                "requested_shares": execution["requested_shares"],
                "spread": execution["spread"],
                "book_age_ms": execution["book_age_ms"],
                "book_skew_ms": execution["book_skew_ms"],
                "price_cap": cap,
                "slippage_bps": SLIPPAGE_BPS,
                "fee_bps": int(fee_bps),
                "window": {
                    "maximumSecondsLeft": WINDOW_MAX_SECONDS_LEFT,
                    "minimumSecondsLeftExclusive": WINDOW_MIN_SECONDS_LEFT,
                },
                "time_ladder": [
                    {
                        "lowerExclusive": lower,
                        "upperInclusive": upper,
                        "maximumRawAsk": ladder_cap,
                    }
                    for lower, upper, ladder_cap in TIME_LADDER
                ],
                "prediction_data_source": context.get("prediction_data_source"),
                "signal_event_sequence": context.get("signal_event_sequence"),
                "signal_timestamp": snapshot.get("timestamp"),
            }
            self.store.open_trade(
                strategy=strategy,
                topic_id=int(snapshot["topic_id"]),
                market_id=market_id,
                side=self.anchor_side,
                entry=float(execution["entry"]),
                target=None,
                stake=stake,
                fee_rate_bps=int(fee_bps),
                note=(
                    f"{strategy} forward-only Microprice entry-timing Paper shadow; "
                    "first eligible direction frozen; never live-forwarded"
                ),
                strategy_version=ENTRY_TIMING_VERSION,
                diagnostics=diagnostics,
            )
            self.opened.add(key)
            self.opened_counts[strategy] += 1
            opened.append(
                {
                    "strategy": strategy,
                    "topic_id": int(snapshot["topic_id"]),
                    "market_id": market_id,
                    "side": self.anchor_side,
                    "entry_price": float(execution["entry"]),
                    "raw_top_ask": float(execution["raw_ask"]),
                    "stake": stake,
                    "seconds_left": seconds_left,
                    "book_age_ms": execution["book_age_ms"],
                    "fee_bps": int(fee_bps),
                    "signal_timestamp": str(snapshot.get("timestamp") or ""),
                    "paper_only": True,
                    "live_orders_affected": False,
                    "market_data_integrity_ok": True,
                    "research_signal": float(score),
                    "microprice_entry_timing_shadow": True,
                }
            )
            self.last_decision = {
                "marketId": market_id,
                "strategy": strategy,
                "status": "OPENED",
                "anchorSide": self.anchor_side,
                "anchorScore": self.anchor_score,
                "currentScore": score,
                "secondsLeft": seconds_left,
                "rawAsk": execution["raw_ask"],
                "entry": execution["entry"],
                "priceCap": cap,
                "version": ENTRY_TIMING_VERSION,
            }
        return opened

    def state(self) -> dict[str, Any]:
        return {
            "version": ENTRY_TIMING_VERSION,
            "paperOnly": True,
            "liveOrdersAffected": False,
            "forwardOnly": True,
            "sourceStrategy": SOURCE_STRATEGY,
            "anchorPolicy": "freeze first >=0.20 Microprice direction in (30,180] seconds",
            "anchors": self.anchors,
            "waitPriceEvents": self.wait_price_events,
            "signalNotRetainedEvents": self.signal_not_retained_events,
            "opened": dict(self.opened_counts),
            "expired": dict(self.expired_counts),
            "lastDecision": self.last_decision,
            "rules": {
                WAIT_PRICE_STRATEGY: {
                    "signalThreshold": SIGNAL_THRESHOLD,
                    "maximumRawAsk": WAIT_PRICE_MAX_ASK,
                    "windowSecondsLeft": [WINDOW_MIN_SECONDS_LEFT, WINDOW_MAX_SECONDS_LEFT],
                },
                TIME_LADDER_STRATEGY: {
                    "signalThreshold": SIGNAL_THRESHOLD,
                    "timeLadder": [
                        {
                            "lowerExclusive": lower,
                            "upperInclusive": upper,
                            "maximumRawAsk": cap,
                        }
                        for lower, upper, cap in TIME_LADDER
                    ],
                    "windowSecondsLeft": [WINDOW_MIN_SECONDS_LEFT, WINDOW_MAX_SECONDS_LEFT],
                },
            },
        }


def _strategy_stats(store: Any, strategy: str) -> dict[str, Any]:
    try:
        row = store.db.execute(
            """SELECT
                   COUNT(*) AS trades,
                   SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) AS open,
                   SUM(CASE WHEN status='SETTLED_WIN' THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN status='SETTLED_LOSS' THEN 1 ELSE 0 END) AS losses,
                   COALESCE(SUM(CASE WHEN pnl IS NOT NULL THEN pnl ELSE 0 END), 0) AS realized_pnl,
                   AVG(entry_price) AS average_entry_price
               FROM trades WHERE strategy=?""",
            (strategy,),
        ).fetchone()
    except Exception:
        row = None
    if row is None:
        trades = opened = wins = losses = 0
        pnl = 0.0
        average_entry = None
    else:
        trades = int(row["trades"] or 0)
        opened = int(row["open"] or 0)
        wins = int(row["wins"] or 0)
        losses = int(row["losses"] or 0)
        pnl = float(row["realized_pnl"] or 0.0)
        average_entry = (
            float(row["average_entry_price"])
            if row["average_entry_price"] is not None
            else None
        )
    settled = wins + losses
    return {
        "strategy": strategy,
        "mode": (
            "WAIT FOR ASK <= 0.45"
            if strategy == WAIT_PRICE_STRATEGY
            else "TIME-LADDER PRICE CAP"
        ),
        "trades": trades,
        "open": opened,
        "settled": settled,
        "wins": wins,
        "losses": losses,
        "winRate": wins / settled if settled else None,
        "realizedPnl": pnl,
        "averageEntryPrice": average_entry,
        "parameters": {
            "sourceStrategy": SOURCE_STRATEGY,
            "signalThreshold": SIGNAL_THRESHOLD,
            "frozenFirstSignalSide": True,
            "windowMaximumSecondsLeft": WINDOW_MAX_SECONDS_LEFT,
            "windowMinimumSecondsLeftExclusive": WINDOW_MIN_SECONDS_LEFT,
            **(
                {"maximumRawAsk": WAIT_PRICE_MAX_ASK}
                if strategy == WAIT_PRICE_STRATEGY
                else {
                    "timeLadder": [
                        f"({lower},{upper}]<= {cap:.2f}"
                        for lower, upper, cap in TIME_LADDER
                    ]
                }
            ),
        },
    }


def _inject_dashboard(payload: dict[str, Any], store: Any, tracker: MicropriceEntryTimingTracker | None) -> dict[str, Any]:
    strategies = {
        strategy: _strategy_stats(store, strategy)
        for strategy in ENTRY_TIMING_STRATEGIES
    }
    runtime = tracker.state() if tracker is not None else {
        "version": ENTRY_TIMING_VERSION,
        "paperOnly": True,
        "liveOrdersAffected": False,
        "forwardOnly": True,
        "status": "UNAVAILABLE",
    }
    experiment = {
        "version": ENTRY_TIMING_VERSION,
        "paperOnly": True,
        "liveOrdersAffected": False,
        "forwardOnly": True,
        "sourceStrategy": SOURCE_STRATEGY,
        "strategies": strategies,
        "runtime": runtime,
    }
    research = payload.get("researchForward")
    if isinstance(research, dict):
        research["micropriceEntryTimingExperiment"] = experiment
        research_strategies = research.get("strategies")
        if isinstance(research_strategies, dict):
            for strategy, stats in strategies.items():
                research_strategies[strategy] = {
                    "enabled": True,
                    "stakeUsdt": tracker._stake() if tracker is not None else DEFAULT_STAKE_USDT,
                    "selectedBacktestParameters": stats["parameters"],
                    "chronologicalValidation": {
                        "status": "ANALYZABLE" if stats["settled"] >= 30 else "COLLECTING",
                        "samples": stats["trades"],
                        "settled": stats["settled"],
                        "wins": stats["wins"],
                        "losses": stats["losses"],
                        "realizedPnl": stats["realizedPnl"],
                        "minimum": 30,
                    },
                    "paperOnly": True,
                    "liveOrdersAffected": False,
                }
    summaries = payload.get("summaries")
    if isinstance(summaries, dict):
        for strategy, stats in strategies.items():
            summaries[strategy] = {
                "trades": stats["trades"],
                "open": stats["open"],
                "wins": stats["wins"],
                "losses": stats["losses"],
                "realized_pnl": stats["realizedPnl"],
            }
    return payload


def _wrap_dashboard(store_class: type[Any]) -> None:
    original = getattr(store_class, "dashboard", None)
    if not callable(original) or getattr(original, "_microprice_entry_timing_v1", False):
        return

    @wraps(original)
    def dashboard_with_entry_timing(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = original(self, *args, **kwargs)
        if not isinstance(payload, dict):
            return payload
        tracker = getattr(self, "_microprice_entry_timing_tracker", None)
        return _inject_dashboard(
            payload,
            self,
            tracker if isinstance(tracker, MicropriceEntryTimingTracker) else None,
        )

    dashboard_with_entry_timing._microprice_entry_timing_v1 = True  # type: ignore[attr-defined]
    store_class.dashboard = dashboard_with_entry_timing


def _wrap_store(engine: Any, store: Any) -> None:
    existing = getattr(store, "_microprice_entry_timing_tracker", None)
    if isinstance(existing, MicropriceEntryTimingTracker):
        existing.engine = engine
        engine.microprice_entry_timing_tracker = existing
        _wrap_dashboard(type(store))
        return
    original = getattr(store, "maybe_enter_m_series", None)
    if not callable(original):
        return
    tracker = MicropriceEntryTimingTracker(engine, store)

    @wraps(original)
    def maybe_enter_with_entry_timing(
        snapshot: dict[str, Any],
        fee_bps: int,
        *,
        realtime_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        opened = list(
            original(
                snapshot,
                fee_bps,
                realtime_context=realtime_context,
            )
            or []
        )
        try:
            derived = tracker.process(
                snapshot,
                int(fee_bps),
                dict(realtime_context or {}),
            )
        except Exception:
            derived = []
        return [*opened, *derived]

    maybe_enter_with_entry_timing._microprice_entry_timing_v1 = True  # type: ignore[attr-defined]
    store.maybe_enter_m_series = maybe_enter_with_entry_timing
    store._microprice_entry_timing_tracker = tracker
    engine.microprice_entry_timing_tracker = tracker
    _wrap_dashboard(type(store))


def install_microprice_entry_timing_shadows() -> None:
    for strategy in ENTRY_TIMING_STRATEGIES:
        register_shadow_strategy(
            strategy,
            parameters={
                "horizon": WINDOW_MAX_SECONDS_LEFT,
                "derived_only": 1.0,
                "native_event_driven": 1.0,
                "paper_shadow": 1.0,
            },
            generic_signal=False,
        )

    engine_class = _realtime.MSeriesRealtimeEngine
    original_init = engine_class.__init__
    if getattr(original_init, "_microprice_entry_timing_v1", False):
        return

    @wraps(original_init)
    def init_with_entry_timing(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        _wrap_store(self, self.store)

    init_with_entry_timing._microprice_entry_timing_v1 = True  # type: ignore[attr-defined]
    engine_class.__init__ = init_with_entry_timing
