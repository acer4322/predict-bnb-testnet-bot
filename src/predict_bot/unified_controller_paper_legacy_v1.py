from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

from . import predict_wallet_maker_ebm_strategy_v1 as maker_ebm
from . import predict_wallet_target_taker_public_side_strategy_v1 as public_side
from .strategy_target_compare_recorder_v1 import StrategyTargetCompareRecorder

ROOT = Path(__file__).resolve().parents[2]
VERSION = "UNIFIED_CONTROLLER_PAPER_V1_FORWARD_RECORDER"
HOST = os.environ.get("UNIFIED_CONTROLLER_PAPER_HOST", "127.0.0.1")
PORT = int(os.environ.get("UNIFIED_CONTROLLER_PAPER_PORT", "8784"))
SOURCE_URL = os.environ.get("UNIFIED_CONTROLLER_PUBLIC_SOURCE_URL", "http://127.0.0.1:8782/state")
POLL_SECONDS = max(0.20, float(os.environ.get("UNIFIED_CONTROLLER_POLL_SECONDS", "0.50")))
SOURCE_MAX_AGE_MS = max(1_000, int(os.environ.get("UNIFIED_CONTROLLER_SOURCE_MAX_AGE_MS", "3000")))


def now_ms() -> int:
    return int(time.time() * 1000)


