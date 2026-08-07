from __future__ import annotations

import json
import math
from functools import wraps
from typing import Any

from . import m_realtime as _realtime


SOURCE_STRATEGY = "R_MICROPRICE"
C3_MID_CONFIRM_STRATEGY = "R_MICROPRICE_C3_MID_CONFIRM"
C3_MID_CONFIRM_VERSION = "MICROPRICE_C3_MID_CONFIRM_V1"
CONFIRM_DELAY_SECONDS = 3.0
MAX_CONFIRM_LATENESS_SECONDS = 1.75
MIN_ABS_SCORE = 0.20
MIN_RETAINED_STRENGTH = 0.60
MIN_MIDPOINT_DELTA = 0.005
MAX_MIDPOINT_DELTA = 0.020
MAX_ASK = 0.55
MAX_SPREAD = 0.03
MAX_BOOK_AGE_MS = 2000.0
MAX_BOOK_SKEW_MS = 500.0
SLIPPAGE_BPS = 50.0
DIRECT_OUTCOME_DATA_SOURCE = "dual_token_rest"


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _score(snapshot: dict[str, Any]) -> float | None:
    values = {
        key: _finite(snapshot.get(key))
        for key in (
            "up_bid_size", "up_ask_size", "down_bid_size", "down_ask_size"
        )
    }
    if any(value is None or value <= 0 for value in values.values()):
        return None

    def imbalance(bid_size: float, ask_size: float) -> float:
        total = bid_size + ask_size
        return (bid_size - ask_size) / total if total > 0 else 0.0

    return imbalance(values["up_bid_size"], values["up_ask_size"]) - imbalance(
        values["down_bid_size"], values["down_ask_size"]
    )


def _side(score: float) -> str:
    return "UP" if score > 0 else "DOWN"


def _midpoint(snapshot: dict[str, Any], side: str) -> float | None:
    prefix = side.lower()
    bid = _finite(snapshot.get(f"{prefix}_bid"))
    ask = _finite(snapshot.get(f"{prefix}_ask"))
    if bid is None or ask is None or not 0 <= bid <= ask <= 1:
        return None
    return (bid + ask) / 2.0


def _direct_context_is_safe(engine: Any, context: dict[str, Any]) -> bool:
    event = getattr(engine, "prediction_event", None)
    return bool(
        context.get("trigger_source") == "prediction"
        and context.get("execution_eligible") is True
        and context.get("prediction_data_source") == DIRECT_OUTCOME_DATA_SOURCE
        and context.get("market_data_integrity_ok") is True
        and isinstance(event, dict)
        and event.get("prediction_data_source") == DIRECT_OUTCOME_DATA_SOURCE
        and event.get("direct_outcome_books") is True
    )


def _execution(snapshot: dict[str, Any], side: str, stake: float) -> tuple[dict[str, float] | None, str]:
    prefix = side.lower()
    ask = _finite(snapshot.get(f"{prefix}_ask"))
    bid = _finite(snapshot.get(f"{prefix}_bid"))
    ask_size = _finite(snapshot.get(f"{prefix}_ask_size"))
    book_age_ms = _finite(snapshot.get("book_age_ms"))
    book_skew_ms = _finite(snapshot.get("book_skew_ms"))
    if ask is None or bid is None or ask_size is None:
        return None, "BOOK_MISSING"
    if book_age_ms is None or not 0 <= book_age_ms <= MAX_BOOK_AGE_MS:
        return None, "BOOK_STALE"
    if book_skew_ms is None or not 0 <= book_skew_ms <= MAX_BOOK_SKEW_MS:
        return None, "BOOK_SKEW"
    if not 0 < ask <= MAX_ASK or not 0 <= bid <= ask or ask_size <= 0:
        return None, "PRICE_OR_BOOK"
    if ask - bid > MAX_SPREAD:
        return None, "SPREAD"
    entry = ask * (1.0 + SLIPPAGE_BPS / 10_000.0)
    if not 0 < entry < 1:
        return None, "ENTRY_INVALID"
    shares = stake / entry
    if ask_size + 1e-12 < shares:
        return None, "DEPTH"
    return {
        "raw_ask": ask,
        "raw_bid": bid,
        "ask_size": ask_size,
        "entry": entry,
        "shares": shares,
        "book_age_ms": book_age_ms,
        "book_skew_ms": book_skew_ms,
    }, "ALLOW"


