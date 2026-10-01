"""Research-only physical work binding and existing passive service fallback."""
import atexit
from copy import deepcopy
import gzip
import json
import os
from pathlib import Path

from sizing import TICKET
EPS = 1e-8
MODE = os.environ.get('V12G_WORK_CONTINUATION', 'OFF')
assert MODE in ('OFF', 'BIND', 'SERVICE')
DEMAND_MODE = os.environ.get('V12G_CONTINUATION_DEMAND', 'OFF')
assert DEMAND_MODE in ('OFF', 'ON')
DEMAND = None
INSTANCES = []


def bound_goal(original, roles):
    class PhysicalGoal(original):
        def __init__(self, initial, extra):
            self.physical_side = roles.weak
            self.opposite_side = roles.strong
            super().__init__(initial, extra)

        def update(self, state, t, end):
            side, other = self.physical_side, self.opposite_side
            remaining = max(0., self.target - state['inv'][side])
            if self.status == 'ACTIVE':
                if remaining <= EPS:
                    self.status = 'CONFIRMED_TARGET_REACHED'
                elif roles.weak != side:
                    self.status = 'WITHDRAWN_DIRECTION_CHANGED_WITH_RESIDUAL'
                elif t >= end:
                    self.status = 'WITHDRAWN_MARKET_END'
                elif state['inv'][other] <= state['inv'][side] + EPS:
                    self.status = 'WITHDRAWN_ORIGINAL_NET_DIRECTION_GONE'
                elif state['payoff'][side] >= 0:
                    self.status = 'WITHDRAWN_DOWN_ALREADY_NONNEGATIVE'
                if self.status != 'ACTIVE':
                    self.stopped_t = t
            pending = state['pending_qty'][side]
            return dict(status=self.status, stopped_t=self.stopped_t, target=self.target,
                        initial_down=self.initial_down, initial_committed_down=self.initial_pending,
                        new_request=self.extra, acquired_since_birth=state['inv'][side]-self.initial_down,
                        remaining_confirmed=remaining, pending_down=pending,
                        pending_cash_down=state['pending_cash'][side], pending_cash_up=state['pending_cash'][other],
                        unreserved_need=max(0., remaining-pending), pending_excess_over_goal=max(0.,pending-remaining),
                        down_payoff=state['payoff'][side], physical_side=side, opposite_side=other)

        def desired(self, original):
            if self.status != 'ACTIVE' or roles.weak != self.physical_side:
                return original
            return dict(original, **{self.physical_side:max(original[self.physical_side],self.target)})
    return PhysicalGoal


def proposal(state, operations, demand_row, goal, roles, capacity_fn, ask, crossing, step):
    """No ledger mutation; original plan and all nonterminal owners have priority."""
    from overlay.economics import with_plan
    row = dict(eligible=False, reason='NO_ACTIVE_PHYSICAL_DUST_WORK')
    if goal is None or goal.status != 'ACTIVE' or goal.physical_side != roles.weak:
        return row
    side = goal.physical_side
    remaining = max(0., goal.target-state['inv'][side])
    eligibility = demand_row['eligibility']
    minimum = eligibility.get('minimum_reference')
    row.update(side=side, old_target=goal.target, old_remaining=remaining,
               work_id=demand_row['work_id'], physical_side=side)
    if minimum is None or not EPS < remaining < minimum-EPS:
        return row
    row['reason'] = 'WAIT_FOR_CANONICAL_SAME_SIDE_TERMINAL'
    if state['pending_qty'][side] > EPS or state['pending_cash'][side] > EPS or any(o['side']==side for o in state['owners']):
        return row
    row['reason'] = 'ORIGINAL_SAME_SIDE_NEW_PRIORITY'
    if any(o['kind']=='NEW' and o['side']==side for o in operations):
        return row
    planned = with_plan(state,operations)
    price = eligibility['price']
    capacity = capacity_fn(planned,demand_row['original_desired'],price,TICKET,step)
    row.update(capacity=capacity,price=price,quantity=TICKET,planned=planned)
    row['reason'] = 'NO_INDEPENDENT_CURRENT_REPAIR_CAPACITY'
    if not capacity['economically_eligible'] or not eligibility['eligible'] or abs(capacity['requested']-TICKET)>EPS:
        return row
    row['reason'] = 'NOT_A_PASSIVE_QUOTE'
    if ask is None or price >= ask-EPS:
        return row
    owners = [dict(key=o['key'],side=o['side'],price=o['limit']) for o in planned['owners']]
    conflicts = crossing(side,price,owners)
    row.update(conflicts=conflicts,reason='PENDING_OR_PLAN_OWN_CROSS')
    if conflicts:
        return row
    row.update(eligible=True,reason='CURRENT_REPAIR_AFTER_SUBTICKET_RESIDUAL',
               inherited_quantity=remaining,independent_quantity=TICKET-remaining,
               independent_inventory_interval=[goal.target,state['inv'][side]+TICKET])
    assert row['independent_quantity']>EPS
    return row


