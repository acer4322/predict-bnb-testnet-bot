from __future__ import annotations
import argparse, json, os, shutil, sys, tempfile, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.run_eth_ms4_r2_18_recoverability_backed_surplus as r218
import tools.run_eth_ms4_r2_35_single_borrow_causal_counterfactual as r235
import tools.run_eth_ms4_r2_36_conditional_borrow_sequence_depth as r236

r28 = r218.r28
EPS = 1e-9

FULL_R218_EXPECTED = {
    1946784: [
        (1788538218447, "DOWN", 0.39, 1788538221830),
        (1788538228847, "DOWN", 0.48, 1788538229836),
        (1788538238236, "DOWN", 0.39, 1788538243061),
        (1788538262386, "DOWN", 0.33, 1788538263439),
    ],
    1946756: [
        (1788537628136, "UP", 0.27, 1788537628770),
        (1788537629131, "UP", 0.26, 1788537630924),
        (1788537643324, "UP", 0.30, 1788537647030),
        (1788537713718, "UP", 0.31, 1788537718121),
    ],
}


def _seq_signature(seq):
    return [
        (
            int(x.get("t") or -1),
            str(x.get("side")),
            round(float(x.get("price") or 0.0), 10),
            int(x.get("firstFillT") or -1),
        )
        for x in seq
    ]


def _prefix_equal(a, b, n):
    return _seq_signature(a[:n]) == _seq_signature(b[:n]) and len(a) >= n and len(b) >= n


def _frozen_correct(r):
    return bool(
        float(r.get("unauthorizedOverflowQty", 0.0)) <= EPS
        and float(r.get("r236TerminalAwareRepairQuotaExcessMax", 0.0)) <= EPS
    )


def _terminal_metrics(r):
    return {
        "pnl": float(r["pnlDiagnosticOnly"]),
        "floor": float(r["floor"]),
        "best": float(r["best"]),
        "fills": int(r["fillEvents"]),
        "submits": int(r["submits"]),
        "scopeBirths": int(r.get("scopeBirths", 0)),
        "scopeCompletions": int(r.get("scopeCompletions", 0)),
        "scopeFlips": int(r.get("scopeFlips", 0)),
        "repairProgressClocks": int(r.get("totalRepairProgressClocks", 0)),
        "borrowSubmitCount": int(r.get("r236BorrowSubmitCount", 0)),
        "materializedBorrowCount": int(r.get("r236MaterializedBorrowCount", 0)),
        "liabilityTotal": float(r.get("r236BorrowLiabilityTotal", 0.0)),
        "liabilityRepaid": float(r.get("r236BorrowLiabilityRepaid", 0.0)),
        "liabilityOutstanding": float(r.get("r236BorrowLiabilityOutstanding", 0.0)),
        "liabilityOrphaning": bool(r.get("r236CreditSemanticsContaminated", False)),
        "runtimeRefunded": float(r.get("recoverabilityBackedRiskRefunded", r.get("recoverableRiskRefunded", 0.0))),
        "runtimeCreditTotal": float(r.get("scopeRiskCreditTotal", 0.0)),
        "runtimeCreditConsumed": float(r.get("scopeRiskCreditConsumed", 0.0)),
        "runtimeCreditReserved": float(r.get("scopeRiskCreditReserved", 0.0)),
        "terminalAwareRepairQuotaExcessMax": float(r.get("r236TerminalAwareRepairQuotaExcessMax", 0.0)),
        "legacyRawRepairQuotaExcessMax": float(r.get("repairQuotaExcessMax", 0.0)),
        "frozenCorrect": _frozen_correct(r),
        "materializedBorrowSequence": r.get("r236MaterializedBorrowSequence", []),
    }


