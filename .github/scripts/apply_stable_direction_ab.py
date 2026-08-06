from __future__ import annotations

from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[2]


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dedent(content).lstrip(), encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement, found {count}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


write(
    "src/predict_bot/microprice_confirm_stable_direction_shadow.py",
    r'''
    from __future__ import annotations

    import json
    import math
    import time
    from contextlib import nullcontext
    from functools import wraps
    from typing import Any

    from . import microprice_variants as _variants
    from .research_strategy_registry_patch import register_shadow_strategy


    SOURCE_STRATEGY = "R_MICROPRICE_CONFIRM"
    LEGACY_STRATEGY = "R_MICROPRICE_CONFIRM_STABLE_DIRECTION"
    BASE_STRATEGY = "R_MICROPRICE_CONFIRM_STABLE_DIRECTION_BASE"
    STRICT_STRATEGY = "R_MICROPRICE_CONFIRM_STABLE_DIRECTION_STRICT"
    STRATEGIES = (BASE_STRATEGY, STRICT_STRATEGY)

    # Backward-compatible aliases for code/tests that imported the first version.
    STRATEGY = BASE_STRATEGY
    PATCH_VERSION = "MICROPRICE_CONFIRM_STABLE_DIRECTION_AB_V3"
    HORIZON_SECONDS = 180.0
    MIN_RAW_TOP_ASK = 0.60
    MAX_RAW_TOP_ASK_EXCLUSIVE = 0.90
    MIN_MIDPOINT_DELTA = 0.01
    STRICT_MAX_EFFECTIVE_CROSSOVERS = 1
    STRICT_MIN_EFFICIENCY_RATIO = 0.55
    STRICT_MAX_DIRECTION_FLIPS = 0


    def _finite(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None


    def _decode_json(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        if not value:
            return {}
        try:
            decoded = json.loads(str(value))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}


    def _config(store: Any) -> dict[str, Any]:
        try:
            value = store.config()
        except Exception:
            value = {}
        return value if isinstance(value, dict) else {}


    def _strategy_enabled(store: Any, strategy: str) -> bool:
        cfg = _config(store)
        key = f"strategy_{strategy.lower()}_enabled"
        if key in cfg:
            return bool(cfg[key])
        # One-release migration: the old single toggle controls both A/B groups
        # until the new keys are persisted by DEFAULT_CONFIG.
        legacy_key = f"strategy_{LEGACY_STRATEGY.lower()}_enabled"
        if legacy_key in cfg:
            return bool(cfg[legacy_key])
        return True


    def _strategy_stake(store: Any, strategy: str, fallback: float) -> float:
        value = _finite(_config(store).get(f"strategy_{strategy.lower()}_stake"))
        return value if value is not None and value > 0 else float(fallback)


    def stable_direction_base_decision(
        raw_top_ask: Any,
        midpoint_delta: Any,
    ) -> dict[str, Any]:
        raw = _finite(raw_top_ask)
        delta = _finite(midpoint_delta)
        price_passed = bool(
            raw is not None
            and MIN_RAW_TOP_ASK <= raw < MAX_RAW_TOP_ASK_EXCLUSIVE
        )
        midpoint_passed = bool(
            delta is not None and delta >= MIN_MIDPOINT_DELTA
        )
        allowed = bool(price_passed and midpoint_passed)
        reasons: list[str] = []
        if not price_passed:
            reasons.append(
                "raw_top_ask must be inside "
                f"[{MIN_RAW_TOP_ASK:.2f}, {MAX_RAW_TOP_ASK_EXCLUSIVE:.2f})"
            )
        if not midpoint_passed:
            reasons.append(
                f"selected-side midpoint_delta must be >= {MIN_MIDPOINT_DELTA:.3f}"
            )
        return {
            "version": PATCH_VERSION,
            "strategy": BASE_STRATEGY,
            "sourceStrategy": SOURCE_STRATEGY,
            "allowed": allowed,
            "status": "ALLOW" if allowed else "BLOCK",
            "reason": (
                "allowed: confirmed Microprice direction, raw Ask 0.60-<0.90, "
                "and selected-side midpoint advanced at least 0.01"
                if allowed
                else "; ".join(reasons)
            ),
            "rawTopAsk": raw,
            "midpointDelta": delta,
            "priceBandPassed": price_passed,
            "midpointAdvancePassed": midpoint_passed,
            "usesObserver": False,
            "usesF1RangeScore": False,
            "paperOnly": True,
        }


    # Backward-compatible name: the former single strategy is now BASE.
    stable_direction_signal_decision = stable_direction_base_decision


    def stable_direction_strict_decision(
        raw_top_ask: Any,
        midpoint_delta: Any,
        observer_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        base = stable_direction_base_decision(raw_top_ask, midpoint_delta)
        observer = dict(observer_context or {})
        crossovers = _finite(observer.get("currentEffectiveCrossovers"))
        short_er = _finite(observer.get("currentShortEr"))
        median_er = _finite(observer.get("currentMedianEr60s"))
        selected_er = short_er if short_er is not None else median_er
        both_touched = observer.get("currentBothSidesTouched")
        aligned = observer.get("signalAlignedWithStartMove")
        direction_flips = _finite(observer.get("directionFlipCount"))
        market_matches = observer.get("marketMatches")
        data_quality = str(observer.get("dataQualityStatus") or "MISSING")
        indicator_readiness = str(
            observer.get("indicatorReadiness") or "NOT_READY"
        )

        checks = {
            "basePassed": base["allowed"] is True,
            "marketMatches": market_matches is True,
            "dataQualityReady": data_quality == "READY",
            "indicatorReady": indicator_readiness == "READY",
            "effectiveCrossoversPassed": bool(
                crossovers is not None
                and 0 <= crossovers <= STRICT_MAX_EFFECTIVE_CROSSOVERS
            ),
            "efficiencyRatioPassed": bool(
                selected_er is not None
                and selected_er >= STRICT_MIN_EFFICIENCY_RATIO
            ),
            "oneSidedTouchPassed": both_touched is False,
            "startMoveAlignmentPassed": aligned is True,
            "directionFlipPassed": bool(
                direction_flips is not None
                and 0 <= direction_flips <= STRICT_MAX_DIRECTION_FLIPS
            ),
        }
        allowed = all(checks.values())
        blockers: list[str] = []
        if not checks["basePassed"]:
            blockers.append(base["reason"])
        if not checks["marketMatches"]:
            blockers.append("Observer current market does not match the source market")
        if not checks["dataQualityReady"]:
            blockers.append(f"Observer data quality is {data_quality}")
        if not checks["indicatorReady"]:
            blockers.append(f"Observer indicator readiness is {indicator_readiness}")
        if not checks["effectiveCrossoversPassed"]:
            blockers.append(
                "current effective crossovers must be available and <= "
                f"{STRICT_MAX_EFFECTIVE_CROSSOVERS}"
            )
        if not checks["efficiencyRatioPassed"]:
            blockers.append(
                "current short/median ER must be available and >= "
                f"{STRICT_MIN_EFFICIENCY_RATIO:.2f}"
            )
        if not checks["oneSidedTouchPassed"]:
            blockers.append("both outcome sides must not have touched the threshold")
        if not checks["startMoveAlignmentPassed"]:
            blockers.append("signal side must align with the current start-price move")
        if not checks["directionFlipPassed"]:
            blockers.append("confirmation direction changed earlier in this market")

        return {
            "version": PATCH_VERSION,
            "strategy": STRICT_STRATEGY,
            "sourceStrategy": SOURCE_STRATEGY,
            "allowed": allowed,
            "status": "ALLOW" if allowed else "BLOCK",
            "reason": (
                "allowed: BASE passed plus one-sided, efficient, aligned trend confirmation"
                if allowed
                else "; ".join(blockers)
            ),
            "rawTopAsk": base["rawTopAsk"],
            "midpointDelta": base["midpointDelta"],
            "selectedEfficiencyRatio": selected_er,
            "currentShortEr": short_er,
            "currentMedianEr60s": median_er,
            "currentEffectiveCrossovers": crossovers,
            "currentBothSidesTouched": both_touched,
            "currentStartMoveBps": _finite(observer.get("currentStartMoveBps")),
            "signalAlignedWithStartMove": aligned,
            "directionFlipCount": direction_flips,
            "dataQualityStatus": data_quality,
            "indicatorReadiness": indicator_readiness,
            "marketMatches": market_matches,
            "checks": checks,
            "usesObserver": True,
            "usesF1RangeScore": False,
            "paperOnly": True,
        }


    def _trade_exists(store: Any, strategy: str, market_id: int) -> bool:
        candidates = (strategy,)
        if strategy == BASE_STRATEGY:
            candidates = (BASE_STRATEGY, LEGACY_STRATEGY)
        try:
            placeholders = ",".join("?" for _ in candidates)
            return store.db.execute(
                f"SELECT 1 FROM trades WHERE strategy IN ({placeholders}) "
                "AND market_id=? LIMIT 1",
                (*candidates, int(market_id)),
            ).fetchone() is not None
        except Exception:
            return False


    def _source_context(store: Any, market_id: int) -> dict[str, Any]:
        try:
            row = store.db.execute(
                """SELECT id, strategy_version, diagnostics_json
                     FROM trades
                    WHERE strategy=? AND market_id=?
                    ORDER BY id DESC LIMIT 1""",
                (SOURCE_STRATEGY, int(market_id)),
            ).fetchone()
        except Exception:
            row = None
        if row is None:
            return {
                "sourceTradeId": None,
                "sourceStrategyVersion": None,
                "diagnostics": {},
            }
        return {
            "sourceTradeId": int(row["id"]),
            "sourceStrategyVersion": row["strategy_version"],
            "diagnostics": _decode_json(row["diagnostics_json"]),
        }


    def _observer_context(
        engine: Any,
        *,
        market_id: int,
        side: str,
        direction_flip_count: int,
    ) -> dict[str, Any]:
        context: dict[str, Any] = {
            "currentMarketId": None,
            "marketMatches": False,
            "currentEffectiveCrossovers": None,
            "currentShortEr": None,
            "currentMedianEr60s": None,
            "currentBothSidesTouched": None,
            "currentStartMoveBps": None,
            "signalAlignedWithStartMove": None,
            "directionFlipCount": int(direction_flip_count),
            "dataQualityStatus": "MISSING_OR_STALE",
            "indicatorReadiness": "NOT_READY",
        }
        observer = getattr(engine, "market_observer", None)
        if observer is None:
            context["observerError"] = "market_observer unavailable"
            return context
        try:
            gate = observer.m01o_entry_gate(
                min_settled_samples=0,
                min_current_range_score=0,
                profile="F1",
                now_ts=time.time(),
            )
        except Exception as exc:
            context["observerError"] = str(exc)
            return context

        context.update(
            {
                "currentMarketId": gate.get("currentMarketId"),
                "currentEffectiveCrossovers": gate.get(
                    "currentEffectiveCrossovers"
                ),
                "currentShortEr": gate.get("currentShortEr"),
                "currentMedianEr60s": gate.get("currentMedianEr60s"),
                "currentBothSidesTouched": gate.get(
                    "currentBothSidesTouched"
                ),
                "dataQualityStatus": gate.get("dataQualityStatus"),
                "indicatorReadiness": gate.get("indicatorReadiness"),
                "observerGateSnapshot": gate,
            }
        )
        try:
            observed_market_id = int(gate.get("currentMarketId"))
        except (TypeError, ValueError):
            observed_market_id = -1
        context["marketMatches"] = observed_market_id == int(market_id)

        lock = getattr(observer, "lock", None)
        manager = lock if lock is not None else nullcontext()
        try:
            with manager:
                start_price = _finite(getattr(observer, "start_price", None))
                spot_price = _finite(getattr(observer, "last_spot_price", None))
        except Exception:
            start_price = None
            spot_price = None
        start_move_bps = (
            (spot_price - start_price) / start_price * 10_000.0
            if start_price is not None
            and spot_price is not None
            and start_price > 0
            else None
        )
        context["currentStartMoveBps"] = start_move_bps
        context["signalAlignedWithStartMove"] = bool(
            start_move_bps is not None
            and (
                (side == "UP" and start_move_bps > 0)
                or (side == "DOWN" and start_move_bps < 0)
            )
        )
        return context


    def _open_shadow(
        store: Any,
        source: dict[str, Any],
        fee_bps: int,
        *,
        strategy: str,
        decision: dict[str, Any],
        source_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        try:
            market_id = int(source["market_id"])
            topic_id = int(source["topic_id"])
            side = str(source["side"]).upper()
            entry = float(source["entry_price"])
            source_stake = float(source["stake"])
        except (KeyError, TypeError, ValueError):
            return None
        if (
            side not in {"UP", "DOWN"}
            or decision.get("allowed") is not True
            or not _strategy_enabled(store, strategy)
            or _trade_exists(store, strategy, market_id)
        ):
            return None

        stake = _strategy_stake(store, strategy, source_stake)
        strict = strategy == STRICT_STRATEGY
        diagnostics = source_context["diagnostics"]
        shadow_diagnostics = {
            "paper_only": True,
            "live_orders_affected": False,
            "shadow_only": True,
            "forward_only": True,
            "source_strategy": SOURCE_STRATEGY,
            "source_trade_id": source_context["sourceTradeId"],
            "source_strategy_version": source_context[
                "sourceStrategyVersion"
            ],
            "source_already_microprice_confirmed": True,
            "variant_mode": (
                "FOLLOW_CONFIRMED_IMBALANCE_STABLE_DIRECTION_STRICT"
                if strict
                else "FOLLOW_CONFIRMED_IMBALANCE_STABLE_DIRECTION_BASE"
            ),
            "selected_side": side,
            "raw_top_ask": decision.get("rawTopAsk"),
            "midpoint_delta": decision.get("midpointDelta"),
            "stable_direction_decision": decision,
            "entry_time_telemetry": {
                key: decision.get(key)
                for key in (
                    "currentEffectiveCrossovers",
                    "currentShortEr",
                    "currentMedianEr60s",
                    "currentBothSidesTouched",
                    "currentStartMoveBps",
                    "signalAlignedWithStartMove",
                    "directionFlipCount",
                    "dataQualityStatus",
                    "indicatorReadiness",
                    "marketMatches",
                )
            },
            "rule": {
                "minimumRawTopAskInclusive": MIN_RAW_TOP_ASK,
                "maximumRawTopAskExclusive": MAX_RAW_TOP_ASK_EXCLUSIVE,
                "minimumSelectedMidpointDeltaInclusive": MIN_MIDPOINT_DELTA,
                "maximumEffectiveCrossoversInclusive": (
                    STRICT_MAX_EFFECTIVE_CROSSOVERS if strict else None
                ),
                "minimumShortOrMedianErInclusive": (
                    STRICT_MIN_EFFICIENCY_RATIO if strict else None
                ),
                "bothSidesTouchedMustBeFalse": strict,
                "signalMustAlignWithStartMove": strict,
                "maximumDirectionFlipsInclusive": (
                    STRICT_MAX_DIRECTION_FLIPS if strict else None
                ),
                "sourceMustBeMicropriceConfirm": True,
                "observerRequired": strict,
                "f1RangeScoreRequired": False,
                "missingDataFailsClosed": True,
            },
            "source_diagnostics": diagnostics,
        }
        store.open_trade(
            strategy=strategy,
            topic_id=topic_id,
            market_id=market_id,
            side=side,
            entry=entry,
            target=None,
            stake=stake,
            fee_rate_bps=int(fee_bps),
            note=(
                f"{strategy} mirrored from {SOURCE_STRATEGY} trade "
                f"#{source_context['sourceTradeId'] or '?'}; paper only"
            ),
            strategy_version=PATCH_VERSION,
            diagnostics=shadow_diagnostics,
        )
        return {
            **source,
            "strategy": strategy,
            "stake": stake,
            "paper_only": True,
            "shadow_only": True,
            "live_orders_affected": False,
            "live_forwardable_when_selected": False,
            "stable_direction_decision": decision,
            "strategy_version": PATCH_VERSION,
            "source_strategy": SOURCE_STRATEGY,
            "source_trade_id": source_context["sourceTradeId"],
        }


    def open_stable_direction_shadow(
        store: Any,
        source: dict[str, Any],
        fee_bps: int,
    ) -> dict[str, Any] | None:
        """Backward-compatible BASE opener."""
        if str(source.get("strategy") or "").upper() != SOURCE_STRATEGY:
            return None
        try:
            market_id = int(source["market_id"])
        except (KeyError, TypeError, ValueError):
            return None
        source_context = _source_context(store, market_id)
        diagnostics = source_context["diagnostics"]
        raw_top_ask = source.get("raw_top_ask", diagnostics.get("raw_top_ask"))
        midpoint_delta = source.get(
            "midpoint_delta", diagnostics.get("midpoint_delta")
        )
        decision = stable_direction_base_decision(raw_top_ask, midpoint_delta)
        return _open_shadow(
            store,
            source,
            fee_bps,
            strategy=BASE_STRATEGY,
            decision=decision,
            source_context=source_context,
        )


    def open_stable_direction_strict_shadow(
        store: Any,
        source: dict[str, Any],
        fee_bps: int,
        observer_context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if str(source.get("strategy") or "").upper() != SOURCE_STRATEGY:
            return None
        try:
            market_id = int(source["market_id"])
        except (KeyError, TypeError, ValueError):
            return None
        source_context = _source_context(store, market_id)
        diagnostics = source_context["diagnostics"]
        raw_top_ask = source.get("raw_top_ask", diagnostics.get("raw_top_ask"))
        midpoint_delta = source.get(
            "midpoint_delta", diagnostics.get("midpoint_delta")
        )
        decision = stable_direction_strict_decision(
            raw_top_ask,
            midpoint_delta,
            observer_context,
        )
        return _open_shadow(
            store,
            source,
            fee_bps,
            strategy=STRICT_STRATEGY,
            decision=decision,
            source_context=source_context,
        )


    def _patch_tracker_process() -> None:
        tracker_class = _variants.MicropriceVariantTracker
        original = tracker_class.process
        if getattr(original, "_stable_direction_shadow_ab_v3", False):
            return

        @wraps(original)
        def process_with_stable_direction_ab(
            self: Any,
            snapshot: dict[str, Any],
            fee_bps: int,
            context: dict[str, Any],
        ) -> list[dict[str, Any]]:
            try:
                market_id = int(snapshot.get("market_id"))
            except (TypeError, ValueError):
                market_id = -1
            previous_market = getattr(
                self, "_stable_direction_ab_market_id", None
            )
            if previous_market != market_id:
                self._stable_direction_ab_market_id = market_id
                self._stable_direction_ab_direction_flips = 0
            before_flips = int(
                getattr(self, "rejected_direction_change", 0) or 0
            )
            opened = list(original(self, snapshot, int(fee_bps), context) or [])
            after_flips = int(
                getattr(self, "rejected_direction_change", 0) or 0
            )
            if after_flips > before_flips:
                self._stable_direction_ab_direction_flips = int(
                    getattr(self, "_stable_direction_ab_direction_flips", 0)
                ) + (after_flips - before_flips)

            result = list(opened)
            for source in opened:
                if str(source.get("strategy") or "").upper() != SOURCE_STRATEGY:
                    continue
                base = open_stable_direction_shadow(
                    self.store, source, int(fee_bps)
                )
                if base is not None:
                    result.append(base)
                try:
                    source_market_id = int(source["market_id"])
                    source_side = str(source["side"]).upper()
                except (KeyError, TypeError, ValueError):
                    continue
                observer = _observer_context(
                    self.engine,
                    market_id=source_market_id,
                    side=source_side,
                    direction_flip_count=int(
                        getattr(
                            self,
                            "_stable_direction_ab_direction_flips",
                            0,
                        )
                    ),
                )
                strict = open_stable_direction_strict_shadow(
                    self.store,
                    source,
                    int(fee_bps),
                    observer,
                )
                if strict is not None:
                    result.append(strict)
            return result

        process_with_stable_direction_ab._stable_direction_shadow_ab_v3 = True  # type: ignore[attr-defined]
        tracker_class.process = process_with_stable_direction_ab


    def _register_research_strategies() -> None:
        common = {
            "horizon": HORIZON_SECONDS,
            "max_ask": MAX_RAW_TOP_ASK_EXCLUSIVE,
            "min_raw_top_ask": MIN_RAW_TOP_ASK,
            "max_raw_top_ask_exclusive": MAX_RAW_TOP_ASK_EXCLUSIVE,
            "min_midpoint_delta": MIN_MIDPOINT_DELTA,
        }
        register_shadow_strategy(
            BASE_STRATEGY,
            parameters=dict(common),
            generic_signal=False,
        )
        register_shadow_strategy(
            STRICT_STRATEGY,
            parameters={
                **common,
                "max_effective_crossovers": float(
                    STRICT_MAX_EFFECTIVE_CROSSOVERS
                ),
                "min_efficiency_ratio": STRICT_MIN_EFFICIENCY_RATIO,
                "max_direction_flips": float(STRICT_MAX_DIRECTION_FLIPS),
            },
            generic_signal=False,
        )


    def install_microprice_confirm_stable_direction_shadow() -> None:
        """Register two derived Paper Shadows; never evaluate or forward live."""
        _register_research_strategies()
        _patch_tracker_process()
    ''',
)

