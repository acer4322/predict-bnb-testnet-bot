from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


UP = "UP"
DOWN = "DOWN"
NEUTRAL = "NEUTRAL"


@dataclass(frozen=True)
class ControllerV0Config:
    """Frozen, deliberately simple architecture-validation settings.

    These values are structural defaults, not tuned from backtest outcomes.
    V0 intentionally uses only spot displacement + Poly top-of-book. Prediction,
    microstructure models, Maker execution, and learned models are deferred.
    """

    spot_deadband_bps: float = 1.5
    poly_confirm_mid: float = 0.55
    target_net_shares: float = 4.0
    gross_cap_shares: float = 8.0
    max_entry_ask: float = 0.75
    max_seconds_left: float = 290.0  # ignore first ~10 s
    min_seconds_left: float = 15.0   # freeze in final ~15 s
    min_action_spacing_seconds: float = 1.0


@dataclass(frozen=True)
class MarketSignal:
    market_id: int
    seconds_left: float
    start_price: float
    spot_price: float
    up_bid: float
    up_ask: float
    down_bid: float
    down_ask: float


@dataclass(frozen=True)
class OpportunityState:
    status: str
    side: str
    spot_move_bps: float
    spot_side: str
    poly_side: str
    up_mid: float
    down_mid: float
    reason: str


@dataclass(frozen=True)
class DesiredExposure:
    side: str
    target_net_shares: float
    reason: str


@dataclass
class PortfolioState:
    up_shares: float = 0.0
    down_shares: float = 0.0
    cash_spent: float = 0.0
    fees_paid: float = 0.0
    last_action_ts: float | None = None

    @property
    def net_shares(self) -> float:
        return self.up_shares - self.down_shares

    @property
    def gross_shares(self) -> float:
        return self.up_shares + self.down_shares

    def add_fill(self, side: str, shares: float, price: float, fee: float, ts: float) -> None:
        if shares <= 0:
            return
        if side == UP:
            self.up_shares += shares
        elif side == DOWN:
            self.down_shares += shares
        else:
            raise ValueError(f"unsupported fill side: {side}")
        self.cash_spent += shares * price
        self.fees_paid += fee
        self.last_action_ts = float(ts)


@dataclass(frozen=True)
class InventoryState:
    up_shares: float
    down_shares: float
    net_shares: float
    gross_shares: float
    desired_net_shares: float
    net_gap_shares: float


@dataclass(frozen=True)
class RiskState:
    gross_cap_shares: float
    gross_remaining: float
    repair_needed: bool
    add_allowed: bool
    reason: str


@dataclass(frozen=True)
class ExecutionIntent:
    action: str
    side: str
    shares: float
    max_price: float | None
    reason: str


@dataclass(frozen=True)
class ControllerTrace:
    opportunity: OpportunityState
    desired: DesiredExposure
    inventory: InventoryState
    risk: RiskState
    intent: ExecutionIntent

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity": asdict(self.opportunity),
            "desired": asdict(self.desired),
            "inventory": asdict(self.inventory),
            "risk": asdict(self.risk),
            "intent": asdict(self.intent),
        }


def _side_from_signed(value: float, deadband: float) -> str:
    if value > deadband:
        return UP
    if value < -deadband:
        return DOWN
    return NEUTRAL


def evaluate_opportunity(signal: MarketSignal, cfg: ControllerV0Config) -> OpportunityState:
    if signal.start_price <= 0 or signal.spot_price <= 0:
        return OpportunityState(
            "INVALID", NEUTRAL, 0.0, NEUTRAL, NEUTRAL, 0.5, 0.5,
            "missing/invalid spot or start price",
        )
    spot_move_bps = (signal.spot_price / signal.start_price - 1.0) * 10_000.0
    spot_side = _side_from_signed(spot_move_bps, cfg.spot_deadband_bps)
    up_mid = (signal.up_bid + signal.up_ask) / 2.0
    down_mid = (signal.down_bid + signal.down_ask) / 2.0
    if up_mid >= cfg.poly_confirm_mid and down_mid <= 1.0 - cfg.poly_confirm_mid:
        poly_side = UP
    elif down_mid >= cfg.poly_confirm_mid and up_mid <= 1.0 - cfg.poly_confirm_mid:
        poly_side = DOWN
    else:
        poly_side = NEUTRAL

    if not (cfg.min_seconds_left < signal.seconds_left <= cfg.max_seconds_left):
        return OpportunityState(
            "TIME_GUARD", NEUTRAL, spot_move_bps, spot_side, poly_side, up_mid, down_mid,
            "outside V0 observation/trading window",
        )
    if spot_side == NEUTRAL:
        return OpportunityState(
            "NO_EDGE", NEUTRAL, spot_move_bps, spot_side, poly_side, up_mid, down_mid,
            "spot remains inside fixed deadband",
        )
    if poly_side == NEUTRAL:
        return OpportunityState(
            "UNCONFIRMED", NEUTRAL, spot_move_bps, spot_side, poly_side, up_mid, down_mid,
            "spot has direction but Poly is not confirming",
        )
    if poly_side != spot_side:
        return OpportunityState(
            "CONFLICT", NEUTRAL, spot_move_bps, spot_side, poly_side, up_mid, down_mid,
            "spot and Poly point in opposite directions",
        )
    return OpportunityState(
        "CONFIRMED", spot_side, spot_move_bps, spot_side, poly_side, up_mid, down_mid,
        "spot and Poly agree",
    )


