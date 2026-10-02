from __future__ import annotations

"""Extract clean PRE-SUBMIT decision rows from current V3B exact-FIFO HFT.

The original Phase-A world-model trace records `_state_row()` after a successful
submit. That is valid for execution-world prediction, but live-slot and managed-
ladder occupancy can already include the candidate carrier. This extractor records
state immediately BEFORE the physical submit, while delegating the actual submit
and lifecycle to the unchanged current V3B implementation.

Research-only. No action authority. No winner/Target future in runtime state.
"""

import argparse
import collections
import json
import math
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path.cwd().resolve() if (Path.cwd() / "tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b

EPS = v3b.EPS
base = v3b.base
H = (3, 5)


def safe(x, d=0.0):
    try:
        z = float(x)
        return z if math.isfinite(z) else d
    except Exception:
        return d


def opp(s):
    return "DOWN" if s == "UP" else "UP"


def _physical_core(r: dict) -> dict:
    keys = (
        "submits", "fillEvents", "filledQty", "upQty", "downQty", "buyNotional",
        "floor", "best", "fillSideAlternations", "twoSidedMaterialized",
        "roleSubmits", "roleFills", "roleFillQty", "reanchors",
        "economicRepairQty", "economicOverflowQty",
    )
    return {k: r.get(k) for k in keys}


def _eq(a, b) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_eq(a[k], b[k]) for k in a)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-10, abs_tol=1e-9)
    return a == b


