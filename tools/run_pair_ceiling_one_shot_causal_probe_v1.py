"""Research-only one-market causal probe for local pair-ceiling reachability.

A = frozen V2 same-intent frontier+retention behavior.
B = same V2 behavior, except the first inherited ECONOMIC_CORE/SATELLITE_REPAIR
    intent whose live best bid is above the Pair-legal frontier may place ONE
    Passive carrier at the live best bid. Pair-invalid reanchor remains inherited.
C = same one-shot best-bid intervention, but that one carrier is allowed to rest
    until inherited TTL instead of being canceled solely by ordinary reanchor /
    local Pair invalidation. No TTL extension, no Active route, no second override.

This is a falsification probe, NOT a candidate strategy and NOT runtime authority.
It intentionally violates the current local pair<=1 admission once to test whether
that ceiling is the direct cause of zero Repair materialization in market 1830119.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import tempfile
import zipfile

ROOT = Path.cwd() if (Path.cwd() / "tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_gpt6_detail_intelligence_v1_external as v1
from tools import run_gpt6_detail_intelligence_v2_external as v2ext
import tools.run_eth_role_separated_minimal_pair_safety_smoke as base

EPS = 1e-9
CELLS = (
    "A_V2_PAIR_LEGAL_RETENTION_BASELINE",
    "B_ONE_SHOT_BID_PAIR_SPEND_LEGACY_REANCHOR",
    "C_ONE_SHOT_BID_PAIR_SPEND_TTL_HOLD",
)
REPAIR_ROLES = {"ECONOMIC_CORE", "SATELLITE_REPAIR"}

V2Base = v2ext.simulator_class(base)


class PairCeilingProbeSim(V2Base):
    def __init__(self, tape, cell: str, trace_path: Path):
        # Freeze all ordinary behavior to the previously tested V2-B cell.
        super().__init__(tape, v2ext.CELLS[1], trace_path)
        self.probe_cell = cell
        self.probe_used = False
        self.probe_armed = False
        self.probe_qv = None
        self.probe_role = None
        self.probe_side = None
        self.probe_t = None
        self.probe_candidate = None
        self.probe_key = None
        self.probe_cancel_suppressed = 0

    def _open_one_option(self, t, qv, end):
        arm = False
        if self.probe_cell != CELLS[0] and not self.probe_used:
            side, role, _, _ = base.MinimalPairRoleSim._role_decision(self, qv)
            if role in REPAIR_ROLES:
                arm = True
                self.probe_armed = True
                self.probe_qv = qv
                self.probe_role = role
                self.probe_side = side
                self.probe_t = int(t)
        try:
            return super()._open_one_option(t, qv, end)
        finally:
            if arm:
                self.probe_armed = False
                self.probe_qv = None
                self.probe_role = None
                self.probe_side = None
                self.probe_t = None

    def _candidate_from_levels(self, side, require_pair=True, require_budget=False):
        original = super()._candidate_from_levels(side, require_pair, require_budget)
        if not self.probe_armed or self.probe_used or self.probe_cell == CELLS[0]:
            return original
        if original is None or side != self.probe_side or self.probe_role not in REPAIR_ROLES:
            return original
        qv = self.probe_qv
        bid = float(qv[side]["bid"])
        ask = float(qv[side]["ask"])
        p0, q0, proj = original
        used = {round(float(p), 10) for p in self._used_prices(side)}
        if not (EPS < bid < ask - EPS and round(bid, 10) not in used and bid > float(p0) + EPS):
            return original
        qty = 1.0 / bid
        if qty > 12.0 + EPS:
            return original
        opposite = "DOWN" if side == "UP" else "UP"
        lots = [(float(q), float(p)) for q, p in self.un[opposite]]
        gap = sum(q for q, _ in lots)
        if gap <= EPS:
            return original
        avg = sum(q * p for q, p in lots) / gap
        pair_sum = avg + bid
        if pair_sum <= 1.0000001 + EPS:
            # This probe exists only to falsify the local binary ceiling.
            return original
        self.probe_candidate = {
            "t": int(self.probe_t),
            "role": self.probe_role,
            "side": side,
            "pairLegalBaselinePrice": float(p0),
            "pairLegalBaselineQty": float(q0),
            "probePrice": bid,
            "probeQty": qty,
            "bid": bid,
            "ask": ask,
            "oppositeUnmatchedQty": gap,
            "oppositeUnmatchedAverage": avg,
            "probePairSum": pair_sum,
            "probePairOverage": pair_sum - 1.0,
            "intervention": "ONE_SHOT_CURRENT_BEST_BID_PASSIVE",
        }
        return bid, qty, proj

    def _submit_role(self, t, side, role, p, q, proj, source):
        before_n = self.n
        ok = super()._submit_role(t, side, role, p, q, proj, source)
        if (ok and self.probe_cell != CELLS[0] and not self.probe_used
                and self.probe_candidate is not None
                and role == self.probe_candidate["role"] and side == self.probe_candidate["side"]
                and abs(float(p) - self.probe_candidate["probePrice"]) <= EPS):
            self.probe_used = True
            self.probe_key = f"{side}_{before_n}"
            self.probe_candidate["key"] = self.probe_key
            self.probe_candidate["submittedAt"] = int(t)
        return ok

    def _request_cancel(self, t, sid, reason):
        key = self.slot_key.get(sid)
        if (self.probe_cell == CELLS[2] and key is not None and key == self.probe_key
                and reason in {"CORE_INVALIDATED", "SATELLITE_FRONTIER_REANCHOR"}):
            o = self.orders.get(key)
            if o is not None and int(t) - int(o["placed"]) < int(base.v2.base.TTL):
                # Do not extend TTL. We only isolate ordinary reanchor/pair invalidation.
                self.probe_cancel_suppressed += 1
                return False
        return super()._request_cancel(t, sid, reason)

    def run_probe(self):
        result = super().run_detail()
        outcome = next((x for x in result.get("orderOutcomes", []) if x.get("key") == self.probe_key), None)
        result.update({
            "probeCell": self.probe_cell,
            "probeUsed": bool(self.probe_used),
            "probeCandidate": self.probe_candidate,
            "probeKey": self.probe_key,
            "probeCancelSuppressed": int(self.probe_cancel_suppressed),
            "probeOutcome": outcome,
            "diagnosticOnlyPairSafetyViolation": bool(self.probe_used),
            "automaticScaleAllowed": False,
        })
        return result


def equal_value(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(equal_value(a[k], b[k]) for k in a)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-10, abs_tol=1e-9)
    return a == b


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--reference", required=True, help="Frozen V2 external result for A parity")
    ap.add_argument("--market-id", type=int, default=1830119)
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    if a.market_id != 1830119:
        ap.error("V1 causal probe is preregistered only for structural market 1830119")
    out_path = Path(a.output).resolve()
    if out_path.exists():
        ap.error("Do not overwrite a result")
    ref = json.loads(Path(a.reference).read_text(encoding="utf-8"))
    old = next(r for r in ref["rows"] if r["marketId"] == a.market_id and r["cell"] == v2ext.CELLS[1])
    if v1.sha256(a.bundle) != ref["bundleSha256"]:
        raise RuntimeError("Frozen bundle mismatch")
    trace_dir = out_path.parent / (out_path.stem + "_traces")
    trace_dir.mkdir(parents=True, exist_ok=False)
    rows = []
    parity_fields = (
        "submits", "fillEvents", "filledQty", "upQty", "downQty", "buyNotional",
        "floor", "best", "fillSideAlternations", "twoSidedMaterialized",
        "roleSubmits", "roleFills", "roleFillQty", "reanchors",
        "economicRepairQty", "economicOverflowQty",
    )
    with tempfile.TemporaryDirectory(prefix="pair_ceiling_probe_") as folder:
        root = Path(folder)
        with zipfile.ZipFile(a.bundle) as z:
            cohort = {int(r["marketId"]): r for r in json.loads(z.read("cohort.json"))["rows"]}
            z.extract(f"tapes/{a.market_id}.json.xz", root)
        tape = root / "tapes" / f"{a.market_id}.json.xz"
        if v1.sha256(tape) != old["tapeSha256"]:
            raise RuntimeError("Frozen tape mismatch")
        for cell in CELLS:
            tp = trace_dir / f"{a.market_id}_{cell}.jsonl"
            sim = PairCeilingProbeSim(tape, cell, tp)
            try:
                r = sim.run_probe()
            finally:
                sim.close()
            winner = str(cohort[a.market_id]["winner"]).upper()
            r["pnlDiagnosticOnly"] = r["upQty" if winner == "UP" else "downQty"] - r["buyNotional"]
            row = {"marketId": a.market_id, "cell": cell, "winnerPostHocOnly": winner,
                   "tapeSha256": old["tapeSha256"], "decisionTracePath": str(tp), **r}
            if cell == CELLS[0]:
                row["baselineParity"] = {f: equal_value(row[f], old[f]) for f in parity_fields}
                if not all(row["baselineParity"].values()):
                    raise RuntimeError("A baseline parity failed")
            rows.append(row)
            v1.write_json(trace_dir / f"{a.market_id}_{cell}_result.json", row)
            po = row.get("probeOutcome") or {}
            print(json.dumps({
                "marketId": a.market_id,
                "cell": cell,
                "fills": row["fillEvents"],
                "economicRepairQty": row["economicRepairQty"],
                "probeUsed": row["probeUsed"],
                "probePairSum": (row.get("probeCandidate") or {}).get("probePairSum"),
                "probeTerminal": (po.get("terminal") or {}).get("status"),
                "probeFillQty": sum(float(x.get("confirmedQty") or 0.0) for x in po.get("fills", [])),
                "probeCancelSuppressed": row["probeCancelSuppressed"],
            }), flush=True)
    result = {
        "version": "PAIR_CEILING_ONE_SHOT_CAUSAL_PROBE_V1",
        "date": "2026-09-06",
        "researchOnly": True,
        "runtimeAuthority": False,
        "marketId": a.market_id,
        "rows": rows,
        "referenceSha256": v1.sha256(a.reference),
        "bundleSha256": v1.sha256(a.bundle),
        "interpretationBoundary": [
            "one diagnostic violation of local pair<=1 admission maximum",
            "same inherited role and side",
            "Passive only; no Active route",
            "no Target runtime input; winner post-hoc only",
            "C suppresses only ordinary reanchor/local-pair invalidation for the probe carrier until inherited TTL",
            "not a strategy candidate and must not auto-scale",
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    v1.write_json(out_path, result)


if __name__ == "__main__":
    main()
