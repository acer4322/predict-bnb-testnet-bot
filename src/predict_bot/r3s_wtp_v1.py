from __future__ import annotations
import math
from typing import Any

VERSION = "R3S_WTP_EPISODE_ANCHOR_V1"
ADD_BASE_DRIFT = 0.02
ADD_ANCHOR_TTL_MS = 15_000
REPAIR_MIN_DRIFT = 0.02
REPAIR_MAX_DRIFT = 0.05
CONTAINMENT_MIN_DRIFT = 0.04
CONTAINMENT_MAX_DRIFT = 0.08


def _f(v: Any, d: float = 0.0) -> float:
    try:
        x=float(v); return x if math.isfinite(x) else d
    except Exception: return d

class R3SWillingnessToPayV1:
    def __init__(self) -> None:
        self.add_anchor: dict[str, Any] | None = None

    def reset_market(self) -> None:
        self.add_anchor=None

    @staticmethod
    def severity(port: dict[str, Any]) -> float:
        gross=max(_f(port.get("combined_gross"),0.0),1.0)
        absnet=max(_f(port.get("combined_abs_net"),0.0),0.0)
        floor=_f(port.get("worst_case_floor"),0.0)
        imbalance=min(1.0,absnet/gross)
        floor_stress=min(1.0,max(0.0,-floor)/max(absnet,5.0))
        return max(imbalance,floor_stress)

    def quote(self, *, structural_effect: str, side: str, observed_ask: float, now_ms: int,
              port: dict[str, Any], containment: bool=False, context: dict[str, Any] | None=None) -> dict[str, Any]:
        ask=max(0.0,min(0.99,_f(observed_ask,0.0)))
        effect=str(structural_effect or '').upper(); side=str(side or '').upper(); now=int(now_ms)
        sev=self.severity(port)
        if effect=="ADD_EFFECT":
            anchor=self.add_anchor
            same=bool(isinstance(anchor,dict) and str(anchor.get("side"))==side and now-int(anchor.get("startedAtMs") or now)<=ADD_ANCHOR_TTL_MS)
            proposed=min(0.99,ask+ADD_BASE_DRIFT)
            if same:
                ceiling=min(_f(anchor.get("maxPrice"),proposed),proposed if ask < _f(anchor.get("observedAsk"),ask) else _f(anchor.get("maxPrice"),proposed))
                # Never ratchet upward inside one ADD episode. A cheaper market may tighten the ceiling.
                ceiling=min(_f(anchor.get("maxPrice"),proposed),proposed)
                anchor["maxPrice"]=ceiling; anchor["lastObservedAsk"]=ask; anchor["lastAtMs"]=now
                mode="ADD_EPISODE_ANCHOR_REUSED"
            else:
                ceiling=proposed
                self.add_anchor={"side":side,"startedAtMs":now,"lastAtMs":now,"observedAsk":ask,"lastObservedAsk":ask,"maxPrice":ceiling}
                mode="ADD_EPISODE_ANCHOR_NEW"
            return {"version":VERSION,"mode":mode,"structuralEffect":effect,"side":side,"observedAsk":ask,"maxPrice":ceiling,"drift":max(0.0,ceiling-ask),"severity":sev,"anchor":dict(self.add_anchor or {}),"context":dict(context or {})}
        # A repair is a new risk-reduction budget, not an ADD retry. Clear ADD willingness to pay.
        self.add_anchor=None
        if containment:
            drift=CONTAINMENT_MIN_DRIFT+(CONTAINMENT_MAX_DRIFT-CONTAINMENT_MIN_DRIFT)*sev
            mode="CONTAINMENT_RISK_BUDGET"
        else:
            drift=REPAIR_MIN_DRIFT+(REPAIR_MAX_DRIFT-REPAIR_MIN_DRIFT)*sev
            mode="REPAIR_RISK_BUDGET"
        ceiling=min(0.99,ask+drift)
        return {"version":VERSION,"mode":mode,"structuralEffect":effect,"side":side,"observedAsk":ask,"maxPrice":ceiling,"drift":max(0.0,ceiling-ask),"severity":sev,"anchor":None,"context":dict(context or {})}
