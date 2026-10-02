"""Reference resource accounting for a future native finite-depth adapter.

Not a backtester, selector, queue model, or runtime guard. Calls represent already
ordered exchange events; they do not infer executions from Target or future tape.
Decimal quantities preserve residuals instead of silently rounding order sizes.
"""
from dataclasses import dataclass, field
from decimal import Decimal


def quantity(value):
    value = Decimal(str(value))
    if not value.is_finite() or value < 0:
        raise ValueError('finite nonnegative quantity required')
    return value


@dataclass
class Level:
    observed: Decimal = Decimal(0)
    consumed: Decimal = Decimal(0)
    sequence: int = -1

    @property
    def available(self):
        return self.observed - self.consumed


class DepthBank:
    """Shared liquidity per native side/price, NOT per order or slot.

    Explicit replay assumption: raw reductions first retire previously simulated
    consumption; only positive raw increments add credit. Identical snapshots do
    not replenish. This is a candidate conservation contract, not identified venue
    market impact. Trade prints must not be applied again as absolute-book deltas.
    """
    def __init__(self):
        self.levels = {}
        self.allocations = {}

    def observe(self, level, sequence, displayed):
        displayed = quantity(displayed)
        old = self.levels.get(level, Level())
        if sequence < old.sequence:
            raise ValueError('out-of-order observation')
        if sequence == old.sequence:
            if displayed != old.observed:
                raise ValueError('conflicting duplicate observation')
            return
        retired = min(old.consumed, max(old.observed - displayed, Decimal(0)))
        self.levels[level] = Level(displayed, old.consumed - retired, sequence)
        self.check()

    def available(self, level):
        return self.levels.get(level, Level()).available

    def allocate(self, level, requested, allocation_id):
        requested = quantity(requested)
        if allocation_id in self.allocations:
            raise ValueError('duplicate allocation id')
        state = self.levels.setdefault(level, Level())
        filled = min(requested, state.available)
        state.consumed += filled
        self.allocations[allocation_id] = (level, filled)
        self.check()
        return filled

    def check(self):
        for state in self.levels.values():
            assert Decimal(0) <= state.consumed <= state.observed
            assert Decimal(0) <= state.available <= state.observed


@dataclass
class Responsibility:
    owner: str
    quantity: Decimal
    filled: Decimal = Decimal(0)
    payment: Decimal = Decimal(0)
    cancel_pending: bool = False
    terminal: str | None = None
    receipts: dict = field(default_factory=dict)

    @property
    def leaves(self):
        return self.quantity - self.filled

    @property
    def outstanding(self):
        return self.leaves if self.terminal is None else Decimal(0)


class ResponsibilityBook:
    """Identity is (native order id, generation); partials retain ownership.

    No fill inference here. A native adapter must supply actual per-price receipts,
    event ordering, price-time priority and quantity permitted by DepthBank or a
    single shared trade budget. A cancel request is never a terminal release.
    """
    def __init__(self):
        self.orders = {}

    def register(self, key, owner, amount):
        amount = quantity(amount)
        if key in self.orders or not owner or amount == 0:
            raise ValueError('new identity, owner and positive amount required')
        if any(old_key[0] == key[0] and order.terminal is None
               for old_key, order in self.orders.items()):
            raise ValueError('native id still occupied by an earlier generation')
        self.orders[key] = Responsibility(owner, amount)

    def fill(self, key, receipt_id, amount, price):
        order = self.orders[key]
        amount, price = quantity(amount), quantity(price)
        fingerprint = (amount, price)
        if receipt_id in order.receipts:
            if order.receipts[receipt_id] != fingerprint:
                raise ValueError('conflicting duplicate receipt')
            return False
        if order.terminal is not None or amount == 0 or amount > order.leaves:
            raise ValueError('unbacked or post-terminal execution')
        order.filled += amount
        order.payment += amount * price
        order.receipts[receipt_id] = fingerprint
        if order.leaves == 0:
            order.terminal = 'FILLED'
            order.cancel_pending = False
        return True

    def request_cancel(self, key):
        order = self.orders[key]
        if order.terminal is not None:
            raise ValueError('terminal order')
        order.cancel_pending = True

    def acknowledge_cancel(self, key):
        order = self.orders[key]
        if order.terminal == 'FILLED':
            return  # A losing cancellation race does not undo a confirmed fill.
        if not order.cancel_pending or order.terminal is not None:
            raise ValueError('no pending cancellation')
        order.terminal = 'CANCELED'
        order.cancel_pending = False


class TradeBudget:
    """One already-observed trade event cannot be offered in full to each slot.

    Queue eligibility/price-time priority must be determined BEFORE allocation.
    This component deliberately does not pretend to infer queue position from L2.
    """
    def __init__(self, amount):
        self.remaining = quantity(amount)

    def allocate(self, eligible):
        fill = min(quantity(eligible), self.remaining)
        self.remaining -= fill
        return fill


class TradeBank:
    """Canonical budget by physical source-event identity, shared by all slots."""
    def __init__(self):
        self.events = {}

    def begin(self, event_id, amount):
        amount = quantity(amount)
        if event_id in self.events:
            previous, budget = self.events[event_id]
            if previous != amount:
                raise ValueError('conflicting physical trade event')
            return budget
        budget = TradeBudget(amount)
        self.events[event_id] = (amount, budget)
        return budget
