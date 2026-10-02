from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Iterable

EPS = 1e-9


class QueueMode(str, enum.Enum):
    STRICT_RISK_AVERSE = "STRICT_RISK_AVERSE"
    L2_DEPLETION_APPROX = "L2_DEPLETION_APPROX"


class OrderState(str, enum.Enum):
    SUBMITTED = "SUBMITTED"
    PENDING_ACK = "PENDING_ACK"
    RESTING = "RESTING"
    PARTIAL_FILL = "PARTIAL_FILL"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


TERMINAL = {OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED, OrderState.EXPIRED}


@dataclass(frozen=True)
class VenueBookEvent:
    event_ms: int
    native_side: str
    native_price: float
    depth_before: float | None = None
    depth_after: float | None = None
    executed_qty: float | None = None
    event_kind: str = "BOOK_CHANGE"

    @property
    def negative_depletion(self) -> float:
        if self.depth_before is None or self.depth_after is None:
            return 0.0
        return max(0.0, float(self.depth_before) - float(self.depth_after))


@dataclass(frozen=True)
class VenueFillDelta:
    event_ms: int
    order_id: str
    side: str
    price: float
    shares: float
    cumulative_shares: float
    remaining_shares: float
    state: str
    queue_ahead_after: float
    source: str


@dataclass
class SimulatedVenueOrder:
    order_id: str
    market_id: int
    side: str
    price: float
    requested_shares: float
    submit_ms: int
    native_side: str
    native_price: float
    initial_visible_depth: float
    queue_mode: QueueMode
    entry_latency_ms: int = 0
    ack_latency_ms: int = 0
    state: OrderState = OrderState.SUBMITTED
    accepted_ms: int | None = None
    resting_ms: int | None = None
    terminal_ms: int | None = None
    queue_ahead: float = 0.0
    filled_shares: float = 0.0
    fill_deltas: list[VenueFillDelta] = field(default_factory=list)
    cancel_requested_ms: int | None = None

    def __post_init__(self) -> None:
        self.queue_ahead = max(0.0, float(self.initial_visible_depth))

    @property
    def remaining_shares(self) -> float:
        return max(0.0, float(self.requested_shares) - float(self.filled_shares))

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    def advance_clock(self, now_ms: int) -> None:
        if self.terminal:
            return
        accepted = self.submit_ms + max(0, int(self.entry_latency_ms))
        resting = accepted + max(0, int(self.ack_latency_ms))
        if now_ms >= accepted and self.accepted_ms is None:
            self.accepted_ms = accepted
            self.state = OrderState.PENDING_ACK
        if now_ms >= resting and self.resting_ms is None:
            self.resting_ms = resting
            self.state = OrderState.RESTING

    def request_cancel(self, now_ms: int) -> None:
        if self.terminal:
            return
        self.cancel_requested_ms = int(now_ms)
        self.state = OrderState.CANCEL_PENDING

    def confirm_cancel(self, now_ms: int) -> None:
        if self.terminal or self.cancel_requested_ms is None:
            return
        self.state = OrderState.CANCELED
        self.terminal_ms = int(now_ms)

    def reject(self, now_ms: int) -> None:
        if self.terminal:
            return
        self.state = OrderState.REJECTED
        self.terminal_ms = int(now_ms)

    def expire(self, now_ms: int) -> None:
        if self.terminal:
            return
        self.state = OrderState.EXPIRED
        self.terminal_ms = int(now_ms)

    def apply_event(self, event: VenueBookEvent) -> VenueFillDelta | None:
        self.advance_clock(int(event.event_ms))
        if self.terminal or self.state not in {OrderState.RESTING, OrderState.PARTIAL_FILL, OrderState.CANCEL_PENDING}:
            return None
        if str(event.native_side) != str(self.native_side):
            return None
        if abs(float(event.native_price) - float(self.native_price)) > 1e-9:
            return None

        if self.queue_mode == QueueMode.STRICT_RISK_AVERSE:
            executable = max(0.0, float(event.executed_qty or 0.0))
            source = "VENUE_EXECUTED_QTY"
        else:
            explicit = max(0.0, float(event.executed_qty or 0.0))
            executable = explicit if explicit > EPS else event.negative_depletion
            source = "VENUE_EXECUTED_QTY" if explicit > EPS else "L2_NEGATIVE_DEPLETION_APPROX"

        if executable <= EPS:
            return None

        queue_consumed = min(self.queue_ahead, executable)
        self.queue_ahead = max(0.0, self.queue_ahead - queue_consumed)
        residual = max(0.0, executable - queue_consumed)
        if residual <= EPS or self.remaining_shares <= EPS:
            return None

        delta = min(self.remaining_shares, residual)
        self.filled_shares += delta
        if self.remaining_shares <= EPS:
            self.state = OrderState.FILLED
            self.terminal_ms = int(event.event_ms)
        else:
            self.state = OrderState.CANCEL_PENDING if self.cancel_requested_ms is not None else OrderState.PARTIAL_FILL
        fill = VenueFillDelta(
            event_ms=int(event.event_ms),
            order_id=self.order_id,
            side=self.side,
            price=float(self.price),
            shares=float(delta),
            cumulative_shares=float(self.filled_shares),
            remaining_shares=float(self.remaining_shares),
            state=self.state.value,
            queue_ahead_after=float(self.queue_ahead),
            source=source,
        )
        self.fill_deltas.append(fill)
        return fill


