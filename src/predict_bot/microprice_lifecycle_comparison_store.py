from __future__ import annotations

import math
import sqlite3
import statistics
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .microprice_signal_lifecycle import TABLE as SOURCE_TABLE

COMPARISON_TABLE = "microprice_lifecycle_v2_hold_pairs"
VERSION = "MICROPRICE_LIFECYCLE_PAIRED_AB_V1"


def _iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1e9, timezone.utc).isoformat(timespec="microseconds")


def _now_ns(snapshot: dict[str, Any]) -> int:
    try:
        value = int(snapshot.get("received_wall_ns") or snapshot.get("timestamp_ns") or 0)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else time.time_ns()


class MicropriceLifecycleComparisonTracker:
    """Paper-only paired counterfactual using one identical simulated fill."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=2.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=2000")
        self.lock = threading.RLock()
        self.last_error: str | None = None
        self.last_sync_at: str | None = None
        self.counters = {
            "syncCalls": 0,
            "seededPairs": 0,
            "rollovers": 0,
            "settledPairs": 0,
            "syncErrors": 0,
        }
        self._schema()

    def _schema(self) -> None:
        with self.lock:
            self.db.executescript(f"""
            CREATE TABLE IF NOT EXISTS {COMPARISON_TABLE}(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              source_episode_id INTEGER NOT NULL UNIQUE,
              market_id INTEGER NOT NULL,
              topic_id INTEGER NOT NULL,
              side TEXT NOT NULL CHECK(side IN('UP','DOWN')),
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              created_ns INTEGER NOT NULL,
              settlement_pending_at TEXT,
              settlement_pending_ns INTEGER,
              settled_at TEXT,
              settled_ns INTEGER,
              official_winner TEXT,
              entry_price REAL NOT NULL,
              stake REAL NOT NULL,
              shares REAL NOT NULL,
              entry_fee REAL NOT NULL DEFAULT 0,
              fee_rate_bps INTEGER NOT NULL DEFAULT 0,
              pnl REAL
            );
            CREATE INDEX IF NOT EXISTS {COMPARISON_TABLE}_market_idx
              ON {COMPARISON_TABLE}(market_id,id DESC);
            CREATE INDEX IF NOT EXISTS {COMPARISON_TABLE}_status_idx
              ON {COMPARISON_TABLE}(status,id DESC);
            """)
            self.db.commit()

    def _seed_pairs(self) -> int:
        now = time.time_ns()
        with self.lock:
            before = self.db.total_changes
            self.db.execute(f"""
            INSERT OR IGNORE INTO {COMPARISON_TABLE}(
              source_episode_id,market_id,topic_id,side,status,created_at,
              created_ns,entry_price,stake,shares,entry_fee,fee_rate_bps
            )
            SELECT id,market_id,topic_id,side,'OPEN',COALESCE(filled_at,?),
              COALESCE(filled_ns,?),entry_price,stake,shares,entry_fee,fee_rate_bps
            FROM {SOURCE_TABLE}
            WHERE filled_at IS NOT NULL
              AND entry_price IS NOT NULL
              AND stake > 0
              AND shares > 0
            """, (_iso(now), now))
            seeded = self.db.total_changes - before
            self.db.commit()
        self.counters["seededPairs"] += seeded
        return seeded

    def _mark_rollovers(self, current_market_id: int, now: int) -> int:
        with self.lock:
            cursor = self.db.execute(f"""
            UPDATE {COMPARISON_TABLE}
            SET status='SETTLEMENT_PENDING',
                settlement_pending_at=COALESCE(settlement_pending_at,?),
                settlement_pending_ns=COALESCE(settlement_pending_ns,?)
            WHERE status='OPEN' AND market_id<>?
            """, (_iso(now), now, current_market_id))
            count = max(0, int(cursor.rowcount or 0))
            self.db.commit()
        self.counters["rollovers"] += count
        return count

    def _settle(self, now: int | None = None) -> int:
        settled_ns = now if now is not None else time.time_ns()
        with self.lock:
            try:
                rows = self.db.execute(f"""
                SELECT h.*,s.official_winner AS settlement_winner
                FROM {COMPARISON_TABLE} h
                JOIN market_settlements s ON s.market_id=h.market_id
                WHERE h.status IN('OPEN','SETTLEMENT_PENDING')
                  AND s.status='OFFICIAL'
                  AND s.official_winner IN('UP','DOWN')
                """).fetchall()
            except sqlite3.OperationalError as exc:
                if "no such table: market_settlements" in str(exc):
                    return 0
                raise
            for row in rows:
                won = str(row["side"]) == str(row["settlement_winner"])
                payout = float(row["shares"] or 0) if won else 0.0
                pnl = payout - float(row["stake"] or 0) - float(row["entry_fee"] or 0)
                self.db.execute(f"""
                UPDATE {COMPARISON_TABLE}
                SET status=?,settled_at=?,settled_ns=?,official_winner=?,pnl=?
                WHERE id=?
                """, (
                    "SETTLED_WIN" if won else "SETTLED_LOSS",
                    _iso(settled_ns), settled_ns, row["settlement_winner"], pnl, row["id"],
                ))
            if rows:
                self.db.commit()
        self.counters["settledPairs"] += len(rows)
        return len(rows)

    def sync(self, snapshot: dict[str, Any]) -> None:
        self.counters["syncCalls"] += 1
        market_id = int(snapshot["market_id"])
        now = _now_ns(snapshot)
        self._seed_pairs()
        self._mark_rollovers(market_id, now)
        self._settle(now)
        self.last_sync_at = _iso(now)
        self.last_error = None

    def state(self) -> dict[str, Any]:
        self._seed_pairs()
        self._settle()
        with self.lock:
            rows = [dict(row) for row in self.db.execute(f"""
            SELECT h.*,
              e.status AS exit_status,
              e.end_reason AS exit_reason,
              e.ended_at AS exit_ended_at,
              e.exit_price AS exit_price,
              e.pnl AS exit_pnl,
              e.signal_duration_ms AS signal_duration_ms,
              e.position_duration_ms AS exit_position_duration_ms
            FROM {COMPARISON_TABLE} h
            JOIN {SOURCE_TABLE} e ON e.id=h.source_episode_id
            ORDER BY h.id
            """).fetchall()]
        completed = [row for row in rows if row.get("pnl") is not None and row.get("exit_pnl") is not None]
        differences = [float(row["exit_pnl"]) - float(row["pnl"]) for row in completed]
        exit_total = sum(float(row["exit_pnl"]) for row in completed)
        hold_total = sum(float(row["pnl"]) for row in completed)
        epsilon = 1e-9
        by_reason: dict[str, dict[str, float | int]] = {}
        for row in completed:
            reason = str(row.get("exit_reason") or "HELD_TO_SETTLEMENT")
            bucket = by_reason.setdefault(reason, {
                "count": 0,
                "exitOnSignalPnl": 0.0,
                "holdToEndPnl": 0.0,
                "netAdvantage": 0.0,
            })
            exit_pnl, hold_pnl = float(row["exit_pnl"]), float(row["pnl"])
            bucket["count"] = int(bucket["count"]) + 1
            bucket["exitOnSignalPnl"] = float(bucket["exitOnSignalPnl"]) + exit_pnl
            bucket["holdToEndPnl"] = float(bucket["holdToEndPnl"]) + hold_pnl
            bucket["netAdvantage"] = float(bucket["netAdvantage"]) + exit_pnl - hold_pnl
        recent = []
        for row in rows[-20:][::-1]:
            difference = (
                float(row["exit_pnl"]) - float(row["pnl"])
                if row.get("pnl") is not None and row.get("exit_pnl") is not None
                else None
            )
            recent.append({
                "pairId": row["id"],
                "sourceEpisodeId": row["source_episode_id"],
                "marketId": row["market_id"],
                "side": row["side"],
                "entryPrice": row["entry_price"],
                "stake": row["stake"],
                "shares": row["shares"],
                "exitArmStatus": row["exit_status"],
                "exitReason": row["exit_reason"],
                "exitPrice": row["exit_price"],
                "exitPnl": row["exit_pnl"],
                "exitEndedAt": row["exit_ended_at"],
                "holdArmStatus": row["status"],
                "officialWinner": row["official_winner"],
                "holdPnl": row["pnl"],
                "settledAt": row["settled_at"],
                "signalDurationMs": row["signal_duration_ms"],
                "exitPositionDurationMs": row["exit_position_duration_ms"],
                "advantage": difference,
                "winner": (
                    "EXIT_ON_SIGNAL" if difference is not None and difference > epsilon
                    else "HOLD_TO_END" if difference is not None and difference < -epsilon
                    else "TIE" if difference is not None else None
                ),
            })
        return {
            "version": VERSION,
            "status": "LIVE" if self.last_error is None else "DEGRADED",
            "paperOnly": True,
            "liveOrdersAffected": False,
            "methodology": {
                "pairing": "same entry time, side, price, stake, shares, and entry fee",
                "exitOnSignalArm": "exit on EDGE_LOST or SIGNAL_REVERSED; otherwise settle",
                "holdToEndArm": "never early-exit; official settlement only",
            },
            "totalPairs": len(rows),
            "completedPairs": len(completed),
            "pendingPairs": len(rows) - len(completed),
            "earlyExitPairs": sum(row.get("exit_status") == "EXITED" for row in rows),
            "exitOnSignal": {
                "totalPnl": exit_total,
                "averagePnl": exit_total / len(completed) if completed else None,
            },
            "holdToEnd": {
                "totalPnl": hold_total,
                "averagePnl": hold_total / len(completed) if completed else None,
            },
            "comparison": {
                "netAdvantage": exit_total - hold_total,
                "averageAdvantage": statistics.fmean(differences) if differences else None,
                "medianAdvantage": statistics.median(differences) if differences else None,
                "exitBetter": sum(value > epsilon for value in differences),
                "holdBetter": sum(value < -epsilon for value in differences),
                "ties": sum(abs(value) <= epsilon for value in differences),
                "p90AbsoluteDifference": (
                    sorted(abs(value) for value in differences)[max(0, math.ceil(len(differences) * 0.9) - 1)]
                    if differences else None
                ),
            },
            "byExitReason": by_reason,
            "recentPairs": recent,
            "runtime": {
                "lastSyncAt": self.last_sync_at,
                "lastError": self.last_error,
                "counters": dict(self.counters),
            },
        }
