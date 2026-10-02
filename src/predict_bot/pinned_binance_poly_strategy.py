from __future__ import annotations

import json
import math
import os
from typing import Any

from . import poly_gap_live as base


ENTRY_MODE_POLY_GAP = "POLY_GAP"
ENTRY_MODE_PINNED_DIVERGENCE = "PINNED_DIVERGENCE"
PINNED_STRATEGY_NAME = "R_PINNED_BINANCE_POLY_DIVERGENCE"
POLY_GAP_STRATEGY_NAME = "R_POLY_GAP_SCALP"
MULTI_OBSERVER_URL = os.environ.get(
    "PREDICT_MULTI_PREDICTION_STATE_URL",
    "http://127.0.0.1:8770/state",
)

DEFAULT_PIN_CENTER = 0.50
DEFAULT_PIN_HALF_WIDTH = 0.03
DEFAULT_PIN_DURATION_SECONDS = 5.0
DEFAULT_PIN_MAX_RANGE = 0.04
DEFAULT_PIN_REQUIRED_RATIO = 0.80
DEFAULT_PIN_POLY_THRESHOLD = 0.85
DEFAULT_PIN_MIN_GAP = 0.30
DEFAULT_PIN_MIN_REMAINING_SECONDS = 20.0
DEFAULT_PIN_MAX_SELECTED_ASK = 0.60
DEFAULT_PIN_ONE_ENTRY_PER_MARKET = True
MAX_OBSERVER_AGE_MS = max(
    500,
    int(os.environ.get("PREDICT_PINNED_DIVERGENCE_MAX_OBSERVER_AGE_MS", "2500")),
)


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    number = _finite(value)
    return int(number) if number is not None else None


def _bool_text(value: bool) -> str:
    return "1" if value else "0"