def install_module(name, module, roles, ctx, risk_guard):
    global DEMAND
    if MODE == 'OFF':
        return
    if name == 'single_repair_demand':
        module.old.FiniteGoal = bound_goal(module.old.FiniteGoal,roles)
        DEMAND = module
        inherited = module.SingleRepairDemand
        class PhysicalDemand(inherited):
            def apply(self,*args,**kwargs):
                result=super().apply(*args,**kwargs)
                if self.goal is not None:
                    self.current_work['physical_side']=self.goal.physical_side
                    self.current_work['opposite_side']=self.goal.opposite_side
                if DEMAND_MODE == 'ON':
                    from intent_demand import desired as continuation_desired
                    orders=[o for instance in INSTANCES for o in instance.continuation_orders]
                    if orders:
                        result, rows=continuation_desired(args[0],result,orders,self.snapshot,roles.weak)
                        self.rows[-1]['continuation_intents']=rows
                        self.rows[-1]['effective_desired']=dict(result)
                        self.rows[-1]['continuation_weak']=roles.weak
                return result
        module.SingleRepairDemand=PhysicalDemand
    if name != 'commitment_repair_probe' or MODE != 'SERVICE':
        return
    assert DEMAND is not None
    inherited = module.CommitmentRepairProbe
    class ContinuedRepair(inherited):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.continuation_rows=[]
            self.continuation_orders=[]
            INSTANCES.append(self)

        def maintenance(self,frame,key,owner,raw_price,stale,threshold):
            if key not in {o['key'] for o in self.continuation_orders}:
                return super().maintenance(frame,key,owner,raw_price,stale,threshold)
            # Same quote rule, independent registry: don't alter the old trigger's outstanding set.
            if not module.LEGAL_QUOTE:
                return raw_price,stale
            ask=((frame.get('quotes') or {}).get(roles.weak) or {}).get('ask')
            quote=module.legal_quote(raw_price,ask,frame['world_profile']['tick'])
            revised=abs(float(owner.limit)-quote['price'])>threshold+1e-9
            self.maintenance_rows.append(dict(t=int(frame['t']),key=key,owner_state=owner.state,
                limit=float(owner.limit),quote=quote,threshold=threshold,old_stale=stale,new_stale=revised,
                continuation=True))
            return quote['price'],revised

        def observe_continuations(self,frame):
            for order in self.continuation_orders:
                carrier=frame['ledger'].carriers.get(order['key'])
                if carrier is None:
                    continue
                filled=float(carrier.filled)
                assert filled <= order['qty']+EPS
                inherited_credit=min(filled,order['inherited_quantity'])
                fresh_credit=max(0.,filled-inherited_credit)
                assert abs(inherited_credit+fresh_credit-filled)<EPS
                order.update(confirmed_fill=filled,old_residual_service=inherited_credit,
                             independent_service=fresh_credit,owner_state=carrier.state,
                             physical_inventory=float(frame['own_view']['inv'][order['side']]),
                             last_observed_t=int(frame['t']))

        def apply(self,frame,producer,operations,validate,crossing):
            self.observe_continuations(frame)
            # Keep the existing service first, including its original count/quote gates.
            operations=super().apply(frame,producer,operations,validate,crossing)
            if not frame['start'] <= frame['t'] < frame['end']:
                return operations
            demand=producer.demand
            if not demand.rows or demand.rows[-1]['t']!=frame['t']:
                return operations
            state=self.snapshot(frame,frame['ledger'])
            ask=((frame.get('quotes') or {}).get(roles.weak) or {}).get('ask')
            row=proposal(state,operations,demand.rows[-1],demand.goal,roles,DEMAND.economic_capacity,
                         ask,crossing,frame['world_profile']['quantity_step'])
            if row['reason']=='NO_ACTIVE_PHYSICAL_DUST_WORK':
                return operations
            row.update(t=int(frame['t']),index=int(frame['index']),state=deepcopy(state),
                       original_operations=deepcopy(operations))
            if row['eligible']:
                planned=row['planned'];price=row['price'];side=row['side'];qty=row['quantity']
                if len(planned['owners'])>=frame['world_profile']['max_live_owners']:
                    row.update(eligible=False,reason='RESOURCE_OWNER_LIMIT')
                elif producer.tail_new.veto(frame,side,'PASSIVE',qty,price,frame['ledger']):
                    row.update(eligible=False,reason='ORIGINAL_TAIL_OR_NEW_ADMISSION')
                elif not ctx.check(planned,side,price,qty,'PASSIVE_RESIDUAL_CONTINUATION'):
                    row.update(eligible=False,reason='ORIGINAL_EARNINGS_ADMISSION')
                elif __import__('governor').INSTANCE.quantity(planned,side,price,qty,'PASSIVE','PASSIVE_RESIDUAL_CONTINUATION',frame)<qty-EPS:
                    row.update(eligible=False,reason='V52_QUANTITY_OR_REPAIR_RESERVE')
                elif risk_guard.quantity(planned,side,price,qty,'PASSIVE','PASSIVE_RESIDUAL_CONTINUATION',frame)<=0:
                    row.update(eligible=False,reason='POSTFLIP_RESERVED_WORST_FLOOR')
                else:
                    validate(frame['world_profile']['asset'],'PASSIVE',price,qty,quantity_step=frame['world_profile']['quantity_step'])
                    assert abs(price/frame['world_profile']['tick']-round(price/frame['world_profile']['tick']))<EPS
            self.continuation_rows.append(row)
            if not row['eligible']:
                return operations
            index=frame['own_view']['n']+sum(o['kind']=='NEW' for o in operations)
            op=dict(kind='NEW',key=f'{side}_{index}',parent_id=roles.pid(side),side=side,
                    route='PASSIVE',price=price,qty=qty,role='PASSIVE_CURRENT_COMMITMENT_REPAIR')
            # Independent registry shares quote maintenance, not the old active-trigger history.
            producer.passive_births+=1
            record=dict(t=int(frame['t']),index=int(frame['index']),**op,
                        work_id=row['work_id'],old_target=row['old_target'],
                        inherited_quantity=row['inherited_quantity'],independent_quantity=row['independent_quantity'],
                        independent_inventory_interval=row['independent_inventory_interval'],
                        confirmed_fill=0.,old_residual_service=0.,independent_service=0.,owner_state='PLANNED')
            self.continuation_orders.append(record)
            row['new_key']=op['key']
            assert demand.goal.target==row['old_target']
            return [*operations,op]
    module.CommitmentRepairProbe=ContinuedRepair


def save():
    out=os.environ.get('BTC5M_LAN_RESULT_DIR')
    if out and Path(out).is_dir():
        value=dict(mode=MODE,rows=[r for i in INSTANCES for r in i.continuation_rows],
                   orders=[r for i in INSTANCES for r in i.continuation_orders],
                   note='Receipt service split is not an additional fill or separate realized profit. Core work exact target unchanged.')
        with gzip.GzipFile(filename=str(Path(out)/'work_continuation_trace.json.gz'),mode='wb',mtime=0) as stream:
            stream.write(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode())

atexit.register(save)
