from __future__ import annotations

import argparse
import copy
import json
import math
import sqlite3
import sys

import joblib
import pandas as pd
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from src.predict_bot import unified_controller_paper_v2 as mod

STRATEGY_DB = ROOT / "data" / "strategy_target_compare_v1.db"
OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
VERSION = mod.VERSION
EPS = 1e-9


class NoopRecorder:
    path = "OFFLINE_NOOP"

    def __getattr__(self, _name: str):
        return lambda *args, **kwargs: None


class HftBookAdapter:
    """Decision book uses the frozen R2 source-timestamp view; HFT uses receipt-time execution tape."""

    def __init__(self, market_id: int, entry_latency_ms: int, response_latency_ms: int, queue_model: str, trade_offset: str):
        self.market_id = int(market_id)
        self.events, self.update_times, self.meta = tape_v1.build_archive_events(self.market_id, trade_offset=trade_offset)
        self.bt = ex.new_bt(self.events, entry_latency_ms=entry_latency_ms, response_latency_ms=response_latency_ms, queue_model=queue_model)
        ex.initialize_bt(self.bt)
        self.decision_book = mod.PublicBookTailer(ex.BOOK_DB)
        self.book: dict[str, dict[float, float]] = {"bids": {}, "asks": {}}
        self.last_source_ms: int | None = None
        self.order_num_by_key: dict[tuple[str, int], int] = {}
        self.prev_cum: dict[int, float] = defaultdict(float)
        self.next_num = 1

    def close(self) -> None:
        try:
            self.decision_book.close()
        except Exception:
            pass
        self.bt.close()

    def reset(self, market_id: int, up_to_ms: int) -> bool:
        if int(market_id) != self.market_id:
            raise RuntimeError(f"adapter market mismatch {market_id} != {self.market_id}")
        ex.advance_to(self.bt, int(up_to_ms))
        ok = self.decision_book.reset(int(market_id), int(up_to_ms))
        self.book = self.decision_book.book
        self.last_source_ms = self.decision_book.last_source_ms
        return bool(ok and self.book["bids"] and self.book["asks"])

    def advance(self, market_id: int, up_to_ms: int, on_changes: Any = None) -> bool:
        if int(market_id) != self.market_id:
            return False
        ex.advance_to(self.bt, int(up_to_ms))
        ok = self.decision_book.advance(int(market_id), int(up_to_ms), on_changes)
        self.book = self.decision_book.book
        self.last_source_ms = self.decision_book.last_source_ms
        return bool(ok and self.book["bids"] and self.book["asks"])

    def submit(self, key: tuple[str, int], order: mod.PaperOrder) -> tuple[int, int]:
        num = self.next_num
        self.next_num += 1
        rc = ex.submit_native(self.bt, num, order.side, order.price, order.shares)
        self.order_num_by_key[key] = num
        self.prev_cum[num] = 0.0
        return num, rc

    def cancel(self, key: tuple[str, int]) -> None:
        num = self.order_num_by_key.get(key)
        if num is None:
            return
        cur = self.bt.orders(0).get(num)
        if cur is not None and int(cur.status) in {int(ex.NEW), int(ex.PARTIALLY_FILLED)} and bool(cur.cancellable):
            try:
                self.bt.cancel(0, num, False)
            except Exception:
                pass

    def snap(self, key: tuple[str, int]) -> dict[str, Any]:
        num = self.order_num_by_key.get(key)
        return ex.order_snapshot(self.bt, num) if num is not None else {"exists": False, "status": "NONE", "cumExecQty": 0.0, "leavesQty": None}

    def snap_num(self, num: int) -> dict[str, Any]:
        return ex.order_snapshot(self.bt, int(num))

    def submit_taker(self, side: str, max_price: float, shares: float) -> tuple[int, int]:
        num = self.next_num
        self.next_num += 1
        native_side, native_price = ex.native_order(side, max_price)
        if native_side == "BUY":
            rc = int(self.bt.submit_buy_order(0, num, native_price, float(shares), ex.hbt.GTC, ex.LIMIT, False))
        else:
            rc = int(self.bt.submit_sell_order(0, num, native_price, float(shares), ex.hbt.GTC, ex.LIMIT, False))
        return num, rc


def jload(v: Any) -> dict[str, Any]:
    try:
        out = json.loads(v) if v else {}
    except Exception:
        return {}
    return out if isinstance(out, dict) else {}


def load_public_snapshots(market_id: int) -> list[dict[str, Any]]:
    con = sqlite3.connect(STRATEGY_DB)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """SELECT decision_ms,public_state_json FROM our_decisions
               WHERE strategy_version=? AND market_id=? ORDER BY decision_ms,decision_id""",
            (VERSION, int(market_id)),
        ).fetchall()
    finally:
        con.close()
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in rows:
        s = jload(row["public_state_json"])
        if not s:
            continue
        sampled = int(s.get("sampledAtMs") or row["decision_ms"])
        if sampled in seen:
            continue
        seen.add(sampled)
        s["sampledAtMs"] = sampled
        if not s.get("timestampNs"):
            s["timestampNs"] = sampled * 1_000_000
        out.append(s)
    return out


