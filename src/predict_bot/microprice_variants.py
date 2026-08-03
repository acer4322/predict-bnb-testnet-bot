from __future__ import annotations

import json
import math
import time
from functools import wraps
from typing import Any

from . import m_realtime as _realtime
from .core import taker_fee


MICROPRICE_CONFIRM_STRATEGY = "R_MICROPRICE_CONFIRM"
MICROPRICE_REVERSION_STRATEGY = "R_MICROPRICE_REVERSION"
MICROPRICE_VARIANT_VERSION = "MICROPRICE_VARIANTS_V1"

MICROPRICE_THRESHOLD = 0.20
MICROPRICE_MIN_CONFIRMATIONS = 3
MICROPRICE_MIN_CONFIRMATION_MS = 300.0
MICROPRICE_MAX_BOOK_AGE_MS = 500.0
MICROPRICE_MAX_BOOK_SKEW_MS = 150.0
MICROPRICE_MAX_SPREAD = 0.03
MICROPRICE_MIN_MIDPOINT_MOVE = 0.001
MICROPRICE_MIN_RETAINED_STRENGTH = 0.75
MICROPRICE_STAKE_USDT = 5.0
MICROPRICE_SLIPPAGE_BPS = 50.0
MICROPRICE_WINDOW_MAX_SECONDS_LEFT = 181.0
MICROPRICE_WINDOW_MIN_SECONDS_LEFT = 176.0
DIRECT_OUTCOME_DATA_SOURCE = "dual_token_rest"


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _imbalance(bid_size: float, ask_size: float) -> float:
    total = bid_size + ask_size
    return (bid_size - ask_size) / total if total > 0 else 0.0


def microprice_score(snapshot: dict[str, Any]) -> float | None:
    values = {
        key: _finite(snapshot.get(key))
        for key in (
            "up_bid_size",
            "up_ask_size",
            "down_bid_size",
            "down_ask_size",
        )
    }
    if any(value is None or value <= 0 for value in values.values()):
        return None
    return _imbalance(
        values["up_bid_size"],
        values["up_ask_size"],
    ) - _imbalance(
        values["down_bid_size"],
        values["down_ask_size"],
    )


def _midpoint(snapshot: dict[str, Any], side: str) -> float | None:
    prefix = side.lower()
    bid = _finite(snapshot.get(f"{prefix}_bid"))
    ask = _finite(snapshot.get(f"{prefix}_ask"))
    if (
        bid is None
        or ask is None
        or not 0 <= bid <= ask <= 1
    ):
        return None
    return (bid + ask) / 2.0


def _opposite(side: str) -> str:
    return "DOWN" if side == "UP" else "UP"


def _direct_context_is_safe(
    engine: Any,
    context: dict[str, Any],
) -> bool:
    event = getattr(engine, "prediction_event", None)
    return bool(
        context.get("trigger_source") == "prediction"
        and context.get("execution_eligible") is True
        and context.get("prediction_data_source")
        == DIRECT_OUTCOME_DATA_SOURCE
        and isinstance(event, dict)
        and event.get("prediction_data_source")
        == DIRECT_OUTCOME_DATA_SOURCE
        and event.get("direct_outcome_books") is True
    )


