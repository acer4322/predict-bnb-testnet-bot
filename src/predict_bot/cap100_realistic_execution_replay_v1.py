from __future__ import annotations

import json
import math
import os
import sqlite3
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from . import unified_controller_cap100_shadow_v1 as base

ROOT = Path(__file__).resolve().parents[2]
EXEC_MODEL = ROOT / "data" / "research" / "target_maker_taker_coordination_big_v1" / "target_maker_execution_hazard_v3_runtime_safe.joblib"
LIVE_RECORDER_DB = ROOT / "data" / "strategy_cap100_echtgeld_v1.db"


class NullRecorder:
    path = Path(":memory:")
    def close(self) -> None: return
    def record_order_cancel(self, **_kw: Any) -> None: return
    def record_order_fill(self, **_kw: Any) -> None: return
    def record_order_placement(self, **_kw: Any) -> None: return
    def record_taker_fill(self, **_kw: Any) -> None: return
    def record_decision(self, **_kw: Any) -> None: return


class RealisticExecutionReplayController(base.UnifiedControllerCap100ShadowV1):
    """Offline CAP100 replay with stochastic, execution-aware Maker fills.

    This does not change CAP100 Maker/Taker decision models.  It replaces only the
    paper QUEUECLEAR_PASS fill assumption with an ACK-latency + public-evidence +
    runtime-safe execution-hazard simulator.  It is intentionally research-only.
    """

    def __init__(
        self,
        *,
        execution_seed: int = 20260820,
        ack_latency_ms: int = 650,
        hazard_scale: float = 1.0,
        partial_boost: float = 5.0,
    ) -> None:
        _live_flag = os.environ.get("PREDICT_LIVE_ENABLED")
        os.environ["PREDICT_LIVE_ENABLED"] = "false"
        try:
            super().__init__()
        finally:
            if _live_flag is None:
                os.environ.pop("PREDICT_LIVE_ENABLED", None)
            else:
                os.environ["PREDICT_LIVE_ENABLED"] = _live_flag
        try:
            self.recorder.close()
        except Exception:
            pass
        self.recorder = NullRecorder()
        self.exec_bundle = joblib.load(EXEC_MODEL)
        self.exec_rng = np.random.default_rng(int(execution_seed))
        self.ack_latency_ms = int(ack_latency_ms)
        self.hazard_scale = float(hazard_scale)
        self.partial_boost = float(partial_boost)
        self.exec_state: dict[str, dict[str, Any]] = {}
        self.sim_fills: list[dict[str, Any]] = []
        self.sim_takers: list[dict[str, Any]] = []
        self.active_intervention_required = False
        self.active_intervention_reason: str | None = None
        # Fractions are deliberately based on the first Echtgeld calibration market's
        # observed first partial-fill shapes, plus full-fill mass.  V1 does not fit
        # these to Taker timing.
        self.partial_fractions = np.asarray([0.10, 0.28, 0.65, 0.75, 0.81, 1.0, 1.0], dtype=float)

    def _reset_market(self, market_id: int, sampled_at: int) -> None:
        super()._reset_market(market_id, sampled_at)
        self.exec_state.clear()
        self.sim_fills.clear()
        self.sim_takers.clear()
        self.active_intervention_required = False
        self.active_intervention_reason = None

    def _active_intervention_required(self) -> bool:
        return bool(self.active_intervention_required)

    def _on_active_intervention_required(self, reason: str, now: int) -> None:
        self.active_intervention_required = True
        self.active_intervention_reason = str(reason)

    def _on_active_intervention_satisfied(self) -> None:
        self.active_intervention_required = False
        self.active_intervention_reason = None

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
        before = {o.id for o in self.orders.values()}
        made = super()._add_order(side, now, snapshot_ns, decision_id, reason, p, snapshot, allow_stack=allow_stack, bypass_guard=bypass_guard)
        if not made:
            return False
        for order in self.orders.values():
            if order.id in before:
                continue
            self.exec_state[order.id] = {
                "ack_ms": int(now) + self.ack_latency_ms,
                "queue_initialized": False,
                "queue_ahead": None,
                "cum_depletion": 0.0,
                "cum_replenish": 0.0,
                "depletion_events": [],
                "replenish_events": [],
                "level_zero_seen": False,
                "level_zero_events": [],
                "last_depletion_ms": None,
                "last_eval_ms": int(now),
                "filled_total": 0.0,
                "requested_total": float(order.shares),
                "partial": False,
            }
        return True

    def _cancel_order(self, key: tuple[str, int], at_ms: int, reason: str) -> None:
        order = self.orders.get(key)
        if order is not None:
            self.exec_state.pop(order.id, None)
        super()._cancel_order(key, at_ms, reason)

    def _update_depletion(self, changes: dict[str, Any], source_ms: int) -> None:
        # Called by PublicBookTailer immediately before each change batch is applied,
        # so self.book.book is the pre-change state.
        for order in list(self.orders.values()):
            st = self.exec_state.get(order.id)
            if st is None or int(source_ms) < int(st["ack_ms"]):
                continue
            if not st["queue_initialized"]:
                st["queue_ahead"] = float(self.book.book.get(order.native_side, {}).get(order.native_price, 0.0))
                st["queue_initialized"] = True
            for ch in changes.get(order.native_side, []) or []:
                try:
                    px = float(ch.get("price"))
                    delta = float(ch.get("delta", 0.0))
                    after = float(ch.get("after", 0.0))
                except Exception:
                    continue
                if abs(px - order.native_price) > 1e-9:
                    continue
                if delta < 0:
                    qty = -delta
                    st["cum_depletion"] += qty
                    st["depletion_events"].append((int(source_ms), qty))
                    st["last_depletion_ms"] = int(source_ms)
                elif delta > 0:
                    st["cum_replenish"] += delta
                    st["replenish_events"].append((int(source_ms), delta))
                if after <= 1e-12:
                    st["level_zero_seen"] = True
                    st["level_zero_events"].append(int(source_ms))

    @staticmethod
    def _recent_qty(events: list[tuple[int, float]], now: int, window_ms: int = 1000) -> float:
        return float(sum(q for t, q in events if int(t) > int(now) - int(window_ms)))

    def _execution_features(self, order: base.PaperOrder, now: int, snapshot: dict[str, Any]) -> dict[str, Any] | None:
        st = self.exec_state.get(order.id)
        if st is None or int(now) < int(st["ack_ms"]):
            return None
        if not st["queue_initialized"]:
            st["queue_ahead"] = float(self.book.book.get(order.native_side, {}).get(order.native_price, 0.0))
            st["queue_initialized"] = True
        inv = self.inventory.features(now)
        combined_net = float(inv.pop("_combined_net"))
        dom = "UP" if combined_net > base.EPS else "DOWN" if combined_net < -base.EPS else None
        bf = base.outcome_book(self.book.book, dom)
        if bf is None:
            return None
        current_bid = float(bf["up_bid"] if order.side == "UP" else bf["down_bid"])
        current_ask = float(bf["up_ask"] if order.side == "UP" else bf["down_ask"])
        current_depth = float(bf["up_bid_depth"] if order.side == "UP" else bf["down_bid_depth"])
        dep1 = self._recent_qty(st["depletion_events"], now)
        rep1 = self._recent_qty(st["replenish_events"], now)
        zero1 = any(int(t) > int(now) - 1000 for t in st["level_zero_events"])
        last_dep = st.get("last_depletion_ms")
        raw = {
            "seconds_left": float(base.number(snapshot.get("secondsLeft")) or math.nan),
            **inv,
            **bf,
            "side_is_up": float(order.side == "UP"),
            "order_age_ms": float(now - int(st["ack_ms"])),
            "quote_price": float(order.price),
            "quote_offset_ticks": float((current_bid - float(order.price)) / base.GRID),
            "cum_depletion_qty": float(st["cum_depletion"]),
            "cum_replenish_qty": float(st["cum_replenish"]),
            "depletion_last1s_qty": dep1,
            "replenish_last1s_qty": rep1,
            "level_zero_seen": float(bool(st["level_zero_seen"])),
            "level_zero_last1s": float(bool(zero1)),
            "pass_through_now": float(current_bid < float(order.price) - base.EPS),
            "ask_touch_now": float(current_ask <= float(order.price) + base.EPS),
            "current_bid": current_bid,
            "current_ask": current_ask,
            "current_spread_ticks": float((current_ask - current_bid) / base.GRID),
            "current_bid_depth": current_depth,
            "time_since_last_depletion_ms": float(now - int(last_dep)) if last_dep is not None else math.nan,
        }
        return raw

    def _apply_sim_fill(self, key: tuple[str, int], order: base.PaperOrder, now: int, qty: float, hazard: float) -> dict[str, Any]:
        st = self.exec_state[order.id]
        qty = min(float(order.shares), max(0.0, float(qty)))
        pre_net = self.inventory.maker_up - self.inventory.maker_down
        pre_g = self.inventory.maker_up + self.inventory.maker_down
        pre_pc = 2 * min(self.inventory.maker_up, self.inventory.maker_down) / pre_g if pre_g > base.EPS else 1.0
        self.inventory.apply({"event_ms": int(now), "role": "MAKER", "side": order.side, "price": float(order.price), "shares": qty})
        self.maker_spent_notional += float(order.price) * qty
        self.current_metrics["makerFills"] += 1
        self.run_metrics["makerFills"] += 1
        st["filled_total"] += qty
        remaining = max(0.0, float(order.shares) - qty)
        terminal = remaining <= base.EPS
        if terminal:
            self.orders.pop(key, None)
            self.last_closed[key] = int(now)
            self.exec_state.pop(order.id, None)
        else:
            order.shares = remaining
            st["partial"] = True
        post_net = self.inventory.maker_up - self.inventory.maker_down
        if order.occupied_before:
            predom = "UP" if pre_net > base.EPS else "DOWN" if pre_net < -base.EPS else None
            if predom == order.side and abs(pre_net) >= base.SHARES - base.EPS and abs(post_net) > abs(pre_net) + 1:
                self.episode = {
                    "kind": "OVERLAP", "side": order.side, "risk_start_ms": int(now),
                    "risk_pre_abs": abs(pre_net), "start_ms": int(now), "pre_abs": abs(pre_net),
                    "start_abs": abs(post_net), "expansion": abs(post_net) - abs(pre_net),
                    "pre_pc": pre_pc, "unresolved": False,
                }
                self.readiness = False
                self.current_metrics["excursions"] += 1
                self.run_metrics["excursions"] += 1
        row = {
            "orderId": order.id,
            "atMs": int(now),
            "side": order.side,
            "price": float(order.price),
            "shares": qty,
            "terminal": terminal,
            "hazard": float(hazard),
            "filledTotal": float(st.get("filled_total", qty)),
        }
        self.sim_fills.append(row)
        return row

    def _fill_orders(self, snapshot: dict[str, Any], now: int) -> list[dict[str, Any]]:
        fills: list[dict[str, Any]] = []
        for key, order in list(self.orders.items()):
            raw = self._execution_features(order, now, snapshot)
            if raw is None:
                continue
            st = self.exec_state.get(order.id)
            if st is None:
                continue
            # Public execution evidence gate.  We intentionally do NOT treat mere
            # pass-through as a deterministic full fill.
            evidence = bool(
                raw["depletion_last1s_qty"] > base.EPS
                or raw["level_zero_last1s"] > 0.5
                or raw["ask_touch_now"] > 0.5
                or (raw["pass_through_now"] > 0.5 and raw["cum_depletion_qty"] > base.EPS)
            )
            last_eval = int(st.get("last_eval_ms") or now)
            dt_ms = max(1, int(now) - last_eval)
            st["last_eval_ms"] = int(now)
            if not evidence:
                continue
            p1 = float(base._binary_prob(self.exec_bundle, raw))
            scale = self.hazard_scale * (self.partial_boost if st.get("partial") else 1.0)
            p1 = max(0.0, min(0.999999, p1 * scale))
            padj = 1.0 - (1.0 - p1) ** (dt_ms / 1000.0)
            if self.exec_rng.random() >= padj:
                continue
            if st.get("partial"):
                qty = float(order.shares)
            else:
                frac = float(self.exec_rng.choice(self.partial_fractions))
                qty = float(order.shares) * frac
                # Venue quantities in the calibration market were effectively 0.01-share granular.
                qty = max(0.01, round(qty, 2))
            row = self._apply_sim_fill(key, order, now, qty, padj)
            fills.append({"side":row["side"],"price":row["price"],"shares":row["shares"],"simulated":True,"hazard":row["hazard"]})
        return fills

    def _record_taker(self, side: str, price: float, now: int, decision_id: str, snapshot: dict[str, Any], raw: dict[str, Any], p1: float, p3: float, ppass: float, pred_effect: str) -> bool:
        ok = super()._record_taker(side, price, now, decision_id, snapshot, raw, p1, p3, ppass, pred_effect)
        if ok:
            self.sim_takers.append({"atMs":int(now),"side":side,"price":float(price),"shares":base.SHARES,"decisionId":decision_id})
            self._on_active_intervention_satisfied()
        return ok