write(
    "tests/test_microprice_confirm_stable_direction_shadow.py",
    r'''
    from __future__ import annotations

    import json
    import sqlite3

    from predict_bot import live_trading, research_forward, server
    from predict_bot.microprice_confirm_stable_direction_shadow import (
        BASE_STRATEGY,
        HORIZON_SECONDS,
        MAX_RAW_TOP_ASK_EXCLUSIVE,
        MIN_MIDPOINT_DELTA,
        MIN_RAW_TOP_ASK,
        STRICT_MIN_EFFICIENCY_RATIO,
        STRICT_STRATEGY,
        open_stable_direction_shadow,
        open_stable_direction_strict_shadow,
        stable_direction_base_decision,
        stable_direction_strict_decision,
    )


    class FakeStore:
        def __init__(self, config: dict | None = None) -> None:
            self.db = sqlite3.connect(":memory:")
            self.db.row_factory = sqlite3.Row
            self.db.execute(
                """CREATE TABLE trades(
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       strategy TEXT NOT NULL,
                       market_id INTEGER NOT NULL,
                       strategy_version TEXT,
                       diagnostics_json TEXT
                   )"""
            )
            self.opened: list[dict] = []
            self._config = {
                f"strategy_{BASE_STRATEGY.lower()}_enabled": True,
                f"strategy_{STRICT_STRATEGY.lower()}_enabled": True,
                f"strategy_{BASE_STRATEGY.lower()}_stake": 5.0,
                f"strategy_{STRICT_STRATEGY.lower()}_stake": 5.0,
                **(config or {}),
            }

        def config(self) -> dict:
            return dict(self._config)

        def open_trade(self, **values):
            self.opened.append(values)
            self.db.execute(
                "INSERT INTO trades(strategy, market_id, strategy_version, diagnostics_json) VALUES (?,?,?,?)",
                (
                    values["strategy"],
                    values["market_id"],
                    values.get("strategy_version"),
                    json.dumps(values.get("diagnostics") or {}),
                ),
            )
            self.db.commit()


    def source(store: FakeStore, *, ask: float, delta: float) -> dict:
        diagnostics = {
            "raw_top_ask": ask,
            "midpoint_delta": delta,
            "confirmation_count": 3,
            "samples": [
                {"side": "UP"},
                {"side": "UP"},
                {"side": "UP"},
            ],
        }
        store.db.execute(
            "INSERT INTO trades(strategy, market_id, strategy_version, diagnostics_json) VALUES (?,?,?,?)",
            (
                "R_MICROPRICE_CONFIRM",
                7001,
                "SOURCE_V1",
                json.dumps(diagnostics),
            ),
        )
        store.db.commit()
        return {
            "strategy": "R_MICROPRICE_CONFIRM",
            "topic_id": 11,
            "market_id": 7001,
            "side": "UP",
            "entry_price": ask * 1.005,
            "raw_top_ask": ask,
            "stake": 5.0,
        }


    def strict_context(**overrides) -> dict:
        return {
            "currentMarketId": 7001,
            "marketMatches": True,
            "currentEffectiveCrossovers": 1,
            "currentShortEr": STRICT_MIN_EFFICIENCY_RATIO,
            "currentMedianEr60s": 0.60,
            "currentBothSidesTouched": False,
            "currentStartMoveBps": 2.0,
            "signalAlignedWithStartMove": True,
            "directionFlipCount": 0,
            "dataQualityStatus": "READY",
            "indicatorReadiness": "READY",
            **overrides,
        }


    def test_base_decision_requires_price_band_and_midpoint_advance() -> None:
        assert stable_direction_base_decision(
            MIN_RAW_TOP_ASK, MIN_MIDPOINT_DELTA
        )["allowed"] is True
        assert stable_direction_base_decision(
            MAX_RAW_TOP_ASK_EXCLUSIVE - 1e-8, 0.02
        )["allowed"] is True
        assert stable_direction_base_decision(
            MIN_RAW_TOP_ASK - 1e-8, 0.02
        )["allowed"] is False
        assert stable_direction_base_decision(
            MAX_RAW_TOP_ASK_EXCLUSIVE, 0.02
        )["allowed"] is False
        assert stable_direction_base_decision(
            0.70, MIN_MIDPOINT_DELTA - 1e-8
        )["allowed"] is False
        assert stable_direction_base_decision(None, 0.02)["allowed"] is False


    def test_strict_requires_every_entry_time_trend_field() -> None:
        assert stable_direction_strict_decision(
            0.70, 0.015, strict_context()
        )["allowed"] is True
        assert stable_direction_strict_decision(
            0.70, 0.015, strict_context(currentEffectiveCrossovers=2)
        )["allowed"] is False
        assert stable_direction_strict_decision(
            0.70, 0.015, strict_context(currentShortEr=0.54)
        )["allowed"] is False
        assert stable_direction_strict_decision(
            0.70, 0.015, strict_context(currentBothSidesTouched=True)
        )["allowed"] is False
        assert stable_direction_strict_decision(
            0.70, 0.015, strict_context(signalAlignedWithStartMove=False)
        )["allowed"] is False
        assert stable_direction_strict_decision(
            0.70, 0.015, strict_context(directionFlipCount=1)
        )["allowed"] is False
        assert stable_direction_strict_decision(
            0.70, 0.015, strict_context(dataQualityStatus="MISSING_OR_STALE")
        )["allowed"] is False
        assert stable_direction_strict_decision(
            0.70, 0.015, None
        )["allowed"] is False


    def test_base_and_strict_open_independent_paper_ledgers() -> None:
        store = FakeStore()
        item = source(store, ask=0.70, delta=0.015)
        base = open_stable_direction_shadow(store, item, 200)
        strict = open_stable_direction_strict_shadow(
            store, item, 200, strict_context()
        )
        assert base is not None and base["strategy"] == BASE_STRATEGY
        assert strict is not None and strict["strategy"] == STRICT_STRATEGY
        assert len(store.opened) == 2
        assert all(item["diagnostics"]["paper_only"] for item in store.opened)
        assert all(
            item["diagnostics"]["live_orders_affected"] is False
            for item in store.opened
        )
        assert store.opened[0]["diagnostics"]["rule"]["observerRequired"] is False
        assert store.opened[1]["diagnostics"]["rule"]["observerRequired"] is True


    def test_each_dashboard_toggle_controls_its_own_shadow() -> None:
        store = FakeStore(
            {
                f"strategy_{BASE_STRATEGY.lower()}_enabled": False,
                f"strategy_{STRICT_STRATEGY.lower()}_enabled": True,
            }
        )
        item = source(store, ask=0.70, delta=0.015)
        assert open_stable_direction_shadow(store, item, 200) is None
        strict = open_stable_direction_strict_shadow(
            store, item, 200, strict_context()
        )
        assert strict is not None
        assert [opened["strategy"] for opened in store.opened] == [STRICT_STRATEGY]


    def test_each_shadow_uses_its_own_configured_stake() -> None:
        store = FakeStore(
            {
                f"strategy_{BASE_STRATEGY.lower()}_stake": 3.0,
                f"strategy_{STRICT_STRATEGY.lower()}_stake": 4.0,
            }
        )
        item = source(store, ask=0.70, delta=0.015)
        open_stable_direction_shadow(store, item, 200)
        open_stable_direction_strict_shadow(
            store, item, 200, strict_context()
        )
        assert [opened["stake"] for opened in store.opened] == [3.0, 4.0]


    def test_strict_fails_closed_when_observer_data_is_missing() -> None:
        store = FakeStore()
        item = source(store, ask=0.70, delta=0.015)
        assert open_stable_direction_strict_shadow(
            store, item, 200, None
        ) is None
        assert store.opened == []


    def test_both_ids_are_native_research_shadows_and_never_live_selectable() -> None:
        for strategy in (BASE_STRATEGY, STRICT_STRATEGY):
            assert strategy in research_forward.SHADOW_RESEARCH_STRATEGIES
            assert strategy in research_forward.RESEARCH_STRATEGIES
            assert strategy not in research_forward.PRIMARY_RESEARCH_STRATEGIES
            assert strategy not in research_forward.GENERIC_SIGNAL_RESEARCH_STRATEGIES
            assert strategy in research_forward.DERIVED_RESEARCH_STRATEGIES
            assert strategy not in live_trading.LIVE_SUPPORTED_STRATEGIES
            assert strategy not in live_trading.LIVE_RESEARCH_STRATEGIES
            assert server.DEFAULT_CONFIG[
                f"strategy_{strategy.lower()}_enabled"
            ] is True


    def test_metadata_keeps_horizon_without_activating_generic_sampling() -> None:
        for strategy in (BASE_STRATEGY, STRICT_STRATEGY):
            params = research_forward.RESEARCH_PARAMETERS[strategy]
            assert params["horizon"] == HORIZON_SECONDS
            assert research_forward.sampling_active(
                HORIZON_SECONDS, {strategy}
            ) is False
            assert research_forward.signal_for_strategy(
                strategy,
                {},
                None,
                fee_bps=200,
                slippage_bps=50.0,
            ) is None
    ''',
)

