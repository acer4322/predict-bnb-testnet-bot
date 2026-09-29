from __future__ import annotations

import json
import re
from http.server import ThreadingHTTPServer
from typing import Any

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v6 as v6
from . import echtgeld_engine_v21 as v21


VERSION = "ECHTGELD_ENGINE_V2_4310_ENTRY_SOURCE_GATE_V1"
HOST = v21.HOST
PORT = v21.PORT
ENTRY_SOURCE_MODE = "EXCLUSIVE_FAIL_CLOSED"
ENTRY_SOURCE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{1,95}$")


class EchtgeldEngine(v21.EchtgeldEngine):
    """V21 plus a durable, fail-closed admission gate for NEW live entries.

    Echtgeld ARM is deliberately not sufficient to admit a new position.  The
    operator must also select exactly one entry producer while PAUSED.  Every new
    entry carries an explicit ``entrySource`` and is checked twice: once before it
    is accepted into the live queue and again immediately before venue execution.

    Risk-reducing flows (Poly exits, ambiguous reconciliation, settlement sync and
    stop-loss handling) are intentionally outside this gate.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Restart clears both keys: global ARMED is already false, and producer
        # selection is cleared so Resume cannot silently revive an old source.
        with self.db_lock:
            self.db.execute(
                "UPDATE engine_entry_source_control SET selected_source_id=NULL,updated_at_ms=? WHERE id=1",
                (v1._now_ms(),),
            )
            self.db.commit()
        self._source_audit(
            "SOURCE_RESET_ON_STARTUP",
            detail="Startup reset: no live entry source selected; operator selection required",
        )

    def _setup_schema(self) -> None:
        super()._setup_schema()
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS engine_entry_source_control (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    selected_source_id TEXT,
                    updated_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS engine_entry_source_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at_ms INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    source_id TEXT,
                    intent_id TEXT,
                    strategy TEXT,
                    market_id INTEGER,
                    detail TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_engine_entry_source_audit_time
                    ON engine_entry_source_audit(occurred_at_ms DESC);
                """
            )
            self.db.execute(
                """INSERT OR IGNORE INTO engine_entry_source_control(
                       id,selected_source_id,updated_at_ms
                   ) VALUES(1,NULL,?)""",
                (v1._now_ms(),),
            )
            self.db.commit()

    @staticmethod
    def _normalize_source_id(value: Any) -> str:
        source = str(value or "").strip().upper()
        if not source:
            return ""
        if not ENTRY_SOURCE_RE.fullmatch(source):
            raise v1.EchtgeldEngineError(
                "entrySource/sourceId must be 2-96 chars using A-Z, 0-9, _, ., :, or -"
            )
        return source

    def _selected_entry_source(self) -> str | None:
        with self.db_lock:
            row = self.db.execute(
                "SELECT selected_source_id FROM engine_entry_source_control WHERE id=1"
            ).fetchone()
        source = str(row["selected_source_id"] or "").strip().upper() if row else ""
        return source or None

    def _source_audit(
        self,
        action: str,
        *,
        source_id: str | None = None,
        payload: dict[str, Any] | None = None,
        detail: str = "",
    ) -> None:
        raw = payload if isinstance(payload, dict) else {}
        with self.db_lock:
            self.db.execute(
                """INSERT INTO engine_entry_source_audit(
                       occurred_at_ms,action,source_id,intent_id,strategy,market_id,detail
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    v1._now_ms(),
                    str(action)[:80],
                    source_id,
                    str(raw.get("intentId") or "")[:240] or None,
                    str(raw.get("strategy") or "")[:160] or None,
                    int(v1._finite(raw.get("marketId")) or 0) or None,
                    str(detail)[:500],
                ),
            )
            self.db.commit()

    def entry_source_state(self) -> dict[str, Any]:
        selected = self._selected_entry_source()
        with self.db_lock:
            rows = [
                dict(row)
                for row in self.db.execute(
                    "SELECT * FROM engine_entry_source_audit ORDER BY id DESC LIMIT 30"
                ).fetchall()
            ]
        return {
            "mode": ENTRY_SOURCE_MODE,
            "selectedSourceId": selected,
            "entryAdmissionReady": bool(selected),
            "failClosedWhenUnselected": True,
            "explicitEntrySourceRequired": True,
            "mutationRequiresPaused": True,
            "enforcement": "INTENT_ACCEPT+PRE_VENUE",
            "riskReducingExitsUnaffected": True,
            "recentAudit": rows,
        }

    def select_entry_source(self, source_id: Any) -> dict[str, Any]:
        source = self._normalize_source_id(source_id)
        with self.runtime_lock:
            if self.armed:
                raise v1.EchtgeldEngineError(
                    "Pause Echtgeld before changing the live entry source"
                )
            with self.db_lock:
                self.db.execute(
                    """UPDATE engine_entry_source_control
                          SET selected_source_id=?,updated_at_ms=? WHERE id=1""",
                    (source or None, v1._now_ms()),
                )
                self.db.commit()
            self._source_audit(
                "SOURCE_SELECTED" if source else "SOURCE_CLEARED",
                source_id=source or None,
                detail=(
                    f"Exclusive live entry source set to {source}"
                    if source
                    else "No live entry source selected; new entries fail closed"
                ),
            )
            self._record_event(
                "WARN",
                "ENTRY_SOURCE_SELECTED" if source else "ENTRY_SOURCE_CLEARED",
                "PAUSED",
                (
                    f"Exclusive Echtgeld entry source selected: {source}"
                    if source
                    else "Echtgeld entry source cleared; all new entries blocked"
                ),
                context={"entrySource": source or None, "mode": ENTRY_SOURCE_MODE},
            )
        return self.entry_source_state()

    def _check_entry_source(self, payload: dict[str, Any]) -> tuple[bool, str, str | None]:
        try:
            supplied = self._normalize_source_id(payload.get("entrySource"))
        except v1.EchtgeldEngineError as exc:
            return False, str(exc), self._selected_entry_source()
        selected = self._selected_entry_source()
        if not selected:
            return False, "No Echtgeld live entry source is selected", None
        if not supplied:
            return False, "entrySource is required for every new Echtgeld entry", selected
        if supplied != selected:
            return (
                False,
                f"Entry source {supplied} is disabled; Echtgeld is locked to {selected}",
                selected,
            )
        return True, "", selected

    def _reject_source_before_parent(
        self, payload: dict[str, Any], *, status: str = "IGNORED_SOURCE_DISABLED"
    ) -> dict[str, Any] | None:
        allowed, message, selected = self._check_entry_source(payload)
        if allowed:
            return None
        supplied = str(payload.get("entrySource") or "").strip().upper() or None
        self._source_audit(
            "ENTRY_REJECTED",
            source_id=supplied,
            payload=payload,
            detail=message,
        )
        self._record_event(
            "WARN",
            "ENTRY_SOURCE_REJECTED",
            status,
            message,
            payload=payload,
            context={
                "entrySource": supplied,
                "selectedEntrySource": selected,
                "mode": ENTRY_SOURCE_MODE,
            },
        )
        return {
            "ok": True,
            "accepted": False,
            "queued": False,
            "intentId": str(payload.get("intentId") or ""),
            "status": status,
            "message": message,
            "entrySource": supplied,
            "selectedEntrySource": selected,
        }

    def submit_intent(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise v1.EchtgeldEngineError("intent must be a JSON object")
        rejected = self._reject_source_before_parent(payload)
        if rejected is not None:
            return rejected
        return super().submit_intent(payload)

    def submit_poly_fast_intent(self, raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise v1.EchtgeldEngineError("Poly Fast intent must be a JSON object")
        rejected = self._reject_source_before_parent(raw)
        if rejected is not None:
            return rejected
        return super().submit_poly_fast_intent(raw)

    def _process_intent(self, intent_id: str) -> None:
        # Final source fence.  A queued entry cannot slip through if the selected
        # source is cleared/changed before the worker reaches the venue.
        with self.db_lock:
            row = self.db.execute(
                "SELECT status,payload_json FROM engine_intents WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
        if row is not None and str(row["status"]) == "QUEUED":
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except Exception:
                payload = {}
            allowed, message, selected = self._check_entry_source(payload)
            if not allowed:
                supplied = str(payload.get("entrySource") or "").strip().upper() or None
                self._source_audit(
                    "PRE_VENUE_ABORT",
                    source_id=supplied,
                    payload=payload,
                    detail=message,
                )
                self._finish_intent_without_order(
                    payload,
                    "ABORTED_SOURCE_DISABLED",
                    message,
                )
                return
        super()._process_intent(intent_id)

    def resume(self) -> dict[str, Any]:
        selected = self._selected_entry_source()
        if not selected:
            raise v1.EchtgeldEngineError(
                "Cannot resume Echtgeld with no live entry source selected; select one source while PAUSED"
            )
        return super().resume()

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION
        payload["entrySourceGate"] = self.entry_source_state()
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        gate = self.entry_source_state()
        payload["version"] = VERSION
        payload["entrySourceGate"] = {
            key: gate[key]
            for key in (
                "mode",
                "selectedSourceId",
                "entryAdmissionReady",
                "failClosedWhenUnselected",
                "explicitEntrySourceRequired",
                "mutationRequiresPaused",
                "enforcement",
                "riskReducingExitsUnaffected",
            )
        }
        payload["entrySourceGateFailClosed"] = True
        payload["entrySourceGatePreVenueRecheck"] = True
        return payload


class _Handler(v6._Handler):
    engine: EchtgeldEngine

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/control/entry-source":
            return super().do_POST()
        try:
            payload = self._body()
            # Empty/null sourceId intentionally clears the source and blocks all
            # new entries.  A non-empty source selects it exclusively.
            gate = self.engine.select_entry_source(payload.get("sourceId"))
            self._send(200, {"ok": True, "entrySourceGate": gate, "state": self.engine.state()})
        except v1.EchtgeldEngineError as exc:
            self._send(400, {"ok": False, "error": str(exc)[:500]})
        except Exception as exc:
            self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:500]}"})


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV22Handler", (_Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; startup=PAUSED; "
        "new entries require explicit exclusive entrySource; source changes require PAUSED; "
        "pre-venue source recheck enabled; exits/reconciliation unaffected",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
