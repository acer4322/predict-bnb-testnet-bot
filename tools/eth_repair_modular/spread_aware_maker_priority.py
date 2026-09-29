from __future__ import annotations
from dataclasses import dataclass

EPS=1e-9

@dataclass(frozen=True)
class SpreadAwareMakerPriorityContext:
    best_bid: float
    best_ask: float
    tick_size: float = 0.01
    economic_ceiling: float | None = None

@dataclass(frozen=True)
class SpreadAwareMakerPriorityDecision:
    price: float
    improved: bool
    reason: str

class SpreadAwareMakerPriorityPolicyV1:
    name='spread_aware_one_tick_maker_priority_v1'
    def evaluate(self,ctx:SpreadAwareMakerPriorityContext)->SpreadAwareMakerPriorityDecision:
        bid=float(ctx.best_bid);ask=float(ctx.best_ask);tick=float(ctx.tick_size)
        if not (tick>EPS and bid>EPS and ask>bid+EPS and ask<1.0+EPS):
            return SpreadAwareMakerPriorityDecision(bid,False,'INVALID_OR_NO_SPREAD')
        candidate=round(bid+tick,10)
        maker_max=round(ask-tick,10)
        if candidate>maker_max+EPS:
            return SpreadAwareMakerPriorityDecision(bid,False,'NO_INSIDE_SPREAD_MAKER_TICK')
        ceiling=ctx.economic_ceiling
        if ceiling is not None and candidate>float(ceiling)+EPS:
            return SpreadAwareMakerPriorityDecision(bid,False,'IMPROVEMENT_ABOVE_ECONOMIC_CEILING')
        return SpreadAwareMakerPriorityDecision(candidate,True,'ONE_TICK_INSIDE_SPREAD_PRIORITY')
