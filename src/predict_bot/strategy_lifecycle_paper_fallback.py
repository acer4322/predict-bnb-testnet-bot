from __future__ import annotations

import os
import sqlite3
import threading
import time
from functools import wraps
from pathlib import Path
from typing import Any, Sequence

from .strategy_lifecycle_guard import build_strategy_lifecycle_summary


LIFECYCLE_PAPER_VERSION = "STRATEGY_LIFECYCLE_GUARD_V1_LIVE_PAPER_FALLBACK"
PAPER_CACHE_SECONDS = 2.0
ROOT = Path(__file__).resolve().parents[2]

_CACHE_LOCK = threading.RLock()
_CACHE: dict[tuple[str, tuple[str, ...], tuple[str, ...]], tuple[float, dict[str, Any]]] = {}


def _paper_db_path() -> Path:
    return Path(os.environ.get("PREDICT_SIM_DB", ROOT / "data" / "simulation.db"))


def _normalized(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(value or "").strip().upper()
            for value in values
            if str(value or "").strip()
        )
    )


def _blank_paper_summary(
    supported: tuple[str, ...],
    active: tuple[str, ...],
    *,
    status: str,
    error: str | None = None,
) -> dict[str, Any]:
    summary = build_strategy_lifecycle_summary([], supported, active)
    summary.update(
        {
            "source": "PAPER",
            "status": status,
            "paperOnly": True,
            "liveOrdersAffected": False,
            "collectorEnabledByStrategy": {strategy: None for strategy in supported},
            "paperTradeSampleBasis": "realized terminal paper trades with pnl",
            "pairSampleBasis": "locked paper pair result at simulated fill",
            "error": error,
        }
    )
    return summary


