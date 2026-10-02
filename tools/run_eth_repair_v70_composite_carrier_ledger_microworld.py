from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

EPS = 1e-9


@dataclass
class RepairObligation:
    obligation_id: str
    side: str
    original_qty: float
    born_seq: int
    paid_qty: float = 0.0
    reserved_qty: float = 0.0

    @property
    def available_qty(self) -> float:
        return max(0.0, self.original_qty - self.paid_qty - self.reserved_qty)


@dataclass
class ExpandObjective:
    objective_id: str
    side: str
    budget_qty: float
    born_seq: int
    paid_qty: float = 0.0
    reserved_qty: float = 0.0

    @property
    def available_qty(self) -> float:
        return max(0.0, self.budget_qty - self.paid_qty - self.reserved_qty)


@dataclass
class CompositeCarrier:
    carrier_id: str
    lane: str
    side: str
    submitted_qty: float
    repair_reservations: list[dict]
    expand_objective_id: str | None
    expand_reserved_qty: float
    remaining_sec: float
    physical_filled_qty: float = 0.0
    repair_paid_qty: float = 0.0
    expand_paid_qty: float = 0.0
    live: bool = True
    execution_ids: set[str] = field(default_factory=set)

    @property
    def remaining_qty(self) -> float:
        return max(0.0, self.submitted_qty - self.physical_filled_qty)


