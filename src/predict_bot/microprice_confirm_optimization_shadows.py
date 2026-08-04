from __future__ import annotations

import json
import math
from contextlib import nullcontext
from datetime import datetime, timezone
from functools import wraps
from typing import Any

from . import m_realtime as _realtime
from . import microprice_variants as _variants
from .core import taker_fee


SOURCE_STRATEGY = "R_MICROPRICE_CONFIRM"
PRICE_SIDE_GUARD_STRATEGY = "R_MICROPRICE_CONFIRM_PRICE_SIDE_GUARD"
EXIT_098_STRATEGY = "R_MICROPRICE_CONFIRM_EXIT_098"
OPTIMIZATION_VERSION = "MICROPRICE_CONFIRM_OPTIMIZATION_V1"
OPTIMIZATION_STRATEGIES = (
    PRICE_SIDE_GUARD_STRATEGY,
    EXIT_098_STRATEGY,
)

UP_BLOCK_BELOW = 0.40
DOWN_BLOCK_MIN_INCLUSIVE = 0.50
DOWN_BLOCK_MAX_EXCLUSIVE = 0.60
EXIT_TARGET_PRICE = 0.98

_ACTIVE_TRACKER: _variants.MicropriceVariantTracker | None = None
_RUNTIME: dict[str, Any] = {
    "mirroredSourceMarkets": 0,
    "priceSideGuardOpened": 0,
    "priceSideGuardBlocked": 0,
    "exit098Opened": 0,
    "targetFills": 0,
    "targetBlockedDepth": 0,
    "targetBlockedBook": 0,
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


def _lock(store: Any) -> Any:
    lock = getattr(store, "lock", None)
    return lock if lock is not None else nullcontext()


def _ensure_schema(store: Any) -> None:
    if bool(getattr(store, "_read_only", False)):
        return
    with _lock(store):
        store.db.execute(
            """CREATE TABLE IF NOT EXISTS
               microprice_confirm_optimization_decisions (
                   market_id INTEGER PRIMARY KEY,
                   source_trade_id INTEGER,
                   source_strategy_version TEXT,
                   source_side TEXT NOT NULL,
                   source_entry_price REAL NOT NULL,
                   price_side_guard_eligible INTEGER NOT NULL,
                   price_side_guard_block_reason TEXT,
                   exit_098_opened INTEGER NOT NULL,
                   exit_098_target_enabled INTEGER NOT NULL,
                   created_at TEXT NOT NULL
               )"""
        )
        store.db.execute(
            """CREATE INDEX IF NOT EXISTS
               microprice_confirm_optimization_created_idx
               ON microprice_confirm_optimization_decisions(created_at DESC)"""
        )
        store.db.commit()


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


def _trade_exists(store: Any, strategy: str, market_id: int) -> bool:
    try:
        return store.db.execute(
            "SELECT 1 FROM trades WHERE strategy=? AND market_id=? LIMIT 1",
            (strategy, int(market_id)),
        ).fetchone() is not None
    except Exception:
        return False


def _price_side_block_reason(side: str, entry: float) -> str | None:
    if side == "UP" and entry < UP_BLOCK_BELOW:
        return "UP_ENTRY_BELOW_040"
    if (
        side == "DOWN"
        and DOWN_BLOCK_MIN_INCLUSIVE
        <= entry
        < DOWN_BLOCK_MAX_EXCLUSIVE
    ):
        return "DOWN_ENTRY_050_060"
    return None


def _selected_source_diagnostics(
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    selected_keys = (
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
    return {
        key: diagnostics[key]
        for key in selected_keys
        if key in diagnostics
    }


def _decision_exists(store: Any, market_id: int) -> bool:
    try:
        return store.db.execute(
            """SELECT 1 FROM microprice_confirm_optimization_decisions
               WHERE market_id=? LIMIT 1""",
            (int(market_id),),
        ).fetchone() is not None
    except Exception:
        return False


def _record_decision(
    store: Any,
    *,
    market_id: int,
    source_trade_id: int | None,
    source_strategy_version: str | None,
    source_side: str,
    source_entry_price: float,
    guard_eligible: bool,
    guard_block_reason: str | None,
    exit_opened: bool,
    exit_target_enabled: bool,
) -> None:
    try:
        store.db.execute(
            """INSERT OR IGNORE INTO
               microprice_confirm_optimization_decisions(
                   market_id, source_trade_id, source_strategy_version,
                   source_side, source_entry_price,
                   price_side_guard_eligible,
                   price_side_guard_block_reason,
                   exit_098_opened, exit_098_target_enabled,
                   created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(market_id),
                source_trade_id,
                source_strategy_version,
                source_side,
                float(source_entry_price),
                1 if guard_eligible else 0,
                guard_block_reason,
                1 if exit_opened else 0,
                1 if exit_target_enabled else 0,
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
    if getattr(original, "_microprice_confirm_optimization_v1", False):
        return

    @wraps(original)
    def open_trade_with_microprice_confirm_optimization(
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
        if (
            normalized_strategy != SOURCE_STRATEGY
            or normalized_side not in {"UP", "DOWN"}
        ):
            return

        source_entry = float(entry)
        source_trade = _source_trade(self, int(market_id))
        source_diagnostics = (
            diagnostics if isinstance(diagnostics, dict) else {}
        )
        block_reason = _price_side_block_reason(
            normalized_side,
            source_entry,
        )
        guard_eligible = block_reason is None
        source_trade_id = (
            int(source_trade["id"])
            if source_trade is not None
            else None
        )
        source_version = (
            str(source_trade.get("strategy_version"))
            if source_trade is not None
            and source_trade.get("strategy_version") is not None
            else strategy_version
        )
        selected_source = _selected_source_diagnostics(
            source_diagnostics
        )

        guard_opened = False
        if (
            guard_eligible
            and not _trade_exists(
                self,
                PRICE_SIDE_GUARD_STRATEGY,
                int(market_id),
            )
        ):
            guard_diagnostics = {
                "paper_only": True,
                "live_orders_affected": False,
                "shadow_only": True,
                "forward_only": True,
                "optimization_version": OPTIMIZATION_VERSION,
                "source_strategy": SOURCE_STRATEGY,
                "source_trade_id": source_trade_id,
                "source_strategy_version": source_version,
                "variant_mode": "FOLLOW_V2_WITH_PRICE_SIDE_GUARD",
                "source_side": normalized_side,
                "source_entry_price": source_entry,
                "guard_rule": {
                    "blockUpEntryBelowExclusive": UP_BLOCK_BELOW,
                    "blockDownEntryMinInclusive": (
                        DOWN_BLOCK_MIN_INCLUSIVE
                    ),
                    "blockDownEntryMaxExclusive": (
                        DOWN_BLOCK_MAX_EXCLUSIVE
                    ),
                },
                "source_diagnostics": selected_source,
            }
            original(
                self,
                strategy=PRICE_SIDE_GUARD_STRATEGY,
                topic_id=int(topic_id),
                market_id=int(market_id),
                side=normalized_side,
                entry=source_entry,
                target=None,
                stake=float(stake),
                fee_rate_bps=int(fee_rate_bps),
                note=(
                    f"{PRICE_SIDE_GUARD_STRATEGY} mirrored from "
                    f"{SOURCE_STRATEGY} trade #{source_trade_id or '?'}; "
                    "V2 confirmation retained; direction-price guard passed; "
                    "paper only"
                ),
                strategy_version=OPTIMIZATION_VERSION,
                model_probability=model_probability,
                model_edge=model_edge,
                model_sigma=model_sigma,
                diagnostics=guard_diagnostics,
            )
            guard_opened = True
            _RUNTIME["priceSideGuardOpened"] += 1
        elif not guard_eligible:
            _RUNTIME["priceSideGuardBlocked"] += 1
            _RUNTIME["lastDecision"] = {
                "marketId": int(market_id),
                "strategy": PRICE_SIDE_GUARD_STRATEGY,
                "status": "BLOCKED",
                "reason": block_reason,
                "side": normalized_side,
                "entry": source_entry,
                "version": OPTIMIZATION_VERSION,
            }

        exit_target_enabled = source_entry < EXIT_TARGET_PRICE
        exit_opened = False
        if not _trade_exists(
            self,
            EXIT_098_STRATEGY,
            int(market_id),
        ):
            exit_diagnostics = {
                "paper_only": True,
                "live_orders_affected": False,
                "shadow_only": True,
                "forward_only": True,
                "optimization_version": OPTIMIZATION_VERSION,
                "source_strategy": SOURCE_STRATEGY,
                "source_trade_id": source_trade_id,
                "source_strategy_version": source_version,
                "variant_mode": "FOLLOW_V2_EXIT_AT_098",
                "source_side": normalized_side,
                "source_entry_price": source_entry,
                "target_exit": {
                    "enabled": exit_target_enabled,
                    "targetPrice": (
                        EXIT_TARGET_PRICE
                        if exit_target_enabled
                        else None
                    ),
                    "executionModel": (
                        "observed_bid_full_depth_limit_v1"
                    ),
                    "requiresDirectDualTokenRest": True,
                    "requiresFullVisibleBidDepth": True,
                    "disabledReason": (
                        None
                        if exit_target_enabled
                        else "source entry is already at or above 0.98"
                    ),
                },
                "source_diagnostics": selected_source,
            }
            original(
                self,
                strategy=EXIT_098_STRATEGY,
                topic_id=int(topic_id),
                market_id=int(market_id),
                side=normalized_side,
                entry=source_entry,
                target=(
                    EXIT_TARGET_PRICE
                    if exit_target_enabled
                    else None
                ),
                stake=float(stake),
                fee_rate_bps=int(fee_rate_bps),
                note=(
                    f"{EXIT_098_STRATEGY} mirrored from "
                    f"{SOURCE_STRATEGY} trade #{source_trade_id or '?'}; "
                    + (
                        "sell target 0.98 against observed full-depth bid; "
                        if unfilled hold to official settlement; paper only"
                        if exit_target_enabled
                        else "entry >=0.98 so target disabled; hold to official "
                        "settlement; paper only"
                    )
                ),
                strategy_version=OPTIMIZATION_VERSION,
                model_probability=model_probability,
                model_edge=model_edge,
                model_sigma=model_sigma,
                diagnostics=exit_diagnostics,
            )
            exit_opened = True
            _RUNTIME["exit098Opened"] += 1

        if not _decision_exists(self, int(market_id)):
            _record_decision(
                self,
                market_id=int(market_id),
                source_trade_id=source_trade_id,
                source_strategy_version=source_version,
                source_side=normalized_side,
                source_entry_price=source_entry,
                guard_eligible=guard_eligible,
                guard_block_reason=block_reason,
                exit_opened=exit_opened,
                exit_target_enabled=exit_target_enabled,
            )
            _RUNTIME["mirroredSourceMarkets"] += 1

        if guard_opened or exit_opened:
            _RUNTIME["lastDecision"] = {
                "marketId": int(market_id),
                "status": "MIRRORED",
                "sourceStrategy": SOURCE_STRATEGY,
                "sourceSide": normalized_side,
                "sourceEntry": source_entry,
                "guardOpened": guard_opened,
                "guardBlockReason": block_reason,
                "exit098Opened": exit_opened,
                "exit098TargetEnabled": exit_target_enabled,
                "version": OPTIMIZATION_VERSION,
            }

    open_trade_with_microprice_confirm_optimization._microprice_confirm_optimization_v1 = True  # type: ignore[attr-defined]
    store_class.open_trade = open_trade_with_microprice_confirm_optimization


def _update_target_blocked(
    store: Any,
    trade: Any,
    snapshot: dict[str, Any],
    *,
    reason: str,
    observed_bid: float | None,
    visible_bid_size: float | None,
) -> None:
    diagnostics = (
        json.loads(trade["diagnostics_json"])
        if trade["diagnostics_json"]
        else {}
    )
    prior = diagnostics.get("target_exit_blocked") or {}
    diagnostics["target_exit_blocked"] = {
        "firstSeenAt": prior.get(
            "firstSeenAt",
            str(snapshot.get("timestamp") or _utc_iso()),
        ),
        "lastSeenAt": str(
            snapshot.get("timestamp") or _utc_iso()
        ),
        "observations": int(prior.get("observations", 0)) + 1,
        "reason": reason,
        "target": EXIT_TARGET_PRICE,
        "observedBid": observed_bid,
        "visibleBidSize": visible_bid_size,
        "requiredShares": float(trade["shares"]),
        "secondsLeft": _finite(snapshot.get("seconds_left")),
    }
    store.db.execute(
        """UPDATE trades SET diagnostics_json=?
           WHERE id=? AND status='OPEN'""",
        (
            json.dumps(diagnostics, sort_keys=True, default=str),
            int(trade["id"]),
        ),
    )


def _process_exit_098_targets(
    tracker: Any,
    snapshot: dict[str, Any],
    context: dict[str, Any],
) -> None:
    if not _variants._direct_context_is_safe(tracker.engine, context):
        return

    try:
        market_id = int(snapshot["market_id"])
    except (KeyError, TypeError, ValueError):
        return

    book_age = _finite(snapshot.get("book_age_ms"))
    book_skew = _finite(snapshot.get("book_skew_ms"))
    if (
        book_age is None
        or book_skew is None
        or book_age < 0
        or book_skew < 0
        or book_age > _variants.MICROPRICE_MAX_BOOK_AGE_MS
        or book_skew > _variants.MICROPRICE_MAX_BOOK_SKEW_MS
    ):
        _RUNTIME["targetBlockedBook"] += 1
        return

    store = tracker.store
    with _lock(store):
        trades = store.db.execute(
            """SELECT * FROM trades
               WHERE strategy=? AND market_id=?
                 AND status='OPEN' AND target_price=?""",
            (
                EXIT_098_STRATEGY,
                market_id,
                EXIT_TARGET_PRICE,
            ),
        ).fetchall()
        changed = False
        for trade in trades:
            side = str(trade["side"])
            bid = _finite(snapshot.get(f"{side.lower()}_bid"))
            bid_size = _finite(
                snapshot.get(f"{side.lower()}_bid_size")
            )
            if bid is None or not 0 <= bid <= 1:
                _update_target_blocked(
                    store,
                    trade,
                    snapshot,
                    reason="missing_or_invalid_bid",
                    observed_bid=None,
                    visible_bid_size=bid_size,
                )
                changed = True
                continue
            if bid < EXIT_TARGET_PRICE:
                continue
            if bid_size is None or bid_size <= 0:
                _update_target_blocked(
                    store,
                    trade,
                    snapshot,
                    reason="missing_or_invalid_bid_size",
                    observed_bid=bid,
                    visible_bid_size=None,
                )
                _RUNTIME["targetBlockedDepth"] += 1
                changed = True
                continue
            if bid_size + 1e-12 < float(trade["shares"]):
                _update_target_blocked(
                    store,
                    trade,
                    snapshot,
                    reason="insufficient_bid_size",
                    observed_bid=bid,
                    visible_bid_size=bid_size,
                )
                _RUNTIME["targetBlockedDepth"] += 1
                changed = True
                continue

            fee_bps = int(trade["fee_rate_bps"])
            shares = float(trade["shares"])
            exit_fee = taker_fee(
                shares,
                EXIT_TARGET_PRICE,
                fee_bps,
            )
            total_fees = float(trade["fees"]) + exit_fee
            exit_value = shares * EXIT_TARGET_PRICE
            pnl = exit_value - float(trade["stake"]) - total_fees
            closed_at = _utc_iso()
            diagnostics = (
                json.loads(trade["diagnostics_json"])
                if trade["diagnostics_json"]
                else {}
            )
            diagnostics["target_exit"] = {
                "executionModel": (
                    "observed_bid_full_depth_limit_v1"
                ),
                "triggeredAt": closed_at,
                "signalTimestamp": str(
                    snapshot.get("timestamp") or closed_at
                ),
                "signalEventSequence": (
                    context.get("signal_event_sequence")
                    or snapshot.get("signal_event_sequence")
                ),
                "secondsLeft": _finite(
                    snapshot.get("seconds_left")
                ),
                "side": side,
                "target": EXIT_TARGET_PRICE,
                "observedBid": bid,
                "visibleBidSize": bid_size,
                "requiredShares": shares,
                "exitFee": exit_fee,
                "totalFees": total_fees,
                "pnl": pnl,
                "bookAgeMs": book_age,
                "bookSkewMs": book_skew,
            }
            store.db.execute(
                """UPDATE trades
                   SET status='TARGET_FILLED', exit_price=?,
                       fees=?, pnl=?, closed_at=?,
                       note=note || ?, diagnostics_json=?
                   WHERE id=? AND status='OPEN'""",
                (
                    EXIT_TARGET_PRICE,
                    total_fees,
                    pnl,
                    closed_at,
                    (
                        "; target limit filled 0.980 "
                        f"(observed bid={bid:.3f})"
                    ),
                    json.dumps(
                        diagnostics,
                        sort_keys=True,
                        default=str,
                    ),
                    int(trade["id"]),
                ),
            )
            _RUNTIME["targetFills"] += 1
            _RUNTIME["lastDecision"] = {
                "marketId": market_id,
                "strategy": EXIT_098_STRATEGY,
                "status": "TARGET_FILLED",
                "target": EXIT_TARGET_PRICE,
                "observedBid": bid,
                "pnl": pnl,
                "version": OPTIMIZATION_VERSION,
            }
            changed = True
        if changed:
            store.db.commit()


def _patch_tracker_process() -> None:
    tracker_class = _variants.MicropriceVariantTracker
    original = tracker_class.process
    if getattr(original, "_microprice_confirm_optimization_v1", False):
        return

    @wraps(original)
    def process_with_microprice_confirm_optimization(
        self: Any,
        snapshot: dict[str, Any],
        fee_bps: int,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        _process_exit_098_targets(
            self,
            snapshot,
            dict(context or {}),
        )
        return original(
            self,
            snapshot,
            int(fee_bps),
            context,
        )

    process_with_microprice_confirm_optimization._microprice_confirm_optimization_v1 = True  # type: ignore[attr-defined]
    tracker_class.process = process_with_microprice_confirm_optimization


def _database_state(store: Any) -> dict[str, Any]:
    placeholders = ",".join("?" for _ in OPTIMIZATION_STRATEGIES)
    try:
        rows = [
            dict(row)
            for row in store.db.execute(
                f"""SELECT id, strategy, market_id, side, status,
                           entry_price, target_price, exit_price, stake,
                           fees, pnl, opened_at, closed_at
                      FROM trades
                     WHERE strategy IN ({placeholders})
                     ORDER BY id ASC""",
                OPTIMIZATION_STRATEGIES,
            ).fetchall()
        ]
    except Exception:
        rows = []

    try:
        decision_rows = [
            dict(row)
            for row in store.db.execute(
                """SELECT * FROM
                   microprice_confirm_optimization_decisions
                   ORDER BY created_at ASC, market_id ASC"""
            ).fetchall()
        ]
    except Exception:
        decision_rows = []

    strategies: dict[str, dict[str, Any]] = {}
    definitions = {
        PRICE_SIDE_GUARD_STRATEGY: {
            "title": "Microprice Confirm V2 · 方向價格防護",
            "mode": "V2 CONFIRM + PRICE/SIDE GUARD",
            "rule": (
                "mirror R_MICROPRICE_CONFIRM except UP entry <0.40 and "
                "DOWN 0.50<=entry<0.60"
            ),
            "parameters": {
                "blockUpEntryBelowExclusive": UP_BLOCK_BELOW,
                "blockDownEntryMinInclusive": (
                    DOWN_BLOCK_MIN_INCLUSIVE
                ),
                "blockDownEntryMaxExclusive": (
                    DOWN_BLOCK_MAX_EXCLUSIVE
                ),
            },
        },
        EXIT_098_STRATEGY: {
            "title": "Microprice Confirm V2 · 0.98 提前退出",
            "mode": "V2 CONFIRM + SELL TARGET 0.98",
            "rule": (
                "mirror every R_MICROPRICE_CONFIRM entry; sell all shares "
                "at 0.98 when observed bid and full depth permit, otherwise "
                "hold to official settlement"
            ),
            "parameters": {
                "targetPrice": EXIT_TARGET_PRICE,
                "targetExecutionModel": (
                    "observed_bid_full_depth_limit_v1"
                ),
                "requiresDirectDualTokenRest": True,
                "requiresFullVisibleBidDepth": True,
                "disableTargetWhenEntryAtOrAbove": EXIT_TARGET_PRICE,
            },
        },
    }
    for strategy in OPTIMIZATION_STRATEGIES:
        selected = [
            row for row in rows
            if str(row.get("strategy")) == strategy
        ]
        terminal = [
            row for row in selected
            if row.get("pnl") is not None
        ]
        wins = sum(
            float(row.get("pnl") or 0.0) > 0
            for row in terminal
        )
        strategies[strategy] = {
            "trades": len(selected),
            "open": sum(
                str(row.get("status")) == "OPEN"
                for row in selected
            ),
            "settled": len(terminal),
            "wins": int(wins),
            "losses": len(terminal) - int(wins),
            "winRate": (
                float(wins) / len(terminal)
                if terminal else None
            ),
            "realizedPnl": sum(
                float(row.get("pnl") or 0.0)
                for row in terminal
            ),
            "averageEntryPrice": (
                sum(float(row["entry_price"]) for row in selected)
                / len(selected)
                if selected else None
            ),
            "averageExitPrice": (
                sum(
                    float(row["exit_price"])
                    for row in terminal
                    if row.get("exit_price") is not None
                )
                / sum(
                    row.get("exit_price") is not None
                    for row in terminal
                )
                if any(
                    row.get("exit_price") is not None
                    for row in terminal
                )
                else None
            ),
            "targetFilled": sum(
                str(row.get("status")) == "TARGET_FILLED"
                for row in selected
            ),
            **definitions[strategy],
        }

    blocked_counts: dict[str, int] = {}
    for row in decision_rows:
        reason = row.get("price_side_guard_block_reason")
        if reason:
            blocked_counts[str(reason)] = (
                blocked_counts.get(str(reason), 0) + 1
            )

    return {
        "version": OPTIMIZATION_VERSION,
        "paperOnly": True,
        "liveOrdersAffected": False,
        "forwardOnly": True,
        "sourceStrategy": SOURCE_STRATEGY,
        "strategyCount": len(OPTIMIZATION_STRATEGIES),
        "sourceMarkets": len(decision_rows),
        "priceSideGuardBlocked": sum(blocked_counts.values()),
        "priceSideGuardBlockReasons": blocked_counts,
        "exit098TargetEnabled": sum(
            int(bool(row.get("exit_098_target_enabled")))
            for row in decision_rows
        ),
        "strategies": strategies,
        "recentDecisions": decision_rows[-20:][::-1],
        "recentTrades": rows[-30:][::-1],
        "runtime": {
            **_RUNTIME,
            "rules": {
                PRICE_SIDE_GUARD_STRATEGY: definitions[
                    PRICE_SIDE_GUARD_STRATEGY
                ]["parameters"],
                EXIT_098_STRATEGY: definitions[
                    EXIT_098_STRATEGY
                ]["parameters"],
            },
        },
    }


def _inject_dashboard(
    payload: dict[str, Any],
    store: Any,
) -> dict[str, Any]:
    experiment = _database_state(store)
    research = payload.get("researchForward")
    if isinstance(research, dict):
        research[
            "micropriceConfirmOptimizationShadows"
        ] = experiment
        strategies = research.get("strategies")
        if isinstance(strategies, dict):
            try:
                source_enabled = bool(
                    store.config().get(
                        "strategy_r_microprice_enabled",
                        True,
                    )
                )
            except Exception:
                source_enabled = True
            for strategy, stats in experiment["strategies"].items():
                strategies[strategy] = {
                    "enabled": source_enabled,
                    "stakeUsdt": _variants.MICROPRICE_STAKE_USDT,
                    "selectedBacktestParameters": {
                        **stats.get("parameters", {}),
                        "sourceStrategy": SOURCE_STRATEGY,
                        "sourceConfirmationVersion": (
                            _variants.MICROPRICE_VARIANT_VERSION
                        ),
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
                "realized_pnl": float(
                    stats.get("realizedPnl") or 0.0
                ),
            }
    return payload


def _wrap_dashboard(store_class: type[Any]) -> None:
    original = getattr(store_class, "dashboard", None)
    if not callable(original):
        return
    if getattr(original, "_microprice_confirm_optimization_v1", False):
        return

    @wraps(original)
    def dashboard_with_microprice_confirm_optimization(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = original(self, *args, **kwargs)
        if not isinstance(payload, dict):
            return payload
        return _inject_dashboard(payload, self)

    dashboard_with_microprice_confirm_optimization._microprice_confirm_optimization_v1 = True  # type: ignore[attr-defined]
    store_class.dashboard = dashboard_with_microprice_confirm_optimization


def install_microprice_confirm_optimization_shadows() -> None:
    _patch_tracker_process()

    engine_class = _realtime.MSeriesRealtimeEngine
    original_init = engine_class.__init__
    if getattr(
        original_init,
        "_microprice_confirm_optimization_v1",
        False,
    ):
        return

    @wraps(original_init)
    def init_with_microprice_confirm_optimization(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        global _ACTIVE_TRACKER
        original_init(self, *args, **kwargs)
        store = getattr(self, "store", None)
        tracker = getattr(
            self,
            "microprice_variant_tracker",
            None,
        )
        if store is not None:
            _ensure_schema(store)
            _wrap_open_trade(type(store))
            _wrap_dashboard(type(store))
        if isinstance(
            tracker,
            _variants.MicropriceVariantTracker,
        ):
            _ACTIVE_TRACKER = tracker

    init_with_microprice_confirm_optimization._microprice_confirm_optimization_v1 = True  # type: ignore[attr-defined]
    engine_class.__init__ = init_with_microprice_confirm_optimization
