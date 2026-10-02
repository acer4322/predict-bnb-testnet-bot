"""EXTERNAL EXECUTION ONLY. V2 same-intent passive geometry / queue retention.

Constructed without strategy execution. A is the unchanged Pair-only lifecycle;
B uses closer legal ticks and retains its synthetic frontier queues; C uses the
same placement as B but the original public-membership cancellation rule.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import itertools
import json
import math
from pathlib import Path
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_gpt6_detail_intelligence_v1_external as v1
from tools.eth_repair_modular.detail_intelligence_reachability_v2 import (
    PassiveIntent, bounded_frontier, pending_reachability, synthetic_keep_decision,
)

CELLS = ("A_PAIR_ONLY_BASELINE", "B_SAME_INTENT_FRONTIER_QUEUE_V2",
         "C_FRONTIER_WITH_LEGACY_CANCEL_CONTROL")
TICK_SIZE = 0.01  # Existing execution substrate, checked before external execution.
EPS = 1e-9


def simulator_class(base):
    Instrumented = v1.simulator_class(base)

    class ReachabilitySim(Instrumented):
        def __init__(self, tape, cell, trace_path):
            # Never enable the rejected V1 ranking override in any V2 cell.
            super().__init__(tape, CELLS[0], trace_path)
            self.v2_cell = cell
            self.v2_synthetic_keys = set()
            self.v2_ttl_attempts = {}
            self.v2_open_intent = None
            self.v2_offer = None
            self.v2_pre_reanchor = None
            self.v2_route_decisions = []
            self.v2_ttl_events = []

        def _state_snapshot(self, t, qv):
            return {"inventory": dict(self.inv), "cost": self.cost,
                    "floor": self._physical_floor(), "best": max(self.inv.values()) - self.cost,
                    "unmatchedInventoryProxy": {s: sum(q for q, _ in self.un[s]) for s in ("UP", "DOWN")},
                    "semanticResponsibilityProgress": "UNKNOWN", "book": qv,
                    "liveSlots": [{"slotId": sid, "key": key, "side": o["side"], "role": role,
                        "price": o["price"], "qty": o["qty"], "remainingQty": self._remaining(key),
                        "cum": o["cum"], "status": o.get("status"), "ageMs": int(t) - o["placed"],
                        "cancelRequested": bool(o.get("cancelRequested")),
                        "ttlCancelAttemptAt": self.v2_ttl_attempts.get(key),
                        "lastConfirmedFillAt": self.detail_last_fill.get(key)}
                        for sid, key, o, role in self._live_role_rows()],
                    "recentFillSideSequence": [x["side"] for x in self.fill_side_sequence[-12:]],
                    "recentRoleSequence": [x["role"] for x in self.fill_side_sequence[-12:]]}

        def _offer(self, side, role, price, qty, qv, exclude_key=None):
            opposite = "DOWN" if side == "UP" else "UP"
            used = tuple(float(o["price"]) for _, key, o, _ in self._live_role_rows(side=side)
                         if key != exclude_key)
            return bounded_frontier(PassiveIntent(side, role, price, qty,
                float(qv[side]["bid"]), float(qv[side]["ask"]),
                tuple((float(q), float(p)) for q, p in self.un[opposite]), used, TICK_SIZE))

        def _candidate_from_levels(self, side, require_pair=True, require_budget=False):
            original = super()._candidate_from_levels(side, require_pair, require_budget)
            if original is None or self.v2_open_intent is None:
                return original
            expected_side, role, qv = self.v2_open_intent
            if side != expected_side:
                raise RuntimeError("V2 must not change the baseline intent side")
            p, q, proj = original
            self.v2_offer = self._offer(side, role, p, q, qv)
            if self.v2_cell == CELLS[0] or not self.v2_offer["changed"]:
                return original
            new_p, new_q = self.v2_offer["price"], self.v2_offer["qty"]
            # Same inherited legality, no different ceiling or role-specific veto.
            if not self._pair_ok(side, new_p):
                raise RuntimeError("Frontier construction disagrees with inherited Pair legality")
            return new_p, new_q, proj

        def _submit_role(self, t, side, role, p, q, proj, source):
            key = f"{side}_{self.n}"
            ok = super()._submit_role(t, side, role, p, q, proj, source)
            if ok:
                changed = (self.v2_cell != CELLS[0] and self.v2_offer is not None
                           and self.v2_offer["changed"] and abs(p - self.v2_offer["price"]) < EPS)
                if changed:
                    self.v2_synthetic_keys.add(key)
                    self.detail_counter["frontierChangedSubmissions"] += 1
                self.detail_outcomes[key].update({"frontierChanged": bool(changed),
                    "economicIntent": {"side": side, "role": role,
                        "decisionId": self.detail_index, "semanticResponsibilityId": None},
                    "placementEvidence": self.v2_offer})
            return ok

        def cancel_expired(self, t):
            # Observe attempts, not acknowledgments. The original TTL method does
            # not set cancelRequested; do not mislabel those orders as useful service.
            for key, o in self.orders.items():
                if key in self.v2_ttl_attempts:
                    continue
                snap = self.snap(o)
                if base.v2.base.live(snap.get("status")) and t - o["placed"] >= base.v2.base.TTL:
                    cur = self.bt.orders(0).get(o["n"])
                    if cur is not None and bool(cur.cancellable):
                        self.v2_ttl_attempts[key] = int(t)
                        self.v2_ttl_events.append({"t": int(t), "key": key,
                            "event": "TTL_CANCEL_ATTEMPT", "outcome": "INHERITED_CALL_RESULT_UNKNOWN"})
            super().cancel_expired(t)

        def _reanchor_stale(self, t):
            qv = base.v2.base.quotes(self.book)
            self.v2_pre_reanchor = self._state_snapshot(t, qv)
            self.v2_route_decisions = []
            super()._reanchor_stale(t)

        def _request_cancel(self, t, sid, reason):
            key = self.slot_key.get(sid)
            o = self.orders.get(key)
            if o and reason in {"CORE_INVALIDATED", "SATELLITE_FRONTIER_REANCHOR"}:
                qv = base.v2.base.quotes(self.book)
                role = self.key_role.get(key, "UNKNOWN")
                offer = self._offer(o["side"], role, o["price"], self._remaining(key), qv, key)
                choice = synthetic_keep_decision(synthetic=key in self.v2_synthetic_keys,
                    pair_ok=self._pair_ok(o["side"], o["price"]), price=o["price"],
                    frontier_price=offer.get("frontierPrice"), age_ms=t - o["placed"],
                    ttl_ms=base.v2.base.TTL, cancel_pending=bool(o.get("cancelRequested"))
                    or key in self.v2_ttl_attempts)
                apply_keep = self.v2_cell == CELLS[1] and choice["suppressPublicMembershipCancel"]
                route = {"key": key, "slotId": sid, "role": role, "side": o["side"],
                    "price": o["price"], "baselineCancelReason": reason,
                    "sameCarrierOffer": offer, "queueDecision": choice,
                    "selectedRoute": "KEEP" if apply_keep else "REANCHOR",
                    "cancelRequestedSuccessfully": False, "decisionId": self.detail_index + 1}
                self.v2_route_decisions.append(route)
                if apply_keep:
                    self.detail_counter["publicMembershipCancelsSuppressed"] += 1
                    return False
                ok = super()._request_cancel(t, sid, reason)
                route["cancelRequestedSuccessfully"] = bool(ok)
                return ok
            return super()._request_cancel(t, sid, reason)

        def _open_one_option(self, t, qv, end):
            self.detail_index += 1
            candidates, blocked = self._snapshot_candidates(t, qv)
            baseline = next((c for c in candidates if c.baseline_preferred), None)
            side, role, _, _ = base.MinimalPairRoleSim._role_decision(self, qv)
            offer = self._offer(side, role, baseline.price, baseline.qty, qv) if baseline else None
            state = self._state_snapshot(t, qv)
            pending = {}
            for s in ("UP", "DOWN"):
                rows = [{**o, "bid": float(qv[s]["bid"]),
                    "pairLegalNow": self._pair_ok(s, o["price"]),
                    "publicLevelPresent": base.v2.kprice(o["price"]) in self._live_price_levels(s)}
                    for o in state["liveSlots"] if o["side"] == s]
                opp = "DOWN" if s == "UP" else "UP"
                pending[s] = pending_reachability(rows, sum(q for q, _ in self.un[opp]))
            try:
                rejected_scores = v1.rank_candidates(candidates)
                score_error = None
            except (ValueError, OverflowError, ZeroDivisionError) as exc:
                rejected_scores, score_error = [], str(exc)
                self.detail_counter["scoreFallbacks"] += 1
            self.v2_open_intent = (side, role, qv)
            self.v2_offer = None
            start = len(self.slot_history)
            try:
                # Original role/side classifier and opener; only the placement seam
                # and synthetic-carrier membership cancellation can change B/C.
                base.MinimalPairRoleSim._open_one_option(self, t, qv, end)
            finally:
                self.v2_open_intent = None
            submissions = [dict(x) for x in self.slot_history[start:] if x.get("event") == "ROLE_SLOT_SUBMIT"]
            selected = None
            if submissions:
                actual = submissions[0]
                if actual["side"] != side or actual["role"] != role or baseline is None:
                    raise RuntimeError("New directional authority or candidate-without-baseline detected")
                selected = {**asdict(baseline), "price": actual["price"], "qty": actual["qty"],
                    "candidate_id": f"{role}:{side}:{actual['price']:.10f}",
                    "source": "SAME_INTENT_FRONTIER_V2" if self.detail_outcomes[actual["key"]]["frontierChanged"]
                    else baseline.source}
                self.detail_counter["routeAttempts"] += 1
                self.detail_role_selections[role] += 1
                self.detail_side_selections[side] += 1
            actions = [dict(x) for x in self.slot_history[self.detail_action_index:]
                       if x.get("event") in {"ROLE_SLOT_SUBMIT", "SLOT_CANCEL_REQUEST"}]
            self.detail_action_index = len(self.slot_history)
            fills = self.fill_side_sequence[self.detail_fill_index:]
            self.detail_fill_index = len(self.fill_side_sequence)
            reason = "SAME_BASELINE_INTENT" if selected else "INHERITED_OPENER_NO_SUBMISSION"
            row = {"decisionId": self.detail_index, "marketId": int(self.payload["marketId"]),
                "t": int(t), "normalizedPhase": 1 - (end - t) / 300000.0,
                "preActionState": self.v2_pre_reanchor, "state": state,
                "candidateCount": len(candidates), "candidateList": [asdict(c) for c in candidates],
                "candidateListMeaning": "LEGACY_DUAL_SIDE_DIAGNOSTIC; route variants below",
                "candidateScores": [{"route": "PASSIVE", "sameIntentFrontier": offer}],
                "rejectedV1DiagnosticScores": rejected_scores, "v1ScoreAuthority": False,
                "scoreError": score_error, "baselinePreferred": asdict(baseline) if baseline else None,
                "selectedCandidate": selected, "selectedRoute": "PASSIVE" if selected else "KEEP",
                "selectedTranche": selected["qty"] if selected else None,
                "legalButNotSelected": [c.candidate_id for c in candidates
                    if selected is None or c.candidate_id != selected["candidate_id"]],
                "sameIntentRouteDecisions": self.v2_route_decisions,
                "pendingReachability": pending,
                "boundedActive": {"status": "DEFERRED_NOT_IMPLEMENTED", "reason": "ISOLATE_PASSIVE_GEOMETRY_FIRST",
                    "sameIntentAskPairLegalDiagnostic": self._pair_ok(side, float(qv[side]["ask"])) if baseline else None},
                "generationDiagnostics": blocked, "reasonCodes": [reason],
                "actualSubmissions": submissions, "actionsSincePreviousDecision": actions,
                "ttlCancelAttemptsSincePreviousDecision": self.v2_ttl_events,
                "confirmedFillsSincePreviousDecision": fills,
                "subsequentPhysicalOutcome": {"joinFile": "cell result.orderOutcomes",
                    "joinKeys": [x["key"] for x in submissions],
                    "managedKeys": [x["key"] for x in self.v2_route_decisions]}}
            self.v2_ttl_events = []
            self.detail_stream.write(json.dumps(row, allow_nan=False) + "\n")

        def run_detail(self):
            result = super().run_detail()
            result.update({"v1ScoreAuthority": False, "activeRouteImplemented": False,
                "pendingRedundancyAuthority": False,
                "syntheticOrderKeys": sorted(self.v2_synthetic_keys),
                "tailTtlCancelAttempts": self.v2_ttl_events,
                "frontierChangedOrderFillQty": sum(f["confirmedQty"] for o in self.detail_outcomes.values()
                    if o.get("frontierChanged") for f in o["fills"]),
                "frontierChangedOrderRepairQty": sum(f["matchedRepairQty"] for o in self.detail_outcomes.values()
                    if o.get("frontierChanged") for f in o["fills"])})
            return result
    return ReachabilitySim


def first_divergence(a_path, b_path):
    prior = []
    with Path(a_path).open(encoding="utf-8") as a, Path(b_path).open(encoding="utf-8") as b:
        pairs = itertools.zip_longest(a, b)
        for al, bl in pairs:
            ar, br = json.loads(al) if al else None, json.loads(bl) if bl else None
            pair = {"baseline": ar, "candidate": br}
            if ar is None or br is None or (ar["t"], ar["decisionId"]) != (br["t"], br["decisionId"]):
                return {"kind": "TRACE_ALIGNMENT_FAILURE", "context": prior + [pair]}
            if (v1.physical_signature(ar) != v1.physical_signature(br)
                    or ar["ttlCancelAttemptsSincePreviousDecision"] != br["ttlCancelAttemptsSincePreviousDecision"]):
                following = [{"baseline": json.loads(x) if x else None,
                              "candidate": json.loads(y) if y else None} for x, y in itertools.islice(pairs, 2)]
                return {"kind": "PHYSICAL_ACTION_DIVERGENCE", "t": ar["t"], "decisionId": ar["decisionId"],
                    "samePreActionState": ar["preActionState"] == br["preActionState"],
                    "context": prior + [pair] + following}
            prior = (prior + [pair])[-2:]
    return None


def equal_value(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(equal_value(a[k], b[k]) for k in a)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-9)
    return a == b


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--market-ids", default="1830119")
    parser.add_argument("--output", required=True)
    parser.add_argument("--reference", required=True, help="Returned V1 full result used for source and A parity")
    args = parser.parse_args()
    mids = [int(x) for x in args.market_ids.split(",") if x.strip()]
    if not mids or len(set(mids)) != len(mids) or any(m not in {1830119, 1829115} for m in mids):
        parser.error("V2 is preregistered only for 1830119 and 1829115; prefer one market per invocation")
    reference = json.loads(Path(args.reference).read_text(encoding="utf-8"))
    output = Path(args.output).resolve()
    if output.exists():
        parser.error("Do not overwrite a result; use a new path")
    import tools.run_eth_role_separated_minimal_pair_safety_smoke as base
    if not math.isclose(float(base.v2.base.TTL), 5000.0):
        raise RuntimeError("Frozen TTL differs")
    module_paths = {"runner": Path(__file__),
        "coordinator": ROOT / "tools/eth_repair_modular/detail_intelligence_reachability_v2.py",
        "v1Instrumentation": Path(v1.__file__),
        "rejectedV1Score": ROOT / "tools/eth_repair_modular/detail_intelligence_coordinator_v1.py",
        "baseline": Path(base.__file__), "v3": Path(base.v3.__file__), "v2Resolved": Path(base.v2.__file__),
        "substrate": Path(base.v2.base.__file__), "execution": Path(base.v2.base.ex.__file__),
        "tapeFeed": Path(base.v2.base.feed.__file__)}
    for key, ref_key in [(k, k) for k in ("baseline", "v3", "v2Resolved", "substrate", "execution", "tapeFeed")] + [
            ("v1Instrumentation", "runner"), ("rejectedV1Score", "coordinator")]:
        if v1.sha256(module_paths[key]) != reference["sourceFiles"][ref_key]["sha256"]:
            raise RuntimeError(f"Frozen source mismatch: {key}")
    bundle_hash = v1.sha256(args.bundle)
    if bundle_hash != reference["bundleSha256"]:
        raise RuntimeError("Frozen bundle mismatch")
    Sim = simulator_class(base)
    output.parent.mkdir(parents=True, exist_ok=True)
    trace_dir = output.parent / (output.stem + "_traces")
    trace_dir.mkdir(exist_ok=False)
    rows, comparisons = [], []
    fields = ("submits", "fillEvents", "filledQty", "upQty", "downQty", "buyNotional", "floor", "best",
              "fillSideAlternations", "twoSidedMaterialized", "roleSubmits", "roleFills", "roleFillQty",
              "reanchors", "economicRepairQty", "economicOverflowQty")
    with tempfile.TemporaryDirectory(prefix="gpt6_reachability_external_") as folder:
        root = Path(folder)
        with zipfile.ZipFile(args.bundle) as archive:
            cohort = {int(r["marketId"]): r for r in json.loads(archive.read("cohort.json"))["rows"]}
            for mid in mids:
                archive.extract(f"tapes/{mid}.json.xz", root)
        for mid in mids:
            tape = root / "tapes" / f"{mid}.json.xz"
            old = next(r for r in reference["rows"] if r["marketId"] == mid and r["cell"] == CELLS[0])
            if v1.sha256(tape) != old["tapeSha256"]:
                raise RuntimeError("Frozen tape mismatch")
            by_cell, paths = {}, {}
            for cell in CELLS:
                paths[cell] = trace_dir / f"{mid}_{cell}.jsonl"
                sim = Sim(tape, cell, paths[cell])
                try:
                    result = sim.run_detail()
                finally:
                    sim.close()
                winner = str(cohort[mid]["winner"]).upper()
                if winner not in {"UP", "DOWN"}:
                    raise ValueError("Unsupported post-hoc winner")
                result["pnlDiagnosticOnly"] = result["upQty" if winner == "UP" else "downQty"] - result["buyNotional"]
                row = {"marketId": mid, "cell": cell, "winnerPostHocOnly": winner,
                       "tapeSha256": old["tapeSha256"], "decisionTracePath": str(paths[cell]), **result}
                by_cell[cell] = row
                rows.append(row)
                if cell == CELLS[0]:
                    row["baselineParity"] = {f: equal_value(row[f], old[f]) for f in fields}
                v1.write_json(trace_dir / f"{mid}_{cell}_result.json", row)
                if cell == CELLS[0] and not all(row["baselineParity"].values()):
                    raise RuntimeError("Baseline parity failed; candidate execution stopped")
                print(json.dumps({"marketId": mid, "cell": cell, "status": "CELL_COMPLETE"}), flush=True)
            for left, right in ((CELLS[0], CELLS[1]), (CELLS[0], CELLS[2]), (CELLS[2], CELLS[1])):
                a, b = by_cell[left], by_cell[right]
                d = first_divergence(paths[left], paths[right])
                comparison = {"marketId": mid, "baselineCell": left, "candidateCell": right,
                    "firstBehavioralDivergence": d,
                    "fillDelta": b["fillEvents"] - a["fillEvents"],
                    "repairQtyDelta": b["economicRepairQty"] - a["economicRepairQty"],
                    "pnlDelta": b["pnlDiagnosticOnly"] - a["pnlDiagnosticOnly"],
                    "floorDelta": b["floor"] - a["floor"],
                    "activityRetention": b["fillEvents"] / a["fillEvents"] if a["fillEvents"] else None}
                comparisons.append(comparison)
                if left == CELLS[0]:
                    b["firstBehavioralDivergence"] = d
                    b["decisionTraceAroundFirstDivergence"] = d["context"] if d else []
            for cell, row in by_cell.items():
                v1.write_json(trace_dir / f"{mid}_{cell}_result.json", row)
    v1.write_json(output, {"version": "GPT6_DETAIL_INTELLIGENCE_REACHABILITY_V2", "researchOnly": True,
        "runtimeAuthority": False, "status": "EXTERNAL_RESULT_REQUIRES_CAUSAL_REVIEW",
        "sourceFiles": {k: {"path": str(p.resolve()), "sha256": v1.sha256(p)} for k, p in module_paths.items()},
        "referenceSha256": v1.sha256(args.reference), "bundleSha256": bundle_hash,
        "markets": mids, "rows": rows, "comparison": comparisons,
        "frozen": {"roleSide": "MinimalPairRoleSim", "pairEconomics": "unchanged", "maxSlots": 4,
            "tickSize": TICK_SIZE, "quantityFormula": "1/p <= 12", "ttlMs": 5000, "latencyEachMs": 250,
            "queueModel": "risk", "noNewExposureWhenRemainingMsLe": 180000},
        "noNewHardGateAttestation": True, "noAutomaticScale": True})


if __name__ == "__main__":
    main()
