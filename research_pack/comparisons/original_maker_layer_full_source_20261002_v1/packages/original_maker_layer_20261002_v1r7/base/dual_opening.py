"""Pure V47 candidate components: dual opening, inventory roles, physical work memory.

This module never imports or executes a native simulator and does not submit
orders. The returned proposals still require the existing canonical gateway.
"""
from contextlib import contextmanager
from copy import deepcopy
import math

EPS=1e-8
SIDES=('UP','DOWN')


def inventory_direction(inv,previous=None):
    assert previous in (*SIDES,None)
    assert all(math.isfinite(inv[s]) and inv[s]>=0 for s in SIDES)
    net=inv['UP']-inv['DOWN']
    return 'UP' if net>EPS else 'DOWN' if net < -EPS else previous


class InventoryRoles:
    def __init__(self):self.side=None;self.rows=[];self.changes=[]

    def observe(self,frame):
        before=self.side;inv=frame['own_view']['inv']
        self.side=inventory_direction(inv,before)
        row=dict(t=frame['t'],index=frame['index'],side=self.side,previous=before,inv=dict(inv))
        self.rows.append(row)
        if self.side!=before:self.changes.append(dict(row,kind='BIRTH' if before is None else 'SWITCH'))
        return self.side


def passive_prices(book,theta9,tick=.01):
    bid=book['best_bid'];ask=book['best_ask']
    assert bid is not None and ask is not None and 0<bid<ask<1
    offset=math.log1p(math.exp(theta9))*tick
    return {s:round(math.floor((b-offset+1e-10)/tick)*tick,10) for s,b in (('UP',bid),('DOWN',round(1-ask,10)))}


def opening_plan(frame,side,prices,asks,owners,crossing,stale_ticks,tick=.01,ticket=15.):
    """At most one neutral pair outstanding; cancel intent never frees a slot.

    Once a confirmed imbalance exists, the original core manager must handle
    orders. Initially tied fills permit another neutral pair only after every
    existing owner has reached canonical TERMINAL.
    """
    assert side in (*SIDES,None) and ticket==15.
    live=[o for o in owners if o['state']!='TERMINAL']
    result=dict(handled=side is None,reason='DIRECTION_SELECTED' if side else 'NEUTRAL_OPENING',operations=[],live_owner_keys=[o['key'] for o in live])
    if side is not None:return result
    now,start,end=frame['t'],frame['start'],frame['end']
    if now<start:result['reason']='BEFORE_MARKET';return result
    if live:
        for o in live:
            can=frame['cancellable'].get(o['key'],False) and o['state'] not in ('CANCEL_PENDING','UNKNOWN')
            stale=abs(o['limit']-prices[o['side']])>tick*stale_ticks+1e-9
            if can and (now>=end or stale):
                result['operations'].append(dict(kind='CANCEL',key=o['key'],origin='WHOLE_POLICY',reason='ACTUAL_MARKET_END' if now>=end else 'NEUTRAL_OPENING_PRICE_MAINTENANCE'))
        result['reason']='WAIT_CANONICAL_TERMINAL_FOR_EXISTING_PAIR'
        return result
    if now>=end:result['reason']='MARKET_ENDED';return result
    proposals=[]
    for s in SIDES:
        p=prices[s];ask=asks[s]
        if not math.isfinite(p) or not tick<=p<1 or ask is None or p>=ask-1e-10 or p*ticket<1-EPS:
            result['reason']='BOTH_LEGS_MUST_HAVE_LEGAL_PASSIVE15_PRICE';return result
        assert abs(p/tick-round(p/tick))<EPS
        conflicts=crossing(s,p,[dict(key=o['key'],side=o['side'],price=o['limit']) for o in live]+proposals)
        if conflicts:result['reason']='OWN_CROSS';result['conflicts']=conflicts;return result
        i=frame['own_view']['n']+len(proposals)
        proposals.append(dict(kind='NEW',key=f'{s}_{i}',parent_id=1 if s=='UP' else 2,side=s,route='PASSIVE',price=p,qty=ticket,role='PASSIVE_NEUTRAL_OPENING'))
    result.update(reason='DUAL_PASSIVE_OPENING',operations=proposals)
    return result


@contextmanager
def physical_role_scope(roles,strong):
    """Bound old pure model callbacks without changing any owner or role history."""
    previous=roles.side
    try:
        roles.side=strong
        yield
    finally:
        roles.side=previous


class PhysicalFiniteWork:
    def __init__(self,goal_class,roles,strong,initial,extra):
        assert strong in SIDES
        self.strong=strong;self.weak='DOWN' if strong=='UP' else 'UP';self.roles=roles
        with physical_role_scope(roles,strong):self.goal=goal_class(initial,extra)

    def update(self,state,t,end):
        with physical_role_scope(self.roles,self.strong):out=self.goal.update(state,t,end)
        return dict(out,physical_strong=self.strong,physical_repair_side=self.weak)

    def desired(self,original,selected_side):
        # Old service remains observed, but cannot generate repair NEW authority
        # for the new addition side after a role switch.
        if selected_side!=self.strong:return dict(original)
        with physical_role_scope(self.roles,self.strong):return self.goal.desired(original)


class PhysicalWorkBank:
    def __init__(self,goal_class,roles):
        self.goal_class=goal_class;self.roles=roles;self.work={s:None for s in SIDES};self.rows=[]

    def birth(self,strong,state,extra):
        assert self.work[strong] is None or self.work[strong].goal.status!='ACTIVE'
        self.work[strong]=PhysicalFiniteWork(self.goal_class,self.roles,strong,state,extra)
        return self.work[strong]

    def observe(self,state,t,end):
        rows={s:w.update(state,t,end) for s,w in self.work.items() if w is not None}
        self.rows.append(dict(t=t,progress=deepcopy(rows)))
        return rows

    def desired(self,original,selected):
        work=self.work.get(selected)
        return work.desired(original,selected) if work else dict(original)


class PhysicalGrowthBank:
    def __init__(self,growth_class):
        self.by_side={s:growth_class(s) for s in SIDES};self.rows=[]

    def update(self,selected,t,inv,cost,desired):
        if selected is None:return dict(desired)
        controller=self.by_side[selected];out=controller.update(t,inv,cost,desired)
        self.rows.append(dict(t=t,strong=selected,record=deepcopy(controller.rows[-1])))
        return out
