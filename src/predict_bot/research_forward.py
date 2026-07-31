from __future__ import annotations

import math
from collections import deque
from datetime import datetime
from statistics import NormalDist
from typing import Any

from .core import taker_fee


PRIMARY_RESEARCH_STRATEGIES = (
    "R_MICROPRICE",
    "R_OFI",
    "R_FUTURES_LEAD",
    "R_CALIBRATED_VALUE",
    "R_CONSENSUS",
)

SHADOW_RESEARCH_STRATEGIES = (
    "R_CALIBRATED_VALUE_CONTINUOUS_V2",
    "R_FUTURES_LEAD_CONTINUOUS_V2",
    "R_FUTURES_LEAD_REVERSE",
    "R_FUTURES_LEAD_REGIME_REVERSE_3L",
    "R_FUTURES_LEAD_EXIT30",
    "R_FUTURES_LEAD_DISTANCE",
    "R_FUTURES_LEAD_EXIT30_DISTANCE",
    "R_OFI_MIN040",
    "R_OFI_EVENT_CUM",
    "R_OFI_EVENT_CUM_FILTERED",
    "R_FUTURES_LEAD_OBSERVER_F1",
    "R_FUTURES_LEAD_OBSERVER_V2",
    "R_FUTURES_LEAD_OBSERVER_V3",
    "R_FUTURES_LEAD_OBSERVER_V4",
    "R_FUTURES_LEAD_OBSERVER_V6",
    "R_OFI_OBSERVER_V3",
    "R_MICROPRICE_OBSERVER_V3",
    "R_MICROPRICE_OBSERVER_V6",
    "R_CALIBRATED_VALUE_OBSERVER_V6",
)

RESEARCH_STRATEGIES = (*PRIMARY_RESEARCH_STRATEGIES, *SHADOW_RESEARCH_STRATEGIES)

CONTINUOUS_CALIBRATION_RULES = {
    "R_CALIBRATED_VALUE_CONTINUOUS_V2": "R_CALIBRATED_VALUE",
    "R_FUTURES_LEAD_CONTINUOUS_V2": "R_FUTURES_LEAD",
}
CONTINUOUS_CALIBRATION_STRATEGIES = tuple(CONTINUOUS_CALIBRATION_RULES)

FUTURES_LEAD_EXPERIMENT_STRATEGIES = (
    "R_FUTURES_LEAD_EXIT30",
    "R_FUTURES_LEAD_DISTANCE",
    "R_FUTURES_LEAD_EXIT30_DISTANCE",
)
FUTURES_LEAD_EXIT_STRATEGIES = (
    "R_FUTURES_LEAD_EXIT30",
    "R_FUTURES_LEAD_EXIT30_DISTANCE",
)
FUTURES_LEAD_DISTANCE_STRATEGIES = (
    "R_FUTURES_LEAD_DISTANCE",
    "R_FUTURES_LEAD_EXIT30_DISTANCE",
)

FUTURES_LEAD_OBSERVER_VERSIONS = ("F1", "V2", "V3", "V4", "V6")
FUTURES_LEAD_OBSERVER_STRATEGIES = tuple(
    f"R_FUTURES_LEAD_OBSERVER_{version}"
    for version in FUTURES_LEAD_OBSERVER_VERSIONS
)
FUTURES_LEAD_OBSERVER_STRATEGY_VERSION = {
    strategy: version
    for strategy, version in zip(
        FUTURES_LEAD_OBSERVER_STRATEGIES,
        FUTURES_LEAD_OBSERVER_VERSIONS,
    )
}
FUTURES_LEAD_LIVE_OBSERVER_STRATEGIES = (
    "R_FUTURES_LEAD",
    "R_FUTURES_LEAD_REVERSE",
    "R_FUTURES_LEAD_REGIME_REVERSE_3L",
)

OBSERVER_COMBINATION_STRATEGY_RULES = {
    "R_OFI_OBSERVER_V3": ("R_OFI", "V3"),
    "R_MICROPRICE_OBSERVER_V3": ("R_MICROPRICE", "V3"),
    "R_MICROPRICE_OBSERVER_V6": ("R_MICROPRICE", "V6"),
    "R_CALIBRATED_VALUE_OBSERVER_V6": ("R_CALIBRATED_VALUE", "V6"),
}
OBSERVER_COMBINATION_STRATEGIES = tuple(
    OBSERVER_COMBINATION_STRATEGY_RULES
)

FUTURES_LEAD_EXPERIMENT_BASE = {
    "horizon": 180.0,
    "lag": 3.0,
    "confirmation_windows": 2.0,
    "entry_delay_seconds": 6.0,
    "min_residual_bps": 0.25,
    "max_source_age_ms": 500.0,
    "max_prediction_age_ms": 1000.0,
    "volatility_lookback_seconds": 60.0,
    "min_volatility_observations": 30.0,
    "min_volatility_span_seconds": 45.0,
    "min_price_changes": 5.0,
    "sample_target_minimum": 100.0,
    "sample_target_preferred": 200.0,
}

