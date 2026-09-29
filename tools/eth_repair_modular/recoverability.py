from __future__ import annotations
from dataclasses import dataclass
import math

EPS = 1e-9

@dataclass(frozen=True)
class RecursiveCompositeRecoverabilityContext:
    floor_before_expand: float
    floor_after_expand: float
    debt_side: str
    repair_debt: float
    up_bid: float
    down_bid: float
    max_carriers: int = 4
    max_venue_qty: float = 12.0

@dataclass(frozen=True)
class RecursiveCompositeRecoverabilityDecision:
    recoverable: bool
    reason: str
    recovered_step: int | None
    terminal_floor: float
    terminal_debt: float
    max_debt: float
    pair_sum: float
    path: tuple[dict, ...]

class RecursiveCompositeCurrentCoordinateRecoverabilityPolicy:
    """Strict-past structural recoverability under current two-sided bids.

    This policy is behavior-inert by itself.  It never assumes future quotes or
    Target actions.  It asks whether a bounded sequence of venue-min physical
    carriers, all priced at the currently visible bids, can restore the floor
    that existed before a proposed Expand.  Each carrier allocates
    Repair-first and overflow-second; overflow becomes the next explicit debt.
    """
    name = 'RECURSIVE_COMPOSITE_CURRENT_COORDINATE_RECOVERABILITY_V1'

    def evaluate(self, ctx: RecursiveCompositeRecoverabilityContext) -> RecursiveCompositeRecoverabilityDecision:
        side = str(ctx.debt_side).upper()
        if side not in ('UP','DOWN'):
            return self._reject(ctx, 'INVALID_DEBT_SIDE')
        up = float(ctx.up_bid); down = float(ctx.down_bid)
        if not (EPS < up < 1.0-EPS and EPS < down < 1.0-EPS):
            return self._reject(ctx, 'INVALID_CURRENT_BID')
        max_steps = int(ctx.max_carriers)
        if max_steps <= 0:
            return self._reject(ctx, 'NO_RELAY_BUDGET')
        debt = max(0.0, float(ctx.repair_debt))
        if debt <= EPS:
            return self._reject(ctx, 'NO_REPAIR_DEBT')
        floor = float(ctx.floor_after_expand); target = float(ctx.floor_before_expand)
        bids = {'UP': up, 'DOWN': down}
        path: list[dict] = []
        max_debt = debt
        for step in range(1, max_steps + 1):
            pay_side = 'DOWN' if side == 'UP' else 'UP'
            price = bids[pay_side]
            physical = 1.0 / price
            if not math.isfinite(physical) or physical <= EPS or physical > float(ctx.max_venue_qty) + EPS:
                return RecursiveCompositeRecoverabilityDecision(False,'VENUE_MIN_INFEASIBLE',None,floor,debt,max_debt,up+down,tuple(path))
            repair = min(debt, physical)
            overflow = max(0.0, physical - debt)
            # Exact two-outcome floor delta for weak-side repair followed by
            # excess same-side exposure from the same physical carrier.
            floor = floor + repair * (1.0-price) - overflow * price
            path.append({
                'step': step,
                'paySide': pay_side,
                'price': price,
                'physicalQty': physical,
                'repairAllocation': repair,
                'overflowAllocation': overflow,
                'floor': floor,
            })
            if floor >= target - EPS:
                return RecursiveCompositeRecoverabilityDecision(True,'BOUNDED_RECURSIVE_COMPOSITE_RECOVERABLE',step,floor,overflow,max_debt,up+down,tuple(path))
            if overflow > EPS:
                debt = overflow
                side = pay_side
            else:
                debt = max(0.0, debt - physical)
            max_debt = max(max_debt, debt)
            if debt <= EPS:
                break
        return RecursiveCompositeRecoverabilityDecision(False,'BOUNDED_RELAY_DID_NOT_RESTORE_PRE_EXPAND_FLOOR',None,floor,debt,max_debt,up+down,tuple(path))

    @staticmethod
    def _reject(ctx: RecursiveCompositeRecoverabilityContext, reason: str) -> RecursiveCompositeRecoverabilityDecision:
        return RecursiveCompositeRecoverabilityDecision(False,reason,None,float(ctx.floor_after_expand),max(0.0,float(ctx.repair_debt)),max(0.0,float(ctx.repair_debt)),float(ctx.up_bid)+float(ctx.down_bid),tuple())
