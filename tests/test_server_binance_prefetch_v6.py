from __future__ import annotations

import sqlite3
import threading

from predict_bot.server_binance_prefetch_v6 import (
    backfill_strong_trend_runtime_config,
)
from predict_bot.strong_trend_guard_shadows import NORMALIZED_STAKE_USDT, STRATEGIES


class FakeStore:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE config (key TEXT PRIMARY KEY, value REAL NOT NULL)")
        self.db.execute("INSERT INTO config(key, value) VALUES ('existing', 7)")
        self.db.commit()
        self._config_cache = {"existing": 7.0}


def test_backfill_inserts_all_late_installed_keys_and_invalidates_cache():
    store = FakeStore()
    defaults: dict[str, float | bool] = {}

    inserted = backfill_strong_trend_runtime_config(
        store=store,
        default_config=defaults,
    )

    assert inserted == len(STRATEGIES) * 2
    assert store._config_cache is None
    rows = dict(store.db.execute("SELECT key, value FROM config").fetchall())
    assert rows["existing"] == 7.0
    for strategy in STRATEGIES:
        enabled_key = f"strategy_{strategy.lower()}_enabled"
        stake_key = f"strategy_{strategy.lower()}_stake"
        assert defaults[enabled_key] is True
        assert defaults[stake_key] == NORMALIZED_STAKE_USDT
        assert rows[enabled_key] == 1.0
        assert rows[stake_key] == NORMALIZED_STAKE_USDT


def test_backfill_preserves_existing_values_and_is_idempotent():
    store = FakeStore()
    first_strategy = STRATEGIES[0]
    enabled_key = f"strategy_{first_strategy.lower()}_enabled"
    stake_key = f"strategy_{first_strategy.lower()}_stake"
    store.db.execute("INSERT INTO config(key, value) VALUES (?, 0)", (enabled_key,))
    store.db.execute("INSERT INTO config(key, value) VALUES (?, 3.5)", (stake_key,))
    store.db.commit()

    defaults: dict[str, float | bool] = {
        enabled_key: True,
        stake_key: NORMALIZED_STAKE_USDT,
    }
    first_inserted = backfill_strong_trend_runtime_config(
        store=store,
        default_config=defaults,
    )
    second_inserted = backfill_strong_trend_runtime_config(
        store=store,
        default_config=defaults,
    )

    assert first_inserted == (len(STRATEGIES) - 1) * 2
    assert second_inserted == 0
    rows = dict(store.db.execute("SELECT key, value FROM config").fetchall())
    assert rows[enabled_key] == 0.0
    assert rows[stake_key] == 3.5
