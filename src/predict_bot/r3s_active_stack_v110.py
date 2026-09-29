from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import joblib
import numpy as np

VERSION = "R3S_ACTIVE_STACK_V1_1_0"
COMPONENTS = {
    "activeQuantity": "R3_ACTIVE_QUANTITY_STRICTPAST_V2",
    "stableAdd": "R3_STABLE_ADD_ELIGIBILITY_PILOT500_V2_NORMALIZED",
    "stableExpansion": "R3_STABLE_EXPANSION_TEACHER_V1_FULL",
    "postAddLifecycle": "R3_POST_ADD_OUR_STATE_DISTILL_V2_STREAM",
    "earlyRecovery": "R3_EARLY_RECOVERY_CONTINUATION_PILOT500_V2_HFTSCALE",
    "tripleConfirmContainment": "R3S_TRIPLE_CONFIRM_CONTAINMENT_V1",
}


class R3SActiveStackV110:
    """Strict-past R3-S ADD/recovery cooperation head.

    Stable-ADD and Stable-Expansion are evidence heads for the triple-confirm
    containment decision; they do not independently veto the initial ADD.
    Post-ADD lifecycle may veto a *re-ADD* while natural Maker recovery is still
    preferred. Structural REPAIR is never hard-vetoed by this head.
    """

    def __init__(self, research_dir: Path) -> None:
        d = Path(research_dir)
        self.stable_add = joblib.load(d / "r3_stable_add_eligibility_pilot500_v2_normalized.joblib")
        self.stable_expansion = joblib.load(d / "r3_stable_expansion_teacher_v1_full.joblib")
        self.post_add = joblib.load(d / "r3_post_add_our_state_student_v2_stream.joblib")
        self.early = joblib.load(d / "r3_early_recovery_continuation_pilot500_v2_hftscale.joblib")
        self._assert_versions()

    def _assert_versions(self) -> None:
        expected = {
            "stable_add": COMPONENTS["stableAdd"],
            "stable_expansion": COMPONENTS["stableExpansion"],
            "post_add": COMPONENTS["postAddLifecycle"],
            "early": COMPONENTS["earlyRecovery"],
        }
        for name, version in expected.items():
            art = getattr(self, name)
            if not isinstance(art, dict) or str(art.get("version") or "") != version:
                raise RuntimeError(f"R3-S artifact/version mismatch: {name}: {art.get('version') if isinstance(art,dict) else type(art).__name__} != {version}")

    @staticmethod
    def structural_effect(side: str, combined_net: float) -> str:
        s = str(side).upper()
        if (s == "DOWN" and combined_net > 1e-9) or (s == "UP" and combined_net < -1e-9):
            return "REPAIR_EFFECT"
        return "ADD_EFFECT"

    @staticmethod
    def _event_index(snapshot: dict[str, Any]) -> float:
        try:
            sec = float(snapshot.get("secondsLeft") if snapshot.get("secondsLeft") is not None else 300.0)
        except Exception:
            sec = 300.0
        return max(0.0, min(1.0, 1.0 - sec / 300.0))

    @staticmethod
    def _events(inventory: Any, before_ms: int | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for z in list(getattr(inventory, "events", []) or []):
            try:
                t = int(z.get("event_ms"))
                if before_ms is not None and t >= int(before_ms):
                    continue
                role = str(z.get("role") or "").upper()
                side = str(z.get("side") or "").upper()
                px = float(z.get("price"))
                sh = float(z.get("shares"))
            except Exception:
                continue
            if role not in {"MAKER", "TAKER"} or side not in {"UP", "DOWN"} or sh <= 0 or not math.isfinite(px):
                continue
            rows.append({"t": t, "role": role, "side": side, "px": px, "sh": sh})
        rows.sort(key=lambda x: x["t"])
        return rows

    @staticmethod
    def _geom(events: list[dict[str, Any]], through_ms: int | None = None) -> dict[str, float]:
        up = down = cost = fees = 0.0
        for z in events:
            if through_ms is not None and int(z["t"]) > int(through_ms):
                break
            sh = float(z["sh"]); px = float(z["px"])
            if z["side"] == "UP": up += sh
            else: down += sh
            cost += px * sh
            if z["role"] == "TAKER": fees += sh * px * 0.02
        pu, pd = up - cost - fees, down - cost - fees
        gross = up + down
        return {
            "up": up, "down": down, "cost": cost, "fees": fees,
            "floor": min(pu, pd), "upside": max(pu, pd), "gross": gross,
            "abs": abs(up - down), "pc": 2.0 * min(up, down) / gross if gross > 1e-12 else 0.0,
        }

    def pre_add_evidence(self, inventory: Any, now_ms: int, snapshot: dict[str, Any]) -> dict[str, Any] | None:
        hist = self._events(inventory, before_ms=int(now_ms))
        if not hist:
            return None
        g = self._geom(hist)
        surplus = "UP" if g["up"] > g["down"] else "DOWN" if g["down"] > g["up"] else "FLAT"
        q15 = [z for z in hist if int(now_ms) - int(z["t"]) <= 15000]
        q5 = [z for z in hist if int(now_ms) - int(z["t"]) <= 5000]
        anchor = q5[0] if q5 else q15[0] if q15 else hist[-1]
        old = self._geom(hist, through_ms=int(anchor["t"]))
        tot = sum(float(z["sh"]) for z in q15) or 1.0
        nev = len(q15) or 1
        last = hist[-1]
        idx = self._event_index(snapshot)
        sa_vals = {
            "paired_coverage": g["pc"],
            "imbalance_ratio": g["abs"] / g["gross"] if g["gross"] else 0.0,
            "cost_per_gross": (g["cost"] + g["fees"]) / g["gross"] if g["gross"] else 0.0,
            "floor_per_gross": g["floor"] / g["gross"] if g["gross"] else 0.0,
            "upside_per_gross": g["upside"] / g["gross"] if g["gross"] else 0.0,
            "floor_to_upside": g["floor"] / g["upside"] if abs(g["upside"]) > 1e-9 else 0.0,
            "last_price": last["px"], "last_role_taker": float(last["role"] == "TAKER"),
            "age_since_last_s": (int(now_ms) - int(last["t"])) / 1000.0,
            "events_5s": len(q5), "events_15s": len(q15),
            "maker_frac_15s": sum(z["role"] == "MAKER" for z in q15) / nev,
            "taker_frac_15s": sum(z["role"] == "TAKER" for z in q15) / nev,
            "same_side_event_frac_15s": sum(surplus != "FLAT" and z["side"] == surplus for z in q15) / nev,
            "same_side_share_frac_15s": sum(z["sh"] for z in q15 if surplus != "FLAT" and z["side"] == surplus) / tot,
            "opp_side_share_frac_15s": sum(z["sh"] for z in q15 if surplus != "FLAT" and z["side"] != surplus) / tot,
            "floor_change5_per_gross": (g["floor"] - old["floor"]) / g["gross"] if g["gross"] else 0.0,
            "upside_change5_per_gross": (g["upside"] - old["upside"]) / g["gross"] if g["gross"] else 0.0,
            "absnet_change5_per_gross": (g["abs"] - old["abs"]) / g["gross"] if g["gross"] else 0.0,
            "event_index_norm": idx,
        }
        xsa = np.asarray([[float(sa_vals.get(f, 0.0)) for f in self.stable_add["features"]]], dtype=float)
        p_sa = float(self.stable_add["model"].predict_proba(xsa)[0, 1])
        base_pair = min(g["up"], g["down"]); ss = g["abs"]
        se_vals = {
            "floor": g["floor"], "upside": g["upside"], "upside_gap": g["upside"] - g["floor"],
            "surplus_shares": ss, "base_pair_shares": base_pair,
            "surplus_ratio": ss / g["gross"] if g["gross"] else 0.0,
            "cost_per_gross_share": (g["cost"] + g["fees"]) / g["gross"] if g["gross"] else 0.0,
            "last_price": last["px"], "last_shares": last["sh"], "last_role_taker": float(last["role"] == "TAKER"),
            "age_since_last_ms": float(int(now_ms) - int(last["t"])), "events_5s": float(len(q5)), "events_15s": float(len(q15)),
            "maker_events_15s": float(sum(z["role"] == "MAKER" for z in q15)),
            "taker_events_15s": float(sum(z["role"] == "TAKER" for z in q15)),
            "same_side_events_15s": float(sum(surplus != "FLAT" and z["side"] == surplus for z in q15)),
            "opp_side_events_15s": float(sum(surplus != "FLAT" and z["side"] != surplus for z in q15)),
            "same_side_shares_15s": float(sum(z["sh"] for z in q15 if surplus != "FLAT" and z["side"] == surplus)),
            "opp_side_shares_15s": float(sum(z["sh"] for z in q15 if surplus != "FLAT" and z["side"] != surplus)),
            "surplus_change_5s": g["abs"] - old["abs"], "floor_change_5s": g["floor"] - old["floor"],
            "upside_change_5s": g["upside"] - old["upside"],
            "floor_to_upside_ratio": g["floor"] / g["upside"] if abs(g["upside"]) > 1e-9 else 0.0,
            "floor_per_base_share": g["floor"] / base_pair if base_pair > 1e-9 else 0.0,
            "upside_per_surplus_share": g["upside"] / ss if ss > 1e-9 else 0.0,
            "event_index_norm": idx,
        }
        xse = np.asarray([[float(se_vals.get(f, 0.0)) for f in self.stable_expansion["features"]]], dtype=float)
        p_se = float(self.stable_expansion["model"].predict_proba(xse)[0, 1])
        utility = float(self.stable_expansion["utilityModel"].predict(xse)[0])
        return {
            "version": VERSION, "asOfMs": int(now_ms), "strictPast": True,
            "pStableAdd": p_sa, "pStableExpand": p_se, "predUtility5s": utility,
            "preFloor": g["floor"], "preUpside": g["upside"], "preAbsNet": g["abs"],
            "prePairedCoverage": g["pc"], "preGross": g["gross"], "eventIndexNorm": idx,
        }

    def start_add_episode(self, *, fill_ms: int, pre_port: dict[str, Any], post_port: dict[str, Any], evidence: dict[str, Any] | None, snapshot: dict[str, Any], intent_id: str) -> dict[str, Any]:
        pre_floor = float(pre_port.get("worst_case_floor") or 0.0)
        post_floor = float(post_port.get("worst_case_floor") or 0.0)
        spent = max(0.0, pre_floor - post_floor)
        return {
            "version": VERSION, "intentId": intent_id, "atMs": int(fill_ms), "strictPast": True,
            "preFloor": pre_floor, "postFloor": post_floor,
            "postUpside": float(post_port.get("best_case_pnl") or 0.0),
            "postAbsNet": float(post_port.get("combined_abs_net") or 0.0),
            "postPairedCoverage": float(post_port.get("combined_paired_coverage") or 0.0),
            "postGross": float(post_port.get("combined_gross") or 0.0),
            "floorSpent": spent, "spentScale": spent / max(abs(pre_floor) + 5.0, 5.0),
            "events": 0, "makerEvents": 0, "repairEvents": 0, "reAddEvents": 0,
            "evidence": dict(evidence or {}), "eventIndexNorm": self._event_index(snapshot),
            "earlyRecovery": None, "tripleConfirm": False, "containmentSubmitted": False,
        }

    def observe_fill(self, episode: dict[str, Any] | None, *, event_ms: int, role: str, structural_effect: str | None = None) -> None:
        if not isinstance(episode, dict):
            return
        elapsed = int(event_ms) - int(episode.get("atMs") or event_ms)
        if elapsed <= 0 or elapsed > 15000:
            return
        episode["events"] = int(episode.get("events") or 0) + 1
        if str(role).upper() == "MAKER": episode["makerEvents"] = int(episode.get("makerEvents") or 0) + 1
        if str(role).upper() == "TAKER":
            if structural_effect == "REPAIR_EFFECT": episode["repairEvents"] = int(episode.get("repairEvents") or 0) + 1
            elif structural_effect == "ADD_EFFECT": episode["reAddEvents"] = int(episode.get("reAddEvents") or 0) + 1

    def post_add_scores(self, inventory: Any, episode: dict[str, Any] | None, now_ms: int, snapshot: dict[str, Any]) -> dict[str, float] | None:
        if not isinstance(episode, dict):
            return None
        elapsed = int(now_ms) - int(episode.get("atMs") or now_ms)
        if elapsed < 0:
            return None
        port = inventory.features(int(now_ms))
        fl = float(port.get("worst_case_floor") or 0.0); spent = float(episode.get("floorSpent") or 0.0)
        rec = max(0.0, min(1.5, (fl - float(episode.get("postFloor") or 0.0)) / max(spent, 1e-9))) if spent > 1e-9 else 0.0
        vals = {k: port.get(k, 0.0) for k in self.post_add["features"]}
        vals.update({
            "event_index_norm": self._event_index(snapshot), "post_add_floor": episode.get("postFloor", 0.0),
            "post_add_upside": episode.get("postUpside", 0.0), "post_add_abs_net": episode.get("postAbsNet", 0.0),
            "floor_spent": spent, "spent_vs_floor_scale": episode.get("spentScale", 0.0),
            "elapsed_since_add_ms": elapsed, "events_since_add": episode.get("events", 0),
            "maker_events_since_add": episode.get("makerEvents", 0), "repair_events_since_add": episode.get("repairEvents", 0),
            "floor_recovered_fraction": rec,
        })
        arr = []
        for f in self.post_add["features"]:
            try: x = float(vals.get(f, 0.0) or 0.0)
            except Exception: x = 0.0
            arr.append(x if math.isfinite(x) else 0.0)
        x = np.asarray([arr], dtype=float)
        return {
            "pMakerRecovery": float(np.clip(self.post_add["makerRecoveryModel"].predict(x)[0], 0.0, 1.0)),
            "pRepairEmergency": float(np.clip(self.post_add["repairEmergencyModel"].predict(x)[0], 0.0, 1.0)),
            "pReAdd": float(np.clip(self.post_add["reAddModel"].predict(x)[0], 0.0, 1.0)),
            "floorRecoveredFraction": rec, "currentFloor": fl,
            "currentUpside": float(port.get("best_case_pnl") or 0.0),
            "currentAbsNet": float(port.get("combined_abs_net") or 0.0), "elapsedMs": float(elapsed),
        }

    def readd_gate(self, inventory: Any, episode: dict[str, Any] | None, now_ms: int, snapshot: dict[str, Any]) -> tuple[bool, str | None, dict[str, float] | None]:
        if isinstance(episode,dict) and int(now_ms)-int(episode.get("atMs") or now_ms) > 15000:
            return True, None, None
        sc = self.post_add_scores(inventory, episode, now_ms, snapshot)
        if sc is None:
            return True, None, None
        if sc["floorRecoveredFraction"] < 0.5 and sc["pReAdd"] < 0.5:
            return False, "READD_NOT_READY", sc
        if sc["pMakerRecovery"] >= 0.5 and sc["floorRecoveredFraction"] < 0.5:
            return False, "MAKER_RECOVERY_PREFERRED", sc
        return True, None, sc

    def early_recovery_score(self, inventory: Any, episode: dict[str, Any] | None, now_ms: int, snapshot: dict[str, Any]) -> dict[str, float] | None:
        if not isinstance(episode, dict):
            return None
        elapsed = int(now_ms) - int(episode.get("atMs") or now_ms)
        if elapsed < 15000:
            return None
        port = inventory.features(int(now_ms))
        spent = float(episode.get("floorSpent") or 0.0)
        cur_floor = float(port.get("worst_case_floor") or 0.0)
        floor_rec = max(0.0, (cur_floor - float(episode.get("postFloor") or 0.0)) / max(spent, 1e-9)) if spent > 1e-9 else 0.0
        post_abs = float(episode.get("postAbsNet") or 0.0); post_gross = float(episode.get("postGross") or 0.0)
        vals = {
            "elapsed_s": elapsed / 1000.0,
            "floor_recovery_frac": floor_rec,
            "absnet_change_frac": (float(port.get("combined_abs_net") or 0.0) - post_abs) / max(post_abs, 1.0),
            "paired_coverage": float(port.get("combined_paired_coverage") or 0.0),
            "paired_change": float(port.get("combined_paired_coverage") or 0.0) - float(episode.get("postPairedCoverage") or 0.0),
            "maker_events": float(episode.get("makerEvents") or 0),
            "repair_events": float(episode.get("repairEvents") or 0),
            "readd_events": float(episode.get("reAddEvents") or 0),
            "gross_change_frac": (float(port.get("combined_gross") or 0.0) - post_gross) / max(post_gross, 1.0),
            "event_index_norm": self._event_index(snapshot),
        }
        x = np.asarray([[float(vals.get(f, 0.0)) for f in self.early["features"]]], dtype=float)
        p = float(self.early["model"].predict_proba(x)[0, 1])
        return {"pEarlyRecovery15s": p, **vals}

    def triple_confirm(self, inventory: Any, episode: dict[str, Any] | None, now_ms: int, snapshot: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
        if not isinstance(episode, dict) or bool(episode.get("containmentSubmitted")):
            return False, None
        early = self.early_recovery_score(inventory, episode, now_ms, snapshot)
        if early is None:
            return False, None
        ev = episode.get("evidence") if isinstance(episode.get("evidence"), dict) else {}
        ok = (
            float(ev.get("pStableAdd", 1.0)) < 0.5
            and float(ev.get("pStableExpand", 1.0)) < 0.5
            and float(ev.get("predUtility5s", 1.0)) <= 0.0
            and float(early.get("pEarlyRecovery15s", 1.0)) < 0.5
        )
        detail = {
            "strictPast": True, "thresholdMs": 15000, "pStableAdd": ev.get("pStableAdd"),
            "pStableExpand": ev.get("pStableExpand"), "predUtility5s": ev.get("predUtility5s"),
            **early, "tripleConfirm": bool(ok),
        }
        episode["earlyRecovery"] = dict(early); episode["tripleConfirm"] = bool(ok)
        return bool(ok), detail
