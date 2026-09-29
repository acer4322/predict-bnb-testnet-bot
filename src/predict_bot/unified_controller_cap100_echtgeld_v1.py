from __future__ import annotations

import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx

from . import unified_controller_cap100_shadow_v1 as base

VERSION = "UNIFIED_PROMOTED_OWNSTATE_V4_R2_CAP100_KEEP18_ECHTGELD_V1"
HOST = os.environ.get("UNIFIED_CONTROLLER_CAP100_LIVE_HOST", "127.0.0.1")
PORT = int(os.environ.get("UNIFIED_CONTROLLER_CAP100_LIVE_PORT", "8787"))
ENGINE_URL = str(os.environ.get("UNIFIED_CONTROLLER_CAP100_ENGINE_URL") or "http://127.0.0.1:8781").rstrip("/")
ENTRY_SOURCE = "CAP100_8787"
HEARTBEAT_SECONDS = 0.60
EVENT_POLL_SECONDS = 0.20
TAKER_CONFIRM_TIMEOUT_SECONDS = 2.2


class UnifiedControllerCap100EchtgeldV1(base.UnifiedControllerCap100ShadowV1):
    """CAP100 controller with 8781 as the only venue owner.

    The frozen CAP100 decision policy remains inherited from the paper controller,
    but paper queue-clear fills are disabled.  Inventory/capital changes are driven
    only by 8781 venue-confirmed FILL_DELTA events.
    """

    def __init__(self) -> None:
        # The inherited 8786 paper runtime intentionally refuses to initialize when
        # PREDICT_LIVE_ENABLED=true.  8787 is a separate adapter whose only venue
        # owner is 8781, so temporarily mask that paper-only startup guard while
        # constructing the frozen decision machinery, then restore the environment.
        _live_flag = os.environ.get("PREDICT_LIVE_ENABLED")
        os.environ["PREDICT_LIVE_ENABLED"] = "false"
        try:
            super().__init__()
        finally:
            if _live_flag is None:
                os.environ.pop("PREDICT_LIVE_ENABLED", None)
            else:
                os.environ["PREDICT_LIVE_ENABLED"] = _live_flag
        # This is a separate process from 8786, so give all inherited decision/order
        # ids the live adapter version and isolate its research trace DB.
        base.VERSION = VERSION
        try:
            self.recorder.close()
        except Exception:
            pass
        self.recorder = base.StrategyTargetCompareRecorder(base.ROOT / "data" / "strategy_cap100_echtgeld_v1.db")
        self.exec_http = httpx.Client(timeout=httpx.Timeout(2.5, connect=0.6))
        self.exec_lock = threading.RLock()
        self.heartbeat_thread: threading.Thread | None = None
        self.last_heartbeat_ms: int | None = None
        self.last_engine_state_ms: int | None = None
        self.engine_state: dict[str, Any] = {}
        self.execution_ready = False
        self.execution_block_reason: str | None = "ENGINE_NOT_CHECKED"
        self.entry_freeze = False
        self.entry_freeze_detail: str | None = None
        self.last_event_seq = 0
        self.active_intervention_required = False
        self.active_intervention_reason: str | None = None
        self.last_event_poll_mono = 0.0
        self.pending_cancels: set[str] = set()
        self.orphan_orders: dict[str, base.PaperOrder] = {}
        self.taker_pending: dict[str, dict[str, Any]] = {}
        self.live_activation_market_id: int | None = None
        self.wait_next_market_after_activation = True
        self.live_metrics = {
            "makerSubmitAccepted": 0,
            "makerSubmitRejected": 0,
            "makerUnknown": 0,
            "makerCancelRequested": 0,
            "makerFillEvents": 0,
            "makerPartialFillEvents": 0,
            "takerSubmitAccepted": 0,
            "takerSubmitRejected": 0,
            "takerUnknown": 0,
            "takerFillEvents": 0,
            "engineHttpErrors": 0,
            "heartbeatFailures": 0,
            "orphanRiskBlocks": 0,
        }

    def start(self) -> None:
        super().start()
        if self.heartbeat_thread is None or not self.heartbeat_thread.is_alive():
            self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, name="cap100-8787-heartbeat", daemon=True)
            self.heartbeat_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.heartbeat_thread is not None and self.heartbeat_thread.is_alive():
            self.heartbeat_thread.join(timeout=2.0)
        try:
            self.exec_http.close()
        except Exception:
            pass
        super().stop()

    def _engine_get(self, path: str) -> dict[str, Any]:
        response = self.exec_http.get(f"{ENGINE_URL}{path}", headers={"Cache-Control": "no-store"})
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("8781 returned non-object JSON")
        return payload

    def _engine_post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.exec_http.post(f"{ENGINE_URL}{path}", json=payload)
        data: dict[str, Any]
        try:
            data = response.json()
        except Exception:
            data = {"ok": False, "error": f"HTTP {response.status_code} non-JSON response"}
        if response.status_code >= 400:
            raise RuntimeError(str(data.get("error") or f"8781 HTTP {response.status_code}"))
        return data

    def _heartbeat_loop(self) -> None:
        while not self.stop_event.wait(HEARTBEAT_SECONDS):
            try:
                state = self._engine_get("/cap100/state")
                cap = state.get("cap100") if isinstance(state.get("cap100"), dict) else {}
                previous_ready = self.execution_ready
                self.engine_state = dict(cap)
                self.last_engine_state_ms = base.now_ms()
                selected = bool(cap.get("selected"))
                armed = bool(cap.get("armed"))
                frozen = bool(cap.get("entryWriteFrozen"))
                self.entry_freeze = frozen
                self.entry_freeze_detail = str((cap.get("unknownWrite") or {}).get("client_order_id") or "") or None
                self.execution_ready = selected and armed and not frozen
                if not selected:
                    self.execution_block_reason = "8781_SOURCE_NOT_SELECTED"
                elif not armed:
                    self.execution_block_reason = "8781_PAUSED"
                elif frozen:
                    self.execution_block_reason = f"8781_UNKNOWN_WRITE:{self.entry_freeze_detail or 'UNKNOWN'}"
                elif self.orphan_orders:
                    self.execution_block_reason = "OLD_MARKET_ORDERS_NOT_TERMINAL"
                    self.execution_ready = False
                else:
                    self.execution_block_reason = None
                if self.execution_ready and not previous_ready:
                    # Do not activate halfway through a 5m market. We wait for a clean
                    # market boundary after the operator arms 8781.
                    self.live_activation_market_id = self.current_market_id
                    self.wait_next_market_after_activation = True
                try:
                    hb = self._engine_post("/cap100/heartbeat", {"entrySource": ENTRY_SOURCE, "controllerVersion": VERSION, "marketId": self.current_market_id})
                    if hb.get("ok"):
                        self.last_heartbeat_ms = base.now_ms()
                except Exception:
                    # If source is not selected this is expected while safely paused.
                    if selected:
                        self.live_metrics["heartbeatFailures"] += 1
            except Exception as exc:
                self.execution_ready = False
                self.execution_block_reason = f"ENGINE_STATE_ERROR:{type(exc).__name__}"
                self.live_metrics["heartbeatFailures"] += 1
                self.last_error = f"8781 heartbeat/state: {type(exc).__name__}: {str(exc)[:350]}"

    def _deployment_live_ready(self) -> bool:
        if not self.execution_ready:
            return False
        if self.orphan_orders:
            self.live_metrics["orphanRiskBlocks"] += 1
            return False
        if self.wait_next_market_after_activation:
            if self.current_market_id is None or self.current_market_id == self.live_activation_market_id:
                return False
            self.wait_next_market_after_activation = False
        return True

    def _payload_market(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            "entrySource": ENTRY_SOURCE,
            "asset": "BTC",
            "marketId": int(base.number(snapshot.get("marketId")) or 0),
            "bucketStartSec": int(base.number(snapshot.get("bucketStartSec")) or 0),
            "windowEndMs": int(base.number(snapshot.get("windowEndMs")) or 0),
        }

    def _poll_engine_events(self, *, force: bool = False) -> list[dict[str, Any]]:
        now_mono = time.monotonic()
        if not force and now_mono - self.last_event_poll_mono < EVENT_POLL_SECONDS:
            return []
        self.last_event_poll_mono = now_mono
        try:
            payload = self._engine_get(f"/cap100/events?afterSeq={self.last_event_seq}&limit=500")
            rows = payload.get("events") if isinstance(payload.get("events"), list) else []
        except Exception as exc:
            self.live_metrics["engineHttpErrors"] += 1
            self.last_error = f"8781 event poll: {type(exc).__name__}: {str(exc)[:300]}"
            return []
        applied: list[dict[str, Any]] = []
        for event in rows:
            if not isinstance(event, dict):
                continue
            seq = int(base.number(event.get("seq")) or 0)
            if seq > self.last_event_seq:
                self.last_event_seq = seq
            self._apply_engine_event(event)
            applied.append(event)
        return applied

    def _find_local_order(self, cid: str) -> tuple[tuple[str, int] | None, base.PaperOrder | None]:
        for key, order in self.orders.items():
            if order.id == cid:
                return key, order
        order = self.orphan_orders.get(cid)
        return None, order

    def _apply_engine_event(self, event: dict[str, Any]) -> None:
        cid = str(event.get("client_order_id") or "")
        role = str(event.get("role") or "").upper()
        state = str(event.get("state") or "").upper()
        event_type = str(event.get("event_type") or "").upper()
        if event_type == "FILL_DELTA":
            shares = float(base.number(event.get("delta_shares")) or 0.0)
            usdt = float(base.number(event.get("delta_usdt")) or 0.0)
            price = float(base.number(event.get("fill_price")) or 0.0)
            side = str(event.get("side") or "").upper()
            event_ms = int(base.number(event.get("occurred_at_ms")) or base.now_ms())
            event_market_id = int(base.number(event.get("source_market_id")) or 0)
            current_market_fill = event_market_id > 0 and event_market_id == int(self.current_market_id or 0)
            if shares > base.EPS and side in {"UP", "DOWN"}:
                if price <= 0 and usdt > 0:
                    price = usdt / shares
                # Old-market/orphan fills remain fully accounted by 8781 PNL, but
                # must never mutate the new market's controller inventory/capital.
                if not current_market_fill:
                    pass
                elif role == "MAKER":
                    pre_net = self.inventory.maker_up - self.inventory.maker_down
                    pre_g = self.inventory.maker_up + self.inventory.maker_down
                    pre_pc = 2 * min(self.inventory.maker_up, self.inventory.maker_down) / pre_g if pre_g > base.EPS else 1.0
                    self.inventory.apply({"event_ms": event_ms, "role": "MAKER", "side": side, "price": price, "shares": shares})
                    self.maker_spent_notional += usdt if usdt > 0 else price * shares
                    self.current_metrics["makerFills"] += 1
                    self.run_metrics["makerFills"] += 1
                    self.live_metrics["makerFillEvents"] += 1
                    if state == "PARTIAL_FILL":
                        self.live_metrics["makerPartialFillEvents"] += 1
                    # The Echtgeld strategy recorder must mirror the same venue
                    # delta stream that drives inventory.  Engine seq is the
                    # deterministic idempotency key, so controller restart/replay
                    # cannot double-count a partial or terminal delta.
                    try:
                        self.recorder.record_order_fill_delta(
                            order_id=cid,
                            fill_id=f"{cid}:ENGINE_FILL_DELTA:{int(base.number(event.get('seq')) or 0)}",
                            filled_at_ms=event_ms,
                            fill_price=price,
                            shares=shares,
                            purpose="PASSIVE_MAKER_REAL",
                            terminal=state == "FILLED",
                            fill_state={
                                "venueConfirmed": True,
                                "engine": "8781",
                                "engineEventSeq": int(base.number(event.get("seq")) or 0),
                                "engineState": state,
                                "deltaShares": shares,
                                "deltaUsdt": usdt,
                            },
                            payload={"paperOnly": False, "executionOwner": "8781_ONLY"},
                        )
                    except KeyError:
                        # A controller restart may observe a durable engine fill for
                        # an order placement that predates this recorder instance.
                        # Inventory remains sourced from 8781; surface the audit gap
                        # instead of fabricating an order row.
                        self.last_error = f"recorder missing Maker order for venue fill: {cid}"
                    key, order = self._find_local_order(cid)
                    occupied = bool(order.occupied_before) if order else False
                    if order and key is not None and state not in {"FILLED", "CANCELED", "REJECTED"}:
                        order.shares = max(0.0, float(order.shares) - shares)
                    post_net = self.inventory.maker_up - self.inventory.maker_down
                    if occupied:
                        predom = "UP" if pre_net > base.EPS else "DOWN" if pre_net < -base.EPS else None
                        if predom == side and abs(pre_net) >= base.SHARES - base.EPS and abs(post_net) > abs(pre_net) + 1:
                            self.episode = {
                                "kind": "OVERLAP", "side": side, "risk_start_ms": event_ms,
                                "risk_pre_abs": abs(pre_net), "start_ms": event_ms, "pre_abs": abs(pre_net),
                                "start_abs": abs(post_net), "expansion": abs(post_net) - abs(pre_net),
                                "pre_pc": pre_pc, "unresolved": False,
                            }
                            self.readiness = False
                            self.current_metrics["excursions"] += 1
                            self.run_metrics["excursions"] += 1
                elif role == "TAKER":
                    self.inventory.apply({"event_ms": event_ms, "role": "TAKER", "side": side, "price": price, "shares": shares})
                    self.taker_spent_notional += usdt if usdt > 0 else price * shares
                    fee = base.taker_fee(shares, price, base.FEE_BPS)
                    self.taker_fee_spent += fee
                    self.current_metrics["takerFills"] += 1
                    self.run_metrics["takerFills"] += 1
                    self.live_metrics["takerFillEvents"] += 1
                    self.last_taker_ms = event_ms
                    pending = self.taker_pending.get(cid)
                    if pending is not None:
                        pending["filledShares"] = float(pending.get("filledShares") or 0.0) + shares
                        pending["filledUsdt"] = float(pending.get("filledUsdt") or 0.0) + usdt
                        pending["lastFillPrice"] = price
        if event_type in {"ORDER_FILLED", "ORDER_CANCELED", "ORDER_REJECTED"} or state in {"FILLED", "CANCELED", "REJECTED"}:
            key, order = self._find_local_order(cid)
            if key is not None:
                self.orders.pop(key, None)
                self.last_closed[key] = int(base.number(event.get("occurred_at_ms")) or base.now_ms())
            self.orphan_orders.pop(cid, None)
            self.pending_cancels.discard(cid)
            if role == "TAKER" and cid in self.taker_pending:
                self.taker_pending[cid]["terminalState"] = state or event_type.replace("ORDER_", "")

    def _fill_orders(self, snapshot: dict[str, Any], now: int) -> list[dict[str, Any]]:
        # Paper pass-through / queue-depletion fill proxy is forbidden in live mode.
        events = self._poll_engine_events()
        fills: list[dict[str, Any]] = []
        for event in events:
            if str(event.get("event_type") or "").upper() != "FILL_DELTA":
                continue
            if int(base.number(event.get("source_market_id")) or 0) != int(self.current_market_id or 0):
                continue
            fills.append({
                "side": event.get("side"),
                "price": event.get("fill_price"),
                "shares": event.get("delta_shares"),
                "venueConfirmed": True,
                "role": event.get("role"),
            })
        return fills

    def _cancel_order(self, key: tuple[str, int], at_ms: int, reason: str) -> None:
        order = self.orders.get(key)
        if order is None:
            return
        if order.id in self.pending_cancels:
            return
        self.pending_cancels.add(order.id)
        try:
            result = self._engine_post("/cap100/cancel", {"entrySource": ENTRY_SOURCE, "clientOrderId": order.id, "reason": reason})
            self.live_metrics["makerCancelRequested"] += 1
            if not result.get("ok") and result.get("uncertain"):
                self.entry_freeze = True
                self.entry_freeze_detail = order.id
        except Exception as exc:
            # Never forget the order. A failed cancel request means it remains live.
            self.pending_cancels.discard(order.id)
            self.live_metrics["engineHttpErrors"] += 1
            self.last_error = f"CAP100 cancel {order.id}: {type(exc).__name__}: {str(exc)[:300]}"

    def _reset_market(self, market_id: int, sampled_at: int) -> None:
        at = base.now_ms()
        # Request cancellation, but preserve every old-market order until 8781 proves
        # it terminal. New market execution is blocked while any orphan remains.
        for key, order in list(self.orders.items()):
            self._cancel_order(key, at, "MARKET_ROLLOVER")
            self.orphan_orders[order.id] = order
        self.orders.clear()
        self.pending_cancels = {cid for cid in self.pending_cancels if cid in self.orphan_orders}
        self.current_market_id = int(market_id)
        self.inventory.reset()
        self.last_closed.clear()
        self.placements.clear()
        self.episode = None
        self.readiness = False
        self.active_intervention_required = False
        self.active_intervention_reason = None
        self.last_taker_ms = -10**18
        self.maker_spent_notional = 0.0
        self.taker_spent_notional = 0.0
        self.taker_fee_spent = 0.0
        self.last_snapshot_ms = None
        self.last_eval_snapshot_ms = None
        self.last_decision = None
        self._reset_metric_counters()
        self.book_ready = self.book.reset(int(market_id), int(sampled_at))
        self.rng = base.np.random.default_rng((base.RNG_SEED * 1000003 + int(market_id)) % (2**63 - 1))
        self.residual_rng = base.np.random.default_rng((base.RNG_SEED * 3000017 + int(market_id) + 7717) % (2**63 - 1))
        if self.excluded_deployment_market_id is None:
            self.excluded_deployment_market_id = int(market_id)
            self.active = False
        else:
            self.active = True
            self.run_metrics["marketsStarted"] += 1
        if self.execution_ready and self.live_activation_market_id is not None and int(market_id) != int(self.live_activation_market_id):
            self.wait_next_market_after_activation = False

    def _add_order(self, side: str, now: int, snapshot_ns: int, decision_id: str, reason: str, p: float, snapshot: dict[str, Any], allow_stack: bool = True, bypass_guard: bool = False) -> bool:
        if not self._deployment_live_ready():
            return False
        before_keys = set(self.orders)
        before_metric = int(self.current_metrics["makerPlacements"])
        made = super()._add_order(side, now, snapshot_ns, decision_id, reason, p, snapshot, allow_stack=allow_stack, bypass_guard=bypass_guard)
        if not made:
            return False
        new_keys = [key for key in self.orders if key not in before_keys]
        if len(new_keys) != 1:
            self.last_error = "CAP100 live adapter could not identify newly planned Maker order"
            return False
        key = new_keys[0]
        order = self.orders[key]
        payload = {
            **self._payload_market(snapshot),
            "clientOrderId": order.id,
            "side": order.side,
            "price": float(order.price),
            "shares": float(order.shares),
            "decisionId": decision_id,
            "reason": reason,
            "controllerVersion": VERSION,
        }
        try:
            result = self._engine_post("/cap100/maker", payload)
        except Exception as exc:
            # HTTP uncertainty is not proof of rejection. Keep committed local risk
            # and block further entries until 8781 state/event feed resolves it.
            self.entry_freeze = True
            self.entry_freeze_detail = order.id
            self.execution_ready = False
            self.execution_block_reason = f"MAKER_HTTP_UNCERTAIN:{order.id}"
            self.live_metrics["makerUnknown"] += 1
            self.live_metrics["engineHttpErrors"] += 1
            self.last_error = f"CAP100 Maker submit uncertain {order.id}: {type(exc).__name__}: {str(exc)[:300]}"
            return True
        remote = result.get("order") if isinstance(result.get("order"), dict) else {}
        state = str(remote.get("state") or "").upper()
        if result.get("uncertain") or state in {"UNKNOWN_SUBMISSION", "CANCEL_UNKNOWN"}:
            self.entry_freeze = True
            self.entry_freeze_detail = order.id
            self.execution_ready = False
            self.execution_block_reason = f"8781_UNKNOWN_WRITE:{order.id}"
            self.live_metrics["makerUnknown"] += 1
            return True
        if not result.get("ok") or state == "REJECTED":
            self.orders.pop(key, None)
            self.last_closed[key] = now
            self.current_metrics["makerPlacements"] = max(before_metric, int(self.current_metrics["makerPlacements"]) - 1)
            self.run_metrics["makerPlacements"] = max(0, int(self.run_metrics["makerPlacements"]) - 1)
            self.live_metrics["makerSubmitRejected"] += 1
            self.recorder.record_order_cancel(order_id=order.id, cancelled_at_ms=base.now_ms(), reason=f"LIVE_ENGINE_REJECTED:{state or 'ERROR'}")
            return False
        self.live_metrics["makerSubmitAccepted"] += 1
        return True

    def _has_unresolved_taker(self) -> bool:
        terminal = {"FILLED", "REJECTED", "CANCELED", "FAILED", "EXPIRED"}
        return any(str(row.get("terminalState") or "").upper() not in terminal for row in self.taker_pending.values())

    def _record_taker(self, side: str, price: float, now: int, decision_id: str, snapshot: dict[str, Any], raw: dict[str, Any], p1: float, p3: float, ppass: float, pred_effect: str) -> bool:
        if not self._deployment_live_ready():
            return False
        # Never create a second active Taker while the first venue write/fill is
        # unresolved. Reconciliation has higher priority than active intervention.
        if self._has_unresolved_taker():
            return False
        fee = base.taker_fee(base.SHARES, price, base.FEE_BPS)
        need = float(price) * base.SHARES + float(fee)
        committed = sum(float(o.price) * float(o.shares) for o in self.orders.values())
        spent = self.maker_spent_notional + self.taker_spent_notional + self.taker_fee_spent
        if spent + committed + need > base.CAP_TOTAL_USDT + 1e-9:
            self.current_metrics["takerCapBlocks"] += 1
            self.run_metrics["takerCapBlocks"] += 1
            return False
        cid = f"{VERSION}:{self.current_market_id}:TAKER:{side}:{now}:{decision_id[-24:]}"
        max_price = min(0.99, float(price) + 0.02)
        payload = {
            **self._payload_market(snapshot),
            "clientOrderId": cid,
            "side": side,
            "price": float(price),
            "maxPrice": max_price,
            "shares": base.SHARES,
            "decisionId": decision_id,
            "predEffect": pred_effect,
            "controllerVersion": VERSION,
        }
        self.taker_pending[cid] = {"filledShares": 0.0, "filledUsdt": 0.0, "terminalState": None}
        try:
            result = self._engine_post("/cap100/taker", payload)
        except Exception as exc:
            self.entry_freeze = True
            self.entry_freeze_detail = cid
            self.execution_ready = False
            self.execution_block_reason = f"TAKER_HTTP_UNCERTAIN:{cid}"
            self.live_metrics["takerUnknown"] += 1
            self.live_metrics["engineHttpErrors"] += 1
            self.last_error = f"CAP100 Taker submit uncertain {cid}: {type(exc).__name__}: {str(exc)[:300]}"
            return False
        remote = result.get("order") if isinstance(result.get("order"), dict) else {}
        rstate = str(remote.get("state") or "").upper()
        if result.get("uncertain") or rstate == "UNKNOWN_SUBMISSION":
            self.entry_freeze = True
            self.entry_freeze_detail = cid
            self.execution_ready = False
            self.execution_block_reason = f"8781_UNKNOWN_WRITE:{cid}"
            self.live_metrics["takerUnknown"] += 1
            return False
        if not result.get("ok") or rstate == "REJECTED":
            self.taker_pending.pop(cid, None)
            self.live_metrics["takerSubmitRejected"] += 1
            return False
        self.live_metrics["takerSubmitAccepted"] += 1
        # Parent strategy treats True as "actually filled" and clears its repair
        # episode. Therefore wait for venue-confirmed FILL_DELTA instead of treating
        # placement acknowledgement as a fill.
        deadline = time.monotonic() + TAKER_CONFIRM_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            self._poll_engine_events(force=True)
            pending = self.taker_pending.get(cid) or {}
            if float(pending.get("filledShares") or 0.0) > base.EPS:
                fill_price = float(pending.get("lastFillPrice") or price)
                self.recorder.record_taker_fill(
                    fill_id=f"{cid}:VENUE_FILL", strategy_version=VERSION,
                    market_id=int(self.current_market_id), decision_id=decision_id,
                    purpose="PROMOTED_ACTIVE_INTERVENTION_CAP100_REAL", side=side,
                    price=fill_price, shares=float(pending.get("filledShares") or 0.0),
                    filled_at_ms=base.now_ms(),
                    decision_state={"version": VERSION, "pTaker1s": p1, "pTaker3s": p3, "pPassiveRepair": ppass, "predEffect": pred_effect, "rawState": raw, "capital": self._capital_state(), "paperOnly": False, "targetDataUsed": False},
                    fill_state={"snapshot": snapshot, "venueConfirmed": True, "engine": "8781"},
                    payload={"feeBps": base.FEE_BPS, "paperOnly": False, "liveOrdersAffected": True, "capitalCapUsdt": base.CAP_TOTAL_USDT},
                )
                return True
            if pending.get("terminalState") in {"REJECTED", "CANCELED", "FAILED", "EXPIRED"}:
                return False
            time.sleep(0.08)
        # No fake fill. Engine owns reconciliation; strategy keeps its episode/readiness.
        return False

    def _capital_state(self) -> dict[str, float]:
        committed = sum(float(o.price) * float(o.shares) for o in self.orders.values())
        orphan_committed = sum(float(o.price) * float(o.shares) for o in self.orphan_orders.values())
        spent = self.maker_spent_notional + self.taker_spent_notional + self.taker_fee_spent
        return {
            "totalCapUsdt": base.CAP_TOTAL_USDT,
            "makerBudgetUsdt": base.CAP_MAKER_BUDGET_USDT,
            "takerReserveUsdt": base.CAP_TAKER_RESERVE_USDT,
            "makerSpentUsdt": self.maker_spent_notional,
            "makerCommittedUsdt": committed,
            "orphanCommittedUsdt": orphan_committed,
            "takerSpentUsdt": self.taker_spent_notional,
            "takerFeesUsdt": self.taker_fee_spent,
            "spentUsdt": spent,
            "worstCaseCommittedUsdt": spent + committed + orphan_committed,
            "totalRemainingUsdt": max(0.0, base.CAP_TOTAL_USDT - spent - committed - orphan_committed),
            "makerRemainingUsdt": max(0.0, base.CAP_MAKER_BUDGET_USDT - self.maker_spent_notional - committed - orphan_committed),
        }

    def _rewrite_live_decision_trace(self) -> None:
        trace = self.last_decision
        if not isinstance(trace, dict):
            return
        trace["version"] = VERSION
        trace["paperOnly"] = False
        trace["liveOrdersAffected"] = bool(self._deployment_live_ready())
        trace["fillProxy"] = "8781_VENUE_CONFIRMED_FILL_DELTA"
        trace["executionOwner"] = "8781_ONLY"
        decision_id = str(trace.get("decisionId") or "")
        if not decision_id:
            return
        try:
            with self.recorder.lock:
                row = self.recorder.db.execute("SELECT payload_json,supporting_reasons_json FROM our_decisions WHERE decision_id=?", (decision_id,)).fetchone()
                if row is None:
                    return
                import json
                payload = json.loads(str(row["payload_json"] or "{}"))
                support = json.loads(str(row["supporting_reasons_json"] or "{}"))
                if not isinstance(payload, dict): payload = {}
                if not isinstance(support, dict): support = {}
                payload.update({"version":VERSION,"paperOnly":False,"liveOrdersAffected":bool(self._deployment_live_ready()),"fillProxy":"8781_VENUE_CONFIRMED_FILL_DELTA","executionOwner":"8781_ONLY"})
                support.update({"fillProxy":"8781_VENUE_CONFIRMED_FILL_DELTA","executionOwner":"8781_ONLY"})
                self.recorder.db.execute("UPDATE our_decisions SET strategy_version=?,payload_json=?,supporting_reasons_json=? WHERE decision_id=?", (VERSION,json.dumps(payload,separators=(",",":"),default=str),json.dumps(support,separators=(",",":"),default=str),decision_id))
                self.recorder.db.commit()
        except Exception as exc:
            self.last_error = f"live recorder trace rewrite: {type(exc).__name__}: {str(exc)[:250]}"

    def _active_intervention_required(self) -> bool:
        return bool(self.active_intervention_required)

    def _on_active_intervention_required(self, reason: str, now: int) -> None:
        self.active_intervention_required = True
        self.active_intervention_reason = str(reason)

    def _on_active_intervention_satisfied(self) -> None:
        self.active_intervention_required = False
        self.active_intervention_reason = None

    def _step(self, snapshot: dict[str, Any]) -> None:
        # Required-intervention scheduling now happens inside the inherited
        # arbitration point, before passive repair / normal Maker.  The hooks
        # above are false/no-op in frozen 8786, so its semantics stay unchanged.
        super()._step(snapshot)
        trace = self.last_decision if isinstance(self.last_decision, dict) else None
        if trace is not None:
            trace["activeInterventionRequired"] = bool(self.active_intervention_required)
            trace["activeInterventionReason"] = self.active_intervention_reason
        self._rewrite_live_decision_trace()

    def snapshot(self) -> dict[str, Any]:
        result = super().snapshot()
        result.update(
            version=VERSION,
            candidate="PROMOTED_OWNSTATE_V4_R2_CAP100_KEEP18_REAL_ADAPTER",
            paperOnly=False,
            liveOrdersAffected=True,
            executionOwner="8781_ONLY",
            entrySource=ENTRY_SOURCE,
        )
        last = result.get("lastDecision")
        if isinstance(last, dict):
            last["paperOnly"] = False
            last["liveOrdersAffected"] = bool(self._deployment_live_ready())
            last["fillProxy"] = "8781_VENUE_CONFIRMED_FILL_DELTA"
        result["liveExecution"] = {
            "engineUrl": ENGINE_URL,
            "executionReady": self.execution_ready,
            "deploymentLiveReady": self._deployment_live_ready(),
            "blockReason": self.execution_block_reason,
            "entryFreeze": self.entry_freeze,
            "entryFreezeDetail": self.entry_freeze_detail,
            "waitNextMarketAfterActivation": self.wait_next_market_after_activation,
            "activationMarketId": self.live_activation_market_id,
            "lastHeartbeatMs": self.last_heartbeat_ms,
            "lastEngineStateMs": self.last_engine_state_ms,
            "lastEventSeq": self.last_event_seq,
            "pendingCancels": sorted(self.pending_cancels),
            "orphanOrders": [o.__dict__ for o in self.orphan_orders.values()],
            "takerPending": dict(self.taker_pending),
            "engineState": self.engine_state,
            "metrics": dict(self.live_metrics),
            "safety": {
                "paperQueueClearFillDisabled": True,
                "inventoryFromVenueFillOnly": True,
                "makerCancelWaitsForTerminalConfirmation": True,
                "marketRolloverPreservesUnconfirmedOldOrders": True,
                "newMarketBlockedByOrphanRisk": True,
                "takerPlacementAckNeverEqualsFill": True,
                "httpWriteUncertaintyFreezesEntries": True,
                "midMarketArmWaitsNextMarket": True,
                "8781IsOnlyCredentialOwner": True,
            },
        }
        result["capital"] = self._capital_state()
        result["policy"] = dict(result.get("policy") or {})
        result["policy"]["makerFill"] = "8781 venue-confirmed cumulative FILL_DELTA only; paper QUEUECLEAR_PASS forbidden"
        result["policy"]["execution"] = "8787 decides; 8781 quotes/places/cancels/reconciles; no venue credentials in 8787"
        return result


