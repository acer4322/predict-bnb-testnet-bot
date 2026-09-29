from __future__ import annotations
from dataclasses import dataclass, field

EPS = 1e-9


@dataclass
class ChildReservation:
    key: str
    parent_id: int
    lane: str
    reserved_qty: float
    remaining_reserved_qty: float
    cumulative_fill_seen: float = 0.0
    terminal: bool = False


@dataclass
class ParentReservationState:
    parent_id: int
    children: dict[str, ChildReservation] = field(default_factory=dict)


class ResponsibilityExecutionReservationLedgerV1:
    """Prospective execution-capacity ledger for one Repair responsibility.

    This ledger never allocates confirmed fills economically. AllocationLedger V2
    remains the sole Repair-first/overflow-second fill-allocation authority.

    The only job here is to prevent multiple sibling execution carriers from
    prospectively committing the same parent Repair capacity before fills are
    known. Callers provide the *current authoritative remaining Repair debt*
    whenever reserving a new child.
    """

    name = "responsibility_native_execution_reservation_v1"

    def __init__(self):
        self.parents: dict[int, ParentReservationState] = {}
        self.child_parent: dict[str, int] = {}
        self.events: list[dict] = []

    def _parent(self, parent_id: int) -> ParentReservationState:
        pid = int(parent_id)
        st = self.parents.get(pid)
        if st is None:
            st = ParentReservationState(parent_id=pid)
            self.parents[pid] = st
        return st

    def reserved_total(self, parent_id: int, exclude_key: str | None = None) -> float:
        st = self.parents.get(int(parent_id))
        if st is None:
            return 0.0
        total = 0.0
        for key, child in st.children.items():
            if exclude_key is not None and str(key) == str(exclude_key):
                continue
            if child.terminal:
                continue
            total += max(0.0, float(child.remaining_reserved_qty))
        return total

    def available(self, parent_id: int, authoritative_remaining_debt: float, exclude_key: str | None = None) -> float:
        debt = max(0.0, float(authoritative_remaining_debt))
        return max(0.0, debt - self.reserved_total(parent_id, exclude_key=exclude_key))

    def reserve(
        self,
        child_key: str,
        parent_id: int,
        lane: str,
        repair_reservation_qty: float,
        authoritative_remaining_debt: float,
    ) -> bool:
        key = str(child_key)
        pid = int(parent_id)
        qty = max(0.0, float(repair_reservation_qty))
        debt = max(0.0, float(authoritative_remaining_debt))
        if qty <= EPS:
            self.events.append({"event": "RESERVE_BLOCK", "key": key, "parentId": pid, "reason": "ZERO_RESERVATION"})
            return False
        existing_pid = self.child_parent.get(key)
        if existing_pid is not None and int(existing_pid) != pid:
            self.events.append({"event": "RESERVE_BLOCK", "key": key, "parentId": pid, "reason": "CHILD_ALREADY_BOUND_OTHER_PARENT"})
            return False
        st = self._parent(pid)
        existing = st.children.get(key)
        if existing is not None and not existing.terminal:
            self.events.append({"event": "RESERVE_IDEMPOTENT", "key": key, "parentId": pid, "remaining": existing.remaining_reserved_qty})
            return abs(float(existing.reserved_qty) - qty) <= EPS
        available = self.available(pid, debt, exclude_key=key)
        if qty > available + EPS:
            self.events.append({
                "event": "RESERVE_BLOCK", "key": key, "parentId": pid,
                "reason": "INSUFFICIENT_UNRESERVED_PARENT_CAPACITY", "qty": qty,
                "authoritativeRemainingDebt": debt, "reservedSiblingQty": self.reserved_total(pid, exclude_key=key),
                "available": available,
            })
            return False
        child = ChildReservation(key=key, parent_id=pid, lane=str(lane), reserved_qty=qty, remaining_reserved_qty=qty)
        st.children[key] = child
        self.child_parent[key] = pid
        self.events.append({
            "event": "RESERVE", "key": key, "parentId": pid, "lane": str(lane), "qty": qty,
            "authoritativeRemainingDebt": debt, "availableAfter": max(0.0, available - qty),
        })
        return True

    def observe_cumulative_fill(self, child_key: str, cumulative_fill: float) -> float:
        """Release only the filled portion of this child's prospective reservation.

        Economic debt consumption must already/also be committed by AllocationLedger V2.
        Returns newly observed fill quantity covered by this reservation.
        """
        key = str(child_key)
        pid = self.child_parent.get(key)
        if pid is None:
            return 0.0
        child = self.parents[int(pid)].children.get(key)
        if child is None:
            return 0.0
        cur = max(0.0, float(cumulative_fill))
        old = max(0.0, float(child.cumulative_fill_seen))
        if cur <= old + EPS:
            return 0.0
        inc = cur - old
        covered = min(inc, max(0.0, float(child.remaining_reserved_qty)))
        child.cumulative_fill_seen = cur
        child.remaining_reserved_qty = max(0.0, float(child.remaining_reserved_qty) - covered)
        self.events.append({
            "event": "FILL_RELEASE", "key": key, "parentId": int(pid), "fillIncrement": inc,
            "reservationReleased": covered, "remainingReservedQty": child.remaining_reserved_qty,
        })
        return covered

    def release_terminal(self, child_key: str, reason: str = "TERMINAL") -> float:
        key = str(child_key)
        pid = self.child_parent.get(key)
        if pid is None:
            return 0.0
        child = self.parents[int(pid)].children.get(key)
        if child is None or child.terminal:
            return 0.0
        released = max(0.0, float(child.remaining_reserved_qty))
        child.remaining_reserved_qty = 0.0
        child.terminal = True
        self.events.append({
            "event": "TERMINAL_RELEASE", "key": key, "parentId": int(pid),
            "reason": str(reason), "releasedQty": released,
        })
        return released

    def release_parent_terminal(self, parent_id: int, reason: str = "PARENT_TERMINAL") -> float:
        pid = int(parent_id)
        st = self.parents.get(pid)
        if st is None:
            return 0.0
        released = 0.0
        for key in list(st.children):
            released += self.release_terminal(key, reason=reason)
        self.events.append({"event": "PARENT_TERMINAL_RELEASE", "parentId": pid, "reason": str(reason), "releasedQty": released})
        return released

    def describe_parent(self, parent_id: int, authoritative_remaining_debt: float | None = None) -> dict:
        pid = int(parent_id)
        st = self.parents.get(pid)
        children = []
        if st is not None:
            for key in sorted(st.children):
                c = st.children[key]
                children.append({
                    "key": c.key, "lane": c.lane, "reservedQty": c.reserved_qty,
                    "remainingReservedQty": c.remaining_reserved_qty,
                    "cumulativeFillSeen": c.cumulative_fill_seen, "terminal": c.terminal,
                })
        out = {"parentId": pid, "reservedTotal": self.reserved_total(pid), "children": children}
        if authoritative_remaining_debt is not None:
            debt = max(0.0, float(authoritative_remaining_debt))
            out["authoritativeRemainingDebt"] = debt
            out["available"] = self.available(pid, debt)
            out["reservationOverDebt"] = max(0.0, self.reserved_total(pid) - debt)
        return out