write(
    "tests/test_research_strategy_registry.py",
    r'''
    from __future__ import annotations

    import math

    from predict_bot import m_realtime, research_forward, server
    from predict_bot.research_strategy_registry_patch import (
        GENERIC_SIGNAL_STRATEGIES,
        validate_research_strategy_registry,
    )


    PRIMARY_BASES = {
        "R_MICROPRICE",
        "R_OFI",
        "R_FUTURES_LEAD",
        "R_CALIBRATED_VALUE",
        "R_CONSENSUS",
    }

    GENERIC_SHADOW_VARIANTS = {
        "R_OFI_MIN040",
        "R_OFI_EVENT_CUM",
        "R_OFI_EVENT_CUM_FILTERED",
        "R_FUTURES_LEAD_EXIT30",
        "R_FUTURES_LEAD_DISTANCE",
        "R_FUTURES_LEAD_EXIT30_DISTANCE",
    }

    DERIVED_SHADOW_VARIANTS = {
        "R_CALIBRATED_VALUE_CONTINUOUS_V2",
        "R_MICROPRICE_REVERSE",
        "R_CALIBRATED_VALUE_REVERSE",
        "R_FUTURES_LEAD_CONTINUOUS_V2",
        "R_FUTURES_LEAD_REVERSE",
        "R_FUTURES_LEAD_REGIME_REVERSE_3L",
        "R_FUTURES_LEAD_SIGNAL_100",
        "R_FUTURES_LEAD_MIN_ENTRY_020",
        "R_FUTURES_LEAD_OBSERVER_F1",
        "R_OFI_OBSERVER_V3",
        "R_MICROPRICE_OBSERVER_V3",
        "R_MICROPRICE_CONFIRM_STABLE_DIRECTION_BASE",
        "R_MICROPRICE_CONFIRM_STABLE_DIRECTION_STRICT",
    }


    def test_registry_is_disjoint_complete_and_valid() -> None:
        primary = research_forward.PRIMARY_RESEARCH_STRATEGIES
        shadow = research_forward.SHADOW_RESEARCH_STRATEGIES
        generic = research_forward.GENERIC_SIGNAL_RESEARCH_STRATEGIES
        derived = research_forward.DERIVED_RESEARCH_STRATEGIES

        assert set(primary).isdisjoint(shadow)
        assert research_forward.RESEARCH_STRATEGIES == (*primary, *shadow)
        assert len(research_forward.RESEARCH_STRATEGIES) == len(
            set(research_forward.RESEARCH_STRATEGIES)
        )
        assert set(generic).isdisjoint(derived)
        assert set(generic) | set(derived) == set(
            research_forward.RESEARCH_STRATEGIES
        )
        assert validate_research_strategy_registry() == ()


    def test_primary_registry_remains_the_shared_capital_base_set() -> None:
        assert set(research_forward.PRIMARY_RESEARCH_STRATEGIES) == PRIMARY_BASES


    def test_independent_signal_experiments_remain_isolated_shadows() -> None:
        assert set(GENERIC_SIGNAL_STRATEGIES).issubset(
            research_forward.GENERIC_SIGNAL_RESEARCH_STRATEGIES
        )
        assert GENERIC_SHADOW_VARIANTS.issubset(
            research_forward.SHADOW_RESEARCH_STRATEGIES
        )
        assert GENERIC_SHADOW_VARIANTS.issubset(
            research_forward.GENERIC_SIGNAL_RESEARCH_STRATEGIES
        )


    def test_derived_variants_are_shadow_only_and_never_generic() -> None:
        assert DERIVED_SHADOW_VARIANTS.issubset(
            research_forward.SHADOW_RESEARCH_STRATEGIES
        )
        assert DERIVED_SHADOW_VARIANTS.issubset(
            research_forward.DERIVED_RESEARCH_STRATEGIES
        )
        assert DERIVED_SHADOW_VARIANTS.isdisjoint(
            research_forward.GENERIC_SIGNAL_RESEARCH_STRATEGIES
        )


    def test_every_generic_signal_strategy_has_a_finite_positive_horizon() -> None:
        for strategy in research_forward.GENERIC_SIGNAL_RESEARCH_STRATEGIES:
            horizon = float(
                research_forward.RESEARCH_PARAMETERS[strategy]["horizon"]
            )
            assert math.isfinite(horizon)
            assert horizon > 0


    def test_sampling_ignores_derived_and_unknown_but_keeps_generic_shadows() -> None:
        assert research_forward.sampling_active(
            180.0, {"R_MICROPRICE_REVERSE"}
        ) is False
        assert research_forward.sampling_active(
            180.0, {"R_UNKNOWN_RESEARCH_STRATEGY"}
        ) is False
        assert research_forward.sampling_active(
            180.0, {"R_FUTURES_LEAD_DISTANCE"}
        ) is True
        assert research_forward.sampling_active(
            180.0, {"R_MICROPRICE_REVERSE", "R_MICROPRICE"}
        ) is True


    def test_generic_signal_path_fails_closed_for_derived_and_unknown() -> None:
        for strategy in (
            "R_MICROPRICE_REVERSE",
            "R_MICROPRICE_CONFIRM_STABLE_DIRECTION_BASE",
            "R_MICROPRICE_CONFIRM_STABLE_DIRECTION_STRICT",
            "R_UNKNOWN_RESEARCH_STRATEGY",
        ):
            assert research_forward.signal_for_strategy(
                strategy,
                {},
                None,
                fee_bps=200,
                slippage_bps=50.0,
            ) is None


    def test_runtime_consumers_receive_generic_horizons_and_all_supported_ids() -> None:
        assert set(m_realtime.RESEARCH_PARAMETERS) == set(
            research_forward.GENERIC_SIGNAL_RESEARCH_STRATEGIES
        )
        for strategy in (
            "R_MICROPRICE_CONFIRM_STABLE_DIRECTION_BASE",
            "R_MICROPRICE_CONFIRM_STABLE_DIRECTION_STRICT",
        ):
            assert strategy not in m_realtime.RESEARCH_PARAMETERS
            assert strategy in server.SUPPORTED_STRATEGIES
        assert "R_FUTURES_LEAD_DISTANCE" in m_realtime.RESEARCH_PARAMETERS
        assert server.RESEARCH_STRATEGIES == research_forward.RESEARCH_STRATEGIES
    ''',
)