RESEARCH_PARAMETERS: dict[str, dict[str, float]] = {
    "R_MICROPRICE": {"horizon": 180.0, "threshold": 0.20, "max_ask": 0.55},
    "R_OFI": {"horizon": 60.0, "lag": 10.0, "threshold": 0.20, "max_ask": 0.85},
    "R_OFI_MIN040": {
        "horizon": 60.0,
        "lag": 10.0,
        "threshold": 0.20,
        "min_ask": 0.40,
        "max_ask": 0.85,
    },
    "R_OFI_EVENT_CUM": {
        "horizon": 60.0,
        "lag": 10.0,
        "window": 10.0,
        "min_events": 3.0,
        "threshold": 0.20,
        "max_ask": 0.85,
    },
    "R_OFI_EVENT_CUM_FILTERED": {
        "horizon": 60.0,
        "lag": 10.0,
        "window": 10.0,
        "min_events": 3.0,
        "threshold": 2.0,
        "min_ask": 0.40,
        "max_ask": 0.69,
    },
    "R_FUTURES_LEAD": {
        "horizon": 180.0,
        "lag": 3.0,
        "min_lead_bps": 0.25,
        "max_ask": 0.55,
    },
    "R_FUTURES_LEAD_CONTINUOUS_V2": {
        "horizon": 180.0,
        "lag": 3.0,
        "min_lead_bps": 0.25,
        "max_ask": 0.55,
        "history_window": 200.0,
        "min_history": 20.0,
        "min_bucket_history": 5.0,
        "prior_strength": 10.0,
        "min_edge": 0.01,
    },
    "R_FUTURES_LEAD_REVERSE": {
        "horizon": 180.0,
        "lag": 3.0,
        "min_lead_bps": 0.25,
        "max_ask": 0.99,
    },
    "R_FUTURES_LEAD_REGIME_REVERSE_3L": {
        "horizon": 180.0,
        "lag": 3.0,
        "min_lead_bps": 0.25,
        "max_ask": 0.99,
        "required_consecutive_lead_losses": 3.0,
    },
    **{
        strategy: {
            "horizon": 180.0,
            "lag": 3.0,
            "min_lead_bps": 0.25,
            "max_ask": 0.55,
        }
        for strategy in FUTURES_LEAD_OBSERVER_STRATEGIES
    },
    "R_OFI_OBSERVER_V3": {
        "horizon": 60.0,
        "lag": 10.0,
        "threshold": 0.20,
        "max_ask": 0.85,
    },
    "R_MICROPRICE_OBSERVER_V3": {
        "horizon": 180.0,
        "threshold": 0.20,
        "max_ask": 0.55,
    },
    "R_MICROPRICE_OBSERVER_V6": {
        "horizon": 180.0,
        "threshold": 0.20,
        "max_ask": 0.55,
    },
    "R_CALIBRATED_VALUE_OBSERVER_V6": {
        "horizon": 60.0,
        "min_edge": 0.01,
        "max_ask": 0.70,
        "beta_0": -0.15376836312439016,
        "beta_1": 0.9895606377583307,
    },
    "R_FUTURES_LEAD_EXIT30": {
        **FUTURES_LEAD_EXPERIMENT_BASE,
        "max_ask": 0.55,
        "exit_after_seconds": 30.0,
        "exit_grace_seconds": 15.0,
    },
    "R_FUTURES_LEAD_DISTANCE": {
        **FUTURES_LEAD_EXPERIMENT_BASE,
        "max_ask": 0.95,
        "min_probability_edge": 0.03,
    },
    "R_FUTURES_LEAD_EXIT30_DISTANCE": {
        **FUTURES_LEAD_EXPERIMENT_BASE,
        "max_ask": 0.95,
        "min_probability_edge": 0.03,
        "exit_after_seconds": 30.0,
        "exit_grace_seconds": 15.0,
    },
    "R_CALIBRATED_VALUE": {
        "horizon": 60.0,
        "min_edge": 0.01,
        "max_ask": 0.70,
        "beta_0": -0.15376836312439016,
        "beta_1": 0.9895606377583307,
    },
    "R_CALIBRATED_VALUE_CONTINUOUS_V2": {
        "horizon": 60.0,
        "min_edge": 0.01,
        "max_ask": 0.70,
        "history_window": 200.0,
        "min_history": 20.0,
        "min_bucket_history": 5.0,
        "prior_strength": 10.0,
    },
    "R_CONSENSUS": {"horizon": 180.0, "lag": 10.0, "votes": 4.0, "max_ask": 0.85},
}


