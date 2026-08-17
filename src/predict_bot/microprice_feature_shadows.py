from __future__ import annotations

import json
import math
from functools import wraps
from typing import Any

from . import m_realtime as _realtime


SOURCE_STRATEGY = "R_MICROPRICE"
FEATURE_SHADOW_VERSION = "MICROPRICE_FEATURE_SHADOWS_V1"
FEATURE_SHADOW_STAKE_USDT = 5.0

DEADZONE_EXCLUDED_STRATEGY = "R_MICROPRICE_NO_020_025"
UP_ONLY_STRATEGY = "R_MICROPRICE_UP_ONLY"
DOWN_ONLY_STRATEGY = "R_MICROPRICE_DOWN_ONLY"
LOW_TAIL_THIN_STRATEGY = "R_MICROPRICE_LOW_010_020_ASK_LE_60"
LOW_TAIL_THICK_STRATEGY = "R_MICROPRICE_LOW_010_020_ASK_GT_60"

FEATURE_SHADOW_STRATEGIES = (
    DEADZONE_EXCLUDED_STRATEGY,
    UP_ONLY_STRATEGY,
    DOWN_ONLY_STRATEGY,
    LOW_TAIL_THIN_STRATEGY,
    LOW_TAIL_THICK_STRATEGY,
)

FEATURE_SHADOW_DEFINITIONS: dict[str, dict[str, Any]] = {
    DEADZONE_EXCLUDED_STRATEGY: {
        "family": "DEADZONE_EXCLUSION",
        "cohort": "EXCLUDE_020_025",
        "title": "Microprice 排除 0.20–0.25 死區",
        "rule": "mirror R_MICROPRICE only when entry is outside [0.20, 0.25)",
        "parameters": {
            "excludedEntryMinInclusive": 0.20,
            "excludedEntryMaxExclusive": 0.25,
        },
    },
    UP_ONLY_STRATEGY: {
        "family": "DIRECTION_SPLIT",
        "cohort": "UP_ONLY",
        "title": "Microprice 方向拆帳 · UP",
        "rule": "mirror only R_MICROPRICE UP entries",
        "parameters": {"side": "UP"},
    },
    DOWN_ONLY_STRATEGY: {
        "family": "DIRECTION_SPLIT",
        "cohort": "DOWN_ONLY",
        "title": "Microprice 方向拆帳 · DOWN",
        "rule": "mirror only R_MICROPRICE DOWN entries",
        "parameters": {"side": "DOWN"},
    },
    LOW_TAIL_THIN_STRATEGY: {
        "family": "LOW_PRICE_TAIL_DEPTH_SPLIT",
        "cohort": "ENTRY_010_020_ASK_LE_60",
        "title": "Microprice 低價肥尾 · Ask ≤60",
        "rule": (
            "mirror R_MICROPRICE when 0.10 <= entry < 0.20 and available "
            "selected Ask size <= 60 shares"
        ),
        "parameters": {
            "entryMinInclusive": 0.10,
            "entryMaxExclusive": 0.20,
            "availableAskSizeMaxInclusive": 60.0,
        },
    },
    LOW_TAIL_THICK_STRATEGY: {
        "family": "LOW_PRICE_TAIL_DEPTH_SPLIT",
        "cohort": "ENTRY_010_020_ASK_GT_60",
        "title": "Microprice 低價肥尾 · Ask >60",
        "rule": (
            "mirror R_MICROPRICE when 0.10 <= entry < 0.20 and available "
            "selected Ask size > 60 shares"
        ),
        "parameters": {
            "entryMinInclusive": 0.10,
            "entryMaxExclusive": 0.20,
            "availableAskSizeMinExclusive": 60.0,
        },
    },
}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _available_ask_size(
    diagnostics: dict[str, Any],
) -> tuple[float | None, str | None]:
    for key in (
        "available_ask_size_after_reservations",
        "visible_ask_size",
        "raw_visible_ask_size",
    ):
        value = _finite(diagnostics.get(key))
        if value is not None and value >= 0:
            return value, key
    return None, None