class PinnedBinancePolyDivergenceMixin:
    """Gate new BUYs on a sustained Binance 0.5/0.5 pin plus strong Poly bias.

    The mixin deliberately does not implement a second execution stack.  Once the
    signal is admitted it falls through to the inherited live executor, preserving
    direct Binance book validation, signed quote/post-quote edge checks, V40 exits,
    V42 TAKE_PROFIT market lock, V43 freshness and V44 execution idempotency.

    Position management is never gated by the pinned detector: an already-open
    position continues to receive the normal inherited Poly reversal/TP exits.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._last_pinned_evaluation: dict[str, Any] | None = None
        self._active_pinned_candidate: dict[str, Any] | None = None
        self._pinned_trigger_count = 0
        self._pinned_direct_book_blocks = 0
        super().__init__(*args, **kwargs)

    def _create_schema(self) -> None:
        super()._create_schema()
        with self.db_lock:
            columns = {
                str(row["name"])
                for row in self.db.execute("PRAGMA table_info(poly_gap_live_rounds)").fetchall()
            }
            if "entry_strategy" not in columns:
                self.db.execute("ALTER TABLE poly_gap_live_rounds ADD COLUMN entry_strategy TEXT")
            if "entry_context_json" not in columns:
                self.db.execute("ALTER TABLE poly_gap_live_rounds ADD COLUMN entry_context_json TEXT")
            self.db.execute(
                """CREATE INDEX IF NOT EXISTS idx_poly_gap_live_rounds_entry_strategy
                     ON poly_gap_live_rounds(market_id, entry_strategy, id)"""
            )
            self.db.commit()

    def _ensure_defaults(self) -> None:
        super()._ensure_defaults()
        defaults = {
            "entry_strategy_mode": ENTRY_MODE_POLY_GAP,
            "pinned_center": f"{DEFAULT_PIN_CENTER:.8f}",
            "pinned_half_width": f"{DEFAULT_PIN_HALF_WIDTH:.8f}",
            "pinned_duration_seconds": f"{DEFAULT_PIN_DURATION_SECONDS:.3f}",
            "pinned_max_range": f"{DEFAULT_PIN_MAX_RANGE:.8f}",
            "pinned_required_ratio": f"{DEFAULT_PIN_REQUIRED_RATIO:.8f}",
            "pinned_poly_threshold": f"{DEFAULT_PIN_POLY_THRESHOLD:.8f}",
            "pinned_min_gap": f"{DEFAULT_PIN_MIN_GAP:.8f}",
            "pinned_min_remaining_seconds": f"{DEFAULT_PIN_MIN_REMAINING_SECONDS:.3f}",
            "pinned_max_selected_ask": f"{DEFAULT_PIN_MAX_SELECTED_ASK:.8f}",
            "pinned_one_entry_per_market": _bool_text(DEFAULT_PIN_ONE_ENTRY_PER_MARKET),
        }
        now = base._now_ms()
        with self.db_lock:
            for key, value in defaults.items():
                self.db.execute(
                    "INSERT OR IGNORE INTO poly_gap_live_settings(key,value,updated_at_ms) VALUES(?,?,?)",
                    (key, value, now),
                )
            self.db.commit()

    def _settings(self) -> dict[str, Any]:
        settings = super()._settings()
        mode = str(self._setting("entry_strategy_mode", ENTRY_MODE_POLY_GAP)).strip().upper()
        if mode not in {ENTRY_MODE_POLY_GAP, ENTRY_MODE_PINNED_DIVERGENCE}:
            mode = ENTRY_MODE_POLY_GAP
        settings.update(
            {
                "entryStrategyMode": mode,
                "pinnedBinanceCenter": float(self._setting("pinned_center", str(DEFAULT_PIN_CENTER))),
                "pinnedBinanceHalfWidth": float(self._setting("pinned_half_width", str(DEFAULT_PIN_HALF_WIDTH))),
                "pinnedMinimumDurationSeconds": float(
                    self._setting("pinned_duration_seconds", str(DEFAULT_PIN_DURATION_SECONDS))
                ),
                "pinnedMaximumRange": float(self._setting("pinned_max_range", str(DEFAULT_PIN_MAX_RANGE))),
                "pinnedRequiredRatio": float(
                    self._setting("pinned_required_ratio", str(DEFAULT_PIN_REQUIRED_RATIO))
                ),
                "pinnedPolyThreshold": float(
                    self._setting("pinned_poly_threshold", str(DEFAULT_PIN_POLY_THRESHOLD))
                ),
                "pinnedMinimumGap": float(self._setting("pinned_min_gap", str(DEFAULT_PIN_MIN_GAP))),
                "pinnedMinimumRemainingSeconds": float(
                    self._setting("pinned_min_remaining_seconds", str(DEFAULT_PIN_MIN_REMAINING_SECONDS))
                ),
                "pinnedMaximumSelectedAsk": float(
                    self._setting("pinned_max_selected_ask", str(DEFAULT_PIN_MAX_SELECTED_ASK))
                ),
                "pinnedOneEntryPerMarket": self._setting(
                    "pinned_one_entry_per_market", _bool_text(DEFAULT_PIN_ONE_ENTRY_PER_MARKET)
                ) == "1",
            }
        )
        return settings

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        extra = {
            "entryStrategyMode",
            "pinnedBinanceCenter",
            "pinnedBinanceHalfWidth",
            "pinnedMinimumDurationSeconds",
            "pinnedMaximumRange",
            "pinnedRequiredRatio",
            "pinnedPolyThreshold",
            "pinnedMinimumGap",
            "pinnedMinimumRemainingSeconds",
            "pinnedMaximumSelectedAsk",
            "pinnedOneEntryPerMarket",
        }
        forwarded = {key: value for key, value in values.items() if key not in extra}
        if forwarded:
            super().update_settings(forwarded)

        current = self._settings()
        mode = str(values.get("entryStrategyMode", current["entryStrategyMode"])).strip().upper()
        if mode not in {ENTRY_MODE_POLY_GAP, ENTRY_MODE_PINNED_DIVERGENCE}:
            raise ValueError("entryStrategyMode must be POLY_GAP or PINNED_DIVERGENCE")

        def number(name: str, fallback: float, low: float, high: float) -> float:
            raw = values.get(name, fallback)
            try:
                parsed = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be a number") from exc
            if not math.isfinite(parsed) or not low <= parsed <= high:
                raise ValueError(f"{name} must be between {low:g} and {high:g}")
            return parsed

        center = number("pinnedBinanceCenter", float(current["pinnedBinanceCenter"]), 0.10, 0.90)
        half_width = number("pinnedBinanceHalfWidth", float(current["pinnedBinanceHalfWidth"]), 0.005, 0.20)
        duration = number(
            "pinnedMinimumDurationSeconds", float(current["pinnedMinimumDurationSeconds"]), 1.0, 120.0
        )
        max_range = number("pinnedMaximumRange", float(current["pinnedMaximumRange"]), 0.001, 0.30)
        required_ratio = number("pinnedRequiredRatio", float(current["pinnedRequiredRatio"]), 0.50, 1.0)
        poly_threshold = number("pinnedPolyThreshold", float(current["pinnedPolyThreshold"]), 0.55, 0.99)
        min_gap = number("pinnedMinimumGap", float(current["pinnedMinimumGap"]), 0.01, 0.80)
        min_remaining = number(
            "pinnedMinimumRemainingSeconds", float(current["pinnedMinimumRemainingSeconds"]), 1.0, 299.0
        )
        max_selected_ask = number(
            "pinnedMaximumSelectedAsk", float(current["pinnedMaximumSelectedAsk"]), 0.05, 0.99
        )
        one_entry = bool(values.get("pinnedOneEntryPerMarket", current["pinnedOneEntryPerMarket"]))
        if center - half_width <= 0.0 or center + half_width >= 1.0:
            raise ValueError("pinnedBinanceCenter ± pinnedBinanceHalfWidth must stay inside (0, 1)")

        updates = {
            "entry_strategy_mode": mode,
            "pinned_center": f"{center:.8f}",
            "pinned_half_width": f"{half_width:.8f}",
            "pinned_duration_seconds": f"{duration:.3f}",
            "pinned_max_range": f"{max_range:.8f}",
            "pinned_required_ratio": f"{required_ratio:.8f}",
            "pinned_poly_threshold": f"{poly_threshold:.8f}",
            "pinned_min_gap": f"{min_gap:.8f}",
            "pinned_min_remaining_seconds": f"{min_remaining:.3f}",
            "pinned_max_selected_ask": f"{max_selected_ask:.8f}",
            "pinned_one_entry_per_market": _bool_text(one_entry),
        }
        changed = False
        for key, value in updates.items():
            setting_name = {
                "entry_strategy_mode": "entryStrategyMode",
                "pinned_center": "pinnedBinanceCenter",
                "pinned_half_width": "pinnedBinanceHalfWidth",
                "pinned_duration_seconds": "pinnedMinimumDurationSeconds",
                "pinned_max_range": "pinnedMaximumRange",
                "pinned_required_ratio": "pinnedRequiredRatio",
                "pinned_poly_threshold": "pinnedPolyThreshold",
                "pinned_min_gap": "pinnedMinimumGap",
                "pinned_min_remaining_seconds": "pinnedMinimumRemainingSeconds",
                "pinned_max_selected_ask": "pinnedMaximumSelectedAsk",
                "pinned_one_entry_per_market": "pinnedOneEntryPerMarket",
            }[key]
            if setting_name in values:
                self._set_setting(key, value)
                changed = True
        if changed:
            self._event(
                "INFO",
                "PINNED_DIVERGENCE_SETTINGS_UPDATED",
                None,
                None,
                f"entry mode={mode}; pin={center:.3f}±{half_width:.3f}; duration={duration:.1f}s; "
                f"Poly>={poly_threshold:.3f}; gap>={min_gap:.3f}",
            )
        return self.snapshot()

    def _asset_name_for_pin(self) -> str:
        value = str(getattr(self, "asset", "BTC") or "BTC").strip().upper()
        return value if value in {"BTC", "ETH", "BNB"} else "BTC"

    def _observer_asset_state_for_pin(self) -> dict[str, Any] | None:
        asset = self._asset_name_for_pin()
        cached = getattr(self, "_last_asset_observer_state", None)
        if asset != "BTC" and isinstance(cached, dict):
            return cached
        try:
            response = self.http.get(MULTI_OBSERVER_URL)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        root = payload.get("state") if isinstance(payload.get("state"), dict) else payload
        assets = root.get("assets") if isinstance(root, dict) else None
        state = assets.get(asset) if isinstance(assets, dict) else None
        return state if isinstance(state, dict) else None

    def _pinned_market_already_traded(self, market_id: int) -> bool:
        with self.db_lock:
            row = self.db.execute(
                """SELECT 1 FROM poly_gap_live_rounds
                    WHERE market_id=? AND entry_strategy=?
                      AND state NOT IN ('REJECTED','FAILED')
                    LIMIT 1""",
                (int(market_id), PINNED_STRATEGY_NAME),
            ).fetchone()
        return row is not None

    def _evaluate_pinned_divergence(self, poly_state: dict[str, Any]) -> dict[str, Any]:
        now_ms = base._now_ms()
        settings = self._settings()
        market = dict(self.market_cache or {})
        market_id = int(market.get("market_id") or 0)
        asset = self._asset_name_for_pin()
        result: dict[str, Any] = {
            "strategy": PINNED_STRATEGY_NAME,
            "asset": asset,
            "checkedAtMs": now_ms,
            "marketId": market_id or None,
            "allowed": False,
            "state": "WAITING_DATA",
            "reason": "observer state unavailable",
        }
        state = self._observer_asset_state_for_pin()
        if not isinstance(state, dict):
            return result

        comparison = _record(state.get("comparison"))
        binance = _record(state.get("binance"))
        poly = _record(state.get("poly"))
        trajectory = state.get("trajectory")
        if not isinstance(trajectory, list):
            trajectory = []

        window_end_ms = _integer(state.get("windowEndMs")) or 0
        market_end_ms = int(market.get("end_ms") or 0)
        result["windowEndMs"] = window_end_ms or None
        result["marketEndMs"] = market_end_ms or None
        if market_end_ms and window_end_ms and abs(market_end_ms - window_end_ms) > base.MAX_MARKET_END_SKEW_MS:
            result.update(state="BLOCKED_MARKET_MISMATCH", reason="8770 and execution market windows do not match")
            return result

        observed_ms = _integer(binance.get("observedAtMs"))
        result["binanceObserverAgeMs"] = max(0, now_ms - observed_ms) if observed_ms else None
        if str(binance.get("status") or "").upper() != "LIVE":
            result.update(state="WAITING_BINANCE", reason="Binance Prediction observer is not LIVE")
            return result
        if observed_ms is None or now_ms - observed_ms > MAX_OBSERVER_AGE_MS:
            result.update(state="WAITING_BINANCE_FRESHNESS", reason="Binance observer snapshot is stale")
            return result
        if str(poly.get("status") or "").upper() != "LIVE":
            result.update(state="WAITING_POLY", reason="Polymarket observer is not LIVE")
            return result

        center = float(settings["pinnedBinanceCenter"])
        half_width = float(settings["pinnedBinanceHalfWidth"])
        duration_s = float(settings["pinnedMinimumDurationSeconds"])
        max_range = float(settings["pinnedMaximumRange"])
        required_ratio = float(settings["pinnedRequiredRatio"])
        min_remaining = float(settings["pinnedMinimumRemainingSeconds"])
        threshold = float(settings["pinnedPolyThreshold"])
        min_gap = float(settings["pinnedMinimumGap"])

        seconds_left = _finite(state.get("secondsLeft"))
        if seconds_left is None and market_end_ms:
            seconds_left = max(0.0, (market_end_ms - now_ms) / 1000.0)
        result["secondsLeft"] = seconds_left
        if seconds_left is None or seconds_left < min_remaining:
            result.update(state="WAITING_REMAINING_TIME", reason=f"requires >= {min_remaining:.1f}s remaining")
            return result

        up_mid = _finite(comparison.get("binanceUpMid"))
        down_mid = _finite(comparison.get("binanceDownMid"))
        result["binanceUpMid"] = up_mid
        result["binanceDownMid"] = down_mid
        if up_mid is None or down_mid is None:
            result.update(state="WAITING_BINANCE_MID", reason="Binance UP/DOWN mids unavailable")
            return result

        cutoff = now_ms - int(duration_s * 1000.0)
        samples: list[tuple[int, float, float]] = []
        for raw in trajectory:
            if not isinstance(raw, dict):
                continue
            at_ms = _integer(raw.get("sampledAtMs"))
            up = _finite(raw.get("binanceUp"))
            down = _finite(raw.get("binanceDown"))
            if at_ms is None or at_ms < cutoff or up is None or down is None:
                continue
            samples.append((at_ms, up, down))
        samples.sort(key=lambda item: item[0])
        min_samples = max(3, int(math.ceil(duration_s * 0.60)))
        span_ms = samples[-1][0] - samples[0][0] if len(samples) >= 2 else 0
        result["pinSamples"] = len(samples)
        result["pinSpanMs"] = span_ms
        if len(samples) < min_samples or span_ms < max(0, int(duration_s * 1000.0) - 1500):
            result.update(
                state="PINNING",
                reason=f"needs {duration_s:.1f}s sustained pin history",
                pinDurationMs=span_ms,
            )
            return result

        pinned_flags = [
            abs(up - center) <= half_width and abs(down - center) <= half_width
            for _, up, down in samples
        ]
        pin_ratio = sum(1 for value in pinned_flags if value) / len(pinned_flags)
        up_values = [item[1] for item in samples]
        down_values = [item[2] for item in samples]
        up_range = max(up_values) - min(up_values)
        down_range = max(down_values) - min(down_values)
        latest_pinned = abs(up_mid - center) <= half_width and abs(down_mid - center) <= half_width
        result.update(
            pinDurationMs=span_ms,
            pinRatio=pin_ratio,
            binanceUpRange=up_range,
            binanceDownRange=down_range,
            pinCenter=center,
            pinHalfWidth=half_width,
            pinMaximumRange=max_range,
        )
        if not latest_pinned:
            result.update(state="WAITING_BINANCE_UNPINNED", reason="latest Binance mids left the configured pin band")
            return result
        if pin_ratio + 1e-12 < required_ratio:
            result.update(state="PINNING", reason=f"pin ratio {pin_ratio:.3f} below {required_ratio:.3f}")
            return result
        if up_range > max_range + 1e-12 or down_range > max_range + 1e-12:
            result.update(state="PINNING", reason="Binance range is too wide for a stable pin")
            return result

        direction = str(poly_state.get("direction") or "").upper()
        selected_poly = _finite(poly_state.get("selectedMid"))
        if direction not in {"UP", "DOWN"} or selected_poly is None:
            result.update(state="WAITING_POLY_DIRECTION", reason="fresh Poly direction is unavailable")
            return result
        result["direction"] = direction
        result["polySelected"] = selected_poly
        if selected_poly + 1e-12 < threshold:
            result.update(state="WAITING_STRONG_POLY", reason=f"Poly selected {selected_poly:.3f} below {threshold:.3f}")
            return result

        selected_binance_mid = up_mid if direction == "UP" else down_mid
        gap = selected_poly - selected_binance_mid
        result["binanceSelectedMid"] = selected_binance_mid
        result["divergenceGap"] = gap
        if gap + 1e-12 < min_gap:
            result.update(state="WAITING_DIVERGENCE", reason=f"Poly-Binance gap {gap:.3f} below {min_gap:.3f}")
            return result

        if bool(settings["pinnedOneEntryPerMarket"]) and market_id > 0 and self._pinned_market_already_traded(market_id):
            result.update(state="BLOCKED_ALREADY_TRADED", reason="pinned strategy already has a non-rejected entry in this market")
            return result

        result.update(
            allowed=True,
            state="ARMED",
            reason="sustained Binance pin + strong fresh Poly divergence confirmed",
        )
        return result

    def _poly_state(self) -> dict[str, Any] | None:
        poly_state = super()._poly_state()
        if not isinstance(poly_state, dict):
            return None
        settings = self._settings()
        if settings["entryStrategyMode"] != ENTRY_MODE_PINNED_DIVERGENCE:
            self._active_pinned_candidate = None
            return poly_state

        # Never interfere with an existing position's inherited exit management.
        if self._current_active_round() is not None:
            return poly_state

        evaluation = self._evaluate_pinned_divergence(poly_state)
        self._last_pinned_evaluation = dict(evaluation)
        if evaluation.get("allowed") is True:
            self._active_pinned_candidate = dict(evaluation)
            admitted = dict(poly_state)
            admitted["direction"] = evaluation.get("direction")
            admitted["selectedMid"] = evaluation.get("polySelected")
            admitted["entryStrategy"] = PINNED_STRATEGY_NAME
            return admitted

        self._active_pinned_candidate = None
        gated = dict(poly_state)
        gated["direction"] = None
        gated["selectedMid"] = None
        gated["entryStrategy"] = PINNED_STRATEGY_NAME
        return gated

    def _direct_book(
        self, market: dict[str, Any], side: str
    ) -> tuple[float | None, float | None, float]:
        ask, ask_size, rtt_ms = super()._direct_book(market, side)
        settings = self._settings()
        if settings["entryStrategyMode"] != ENTRY_MODE_PINNED_DIVERGENCE:
            return ask, ask_size, rtt_ms
        if self._current_active_round() is not None:
            return ask, ask_size, rtt_ms

        candidate = self._active_pinned_candidate
        if not isinstance(candidate, dict) or candidate.get("allowed") is not True:
            self._pinned_direct_book_blocks += 1
            return None, ask_size, rtt_ms
        if int(candidate.get("marketId") or 0) != int(market.get("market_id") or 0):
            self._pinned_direct_book_blocks += 1
            return None, ask_size, rtt_ms
        if str(candidate.get("direction") or "") != str(side):
            self._pinned_direct_book_blocks += 1
            return None, ask_size, rtt_ms
        if base._now_ms() - int(candidate.get("checkedAtMs") or 0) > MAX_OBSERVER_AGE_MS:
            self._pinned_direct_book_blocks += 1
            candidate.update(allowed=False, state="BLOCKED_TRIGGER_STALE", reason="pin trigger aged out before direct book")
            self._last_pinned_evaluation = dict(candidate)
            return None, ask_size, rtt_ms
        max_ask = float(settings["pinnedMaximumSelectedAsk"])
        if ask is None or ask > max_ask + 1e-12:
            self._pinned_direct_book_blocks += 1
            candidate.update(
                allowed=False,
                state="BLOCKED_DIRECT_ASK_MOVED",
                reason=f"fresh Binance {side} Ask {ask} exceeded pinned max {max_ask:.3f}",
                directAsk=ask,
            )
            self._last_pinned_evaluation = dict(candidate)
            return None, ask_size, rtt_ms
        candidate["directAsk"] = ask
        candidate["directBookRttMs"] = rtt_ms
        self._last_pinned_evaluation = dict(candidate)
        return ask, ask_size, rtt_ms

    def _insert_round(self, **kwargs: Any) -> dict[str, Any]:
        row = super()._insert_round(**kwargs)
        mode = str(self._settings()["entryStrategyMode"])
        strategy = PINNED_STRATEGY_NAME if mode == ENTRY_MODE_PINNED_DIVERGENCE else POLY_GAP_STRATEGY_NAME
        context = self._last_pinned_evaluation if strategy == PINNED_STRATEGY_NAME else None
        with self.db_lock:
            self.db.execute(
                "UPDATE poly_gap_live_rounds SET entry_strategy=?, entry_context_json=? WHERE id=?",
                (
                    strategy,
                    json.dumps(context, separators=(",", ":"), allow_nan=False, default=str) if context else None,
                    int(row["id"]),
                ),
            )
            self.db.commit()
            refreshed = self.db.execute(
                "SELECT * FROM poly_gap_live_rounds WHERE id=?", (int(row["id"]),)
            ).fetchone()
        if strategy == PINNED_STRATEGY_NAME:
            self._pinned_trigger_count += 1
            self._event(
                "WARN",
                "PINNED_DIVERGENCE_ENTRY_SIGNAL",
                int(row.get("market_id") or 0) or None,
                int(row["id"]),
                f"{self._asset_name_for_pin()} {row.get('side')} sustained Binance pin; "
                f"Poly={_finite((context or {}).get('polySelected'))}; "
                f"BinanceMid={_finite((context or {}).get('binanceSelectedMid'))}; "
                f"gap={_finite((context or {}).get('divergenceGap'))}",
            )
        return dict(refreshed) if refreshed is not None else row

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        settings = self._settings()
        payload["pinnedDivergence"] = {
            "strategy": PINNED_STRATEGY_NAME,
            "entryStrategyMode": settings["entryStrategyMode"],
            "selected": settings["entryStrategyMode"] == ENTRY_MODE_PINNED_DIVERGENCE,
            "lastEvaluation": self._last_pinned_evaluation,
            "activeCandidate": self._active_pinned_candidate,
            "triggerCount": int(self._pinned_trigger_count),
            "directBookBlocks": int(self._pinned_direct_book_blocks),
            "observerUrl": MULTI_OBSERVER_URL,
            "observerMaxAgeMs": MAX_OBSERVER_AGE_MS,
            "executionPipelineReused": True,
            "positionExitPolicyUnchanged": True,
        }
        payload.setdefault("rules", {}).update(
            selectableEntryStrategy=True,
            pinnedBinancePolyDivergence=True,
            pinnedDivergenceExecutionUsesInheritedSignedQuote=True,
            pinnedDivergenceV40ExitPreserved=True,
            pinnedDivergenceV42TakeProfitLockPreserved=True,
            pinnedDivergenceV43FreshnessPreserved=True,
            pinnedDivergenceV44IdempotencyPreserved=True,
        )
        return payload
