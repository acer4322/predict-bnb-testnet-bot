from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
import urllib.request
from decimal import Decimal
from functools import wraps
from pathlib import Path
from typing import Any, Iterable

STRATEGIES = ("R_POLY_LEAD_ENTRY", "R_POLY_LEAD_EXIT", "R_POLY_GAP_SCALP")
EXIT_STRATEGIES = {"R_POLY_LEAD_EXIT", "R_POLY_GAP_SCALP"}
VERSION = "POLY_LIVE_BRIDGE_V1"
REPRICE_GAP = Decimal("0.05")
ENTRY_MAX_AGE_MS = max(250, int(os.environ.get("PREDICT_POLY_LIVE_ENTRY_MAX_AGE_MS", "2000")))
FEED_MAX_AGE_MS = max(250, int(os.environ.get("PREDICT_POLY_LIVE_FEED_MAX_AGE_MS", "2000")))
POLL_SECONDS = max(0.05, float(os.environ.get("PREDICT_POLY_LIVE_BRIDGE_POLL_SECONDS", "0.10")))
POLY_DB = Path(os.environ.get("PREDICT_CROSS_ORACLE_DB", Path(__file__).resolve().parents[2] / "data" / "cross_oracle.db"))
STATE_URL = os.environ.get("PREDICT_CROSS_ORACLE_STATE_URL", "http://127.0.0.1:8767/state")


def _append(values: Iterable[str], additions: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*tuple(values), *tuple(additions))))


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _mid(side: Any) -> float | None:
    if not isinstance(side, dict):
        return None
    bid, ask = _num(side.get("bestBid")), _num(side.get("bestAsk"))
    return (bid + ask) / 2 if bid is not None and ask is not None and 0 <= bid <= ask <= 1 else None


def _direction(up_mid: float | None) -> str | None:
    if up_mid is None:
        return None
    return "UP" if up_mid >= 0.55 else "DOWN" if up_mid <= 0.45 else None


def _poly_state() -> dict[str, Any] | None:
    try:
        req = urllib.request.Request(STATE_URL, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=0.35) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _healthy_poly() -> tuple[bool, dict[str, Any]]:
    state = _poly_state() or {}
    poly = state.get("polymarket") if isinstance(state.get("polymarket"), dict) else {}
    continuity = state.get("continuity") if isinstance(state.get("continuity"), dict) else poly.get("continuity") or {}
    market = poly.get("market") if isinstance(poly.get("market"), dict) else {}
    age = _num(poly.get("ageMs"))
    up_mid = _mid(poly.get("up"))
    healthy = bool(str(poly.get("status") or "").upper() == "LIVE" and age is not None and 0 <= age <= FEED_MAX_AGE_MS and continuity.get("gapActive") is not True and continuity.get("healthy") is not False and up_mid is not None)
    return healthy, {
        "slug": market.get("slug"), "ageMs": age, "upMid": up_mid,
        "direction": _direction(up_mid), "gapGeneration": continuity.get("gapGeneration"),
    }


