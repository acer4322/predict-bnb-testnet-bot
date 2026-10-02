from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(
    os.environ.get(
        "PREDICT_CROSS_ORACLE_STRATEGY_DB",
        ROOT / "data" / "cross_oracle_strategy.db",
    )
)
ACTIVE_STATUSES = ("PENDING_ATTACH", "ARMED", "EXIT_TRIGGERED")


def invalidate_unfinished_confidence_shadows(db_path: Path = DB_PATH) -> int:
    """Exclude shadows whose causal Poly path may be incomplete across downtime.

    EXITED rows are deliberately preserved: their exit was already observed and
    only the original source trade's eventual final PnL remains to be compared.
    """
    path = Path(db_path)
    if not path.exists():
        return 0
    connection = sqlite3.connect(path, timeout=5.0)
    try:
        table = connection.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type='table' AND name='poly_confidence_shadows'"""
        ).fetchone()
        if table is None:
            return 0
        now_ms = int(time.time() * 1000)
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        cursor = connection.execute(
            f"""UPDATE poly_confidence_shadows
                SET status='NOT_EVALUABLE_PROCESS_RESTART', finalized_at_ms=?
                WHERE status IN ({placeholders})""",
            (now_ms, *ACTIVE_STATUSES),
        )
        connection.commit()
        return int(cursor.rowcount or 0)
    finally:
        connection.close()


def main() -> int:
    count = invalidate_unfinished_confidence_shadows()
    if count:
        print(
            f"Poly confidence restart guard: excluded {count} unfinished shadow(s)",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
