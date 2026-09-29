from __future__ import annotations
from dataclasses import dataclass, field

EPS = 1e-9

@dataclass
class MultiSlotState:
    slot_id: int
    born_phase: float
    thesis_side: str
    mode: str = 'EXPAND_AND_REPAIR'
    thesis_id: int | None = None
    objective_ids: set[int] = field(default_factory=set)
    expand_carriers: set[str] = field(default_factory=set)
    repair_parent_ids: set[int] = field(default_factory=set)
    generation_tokens: set[str] = field(default_factory=set)
    confirmed_expand_qty: float = 0.0
    confirmed_repair_qty: float = 0.0
    terminal: bool = False

    @property
    def responsibility_live(self) -> bool:
        return (not self.terminal) and (self.confirmed_expand_qty > self.confirmed_repair_qty + EPS or bool(self.repair_parent_ids))


class MultiSlotStateRegistryV1:
    """Management state registry for parallel economic-cycle slots.

    This is not accounting authority. Confirmed-fill accounting remains in
    AllocationLedger; physical execution occupancy remains parent-scoped. The
    registry only prevents legacy singleton pointers (self.thesis/repairParent)
    from being treated as the sole source of truth once >1 cycle is live.
    """
    name = 'multi_slot_state_registry_v1'

    def __init__(self):
        self.slots: dict[int, MultiSlotState] = {}
        self.carrier_to_slot: dict[str, int] = {}
        self.parent_to_slots: dict[int, set[int]] = {}

    def create_slot(self, slot_id: int, *, born_phase: float, thesis_side: str, thesis_id: int | None = None) -> MultiSlotState:
        sid = int(slot_id)
        side = str(thesis_side).upper()
        if side not in ('UP', 'DOWN'):
            raise ValueError('invalid thesis side')
        if sid in self.slots and not self.slots[sid].terminal:
            raise ValueError('slot already live')
        state = MultiSlotState(sid, float(born_phase), side, thesis_id=thesis_id)
        self.slots[sid] = state
        return state

    def attach_expand_carrier(self, slot_id: int, key: str, objective_id: int | None = None) -> None:
        s = self.slots[int(slot_id)]
        k = str(key)
        prior = self.carrier_to_slot.get(k)
        if prior is not None and prior != s.slot_id:
            raise ValueError('carrier already owned by another slot')
        s.expand_carriers.add(k)
        self.carrier_to_slot[k] = s.slot_id
        if objective_id is not None:
            s.objective_ids.add(int(objective_id))

    def observe_expand_fill(self, key: str, fill_increment: float, generation_token: str | None = None) -> int | None:
        sid = self.carrier_to_slot.get(str(key))
        if sid is None:
            return None
        s = self.slots[sid]
        inc = max(0.0, float(fill_increment))
        s.confirmed_expand_qty += inc
        if generation_token:
            s.generation_tokens.add(str(generation_token))
        return sid

    def attach_repair_parent(self, slot_id: int, parent_id: int) -> None:
        sid = int(slot_id); pid = int(parent_id)
        s = self.slots[sid]
        s.repair_parent_ids.add(pid)
        self.parent_to_slots.setdefault(pid, set()).add(sid)

    def observe_repair_payment(self, parent_id: int, repair_increment: float) -> None:
        pid = int(parent_id); inc = max(0.0, float(repair_increment))
        owners = sorted(self.parent_to_slots.get(pid, set()))
        # Attribution here is observability only. If multiple slots share one
        # aggregate parent, consume oldest slot debt first; accounting authority
        # remains AllocationLedger and must independently conserve quantity.
        remaining = inc
        for sid in owners:
            if remaining <= EPS:
                break
            s = self.slots[sid]
            debt = max(0.0, s.confirmed_expand_qty - s.confirmed_repair_qty)
            paid = min(debt, remaining)
            s.confirmed_repair_qty += paid
            remaining -= paid

    def set_mode(self, slot_id: int, mode: str) -> None:
        m = str(mode).upper()
        if m not in ('EXPAND_AND_REPAIR', 'DRAIN_REPAIR_ONLY'):
            raise ValueError('invalid slot mode')
        self.slots[int(slot_id)].mode = m

    def close_if_repaired(self, slot_id: int) -> bool:
        s = self.slots[int(slot_id)]
        if s.confirmed_expand_qty <= s.confirmed_repair_qty + EPS and s.confirmed_expand_qty > EPS:
            s.terminal = True
            return True
        return False

    def live_slots(self) -> list[MultiSlotState]:
        return [s for s in self.slots.values() if not s.terminal]

    def describe(self) -> dict:
        return {
            str(sid): {
                'slotId': s.slot_id,
                'bornPhase': s.born_phase,
                'thesisSide': s.thesis_side,
                'mode': s.mode,
                'thesisId': s.thesis_id,
                'objectiveIds': sorted(s.objective_ids),
                'expandCarriers': sorted(s.expand_carriers),
                'repairParentIds': sorted(s.repair_parent_ids),
                'generationTokens': sorted(s.generation_tokens),
                'confirmedExpandQty': s.confirmed_expand_qty,
                'confirmedRepairQty': s.confirmed_repair_qty,
                'terminal': s.terminal,
            }
            for sid, s in sorted(self.slots.items())
        }