def _open_poly() -> sqlite3.Connection | None:
    try:
        db = sqlite3.connect(f"file:{POLY_DB.resolve().as_posix()}?mode=ro", uri=True, timeout=0.5, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        return db
    except sqlite3.Error:
        return None


def _ensure_links(engine: Any) -> None:
    with engine.ledger.lock:
        engine.ledger.db.executescript("""
        CREATE TABLE IF NOT EXISTS poly_live_bridge_links(
          source_trade_id INTEGER PRIMARY KEY, strategy TEXT NOT NULL,
          market_id INTEGER NOT NULL, side TEXT NOT NULL,
          live_order_local_id INTEGER, status TEXT NOT NULL,
          error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(strategy, market_id));
        """)
        engine.ledger.db.commit()


def _link(engine: Any, row: sqlite3.Row, status: str, error: str | None = None) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with engine.ledger.lock:
        engine.ledger.db.execute(
            "INSERT OR IGNORE INTO poly_live_bridge_links(source_trade_id,strategy,market_id,side,status,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (int(row["id"]), str(row["strategy"]), int(row["binance_market_id"]), str(row["side"]), status, error, now, now),
        )
        engine.ledger.db.commit()


def _set_link(engine: Any, source_id: int, **values: Any) -> None:
    allowed = {"live_order_local_id", "status", "error"}
    values = {k: v for k, v in values.items() if k in allowed}
    if not values:
        return
    values["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with engine.ledger.lock:
        engine.ledger.db.execute(
            f"UPDATE poly_live_bridge_links SET {', '.join(f'{k}=?' for k in values)} WHERE source_trade_id=?",
            (*values.values(), int(source_id)),
        )
        engine.ledger.db.commit()


def _existing_link(engine: Any, strategy: str, market_id: int) -> bool:
    with engine.ledger.lock:
        row = engine.ledger.db.execute("SELECT 1 FROM poly_live_bridge_links WHERE strategy=? AND market_id=?", (strategy, market_id)).fetchone()
    return row is not None


def _active_links(engine: Any) -> list[dict[str, Any]]:
    with engine.ledger.lock:
        rows = engine.ledger.db.execute("SELECT * FROM poly_live_bridge_links WHERE status IN ('ENTRY_FORWARDED','LIVE_LINKED') ORDER BY source_trade_id").fetchall()
    return [dict(row) for row in rows]


def _live_order(engine: Any, strategy: str, market_id: int) -> dict[str, Any] | None:
    with engine.ledger.lock:
        row = engine.ledger.db.execute("SELECT * FROM live_orders WHERE strategy=? AND market_id=? ORDER BY id ASC LIMIT 1", (strategy, market_id)).fetchone()
    return dict(row) if row is not None else None


def _book(engine: Any, market_id: int, side: str) -> tuple[dict[str, Any] | None, str]:
    try:
        book = engine.current_verified_prediction_book() if callable(engine.current_verified_prediction_book) else None
    except Exception as exc:
        return None, f"verified book callback failed: {str(exc)[:120]}"
    if not isinstance(book, dict) or book.get("verified") is not True:
        return None, "verified Prediction book unavailable"
    if int(book.get("market_id") or 0) != market_id:
        return None, "Prediction market mismatch"
    age = _num(book.get("effective_book_age_ms", book.get("book_age_ms")))
    ask = _num(book.get("up_ask" if side == "UP" else "down_ask"))
    if age is None or age > float(engine.max_prediction_book_age_ms) or ask is None or not 0 < ask < 1:
        return None, "Prediction book stale or missing executable ask"
    return book, ""


def _entry_signal(engine: Any, row: sqlite3.Row) -> tuple[dict[str, Any] | None, str]:
    now_ms = int(time.time() * 1000)
    opened = int(row["opened_at_ms"] or 0)
    strategy, side = str(row["strategy"]).upper(), str(row["side"]).upper()
    market_id = int(row["binance_market_id"])
    if str(row["status"]).upper() != "OPEN" or strategy not in STRATEGIES or side not in {"UP", "DOWN"}:
        return None, "source Paper trade is not an open supported entry"
    if opened <= 0 or not 0 <= now_ms - opened <= ENTRY_MAX_AGE_MS:
        return None, "source Paper entry is stale"
    reference = engine.current_market() or {}
    if int(reference.get("market_id") or 0) != market_id:
        return None, "source Paper market is not current"
    healthy, poly = _healthy_poly()
    if not healthy or str(poly.get("slug") or "") != str(row["poly_market_slug"] or ""):
        return None, "Polymarket feed is stale, gapped, or on another market"
    book, reason = _book(engine, market_id, side)
    if book is None:
        return None, reason
    poly_up = _num(poly.get("upMid"))
    bin_bid, bin_ask = _num(book.get("up_bid")), _num(book.get("up_ask"))
    bin_mid = (bin_bid + bin_ask) / 2 if bin_bid is not None and bin_ask is not None else None
    if strategy in {"R_POLY_LEAD_ENTRY", "R_POLY_LEAD_EXIT"}:
        if poly.get("direction") != side or _direction(bin_mid) == side:
            return None, "lead condition no longer holds"
    else:
        selected_poly = poly_up if side == "UP" else (1 - poly_up if poly_up is not None else None)
        selected_ask = _num(book.get("up_ask" if side == "UP" else "down_ask"))
        if selected_poly is None or selected_ask is None or selected_poly - selected_ask < 0.03:
            return None, "Poly/Binance gap no longer >= 0.03"
    entry = _num(row["entry_price"])
    if entry is None or not 0 < entry < 1:
        return None, "source Paper entry price invalid"
    end_ms = int(reference.get("end_ms") or 0)
    server_ms = int(engine.client.server_timestamp_ms()) if engine.client is not None else now_ms
    recv_ns = int(book.get("received_monotonic_ns") or time.monotonic_ns())
    mono_ns = time.monotonic_ns()
    return {
        "strategy": strategy, "market_id": market_id, "side": side,
        "entry_price": entry, "seconds_left": max(0.0, (end_ms-server_ms)/1000),
        "book_age_ms": _num(book.get("effective_book_age_ms", book.get("book_age_ms"))),
        "book_skew_ms": _num(book.get("book_skew_ms")),
        "paper_only": False, "live_orders_affected": True, "live_forwarded_from_paper": True,
        "poly_live_bridge": True, "poly_live_version": VERSION,
        "poly_source_trade_id": int(row["id"]), "poly_source_opened_at_ms": opened,
        "poly_market_slug": row["poly_market_slug"], "poly_condition_verified_at_ms": now_ms,
        "poly_current_up_mid": poly_up, "poly_current_direction": poly.get("direction"),
        "poly_binance_up_mid": bin_mid, "poly_gap_generation": poly.get("gapGeneration"),
        "signal_prediction_book_age_ms": _num(book.get("effective_book_age_ms", book.get("book_age_ms"))),
        "signal_prediction_ask": _num(book.get("up_ask" if side == "UP" else "down_ask")),
        "signal_prediction_ask_size": _num(book.get("up_ask_size" if side == "UP" else "down_ask_size")),
        "market_event_received_monotonic_ns": recv_ns,
        "strategy_decision_started_monotonic_ns": mono_ns,
        "strategy_store_started_monotonic_ns": mono_ns,
        "strategy_store_finished_monotonic_ns": mono_ns,
        "live_candidate_created_monotonic_ns": mono_ns,
    }, ""


def _provenance(signal: dict[str, Any]) -> tuple[bool, str]:
    strategy = str(signal.get("strategy") or "").upper()
    if strategy not in STRATEGIES:
        return True, ""
    if signal.get("poly_live_bridge") is not True or signal.get("poly_live_version") != VERSION:
        return False, "Poly live bridge provenance missing"
    source_id, opened = int(signal.get("poly_source_trade_id") or 0), int(signal.get("poly_source_opened_at_ms") or 0)
    if source_id <= 0 or opened <= 0 or not 0 <= int(time.time()*1000)-opened <= ENTRY_MAX_AGE_MS:
        return False, "Poly live source id/time invalid or stale"
    db = _open_poly()
    if db is None:
        return False, "cross_oracle.db unavailable"
    try:
        row = db.execute("SELECT * FROM cross_oracle_strategy_trades WHERE id=?", (source_id,)).fetchone()
        if row is None or str(row["status"]).upper() != "OPEN":
            return False, "Poly source trade no longer OPEN"
        if str(row["strategy"]).upper() != strategy or int(row["binance_market_id"]) != int(signal.get("market_id") or 0) or str(row["side"]).upper() != str(signal.get("side") or "").upper():
            return False, "Poly source provenance mismatch"
        return True, ""
    finally:
        db.close()


def _auto_exit(engine: Any, local_id: int, source_id: int, reason: str) -> bool:
    order = engine.ledger.order_for_manual_exit(local_id)
    if order is None or str(order.get("strategy") or "").upper() not in EXIT_STRATEGIES or str(order.get("status") or "").upper() != "FILLED":
        return False
    with engine.lock:
        if not engine.runtime_enabled:
            return False
    engine.ledger.record_event("WARN", "POLY_AUTO_EXIT_TRIGGERED", f"source #{source_id}: {reason}; current-Bid LIMIT exit", int(order["market_id"]))
    engine.manual_sell(local_id, "LIMIT")
    return True


def _loop(engine: Any) -> None:
    try:
        _ensure_links(engine)
    except Exception as exc:
        engine.ledger.record_event("ERROR", "POLY_LIVE_BRIDGE_SCHEMA_ERROR", str(exc)[:300])
        return
    db, cursor = None, 0
    while not engine.stop_event.is_set():
        try:
            if db is None:
                db = _open_poly()
                if db is None:
                    engine.stop_event.wait(0.5)
                    continue
                row = db.execute("SELECT COALESCE(MAX(id),0) max_id FROM cross_oracle_strategy_trades").fetchone()
                cursor = int(row["max_id"] if row else 0)
                engine.ledger.record_event("INFO", "POLY_LIVE_BRIDGE_READY", f"{VERSION}; forward only after source #{cursor}")
            for link in _active_links(engine):
                sid, strategy, market_id = int(link["source_trade_id"]), str(link["strategy"]), int(link["market_id"])
                local_id = int(link.get("live_order_local_id") or 0)
                if local_id <= 0:
                    order = _live_order(engine, strategy, market_id)
                    if order is not None:
                        local_id = int(order["id"])
                        _set_link(engine, sid, live_order_local_id=local_id, status="ENTRY_COMPLETE" if strategy == "R_POLY_LEAD_ENTRY" else "LIVE_LINKED", error=None)
                if strategy == "R_POLY_LEAD_ENTRY":
                    continue
                source = db.execute("SELECT * FROM cross_oracle_strategy_trades WHERE id=?", (sid,)).fetchone()
                if source is None:
                    continue
                status = str(source["status"]).upper()
                if status in {"SETTLED_WIN", "SETTLED_LOSS"}:
                    _set_link(engine, sid, status="NO_EXIT_FINAL", error=None)
                    continue
                if status != "EXITED" or local_id <= 0:
                    continue
                try:
                    if _auto_exit(engine, local_id, sid, str(source["exit_reason"] or "POLY_DIRECTION_FLIP")):
                        _set_link(engine, sid, status="EXIT_FORWARDED", error=None)
                except Exception as exc:
                    _set_link(engine, sid, status="EXIT_FAILED_NO_RETRY", error=str(exc)[:500])
            rows = db.execute("SELECT * FROM cross_oracle_strategy_trades WHERE id>? AND strategy IN (?,?,?) ORDER BY id", (cursor, *STRATEGIES)).fetchall()
            for row in rows:
                cursor = max(cursor, int(row["id"]))
                strategy, market_id = str(row["strategy"]).upper(), int(row["binance_market_id"])
                if _existing_link(engine, strategy, market_id):
                    continue
                with engine.lock:
                    selected = strategy in set(str(x) for x in engine.live_rules["strategies"])
                if not selected:
                    continue
                signal, reason = _entry_signal(engine, row)
                if signal is None:
                    _link(engine, row, "ENTRY_NOT_FORWARDED", reason)
                    continue
                _link(engine, row, "ENTRY_FORWARDED")
                engine.submit_signal(signal)
        except sqlite3.Error as exc:
            if db is not None:
                try:
                    db.close()
                except sqlite3.Error:
                    pass
            db = None
            engine.ledger.record_event("WARN", "POLY_LIVE_BRIDGE_DB_RETRY", str(exc)[:250])
        except Exception as exc:
            engine.ledger.record_event("ERROR", "POLY_LIVE_BRIDGE_ERROR", str(exc)[:300])
        engine.stop_event.wait(POLL_SECONDS)
    if db is not None:
        db.close()


def install_poly_live_bridge_patch() -> None:
    from . import live_trading as live
    live.LIVE_RESEARCH_STRATEGIES = _append(live.LIVE_RESEARCH_STRATEGIES, STRATEGIES)
    live.LIVE_SUPPORTED_STRATEGIES = _append(live.LIVE_SUPPORTED_STRATEGIES, STRATEGIES)
    live.LIVE_RESEARCH_REPRICE_GAPS = {**live.LIVE_RESEARCH_REPRICE_GAPS, **{s: REPRICE_GAP for s in STRATEGIES}}
    cls = live.LiveM0WEngine
    original_process = cls._process_single_signal
    if not getattr(original_process, "_poly_live_bridge_v1", False):
        @wraps(original_process)
        def guarded(self: Any, signal: dict[str, Any], *, defer_placement: bool=False, allow_paused_quote_only: bool=False) -> Any:
            safe, reason = _provenance(signal)
            if not safe:
                self._record_blocked_signal(signal, "BLOCKED_POLY_PROVENANCE", reason)
                return None
            return original_process(self, signal, defer_placement=defer_placement, allow_paused_quote_only=allow_paused_quote_only)
        guarded._poly_live_bridge_v1 = True  # type: ignore[attr-defined]
        cls._process_single_signal = guarded
    original_start = cls.start
    if not getattr(original_start, "_poly_live_bridge_v1", False):
        @wraps(original_start)
        def start(self: Any) -> None:
            original_start(self)
            thread = getattr(self, "_poly_live_bridge_thread", None)
            if thread is None or not thread.is_alive():
                self._poly_live_bridge_thread = threading.Thread(target=_loop, args=(self,), name="poly-live-bridge", daemon=True)
                self._poly_live_bridge_thread.start()
        start._poly_live_bridge_v1 = True  # type: ignore[attr-defined]
        cls.start = start
