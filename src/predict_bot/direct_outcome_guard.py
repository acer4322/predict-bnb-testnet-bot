from __future__ import annotations

import time
from functools import wraps
from typing import Any

from . import live_trading as _live
from . import m_realtime as _realtime


DIRECT_OUTCOME_GUARD_VERSION = "DIRECT_OUTCOME_GUARD_V2"
DIRECT_OUTCOME_DATA_SOURCE = "dual_token_rest"
DIRECT_OUTCOME_BLOCK_STATUS = "BLOCKED_NON_DIRECT_PREDICTION_SOURCE"


def is_direct_outcome_event(event: Any) -> bool:
    """Return True only for independently fetched UP and DOWN outcome books."""
    return bool(
        isinstance(event, dict)
        and event.get("prediction_data_source") == DIRECT_OUTCOME_DATA_SOURCE
        and event.get("direct_outcome_books") is True
    )


def is_explicit_non_direct_event(event: Any) -> bool:
    """Fail closed unless the event explicitly proves independent outcomes.

    Missing provenance is unsafe in production because legacy single-outcome
    WSS frames did not always carry ``prediction_data_source`` or
    ``direct_outcome_books``. Treating those fields as optional allowed the old
    ``DOWN = 1 - UP`` compatibility path to reach strategy calculations.
    """
    return not is_direct_outcome_event(event)


def signal_has_direct_outcome_provenance(signal: Any) -> bool:
    return bool(
        isinstance(signal, dict)
        and signal.get("prediction_source_guard_version")
        == DIRECT_OUTCOME_GUARD_VERSION
        and signal.get("signal_prediction_data_source")
        == DIRECT_OUTCOME_DATA_SOURCE
        and signal.get("signal_prediction_direct_outcome_books") is True
    )


def _guarded_live_signal_sink(engine: Any, sink: Any) -> Any:
    if sink is None or getattr(sink, "_direct_outcome_guard", False):
        return sink

    @wraps(sink)
    def guarded(signal: dict[str, Any]) -> Any:
        payload = dict(signal)
        direct = is_direct_outcome_event(
            getattr(engine, "prediction_event", None)
        )
        payload.update(
            {
                "prediction_source_guard_version": (
                    DIRECT_OUTCOME_GUARD_VERSION
                ),
                "signal_prediction_data_source": (
                    DIRECT_OUTCOME_DATA_SOURCE if direct else None
                ),
                "signal_prediction_direct_outcome_books": direct,
                "signal_prediction_price_basis": (
                    "INDEPENDENT_UP_DOWN_TOP_OF_BOOK"
                    if direct
                    else "NON_DIRECT_OR_UNPROVEN"
                ),
            }
        )
        return sink(payload)

    guarded._direct_outcome_guard = True  # type: ignore[attr-defined]
    return guarded


