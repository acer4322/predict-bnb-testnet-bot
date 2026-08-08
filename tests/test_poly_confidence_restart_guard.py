from __future__ import annotations

import sqlite3
from pathlib import Path

from predict_bot.poly_confidence_restart_guard import (
    invalidate_unfinished_confidence_shadows,
)


def _db(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE poly_confidence_shadows (
            id INTEGER PRIMARY KEY,
            status TEXT NOT NULL,
            finalized_at_ms INTEGER
        )
        """
    )
    connection.executemany(
        "INSERT INTO poly_confidence_shadows(id, status) VALUES (?, ?)",
        [
            (1, "PENDING_ATTACH"),
            (2, "ARMED"),
            (3, "EXIT_TRIGGERED"),
            (4, "EXITED"),
            (5, "FINALIZED_EXIT"),
        ],
    )
    connection.commit()
    return connection


def test_restart_guard_excludes_only_unfinished_causal_paths(tmp_path: Path) -> None:
    path = tmp_path / "cross_oracle.db"
    writer = _db(path)
    writer.close()

    changed = invalidate_unfinished_confidence_shadows(path)
    assert changed == 3

    reader = sqlite3.connect(path)
    rows = dict(reader.execute("SELECT id, status FROM poly_confidence_shadows"))
    finalized = dict(reader.execute("SELECT id, finalized_at_ms FROM poly_confidence_shadows"))
    reader.close()

    assert rows[1] == "NOT_EVALUABLE_PROCESS_RESTART"
    assert rows[2] == "NOT_EVALUABLE_PROCESS_RESTART"
    assert rows[3] == "NOT_EVALUABLE_PROCESS_RESTART"
    assert rows[4] == "EXITED"
    assert rows[5] == "FINALIZED_EXIT"
    assert finalized[1] is not None
    assert finalized[2] is not None
    assert finalized[3] is not None


def test_restart_guard_is_safe_before_confidence_table_exists(tmp_path: Path) -> None:
    path = tmp_path / "cross_oracle.db"
    sqlite3.connect(path).close()
    assert invalidate_unfinished_confidence_shadows(path) == 0