def load_reference_paper(market_id: int) -> dict[str, Any]:
    con = sqlite3.connect(STRATEGY_DB)
    con.row_factory = sqlite3.Row
    try:
        orders = [dict(r) for r in con.execute(
            """SELECT side,price,shares,placed_at_ms,status,filled_at_ms,cancelled_at_ms
               FROM our_orders WHERE strategy_version=? AND market_id=? AND channel='MAKER'
               ORDER BY placed_at_ms,order_id""",
            (VERSION, int(market_id)),
        )]
        takers = [dict(r) for r in con.execute(
            """SELECT side,price,shares,filled_at_ms,decision_id FROM our_fills
               WHERE strategy_version=? AND market_id=? AND channel='TAKER'
               ORDER BY filled_at_ms,fill_id""",
            (VERSION, int(market_id)),
        )]
    finally:
        con.close()
    return {"orders": orders, "takers": takers}


def new_controller(adapter: HftBookAdapter) -> mod.UnifiedControllerPaperV2:
    c = object.__new__(mod.UnifiedControllerPaperV2)
    c.recorder = NoopRecorder()
    c.book = adapter
    c.models = mod._load_artifacts()
    c.p_maker_up_base = mod._fast_binary(c.models["maker_up"])
    c.p_maker_down_base = mod._fast_binary(c.models["maker_down"])
    c.p_taker1 = mod._fast_binary(c.models["taker_1s"])
    c.p_taker3 = mod._fast_binary(c.models["taker_3s"])
    c.current_market_id = None
    c.excluded_deployment_market_id = 0
    c.active = True
    c.source_ready = True
    c.book_ready = True
    c.source_wait_reason = None
    c.source_health = {}
    c.last_snapshot_ms = None
    c.last_eval_snapshot_ms = None
    c.last_loop_ms = None
    c.last_error = None
    c.last_decision = None
    c.sequence = 0
    c.inventory = mod.Inventory()
    c.orders = {}
    c.last_closed = {}
    c.placements = []
    c.episode = None
    c.readiness = False
    c.last_taker_ms = -10**18
    import numpy as np
    c.rng = np.random.default_rng(mod.RNG_SEED)
    c.residual_rng = np.random.default_rng(mod.RNG_SEED + 7717)
    c.current_metrics = {}
    c.run_metrics = {
        "marketsStarted": 0, "decisions": 0, "makerPlacements": 0, "makerFills": 0,
        "takerFills": 0, "excursions": 0, "passivePrioritySteps": 0,
        "unresolvedGuardEntries": 0, "residualWakes": 0, "residualRecoveries": 0,
        "burstPlacements": 0,
    }
    c._reset_metric_counters()
    c.taker_fee_spent = 0.0
    return c


def _portfolio(c: mod.UnifiedControllerPaperV2, now: int) -> dict[str, Any]:
    out = c.inventory.features(now)
    out.pop("_combined_net", None)
    return out


def _order_state(c: mod.UnifiedControllerPaperV2, a: HftBookAdapter, key: tuple[str, int], order: mod.PaperOrder, now: int, context: str, decision_id: str | None = None) -> dict[str, Any]:
    snap = a.snap(key)
    bf = mod.outcome_book(c.book.book, None) or {}
    bid = bf.get("up_bid") if order.side == "UP" else bf.get("down_bid")
    ask = bf.get("up_ask") if order.side == "UP" else bf.get("down_ask")
    opp = "DOWN" if order.side == "UP" else "UP"
    same = [o for o in c.orders.values() if o.side == order.side]
    other = [o for o in c.orders.values() if o.side == opp]
    cum = float(snap.get("cumExecQty") or 0.0)
    leaves = snap.get("leavesQty")
    if leaves is None:
        leaves = max(0.0, float(order.shares))
    quote_offset = ((float(bid) - float(order.price)) / mod.GRID) if bid is not None else math.nan
    return {
        "marketId": int(c.current_market_id or a.market_id),
        "checkpointMs": int(now),
        "decisionId": decision_id,
        "context": context,
        "orderId": order.id,
        "side": order.side,
        "price": float(order.price),
        "placedAtMs": int(order.placed_at_ms),
        "orderAgeMs": float(now - int(order.placed_at_ms)),
        "hftStatus": str(snap.get("status") or "NONE"),
        "originalQty": float(snap.get("qty") or (cum + float(leaves))),
        "cumExecQty": cum,
        "remainingQty": float(leaves),
        "partialFillRatio": (cum / float(snap.get("qty"))) if snap.get("qty") not in {None, 0} else 0.0,
        "activeSameCount": len(same),
        "activeOppCount": len(other),
        "quoteOffsetTicks": float(quote_offset),
        "currentBid": bid,
        "currentAsk": ask,
        "currentSpreadTicks": ((float(ask) - float(bid)) / mod.GRID) if bid is not None and ask is not None else math.nan,
        "initialDepth": float(order.initial_depth),
        "publicCumDepletion": float(order.cum_depletion),
        "publicAnyDepletion": bool(order.any_depletion),
        "portfolio": _portfolio(c, now),
    }