class CompositeLedger:
    def __init__(self) -> None:
        self.repairs: dict[str, RepairObligation] = {}
        self.expands: dict[str, ExpandObjective] = {}
        self.latest_expand_by_side: dict[str, str] = {}
        self.carriers: dict[str, CompositeCarrier] = {}
        self.generations: list[dict] = []
        self.events: list[dict] = []
        self.block_counts: dict[str, int] = {}
        self.duplicate_fill_ignored = 0
        self.over_owned = 0
        self.truth_mismatch = 0
        self.unauthorized_role_drift = 0
        self.responsibility_overfill = 0.0
        self.expand_budget_overfill = 0.0

    def add_repair(self, obligation_id: str, side: str, qty: float, born_seq: int) -> None:
        if obligation_id in self.repairs or qty <= EPS or side not in ("UP", "DOWN"):
            raise ValueError("invalid repair obligation")
        self.repairs[obligation_id] = RepairObligation(obligation_id, side, float(qty), int(born_seq))

    def add_expand(self, objective_id: str, side: str, budget_qty: float, born_seq: int) -> None:
        if objective_id in self.expands or budget_qty <= EPS or side not in ("UP", "DOWN"):
            raise ValueError("invalid expand objective")
        prior = self.latest_expand_by_side.get(side)
        if prior is None or self.expands[prior].born_seq < int(born_seq):
            self.latest_expand_by_side[side] = objective_id
        self.expands[objective_id] = ExpandObjective(objective_id, side, float(budget_qty), int(born_seq))

    def _block(self, reason: str, **detail) -> dict:
        self.block_counts[reason] = self.block_counts.get(reason, 0) + 1
        event = {"event": "SUBMIT_BLOCK", "reason": reason, **detail}
        self.events.append(event)
        return {"ok": False, "reason": reason}

    def submit(
        self,
        carrier_id: str,
        lane: str,
        side: str,
        qty: float,
        venue_min_qty: float,
        remaining_sec: float,
        expand_objective_id: str | None = None,
    ) -> dict:
        qty = float(qty)
        if carrier_id in self.carriers or lane not in ("PASSIVE", "ACTIVE") or side not in ("UP", "DOWN"):
            return self._block("INVALID_CARRIER", carrierId=carrier_id)
        if qty + EPS < float(venue_min_qty):
            return self._block("VENUE_MIN", carrierId=carrier_id)

        # Stage-0 execution certainty has hard precedence over capacity/routing.
        # Reserved capacity is unavailable precisely because another live carrier owns it.
        for c in self.carriers.values():
            if not c.live:
                continue
            owns_repair_on_side = any(
                z["remainingReservedQty"] > EPS
                and self.repairs[z["obligationId"]].side == side
                for z in c.repair_reservations
            )
            owns_same_expand = (
                expand_objective_id is not None
                and c.expand_objective_id == expand_objective_id
                and c.expand_reserved_qty > EPS
            )
            if owns_repair_on_side or owns_same_expand:
                return self._block(
                    "LIVE_SHARED_OWNERSHIP",
                    carrierId=carrier_id,
                    liveCarrierId=c.carrier_id,
                )

        repairs = sorted(
            (o for o in self.repairs.values() if o.side == side and o.available_qty > EPS),
            key=lambda o: (o.born_seq, o.obligation_id),
        )
        repair_available = sum(o.available_qty for o in repairs)
        repair_authorized = min(qty, repair_available)
        expand_authorized = max(0.0, qty - repair_authorized)

        if repair_authorized <= EPS:
            return self._block("NO_REPAIR_RESPONSIBILITY", carrierId=carrier_id)
        if remaining_sec <= 180.0 + EPS and expand_authorized > EPS:
            return self._block("TIME_CUTOFF_NO_NEW_EXPOSURE", carrierId=carrier_id)

        objective = None
        if expand_authorized > EPS:
            if expand_objective_id is None or expand_objective_id not in self.expands:
                return self._block("MISSING_EXPAND_AUTHORIZATION", carrierId=carrier_id)
            objective = self.expands[expand_objective_id]
            if objective.side != side:
                return self._block("EXPAND_SIDE_MISMATCH", carrierId=carrier_id)
            if self.latest_expand_by_side.get(side) != expand_objective_id:
                return self._block("SUPERSEDED_EXPAND_OBJECTIVE", carrierId=carrier_id)
            if objective.available_qty + EPS < expand_authorized:
                return self._block("EXPAND_SHARED_BUDGET", carrierId=carrier_id)

        left = repair_authorized
        reservations: list[dict] = []
        for obligation in repairs:
            take = min(left, obligation.available_qty)
            if take <= EPS:
                continue
            obligation.reserved_qty += take
            reservations.append({"obligationId": obligation.obligation_id, "remainingReservedQty": take})
            left -= take
            if left <= EPS:
                break
        if left > EPS:
            raise AssertionError("repair reservation underflow")

        if objective is not None:
            objective.reserved_qty += expand_authorized

        carrier = CompositeCarrier(
            carrier_id=carrier_id,
            lane=lane,
            side=side,
            submitted_qty=qty,
            repair_reservations=reservations,
            expand_objective_id=expand_objective_id if expand_authorized > EPS else None,
            expand_reserved_qty=expand_authorized,
            remaining_sec=float(remaining_sec),
        )
        self.carriers[carrier_id] = carrier
        self.events.append(
            {
                "event": "COMPOSITE_SUBMIT",
                "carrierId": carrier_id,
                "lane": lane,
                "side": side,
                "submittedQty": qty,
                "repairAuthorizedQty": repair_authorized,
                "expandAuthorizedQty": expand_authorized,
                "expandObjectiveId": carrier.expand_objective_id,
            }
        )
        return {
            "ok": True,
            "carrierId": carrier_id,
            "repairAuthorizedQty": repair_authorized,
            "expandAuthorizedQty": expand_authorized,
        }

    def fill(self, carrier_id: str, execution_id: str, qty: float) -> dict:
        carrier = self.carriers[carrier_id]
        qty = float(qty)
        if execution_id in carrier.execution_ids:
            self.duplicate_fill_ignored += 1
            self.events.append({"event": "DUPLICATE_FILL_IGNORED", "carrierId": carrier_id, "executionId": execution_id})
            return {"ok": True, "duplicate": True, "repairQty": 0.0, "expandQty": 0.0}
        if not carrier.live or qty <= EPS or qty > carrier.remaining_qty + EPS:
            raise ValueError("invalid fill")
        carrier.execution_ids.add(execution_id)

        left = qty
        repair_qty = 0.0
        payments = []
        for reservation in carrier.repair_reservations:
            available = float(reservation["remainingReservedQty"])
            take = min(left, available)
            if take <= EPS:
                continue
            obligation = self.repairs[reservation["obligationId"]]
            reservation["remainingReservedQty"] -= take
            obligation.reserved_qty -= take
            obligation.paid_qty += take
            repair_qty += take
            left -= take
            payments.append({"obligationId": obligation.obligation_id, "qty": take})
            if obligation.paid_qty > obligation.original_qty + EPS:
                self.responsibility_overfill += obligation.paid_qty - obligation.original_qty
            if left <= EPS:
                break

        expand_qty = 0.0
        if left > EPS:
            if carrier.expand_objective_id is None or carrier.expand_reserved_qty + EPS < left:
                self.unauthorized_role_drift += 1
                raise AssertionError("fill exceeded preauthorized responsibilities")
            objective = self.expands[carrier.expand_objective_id]
            take = min(left, carrier.expand_reserved_qty)
            carrier.expand_reserved_qty -= take
            objective.reserved_qty -= take
            objective.paid_qty += take
            expand_qty += take
            left -= take
            if objective.paid_qty > objective.budget_qty + EPS:
                self.expand_budget_overfill += objective.paid_qty - objective.budget_qty
            generation_id = f"G{len(self.generations) + 1}"
            self.generations.append(
                {
                    "generationId": generation_id,
                    "sourceCarrierId": carrier_id,
                    "executionId": execution_id,
                    "side": carrier.side,
                    "debtQty": take,
                }
            )

        if left > EPS:
            self.unauthorized_role_drift += 1
            raise AssertionError("unallocated physical fill")

        carrier.physical_filled_qty += qty
        carrier.repair_paid_qty += repair_qty
        carrier.expand_paid_qty += expand_qty
        if carrier.physical_filled_qty + EPS >= carrier.submitted_qty:
            carrier.live = False

        event = {
            "event": "COMPOSITE_FILL",
            "carrierId": carrier_id,
            "executionId": execution_id,
            "physicalQty": qty,
            "repairQty": repair_qty,
            "expandQty": expand_qty,
            "repairPayments": payments,
        }
        self.events.append(event)
        return {"ok": True, "duplicate": False, "repairQty": repair_qty, "expandQty": expand_qty}

    def terminal(self, carrier_id: str) -> None:
        carrier = self.carriers[carrier_id]
        if not carrier.live:
            return
        for reservation in carrier.repair_reservations:
            remain = float(reservation["remainingReservedQty"])
            if remain > EPS:
                self.repairs[reservation["obligationId"]].reserved_qty -= remain
                reservation["remainingReservedQty"] = 0.0
        if carrier.expand_objective_id is not None and carrier.expand_reserved_qty > EPS:
            self.expands[carrier.expand_objective_id].reserved_qty -= carrier.expand_reserved_qty
            carrier.expand_reserved_qty = 0.0
        carrier.live = False
        self.events.append({"event": "CARRIER_TERMINAL", "carrierId": carrier_id})

    def audit(self) -> dict:
        physical = sum(c.physical_filled_qty for c in self.carriers.values())
        repair_paid = sum(o.paid_qty for o in self.repairs.values())
        expand_paid = sum(o.paid_qty for o in self.expands.values())
        generation_debt = sum(float(g["debtQty"]) for g in self.generations)
        carrier_conservation = all(
            abs(c.physical_filled_qty - c.repair_paid_qty - c.expand_paid_qty) <= EPS
            for c in self.carriers.values()
        )
        nonnegative_reservations = all(
            o.reserved_qty >= -EPS and o.available_qty >= -EPS for o in self.repairs.values()
        ) and all(o.reserved_qty >= -EPS and o.available_qty >= -EPS for o in self.expands.values())
        responsibility_ok = all(o.paid_qty <= o.original_qty + EPS for o in self.repairs.values())
        expand_budget_ok = all(o.paid_qty <= o.budget_qty + EPS for o in self.expands.values())
        return {
            "physicalFilledQty": physical,
            "repairPaidQty": repair_paid,
            "expandPaidQty": expand_paid,
            "generationDebtQty": generation_debt,
            "physicalConservation": abs(physical - repair_paid - expand_paid) <= EPS and carrier_conservation,
            "generationDebtExact": abs(generation_debt - expand_paid) <= EPS,
            "nonnegativeReservations": nonnegative_reservations,
            "responsibilityWithinBounds": responsibility_ok and self.responsibility_overfill <= EPS,
            "expandBudgetWithinBounds": expand_budget_ok and self.expand_budget_overfill <= EPS,
            "overOwned": self.over_owned,
            "truthMismatch": self.truth_mismatch,
            "unauthorizedRoleDrift": self.unauthorized_role_drift,
            "duplicateFillIgnored": self.duplicate_fill_ignored,
            "blockCounts": self.block_counts,
        }


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def scenario_full_cross() -> dict:
    x = CompositeLedger()
    x.add_repair("R1", "UP", 2.0, 1)
    x.add_expand("E1", "UP", 8.0, 2)
    s = x.submit("C1", "PASSIVE", "UP", 10.0, 1.0, 240.0, "E1")
    f = x.fill("C1", "F1", 10.0)
    expect(s["ok"] and f["repairQty"] == 2.0 and f["expandQty"] == 8.0, "full cross allocation")
    return x.audit()


