from __future__ import annotations

import json
import math
from contextlib import nullcontext
from datetime import datetime, timezone
from functools import wraps
from typing import Any

from . import m_realtime as _realtime
from . import microprice_variants as _variants


SOURCE_STRATEGY = "R_MICROPRICE_CONFIRM"
STALE_EXHAUSTED_GUARD_STRATEGY = "R_MICROPRICE_CONFIRM_STALE_EXHAUSTED_GUARD"
STALE_EXHAUSTED_GUARD_VERSION = "MICROPRICE_CONFIRM_STALE_EXHAUSTED_GUARD_V1"

MAX_GUARDED_ENTRY_PRICE = 0.55
MIN_EFFECTIVE_BOOK_AGE_MS = 450.0
MIN_DELAY_TRIGGER_MS = 60.0

_RUNTIME: dict[str, Any] = {
    "sourceMarkets": 0,
    "opened": 0,
    "blocked": 0,
    "missingDiagnostics": 0,
    "lastDecision": None,
}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp_ms(value: Any) -> float | None:
    number = _finite(value)
    if number is not None:
        if number > 10_000_000_000:
            return number
        if number > 1_000_000_000:
            return number * 1_000.0
        return None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp() * 1_000.0


def _lock(store: Any) -> Any:
    lock = getattr(store, "lock", None)
    return lock if lock is not None else nullcontext()


def _ensure_schema(store: Any) -> None:
    if bool(getattr(store, "_read_only", False)):
        return
    with _lock(store):
        store.db.execute(
            """CREATE TABLE IF NOT EXISTS
               microprice_confirm_stale_exhausted_decisions (
                   market_id INTEGER PRIMARY KEY,
                   source_trade_id INTEGER,
                   source_strategy_version TEXT,
                   source_side TEXT NOT NULL,
                   source_entry_price REAL NOT NULL,
                   eligible INTEGER NOT NULL,
                   block_reason TEXT,
                   book_age_ms REAL,
                   signal_to_open_ms REAL,
                   effective_book_age_ms REAL,
                   first_signal REAL,
                   final_signal REAL,
                   signal_decayed INTEGER,
                   opened INTEGER NOT NULL,
                   created_at TEXT NOT NULL
               )"""
        )
        store.db.execute(
            """CREATE INDEX IF NOT EXISTS
               microprice_confirm_stale_exhausted_created_idx
               ON microprice_confirm_stale_exhausted_decisions(created_at DESC)"""
        )
        store.db.commit()


def _trade_exists(store: Any, strategy: str, market_id: int) -> bool:
    try:
        return store.db.execute(
            "SELECT 1 FROM trades WHERE strategy=? AND market_id=? LIMIT 1",
            (strategy, int(market_id)),
        ).fetchone() is not None
    except Exception:
        return False


def _decision_exists(store: Any, market_id: int) -> bool:
    try:
        return store.db.execute(
            """SELECT 1 FROM microprice_confirm_stale_exhausted_decisions
               WHERE market_id=? LIMIT 1""",
            (int(market_id),),
        ).fetchone() is not None
    except Exception:
        return False


def _source_trade(store: Any, market_id: int) -> dict[str, Any] | None:
    try:
        row = store.db.execute(
            """SELECT * FROM trades
               WHERE strategy=? AND market_id=?
               ORDER BY id DESC LIMIT 1""",
            (SOURCE_STRATEGY, int(market_id)),
        ).fetchone()
    except Exception:
        return None
    return dict(row) if row is not None else None


def _selected_source_diagnostics(diagnostics: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "variant_mode",
        "pair_id",
        "source_strategy",
        "source_signal",
        "source_side",
        "selected_side",
        "confirmation_count",
        "confirmation_duration_ms",
        "midpoint_delta",
        "opposite_midpoint_delta",
        "book_age_ms",
        "book_skew_ms",
        "prediction_data_source",
        "direct_outcome_books",
        "signal_event_sequence",
        "signal_timestamp",
        "raw_top_ask",
        "raw_top_bid",
        "visible_ask_size",
        "requested_shares",
        "slippage_bps",
        "fee_bps",
        "rule",
        "samples",
    )
    return {key: diagnostics[key] for key in keys if key in diagnostics}


