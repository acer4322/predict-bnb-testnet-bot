from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections import deque
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .xpair_btc_eth_canary import (
    DEFAULT_DB_PATH,
    LIVE_CONFIRM_ENV,
    LIVE_CONFIRM_VALUE,
    CanaryStore,
    utc_iso,
)

API_HOST = os.environ.get("XPAIR_CANARY_API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("XPAIR_CANARY_API_PORT", "8767"))
DB_PATH = Path(os.environ.get("XPAIR_CANARY_DB", DEFAULT_DB_PATH))
MAX_LOG_LINES = 200


@dataclass(frozen=True)
class LaunchRequest:
    mode: str
    selection: str
    pair_budget_usdt: float
    balance_buffer_usdt: float
    max_total_cost: float
    max_leg_reprice: float
    entry_seconds_left: float
    entry_window_seconds: float
    slippage_bps: int
    account_type: str
    reconcile_seconds: float

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "LaunchRequest":
        request = cls(
            mode=str(payload.get("mode") or "dry-run").strip().lower(),
            selection=str(
                payload.get("selection") or "BTC_DOWN_ETH_UP"
            ).strip().upper(),
            pair_budget_usdt=float(payload.get("pairBudgetUsdt", 2.0)),
            balance_buffer_usdt=float(payload.get("balanceBufferUsdt", 0.1)),
            max_total_cost=float(payload.get("maxTotalCost", 0.98)),
            max_leg_reprice=float(payload.get("maxLegReprice", 0.01)),
            entry_seconds_left=float(payload.get("entrySecondsLeft", 180.0)),
            entry_window_seconds=float(payload.get("entryWindowSeconds", 10.0)),
            slippage_bps=int(payload.get("slippageBps", 100)),
            account_type=str(payload.get("accountType") or "SPOT").upper(),
            reconcile_seconds=float(payload.get("reconcileSeconds", 15.0)),
        )
        request.validate()
        return request

    def validate(self) -> None:
        if self.mode not in {"dry-run", "quote-only", "live"}:
            raise ValueError("mode must be dry-run, quote-only, or live")
        if self.selection not in {
            "BTC_DOWN_ETH_UP",
            "BTC_UP_ETH_DOWN",
            "CHEAPEST_ELIGIBLE",
        }:
            raise ValueError("unsupported XPAIR selection")
        if not 0.02 <= self.pair_budget_usdt <= 3.0:
            raise ValueError("pair budget must be between 0.02 and 3.00 USDT")
        if self.balance_buffer_usdt < 0:
            raise ValueError("balance buffer cannot be negative")
        if not 0 < self.max_total_cost < 2:
            raise ValueError("max total cost must be between 0 and 2")
        if not 0 <= self.max_leg_reprice <= 0.05:
            raise ValueError("max leg reprice must be between 0 and 0.05")
        if not 0 < self.entry_window_seconds < self.entry_seconds_left:
            raise ValueError("entry window must be positive and below entry time")
        if not 0 <= self.slippage_bps <= 500:
            raise ValueError("slippage bps must be between 0 and 500")
        if self.account_type not in {"SPOT", "FUNDING"}:
            raise ValueError("account type must be SPOT or FUNDING")
        if not 1 <= self.reconcile_seconds <= 120:
            raise ValueError("reconcile seconds must be between 1 and 120")

    @property
    def required_balance_usdt(self) -> float:
        return self.pair_budget_usdt + self.balance_buffer_usdt

    def command(self) -> list[str]:
        command = [
            sys.executable,
            "-m",
            "predict_bot.xpair_btc_eth_canary",
            "--db",
            str(DB_PATH),
            "--mode",
            self.mode,
            "--selection",
            self.selection,
            "--pair-budget-usdt",
            str(self.pair_budget_usdt),
            "--balance-buffer-usdt",
            str(self.balance_buffer_usdt),
            "--max-total-cost",
            str(self.max_total_cost),
            "--max-leg-reprice",
            str(self.max_leg_reprice),
            "--entry-seconds-left",
            str(self.entry_seconds_left),
            "--entry-window-seconds",
            str(self.entry_window_seconds),
            "--slippage-bps",
            str(self.slippage_bps),
            "--account-type",
            self.account_type,
            "--reconcile-seconds",
            str(self.reconcile_seconds),
        ]
        if self.mode == "live":
            command.append("--execute-live")
        return command


class RuntimeState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.process: subprocess.Popen[str] | None = None
        self.request: LaunchRequest | None = None
        self.phase = "IDLE"
        self.started_at: str | None = None
        self.completed_at: str | None = None
        self.exit_code: int | None = None
        self.last_error: str | None = None
        self.logs: deque[dict[str, str]] = deque(maxlen=MAX_LOG_LINES)

    def log(self, message: str, level: str = "INFO") -> None:
        item = {"timestamp": utc_iso(), "level": level, "message": message[:1000]}
        with self.lock:
            self.logs.append(item)
            self.phase = infer_phase(message, self.phase)

    def running(self) -> bool:
        with self.lock:
            process = self.process
            return process is not None and process.poll() is None

    def launch(self, request: LaunchRequest) -> None:
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError("another XPAIR canary run is already active")
            environment = dict(os.environ)
            if request.mode == "live":
                environment[LIVE_CONFIRM_ENV] = LIVE_CONFIRM_VALUE
            else:
                environment.pop(LIVE_CONFIRM_ENV, None)
            self.request = request
            self.phase = "STARTING"
            self.started_at = utc_iso()
            self.completed_at = None
            self.exit_code = None
            self.last_error = None
            self.logs.clear()
            self.process = subprocess.Popen(
                request.command(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=environment,
            )
            process = self.process
        self.log(
            f"RUN_ACCEPTED mode={request.mode} selection={request.selection} "
            f"budget={request.pair_budget_usdt:.2f}"
        )
        threading.Thread(
            target=self._consume,
            args=(process,),
            name="xpair-canary-output",
            daemon=True,
        ).start()

    def _consume(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        for raw in process.stdout:
            line = raw.rstrip()
            if not line:
                continue
            level = "ERROR" if any(
                token in line
                for token in (
                    "ERROR",
                    "REJECTED",
                    "INCOMPLETE",
                    "ONE_SIDED",
                    "Traceback",
                )
            ) else "WARN" if any(
                token in line for token in ("SUBMITTED", "Manual", "Do not rerun")
            ) else "INFO"
            self.log(line, level)
        exit_code = process.wait()
        with self.lock:
            self.exit_code = exit_code
            self.completed_at = utc_iso()
            if exit_code == 0:
                self.phase = infer_final_phase(self.logs, "COMPLETED")
            else:
                self.phase = infer_final_phase(self.logs, "ERROR")
                self.last_error = next(
                    (
                        item["message"]
                        for item in reversed(self.logs)
                        if item["level"] == "ERROR"
                    ),
                    f"canary process exited with code {exit_code}",
                )

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "running": self.process is not None and self.process.poll() is None,
                "phase": self.phase,
                "startedAt": self.started_at,
                "completedAt": self.completed_at,
                "exitCode": self.exit_code,
                "lastError": self.last_error,
                "request": (
                    {
                        **asdict(self.request),
                        "requiredBalanceUsdt": self.request.required_balance_usdt,
                    }
                    if self.request is not None
                    else None
                ),
                "logs": list(self.logs),
            }


RUNTIME = RuntimeState()


def infer_phase(message: str, fallback: str) -> str:
    for phase in (
        "PLACEMENT_INCOMPLETE_MANUAL_RECONCILE",
        "SUBMITTED_NOT_BOTH_FILLED",
        "ONE_SIDED_FILL_ALERT",
        "QUOTE_ONLY_READY",
        "DRY_RUN_READY",
        "FILLED_BOTH",
        "SUBMITTED_BOTH",
        "QUOTE_PAIR_ACCEPTED",
        "PREFLIGHT_OK",
        "PLAN",
        "WAITING_ENTRY_WINDOW",
        "WAITING_ALIGNED_MARKETS",
    ):
        if phase in message:
            return phase
    return fallback


def infer_final_phase(logs: deque[dict[str, str]], fallback: str) -> str:
    for item in reversed(logs):
        phase = infer_phase(item["message"], "")
        if phase:
            return phase
    return fallback


def recent_runs() -> list[dict[str, Any]]:
    try:
        store = CanaryStore(DB_PATH)
        try:
            return store.recent()
        finally:
            store.close()
    except Exception as exc:
        return [{"id": -1, "status": "LEDGER_ERROR", "message": str(exc)[:500]}]


def state_payload() -> dict[str, Any]:
    return {
        "strategy": "XPAIR_BTC_ETH_LIVE_CANARY",
        "nonAtomic": True,
        "api": {"host": API_HOST, "port": API_PORT, "loopbackDefault": True},
        "defaults": {
            "mode": "dry-run",
            "selection": "BTC_DOWN_ETH_UP",
            "pairBudgetUsdt": 2.0,
            "balanceBufferUsdt": 0.1,
            "requiredBalanceUsdt": 2.1,
            "recommendedAvailableBalanceUsdt": "3–5",
            "maxTotalCost": 0.98,
            "maxLegReprice": 0.01,
            "entrySecondsLeft": 180.0,
            "entryWindowSeconds": 10.0,
            "slippageBps": 100,
            "accountType": "SPOT",
        },
        "policy": {
            "oneActiveRunPerProcess": True,
            "retryPlacement": False,
            "automaticCancel": False,
            "automaticUnwind": False,
            "liveConfirmationPhrase": LIVE_CONFIRM_VALUE,
            "otherLiveExecutorMustBeStopped": True,
        },
        "runtime": RUNTIME.snapshot(),
        "recentRuns": recent_runs(),
        "updatedAt": utc_iso(),
    }


def origin_is_allowed(origin: str | None) -> bool:
    if not origin:
        return True
    try:
        host = urlsplit(origin).hostname
    except ValueError:
        return False
    return host in {"localhost", "127.0.0.1", "::1"}


class Handler(BaseHTTPRequestHandler):
    server_version = "BTC5MLabXPairCanary/1.0"

    def _send_headers(self, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        origin = self.headers.get("Origin")
        if origin and origin_is_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-BTC-Lab-XPair-Live",
        )
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def respond(self, status: int, payload: dict[str, Any]) -> None:
        self._send_headers(status)
        self.wfile.write(
            json.dumps(payload, ensure_ascii=False, allow_nan=False, default=str).encode(
                "utf-8"
            )
        )

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 64_000:
            raise ValueError("request body is missing or too large")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def do_OPTIONS(self) -> None:
        if not origin_is_allowed(self.headers.get("Origin")):
            self.respond(403, {"error": "origin is not allowed"})
            return
        self._send_headers(204)

    def do_GET(self) -> None:
        if not origin_is_allowed(self.headers.get("Origin")):
            self.respond(403, {"error": "origin is not allowed"})
            return
        path = urlsplit(self.path).path
        if path == "/health":
            self.respond(200, {"status": "ok", "running": RUNTIME.running()})
        elif path == "/api/xpair-canary":
            self.respond(200, state_payload())
        else:
            self.respond(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not origin_is_allowed(self.headers.get("Origin")):
            self.respond(403, {"error": "origin is not allowed"})
            return
        if urlsplit(self.path).path != "/api/xpair-canary/run":
            self.respond(404, {"error": "not found"})
            return
        try:
            payload = self.read_json()
            request = LaunchRequest.from_payload(payload)
            if request.mode == "live":
                if self.headers.get("X-BTC-Lab-XPair-Live") != "confirmed":
                    raise ValueError("live request is missing the confirmation header")
                if str(payload.get("confirmation") or "") != LIVE_CONFIRM_VALUE:
                    raise ValueError("live confirmation phrase does not match")
            RUNTIME.launch(request)
            self.respond(202, state_payload())
        except RuntimeError as exc:
            self.respond(409, {"error": str(exc)})
        except (ValueError, json.JSONDecodeError) as exc:
            self.respond(400, {"error": str(exc)})
        except Exception as exc:
            self.respond(500, {"error": str(exc)[:1000]})

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer((API_HOST, API_PORT), Handler)
    print(
        f"XPAIR Canary dashboard API listening on http://{API_HOST}:{API_PORT}",
        flush=True,
    )
    print("Open http://localhost:3000/xpair-canary", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
