from __future__ import annotations

import math
from functools import wraps
from typing import Any

from . import m_realtime as _realtime
from .core import taker_fee


SOURCE_STRATEGY = "R_CALIBRATED_VALUE"
IMMEDIATE_CONTROL_STRATEGY = "R_CALIBRATED_VALUE_IMMEDIATE_CONTROL"
CONFIRM_V2_STRATEGY = "R_CALIBRATED_VALUE_CONFIRM_V2"
CONFIRM_V2_REVERSE_STRATEGY = "R_CALIBRATED_VALUE_CONFIRM_V2_REVERSE"
CALIBRATED_VALUE_CONFIRMATION_VERSION = "CALIBRATED_VALUE_CONFIRMATION_V2"

CALIBRATED_VALUE_CONFIRMATION_STRATEGIES = (
    IMMEDIATE_CONTROL_STRATEGY,
    CONFIRM_V2_STRATEGY,
    CONFIRM_V2_REVERSE_STRATEGY,
)

CALIBRATED_BETA_0 = -0.15376836312439016
CALIBRATED_BETA_1 = 0.9895606377583307
INITIAL_MIN_NET_EDGE = 0.015
FINAL_MIN_NET_EDGE = 0.010
MIN_CONFIRMATIONS = 2
MIN_CONFIRMATION_MS = 150.0
MAX_CONFIRMATION_MS = 1200.0
MAX_BOOK_AGE_MS = 500.0
MAX_BOOK_SKEW_MS = 150.0
MAX_SPREAD = 0.03
MIN_CHOSEN_MIDPOINT_MOVE = 0.005
MIN_RETAINED_EDGE_RATIO = 0.65
MAX_FOLLOW_ASK = 0.70
STAKE_USDT_PER_VARIANT = 5.0
SLIPPAGE_BPS = 50.0
WINDOW_MIN_SECONDS_LEFT = 55.0
WINDOW_MAX_SECONDS_LEFT = 61.0
DIRECT_OUTCOME_DATA_SOURCE = "dual_token_rest"

_ACTIVE_TRACKER: CalibratedValueConfirmationTracker | None = None


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _opposite(side: str) -> str:
    return "DOWN" if side == "UP" else "UP"


def _midpoint(snapshot: dict[str, Any], side: str) -> float | None:
    prefix = side.lower()
    bid = _finite(snapshot.get(f"{prefix}_bid"))
    ask = _finite(snapshot.get(f"{prefix}_ask"))
    if bid is None or ask is None or not 0 <= bid <= ask <= 1:
        return None
    return (bid + ask) / 2.0


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


def _calibrated_up_probability(snapshot: dict[str, Any]) -> float | None:
    up_mid = _midpoint(snapshot, "UP")
    down_mid = _midpoint(snapshot, "DOWN")
    if up_mid is None or down_mid is None or up_mid + down_mid <= 0:
        return None
    probability = up_mid / (up_mid + down_mid)
    bounded = min(1 - 1e-6, max(1e-6, probability))
    z = CALIBRATED_BETA_0 + CALIBRATED_BETA_1 * math.log(
        bounded / (1 - bounded)
    )
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))


def _execution(
    snapshot: dict[str, Any],
    side: str,
    *,
    maximum_ask: float,
) -> dict[str, float] | None:
    prefix = side.lower()
    ask = _finite(snapshot.get(f"{prefix}_ask"))
    bid = _finite(snapshot.get(f"{prefix}_bid"))
    ask_size = _finite(snapshot.get(f"{prefix}_ask_size"))
    if (
        ask is None
        or bid is None
        or ask_size is None
        or not 0 < ask <= maximum_ask
        or not 0 <= bid <= ask
        or ask_size <= 0
        or ask - bid > MAX_SPREAD
    ):
        return None
    entry = ask * (1.0 + SLIPPAGE_BPS / 10_000.0)
    if not 0 < entry < 1:
        return None
    requested_shares = STAKE_USDT_PER_VARIANT / entry
    if ask_size + 1e-12 < requested_shares:
        return None
    return {
        "ask": ask,
        "bid": bid,
        "ask_size": ask_size,
        "entry": entry,
        "requested_shares": requested_shares,
        "midpoint": (bid + ask) / 2.0,
    }


def _candidate(
    snapshot: dict[str, Any],
    fee_bps: int,
) -> dict[str, float | str] | None:
    up_probability = _calibrated_up_probability(snapshot)
    if up_probability is None:
        return None
    choices: list[dict[str, float | str]] = []
    for side, probability in (
        ("UP", up_probability),
        ("DOWN", 1.0 - up_probability),
    ):
        execution = _execution(
            snapshot,
            side,
            maximum_ask=MAX_FOLLOW_ASK,
        )
        if execution is None:
            continue
        entry = float(execution["entry"])
        fee_cost = taker_fee(1.0, entry, int(fee_bps))
        edge = float(probability) - entry - fee_cost
        choices.append(
            {
                **execution,
                "side": side,
                "probability": float(probability),
                "edge": edge,
                "fee_cost_per_stake": fee_cost,
                "up_probability": up_probability,
            }
        )
    if not choices:
        return None
    return max(choices, key=lambda item: float(item["edge"]))


