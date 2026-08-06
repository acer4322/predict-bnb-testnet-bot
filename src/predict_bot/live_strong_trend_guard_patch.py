from __future__ import annotations

import json
from decimal import Decimal
from functools import wraps
from typing import Any, Iterable


LIVE_SOURCE_TO_GUARD = {
    "R_FUTURES_LEAD": "R_STRONG_TREND_GUARD_FUTURES_LEAD",
    "R_CONSENSUS": "R_STRONG_TREND_GUARD_CONSENSUS",
}
LIVE_GUARD_STRATEGIES = tuple(LIVE_SOURCE_TO_GUARD.values())


def _table_exists(store: Any, table: str) -> bool:
    try:
        return store.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone() is not None
    except Exception:
        return False


def live_guard_candidates_for_opened(
    store: Any,
    opened: Iterable[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Create live-queue candidates only from durable allowed Guard decisions.

    The corresponding Paper Shadow remains paper-only.  This bridge produces a
    separate candidate which the existing realtime and live executors still
    subject to selected-strategy, freshness, depth, price, stake, drawdown,
    observer and one-attempt checks.
    """
    if not _table_exists(store, "strong_trend_guard_decisions"):
        return []

    derived: list[dict[str, Any]] = []
    for raw_candidate in list(opened or []):
        if not isinstance(raw_candidate, dict):
            continue
        source_strategy = str(raw_candidate.get("strategy") or "").upper()
        guard_strategy = LIVE_SOURCE_TO_GUARD.get(source_strategy)
        if guard_strategy is None:
            continue
        try:
            market_id = int(raw_candidate["market_id"])
            topic_id = int(raw_candidate["topic_id"])
        except (KeyError, TypeError, ValueError):
            continue
        side = str(raw_candidate.get("side") or "").upper()
        if side not in {"UP", "DOWN"}:
            continue

        with store.lock:
            row = store.db.execute(
                """SELECT d.*,
                          shadow.strategy AS actual_shadow_strategy,
                          shadow.market_id AS actual_shadow_market_id,
                          shadow.side AS actual_shadow_side,
                          shadow.entry_price AS actual_shadow_entry_price
                     FROM strong_trend_guard_decisions AS d
                     LEFT JOIN trades AS shadow ON shadow.id=d.shadow_trade_id
                    WHERE d.source_strategy=?
                      AND d.shadow_strategy=?
                      AND d.market_id=?
                      AND d.topic_id=?
                      AND d.side=?
                    ORDER BY d.id DESC
                    LIMIT 1""",
                (
                    source_strategy,
                    guard_strategy,
                    market_id,
                    topic_id,
                    side,
                ),
            ).fetchone()

        if row is None:
            continue
        if str(row["decision"] or "") == "BLOCK_STRONG_OPPOSING_TREND":
            continue
        if not bool(row["shadow_opened"]) or row["shadow_trade_id"] is None:
            continue
        if str(row["actual_shadow_strategy"] or "") != guard_strategy:
            continue
        if int(row["actual_shadow_market_id"] or -1) != market_id:
            continue
        if str(row["actual_shadow_side"] or "").upper() != side:
            continue

        try:
            decision_diagnostics = json.loads(
                str(row["diagnostics_json"] or "{}")
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            decision_diagnostics = {}

        candidate = dict(raw_candidate)
        candidate.update(
            {
                "strategy": guard_strategy,
                "side": side,
                "entry_price": float(row["source_entry_price"]),
                "paper_only": True,
                "live_orders_affected": False,
                "shadow_only": True,
                "forward_only": True,
                "live_guard_bridge": True,
                "source_strategy": source_strategy,
                "source_trade_id": int(row["source_trade_id"]),
                "shadow_trade_id": int(row["shadow_trade_id"]),
                "strategy_version": str(row["strategy_version"] or ""),
                "strong_trend_guard_decision": decision_diagnostics,
                "note": (
                    f"{guard_strategy} live candidate from allowed "
                    f"{source_strategy} Guard decision #{int(row['id'])}"
                ),
            }
        )
        derived.append(candidate)
    return derived


def _install_live_constants() -> None:
    from . import live_trading

    live_trading.LIVE_RESEARCH_STRATEGIES = tuple(
        dict.fromkeys(
            (*live_trading.LIVE_RESEARCH_STRATEGIES, *LIVE_GUARD_STRATEGIES)
        )
    )
    live_trading.LIVE_SUPPORTED_STRATEGIES = tuple(
        dict.fromkeys(
            (*live_trading.LIVE_SUPPORTED_STRATEGIES, *LIVE_GUARD_STRATEGIES)
        )
    )
    for strategy in LIVE_GUARD_STRATEGIES:
        live_trading.LIVE_RESEARCH_REPRICE_GAPS.setdefault(
            strategy,
            Decimal("0.05"),
        )


def _install_store_bridge() -> None:
    from . import strong_trend_guard_shadows

    original_installer = strong_trend_guard_shadows.install_strong_trend_guard_shadows
    if getattr(original_installer, "_live_guard_bridge_v1", False):
        return

    @wraps(original_installer)
    def install_with_live_guard_bridge(namespace: dict[str, Any]) -> None:
        original_installer(namespace)
        store_class = namespace["Store"]
        original_maybe_enter = store_class.maybe_enter_m_series
        if getattr(original_maybe_enter, "_live_guard_bridge_v1", False):
            return

        @wraps(original_maybe_enter)
        def maybe_enter_with_live_guards(
            self: Any,
            snapshot: dict[str, Any],
            fee_bps: int,
            *,
            realtime_context: dict[str, Any] | None = None,
        ) -> list[dict[str, Any]]:
            opened = list(
                original_maybe_enter(
                    self,
                    snapshot,
                    fee_bps,
                    realtime_context=realtime_context,
                )
                or []
            )
            opened.extend(live_guard_candidates_for_opened(self, opened))
            return opened

        maybe_enter_with_live_guards._live_guard_bridge_v1 = True  # type: ignore[attr-defined]
        store_class.maybe_enter_m_series = maybe_enter_with_live_guards

    install_with_live_guard_bridge._live_guard_bridge_v1 = True  # type: ignore[attr-defined]
    strong_trend_guard_shadows.install_strong_trend_guard_shadows = (
        install_with_live_guard_bridge
    )


def install_live_strong_trend_guard_patch() -> None:
    _install_live_constants()
    _install_store_bridge()