def scenario_partial_inside_repair() -> dict:
    x = CompositeLedger()
    x.add_repair("R1", "DOWN", 4.0, 1)
    x.add_expand("E1", "DOWN", 6.0, 2)
    x.submit("C1", "PASSIVE", "DOWN", 10.0, 1.0, 240.0, "E1")
    f = x.fill("C1", "F1", 2.5)
    expect(f["repairQty"] == 2.5 and f["expandQty"] == 0.0 and len(x.generations) == 0, "partial inside repair")
    return x.audit()


def scenario_later_partial_cross() -> dict:
    x = CompositeLedger()
    x.add_repair("R1", "UP", 3.0, 1)
    x.add_expand("E1", "UP", 7.0, 2)
    x.submit("C1", "PASSIVE", "UP", 10.0, 1.0, 240.0, "E1")
    f1 = x.fill("C1", "F1", 1.0)
    f2 = x.fill("C1", "F2", 5.0)
    expect(f1["repairQty"] == 1.0 and f1["expandQty"] == 0.0, "first partial")
    expect(f2["repairQty"] == 2.0 and f2["expandQty"] == 3.0, "later crossing partial")
    return x.audit()


def scenario_oldest_first() -> dict:
    x = CompositeLedger()
    x.add_repair("R_OLD", "DOWN", 2.0, 1)
    x.add_repair("R_NEW", "DOWN", 3.0, 2)
    x.add_expand("E1", "DOWN", 5.0, 3)
    x.submit("C1", "ACTIVE", "DOWN", 10.0, 1.0, 240.0, "E1")
    f = x.fill("C1", "F1", 10.0)
    ev = x.events[-1]
    expect(f["repairQty"] == 5.0 and f["expandQty"] == 5.0, "multi obligation split")
    expect([p["obligationId"] for p in ev["repairPayments"]] == ["R_OLD", "R_NEW"], "oldest first")
    return x.audit()


