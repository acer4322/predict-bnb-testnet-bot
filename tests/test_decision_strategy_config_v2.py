from __future__ import annotations

from predict_bot import server
from predict_bot.decision_strategy_config_v2 import (
    MIGRATION_KEY,
    initialize_controller_config,
)
from predict_bot.decision_strategy_rules import STRATEGIES


def test_initial_config_enables_controllers_once_and_preserves_later_disable(
    tmp_path,
) -> None:
    store = server.Store(tmp_path / "simulation.db")

    with store.lock:
        for strategy in STRATEGIES:
            store.db.execute(
                """INSERT INTO config(key, value) VALUES (?, 0)
                   ON CONFLICT(key) DO UPDATE SET value=0""",
                (f"strategy_{strategy.lower()}_enabled",),
            )
        store.db.commit()

    initialize_controller_config(store)
    first = store.config()
    for strategy in STRATEGIES:
        assert first[f"strategy_{strategy.lower()}_enabled"] is True

    with store.lock:
        for strategy in STRATEGIES:
            store.db.execute(
                "UPDATE config SET value=0 WHERE key=?",
                (f"strategy_{strategy.lower()}_enabled",),
            )
        store.db.commit()
    store._config_cache = None

    initialize_controller_config(store)
    second = store.config()
    for strategy in STRATEGIES:
        assert second[f"strategy_{strategy.lower()}_enabled"] is False

    marker = store.db.execute(
        "SELECT value FROM decision_strategy_install_state WHERE key=?",
        (MIGRATION_KEY,),
    ).fetchone()
    assert marker is not None
    assert str(marker["value"]) == "applied"
