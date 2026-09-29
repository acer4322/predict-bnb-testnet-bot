"""Research-only finite risk wheel on R247's physical/FIFO execution substrate.

No Target data, model, outcome, or market identifier is read by this manager.
The explicit extra-risk envelope is 2 quote units for the entire market, not
per generation. Only actual FIFO pairing releases open principal; pair losses
permanently burn capacity. This is not cash, realized credit, or an alpha model.
"""
from collections import Counter, deque
import math

from tools import run_eth_ms4_r2_47_bounded_core_service_favorable_recycle as r247

EPS = 1e-9
INVARIANTS = (
    "fifoQuantityErrorMax", "fifoCostErrorMax", "fifoPairReserveErrorMax",
    "riskQuantityConservationErrorMax", "riskEnvelopeExcessMax",
    "physicalCapacityExcessMax", "lateNewExposureCount", "fillOwnerMismatchCount",
    "riskExecutionAboveLimitMax", "riskLotDoubleUseQty", "invalidPriceCount",
    "staleRiskSubmitCount", "duplicateRiskOwnerCount",
)


class TargetOurSynthesisSim(r247.BoundedCoreServiceFavorableRecycleSim):
    EXTRA_RISK_CAPITAL = 2.0
    MAX_OPEN_RISK_LEASES = 2

    def __init__(self, tape, fanout_limit=1, max_slots=4):
        if fanout_limit != 1 or max_slots != 4:
            raise ValueError("Synthesis V1 is frozen at fanout=1 and shared max4")
        self.synthesisRisk = {}
        self.synthesisFIFO = {s: deque() for s in ("UP", "DOWN")}
        self.synthesisStats = Counter()
        self.synthesisEvents = []
        self.synthesisChecks = {k: 0.0 for k in INVARIANTS}
        self.riskLossBurn = 0.0
        self.mirrorPairReserve = 0.0
        self._physicalFillOwners = deque()
        self._retireRepairLots = Counter()
        self._retireRequested = Counter()
        self._retiredThisClock = Counter()
        self._fillClock = None
        self._endMs = None
        self._receiptRisk = None
        self._dedicatedKeys = set()
        self._managerState = None
        super().__init__(tape, fanout_limit, max_slots)

    def _event(self, t, event, **details):
        ev = {"t": int(t), "event": event, **details}
        self.synthesisEvents.append(ev)
        self.slot_history.append(ev)

    def _held_risk(self, x):
        return 0.0 if x["terminal"] is not None else max(0.0, x["qty"] - x["filledQty"]) * x["limitPrice"]

    def _risk_commitment(self):
        return self.riskLossBurn + sum(self._held_risk(x) + x["unmatchedCost"] for x in self.synthesisRisk.values())

    def _risk_free(self):
        return max(0.0, self.EXTRA_RISK_CAPITAL - self._risk_commitment())

    def _risk_open(self):
        return [x for x in self.synthesisRisk.values() if self._held_risk(x) > EPS or x["unmatchedQty"] > EPS]

    def _issued_authority_current(self):
        # Accounting allowance only, NEVER added to ordinary spendable credit.
        # Native consumed contains historical risk notional, including matched lots.
        return sum(self._held_risk(x) + x["filledQty"] * x["limitPrice"]
                   for x in self.synthesisRisk.values() if x["generation"] == self.scopeGeneration)

    def _audit_r247(self):
        # R255's explicit-authority semantics, with our market-wide loss-bearing
        # envelope controlling issuance instead of a resettable generation token.
        for x in self.serviceLedger.values():
            e = abs(x["authorized"] - x["held"] - x["spent"] - x["released"])
            self.serviceChecks["serviceConservationErrorMax"] = max(self.serviceChecks["serviceConservationErrorMax"], e)
        if self.scopeSide is not None:
            committed = self.scopeRiskCreditConsumed + self._reserved_current_expand_risk() + self._service_claim()
            allowance = self.scopeRiskCreditTotal + self._issued_authority_current()
            self.serviceChecks["combinedAuthorityExcessMax"] = max(self.serviceChecks["combinedAuthorityExcessMax"], max(0.0, committed - allowance))

    def _occupancy(self):
        # Cancel-pending / unknown remains owned until native terminal release.
        return len(set(self.slot_key.values()) | set(self.activeKeys))

    def _audit_synthesis(self):
        c = self.synthesisChecks
        c["physicalCapacityExcessMax"] = max(c["physicalCapacityExcessMax"], max(0, self._occupancy() - 4))
        c["riskEnvelopeExcessMax"] = max(c["riskEnvelopeExcessMax"], max(0.0, self._risk_commitment() - self.EXTRA_RISK_CAPITAL))
        for x in self.synthesisRisk.values():
            error = abs(x["filledQty"] - x["pairedQty"] - x["unmatchedQty"])
            c["riskQuantityConservationErrorMax"] = max(c["riskQuantityConservationErrorMax"], error)
        for side in ("UP", "DOWN"):
            q = sum(x["remaining"] for x in self.synthesisFIFO[side])
            cost = sum(x["remaining"] * x["price"] for x in self.synthesisFIFO[side])
            nq = sum(qty for qty, price in self.un[side])
            nc = sum(qty * price for qty, price in self.un[side])
            c["fifoQuantityErrorMax"] = max(c["fifoQuantityErrorMax"], abs(q - nq))
            c["fifoCostErrorMax"] = max(c["fifoCostErrorMax"], abs(cost - nc))
        c["fifoPairReserveErrorMax"] = max(c["fifoPairReserveErrorMax"], abs(self.mirrorPairReserve - self.pairReserve))

    def record_fill(self, t, side, q, p):
        # Called by the unchanged native physical process in actual orders.values
        # iteration order, with execution price (NOT the submitted limit price).
        if not self._physicalFillOwners:
            self.synthesisChecks["fillOwnerMismatchCount"] += 1
            raise RuntimeError("Native fill without attributed order")
        owner, expected_side, expected_qty = self._physicalFillOwners.popleft()
        if side != expected_side or abs(float(q) - expected_qty) > EPS:
            self.synthesisChecks["fillOwnerMismatchCount"] += 1
            raise RuntimeError("Native fill iteration changed")
        if not math.isfinite(p) or p < 0 or p > 1:
            self.synthesisChecks["invalidPriceCount"] += 1
            raise ValueError("Invalid binary-contract execution price")
        super().record_fill(t, side, q, p)  # physical accounting remains authoritative
        risk = self.synthesisRisk.get(owner)
        if risk:
            risk["filledQty"] += q
            risk["actualCost"] += q * p
            self.synthesisChecks["riskExecutionAboveLimitMax"] = max(
                self.synthesisChecks["riskExecutionAboveLimitMax"], max(0.0, p - risk["limitPrice"]))
            self.synthesisStats["RISK_FILL_EVENTS"] += 1
        opposite = "DOWN" if side == "UP" else "UP"
        left = q
        while left > EPS and self.synthesisFIFO[opposite]:
            prior = self.synthesisFIFO[opposite][0]
            paid = min(left, prior["remaining"])
            old_risk = self.synthesisRisk.get(prior["key"])
            edge = paid * (1.0 - p - prior["price"])
            self.mirrorPairReserve += edge
            if old_risk:
                old_risk["pairedQty"] += paid
                old_risk["unmatchedQty"] = max(0.0, old_risk["unmatchedQty"] - paid)
                old_risk["unmatchedCost"] = max(0.0, old_risk["unmatchedCost"] - paid * prior["price"])
                # This payment may release risk principal OR supply a R247 lot,
                # not both. Native scalar credit remains its frozen separate lane.
                if self.key_role.get(owner) in r247.REPAIR_ROLES:
                    self._retireRepairLots[owner] += paid
                    self._retireRequested[owner] += paid
            if risk:
                risk["pairedQty"] += paid
            if old_risk or risk:
                loss = max(0.0, -edge)  # gains do not enlarge the explicit pool
                self.riskLossBurn += loss
                self.synthesisStats["RISK_FIFO_PAYMENT_EVENTS"] += 1
                self._event(t, "SYNTHESIS_RISK_FIFO_PAYMENT", oldKey=prior["key"],
                            paymentKey=owner, qty=paid, entryPrice=prior["price"],
                            paymentPrice=p, lockedPairEdge=edge, permanentLossBurn=loss,
                            cumulativeLossBurn=self.riskLossBurn)
            prior["remaining"] -= paid
            left -= paid
            if prior["remaining"] <= EPS:
                self.synthesisFIFO[opposite].popleft()
        if left > EPS:
            self.synthesisFIFO[side].append({"key": owner, "side": side, "price": p, "remaining": left, "bornT": int(t)})
            if risk:
                risk["unmatchedQty"] += left
                risk["unmatchedCost"] += left * p
        self._event(t, "SYNTHESIS_PHYSICAL_FILL", key=owner, side=side, qty=q,
                    executionPrice=p, unmatchedBirthQty=left, riskOwned=bool(risk))
        self._audit_synthesis()

    def _clean_lots(self):
        # Runs before ordinary-lot claims/consumption and after R247 process.
        # Only this clock's new lots can be retired for this clock's payments.
        for lot in self.repairLots:
            key = lot["sourceKey"]
            if lot["t"] != self._fillClock or self._retireRepairLots[key] <= EPS:
                continue
            take = min(lot["remaining"], self._retireRepairLots[key])
            lot["remaining"] -= take
            self._retireRepairLots[key] -= take
            self._retiredThisClock[key] += take
            self.synthesisStats["REPAIR_LOT_QTY_ASSIGNED_TO_RISK_RETURN_MICRO"] += int(round(take * 1e6))
            self._event(self._fillClock, "SYNTHESIS_LOT_ASSIGNED_TO_RISK_RETURN", sourceKey=key, lotId=lot["lotId"], qty=take)
        return super()._clean_lots()

    def process(self, t):
        self._fillClock = int(t)
        self._retireRepairLots.clear()
        self._retireRequested.clear()
        self._retiredThisClock.clear()
        self._physicalFillOwners.clear()
        split_start = len(self.splitEvents)
        for key, order in self.orders.items():
            snap = self.snap(order)
            inc = max(0.0, float(snap.get("cumExecQty") or 0.0) - float(order["cum"]))
            if inc > EPS:
                self._physicalFillOwners.append((key, order["side"], inc))
        super().process(t)
        minted = Counter()
        for ev in self.splitEvents[split_start:]:
            if (ev.get("event") == "ROLE_FILL_SPLIT" and ev["role"] in r247.REPAIR_ROLES
                    and ev["generationAtSubmit"] == self.scopeGeneration):
                minted[ev["key"]] += float(ev["repairAllocated"])
        for key, requested in self._retireRequested.items():
            missing = max(0.0, min(requested, minted[key]) - self._retiredThisClock[key])
            self.synthesisChecks["riskLotDoubleUseQty"] = max(self.synthesisChecks["riskLotDoubleUseQty"], missing)
        if self._physicalFillOwners:
            self.synthesisChecks["fillOwnerMismatchCount"] += len(self._physicalFillOwners)
            raise RuntimeError("Native fill omitted from attribution")
        self._release_risk_terminals(t)
        self._audit_synthesis()

    def _release_risk_terminals(self, t):
        owned = set(self.slot_key.values()) | set(self.activeKeys)
        for key, x in self.synthesisRisk.items():
            if x["terminal"] is not None or key in owned:
                continue
            status = str(self.snap(self.orders[key]).get("status") or "").upper()
            if status not in r247.v2.TERMINAL_STATUSES:
                continue
            x["releasedUnfilledNotional"] = self._held_risk(x)
            x["terminal"] = status
            self._event(t, "SYNTHESIS_RISK_CARRIER_TERMINAL", **x)

    def _refresh_slots(self, t):
        super()._refresh_slots(t)
        self._release_risk_terminals(t)
        self._audit_synthesis()

    def _submit_active(self, t, side, role, q, score, diag):
        if self._occupancy() >= self.max_slots:
            self.synthesisStats["ACTIVE_WAIT_SHARED_MAX4"] += 1
            return False  # preserves pending evidence; never turns it into payment
        ok = super()._submit_active(t, side, role, q, score, diag)
        self._audit_synthesis()
        return ok

    def _submit_role_v8(self, t, side, role, p, q, proj, split=None):
        if self._occupancy() >= self.max_slots:
            self.synthesisStats["PASSIVE_WAIT_SHARED_MAX4"] += 1
            return False
        # Close the inherited composite-after-cutoff hole, while allowing pure
        # Repair. Do not add a Floor gate to risk-bearing actions before cutoff.
        new_qty = q if role in ("PROBE_CORE", "SATELLITE_EXPAND") else float((split or {}).get("overflowQty", 0.0))
        if self._endMs is not None and self._endMs - int(t) <= r247.v2.NO_NEW_EXPOSURE_MS and new_qty > EPS:
            self.synthesisStats["LATE_NEW_EXPOSURE_BLOCK"] += 1
            return False
        last = self._last_new_receipt
        if last == int(t):
            self._last_new_receipt = None  # bounded by real shared slots, not one role/receipt
        ok = super()._submit_role_v8(t, side, role, p, q, proj, split)
        if not ok:
            self._last_new_receipt = last
        elif last == int(t):
            self.synthesisStats["SAME_RECEIPT_SPARE_SLOT_SUBMIT"] += 1
        if ok and new_qty > EPS and self._endMs is not None and self._endMs - int(t) <= r247.v2.NO_NEW_EXPOSURE_MS:
            self.synthesisChecks["lateNewExposureCount"] += 1
        self._audit_synthesis()
        return ok

    def _try_risk(self, t, end):
        if self.scopeSide is None or end - t <= r247.v2.NO_NEW_EXPOSURE_MS or self._receiptRisk == int(t):
            return False
        if self._has_stale_scope_reservation() or self._occupancy() >= 4:
            return False
        opened = self._risk_open()
        if len(opened) >= self.MAX_OPEN_RISK_LEASES:
            self.synthesisStats["RISK_WAIT_LEASE_CAP"] += 1
            return False
        # Preserve a pending risk carrier; reevaluation is not blind reposting.
        if any(self._held_risk(x) > EPS for x in opened):
            return False
        side = str(self.scopeSide)  # responsibility orientation, not a claimed alpha thesis
        cand = self._candidate_from_levels_v8(side, "SATELLITE_EXPAND", False)
        if cand is None:
            return False
        p, q, proj, split = cand
        charge = float(p) * float(q)
        if not math.isfinite(charge) or charge <= EPS or charge > self._risk_free() + EPS:
            self.synthesisStats["RISK_WAIT_FINITE_CAPITAL"] += 1
            return False
        key = f"{side}_{self.n}"
        if key in self.synthesisRisk:
            self.synthesisChecks["duplicateRiskOwnerCount"] += 1
            raise RuntimeError("Duplicate risk owner")
        x = {"key": key, "generation": int(self.scopeGeneration), "scopeSide": side,
             "bornT": int(t), "limitPrice": float(p), "qty": float(q), "filledQty": 0.0,
             "actualCost": 0.0, "pairedQty": 0.0, "unmatchedQty": 0.0, "unmatchedCost": 0.0,
             "releasedUnfilledNotional": 0.0, "terminal": None}
        # Register before native audit/submission; roll back only an unsubmitted proposal.
        self.synthesisRisk[key] = x
        if not self._submit_role_v8(t, side, "SATELLITE_EXPAND", p, q, proj, None):
            del self.synthesisRisk[key]
            return False
        if x["generation"] != self.scopeGeneration or side != self.scopeSide:
            self.synthesisChecks["staleRiskSubmitCount"] += 1
        self._receiptRisk = int(t)
        self.synthesisStats["RISK_LEASE_SUBMIT"] += 1
        self._event(t, "SYNTHESIS_RISK_LEASE_SUBMIT", **x, reservedNotional=charge,
                    poolCommittedAfter=self._risk_commitment(), permanentLossBurn=self.riskLossBurn,
                    otherOpenLeases=len(opened), debtAtDecision=self._scope_debt_qty())
        return True

    def _try_risk_service(self, t):
        # Service the native aggregate queue, never the youngest diagnostic lot
        # as if older Probe debt had already been paid.
        if self.scopeSide is None or not any(x["unmatchedQty"] > EPS for x in self._risk_open()):
            return False
        if self._has_stale_scope_reservation() or self._occupancy() >= 4:
            return False
        for key in self._dedicatedKeys:
            if key in self.slot_key.values():
                return False
        side = self._repair_side()
        cand = self._candidate_from_levels_v8(side, "ECONOMIC_CORE", False)
        if cand is None:
            self.synthesisStats["RISK_SERVICE_NO_LEGAL_CARRIER"] += 1
            return False
        p, q, proj, split = cand
        if not split or split["repairQty"] <= EPS:
            return False
        key = f"{side}_{self.n}"
        if not self._submit_role_v8(t, side, "ECONOMIC_CORE", p, q, proj, split):
            return False
        self._dedicatedKeys.add(key)
        self.synthesisStats["RISK_SERVICE_PASSIVE_SUBMIT"] += 1
        self._event(t, "SYNTHESIS_RISK_QUEUE_SERVICE", key=key, side=side, price=p, qty=q,
                    nativeDebt=self._scope_debt_qty(), repairQuota=split["repairQty"],
                    overflowQuota=split["overflowQty"])
        return True

    def _open_one_option(self, t, qv, end):
        self._endMs = int(end)
        state = ("DRAIN" if end - t <= r247.v2.NO_NEW_EXPOSURE_MS else
                 "INITIATE" if self.scopeSide is None else
                 "CYCLE" if self._risk_free() >= 1.0 - EPS else "SERVICE_COMMITTED")
        if state != self._managerState:
            self._managerState = state
            self._event(t, "SYNTHESIS_STATE", state=state, freeRisk=self._risk_free(),
                        openLeases=len(self._risk_open()), scopeGeneration=self.scopeGeneration)
        # Existing Active was considered in refresh. Never invert it into an
        # ordinary-first handback. Use only remaining real physical capacity.
        self._try_risk_service(t)
        super()._open_one_option(t, qv, end)
        # Decontaminated materialization: favorable lot capacity gets a spare
        # slot even if another role used the same receipt; quantity coverage stays.
        self._try_favorable_replenishment(t, end)
        self._try_risk(t, end)
        self._audit_synthesis()

    def run_synthesis(self, winner):
        self._endMs = int((self.payload.get("market") or {}).get("window_end_ms") or self.meta["lastReceivedMs"])
        result = self.run_r247(winner)  # only terminal scoring consumes winner
        self._audit_synthesis()
        result.update({
            "candidateVersion": "GPT6_TARGET_OUR_SYNTHESIS_V1",
            "candidateInvariantViolations": dict(self.synthesisChecks),
            "candidateCorrectnessPass": (all(v <= EPS for v in self.synthesisChecks.values())
                and result["r247ServiceCorrectnessPass"]
                and result["unauthorizedOverflowQty"] <= EPS and result["repairQuotaExcessMax"] <= EPS),
            "synthesisStats": dict(self.synthesisStats), "synthesisEvents": self.synthesisEvents,
            "synthesisRiskLedger": list(self.synthesisRisk.values()),
            "synthesisFifoOutstanding": {s: list(q) for s, q in self.synthesisFIFO.items()},
            "extraRiskCapital": self.EXTRA_RISK_CAPITAL, "extraRiskCommitted": self._risk_commitment(),
            "permanentRiskLossBurn": self.riskLossBurn, "extraRiskFree": self._risk_free(),
            "riskCyclesWithConfirmedPayment": sum(x["pairedQty"] > EPS for x in self.synthesisRisk.values()),
            "riskCyclesFullyPaired": sum(x["filledQty"] > EPS and x["unmatchedQty"] <= EPS and self._held_risk(x) <= EPS for x in self.synthesisRisk.values()),
            "slotHistory": self.slot_history, "splitEvents": self.splitEvents,
            "r247Events": self.r247Events, "r247ServiceEvents": self.serviceEvents,
            "failureEvidenceActiveDrainEvents": self.drainEvents,
            "ms4R2ExecutionDecisions": self.executionDecisions,
        })
        return result
