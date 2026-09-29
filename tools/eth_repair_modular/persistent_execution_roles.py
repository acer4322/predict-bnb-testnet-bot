"""Research-only role selection; no price, quantity, debt or order authority."""
from __future__ import annotations

from dataclasses import dataclass
import math

CORE = "ECONOMIC_CORE"
SATELLITE = "PRIORITY_SATELLITE"


@dataclass(frozen=True)
class ExecutionRole:
    key: str
    parent_id: int
    side: str
    role: str
    root_key: str
    lease_price: float | None = None


class PersistentExecutionRoles:
    name = "GPT6_PERSISTENT_EXECUTION_ROLES_V1"

    def __init__(self):
        self.entries: dict[str, ExecutionRole] = {}

    def bind(self, key, parent_id, side, role, *, lease_price=None, root_key=None):
        side = str(side).upper()
        if side not in ("UP", "DOWN") or role not in (CORE, SATELLITE):
            raise ValueError("invalid execution role")
        if role == CORE and (lease_price is None or not math.isfinite(float(lease_price))
                             or not 0 < float(lease_price) < 1):
            raise ValueError("economic core requires an approved price lease")
        entry = ExecutionRole(str(key), int(parent_id), side, role,
                              str(root_key or key), lease_price)
        old = self.entries.get(str(key))
        if old is not None and old != entry:
            raise ValueError("execution role or parent identity cannot be rewritten")
        self.entries[str(key)] = entry
        return entry

    def inherit(self, old_key, new_key, parent_id, side, *, terminal_confirmed):
        if not terminal_confirmed:
            raise ValueError("replacement requires terminal confirmation")
        old = self.entries[str(old_key)]
        if old.parent_id != int(parent_id) or old.side != str(side).upper():
            raise ValueError("replacement must retain original parent and side")
        return self.bind(new_key, parent_id, side, old.role,
                         lease_price=old.lease_price, root_key=old.root_key)

    def role(self, key):
        entry = self.entries.get(str(key))
        return entry.role if entry else None

    def select(self, rows, reason):
        """Only choose among supplied live carriers; preserve caller ownership."""
        if not rows:
            return None
        if len({(int(r['parentId']), r['side']) for r in rows}) != 1:
            raise ValueError("role selection must be parent and side scoped")
        for row in rows:
            e = self.entries.get(str(row['key']))
            if e and (e.parent_id != int(row['parentId']) or e.side != row['side']):
                raise ValueError("live carrier identity mismatch")
        satellites = [r for r in rows if self.role(r['key']) == SATELLITE]
        if satellites:
            return min(satellites, key=lambda r: (r['price'], r['key']))
        if reason == 'FRONTIER_REANCHOR':
            eligible = [r for r in rows if self.role(r['key']) != CORE]
        elif reason == 'CAPACITY_CONTRACTION':
            eligible = rows  # A zero-capacity parent can still retire its last core.
        else:
            raise ValueError("unsupported management cancellation reason")
        return min(eligible, key=lambda r: (r['price'], r['key'])) if eligible else None