class MicropriceVariantTracker:
    def __init__(self, engine: Any, store: Any) -> None:
        self.engine = engine
        self.store = store
        self.market_id: int | None = None
        self.direction: str | None = None
        self.samples: list[dict[str, Any]] = []
        self.opened_markets: set[int] = set()
        self.last_event_sequence: str | None = None
        self.confirmed_pairs = 0
        self.rejected_stale = 0
        self.rejected_skew = 0
        self.rejected_window = 0
        self.rejected_direction_change = 0
        self.rejected_midpoint = 0
        self.rejected_decay = 0
        self.rejected_depth = 0
        self.last_decision: dict[str, Any] | None = None

    def _reset_market(self, market_id: int) -> None:
        if self.market_id == market_id:
            return
        self.market_id = market_id
        self.direction = None
        self.samples = []
        self.last_event_sequence = None

    def _reset_streak(
        self,
        *,
        market_id: int,
        reason: str,
        score: float | None = None,
    ) -> None:
        self.direction = None
        self.samples = []
        self.last_decision = {
            "marketId": market_id,
            "status": "WAITING",
            "reason": reason,
            "score": score,
            "strategyVersion": MICROPRICE_VARIANT_VERSION,
        }

    def _trade_exists(self, strategy: str, market_id: int) -> bool:
        db = getattr(self.store, "db", None)
        if db is None:
            return False
        try:
            row = db.execute(
                "SELECT 1 FROM trades WHERE strategy=? AND market_id=? LIMIT 1",
                (strategy, market_id),
            ).fetchone()
        except Exception:
            return False
        return row is not None

    @staticmethod
    def _execution(
        snapshot: dict[str, Any],
        side: str,
    ) -> dict[str, float] | None:
        prefix = side.lower()
        ask = _finite(snapshot.get(f"{prefix}_ask"))
        bid = _finite(snapshot.get(f"{prefix}_bid"))
        ask_size = _finite(snapshot.get(f"{prefix}_ask_size"))
        if (
            ask is None
            or bid is None
            or ask_size is None
            or not 0 < ask < 1
            or not 0 <= bid <= ask
            or ask_size <= 0
            or ask - bid > MICROPRICE_MAX_SPREAD
        ):
            return None
        entry = ask * (1.0 + MICROPRICE_SLIPPAGE_BPS / 10_000.0)
        if not 0 < entry < 1:
            return None
        requested_shares = MICROPRICE_STAKE_USDT / entry
        if ask_size + 1e-12 < requested_shares:
            return None
        return {
            "ask": ask,
            "bid": bid,
            "ask_size": ask_size,
            "entry": entry,
            "requested_shares": requested_shares,
        }

    def _open_pair(
        self,
        snapshot: dict[str, Any],
        context: dict[str, Any],
        fee_bps: int,
        source_side: str,
        score: float,
        duration_ms: float,
        midpoint_delta: float,
        opposite_midpoint_delta: float,
    ) -> list[dict[str, Any]]:
        market_id = int(snapshot["market_id"])
        confirm_side = source_side
        reversion_side = _opposite(source_side)
        confirm = self._execution(snapshot, confirm_side)
        reversion = self._execution(snapshot, reversion_side)
        if confirm is None or reversion is None:
            self.rejected_depth += 1
            self.last_decision = {
                "marketId": market_id,
                "status": "BLOCK",
                "reason": "paired top-of-book depth or spread unavailable",
                "score": score,
                "sourceSide": source_side,
                "strategyVersion": MICROPRICE_VARIANT_VERSION,
            }
            return []
        if (
            market_id in self.opened_markets
            or self._trade_exists(MICROPRICE_CONFIRM_STRATEGY, market_id)
            or self._trade_exists(MICROPRICE_REVERSION_STRATEGY, market_id)
        ):
            self.opened_markets.add(market_id)
            return []

        sample_payload = [
            {
                "sequence": item["sequence"],
                "receivedMonotonicNs": item["received_monotonic_ns"],
                "score": item["score"],
                "side": item["side"],
                "selectedMidpoint": item["selected_midpoint"],
                "oppositeMidpoint": item["opposite_midpoint"],
                "bookAgeMs": item["book_age_ms"],
                "bookSkewMs": item["book_skew_ms"],
            }
            for item in self.samples
        ]
        common = {
            "paper_only": True,
            "live_orders_affected": False,
            "paired_counterfactual": True,
            "pair_id": f"{MICROPRICE_VARIANT_VERSION}:{market_id}",
            "source_strategy": "R_MICROPRICE",
            "source_signal": score,
            "source_side": source_side,
            "strategy_version": MICROPRICE_VARIANT_VERSION,
            "rule": {
                "threshold": MICROPRICE_THRESHOLD,
                "minimumConfirmations": MICROPRICE_MIN_CONFIRMATIONS,
                "minimumConfirmationMs": MICROPRICE_MIN_CONFIRMATION_MS,
                "maximumBookAgeMs": MICROPRICE_MAX_BOOK_AGE_MS,
                "maximumBookSkewMs": MICROPRICE_MAX_BOOK_SKEW_MS,
                "maximumSpread": MICROPRICE_MAX_SPREAD,
                "minimumMidpointMove": MICROPRICE_MIN_MIDPOINT_MOVE,
                "minimumRetainedStrength": (
                    MICROPRICE_MIN_RETAINED_STRENGTH
                ),
                "windowSecondsLeft": [
                    MICROPRICE_WINDOW_MIN_SECONDS_LEFT,
                    MICROPRICE_WINDOW_MAX_SECONDS_LEFT,
                ],
            },
            "confirmation_count": len(self.samples),
            "confirmation_duration_ms": duration_ms,
            "midpoint_delta": midpoint_delta,
            "opposite_midpoint_delta": opposite_midpoint_delta,
            "book_age_ms": _finite(snapshot.get("book_age_ms")),
            "book_skew_ms": _finite(snapshot.get("book_skew_ms")),
            "prediction_data_source": context.get(
                "prediction_data_source"
            ),
            "direct_outcome_books": True,
            "signal_event_sequence": context.get("signal_event_sequence"),
            "signal_timestamp": snapshot.get("timestamp"),
            "samples": sample_payload,
        }

        opened: list[dict[str, Any]] = []
        for strategy, side, execution, mode in (
            (
                MICROPRICE_CONFIRM_STRATEGY,
                confirm_side,
                confirm,
                "FOLLOW_CONFIRMED_IMBALANCE",
            ),
            (
                MICROPRICE_REVERSION_STRATEGY,
                reversion_side,
                reversion,
                "REVERSE_CONFIRMED_IMBALANCE",
            ),
        ):
            diagnostics = {
                **common,
                "variant_mode": mode,
                "selected_side": side,
                "raw_top_ask": execution["ask"],
                "raw_top_bid": execution["bid"],
                "visible_ask_size": execution["ask_size"],
                "requested_shares": execution["requested_shares"],
                "slippage_bps": MICROPRICE_SLIPPAGE_BPS,
                "fee_bps": int(fee_bps),
            }
            self.store.open_trade(
                strategy=strategy,
                topic_id=int(snapshot["topic_id"]),
                market_id=market_id,
                side=side,
                entry=float(execution["entry"]),
                target=None,
                stake=MICROPRICE_STAKE_USDT,
                fee_rate_bps=int(fee_bps),
                note=(
                    f"{strategy} paired paper experiment; "
                    "direct dual-token REST only; never live-forwarded"
                ),
                strategy_version=MICROPRICE_VARIANT_VERSION,
                diagnostics=diagnostics,
            )
            opened.append(
                {
                    "strategy": strategy,
                    "topic_id": int(snapshot["topic_id"]),
                    "market_id": market_id,
                    "side": side,
                    "entry_price": float(execution["entry"]),
                    "raw_top_ask": float(execution["ask"]),
                    "stake": MICROPRICE_STAKE_USDT,
                    "seconds_left": float(snapshot["seconds_left"]),
                    "book_age_ms": snapshot.get("book_age_ms"),
                    "fee_bps": int(fee_bps),
                    "signal_timestamp": str(
                        snapshot.get("timestamp") or ""
                    ),
                    "paper_only": True,
                    "live_orders_affected": False,
                    "market_data_integrity_ok": True,
                    "research_signal": score,
                    "paired_microprice_variant": True,
                    "variant_mode": mode,
                }
            )

        self.opened_markets.add(market_id)
        self.confirmed_pairs += 1
        self.last_decision = {
            "marketId": market_id,
            "status": "OPENED_PAIR",
            "score": score,
            "sourceSide": source_side,
            "confirmSide": confirm_side,
            "reversionSide": reversion_side,
            "confirmationCount": len(self.samples),
            "confirmationDurationMs": duration_ms,
            "midpointDelta": midpoint_delta,
            "oppositeMidpointDelta": opposite_midpoint_delta,
            "strategyVersion": MICROPRICE_VARIANT_VERSION,
        }
        return opened

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
        if not _direct_context_is_safe(self.engine, context):
            return []
        try:
            market_id = int(snapshot["market_id"])
            seconds_left = float(snapshot["seconds_left"])
            received_monotonic_ns = int(
                context.get("received_monotonic_ns")
                or context.get("signal_received_monotonic_ns")
                or snapshot.get("received_monotonic_ns")
                or 0
            )
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

        if not (
            MICROPRICE_WINDOW_MIN_SECONDS_LEFT
            <= seconds_left
            <= MICROPRICE_WINDOW_MAX_SECONDS_LEFT
        ):
            self.rejected_window += 1
            self._reset_streak(
                market_id=market_id,
                reason="outside microprice confirmation window",
            )
            return []

        book_age_ms = _finite(snapshot.get("book_age_ms"))
        book_skew_ms = _finite(snapshot.get("book_skew_ms"))
        if (
            book_age_ms is None
            or book_age_ms < 0
            or book_age_ms > MICROPRICE_MAX_BOOK_AGE_MS
        ):
            self.rejected_stale += 1
            self._reset_streak(
                market_id=market_id,
                reason="book age exceeds variant limit",
            )
            return []
        if (
            book_skew_ms is None
            or book_skew_ms < 0
            or book_skew_ms > MICROPRICE_MAX_BOOK_SKEW_MS
        ):
            self.rejected_skew += 1
            self._reset_streak(
                market_id=market_id,
                reason="book skew exceeds variant limit",
            )
            return []

        score = microprice_score(snapshot)
        if score is None or abs(score) < MICROPRICE_THRESHOLD:
            self._reset_streak(
                market_id=market_id,
                reason="score below threshold",
                score=score,
            )
            return []
        side = "UP" if score > 0 else "DOWN"
        selected_midpoint = _midpoint(snapshot, side)
        opposite_midpoint = _midpoint(snapshot, _opposite(side))
        if selected_midpoint is None or opposite_midpoint is None:
            self._reset_streak(
                market_id=market_id,
                reason="midpoint unavailable",
                score=score,
            )
            return []

        if self.direction is not None and side != self.direction:
            self.rejected_direction_change += 1
            self.samples = []
        self.direction = side
        self.samples.append(
            {
                "sequence": sequence,
                "received_monotonic_ns": received_monotonic_ns,
                "score": score,
                "side": side,
                "selected_midpoint": selected_midpoint,
                "opposite_midpoint": opposite_midpoint,
                "book_age_ms": book_age_ms,
                "book_skew_ms": book_skew_ms,
            }
        )
        self.samples = self.samples[-12:]

        if len(self.samples) < MICROPRICE_MIN_CONFIRMATIONS:
            self.last_decision = {
                "marketId": market_id,
                "status": "ACCUMULATING",
                "reason": "waiting for consecutive confirmations",
                "score": score,
                "sourceSide": side,
                "confirmationCount": len(self.samples),
                "strategyVersion": MICROPRICE_VARIANT_VERSION,
            }
            return []

        first = self.samples[0]
        duration_ms = max(
            0.0,
            (
                received_monotonic_ns
                - int(first["received_monotonic_ns"])
            )
            / 1_000_000,
        )
        if duration_ms < MICROPRICE_MIN_CONFIRMATION_MS:
            self.last_decision = {
                "marketId": market_id,
                "status": "ACCUMULATING",
                "reason": "confirmation duration not reached",
                "score": score,
                "sourceSide": side,
                "confirmationCount": len(self.samples),
                "confirmationDurationMs": duration_ms,
                "strategyVersion": MICROPRICE_VARIANT_VERSION,
            }
            return []

        maximum_strength = max(abs(float(item["score"])) for item in self.samples)
        retained_strength = (
            abs(score) / maximum_strength if maximum_strength > 0 else 0.0
        )
        if retained_strength < MICROPRICE_MIN_RETAINED_STRENGTH:
            self.rejected_decay += 1
            self._reset_streak(
                market_id=market_id,
                reason="score decayed before confirmation",
                score=score,
            )
            return []

        midpoint_delta = selected_midpoint - float(
            first["selected_midpoint"]
        )
        opposite_midpoint_delta = opposite_midpoint - float(
            first["opposite_midpoint"]
        )
        midpoint_confirmed = bool(
            midpoint_delta >= MICROPRICE_MIN_MIDPOINT_MOVE
            or opposite_midpoint_delta <= -MICROPRICE_MIN_MIDPOINT_MOVE
        )
        if not midpoint_confirmed:
            self.rejected_midpoint += 1
            self.last_decision = {
                "marketId": market_id,
                "status": "ACCUMULATING",
                "reason": "selected midpoint has not confirmed direction",
                "score": score,
                "sourceSide": side,
                "confirmationCount": len(self.samples),
                "confirmationDurationMs": duration_ms,
                "midpointDelta": midpoint_delta,
                "oppositeMidpointDelta": opposite_midpoint_delta,
                "strategyVersion": MICROPRICE_VARIANT_VERSION,
            }
            return []

        return self._open_pair(
            snapshot,
            context,
            fee_bps,
            side,
            score,
            duration_ms,
            midpoint_delta,
            opposite_midpoint_delta,
        )

    def state(self) -> dict[str, Any]:
        return {
            "version": MICROPRICE_VARIANT_VERSION,
            "paperOnly": True,
            "liveOrdersAffected": False,
            "strategies": [
                MICROPRICE_CONFIRM_STRATEGY,
                MICROPRICE_REVERSION_STRATEGY,
            ],
            "confirmedPairs": self.confirmed_pairs,
            "currentMarketId": self.market_id,
            "currentDirection": self.direction,
            "currentConfirmations": len(self.samples),
            "lastDecision": self.last_decision,
            "rejections": {
                "staleBook": self.rejected_stale,
                "bookSkew": self.rejected_skew,
                "outsideWindow": self.rejected_window,
                "directionChange": self.rejected_direction_change,
                "midpointNotConfirmed": self.rejected_midpoint,
                "scoreDecay": self.rejected_decay,
                "pairedDepth": self.rejected_depth,
            },
            "rules": {
                "threshold": MICROPRICE_THRESHOLD,
                "minimumConfirmations": MICROPRICE_MIN_CONFIRMATIONS,
                "minimumConfirmationMs": MICROPRICE_MIN_CONFIRMATION_MS,
                "maximumBookAgeMs": MICROPRICE_MAX_BOOK_AGE_MS,
                "maximumBookSkewMs": MICROPRICE_MAX_BOOK_SKEW_MS,
                "minimumMidpointMove": MICROPRICE_MIN_MIDPOINT_MOVE,
                "minimumRetainedStrength": (
                    MICROPRICE_MIN_RETAINED_STRENGTH
                ),
                "stakeUsdtPerVariant": MICROPRICE_STAKE_USDT,
                "slippageBps": MICROPRICE_SLIPPAGE_BPS,
            },
        }


