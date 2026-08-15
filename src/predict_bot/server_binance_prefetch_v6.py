from __future__ import annotations

from typing import Any

# Install the optional lightweight observer patch before importing any server
# wrapper. server.py binds MicrostructureObserver at import time, so doing this
# first lets POLY_LIVE skip the large microstructure archive cleanly.
from .microstructure_lightweight_patch import install_microstructure_lightweight_patch

install_microstructure_lightweight_patch()

from . import server_binance_prefetch_v5 as previous
from .strong_trend_guard_shadows import NORMALIZED_STAKE_USDT, STRATEGIES


server = previous.server


def backfill_strong_trend_runtime_config(
    *,
    store: Any | None = None,
    default_config: dict[str, float | bool] | None = None,
) -> int:
    """Backfill Strong Trend Shadow keys into an already-created Store.

    ``server.Store`` is instantiated before ``install_strong_trend_guard_shadows``
    runs.  The installer extends ``DEFAULT_CONFIG`` afterwards, so older startup
    order can leave the already-open SQLite ``config`` table and its cache without
    the eight shadow strategies' enabled/stake keys.  MRealtime consumers that
    index those keys directly then raise KeyError and stop draining the event
    queue.

    This startup migration is deliberately narrow and idempotent: it only inserts
    the Strong Trend Shadow defaults when absent, preserves any existing user
    values, invalidates the primary Store config cache, and never touches live
    trading state.
    """

    target_store = server.STORE if store is None else store
    defaults = server.DEFAULT_CONFIG if default_config is None else default_config

    expected: dict[str, float | bool] = {}
    for strategy in STRATEGIES:
        enabled_key = f"strategy_{strategy.lower()}_enabled"
        stake_key = f"strategy_{strategy.lower()}_stake"
        defaults.setdefault(enabled_key, True)
        defaults.setdefault(stake_key, NORMALIZED_STAKE_USDT)
        expected[enabled_key] = bool(defaults[enabled_key])
        expected[stake_key] = float(defaults[stake_key])

    inserted = 0
    with target_store.lock:
        for key, value in expected.items():
            before = target_store.db.total_changes
            target_store.db.execute(
                "INSERT OR IGNORE INTO config(key, value) VALUES (?, ?)",
                (key, float(value)),
            )
            if target_store.db.total_changes > before:
                inserted += 1
        target_store.db.commit()
        if hasattr(target_store, "_config_cache"):
            target_store._config_cache = None

    return inserted


# Run before ``server.main()`` creates/starts MRealtime.  Re-run in main as an
# idempotent safety check in case a wrapper imports this module unusually early.
backfill_strong_trend_runtime_config()


def main() -> int:
    backfill_strong_trend_runtime_config()
    return previous.main()


if __name__ == "__main__":
    raise SystemExit(main())
