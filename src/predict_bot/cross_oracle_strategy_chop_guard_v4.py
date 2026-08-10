from __future__ import annotations

import time
from typing import Any

from . import cross_oracle_strategy_chop_guard_v3 as launch
from . import cross_oracle_strategy_rolling_stats as rolling


HEARTBEAT_WRITE_INTERVAL_SECONDS = 1.0


class HeartbeatRollingStatsPaperEngine(rolling.RollingStatsPaperEngine):
    """Persist a tiny health heartbeat for the Live persistent-guard gate.

    The 8768 /state endpoint intentionally exposes a large diagnostic snapshot.
    Live should not need that whole payload merely to verify whether the Paper
    regime guard is alive and whether its persisted cross-market pause is active.

    The heartbeat is touched only after a normal Paper evaluation loop completes.
    It is independent of confident UP/DOWN direction, so neutral-but-healthy
    markets do not look stale. Writes are throttled to about once per second.
    A newly created heartbeat starts at zero, so Live remains fail-closed until
    the first successful Paper evaluation has actually completed.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._last_guard_heartbeat_write_monotonic = 0.0
        super().__init__(*args, **kwargs)

    def _create_schema(self) -> None:
        super()._create_schema()
        with self.db_lock:
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS poly_chop_guard_heartbeat (
                       id INTEGER PRIMARY KEY CHECK(id=1),
                       healthy_at_ms INTEGER NOT NULL
                   )"""
            )
            self.db.execute(
                """INSERT OR IGNORE INTO poly_chop_guard_heartbeat(id,healthy_at_ms)
                   VALUES(1,0)"""
            )
            self.db.commit()

    def _touch_guard_heartbeat(self) -> None:
        now = time.monotonic()
        if now - self._last_guard_heartbeat_write_monotonic < HEARTBEAT_WRITE_INTERVAL_SECONDS:
            return
        self._last_guard_heartbeat_write_monotonic = now
        now_ms = int(time.time() * 1000)
        with self.db_lock:
            self.db.execute(
                """INSERT INTO poly_chop_guard_heartbeat(id,healthy_at_ms)
                   VALUES(1,?)
                   ON CONFLICT(id) DO UPDATE SET healthy_at_ms=excluded.healthy_at_ms""",
                (now_ms,),
            )
            self.db.commit()

    def _evaluate_once(self) -> None:
        super()._evaluate_once()
        self._touch_guard_heartbeat()

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        with self.db_lock:
            row = self.db.execute(
                "SELECT healthy_at_ms FROM poly_chop_guard_heartbeat WHERE id=1"
            ).fetchone()
        payload["chopGuardHeartbeat"] = {
            "enabled": True,
            "healthyAtMs": int(row["healthy_at_ms"] if row else 0),
            "writeIntervalMs": int(HEARTBEAT_WRITE_INTERVAL_SECONDS * 1000),
            "purpose": "lightweight persisted health source for dedicated Live guard verification",
            "requiresSuccessfulPaperEvaluation": True,
        }
        return payload


# rolling_stats already installs the concrete engine used by the v3 launcher.
# Replace only that concrete class; all Paper strategies, rolling stats and guard
# behavior remain unchanged.
rolling.base.guard.stable.launch.strategy_module.GapAwareCrossOraclePaperEngine = (
    HeartbeatRollingStatsPaperEngine
)


def main() -> int:
    return launch.main()


if __name__ == "__main__":
    raise SystemExit(main())
