"""Research-only explicit grants around the existing Repair-first allocator.

No signal, runtime integration, automatic credit recycling, or value estimation.
The caller must supply economic authority; this class checks its accounting.
Terminal release requires reconciled final cumulative execution, not cancel intent.
"""
from dataclasses import dataclass
import math
from tools.allocation_ledger_v2 import SharedParentDebtAllocationLedgerV2
from tools.hft244_pair_route_legality_v1 import crossing_owners

EPS=1e-9


def nonnegative(*values):
    if not all(isinstance(v,(float,int)) and not isinstance(v,bool) and math.isfinite(v) and v>=0 for v in values):
        raise ValueError('finite nonnegative amounts required')


@dataclass(frozen=True)
class Grant:
    parent_id:int
    generation:str
    side:str
    repair_qty:float
    add_qty:float
    cash_limit:float
    authority_reference:str


@dataclass
class Carrier:
    key:str
    parent_id:int
    route:str
    qty:float
    limit:float
    fee_cap:float
    filled:float=0.
    payment:float=0.
    fees:float=0.
    state:str='SUBMITTED'

    @property
    def reserved_qty(self):return 0. if self.state=='TERMINAL' else self.qty-self.filled

    @property
    def reserved_cash(self):
        return 0. if self.state=='TERMINAL' else self.reserved_qty*self.limit+self.fee_cap-self.fees


class EconomicGrantLedger:
    def __init__(self,capital):
        nonnegative(capital)
        self.capital=float(capital);self.grants={};self.carriers={}
        self.allocation=SharedParentDebtAllocationLedgerV2()

    def issue(self,grant):
        if not isinstance(grant,Grant):raise ValueError('explicit Grant required')
        if type(grant.parent_id) is not int or grant.parent_id<0:raise ValueError('invalid parent identity')
        if grant.parent_id in self.grants:raise ValueError('parent/generation cannot be reset')
        if grant.side not in ('UP','DOWN') or not grant.generation or not grant.authority_reference:
            raise ValueError('side, generation and authority provenance required')
        nonnegative(grant.repair_qty,grant.add_qty,grant.cash_limit)
        if grant.repair_qty+grant.add_qty<=EPS or grant.cash_limit<=EPS:raise ValueError('empty grant')
        if sum(g.cash_limit for g in self.grants.values())+grant.cash_limit>self.capital+EPS:
            raise ValueError('capital already committed to another grant')
        self.grants[grant.parent_id]=grant

    def account(self,parent_id):
        g=self.grants[parent_id];p=self.allocation.parents.get(parent_id)
        cs=[c for c in self.carriers.values() if c.parent_id==parent_id]
        return dict(repair_remaining=p.remaining_debt if p else g.repair_qty,
            repair_paid=p.repair_paid if p else 0.,add_filled=p.overflow_born if p else 0.,
            add_remaining=g.add_qty-(p.overflow_born if p else 0.),
            spent=sum(c.payment+c.fees for c in cs),reserved_cash=sum(c.reserved_cash for c in cs),
            reserved_qty=sum(c.reserved_qty for c in cs))

    def reserve(self,key,parent_id,route,qty,limit,fee_cap,*,now_ms,market_end_ms):
        if not key or key in self.carriers:raise ValueError('carrier identity cannot be rebound')
        if parent_id not in self.grants:raise ValueError('no economic grant')
        if route not in ('PASSIVE','ACTIVE'):raise ValueError('unknown execution route')
        nonnegative(qty,limit,fee_cap,now_ms,market_end_ms)
        if now_ms>=market_end_ms:raise ValueError('market ended')
        if qty<=EPS or not 0<limit<1:raise ValueError('invalid carrier')
        live=[c for c in self.carriers.values() if c.state!='TERMINAL']
        if sum(c.route==route for c in live)>=(4 if route=='PASSIVE' else 1):
            raise ValueError('execution pool full')
        g=self.grants[parent_id];a=self.account(parent_id)
        if qty+a['reserved_qty']>a['repair_remaining']+a['add_remaining']+EPS:
            raise ValueError('shared quantity authority already reserved')
        if qty*limit+fee_cap+a['spent']+a['reserved_cash']>g.cash_limit+EPS:
            raise ValueError('shared cash authority already reserved')
        owners=[dict(key=c.key,side=self.grants[c.parent_id].side,price=c.limit) for c in live]
        if crossing_owners(g.side,limit,owners):raise ValueError('potential own cross including pending')
        # No policy ceilings, fixed seconds, or implied debt from share imbalance.
        self.allocation.register_carrier(key,parent_id,g.repair_qty)
        self.carriers[key]=Carrier(key,parent_id,route,float(qty),float(limit),float(fee_cap))

    def request_cancel(self,key):
        c=self.carriers[key]
        if c.state!='TERMINAL':c.state='CANCEL_PENDING'

    def confirm_cumulative(self,key,filled,payment,fees=0.):
        nonnegative(filled,payment,fees)
        c=self.carriers[key];g=self.grants[c.parent_id]
        if filled<c.filled-EPS or payment<c.payment-EPS or fees<c.fees-EPS:
            raise ValueError('nonmonotone canonical cumulative execution')
        if filled>c.qty+EPS or fees>c.fee_cap+EPS:raise ValueError('execution exceeds reservation')
        dq=filled-c.filled;dp=payment-c.payment
        if dp>dq*c.limit+EPS:raise ValueError('incremental execution exceeds limit')
        if dq<=EPS and (abs(dp)>EPS or abs(fees-c.fees)>EPS):
            raise ValueError('duplicate fill changed payment/fees')
        if c.state=='TERMINAL' and (dq>EPS or dp>EPS or fees>c.fees+EPS):
            raise ValueError('new execution after reconciled terminal: upstream ordering fault')
        a=self.account(c.parent_id)
        if max(0.,dq-a['repair_remaining'])>a['add_remaining']+EPS:
            raise ValueError('unauthorized ADD overflow')
        result=self.allocation.allocate_cumulative(key,c.parent_id,filled,g.repair_qty)
        c.filled=float(filled);c.payment=float(payment);c.fees=float(fees)
        return result

    def confirm_terminal(self,key,*,filled,payment,fees=0.):
        # Atomic validation before releasing resources; late fills must be included.
        result=self.confirm_cumulative(key,filled,payment,fees)
        self.carriers[key].state='TERMINAL'
        return result

    def invariants(self):
        for pid,g in self.grants.items():
            a=self.account(pid)
            assert a['spent']+a['reserved_cash']<=g.cash_limit+EPS
            assert a['add_filled']<=g.add_qty+EPS
            assert abs(a['repair_paid']+a['repair_remaining']-g.repair_qty)<=EPS
            assert a['reserved_qty']<=a['repair_remaining']+a['add_remaining']+EPS
            assert abs(sum(c.filled for c in self.carriers.values() if c.parent_id==pid)-a['repair_paid']-a['add_filled'])<=EPS
        return True
