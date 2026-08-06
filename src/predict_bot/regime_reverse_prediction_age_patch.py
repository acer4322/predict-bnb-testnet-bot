from __future__ import annotations

import threading
from contextlib import contextmanager
from functools import wraps
from typing import Any, Iterator


REGIME_REVERSE_3L_STRATEGY = "R_FUTURES_LEAD_REGIME_REVERSE_3L"
REGIME_REVERSE_3L_MAX_PREDICTION_BOOK_AGE_MS = 2_500.0

_CONTEXT = threading.local()


def prediction_book_age_limit_ms(
    strategy: Any,
    default_limit_ms: float,
) -> float:
    normalized = str(strategy or "").strip().upper()
    if normalized == REGIME_REVERSE_3L_STRATEGY:
        return max(
            float(default_limit_ms),
            REGIME_REVERSE_3L_MAX_PREDICTION_BOOK_AGE_MS,
        )
    return float(default_limit_ms)


@contextmanager
def _strategy_prediction_age_context(strategy: Any) -> Iterator[None]:
    previous = getattr(_CONTEXT, "strategy", None)
    _CONTEXT.strategy = str(strategy or "").strip().upper()
    try:
        yield
    finally:
        if previous is None:
            try:
                delattr(_CONTEXT, "strategy")
            except AttributeError:
                pass
        else:
            _CONTEXT.strategy = previous


def _current_strategy() -> str:
    return str(getattr(_CONTEXT, "strategy", "") or "").strip().upper()


def install_regime_reverse_prediction_age_patch() -> None:
    from .live_trading import LiveM0WEngine

    current_property = getattr(LiveM0WEngine, "max_prediction_book_age_ms", None)
    if not isinstance(current_property, property):
        def get_limit(self: Any) -> float:
            base = max(
                1.0,
                float(
                    getattr(
                        self,
                        "_base_max_prediction_book_age_ms",
                        2_000.0,
                    )
                ),
            )
            return prediction_book_age_limit_ms(_current_strategy(), base)

        def set_limit(self: Any, value: Any) -> None:
            self._base_max_prediction_book_age_ms = max(1.0, float(value))

        LiveM0WEngine.max_prediction_book_age_ms = property(  # type: ignore[attr-defined]
            get_limit,
            set_limit,
        )

    original = LiveM0WEngine._process_single_signal
    if getattr(original, "_regime_reverse_prediction_age_v1", False):
        return

    @wraps(original)
    def process_single_signal_with_strategy_age(
        self: Any,
        signal: dict[str, Any],
        *,
        defer_placement: bool = False,
        allow_paused_quote_only: bool = False,
    ) -> Any:
        with _strategy_prediction_age_context(signal.get("strategy")):
            return original(
                self,
                signal,
                defer_placement=defer_placement,
                allow_paused_quote_only=allow_paused_quote_only,
            )

    process_single_signal_with_strategy_age._regime_reverse_prediction_age_v1 = True  # type: ignore[attr-defined]
    LiveM0WEngine._process_single_signal = process_single_signal_with_strategy_age
