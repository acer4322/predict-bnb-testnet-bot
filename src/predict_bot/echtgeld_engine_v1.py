from __future__ import annotations

import json
import os
import queue
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from . import predict_wallet_target_taker_public_side_strategy_v1 as public_side
from .target_taker_live_execution_v4 import TargetTakerLiveConfig, TargetTakerLiveExecutor


VERSION = "ECHTGELD_ENGINE_V1_TARGET_TAKER_ADAPTER_V1"
HOST = str(os.environ.get("PREDICT_ECHTGELD_ENGINE_HOST") or "127.0.0.1").strip()
PORT = int(os.environ.get("PREDICT_ECHTGELD_ENGINE_PORT") or "8780")
ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("PREDICT_ECHTGELD_ENGINE_DB") or ROOT / "data" / "echtgeld_engine_v1.db")
MAX_INTENT_AGE_MS = 5_000
BALANCE_CACHE_MS = 15_000
EVENT_RETENTION_MS = 7 * 24 * 60 * 60 * 1000
ALLOWED_STRATEGIES = {public_side.VERSION}
ALLOWED_COHORTS = {public_side.SIDE_ONLY_COHORT, public_side.HAZARD_SIDE_COHORT}


class EchtgeldEngineError(RuntimeError):
    pass


def _now_ms() -> int:
    return int(time.time() * 1000)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _safe_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def _sanitize(value: Any) -> Any:
    """Strip credential-like fields before durable audit/UI exposure."""
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower().replace("_", "")
            if any(marker in lowered for marker in ("privatekey", "apikey", "apisecret", "signature", "jwt")):
                continue
            output[str(key)] = _sanitize(item)
        return output
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _market_context(payload: dict[str, Any]) -> dict[str, Any]:
    decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    return {
        "strategy": payload.get("strategy"),
        "cohort": payload.get("cohort"),
        "intentId": payload.get("intentId"),
        "signalId": payload.get("signalId"),
        "marketId": payload.get("marketId"),
        "side": decision.get("side") or payload.get("side"),
        "signalAsk": decision.get("ask") or payload.get("signalAsk"),
        "secondsLeft": snapshot.get("seconds_left") or snapshot.get("secondsLeft"),
        "sampledAtMs": snapshot.get("sampled_at_ms") or snapshot.get("sampledAtMs"),
        "bucketStartSec": snapshot.get("bucket_start_sec") or snapshot.get("bucketStartSec"),
        "windowEndMs": snapshot.get("window_end_ms") or snapshot.get("windowEndMs"),
    }