def _guard_metrics(
    source_trade: dict[str, Any] | None,
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    samples = diagnostics.get("samples")
    first_signal: float | None = None
    last_sample_signal: float | None = None
    if isinstance(samples, list) and samples:
        first = samples[0] if isinstance(samples[0], dict) else {}
        last = samples[-1] if isinstance(samples[-1], dict) else {}
        first_signal = _finite(first.get("score"))
        last_sample_signal = _finite(last.get("score"))

    final_signal = _finite(diagnostics.get("source_signal"))
    if final_signal is None:
        final_signal = last_sample_signal

    book_age_ms = _finite(diagnostics.get("book_age_ms"))
    opened_at_ms = _timestamp_ms(
        source_trade.get("opened_at") if source_trade else None
    )
    signal_at_ms = _timestamp_ms(diagnostics.get("signal_timestamp"))
    signal_to_open_ms = (
        max(0.0, opened_at_ms - signal_at_ms)
        if opened_at_ms is not None and signal_at_ms is not None
        else None
    )
    effective_book_age_ms = (
        book_age_ms + signal_to_open_ms
        if book_age_ms is not None and signal_to_open_ms is not None
        else None
    )
    signal_decayed = (
        abs(final_signal) < abs(first_signal)
        if first_signal is not None and final_signal is not None
        else None
    )
    return {
        "book_age_ms": book_age_ms,
        "signal_to_open_ms": signal_to_open_ms,
        "effective_book_age_ms": effective_book_age_ms,
        "first_signal": first_signal,
        "final_signal": final_signal,
        "signal_decayed": signal_decayed,
    }


def _block_reason(entry: float, metrics: dict[str, Any]) -> str | None:
    if entry > MAX_GUARDED_ENTRY_PRICE:
        return None
    effective_age = _finite(metrics.get("effective_book_age_ms"))
    delay = _finite(metrics.get("signal_to_open_ms"))
    decayed = metrics.get("signal_decayed")
    if effective_age is None or delay is None or decayed is None:
        return "MISSING_REQUIRED_DIAGNOSTICS"
    if (
        effective_age >= MIN_EFFECTIVE_BOOK_AGE_MS
        and (bool(decayed) or delay >= MIN_DELAY_TRIGGER_MS)
    ):
        if bool(decayed) and delay >= MIN_DELAY_TRIGGER_MS:
            return "LOW_ENTRY_STALE_DECAYED_AND_DELAYED"
        if bool(decayed):
            return "LOW_ENTRY_STALE_AND_DECAYED"
        return "LOW_ENTRY_STALE_AND_DELAYED"
    return None


def _record_decision(
    store: Any,
    *,
    market_id: int,
    source_trade_id: int | None,
    source_strategy_version: str | None,
    source_side: str,
    source_entry_price: float,
    metrics: dict[str, Any],
    eligible: bool,
    block_reason: str | None,
    opened: bool,
) -> None:
    try:
        store.db.execute(
            """INSERT OR IGNORE INTO
               microprice_confirm_stale_exhausted_decisions(
                   market_id, source_trade_id, source_strategy_version,
                   source_side, source_entry_price, eligible, block_reason,
                   book_age_ms, signal_to_open_ms, effective_book_age_ms,
                   first_signal, final_signal, signal_decayed, opened, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(market_id),
                source_trade_id,
                source_strategy_version,
                source_side,
                float(source_entry_price),
                1 if eligible else 0,
                block_reason,
                metrics.get("book_age_ms"),
                metrics.get("signal_to_open_ms"),
                metrics.get("effective_book_age_ms"),
                metrics.get("first_signal"),
                metrics.get("final_signal"),
                (
                    None
                    if metrics.get("signal_decayed") is None
                    else 1 if bool(metrics.get("signal_decayed")) else 0
                ),
                1 if opened else 0,
                _utc_iso(),
            ),
        )
        store.db.commit()
    except Exception:
        return


def _wrap_open_trade(store_class: type[Any]) -> None:
    original = getattr(store_class, "open_trade", None)
    if not callable(original):
        return
    if getattr(original, "_microprice_stale_exhausted_guard_v1", False):
        return

    @wraps(original)
    def open_trade_with_stale_exhausted_guard(
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
        if normalized_strategy != SOURCE_STRATEGY or normalized_side not in {"UP", "DOWN"}:
            return
        if _decision_exists(self, int(market_id)):
            return

        source_trade = _source_trade(self, int(market_id))
        source_diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
        source_entry = float(entry)
        metrics = _guard_metrics(source_trade, source_diagnostics)
        reason = _block_reason(source_entry, metrics)
        eligible = reason is None
        source_trade_id = int(source_trade["id"]) if source_trade is not None else None
        source_version = (
            str(source_trade.get("strategy_version"))
            if source_trade is not None and source_trade.get("strategy_version") is not None
            else strategy_version
        )

        opened = False
        if eligible and not _trade_exists(self, STALE_EXHAUSTED_GUARD_STRATEGY, int(market_id)):
            guard_diagnostics = {
                "paper_only": True,
                "live_orders_affected": False,
                "shadow_only": True,
                "forward_only": True,
                "guard_version": STALE_EXHAUSTED_GUARD_VERSION,
                "source_strategy": SOURCE_STRATEGY,
                "source_trade_id": source_trade_id,
                "source_strategy_version": source_version,
                "variant_mode": "FOLLOW_V2_WITH_STALE_EXHAUSTED_GUARD",
                "source_side": normalized_side,
                "source_entry_price": source_entry,
                "guard_rule": {
                    "maximumGuardedEntryPriceInclusive": MAX_GUARDED_ENTRY_PRICE,
                    "minimumEffectiveBookAgeMsInclusive": MIN_EFFECTIVE_BOOK_AGE_MS,
                    "minimumSignalToOpenDelayMsInclusive": MIN_DELAY_TRIGGER_MS,
                    "effectiveBookAgeFormula": "book_age_ms + signal_to_open_ms",
                    "blockWhen": "low entry AND stale effective age AND (signal decayed OR delay threshold)",
                    "missingLowEntryDiagnostics": "fail_closed",
                },
                "guard_metrics": metrics,
                "source_diagnostics": _selected_source_diagnostics(source_diagnostics),
            }
            original(
                self,
                strategy=STALE_EXHAUSTED_GUARD_STRATEGY,
                topic_id=int(topic_id),
                market_id=int(market_id),
                side=normalized_side,
                entry=source_entry,
                target=None,
                stake=float(stake),
                fee_rate_bps=int(fee_rate_bps),
                note=(
                    f"{STALE_EXHAUSTED_GUARD_STRATEGY} mirrored from "
                    f"{SOURCE_STRATEGY} trade #{source_trade_id or '?'}; "
                    "low-price stale/decay guard passed; paper only"
                ),
                strategy_version=STALE_EXHAUSTED_GUARD_VERSION,
                model_probability=model_probability,
                model_edge=model_edge,
                model_sigma=model_sigma,
                diagnostics=guard_diagnostics,
            )
            opened = True
            _RUNTIME["opened"] += 1
        else:
            _RUNTIME["blocked"] += 1
            if reason == "MISSING_REQUIRED_DIAGNOSTICS":
                _RUNTIME["missingDiagnostics"] += 1

        _record_decision(
            self,
            market_id=int(market_id),
            source_trade_id=source_trade_id,
            source_strategy_version=source_version,
            source_side=normalized_side,
            source_entry_price=source_entry,
            metrics=metrics,
            eligible=eligible,
            block_reason=reason,
            opened=opened,
        )
        _RUNTIME["sourceMarkets"] += 1
        _RUNTIME["lastDecision"] = {
            "marketId": int(market_id),
            "strategy": STALE_EXHAUSTED_GUARD_STRATEGY,
            "status": "MIRRORED" if opened else "BLOCKED",
            "reason": reason,
            "side": normalized_side,
            "entry": source_entry,
            "metrics": metrics,
            "version": STALE_EXHAUSTED_GUARD_VERSION,
        }

    open_trade_with_stale_exhausted_guard._microprice_stale_exhausted_guard_v1 = True  # type: ignore[attr-defined]
    store_class.open_trade = open_trade_with_stale_exhausted_guard


def _database_state(store: Any) -> dict[str, Any]:
    try:
        rows = [
            dict(row)
            for row in store.db.execute(
                """SELECT id, strategy, market_id, side, status, entry_price,
                          exit_price, stake, fees, pnl, opened_at, closed_at
                     FROM trades WHERE strategy=? ORDER BY id ASC""",
                (STALE_EXHAUSTED_GUARD_STRATEGY,),
            ).fetchall()
        ]
    except Exception:
        rows = []
    try:
        decisions = [
            dict(row)
            for row in store.db.execute(
                """SELECT * FROM microprice_confirm_stale_exhausted_decisions
                   ORDER BY created_at ASC, market_id ASC"""
            ).fetchall()
        ]
    except Exception:
        decisions = []

    terminal = [row for row in rows if row.get("pnl") is not None]
    wins = sum(float(row.get("pnl") or 0.0) > 0 for row in terminal)
    blocked_reasons: dict[str, int] = {}
    for decision in decisions:
        reason = decision.get("block_reason")
        if reason:
            blocked_reasons[str(reason)] = blocked_reasons.get(str(reason), 0) + 1

    stats = {
        "trades": len(rows),
        "open": sum(str(row.get("status")) == "OPEN" for row in rows),
        "settled": len(terminal),
        "wins": int(wins),
        "losses": len(terminal) - int(wins),
        "winRate": float(wins) / len(terminal) if terminal else None,
        "realizedPnl": sum(float(row.get("pnl") or 0.0) for row in terminal),
        "averageEntryPrice": (
            sum(float(row["entry_price"]) for row in rows) / len(rows)
            if rows else None
        ),
        "title": "Microprice Confirm V2 · 舊簿／衰退防護",
        "mode": "V2 CONFIRM + STALE/DECAY GUARD",
        "rule": (
            "mirror R_MICROPRICE_CONFIRM except entry<=0.55 with "
            "effective book age>=450ms and either decayed signal or "
            "signal-to-open delay>=60ms"
        ),
        "parameters": {
            "maximumGuardedEntryPriceInclusive": MAX_GUARDED_ENTRY_PRICE,
            "minimumEffectiveBookAgeMsInclusive": MIN_EFFECTIVE_BOOK_AGE_MS,
            "minimumSignalToOpenDelayMsInclusive": MIN_DELAY_TRIGGER_MS,
            "effectiveBookAgeFormula": "book_age_ms + signal_to_open_ms",
            "missingLowEntryDiagnostics": "fail_closed",
        },
    }
    return {
        "version": STALE_EXHAUSTED_GUARD_VERSION,
        "paperOnly": True,
        "liveOrdersAffected": False,
        "forwardOnly": True,
        "sourceStrategy": SOURCE_STRATEGY,
        "sourceMarkets": len(decisions),
        "blocked": sum(blocked_reasons.values()),
        "blockReasons": blocked_reasons,
        "strategies": {STALE_EXHAUSTED_GUARD_STRATEGY: stats},
        "recentDecisions": decisions[-20:][::-1],
        "recentTrades": rows[-30:][::-1],
        "runtime": {
            **_RUNTIME,
            "rules": {
                STALE_EXHAUSTED_GUARD_STRATEGY: stats["parameters"],
            },
        },
    }


def _inject_dashboard(payload: dict[str, Any], store: Any) -> dict[str, Any]:
    experiment = _database_state(store)
    stats = experiment["strategies"][STALE_EXHAUSTED_GUARD_STRATEGY]
    research = payload.get("researchForward")
    if isinstance(research, dict):
        research["micropriceConfirmStaleExhaustedGuard"] = experiment
        strategies = research.get("strategies")
        if isinstance(strategies, dict):
            try:
                source_enabled = bool(
                    store.config().get("strategy_r_microprice_enabled", True)
                )
            except Exception:
                source_enabled = True
            strategies[STALE_EXHAUSTED_GUARD_STRATEGY] = {
                "enabled": source_enabled,
                "stakeUsdt": _variants.MICROPRICE_STAKE_USDT,
                "selectedBacktestParameters": {
                    **stats["parameters"],
                    "sourceStrategy": SOURCE_STRATEGY,
                    "sourceConfirmationVersion": _variants.MICROPRICE_VARIANT_VERSION,
                    "forwardOnly": True,
                },
                "chronologicalValidation": {
                    "status": "ANALYZABLE" if int(stats["settled"]) >= 30 else "COLLECTING",
                    "samples": int(stats["trades"]),
                    "settled": int(stats["settled"]),
                    "wins": int(stats["wins"]),
                    "losses": int(stats["losses"]),
                    "realizedPnl": float(stats["realizedPnl"]),
                    "minimum": 30,
                    "sourceMirrored": True,
                },
                "paperOnly": True,
                "liveOrdersAffected": False,
            }
    summaries = payload.get("summaries")
    if isinstance(summaries, dict):
        summaries[STALE_EXHAUSTED_GUARD_STRATEGY] = {
            "trades": int(stats["trades"]),
            "open": int(stats["open"]),
            "wins": int(stats["wins"]),
            "losses": int(stats["losses"]),
            "realized_pnl": float(stats["realizedPnl"]),
        }
    return payload


def _wrap_dashboard(store_class: type[Any]) -> None:
    original = getattr(store_class, "dashboard", None)
    if not callable(original):
        return
    if getattr(original, "_microprice_stale_exhausted_guard_v1", False):
        return

    @wraps(original)
    def dashboard_with_stale_exhausted_guard(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = original(self, *args, **kwargs)
        if not isinstance(payload, dict):
            return payload
        return _inject_dashboard(payload, self)

    dashboard_with_stale_exhausted_guard._microprice_stale_exhausted_guard_v1 = True  # type: ignore[attr-defined]
    store_class.dashboard = dashboard_with_stale_exhausted_guard


def install_microprice_confirm_stale_exhausted_guard() -> None:
    engine_class = _realtime.MSeriesRealtimeEngine
    original_init = engine_class.__init__
    if getattr(original_init, "_microprice_stale_exhausted_guard_v1", False):
        return

    @wraps(original_init)
    def init_with_stale_exhausted_guard(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        original_init(self, *args, **kwargs)
        store = getattr(self, "store", None)
        if store is not None:
            _ensure_schema(store)
            _wrap_open_trade(type(store))
            _wrap_dashboard(type(store))

    init_with_stale_exhausted_guard._microprice_stale_exhausted_guard_v1 = True  # type: ignore[attr-defined]
    engine_class.__init__ = init_with_stale_exhausted_guard
