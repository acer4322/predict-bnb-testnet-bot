"""Research-only one-market probe for Passive queue priority / inside-spread placement.

A = frozen V2 same-intent frontier+retention baseline.
B = first inherited Core/Repair intent receives ONE Passive GTX quote at ask-1 tick;
    inherited ordinary reanchor / Pair invalidation remains unchanged.
C = same one-shot inside-spread GTX quote, but ordinary reanchor/local-Pair
    invalidation for that one carrier is suppressed until inherited TTL.

No Active route. No repeated override. Same inherited role/side. This intentionally
spends more local pair value once to isolate execution priority. It is not a
strategy candidate and must not auto-scale.
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
TICK = 0.01
REPAIR_ROLES = {"ECONOMIC_CORE", "SATELLITE_REPAIR"}
CELLS = (
    "A_V2_PAIR_LEGAL_RETENTION_BASELINE",
    "B_ONE_SHOT_INSIDE_SPREAD_LEGACY_REANCHOR",
    "C_ONE_SHOT_INSIDE_SPREAD_TTL_HOLD",
)
V2Base = v2ext.simulator_class(base)


class InsideSpreadProbeSim(V2Base):
    def __init__(self, tape, cell: str, trace_path: Path):
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
        armed_here = False
        if self.probe_cell != CELLS[0] and not self.probe_used:
            side, role, _, _ = base.MinimalPairRoleSim._role_decision(self, qv)
            if role in REPAIR_ROLES:
                armed_here = True
                self.probe_armed = True
                self.probe_qv = qv
                self.probe_role = role
                self.probe_side = side
                self.probe_t = int(t)
        try:
            return super()._open_one_option(t, qv, end)
        finally:
            if armed_here:
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
        # Closest non-crossing maker tick in the current strict-past book.
        price = round(ask - TICK, 10)
        used = {round(float(p), 10) for p in self._used_prices(side)}
        if not (EPS < bid < price < ask - EPS and price not in used and price > float(p0) + EPS):
            return original
        qty = 1.0 / price
        if qty > 12.0 + EPS:
            return original
        opposite = "DOWN" if side == "UP" else "UP"
        lots = [(float(q), float(p)) for q, p in self.un[opposite]]
        gap = sum(q for q, _ in lots)
        if gap <= EPS:
            return original
        avg = sum(q * p for q, p in lots) / gap
        pair_sum = avg + price
        if pair_sum <= 1.0000001 + EPS:
            # This probe is specifically for costly execution priority.
            return original
        self.probe_candidate = {
            "t": int(self.probe_t),
            "role": self.probe_role,
            "side": side,
            "pairLegalBaselinePrice": float(p0),
            "pairLegalBaselineQty": float(q0),
            "probePrice": price,
            "probeQty": qty,
            "bidAtDecision": bid,
            "askAtDecision": ask,
            "insideSpreadTicksAheadOfBid": (price - bid) / TICK,
            "oppositeUnmatchedQty": gap,
            "oppositeUnmatchedAverage": avg,
            "probePairSum": pair_sum,
            "probePairOverage": pair_sum - 1.0,
            "route": "PASSIVE_GTX_POST_ONLY",
            "intervention": "ONE_SHOT_ASK_MINUS_ONE_TICK",
        }
        return price, qty, proj

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
        if (self.probe_cell == CELLS[2] and key == self.probe_key
                and reason in {"CORE_INVALIDATED", "SATELLITE_FRONTIER_REANCHOR"}):
            o = self.orders.get(key)
            if o is not None and int(t) - int(o["placed"]) < int(base.v2.base.TTL):
                self.probe_cancel_suppressed += 1
                return False
        return super()._request_cancel(t, sid, reason)

    def run_probe(self):
        r = super().run_detail()
        outcome = next((x for x in r.get("orderOutcomes", []) if x.get("key") == self.probe_key), None)
        snap = None
        if self.probe_key and self.probe_key in self.orders:
            try:
                snap = self.snap(self.orders[self.probe_key])
            except Exception as exc:
                snap = {"snapshotError": str(exc)}
        r.update({
            "probeCell": self.probe_cell,
            "probeUsed": bool(self.probe_used),
            "probeCandidate": self.probe_candidate,
            "probeKey": self.probe_key,
            "probeCancelSuppressed": int(self.probe_cancel_suppressed),
            "probeOutcome": outcome,
            "probeFinalOrderSnapshot": snap,
            "diagnosticOnlyLocalPairSafetyViolation": bool(self.probe_used),
            "automaticScaleAllowed": False,
        })
        return r


def eq(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(eq(a[k], b[k]) for k in a)
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
        ap.error("V1 preregistered only for structural market 1830119")
    op = Path(a.output).resolve()
    if op.exists():
        ap.error("Do not overwrite result")
    ref = json.loads(Path(a.reference).read_text(encoding="utf-8"))
    old = next(r for r in ref["rows"] if r["marketId"] == a.market_id and r["cell"] == v2ext.CELLS[1])
    if v1.sha256(a.bundle) != ref["bundleSha256"]:
        raise RuntimeError("Frozen bundle mismatch")
    trace_dir = op.parent / (op.stem + "_traces")
    trace_dir.mkdir(parents=True, exist_ok=False)
    parity_fields = (
        "submits", "fillEvents", "filledQty", "upQty", "downQty", "buyNotional",
        "floor", "best", "fillSideAlternations", "twoSidedMaterialized",
        "roleSubmits", "roleFills", "roleFillQty", "reanchors",
        "economicRepairQty", "economicOverflowQty",
    )
    rows = []
    with tempfile.TemporaryDirectory(prefix="inside_spread_probe_") as folder:
        root = Path(folder)
        with zipfile.ZipFile(a.bundle) as z:
            cohort = {int(r["marketId"]): r for r in json.loads(z.read("cohort.json"))["rows"]}
            z.extract(f"tapes/{a.market_id}.json.xz", root)
        tape = root / "tapes" / f"{a.market_id}.json.xz"
        if v1.sha256(tape) != old["tapeSha256"]:
            raise RuntimeError("Frozen tape mismatch")
        for cell in CELLS:
            tp = trace_dir / f"{a.market_id}_{cell}.jsonl"
            sim = InsideSpreadProbeSim(tape, cell, tp)
            try:
                r = sim.run_probe()
            finally:
                sim.close()
            winner = str(cohort[a.market_id]["winner"]).upper()
            r["pnlDiagnosticOnly"] = r["upQty" if winner == "UP" else "downQty"] - r["buyNotional"]
            row = {"marketId": a.market_id, "cell": cell, "winnerPostHocOnly": winner,
                   "tapeSha256": old["tapeSha256"], "decisionTracePath": str(tp), **r}
            if cell == CELLS[0]:
                row["baselineParity"] = {f: eq(row[f], old[f]) for f in parity_fields}
                if not all(row["baselineParity"].values()):
                    raise RuntimeError("A baseline parity failed")
            rows.append(row)
            v1.write_json(trace_dir / f"{a.market_id}_{cell}_result.json", row)
            po = row.get("probeOutcome") or {}
            pc = row.get("probeCandidate") or {}
            print(json.dumps({
                "marketId": a.market_id, "cell": cell,
                "submits": row["submits"], "fills": row["fillEvents"],
                "economicRepairQty": row["economicRepairQty"],
                "alternations": row["fillSideAlternations"],
                "probeUsed": row["probeUsed"], "probePrice": pc.get("probePrice"),
                "probePairSum": pc.get("probePairSum"),
                "probeTerminal": (po.get("terminal") or {}).get("status"),
                "probeFillQty": sum(float(x.get("confirmedQty") or 0.0) for x in po.get("fills", [])),
                "probeRepairQty": sum(float(x.get("matchedRepairQty") or 0.0) for x in po.get("fills", [])),
                "probeCancelSuppressed": row["probeCancelSuppressed"],
                "finalSnapshot": row.get("probeFinalOrderSnapshot"),
            }, allow_nan=False), flush=True)
    result = {
        "version": "INSIDE_SPREAD_MAKER_PRIORITY_ONE_SHOT_PROBE_V1",
        "date": "2026-09-06", "researchOnly": True, "runtimeAuthority": False,
        "marketId": a.market_id, "rows": rows,
        "referenceSha256": v1.sha256(a.reference), "bundleSha256": v1.sha256(a.bundle),
        "boundary": [
            "same inherited role/side", "one diagnostic inside-spread Passive GTX override maximum",
            "no Active route", "no repeated override", "C retains only until inherited TTL",
            "no Target runtime input; winner post-hoc only", "not a strategy candidate; no auto-scale"
        ]
    }
    op.parent.mkdir(parents=True, exist_ok=True)
    v1.write_json(op, result)


if __name__ == "__main__":
    main()