class CalibratedValueConfirmationTracker:
    def __init__(self, engine: Any, store: Any) -> None:
        self.engine = engine
        self.store = store
        self.market_id: int | None = None
        self.initial: dict[str, Any] | None = None
        self.samples: list[dict[str, Any]] = []
        self.last_event_sequence: str | None = None
        self.immediate_markets: set[int] = set()
        self.confirmed_markets: set[int] = set()
        self.terminal_markets: set[int] = set()
        self.immediate_controls = 0
        self.confirmed_pairs = 0
        self.rejected_window = 0
        self.rejected_stale = 0
        self.rejected_skew = 0
        self.rejected_initial_edge = 0
        self.rejected_direction_change = 0
        self.rejected_timeout = 0
        self.rejected_midpoint = 0
        self.rejected_final_edge = 0
        self.rejected_retention = 0
        self.rejected_depth = 0
        self.last_decision: dict[str, Any] | None = None

    def _reset_market(self, market_id: int) -> None:
        if self.market_id == market_id:
            return
        self.market_id = market_id
        self.initial = None
        self.samples = []
        self.last_event_sequence = None

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

    def _common_diagnostics(
        self,
        *,
        snapshot: dict[str, Any],
        context: dict[str, Any],
        fee_bps: int,
        pair_id: str,
    ) -> dict[str, Any]:
        return {
            "paper_only": True,
            "live_orders_affected": False,
            "forward_only": True,
            "paired_counterfactual": True,
            "pair_id": pair_id,
            "source_strategy": SOURCE_STRATEGY,
            "strategy_version": CALIBRATED_VALUE_CONFIRMATION_VERSION,
            "prediction_data_source": context.get("prediction_data_source"),
            "direct_outcome_books": True,
            "signal_event_sequence": context.get("signal_event_sequence"),
            "signal_timestamp": snapshot.get("timestamp"),
            "book_age_ms": _finite(snapshot.get("book_age_ms")),
            "book_skew_ms": _finite(snapshot.get("book_skew_ms")),
            "fee_bps": int(fee_bps),
            "slippage_bps": SLIPPAGE_BPS,
            "rule": {
                "minimumInitialNetEdge": INITIAL_MIN_NET_EDGE,
                "minimumFinalNetEdge": FINAL_MIN_NET_EDGE,
                "minimumConfirmations": MIN_CONFIRMATIONS,
                "minimumConfirmationMs": MIN_CONFIRMATION_MS,
                "maximumConfirmationMs": MAX_CONFIRMATION_MS,
                "maximumBookAgeMs": MAX_BOOK_AGE_MS,
                "maximumBookSkewMs": MAX_BOOK_SKEW_MS,
                "maximumSpread": MAX_SPREAD,
                "minimumChosenMidpointMove": MIN_CHOSEN_MIDPOINT_MOVE,
                "minimumRetainedEdgeRatio": MIN_RETAINED_EDGE_RATIO,
                "maximumFollowAsk": MAX_FOLLOW_ASK,
                "windowSecondsLeft": [
                    WINDOW_MIN_SECONDS_LEFT,
                    WINDOW_MAX_SECONDS_LEFT,
                ],
                "stakeUsdtPerVariant": STAKE_USDT_PER_VARIANT,
                "slippageBps": SLIPPAGE_BPS,
                "beta0": CALIBRATED_BETA_0,
                "beta1": CALIBRATED_BETA_1,
            },
        }

    def _open_immediate(
        self,
        snapshot: dict[str, Any],
        context: dict[str, Any],
        fee_bps: int,
        candidate: dict[str, float | str],
        received_monotonic_ns: int,
        sequence: str,
    ) -> list[dict[str, Any]]:
        market_id = int(snapshot["market_id"])
        side = str(candidate["side"])
        pair_id = f"{CALIBRATED_VALUE_CONFIRMATION_VERSION}:{market_id}"
        if (
            market_id in self.immediate_markets
            or self._trade_exists(IMMEDIATE_CONTROL_STRATEGY, market_id)
        ):
            self.immediate_markets.add(market_id)
            return []

        diagnostics = {
            **self._common_diagnostics(
                snapshot=snapshot,
                context=context,
                fee_bps=fee_bps,
                pair_id=pair_id,
            ),
            "variant_mode": "IMMEDIATE_INITIAL_EDGE_CONTROL",
            "selected_side": side,
            "model_probability": float(candidate["probability"]),
            "model_edge": float(candidate["edge"]),
            "up_probability": float(candidate["up_probability"]),
            "raw_top_ask": float(candidate["ask"]),
            "raw_top_bid": float(candidate["bid"]),
            "visible_ask_size": float(candidate["ask_size"]),
            "requested_shares": float(candidate["requested_shares"]),
            "initial_event_sequence": sequence,
            "initial_received_monotonic_ns": received_monotonic_ns,
            "initial_midpoint": float(candidate["midpoint"]),
        }
        self.store.open_trade(
            strategy=IMMEDIATE_CONTROL_STRATEGY,
            topic_id=int(snapshot["topic_id"]),
            market_id=market_id,
            side=side,
            entry=float(candidate["entry"]),
            target=None,
            stake=STAKE_USDT_PER_VARIANT,
            fee_rate_bps=int(fee_bps),
            note=(
                f"{IMMEDIATE_CONTROL_STRATEGY} forward-only paper control; "
                "first qualifying calibrated edge; never live-forwarded"
            ),
            strategy_version=CALIBRATED_VALUE_CONFIRMATION_VERSION,
            model_probability=float(candidate["probability"]),
            model_edge=float(candidate["edge"]),
            model_sigma=None,
            diagnostics=diagnostics,
        )
        self.immediate_markets.add(market_id)
        self.immediate_controls += 1
        self.initial = {
            "side": side,
            "edge": float(candidate["edge"]),
            "probability": float(candidate["probability"]),
            "up_probability": float(candidate["up_probability"]),
            "entry": float(candidate["entry"]),
            "ask": float(candidate["ask"]),
            "midpoint": float(candidate["midpoint"]),
            "received_monotonic_ns": received_monotonic_ns,
            "sequence": sequence,
            "timestamp": snapshot.get("timestamp"),
        }
        self.samples = [
            {
                "sequence": sequence,
                "received_monotonic_ns": received_monotonic_ns,
                "side": side,
                "edge": float(candidate["edge"]),
                "probability": float(candidate["probability"]),
                "midpoint": float(candidate["midpoint"]),
                "book_age_ms": _finite(snapshot.get("book_age_ms")),
                "book_skew_ms": _finite(snapshot.get("book_skew_ms")),
            }
        ]
        self.last_decision = {
            "marketId": market_id,
            "status": "IMMEDIATE_OPENED",
            "reason": "initial calibrated edge qualified",
            "sourceSide": side,
            "initialEdge": float(candidate["edge"]),
            "confirmationCount": 1,
            "strategyVersion": CALIBRATED_VALUE_CONFIRMATION_VERSION,
        }
        return [
            {
                "strategy": IMMEDIATE_CONTROL_STRATEGY,
                "topic_id": int(snapshot["topic_id"]),
                "market_id": market_id,
                "side": side,
                "entry_price": float(candidate["entry"]),
                "raw_top_ask": float(candidate["ask"]),
                "stake": STAKE_USDT_PER_VARIANT,
                "seconds_left": float(snapshot["seconds_left"]),
                "book_age_ms": snapshot.get("book_age_ms"),
                "fee_bps": int(fee_bps),
                "signal_timestamp": str(snapshot.get("timestamp") or ""),
                "paper_only": True,
                "live_orders_affected": False,
                "market_data_integrity_ok": True,
                "research_signal": float(candidate["edge"]),
                "calibrated_value_confirmation_variant": True,
                "variant_mode": "IMMEDIATE_INITIAL_EDGE_CONTROL",
            }
        ]

    def _open_confirmed_pair(
        self,
        snapshot: dict[str, Any],
        context: dict[str, Any],
        fee_bps: int,
        candidate: dict[str, float | str],
        duration_ms: float,
        midpoint_delta: float,
        retained_edge_ratio: float,
    ) -> list[dict[str, Any]]:
        if self.initial is None:
            return []
        market_id = int(snapshot["market_id"])
        follow_side = str(candidate["side"])
        reverse_side = _opposite(follow_side)
        follow = _execution(
            snapshot,
            follow_side,
            maximum_ask=MAX_FOLLOW_ASK,
        )
        reverse = _execution(
            snapshot,
            reverse_side,
            maximum_ask=0.999999,
        )
        if follow is None or reverse is None:
            self.rejected_depth += 1
            self.last_decision = {
                "marketId": market_id,
                "status": "BLOCK",
                "reason": "paired confirmation depth or spread unavailable",
                "sourceSide": follow_side,
                "finalEdge": float(candidate["edge"]),
                "confirmationCount": len(self.samples),
                "confirmationDurationMs": duration_ms,
                "strategyVersion": CALIBRATED_VALUE_CONFIRMATION_VERSION,
            }
            return []
        if (
            market_id in self.confirmed_markets
            or self._trade_exists(CONFIRM_V2_STRATEGY, market_id)
            or self._trade_exists(CONFIRM_V2_REVERSE_STRATEGY, market_id)
        ):
            self.confirmed_markets.add(market_id)
            return []

        pair_id = f"{CALIBRATED_VALUE_CONFIRMATION_VERSION}:{market_id}"
        samples = [dict(item) for item in self.samples]
        common = {
            **self._common_diagnostics(
                snapshot=snapshot,
                context=context,
                fee_bps=fee_bps,
                pair_id=pair_id,
            ),
            "initial_side": self.initial["side"],
            "initial_edge": self.initial["edge"],
            "initial_probability": self.initial["probability"],
            "initial_entry": self.initial["entry"],
            "initial_ask": self.initial["ask"],
            "initial_midpoint": self.initial["midpoint"],
            "initial_event_sequence": self.initial["sequence"],
            "initial_timestamp": self.initial["timestamp"],
            "final_side": follow_side,
            "final_edge": float(candidate["edge"]),
            "final_probability": float(candidate["probability"]),
            "final_up_probability": float(candidate["up_probability"]),
            "retained_edge_ratio": retained_edge_ratio,
            "confirmation_count": len(self.samples),
            "confirmation_duration_ms": duration_ms,
            "chosen_midpoint_delta": midpoint_delta,
            "samples": samples,
        }
        up_probability = float(candidate["up_probability"])
        opened: list[dict[str, Any]] = []
        for strategy, side, execution, mode in (
            (
                CONFIRM_V2_STRATEGY,
                follow_side,
                follow,
                "FOLLOW_CONFIRMED_REPRICING",
            ),
            (
                CONFIRM_V2_REVERSE_STRATEGY,
                reverse_side,
                reverse,
                "REVERSE_CONFIRMED_REPRICING",
            ),
        ):
            probability = (
                up_probability if side == "UP" else 1.0 - up_probability
            )
            entry = float(execution["entry"])
            edge = probability - entry - taker_fee(1.0, entry, int(fee_bps))
            diagnostics = {
                **common,
                "variant_mode": mode,
                "selected_side": side,
                "model_probability": probability,
                "model_edge": edge,
                "raw_top_ask": float(execution["ask"]),
                "raw_top_bid": float(execution["bid"]),
                "visible_ask_size": float(execution["ask_size"]),
                "requested_shares": float(execution["requested_shares"]),
            }
            self.store.open_trade(
                strategy=strategy,
                topic_id=int(snapshot["topic_id"]),
                market_id=market_id,
                side=side,
                entry=entry,
                target=None,
                stake=STAKE_USDT_PER_VARIANT,
                fee_rate_bps=int(fee_bps),
                note=(
                    f"{strategy} paired forward-only paper experiment; "
                    "direct dual-token REST only; never live-forwarded"
                ),
                strategy_version=CALIBRATED_VALUE_CONFIRMATION_VERSION,
                model_probability=probability,
                model_edge=edge,
                model_sigma=None,
                diagnostics=diagnostics,
            )
            opened.append(
                {
                    "strategy": strategy,
                    "topic_id": int(snapshot["topic_id"]),
                    "market_id": market_id,
                    "side": side,
                    "entry_price": entry,
                    "raw_top_ask": float(execution["ask"]),
                    "stake": STAKE_USDT_PER_VARIANT,
                    "seconds_left": float(snapshot["seconds_left"]),
                    "book_age_ms": snapshot.get("book_age_ms"),
                    "fee_bps": int(fee_bps),
                    "signal_timestamp": str(snapshot.get("timestamp") or ""),
                    "paper_only": True,
                    "live_orders_affected": False,
                    "market_data_integrity_ok": True,
                    "research_signal": edge,
                    "calibrated_value_confirmation_variant": True,
                    "variant_mode": mode,
                }
            )

        self.confirmed_markets.add(market_id)
        self.confirmed_pairs += 1
        self.last_decision = {
            "marketId": market_id,
            "status": "OPENED_CONFIRMED_PAIR",
            "reason": "same-side repricing confirmed and edge retained",
            "sourceSide": follow_side,
            "confirmSide": follow_side,
            "reverseSide": reverse_side,
            "initialEdge": self.initial["edge"],
            "finalEdge": float(candidate["edge"]),
            "retainedEdgeRatio": retained_edge_ratio,
            "confirmationCount": len(self.samples),
            "confirmationDurationMs": duration_ms,
            "chosenMidpointDelta": midpoint_delta,
            "strategyVersion": CALIBRATED_VALUE_CONFIRMATION_VERSION,
        }
        return opened

    def process(
        self,
        snapshot: dict[str, Any],
        fee_bps: int,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        try:
            config = self.store.config()
        except Exception:
            config = {}
        if not bool(config.get("strategy_r_calibrated_value_enabled", True)):
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
        if received_monotonic_ns <= 0:
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

        if market_id in self.terminal_markets or market_id in self.confirmed_markets:
            return []

        if not (
            WINDOW_MIN_SECONDS_LEFT
            <= seconds_left
            <= WINDOW_MAX_SECONDS_LEFT
        ):
            self.rejected_window += 1
            self.last_decision = {
                "marketId": market_id,
                "status": "WAITING",
                "reason": "outside calibrated value confirmation window",
                "secondsLeft": seconds_left,
                "strategyVersion": CALIBRATED_VALUE_CONFIRMATION_VERSION,
            }
            return []

        book_age_ms = _finite(snapshot.get("book_age_ms"))
        book_skew_ms = _finite(snapshot.get("book_skew_ms"))
        if (
            book_age_ms is None
            or book_age_ms < 0
            or book_age_ms > MAX_BOOK_AGE_MS
        ):
            self.rejected_stale += 1
            self.last_decision = {
                "marketId": market_id,
                "status": "BLOCK",
                "reason": "book age exceeds confirmation limit",
                "bookAgeMs": book_age_ms,
                "strategyVersion": CALIBRATED_VALUE_CONFIRMATION_VERSION,
            }
            return []
        if (
            book_skew_ms is None
            or book_skew_ms < 0
            or book_skew_ms > MAX_BOOK_SKEW_MS
        ):
            self.rejected_skew += 1
            self.last_decision = {
                "marketId": market_id,
                "status": "BLOCK",
                "reason": "book skew exceeds confirmation limit",
                "bookSkewMs": book_skew_ms,
                "strategyVersion": CALIBRATED_VALUE_CONFIRMATION_VERSION,
            }
            return []

        candidate = _candidate(snapshot, int(fee_bps))
        if self.initial is None:
            if candidate is None or float(candidate["edge"]) < INITIAL_MIN_NET_EDGE:
                self.rejected_initial_edge += 1
                self.last_decision = {
                    "marketId": market_id,
                    "status": "WAITING",
                    "reason": "initial calibrated net edge below threshold",
                    "initialEdge": (
                        float(candidate["edge"]) if candidate is not None else None
                    ),
                    "strategyVersion": CALIBRATED_VALUE_CONFIRMATION_VERSION,
                }
                return []
            return self._open_immediate(
                snapshot,
                context,
                int(fee_bps),
                candidate,
                received_monotonic_ns,
                sequence,
            )

        duration_ms = max(
            0.0,
            (
                received_monotonic_ns
                - int(self.initial["received_monotonic_ns"])
            )
            / 1_000_000,
        )
        if duration_ms > MAX_CONFIRMATION_MS:
            self.rejected_timeout += 1
            self.terminal_markets.add(market_id)
            self.last_decision = {
                "marketId": market_id,
                "status": "EXPIRED",
                "reason": "confirmation timeout reached",
                "confirmationDurationMs": duration_ms,
                "strategyVersion": CALIBRATED_VALUE_CONFIRMATION_VERSION,
            }
            return []

        if candidate is None or str(candidate["side"]) != self.initial["side"]:
            self.rejected_direction_change += 1
            self.terminal_markets.add(market_id)
            self.last_decision = {
                "marketId": market_id,
                "status": "REJECTED",
                "reason": "best calibrated direction changed before confirmation",
                "initialSide": self.initial["side"],
                "finalSide": (
                    str(candidate["side"]) if candidate is not None else None
                ),
                "strategyVersion": CALIBRATED_VALUE_CONFIRMATION_VERSION,
            }
            return []

        self.samples.append(
            {
                "sequence": sequence,
                "received_monotonic_ns": received_monotonic_ns,
                "side": str(candidate["side"]),
                "edge": float(candidate["edge"]),
                "probability": float(candidate["probability"]),
                "midpoint": float(candidate["midpoint"]),
                "book_age_ms": book_age_ms,
                "book_skew_ms": book_skew_ms,
            }
        )
        self.samples = self.samples[-12:]

        if len(self.samples) < MIN_CONFIRMATIONS or duration_ms < MIN_CONFIRMATION_MS:
            self.last_decision = {
                "marketId": market_id,
                "status": "ACCUMULATING",
                "reason": "waiting for distinct confirmation events",
                "sourceSide": self.initial["side"],
                "confirmationCount": len(self.samples),
                "confirmationDurationMs": duration_ms,
                "strategyVersion": CALIBRATED_VALUE_CONFIRMATION_VERSION,
            }
            return []

        midpoint_delta = float(candidate["midpoint"]) - float(
            self.initial["midpoint"]
        )
        if midpoint_delta < MIN_CHOSEN_MIDPOINT_MOVE:
            self.rejected_midpoint += 1
            self.last_decision = {
                "marketId": market_id,
                "status": "ACCUMULATING",
                "reason": "chosen midpoint has not repriced enough",
                "sourceSide": self.initial["side"],
                "confirmationCount": len(self.samples),
                "confirmationDurationMs": duration_ms,
                "chosenMidpointDelta": midpoint_delta,
                "strategyVersion": CALIBRATED_VALUE_CONFIRMATION_VERSION,
            }
            return []

        final_edge = float(candidate["edge"])
        if final_edge < FINAL_MIN_NET_EDGE:
            self.rejected_final_edge += 1
            self.terminal_markets.add(market_id)
            self.last_decision = {
                "marketId": market_id,
                "status": "REJECTED",
                "reason": "final calibrated net edge below threshold",
                "initialEdge": self.initial["edge"],
                "finalEdge": final_edge,
                "strategyVersion": CALIBRATED_VALUE_CONFIRMATION_VERSION,
            }
            return []

        initial_edge = float(self.initial["edge"])
        retained_edge_ratio = (
            final_edge / initial_edge if initial_edge > 0 else 0.0
        )
        if retained_edge_ratio < MIN_RETAINED_EDGE_RATIO:
            self.rejected_retention += 1
            self.terminal_markets.add(market_id)
            self.last_decision = {
                "marketId": market_id,
                "status": "REJECTED",
                "reason": "calibrated edge decayed before confirmation",
                "initialEdge": initial_edge,
                "finalEdge": final_edge,
                "retainedEdgeRatio": retained_edge_ratio,
                "strategyVersion": CALIBRATED_VALUE_CONFIRMATION_VERSION,
            }
            return []

        return self._open_confirmed_pair(
            snapshot,
            context,
            int(fee_bps),
            candidate,
            duration_ms,
            midpoint_delta,
            retained_edge_ratio,
        )

    def state(self) -> dict[str, Any]:
        initial = self.initial or {}
        return {
            "version": CALIBRATED_VALUE_CONFIRMATION_VERSION,
            "paperOnly": True,
            "liveOrdersAffected": False,
            "forwardOnly": True,
            "sourceStrategy": SOURCE_STRATEGY,
            "strategies": list(CALIBRATED_VALUE_CONFIRMATION_STRATEGIES),
            "immediateControls": self.immediate_controls,
            "confirmedPairs": self.confirmed_pairs,
            "currentMarketId": self.market_id,
            "currentInitialSide": initial.get("side"),
            "currentInitialEdge": initial.get("edge"),
            "currentInitialMidpoint": initial.get("midpoint"),
            "currentConfirmations": len(self.samples),
            "lastDecision": self.last_decision,
            "rejections": {
                "outsideWindow": self.rejected_window,
                "staleBook": self.rejected_stale,
                "bookSkew": self.rejected_skew,
                "initialEdge": self.rejected_initial_edge,
                "directionChange": self.rejected_direction_change,
                "confirmationTimeout": self.rejected_timeout,
                "midpointNotConfirmed": self.rejected_midpoint,
                "finalEdge": self.rejected_final_edge,
                "edgeRetention": self.rejected_retention,
                "pairedDepth": self.rejected_depth,
            },
            "rules": {
                "minimumInitialNetEdge": INITIAL_MIN_NET_EDGE,
                "minimumFinalNetEdge": FINAL_MIN_NET_EDGE,
                "minimumConfirmations": MIN_CONFIRMATIONS,
                "minimumConfirmationMs": MIN_CONFIRMATION_MS,
                "maximumConfirmationMs": MAX_CONFIRMATION_MS,
                "maximumBookAgeMs": MAX_BOOK_AGE_MS,
                "maximumBookSkewMs": MAX_BOOK_SKEW_MS,
                "maximumSpread": MAX_SPREAD,
                "minimumChosenMidpointMove": MIN_CHOSEN_MIDPOINT_MOVE,
                "minimumRetainedEdgeRatio": MIN_RETAINED_EDGE_RATIO,
                "maximumFollowAsk": MAX_FOLLOW_ASK,
                "windowSecondsLeft": [
                    WINDOW_MIN_SECONDS_LEFT,
                    WINDOW_MAX_SECONDS_LEFT,
                ],
                "stakeUsdtPerVariant": STAKE_USDT_PER_VARIANT,
                "slippageBps": SLIPPAGE_BPS,
                "beta0": CALIBRATED_BETA_0,
                "beta1": CALIBRATED_BETA_1,
            },
        }


def _database_state(
    store: Any,
    tracker: CalibratedValueConfirmationTracker | None,
) -> dict[str, Any]:
    placeholders = ",".join(
        "?" for _ in CALIBRATED_VALUE_CONFIRMATION_STRATEGIES
    )
    try:
        rows = store.db.execute(
            f"""SELECT id, strategy, market_id, side, status, entry_price,
                       stake, pnl, opened_at, closed_at
                  FROM trades
                 WHERE strategy IN ({placeholders})
                 ORDER BY id ASC""",
            CALIBRATED_VALUE_CONFIRMATION_STRATEGIES,
        ).fetchall()
    except Exception:
        rows = []
    normalized = [dict(row) for row in rows]
    strategies: dict[str, dict[str, Any]] = {}
    modes = {
        IMMEDIATE_CONTROL_STRATEGY: "IMMEDIATE_INITIAL_EDGE_CONTROL",
        CONFIRM_V2_STRATEGY: "FOLLOW_CONFIRMED_REPRICING",
        CONFIRM_V2_REVERSE_STRATEGY: "REVERSE_CONFIRMED_REPRICING",
    }
    for strategy in CALIBRATED_VALUE_CONFIRMATION_STRATEGIES:
        selected = [
            row for row in normalized
            if str(row.get("strategy")) == strategy
        ]
        settled = [
            row for row in selected
            if str(row.get("status")) in {"SETTLED_WIN", "SETTLED_LOSS"}
        ]
        wins = sum(
            str(row.get("status")) == "SETTLED_WIN" for row in settled
        )
        strategies[strategy] = {
            "trades": len(selected),
            "open": sum(
                str(row.get("status")) == "OPEN" for row in selected
            ),
            "settled": len(settled),
            "wins": int(wins),
            "losses": len(settled) - int(wins),
            "winRate": float(wins) / len(settled) if settled else None,
            "realizedPnl": sum(
                float(row.get("pnl") or 0.0) for row in settled
            ),
            "averageEntryPrice": (
                sum(float(row["entry_price"]) for row in selected)
                / len(selected)
                if selected else None
            ),
            "mode": modes[strategy],
        }

    by_market: dict[int, dict[str, dict[str, Any]]] = {}
    for row in normalized:
        by_market.setdefault(int(row["market_id"]), {})[
            str(row["strategy"])
        ] = row
    immediate_markets = sum(
        IMMEDIATE_CONTROL_STRATEGY in items for items in by_market.values()
    )
    paired_markets = sum(
        CONFIRM_V2_STRATEGY in items
        and CONFIRM_V2_REVERSE_STRATEGY in items
        for items in by_market.values()
    )
    complete_cohorts = [
        {
            "marketId": market_id,
            "immediate": items[IMMEDIATE_CONTROL_STRATEGY],
            "confirm": items[CONFIRM_V2_STRATEGY],
            "reverse": items[CONFIRM_V2_REVERSE_STRATEGY],
        }
        for market_id, items in sorted(by_market.items())
        if all(
            strategy in items
            for strategy in CALIBRATED_VALUE_CONFIRMATION_STRATEGIES
        )
    ]
    runtime = (
        tracker.state()
        if isinstance(tracker, CalibratedValueConfirmationTracker)
        else {
            "version": CALIBRATED_VALUE_CONFIRMATION_VERSION,
            "paperOnly": True,
            "liveOrdersAffected": False,
            "forwardOnly": True,
            "status": "UNAVAILABLE",
            "rules": {
                "minimumInitialNetEdge": INITIAL_MIN_NET_EDGE,
                "minimumFinalNetEdge": FINAL_MIN_NET_EDGE,
                "minimumConfirmations": MIN_CONFIRMATIONS,
                "minimumConfirmationMs": MIN_CONFIRMATION_MS,
                "maximumConfirmationMs": MAX_CONFIRMATION_MS,
                "maximumBookAgeMs": MAX_BOOK_AGE_MS,
                "maximumBookSkewMs": MAX_BOOK_SKEW_MS,
                "minimumChosenMidpointMove": MIN_CHOSEN_MIDPOINT_MOVE,
                "minimumRetainedEdgeRatio": MIN_RETAINED_EDGE_RATIO,
                "windowSecondsLeft": [
                    WINDOW_MIN_SECONDS_LEFT,
                    WINDOW_MAX_SECONDS_LEFT,
                ],
                "stakeUsdtPerVariant": STAKE_USDT_PER_VARIANT,
                "slippageBps": SLIPPAGE_BPS,
            },
        }
    )
    return {
        "version": CALIBRATED_VALUE_CONFIRMATION_VERSION,
        "paperOnly": True,
        "liveOrdersAffected": False,
        "forwardOnly": True,
        "sourceStrategy": SOURCE_STRATEGY,
        "pairingRule": (
            "immediate control opens on the first qualifying event; follow and "
            "reverse open together on the same later confirmation event"
        ),
        "immediateMarkets": int(immediate_markets),
        "pairedMarkets": int(paired_markets),
        "completeCohorts": len(complete_cohorts),
        "strategies": strategies,
        "recentCohorts": complete_cohorts[-20:][::-1],
        "runtime": runtime,
    }


def _inject_dashboard(
    payload: dict[str, Any],
    store: Any,
    tracker: CalibratedValueConfirmationTracker | None,
) -> dict[str, Any]:
    experiment = _database_state(store, tracker)
    research = payload.get("researchForward")
    if isinstance(research, dict):
        research["calibratedValueConfirmationExperiment"] = experiment
        strategies = research.get("strategies")
        if isinstance(strategies, dict):
            try:
                source_enabled = bool(
                    store.config().get(
                        "strategy_r_calibrated_value_enabled",
                        True,
                    )
                )
            except Exception:
                source_enabled = True
            for strategy, stats in experiment["strategies"].items():
                strategies[strategy] = {
                    "enabled": source_enabled,
                    "stakeUsdt": STAKE_USDT_PER_VARIANT,
                    "selectedBacktestParameters": {
                        **experiment["runtime"].get("rules", {}),
                        "variantMode": stats.get("mode"),
                        "sourceStrategy": SOURCE_STRATEGY,
                        "pairedOnly": strategy != IMMEDIATE_CONTROL_STRATEGY,
                        "forwardOnly": True,
                    },
                    "chronologicalValidation": {
                        "status": (
                            "ANALYZABLE"
                            if int(stats.get("settled") or 0) >= 30
                            else "COLLECTING"
                        ),
                        "samples": int(stats.get("trades") or 0),
                        "settled": int(stats.get("settled") or 0),
                        "wins": int(stats.get("wins") or 0),
                        "losses": int(stats.get("losses") or 0),
                        "realizedPnl": float(
                            stats.get("realizedPnl") or 0.0
                        ),
                        "minimum": 30,
                        "fixedCohort": True,
                    },
                    "paperOnly": True,
                    "liveOrdersAffected": False,
                }

    summaries = payload.get("summaries")
    if isinstance(summaries, dict):
        for strategy, stats in experiment["strategies"].items():
            summaries[strategy] = {
                "trades": int(stats.get("trades") or 0),
                "open": int(stats.get("open") or 0),
                "wins": int(stats.get("wins") or 0),
                "losses": int(stats.get("losses") or 0),
                "realized_pnl": float(stats.get("realizedPnl") or 0.0),
            }
    return payload


def _wrap_dashboard(store_class: type[Any]) -> None:
    original = getattr(store_class, "dashboard", None)
    if not callable(original):
        return
    if getattr(original, "_calibrated_value_confirmation_dashboard_v2", False):
        return

    @wraps(original)
    def dashboard_with_calibrated_value_confirmation(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = original(self, *args, **kwargs)
        if not isinstance(payload, dict):
            return payload
        tracker = _ACTIVE_TRACKER
        return _inject_dashboard(
            payload,
            self,
            tracker
            if isinstance(tracker, CalibratedValueConfirmationTracker)
            else None,
        )

    dashboard_with_calibrated_value_confirmation._calibrated_value_confirmation_dashboard_v2 = True  # type: ignore[attr-defined]
    store_class.dashboard = dashboard_with_calibrated_value_confirmation


def _wrap_store(engine: Any, store: Any) -> None:
    global _ACTIVE_TRACKER
    if getattr(store, "_calibrated_value_confirmation_wrapped_v2", False):
        tracker = getattr(
            store,
            "_calibrated_value_confirmation_tracker_v2",
            None,
        )
        if isinstance(tracker, CalibratedValueConfirmationTracker):
            tracker.engine = engine
            _ACTIVE_TRACKER = tracker
        engine.calibrated_value_confirmation_tracker = tracker
        _wrap_dashboard(type(store))
        return

    original = getattr(store, "maybe_enter_m_series", None)
    if not callable(original):
        return
    tracker = CalibratedValueConfirmationTracker(engine, store)

    @wraps(original)
    def maybe_enter_with_calibrated_value_confirmation(
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
        variants = tracker.process(
            snapshot,
            int(fee_bps),
            dict(realtime_context or {}),
        )
        return [*(opened or []), *variants]

    store.maybe_enter_m_series = maybe_enter_with_calibrated_value_confirmation
    store._calibrated_value_confirmation_wrapped_v2 = True
    store._calibrated_value_confirmation_tracker_v2 = tracker
    engine.calibrated_value_confirmation_tracker = tracker
    _ACTIVE_TRACKER = tracker
    _wrap_dashboard(type(store))


def install_calibrated_value_confirmation_variants() -> None:
    engine_class = _realtime.MSeriesRealtimeEngine
    original_init = engine_class.__init__
    if not getattr(
        original_init,
        "_calibrated_value_confirmation_variants_v2",
        False,
    ):

        @wraps(original_init)
        def init_with_calibrated_value_confirmation(
            self: Any,
            *args: Any,
            **kwargs: Any,
        ) -> None:
            original_init(self, *args, **kwargs)
            _wrap_store(self, self.store)

        init_with_calibrated_value_confirmation._calibrated_value_confirmation_variants_v2 = True  # type: ignore[attr-defined]
        engine_class.__init__ = init_with_calibrated_value_confirmation

    original_state = engine_class.state
    if not getattr(
        original_state,
        "_calibrated_value_confirmation_variants_v2",
        False,
    ):

        @wraps(original_state)
        def state_with_calibrated_value_confirmation(
            self: Any,
        ) -> dict[str, Any]:
            state = original_state(self)
            tracker = getattr(
                self,
                "calibrated_value_confirmation_tracker",
                None,
            )
            state["calibratedValueConfirmationVariants"] = (
                tracker.state()
                if isinstance(tracker, CalibratedValueConfirmationTracker)
                else {
                    "version": CALIBRATED_VALUE_CONFIRMATION_VERSION,
                    "paperOnly": True,
                    "liveOrdersAffected": False,
                    "forwardOnly": True,
                    "status": "UNAVAILABLE",
                }
            )
            return state

        state_with_calibrated_value_confirmation._calibrated_value_confirmation_variants_v2 = True  # type: ignore[attr-defined]
        engine_class.state = state_with_calibrated_value_confirmation