def _wrap_store(engine: Any, store: Any) -> None:
    if getattr(store, "_microprice_variants_wrapped", False):
        engine.microprice_variant_tracker = getattr(
            store,
            "_microprice_variant_tracker",
            None,
        )
        return
    original = getattr(store, "maybe_enter_m_series", None)
    if not callable(original):
        return
    tracker = MicropriceVariantTracker(engine, store)

    @wraps(original)
    def maybe_enter_with_variants(
        snapshot: dict[str, Any],
        fee_bps: int,
        *,
        realtime_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        opened = original(
            snapshot,
            fee_bps,
            realtime_context=realtime_context,
        )
        context = dict(realtime_context or {})
        variants = tracker.process(snapshot, fee_bps, context)
        return [*(opened or []), *variants]

    store.maybe_enter_m_series = maybe_enter_with_variants
    store._microprice_variants_wrapped = True
    store._microprice_variant_tracker = tracker
    engine.microprice_variant_tracker = tracker


def install_microprice_variants() -> None:
    engine_cls = _realtime.MSeriesRealtimeEngine

    original_init = engine_cls.__init__
    if not getattr(original_init, "_microprice_variants", False):

        @wraps(original_init)
        def init_with_microprice_variants(
            self: Any,
            *args: Any,
            **kwargs: Any,
        ) -> None:
            original_init(self, *args, **kwargs)
            _wrap_store(self, self.store)

        init_with_microprice_variants._microprice_variants = True  # type: ignore[attr-defined]
        engine_cls.__init__ = init_with_microprice_variants

    original_state = engine_cls.state
    if not getattr(original_state, "_microprice_variants", False):

        @wraps(original_state)
        def state_with_microprice_variants(self: Any) -> dict[str, Any]:
            state = original_state(self)
            tracker = getattr(self, "microprice_variant_tracker", None)
            state["micropriceVariants"] = (
                tracker.state()
                if isinstance(tracker, MicropriceVariantTracker)
                else {
                    "version": MICROPRICE_VARIANT_VERSION,
                    "paperOnly": True,
                    "liveOrdersAffected": False,
                    "status": "UNAVAILABLE",
                }
            )
            return state

        state_with_microprice_variants._microprice_variants = True  # type: ignore[attr-defined]
        engine_cls.state = state_with_microprice_variants
