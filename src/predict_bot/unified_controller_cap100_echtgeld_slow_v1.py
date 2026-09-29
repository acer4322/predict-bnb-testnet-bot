from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import unified_controller_cap100_echtgeld_v1 as live
from . import unified_controller_cap100_shadow_v1 as base

VERSION = "UNIFIED_CAP100_ECHTGELD_SLOW_EXECUTION_V1"
HOST = os.environ.get("UNIFIED_CONTROLLER_CAP100_SLOW_HOST", "127.0.0.1")
PORT = int(os.environ.get("UNIFIED_CONTROLLER_CAP100_SLOW_PORT", "8788"))

# This variant is deliberately event/state paced rather than paper-hazard paced.
# It does not promise profitability; its purpose is to bound live-path divergence
# while collecting execution evidence under real resting/partial-fill behaviour.
MAKER_TERMINAL_SETTLE_MS = 1500


class UnifiedControllerCap100EchtgeldSlowV1(live.UnifiedControllerCap100EchtgeldV1):
    """CAP100 Echtgeld slow-execution research variant.

    Ordinary Maker generation is serialized by venue lifecycle. After a Maker is
    accepted, no new ordinary Maker/burst generation is allowed while any current-
    market Maker remains RESTING/PARTIAL/CANCEL_PENDING locally. Once the last Maker
    becomes terminal, a short settle window allows reconciliation/inventory state to
    land before a new Maker generation is eligible.

    ACTIVE_INTERVENTION_REQUIRED is not paced by this Maker gate. Reconciliation,
    cancel safety and active Taker intervention retain higher priority.
    """

    def __init__(self) -> None:
        super().__init__()
        base.VERSION = VERSION
        live.VERSION = VERSION
        # Isolate this research variant from the canonical 8787 recorder.
        try:
            self.recorder.close()
        except Exception:
            pass
        self.recorder = base.StrategyTargetCompareRecorder(base.ROOT / "data" / "strategy_cap100_echtgeld_slow_v1.db")
        self.maker_generation_blocked = False
        self.maker_generation_order_ids: set[str] = set()
        self.last_maker_terminal_ms: int | None = None
        self.slow_metrics = {
            "makerLifecycleBlocks": 0,
            "makerGenerationsOpened": 0,
            "makerGenerationsTerminal": 0,
            "makerSettleWindowBlocks": 0,
            "makerImbalanceExpansionBlocks": 0,
        }

    def _reset_market(self, market_id: int, sampled_at: int) -> None:
        super()._reset_market(market_id, sampled_at)
        self.maker_generation_blocked = False
        self.maker_generation_order_ids.clear()
        self.last_maker_terminal_ms = None

    def _maker_generation_open(self, now: int) -> bool:
        # Any known local current-market Maker means its execution lifecycle is still
        # relevant. Do not let paper-style hazard/burst scheduling pile another
        # generation on top of an unresolved venue lifecycle.
        if self.orders:
            self.slow_metrics["makerLifecycleBlocks"] += 1
            return False
        if self.orphan_orders:
            self.slow_metrics["makerLifecycleBlocks"] += 1
            return False
        if self.last_maker_terminal_ms is not None and now - self.last_maker_terminal_ms < MAKER_TERMINAL_SETTLE_MS:
            self.slow_metrics["makerSettleWindowBlocks"] += 1
            return False
        return True

    def _maker_would_expand_imbalance(self, side: str) -> bool:
        maker_net = float(self.inventory.maker_up - self.inventory.maker_down)
        return (maker_net > base.EPS and side == "UP") or (maker_net < -base.EPS and side == "DOWN")

    def _add_order(
        self,
        side: str,
        now: int,
        snapshot_ns: int,
        decision_id: str,
        reason: str,
        p: float,
        snapshot: dict[str, Any],
        allow_stack: bool = True,
        bypass_guard: bool = False,
    ) -> bool:
        # ACTIVE repair Maker is still passive Maker. Only active Taker may bypass
        # lifecycle pacing. This intentionally disables same-tick burst stacking.
        if not self._maker_generation_open(now):
            return False

        # Collection-mode risk envelope: ordinary/passive Maker may reduce an
        # existing directional imbalance, but may not knowingly expand it. This is
        # intentionally more conservative than the target-imitation policy and is
        # why this variant must not be used to score strategy fidelity.
        if self._maker_would_expand_imbalance(side):
            self.slow_metrics["makerImbalanceExpansionBlocks"] += 1
            return False
        before = set(self.orders)
        made = super()._add_order(
            side,
            now,
            snapshot_ns,
            decision_id,
            reason,
            p,
            snapshot,
            allow_stack=False,
            bypass_guard=bypass_guard,
        )
        if made:
            created = [order.id for key, order in self.orders.items() if key not in before]
            self.maker_generation_order_ids.update(created)
            self.maker_generation_blocked = True
            self.slow_metrics["makerGenerationsOpened"] += 1
        return made

    def _apply_engine_event(self, event: dict[str, Any]) -> None:
        cid = str(event.get("client_order_id") or "")
        role = str(event.get("role") or "").upper()
        event_type = str(event.get("event_type") or "").upper()
        state = str(event.get("state") or "").upper()
        super()._apply_engine_event(event)
        if role != "MAKER" or cid not in self.maker_generation_order_ids:
            return
        terminal = event_type in {"ORDER_FILLED", "ORDER_CANCELED", "ORDER_REJECTED"} or state in {"FILLED", "CANCELED", "REJECTED"}
        if not terminal:
            return
        self.maker_generation_order_ids.discard(cid)
        if not self.maker_generation_order_ids:
            self.maker_generation_blocked = False
            self.last_maker_terminal_ms = int(base.number(event.get("occurred_at_ms")) or base.now_ms())
            self.slow_metrics["makerGenerationsTerminal"] += 1

    def snapshot(self) -> dict[str, Any]:
        result = super().snapshot()
        result["version"] = VERSION
        result["candidate"] = "CAP100_ECHTGELD_SLOW_EXECUTION_RESEARCH_V1"
        result["slowExecution"] = {
            "enabled": True,
            "makerGenerationBlocked": bool(self.maker_generation_blocked or self.orders or self.orphan_orders),
            "generationOrderIds": sorted(self.maker_generation_order_ids),
            "lastMakerTerminalMs": self.last_maker_terminal_ms,
            "terminalSettleMs": MAKER_TERMINAL_SETTLE_MS,
            "metrics": dict(self.slow_metrics),
            "semantics": {
                "ordinaryMakerSerializedByVenueLifecycle": True,
                "sameTickMakerBurstDisabled": True,
                "restingMakerBlocksNewMakerGeneration": True,
                "partialMakerBlocksNewMakerGeneration": True,
                "activeInterventionCanPreemptMakerWait": True,
                "paperImmediateFillAssumption": False,
                "makerMayNotExpandExistingDirectionalImbalance": True,
                "intendedUse": "ECHTGELD_EXECUTION_DATA_COLLECTION_NOT_TARGET_FIDELITY_SCORING",
            },
        }
        return result


class Handler(BaseHTTPRequestHandler):
    runtime: UnifiedControllerCap100EchtgeldSlowV1

    def log_message(self, *_args: Any) -> None:
        return

    def _write(self, payload: Any, code: int = 200) -> None:
        import json
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] in {"/", "/state", "/health"}:
            payload = self.runtime.snapshot()
            if self.path.split("?", 1)[0] == "/health":
                payload = {
                    "ok": True,
                    "version": VERSION,
                    "paperOnly": False,
                    "liveOrdersAffected": True,
                    "researchVariant": True,
                    "slowExecution": payload.get("slowExecution"),
                    "lastError": self.runtime.last_error,
                }
            self._write(payload)
        else:
            self._write({"ok": False, "error": "not found"}, 404)


def main() -> int:
    runtime = UnifiedControllerCap100EchtgeldSlowV1()
    runtime.start()
    handler = type("UnifiedControllerCap100EchtgeldSlowV1Handler", (Handler,), {"runtime": runtime})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}/state; research variant only; "
        "Maker generation serialized by venue lifecycle; does not auto-arm Echtgeld",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown(); server.server_close(); runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
