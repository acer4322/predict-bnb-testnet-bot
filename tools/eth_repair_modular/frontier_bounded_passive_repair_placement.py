from __future__ import annotations
from dataclasses import dataclass
import math
EPS=1e-9

@dataclass(frozen=True)
class FrontierBoundedPassiveRepairContext:
    objective_role:str
    parent_id:int|None
    side:str|None
    proposed_price:float
    proposed_qty:float
    live_best_bid:float|None
    live_best_ask:float|None
    inherited_economic_ceiling:float|None
    floor_before:float
    projected_floor_at_target:float|None
    tick_size:float=.01

@dataclass(frozen=True)
class FrontierBoundedPassiveRepairDecision:
    change_price:bool
    target_price:float
    reason:str
    projected_floor:float|None

class FrontierBoundedPassiveRepairPlacementPolicyV1:
    """Move a newly-created passive Repair child toward the live frontier without
    exceeding inherited economics. No qty/debt/role change and no copied tick gap.
    """
    name='frontier_bounded_passive_repair_placement_v1'
    @staticmethod
    def _floor_to_grid(x:float,tick:float)->float:
        return math.floor((float(x)+1e-12)/float(tick))*float(tick)
    def evaluate(self,ctx:FrontierBoundedPassiveRepairContext)->FrontierBoundedPassiveRepairDecision:
        p=float(ctx.proposed_price);q=float(ctx.proposed_qty)
        if str(ctx.objective_role).upper()!='REPAIR' or ctx.parent_id is None or ctx.side not in ('UP','DOWN'):
            return FrontierBoundedPassiveRepairDecision(False,p,'NOT_PARENT_REPAIR',None)
        if q<=EPS or p<=EPS:return FrontierBoundedPassiveRepairDecision(False,p,'INVALID_PROPOSED_ORDER',None)
        if ctx.live_best_bid is None or ctx.live_best_ask is None or ctx.inherited_economic_ceiling is None:
            return FrontierBoundedPassiveRepairDecision(False,p,'NO_LIVE_FRONTIER_OR_CEILING',None)
        bid=float(ctx.live_best_bid);ask=float(ctx.live_best_ask);ceil=float(ctx.inherited_economic_ceiling);tick=float(ctx.tick_size)
        if not all(math.isfinite(x) for x in (bid,ask,ceil,tick)) or tick<=EPS:
            return FrontierBoundedPassiveRepairDecision(False,p,'INVALID_FRONTIER',None)
        # Highest maker-safe price that is still inside inherited economics.
        raw=min(bid,ceil)
        target=self._floor_to_grid(raw,tick)
        # Never cross the live ask; if rounding/invalid book makes that possible, retain original.
        if target>=ask-EPS:return FrontierBoundedPassiveRepairDecision(False,p,'TARGET_NOT_MAKER_SAFE',None)
        if target<=p+EPS:return FrontierBoundedPassiveRepairDecision(False,p,'PROPOSED_PRICE_ALREADY_AT_OR_ABOVE_BOUNDED_FRONTIER',None)
        proj=ctx.projected_floor_at_target
        if proj is None or not math.isfinite(float(proj)):
            return FrontierBoundedPassiveRepairDecision(False,p,'NO_EXACT_FLOOR_PROJECTION',None)
        proj=float(proj)
        if proj+EPS<float(ctx.floor_before):
            return FrontierBoundedPassiveRepairDecision(False,p,'BOUNDED_FRONTIER_PRICE_DAMAGES_FLOOR',proj)
        return FrontierBoundedPassiveRepairDecision(True,target,'RAISE_PASSIVE_REPAIR_TO_ECONOMIC_FRONTIER',proj)