def build_paper_strategy_lifecycle_summary(
    db_path: Path,
    supported_strategies: Sequence[str],
    active_strategies: Sequence[str] = (),
) -> dict[str, Any]:
    """Read realized Paper evidence without changing the Paper ledger.

    Normal strategies use terminal Paper trades with a realized PnL.  This
    includes both official SETTLED_WIN/SETTLED_LOSS outcomes and strategies
    with a causal simulated exit such as TARGET_FILLED or TIMEOUT_EXIT.
    Pair-arbitrage strategies use their dedicated simulated locked-PnL ledger,
    because their result is determined by the two-leg fill rather than a later
    UP/DOWN outcome.
    """
    supported = _normalized(supported_strategies)
    active = _normalized(active_strategies)
    if not supported:
        return _blank_paper_summary(supported, active, status="READY")
    if not db_path.exists():
        return _blank_paper_summary(
            supported,
            active,
            status="UNAVAILABLE",
            error="simulation database does not exist yet",
        )

    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    rows: list[dict[str, Any]] = []
    collector_enabled: dict[str, bool | None] = {
        strategy: None for strategy in supported
    }
    db: sqlite3.Connection | None = None
    try:
        db = sqlite3.connect(
            uri,
            uri=True,
            check_same_thread=False,
            timeout=2.0,
        )
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA busy_timeout=2000")
        tables = {
            str(row[0])
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        if "trades" in tables:
            placeholders = ",".join("?" for _ in supported)
            paper_rows = db.execute(
                f"""SELECT strategy, market_id, stake AS cost_usdt,
                           pnl AS pnl_usdt,
                           COALESCE(closed_at, opened_at) AS settled_at
                      FROM trades
                     WHERE strategy IN ({placeholders})
                       AND UPPER(status) <> 'OPEN'
                       AND pnl IS NOT NULL
                     ORDER BY COALESCE(closed_at, opened_at) ASC, id ASC""",
                supported,
            ).fetchall()
            rows.extend(dict(row) for row in paper_rows)

        pair_strategies = tuple(
            strategy for strategy in supported if strategy.startswith("PAIR_ARB_")
        )
        if pair_strategies and "strategy_pair_arb_trades" in tables:
            placeholders = ",".join("?" for _ in pair_strategies)
            pair_rows = db.execute(
                f"""SELECT strategy, market_id, total_cost AS cost_usdt,
                           locked_pnl AS pnl_usdt, opened_at AS settled_at
                      FROM strategy_pair_arb_trades
                     WHERE strategy IN ({placeholders})
                       AND total_cost > 0 AND shares > 0
                     ORDER BY opened_at ASC, id ASC""",
                pair_strategies,
            ).fetchall()
            rows.extend(dict(row) for row in pair_rows)

        if "config" in tables:
            config_keys = [
                f"strategy_{strategy.lower()}_enabled" for strategy in supported
            ]
            placeholders = ",".join("?" for _ in config_keys)
            config_rows = db.execute(
                f"SELECT key, value FROM config WHERE key IN ({placeholders})",
                config_keys,
            ).fetchall()
            by_key = {str(row["key"]): row["value"] for row in config_rows}
            for strategy in supported:
                key = f"strategy_{strategy.lower()}_enabled"
                if key not in by_key:
                    continue
                try:
                    collector_enabled[strategy] = bool(float(by_key[key]))
                except (TypeError, ValueError):
                    collector_enabled[strategy] = None
    except sqlite3.Error as exc:
        return _blank_paper_summary(
            supported,
            active,
            status="UNAVAILABLE",
            error=str(exc)[:240],
        )
    finally:
        if db is not None:
            try:
                db.close()
            except sqlite3.Error:
                pass

    summary = build_strategy_lifecycle_summary(rows, supported, active)
    summary.update(
        {
            "source": "PAPER",
            "status": "READY",
            "paperOnly": True,
            "liveOrdersAffected": False,
            "collectorEnabledByStrategy": collector_enabled,
            "paperTradeSampleBasis": "realized terminal paper trades with pnl",
            "pairSampleBasis": "locked paper pair result at simulated fill",
            "error": None,
        }
    )
    return summary


def cached_paper_strategy_lifecycle_summary(
    supported_strategies: Sequence[str],
    active_strategies: Sequence[str] = (),
) -> dict[str, Any]:
    supported = _normalized(supported_strategies)
    active = _normalized(active_strategies)
    path = _paper_db_path()
    key = (str(path.resolve()), supported, active)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None and now - cached[0] < PAPER_CACHE_SECONDS:
            return cached[1]
    summary = build_paper_strategy_lifecycle_summary(path, supported, active)
    with _CACHE_LOCK:
        _CACHE[key] = (now, summary)
    return summary


def merge_live_and_paper_lifecycle(
    live_summary: dict[str, Any] | None,
    paper_summary: dict[str, Any] | None,
    supported_strategies: Sequence[str],
    active_strategies: Sequence[str] = (),
) -> dict[str, Any]:
    """Prefer real settled evidence per strategy; use Paper only when Live n=0."""
    supported = _normalized(supported_strategies)
    active = set(_normalized(active_strategies))
    live = live_summary or build_strategy_lifecycle_summary([], supported, tuple(active))
    paper = paper_summary or _blank_paper_summary(
        supported,
        tuple(active),
        status="UNAVAILABLE",
        error="paper lifecycle snapshot unavailable",
    )
    live_by_strategy = {
        str(row.get("strategy") or "").upper(): dict(row)
        for row in live.get("strategies") or []
        if isinstance(row, dict)
    }
    paper_by_strategy = {
        str(row.get("strategy") or "").upper(): dict(row)
        for row in paper.get("strategies") or []
        if isinstance(row, dict)
    }
    collector_enabled = paper.get("collectorEnabledByStrategy") or {}

    rows: list[dict[str, Any]] = []
    paper_fallback_strategies: list[str] = []
    for strategy in supported:
        live_row = live_by_strategy.get(strategy) or build_strategy_lifecycle_summary(
            [], [strategy], [strategy] if strategy in active else []
        )["strategies"][0]
        paper_row = paper_by_strategy.get(strategy) or build_strategy_lifecycle_summary(
            [], [strategy], [strategy] if strategy in active else []
        )["strategies"][0]
        live_samples = int(live_row.get("settledMarkets") or 0)
        paper_samples = int(paper_row.get("settledMarkets") or 0)
        if live_samples > 0:
            effective = dict(live_row)
            source = "LIVE"
            fallback = False
        elif paper_samples > 0:
            effective = dict(paper_row)
            source = "PAPER_FALLBACK"
            fallback = True
            paper_fallback_strategies.append(strategy)
        else:
            effective = dict(live_row)
            source = "NO_DATA"
            fallback = False
        effective.update(
            {
                "strategy": strategy,
                "active": strategy in active,
                "dataSource": source,
                "paperFallbackActive": fallback,
                "paperCollectorEnabled": collector_enabled.get(strategy),
                "live": live_row,
                "paper": paper_row,
            }
        )
        rows.append(effective)

    status_names = (
        "ACTIVE",
        "WATCH",
        "DEGRADED",
        "RECOVERY",
        "BUILDING",
        "NO_DATA",
    )
    counts = {
        status: sum(str(row.get("status")) == status for row in rows)
        for status in status_names
    }
    return {
        **live,
        "version": LIFECYCLE_PAPER_VERSION,
        "status": "READY",
        "advisoryOnly": True,
        "automaticBlocking": False,
        "automaticStakeChanges": False,
        "paperFallbackEnabled": True,
        "paperStatus": paper.get("status"),
        "paperError": paper.get("error"),
        "sampleBasis": (
            "per strategy: use settled LIVE evidence when any exists; otherwise "
            "use same-strategy realized PAPER evidence; LIVE and PAPER PnL are never added"
        ),
        "paperTradeSampleBasis": paper.get("paperTradeSampleBasis"),
        "paperPairSampleBasis": paper.get("pairSampleBasis"),
        "supportedStrategies": len(rows),
        "activeStrategies": [row["strategy"] for row in rows if row["active"]],
        "paperFallbackStrategies": paper_fallback_strategies,
        "paperFallbackCount": len(paper_fallback_strategies),
        "liveStrategiesWithSamples": sum(
            int(row["live"].get("settledMarkets") or 0) > 0 for row in rows
        ),
        "paperStrategiesWithSamples": sum(
            int(row["paper"].get("settledMarkets") or 0) > 0 for row in rows
        ),
        "counts": counts,
        "strategies": rows,
    }


def install_strategy_lifecycle_paper_fallback(live_module: Any) -> None:
    """Attach read-only Paper fallback to the existing live lifecycle payload."""
    engine_class = live_module.LiveM0WEngine
    original = engine_class.state
    if getattr(original, "_strategy_lifecycle_paper_fallback", False):
        return

    @wraps(original)
    def state_with_paper_fallback(
        self: Any,
        m0_hourly_performance: dict[str, Any] | None = None,
        *,
        include_ledger: bool = True,
    ) -> dict[str, Any]:
        state = original(
            self,
            m0_hourly_performance,
            include_ledger=include_ledger,
        )
        if not include_ledger or not isinstance(state, dict):
            return state
        supported = tuple(
            str(value)
            for value in state.get("supportedStrategies")
            or live_module.LIVE_SUPPORTED_STRATEGIES
        )
        active = tuple(str(value) for value in state.get("strategies") or ())
        paper = cached_paper_strategy_lifecycle_summary(supported, active)
        state["strategyLifecycle"] = merge_live_and_paper_lifecycle(
            state.get("strategyLifecycle"),
            paper,
            supported,
            active,
        )
        return state

    state_with_paper_fallback._strategy_lifecycle_paper_fallback = True  # type: ignore[attr-defined]
    engine_class.state = state_with_paper_fallback