class RefundBorrowDepthSim(r236.BorrowDepthSim):
    """R2.36 materialized-depth cap with original R2.18 runtime refund semantics.

    Runtime behavior uses R2.18's refund. In parallel, an independent audit-only liability ledger
    treats every borrowed fill as unpaid until confirmed Repair credit arrives. The audit ledger
    never grants or blocks runtime authority.
    """
    def __init__(self, tape, max_materialized_borrows: int, max_slots: int = 4):
        super().__init__(tape, max_materialized_borrows, max_slots)
        self.r237RefundEvents = []
        self.r237ReborrowWhileLiability = []

    def _submit_expand_with_authority(self, t, side, p, q, proj, auth, event_name):
        liability_before = float(self.borrowLiabilityOutstanding)
        runtime_available = float(self._available_expand_risk_credit())
        ok = super()._submit_expand_with_authority(t, side, p, q, proj, auth, event_name)
        if ok and auth.get("source") == "RECOVERABILITY_BACKED" and liability_before > EPS:
            ev = {
                "t": int(t),
                "event": "R237_REBORROW_WHILE_INDEPENDENT_LIABILITY_UNPAID",
                "side": side,
                "price": float(p),
                "qty": float(q),
                "liabilityOutstandingBeforeSubmit": liability_before,
                "runtimeAvailableCreditBeforeSubmit": runtime_available,
                "runtimeRiskCost": float(auth.get("riskCost") or 0.0),
                "runtimeRealCredit": float(auth.get("realCredit") or 0.0),
                "scopeGeneration": int(self.scopeGeneration),
                "repairProgressClocks": int(self.scopeRepairProgressClocks),
            }
            self.r237ReborrowWhileLiability.append(ev)
        return ok

    def process(self, t):
        self._auditClockT = int(t)
        old_scope = self.scopeSide
        old_gen = int(self.scopeGeneration)
        before_credit_events = len(self.scope_credit_events)
        before_refund = float(self.recoverableRiskRefunded)
        before = {
            k: float(self.orders.get(k, {}).get("cum") or 0.0)
            for k in self.recoverableExpandKeys
        }

        # Exact R2.18 runtime processing, including its post-fill refund.
        r218.RecoverabilityBackedSurplusSim.process(self, t)

        fill_notional = 0.0
        for k, old in before.items():
            o = self.orders.get(k)
            if not o:
                continue
            inc = max(0.0, float(o.get("cum") or 0.0) - old)
            if inc <= EPS:
                continue
            notion = inc * float(o["price"])
            fill_notional += notion
            if k not in self.materializedBorrowKeys:
                self.materializedBorrowKeys.add(k)
                self.borrowFillFirstTByKey[k] = int(t)
            self.borrowFillByKey[k] += inc
            self.borrowNotionalByKey[k] += notion
            self.borrowLiabilityTotal += notion
            self.borrowLiabilityOutstanding += notion

        # Independent economic liability can only be repaid by confirmed Repair credit.
        repair_credit = 0.0
        for ev in self.scope_credit_events[before_credit_events:]:
            if ev.get("event") not in {"CONFIRMED_REPAIR_PROGRESS_CREDIT", "CONFIRMED_REPAIR_ALLOCATED_CREDIT"}:
                continue
            credit = max(0.0, float(ev.get("creditValue") or 0.0))
            repair_credit += credit
            pay = min(self.borrowLiabilityOutstanding, credit)
            self.borrowLiabilityOutstanding -= pay
            self.borrowLiabilityRepaid += pay

        refund_delta = max(0.0, float(self.recoverableRiskRefunded) - before_refund)
        if refund_delta > EPS or fill_notional > EPS:
            self.r237RefundEvents.append({
                "t": int(t),
                "event": "R237_REFUND_CLOCK",
                "borrowFillNotional": fill_notional,
                "runtimeRefundDelta": refund_delta,
                "confirmedRepairCreditThisClock": repair_credit,
                "independentLiabilityOutstandingAfter": float(self.borrowLiabilityOutstanding),
                "runtimeCreditTotalAfter": float(self.scopeRiskCreditTotal),
                "runtimeCreditConsumedAfter": float(self.scopeRiskCreditConsumed),
                "runtimeAvailableCreditAfter": float(self._available_expand_risk_credit()),
            })

        transitioned = (old_scope is not None and self.scopeSide is None) or (
            old_scope is not None and self.scopeSide is not None and int(self.scopeGeneration) != old_gen
        )
        if transitioned and self.borrowLiabilityOutstanding > EPS:
            self.creditSemanticsContaminated = True
            self.creditContaminationEvents.append({
                "t": int(t),
                "event": "R237_SCOPE_TRANSITION_WITH_UNPAID_INDEPENDENT_LIABILITY",
                "oldScope": old_scope,
                "oldGeneration": old_gen,
                "newScope": self.scopeSide,
                "newGeneration": int(self.scopeGeneration),
                "liabilityOutstanding": float(self.borrowLiabilityOutstanding),
                "runtimeCreditConsumedAfter": float(self.scopeRiskCreditConsumed),
            })

    def run_depth(self, winner):
        r = super().run_depth(winner)
        r["r237RefundMode"] = True
        r["r237RefundEvents"] = self.r237RefundEvents[:300]
        r["r237ReborrowWhileLiability"] = self.r237ReborrowWhileLiability[:300]
        r["r237ReborrowWhileLiabilityCount"] = len(self.r237ReborrowWhileLiability)
        return r


