"""Research-only logical intent. This module cannot cancel, reserve or submit."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CancelObservation:
    local_order_present: bool
    snapshot_ok: bool
    snapshot_status: str | None
    exchange_order_present: bool
    cancellable: bool
    terminal: bool
    error: str | None = None

    @property
    def ready(self) -> bool:
        return (self.local_order_present and self.snapshot_ok and
                self.exchange_order_present and self.cancellable and
                not self.terminal and self.error is None)

    @property
    def reason(self) -> str:
        if self.terminal:
            return 'TERMINAL'
        if not self.local_order_present:
            return 'LOCAL_ORDER_MISSING'
        if not self.snapshot_ok:
            return 'SNAPSHOT_UNAVAILABLE'
        if self.error:
            return 'EXCHANGE_READ_UNAVAILABLE'
        if not self.exchange_order_present:
            return 'EXCHANGE_ORDER_NOT_VISIBLE'
        return 'READY' if self.cancellable else 'EXCHANGE_ORDER_NOT_CANCELLABLE'


@dataclass
class ReanchorIntent:
    key: str
    parent_id: int
    side: str
    root_key: str
    first_requested_at: int
    last_requested_at: int
    state: str = 'WAIT_READINESS'
    last_observation: CancelObservation | None = None
    accepted_at: int | None = None


class ReanchorIntentLedger:
    """No age threshold; time fields are evidence, never eligibility inputs."""

    def __init__(self):
        self.entries: dict[str, ReanchorIntent] = {}

    def request(self, key, parent_id, side, root_key, t):
        identity = (int(parent_id), str(side).upper(), str(root_key))
        if identity[1] not in ('UP', 'DOWN'):
            raise ValueError('invalid side')
        old = self.entries.get(str(key))
        if old is not None:
            if (old.parent_id, old.side, old.root_key) != identity:
                raise ValueError('physical carrier identity cannot migrate')
            old.last_requested_at = int(t)
            # A retired physical key cannot be made live by a logical request.
            return old
        entry = ReanchorIntent(str(key), *identity, int(t), int(t))
        self.entries[str(key)] = entry
        return entry

    def observe(self, key, observation):
        entry = self.entries[str(key)]
        entry.last_observation = observation
        if observation.terminal:
            entry.state = 'TERMINAL'
        elif entry.state not in ('CANCEL_ACCEPTED', 'CANCEL_OUTCOME_UNKNOWN', 'TERMINAL'):
            entry.state = 'READY' if observation.ready else 'WAIT_READINESS'
        return entry

    def can_dispatch(self, key):
        entry = self.entries[str(key)]
        return (entry.state == 'READY' and entry.last_observation is not None
                and entry.last_observation.ready and entry.accepted_at is None)

    def record_dispatch(self, key, *, accepted, t):
        entry = self.entries[str(key)]
        if not self.can_dispatch(key):
            raise ValueError('cancel requires current readiness and no prior dispatch')
        # A failed underlying call may have side effects. Do not roll it back.
        entry.state = 'CANCEL_ACCEPTED' if accepted else 'CANCEL_OUTCOME_UNKNOWN'
        if accepted:
            entry.accepted_at = int(t)

    def abandon_unsubmitted(self, key):
        entry = self.entries.get(str(key))
        if entry is not None and entry.state in ('READY', 'WAIT_READINESS'):
            del self.entries[str(key)]

