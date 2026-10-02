from __future__ import annotations
import argparse, json, os, shutil, sys, tempfile, zipfile
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import tools.run_eth_ms4_r2_18_recoverability_backed_surplus as r218
import tools.run_eth_ms4_r2_35_single_borrow_causal_counterfactual as r235

r28 = r218.r28
v2 = r218.v2
EPS = 1e-9

EXPECTED_FIRST = {
    1946784: {"t": 1788538218447, "side": "DOWN", "price": 0.39, "fillT": 1788538221830},
    1946756: {"t": 1788537628136, "side": "UP", "price": 0.27, "fillT": 1788537628770},
}


class BorrowDepthSim(r218.RecoverabilityBackedSurplusSim):
    def __init__(self, tape, max_materialized_borrows: int, max_slots: int = 4):
        super().__init__(tape, max_slots)
        self.maxMaterializedBorrows = int(max_materialized_borrows)
        self.borrowSequence = []
        self.materializedBorrowKeys = set()
        self.borrowFillFirstTByKey = {}
        self.borrowFillByKey = Counter()
        self.borrowNotionalByKey = Counter()
        self.borrowLiabilityTotal = 0.0
        self.borrowLiabilityRepaid = 0.0
        self.borrowLiabilityOutstanding = 0.0
        self.creditSemanticsContaminated = False
        self.creditContaminationEvents = []
        self.terminalAwareRepairQuotaExcessMax = 0.0
        self.terminalAwareReservationExcessEvents = []
        self._auditClockT = None

    def _expand_authority(self, t, side, p, q):
        auth = super()._expand_authority(t, side, p, q)
        if auth.get("ok") and auth.get("source") == "RECOVERABILITY_BACKED":
            if self.maxMaterializedBorrows <= 0 or len(self.materializedBorrowKeys) >= self.maxMaterializedBorrows:
                return {
                    "ok": False,
                    "source": "SEQUENCE_MATERIALIZED_BORROW_LIMIT",
                    "riskCost": auth.get("riskCost"),
                    "realCredit": auth.get("realCredit"),
                    "recoverability": auth.get("recoverability"),
                }
        return auth

    def _live_roles_snapshot(self):
        out = []
        for sid, key, o, role in self._live_role_rows():
            try:
                status = str(self.snap(o).get("status") or "").upper()
            except Exception:
                status = ""
            out.append({
                "slotId": int(sid), "key": key, "role": role,
                "side": str(o["side"]), "price": float(o["price"]),
                "remaining": float(self._remaining(key)),
                "generation": int(self.key_scope_gen.get(key, self.scopeGeneration)),
                "cancelRequested": bool(o.get("cancelRequested")), "status": status,
            })
        return sorted(out, key=lambda x: (x["slotId"], x["key"]))

    def _submit_expand_with_authority(self, t, side, p, q, proj, auth, event_name):
        before_n = self.n
        before_count = int(self.r218.get("RECOVERABILITY_BACKED_EXPAND_SUBMIT", 0))
        pre = None
        if auth.get("source") == "RECOVERABILITY_BACKED":
            pre = {
                "submitAttemptIndex": before_count + 1,
                "t": int(t), "side": side, "price": float(p), "qty": float(q),
                "riskCost": float(auth.get("riskCost") or 0.0),
                "realCredit": float(auth.get("realCredit") or 0.0),
                "scopeSide": self.scopeSide, "scopeGeneration": int(self.scopeGeneration),
                "physicalFloor": float(self._physical_floor()),
                "physicalBest": float(max(self.inv.values()) - self.cost),
                "upQty": float(self.inv["UP"]), "downQty": float(self.inv["DOWN"]),
                "cost": float(self.cost), "scopeDebtQty": float(self._scope_debt_qty()),
                "reservedRepairQty": float(self._reserved_repair_quota()),
                "repairProgressClocks": int(self.scopeRepairProgressClocks),
                "riskCreditTotal": float(self.scopeRiskCreditTotal),
                "riskCreditConsumed": float(self.scopeRiskCreditConsumed),
                "riskCreditReserved": float(self._reserved_current_expand_risk()),
                "runtimeAvailableCredit": float(self._available_expand_risk_credit()),
                "auditBorrowLiabilityOutstandingBeforeSubmit": float(self.borrowLiabilityOutstanding),
                "recoverability": auth.get("recoverability"),
                "liveRoles": self._live_roles_snapshot(),
            }
        ok = super()._submit_expand_with_authority(t, side, p, q, proj, auth, event_name)
        if ok and pre is not None:
            pre["key"] = f"{side}_{before_n}"
            self.borrowSequence.append(pre)
        return ok

    def _audit_reservation(self):
        if self.scopeSide is not None:
            debt = float(self._scope_debt_qty())
            repair_side = self._repair_side()
            live_reserved = 0.0
            for _, key, o, role in self._live_role_rows():
                if role not in {"ECONOMIC_CORE", "SATELLITE_REPAIR"}:
                    continue
                if str(o["side"]) != repair_side or self.key_scope_gen.get(key) != self.scopeGeneration:
                    continue
                try:
                    status = str(self.snap(o).get("status") or "").upper()
                except Exception:
                    status = ""
                if status in v2.TERMINAL_STATUSES:
                    continue
                live_reserved += max(0.0, float(self.keyRepairQuotaRemaining.get(key, 0.0)))
        else:
            debt = live_reserved = 0.0
        old = self.terminalAwareRepairQuotaExcessMax
        self.terminalAwareRepairQuotaExcessMax = max(old, max(0.0, live_reserved - debt))
        if self.terminalAwareRepairQuotaExcessMax > old + EPS:
            self.terminalAwareReservationExcessEvents.append({
                "t": self._auditClockT, "scopeSide": self.scopeSide,
                "scopeGeneration": int(self.scopeGeneration), "debt": debt,
                "liveReserved": live_reserved,
                "newMax": self.terminalAwareRepairQuotaExcessMax,
                "liveRoles": self._live_roles_snapshot(),
            })
        super()._audit_reservation()

    def _open_one_option(self, t, qv, end):
        self._auditClockT = int(t)
        return super()._open_one_option(t, qv, end)

    def process(self, t):
        self._auditClockT = int(t)
        old_scope = self.scopeSide
        old_gen = int(self.scopeGeneration)
        before_credit_events = len(self.scope_credit_events)
        before = {k: float(self.orders.get(k, {}).get("cum") or 0.0) for k in self.recoverableExpandKeys}

        # Frozen CAP1 physical/accounting path, deliberately bypassing the R2.18 refund wrapper.
        r28.FanoutRoleCapacitySim.process(self, t)

        for k, old in before.items():
            o = self.orders.get(k)
            if not o:
                continue
            inc = max(0.0, float(o.get("cum") or 0.0) - old)
            if inc <= EPS:
                continue
            notion = inc * float(o["price"])
            if k not in self.materializedBorrowKeys:
                self.materializedBorrowKeys.add(k)
                self.borrowFillFirstTByKey[k] = int(t)
            self.borrowFillByKey[k] += inc
            self.borrowNotionalByKey[k] += notion
            self.borrowLiabilityTotal += notion
            self.borrowLiabilityOutstanding += notion

        for ev in self.scope_credit_events[before_credit_events:]:
            if ev.get("event") not in {"CONFIRMED_REPAIR_PROGRESS_CREDIT", "CONFIRMED_REPAIR_ALLOCATED_CREDIT"}:
                continue
            credit = max(0.0, float(ev.get("creditValue") or 0.0))
            pay = min(self.borrowLiabilityOutstanding, credit)
            self.borrowLiabilityOutstanding -= pay
            self.borrowLiabilityRepaid += pay

        transitioned = (old_scope is not None and self.scopeSide is None) or (
            old_scope is not None and self.scopeSide is not None and int(self.scopeGeneration) != old_gen
        )
        if transitioned and self.borrowLiabilityOutstanding > EPS:
            self.creditSemanticsContaminated = True
            self.creditContaminationEvents.append({
                "t": int(t), "oldScope": old_scope, "oldGeneration": old_gen,
                "newScope": self.scopeSide, "newGeneration": int(self.scopeGeneration),
                "liabilityOutstanding": self.borrowLiabilityOutstanding,
            })

    def run_depth(self, winner):
        r = super().run_r218(winner)
        seq = []
        materialized = []
        for e in self.borrowSequence:
            k = e["key"]
            x = dict(e)
            x["filledQty"] = float(self.borrowFillByKey.get(k, 0.0))
            x["filledNotional"] = float(self.borrowNotionalByKey.get(k, 0.0))
            x["firstFillT"] = self.borrowFillFirstTByKey.get(k)
            seq.append(x)
            if x["filledQty"] > EPS:
                y = dict(x)
                y["materializedIndex"] = len(materialized) + 1
                materialized.append(y)
        r.update({
            "r236MaxMaterializedBorrows": self.maxMaterializedBorrows,
            "r236BorrowSequence": seq,
            "r236BorrowSubmitCount": len(seq),
            "r236MaterializedBorrowSequence": materialized,
            "r236MaterializedBorrowCount": len(materialized),
            "r236BorrowFilledCount": len(materialized),
            "r236BorrowLiabilityTotal": float(self.borrowLiabilityTotal),
            "r236BorrowLiabilityRepaid": float(self.borrowLiabilityRepaid),
            "r236BorrowLiabilityOutstanding": float(self.borrowLiabilityOutstanding),
            "r236CreditSemanticsContaminated": bool(self.creditSemanticsContaminated),
            "r236CreditContaminationEvents": self.creditContaminationEvents[:50],
            "r236TerminalAwareRepairQuotaExcessMax": float(self.terminalAwareRepairQuotaExcessMax),
            "r236TerminalAwareReservationExcessEvents": self.terminalAwareReservationExcessEvents[:50],
        })
        return r