def _run_mode(tape, winner, cls, max_depth):
    cells = {}
    for k in range(max_depth + 1):
        sim = cls(tape, k, 4)
        try:
            cells[k] = sim.run_depth(winner)
        finally:
            sim.close()
    return cells


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--market-ids", required=True)
    ap.add_argument("--max-depth", type=int, default=3)
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    mids = [int(x) for x in a.market_ids.split(",") if x.strip()]
    max_depth = max(0, int(a.max_depth))
    tmp = Path(tempfile.mkdtemp(prefix="ms4_r237_"))
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

            N = _run_mode(tape, cr["winner"], r236.BorrowDepthSim, max_depth)
            R = _run_mode(tape, cr["winner"], RefundBorrowDepthSim, max_depth)
            n0parity, n0mis = r235._parity(cap, N[0])
            r0parity, r0mis = r235._parity(cap, R[0])

            by_depth = {}
            nr_terminal_delta = {}
            nr_sequence_equal = {}
            expected = FULL_R218_EXPECTED.get(mid, [])
            refund_expected_prefix = {}
            for k in range(max_depth + 1):
                nm = _terminal_metrics(N[k])
                rm = _terminal_metrics(R[k])
                by_depth[f"K{k}"] = {"N": nm, "R": rm}
                nr_terminal_delta[f"K{k}"] = r236._delta(N[k], R[k])
                nseq = N[k].get("r236MaterializedBorrowSequence", [])
                rseq = R[k].get("r236MaterializedBorrowSequence", [])
                nr_sequence_equal[f"K{k}"] = {
                    "equal": _seq_signature(nseq) == _seq_signature(rseq),
                    "N": _seq_signature(nseq),
                    "R": _seq_signature(rseq),
                }
                if k > 0 and len(expected) >= k:
                    refund_expected_prefix[f"K{k}"] = {
                        "matchFullR218Prefix": _seq_signature(rseq[:k]) == expected[:k],
                        "expected": expected[:k],
                        "observed": _seq_signature(rseq[:k]),
                    }

            first_seq_divergence = None
            for k in range(max_depth + 1):
                if not nr_sequence_equal[f"K{k}"]["equal"]:
                    first_seq_divergence = k
                    break

            reborrow_events = []
            for k in range(max_depth + 1):
                for ev in R[k].get("r237ReborrowWhileLiability", []):
                    reborrow_events.append({"depth": k, **ev})

            comp = {
                "marketId": mid,
                "maxDepth": max_depth,
                "N_K0ParityCAP1": n0parity,
                "R_K0ParityCAP1": r0parity,
                "N_K0ParityMismatches": n0mis,
                "R_K0ParityMismatches": r0mis,
                "byDepth": by_depth,
                "RminusNTerminalDeltaByDepth": nr_terminal_delta,
                "NvsRMaterializedSequenceByDepth": nr_sequence_equal,
                "firstSequenceDivergenceDepth": first_seq_divergence,
                "refundMatchesFullR218ExpectedPrefix": refund_expected_prefix,
                "refundReborrowWhileIndependentLiability": reborrow_events,
                "refundReborrowWhileIndependentLiabilityCount": len(reborrow_events),
            }
            comparison.append(comp)
            rows.append({"marketId": mid, "cell": "CAP1", "winnerPostHocOnly": cr["winner"], **cap})
            for k in range(max_depth + 1):
                rows.append({"marketId": mid, "cell": f"N_K{k}", "winnerPostHocOnly": cr["winner"], **N[k]})
                rows.append({"marketId": mid, "cell": f"R_K{k}", "winnerPostHocOnly": cr["winner"], **R[k]})

            print(json.dumps({
                "marketId": mid,
                "maxDepth": max_depth,
                "N0Parity": n0parity,
                "R0Parity": r0parity,
                "firstSequenceDivergenceDepth": first_seq_divergence,
                "RminusN": nr_terminal_delta,
                "refundExpected": refund_expected_prefix,
                "reborrowWhileLiabilityCount": len(reborrow_events),
                "frozenCorrectN": [by_depth[f"K{k}"]["N"]["frozenCorrect"] for k in range(max_depth + 1)],
                "frozenCorrectR": [by_depth[f"K{k}"]["R"]["frozenCorrect"] for k in range(max_depth + 1)],
            }, ensure_ascii=False), flush=True)

        out = {
            "version": "MS4_R2_37_REFUND_X_MATERIALIZED_SEQUENCE_DEPTH_FACTORIAL_V1",
            "date": "2026-09-06",
            "researchOnly": True,
            "runtimeAuthority": False,
            "markets": mids,
            "maxDepth": max_depth,
            "rows": rows,
            "comparison": comparison,
            "gates": {
                "allN_K0ParityCAP1": all(x["N_K0ParityCAP1"] for x in comparison),
                "allR_K0ParityCAP1": all(x["R_K0ParityCAP1"] for x in comparison),
                "allFrozenCorrect": all(
                    mode["frozenCorrect"]
                    for x in comparison
                    for d in x["byDepth"].values()
                    for mode in (d["N"], d["R"])
                ),
                "allMaterializedDepthLimitsRespected": all(
                    mode["materializedBorrowCount"] <= int(k[1:])
                    for x in comparison
                    for k, d in x["byDepth"].items()
                    for mode in (d["N"], d["R"])
                ),
            },
            "boundary": [
                "refund is a tested mechanism, not a promotion candidate",
                "N uses frozen CAP1 credit accounting with no R2.18 refund",
                "R uses original R2.18 post-fill consumed-credit refund",
                "both modes use the same materialized-borrow depth cap and preserve zero-fill attempt history",
                "independent liability audit never grants/blocks runtime authority",
                "frozen correctness is evaluated separately from independent liability orphaning",
                "winner post-hoc only",
                "<=180s unchanged",
                "realistic HFT",
                "no dream fill",
                "no 8781",
            ],
        }
        op = Path(os.environ["BTC5M_LAN_RESULT_DIR"]) / "result.json" if str(a.output).upper() == "AUTO" else Path(a.output)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "gates": out["gates"], "summary": comparison}, ensure_ascii=False), flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
