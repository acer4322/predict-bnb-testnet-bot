"""Research-only joint management plan interface. No model or native order sender.

Reservations commit locally as a group; venue submissions/fills are NEVER atomic.
A fault-injection receipt test is not a fill simulator or market-value teacher.
"""
from dataclasses import dataclass, asdict
from copy import deepcopy
import hashlib
import json
import math

from tools.pair_core_asset_route_sizing_v2 import validate_size


@dataclass(frozen=True)
class PlanAction:
    kind: str
    key: str
    parent_id: int | None = None
    route: str | None = None
    price: float | None = None
    qty: float | None = None
    fee_cap: float = 0.


@dataclass(frozen=True)
class SystemPlan:
    decision_id: str
    state_id: str
    policy_id: str
    continuation_id: str
    actions: tuple[PlanAction, ...]


class PlanRejected(ValueError):
    pass


class SystemPlanGateway:
    """Complete plans operate over one shared grant ledger and confirmed own state.

    Caller provides grants and capability contracts; this object never issues
    economic authority, infers debt from imbalance, or supplies an execution oracle.
    """
    def __init__(self,ledger,*,asset,policy_id,capabilities,initial_inventory=None,initial_cost=0.,
                 tick=.01,quantity_step=.01):
        if asset not in ('BTC','ETH') or not policy_id:raise ValueError('declared asset/policy required')
        if not capabilities or not set(capabilities)<= {'PASSIVE','ACTIVE'}:raise ValueError('capabilities required')
        if any(not math.isfinite(v) or v<=0 for v in (tick,quantity_step)):raise ValueError('explicit grid required')
        self.ledger=deepcopy(ledger);self.asset=asset;self.policy_id=policy_id
        self.capabilities=frozenset(capabilities);self.tick=tick;self.step=quantity_step
        self.initial_inventory=dict(initial_inventory or {'UP':0.,'DOWN':0.})
        if set(self.initial_inventory)!= {'UP','DOWN'}:raise ValueError('two-sided inventory required')
        if any(not math.isfinite(v) or v<0 for v in list(self.initial_inventory.values())+[initial_cost]):
            raise ValueError('finite nonnegative initial accounting required')
        self.initial_cost=float(initial_cost);self.transport={};self.events=[];self.committed_ids=set()
        self.active_continuation=None

    def own_state(self):
        inv=dict(self.initial_inventory);cost=self.initial_cost
        for c in self.ledger.carriers.values():
            inv[self.ledger.grants[c.parent_id].side]+=c.filled
            cost+=c.payment+c.fees
        return dict(inventory=inv,cost=cost,floor=min(inv.values())-cost,best=max(inv.values())-cost,
                    accounts={str(i):self.ledger.account(i) for i in sorted(self.ledger.grants)},
                    transport=dict(sorted(self.transport.items())),continuation=self.active_continuation)

    def snapshot_id(self):
        payload=dict(own=self.own_state(),policy=self.policy_id,asset=self.asset,
            capabilities=sorted(self.capabilities),capital=self.ledger.capital,
            grants={str(k):asdict(v) for k,v in sorted(self.ledger.grants.items())},
            carriers={k:asdict(v) for k,v in sorted(self.ledger.carriers.items())})
        return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

    def propose(self,decision_id,actions,continuation_id):
        return SystemPlan(decision_id,self.snapshot_id(),self.policy_id,continuation_id,tuple(actions))

    def commit(self,plan,*,now_ms,market_end_ms):
        if not isinstance(plan,SystemPlan) or not plan.decision_id or not plan.continuation_id:
            raise PlanRejected('WHOLE_PLAN_AND_CONTINUATION_REQUIRED')
        if plan.policy_id!=self.policy_id:raise PlanRejected('MIXED_POLICY_FRAGMENT')
        if plan.state_id!=self.snapshot_id():raise PlanRejected('STALE_OWN_STATE_REPLAN_REQUIRED')
        if plan.decision_id in self.committed_ids:raise PlanRejected('DUPLICATE_DECISION_ID')
        if len({a.key for a in plan.actions})!=len(plan.actions):raise PlanRejected('DUPLICATE_ACTION_KEY')
        if any(a.kind=='NEW' and a.route not in self.capabilities for a in plan.actions):
            raise PlanRejected('UNSUPPORTED_COMPLETE_PLAN_NOT_HOLD')
        draft=deepcopy(self.ledger);transport=dict(self.transport);before=self.snapshot_id()
        try:
            for a in plan.actions:
                if a.kind in ('KEEP','CANCEL'):
                    if a.key not in draft.carriers or draft.carriers[a.key].state=='TERMINAL':
                        raise PlanRejected('NO_LIVE_OWNER_FOR_MAINTENANCE')
                    if a.kind=='CANCEL':draft.request_cancel(a.key)
                elif a.kind=='NEW':
                    if a.parent_id not in draft.grants:raise PlanRejected('NO_ECONOMIC_AUTHORITY')
                    validate_size(self.asset,a.route,a.price,a.qty,quantity_step=self.step)
                    if abs(a.price/self.tick-round(a.price/self.tick))>1e-8:
                        raise PlanRejected('PRICE_OFF_DECLARED_GRID')
                    account=draft.account(a.parent_id)
                    # Explicit OUR safety fence, not a Target timing rule. Other
                    # pending children might pay Repair first: do not assume this
                    # new order will retain exclusive Repair capacity.
                    if market_end_ms-now_ms<=180000 and account['reserved_qty']+a.qty>account['repair_remaining']+1e-9:
                        raise PlanRejected('NO_NEW_SPECULATIVE_EXPOSURE_AFTER180')
                    draft.reserve(a.key,a.parent_id,a.route,a.qty,a.price,a.fee_cap,
                                  now_ms=now_ms,market_end_ms=market_end_ms)
                    transport[a.key]='RESERVED_NOT_SENT'
                else:raise PlanRejected('UNKNOWN_ACTION')
            draft.invariants()
        except (ValueError,AssertionError,TypeError) as e:
            if isinstance(e,PlanRejected):raise
            raise PlanRejected('JOINT_PLAN_REJECTED: '+str(e)) from e
        # Only local financial reservations publish atomically. This is NOT a
        # claim that multiple native orders can be rolled back together.
        self.ledger=draft;self.transport=transport;self.active_continuation=plan.continuation_id
        self.committed_ids.add(plan.decision_id)
        event=dict(event='JOINT_RESERVATION_COMMITTED',decision=asdict(plan),before=before,
                   after=self.snapshot_id(),own=self.own_state(),venue_atomic=False)
        self.events.append(event);return event

    def record_send(self,key,status,*,evidence):
        if status not in ('SENT','UNKNOWN','NOT_SENT') or not evidence:
            raise ValueError('explicit transport evidence required')
        if self.transport.get(key)!='RESERVED_NOT_SENT':
            raise ValueError('cannot declare existing SENT/UNKNOWN owner unsent')
        if status=='NOT_SENT':
            c=self.ledger.carriers[key]
            if c.filled:raise ValueError('filled owner cannot be unsent')
            self.ledger.confirm_terminal(key,filled=0.,payment=0.,fees=0.)
            self.transport[key]='NOT_SENT_TERMINAL'
        else:self.transport[key]=status
        self.events.append(dict(event='TRANSPORT',key=key,status=status,evidence=evidence,
            needs_replan=status!='SENT',own=self.own_state()))
        self.ledger.invariants()

    def receipt(self,key,*,filled,payment,fees=0.,terminal=False,evidence):
        if not evidence or self.transport.get(key) not in ('SENT','UNKNOWN','CANONICAL_TERMINAL'):
            raise ValueError('unowned or unsent receipt')
        if terminal:
            allocations=self.ledger.confirm_terminal(key,filled=filled,payment=payment,fees=fees)
            self.transport[key]='CANONICAL_TERMINAL'
        else:allocations=self.ledger.confirm_cumulative(key,filled,payment,fees)
        self.ledger.invariants()
        self.events.append(dict(event='CANONICAL_RECEIPT',key=key,filled=filled,payment=payment,
            terminal=terminal,evidence=evidence,own=self.own_state()))
        return allocations