def number(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def phase_from_seconds(seconds_left: float | None) -> str:
    if seconds_left is None:
        return "UNKNOWN"
    if seconds_left > 240:
        return "OPEN"
    if seconds_left > 60:
        return "MID"
    return "TAIL"


def simple3(snapshot: dict[str, Any]) -> dict[str, Any]:
    up_mid = number(snapshot.get("predictUpMid"))
    spot_bps = number(snapshot.get("spotMinusStrikeBps"))
    chain_bps = number(snapshot.get("chainlinkMinusStrikeBps"))
    votes: list[str] = []
    components: dict[str, Any] = {}
    for name, value, split in (
        ("predict", up_mid, 0.5),
        ("spot", spot_bps, 0.0),
        ("chainlink", chain_bps, 0.0),
    ):
        side = None if value is None or abs(value - split) <= 1e-15 else ("UP" if value > split else "DOWN")
        components[name] = {"value": value, "side": side}
        if side:
            votes.append(side)
    up = votes.count("UP")
    down = votes.count("DOWN")
    side = "UP" if up >= 2 else "DOWN" if down >= 2 else "NEUTRAL"
    strength = max(up, down) if side != "NEUTRAL" else 0
    return {"proxy": "SIMPLE3", "side": side, "strength": strength, "components": components}


class UnifiedControllerPaperV1:
    """Forward-only research runtime.

    Accepted structure only:
      - frozen public-side EBM is used once as OPEN/SEED side ranker;
      - frozen Maker Hazard + Level control passive quoting;
      - no MID/LATE Taker hard trigger is enabled because none has survived blind validation.

    Critical contamination boundary: this process never reads Target wallet events.
    It consumes only latestPublicSnapshot from the already-running 8782 public-feature bridge.
    """

    def __init__(self) -> None:
        self.started_at_ms = now_ms()
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.http = httpx.Client(timeout=httpx.Timeout(2.0, connect=0.5), trust_env=False)
        self.recorder = StrategyTargetCompareRecorder()
        self.side_model = public_side.load_side_model()
        self.maker_models = maker_ebm.load_models()
        self.current_market_id: int | None = None
        self.excluded_deployment_market_id: int | None = None
        self.active = False
        self.last_snapshot_ms: int | None = None
        self.last_loop_ms: int | None = None
        self.last_error: str | None = None
        self.source_ready = False
        self.source_wait_reason: str | None = "STARTING"
        self.source_health: dict[str, Any] = {}
        self.sequence = 0
        self.up_shares = 0.0
        self.down_shares = 0.0
        self.up_cost = 0.0
        self.down_cost = 0.0
        self.seed_done = False
        self.seed_info: dict[str, Any] | None = None
        self.orders: dict[tuple[str, int], dict[str, Any]] = {}
        self.last_closed: dict[tuple[str, int], int] = {}
        self.last_decision: dict[str, Any] | None = None
        self.fills = 0
        self.decisions = 0
        self.markets_started = 0

    def start(self) -> None:
        threading.Thread(target=self._loop, name="unified-controller-paper-v1", daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()
        self.http.close()
        self.recorder.close()

    def _fetch_snapshot(self) -> dict[str, Any] | None:
        response = self.http.get(SOURCE_URL)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            self.source_ready = False
            self.source_wait_reason = "SOURCE_STATE_UNAVAILABLE"
            return None

        health = payload.get("health") if isinstance(payload.get("health"), dict) else {}
        integrity = payload.get("dataIntegrity") if isinstance(payload.get("dataIntegrity"), dict) else {}
        self.source_health = {
            "processHealthy": health.get("processHealthy"),
            "strategyInputReady": health.get("strategyInputReady"),
            "strategyStatus": health.get("strategyStatus"),
            "missingFeatures": list(integrity.get("missingFeatures") or health.get("missingFeatures") or []),
            "lastLoopAgeMs": health.get("lastLoopAgeMs"),
        }
        if health.get("targetEventsUsedForDecision") is True:
            raise RuntimeError("SOURCE_CONTAMINATION_TARGET_EVENTS_USED_FOR_DECISION")
        if health.get("processHealthy") is not True:
            self.source_ready = False
            self.source_wait_reason = "SOURCE_PROCESS_DEGRADED"
            return None
        if integrity.get("ready") is not True:
            missing = self.source_health.get("missingFeatures") or []
            suffix = ":" + ",".join(str(x) for x in missing) if missing else ""
            self.source_ready = False
            self.source_wait_reason = f"SOURCE_INPUT_INCOMPLETE{suffix}"
            return None

        snap = payload.get("latestPublicSnapshot")
        if not isinstance(snap, dict):
            self.source_ready = False
            self.source_wait_reason = "SOURCE_SNAPSHOT_MISSING"
            return None
        sampled_at = int(number(snap.get("sampledAtMs")) or 0)
        age_ms = now_ms() - sampled_at if sampled_at > 0 else SOURCE_MAX_AGE_MS + 1
        if sampled_at <= 0 or age_ms > SOURCE_MAX_AGE_MS or age_ms < -1_000:
            self.source_ready = False
            self.source_wait_reason = f"SOURCE_SNAPSHOT_STALE:{age_ms}"
            return None

        self.source_ready = True
        self.source_wait_reason = None
        # Do not consume currentTrade, Target official state, Target fills, or performance.
        return dict(snap)

    def _reset_market(self, market_id: int, now: int) -> None:
        for order in list(self.orders.values()):
            self.recorder.record_order_cancel(order_id=order["id"], cancelled_at_ms=now, reason="MARKET_ROLLOVER")
        self.orders.clear()
        self.last_closed.clear()
        self.current_market_id = int(market_id)
        self.last_snapshot_ms = None
        self.up_shares = self.down_shares = 0.0
        self.up_cost = self.down_cost = 0.0
        self.seed_done = False
        self.seed_info = None
        self.last_decision = None
        if self.excluded_deployment_market_id is None:
            self.excluded_deployment_market_id = int(market_id)
            self.active = False
        else:
            self.active = True
            self.markets_started += 1

    def _portfolio(self, direction: dict[str, Any]) -> dict[str, Any]:
        cost = self.up_cost + self.down_cost
        net = self.up_shares - self.down_shares
        settle_up = self.up_shares - cost
        settle_down = self.down_shares - cost
        worst = min(settle_up, settle_down)
        side = direction.get("side")
        signed_direction = 1.0 if side == "UP" else -1.0 if side == "DOWN" else 0.0
        alignment_score = signed_direction * net
        alignment = "FAVORABLE" if alignment_score > 1e-9 else "UNFAVORABLE" if alignment_score < -1e-9 else "NEUTRAL"
        return {
            "upShares": self.up_shares,
            "downShares": self.down_shares,
            "upCostUsdt": self.up_cost,
            "downCostUsdt": self.down_cost,
            "costUsdt": cost,
            "netShares": net,
            "grossShares": self.up_shares + self.down_shares,
            "settleUpPnlUsdt": settle_up,
            "settleDownPnlUsdt": settle_down,
            "worstCasePnlUsdt": worst,
            "riskDeficitUsdt": max(0.0, -worst),
            "payoffGapShares": net,
            "alignment": alignment,
            "alignmentScore": alignment_score,
            "seedDone": self.seed_done,
        }

    @staticmethod
    def _economics(snapshot: dict[str, Any], portfolio: dict[str, Any]) -> dict[str, Any]:
        up_ask = number(snapshot.get("predictUpAsk"))
        down_ask = number(snapshot.get("predictDownAsk"))
        net = float(portfolio.get("netShares") or 0.0)
        repair_side = "DOWN" if net > 1e-9 else "UP" if net < -1e-9 else None
        repair_ask = down_ask if repair_side == "DOWN" else up_ask if repair_side == "UP" else None
        return {
            "upAsk": up_ask,
            "downAsk": down_ask,
            "repairSide": repair_side,
            "repairAsk": repair_ask,
            "pairAskSum": (up_ask + down_ask) if up_ask is not None and down_ask is not None else None,
        }

    def _fill_maker_orders(self, snapshot: dict[str, Any], snapshot_ns: int, now: int) -> int:
        filled = 0
        for key, order in list(self.orders.items()):
            if not maker_ebm.ask_touch_fill(order, snapshot, snapshot_ns=snapshot_ns, now_ms=now):
                continue
            side = str(order["side"])
            ask = number(snapshot.get("predictUpAsk" if side == "UP" else "predictDownAsk"))
            shares = float(order["shares"])
            price = float(order["price"])
            if side == "UP":
                self.up_shares += shares
                self.up_cost += shares * price
            else:
                self.down_shares += shares
                self.down_cost += shares * price
            self.orders.pop(key, None)
            self.last_closed[key] = now
            self.fills += 1
            self.recorder.record_order_fill(
                order_id=order["id"],
                fill_id=f"{order['id']}:FILL:{now}",
                filled_at_ms=now,
                fill_price=price,
                fill_state={"snapshot": snapshot, "observedAsk": ask, "fillProxy": "STRICT_LATER_ASK_TOUCH"},
                purpose="PASSIVE_MAKER",
                payload={"paperOnly": True, "queuePriorityClaim": False},
            )
            filled += 1
        return filled

    def _apply_maker_plan(self, decision_id: str, trace: dict[str, Any], maker_decision: dict[str, Any], snapshot_ns: int, now: int, allow_new: bool) -> None:
        desired_rows = maker_decision.get("orders") if maker_decision.get("decision") == "QUOTE" else []
        desired = {(str(r["side"]), int(r["priceTick"])): r for r in (desired_rows or [])}
        for key, order in list(self.orders.items()):
            if key in desired:
                continue
            self.recorder.record_order_cancel(order_id=order["id"], cancelled_at_ms=now, reason=str(maker_decision.get("reason") or "PLAN_CHANGE"))
            self.last_closed[key] = now
            self.orders.pop(key, None)
        if not allow_new:
            return
        for key, row in desired.items():
            if key in self.orders:
                continue
            if now - int(self.last_closed.get(key, 0)) < maker_ebm.REFILL_COOLDOWN_MS:
                continue
            self.sequence += 1
            order_id = f"{VERSION}:{self.current_market_id}:MAKER:{key[0]}:{key[1]}:{now}:{self.sequence}"
            order = {
                "id": order_id,
                "side": key[0],
                "priceTick": key[1],
                "price": float(row["price"]),
                "shares": float(row["shares"]),
                "placedAtMs": now,
                "placedSnapshotNs": snapshot_ns,
            }
            self.orders[key] = order
            self.recorder.record_order_placement(
                order_id=order_id,
                strategy_version=VERSION,
                market_id=int(self.current_market_id),
                placement_decision_id=decision_id,
                channel="MAKER",
                side=key[0],
                quote_type="BID",
                price=order["price"],
                shares=order["shares"],
                placed_at_ms=now,
                placement_state=trace,
            )

    def _step(self, snapshot: dict[str, Any]) -> None:
        market_id = int(number(snapshot.get("marketId")) or 0)
        sampled_at = int(number(snapshot.get("sampledAtMs")) or 0)
        snapshot_ns = int(number(snapshot.get("timestampNs")) or 0)
        if market_id <= 0 or sampled_at <= 0 or snapshot_ns <= 0:
            return
        now = now_ms()
        if self.current_market_id != market_id:
            self._reset_market(market_id, now)
        if sampled_at == self.last_snapshot_ms:
            return
        self.last_snapshot_ms = sampled_at
        if not self.active:
            return

        maker_filled = self._fill_maker_orders(snapshot, snapshot_ns, now)
        direction = simple3(snapshot)
        portfolio_before = self._portfolio(direction)
        economics = self._economics(snapshot, portfolio_before)
        seed_decision = public_side.decide_side(snapshot, self.side_model, expected_market_id=market_id, now_ms=now)
        seed_fill = None
        if not self.seed_done and seed_decision.get("decision") == "TRADE":
            seed_fill = public_side.execution(seed_decision)

        maker_inventory = maker_ebm.inventory(self.up_shares, self.down_shares, self.up_cost, self.down_cost)
        maker_decision = maker_ebm.decide(
            snapshot,
            self.maker_models,
            maker_inventory,
            cohort=maker_ebm.COMBINED_COHORT,
            expected_market_id=market_id,
            now_ms=now,
        )

        if seed_fill is not None:
            desired_action = "OPEN_SEED"
            execution_choice = "TAKER"
            primary_reason = "FROZEN_PUBLIC_SIDE_EBM_FIRST_QUALIFYING_SEED"
            side = str(seed_decision["side"])
            size = float(seed_fill["shares"])
        elif maker_decision.get("decision") == "QUOTE" and maker_decision.get("orders"):
            alignment = str(portfolio_before.get("alignment"))
            desired_action = "PASSIVE_REDUCE" if alignment == "UNFAVORABLE" else "PASSIVE_MAINTAIN"
            execution_choice = "MAKER"
            primary_reason = str(maker_decision.get("reason") or "MAKER_EBM_ACTIVE")
            side = None
            size = sum(float(x.get("shares") or 0.0) for x in maker_decision.get("orders") or [])
        else:
            desired_action = "HOLD"
            execution_choice = "WAIT"
            primary_reason = str(maker_decision.get("reason") or seed_decision.get("reason") or "NO_VALIDATED_ACTION")
            side = None
            size = 0.0

        vetoes = []
        if self.seed_done:
            vetoes.append("MID_LATE_TAKER_ESCALATION_NOT_YET_BLIND_VALIDATED")
        elif seed_fill is None:
            vetoes.append(str(seed_decision.get("reason") or "SEED_NOT_READY"))
        if maker_decision.get("decision") != "QUOTE":
            vetoes.append(str(maker_decision.get("reason") or "MAKER_NOT_ACTIVE"))

        decision_id = f"{VERSION}:{market_id}:DECISION:{sampled_at}"
        trace = {
            "version": VERSION,
            "decisionId": decision_id,
            "marketId": market_id,
            "decisionMs": now,
            "sourceSnapshotMs": sampled_at,
            "snapshotTimestampNs": snapshot_ns,
            "phase": phase_from_seconds(number(snapshot.get("secondsLeft"))),
            "desiredPortfolioAction": desired_action,
            "executionChoice": execution_choice,
            "primaryReason": primary_reason,
            "direction": direction,
            "portfolioBefore": portfolio_before,
            "economics": economics,
            "seedDecision": seed_decision,
            "makerDecision": maker_decision,
            "makerFilledThisSnapshot": maker_filled,
            "midLateTakerPolicy": "WAIT_NO_VALIDATED_HARD_TRIGGER",
            "paperOnly": True,
            "liveOrdersAffected": False,
            "targetEventsUsedForDecision": False,
        }
        self.recorder.record_decision(
            decision_id=decision_id,
            strategy_version=VERSION,
            market_id=market_id,
            decision_ms=now,
            source_snapshot_ms=sampled_at,
            seconds_left=number(snapshot.get("secondsLeft")),
            phase=trace["phase"],
            desired_portfolio_action=desired_action,
            execution_choice=execution_choice,
            side=side,
            size=size,
            primary_reason=primary_reason,
            supporting_reasons={
                "simple3": direction,
                "makerHazard": maker_decision.get("hazard"),
                "makerLevels": maker_decision.get("levels"),
                "seedSignal": seed_decision.get("signal"),
            },
            veto_reasons=vetoes,
            direction_state=direction,
            portfolio_state=portfolio_before,
            economics_state=economics,
            arbitration_state={
                "seedDone": self.seed_done,
                "makerDecision": maker_decision.get("decision"),
                "makerReason": maker_decision.get("reason"),
                "midLateTaker": "WAIT_UNVALIDATED",
            },
            public_state=snapshot,
            payload=trace,
        )
        self.decisions += 1

        if seed_fill is not None:
            side = str(seed_decision["side"])
            shares = float(seed_fill["shares"])
            unit_cost = float(seed_fill["effectiveUnitCost"])
            if side == "UP":
                self.up_shares += shares
                self.up_cost += shares * unit_cost
            else:
                self.down_shares += shares
                self.down_cost += shares * unit_cost
            self.seed_done = True
            self.seed_info = {"side": side, **seed_fill, "filledAtMs": now, "decisionId": decision_id}
            self.fills += 1
            self.recorder.record_taker_fill(
                fill_id=f"{decision_id}:TAKER_FILL",
                strategy_version=VERSION,
                market_id=market_id,
                decision_id=decision_id,
                purpose="OPEN_SEED",
                side=side,
                price=unit_cost,
                shares=shares,
                filled_at_ms=now,
                decision_state=trace,
                fill_state={"snapshot": snapshot, "observedAsk": seed_fill["ask"], "effectiveUnitCost": unit_cost},
                payload={"stakeUsdt": seed_fill["stakeUsdt"], "paperOnly": True},
            )

        # Do not place fresh Maker orders on a snapshot that already produced any fill.
        self._apply_maker_plan(
            decision_id,
            trace,
            maker_decision,
            snapshot_ns,
            now,
            allow_new=(maker_filled == 0 and seed_fill is None),
        )
        self.last_decision = trace

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                snapshot = self._fetch_snapshot()
                if snapshot:
                    with self.lock:
                        self._step(snapshot)
                self.last_error = None
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {str(exc)[:500]}"
            self.last_loop_ms = now_ms()
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(0.05, POLL_SECONDS - elapsed))

    def snapshot(self) -> dict[str, Any]:
        at = now_ms()
        with self.lock:
            direction = self.last_decision.get("direction") if self.last_decision else {"side": "NEUTRAL"}
            portfolio = self._portfolio(direction if isinstance(direction, dict) else {"side": "NEUTRAL"})
            return {
                "ok": self.last_error is None,
                "status": (
                    "DEGRADED" if self.last_error is not None
                    else "WAITING_SOURCE" if not self.source_ready
                    else "ACTIVE" if self.active
                    else "WAITING_NEXT_COMPLETE_MARKET"
                ),
                "version": VERSION,
                "paperOnly": True,
                "forwardOnly": True,
                "liveOrdersAffected": False,
                "targetEventsUsedForDecision": False,
                "targetDataRead": False,
                "sourceUrl": SOURCE_URL,
                "sourceReady": self.source_ready,
                "sourceWaitReason": self.source_wait_reason,
                "sourceHealth": dict(self.source_health),
                "sourceMaxAgeMs": SOURCE_MAX_AGE_MS,
                "currentMarketId": self.current_market_id,
                "excludedDeploymentMarketId": self.excluded_deployment_market_id,
                "lastSnapshotMs": self.last_snapshot_ms,
                "lastSnapshotAgeMs": at - self.last_snapshot_ms if self.last_snapshot_ms else None,
                "lastLoopAgeMs": at - self.last_loop_ms if self.last_loop_ms else None,
                "lastError": self.last_error,
                "portfolio": portfolio,
                "seed": self.seed_info,
                "activeMakerOrders": list(self.orders.values()),
                "lastDecision": self.last_decision,
                "run": {"marketsStarted": self.markets_started, "decisions": self.decisions, "fills": self.fills},
                "recorderDb": str(self.recorder.path),
                "policy": {
                    "seed": "first qualifying frozen public-side EBM paper Taker, one per market",
                    "maker": "frozen TARGET_MAKER_EBM_V1 Hazard+Level with own unified inventory",
                    "midLateTaker": "disabled until a strict-past blind trigger survives validation",
                    "directionState": "SIMPLE3 proxy only",
                    "deploymentBoundary": "current market at process startup excluded; begins next complete market",
                },
            }


class Handler(BaseHTTPRequestHandler):
    runtime: UnifiedControllerPaperV1

    def log_message(self, *_args: Any) -> None:
        return

    def _write(self, payload: Any, code: int = 200) -> None:
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

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in {"/", "/state", "/health"}:
            self._write(self.runtime.snapshot())
        else:
            self._write({"ok": False, "error": "not found"}, 404)


def main() -> int:
    runtime = UnifiedControllerPaperV1()
    runtime.start()
    handler = type("UnifiedControllerPaperV1Handler", (Handler,), {"runtime": runtime})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}/state; source={SOURCE_URL}; "
        "paperOnly=true; targetDataRead=false; liveOrdersAffected=false",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