def _selected_source_diagnostics(
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    selected_keys = (
        "signal",
        "signal_horizon_seconds",
        "signal_lag_seconds",
        "signal_timestamp",
        "raw_top_ask",
        "simulated_entry_after_slippage",
        "available_ask_size_after_reservations",
        "visible_ask_size",
        "raw_visible_ask_size",
        "spread",
        "book_age_ms",
        "book_skew_ms",
        "selected_backtest_parameters",
    )
    selected = {
        key: diagnostics[key]
        for key in selected_keys
        if key in diagnostics
    }
    context = diagnostics.get("realtime_context")
    if isinstance(context, dict):
        selected_context_keys = (
            "trigger_source",
            "trigger_stream",
            "prediction_data_source",
            "prediction_sampling_mode",
            "market_data_integrity_ok",
            "market_elapsed_seconds",
            "queue_delay_ms",
            "spot_age_ms",
            "futures_age_ms",
            "prediction_book_age_ms",
            "signal_event_sequence",
            "m01o_observer_gate",
        )
        selected["realtime_context"] = {
            key: context[key]
            for key in selected_context_keys
            if key in context
        }
    return selected


def _matching_strategies(
    *,
    entry: float,
    side: str,
    available_ask_size: float | None,
) -> list[str]:
    matches: list[str] = []

    if not (0.20 <= entry < 0.25):
        matches.append(DEADZONE_EXCLUDED_STRATEGY)

    if side == "UP":
        matches.append(UP_ONLY_STRATEGY)
    elif side == "DOWN":
        matches.append(DOWN_ONLY_STRATEGY)

    if 0.10 <= entry < 0.20 and available_ask_size is not None:
        matches.append(
            LOW_TAIL_THIN_STRATEGY
            if available_ask_size <= 60.0
            else LOW_TAIL_THICK_STRATEGY
        )

    return matches


def _source_trade_id(store: Any, market_id: int) -> int | None:
    try:
        row = store.db.execute(
            """SELECT id FROM trades
                WHERE strategy=? AND market_id=?
                ORDER BY id DESC LIMIT 1""",
            (SOURCE_STRATEGY, market_id),
        ).fetchone()
    except Exception:
        return None
    return int(row["id"]) if row is not None else None


def _shadow_exists(store: Any, strategy: str, market_id: int) -> bool:
    try:
        row = store.db.execute(
            "SELECT 1 FROM trades WHERE strategy=? AND market_id=? LIMIT 1",
            (strategy, market_id),
        ).fetchone()
    except Exception:
        return False
    return row is not None


def _wrap_open_trade(store_class: type[Any]) -> None:
    original = getattr(store_class, "open_trade", None)
    if not callable(original):
        return
    if getattr(original, "_microprice_feature_shadows_v1", False):
        return

    @wraps(original)
    def open_trade_with_microprice_feature_shadows(
        self: Any,
        *,
        strategy: str,
        topic_id: int,
        market_id: int,
        side: str,
        entry: float,
        target: float | None,
        stake: float,
        fee_rate_bps: int,
        note: str,
        strategy_version: str | None = None,
        model_probability: float | None = None,
        model_edge: float | None = None,
        model_sigma: float | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        original(
            self,
            strategy=strategy,
            topic_id=topic_id,
            market_id=market_id,
            side=side,
            entry=entry,
            target=target,
            stake=stake,
            fee_rate_bps=fee_rate_bps,
            note=note,
            strategy_version=strategy_version,
            model_probability=model_probability,
            model_edge=model_edge,
            model_sigma=model_sigma,
            diagnostics=diagnostics,
        )

        normalized_strategy = str(strategy or "").strip().upper()
        normalized_side = str(side or "").strip().upper()
        if normalized_strategy != SOURCE_STRATEGY:
            return

        source_diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
        available_ask_size, ask_size_source = _available_ask_size(
            source_diagnostics
        )
        matches = _matching_strategies(
            entry=float(entry),
            side=normalized_side,
            available_ask_size=available_ask_size,
        )
        if not matches:
            return

        source_trade_id = _source_trade_id(self, int(market_id))
        selected_diagnostics = _selected_source_diagnostics(source_diagnostics)

        for shadow_strategy in matches:
            if _shadow_exists(self, shadow_strategy, int(market_id)):
                continue
            definition = FEATURE_SHADOW_DEFINITIONS[shadow_strategy]
            shadow_diagnostics = {
                "paper_only": True,
                "live_orders_affected": False,
                "shadow_only": True,
                "forward_only": True,
                "source_strategy": SOURCE_STRATEGY,
                "source_trade_id": source_trade_id,
                "source_strategy_version": strategy_version,
                "feature_shadow_version": FEATURE_SHADOW_VERSION,
                "family": definition["family"],
                "cohort": definition["cohort"],
                "rule": definition["rule"],
                "parameters": definition["parameters"],
                "source_entry_price": float(entry),
                "source_side": normalized_side,
                "available_ask_size": available_ask_size,
                "available_ask_size_source": ask_size_source,
                "source_diagnostics": selected_diagnostics,
            }
            original(
                self,
                strategy=shadow_strategy,
                topic_id=int(topic_id),
                market_id=int(market_id),
                side=normalized_side,
                entry=float(entry),
                target=target,
                stake=float(stake),
                fee_rate_bps=int(fee_rate_bps),
                note=(
                    f"{shadow_strategy} forward-only Shadow mirrored from "
                    f"{SOURCE_STRATEGY} trade #{source_trade_id or '?'}; "
                    "never live-forwarded"
                ),
                strategy_version=FEATURE_SHADOW_VERSION,
                model_probability=model_probability,
                model_edge=model_edge,
                model_sigma=model_sigma,
                diagnostics=shadow_diagnostics,
            )

    open_trade_with_microprice_feature_shadows._microprice_feature_shadows_v1 = True  # type: ignore[attr-defined]
    store_class.open_trade = open_trade_with_microprice_feature_shadows


def _database_state(store: Any) -> dict[str, Any]:
    placeholders = ",".join("?" for _ in FEATURE_SHADOW_STRATEGIES)
    try:
        rows = store.db.execute(
            f"""SELECT id, strategy, market_id, side, status, entry_price,
                       stake, pnl, opened_at, closed_at
                  FROM trades
                 WHERE strategy IN ({placeholders})
                 ORDER BY id ASC""",
            FEATURE_SHADOW_STRATEGIES,
        ).fetchall()
    except Exception:
        rows = []

    normalized = [dict(row) for row in rows]
    strategies: dict[str, dict[str, Any]] = {}
    for strategy in FEATURE_SHADOW_STRATEGIES:
        selected = [
            row for row in normalized
            if str(row.get("strategy")) == strategy
        ]
        settled = [
            row for row in selected
            if str(row.get("status")) in {"SETTLED_WIN", "SETTLED_LOSS"}
        ]
        wins = sum(
            str(row.get("status")) == "SETTLED_WIN"
            for row in settled
        )
        realized_pnl = sum(float(row.get("pnl") or 0.0) for row in settled)
        strategies[strategy] = {
            "trades": len(selected),
            "open": sum(
                str(row.get("status")) == "OPEN"
                for row in selected
            ),
            "settled": len(settled),
            "wins": int(wins),
            "losses": len(settled) - int(wins),
            "winRate": (
                float(wins) / len(settled) if settled else None
            ),
            "realizedPnl": realized_pnl,
            "averageEntryPrice": (
                sum(float(row["entry_price"]) for row in selected)
                / len(selected)
                if selected else None
            ),
            **FEATURE_SHADOW_DEFINITIONS[strategy],
        }

    families = {
        "DEADZONE_EXCLUSION": [DEADZONE_EXCLUDED_STRATEGY],
        "DIRECTION_SPLIT": [UP_ONLY_STRATEGY, DOWN_ONLY_STRATEGY],
        "LOW_PRICE_TAIL_DEPTH_SPLIT": [
            LOW_TAIL_THIN_STRATEGY,
            LOW_TAIL_THICK_STRATEGY,
        ],
    }
    return {
        "version": FEATURE_SHADOW_VERSION,
        "paperOnly": True,
        "liveOrdersAffected": False,
        "forwardOnly": True,
        "sourceStrategy": SOURCE_STRATEGY,
        "familyCount": len(families),
        "cohortCount": len(FEATURE_SHADOW_STRATEGIES),
        "families": families,
        "strategies": strategies,
        "recentTrades": normalized[-30:][::-1],
    }


def _inject_dashboard(payload: dict[str, Any], store: Any) -> dict[str, Any]:
    experiment = _database_state(store)
    research = payload.get("researchForward")
    if isinstance(research, dict):
        research["micropriceFeatureShadows"] = experiment
        strategies = research.get("strategies")
        if isinstance(strategies, dict):
            try:
                source_enabled = bool(
                    store.config().get("strategy_r_microprice_enabled", True)
                )
            except Exception:
                source_enabled = True
            for strategy, stats in experiment["strategies"].items():
                strategies[strategy] = {
                    "enabled": source_enabled,
                    "stakeUsdt": FEATURE_SHADOW_STAKE_USDT,
                    "selectedBacktestParameters": {
                        **stats.get("parameters", {}),
                        "sourceStrategy": SOURCE_STRATEGY,
                        "family": stats.get("family"),
                        "cohort": stats.get("cohort"),
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
                        "realizedPnl": float(stats.get("realizedPnl") or 0.0),
                        "minimum": 30,
                        "sourceMirrored": True,
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
    if getattr(original, "_microprice_feature_dashboard_v1", False):
        return

    @wraps(original)
    def dashboard_with_microprice_feature_shadows(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = original(self, *args, **kwargs)
        if not isinstance(payload, dict):
            return payload
        return _inject_dashboard(payload, self)

    dashboard_with_microprice_feature_shadows._microprice_feature_dashboard_v1 = True  # type: ignore[attr-defined]
    store_class.dashboard = dashboard_with_microprice_feature_shadows


def install_microprice_feature_shadows() -> None:
    engine_class = _realtime.MSeriesRealtimeEngine
    original_init = engine_class.__init__
    if getattr(original_init, "_microprice_feature_shadows_v1", False):
        return

    @wraps(original_init)
    def init_with_microprice_feature_shadows(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        original_init(self, *args, **kwargs)
        store = getattr(self, "store", None)
        if store is None:
            return
        store_class = type(store)
        _wrap_open_trade(store_class)
        _wrap_dashboard(store_class)

    init_with_microprice_feature_shadows._microprice_feature_shadows_v1 = True  # type: ignore[attr-defined]
    engine_class.__init__ = init_with_microprice_feature_shadows