def install_direct_outcome_guard() -> None:
    """Fail closed unless strategy prices come from independent UP/DOWN books.

    Prediction WSS remains available to the microstructure transport-health
    layer. Every non-direct or provenance-missing Prediction event is rejected
    before Market Observer and strategy evaluation because the legacy fallback
    inferred DOWN as ``1 - UP top level``.
    """

    engine_cls = _realtime.MSeriesRealtimeEngine

    original_init = engine_cls.__init__
    if not getattr(original_init, "_direct_outcome_guard", False):

        @wraps(original_init)
        def init_with_guard(self: Any, *args: Any, **kwargs: Any) -> None:
            original_init(self, *args, **kwargs)
            self.rejected_non_direct_prediction_events = 0
            self.last_rejected_non_direct_prediction_at = None
            self.live_signal_sink = _guarded_live_signal_sink(
                self,
                self.live_signal_sink,
            )

        init_with_guard._direct_outcome_guard = True  # type: ignore[attr-defined]
        engine_cls.__init__ = init_with_guard

    original_update_market_observer = engine_cls._update_market_observer
    if not getattr(
        original_update_market_observer, "_direct_outcome_guard", False
    ):

        @wraps(original_update_market_observer)
        def update_market_observer_direct_only(
            self: Any,
            event: dict[str, Any],
        ) -> Any:
            if (
                str(event.get("source") or "") == "prediction"
                and str(event.get("stream") or "") == "orderbook"
                and not is_direct_outcome_event(event)
            ):
                return None
            return original_update_market_observer(self, event)

        update_market_observer_direct_only._direct_outcome_guard = True  # type: ignore[attr-defined]
        engine_cls._update_market_observer = update_market_observer_direct_only

    original_prediction_values = engine_cls._prediction_values
    if not getattr(
        original_prediction_values, "_direct_outcome_guard", False
    ):

        @wraps(original_prediction_values)
        def direct_prediction_values(
            self: Any,
            event: dict[str, Any] | None,
            now_mono_ns: int,
            *,
            require_feature_eligible: bool = True,
        ) -> dict[str, float] | None:
            if not is_direct_outcome_event(event):
                return None
            return original_prediction_values(
                self,
                event,
                now_mono_ns,
                require_feature_eligible=require_feature_eligible,
            )

        direct_prediction_values._direct_outcome_guard = True  # type: ignore[attr-defined]
        engine_cls._prediction_values = direct_prediction_values

    original_prediction_book_copy = engine_cls._prediction_book_copy
    if not getattr(
        original_prediction_book_copy, "_direct_outcome_guard", False
    ):

        @wraps(original_prediction_book_copy)
        def direct_prediction_book_copy(
            self: Any,
            event: dict[str, Any],
            market_id: int | None,
            now_ns: int,
            *,
            require_feature_eligible: bool = True,
        ) -> dict[str, Any] | None:
            if not is_direct_outcome_event(event):
                return None
            return original_prediction_book_copy(
                self,
                event,
                market_id,
                now_ns,
                require_feature_eligible=require_feature_eligible,
            )

        direct_prediction_book_copy._direct_outcome_guard = True  # type: ignore[attr-defined]
        engine_cls._prediction_book_copy = direct_prediction_book_copy

    original_handle = engine_cls._handle
    if not getattr(original_handle, "_direct_outcome_guard", False):

        @wraps(original_handle)
        def handle_direct_outcomes_only(
            self: Any,
            event: dict[str, Any],
        ) -> Any:
            if (
                str(event.get("source") or "") == "prediction"
                and str(event.get("stream") or "") == "orderbook"
                and not is_direct_outcome_event(event)
            ):
                with self.lock:
                    self.rejected_non_direct_prediction_events = int(
                        getattr(
                            self,
                            "rejected_non_direct_prediction_events",
                            0,
                        )
                    ) + 1
                    self.last_rejected_non_direct_prediction_at = (
                        _realtime._utc_iso_from_ns(
                            int(
                                event.get("received_wall_ns")
                                or time.time_ns()
                            )
                        )
                    )
                return None
            return original_handle(self, event)

        handle_direct_outcomes_only._direct_outcome_guard = True  # type: ignore[attr-defined]
        engine_cls._handle = handle_direct_outcomes_only

    original_state = engine_cls.state
    if not getattr(original_state, "_direct_outcome_guard", False):

        @wraps(original_state)
        def state_with_source_guard(self: Any) -> dict[str, Any]:
            state = original_state(self)
            state.update(
                {
                    "predictionStrategySourcePolicy": (
                        "DIRECT_DUAL_TOKEN_REST_ONLY"
                    ),
                    "predictionSourceGuardVersion": (
                        DIRECT_OUTCOME_GUARD_VERSION
                    ),
                    "rejectedNonDirectPredictionEvents": int(
                        getattr(
                            self,
                            "rejected_non_direct_prediction_events",
                            0,
                        )
                    ),
                    "lastRejectedNonDirectPredictionAt": getattr(
                        self,
                        "last_rejected_non_direct_prediction_at",
                        None,
                    ),
                }
            )
            return state

        state_with_source_guard._direct_outcome_guard = True  # type: ignore[attr-defined]
        engine_cls.state = state_with_source_guard

    live_cls = _live.LiveM0WEngine
    original_single_signal = live_cls._process_single_signal
    if not getattr(original_single_signal, "_direct_outcome_guard", False):

        @wraps(original_single_signal)
        def process_direct_source_only(
            self: Any,
            signal: dict[str, Any],
            *args: Any,
            **kwargs: Any,
        ) -> Any:
            strategy = str(signal.get("strategy") or "").strip().upper()
            requires_two_sided_prediction = bool(
                strategy in _live.LIVE_RESEARCH_STRATEGIES
                or strategy == "M01O_F1"
            )
            forwarded_from_realtime = bool(
                signal.get("live_forwarded_from_paper") is True
            )
            if (
                requires_two_sided_prediction
                and forwarded_from_realtime
                and not signal_has_direct_outcome_provenance(signal)
            ):
                self._record_blocked_signal(
                    signal,
                    DIRECT_OUTCOME_BLOCK_STATUS,
                    (
                        "strategy signal did not originate from independent "
                        "current-market UP and DOWN outcome books"
                    ),
                    error_kind="NON_DIRECT_PREDICTION_SOURCE",
                    diagnostics={
                        "predictionSourceGuardVersion": (
                            DIRECT_OUTCOME_GUARD_VERSION
                        ),
                        "requiredPredictionDataSource": (
                            DIRECT_OUTCOME_DATA_SOURCE
                        ),
                        "signalPredictionDataSource": signal.get(
                            "signal_prediction_data_source"
                        ),
                        "signalDirectOutcomeBooks": signal.get(
                            "signal_prediction_direct_outcome_books"
                        ),
                    },
                )
                return None
            return original_single_signal(
                self,
                signal,
                *args,
                **kwargs,
            )

        process_direct_source_only._direct_outcome_guard = True  # type: ignore[attr-defined]
        live_cls._process_single_signal = process_direct_source_only
