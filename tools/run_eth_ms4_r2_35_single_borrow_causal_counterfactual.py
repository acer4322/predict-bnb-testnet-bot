from __future__ import annotations
import argparse, hashlib, json, math, os, shutil, sys, tempfile, zipfile
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import tools.run_eth_ms4_r2_17_recoverable_safe_surplus_shadow as r217

r28 = r217.r28
v2 = r217.v2
EPS = 1e-9


class SingleBorrowCausalSim(r217.RecoverableSafeSurplusShadow):
    """CAP1 plus instrumentation, optionally allowing exactly one temporary-risk Expand attempt.

    Important: unlike R2.18, this class never refunds scopeRiskCreditConsumed after a
    recoverability-backed Expand fill. The extra fill therefore passes through frozen CAP1
    credit accounting. An independent audit-only liability ledger records whether later
    confirmed Repair-progress credit actually repays the borrowed notional.
    """

    def __init__(self, tape, mode: str = "instrument", target_ordinal: int = 1, max_slots: int = 4):
        super().__init__(tape, max_slots)
        if mode not in {"instrument", "single_borrow"}:
            raise ValueError(mode)
        self.r235Mode = mode
        self.targetOrdinal = int(target_ordinal)
        self.eligibleOrdinal = 0
        self.eligibleEvents = []
        self.borrowAttempt = None
        self.borrowKey = None
        self.borrowFilledQty = 0.0
        self.borrowFilledNotional = 0.0
        self.borrowLiabilityTotal = 0.0
        self.borrowLiabilityRepaid = 0.0
        self.borrowLiabilityOutstanding = 0.0
        self.creditSemanticsContaminated = False
        self.creditContaminationEvents = []
        self.reservationExcessEvents = []
        self.terminalAwareRepairQuotaExcessMax = 0.0
        self.terminalAwareReservationExcessEvents = []
        self._auditClockT = None
        self.r235Events = []
        self._targetT = None

    def _live_roles_snapshot(self):
        rows = []
        for sid, key, o, role in self._live_role_rows():
            try:
                st = str(self.snap(o).get("status") or "").upper()
            except Exception:
                st = ""
            rows.append({
                "slotId": int(sid),
                "key": key,
                "role": role,
                "side": str(o["side"]),
                "price": float(o["price"]),
                "remaining": float(self._remaining(key)),
                "generation": int(self.key_scope_gen.get(key, self.scopeGeneration)),
                "cancelRequested": bool(o.get("cancelRequested")),
                "status": st,
            })
        return sorted(rows, key=lambda x: (x["slotId"], x["key"]))

    def _pre_state(self, t, qv, side, p, q, risk, credit, rec):
        repair_side = self._repair_side() if self.scopeSide is not None else None
        state = {
            "t": int(t),
            "ordinal": int(self.eligibleOrdinal),
            "scopeSide": self.scopeSide,
            "scopeGeneration": int(self.scopeGeneration),
            "directionSignal": self._direction(qv),
            "candidateSide": side,
            "candidatePrice": float(p),
            "candidateQty": float(q),
            "riskCost": float(risk),
            "availableRealizedCredit": float(credit),
            "borrowDeficit": float(max(0.0, risk - credit)),
            "upQty": float(self.inv["UP"]),
            "downQty": float(self.inv["DOWN"]),
            "cost": float(self.cost),
            "physicalFloor": float(self._physical_floor()),
            "physicalBest": float(max(self.inv.values()) - self.cost),
            "scopeDebtQty": float(self._scope_debt_qty()),
            "reservedRepairQty": float(self._reserved_repair_quota(repair_side)) if repair_side else 0.0,
            "scopeRepairProgressClocks": int(self.scopeRepairProgressClocks),
            "scopeRiskCreditTotal": float(self.scopeRiskCreditTotal),
            "scopeRiskCreditConsumed": float(self.scopeRiskCreditConsumed),
            "scopeRiskCreditReserved": float(self._reserved_current_expand_risk()),
            "recoverabilityReason": rec.get("reason"),
            "forwardPairSum": rec.get("forwardPairSum"),
            "ownedRepairQty": rec.get("ownedRepairQty"),
            "futureRepairPrice": rec.get("futureRepairPrice"),
            "futureRepairQty": rec.get("futureRepairQty"),
            "floorAfterExpand": rec.get("floorAfterExpand"),
            "floorAfterOwned": rec.get("floorAfterOwned"),
            "liveRoles": self._live_roles_snapshot(),
            "usedExpandSidePrices": sorted(float(x) for x in self._used_prices(side)),
            "usedRepairSidePrices": sorted(float(x) for x in self._used_prices(repair_side)) if repair_side else [],
        }
        raw = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        state["stateHash"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return state

    def _eligible_borrow_opportunity(self, t, qv, end):
        if int(end) - int(t) <= v2.NO_NEW_EXPOSURE_MS:
            return None
        if self.scopeSide is None or self._has_stale_scope_reservation():
            return None
        side = str(self.scopeSide)
        if len(self.slot_key) >= self.max_slots or len(self._live_role_rows(side=side)) >= self.max_slots:
            return None
        if getattr(self, "_last_new_receipt", None) == int(t):
            return None
        cand = self._expand_candidate(side)
        if cand is None:
            return None
        p, q = cand
        before = float(self._physical_floor())
        after = float(self._candidate_alone_floor(side, p, q))
        risk = max(0.0, before - after)
        credit = float(self._available_expand_risk_credit())
        if credit + EPS >= risk:
            return None
        rec = self._recoverability(side, p, q)
        if not rec.get("recoverable"):
            return None
        self.eligibleOrdinal += 1
        pre = self._pre_state(t, qv, side, p, q, risk, credit, rec)
        ev = {
            "event": "R235_CAP1_ELIGIBLE_BORROW_OPPORTUNITY",
            **pre,
        }
        self.eligibleEvents.append(ev)
        return ev

    def _attempt_single_borrow(self, t, ev):
        self.borrowAttempt = {
            "event": "R235_SINGLE_BORROW_ATTEMPT",
            "t": int(t),
            "targetOrdinal": int(self.targetOrdinal),
            "preStateHash": ev["stateHash"],
            "side": ev["candidateSide"],
            "price": float(ev["candidatePrice"]),
            "qty": float(ev["candidateQty"]),
            "riskCost": float(ev["riskCost"]),
            "availableRealizedCredit": float(ev["availableRealizedCredit"]),
            "borrowDeficit": float(ev["borrowDeficit"]),
            "accepted": False,
            "key": None,
        }
        before_n = self.n
        ok = self._submit_role_v8(
            int(t), ev["candidateSide"], "SATELLITE_EXPAND",
            float(ev["candidatePrice"]), float(ev["candidateQty"]),
            float(ev["floorAfterExpand"]), None,
        )
        self.borrowAttempt["accepted"] = bool(ok)
        if ok:
            key = f"{ev['candidateSide']}_{before_n}"
            self.borrowKey = key
            self.borrowAttempt["key"] = key
            self._targetT = int(t)
        self.r235Events.append(dict(self.borrowAttempt))
        return ok

    def _open_one_option(self, t, qv, end):
        self._auditClockT = int(t)
        ev = self._eligible_borrow_opportunity(t, qv, end)
        if ev is not None and int(ev["ordinal"]) == int(self.targetOrdinal) and self._targetT is None:
            self._targetT = int(t)
        if (
            ev is not None
            and self.r235Mode == "single_borrow"
            and self.borrowAttempt is None
            and int(ev["ordinal"]) == int(self.targetOrdinal)
        ):
            self._attempt_single_borrow(t, ev)
        # Continue with frozen CAP1 decision/routing after the intervention (if any).
        return r28.FanoutRoleCapacitySim._open_one_option(self, t, qv, end)

    def _audit_reservation(self):
        old_max = float(getattr(self, "repairQuotaExcessMax", 0.0))
        old_terminal_aware = float(self.terminalAwareRepairQuotaExcessMax)
        if self.scopeSide is not None:
            debt = float(self._scope_debt_qty())
            reserved = float(self._reserved_repair_quota())
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
            debt = reserved = live_reserved = 0.0
        self.terminalAwareRepairQuotaExcessMax = max(self.terminalAwareRepairQuotaExcessMax, max(0.0, live_reserved - debt))
        if self.terminalAwareRepairQuotaExcessMax > old_terminal_aware + EPS:
            self.terminalAwareReservationExcessEvents.append({
                "t": self._auditClockT, "event": "R235_TERMINAL_AWARE_REPAIR_RESERVATION_EXCESS_NEW_MAX",
                "scopeSide": self.scopeSide, "scopeGeneration": int(self.scopeGeneration),
                "debt": debt, "liveReserved": live_reserved,
                "newMax": self.terminalAwareRepairQuotaExcessMax,
                "liveRoles": self._live_roles_snapshot(),
            })
        super()._audit_reservation()
        new_max = float(getattr(self, "repairQuotaExcessMax", 0.0))
        if new_max > old_max + EPS:
            ev = {
                "t": self._auditClockT,
                "event": "R235_REPAIR_RESERVATION_EXCESS_NEW_MAX",
                "scopeSide": self.scopeSide,
                "scopeGeneration": int(self.scopeGeneration),
                "debt": debt,
                "reserved": reserved,
                "excess": max(0.0, reserved - debt),
                "newMax": new_max,
                "liveRoles": self._live_roles_snapshot(),
            }
            self.reservationExcessEvents.append(ev)
            self.r235Events.append(ev)

    def process(self, t):
        self._auditClockT = int(t)
        old_scope = self.scopeSide
        old_gen = int(self.scopeGeneration)
        before_credit_events = len(self.scope_credit_events)
        before_cum = None
        if self.borrowKey and self.borrowKey in self.orders:
            before_cum = float(self.orders[self.borrowKey].get("cum") or 0.0)
        super().process(t)

        # Borrow fill becomes an independent audit-only liability. No runtime authority is granted.
        if self.borrowKey and self.borrowKey in self.orders and before_cum is not None:
            o = self.orders[self.borrowKey]
            inc = max(0.0, float(o.get("cum") or 0.0) - before_cum)
            if inc > EPS:
                notional = inc * float(o["price"])
                self.borrowFilledQty += inc
                self.borrowFilledNotional += notional
                self.borrowLiabilityTotal += notional
                self.borrowLiabilityOutstanding += notional
                self.r235Events.append({
                    "t": int(t), "event": "R235_BORROW_FILL_LIABILITY_BORN",
                    "key": self.borrowKey, "fillInc": inc, "notional": notional,
                    "liabilityOutstanding": self.borrowLiabilityOutstanding,
                })

        # Only confirmed CAP1 Repair-progress credit can repay the audit liability.
        for ev in self.scope_credit_events[before_credit_events:]:
            if ev.get("event") not in {"CONFIRMED_REPAIR_PROGRESS_CREDIT", "CONFIRMED_REPAIR_ALLOCATED_CREDIT"}:
                continue
            credit = max(0.0, float(ev.get("creditValue") or 0.0))
            pay = min(self.borrowLiabilityOutstanding, credit)
            if pay > EPS:
                self.borrowLiabilityOutstanding -= pay
                self.borrowLiabilityRepaid += pay
                self.r235Events.append({
                    "t": int(t), "event": "R235_BORROW_LIABILITY_REPAID_BY_CONFIRMED_REPAIR",
                    "creditValue": credit, "repaid": pay,
                    "liabilityOutstanding": self.borrowLiabilityOutstanding,
                })

        transitioned = (old_scope is not None and self.scopeSide is None) or (
            old_scope is not None and self.scopeSide is not None and int(self.scopeGeneration) != old_gen
        )
        if transitioned and self.borrowLiabilityOutstanding > EPS:
            self.creditSemanticsContaminated = True
            ce = {
                "t": int(t), "event": "R235_CREDIT_SEMANTICS_CONTAMINATED",
                "oldScope": old_scope, "oldGeneration": old_gen,
                "newScope": self.scopeSide, "newGeneration": int(self.scopeGeneration),
                "liabilityOutstanding": self.borrowLiabilityOutstanding,
                "cap1CreditTotalAfter": float(self.scopeRiskCreditTotal),
                "cap1CreditConsumedAfter": float(self.scopeRiskCreditConsumed),
            }
            self.creditContaminationEvents.append(ce)
            self.r235Events.append(ce)

    def _borrow_terminal(self):
        if not self.borrowKey:
            return None
        o = self.orders.get(self.borrowKey)
        if not o:
            return {"key": self.borrowKey, "missingOrder": True}
        try:
            s = self.snap(o)
        except Exception:
            s = {}
        return {
            "key": self.borrowKey,
            "side": str(o["side"]),
            "price": float(o["price"]),
            "qty": float(o["qty"]),
            "cum": float(o.get("cum") or 0.0),
            "remaining": float(self._remaining(self.borrowKey)),
            "cancelRequested": bool(o.get("cancelRequested")),
            "status": str(s.get("status") or "").upper(),
        }

    def _downstream_events(self):
        if self._targetT is None:
            return []
        keep = {
            "ROLE_SLOT_SUBMIT", "ROLE_FILL", "RESPONSIBILITY_SCOPE_BIRTH",
            "RESPONSIBILITY_SCOPE_COMPLETE", "RESPONSIBILITY_SCOPE_FLIP",
            "CONFIRMED_REPAIR_PROGRESS_CREDIT", "EXPAND_RISK_CREDIT_CONSUMED",
            "FAILURE_EVIDENCE_ACTIVE_DRAIN_SUBMIT", "PARALLEL_PASSIVE_REPAIR_FANOUT_SUBMIT",
        }
        out = []
        for e in self.slot_history:
            if int(e.get("t", -1)) < int(self._targetT):
                continue
            if e.get("event") not in keep:
                continue
            if self.borrowKey and e.get("key") == self.borrowKey:
                continue
            out.append({k: e.get(k) for k in (
                "t", "event", "key", "role", "side", "price", "qty", "fillInc",
                "generation", "scopeGeneration", "scopeSide", "creditValue", "riskSpend",
                "sourceKey", "sourceRole"
            ) if k in e})
        return out[:500]

    def run_r235(self, winner):
        r = r28.FanoutRoleCapacitySim.run_cap(self, winner)
        r.update({
            "r235Mode": self.r235Mode,
            "r235TargetOrdinal": int(self.targetOrdinal),
            "r235EligibleCount": int(self.eligibleOrdinal),
            "r235EligibleEvents": self.eligibleEvents[:200],
            "r235BorrowAttempt": self.borrowAttempt,
            "r235BorrowTerminal": self._borrow_terminal(),
            "r235BorrowFilledQty": float(self.borrowFilledQty),
            "r235BorrowFilledNotional": float(self.borrowFilledNotional),
            "r235BorrowLiabilityTotal": float(self.borrowLiabilityTotal),
            "r235BorrowLiabilityRepaid": float(self.borrowLiabilityRepaid),
            "r235BorrowLiabilityOutstanding": float(self.borrowLiabilityOutstanding),
            "r235CreditSemanticsContaminated": bool(self.creditSemanticsContaminated),
            "r235CreditContaminationEvents": self.creditContaminationEvents[:50],
            "r235ReservationExcessEvents": self.reservationExcessEvents[:50],
            "r235TerminalAwareRepairQuotaExcessMax": float(self.terminalAwareRepairQuotaExcessMax),
            "r235TerminalAwareReservationExcessEvents": self.terminalAwareReservationExcessEvents[:50],
            "r235Events": self.r235Events[:300],
            "r235DownstreamEvents": self._downstream_events(),
        })
        return r


def _float_eq(a, b, tol=1e-10):
    try:
        return abs(float(a) - float(b)) <= tol
    except Exception:
        return a == b


def _parity(a, b):
    scalar = [
        "submits", "fillEvents", "filledQty", "upQty", "downQty", "buyNotional",
        "pnlDiagnosticOnly", "floor", "best", "scopeBirths", "scopeCompletions",
        "scopeFlips", "totalRepairProgressClocks", "scopeRiskCreditTotal",
        "scopeRiskCreditConsumed", "scopeRiskCreditReserved", "unauthorizedOverflowQty",
        "repairQuotaExcessMax",
    ]
    mismatches = []
    for k in scalar:
        if k in a or k in b:
            if not _float_eq(a.get(k), b.get(k)):
                mismatches.append({"field": k, "A": a.get(k), "A0": b.get(k)})
    for k in ("roleSubmits", "roleFills", "roleFillQty", "failureEvidenceActiveDrainStats", "r26Stats"):
        if (a.get(k) or {}) != (b.get(k) or {}):
            mismatches.append({"field": k, "A": a.get(k), "A0": b.get(k)})
    return len(mismatches) == 0, mismatches


def _correct(r):
    checks = {
        "unauthorizedOverflowQty": float(r.get("unauthorizedOverflowQty", 0.0)) <= EPS,
        "repairQuotaExcessMax": float(r.get("repairQuotaExcessMax", 0.0)) <= EPS,
    }
    for k in ("truthMismatch", "unexplainedOverOwned", "unexplainedRepairDrift", "responsibilityOverfill", "preBirthLeak", "duplicateDebt", "sharedOverfill"):
        if k in r:
            checks[k] = float(r.get(k, 0.0)) <= EPS
    return all(checks.values()), checks


def _semantic_event(e):
    if e is None:
        return None
    # Generated order keys shift mechanically after inserting the intervention; they are not
    # themselves downstream semantic divergence. Compare lifecycle/role/price/qty semantics.
    return {k: v for k, v in e.items() if k not in {"key", "sourceKey"}}

def _first_downstream_divergence(a0_events, b_events):
    n = min(len(a0_events), len(b_events))
    for i in range(n):
        if _semantic_event(a0_events[i]) != _semantic_event(b_events[i]):
            return {"index": i, "A0": a0_events[i], "B": b_events[i]}
    if len(a0_events) != len(b_events):
        return {
            "index": n,
            "A0": a0_events[n] if n < len(a0_events) else None,
            "B": b_events[n] if n < len(b_events) else None,
        }
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--market-ids", required=True)
    ap.add_argument("--target-ordinal", type=int, default=1)
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    mids = [int(x) for x in a.market_ids.split(",") if x.strip()]
    tmp = Path(tempfile.mkdtemp(prefix="ms4_r235_"))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort = {int(x["marketId"]): x for x in json.load(open(tmp / "cohort.json", encoding="utf-8"))["rows"]}
        rows = []
        comparisons = []
        for mid in mids:
            cr = cohort[mid]
            tape = tmp / "tapes" / f"{mid}.json.xz"

            ctl = r28.FanoutRoleCapacitySim(tape, 1, 4)
            try:
                A = ctl.run_cap(cr["winner"])
            finally:
                ctl.close()

            sim0 = SingleBorrowCausalSim(tape, "instrument", a.target_ordinal, 4)
            try:
                A0 = sim0.run_r235(cr["winner"])
            finally:
                sim0.close()

            simb = SingleBorrowCausalSim(tape, "single_borrow", a.target_ordinal, 4)
            try:
                B = simb.run_r235(cr["winner"])
            finally:
                simb.close()

            parity_ok, parity_mismatch = _parity(A, A0)
            correct_A0, checks_A0 = _correct(A0)
            correct_B, checks_B = _correct(B)
            a0_target = next((e for e in A0.get("r235EligibleEvents", []) if int(e.get("ordinal", -1)) == a.target_ordinal), None)
            b_target = next((e for e in B.get("r235EligibleEvents", []) if int(e.get("ordinal", -1)) == a.target_ordinal), None)
            target_match = bool(a0_target and b_target and a0_target.get("stateHash") == b_target.get("stateHash"))
            attempt = B.get("r235BorrowAttempt")
            attempt_count = 1 if attempt is not None else 0
            semantic_correct_B = bool(
                float(B.get("unauthorizedOverflowQty", 0.0)) <= EPS
                and float(B.get("r235TerminalAwareRepairQuotaExcessMax", 0.0)) <= EPS
            )
            clean_label = bool(
                parity_ok and correct_A0 and semantic_correct_B and target_match and attempt_count <= 1
                and not B.get("r235CreditSemanticsContaminated", False)
            )
            cmp = {
                "marketId": mid,
                "targetOrdinal": int(a.target_ordinal),
                "structuralSupport": bool(a0_target),
                "targetPreStateMatch": target_match,
                "A0ParityPass": parity_ok,
                "A0ParityMismatches": parity_mismatch,
                "borrowAttempted": attempt is not None,
                "borrowAccepted": bool(attempt and attempt.get("accepted")),
                "borrowFilledQty": float(B.get("r235BorrowFilledQty", 0.0)),
                "borrowFilledNotional": float(B.get("r235BorrowFilledNotional", 0.0)),
                "borrowLiabilityRepaid": float(B.get("r235BorrowLiabilityRepaid", 0.0)),
                "borrowLiabilityOutstanding": float(B.get("r235BorrowLiabilityOutstanding", 0.0)),
                "creditSemanticsContaminated": bool(B.get("r235CreditSemanticsContaminated", False)),
                "pnlDelta": float(B["pnlDiagnosticOnly"] - A0["pnlDiagnosticOnly"]),
                "floorDelta": float(B["floor"] - A0["floor"]),
                "bestDelta": float(B["best"] - A0["best"]),
                "fillDelta": int(B["fillEvents"] - A0["fillEvents"]),
                "submitDelta": int(B["submits"] - A0["submits"]),
                "scopeBirthDelta": int(B.get("scopeBirths", 0) - A0.get("scopeBirths", 0)),
                "scopeCompletionDelta": int(B.get("scopeCompletions", 0) - A0.get("scopeCompletions", 0)),
                "scopeFlipDelta": int(B.get("scopeFlips", 0) - A0.get("scopeFlips", 0)),
                "repairProgressDelta": int(B.get("totalRepairProgressClocks", 0) - A0.get("totalRepairProgressClocks", 0)),
                "firstDownstreamNonBorrowDivergence": _first_downstream_divergence(A0.get("r235DownstreamEvents", []), B.get("r235DownstreamEvents", [])),
                "correctnessA0": checks_A0,
                "correctnessB": checks_B,
                "terminalAwareRepairQuotaExcessMax": float(B.get("r235TerminalAwareRepairQuotaExcessMax", 0.0)),
                "semanticCorrectnessB": semantic_correct_B,
                "legacyRawQuotaAuditWarning": bool(float(B.get("repairQuotaExcessMax", 0.0)) > EPS and semantic_correct_B),
                "cleanCausalLabel": clean_label,
            }
            comparisons.append(cmp)
            rows.extend([
                {"marketId": mid, "cell": "A_CAP1", "winnerPostHocOnly": cr["winner"], **A},
                {"marketId": mid, "cell": "A0_CAP1_INSTRUMENT_ONLY", "winnerPostHocOnly": cr["winner"], **A0},
                {"marketId": mid, "cell": "B_SINGLE_BORROW", "winnerPostHocOnly": cr["winner"], **B},
            ])
            print(json.dumps({
                "marketId": mid,
                "support": cmp["structuralSupport"],
                "parity": parity_ok,
                "preMatch": target_match,
                "attempted": cmp["borrowAttempted"],
                "accepted": cmp["borrowAccepted"],
                "borrowFillQty": cmp["borrowFilledQty"],
                "pnlDelta": cmp["pnlDelta"],
                "floorDelta": cmp["floorDelta"],
                "bestDelta": cmp["bestDelta"],
                "creditContam": cmp["creditSemanticsContaminated"],
                "clean": clean_label,
            }, ensure_ascii=False), flush=True)

        out = {
            "version": "MS4_R2_35_SINGLE_BORROW_CAUSAL_COUNTERFACTUAL_V1",
            "date": "2026-09-06",
            "researchOnly": True,
            "runtimeAuthority": False,
            "markets": mids,
            "targetOrdinal": int(a.target_ordinal),
            "rows": rows,
            "comparison": comparisons,
            "gates": {
                "allA0ParityPass": all(x["A0ParityPass"] for x in comparisons),
                "allCorrectnessPass": all(all(x["correctnessA0"].values()) and all(x["correctnessB"].values()) for x in comparisons),
                "atMostOneBorrowAttemptPerMarket": True,
                "anyStructuralSupport": any(x["structuralSupport"] for x in comparisons),
                "anyCleanCausalLabel": any(x["cleanCausalLabel"] for x in comparisons),
            },
            "boundary": [
                "A0 instrumentation must be exact CAP1 behavior",
                "B grants exactly one selected recoverability-backed SATELLITE_EXPAND submission attempt and no other temporary-risk authority",
                "no R2.18 risk-credit refund",
                "independent borrow-liability ledger is instrumentation only and receives repayment only from confirmed CAP1 Repair-progress credit",
                "scope transition with unpaid independent liability invalidates clean-label interpretation",
                "winner is post-hoc evaluation only",
                "<=180s unchanged",
                "realistic HFT",
                "no dream fill",
                "no 8781",
            ],
        }
        op = Path(os.environ["BTC5M_LAN_RESULT_DIR"]) / "result.json" if str(a.output).upper() == "AUTO" else Path(a.output)
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "gates": out["gates"], "comparison": comparisons}, ensure_ascii=False), flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