def scenario_missing_expand_rejected() -> dict:
    x = CompositeLedger()
    x.add_repair("R1", "UP", 2.0, 1)
    s = x.submit("C1", "PASSIVE", "UP", 3.0, 1.0, 240.0, None)
    expect(not s["ok"] and s["reason"] == "MISSING_EXPAND_AUTHORIZATION", "missing expand auth")
    return x.audit()


def scenario_superseded_expand_rejected() -> dict:
    x = CompositeLedger()
    x.add_repair("R1", "UP", 2.0, 1)
    x.add_expand("E_OLD", "UP", 8.0, 2)
    x.add_expand("E_NEW", "UP", 8.0, 3)
    s = x.submit("C1", "PASSIVE", "UP", 5.0, 1.0, 240.0, "E_OLD")
    expect(not s["ok"] and s["reason"] == "SUPERSEDED_EXPAND_OBJECTIVE", "global expand supersede")
    return x.audit()


def scenario_shared_budget_and_ownership() -> dict:
    x = CompositeLedger()
    x.add_repair("R1", "DOWN", 2.0, 1)
    x.add_expand("E1", "DOWN", 8.0, 2)
    s1 = x.submit("C_PASSIVE", "PASSIVE", "DOWN", 10.0, 1.0, 240.0, "E1")
    blocked = x.submit("C_ACTIVE_DUP", "ACTIVE", "DOWN", 3.0, 1.0, 240.0, "E1")
    f1 = x.fill("C_PASSIVE", "F1", 6.0)
    x.terminal("C_PASSIVE")
    x.add_repair("R2", "DOWN", 2.0, 3)
    over = x.submit("C_ACTIVE_OVER", "ACTIVE", "DOWN", 8.0, 1.0, 240.0, "E1")
    s2 = x.submit("C_ACTIVE", "ACTIVE", "DOWN", 6.0, 1.0, 240.0, "E1")
    f2 = x.fill("C_ACTIVE", "F2", 6.0)
    expect(s1["ok"] and not blocked["ok"] and blocked["reason"] == "LIVE_SHARED_OWNERSHIP", "live ownership")
    expect(f1["expandQty"] == 4.0 and not over["ok"] and over["reason"] == "EXPAND_SHARED_BUDGET", "shared budget block")
    expect(s2["ok"] and f2["repairQty"] == 2.0 and f2["expandQty"] == 4.0, "remaining shared budget")
    return x.audit()