def _pending_skill_frame(row: dict[str, Any], feature_names: list[str]) -> pd.DataFrame:
    port = row.get("portfolio") if isinstance(row.get("portfolio"), dict) else {}
    cum = float(row.get("cumExecQty") or 0.0)
    rem = float(row.get("remainingQty") or 0.0)
    total = cum + rem
    init_depth = float(row.get("initialDepth") or 0.0)
    depletion = float(row.get("publicCumDepletion") or 0.0)
    status = str(row.get("hftStatus") or "NONE")
    vals: dict[str, Any] = {
        "side_is_up": float(str(row.get("side") or "").upper() == "UP"),
        "order_age_ms": float(row.get("orderAgeMs") or 0.0),
        "quote_price": float(row.get("price") or math.nan),
        "status_none": float(status == "NONE"),
        "status_new": float(status == "NEW"),
        "status_partial": float(status == "PARTIALLY_FILLED"),
        "cum_exec_qty": cum,
        "remaining_qty": rem,
        "remaining_ratio": rem / total if total > EPS else math.nan,
        "partial_fill_ratio": float(row.get("partialFillRatio") or 0.0),
        "active_same_count": float(row.get("activeSameCount") or 0.0),
        "active_opp_count": float(row.get("activeOppCount") or 0.0),
        "quote_offset_ticks": float(row.get("quoteOffsetTicks")) if row.get("quoteOffsetTicks") is not None else math.nan,
        "current_bid": float(row.get("currentBid")) if row.get("currentBid") is not None else math.nan,
        "current_ask": float(row.get("currentAsk")) if row.get("currentAsk") is not None else math.nan,
        "current_spread_ticks": float(row.get("currentSpreadTicks")) if row.get("currentSpreadTicks") is not None else math.nan,
        "initial_depth": init_depth,
        "public_cum_depletion": depletion,
        "public_depletion_ratio": depletion / init_depth if init_depth > EPS else math.nan,
        "public_any_depletion": float(bool(row.get("publicAnyDepletion"))),
    }
    vals.update(port)
    return pd.DataFrame([{f: vals.get(f, math.nan) for f in feature_names}], columns=feature_names)




def _artifact_ref_path(ref: str | Path) -> Path:
    p = Path(ref)
    if p.exists():
        return p
    q = OUT_DIR / p.name
    if q.exists():
        return q
    return p


def _add_intervention_frame(
    row: dict[str, Any],
    reason: str,
    adapter_features: list[str],
    fill_skill: dict[str, Any],
    pending_skill_expert: dict[str, Any],
) -> pd.DataFrame:
    fill_features = list(fill_skill.get("features") or [])
    pending_features = list(pending_skill_expert.get("features") or [])
    xf = _pending_skill_frame(row, fill_features)
    xp = _pending_skill_frame(row, pending_features)
    fill_models = fill_skill.get("models") or {}
    p1 = float(fill_models["fill_1s"].predict_proba(xf)[0, 1])
    p3 = float(fill_models["fill_3s"].predict_proba(xf)[0, 1])
    p5 = float(fill_models["fill_5s"].predict_proba(xf)[0, 1])
    pp = float(pending_skill_expert["model"].predict_proba(xp)[0, 1])
    port = row.get("portfolio") if isinstance(row.get("portfolio"), dict) else {}
    cum = float(row.get("cumExecQty") or 0.0)
    rem = float(row.get("remainingQty") or 0.0)
    total = cum + rem
    init_depth = float(row.get("initialDepth") or 0.0)
    depletion = float(row.get("publicCumDepletion") or 0.0)
    def fv(v: Any) -> float:
        try:
            x = float(v)
            return x if math.isfinite(x) else math.nan
        except Exception:
            return math.nan
    vals: dict[str, Any] = {
        "p_fill_1s": p1,
        "p_fill_3s": p3,
        "p_fill_5s": p5,
        "p_continue_teacher_v0": pp,
        "order_age_ms": fv(row.get("orderAgeMs")),
        "remaining_ratio": rem / total if total > EPS else math.nan,
        "partial_fill_ratio": fv(row.get("partialFillRatio")),
        "active_same_count": fv(row.get("activeSameCount")),
        "active_opp_count": fv(row.get("activeOppCount")),
        "quote_offset_ticks": fv(row.get("quoteOffsetTicks")),
        "current_spread_ticks": fv(row.get("currentSpreadTicks")),
        "public_depletion_ratio": depletion / init_depth if init_depth > EPS else math.nan,
        "maker_abs_net": fv(port.get("maker_abs_net")),
        "maker_paired_coverage": fv(port.get("maker_paired_coverage")),
        "combined_abs_net": fv(port.get("combined_abs_net")),
        "combined_paired_coverage": fv(port.get("combined_paired_coverage")),
        "worst_case_floor": fv(port.get("worst_case_floor")),
        "reason_is_hazard": float(reason == "MAKER_HAZARD"),
        "reason_is_burst": float(reason == "MAKER_BURST"),
        "side_is_up": float(str(row.get("side") or "").upper() == "UP"),
    }
    return pd.DataFrame([{f: vals.get(f, math.nan) for f in adapter_features}], columns=adapter_features)


