"""Research-only shared continuation/service authority; never imported by live.

R240 owns physical debt and handoff. R246 supplies isolated Core evidence and
the existing pure-Repair actuator. No learned selector or extra funding source.
"""
from collections import Counter
import math

from tools import run_eth_ms4_r2_46_r240_core_active_credit_mechanism_ablation as r246

EPS = 1e-9


class ResourceServiceSim(r246.R240OneCoreActiveMechanismSim):
    def __init__(self, tape, fanout_limit=1, max_slots=4):
        if fanout_limit != 1 or max_slots != 4:
            raise ValueError("Frozen CAP1 capacity required")
        # Created first because virtual credit methods may be called by ancestors.
        self.serviceLedger = {}
        self.serviceEvents = []
        self.serviceStats = Counter()
        self.serviceChecks = {
            "insuranceConservationErrorMax": 0.0,
            "insuranceOverfillNotionalMax": 0.0,
            "combinedAuthorityExcessMax": 0.0,
            "quarantineConservationErrorMax": 0.0,
            "quarantineShortfallTotal": 0.0,
            "duplicateOwnershipCount": 0,
            "staleServiceSubmitCount": 0,
            "nonzeroNativeSubmitRcCount": 0,
            "coreSubmitBudgetShortfallCount": 0,
            "activeHandoffOverlapCount": 0,
            "serviceSourceMissingCount": 0,
        }
        self.activeCreditRemoved = 0.0
        self.activeCreditEligible = 0.0
        self._coreServiceContext = False
        super().__init__(tape, "ACTIVE", fanout_limit, max_slots)

    def _service_claim(self):
        """Existing generation authority spent or still reserved by new service."""
        return sum(x["spent"] + x["held"] for x in self.serviceLedger.values()
                   if x["generation"] == self.scopeGeneration
                   and x["scopeSide"] == self.scopeSide)

    def _available_expand_risk_credit(self):
        native = float(super()._available_expand_risk_credit())
        # An old unsettled service cannot fund new-generation exposure.
        if any(x["held"] > EPS and x["generation"] != self.scopeGeneration
               for x in self.serviceLedger.values()):
            return 0.0
        return max(0.0, native - self._service_claim())

    def _event(self, t, event, **fields):
        row = {"t": int(t), "event": event, **fields}
        self.serviceEvents.append(row)
        self.slot_history.append(row)

    def _try_core_active(self, t):
        # Frozen Satellite evidence already ran first in inherited refresh.
        # A failure count is not permission: the actuator below must acquire
        # the same finite authority that Expand/overflow would otherwise use.
        self._coreServiceContext = True
        try:
            return super()._try_core_active(t)
        finally:
            self._coreServiceContext = False

    def _submit_active(self, t, side, role, q, score, diag):
        if not self._coreServiceContext:
            # Existing Satellite insurance keeps its original authorization.
            return super()._submit_active(t, side, role, q, score, diag)
        ev = self.pendingCoreEvidence
        if (not ev or self.scopeSide is None
                or int(ev["generation"]) != self.scopeGeneration
                or side != self._repair_side()):
            self.serviceChecks["staleServiceSubmitCount"] += 1
            return False
        quotes = r246.r1.v2.base.quotes(self.book)
        if not quotes or quotes.get(side, {}).get("ask") is None:
            return False
        price = float(quotes[side]["ask"])
        notional = price * float(q)
        available = float(self._available_expand_risk_credit())
        if not math.isfinite(notional) or notional <= 0:
            raise ValueError("Nonfinite or nonpositive service cost")
        if notional > available + EPS:
            self.serviceStats["CORE_WAIT_EXISTING_AUTHORITY_COMMITTED"] += 1
            # First occurrence per source is enough; keep raw wait count too.
            tag = "wait:" + str(ev["sourceKey"])
            if not self.serviceStats[tag]:
                self._event(t, "SERVICE_AUTHORITY_WAIT", sourceKey=ev["sourceKey"],
                            required=notional, available=available,
                            nativeReserved=self._reserved_current_expand_risk(),
                            serviceClaim=self._service_claim())
            self.serviceStats[tag] += 1
            return False
        # No extra fifth carrier introduced by this new Core service. Frozen
        # Satellite Active accounting remains unchanged and separately visible.
        if len(self.slot_key) + len(self.activeKeys) >= self.max_slots:
            self.serviceStats["CORE_WAIT_SHARED_CARRIER_CAPACITY"] += 1
            return False
        predicted_key = f"{side}_{self.n}"
        if predicted_key in self.serviceLedger:
            self.serviceChecks["duplicateOwnershipCount"] += 1
            raise RuntimeError("Duplicate service owner")
        # Submission runs synchronously; no receipt/other option can interleave.
        start = len(self.executionDecisions)
        ok = super()._submit_active(t, side, role, q, score, diag)
        if not ok:
            return False
        submits = [x for x in self.executionDecisions[start:]
                   if x.get("event") == "MS4_R2_ACTIVE_REPAIR_SUBMIT"]
        if len(submits) != 1 or submits[0]["key"] != predicted_key:
            raise RuntimeError("Service submit has no unique native owner")
        actual = submits[0]
        if actual["submitRc"] != 0:
            self.serviceChecks["nonzeroNativeSubmitRcCount"] += 1
        charge = float(actual["activePrice"]) * float(actual["qty"])
        if charge > available + EPS:
            self.serviceChecks["coreSubmitBudgetShortfallCount"] += 1
        self.serviceLedger[predicted_key] = {
            "key": predicted_key, "sourceKey": ev["sourceKey"],
            "generation": int(self.scopeGeneration), "scopeSide": self.scopeSide,
            "authorized": charge, "held": charge, "spent": 0.0,
            "released": 0.0, "repairAllocated": 0.0, "terminal": None,
        }
        self.serviceStats["CORE_SERVICE_SUBMIT"] += 1
        self._event(t, "SERVICE_AUTHORITY_RESERVED", **self.serviceLedger[predicted_key],
                    availableBefore=available, submitRc=actual["submitRc"])
        self._check_service()
        return True

    def _check_service(self):
        checks = self.serviceChecks
        for x in self.serviceLedger.values():
            error = abs(x["authorized"] - x["held"] - x["spent"] - x["released"])
            checks["insuranceConservationErrorMax"] = max(checks["insuranceConservationErrorMax"], error)
        if self.scopeSide is not None:
            committed = (float(self.scopeRiskCreditConsumed)
                         + float(self._reserved_current_expand_risk())
                         + self._service_claim())
            checks["combinedAuthorityExcessMax"] = max(
                checks["combinedAuthorityExcessMax"],
                max(0.0, committed - float(self.scopeRiskCreditTotal)))

    def process(self, t):
        old_side, old_gen = self.scopeSide, int(self.scopeGeneration)
        start = len(self.splitEvents)
        # Deliberately skip R246's process/quarantine; retain R240 exactly once.
        r246.r240.HandoffRepairCreditQuarantineSim.process(self, t)
        same = self.scopeSide == old_side and self.scopeGeneration == old_gen
        eligible = 0.0
        details = []
        for ev in self.splitEvents[start:]:
            if ev.get("event") != "ROLE_FILL_SPLIT":
                continue
            key = str(ev["key"])
            x = self.serviceLedger.get(key)
            if x is not None:
                debit = float(ev["fillInc"]) * float(ev["price"])
                self.serviceChecks["insuranceOverfillNotionalMax"] = max(
                    self.serviceChecks["insuranceOverfillNotionalMax"], max(0.0, debit - x["held"]))
                x["held"] = max(0.0, x["held"] - debit)
                x["spent"] += debit
                x["repairAllocated"] += float(ev["repairAllocated"])
                self.serviceStats["CORE_SERVICE_FILL_EVENTS"] += 1
                self._event(t, "SERVICE_AUTHORITY_SPENT", **x, fillNotional=debit,
                            sameScopeGeneration=same)
            # activeMeta persists across terminal reconciliation; activeKeys does not.
            if key not in self.activeMeta:
                continue
            if key in self.handoffKeys:
                self.serviceChecks["activeHandoffOverlapCount"] += 1
                continue  # never quarantine twice
            rq = float(ev["repairAllocated"])
            if rq <= EPS:
                continue
            credit = rq * (1.0 - float(ev["price"]))
            minted_here = (same and old_side is not None
                           and ev["generationAtSubmit"] == old_gen
                           and ev["side"] != old_side)
            self._event(t, "ACTIVE_SERVICE_REPAIR_OBSERVED", key=key,
                        repairQty=rq, creditPotential=credit, creditMintedHere=minted_here,
                        generationAtSubmit=ev["generationAtSubmit"], sameScopeGeneration=same)
            if minted_here:
                eligible += credit
                details.append({"key": key, "credit": credit, "repairQty": rq})
        # Release only after native process has allocated fills AND removed a
        # confirmed terminal key. No release on cancel request or missing snapshot.
        for key, x in self.serviceLedger.items():
            if x["terminal"] is not None or key in self.activeKeys:
                continue
            order = self.orders.get(key)
            if order is None:
                continue
            status = str(self.snap(order).get("status") or "").upper()
            if status not in r246.v2.TERMINAL_STATUSES:
                continue
            x["released"] += x["held"]
            x["held"] = 0.0
            x["terminal"] = status
            self._event(t, "SERVICE_AUTHORITY_TERMINAL", **x)
        if eligible > EPS:
            before = float(self.scopeRiskCreditTotal)
            protected = (float(self.scopeRiskCreditConsumed)
                         + float(self._reserved_current_expand_risk()) + self._service_claim())
            removed = min(eligible, max(0.0, before - protected))
            # Never max(protected, total): that would mint credit if underfunded.
            self.scopeRiskCreditTotal = before - removed
            self.activeCreditEligible += eligible
            self.activeCreditRemoved += removed
            shortfall = max(0.0, eligible - removed)
            self.serviceChecks["quarantineShortfallTotal"] += shortfall
            self.serviceChecks["quarantineConservationErrorMax"] = max(
                self.serviceChecks["quarantineConservationErrorMax"],
                abs(before - self.scopeRiskCreditTotal - removed))
            self.serviceStats["ACTIVE_CREDIT_QUARANTINE_CLOCKS"] += 1
            self._event(t, "ACTIVE_SERVICE_CREDIT_QUARANTINED", eligible=eligible,
                        removed=removed, shortfall=shortfall, before=before,
                        after=self.scopeRiskCreditTotal, protected=protected, details=details)
        self._check_service()

    def run_candidate(self, winner):
        result = self.run_r240(winner)  # winner is only consumed by inherited terminal scoring
        self._check_service()
        if self.coreActiveMaterialized and (self.coreActiveKey not in self.serviceLedger):
            self.serviceChecks["serviceSourceMissingCount"] += 1
        checks = dict(self.serviceChecks)
        result.update({
            "candidateVersion": "GPT6_RESOURCE_SERVICE_V1",
            "candidateCorrectness": checks,
            "candidateCorrectnessPass": all(v <= EPS for v in checks.values()),
            "candidateInterventions": {
                "A_handoffSubmitsInherited": int(self.r239["HANDOFF_SUBMIT"]),
                "B_activeCreditQuarantineClocks": int(self.serviceStats["ACTIVE_CREDIT_QUARANTINE_CLOCKS"]),
                "C_coreServiceSubmits": int(self.serviceStats["CORE_SERVICE_SUBMIT"]),
                "C_coreServiceFillEvents": int(self.serviceStats["CORE_SERVICE_FILL_EVENTS"]),
            },
            "serviceStats": dict(self.serviceStats), "serviceLedger": list(self.serviceLedger.values()),
            "serviceAuthorityClaimCurrent": self._service_claim(),
            "serviceAvailableContinuationCredit": self._available_expand_risk_credit(),
            "activeServiceCreditEligible": self.activeCreditEligible,
            "activeServiceCreditRemoved": self.activeCreditRemoved,
            "coreServiceIntervention": self.coreActiveIntervention,
            "coreEvidenceStats": dict(self.r246),
            "pendingCoreEvidence": self.pendingCoreEvidence,
            "serviceEvents": self.serviceEvents,
            # Complete, uncapped diagnostic chains for attribution outside scoring.
            "slotHistory": self.slot_history, "splitEvents": self.splitEvents,
            "failureEvidenceActiveDrainEvents": self.drainEvents,
            "ms4R2ExecutionDecisions": self.executionDecisions,
            "r240Events": self.r240Events,
            "evidenceBoundary": "No policy training; no extra taker-fee model; consumed cohort only",
        })
        return result