def scenario_time_cutoff() -> dict:
    x = CompositeLedger()
    x.add_repair("R1", "UP", 4.0, 1)
    x.add_expand("E1", "UP", 6.0, 2)
    blocked = x.submit("C_BLOCK", "PASSIVE", "UP", 5.0, 1.0, 180.0, "E1")
    allowed = x.submit("C_REPAIR", "PASSIVE", "UP", 4.0, 1.0, 180.0, "E1")
    f = x.fill("C_REPAIR", "F1", 4.0)
    expect(not blocked["ok"] and blocked["reason"] == "TIME_CUTOFF_NO_NEW_EXPOSURE", "cutoff blocks overflow")
    expect(allowed["ok"] and allowed["expandAuthorizedQty"] == 0.0 and f["expandQty"] == 0.0, "repair only at cutoff")
    return x.audit()


def scenario_active_sizing_not_maker_cap() -> dict:
    x = CompositeLedger()
    x.add_repair("R1", "DOWN", 25.0, 1)
    x.add_expand("E1", "DOWN", 7.0, 2)
    s = x.submit("C1", "ACTIVE", "DOWN", 32.0, 1.0, 240.0, "E1")
    f = x.fill("C1", "F1", 32.0)
    expect(s["ok"] and s["repairAuthorizedQty"] == 25.0 and s["expandAuthorizedQty"] == 7.0, "active shared sizing")
    expect(f["repairQty"] == 25.0 and f["expandQty"] == 7.0 and 32.0 > 18.0, "active does not inherit maker cap")
    return x.audit()


def scenario_duplicate_fill_idempotent() -> dict:
    x = CompositeLedger()
    x.add_repair("R1", "UP", 2.0, 1)
    x.add_expand("E1", "UP", 8.0, 2)
    x.submit("C1", "ACTIVE", "UP", 10.0, 1.0, 240.0, "E1")
    first = x.fill("C1", "VENUE_EXEC_1", 10.0)
    before = x.audit()
    dup = x.fill("C1", "VENUE_EXEC_1", 10.0)
    after = x.audit()
    expect(first["repairQty"] == 2.0 and first["expandQty"] == 8.0, "first fill")
    expect(dup["duplicate"] and before["physicalFilledQty"] == after["physicalFilledQty"], "duplicate physical idempotence")
    expect(before["generationDebtQty"] == after["generationDebtQty"] and len(x.generations) == 1, "duplicate generation idempotence")
    return after


