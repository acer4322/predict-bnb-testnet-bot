from __future__ import annotations

from functools import wraps
from typing import Any

from . import microprice_confirm_exit_098_live_patch as _base
from . import microprice_confirm_exit_098_live_v3_patch as _v3
from .microprice_confirm_optimization_shadows import EXIT_098_STRATEGY


EXIT_098_LIVE_V4_VERSION = "MICROPRICE_CONFIRM_EXIT_098_LIVE_V4"


def _is_exit_098_family(strategy: Any) -> bool:
    normalized = str(strategy or "").strip().upper()
    return normalized == EXIT_098_STRATEGY or normalized.startswith(
        f"{EXIT_098_STRATEGY}:CONFIRM_ADD_"
    )


def _live_exit_candidates_v4(
    live_engine: Any,
    market_id: int,
) -> list[dict[str, Any]]:
    ledger = getattr(live_engine, "ledger", None)
    if ledger is None:
        return []
    lock = getattr(ledger, "lock", None)
    if lock is None:
        return []
    with lock:
        try:
            rows = ledger.db.execute(
                """SELECT o.id, o.strategy, o.market_id, o.side,
                          o.status, o.token_id, o.filled_share_qty,
                          x.status AS exit_status
                     FROM live_orders AS o
                     LEFT JOIN live_strategy_settlements AS s
                       ON s.order_local_id=o.id
                     LEFT JOIN live_manual_exits AS x
                       ON x.order_local_id=o.id
                    WHERE (o.strategy=? OR o.strategy LIKE ?)
                      AND o.market_id=?
                      AND UPPER(o.status) IN (
                          'FILLED','CANCELED','CANCELLED','EXPIRED',
                          'REJECTED','FAILED'
                      )
                      AND COALESCE(o.filled_share_qty, 0)>0
                      AND COALESCE(o.token_id, '')<>''
                      AND s.order_local_id IS NULL
                      AND (
                          x.order_local_id IS NULL
                          OR UPPER(x.status) IN (
                              'REJECTED','FAILED','CANCELED','CANCELLED','EXPIRED'
                          )
                      )
                    ORDER BY o.id ASC""",
                (
                    EXIT_098_STRATEGY,
                    f"{EXIT_098_STRATEGY}:CONFIRM_ADD_%",
                    int(market_id),
                ),
            ).fetchall()
        except Exception:
            return []
    return [dict(row) for row in rows]


def _patch_ledger_strategy_view() -> None:
    from . import live_trading as live

    ledger_class = live.LiveM0WLedger
    original = ledger_class.order_for_manual_exit
    if getattr(original, "_microprice_exit_098_live_v4", False):
        return

    @wraps(original)
    def order_for_exit_098_family(
        self: Any,
        local_id: int,
    ) -> dict[str, Any] | None:
        row = original(self, local_id)
        if row is None or not _is_exit_098_family(row.get("strategy")):
            return row
        if str(row.get("strategy") or "").upper() == EXIT_098_STRATEGY:
            return row
        normalized = dict(row)
        normalized["exit_098_source_strategy"] = row.get("strategy")
        normalized["strategy"] = EXIT_098_STRATEGY
        return normalized

    order_for_exit_098_family._microprice_exit_098_live_v4 = True  # type: ignore[attr-defined]
    ledger_class.order_for_manual_exit = order_for_exit_098_family


def install_microprice_confirm_exit_098_live_v4_patch() -> None:
    _base.EXIT_098_LIVE_PATCH_VERSION = EXIT_098_LIVE_V4_VERSION
    _v3.EXIT_098_LIVE_V3_VERSION = EXIT_098_LIVE_V4_VERSION
    _v3._live_exit_candidates_v3 = _live_exit_candidates_v4
    _base._live_exit_candidates = _live_exit_candidates_v4
    _patch_ledger_strategy_view()
