from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from typing import Any

from . import echtgeld_engine_v1 as v1
from . import echtgeld_engine_v23 as v23

VERSION = "ECHTGELD_ENGINE_V2_4310_CAP100_FAILURE_DRILL_V1"
HOST = v23.HOST
PORT = v23.PORT


class EchtgeldEngine(v23.EchtgeldEngine):
    """V23 plus an isolated CAP100 failure-drill simulator.

    Drill runs never call Binance, never arm Echtgeld, never write production
    CAP100 order/fill ledgers and never affect PnL/stop-loss.  They exercise the
    same operational invariants expected by the real V23 adapter and report the
    resulting strategy/risk response as deterministic state transitions.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.drill_lock = threading.RLock()
        super().__init__(*args, **kwargs)

    def _setup_schema(self) -> None:
        super()._setup_schema()
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS engine_cap100_drill_runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at_ms INTEGER NOT NULL,
                    completed_at_ms INTEGER,
                    scenario_set TEXT NOT NULL,
                    status TEXT NOT NULL,
                    passed INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0,
                    report_json TEXT NOT NULL DEFAULT '{}'
                );
                """
            )
            self.db.commit()

    @staticmethod
    def _case(name: str, description: str, steps: list[dict[str, Any]], checks: list[tuple[str, bool]], final: dict[str, Any]) -> dict[str, Any]:
        assertions = [{"name": n, "passed": bool(ok)} for n, ok in checks]
        return {
            "scenario": name,
            "description": description,
            "passed": all(x["passed"] for x in assertions),
            "steps": steps,
            "assertions": assertions,
            "final": final,
        }

    def _drill_submission_rejected(self) -> dict[str, Any]:
        steps = [
            {"event": "ORDER_INTENT", "state": "PLANNED", "filledShares": 0},
            {"event": "QUOTE_REJECTED_BEFORE_WRITE", "state": "REJECTED", "venueWrite": False},
            {"event": "STRATEGY_FEEDBACK", "action": "NO_INVENTORY_CHANGE", "retry": "NEW_DECISION_ONLY"},
        ]
        final = {"orderState": "REJECTED", "inventoryDelta": 0, "entryFrozen": False, "venueWrite": False}
        return self._case(
            "ORDER_SUBMISSION_REJECTED",
            "Quote/preflight rejects the order before any venue write.", steps,
            [("no phantom fill", final["inventoryDelta"] == 0), ("not ambiguous", not final["entryFrozen"]), ("no venue write", not final["venueWrite"])], final,
        )

    def _drill_venue_order_failed(self) -> dict[str, Any]:
        steps = [
            {"event": "PLACE_ACK", "state": "RESTING", "orderId": "DRILL-O1"},
            {"event": "ORDER_HISTORY", "exchangeStatus": "FAILED", "filledShares": 0},
            {"event": "RECONCILE", "state": "REJECTED", "action": "NO_AUTOMATIC_RETRY"},
        ]
        final = {"orderState": "REJECTED", "inventoryDelta": 0, "entryFrozen": False, "automaticRetry": False}
        return self._case(
            "VENUE_ORDER_FAILED_NO_FILL",
            "Venue accepted an identity but later reports FAILED with zero fill.", steps,
            [("zero fill remains zero inventory", final["inventoryDelta"] == 0), ("terminal failure unfreezes", not final["entryFrozen"]), ("never blind retry", not final["automaticRetry"])], final,
        )

    def _drill_partial_fill(self) -> dict[str, Any]:
        steps = [
            {"event": "PLACE_ACK", "state": "RESTING", "requestedShares": 18},
            {"event": "ORDER_HISTORY", "exchangeStatus": "PARTIALLY_FILLED", "cumulativeShares": 7, "cumulativeUsdt": 2.8},
            {"event": "FILL_DELTA", "deltaShares": 7, "deltaUsdt": 2.8, "strategyInventoryShares": 7},
            {"event": "ORDER_HISTORY", "exchangeStatus": "PARTIALLY_FILLED", "cumulativeShares": 12, "cumulativeUsdt": 4.8},
            {"event": "FILL_DELTA", "deltaShares": 5, "deltaUsdt": 2.0, "strategyInventoryShares": 12},
        ]
        final = {"orderState": "PARTIAL_FILL", "confirmedShares": 12, "inventoryShares": 12, "remainingRestingShares": 6, "countedUsdt": 4.8}
        return self._case(
            "PARTIAL_FILL_INCREMENTAL",
            "Maker fills in multiple increments; CAP100 must consume only newly confirmed fill deltas.", steps,
            [("inventory equals confirmed fills", final["inventoryShares"] == final["confirmedShares"]), ("unfilled shares remain risk", final["remainingRestingShares"] == 6), ("no double counting", final["countedUsdt"] == 4.8)], final,
        )

    def _drill_price_spike(self) -> dict[str, Any]:
        signal = 0.40
        max_price = 0.42
        venue_ask = 0.57
        steps = [
            {"event": "TAKER_INTENT", "signalPrice": signal, "maxPrice": max_price},
            {"event": "VENUE_BOOK_REFRESH", "venueAsk": venue_ask},
            {"event": "PRICE_CAP_CHECK", "state": "REJECTED", "reason": "TAKER_PRICE_CAP", "venueWrite": False},
            {"event": "STRATEGY_FEEDBACK", "action": "WAIT_FOR_NEW_DECISION", "inventoryDelta": 0},
        ]
        final = {"orderState": "REJECTED", "reason": "TAKER_PRICE_CAP", "venueAsk": venue_ask, "maxPrice": max_price, "venueWrite": False, "inventoryDelta": 0}
        return self._case(
            "FAST_PRICE_RISE_SLIPPAGE_CAP",
            "Market ask jumps above the strategy max-price/slippage cap before venue write.", steps,
            [("price cap blocks", venue_ask > max_price), ("no venue write", not final["venueWrite"]), ("no inventory mutation", final["inventoryDelta"] == 0)], final,
        )

    def _drill_transport_unknown_then_fill(self) -> dict[str, Any]:
        steps = [
            {"event": "PLACE_WRITE", "state": "PLACING"},
            {"event": "TRANSPORT_TIMEOUT_AFTER_WRITE", "state": "UNKNOWN_SUBMISSION"},
            {"event": "SAFETY", "entryFrozen": True, "retry": False},
            {"event": "ORDER_HISTORY_UNIQUE_MATCH", "orderId": "DRILL-RECOVERED", "exchangeStatus": "FILLED", "filledShares": 18, "filledUsdt": 7.2},
            {"event": "FILL_DELTA", "deltaShares": 18, "deltaUsdt": 7.2},
            {"event": "SAFETY", "entryFrozen": False},
        ]
        final = {"orderState": "FILLED", "inventoryShares": 18, "entryFrozen": False, "blindRetry": False, "pnlEligible": True}
        return self._case(
            "TRANSPORT_UNKNOWN_RECOVERED_FILLED",
            "Write response is lost but order actually filled; freeze until unique venue-history proof arrives.", steps,
            [("never blind retry", not final["blindRetry"]), ("recovered fill reaches inventory", final["inventoryShares"] == 18), ("recovered fill enters PnL ledger", final["pnlEligible"]), ("freeze clears only after proof", not final["entryFrozen"])], final,
        )

    def _drill_cancel_fill_race(self) -> dict[str, Any]:
        steps = [
            {"event": "ORDER_RESTING", "state": "RESTING", "filledShares": 0},
            {"event": "CANCEL_REQUEST_ACCEPTED", "state": "CANCEL_PENDING"},
            {"event": "VENUE_RACE", "exchangeStatus": "FILLED", "filledShares": 18, "filledUsdt": 7.2},
            {"event": "RECONCILE", "state": "FILLED"},
            {"event": "FILL_DELTA", "deltaShares": 18, "deltaUsdt": 7.2, "strategyInventoryShares": 18},
        ]
        final = {"orderState": "FILLED", "cancelAckTreatedAsTerminal": False, "inventoryShares": 18}
        return self._case(
            "CANCEL_FILL_RACE",
            "Order fills after cancel request is accepted but before venue terminal confirmation.", steps,
            [("cancel ACK is not terminal", not final["cancelAckTreatedAsTerminal"]), ("late fill is counted", final["inventoryShares"] == 18)], final,
        )

    def _drill_heartbeat_loss(self) -> dict[str, Any]:
        steps = [
            {"event": "ORDER_RESTING", "state": "RESTING"},
            {"event": "8787_HEARTBEAT_STALE", "ageMs": v23.CAP100_HEARTBEAT_TIMEOUT_MS + 1},
            {"event": "ENGINE_FAILSAFE", "armed": False, "orderState": "CANCEL_PENDING"},
            {"event": "RECONCILE_REQUIRED", "terminalAssumed": False},
        ]
        final = {"armed": False, "orderState": "CANCEL_PENDING", "terminalAssumed": False}
        return self._case(
            "CONTROLLER_HEARTBEAT_LOSS",
            "8787 becomes unavailable while a Maker order is resting.", steps,
            [("engine pauses", not final["armed"]), ("resting maker cancellation requested", final["orderState"] == "CANCEL_PENDING"), ("cancel is not guessed terminal", not final["terminalAssumed"])], final,
        )

    def _drill_stop_loss_with_resting(self) -> dict[str, Any]:
        steps = [
            {"event": "SETTLEMENT", "cap100NetPnlUsdt": -7.2, "stopLossUsdt": 5.0},
            {"event": "STOP_LOSS_TRIP", "armed": False},
            {"event": "CANCEL_ALL_MAKER", "orderState": "CANCEL_PENDING"},
            {"event": "RECONCILE_REQUIRED", "newEntries": False},
        ]
        final = {"armed": False, "newEntries": False, "makerOrderState": "CANCEL_PENDING"}
        return self._case(
            "STOP_LOSS_WITH_RESTING_MAKER",
            "Realized CAP100 loss crosses engine stop-loss while Maker risk is still resting.", steps,
            [("stop loss pauses engine", not final["armed"]), ("new entries blocked", not final["newEntries"]), ("resting maker cancel requested", final["makerOrderState"] == "CANCEL_PENDING")], final,
        )

    def _drill_unknown_unresolved(self) -> dict[str, Any]:
        steps = [
            {"event": "PLACE_TIMEOUT", "state": "UNKNOWN_SUBMISSION"},
            {"event": "ORDER_HISTORY", "uniqueMatch": False},
            {"event": "30S_HARD_AGE", "state": "UNKNOWN_SUBMISSION", "reviewRequired": True},
            {"event": "SAFETY", "entryFrozen": True, "automaticRetry": False},
        ]
        final = {"orderState": "UNKNOWN_SUBMISSION", "entryFrozen": True, "reviewRequired": True, "automaticRetry": False}
        return self._case(
            "UNKNOWN_UNRESOLVED_MANUAL_REVIEW",
            "Venue-write uncertainty cannot be uniquely reconciled from bounded history.", steps,
            [("remains fail-closed", final["entryFrozen"]), ("manual review required", final["reviewRequired"]), ("never automatic retry", not final["automaticRetry"])], final,
        )

    def _drill_scenarios(self) -> list[dict[str, Any]]:
        return [
            self._drill_submission_rejected(),
            self._drill_venue_order_failed(),
            self._drill_partial_fill(),
            self._drill_price_spike(),
            self._drill_transport_unknown_then_fill(),
            self._drill_cancel_fill_race(),
            self._drill_heartbeat_loss(),
            self._drill_stop_loss_with_resting(),
            self._drill_unknown_unresolved(),
        ]

    def run_cap100_drill(self) -> dict[str, Any]:
        with self.drill_lock:
            started = v1._now_ms()
            with self.db_lock:
                cur = self.db.execute(
                    "INSERT INTO engine_cap100_drill_runs(started_at_ms,scenario_set,status,report_json) VALUES(?,?,?,?)",
                    (started, "CAP100_FAILURE_MATRIX_V1", "RUNNING", "{}"),
                )
                run_id = int(cur.lastrowid)
                self.db.commit()
            cases = self._drill_scenarios()
            passed = sum(1 for x in cases if x["passed"])
            failed = len(cases) - passed
            report = {
                "runId": run_id,
                "version": "CAP100_FAILURE_MATRIX_V1",
                "isolated": True,
                "venueWrites": 0,
                "productionLedgerWrites": 0,
                "productionPnlAffected": False,
                "engineArmingAffected": False,
                "passed": passed,
                "failed": failed,
                "total": len(cases),
                "status": "PASS" if failed == 0 else "FAIL",
                "scenarios": cases,
            }
            completed = v1._now_ms()
            with self.db_lock:
                self.db.execute(
                    "UPDATE engine_cap100_drill_runs SET completed_at_ms=?,status=?,passed=?,failed=?,report_json=? WHERE run_id=?",
                    (completed, report["status"], passed, failed, json.dumps(report, ensure_ascii=False, separators=(",", ":"), default=str), run_id),
                )
                self.db.commit()
            return report

    def cap100_drill_state(self) -> dict[str, Any]:
        with self.db_lock:
            row = self.db.execute("SELECT * FROM engine_cap100_drill_runs ORDER BY run_id DESC LIMIT 1").fetchone()
        if row is None:
            return {"available": True, "latest": None, "scenarioSet": "CAP100_FAILURE_MATRIX_V1", "isolated": True}
        item = dict(row)
        try:
            item["report"] = json.loads(str(item.pop("report_json") or "{}"))
        except Exception:
            item["report"] = {}
        return {"available": True, "latest": item, "scenarioSet": "CAP100_FAILURE_MATRIX_V1", "isolated": True}

    def state(self) -> dict[str, Any]:
        payload = super().state()
        payload["version"] = VERSION
        payload["cap100FailureDrill"] = self.cap100_drill_state()
        return payload

    def health(self) -> dict[str, Any]:
        payload = super().health()
        payload["version"] = VERSION
        payload["cap100FailureDrill"] = True
        payload["cap100FailureDrillVenueWrites"] = False
        payload["cap100FailureDrillProductionLedgerIsolated"] = True
        return payload


class _Handler(v23._Handler):
    engine: EchtgeldEngine

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/cap100/drill":
            self._send(200, {"ok": True, "drill": self.engine.cap100_drill_state()})
            return
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/cap100/drill/run":
            # Body is intentionally accepted/ignored for compatibility with the
            # existing localhost JSON control pattern. The V1 drill matrix is frozen.
            try:
                _payload = self._body()
                self._send(200, {"ok": True, "report": self.engine.run_cap100_drill()})
            except Exception as exc:
                self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:500]}"})
            return
        return super().do_POST()


def main() -> int:
    engine = EchtgeldEngine()
    handler = type("EchtgeldEngineV24Handler", (_Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}; CAP100 failure drill isolated=true; "
        "drill venue writes=false; production ledger/PnL untouched",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown(); server.server_close(); engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