def run_market(
    market_id: int,
    *,
    entry_latency_ms: int = 1092,
    response_latency_ms: int = 273,
    queue_model: str = "risk",
    trade_offset: str = "mid",
    taker_confirm_ms: int = 2200,
    pending_management_artifact: str | Path | None = None,
    add_intervention_artifact: str | Path | None = None,
    pending_option_horizon_ms: int = 5000,
    event_clock_mode: str = "OFF",
    max_policy_gap_ms: int = 5000,
) -> dict[str, Any]:
    snapshots = load_public_snapshots(market_id)
    if not snapshots:
        raise RuntimeError(f"no R2 public snapshots for market {market_id}")
    paper = load_reference_paper(market_id)
    a = HftBookAdapter(market_id, entry_latency_ms, response_latency_ms, queue_model, trade_offset)
    c = new_controller(a)
    pending_skill = joblib.load(Path(pending_management_artifact)) if pending_management_artifact else None
    pending_model = pending_skill.get("model") if isinstance(pending_skill, dict) else None
    pending_features = list(pending_skill.get("features") or []) if isinstance(pending_skill, dict) else []
    add_skill = joblib.load(Path(add_intervention_artifact)) if add_intervention_artifact else None
    add_model = add_skill.get("model") if isinstance(add_skill, dict) else None
    add_features = list(add_skill.get("features") or []) if isinstance(add_skill, dict) else []
    add_fill_expert = joblib.load(_artifact_ref_path(add_skill["fillArtifact"])) if isinstance(add_skill, dict) and add_skill.get("fillArtifact") else None
    add_pending_expert = joblib.load(_artifact_ref_path(add_skill["pendingArtifact"])) if isinstance(add_skill, dict) and add_skill.get("pendingArtifact") else None

    decisions: list[dict[str, Any]] = []
    maker_fill_events: list[dict[str, Any]] = []
    taker_events: list[dict[str, Any]] = []
    taker_attempts: list[dict[str, Any]] = []
    submit_rejects: list[dict[str, Any]] = []
    cancels: list[dict[str, Any]] = []
    order_states: list[dict[str, Any]] = []
    order_meta: dict[str, dict[str, Any]] = {}
    pending_skill_vetoes: list[dict[str, Any]] = []
    add_intervention_vetoes: list[dict[str, Any]] = []
    pending_option_starts: dict[str, int] = {}
    pending_option_start_prob: dict[str, float] = {}
    pending_option_consumed: set[str] = set()
    pending_option_terminations: list[dict[str, Any]] = []

    orig_add = c._add_order
    orig_cancel = c._cancel_order

    def snapshot_existing(now: int, context: str, decision_id: str | None = None) -> None:
        for key, order in list(c.orders.items()):
            order_states.append(_order_state(c, a, key, order, now, context, decision_id))

    def add_wrap(side: str, now: int, snapshot_ns: int, decision_id: str, reason: str, p: float, snapshot: dict[str, Any], allow_stack: bool = True, bypass_guard: bool = False) -> bool:
        snapshot_existing(now, f"BEFORE_ADD:{side}:{reason}", decision_id)
        if add_model is not None and add_features and isinstance(add_fill_expert, dict) and isinstance(add_pending_expert, dict):
            active_options = []
            candidates = []
            for key, existing in list(c.orders.items()):
                if existing.side != side:
                    continue
                oid = str(existing.id)
                if oid in pending_option_consumed:
                    continue
                state = _order_state(c, a, key, existing, now, f"ADD_INTERVENTION_CHECK:{side}:{reason}", decision_id)
                if state.get("hftStatus") not in {"NEW", "PARTIALLY_FILLED"} or float(state.get("remainingQty") or 0.0) <= EPS:
                    continue
                started = pending_option_starts.get(oid)
                if started is not None:
                    age = int(now) - int(started)
                    if age >= int(pending_option_horizon_ms):
                        pending_option_consumed.add(oid)
                        pending_option_terminations.append({
                            "atMs": int(now), "orderId": oid, "reason": "OPTION_HORIZON_EXPIRED",
                            "optionStartedMs": int(started), "optionAgeMs": age,
                        })
                        continue
                    active_options.append((state, oid, started, float(pending_option_start_prob.get(oid, math.nan))))
                    continue
                frame = _add_intervention_frame(state, reason, add_features, add_fill_expert, add_pending_expert)
                prob = float(add_model.predict_proba(frame)[0, 1])
                candidates.append((prob, state, oid))
            if active_options:
                state, oid, started, start_prob = min(active_options, key=lambda z: int(z[2]))
                add_intervention_vetoes.append({
                    "atMs": int(now), "decisionId": decision_id, "attemptedSide": side,
                    "attemptReason": reason, "pVetoAtOptionStart": start_prob,
                    "existingOrderId": state.get("orderId"), "existingPrice": state.get("price"),
                    "existingAgeMs": state.get("orderAgeMs"), "existingStatus": state.get("hftStatus"),
                    "existingRemainingQty": state.get("remainingQty"), "quoteOffsetTicks": state.get("quoteOffsetTicks"),
                    "optionStartedMs": int(started), "optionAgeMs": int(now)-int(started),
                    "optionHorizonMs": int(pending_option_horizon_ms), "vetoPhase": "OPTION_ACTIVE",
                })
                return False
            if candidates:
                prob, state, oid = max(candidates, key=lambda z: z[0])
                if prob >= 0.5:
                    pending_option_starts[oid] = int(now)
                    pending_option_start_prob[oid] = prob
                    add_intervention_vetoes.append({
                        "atMs": int(now), "decisionId": decision_id, "attemptedSide": side,
                        "attemptReason": reason, "pVetoAtOptionStart": prob,
                        "existingOrderId": state.get("orderId"), "existingPrice": state.get("price"),
                        "existingAgeMs": state.get("orderAgeMs"), "existingStatus": state.get("hftStatus"),
                        "existingRemainingQty": state.get("remainingQty"), "quoteOffsetTicks": state.get("quoteOffsetTicks"),
                        "optionStartedMs": int(now), "optionAgeMs": 0,
                        "optionHorizonMs": int(pending_option_horizon_ms), "vetoPhase": "OPTION_START",
                    })
                    return False
        elif pending_model is not None and pending_features:
            candidates = []
            for key, existing in list(c.orders.items()):
                if existing.side != side:
                    continue
                oid = str(existing.id)
                if oid in pending_option_consumed:
                    continue
                state = _order_state(c, a, key, existing, now, f"SKILL_CHECK_BEFORE_ADD:{side}:{reason}", decision_id)
                # Preserve simultaneous/multi-rail placement intent. The execution option can begin only
                # after venue acknowledgement, and its lifetime is bounded by the teacher's 5s semantics.
                if state.get("hftStatus") not in {"NEW", "PARTIALLY_FILLED"} or float(state.get("remainingQty") or 0.0) <= EPS:
                    continue
                started = pending_option_starts.get(oid)
                if started is not None and int(now) - int(started) >= int(pending_option_horizon_ms):
                    pending_option_consumed.add(oid)
                    pending_option_terminations.append({
                        "atMs": int(now), "orderId": oid, "reason": "OPTION_HORIZON_EXPIRED",
                        "optionStartedMs": int(started), "optionAgeMs": int(now)-int(started),
                    })
                    continue
                prob = float(pending_model.predict_proba(_pending_skill_frame(state, pending_features))[0, 1])
                candidates.append((prob, state, oid, started))
            if candidates:
                prob, state, oid, started = max(candidates, key=lambda z: z[0])
                if prob >= 0.5:
                    if started is None:
                        pending_option_starts[oid] = int(now)
                        started = int(now)
                    pending_skill_vetoes.append({
                        "atMs": int(now), "decisionId": decision_id, "attemptedSide": side,
                        "attemptReason": reason, "pContinuePending": prob,
                        "existingOrderId": state.get("orderId"), "existingPrice": state.get("price"),
                        "existingAgeMs": state.get("orderAgeMs"), "existingStatus": state.get("hftStatus"),
                        "existingRemainingQty": state.get("remainingQty"), "quoteOffsetTicks": state.get("quoteOffsetTicks"),
                        "optionStartedMs": int(started), "optionAgeMs": int(now)-int(started),
                        "optionHorizonMs": int(pending_option_horizon_ms),
                    })
                    return False
                if started is not None:
                    pending_option_consumed.add(oid)
                    pending_option_terminations.append({
                        "atMs": int(now), "orderId": oid, "reason": "MODEL_RELEASE",
                        "optionStartedMs": int(started), "optionAgeMs": int(now)-int(started),
                        "pContinuePending": prob,
                    })
        before = set(c.orders)
        made = orig_add(side, now, snapshot_ns, decision_id, reason, p, snapshot, allow_stack, bypass_guard)
        if made:
            new_keys = list(set(c.orders) - before)
            if new_keys:
                key = new_keys[0]
                order = c.orders[key]
                num, rc = a.submit(key, order)
                order_meta[order.id] = {
                    "key": list(key), "orderNum": num, "placedAtMs": int(now), "side": side,
                    "price": float(order.price), "shares": float(order.shares), "reason": reason,
                }
                if rc != 0:
                    submit_rejects.append({"atMs": now, "orderId": order.id, "key": list(key), "submitRc": rc})
        return made

    def cancel_wrap(key: tuple[str, int], at_ms: int, reason: str) -> None:
        order = c.orders.get(key)
        if order is not None:
            order_states.append(_order_state(c, a, key, order, at_ms, f"BEFORE_CANCEL:{reason}", None))
            cancels.append({"atMs": int(at_ms), "orderId": order.id, "side": order.side, "price": float(order.price), "reason": reason})
        a.cancel(key)
        orig_cancel(key, at_ms, reason)

    def fill_wrap(snapshot: dict[str, Any], now: int) -> list[dict[str, Any]]:
        fills: list[dict[str, Any]] = []
        for key, order in list(c.orders.items()):
            snap = a.snap(key)
            num = a.order_num_by_key.get(key)
            cum = float(snap.get("cumExecQty") or 0.0)
            old = float(a.prev_cum.get(num, 0.0)) if num is not None else 0.0
            if cum > old + EPS:
                delta = cum - old
                native_px = snap.get("execPrice")
                px = float(order.price)
                if native_px is not None and math.isfinite(float(native_px)):
                    px = float(native_px) if order.side == "UP" else 1.0 - float(native_px)
                fill_ms = int((snap.get("exchangeTs") or now * 1_000_000) // 1_000_000)
                pre_net = c.inventory.maker_up - c.inventory.maker_down
                pre_g = c.inventory.maker_up + c.inventory.maker_down
                pre_pc = 2 * min(c.inventory.maker_up, c.inventory.maker_down) / pre_g if pre_g > mod.EPS else 1.0
                c.inventory.apply({"event_ms": fill_ms, "role": "MAKER", "side": order.side, "price": px, "shares": delta})
                c.current_metrics["makerFills"] += 1
                c.run_metrics["makerFills"] += 1
                post_net = c.inventory.maker_up - c.inventory.maker_down
                if order.occupied_before:
                    predom = "UP" if pre_net > mod.EPS else "DOWN" if pre_net < -mod.EPS else None
                    if predom == order.side and abs(pre_net) >= mod.SHARES - mod.EPS and abs(post_net) > abs(pre_net) + 1:
                        c.episode = {
                            "kind": "OVERLAP", "side": order.side, "risk_start_ms": fill_ms,
                            "risk_pre_abs": abs(pre_net), "start_ms": fill_ms, "pre_abs": abs(pre_net),
                            "start_abs": abs(post_net), "expansion": abs(post_net) - abs(pre_net),
                            "pre_pc": pre_pc, "unresolved": False,
                        }
                        c.readiness = False
                        c.current_metrics["excursions"] += 1
                        c.run_metrics["excursions"] += 1
                maker_fill_events.append({
                    "atMs": fill_ms, "observedAtMs": int(now), "orderId": order.id,
                    "side": order.side, "price": px, "deltaShares": delta,
                    "cumShares": cum, "status": snap.get("status"),
                    "occupiedBefore": bool(order.occupied_before),
                })
                fills.append({"side": order.side, "price": px, "shares": delta, "hftStatus": snap.get("status")})
                if num is not None:
                    a.prev_cum[num] = cum
                if snap.get("status") not in {"FILLED", "REJECTED", "EXPIRED", "CANCELED"}:
                    order.shares = max(0.0, float(order.shares) - delta)
            if snap.get("status") in {"FILLED", "REJECTED", "EXPIRED", "CANCELED"}:
                c.orders.pop(key, None)
                c.last_closed[key] = int(now)
        return fills

    def taker_wrap(side: str, price: float, now: int, decision_id: str, snapshot: dict[str, Any], raw: dict[str, Any], p1: float, p3: float, ppass: float, pred_effect: str) -> bool:
        snapshot_existing(now, f"BEFORE_TAKER:{side}", decision_id)
        max_price = min(0.99, float(price) + 0.02)
        num, rc = a.submit_taker(side, max_price, mod.SHARES)
        attempt = {"atMs": int(now), "side": side, "observedAsk": float(price), "maxPrice": max_price, "orderNum": num, "submitRc": rc, "decisionId": decision_id}
        taker_attempts.append(attempt)
        if rc != 0:
            attempt["result"] = "SUBMIT_REJECT"
            return False
        target = int(now) + int(taker_confirm_ms)
        a.advance(market_id, target)
        fill_wrap(snapshot, target)
        snap = a.snap_num(num)
        cum = float(snap.get("cumExecQty") or 0.0)
        if cum <= mod.EPS:
            attempt.update({"result": "NO_FILL", "status": snap.get("status"), "confirmMs": target})
            return False
        native_px = snap.get("execPrice")
        fill_px = float(price)
        if native_px is not None and math.isfinite(float(native_px)):
            fill_px = float(native_px) if side == "UP" else 1.0 - float(native_px)
        fill_ms = int((snap.get("exchangeTs") or target * 1_000_000) // 1_000_000)
        c.inventory.apply({"event_ms": fill_ms, "role": "TAKER", "side": side, "price": fill_px, "shares": cum})
        fee = mod.taker_fee(cum, fill_px, mod.FEE_BPS)
        c.taker_fee_spent += fee
        c.current_metrics["takerFills"] += 1
        c.run_metrics["takerFills"] += 1
        c.last_taker_ms = fill_ms
        event = {
            "atMs": fill_ms, "decisionMs": int(now), "side": side, "price": fill_px, "shares": cum,
            "feeUsdt": fee, "pTaker1s": p1, "pTaker3s": p3,
            "secondsLeft": snapshot.get("secondsLeft"), "decisionId": decision_id,
            "hftStatus": snap.get("status"),
        }
        taker_events.append(event)
        attempt.update({"result": "FILLED", "filledShares": cum, "fillPrice": fill_px, "fillMs": fill_ms, "status": snap.get("status")})
        return True

    c._add_order = add_wrap
    c._cancel_order = cancel_wrap
    c._fill_orders = fill_wrap
    c._record_taker = taker_wrap

    event_clock_mode = str(event_clock_mode or "OFF").upper()
    last_policy_ms: int | None = None
    fill_count_at_policy = 0
    event_clock_stats = {"policySteps": 0, "skippedSnapshots": 0, "fillTriggeredSteps": 0, "timeoutTriggeredSteps": 0}
    try:
        for s in snapshots:
            # A Taker confirmation may advance HFT beyond intermediate controller snapshots.
            now = int(s["sampledAtMs"])
            if int(a.bt.current_timestamp // 1_000_000) > now:
                continue
            if event_clock_mode == "FILL_PROGRESS" and last_policy_ms is not None:
                # Keep the real venue/book clock moving and consume any actual fills, but do not
                # advance the coupled R2 policy until execution progresses or the bounded timeout fires.
                a.advance(market_id, now)
                fill_wrap(dict(s), now)
                fill_progress = len(maker_fill_events) > fill_count_at_policy
                timeout = now - last_policy_ms >= int(max_policy_gap_ms)
                # Never starve the final 30s safety phase.
                sec = float(s.get("secondsLeft") or 999.0)
                if not fill_progress and not timeout and sec > 30.0:
                    event_clock_stats["skippedSnapshots"] += 1
                    snapshot_existing(now, "EVENT_CLOCK_HOLD", None)
                    continue
                if fill_progress:
                    event_clock_stats["fillTriggeredSteps"] += 1
                elif timeout:
                    event_clock_stats["timeoutTriggeredSteps"] += 1
            c._step(dict(s))
            last_policy_ms = now
            fill_count_at_policy = len(maker_fill_events)
            event_clock_stats["policySteps"] += 1
            decision_id = None
            if c.last_decision is not None and int(c.last_decision.get("decisionMs") or -1) == now:
                decisions.append(copy.deepcopy(c.last_decision))
                decision_id = str(c.last_decision.get("decisionId") or "")
            snapshot_existing(now, "POST_DECISION_ACTIVE", decision_id)
        terminal = int(a.meta["lastReceivedMs"])
        a.advance(market_id, terminal)
        fill_wrap(snapshots[-1], terminal)
        final_portfolio = _portfolio(c, terminal)
    finally:
        a.close()

    # Hindsight labels are produced only after the episode is over. Inputs above remain strict-past.
    fills_by_order: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in maker_fill_events:
        fills_by_order[str(f["orderId"])].append(f)
    cancel_by_order = {str(x["orderId"]): x for x in cancels}
    for row in order_states:
        cp = int(row["checkpointMs"])
        oid = str(row["orderId"])
        future = [x for x in fills_by_order.get(oid, []) if int(x["atMs"]) > cp]
        for horizon in (1000, 3000, 5000):
            z = [x for x in future if int(x["atMs"]) <= cp + horizon]
            row[f"futureFillShares{horizon//1000}s"] = float(sum(float(x["deltaShares"]) for x in z))
            row[f"labelAnyFill{horizon//1000}s"] = int(bool(z))
            can = cancel_by_order.get(oid)
            row[f"censoredBefore{horizon//1000}s"] = int(can is not None and cp < int(can["atMs"]) <= cp + horizon)
        row["futureFirstFillMs"] = int(future[0]["atMs"]) if future else None
        row["futureFirstFillDelayMs"] = int(future[0]["atMs"]) - cp if future else None
        row["eventualAdditionalFillShares"] = float(sum(float(x["deltaShares"]) for x in future))
        row["cancelAtMs"] = int(cancel_by_order[oid]["atMs"]) if oid in cancel_by_order else None
        row["cancelReason"] = cancel_by_order[oid]["reason"] if oid in cancel_by_order else None

    maker_cost = float(c.inventory.maker_up_cost + c.inventory.maker_down_cost)
    taker_cost = float(c.inventory.taker_up_cost + c.inventory.taker_down_cost)
    return {
        "version": "HFTBACKTEST_R2_EVENT_CLOCK_V0",
        "researchOnly": True,
        "liveTradingChanges": False,
        "marketId": int(market_id),
        "student": VERSION,
        "studentScale": "PRE_CAP100_ORIGINAL_R2",
        "config": {
            "entryLatencyMs": int(entry_latency_ms), "responseLatencyMs": int(response_latency_ms),
            "queueModel": queue_model, "tradeOffset": trade_offset,
            "makerExecution": "HFTBACKTEST_EXECUTION_TAPE_V1_ARCHIVE",
            "takerExecution": "HFTBACKTEST_MARKETABLE_LIMIT_CONFIRM",
            "takerConfirmMs": int(taker_confirm_ms),
            "dreamFillAllowed": False,
            "pendingManagementSkill": str(pending_management_artifact) if pending_management_artifact else None,
            "pendingManagementNaturalThreshold": 0.5 if pending_management_artifact else None,
            "addInterventionSkill": str(add_intervention_artifact) if add_intervention_artifact else None,
            "addInterventionNaturalThreshold": 0.5 if add_intervention_artifact else None,
            "pendingOptionHorizonMs": int(pending_option_horizon_ms) if (pending_management_artifact or add_intervention_artifact) else None,
            "pendingOptionRearmSameOrder": False if (pending_management_artifact or add_intervention_artifact) else None,
            "eventClockMode": event_clock_mode, "maxPolicyGapMs": int(max_policy_gap_ms),
        },
        "feed": a.meta,
        "paperReference": {"makerOrders": len(paper["orders"]), "takerFills": len(paper["takers"])},
        "studentRollout": {
            "decisions": len(decisions), "makerPlacements": int(c.run_metrics["makerPlacements"]),
            "makerFillEvents": len(maker_fill_events), "makerFilledShares": float(sum(float(x["deltaShares"]) for x in maker_fill_events)),
            "takerFills": len(taker_events), "takerFilledShares": float(sum(float(x.get("shares") or 0.0) for x in taker_events)),
            "makerCostUsdt": maker_cost, "takerCostUsdt": taker_cost, "takerFeesUsdt": float(c.taker_fee_spent),
            "finalPortfolio": final_portfolio, "activeOrdersAtEnd": len(c.orders), "runMetrics": dict(c.run_metrics),
            "submitRejects": submit_rejects,
            "pendingSkillVetoes": len(pending_skill_vetoes),
            "addInterventionVetoes": len(add_intervention_vetoes),
            "pendingOptionUniqueOrders": len(pending_option_starts),
            "pendingOptionTerminations": len(pending_option_terminations),
            "eventClockStats": event_clock_stats,
        },
        "makerFillEvents": maker_fill_events,
        "takerEvents": taker_events,
        "takerAttempts": taker_attempts,
        "cancelEvents": cancels,
        "pendingSkillVetoEvents": pending_skill_vetoes,
        "addInterventionVetoEvents": add_intervention_vetoes,
        "pendingOptionTerminationEvents": pending_option_terminations,
        "orderMeta": order_meta,
        "orderStateRows": order_states,
        "decisionRows": decisions,
        "boundary": "Student decisions use only R2 public/own state. All Student Maker/Taker fills come from HftBacktest replay of Execution Tape V1. Future fill fields are attached post-episode for training labels only and must never be runtime inputs.",
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--market-id", type=int, required=True)
    p.add_argument("--entry-latency-ms", type=int, default=1092)
    p.add_argument("--response-latency-ms", type=int, default=273)
    p.add_argument("--queue-model", choices=["risk", "log"], default="risk")
    p.add_argument("--trade-offset", choices=["early", "mid", "late"], default="mid")
    p.add_argument("--taker-confirm-ms", type=int, default=2200)
    p.add_argument("--add-intervention-artifact", type=str, default=None)
    p.add_argument("--event-clock-mode", choices=["OFF","FILL_PROGRESS"], default="OFF")
    p.add_argument("--max-policy-gap-ms", type=int, default=5000)
    args = p.parse_args()
    report = run_market(
        args.market_id,
        entry_latency_ms=args.entry_latency_ms,
        response_latency_ms=args.response_latency_ms,
        queue_model=args.queue_model,
        trade_offset=args.trade_offset,
        taker_confirm_ms=args.taker_confirm_ms,
        add_intervention_artifact=args.add_intervention_artifact,
        event_clock_mode=args.event_clock_mode, max_policy_gap_ms=args.max_policy_gap_ms,
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"r2_execution_school_market{args.market_id}_mid_risk_v0.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(json.dumps({
        "ok": True, "path": str(out), "marketId": args.market_id,
        "studentRollout": report["studentRollout"], "orderStateRows": len(report["orderStateRows"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