class PreActionTrainingTraceSim(v3b.FifoAggregateResponsibilityLadderV3B):
    def __init__(self, tape):
        super().__init__(tape)
        self.training_rows = []
        self._end_ms = int((self.payload.get("market") or {}).get("window_end_ms") or self.meta["lastReceivedMs"])

    def _state_row(self, t, side, role, price, qty, route, key, source):
        qv = base.v2.base.quotes(self.book) or {}
        u = float(self.inv["UP"])
        d = float(self.inv["DOWN"])
        gross = u + d
        dom = "UP" if u > d + EPS else "DOWN" if d > u + EPS else "FLAT"
        debt_up = float(sum(float(x["remainingQty"]) for x in self.resp_queues["UP"]))
        debt_dn = float(sum(float(x["remainingQty"]) for x in self.resp_queues["DOWN"]))
        oldest = None
        rs = opp(side)
        if self.resp_queues[rs]:
            oldest = self.resp_queues[rs][0]
        old_frac = 0.0
        if oldest is not None and float(oldest.get("initialQty") or 0) > EPS:
            old_frac = float(oldest.get("paidQty") or 0) / float(oldest["initialQty"])
        live = []
        pending = 0
        for sid, k in self.slot_key.items():
            o = self.orders.get(k)
            if not o:
                continue
            live.append((sid, k, o, self.key_role.get(k, "UNASSIGNED")))
            if o.get("cancelRequested"):
                pending += 1
        side_live = sum(1 for _, _, o, _ in live if str(o.get("side")) == side)
        repair_live = sum(1 for _, _, _, r in live if r in {"ECONOMIC_CORE", "SATELLITE_REPAIR"})
        expand_live = sum(1 for _, _, _, r in live if r == "SATELLITE_EXPAND")
        bid = safe((qv.get(side) or {}).get("bid"))
        ask = safe((qv.get(side) or {}).get("ask"))
        mid = (bid + ask) / 2 if ask > 0 else bid
        dbid = safe((qv.get(dom) or {}).get("bid")) if dom in {"UP", "DOWN"} else 0.0
        dask = safe((qv.get(dom) or {}).get("ask")) if dom in {"UP", "DOWN"} else 0.0
        dmid = (dbid + dask) / 2 if dask > 0 else dbid
        outstanding_for_side = float(sum(float(x["remainingQty"]) for x in self.resp_queues[opp(side)]))
        return {
            "t": int(t), "key": str(key), "side": str(side), "role": str(role), "route": str(route),
            "source": str(source), "price": float(price), "qty": float(qty),
            "secondsLeft": max(0.0, (self._end_ms - int(t)) / 1000.0),
            "upQty": u, "downQty": d, "cost": float(self.cost),
            "floor": float(min(u, d) - self.cost), "best": float(max(u, d) - self.cost),
            "absNet": abs(u - d), "coverage": (2 * min(u, d) / gross if gross > EPS else 0.0),
            "gross": gross, "dominantSide": dom,
            "debtUp": debt_up, "debtDown": debt_dn, "totalDebt": debt_up + debt_dn,
            "targetDebtForActionSide": outstanding_for_side,
            "oldestRepairProgress": old_frac,
            "oldestRepairAgeMs": (int(t) - int(oldest["bornAt"]) if oldest else 0),
            "responsibilityCount": len(self.resp_all),
            "liveSlots": len(live), "sideLiveSlots": side_live,
            "repairLiveSlots": repair_live, "expandLiveSlots": expand_live,
            "pendingCancelCount": pending,
            "bookImbalance": safe(qv.get("imb")), "spread": safe(qv.get("spread")),
            "sideBid": bid, "sideAsk": ask, "sideMid": mid, "dominantMid": dmid,
            "priceToBid": float(price) - bid, "askToPrice": ask - float(price),
            "pairLegal": 1.0 if self._pair_ok(side, float(price)) else 0.0,
            "qLadderLive": 1.0 if self.q_ladder is not None else 0.0,
            "qPendingActive": 1.0 if self.q_pending_active is not None else 0.0,
            "isRepairRole": 1.0 if role in {"ECONOMIC_CORE", "SATELLITE_REPAIR"} else 0.0,
            "isExpandRole": 1.0 if role == "SATELLITE_EXPAND" else 0.0,
            "sideIsDominant": 1.0 if dom == side else 0.0,
            "sideIsWeak": 1.0 if dom in {"UP", "DOWN"} and dom != side else 0.0,
            "stateTiming": "PRE_SUBMIT_DECISION",
            "candidateAlreadyInState": 0,
        }

    def _submit_role(self, t, side, role, p, q, proj, source):
        before_n = self.n
        key = f"{side}_{before_n}"
        pre = self._state_row(t, side, role, p, q, "PASSIVE", key, source)
        ok = v3b.FifoAggregateResponsibilityLadderV3B._submit_role(self, t, side, role, p, q, proj, source)
        if ok:
            self.training_rows.append(pre)
        return ok

    def _submit_protected_active_qty(self, t, qv):
        pnd = self.q_pending_active
        ladder = self.q_ladder
        pre = None
        before_n = self.n
        if pnd is not None and ladder is not None:
            target = self._aggregate_outstanding_expand_side(pnd["targetExpandSide"])
            side = str(pnd["side"])
            role = str(pnd["role"])
            ask = float(qv[side]["ask"])
            limit = round(min(0.99, ask + v3b.TICK), 10)
            qty = min(float(pnd["sourceRemainingQty"]), target)
            if target > EPS and qty > EPS:
                pre = self._state_row(t, side, role, limit, qty, "ACTIVE", f"{side}_{before_n}", "PROTECTED_ACTIVE")
        ok = v3b.FifoAggregateResponsibilityLadderV3B._submit_protected_active_qty(self, t, qv)
        if ok and pre is not None:
            self.training_rows.append(pre)
        return ok

    def finalize_labels(self):
        fills = collections.defaultdict(list)
        for x in self.fill_accounting:
            fills[str(x["key"])].append(x)
        cancels = collections.defaultdict(list)
        terms = collections.defaultdict(list)
        for x in self.slot_history:
            if x.get("event") == "SLOT_CANCEL_REQUEST":
                cancels[str(x.get("key"))].append(x)
            elif x.get("event") == "SLOT_RELEASE":
                terms[str(x.get("key"))].append(x)
        for r in self.training_rows:
            t0 = int(r["t"])
            key = r["key"]
            for h in H:
                t1 = t0 + h * 1000
                fs = [x for x in fills.get(key, []) if t0 < int(x["t"]) <= t1]
                r[f"fillQty{h}s"] = float(sum(float(x["confirmedQty"]) for x in fs))
                r[f"anyFill{h}s"] = 1 if r[f"fillQty{h}s"] > EPS else 0
                r[f"repairPayQty{h}s"] = float(sum(float(x.get("matchedRepairQty") or 0) for x in fs))
                r[f"overflowQty{h}s"] = float(sum(float(x.get("overflowQty") or 0) for x in fs))
                r[f"cancelReq{h}s"] = 1 if any(t0 < int(x.get("t") or 0) <= t1 for x in cancels.get(key, [])) else 0
                r[f"terminal{h}s"] = 1 if any(t0 < int(x.get("t") or 0) <= t1 for x in terms.get(key, [])) else 0
            o = self.orders.get(key) or {}
            r["finalCum"] = float(o.get("cum") or 0.0)
            r["eventualFill"] = 1 if r["finalCum"] > EPS else 0
        return self.training_rows


