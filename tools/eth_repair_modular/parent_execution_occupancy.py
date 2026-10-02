from __future__ import annotations
from dataclasses import dataclass

EPS = 1e-9
TERMINAL = {'FILLED','CANCELED','CANCELLED','REJECTED','EXPIRED'}

@dataclass
class CarrierOccupancy:
    key: str
    parent_id: int
    route: str
    submitted_qty: float
    filled_qty: float = 0.0
    cancel_pending: bool = False
    terminal_confirmed: bool = False

    @property
    def unresolved_qty(self) -> float:
        if self.terminal_confirmed:
            return 0.0
        return max(0.0, float(self.submitted_qty) - float(self.filled_qty))


class ParentRepairExecutionOccupancyV1:
    """Prospective parent-scoped execution occupancy for one Repair responsibility.

    This ledger is deliberately *not* payment accounting.  It only prevents
    Passive and Active execution carriers from jointly reserving more physical
    quantity than the authoritative Repair parent debt can support.

    Confirmed fills continue to be allocated by AllocationLedger V2.  A cancel
    request does not release occupancy until terminal confirmation arrives.
    """
    name = 'parent_scoped_repair_execution_occupancy_v1'

    def __init__(self):
        self.carriers: dict[str, CarrierOccupancy] = {}

    def reserve(self, *, key: str, parent_id: int, route: str, qty: float) -> CarrierOccupancy:
        q = max(0.0, float(qty))
        if q <= EPS:
            raise ValueError('qty must be positive')
        k = str(key)
        existing = self.carriers.get(k)
        if existing is not None:
            if existing.parent_id != int(parent_id):
                raise ValueError('carrier parent mismatch')
            if abs(existing.submitted_qty - q) > 1e-9:
                raise ValueError('carrier qty mismatch')
            return existing
        c = CarrierOccupancy(k, int(parent_id), str(route).upper(), q)
        self.carriers[k] = c
        return c

    def observe_fill(self, key: str, cumulative_fill: float) -> None:
        c = self.carriers.get(str(key))
        if c is None:
            return
        c.filled_qty = max(c.filled_qty, min(c.submitted_qty, max(0.0, float(cumulative_fill))))

    def mark_cancel_pending(self, key: str) -> None:
        c = self.carriers.get(str(key))
        if c is not None and not c.terminal_confirmed:
            c.cancel_pending = True

    def confirm_terminal(self, key: str, *, cumulative_fill: float | None = None) -> None:
        c = self.carriers.get(str(key))
        if c is None:
            return
        if cumulative_fill is not None:
            self.observe_fill(key, cumulative_fill)
        c.terminal_confirmed = True
        c.cancel_pending = False

    def sync_from_carrier(self, key: str, entry: dict, *, parent_id: int | None = None, route: str | None = None) -> None:
        pid = parent_id if parent_id is not None else entry.get('parentId')
        if pid is None:
            return
        q = float(entry.get('submittedQty') or 0.0)
        if q <= EPS:
            return
        c = self.reserve(key=str(key), parent_id=int(pid), route=route or entry.get('lane') or entry.get('execution_role') or 'PASSIVE', qty=q)
        self.observe_fill(str(key), float(entry.get('actualFilled') or 0.0))
        if bool(entry.get('cancelRequested')) and not bool(entry.get('terminalConfirmed')):
            c.cancel_pending = True
        if bool(entry.get('terminalConfirmed')):
            self.confirm_terminal(str(key), cumulative_fill=float(entry.get('actualFilled') or 0.0))

    def parent_reserved(self, parent_id: int, *, exclude_keys: set[str] | None = None) -> float:
        ex = exclude_keys or set()
        return sum(c.unresolved_qty for k,c in self.carriers.items() if c.parent_id == int(parent_id) and k not in ex)

    def available(self, parent_id: int, authoritative_debt: float, *, exclude_keys: set[str] | None = None) -> float:
        return max(0.0, float(authoritative_debt) - self.parent_reserved(int(parent_id), exclude_keys=exclude_keys))

    def can_reserve(self, parent_id: int, authoritative_debt: float, qty: float, *, exclude_keys: set[str] | None = None) -> bool:
        return float(qty) <= self.available(int(parent_id), authoritative_debt, exclude_keys=exclude_keys) + EPS

    def describe_parent(self, parent_id: int, authoritative_debt: float | None = None) -> dict:
        rows=[]
        for c in self.carriers.values():
            if c.parent_id != int(parent_id):
                continue
            rows.append({'key':c.key,'route':c.route,'submittedQty':c.submitted_qty,'filledQty':c.filled_qty,'unresolvedQty':c.unresolved_qty,'cancelPending':c.cancel_pending,'terminalConfirmed':c.terminal_confirmed})
        reserved=sum(float(r['unresolvedQty']) for r in rows)
        out={'parentId':int(parent_id),'reservedQty':reserved,'carriers':rows}
        if authoritative_debt is not None:
            out['authoritativeDebt']=float(authoritative_debt)
            out['availableQty']=max(0.0,float(authoritative_debt)-reserved)
            out['overReservedQty']=max(0.0,reserved-float(authoritative_debt))
        return out
