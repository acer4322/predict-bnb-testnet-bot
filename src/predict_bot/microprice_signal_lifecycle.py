from __future__ import annotations

import json
import math
import statistics
import threading
from datetime import datetime, timezone
from functools import wraps
from typing import Any

from . import m_realtime as _realtime
from .core import taker_fee
from .microprice_variants import microprice_score

STRATEGY = MICROPRICE_LIFECYCLE_STRATEGY = "R_MICROPRICE_LIFECYCLE"
VERSION = MICROPRICE_LIFECYCLE_VERSION = "MICROPRICE_SIGNAL_LIFECYCLE_V1"
ENTER, RELEASE, REVERSE = 0.20, 0.10, 0.20
MIN_CONFIRMATIONS, CONFIRM_MS, FILL_DELAY_MS, PENDING_MS = 3, 300.0, 250.0, 1500.0
MAX_AGE_MS, MAX_SKEW_MS, MAX_SPREAD, MAX_ENTRY = 500.0, 150.0, 0.03, 0.55
STAKE, WINDOW_MIN, WINDOW_MAX = 5.0, 176.0, 181.0
DIRECT_SOURCE = "dual_token_rest"
_ACTIVE: "MicropriceSignalLifecycleTracker | None" = None


def _f(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _iso(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, timezone.utc).isoformat(timespec="microseconds")


def _sign(side: str) -> float:
    return 1.0 if side == "UP" else -1.0


def _context_ok(engine: Any, ctx: dict[str, Any]) -> bool:
    event = getattr(engine, "prediction_event", None)
    return bool(
        ctx.get("trigger_source") == "prediction"
        and ctx.get("execution_eligible") is True
        and ctx.get("prediction_data_source") == DIRECT_SOURCE
        and isinstance(event, dict)
        and event.get("prediction_data_source") == DIRECT_SOURCE
        and event.get("direct_outcome_books") is True
    )


def _book(snapshot: dict[str, Any], side: str) -> dict[str, float] | None:
    p = side.lower()
    result = {
        "ask": _f(snapshot.get(f"{p}_ask")),
        "bid": _f(snapshot.get(f"{p}_bid")),
        "ask_size": _f(snapshot.get(f"{p}_ask_size")),
        "bid_size": _f(snapshot.get(f"{p}_bid_size")),
        "age": _f(snapshot.get("book_age_ms")),
        "skew": _f(snapshot.get("book_skew_ms")),
    }
    return None if any(v is None for v in result.values()) else {k: float(v) for k, v in result.items()}


def _safe(snapshot: dict[str, Any], side: str, *, entry: bool, shares: float = 0.0) -> tuple[bool, str, dict[str, float] | None]:
    b = _book(snapshot, side)
    if b is None:
        return False, "required direct book field unavailable", None
    if not 0 < b["bid"] <= b["ask"] < 1:
        return False, "invalid top of book", b
    if b["ask"] - b["bid"] > MAX_SPREAD:
        return False, "spread exceeds lifecycle limit", b
    if not 0 <= b["age"] <= MAX_AGE_MS:
        return False, "book age exceeds lifecycle limit", b
    if not 0 <= b["skew"] <= MAX_SKEW_MS:
        return False, "book skew exceeds lifecycle limit", b
    if entry:
        if b["ask"] > MAX_ENTRY:
            return False, "entry ask exceeds lifecycle limit", b
        if b["ask_size"] + 1e-12 < STAKE / b["ask"]:
            return False, "visible ask depth is insufficient", b
    elif b["bid_size"] + 1e-12 < shares:
        return False, "visible bid depth is insufficient", b
    return True, "safe", b


class MicropriceSignalLifecycleTracker:
    def __init__(self, engine: Any, store: Any) -> None:
        self.engine, self.store = engine, store
        self.lock = threading.RLock()
        self.active: dict[str, Any] | None = None
        self.last_sequence: str | None = None
        self.last_decision: dict[str, Any] | None = None
        self.counters = {"eventsProcessed": 0, "duplicateEvents": 0, "invalidContextEvents": 0, "exitDepthBlocks": 0}
        self._schema()
        self._recover()

    def _schema(self) -> None:
        with self.store.lock:
            self.store.db.executescript("""
            CREATE TABLE IF NOT EXISTS microprice_signal_episodes(
              id INTEGER PRIMARY KEY AUTOINCREMENT, market_id INTEGER NOT NULL, topic_id INTEGER NOT NULL,
              side TEXT NOT NULL CHECK(side IN('UP','DOWN')), status TEXT NOT NULL,
              signal_started_at TEXT NOT NULL, signal_started_wall_ns INTEGER NOT NULL,
              signal_confirmed_at TEXT, signal_confirmed_wall_ns INTEGER,
              order_created_at TEXT, order_created_wall_ns INTEGER, order_limit_price REAL,
              filled_at TEXT, filled_wall_ns INTEGER, trade_id INTEGER,
              ended_at TEXT, ended_wall_ns INTEGER, end_reason TEXT,
              start_score REAL NOT NULL, peak_abs_score REAL NOT NULL, last_score REAL, end_score REAL,
              confirmations INTEGER NOT NULL DEFAULT 1, event_count INTEGER NOT NULL DEFAULT 1,
              signal_duration_ms REAL, pending_duration_ms REAL, position_duration_ms REAL,
              entry_price REAL, exit_price REAL, pnl REAL,
              exit_requested_reason TEXT, exit_requested_at TEXT, exit_requested_wall_ns INTEGER,
              fee_rate_bps INTEGER NOT NULL DEFAULT 200, last_event_sequence TEXT, diagnostics_json TEXT
            );
            CREATE INDEX IF NOT EXISTS microprice_signal_market_idx ON microprice_signal_episodes(market_id,id DESC);
            CREATE INDEX IF NOT EXISTS microprice_signal_status_idx ON microprice_signal_episodes(status,id DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS microprice_signal_active_idx ON microprice_signal_episodes(market_id)
              WHERE status IN('SIGNAL','PENDING','OPEN');
            """)
            self.store.db.commit()

    def _recover(self) -> None:
        with self.store.lock:
            row = self.store.db.execute(
                "SELECT * FROM microprice_signal_episodes WHERE status IN('SIGNAL','PENDING','OPEN') ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is not None:
            self.active = dict(row)
            self.last_sequence = str(row["last_event_sequence"] or "") or None
            self.last_decision = {"status": "RECOVERED", "episodeId": row["id"], "marketId": row["market_id"]}

    def _start(self, snap: dict[str, Any], ctx: dict[str, Any], side: str, score: float, fee: int, now: int, seq: str) -> None:
        diag = {"paper_only": True, "live_orders_affected": False, "prediction_data_source": ctx.get("prediction_data_source")}
        with self.store.lock:
            cur = self.store.db.execute("""INSERT INTO microprice_signal_episodes(
                market_id,topic_id,side,status,signal_started_at,signal_started_wall_ns,start_score,
                peak_abs_score,last_score,fee_rate_bps,last_event_sequence,diagnostics_json)
                VALUES(?,?,?,'SIGNAL',?,?,?,?,?,?,?,?)""", (
                int(snap["market_id"]), int(snap["topic_id"]), side, _iso(now), now, score, abs(score), score,
                int(fee), seq, json.dumps(diag, sort_keys=True)))
            self.store.db.commit()
        self.active = {"id": int(cur.lastrowid), "market_id": int(snap["market_id"]), "topic_id": int(snap["topic_id"]),
                       "side": side, "status": "SIGNAL", "signal_started_wall_ns": now, "start_score": score,
                       "peak_abs_score": abs(score), "last_score": score, "confirmations": 1, "event_count": 1,
                       "fee_rate_bps": int(fee), "last_event_sequence": seq}
        self.last_decision = {"status": "SIGNAL_STARTED", "marketId": snap["market_id"], "episodeId": cur.lastrowid, "side": side, "score": score}

    def _event(self, score: float | None, seq: str) -> None:
        assert self.active is not None
        peak = max(float(self.active.get("peak_abs_score") or 0), abs(score or 0))
        confirmations = int(self.active.get("confirmations") or 0) + 1
        count = int(self.active.get("event_count") or 0) + 1
        with self.store.lock:
            self.store.db.execute("UPDATE microprice_signal_episodes SET peak_abs_score=?,last_score=?,confirmations=?,event_count=?,last_event_sequence=? WHERE id=?",
                                  (peak, score, confirmations, count, seq, int(self.active["id"])))
            self.store.db.commit()
        self.active.update(peak_abs_score=peak, last_score=score, confirmations=confirmations, event_count=count, last_event_sequence=seq)

    def _cancel(self, reason: str, score: float | None, now: int, detail: str | None = None) -> None:
        assert self.active is not None
        start, order = int(self.active["signal_started_wall_ns"]), self.active.get("order_created_wall_ns")
        with self.store.lock:
            self.store.db.execute("""UPDATE microprice_signal_episodes SET status='CANCELLED',ended_at=?,ended_wall_ns=?,
                end_reason=?,end_score=?,signal_duration_ms=?,pending_duration_ms=? WHERE id=?""",
                (_iso(now), now, reason, score, (now-start)/1e6, ((now-int(order))/1e6 if order else None), int(self.active["id"])))
            self.store.db.commit()
        self.last_decision = {"status": "CANCELLED", "reason": reason, "detail": detail, "episodeId": self.active["id"],
                              "marketId": self.active["market_id"], "side": self.active["side"], "score": score,
                              "signalDurationMs": (now-start)/1e6}
        self.active = None

    def _pending(self, snap: dict[str, Any], now: int) -> None:
        assert self.active is not None
        ok, reason, b = _safe(snap, str(self.active["side"]), entry=True)
        if not ok or b is None:
            self._cancel("DATA_INVALIDATED", _f(self.active.get("last_score")), now, reason)
            return
        with self.store.lock:
            self.store.db.execute("""UPDATE microprice_signal_episodes SET status='PENDING',signal_confirmed_at=?,
                signal_confirmed_wall_ns=?,order_created_at=?,order_created_wall_ns=?,order_limit_price=? WHERE id=?""",
                (_iso(now), now, _iso(now), now, b["ask"], int(self.active["id"])))
            self.store.db.commit()
        self.active.update(status="PENDING", signal_confirmed_wall_ns=now, order_created_wall_ns=now, order_limit_price=b["ask"])
        self.last_decision = {"status": "PENDING_ORDER", "episodeId": self.active["id"], "side": self.active["side"],
                              "score": self.active["last_score"], "limitPrice": b["ask"]}

    def _fill(self, snap: dict[str, Any], fee: int, now: int) -> list[dict[str, Any]]:
        assert self.active is not None
        side = str(self.active["side"])
        ok, reason, b = _safe(snap, side, entry=True)
        if not ok or b is None or b["ask"] > float(self.active["order_limit_price"]) + 1e-12:
            self.last_decision = {"status": "PENDING_WAIT", "reason": reason if not ok else "ask moved above frozen limit",
                                  "episodeId": self.active["id"], "currentAsk": b["ask"] if b else None,
                                  "limitPrice": self.active["order_limit_price"]}
            return []
        self.store.open_trade(strategy=STRATEGY, topic_id=int(snap["topic_id"]), market_id=int(snap["market_id"]),
            side=side, entry=b["ask"], target=None, stake=STAKE, fee_rate_bps=int(fee),
            note="paper-only Microprice lifecycle: cancel before fill or exit when edge disappears/reverses",
            strategy_version=VERSION, diagnostics={"paper_only": True, "live_orders_affected": False,
                "signal_lifecycle_episode_id": int(self.active["id"]), "order_limit_price": self.active["order_limit_price"]})
        with self.store.lock:
            trade = self.store.db.execute("SELECT * FROM trades WHERE strategy=? AND market_id=? ORDER BY id DESC LIMIT 1",
                                          (STRATEGY, int(snap["market_id"]))).fetchone()
            if trade is None:
                return []
            self.store.db.execute("""UPDATE microprice_signal_episodes SET status='OPEN',filled_at=?,filled_wall_ns=?,
                trade_id=?,entry_price=?,pending_duration_ms=? WHERE id=?""",
                (_iso(now), now, int(trade["id"]), b["ask"], (now-int(self.active["order_created_wall_ns"]))/1e6, int(self.active["id"])))
            self.store.db.commit()
        self.active.update(status="OPEN", filled_wall_ns=now, trade_id=int(trade["id"]), entry_price=b["ask"])
        self.last_decision = {"status": "FILLED", "episodeId": self.active["id"], "tradeId": trade["id"], "side": side,
                              "entryPrice": b["ask"], "signalAgeMs": (now-int(self.active["signal_started_wall_ns"]))/1e6}
        return [{"strategy": STRATEGY, "topic_id": int(snap["topic_id"]), "market_id": int(snap["market_id"]),
                 "side": side, "entry_price": b["ask"], "raw_top_ask": b["ask"], "stake": STAKE,
                 "seconds_left": float(snap["seconds_left"]), "fee_bps": int(fee), "paper_only": True,
                 "live_orders_affected": False, "market_data_integrity_ok": True,
                 "research_signal": self.active.get("last_score"), "signal_lifecycle_episode_id": int(self.active["id"])}]

    def _exit(self, snap: dict[str, Any], reason: str, score: float | None, now: int) -> bool:
        assert self.active is not None
        with self.store.lock:
            trade = self.store.db.execute("SELECT * FROM trades WHERE id=?", (int(self.active["trade_id"]),)).fetchone()
        if trade is None or str(trade["status"]) != "OPEN":
            self._held(now, "TRADE_ALREADY_CLOSED", score)
            return True
        ok, detail, b = _safe(snap, str(self.active["side"]), entry=False, shares=float(trade["shares"]))
        if not ok or b is None:
            self.counters["exitDepthBlocks"] += 1
            if not self.active.get("exit_requested_reason"):
                with self.store.lock:
                    self.store.db.execute("UPDATE microprice_signal_episodes SET exit_requested_reason=?,exit_requested_at=?,exit_requested_wall_ns=? WHERE id=?",
                                          (reason, _iso(now), now, int(self.active["id"])))
                    self.store.db.commit()
                self.active.update(exit_requested_reason=reason, exit_requested_at=_iso(now), exit_requested_wall_ns=now)
            self.last_decision = {"status": "EXIT_PENDING", "reason": reason, "detail": detail,
                                  "episodeId": self.active["id"], "tradeId": trade["id"], "score": score}
            return False
        shares, bid, fee_bps = float(trade["shares"]), b["bid"], int(trade["fee_rate_bps"])
        fees = float(trade["fees"] or 0) + taker_fee(shares, bid, fee_bps)
        pnl = shares * bid - float(trade["stake"]) - fees
        trade_status = "STOP_LOSS_EXIT"
        start, filled = int(self.active["signal_started_wall_ns"]), int(self.active["filled_wall_ns"])
        with self.store.lock:
            self.store.db.execute("""UPDATE trades SET status=?,exit_price=?,fees=?,pnl=?,closed_at=?,note=note||? WHERE id=? AND status='OPEN'""",
                (trade_status, bid, fees, pnl, _iso(now), f"; lifecycle exit {reason}", int(trade["id"])))
            self.store.db.execute("""UPDATE microprice_signal_episodes SET status='EXITED',ended_at=?,ended_wall_ns=?,end_reason=?,
                end_score=?,signal_duration_ms=?,position_duration_ms=?,exit_price=?,pnl=? WHERE id=?""",
                (_iso(now), now, reason, score, (now-start)/1e6, (now-filled)/1e6, bid, pnl, int(self.active["id"])))
            self.store.db.commit()
        self.last_decision = {"status": "EXITED", "reason": reason, "episodeId": self.active["id"], "tradeId": trade["id"],
                              "exitPrice": bid, "pnl": pnl, "signalDurationMs": (now-start)/1e6,
                              "positionDurationMs": (now-filled)/1e6}
        self.active = None
        return True

    def _held(self, now: int, reason: str, score: float | None) -> None:
        assert self.active is not None
        start, filled = int(self.active["signal_started_wall_ns"]), self.active.get("filled_wall_ns")
        with self.store.lock:
            self.store.db.execute("""UPDATE microprice_signal_episodes SET status='HELD_TO_SETTLEMENT',ended_at=?,ended_wall_ns=?,
                end_reason=?,end_score=?,signal_duration_ms=?,position_duration_ms=? WHERE id=?""",
                (_iso(now), now, reason, score, (now-start)/1e6, ((now-int(filled))/1e6 if filled else None), int(self.active["id"])))
            self.store.db.commit()
        self.last_decision = {"status": "HELD_TO_SETTLEMENT", "reason": reason, "episodeId": self.active["id"]}
        self.active = None

    def process(self, snap: dict[str, Any], fee: int, ctx: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            if not bool(self.store.config().get("strategy_r_microprice_enabled", True)):
                return []
        except Exception:
            pass
        if not _context_ok(self.engine, ctx):
            self.counters["invalidContextEvents"] += 1
            return []
        try:
            market, seconds = int(snap["market_id"]), float(snap["seconds_left"])
            now = int(ctx.get("received_wall_ns") or ctx.get("signal_received_wall_ns") or snap.get("timestamp_ns") or 0)
        except (KeyError, TypeError, ValueError):
            return []
        seq = str(ctx.get("signal_event_sequence") or snap.get("signal_event_sequence") or "")
        if now <= 0 or not seq:
            return []
        if seq == self.last_sequence:
            self.counters["duplicateEvents"] += 1
            return []
        self.last_sequence = seq
        self.counters["eventsProcessed"] += 1
        if self.active is not None and int(self.active["market_id"]) != market:
            self._held(now, "MARKET_ROLLOVER_HELD", _f(self.active.get("last_score"))) if self.active["status"] == "OPEN" else self._cancel("MARKET_ROLLOVER_CANCELLED", _f(self.active.get("last_score")), now)
        score = microprice_score(snap)
        if self.active is None:
            if score is None or abs(score) < ENTER:
                return []
            if not WINDOW_MIN <= seconds <= WINDOW_MAX:
                self.last_decision = {"status": "WAITING", "reason": "strong score outside frozen entry window", "marketId": market, "score": score, "secondsLeft": seconds}
                return []
            side = "UP" if score > 0 else "DOWN"
            ok, reason, _ = _safe(snap, side, entry=True)
            if not ok:
                self.last_decision = {"status": "BLOCK", "reason": reason, "marketId": market, "side": side, "score": score}
                return []
            self._start(snap, ctx, side, score, fee, now, seq)
            return []
        self._event(score, seq)
        assert self.active is not None
        if self.active["status"] == "OPEN" and self.active.get("exit_requested_reason"):
            self._exit(snap, str(self.active["exit_requested_reason"]), score, now)
            return []
        signed = (score or 0.0) * _sign(str(self.active["side"]))
        if score is not None and signed <= -REVERSE:
            if self.active["status"] == "OPEN":
                self._exit(snap, "SIGNAL_REVERSED", score, now)
            else:
                self._cancel("SIGNAL_REVERSED", score, now)
            return []
        if score is None or signed < RELEASE:
            if self.active["status"] == "OPEN":
                self._exit(snap, "EDGE_LOST", score, now)
            else:
                self._cancel("EDGE_LOST", score, now)
            return []
        age = (now-int(self.active["signal_started_wall_ns"]))/1e6
        if self.active["status"] == "SIGNAL":
            if int(self.active["confirmations"]) >= MIN_CONFIRMATIONS and age >= CONFIRM_MS:
                self._pending(snap, now)
            else:
                self.last_decision = {"status": "ACCUMULATING", "episodeId": self.active["id"], "side": self.active["side"],
                                      "score": score, "confirmations": self.active["confirmations"], "signalAgeMs": age}
            return []
        if self.active["status"] == "PENDING":
            pending = (now-int(self.active["order_created_wall_ns"]))/1e6
            if pending > PENDING_MS:
                self._cancel("ORDER_NOT_FILLED", score, now, "frozen ask limit was not executable before timeout")
                return []
            if pending >= FILL_DELAY_MS:
                return self._fill(snap, fee, now)
            self.last_decision = {"status": "PENDING_ORDER", "episodeId": self.active["id"], "side": self.active["side"],
                                  "score": score, "limitPrice": self.active["order_limit_price"], "pendingAgeMs": pending}
            return []
        self.last_decision = {"status": "OPEN_MONITORING", "episodeId": self.active["id"], "tradeId": self.active.get("trade_id"),
                              "side": self.active["side"], "score": score, "signalAgeMs": age,
                              "positionAgeMs": (now-int(self.active["filled_wall_ns"]))/1e6}
        return []

    def state(self) -> dict[str, Any]:
        active = dict(self.active) if self.active else None
        if active:
            now = int(datetime.now(timezone.utc).timestamp()*1e9)
            active["signalAgeMs"] = max(0.0, (now-int(active["signal_started_wall_ns"]))/1e6)
            if active.get("order_created_wall_ns"):
                active["pendingAgeMs"] = max(0.0, (now-int(active["order_created_wall_ns"]))/1e6)
            if active.get("filled_wall_ns"):
                active["positionAgeMs"] = max(0.0, (now-int(active["filled_wall_ns"]))/1e6)
        return {"version": VERSION, "strategy": STRATEGY, "paperOnly": True, "liveOrdersAffected": False,
                "active": active, "lastDecision": self.last_decision, "counters": dict(self.counters),
                "rules": {"entryThreshold": ENTER, "releaseThreshold": RELEASE, "reverseThreshold": REVERSE,
                          "minimumConfirmations": MIN_CONFIRMATIONS, "minimumConfirmationMs": CONFIRM_MS,
                          "minimumFillDelayMs": FILL_DELAY_MS, "maximumPendingMs": PENDING_MS,
                          "maximumBookAgeMs": MAX_AGE_MS, "maximumBookSkewMs": MAX_SKEW_MS,
                          "maximumSpread": MAX_SPREAD, "maximumEntry": MAX_ENTRY, "stakeUsdt": STAKE,
                          "entryWindowSecondsLeft": [WINDOW_MIN, WINDOW_MAX]}}

    def database_state(self, source_store: Any | None = None) -> dict[str, Any]:
        store = source_store or self.store
        try:
            rows = [dict(r) for r in store.db.execute("""SELECT e.*,t.status AS trade_status,t.pnl AS trade_pnl
                FROM microprice_signal_episodes e LEFT JOIN trades t ON t.id=e.trade_id ORDER BY e.id""").fetchall()]
        except Exception:
            rows = []
        closed = [r for r in rows if r.get("signal_duration_ms") is not None]
        durations = [float(r["signal_duration_ms"]) for r in closed]
        reasons: dict[str, list[float]] = {}
        for r in closed:
            reasons.setdefault(str(r.get("end_reason") or "UNKNOWN"), []).append(float(r["signal_duration_ms"]))
        reason_state = {k: {"count": len(v), "averageDurationMs": statistics.fmean(v), "medianDurationMs": statistics.median(v)} for k,v in reasons.items()}
        def pnl(r: dict[str, Any]) -> float | None:
            value = r.get("pnl") if r.get("pnl") is not None else r.get("trade_pnl")
            return float(value) if value is not None else None
        recent = [{"id": r["id"], "marketId": r["market_id"], "side": r["side"], "status": r["status"],
                   "startedAt": r["signal_started_at"], "confirmedAt": r["signal_confirmed_at"], "filledAt": r["filled_at"],
                   "endedAt": r["ended_at"], "endReason": r["end_reason"], "startScore": r["start_score"],
                   "peakAbsScore": r["peak_abs_score"], "endScore": r["end_score"], "confirmations": r["confirmations"],
                   "eventCount": r["event_count"], "signalDurationMs": r["signal_duration_ms"],
                   "pendingDurationMs": r["pending_duration_ms"], "positionDurationMs": r["position_duration_ms"],
                   "entryPrice": r["entry_price"], "exitPrice": r["exit_price"], "pnl": pnl(r),
                   "tradeStatus": r["trade_status"], "exitRequestedReason": r["exit_requested_reason"]}
                  for r in rows[-20:][::-1]]
        realized = sum(v for v in (pnl(r) for r in rows) if v is not None)
        return {"version": VERSION, "strategy": STRATEGY, "paperOnly": True, "liveOrdersAffected": False,
                "episodes": len(rows), "activeEpisodes": sum(r["status"] in {"SIGNAL","PENDING","OPEN"} for r in rows),
                "confirmedEpisodes": sum(r["signal_confirmed_at"] is not None for r in rows),
                "filledEpisodes": sum(r["filled_at"] is not None for r in rows),
                "cancelledEpisodes": sum(r["status"] == "CANCELLED" for r in rows),
                "exitedEpisodes": sum(r["status"] == "EXITED" for r in rows),
                "heldEpisodes": sum(r["status"] == "HELD_TO_SETTLEMENT" for r in rows), "realizedPnl": realized,
                "duration": {"averageMs": statistics.fmean(durations) if durations else None,
                             "medianMs": statistics.median(durations) if durations else None,
                             "p90Ms": sorted(durations)[max(0, math.ceil(len(durations)*0.9)-1)] if durations else None,
                             "minimumMs": min(durations) if durations else None, "maximumMs": max(durations) if durations else None},
                "reasons": reason_state, "recentEpisodes": recent, "runtime": self.state()}


def _inject(payload: dict[str, Any], tracker: MicropriceSignalLifecycleTracker, store: Any) -> dict[str, Any]:
    state = tracker.database_state(store)
    research = payload.get("researchForward")
    if isinstance(research, dict):
        research["micropriceSignalLifecycle"] = state
        strategies = research.get("strategies")
        if isinstance(strategies, dict):
            strategies[STRATEGY] = {"enabled": True, "stakeUsdt": STAKE, "selectedBacktestParameters": state["runtime"]["rules"],
                "chronologicalValidation": {"status": "ANALYZABLE" if state["episodes"] >= 30 else "COLLECTING",
                    "samples": state["episodes"], "settled": state["exitedEpisodes"]+state["heldEpisodes"],
                    "realizedPnl": state["realizedPnl"], "minimum": 30}, "paperOnly": True, "liveOrdersAffected": False}
    summaries = payload.get("summaries")
    if isinstance(summaries, dict):
        summaries[STRATEGY] = {"trades": state["filledEpisodes"], "open": state["activeEpisodes"],
                               "wins": 0, "losses": 0, "realized_pnl": state["realizedPnl"]}
    return payload


def _wrap_dashboard(store: Any, tracker: MicropriceSignalLifecycleTracker) -> None:
    global _ACTIVE
    _ACTIVE = tracker
    cls, original = type(store), getattr(type(store), "dashboard", None)
    if not callable(original) or getattr(original, "_microprice_signal_lifecycle", False):
        return
    @wraps(original)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = original(self, *args, **kwargs)
        return _inject(payload, _ACTIVE, self) if isinstance(payload, dict) and isinstance(_ACTIVE, MicropriceSignalLifecycleTracker) else payload
    wrapped._microprice_signal_lifecycle = True  # type: ignore[attr-defined]
    cls.dashboard = wrapped


def _wrap_store(engine: Any, store: Any) -> None:
    tracker = getattr(store, "_microprice_signal_lifecycle_tracker", None)
    if not isinstance(tracker, MicropriceSignalLifecycleTracker):
        tracker = MicropriceSignalLifecycleTracker(engine, store)
        original = store.maybe_enter_m_series
        @wraps(original)
        def wrapped(snapshot: dict[str, Any], fee_bps: int, *, realtime_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
            opened = original(snapshot, fee_bps, realtime_context=realtime_context)
            return [*(opened or []), *tracker.process(snapshot, fee_bps, dict(realtime_context or {}))]
        store.maybe_enter_m_series = wrapped
        store._microprice_signal_lifecycle_tracker = tracker
        original_state = getattr(store, "state", None)
        if callable(original_state):
            @wraps(original_state)
            def state_wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
                payload = original_state(*args, **kwargs)
                return _inject(payload, tracker, store) if isinstance(payload, dict) else payload
            store.state = state_wrapped
    else:
        tracker.engine = engine
    engine.microprice_signal_lifecycle_tracker = tracker
    _wrap_dashboard(store, tracker)


def install_microprice_signal_lifecycle() -> None:
    cls = _realtime.MSeriesRealtimeEngine
    init = cls.__init__
    if not getattr(init, "_microprice_signal_lifecycle", False):
        @wraps(init)
        def init_wrapped(self: Any, *args: Any, **kwargs: Any) -> None:
            init(self, *args, **kwargs)
            _wrap_store(self, self.store)
        init_wrapped._microprice_signal_lifecycle = True  # type: ignore[attr-defined]
        cls.__init__ = init_wrapped
    state = cls.state
    if not getattr(state, "_microprice_signal_lifecycle", False):
        @wraps(state)
        def state_wrapped(self: Any) -> dict[str, Any]:
            payload = state(self)
            tracker = getattr(self, "microprice_signal_lifecycle_tracker", None)
            payload["micropriceSignalLifecycle"] = tracker.state() if isinstance(tracker, MicropriceSignalLifecycleTracker) else {
                "version": VERSION, "paperOnly": True, "liveOrdersAffected": False, "status": "UNAVAILABLE"}
            return payload
        state_wrapped._microprice_signal_lifecycle = True  # type: ignore[attr-defined]
        cls.state = state_wrapped
