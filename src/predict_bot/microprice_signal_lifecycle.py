from __future__ import annotations

import math
import os
import sqlite3
import statistics
import threading
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any

STRATEGY = "R_MICROPRICE_LIFECYCLE"
VERSION = "MICROPRICE_SIGNAL_LIFECYCLE_V2_ISOLATED"
TABLE = "microprice_lifecycle_v2_episodes"
ACTIVE = ("SIGNAL", "PENDING", "OPEN", "EXIT_PENDING")
ENTRY, RELEASE, REVERSE = 0.20, 0.10, 0.20
CONFIRMATIONS, CONFIRM_MS, FILL_DELAY_MS, PENDING_MS = 3, 300.0, 250.0, 1500.0
MAX_AGE_MS, MAX_SKEW_MS, MAX_SPREAD, MAX_ENTRY = 500.0, 150.0, 0.03, 0.55
STAKE, WINDOW_MIN, WINDOW_MAX = 5.0, 176.0, 181.0


def _f(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1e9, timezone.utc).isoformat(timespec="microseconds")


def _ns(snapshot: dict[str, Any]) -> int:
    for key in ("received_wall_ns", "timestamp_ns"):
        try:
            value = int(snapshot.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    try:
        parsed = datetime.fromisoformat(str(snapshot["timestamp"]).replace("Z", "+00:00"))
        return int(parsed.timestamp() * 1e9)
    except (KeyError, TypeError, ValueError):
        return time.time_ns()


def _event(snapshot: dict[str, Any]) -> str:
    return str(snapshot.get("signal_event_sequence") or ":".join(str(value or "") for value in (
        snapshot.get("market_id"), snapshot.get("up_book_timestamp_ms"),
        snapshot.get("down_book_timestamp_ms"), snapshot.get("received_wall_ns"),
    )))


def _imbalance(bid: float, ask: float) -> float:
    return (bid - ask) / (bid + ask) if bid + ask > 0 else 0.0


def microprice_score(snapshot: dict[str, Any]) -> float | None:
    values = [_f(snapshot.get(key)) for key in (
        "up_bid_size", "up_ask_size", "down_bid_size", "down_ask_size",
    )]
    if any(value is None or value <= 0 for value in values):
        return None
    up_bid, up_ask, down_bid, down_ask = (float(value) for value in values)
    return _imbalance(up_bid, up_ask) - _imbalance(down_bid, down_ask)


def _book(snapshot: dict[str, Any], side: str) -> tuple[bool, str, dict[str, float] | None]:
    prefix = side.lower()
    raw = {
        "ask": _f(snapshot.get(f"{prefix}_ask")),
        "bid": _f(snapshot.get(f"{prefix}_bid")),
        "ask_size": _f(snapshot.get(f"{prefix}_ask_size")),
        "bid_size": _f(snapshot.get(f"{prefix}_bid_size")),
        "age": _f(snapshot.get("book_age_ms")),
        "skew": _f(snapshot.get("book_skew_ms")),
    }
    if any(value is None for value in raw.values()):
        return False, "required direct book fields unavailable", None
    book = {key: float(value) for key, value in raw.items()}
    if not 0 < book["ask"] < 1 or not 0 <= book["bid"] <= book["ask"]:
        return False, "invalid top of book", book
    if book["ask_size"] <= 0 or book["bid_size"] < 0:
        return False, "invalid visible depth", book
    if book["ask"] - book["bid"] > MAX_SPREAD:
        return False, "spread exceeds lifecycle limit", book
    if not 0 <= book["age"] <= MAX_AGE_MS:
        return False, "book age exceeds lifecycle limit", book
    if not 0 <= book["skew"] <= MAX_SKEW_MS:
        return False, "book skew exceeds lifecycle limit", book
    return True, "safe", book


def _fee(shares: float, price: float, bps: int) -> float:
    return shares * min(price, 1.0 - price) * bps / 10_000 if shares > 0 else 0.0


class MicropriceSignalLifecycleTracker:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=2.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=2000")
        self.lock = threading.RLock()
        self.active: dict[str, Any] | None = None
        self.last_event: str | None = None
        self.last_decision: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.counters = {"processedSnapshots": 0, "duplicateSnapshots": 0,
                         "processingErrors": 0, "exitDepthBlocks": 0}
        self._schema()
        self._recover()

    def _schema(self) -> None:
        with self.lock:
            self.db.executescript(f"""
            CREATE TABLE IF NOT EXISTS {TABLE}(
              id INTEGER PRIMARY KEY AUTOINCREMENT, market_id INTEGER NOT NULL,
              topic_id INTEGER NOT NULL, side TEXT NOT NULL CHECK(side IN('UP','DOWN')),
              status TEXT NOT NULL, started_at TEXT NOT NULL, started_ns INTEGER NOT NULL,
              confirmed_at TEXT, confirmed_ns INTEGER, order_created_at TEXT,
              order_created_ns INTEGER, order_limit_price REAL, filled_at TEXT,
              filled_ns INTEGER, ended_at TEXT, ended_ns INTEGER, end_reason TEXT,
              exit_requested_at TEXT, exit_requested_ns INTEGER, exit_requested_reason TEXT,
              start_score REAL NOT NULL, peak_abs_score REAL NOT NULL, last_score REAL,
              end_score REAL, confirmations INTEGER NOT NULL DEFAULT 1,
              event_count INTEGER NOT NULL DEFAULT 1, signal_duration_ms REAL,
              pending_duration_ms REAL, position_duration_ms REAL,
              stake REAL NOT NULL DEFAULT 0, shares REAL NOT NULL DEFAULT 0,
              entry_price REAL, exit_price REAL, entry_fee REAL NOT NULL DEFAULT 0,
              exit_fee REAL NOT NULL DEFAULT 0, pnl REAL,
              fee_rate_bps INTEGER NOT NULL DEFAULT 0, last_event_key TEXT
            );
            CREATE INDEX IF NOT EXISTS {TABLE}_market_idx ON {TABLE}(market_id,id DESC);
            CREATE INDEX IF NOT EXISTS {TABLE}_status_idx ON {TABLE}(status,id DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS {TABLE}_active_idx ON {TABLE}(market_id)
              WHERE status IN('SIGNAL','PENDING','OPEN','EXIT_PENDING');
            """)
            self.db.commit()

    def _recover(self) -> None:
        with self.lock:
            row = self.db.execute(
                f"SELECT * FROM {TABLE} WHERE status IN (?,?,?,?) ORDER BY id DESC LIMIT 1",
                ACTIVE,
            ).fetchone()
        if row:
            self.active = dict(row)
            self.last_event = str(row["last_event_key"] or "") or None
            self.last_decision = {"status": "RECOVERED", "episodeId": row["id"],
                                  "marketId": row["market_id"]}

    def _update(self, sql: str, values: tuple[Any, ...]) -> None:
        with self.lock:
            self.db.execute(sql, values)
            self.db.commit()

    def _start(self, snapshot: dict[str, Any], side: str, score: float,
               fee_bps: int, now: int, event: str) -> None:
        with self.lock:
            cursor = self.db.execute(
                f"""INSERT INTO {TABLE}(market_id,topic_id,side,status,started_at,
                started_ns,start_score,peak_abs_score,last_score,fee_rate_bps,last_event_key)
                VALUES(?,?,?,'SIGNAL',?,?,?,?,?,?,?)""",
                (int(snapshot["market_id"]), int(snapshot["topic_id"]), side, _iso(now),
                 now, score, abs(score), score, int(fee_bps), event),
            )
            self.db.commit()
        self.active = {"id": int(cursor.lastrowid), "market_id": int(snapshot["market_id"]),
                       "topic_id": int(snapshot["topic_id"]), "side": side, "status": "SIGNAL",
                       "started_ns": now, "start_score": score, "peak_abs_score": abs(score),
                       "last_score": score, "confirmations": 1, "event_count": 1,
                       "fee_rate_bps": int(fee_bps), "last_event_key": event}
        self.last_decision = {"status": "SIGNAL_STARTED", "episodeId": cursor.lastrowid,
                              "marketId": snapshot["market_id"], "side": side, "score": score}

    def _record(self, score: float | None, event: str) -> None:
        assert self.active
        peak = max(float(self.active.get("peak_abs_score") or 0), abs(score or 0))
        confirms = int(self.active.get("confirmations") or 0) + 1
        count = int(self.active.get("event_count") or 0) + 1
        self._update(f"UPDATE {TABLE} SET peak_abs_score=?,last_score=?,confirmations=?,"
                     "event_count=?,last_event_key=? WHERE id=?",
                     (peak, score, confirms, count, event, int(self.active["id"])))
        self.active.update(peak_abs_score=peak, last_score=score, confirmations=confirms,
                           event_count=count, last_event_key=event)

    def _cancel(self, reason: str, score: float | None, now: int,
                detail: str | None = None) -> None:
        assert self.active
        started = int(self.active["started_ns"])
        order = self.active.get("order_created_ns")
        self._update(f"""UPDATE {TABLE} SET status='CANCELLED',ended_at=?,ended_ns=?,
        end_reason=?,end_score=?,signal_duration_ms=?,pending_duration_ms=? WHERE id=?""",
        (_iso(now), now, reason, score, (now-started)/1e6,
         (now-int(order))/1e6 if order is not None else None, int(self.active["id"])))
        self.last_decision = {"status": "CANCELLED", "reason": reason, "detail": detail,
                              "episodeId": self.active["id"], "marketId": self.active["market_id"],
                              "side": self.active["side"], "score": score,
                              "signalDurationMs": (now-started)/1e6}
        self.active = None

    def _pending(self, snapshot: dict[str, Any], score: float, now: int) -> None:
        assert self.active
        ok, reason, book = _book(snapshot, str(self.active["side"]))
        if not ok or book is None or book["ask"] > MAX_ENTRY:
            self._cancel("DATA_INVALIDATED", score, now,
                         reason if not ok else "entry ask exceeds lifecycle limit")
            return
        if book["ask_size"] + 1e-12 < STAKE / book["ask"]:
            self._cancel("DATA_INVALIDATED", score, now, "visible ask depth insufficient")
            return
        self._update(f"""UPDATE {TABLE} SET status='PENDING',confirmed_at=?,confirmed_ns=?,
        order_created_at=?,order_created_ns=?,order_limit_price=? WHERE id=?""",
        (_iso(now), now, _iso(now), now, book["ask"], int(self.active["id"])))
        self.active.update(status="PENDING", confirmed_at=_iso(now), confirmed_ns=now,
                           order_created_at=_iso(now), order_created_ns=now,
                           order_limit_price=book["ask"])
        self.last_decision = {"status": "PENDING_ORDER", "episodeId": self.active["id"],
                              "side": self.active["side"], "score": score,
                              "limitPrice": book["ask"]}

    def _fill(self, snapshot: dict[str, Any], score: float, now: int) -> None:
        assert self.active
        ok, reason, book = _book(snapshot, str(self.active["side"]))
        if not ok or book is None:
            self._cancel("DATA_INVALIDATED", score, now, reason)
            return
        if book["ask"] > float(self.active["order_limit_price"]) + 1e-12:
            self.last_decision = {"status": "PENDING_WAIT", "reason": "ask above frozen limit",
                                  "episodeId": self.active["id"], "currentAsk": book["ask"],
                                  "limitPrice": self.active["order_limit_price"]}
            return
        shares = STAKE / book["ask"]
        if book["ask_size"] + 1e-12 < shares:
            self.last_decision = {"status": "PENDING_WAIT", "reason": "ask depth insufficient",
                                  "episodeId": self.active["id"]}
            return
        fee = _fee(shares, book["ask"], int(self.active.get("fee_rate_bps") or 0))
        order = int(self.active["order_created_ns"])
        self._update(f"""UPDATE {TABLE} SET status='OPEN',filled_at=?,filled_ns=?,stake=?,
        shares=?,entry_price=?,entry_fee=?,pending_duration_ms=? WHERE id=?""",
        (_iso(now), now, STAKE, shares, book["ask"], fee, (now-order)/1e6,
         int(self.active["id"])))
        self.active.update(status="OPEN", filled_at=_iso(now), filled_ns=now, stake=STAKE,
                           shares=shares, entry_price=book["ask"], entry_fee=fee)
        self.last_decision = {"status": "FILLED", "episodeId": self.active["id"],
                              "side": self.active["side"], "entryPrice": book["ask"],
                              "shares": shares, "signalAgeMs": (now-int(self.active["started_ns"]))/1e6}

    def _exit(self, snapshot: dict[str, Any], reason: str,
              score: float | None, now: int) -> bool:
        assert self.active
        ok, detail, book = _book(snapshot, str(self.active["side"]))
        shares = float(self.active.get("shares") or 0)
        if not ok or book is None or book["bid_size"] + 1e-12 < shares:
            self.counters["exitDepthBlocks"] += 1
            if self.active.get("exit_requested_ns") is None:
                self._update(f"""UPDATE {TABLE} SET status='EXIT_PENDING',exit_requested_at=?,
                exit_requested_ns=?,exit_requested_reason=? WHERE id=?""",
                (_iso(now), now, reason, int(self.active["id"])))
                self.active.update(status="EXIT_PENDING", exit_requested_at=_iso(now),
                                   exit_requested_ns=now, exit_requested_reason=reason)
            self.last_decision = {"status": "EXIT_PENDING", "reason": reason,
                                  "detail": detail if not ok else "bid depth insufficient",
                                  "episodeId": self.active["id"], "score": score}
            return False
        exit_fee = _fee(shares, book["bid"], int(self.active.get("fee_rate_bps") or 0))
        pnl = shares*book["bid"] - STAKE - float(self.active.get("entry_fee") or 0) - exit_fee
        started, filled = int(self.active["started_ns"]), int(self.active["filled_ns"])
        self._update(f"""UPDATE {TABLE} SET status='EXITED',ended_at=?,ended_ns=?,end_reason=?,
        end_score=?,signal_duration_ms=?,position_duration_ms=?,exit_price=?,exit_fee=?,pnl=?
        WHERE id=?""", (_iso(now), now, reason, score, (now-started)/1e6,
        (now-filled)/1e6, book["bid"], exit_fee, pnl, int(self.active["id"])))
        self.last_decision = {"status": "EXITED", "reason": reason,
                              "episodeId": self.active["id"], "exitPrice": book["bid"],
                              "pnl": pnl, "signalDurationMs": (now-started)/1e6,
                              "positionDurationMs": (now-filled)/1e6}
        self.active = None
        return True

    def _rollover(self, now: int) -> None:
        assert self.active
        if self.active["status"] in {"SIGNAL", "PENDING"}:
            self._cancel("MARKET_ROLLOVER_CANCELLED", _f(self.active.get("last_score")), now)
            return
        started, filled = int(self.active["started_ns"]), self.active.get("filled_ns")
        self._update(f"""UPDATE {TABLE} SET status='SETTLEMENT_PENDING',ended_at=?,ended_ns=?,
        end_reason='MARKET_ROLLOVER_HELD',signal_duration_ms=?,position_duration_ms=? WHERE id=?""",
        (_iso(now), now, (now-started)/1e6,
         (now-int(filled))/1e6 if filled is not None else None, int(self.active["id"])))
        self.last_decision = {"status": "SETTLEMENT_PENDING",
                              "reason": "MARKET_ROLLOVER_HELD", "episodeId": self.active["id"]}
        self.active = None

    def _settle(self) -> int:
        with self.lock:
            try:
                rows = self.db.execute(f"""SELECT e.*,s.official_winner FROM {TABLE} e
                JOIN market_settlements s ON s.market_id=e.market_id
                WHERE e.status='SETTLEMENT_PENDING' AND s.status='OFFICIAL'
                AND s.official_winner IN('UP','DOWN')""").fetchall()
            except sqlite3.OperationalError as exc:
                if "no such table: market_settlements" in str(exc):
                    return 0
                raise
            for row in rows:
                won = row["side"] == row["official_winner"]
                payout = float(row["shares"] or 0) if won else 0.0
                pnl = payout - float(row["stake"] or 0) - float(row["entry_fee"] or 0)
                self.db.execute(f"UPDATE {TABLE} SET status=?,exit_price=?,pnl=? WHERE id=?",
                                ("SETTLED_WIN" if won else "SETTLED_LOSS",
                                 1.0 if won else 0.0, pnl, row["id"]))
            if rows:
                self.db.commit()
        return len(rows)

    def process(self, snapshot: dict[str, Any], fee_bps: int) -> None:
        self._settle()
        market, seconds, now, event = (int(snapshot["market_id"]),
            float(snapshot["seconds_left"]), _ns(snapshot), _event(snapshot))
        if event == self.last_event:
            self.counters["duplicateSnapshots"] += 1
            return
        self.last_event = event
        self.counters["processedSnapshots"] += 1
        if self.active and int(self.active["market_id"]) != market:
            self._rollover(now)
        score = microprice_score(snapshot)
        if self.active is None:
            if score is None or abs(score) < ENTRY:
                return
            if not WINDOW_MIN <= seconds <= WINDOW_MAX:
                self.last_decision = {"status": "WAITING", "reason": "outside frozen window",
                                      "marketId": market, "score": score, "secondsLeft": seconds}
                return
            side = "UP" if score > 0 else "DOWN"
            ok, reason, book = _book(snapshot, side)
            if not ok or book is None or book["ask"] > MAX_ENTRY or \
                    book["ask_size"] + 1e-12 < STAKE/book["ask"]:
                self.last_decision = {"status": "BLOCK", "reason": reason if not ok else
                    ("entry ask exceeds limit" if book and book["ask"] > MAX_ENTRY else
                     "visible ask depth insufficient"), "marketId": market,
                     "side": side, "score": score}
                return
            self._start(snapshot, side, score, fee_bps, now, event)
            return
        self._record(score, event)
        assert self.active
        if self.active["status"] == "EXIT_PENDING":
            self._exit(snapshot, str(self.active.get("exit_requested_reason") or "EDGE_LOST"),
                       score, now)
            return
        signed = (score or 0.0) * (1 if self.active["side"] == "UP" else -1)
        if score is not None and signed <= -REVERSE:
            self._exit(snapshot, "SIGNAL_REVERSED", score, now) if self.active["status"] == "OPEN" \
                else self._cancel("SIGNAL_REVERSED", score, now)
            return
        if score is None or signed < RELEASE:
            self._exit(snapshot, "EDGE_LOST", score, now) if self.active["status"] == "OPEN" \
                else self._cancel("EDGE_LOST", score, now)
            return
        age = (now-int(self.active["started_ns"]))/1e6
        if self.active["status"] == "SIGNAL":
            if int(self.active["confirmations"]) >= CONFIRMATIONS and age >= CONFIRM_MS:
                self._pending(snapshot, score, now)
            else:
                self.last_decision = {"status": "ACCUMULATING", "episodeId": self.active["id"],
                    "side": self.active["side"], "score": score,
                    "confirmations": self.active["confirmations"], "signalAgeMs": age}
            return
        if self.active["status"] == "PENDING":
            pending = (now-int(self.active["order_created_ns"]))/1e6
            if pending > PENDING_MS:
                self._cancel("ORDER_NOT_FILLED", score, now, "frozen ask timed out")
            elif pending >= FILL_DELAY_MS:
                self._fill(snapshot, score, now)
            else:
                self.last_decision = {"status": "PENDING_ORDER", "episodeId": self.active["id"],
                    "side": self.active["side"], "score": score,
                    "limitPrice": self.active["order_limit_price"], "pendingAgeMs": pending}
            return
        self.last_decision = {"status": "OPEN_MONITORING", "episodeId": self.active["id"],
            "side": self.active["side"], "score": score, "signalAgeMs": age,
            "positionAgeMs": (now-int(self.active["filled_ns"]))/1e6}

    def state(self) -> dict[str, Any]:
        self._settle()
        with self.lock:
            rows = [dict(row) for row in self.db.execute(f"SELECT * FROM {TABLE} ORDER BY id").fetchall()]
        now = time.time_ns()
        active = dict(self.active) if self.active else None
        if active:
            active["signalAgeMs"] = max(0.0, (now-int(active["started_ns"]))/1e6)
            if active.get("order_created_ns") is not None:
                active["pendingAgeMs"] = max(0.0, (now-int(active["order_created_ns"]))/1e6)
            if active.get("filled_ns") is not None:
                active["positionAgeMs"] = max(0.0, (now-int(active["filled_ns"]))/1e6)
        closed = [row for row in rows if row.get("signal_duration_ms") is not None]
        durations = [float(row["signal_duration_ms"]) for row in closed]
        reasons: dict[str, list[float]] = {}
        for row in closed:
            reasons.setdefault(str(row.get("end_reason") or "UNKNOWN"), []).append(float(row["signal_duration_ms"]))
        reason_state = {key: {"count": len(values), "averageDurationMs": statistics.fmean(values),
                              "medianDurationMs": statistics.median(values)}
                        for key, values in reasons.items()}
        recent = [{"id": row["id"], "marketId": row["market_id"], "side": row["side"],
            "status": row["status"], "startedAt": row["started_at"], "confirmedAt": row["confirmed_at"],
            "filledAt": row["filled_at"], "endedAt": row["ended_at"], "endReason": row["end_reason"],
            "startScore": row["start_score"], "peakAbsScore": row["peak_abs_score"],
            "endScore": row["end_score"], "confirmations": row["confirmations"],
            "eventCount": row["event_count"], "signalDurationMs": row["signal_duration_ms"],
            "pendingDurationMs": row["pending_duration_ms"], "positionDurationMs": row["position_duration_ms"],
            "entryPrice": row["entry_price"], "exitPrice": row["exit_price"], "pnl": row["pnl"],
            "exitRequestedReason": row["exit_requested_reason"]} for row in rows[-20:][::-1]]
        p90 = sorted(durations)[max(0, math.ceil(len(durations)*.9)-1)] if durations else None
        return {"version": VERSION, "strategy": STRATEGY,
            "status": "LIVE" if self.last_error is None else "DEGRADED",
            "paperOnly": True, "liveOrdersAffected": False,
            "source": "collector_direct_dual_token_rest", "samplingMode": "periodic_snapshot",
            "episodes": len(rows), "activeEpisodes": sum(row["status"] in ACTIVE for row in rows),
            "confirmedEpisodes": sum(row["confirmed_at"] is not None for row in rows),
            "filledEpisodes": sum(row["filled_at"] is not None for row in rows),
            "cancelledEpisodes": sum(row["status"] == "CANCELLED" for row in rows),
            "exitedEpisodes": sum(row["status"] == "EXITED" for row in rows),
            "settlementPendingEpisodes": sum(row["status"] == "SETTLEMENT_PENDING" for row in rows),
            "settledWins": sum(row["status"] == "SETTLED_WIN" for row in rows),
            "settledLosses": sum(row["status"] == "SETTLED_LOSS" for row in rows),
            "realizedPnl": sum(float(row["pnl"]) for row in rows if row.get("pnl") is not None),
            "duration": {"averageMs": statistics.fmean(durations) if durations else None,
                "medianMs": statistics.median(durations) if durations else None, "p90Ms": p90,
                "minimumMs": min(durations) if durations else None,
                "maximumMs": max(durations) if durations else None},
            "reasons": reason_state, "recentEpisodes": recent,
            "runtime": {"active": active, "lastDecision": self.last_decision,
                "lastError": self.last_error, "counters": dict(self.counters),
                "rules": {"entryThreshold": ENTRY, "releaseThreshold": RELEASE,
                    "reverseThreshold": REVERSE, "minimumConfirmations": CONFIRMATIONS,
                    "minimumConfirmationMs": CONFIRM_MS, "minimumFillDelayMs": FILL_DELAY_MS,
                    "maximumPendingMs": PENDING_MS, "maximumBookAgeMs": MAX_AGE_MS,
                    "maximumBookSkewMs": MAX_SKEW_MS, "maximumSpread": MAX_SPREAD,
                    "maximumEntry": MAX_ENTRY, "stakeUsdt": STAKE,
                    "entryWindowSecondsLeft": [WINDOW_MIN, WINDOW_MAX]}}}


def _tracker(engine: Any) -> MicropriceSignalLifecycleTracker:
    tracker = getattr(engine, "_microprice_signal_lifecycle_tracker", None)
    if isinstance(tracker, MicropriceSignalLifecycleTracker):
        return tracker
    root = Path(__file__).resolve().parents[2]
    tracker = MicropriceSignalLifecycleTracker(Path(os.environ.get(
        "PREDICT_SIM_DB", root / "data" / "simulation.db")))
    engine._microprice_signal_lifecycle_tracker = tracker
    return tracker


def _error(message: str | None = None) -> dict[str, Any]:
    return {"version": VERSION, "strategy": STRATEGY,
            "status": "ERROR" if message else "WAITING_FOR_FIRST_SNAPSHOT",
            "paperOnly": True, "liveOrdersAffected": False, "error": message}


def install_microprice_signal_lifecycle_sidecar(live: Any) -> None:
    engine = live.LiveM0WEngine
    original_record = engine.record_confirmation_add_snapshot
    if not getattr(original_record, "_microprice_lifecycle_v2", False):
        @wraps(original_record)
        def record(self: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
            result = original_record(self, snapshot)
            try:
                tracker = _tracker(self)
                tracker.process(snapshot, int(snapshot.get("fee_bps") or 200))
                tracker.last_error = None
                self._microprice_signal_lifecycle_error = None
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"[:500]
                self._microprice_signal_lifecycle_error = message
                tracker = getattr(self, "_microprice_signal_lifecycle_tracker", None)
                if tracker is not None:
                    tracker.last_error = message
                    tracker.counters["processingErrors"] = int(tracker.counters.get("processingErrors") or 0)+1
            return result
        record._microprice_lifecycle_v2 = True  # type: ignore[attr-defined]
        engine.record_confirmation_add_snapshot = record
    original_state = engine.state
    if not getattr(original_state, "_microprice_lifecycle_v2", False):
        @wraps(original_state)
        def state(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
            payload = original_state(self, *args, **kwargs)
            if kwargs.get("include_ledger", True) is False or not isinstance(payload, dict):
                return payload
            tracker = getattr(self, "_microprice_signal_lifecycle_tracker", None)
            if tracker is None:
                payload["micropriceSignalLifecycle"] = _error(
                    getattr(self, "_microprice_signal_lifecycle_error", None))
            else:
                try:
                    payload["micropriceSignalLifecycle"] = tracker.state()
                except Exception as exc:
                    message = f"{type(exc).__name__}: {exc}"[:500]
                    self._microprice_signal_lifecycle_error = message
                    payload["micropriceSignalLifecycle"] = _error(message)
            return payload
        state._microprice_lifecycle_v2 = True  # type: ignore[attr-defined]
        engine.state = state