def _market_rows_from_bundle(bundle: Path) -> list[dict]:
    with zipfile.ZipFile(bundle) as z:
        return sorted(
            json.loads(z.read("cohort.json"))["rows"],
            key=lambda r: (int(r.get("windowEndMs") or 0), int(r["marketId"])),
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True, type=Path)
    ap.add_argument("--market-ids", help="comma-separated; default = all cohort markets")
    ap.add_argument("--output", required=True, help="result json path or AUTO")
    ap.add_argument("--rows-output", help="JSONL path; default beside output")
    ap.add_argument("--verify-baseline", action="store_true", help="run plain V3B too; smoke only")
    a = ap.parse_args()

    bundle = a.bundle.resolve()
    cohort = _market_rows_from_bundle(bundle)
    if a.market_ids:
        selected = {int(x) for x in a.market_ids.split(",") if x.strip()}
        cohort = [r for r in cohort if int(r["marketId"]) in selected]
        missing = sorted(selected - {int(r["marketId"]) for r in cohort})
        if missing:
            raise SystemExit(f"market ids missing from cohort: {missing}")

    if str(a.output).upper() == "AUTO":
        result_dir = Path(os.environ["BTC5M_LAN_RESULT_DIR"])
        out = result_dir / "result.json"
    else:
        out = Path(a.output)
        result_dir = out.parent
    rows_out = Path(a.rows_output) if a.rows_output else result_dir / "preaction_rows.jsonl"
    result_dir.mkdir(parents=True, exist_ok=True)

    all_rows = []
    market_reports = []
    with tempfile.TemporaryDirectory(prefix="mgmt_v1_preaction_") as td:
        root = Path(td)
        with zipfile.ZipFile(bundle) as z:
            for cr in cohort:
                z.extract(f"tapes/{int(cr['marketId'])}.json.xz", root)
        for i, cr in enumerate(cohort, 1):
            mid = int(cr["marketId"])
            tape_path = root / "tapes" / f"{mid}.json.xz"
            sim = PreActionTrainingTraceSim(tape_path)
            try:
                r = sim.run_qty("__UNSCORED__")
                rr = sim.finalize_labels()
            finally:
                sim.close()
            for row in rr:
                row["marketId"] = mid
                row["windowEndMs"] = int(cr.get("windowEndMs") or 0)
            all_rows.extend(rr)

            parity = None
            if a.verify_baseline:
                b = v3b.FifoAggregateResponsibilityLadderV3B(tape_path)
                try:
                    rb = b.run_qty("__UNSCORED__")
                finally:
                    b.close()
                parity = {k: _eq(_physical_core(r)[k], _physical_core(rb)[k]) for k in _physical_core(r)}

            inv = (r.get("quantityLedgerSummary") or {}).get("invariantViolations") or {}
            report = {
                "marketId": mid,
                "rows": len(rr),
                "fills": int(r.get("fillEvents") or 0),
                "ledgerInvariantViolations": inv,
                "baselinePhysicalParity": None if parity is None else all(parity.values()),
                "baselineParityByField": parity,
            }
            market_reports.append(report)
            print(json.dumps({"progress": i, "of": len(cohort), **report}, ensure_ascii=False), flush=True)

    with rows_out.open("w", encoding="utf-8", newline="\n") as fh:
        for row in all_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = {
        "version": "MANAGEMENT_V1_V3B_PREACTION_ROWS_V1",
        "date": "2026-09-07",
        "researchOnly": True,
        "actionAuthority": False,
        "stateTiming": "PRE_SUBMIT_DECISION",
        "candidateAlreadyInState": False,
        "bundle": str(bundle),
        "markets": len(cohort),
        "rows": len(all_rows),
        "allLedgerInvariantsPass": all(not x["ledgerInvariantViolations"] for x in market_reports),
        "baselineVerificationRequested": bool(a.verify_baseline),
        "allBaselinePhysicalParity": all(x["baselinePhysicalParity"] for x in market_reports) if a.verify_baseline else None,
        "rowsOutput": str(rows_out),
        "marketReports": market_reports,
        "guards": [
            "current V3B exact-FIFO substrate",
            "snapshot immediately before physical submit",
            "candidate carrier excluded from pre-action live-slot state",
            "physical submit path delegated unchanged to current V3B",
            "winner/Target future absent from runtime state",
            "3s/5s outcomes are labels only",
            "no dream fill",
            "no 8781",
        ],
    }
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"ok": True, "markets": len(cohort), "rows": len(all_rows), "allLedgerInvariantsPass": summary["allLedgerInvariantsPass"], "allBaselinePhysicalParity": summary["allBaselinePhysicalParity"], "rowsOutput": str(rows_out)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