class EchtgeldEngine:
    def __init__(
        self,
        db_path: Path | str = DB_PATH,
        *,
        executor_factory: Callable[[TargetTakerLiveConfig], TargetTakerLiveExecutor] = TargetTakerLiveExecutor,
        start_worker: bool = True,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db_lock = threading.RLock()
        self.runtime_lock = threading.RLock()
        self.executor_factory = executor_factory
        self.intent_queue: queue.Queue[str | None] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.last_result: dict[str, Any] | None = None
        self.last_intent: dict[str, Any] | None = None
        self.balance_cache: dict[str, Any] | None = None
        self._setup_schema()
        self.config = self._load_config()
        # Engine restarts are always fail-safe. Configuration survives; arming never does.
        self.armed = False
        self.executor = self.executor_factory(self._executor_config(mode="paper"))
        self._abandon_restart_queue()
        self._record_event("INFO", "ENGINE_STARTED", "PAUSED", "Echtgeld Engine started PAUSED; operator Resume is required")
        if start_worker:
            self.worker = threading.Thread(target=self._worker_loop, name="echtgeld-engine-worker", daemon=True)
            self.worker.start()

    def _setup_schema(self) -> None:
        with self.db_lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS engine_runtime (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    config_json TEXT NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS engine_intents (
                    intent_id TEXT PRIMARY KEY,
                    dedupe_key TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    signal_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    signal_ask REAL NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    received_at_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_engine_intents_time
                    ON engine_intents(received_at_ms DESC);
                CREATE TABLE IF NOT EXISTS engine_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    intent_id TEXT NOT NULL UNIQUE,
                    strategy TEXT NOT NULL,
                    cohort TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    venue TEXT NOT NULL,
                    side TEXT NOT NULL,
                    signal_ask REAL NOT NULL,
                    target_notional_usdt REAL NOT NULL,
                    status TEXT NOT NULL,
                    attempted_at_ms INTEGER NOT NULL,
                    completed_at_ms INTEGER,
                    execution_price REAL,
                    shares REAL,
                    submitted_usdt REAL,
                    vendor_order_id TEXT,
                    vendor_order_hash TEXT,
                    error_message TEXT,
                    result_json TEXT NOT NULL,
                    context_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_engine_orders_time
                    ON engine_orders(attempted_at_ms DESC);
                CREATE TABLE IF NOT EXISTS engine_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at_ms INTEGER NOT NULL,
                    level TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    phase TEXT,
                    strategy TEXT,
                    cohort TEXT,
                    intent_id TEXT,
                    signal_id TEXT,
                    market_id INTEGER,
                    venue TEXT,
                    side TEXT,
                    message TEXT NOT NULL,
                    error_class TEXT,
                    context_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_engine_events_time
                    ON engine_events(occurred_at_ms DESC);
                """
            )
            cutoff = _now_ms() - EVENT_RETENTION_MS
            self.db.execute("DELETE FROM engine_events WHERE occurred_at_ms<?", (cutoff,))
            self.db.commit()

    def _load_config(self) -> TargetTakerLiveConfig:
        with self.db_lock:
            row = self.db.execute("SELECT config_json FROM engine_runtime WHERE id=1").fetchone()
        if row is not None:
            try:
                values = json.loads(str(row["config_json"]))
                return self._validated_config(values, mode="paper")
            except Exception:
                pass
        startup = TargetTakerLiveConfig.from_env()
        config = TargetTakerLiveConfig(
            mode="paper",
            venue=startup.venue,
            notional_usdt=startup.notional_usdt,
            cohort=startup.cohort if startup.cohort in ALLOWED_COHORTS else public_side.SIDE_ONLY_COHORT,
            max_price_drift=startup.max_price_drift,
        )
        self._persist_config(config)
        return config

    def _validated_config(self, values: dict[str, Any], *, mode: str) -> TargetTakerLiveConfig:
        current = getattr(self, "config", None)
        venue = str(values.get("venue", current.venue if current else "predictfun")).strip().lower()
        cohort = str(values.get("cohort", current.cohort if current else public_side.SIDE_ONLY_COHORT)).strip()
        notional = _finite(values.get("notionalUsdt", current.notional_usdt if current else 1.0))
        drift = _finite(values.get("maxPriceDrift", current.max_price_drift if current else 0.02))
        if venue not in {"predictfun", "binance"}:
            raise EchtgeldEngineError("venue must be predictfun or binance")
        if cohort not in ALLOWED_COHORTS:
            raise EchtgeldEngineError("cohort must be SIDE_ONLY or HAZARD_SIDE Target Taker V1")
        if notional is None or not 0 < notional <= 100:
            raise EchtgeldEngineError("notionalUsdt must be within (0, 100]")
        if drift is None or not 0 <= drift <= 0.10:
            raise EchtgeldEngineError("maxPriceDrift must be within [0, 0.10]")
        return TargetTakerLiveConfig(
            mode=mode,
            venue=venue,
            notional_usdt=float(notional),
            cohort=cohort,
            max_price_drift=float(drift),
        )

    def _executor_config(self, *, mode: str) -> TargetTakerLiveConfig:
        return TargetTakerLiveConfig(
            mode=mode,
            venue=self.config.venue,
            notional_usdt=self.config.notional_usdt,
            cohort=self.config.cohort,
            max_price_drift=self.config.max_price_drift,
        )

    def _persist_config(self, config: TargetTakerLiveConfig) -> None:
        payload = {
            "venue": config.venue,
            "notionalUsdt": config.notional_usdt,
            "cohort": config.cohort,
            "maxPriceDrift": config.max_price_drift,
        }
        with self.db_lock:
            self.db.execute(
                """INSERT INTO engine_runtime(id,config_json,updated_at_ms) VALUES (1,?,?)
                   ON CONFLICT(id) DO UPDATE SET config_json=excluded.config_json,updated_at_ms=excluded.updated_at_ms""",
                (_safe_json(payload), _now_ms()),
            )
            self.db.commit()

    def _abandon_restart_queue(self) -> None:
        with self.db_lock:
            rows = self.db.execute(
                "SELECT intent_id,market_id,strategy,cohort,signal_id,side FROM engine_intents WHERE status IN ('QUEUED','PROCESSING')"
            ).fetchall()
            self.db.execute(
                "UPDATE engine_intents SET status='ABANDONED_RESTART',error_message='Engine restarted before execution; never auto-replayed' WHERE status IN ('QUEUED','PROCESSING')"
            )
            self.db.commit()
        for row in rows:
            self._record_event(
                "WARN",
                "INTENT_ABANDONED_RESTART",
                "PAUSED",
                "Queued intent was abandoned on engine restart and will not be replayed",
                context=dict(row),
            )

    def close(self) -> None:
        self.stop_event.set()
        try:
            self.intent_queue.put_nowait(None)
        except Exception:
            pass
        if self.worker is not None and self.worker.is_alive():
            self.worker.join(timeout=2.0)
        with self.runtime_lock:
            try:
                self.executor.close()
            except Exception:
                pass
        with self.db_lock:
            self.db.commit()
            self.db.close()

    def _record_event(
        self,
        level: str,
        event_type: str,
        phase: str | None,
        message: str,
        *,
        payload: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        error_class: str | None = None,
    ) -> None:
        payload = payload or {}
        event_context = _sanitize(context if context is not None else _market_context(payload))
        decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
        with self.db_lock:
            self.db.execute(
                """INSERT INTO engine_events(
                       occurred_at_ms,level,event_type,phase,strategy,cohort,intent_id,signal_id,
                       market_id,venue,side,message,error_class,context_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _now_ms(),
                    str(level).upper(),
                    event_type,
                    phase,
                    payload.get("strategy") or event_context.get("strategy"),
                    payload.get("cohort") or event_context.get("cohort"),
                    payload.get("intentId") or event_context.get("intentId"),
                    payload.get("signalId") or event_context.get("signalId"),
                    int(payload.get("marketId") or event_context.get("marketId") or 0) or None,
                    self.config.venue if hasattr(self, "config") else None,
                    decision.get("side") or payload.get("side") or event_context.get("side"),
                    str(message)[:1000],
                    error_class,
                    _safe_json(event_context),
                ),
            )
            self.db.commit()

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        with self.runtime_lock:
            if self.armed:
                raise EchtgeldEngineError("Pause Echtgeld before changing venue, cohort, notional or drift")
            replacement = self._validated_config(values, mode="paper")
            old = self.executor
            self.config = replacement
            self.executor = self.executor_factory(self._executor_config(mode="paper"))
            self._persist_config(self.config)
            self.balance_cache = None
            old.close()
            self._record_event("INFO", "SETTINGS_UPDATED", "PAUSED", "Echtgeld settings updated while PAUSED", context=self.config.snapshot())
        return self.state()

    def resume(self) -> dict[str, Any]:
        with self.runtime_lock:
            if self.armed:
                return self.state()
            old = self.executor
            self.executor = self.executor_factory(self._executor_config(mode="live"))
            self.armed = True
            self.balance_cache = None
            old.close()
            self._record_event("WARN", "ENGINE_RESUMED", "ARMED", "Operator resumed Echtgeld; future eligible intents may submit real orders")
        return self.state()

    def pause(self, reason: str = "operator") -> dict[str, Any]:
        with self.runtime_lock:
            if self.armed:
                old = self.executor
                self.armed = False
                self.executor = self.executor_factory(self._executor_config(mode="paper"))
                self.balance_cache = None
                old.close()
                self._record_event("WARN", "ENGINE_PAUSED", "PAUSED", f"Echtgeld paused: {str(reason)[:200]}")
        return self.state()

    def submit_intent(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise EchtgeldEngineError("intent must be a JSON object")
        intent_id = str(payload.get("intentId") or "").strip()
        strategy = str(payload.get("strategy") or "").strip()
        cohort = str(payload.get("cohort") or "").strip()
        signal_id = str(payload.get("signalId") or intent_id).strip()
        market_id = int(_finite(payload.get("marketId")) or 0)
        created_at_ms = int(_finite(payload.get("createdAtMs")) or 0)
        decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
        snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
        side = str(decision.get("side") or payload.get("side") or "").strip().upper()
        signal_ask = _finite(decision.get("ask") or payload.get("signalAsk"))
        dedupe_key = str(payload.get("dedupeKey") or f"{strategy}:{cohort}:{market_id}").strip()
        if not intent_id or len(intent_id) > 240:
            raise EchtgeldEngineError("intentId is required and must be <=240 characters")
        if not dedupe_key or len(dedupe_key) > 320:
            raise EchtgeldEngineError("dedupeKey is invalid")
        if strategy not in ALLOWED_STRATEGIES:
            raise EchtgeldEngineError(f"strategy is not supported by Echtgeld Engine V1: {strategy}")
        if cohort not in ALLOWED_COHORTS:
            raise EchtgeldEngineError("Target Taker cohort is not live-eligible")
        if market_id <= 0 or created_at_ms <= 0:
            raise EchtgeldEngineError("marketId and createdAtMs are required")
        if side not in {"UP", "DOWN"} or signal_ask is None or not 0 < signal_ask <= 1:
            raise EchtgeldEngineError("intent side/signal ask is invalid")
        if str(decision.get("decision") or "") != "TRADE":
            raise EchtgeldEngineError("only TRADE decisions may enter Echtgeld Engine")
        snapshot_market_id = int(_finite(snapshot.get("market_id") or snapshot.get("marketId")) or 0)
        if snapshot_market_id != market_id:
            raise EchtgeldEngineError("intent snapshot market does not match marketId")

        received_at_ms = _now_ms()
        safe_payload = _sanitize(payload)
        with self.db_lock:
            try:
                self.db.execute(
                    """INSERT INTO engine_intents(
                           intent_id,dedupe_key,strategy,cohort,market_id,signal_id,side,signal_ask,
                           created_at_ms,received_at_ms,status,payload_json
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        intent_id, dedupe_key, strategy, cohort, market_id, signal_id, side,
                        float(signal_ask), created_at_ms, received_at_ms, "RECEIVED", _safe_json(safe_payload),
                    ),
                )
                self.db.commit()
            except sqlite3.IntegrityError:
                row = self.db.execute("SELECT status,error_message FROM engine_intents WHERE intent_id=?", (intent_id,)).fetchone()
                return {
                    "ok": True,
                    "accepted": False,
                    "duplicateIntent": True,
                    "intentId": intent_id,
                    "status": str(row["status"] if row else "DUPLICATE_INTENT"),
                    "error": row["error_message"] if row else None,
                }
        self.last_intent = _market_context(payload)
        self._record_event("INFO", "INTENT_RECEIVED", "RECEIVED", "Strategy intent received and persisted", payload=payload)

        age_ms = max(0, received_at_ms - created_at_ms)
        with self.runtime_lock:
            armed = self.armed
            selected_cohort = self.config.cohort
        if age_ms > MAX_INTENT_AGE_MS:
            return self._finish_intent_without_order(payload, "REJECTED_STALE_INTENT", f"Intent age {age_ms}ms exceeds {MAX_INTENT_AGE_MS}ms")
        if not armed:
            return self._finish_intent_without_order(payload, "IGNORED_PAUSED", "Echtgeld Engine is PAUSED; signal is recorded but never replayed")
        if cohort != selected_cohort:
            return self._finish_intent_without_order(payload, "IGNORED_COHORT", f"Engine is armed for {selected_cohort}, not {cohort}")

        with self.db_lock:
            self.db.execute("UPDATE engine_intents SET status='QUEUED' WHERE intent_id=?", (intent_id,))
            self.db.commit()
        self.intent_queue.put(intent_id)
        self._record_event("INFO", "INTENT_QUEUED", "QUEUED", "Intent queued for Echtgeld worker", payload=payload)
        return {"ok": True, "accepted": True, "queued": True, "intentId": intent_id, "status": "QUEUED"}

    def _finish_intent_without_order(self, payload: dict[str, Any], status: str, message: str) -> dict[str, Any]:
        intent_id = str(payload.get("intentId") or "")
        with self.db_lock:
            self.db.execute(
                "UPDATE engine_intents SET status=?,error_message=? WHERE intent_id=?",
                (status, message[:500], intent_id),
            )
            self.db.commit()
        self._record_event("WARN", "INTENT_NOT_EXECUTED", status, message, payload=payload)
        return {"ok": True, "accepted": False, "intentId": intent_id, "status": status, "message": message}

    def _worker_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                intent_id = self.intent_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if intent_id is None:
                return
            try:
                self._process_intent(intent_id)
            except Exception as exc:
                self._record_event(
                    "ERROR", "WORKER_ERROR", "ERROR", f"Worker failure: {type(exc).__name__}: {str(exc)[:500]}",
                    context={"intentId": intent_id}, error_class=type(exc).__name__,
                )
            finally:
                self.intent_queue.task_done()

    def _process_intent(self, intent_id: str) -> None:
        with self.db_lock:
            row = self.db.execute("SELECT * FROM engine_intents WHERE intent_id=?", (intent_id,)).fetchone()
        if row is None or str(row["status"]) != "QUEUED":
            return
        payload = json.loads(str(row["payload_json"]))
        created_at_ms = int(row["created_at_ms"])
        if _now_ms() - created_at_ms > MAX_INTENT_AGE_MS:
            self._finish_intent_without_order(payload, "REJECTED_STALE_WORKER", "Intent became stale before worker execution")
            return

        with self.runtime_lock:
            if not self.armed:
                self._finish_intent_without_order(payload, "ABORTED_PAUSED", "Engine was paused before this queued intent reached the worker")
                return
            if str(row["cohort"]) != self.config.cohort:
                self._finish_intent_without_order(payload, "ABORTED_COHORT", "Engine cohort changed before worker execution")
                return

            attempted_at_ms = _now_ms()
            context = _market_context(payload)
            with self.db_lock:
                try:
                    self.db.execute(
                        """INSERT INTO engine_orders(
                               dedupe_key,intent_id,strategy,cohort,market_id,venue,side,signal_ask,
                               target_notional_usdt,status,attempted_at_ms,result_json,context_json
                           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            row["dedupe_key"], intent_id, row["strategy"], row["cohort"], row["market_id"],
                            self.config.venue, row["side"], row["signal_ask"], self.config.notional_usdt,
                            "ATTEMPTING", attempted_at_ms, _safe_json({"status": "ATTEMPTING"}), _safe_json(context),
                        ),
                    )
                    self.db.execute("UPDATE engine_intents SET status='PROCESSING' WHERE intent_id=?", (intent_id,))
                    self.db.commit()
                except sqlite3.IntegrityError:
                    self.db.execute(
                        "UPDATE engine_intents SET status='DUPLICATE_FENCE',error_message='Live dedupe fence already exists' WHERE intent_id=?",
                        (intent_id,),
                    )
                    self.db.commit()
                    self._record_event("WARN", "DUPLICATE_FENCE", "BLOCKED", "Live dedupe fence already exists; no venue write performed", payload=payload)
                    return

            self._record_event("WARN", "ORDER_ATTEMPTING", "ATTEMPTING", "Durable ATTEMPTING fence committed before venue write", payload=payload)
            result = self.executor.execute(
                cohort=str(row["cohort"]),
                market_id=int(row["market_id"]),
                decision=payload["decision"],
                snapshot=payload["snapshot"],
                signal_id=str(row["signal_id"]),
            )
            safe_result = _sanitize(result)
            result_status = str(result.get("status") or "UNKNOWN").upper()
            error = str(result.get("error") or "")[:500] or None
            completed_at_ms = int(result.get("completedAtMs") or _now_ms())
            with self.db_lock:
                self.db.execute(
                    """UPDATE engine_orders SET
                           status=?,completed_at_ms=?,execution_price=?,shares=?,submitted_usdt=?,
                           vendor_order_id=?,vendor_order_hash=?,error_message=?,result_json=?
                         WHERE intent_id=?""",
                    (
                        result_status, completed_at_ms, result.get("executionPrice"), result.get("shares"),
                        result.get("submittedUsdt"), result.get("vendorOrderId"), result.get("vendorOrderHash"),
                        error, _safe_json(safe_result), intent_id,
                    ),
                )
                self.db.execute(
                    "UPDATE engine_intents SET status=?,error_message=? WHERE intent_id=?",
                    (result_status, error, intent_id),
                )
                self.db.commit()
            self.last_result = safe_result
            if result_status == "SUBMITTED":
                level, event_type = "INFO", "ORDER_SUBMITTED"
            elif result_status == "AMBIGUOUS":
                level, event_type = "ERROR", "ORDER_AMBIGUOUS_NO_RETRY"
            else:
                level, event_type = "WARN", "ORDER_REJECTED"
            message = error or f"Venue result: {result_status}"
            self._record_event(level, event_type, result_status, message, payload=payload, context={**context, **safe_result}, error_class=("AMBIGUOUS" if result_status == "AMBIGUOUS" else None))

    def _balance_snapshot(self) -> dict[str, Any]:
        now_ms = _now_ms()
        cached = self.balance_cache
        if isinstance(cached, dict) and now_ms - int(cached.get("asOfMs") or 0) < BALANCE_CACHE_MS:
            return dict(cached)
        with self.runtime_lock:
            try:
                result = dict(self.executor.available_balance_snapshot())
            except Exception as exc:
                result = {"status": "UNAVAILABLE", "availableUsdt": None, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
        address = str(result.get("predictionWalletAddress") or "")
        if len(address) > 12:
            result["predictionWalletAddress"] = f"{address[:6]}…{address[-4:]}"
        result["asOfMs"] = now_ms
        self.balance_cache = _sanitize(result)
        return dict(self.balance_cache)

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.db_lock:
            rows = self.db.execute(
                "SELECT * FROM engine_events ORDER BY occurred_at_ms DESC,id DESC LIMIT ?",
                (max(1, min(500, int(limit))),),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["context"] = json.loads(str(item.pop("context_json")))
            except Exception:
                item["context"] = {}
            output.append(item)
        return output

    def orders(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.db_lock:
            rows = self.db.execute(
                "SELECT * FROM engine_orders ORDER BY attempted_at_ms DESC,id DESC LIMIT ?",
                (max(1, min(500, int(limit))),),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["result"] = json.loads(str(item.pop("result_json")))
            except Exception:
                item["result"] = {}
            try:
                item["context"] = json.loads(str(item.pop("context_json")))
            except Exception:
                item["context"] = {}
            output.append(item)
        return output

    def state(self) -> dict[str, Any]:
        with self.db_lock:
            intent_counts = {
                str(row["status"]): int(row["count"])
                for row in self.db.execute("SELECT status,COUNT(*) count FROM engine_intents GROUP BY status")
            }
            order_counts = {
                str(row["status"]): int(row["count"])
                for row in self.db.execute("SELECT status,COUNT(*) count FROM engine_orders GROUP BY status")
            }
        recent_events = self.events(80)
        latest_error = next((row for row in recent_events if str(row.get("level")) == "ERROR"), None)
        latest_warning = next((row for row in recent_events if str(row.get("level")) == "WARN"), None)
        return {
            "version": VERSION,
            "ok": True,
            "armed": bool(self.armed),
            "runtimeStatus": "LIVE_ARMED" if self.armed else "PAUSED",
            "restartPolicy": "ALWAYS_PAUSED_NO_QUEUE_REPLAY",
            "strategyProducerIndependent": True,
            "allowedStrategies": sorted(ALLOWED_STRATEGIES),
            "allowedCohorts": sorted(ALLOWED_COHORTS),
            "config": {
                "venue": self.config.venue,
                "notionalUsdt": self.config.notional_usdt,
                "cohort": self.config.cohort,
                "maxPriceDrift": self.config.max_price_drift,
            },
            "balance": self._balance_snapshot(),
            "lastIntent": self.last_intent,
            "lastResult": self.last_result,
            "latestError": latest_error,
            "latestWarning": latest_warning,
            "intentCounts": intent_counts,
            "orderCounts": order_counts,
            "recentEvents": recent_events,
            "recentOrders": self.orders(50),
            "asOfMs": _now_ms(),
        }

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "version": VERSION,
            "armed": bool(self.armed),
            "runtimeStatus": "LIVE_ARMED" if self.armed else "PAUSED",
            "workerAlive": bool(self.worker and self.worker.is_alive()) if self.worker is not None else True,
            "dbPath": str(self.db_path),
            "asOfMs": _now_ms(),
        }


class _Handler(BaseHTTPRequestHandler):
    engine: EchtgeldEngine

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length <= 0 or length > 1_000_000:
            raise EchtgeldEngineError("invalid request size")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise EchtgeldEngineError("request JSON must be an object")
        return payload

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._send(200, self.engine.health())
        elif path == "/state":
            self._send(200, self.engine.state())
        elif path == "/orders":
            self._send(200, {"ok": True, "orders": self.engine.orders(200)})
        elif path == "/events":
            self._send(200, {"ok": True, "events": self.engine.events(300)})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        try:
            payload = self._body()
            if path == "/intent":
                result = self.engine.submit_intent(payload)
                self._send(202 if result.get("queued") else 200, result)
                return
            if path == "/control/pause":
                self._send(200, {"ok": True, "state": self.engine.pause(str(payload.get("reason") or "operator"))})
                return
            if path == "/control/resume":
                self._send(200, {"ok": True, "state": self.engine.resume()})
                return
            if path == "/control/settings":
                self._send(200, {"ok": True, "state": self.engine.update_settings(payload)})
                return
            self._send(404, {"ok": False, "error": "not found"})
        except EchtgeldEngineError as exc:
            self._send(400, {"ok": False, "error": str(exc)[:500]})
        except Exception as exc:
            self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:500]}"})


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV1Handler", (_Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; startup=PAUSED; "
        "strategy-observer-independent=true; queued-intents-never-replayed-after-restart=true",
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
