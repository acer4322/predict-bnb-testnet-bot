from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from typing import Any

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v26 as v26

VERSION = "ECHTGELD_ENGINE_V2_4310_CAP100_LIVE_REPLAY_RECORDER_V1"
HOST = v26.HOST
PORT = v26.PORT
ROOT = Path(__file__).resolve().parents[2]
REPLAY_DB = ROOT / "data" / "cap100_echtgeld_replay_v1.db"
CONTROLLER_DB = ROOT / "data" / "strategy_cap100_echtgeld_v1.db"
R2_R21_CONTROLLER_DB = ROOT / "data" / "strategy_r2_r21_echtgeld_v1.db"


class EchtgeldEngine(v26.EchtgeldEngine):
    """V26 plus an append-only CAP100 live execution replay recorder.

    This recorder contains only real Echtgeld runtime observations. Failure-drill and
    randomized stress-exam data never enter this DB. The 8787 decision recorder stays
    separate and can be joined through decision_id/client_order_id/market_id.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.replay_lock = threading.RLock()
        self.replay_db: sqlite3.Connection | None = None
        self.replay_last_heartbeat_record_ms: int | None = None
        super().__init__(*args, **kwargs)
        REPLAY_DB.parent.mkdir(parents=True, exist_ok=True)
        self.replay_db = sqlite3.connect(REPLAY_DB, check_same_thread=False, timeout=5.0)
        self.replay_db.row_factory = sqlite3.Row
        self.replay_db.execute("PRAGMA journal_mode=WAL")
        self.replay_db.execute("PRAGMA synchronous=NORMAL")
        self.replay_db.execute("PRAGMA busy_timeout=5000")
        self._setup_replay_schema()
        self._replay_event("ENGINE_RECORDER_STARTED", detail="CAP100 live replay recorder online")

    def _setup_replay_schema(self) -> None:
        assert self.replay_db is not None
        with self.replay_lock:
            self.replay_db.executescript(
                """
                CREATE TABLE IF NOT EXISTS replay_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS live_replay_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_ms INTEGER NOT NULL,
                    engine_version TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    market_id INTEGER,
                    bucket_start_sec INTEGER,
                    window_end_ms INTEGER,
                    event_type TEXT NOT NULL,
                    decision_id TEXT,
                    client_order_id TEXT,
                    role TEXT,
                    side TEXT,
                    order_state TEXT,
                    exchange_status TEXT,
                    requested_price REAL,
                    requested_shares REAL,
                    filled_shares REAL,
                    filled_usdt REAL,
                    avg_fill_price REAL,
                    armed INTEGER NOT NULL,
                    runtime_status TEXT,
                    selected_source TEXT,
                    waiting_next_market INTEGER NOT NULL,
                    entry_write_frozen INTEGER NOT NULL,
                    active_order_count INTEGER NOT NULL,
                    detail TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_live_replay_market_time ON live_replay_events(market_id,event_ms);
                CREATE INDEX IF NOT EXISTS idx_live_replay_client ON live_replay_events(client_order_id,event_ms);
                CREATE INDEX IF NOT EXISTS idx_live_replay_decision ON live_replay_events(decision_id,event_ms);
                CREATE TABLE IF NOT EXISTS replay_market_checkpoints (
                    checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    checkpoint_ms INTEGER NOT NULL,
                    market_id INTEGER,
                    bucket_start_sec INTEGER,
                    source_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    engine_state_json TEXT NOT NULL DEFAULT '{}',
                    capital_state_json TEXT NOT NULL DEFAULT '{}'
                );
                """
            )
            now = v1._now_ms()
            meta = {
                "version": VERSION,
                "datasetKind": "CAP100_REAL_ECHTGELD_REPLAY",
                "syntheticDataIncluded": False,
                "targetDataIncluded": False,
                "controllerDecisionDb": str(CONTROLLER_DB),
                "controllerDecisionDbs": [str(CONTROLLER_DB), str(R2_R21_CONTROLLER_DB)],
                "joinKeys": ["decision_id", "client_order_id", "market_id"],
            }
            self.replay_db.execute(
                "INSERT OR REPLACE INTO replay_meta(key,value,updated_at_ms) VALUES('dataset',?,?)",
                (json.dumps(meta, ensure_ascii=False, separators=(",", ":")), now),
            )
            self.replay_db.commit()

    def _replay_event(self, event_type: str, *, payload: dict[str, Any] | None = None, row: dict[str, Any] | None = None, detail: str = "") -> None:
        if self.replay_db is None:
            return
        p = payload or {}
        r = row or {}
        gate = self.next_market_arm_state()
        market_id = int(v1._finite(p.get("marketId")) or v1._finite(r.get("source_market_id")) or 0) or None
        bucket = int(v1._finite(p.get("bucketStartSec")) or v1._finite(r.get("bucket_start_sec")) or 0) or None
        window_end = int(v1._finite(p.get("windowEndMs")) or v1._finite(r.get("window_end_ms")) or 0) or None
        decision_id = str(p.get("decisionId") or "").strip() or None
        client_order_id = str(p.get("clientOrderId") or r.get("client_order_id") or "").strip() or None
        role = str(r.get("role") or p.get("role") or "").strip().upper() or None
        side = str(r.get("side") or p.get("side") or "").strip().upper() or None
        source_id = str(
            p.get("entrySource")
            or r.get("source_id")
            or self._selected_entry_source()
            or v26.CAP100_SOURCE
        ).strip().upper()
        active_count = len(self._cap100_rows(active_only=True))
        unknown = self._cap100_unknown_write()
        values = (
            v1._now_ms(), VERSION, source_id, market_id, bucket, window_end,
            str(event_type)[:96], decision_id, client_order_id, role, side,
            str(r.get("state") or "") or None, str(r.get("exchange_status") or "") or None,
            v1._finite(r.get("requested_price") if r else p.get("price")),
            v1._finite(r.get("requested_shares") if r else p.get("shares")),
            v1._finite(r.get("filled_share_qty")), v1._finite(r.get("filled_usdt_amount")), v1._finite(r.get("avg_fill_price")),
            int(bool(self.armed)), str(gate.get("runtimeStatus") or ""), self._selected_entry_source(),
            int(bool(gate.get("waitingNextMarket"))), int(unknown is not None), int(active_count),
            str(detail)[:800], json.dumps(p, ensure_ascii=False, separators=(",", ":"), default=str),
        )
        with self.replay_lock:
            self.replay_db.execute(
                """INSERT INTO live_replay_events(
                       event_ms,engine_version,source_id,market_id,bucket_start_sec,window_end_ms,event_type,
                       decision_id,client_order_id,role,side,order_state,exchange_status,requested_price,
                       requested_shares,filled_shares,filled_usdt,avg_fill_price,armed,runtime_status,
                       selected_source,waiting_next_market,entry_write_frozen,active_order_count,detail,payload_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values,
            )
            self.replay_db.commit()

    def _cap100_event(self, event_type: str, *, row: dict[str, Any] | None = None, delta_shares: float | None = None,
                      delta_usdt: float | None = None, fill_price: float | None = None, detail: str = "",
                      payload: dict[str, Any] | None = None) -> None:
        super()._cap100_event(event_type, row=row, delta_shares=delta_shares, delta_usdt=delta_usdt, fill_price=fill_price, detail=detail, payload=payload)
        replay_payload = dict(payload or {})
        if delta_shares is not None: replay_payload["deltaShares"] = delta_shares
        if delta_usdt is not None: replay_payload["deltaUsdt"] = delta_usdt
        if fill_price is not None: replay_payload["fillPrice"] = fill_price
        self._replay_event(f"ENGINE_{event_type}", payload=replay_payload, row=row, detail=detail)

    def cap100_heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = super().cap100_heartbeat(payload)
        now = v1._now_ms()
        market_changed = int(v1._finite(payload.get("marketId")) or 0) != int(self.cap100_heartbeat_market_id or 0)
        if self.replay_last_heartbeat_record_ms is None or now - self.replay_last_heartbeat_record_ms >= 5_000 or market_changed:
            self.replay_last_heartbeat_record_ms = now
            self._replay_event(
                "CONTROLLER_HEARTBEAT",
                payload=payload,
                detail=f"throttled {str(payload.get('entrySource') or 'controller')} runtime heartbeat",
            )
        return result

    def resume(self) -> dict[str, Any]:
        result = super().resume()
        self._replay_event("OPERATOR_RESUME", payload={"gate": self.next_market_arm_state()}, detail="operator requested Echtgeld resume")
        return result

    def pause(self, reason: str = "operator") -> dict[str, Any]:
        result = super().pause(reason)
        self._replay_event("OPERATOR_PAUSE", payload={"reason": reason}, detail=reason)
        return result

    def submit_cap100_maker(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._replay_event("MAKER_INTENT_RECEIVED", payload=payload)
        try:
            result = super().submit_cap100_maker(payload)
            row = result.get("order") if isinstance(result.get("order"), dict) else None
            self._replay_event("MAKER_INTENT_RESULT", payload=payload, row=row, detail=str(result.get("error") or ""))
            return result
        except Exception as exc:
            self._replay_event("MAKER_INTENT_BLOCKED", payload=payload, detail=f"{type(exc).__name__}: {str(exc)[:600]}")
            raise

    def submit_cap100_taker(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._replay_event("TAKER_INTENT_RECEIVED", payload=payload)
        try:
            result = super().submit_cap100_taker(payload)
            row = result.get("order") if isinstance(result.get("order"), dict) else None
            self._replay_event("TAKER_INTENT_RESULT", payload=payload, row=row, detail=str(result.get("error") or ""))
            return result
        except Exception as exc:
            self._replay_event("TAKER_INTENT_BLOCKED", payload=payload, detail=f"{type(exc).__name__}: {str(exc)[:600]}")
            raise

    def cancel_cap100_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._replay_event("CANCEL_INTENT_RECEIVED", payload=payload)
        try:
            result = super().cancel_cap100_order(payload)
            row = result.get("order") if isinstance(result.get("order"), dict) else None
            self._replay_event("CANCEL_INTENT_RESULT", payload=payload, row=row)
            return result
        except Exception as exc:
            self._replay_event("CANCEL_INTENT_ERROR", payload=payload, detail=f"{type(exc).__name__}: {str(exc)[:600]}")
            raise

    def replay_state(self) -> dict[str, Any]:
        if self.replay_db is None:
            return {"enabled": False}
        with self.replay_lock:
            total = int(self.replay_db.execute("SELECT COUNT(*) FROM live_replay_events").fetchone()[0])
            markets = int(self.replay_db.execute("SELECT COUNT(DISTINCT market_id) FROM live_replay_events WHERE market_id IS NOT NULL").fetchone()[0])
            last = self.replay_db.execute("SELECT * FROM live_replay_events ORDER BY seq DESC LIMIT 1").fetchone()
        return {
            "enabled": True,
            "datasetKind": "CAP100_REAL_ECHTGELD_REPLAY",
            "syntheticDataIncluded": False,
            "targetDataIncluded": False,
            "events": total,
            "markets": markets,
            "db": str(REPLAY_DB),
            "controllerDecisionDb": str(CONTROLLER_DB),
            "joinKeys": ["decision_id", "client_order_id", "market_id"],
            "lastEvent": dict(last) if last else None,
        }

    def replay_events(self, after_seq: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        if self.replay_db is None:
            return []
        with self.replay_lock:
            rows = self.replay_db.execute(
                "SELECT * FROM live_replay_events WHERE seq>? ORDER BY seq ASC LIMIT ?",
                (max(0, int(after_seq)), max(1, min(2000, int(limit)))),
            ).fetchall()
        out = []
        for raw in rows:
            row = dict(raw)
            try: row["payload"] = json.loads(str(row.pop("payload_json") or "{}"))
            except Exception: row["payload"] = {}
            out.append(row)
        return out

    def state(self) -> dict[str, Any]:
        payload = super().state(); payload["version"] = VERSION; payload["cap100LiveReplay"] = self.replay_state(); return payload

    def health(self) -> dict[str, Any]:
        payload = super().health(); payload["version"] = VERSION
        payload["cap100LiveReplayRecorder"] = True
        payload["cap100LiveReplaySyntheticExcluded"] = True
        payload["cap100LiveReplayJoinableTo8787Decisions"] = True
        return payload

    def close(self) -> None:
        try:
            self._replay_event("ENGINE_RECORDER_STOPPED", detail="engine close")
        except Exception:
            pass
        if self.replay_db is not None:
            with self.replay_lock:
                self.replay_db.commit()
                self.replay_db.execute("PRAGMA wal_checkpoint(PASSIVE)")
                self.replay_db.close()
                self.replay_db = None
        super().close()


class _Handler(v26._Handler):
    engine: EchtgeldEngine

    def do_GET(self) -> None:  # noqa: N802
        from urllib.parse import parse_qs
        path, _, query = self.path.partition("?")
        if path == "/cap100/replay":
            self._send(200, {"ok": True, "replay": self.engine.replay_state()}); return
        if path == "/cap100/replay/events":
            args = parse_qs(query)
            after = int((args.get("afterSeq") or ["0"])[0] or 0)
            limit = int((args.get("limit") or ["500"])[0] or 500)
            self._send(200, {"ok": True, "events": self.engine.replay_events(after, limit)}); return
        return super().do_GET()


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV27Handler", (_Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(f"{VERSION} listening on http://{HOST}:{PORT}; CAP100 real execution replay recording enabled", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown(); server.server_close(); engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
