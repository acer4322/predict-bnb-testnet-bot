from __future__ import annotations

from typing import Any

from .decision_strategy_rules import STAKE_USDT, STRATEGIES


MIGRATION_KEY = "decision_strategy_native_v2_initial_config"


def initialize_controller_config(store: Any) -> None:
    """Enable new Paper controllers exactly once without overriding later choices."""
    if getattr(store, "_read_only", False):
        return

    migrated = False
    with store.lock:
        store.db.execute(
            """CREATE TABLE IF NOT EXISTS decision_strategy_install_state (
                   key TEXT PRIMARY KEY,
                   value TEXT NOT NULL,
                   updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        existing = store.db.execute(
            "SELECT value FROM decision_strategy_install_state WHERE key=?",
            (MIGRATION_KEY,),
        ).fetchone()
        if existing is None:
            for strategy in STRATEGIES:
                store.db.execute(
                    """INSERT INTO config(key, value) VALUES (?, 1)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                    (f"strategy_{strategy.lower()}_enabled",),
                )
                store.db.execute(
                    """INSERT INTO config(key, value) VALUES (?, ?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                    (f"strategy_{strategy.lower()}_stake", STAKE_USDT),
                )
            store.db.execute(
                """INSERT INTO decision_strategy_install_state(key, value)
                   VALUES (?, 'applied')""",
                (MIGRATION_KEY,),
            )
            migrated = True
        store.db.commit()

    if migrated:
        cache = getattr(store, "_config_cache", None)
        if isinstance(cache, dict):
            for strategy in STRATEGIES:
                cache[f"strategy_{strategy.lower()}_enabled"] = True
                cache[f"strategy_{strategy.lower()}_stake"] = STAKE_USDT