replace_once(
    "src/predict_bot/server.py",
    '''    "strategy_r_microprice_confirm_stable_direction_enabled": True,\n    "strategy_r_microprice_confirm_stable_direction_stake": 5.0,\n''',
    '''    "strategy_r_microprice_confirm_stable_direction_base_enabled": True,\n    "strategy_r_microprice_confirm_stable_direction_base_stake": 5.0,\n    "strategy_r_microprice_confirm_stable_direction_strict_enabled": True,\n    "strategy_r_microprice_confirm_stable_direction_strict_stake": 5.0,\n''',
)

replace_once(
    "dashboard/app/page.tsx",
    '''  | "R_MICROPRICE" | "R_MICROPRICE_CONFIRM_STABLE_DIRECTION" | "R_MICROPRICE_REVERSE"''',
    '''  | "R_MICROPRICE" | "R_MICROPRICE_CONFIRM_STABLE_DIRECTION_BASE" | "R_MICROPRICE_CONFIRM_STABLE_DIRECTION_STRICT" | "R_MICROPRICE_REVERSE"''',
)
replace_once(
    "dashboard/app/page.tsx",
    '''    R_MICROPRICE: { ...EMPTY_SUMMARY }, R_MICROPRICE_CONFIRM_STABLE_DIRECTION: { ...EMPTY_SUMMARY }, R_OFI: { ...EMPTY_SUMMARY },''',
    '''    R_MICROPRICE: { ...EMPTY_SUMMARY }, R_MICROPRICE_CONFIRM_STABLE_DIRECTION_BASE: { ...EMPTY_SUMMARY }, R_MICROPRICE_CONFIRM_STABLE_DIRECTION_STRICT: { ...EMPTY_SUMMARY }, R_OFI: { ...EMPTY_SUMMARY },''',
)
replace_once(
    "dashboard/app/page.tsx",
    '''  { id: "R_MICROPRICE_CONFIRM_STABLE_DIRECTION", title: "Microprice Confirm · 穩定方向共識", rule: "依賴式 Shadow：只有 R_MICROPRICE_CONFIRM 已完成同方向多事件確認後才評估；訊號側 raw Ask 必須為 0.60–<0.90，且確認期間 selected-side midpoint delta ≥0.01。完全不使用 F1、震盪分或 Observer；paper only，不可轉送實單。", tone: "green", shadow: true },''',
    '''  { id: "R_MICROPRICE_CONFIRM_STABLE_DIRECTION_BASE", title: "穩定方向 BASE · Ask 0.60–0.90", rule: "依賴式 Paper Shadow：只有 R_MICROPRICE_CONFIRM 已完成同方向多事件確認後才評估；訊號側 raw Ask 為 0.60–<0.90，且 selected-side midpoint delta ≥0.01。不使用 F1 或 Observer。", tone: "green", shadow: true },\n  { id: "R_MICROPRICE_CONFIRM_STABLE_DIRECTION_STRICT", title: "穩定方向 STRICT · ER／穿越確認", rule: "依賴式 Paper Shadow：先通過 BASE，再要求進場當下 Observer 資料 READY、有效穿越 ≤1、短窗或 Median ER ≥0.55、雙邊未同時觸及、訊號與 start move 同向，且本市場確認方向未翻轉；缺資料一律阻擋。", tone: "blue", shadow: true },''',
)

