"""External execution only. Constructed without running strategy tests.

Uses the existing Pair-only lifecycle for both cells. Only _role_decision is
substituted during an opening opportunity in B; all execution methods are inherited.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import itertools
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Dependency imports are deliberately inside main; static review need not load HFT.
from tools.eth_repair_modular.detail_intelligence_coordinator_v1 import DetailCandidate, rank_candidates

EPS = 1e-9


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, allow_nan=False), encoding="utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def simulator_class(base):
    class InstrumentedPairSim(base.MinimalPairRoleSim):
        def __init__(self, tape, mode, trace_path):
            super().__init__(tape, 4, False)
            self.detail_mode = mode
            self.detail_stream = Path(trace_path).open("w", encoding="utf-8")
            self.detail_counter = Counter()
            self.detail_index = 0
            self.detail_override = None
            self.detail_key_decision = {}
            self.detail_side_selections = Counter()
            self.detail_role_selections = Counter()
            self.detail_action_index = len(self.slot_history)
            self.detail_fill_index = len(self.fill_side_sequence)
            self.detail_outcomes = {}
            self.detail_last_fill = {}
            self.detail_fill_accounting = []

        def record_fill(self, t, side, q, p):
            opposite = "DOWN" if side == "UP" else "UP"
            left, matched, credit = float(q), 0.0, 0.0
            for lot_qty, lot_price in self.un[opposite]:
                paid = min(left, float(lot_qty))
                matched += paid
                credit += paid * (1.0 - float(p) - float(lot_price))
                left -= paid
                if left <= 0:
                    break
            floor_before = self._physical_floor()
            super().record_fill(t, side, q, p)
            self.detail_fill_accounting.append({"t": int(t), "side": side,
                "confirmedQty": q, "executionPriceFromInheritedSubstrate": p,
                "matchedRepairQty": matched, "overflowQty": left,
                "realizedFifoPairCredit": credit, "floorBefore": floor_before,
                "floorAfter": self._physical_floor(), "key": None, "role": "UNKNOWN"})

        def close(self):
            try:
                super().close()
            finally:
                self.detail_stream.close()

        def _role_decision(self, qv):
            if self.detail_override is not None:
                c = self.detail_override
                return c.side, c.role, True, False
            return super()._role_decision(qv)

        def _submit_role(self, t, side, role, p, q, proj, source):
            key = f"{side}_{self.n}"
            ok = super()._submit_role(t, side, role, p, q, proj, source)
            if ok:
                self.detail_key_decision[key] = self.detail_index
                self.detail_outcomes[key] = {"decisionId": self.detail_index, "key": key,
                    "role": role, "side": side, "route": "PASSIVE", "submittedAt": int(t),
                    "submittedPrice": p, "submittedQty": q, "fills": [], "terminal": None}
            return ok

        def process(self, t):
            before = len(self.fill_side_sequence)
            accounting_before = len(self.detail_fill_accounting)
            super().process(t)
            new_fills = self.fill_side_sequence[before:]
            new_accounting = self.detail_fill_accounting[accounting_before:]
            if len(new_fills) != len(new_accounting):
                raise RuntimeError("Confirmed fill instrumentation alignment mismatch")
            for fill, accounting in zip(new_fills, new_accounting):
                accounting.update({"key": fill["key"], "role": fill["role"]})
                self.detail_last_fill[fill["key"]] = int(t)
                if fill["key"] in self.detail_outcomes:
                    self.detail_outcomes[fill["key"]]["fills"].append(dict(accounting))

        def _refresh_slots(self, t):
            start = len(self.slot_history)
            super()._refresh_slots(t)
            for event in self.slot_history[start:]:
                if event.get("event") == "SLOT_RELEASE":
                    key = event["key"]
                    if key in self.detail_outcomes:
                        self.detail_outcomes[key]["terminal"] = dict(event)

        def _snapshot_candidates(self, t, qv):
            # Call the original classifier explicitly; shadow collection cannot change A.
            baseline_side, baseline_role, _, _ = base.MinimalPairRoleSim._role_decision(self, qv)
            state, held, weak = self._state()
            candidates, blocked = [], []
            sides = [baseline_side] if state == "EMPTY" else [baseline_side, "DOWN" if baseline_side == "UP" else "UP"]
            for side in sides:
                if side == baseline_side:
                    role = baseline_role
                elif side == weak:
                    role = "ECONOMIC_CORE" if self._core_for_side(side) is None else "SATELLITE_REPAIR"
                else:
                    role = "SATELLITE_EXPAND"
                used = self._used_prices(side)
                found = None
                examined = 0
                for price in self._live_price_levels(side):
                    price = base.v2.kprice(price)
                    if price in used:
                        continue
                    examined += 1
                    if self._pair_ok(side, price):
                        found = price
                        break
                if found is None:
                    blocked.append({"side": side, "role": role,
                        "reason": "NO_UNUSED_PAIR_LEGAL_LEVEL", "levelsExamined": examined})
                    continue
                opposite = "DOWN" if side == "UP" else "UP"
                pending = sum(self._remaining(key) for _, key, _, _ in self._live_role_rows(side=side))
                candidates.append(DetailCandidate(
                    candidate_id=f"{role}:{side}:{found:.10f}", side=side, role=role,
                    price=found, qty=1.0 / found,
                    opposite_lots=tuple((float(q), float(p)) for q, p in self.un[opposite]),
                    pending_same_side_qty=pending, bid=float(qv[side]["bid"]),
                    ask=float(qv[side]["ask"]), up_imbalance=float(qv["imb"]),
                    baseline_preferred=side == baseline_side,
                ))
            return candidates, blocked

        def _open_one_option(self, t, qv, end):
            self.detail_index += 1
            candidates, blocked = self._snapshot_candidates(t, qv)
            try:
                scores = rank_candidates(candidates)
                score_error = None
            except (ValueError, OverflowError, ZeroDivisionError) as exc:
                scores, score_error = [], str(exc)
                self.detail_counter["scoreFallbacks"] += 1
            baseline_choice = next((c for c in candidates if c.baseline_preferred), None)
            choice = baseline_choice
            if self.detail_mode == "B_GPT6_DETAIL_INTELLIGENCE_V1" and scores:
                choice = next(c for c in candidates if c.candidate_id == scores[0]["candidate"]["candidate_id"])
            if self.detail_mode == "C_ENUMERATION_ONLY_CONTROL" and choice is None and candidates:
                choice = candidates[0]
            eligible_time = int(end) - int(t) > base.v2.NO_NEW_EXPOSURE_MS
            free = len(self.slot_key) < self.max_slots
            can_schedule = eligible_time and free
            selected = choice if can_schedule else None
            if selected:
                self.detail_role_selections[selected.role] += 1
                self.detail_side_selections[selected.side] += 1
                self.detail_counter["routeAttempts"] += 1
                for candidate in candidates:
                    if candidate != selected:
                        prefix = "repair" if candidate.role in {"ECONOMIC_CORE", "SATELLITE_REPAIR"} else "expand"
                        self.detail_counter[prefix + "CandidateAvailableButNotSelectedCount"] += 1
            state = {
                "inventory": dict(self.inv), "cost": self.cost,
                "floor": self._physical_floor(), "best": max(self.inv.values()) - self.cost,
                "unmatchedInventoryProxy": {side: sum(q for q, _ in self.un[side]) for side in ("UP", "DOWN")},
                "semanticResponsibilityProgress": "UNKNOWN",
                "book": qv,
                "liveSlots": [{"slotId": sid, "key": key, "role": role,
                    "side": o["side"], "price": o["price"], "qty": o["qty"],
                    "cum": o["cum"], "remainingQty": self._remaining(key),
                    "ageMs": int(t) - int(o["placed"]), "status": o.get("status"),
                    "cancelRequested": bool(o.get("cancelRequested")),
                    "lastConfirmedFillAt": self.detail_last_fill.get(key)}
                    for sid, key, o, role in self._live_role_rows()],
                "recentFillSideSequence": [x["side"] for x in self.fill_side_sequence[-12:]],
                "recentRoleSequence": [x["role"] for x in self.fill_side_sequence[-12:]],
            }
            # Same inherited opener in A/B; no independent submit/cancel implementation.
            before_history = len(self.slot_history)
            self.detail_override = choice if self.detail_mode != "A_PAIR_ONLY_BASELINE" else None
            try:
                super()._open_one_option(t, qv, end)
            finally:
                self.detail_override = None
            submissions = [dict(x) for x in self.slot_history[before_history:] if x.get("event") == "ROLE_SLOT_SUBMIT"]
            actions = [dict(x) for x in self.slot_history[self.detail_action_index:]
                       if x.get("event") in {"ROLE_SLOT_SUBMIT", "SLOT_CANCEL_REQUEST"}]
            self.detail_action_index = len(self.slot_history)
            newly_observed_fills = self.fill_side_sequence[self.detail_fill_index:]
            self.detail_fill_index = len(self.fill_side_sequence)
            from dataclasses import asdict
            reason = "MARKET_TIME_BOUNDARY" if not eligible_time else "STRUCTURAL_CAPACITY_FULL" if not free else (
                "BASELINE_SIDE_HAS_NO_CANDIDATE" if choice is None and candidates else "NO_LEGAL_CANDIDATE" if choice is None else "BASELINE_PRESELECTION" if self.detail_mode == "A_PAIR_ONLY_BASELINE"
                else "BASELINE_FALLBACK" if score_error else "MAX_CONTINUOUS_VALUE" if self.detail_mode.startswith("B_") else "ENUMERATION_ONLY")
            row = {"decisionId": self.detail_index, "marketId": int(self.payload["marketId"]),
                "t": int(t), "normalizedPhase": 1.0 - (int(end) - int(t)) / 300000.0,
                "state": state, "candidateCount": len(candidates),
                "candidateList": [asdict(c) for c in candidates], "candidateScores": scores,
                "baselinePreferred": asdict(baseline_choice) if baseline_choice else None,
                "selectedCandidate": asdict(selected) if selected else None,
                "selectedRoute": selected.route if selected else "KEEP",
                "selectedTranche": selected.qty if selected else None,
                "legalButNotSelected": [c.candidate_id for c in candidates if c != selected],
                "generationDiagnostics": blocked, "reasonCodes": [reason], "scoreError": score_error,
                "actualSubmissions": submissions, "actionsSincePreviousDecision": actions,
                "confirmedFillsSincePreviousDecision": newly_observed_fills,
                "subsequentPhysicalOutcome": {"joinFile": "cell result.orderOutcomes", "joinKeys": [x["key"] for x in submissions]},
            }
            self.detail_stream.write(json.dumps(row, allow_nan=False) + "\n")

        def run_detail(self):
            # Winner reaches only external scoring after execution finishes.
            result = self.run_minimal("__UNSCORED__")
            result.pop("pnlDiagnosticOnly", None)
            self.detail_stream.flush()
            outcomes = list(self.detail_outcomes.values())
            counters = dict(self.detail_counter)
            for key in ("repairCandidateAvailableButNotSelectedCount", "expandCandidateAvailableButNotSelectedCount", "routeAttempts", "scoreFallbacks"):
                counters.setdefault(key, 0)
            counters.update({"pairEconomicsBlocks": self.minimal_pair_blocks,
                "slotCapacityBlocks": self.veto.get("GLOBAL_SLOT_CAP_FULL", 0) + self.veto.get("SIDE_SLOT_CAP_FULL", 0),
                "passiveTerminalZeroFillCount": sum(x["terminal"] is not None and not x["fills"] for x in outcomes),
                "activeRelayAttemptsIfAny": 0, "activeRelayFillsIfAny": 0,
                "roleSelectionCounts": dict(self.detail_role_selections),
                "sideSelectionCounts": dict(self.detail_side_selections)})
            result.update({"diagnosticCounters": counters, "orderOutcomes": outcomes,
                "decisionTraceCount": self.detail_index,
                "fullFillSideSequence": self.fill_side_sequence,
                "confirmedFillAccounting": self.detail_fill_accounting,
                "economicRepairQty": sum(x["matchedRepairQty"] for x in self.detail_fill_accounting),
                "economicOverflowQty": sum(x["overflowQty"] for x in self.detail_fill_accounting),
                "fillSideSequencePriceMeaning": "SUBMITTED_PRICE; execution price in confirmedFillAccounting",
                "tailActions": self.slot_history[self.detail_action_index:]})
            return result
    return InstrumentedPairSim


def physical_signature(row):
    return [(x["event"], x.get("key"), x.get("side"), x.get("role"),
             x.get("price"), x.get("qty"), x.get("reason"))
            for x in row.get("actionsSincePreviousDecision", [])]


def first_divergence(a_path, b_path):
    # Stream the entire trace: no prefix cap can hide a late first divergence.
    prior = []
    with Path(a_path).open(encoding="utf-8") as a, Path(b_path).open(encoding="utf-8") as b:
        pairs = itertools.zip_longest(a, b)
        for a_line, b_line in pairs:
            ar = json.loads(a_line) if a_line else None
            br = json.loads(b_line) if b_line else None
            pair = {"baseline": ar, "candidate": br}
            if ar is None or br is None or (ar["t"], ar["decisionId"]) != (br["t"], br["decisionId"]):
                return {"kind": "TRACE_ALIGNMENT_FAILURE", "context": prior + [pair]}
            if physical_signature(ar) != physical_signature(br):
                following = [{"baseline": json.loads(x) if x else None,
                              "candidate": json.loads(y) if y else None}
                             for x, y in itertools.islice(pairs, 2)]
                return {"kind": "PHYSICAL_ACTION_DIVERGENCE", "t": ar["t"],
                    "decisionId": ar["decisionId"], "samePreActionState": ar["state"] == br["state"],
                    "context": prior + [pair] + following}
            prior = (prior + [pair])[-2:]
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--market-ids", default="1830119,1829115")
    parser.add_argument("--output", required=True)
    parser.add_argument("--include-enumeration-control", action="store_true")
    args = parser.parse_args()
    mids = [int(x) for x in args.market_ids.split(",") if x.strip()]
    if not 1 <= len(mids) <= 3 or len(set(mids)) != len(mids):
        parser.error("Use 1-3 distinct structural markets for this first external test")
    output = Path(args.output).resolve()
    if output.exists():
        parser.error("Output already exists; choose a new result path")
    output.parent.mkdir(parents=True, exist_ok=True)
    trace_dir = output.parent / (output.stem + "_traces")
    trace_dir.mkdir(exist_ok=False)
    import tools.run_eth_role_separated_minimal_pair_safety_smoke as base
    Sim = simulator_class(base)
    module_paths = {"runner": Path(__file__), "coordinator": ROOT / "tools/eth_repair_modular/detail_intelligence_coordinator_v1.py",
        "baseline": Path(base.__file__), "v3": Path(base.v3.__file__),
        "v2Resolved": Path(base.v2.__file__), "substrate": Path(base.v2.base.__file__),
        "execution": Path(base.v2.base.ex.__file__), "tapeFeed": Path(base.v2.base.feed.__file__)}
    cells = ["A_PAIR_ONLY_BASELINE", "B_GPT6_DETAIL_INTELLIGENCE_V1"]
    if args.include_enumeration_control:
        cells.append("C_ENUMERATION_ONLY_CONTROL")
    rows, comparison = [], []
    with tempfile.TemporaryDirectory(prefix="gpt6_detail_external_") as folder:
        root = Path(folder)
        with zipfile.ZipFile(args.bundle) as archive:
            # Extract only selected tapes. Cohort labels remain in the external scorer.
            cohort = json.loads(archive.read("cohort.json"))
            by_market = {int(r["marketId"]): r for r in cohort["rows"]}
            for mid in mids:
                if mid not in by_market:
                    raise ValueError(f"Market absent from cohort: {mid}")
                name = f"tapes/{mid}.json.xz"
                archive.extract(name, root)
        for mid in mids:
            tape = root / "tapes" / f"{mid}.json.xz"
            paths, market_rows = {}, {}
            for cell in cells:
                paths[cell] = trace_dir / f"{mid}_{cell}.jsonl"
                sim = Sim(tape, cell, paths[cell])
                try:
                    result = sim.run_detail()
                finally:
                    sim.close()
                winner = str(by_market[mid]["winner"]).upper()
                if winner not in {"UP", "DOWN"}:
                    raise ValueError("Unsupported post-hoc winner")
                result["pnlDiagnosticOnly"] = result["upQty" if winner == "UP" else "downQty"] - result["buyNotional"]
                row = {"marketId": mid, "cell": cell, "winnerPostHocOnly": winner,
                    "tapeSha256": sha256(tape), "decisionTracePath": str(paths[cell]), **result}
                rows.append(row)
                market_rows[cell] = row
                write_json(trace_dir / f"{mid}_{cell}_result.json", row)
            a = market_rows[cells[0]]
            for cell in cells[1:]:
                b = market_rows[cell]
                divergence = first_divergence(paths[cells[0]], paths[cell])
                b["firstBehavioralDivergence"] = divergence
                b["decisionTraceAroundFirstDivergence"] = divergence["context"] if divergence else []
                comparison.append({"marketId": mid, "cell": cell,
                    "firstBehavioralDivergence": divergence,
                    "fillDelta": b["fillEvents"] - a["fillEvents"],
                    "filledQtyDelta": b["filledQty"] - a["filledQty"],
                    "pnlDelta": b["pnlDiagnosticOnly"] - a["pnlDiagnosticOnly"],
                    "floorDelta": b["floor"] - a["floor"],
                    "activityRetention": b["fillEvents"] / a["fillEvents"] if a["fillEvents"] else None})
            a["firstBehavioralDivergence"] = market_rows[cells[1]]["firstBehavioralDivergence"]
            a["decisionTraceAroundFirstDivergence"] = market_rows[cells[1]]["decisionTraceAroundFirstDivergence"]
            for cell, row in market_rows.items():
                write_json(trace_dir / f"{mid}_{cell}_result.json", row)
    out = {"version": "GPT6_DETAIL_INTELLIGENCE_COORDINATOR_V1", "researchOnly": True,
        "runtimeAuthority": False, "status": "EXTERNAL_RESULT_REQUIRES_CAUSAL_REVIEW",
        "sourceFiles": {k: {"path": str(p.resolve()), "sha256": sha256(p)} for k, p in module_paths.items()},
        "bundleSha256": sha256(args.bundle), "markets": mids, "rows": rows, "comparison": comparison,
        "frozen": {"maxSlots": 4, "pairEconomics": "inherited unchanged", "route": "PASSIVE",
            "priceAndQty": "inherited first legal level and 1/p", "latencyMs": 250,
            "queue": "risk", "ttlMs": 5000, "newExposureBoundaryMs": 180000},
        "noNewHardGateAttestation": True}
    write_json(output, out)


if __name__ == "__main__":
    main()