def futures_lead_observer_decision(
    version: str,
    gate: dict[str, Any] | None,
    *,
    expected_market_id: int | None = None,
) -> dict[str, Any]:
    """Evaluate a frozen Futures Lead Observer rule, failing closed."""
    normalized_version = str(version or "").strip().upper()
    result: dict[str, Any] = {
        "version": normalized_version,
        "allowed": False,
        "status": "BLOCK",
        "reason": "Observer gate is unavailable",
    }
    if normalized_version not in FUTURES_LEAD_OBSERVER_VERSIONS:
        result["reason"] = f"Unsupported Observer version: {normalized_version or 'empty'}"
        return result
    if not isinstance(gate, dict):
        return result
    result.update(
        {
            "profile": str(gate.get("profile") or "").upper(),
            "dataQualityStatus": str(
                gate.get("dataQualityStatus") or ""
            ).upper(),
            "historicalSampleCount": gate.get("historicalSampleCount"),
            "currentMarketId": gate.get("currentMarketId"),
            "currentRangeScore": gate.get("currentRangeScore"),
            "currentEffectiveCrossovers": gate.get(
                "currentEffectiveCrossovers"
            ),
            "currentBothSidesTouched": gate.get("currentBothSidesTouched"),
            "currentTrendVeto": gate.get("currentTrendVeto"),
            "currentPhase": gate.get("currentPhase"),
            "currentShortEr": gate.get("currentShortEr"),
            "currentMedianEr60s": gate.get("currentMedianEr60s"),
        }
    )
    if result["profile"] != "F1":
        result["reason"] = "Observer profile must be F1"
        return result
    if result["dataQualityStatus"] != "READY":
        result["reason"] = "Observer data quality is not READY"
        return result
    try:
        sample_count = int(gate["historicalSampleCount"])
        min_samples = int(gate.get("minSettledSamples") or 6)
        gate_market_id = int(gate["currentMarketId"])
    except (KeyError, TypeError, ValueError):
        result["reason"] = "Observer gate is missing required fields"
        return result
    if sample_count < min_samples:
        result["reason"] = f"Observer history {sample_count}/{min_samples} is incomplete"
        return result
    if expected_market_id is not None and gate_market_id != int(expected_market_id):
        result["reason"] = "Observer gate belongs to a different market"
        return result

    trend_veto = gate.get("currentTrendVeto") is True
    try:
        range_score = int(gate.get("currentRangeScore") or 0)
        crossovers = int(gate.get("currentEffectiveCrossovers") or 0)
    except (TypeError, ValueError):
        result["reason"] = "Observer indicators are invalid"
        return result

    if normalized_version == "F1":
        allowed = (
            gate.get("allowed") is True
            and str(gate.get("status") or "").upper() == "ALLOW"
        )
        reason = str(gate.get("reason") or "F1 gate blocked")
    elif normalized_version == "V2":
        allowed = range_score >= 1 and not trend_veto
        reason = "requires range score >= 1 and no current trend veto"
    elif normalized_version == "V3":
        allowed = crossovers >= 2 and not trend_veto
        reason = "requires >= 2 effective crossovers and no current trend veto"
    elif normalized_version == "V4":
        phase = str(gate.get("currentPhase") or "").upper()
        er_value = (
            gate.get("currentShortEr")
            if phase == "EARLY_0_60S"
            else gate.get("currentMedianEr60s")
        )
        try:
            phase_er = float(er_value)
        except (TypeError, ValueError):
            phase_er = math.nan
        result["phaseEr"] = phase_er if math.isfinite(phase_er) else None
        if not math.isfinite(phase_er):
            result["reason"] = "Observer phase ER is unavailable"
            return result
        allowed = phase_er <= 0.35 and not trend_veto
        reason = "requires phase ER <= 0.35 and no current trend veto"
    else:
        allowed = (
            crossovers >= 2
            and not trend_veto
            and gate.get("currentBothSidesTouched") is not True
        )
        reason = (
            "requires >= 2 effective crossovers, no trend veto, "
            "and no dual touch"
        )
    result["allowed"] = bool(allowed)
    result["status"] = "ALLOW" if allowed else "BLOCK"
    result["reason"] = "allowed" if allowed else reason
    return result


def _finite(*values: Any) -> bool:
    try:
        return all(math.isfinite(float(value)) for value in values)
    except (TypeError, ValueError):
        return False