replace_once(
    "dashboard/tests/rendered-html.test.mjs",
    '''  assert.match(page, /R_MICROPRICE/);\n  assert.match(page, /R_MICROPRICE: "研究實單 · Microprice 深度失衡"/);''',
    '''  assert.match(page, /R_MICROPRICE/);\n  assert.match(page, /R_MICROPRICE_CONFIRM_STABLE_DIRECTION_BASE/);\n  assert.match(page, /R_MICROPRICE_CONFIRM_STABLE_DIRECTION_STRICT/);\n  assert.match(page, /穩定方向 BASE · Ask 0\\.60–0\\.90/);\n  assert.match(page, /穩定方向 STRICT · ER／穿越確認/);\n  assert.match(page, /R_MICROPRICE: "研究實單 · Microprice 深度失衡"/);''',
)

page = (ROOT / "dashboard/app/page.tsx").read_text(encoding="utf-8")
for required in (
    '"R_MICROPRICE_CONFIRM_STABLE_DIRECTION_BASE"',
    '"R_MICROPRICE_CONFIRM_STABLE_DIRECTION_STRICT"',
    '穩定方向 BASE · Ask 0.60–0.90',
    '穩定方向 STRICT · ER／穿越確認',
):
    if required not in page:
        raise RuntimeError(f"dashboard missing {required}")
if '"R_MICROPRICE_CONFIRM_STABLE_DIRECTION" |' in page:
    raise RuntimeError("legacy stable-direction StrategyId remains in dashboard")

server = (ROOT / "src/predict_bot/server.py").read_text(encoding="utf-8")
if "strategy_r_microprice_confirm_stable_direction_enabled" in server:
    raise RuntimeError("legacy stable-direction config remains in server")

print("Stable Direction BASE/STRICT patch applied")