def _trade_exists(store: Any, market_id: int) -> bool:
    db = getattr(store, "db", None)
    if db is None:
        return False
    try:
        return db.execute(
            "SELECT 1 FROM trades WHERE strategy=? AND market_id=? LIMIT 1",
            (C3_MID_CONFIRM_STRATEGY, int(market_id)),
        ).fetchone() is not None
    except Exception:
        return False


class MicropriceC3MidConfirmTracker:
    def __init__(self, engine: Any, store: Any) -> None:
        self.engine = engine
        self.store = store
        self.anchor: dict[str, Any] | None = None
        self.opened_markets: set[int] = set()
        self.last_decision: dict[str, Any] | None = None
        self.anchors = 0
        self.opened = 0
        self.blocked = 0

    def _capture_anchor(
        self,
        snapshot: dict[str, Any],
        context: dict[str, Any],
        source_trade: dict[str, Any],
        fee_bps: int,
    ) -> None:
        if not _direct_context_is_safe(self.engine, context):
            return
        try:
            market_id = int(snapshot["market_id"])
            side = str(source_trade["side"]).upper()
            received_ns = int(
                context.get("received_monotonic_ns")
                or context.get("signal_received_monotonic_ns")
                or snapshot.get("received_monotonic_ns")
                or 0
            )
        except (KeyError, TypeError, ValueError):
            return
        if side not in {"UP", "DOWN"} or received_ns <= 0:
            return
        score = _score(snapshot)
        midpoint = _midpoint(snapshot, side)
        if score is None or midpoint is None or abs(score) < MIN_ABS_SCORE or _side(score) != side:
            return
        stake = _finite(source_trade.get("stake")) or 5.0
        self.anchor = {
            "market_id": market_id,
            "topic_id": int(snapshot["topic_id"]),
            "side": side,
            "score": float(score),
            "midpoint": float(midpoint),
            "received_monotonic_ns": received_ns,
            "signal_timestamp": snapshot.get("timestamp"),
            "source_entry_price": _finite(source_trade.get("entry_price")),
            "stake": float(stake),
            "fee_bps": int(fee_bps),
        }
        self.anchors += 1
        self.last_decision = {
            "marketId": market_id,
            "status": "WAITING_3S",
            "side": side,
            "anchorScore": float(score),
            "anchorMidpoint": float(midpoint),
        }

    def _evaluate(
        self,
        snapshot: dict[str, Any],
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        anchor = self.anchor
        if anchor is None or not _direct_context_is_safe(self.engine, context):
            return []
        try:
            market_id = int(snapshot["market_id"])
            received_ns = int(
                context.get("received_monotonic_ns")
                or context.get("signal_received_monotonic_ns")
                or snapshot.get("received_monotonic_ns")
                or 0
            )
        except (KeyError, TypeError, ValueError):
            return []
        if market_id != int(anchor["market_id"]):
            self.anchor = None
            return []
        elapsed = (received_ns - int(anchor["received_monotonic_ns"])) / 1_000_000_000.0
        if elapsed < CONFIRM_DELAY_SECONDS:
            return []
        self.anchor = None
        if elapsed > CONFIRM_DELAY_SECONDS + MAX_CONFIRM_LATENESS_SECONDS:
            return self._block(market_id, "CONFIRM_SAMPLE_LATE", elapsed=elapsed)

        score = _score(snapshot)
        side = str(anchor["side"])
        if score is None or abs(score) < MIN_ABS_SCORE:
            return self._block(market_id, "SCORE_BELOW_MINIMUM", elapsed=elapsed, score=score)
        if _side(score) != side:
            return self._block(market_id, "SIGNAL_FLIPPED", elapsed=elapsed, score=score)
        retained = abs(score) / abs(float(anchor["score"]))
        if retained < MIN_RETAINED_STRENGTH:
            return self._block(market_id, "RETAINED_STRENGTH", elapsed=elapsed, score=score, retained=retained)
        midpoint = _midpoint(snapshot, side)
        if midpoint is None:
            return self._block(market_id, "MIDPOINT_UNAVAILABLE", elapsed=elapsed)
        midpoint_delta = midpoint - float(anchor["midpoint"])
        if midpoint_delta < MIN_MIDPOINT_DELTA:
            return self._block(market_id, "MIDPOINT_MOVE_TOO_SMALL", elapsed=elapsed, retained=retained, midpointDelta=midpoint_delta)
        if midpoint_delta >= MAX_MIDPOINT_DELTA:
            return self._block(market_id, "MIDPOINT_MOVE_TOO_LARGE", elapsed=elapsed, retained=retained, midpointDelta=midpoint_delta)

        execution, reason = _execution(snapshot, side, float(anchor["stake"]))
        if execution is None:
            return self._block(market_id, reason, elapsed=elapsed, retained=retained, midpointDelta=midpoint_delta)
        if market_id in self.opened_markets or _trade_exists(self.store, market_id):
            self.opened_markets.add(market_id)
            return []

        diagnostics = {
            "paper_only": True,
            "live_orders_affected": False,
            "shadow_only": True,
            "forward_only": True,
            "source_strategy": SOURCE_STRATEGY,
            "strategy_version": C3_MID_CONFIRM_VERSION,
            "rule": {
                "confirmDelaySeconds": CONFIRM_DELAY_SECONDS,
                "maxConfirmLatenessSeconds": MAX_CONFIRM_LATENESS_SECONDS,
                "minimumAbsScore": MIN_ABS_SCORE,
                "minimumRetainedStrength": MIN_RETAINED_STRENGTH,
                "minimumMidpointDeltaInclusive": MIN_MIDPOINT_DELTA,
                "maximumMidpointDeltaExclusive": MAX_MIDPOINT_DELTA,
                "maxAsk": MAX_ASK,
                "maxSpread": MAX_SPREAD,
                "maxBookAgeMs": MAX_BOOK_AGE_MS,
                "maxBookSkewMs": MAX_BOOK_SKEW_MS,
                "slippageBps": SLIPPAGE_BPS,
            },
            "anchor": {
                "side": side,
                "score": float(anchor["score"]),
                "midpoint": float(anchor["midpoint"]),
                "signalTimestamp": anchor.get("signal_timestamp"),
                "sourceEntryPrice": anchor.get("source_entry_price"),
            },
            "confirmation": {
                "elapsedSeconds": elapsed,
                "score": float(score),
                "retainedStrength": retained,
                "midpoint": float(midpoint),
                "midpointDelta": midpoint_delta,
                "rawTopAsk": execution["raw_ask"],
                "rawTopBid": execution["raw_bid"],
                "bookAgeMs": execution["book_age_ms"],
                "bookSkewMs": execution["book_skew_ms"],
            },
        }
        self.store.open_trade(
            strategy=C3_MID_CONFIRM_STRATEGY,
            topic_id=int(anchor["topic_id"]),
            market_id=market_id,
            side=side,
            entry=float(execution["entry"]),
            target=None,
            stake=float(anchor["stake"]),
            fee_rate_bps=int(anchor["fee_bps"]),
            note=(
                f"{C3_MID_CONFIRM_STRATEGY} forward-only Paper Shadow; "
                "3s same-direction retained-strength + midpoint confirmation; never live-forwarded"
            ),
            strategy_version=C3_MID_CONFIRM_VERSION,
            diagnostics=diagnostics,
        )
        self.opened_markets.add(market_id)
        self.opened += 1
        self.last_decision = {
            "marketId": market_id,
            "status": "OPENED",
            "side": side,
            "elapsedSeconds": elapsed,
            "retainedStrength": retained,
            "midpointDelta": midpoint_delta,
            "entry": float(execution["entry"]),
        }
        return [{
            "strategy": C3_MID_CONFIRM_STRATEGY,
            "topic_id": int(anchor["topic_id"]),
            "market_id": market_id,
            "side": side,
            "entry_price": float(execution["entry"]),
            "stake": float(anchor["stake"]),
            "seconds_left": _finite(snapshot.get("seconds_left")),
            "paper_only": True,
            "live_orders_affected": False,
            "research_signal": float(score),
        }]

    def _block(self, market_id: int, reason: str, **extra: Any) -> list[dict[str, Any]]:
        self.blocked += 1
        self.last_decision = {
            "marketId": int(market_id),
            "status": "BLOCKED",
            "reason": reason,
            **extra,
        }
        return []

    def process(
        self,
        snapshot: dict[str, Any],
        fee_bps: int,
        context: dict[str, Any],
        opened: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        created = self._evaluate(snapshot, context)
        for trade in opened:
            if str(trade.get("strategy") or "").upper() == SOURCE_STRATEGY:
                self._capture_anchor(snapshot, context, trade, fee_bps)
                break
        return created

    def state(self) -> dict[str, Any]:
        return {
            "version": C3_MID_CONFIRM_VERSION,
            "paperOnly": True,
            "liveOrdersAffected": False,
            "forwardOnly": True,
            "sourceStrategy": SOURCE_STRATEGY,
            "anchors": self.anchors,
            "opened": self.opened,
            "blocked": self.blocked,
            "pendingAnchor": self.anchor is not None,
            "lastDecision": self.last_decision,
            "rules": {
                "confirmDelaySeconds": CONFIRM_DELAY_SECONDS,
                "minimumRetainedStrength": MIN_RETAINED_STRENGTH,
                "midpointDelta": [MIN_MIDPOINT_DELTA, MAX_MIDPOINT_DELTA],
                "minimumAbsScore": MIN_ABS_SCORE,
            },
        }


def _database_stats(store: Any) -> dict[str, Any]:
    db = getattr(store, "db", None)
    rows = []
    if db is not None:
        try:
            rows = [dict(row) for row in db.execute(
                """SELECT id, market_id, side, status, entry_price, pnl, opened_at, closed_at
                     FROM trades WHERE strategy=? ORDER BY id ASC""",
                (C3_MID_CONFIRM_STRATEGY,),
            ).fetchall()]
        except Exception:
            rows = []
    settled = [row for row in rows if str(row.get("status")) in {"SETTLED_WIN", "SETTLED_LOSS"}]
    wins = sum(str(row.get("status")) == "SETTLED_WIN" for row in settled)
    return {
        "trades": len(rows),
        "open": sum(str(row.get("status")) == "OPEN" for row in rows),
        "settled": len(settled),
        "wins": int(wins),
        "losses": len(settled) - int(wins),
        "winRate": float(wins) / len(settled) if settled else None,
        "realizedPnl": sum(float(row.get("pnl") or 0.0) for row in settled),
        "averageEntryPrice": (
            sum(float(row["entry_price"]) for row in rows) / len(rows) if rows else None
        ),
        "mode": "3S RETAINED + MIDPOINT CONFIRM",
        "parameters": {
            "confirmDelaySeconds": CONFIRM_DELAY_SECONDS,
            "minimumRetainedStrength": MIN_RETAINED_STRENGTH,
            "minimumMidpointDeltaInclusive": MIN_MIDPOINT_DELTA,
            "maximumMidpointDeltaExclusive": MAX_MIDPOINT_DELTA,
            "minimumAbsScore": MIN_ABS_SCORE,
            "sourceStrategy": SOURCE_STRATEGY,
            "forwardOnly": True,
        },
    }


def _inject_dashboard(payload: dict[str, Any], store: Any) -> dict[str, Any]:
    stats = _database_stats(store)
    tracker = getattr(store, "_microprice_c3_mid_confirm_tracker", None)
    runtime = tracker.state() if isinstance(tracker, MicropriceC3MidConfirmTracker) else None
    experiment = {
        "version": C3_MID_CONFIRM_VERSION,
        "paperOnly": True,
        "liveOrdersAffected": False,
        "forwardOnly": True,
        "sourceStrategy": SOURCE_STRATEGY,
        "strategies": {C3_MID_CONFIRM_STRATEGY: stats},
        "runtime": runtime or {},
    }
    research = payload.get("researchForward")
    if isinstance(research, dict):
        research["micropriceC3MidConfirm"] = experiment
        strategies = research.get("strategies")
        if isinstance(strategies, dict):
            strategies[C3_MID_CONFIRM_STRATEGY] = {
                "enabled": True,
                "stakeUsdt": 5.0,
                "selectedBacktestParameters": stats["parameters"],
                "chronologicalValidation": {
                    "status": "ANALYZABLE" if stats["settled"] >= 30 else "COLLECTING",
                    "samples": stats["trades"],
                    "settled": stats["settled"],
                    "wins": stats["wins"],
                    "losses": stats["losses"],
                    "realizedPnl": stats["realizedPnl"],
                    "minimum": 30,
                    "forwardOnly": True,
                },
                "paperOnly": True,
                "liveOrdersAffected": False,
            }
    summaries = payload.get("summaries")
    if isinstance(summaries, dict):
        summaries[C3_MID_CONFIRM_STRATEGY] = {
            "trades": stats["trades"],
            "open": stats["open"],
            "wins": stats["wins"],
            "losses": stats["losses"],
            "realized_pnl": stats["realizedPnl"],
        }
    return payload


def _wrap_store(engine: Any, store: Any) -> None:
    if getattr(store, "_microprice_c3_mid_confirm_wrapped", False):
        tracker = getattr(store, "_microprice_c3_mid_confirm_tracker", None)
        if isinstance(tracker, MicropriceC3MidConfirmTracker):
            tracker.engine = engine
        return
    original = getattr(store, "maybe_enter_m_series", None)
    if not callable(original):
        return
    tracker = MicropriceC3MidConfirmTracker(engine, store)

    @wraps(original)
    def maybe_enter_with_c3_mid_confirm(
        snapshot: dict[str, Any],
        fee_bps: int,
        *,
        realtime_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        opened = original(snapshot, fee_bps, realtime_context=realtime_context) or []
        shadow = tracker.process(snapshot, fee_bps, dict(realtime_context or {}), list(opened))
        return [*opened, *shadow]

    store.maybe_enter_m_series = maybe_enter_with_c3_mid_confirm
    store._microprice_c3_mid_confirm_wrapped = True
    store._microprice_c3_mid_confirm_tracker = tracker


def _wrap_dashboard(store_class: type[Any]) -> None:
    original = getattr(store_class, "dashboard", None)
    if not callable(original) or getattr(original, "_microprice_c3_mid_dashboard_v1", False):
        return

    @wraps(original)
    def dashboard_with_c3_mid(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = original(self, *args, **kwargs)
        return _inject_dashboard(payload, self) if isinstance(payload, dict) else payload

    dashboard_with_c3_mid._microprice_c3_mid_dashboard_v1 = True  # type: ignore[attr-defined]
    store_class.dashboard = dashboard_with_c3_mid


def install_microprice_c3_mid_confirm() -> None:
    engine_class = _realtime.MSeriesRealtimeEngine
    original_init = engine_class.__init__
    if getattr(original_init, "_microprice_c3_mid_confirm_v1", False):
        return

    @wraps(original_init)
    def init_with_c3_mid(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        store = getattr(self, "store", None)
        if store is None:
            return
        _wrap_store(self, store)
        _wrap_dashboard(type(store))

    init_with_c3_mid._microprice_c3_mid_confirm_v1 = True  # type: ignore[attr-defined]
    engine_class.__init__ = init_with_c3_mid