def _timestamp_ns(snapshot: dict[str, Any]) -> int | None:
    try:
        direct = int(snapshot.get("timestamp_ns") or 0)
    except (TypeError, ValueError):
        direct = 0
    if direct > 0:
        return direct
    try:
        parsed = datetime.fromisoformat(str(snapshot["timestamp"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return None
    return int(parsed.timestamp() * 1_000_000_000)


def sample_from_snapshot(snapshot: dict[str, Any]) -> dict[str, float] | None:
    keys = (
        "seconds_left",
        "up_ask",
        "up_bid",
        "down_ask",
        "down_bid",
        "up_ask_size",
        "up_bid_size",
        "down_ask_size",
        "down_bid_size",
        "spot_price",
        "start_price",
    )
    values = [snapshot.get(key) for key in keys]
    timestamp_ns = _timestamp_ns(snapshot)
    if timestamp_ns is None or not _finite(*values):
        return None
    sample = {key: float(snapshot[key]) for key in keys}
    sample["timestamp_ns"] = float(timestamp_ns)
    futures_price = snapshot.get("futures_price")
    sample["futures_price"] = (
        float(futures_price) if _finite(futures_price) and float(futures_price) > 0 else math.nan
    )
    for key in ("spot_age_ms", "futures_age_ms"):
        value = snapshot.get(key)
        sample[key] = float(value) if _finite(value) and float(value) >= 0 else math.nan
    return sample


class ResearchSampleBuffer:
    """Small causal, restart-safe-by-failing-closed rolling signal buffer."""

    def __init__(self) -> None:
        self._markets: dict[int, deque[dict[str, float]]] = {}
        self._event_flows: dict[int, deque[dict[str, float]]] = {}
        self._last_event_samples: dict[int, dict[str, float]] = {}
        self._last_event_keys: dict[int, str] = {}

    def append(self, market_id: int, snapshot: dict[str, Any]) -> dict[str, float] | None:
        sample = sample_from_snapshot(snapshot)
        if sample is None:
            return None
        rows = self._markets.setdefault(market_id, deque(maxlen=4096))
        if rows and rows[-1]["timestamp_ns"] == sample["timestamp_ns"]:
            rows[-1] = sample
        else:
            rows.append(sample)
        cutoff = sample["timestamp_ns"] - 90_000_000_000
        while rows and rows[0]["timestamp_ns"] < cutoff:
            rows.popleft()
        for stale_market in tuple(self._markets):
            if stale_market != market_id:
                del self._markets[stale_market]
        return sample

    def append_event_ofi(
        self,
        market_id: int,
        current: dict[str, float],
        event_key: str,
    ) -> None:
        """Record one normalized OFI contribution per Prediction book event."""
        if self._last_event_keys.get(market_id) == event_key:
            return
        previous = self._last_event_samples.get(market_id)
        self._last_event_keys[market_id] = event_key
        self._last_event_samples[market_id] = current
        rows = self._event_flows.setdefault(market_id, deque(maxlen=4096))
        if previous is not None:
            rows.append(
                {
                    "timestamp_ns": current["timestamp_ns"],
                    "value": _ofi(previous, current, "UP")
                    - _ofi(previous, current, "DOWN"),
                }
            )
        cutoff = current["timestamp_ns"] - 25_000_000_000
        while rows and rows[0]["timestamp_ns"] < cutoff:
            rows.popleft()
        for stale_market in tuple(self._event_flows):
            if stale_market != market_id:
                del self._event_flows[stale_market]
                self._last_event_samples.pop(stale_market, None)
                self._last_event_keys.pop(stale_market, None)

    def cumulative_event_ofi(
        self,
        market_id: int,
        current: dict[str, float],
        window_seconds: float,
        min_events: int,
    ) -> dict[str, float] | None:
        cutoff = current["timestamp_ns"] - window_seconds * 1_000_000_000
        values = [
            row["value"]
            for row in self._event_flows.get(market_id, ())
            if cutoff <= row["timestamp_ns"] <= current["timestamp_ns"]
        ]
        if len(values) < min_events:
            return None
        return {"signal": sum(values), "event_count": float(len(values))}

    def lagged(
        self, market_id: int, current: dict[str, float], lag_seconds: float
    ) -> dict[str, float] | None:
        target = current["timestamp_ns"] - lag_seconds * 1_000_000_000
        selected = None
        for row in self._markets.get(market_id, ()):
            if row["timestamp_ns"] <= target:
                selected = row
            else:
                break
        if selected is None:
            return None
        actual_lag = (current["timestamp_ns"] - selected["timestamp_ns"]) / 1_000_000_000
        return selected if lag_seconds <= actual_lag <= lag_seconds + 2.5 else None

    def realized_volatility_per_sqrt_second(
        self,
        market_id: int,
        current: dict[str, float],
        window_seconds: float,
        min_observations: int,
        min_span_seconds: float,
        min_price_changes: int,
    ) -> dict[str, float] | None:
        """Estimate causal spot log-return volatility without annualization."""
        cutoff = current["timestamp_ns"] - window_seconds * 1_000_000_000
        rows = [
            row
            for row in self._markets.get(market_id, ())
            if cutoff <= row["timestamp_ns"] <= current["timestamp_ns"]
        ]
        if len(rows) < min_observations:
            return None
        span_seconds = (rows[-1]["timestamp_ns"] - rows[0]["timestamp_ns"]) / 1e9
        if span_seconds < min_span_seconds:
            return None
        squared_returns = []
        price_changes = 0
        for previous, latest in zip(rows, rows[1:]):
            value = _log_return(previous["spot_price"], latest["spot_price"])
            if value is None:
                return None
            squared_returns.append(value * value)
            if abs(value) > 1e-15:
                price_changes += 1
        if price_changes < min_price_changes or not squared_returns:
            return None
        variance_rate = sum(squared_returns) / span_seconds
        if not math.isfinite(variance_rate) or variance_rate <= 0:
            return None
        return {
            "sigma_per_sqrt_second": math.sqrt(variance_rate),
            "observation_count": float(len(rows)),
            "price_change_count": float(price_changes),
            "span_seconds": span_seconds,
        }


def sampling_active(seconds_left: float, enabled: set[str]) -> bool:
    for strategy in enabled:
        params = RESEARCH_PARAMETERS[strategy]
        horizon = params["horizon"]
        lag = params.get("lag", 0.0)
        entry_delay = params.get("entry_delay_seconds", 3.0)
        warmup = params.get("volatility_lookback_seconds", 0.0)
        if horizon - entry_delay <= seconds_left <= horizon + warmup + lag + 2.5:
            return True
    return False


def _mid(row: dict[str, float], side: str) -> float:
    prefix = side.lower()
    return (row[f"{prefix}_bid"] + row[f"{prefix}_ask"]) / 2.0


def _imbalance(row: dict[str, float], side: str) -> float:
    prefix = side.lower()
    bid_size = row[f"{prefix}_bid_size"]
    ask_size = row[f"{prefix}_ask_size"]
    total = bid_size + ask_size
    return (bid_size - ask_size) / total if total > 0 else 0.0


def _microprice_score(row: dict[str, float]) -> float:
    return _imbalance(row, "UP") - _imbalance(row, "DOWN")


def _ofi(previous: dict[str, float], current: dict[str, float], side: str) -> float:
    prefix = side.lower()
    old_bid, new_bid = previous[f"{prefix}_bid"], current[f"{prefix}_bid"]
    old_ask, new_ask = previous[f"{prefix}_ask"], current[f"{prefix}_ask"]
    old_bid_size, new_bid_size = (
        previous[f"{prefix}_bid_size"],
        current[f"{prefix}_bid_size"],
    )
    old_ask_size, new_ask_size = (
        previous[f"{prefix}_ask_size"],
        current[f"{prefix}_ask_size"],
    )
    value = (
        (new_bid_size if new_bid >= old_bid else 0.0)
        - (old_bid_size if new_bid <= old_bid else 0.0)
        - (new_ask_size if new_ask <= old_ask else 0.0)
        + (old_ask_size if new_ask >= old_ask else 0.0)
    )
    depth = old_bid_size + new_bid_size + old_ask_size + new_ask_size
    return value / depth if depth > 0 else 0.0


def _log_return(previous: float, current: float) -> float | None:
    if not _finite(previous, current) or previous <= 0 or current <= 0:
        return None
    return math.log(current / previous)


def reverse_futures_lead_signal(
    source_side: str, source_signal: float
) -> dict[str, Any] | None:
    """Derive the shadow direction only from an opened Futures Lead trade."""
    normalized_side = str(source_side).upper()
    if normalized_side not in {"UP", "DOWN"} or not _finite(source_signal):
        return None
    return {
        "side": "DOWN" if normalized_side == "UP" else "UP",
        "signal": -float(source_signal),
        "source_strategy": "R_FUTURES_LEAD",
        "source_side": normalized_side,
        "source_signal": float(source_signal),
        "direction_reversed": True,
    }


def regime_futures_lead_signal(
    source_side: str,
    source_signal: float,
    *,
    reverse_after_three_losses: bool,
) -> dict[str, Any] | None:
    """Follow Lead normally, reversing only under the frozen 3-loss regime."""
    normalized_side = str(source_side).upper()
    if normalized_side not in {"UP", "DOWN"} or not _finite(source_signal):
        return None
    reversed_direction = bool(reverse_after_three_losses)
    return {
        "side": (
            "DOWN" if normalized_side == "UP" else "UP"
        ) if reversed_direction else normalized_side,
        "signal": (
            -float(source_signal) if reversed_direction else float(source_signal)
        ),
        "source_strategy": "R_FUTURES_LEAD",
        "source_side": normalized_side,
        "source_signal": float(source_signal),
        "direction_reversed": reversed_direction,
        "regime_reversed": reversed_direction,
    }


def _fresh_source_sample(row: dict[str, float], max_age_ms: float) -> bool:
    return bool(
        _finite(row.get("spot_age_ms"), row.get("futures_age_ms"))
        and 0 <= float(row["spot_age_ms"]) <= max_age_ms
        and 0 <= float(row["futures_age_ms"]) <= max_age_ms
    )


def _signed_residual_leg(
    previous: dict[str, float],
    current: dict[str, float],
    *,
    min_residual_bps: float,
    max_source_age_ms: float,
) -> dict[str, float | str] | None:
    if not (
        _fresh_source_sample(previous, max_source_age_ms)
        and _fresh_source_sample(current, max_source_age_ms)
    ):
        return None
    spot_return = _log_return(previous["spot_price"], current["spot_price"])
    futures_return = _log_return(previous["futures_price"], current["futures_price"])
    if spot_return is None or futures_return is None or spot_return * futures_return <= 0:
        return None
    residual = futures_return - spot_return
    side = "UP" if futures_return > 0 else "DOWN"
    if residual * futures_return <= 0 or abs(residual) * 10_000 < min_residual_bps:
        return None
    return {
        "side": side,
        "spot_return_bps": spot_return * 10_000,
        "futures_return_bps": futures_return * 10_000,
        "signed_residual_bps": residual * 10_000,
    }


def confirmed_futures_lead_signal(
    current: dict[str, float],
    previous: dict[str, float] | None,
    confirmation_previous: dict[str, float] | None,
    *,
    min_residual_bps: float,
    max_source_age_ms: float,
) -> dict[str, Any] | None:
    """Require two causal, same-direction signed-residual lead windows."""
    if previous is None or confirmation_previous is None:
        return None
    first = _signed_residual_leg(
        confirmation_previous,
        previous,
        min_residual_bps=min_residual_bps,
        max_source_age_ms=max_source_age_ms,
    )
    second = _signed_residual_leg(
        previous,
        current,
        min_residual_bps=min_residual_bps,
        max_source_age_ms=max_source_age_ms,
    )
    if first is None or second is None or first["side"] != second["side"]:
        return None
    return {
        "side": second["side"],
        "signal": float(second["signed_residual_bps"]),
        "signed_residual_bps": float(second["signed_residual_bps"]),
        "confirmation_first": first,
        "confirmation_second": second,
        "confirmation_windows": 2,
        "same_direction_required": True,
    }


def terminal_probability_from_distance(
    current: dict[str, float],
    side: str,
    sigma_per_sqrt_second: float,
) -> dict[str, float] | None:
    """Estimate terminal side probability from strike distance and remaining RV."""
    try:
        spot_price = float(current["spot_price"])
        start_price = float(current["start_price"])
        seconds_left = float(current["seconds_left"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (
        _finite(spot_price, start_price, seconds_left, sigma_per_sqrt_second)
        and spot_price > 0
        and start_price > 0
        and seconds_left > 0
        and sigma_per_sqrt_second > 0
    ):
        return None
    remaining_sigma = sigma_per_sqrt_second * math.sqrt(seconds_left)
    if remaining_sigma <= 0:
        return None
    distance = math.log(spot_price / start_price)
    z_score = distance / remaining_sigma
    up_probability = NormalDist().cdf(z_score)
    probability = up_probability if side == "UP" else 1.0 - up_probability
    return {
        "model_probability": probability,
        "up_probability": up_probability,
        "distance_to_strike_bps": distance * 10_000,
        "remaining_sigma_bps": remaining_sigma * 10_000,
        "distance_z_score": z_score,
    }


def _probability_bucket(probability: float) -> str:
    index = min(9, max(0, int(probability * 10)))
    return f"p{index / 10:.1f}-{(index + 1) / 10:.1f}"


def _lead_strength_bucket(signal: float) -> str:
    strength = abs(signal)
    if strength < 0.50:
        return "lead0.25-0.50bps"
    if strength < 1.00:
        return "lead0.50-1.00bps"
    return "lead1.00+bps"


def continuous_calibration_decision(
    strategy: str,
    *,
    source_side: str,
    source_signal: float,
    source_probability: float | None,
    effective_cost: float,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a causal rolling calibration decision from prior official results.

    The caller must provide only source trades from earlier markets whose
    official outcomes were already available.  Fixed buckets and sample
    thresholds keep this forward test stable while every new settlement can
    update the next market's estimate.
    """
    if strategy not in CONTINUOUS_CALIBRATION_RULES:
        raise ValueError(f"unsupported continuous calibration strategy: {strategy}")
    source_side = str(source_side).upper()
    if source_side not in {"UP", "DOWN"} or not _finite(
        source_signal, effective_cost
    ):
        return {"allowed": False, "reason": "INVALID_SOURCE"}
    params = RESEARCH_PARAMETERS[strategy]
    window = int(params["history_window"])
    usable = []
    for row in history[-window:]:
        try:
            side = str(row["side"]).upper()
            signal = float(row["signal"])
            won = int(row["won"])
            probability = row.get("model_probability")
            probability = float(probability) if probability is not None else None
        except (KeyError, TypeError, ValueError):
            continue
        if side not in {"UP", "DOWN"} or won not in {0, 1} or not _finite(signal):
            continue
        if probability is not None and not (
            _finite(probability) and 0 < probability < 1
        ):
            probability = None
        usable.append(
            {
                "side": side,
                "signal": signal,
                "won": won,
                "model_probability": probability,
                "market_id": row.get("market_id"),
            }
        )

    source_strategy = CONTINUOUS_CALIBRATION_RULES[strategy]
    if source_strategy == "R_CALIBRATED_VALUE":
        if source_probability is None or not _finite(source_probability):
            return {"allowed": False, "reason": "SOURCE_PROBABILITY_UNAVAILABLE"}
        source_probability = min(1 - 1e-6, max(1e-6, float(source_probability)))
        bucket = _probability_bucket(source_probability)
        local = [
            row
            for row in usable
            if row["side"] == source_side
            and row["model_probability"] is not None
            and _probability_bucket(float(row["model_probability"])) == bucket
        ]
        prior_center = source_probability
    else:
        bucket = _lead_strength_bucket(float(source_signal))
        local = [
            row
            for row in usable
            if row["side"] == source_side
            and _lead_strength_bucket(float(row["signal"])) == bucket
        ]
        prior_center = (
            (sum(int(row["won"]) for row in usable) + 2.0)
            / (len(usable) + 4.0)
            if usable
            else 0.5
        )

    local_wins = sum(int(row["won"]) for row in local)
    prior_strength = float(params["prior_strength"])
    calibrated_probability = (
        local_wins + prior_strength * prior_center
    ) / (len(local) + prior_strength)
    calibrated_probability = min(1 - 1e-6, max(1e-6, calibrated_probability))
    calibrated_edge = calibrated_probability - float(effective_cost)
    history_ready = len(usable) >= int(params["min_history"])
    bucket_ready = len(local) >= int(params["min_bucket_history"])
    allowed = bool(
        history_ready
        and bucket_ready
        and calibrated_edge >= float(params["min_edge"])
    )
    reason = (
        "HISTORY_WARMUP"
        if not history_ready
        else "BUCKET_WARMUP"
        if not bucket_ready
        else "CALIBRATED_EDGE_BELOW_MINIMUM"
        if not allowed
        else "ALLOW"
    )
    return {
        "allowed": allowed,
        "reason": reason,
        "source_strategy": source_strategy,
        "source_side": source_side,
        "source_signal": float(source_signal),
        "source_probability": source_probability,
        "calibration_bucket": f"{source_side}:{bucket}",
        "history_samples": len(usable),
        "history_wins": sum(int(row["won"]) for row in usable),
        "bucket_samples": len(local),
        "bucket_wins": local_wins,
        "history_window": window,
        "minimum_history": int(params["min_history"]),
        "minimum_bucket_history": int(params["min_bucket_history"]),
        "prior_center": prior_center,
        "prior_strength": prior_strength,
        "calibrated_probability": calibrated_probability,
        "effective_cost": float(effective_cost),
        "calibrated_edge": calibrated_edge,
        "minimum_edge": float(params["min_edge"]),
        "history_max_market_id": max(
            (
                int(row["market_id"])
                for row in usable
                if row.get("market_id") is not None
            ),
            default=None,
        ),
        "causal_prior_official_only": True,
    }


def signal_for_strategy(
    strategy: str,
    current: dict[str, float],
    previous: dict[str, float] | None,
    *,
    fee_bps: int,
    slippage_bps: float,
    cumulative_event_ofi: dict[str, float] | None = None,
    confirmation_previous: dict[str, float] | None = None,
    volatility: dict[str, float] | None = None,
) -> dict[str, Any] | None:
    params = RESEARCH_PARAMETERS[strategy]
    if strategy in {
        *CONTINUOUS_CALIBRATION_STRATEGIES,
        "R_FUTURES_LEAD_REVERSE",
        "R_FUTURES_LEAD_REGIME_REVERSE_3L",
        *FUTURES_LEAD_OBSERVER_STRATEGIES,
        *OBSERVER_COMBINATION_STRATEGIES,
    }:
        # This shadow is derived from an actual R_FUTURES_LEAD paper entry by
        # the store.  It must never create an independent market signal.
        return None
    if strategy in FUTURES_LEAD_EXPERIMENT_STRATEGIES:
        confirmed = confirmed_futures_lead_signal(
            current,
            previous,
            confirmation_previous,
            min_residual_bps=float(params["min_residual_bps"]),
            max_source_age_ms=float(params["max_source_age_ms"]),
        )
        if confirmed is None:
            return None
        if strategy not in FUTURES_LEAD_DISTANCE_STRATEGIES:
            return confirmed
        if volatility is None:
            return None
        probability = terminal_probability_from_distance(
            current,
            str(confirmed["side"]),
            float(volatility["sigma_per_sqrt_second"]),
        )
        if probability is None:
            return None
        side_key = str(confirmed["side"]).lower()
        entry = current[f"{side_key}_ask"] * (1 + slippage_bps / 10_000)
        edge = (
            float(probability["model_probability"])
            - entry
            - taker_fee(1.0, entry, fee_bps)
        )
        if edge < float(params["min_probability_edge"]):
            return None
        return {
            **confirmed,
            **probability,
            "model_edge": edge,
            "model_sigma": float(probability["remaining_sigma_bps"]),
            "volatility_observation_count": int(volatility["observation_count"]),
            "volatility_price_change_count": int(volatility["price_change_count"]),
            "volatility_span_seconds": float(volatility["span_seconds"]),
        }
    if strategy == "R_MICROPRICE":
        score = _microprice_score(current)
        if abs(score) < params["threshold"]:
            return None
        return {"side": "UP" if score > 0 else "DOWN", "signal": score}

    if strategy == "R_CALIBRATED_VALUE":
        up_mid, down_mid = _mid(current, "UP"), _mid(current, "DOWN")
        probability = up_mid / (up_mid + down_mid)
        bounded = min(1 - 1e-6, max(1e-6, probability))
        z = params["beta_0"] + params["beta_1"] * math.log(bounded / (1 - bounded))
        calibrated = 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))
        choices = []
        for side, chance in (("UP", calibrated), ("DOWN", 1 - calibrated)):
            entry = current[f"{side.lower()}_ask"] * (1 + slippage_bps / 10_000)
            edge = chance - entry - taker_fee(1.0, entry, fee_bps)
            choices.append((edge, side))
        edge, side = max(choices)
        if edge < params["min_edge"]:
            return None
        return {
            "side": side,
            "signal": edge,
            "model_probability": calibrated if side == "UP" else 1 - calibrated,
            "model_edge": edge,
        }

    if strategy in {"R_OFI_EVENT_CUM", "R_OFI_EVENT_CUM_FILTERED"}:
        if cumulative_event_ofi is None:
            return None
        score = float(cumulative_event_ofi["signal"])
        if abs(score) < params["threshold"]:
            return None
        return {
            "side": "UP" if score > 0 else "DOWN",
            "signal": score,
            "event_ofi_count": int(cumulative_event_ofi["event_count"]),
        }

    if previous is None:
        return None
    spot_return = _log_return(previous["spot_price"], current["spot_price"])
    futures_return = _log_return(previous["futures_price"], current["futures_price"])
    ofi_score = _ofi(previous, current, "UP") - _ofi(previous, current, "DOWN")
    up_mid_change = _mid(current, "UP") - _mid(previous, "UP")

    if strategy in {"R_OFI", "R_OFI_MIN040"}:
        if abs(ofi_score) < params["threshold"]:
            return None
        return {"side": "UP" if ofi_score > 0 else "DOWN", "signal": ofi_score}

    if futures_return is None or spot_return is None:
        return None
    if strategy == "R_FUTURES_LEAD":
        lead = abs(futures_return) - abs(spot_return)
        if abs(futures_return) < 1e-12 or lead * 10_000 < params["min_lead_bps"]:
            return None
        source_side = "UP" if futures_return > 0 else "DOWN"
        source_signal = math.copysign(lead * 10_000, futures_return)
        return {
            "side": source_side,
            "signal": source_signal,
        }

    signals = [_microprice_score(current), ofi_score, futures_return, spot_return, up_mid_change]
    up_votes = sum(value > 0 for value in signals)
    down_votes = sum(value < 0 for value in signals)
    if max(up_votes, down_votes) < int(params["votes"]):
        return None
    return {
        "side": "UP" if up_votes > down_votes else "DOWN",
        "signal": float(up_votes - down_votes),
        "votes": signals,
    }


def execution_candidate(
    strategy: str,
    signal: dict[str, Any],
    current: dict[str, float],
    snapshot: dict[str, Any],
    *,
    stake: float,
    minimum_stake: float,
    slippage_bps: float,
    max_spread: float,
    min_entry: float,
    max_book_age_ms: float,
    max_book_skew_ms: float,
) -> dict[str, Any] | None:
    params = RESEARCH_PARAMETERS[strategy]
    side = str(signal["side"])
    prefix = side.lower()
    raw_ask, bid = current[f"{prefix}_ask"], current[f"{prefix}_bid"]
    ask_size = current[f"{prefix}_ask_size"]
    book_age, book_skew = snapshot.get("book_age_ms"), snapshot.get("book_skew_ms")
    if not (
        stake >= minimum_stake
        and max(min_entry, params.get("min_ask", min_entry))
        <= raw_ask
        <= params["max_ask"]
        and 0 <= raw_ask - bid <= max_spread
        and _finite(book_age, book_skew)
        and 0 <= float(book_age) <= max_book_age_ms
        and 0 <= float(book_skew) <= max_book_skew_ms
    ):
        return None
    entry = raw_ask * (1 + slippage_bps / 10_000)
    if not 0 < entry < 1:
        return None
    requested_shares = stake / entry
    if ask_size + 1e-12 < requested_shares:
        return None
    return {
        **signal,
        "entry": entry,
        "raw_ask": raw_ask,
        "bid": bid,
        "visible_size": ask_size,
        "requested_stake": stake,
        "requested_shares": requested_shares,
        "filled_stake": stake,
        "filled_shares": requested_shares,
        "fill_ratio": 1.0,
        "partial_fill": False,
        "book_age_ms": float(book_age),
        "book_skew_ms": float(book_skew),
        "visible_notional_at_simulated_entry": ask_size * entry,
    }