def load_live_public_snapshots(market_id: int) -> list[dict[str, Any]]:
    db = sqlite3.connect(str(LIVE_RECORDER_DB))
    db.row_factory = sqlite3.Row
    rows = db.execute(
        """select public_state_json from our_decisions
           where market_id=? order by decision_ms""",
        (int(market_id),),
    ).fetchall()
    db.close()
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in rows:
        snap = json.loads(str(row["public_state_json"] or "{}"))
        ms = int(base.number(snap.get("sampledAtMs")) or 0)
        if ms <= 0 or ms in seen:
            continue
        seen.add(ms)
        out.append(snap)
    out.sort(key=lambda x: int(base.number(x.get("sampledAtMs")) or 0))
    return out


def run_replay(
    market_id: int = 1513668,
    *,
    execution_seed: int = 20260820,
    ack_latency_ms: int = 650,
    hazard_scale: float = 1.0,
    partial_boost: float = 5.0,
) -> dict[str, Any]:
    snaps = load_live_public_snapshots(int(market_id))
    if not snaps:
        raise RuntimeError(f"no live public snapshots for market {market_id}")
    ctl = RealisticExecutionReplayController(
        execution_seed=execution_seed,
        ack_latency_ms=ack_latency_ms,
        hazard_scale=hazard_scale,
        partial_boost=partial_boost,
    )
    # Bypass deployment-boundary exclusion for an explicit historical replay.
    ctl.excluded_deployment_market_id = -1
    try:
        for snap in snaps:
            ctl._step(dict(snap))
        start_ms = int(base.number(snaps[0].get("bucketStartSec")) or 0) * 1000
        first_taker = ctl.sim_takers[0] if ctl.sim_takers else None
        return {
            "marketId": int(market_id),
            "snapshots": len(snaps),
            "executionSeed": int(execution_seed),
            "ackLatencyMs": int(ack_latency_ms),
            "hazardScale": float(hazard_scale),
            "partialBoost": float(partial_boost),
            "makerPlacements": int(ctl.run_metrics.get("makerPlacements", 0)),
            "makerFillEvents": len(ctl.sim_fills),
            "makerFilledShares": float(sum(float(x["shares"]) for x in ctl.sim_fills)),
            "takerCount": len(ctl.sim_takers),
            "firstTakerMs": int(first_taker["atMs"]) if first_taker else None,
            "firstTakerElapsedSec": ((int(first_taker["atMs"]) - start_ms) / 1000.0) if first_taker else None,
            "firstTakerSide": first_taker.get("side") if first_taker else None,
            "firstTakerPrice": first_taker.get("price") if first_taker else None,
            "fills": list(ctl.sim_fills),
            "takers": list(ctl.sim_takers),
            "finalCapital": ctl._capital_state(),
            "finalPortfolio": {k:v for k,v in ctl.inventory.features(int(base.number(snaps[-1].get("sampledAtMs")) or 0)).items() if k != "_combined_net"},
        }
    finally:
        ctl.stop()


if __name__ == "__main__":
    print(json.dumps(run_replay(), ensure_ascii=False, indent=2, default=str))