class Cap100VenueExecutionSimulatorV1:
    """Execution-only simulator for CAP100 Maker lifecycle.

    It deliberately does not decide strategy actions. The controller submits/cancels
    orders; this layer owns latency, resting state, queue-ahead accounting and partial
    fill generation. Inventory must be updated only from returned VenueFillDelta rows.

    STRICT_RISK_AVERSE only advances queue using explicit executed_qty. With the current
    wallet_maker_book_inference L2 dataset, executed_qty is often unavailable; this mode
    therefore provides a conservative lower bound rather than pretending cancellations
    are trades. L2_DEPLETION_APPROX may use negative price-level depletion as a queue
    consumption proxy, and must remain labelled approximate in reports.
    """

    VERSION = "CAP100_VENUE_EXECUTION_SIMULATOR_V1"

    def __init__(self, *, queue_mode: QueueMode = QueueMode.STRICT_RISK_AVERSE,
                 entry_latency_ms: int = 0, ack_latency_ms: int = 0) -> None:
        self.queue_mode = QueueMode(queue_mode)
        self.entry_latency_ms = max(0, int(entry_latency_ms))
        self.ack_latency_ms = max(0, int(ack_latency_ms))
        self.orders: dict[str, SimulatedVenueOrder] = {}

    def submit_maker(self, *, order_id: str, market_id: int, side: str, price: float,
                     shares: float, submit_ms: int, initial_visible_depth: float,
                     native_side: str, native_price: float) -> SimulatedVenueOrder:
        if order_id in self.orders:
            raise ValueError(f"duplicate simulator order_id: {order_id}")
        if side not in {"UP", "DOWN"}:
            raise ValueError("side must be UP or DOWN")
        if shares <= 0 or not 0 < price < 1:
            raise ValueError("invalid Maker order")
        order = SimulatedVenueOrder(
            order_id=str(order_id), market_id=int(market_id), side=str(side),
            price=float(price), requested_shares=float(shares), submit_ms=int(submit_ms),
            native_side=str(native_side), native_price=float(native_price),
            initial_visible_depth=max(0.0, float(initial_visible_depth)),
            queue_mode=self.queue_mode, entry_latency_ms=self.entry_latency_ms,
            ack_latency_ms=self.ack_latency_ms,
        )
        self.orders[order.order_id] = order
        return order

    def advance_clock(self, now_ms: int) -> None:
        for order in self.orders.values():
            order.advance_clock(int(now_ms))

    def apply_event(self, event: VenueBookEvent) -> list[VenueFillDelta]:
        fills: list[VenueFillDelta] = []
        for order in list(self.orders.values()):
            fill = order.apply_event(event)
            if fill is not None:
                fills.append(fill)
        return fills

    def replay(self, events: Iterable[VenueBookEvent]) -> list[VenueFillDelta]:
        fills: list[VenueFillDelta] = []
        for event in sorted(events, key=lambda x: int(x.event_ms)):
            fills.extend(self.apply_event(event))
        return fills

    def request_cancel(self, order_id: str, now_ms: int) -> None:
        self.orders[str(order_id)].request_cancel(int(now_ms))

    def confirm_cancel(self, order_id: str, now_ms: int) -> None:
        self.orders[str(order_id)].confirm_cancel(int(now_ms))

    def snapshot(self) -> dict:
        return {
            "version": self.VERSION,
            "queueMode": self.queue_mode.value,
            "entryLatencyMs": self.entry_latency_ms,
            "ackLatencyMs": self.ack_latency_ms,
            "orders": {
                oid: {
                    "marketId": o.market_id,
                    "side": o.side,
                    "price": o.price,
                    "requestedShares": o.requested_shares,
                    "state": o.state.value,
                    "queueAhead": o.queue_ahead,
                    "filledShares": o.filled_shares,
                    "remainingShares": o.remaining_shares,
                    "submitMs": o.submit_ms,
                    "acceptedMs": o.accepted_ms,
                    "restingMs": o.resting_ms,
                    "terminalMs": o.terminal_ms,
                    "fillDeltas": [f.__dict__ for f in o.fill_deltas],
                }
                for oid, o in self.orders.items()
            },
        }
