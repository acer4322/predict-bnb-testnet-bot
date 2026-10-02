from __future__ import annotations
from dataclasses import dataclass
from .recoverability import RecursiveCompositeCurrentCoordinateRecoverabilityPolicy, RecursiveCompositeRecoverabilityContext
EPS=1e-9
@dataclass(frozen=True)
class JointMultiSlotContext:
    up_shares_before: float
    down_shares_before: float
    cost_before: float
    slots: tuple[dict,...]
    up_bid: float
    down_bid: float
    max_carriers: int=4
    max_venue_qty: float=12.0
@dataclass(frozen=True)
class JointMultiSlotDecision:
    recoverable: bool
    reason: str
    floor_before: float
    floor_after_joint_fill: float
    joint_repair_debt: float
    debt_side: str|None
    recursive: object|None
class JointMultiSlotCurrentCoordinateRecoverabilityPolicyV1:
    name='JOINT_MULTISLOT_CURRENT_COORDINATE_RECOVERABILITY_V1'
    def __init__(self): self.recursive=RecursiveCompositeCurrentCoordinateRecoverabilityPolicy()
    def evaluate(self,ctx:JointMultiSlotContext)->JointMultiSlotDecision:
        u=float(ctx.up_shares_before);d=float(ctx.down_shares_before);c=float(ctx.cost_before);fb=min(u,d)-c
        if not ctx.slots:return JointMultiSlotDecision(False,'NO_SLOTS',fb,fb,abs(u-d),None,None)
        sides=set()
        for z in ctx.slots:
            s=str(z.get('side') or '').upper();p=float(z.get('price') or 0);q=float(z.get('qty') or 0)
            if s not in ('UP','DOWN') or not(EPS<p<1-EPS) or q<=EPS:return JointMultiSlotDecision(False,'INVALID_SLOT',fb,min(u,d)-c,abs(u-d),None,None)
            sides.add(s);c+=p*q
            if s=='UP':u+=q
            else:d+=q
        fa=min(u,d)-c;gap=abs(u-d)
        if len(sides)!=1:return JointMultiSlotDecision(False,'MIXED_SIDE_NOT_SUPPORTED_V1',fb,fa,gap,None,None)
        if fa>=fb-EPS:return JointMultiSlotDecision(True,'JOINT_FILL_IMMEDIATE_FLOOR_NONWORSE',fb,fa,gap,None,None)
        if gap<=EPS:return JointMultiSlotDecision(False,'JOINT_FILL_NO_REPAIR_DEBT_BUT_FLOOR_WORSE',fb,fa,gap,None,None)
        debt_side='UP' if u>d else 'DOWN'
        r=self.recursive.evaluate(RecursiveCompositeRecoverabilityContext(fb,fa,debt_side,gap,float(ctx.up_bid),float(ctx.down_bid),int(ctx.max_carriers),float(ctx.max_venue_qty)))
        return JointMultiSlotDecision(bool(r.recoverable),'JOINT_'+str(r.reason),fb,fa,gap,debt_side,r)