class Handler(BaseHTTPRequestHandler):
    runtime: UnifiedControllerCap100EchtgeldV1

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
        path = self.path.split("?", 1)[0]
        if path in {"/", "/state", "/health"}:
            payload = self.runtime.snapshot()
            if path == "/health":
                live = payload.get("liveExecution") or {}
                payload = {
                    "ok": bool(payload.get("sourceReady")) and self.runtime.last_error is None,
                    "version": VERSION,
                    "paperOnly": False,
                    "liveOrdersAffected": True,
                    "executionReady": bool(live.get("executionReady")),
                    "deploymentLiveReady": bool(live.get("deploymentLiveReady")),
                    "blockReason": live.get("blockReason"),
                    "entryFreeze": bool(live.get("entryFreeze")),
                    "orphanOrders": len(live.get("orphanOrders") or []),
                    "lastError": self.runtime.last_error,
                }
            self._write(payload)
        else:
            self._write({"ok": False, "error": "not found"}, 404)


def main() -> int:
    runtime = UnifiedControllerCap100EchtgeldV1()
    runtime.start()
    handler = type("UnifiedControllerCap100EchtgeldV1Handler", (Handler,), {"runtime": runtime})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}/state; engine={ENGINE_URL}; source={ENTRY_SOURCE}; "
        "paperFillProxy=false; venueConfirmedFillOnly=true; live activation requires 8781 arm + next market boundary",
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