def _semantic_correct(r):
    return bool(
        float(r.get("unauthorizedOverflowQty", 0.0)) <= EPS
        and float(r.get("r236TerminalAwareRepairQuotaExcessMax", 0.0)) <= EPS
        and not r.get("r236CreditSemanticsContaminated", False)
    )


def _delta(a, b):
    return {
        "pnlDelta": float(b["pnlDiagnosticOnly"] - a["pnlDiagnosticOnly"]),
        "floorDelta": float(b["floor"] - a["floor"]),
        "bestDelta": float(b["best"] - a["best"]),
        "fillDelta": int(b["fillEvents"] - a["fillEvents"]),
        "submitDelta": int(b["submits"] - a["submits"]),
        "scopeBirthDelta": int(b.get("scopeBirths", 0) - a.get("scopeBirths", 0)),
        "scopeFlipDelta": int(b.get("scopeFlips", 0) - a.get("scopeFlips", 0)),
        "repairProgressDelta": int(b.get("totalRepairProgressClocks", 0) - a.get("totalRepairProgressClocks", 0)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--market-ids", required=True)
    ap.add_argument("--max-depth", type=int, default=2)
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    mids = [int(x) for x in a.market_ids.split(",") if x.strip()]
    tmp = Path(tempfile.mkdtemp(prefix="ms4_r236_"))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort = {int(x["marketId"]): x for x in json.load(open(tmp / "cohort.json", encoding="utf-8"))["rows"]}
        rows = []
        comparison = []
        for mid in mids:
            cr = cohort[mid]
            tape = tmp / "tapes" / f"{mid}.json.xz"
            ctl = r28.FanoutRoleCapacitySim(tape, 1, 4)
            try:
                cap = ctl.run_cap(cr["winner"])
            finally:
                ctl.close()
            max_depth = max(0, int(a.max_depth))
            cells = {}
            for k in range(max_depth + 1):
                sim = BorrowDepthSim(tape, k, 4)
                try:
                    cells[k] = sim.run_depth(cr["winner"])
                finally:
                    sim.close()
            parity, mismatches = r235._parity(cap, cells[0])
            first = cells[1].get("r236MaterializedBorrowSequence", [])[:1] if max_depth >= 1 else []
            expected = EXPECTED_FIRST.get(mid)
            first_match = None
            if expected is not None and first:
                x = first[0]
                first_match = bool(
                    int(x["t"]) == int(expected["t"])
                    and x["side"] == expected["side"]
                    and abs(float(x["price"]) - float(expected["price"])) <= EPS
                    and int(x.get("firstFillT") or -1) == int(expected["fillT"])
                )
            elif expected is not None and max_depth >= 1:
                first_match = False

            depth_metrics = {}
            marginal_by_depth = {}
            total_vs_k0 = {}
            prefix_matches = {}
            for k in range(max_depth + 1):
                r = cells[k]
                depth_metrics[f"K{k}"] = {
                    "borrowSubmitCount": int(r.get("r236BorrowSubmitCount", 0)),
                    "materializedBorrowCount": int(r.get("r236MaterializedBorrowCount", 0)),
                    "pnl": float(r["pnlDiagnosticOnly"]),
                    "floor": float(r["floor"]),
                    "best": float(r["best"]),
                    "fills": int(r["fillEvents"]),
                    "submits": int(r["submits"]),
                    "scopeBirths": int(r.get("scopeBirths", 0)),
                    "scopeCompletions": int(r.get("scopeCompletions", 0)),
                    "scopeFlips": int(r.get("scopeFlips", 0)),
                    "repairProgressClocks": int(r.get("totalRepairProgressClocks", 0)),
                    "liabilityTotal": float(r.get("r236BorrowLiabilityTotal", 0.0)),
                    "liabilityRepaid": float(r.get("r236BorrowLiabilityRepaid", 0.0)),
                    "liabilityOutstanding": float(r.get("r236BorrowLiabilityOutstanding", 0.0)),
                    "creditContaminated": bool(r.get("r236CreditSemanticsContaminated", False)),
                    "terminalAwareRepairQuotaExcessMax": float(r.get("r236TerminalAwareRepairQuotaExcessMax", 0.0)),
                    "legacyRawRepairQuotaExcessMax": float(r.get("repairQuotaExcessMax", 0.0)),
                    "semanticCorrect": bool(_semantic_correct(r)),
                    "materializedBorrowSequence": r.get("r236MaterializedBorrowSequence", []),
                }
                if k > 0:
                    marginal_by_depth[f"K{k}-K{k-1}"] = _delta(cells[k-1], cells[k])
                    total_vs_k0[f"K{k}-K0"] = _delta(cells[0], cells[k])
                    prev = cells[k-1].get("r236MaterializedBorrowSequence", [])
                    cur = cells[k].get("r236MaterializedBorrowSequence", [])
                    need = min(k - 1, len(prev))
                    ok = len(cur) >= need
                    if ok:
                        for i in range(need):
                            a1, b1 = prev[i], cur[i]
                            if not all(a1.get(f) == b1.get(f) for f in ("t", "side", "price", "qty", "firstFillT")):
                                ok = False
                                break
                    prefix_matches[f"K{k-1}->K{k}"] = {
                        "match": bool(ok),
                        "prefixLength": int(need),
                        "prevMaterializedCount": len(prev),
                        "curMaterializedCount": len(cur),
                    }

            comp = {
                "marketId": mid,
                "maxDepth": max_depth,
                "K0ParityCAP1": parity,
                "K0ParityMismatches": mismatches,
                "K1FirstMaterializedBorrowMatchesExpected": first_match,
                "depthMetrics": depth_metrics,
                "marginalByDepth": marginal_by_depth,
                "totalVsK0": total_vs_k0,
                "prefixMaterializedHistoryMatch": prefix_matches,
            }
            comparison.append(comp)
            rows.append({"marketId": mid, "cell": "CAP1", "winnerPostHocOnly": cr["winner"], **cap})
            for k in range(max_depth + 1):
                rows.append({"marketId": mid, "cell": f"K{k}", "winnerPostHocOnly": cr["winner"], **cells[k]})
            print(json.dumps({
                "marketId": mid, "maxDepth": max_depth, "K0Parity": parity, "K1FirstMatch": first_match,
                "submitCounts": [cells[k]["r236BorrowSubmitCount"] for k in range(max_depth + 1)],
                "materializedCounts": [cells[k]["r236MaterializedBorrowCount"] for k in range(max_depth + 1)],
                "marginalByDepth": marginal_by_depth,
                "semanticCorrect": [depth_metrics[f"K{k}"]["semanticCorrect"] for k in range(max_depth + 1)],
                "prefixMatches": prefix_matches,
            }, ensure_ascii=False), flush=True)

        out = {
            "version": "MS4_R2_36_CONDITIONAL_BORROW_SEQUENCE_DEPTH_V3_DYNAMIC",
            "date": "2026-09-06",
            "researchOnly": True,
            "runtimeAuthority": False,
            "markets": mids,
            "maxDepth": int(a.max_depth),
            "rows": rows,
            "comparison": comparison,
            "gates": {
                "allK0ParityCAP1": all(x["K0ParityCAP1"] for x in comparison),
                "allKnownFirstMaterializedBorrowMatch": all(x["K1FirstMaterializedBorrowMatchesExpected"] is not False for x in comparison),
                "allSemanticCorrect": all(v["semanticCorrect"] for x in comparison for v in x["depthMetrics"].values()),
                "allMaterializedBorrowLimitsRespected": all(v["materializedBorrowCount"] <= int(k[1:]) for x in comparison for k,v in x["depthMetrics"].items()),
                "allAdjacentPrefixMaterializedHistoryMatch": all(v["match"] for x in comparison for v in x["prefixMaterializedHistoryMatch"].values()),
            },
            "boundary": [
                "K0..Kmax differ only in maximum number of distinct recoverability-backed carriers allowed to materialize confirmed fill; zero-fill attempts do not consume depth",
                "R2.18 refund disabled; frozen CAP1 credit accounting remains physical authority",
                "second borrow is naturally generated from its own branch state",
                "independent liability audit is instrumentation only",
                "terminal-aware reservation correctness reported separately from legacy pre-refresh raw audit",
                "consumed mechanism markets only",
                "winner post-hoc only", "<=180s unchanged", "realistic HFT", "no dream fill", "no 8781",
            ],
        }
        op = Path(os.environ["BTC5M_LAN_RESULT_DIR"]) / "result.json" if str(a.output).upper() == "AUTO" else Path(a.output)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "gates": out["gates"], "comparison": comparison}, ensure_ascii=False), flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