def desired_exposure(opportunity: OpportunityState, cfg: ControllerV0Config) -> DesiredExposure:
    if opportunity.status != "CONFIRMED" or opportunity.side not in {UP, DOWN}:
        return DesiredExposure(NEUTRAL, 0.0, f"{opportunity.status}: no new target exposure")
    signed = cfg.target_net_shares if opportunity.side == UP else -cfg.target_net_shares
    return DesiredExposure(opportunity.side, signed, "fixed V0 target exposure on confirmed opportunity")


def inventory_state(portfolio: PortfolioState, desired: DesiredExposure) -> InventoryState:
    return InventoryState(
        up_shares=portfolio.up_shares,
        down_shares=portfolio.down_shares,
        net_shares=portfolio.net_shares,
        gross_shares=portfolio.gross_shares,
        desired_net_shares=desired.target_net_shares,
        net_gap_shares=desired.target_net_shares - portfolio.net_shares,
    )


def risk_state(inv: InventoryState, desired: DesiredExposure, cfg: ControllerV0Config) -> RiskState:
    remaining = max(0.0, cfg.gross_cap_shares - inv.gross_shares)
    repair = (
        desired.side == UP and inv.net_shares < -1e-12
    ) or (
        desired.side == DOWN and inv.net_shares > 1e-12
    )
    if remaining <= 1e-12:
        reason = "gross cap exhausted"
    elif repair:
        reason = "desired side opposes current net inventory; hedge only toward neutral"
    else:
        reason = "gross capacity available"
    return RiskState(
        gross_cap_shares=cfg.gross_cap_shares,
        gross_remaining=remaining,
        repair_needed=repair,
        add_allowed=remaining > 1e-12,
        reason=reason,
    )


def execution_intent(
    signal: MarketSignal,
    portfolio: PortfolioState,
    desired: DesiredExposure,
    inv: InventoryState,
    risk: RiskState,
    cfg: ControllerV0Config,
    *,
    now_ts: float,
) -> ExecutionIntent:
    if desired.side not in {UP, DOWN}:
        return ExecutionIntent("HOLD", NEUTRAL, 0.0, None, "no confirmed desired exposure")
    if portfolio.last_action_ts is not None and now_ts - portfolio.last_action_ts < cfg.min_action_spacing_seconds:
        return ExecutionIntent("HOLD", NEUTRAL, 0.0, None, "action spacing guard")
    if not risk.add_allowed:
        return ExecutionIntent("HOLD", NEUTRAL, 0.0, None, "gross exposure cap")

    ask = signal.up_ask if desired.side == UP else signal.down_ask
    if not (0.0 < ask <= cfg.max_entry_ask):
        return ExecutionIntent("HOLD", NEUTRAL, 0.0, None, "selected-side ask exceeds fixed V0 price cap")

    if risk.repair_needed:
        # V0 never flips an existing position in one reversal. It buys the
        # opposite outcome only far enough to neutralize the current net.
        qty = min(abs(inv.net_shares), risk.gross_remaining)
        if qty <= 1e-12:
            return ExecutionIntent("HOLD", NEUTRAL, 0.0, None, "no repair capacity")
        return ExecutionIntent(
            "TAKER_REPAIR", desired.side, qty, ask,
            "opposite confirmed regime: hedge existing net exposure toward neutral",
        )

    gap = abs(inv.net_gap_shares)
    qty = min(gap, risk.gross_remaining)
    if qty <= 1e-12:
        return ExecutionIntent("HOLD", NEUTRAL, 0.0, None, "already at desired exposure")
    return ExecutionIntent(
        "TAKER_ADD", desired.side, qty, ask,
        "build own inventory toward fixed desired exposure",
    )


class TargetStyleControllerV0:
    """Minimal hierarchical portfolio controller; backtest/research only."""

    def __init__(self, config: ControllerV0Config | None = None) -> None:
        self.config = config or ControllerV0Config()

    def step(self, signal: MarketSignal, portfolio: PortfolioState, *, now_ts: float) -> ControllerTrace:
        opportunity = evaluate_opportunity(signal, self.config)
        desired = desired_exposure(opportunity, self.config)
        inv = inventory_state(portfolio, desired)
        risk = risk_state(inv, desired, self.config)
        intent = execution_intent(
            signal, portfolio, desired, inv, risk, self.config, now_ts=now_ts
        )
        return ControllerTrace(opportunity, desired, inv, risk, intent)


def market_signal_from_mapping(row: Mapping[str, Any]) -> MarketSignal:
    return MarketSignal(
        market_id=int(row["market_id"]),
        seconds_left=float(row["seconds_left"]),
        start_price=float(row["start_price"]),
        spot_price=float(row["spot_price"]),
        up_bid=float(row["up_bid"]),
        up_ask=float(row["up_ask"]),
        down_bid=float(row["down_bid"]),
        down_ask=float(row["down_ask"]),
    )