SCENARIOS = [
    ("FULL_FILL_REPAIR_THEN_EXPAND", scenario_full_cross),
    ("PARTIAL_FILL_INSIDE_REPAIR", scenario_partial_inside_repair),
    ("LATER_PARTIAL_CROSSES_TO_EXPAND", scenario_later_partial_cross),
    ("MULTI_REPAIR_OLDEST_FIRST", scenario_oldest_first),
    ("NO_EXPAND_AUTH_REJECTED", scenario_missing_expand_rejected),
    ("SUPERSEDED_EXPAND_REJECTED", scenario_superseded_expand_rejected),
    ("SHARED_BUDGET_AND_LIVE_OWNERSHIP", scenario_shared_budget_and_ownership),
    ("TIME_CUTOFF_REPAIR_ONLY", scenario_time_cutoff),
    ("ACTIVE_SIZING_FROM_SHARED_RESPONSIBILITY", scenario_active_sizing_not_maker_cap),
    ("DUPLICATE_FILL_IDEMPOTENT", scenario_duplicate_fill_idempotent),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps({"heartbeat": "V70_MICROWORLD_START"}), flush=True)

    rows = []
    for name, fn in SCENARIOS:
        try:
            audit = fn()
            passed = all(
                [
                    audit["physicalConservation"],
                    audit["generationDebtExact"],
                    audit["nonnegativeReservations"],
                    audit["responsibilityWithinBounds"],
                    audit["expandBudgetWithinBounds"],
                    audit["overOwned"] == 0,
                    audit["truthMismatch"] == 0,
                    audit["unauthorizedRoleDrift"] == 0,
                ]
            )
            rows.append({"scenario": name, "passed": passed, "audit": audit})
            print(json.dumps({"scenario": name, "passed": passed}), flush=True)
        except Exception as exc:
            rows.append({"scenario": name, "passed": False, "error": f"{type(exc).__name__}: {exc}"})
            print(json.dumps({"scenario": name, "passed": False, "error": str(exc)}), flush=True)

    audits = [r["audit"] for r in rows if "audit" in r]
    gates = {
        "allTenScenariosPassed": len(rows) == 10 and all(r["passed"] for r in rows),
        "physicalFillConservation": all(a["physicalConservation"] for a in audits),
        "generationDebtEqualsExpandOverflow": all(a["generationDebtExact"] for a in audits),
        "zeroResponsibilityOverfill": all(a["responsibilityWithinBounds"] for a in audits),
        "zeroExpandBudgetOverfill": all(a["expandBudgetWithinBounds"] for a in audits),
        "zeroOverOwned": all(a["overOwned"] == 0 for a in audits),
        "zeroTruthMismatch": all(a["truthMismatch"] == 0 for a in audits),
        "zeroUnauthorizedRoleDrift": all(a["unauthorizedRoleDrift"] == 0 for a in audits),
        "duplicateFillDoesNotDoublePay": any(a["duplicateFillIgnored"] == 1 for a in audits),
        "globalExpandSupersedeExercised": any(a["blockCounts"].get("SUPERSEDED_EXPAND_OBJECTIVE", 0) == 1 for a in audits),
        "timeCutoffProtectionExercised": any(a["blockCounts"].get("TIME_CUTOFF_NO_NEW_EXPOSURE", 0) == 1 for a in audits),
        "sharedOwnershipAndBudgetExercised": any(
            a["blockCounts"].get("LIVE_SHARED_OWNERSHIP", 0) == 1
            and a["blockCounts"].get("EXPAND_SHARED_BUDGET", 0) == 1
            for a in audits
        ),
    }
    report = {
        "version": "ETH_REPAIR_V70_COMPOSITE_CARRIER_LEDGER_MICROWORLD",
        "researchOnly": True,
        "behaviorChange": False,
        "actionAuthority": False,
        "executionSemantics": "PURE_LEDGER_MICROWORLD_NO_HFT_NO_FILL_ASSUMPTION",
        "hypothesis": "One physical carrier can safely pay preauthorized Repair oldest-first and allocate only its authorized overflow to an existing Expand objective.",
        "gates": gates,
        "functionalPass": all(gates.values()),
        "rows": rows,
        "boundary": [
            "One physical fill may map to Repair and Expand ledger allocations only when both were authorized before submit.",
            "Repair allocation is oldest-first; Expand receives overflow only.",
            "All lanes share the same total objective and responsibility budgets.",
            "Expand overflow creates generation debt exactly once per unique execution.",
            "At <=180 seconds no Expand overflow is authorized.",
            "No Target clock, winner, PnL, threshold, qty, delay, dream fill, H100, or 8781.",
            "A pass authorizes only a 1-3 market reachability/functional smoke.",
        ],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "functionalPass": report["functionalPass"], "gates": gates}), flush=True)


if __name__ == "__main__":
    main()
