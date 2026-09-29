"""One receipt-triggered repair after UP acquisition breaks an observed lock.

Compare the minimum legal Active order with restoring the pre-addition floor.
The reference is captured causally, not fixed at zero or read from Target.
"""
import math

MODE = 'MINIMUM'
EPS = 1e-8


def detect(previous,state,confirmed,seen_repair,t):
    if previous is None or not confirmed or not seen_repair:return None
    inc={s:state['inv'][s]-previous['inv'][s] for s in ('UP','DOWN')}
    if min(previous['payoff'].values())<=EPS or state['payoff']['DOWN']>=-EPS:return None
    if inc['UP']<=EPS or abs(inc['DOWN'])>EPS or state['cost']<=previous['cost']+EPS:return None
    return dict(born_t=t,anchor_floor=min(previous['payoff'].values()),before=previous,after=state,
                up_fill_increment=inc['UP'],up_acquisition_cost=state['cost']-previous['cost'],
                reason='CONFIRMED_UP_ONLY_FILL_BREAKS_PREVIOUS_BOTH_POSITIVE_STATE')


def decide(state,operations,ask,depth,episode,confirmed,crossing,step=.01,tick=.01):
    pending=dict(state['pending_qty']);cash=dict(state['pending_cash'])
    owners=[dict(key=o['key'],side=o['side'],price=o['limit']) for o in state['owners']]
    for op in operations:
        if op['kind']=='NEW':
            pending[op['side']]+=op['qty'];cash[op['side']]+=op['qty']*op['price']
            owners.append(dict(key=op['key'],side=op['side'],price=op['price']))
    floor_price=round(math.ceil((1./15.-EPS)/tick)*tick,10)
    valid=ask is not None and 0<ask<1 and depth>0
    target=episode['anchor_floor'] if episode else None
    projected=state['payoff']['DOWN']+pending['DOWN']-cash['DOWN']
    gap=max(0.,state['inv']['UP']-state['inv']['DOWN']-pending['DOWN'])
    need=max(0.,(target-projected)/(1-ask)) if target is not None and valid else 0.
    cap=min(gap,need,depth) if valid else 0.
    minimum=round(math.ceil((1./ask-EPS)/step)*step,8) if valid else None
    qty=minimum if MODE=='MINIMUM' else max(0.,round(math.floor((cap+EPS)/step)*step,8))
    row=dict(eligible=False,reason='NOT_ELIGIBLE',mode=MODE,active_confirmed=confirmed,
        active_ask=ask,visible_depth=depth,minimum_passive_price=floor_price,minimum_active_qty=minimum,
        anchor_floor=target,pending_qty=pending,pending_cash=cash,projected_down_from_pending_down=projected,
        filled_net_quantity_capacity=gap,restore_quantity_need=need,quantity_capacity=cap,
        quantity=qty,cash_budget_enabled=False,available_cash=None,conflicts=[])
    if not confirmed or episode is None:row['reason']='WAIT_FOR_CONFIRMED_REEXPOSURE_EPISODE'
    elif state['payoff']['DOWN']>=-EPS:row['reason']='CURRENT_DOWN_NO_LONGER_NEGATIVE'
    elif any(o['kind']=='NEW' and o['side']=='DOWN' for o in operations):row['reason']='ORIGINAL_DOWN_NEW_HAS_PRIORITY'
    elif not valid:row['reason']='NO_CURRENT_ACTIVE_LIQUIDITY'
    elif floor_price<ask-EPS:row['reason']='LEGAL_PASSIVE15_STILL_AVAILABLE'
    elif qty is None or qty<=0 or qty*ask<1.-EPS:row['reason']='ACTIVE_NEW_BELOW_ONE_DOLLAR'
    elif qty>cap+EPS:row['reason']='FINITE_REPAIR_OR_DEPTH_BELOW_MINIMUM_ACTIVE'
    else:
        row['conflicts']=crossing('DOWN',ask,owners)
        row.update(eligible=not row['conflicts'],reason='PENDING_OR_SAME_PLAN_OWN_CROSS' if row['conflicts'] else 'REPAIR_CONFIRMED_UP_REEXPOSURE')
    return row


class CoordinationProbe:
    def __init__(self,snapshot):
        self.snapshot=snapshot;self.previous=None;self.seen_repair=False
        self.episode=None;self.rows=[];self.submissions=[]

    def apply(self,frame,producer,operations,validate,crossing):
        if self.submissions or not frame['start']<=frame['t']<frame['end']:return operations
        if not producer.demand.rows or producer.demand.rows[-1]['t']!=frame['t']:return operations
        ledger=frame['ledger'];active=producer.opportunity.submissions
        owner=ledger.carriers.get(active[0]['key']) if len(active)==1 else None
        confirmed=owner is not None and owner.state=='TERMINAL' and float(owner.filled)>EPS
        state=self.snapshot(frame,ledger)
        if self.episode is None:self.episode=detect(self.previous,state,confirmed,self.seen_repair,int(frame['t']))
        if confirmed and self.previous and state['inv']['DOWN']>self.previous['inv']['DOWN']+EPS:self.seen_repair=True
        self.previous={k:state[k] for k in ('inv','cost','payoff')}
        ask=((frame.get('quotes') or {}).get('DOWN') or {}).get('ask')
        bids=frame['book']['bids'];best=max(bids) if bids else None
        depth=float(bids[best]) if best is not None else 0.
        if ask is not None and best is not None:assert abs(ask-round(1-best,10))<EPS
        row=decide(state,operations,ask,depth,self.episode,confirmed,crossing,
                   frame['world_profile']['quantity_step'],frame['world_profile']['tick'])
        row.update(t=int(frame['t']),state=state,original_operations=[dict(o) for o in operations],
                   gateway_state_id=frame['gateway_state_id'])
        if row['eligible']:
            if len(state['owners'])+sum(o['kind']=='NEW' for o in operations)>=frame['world_profile']['max_live_owners']:
                row.update(eligible=False,reason='RESOURCE_OWNER_LIMIT')
            else:
                validate(frame['world_profile']['asset'],'ACTIVE',ask,row['quantity'],quantity_step=frame['world_profile']['quantity_step'])
                assert abs(ask/frame['world_profile']['tick']-round(ask/frame['world_profile']['tick']))<EPS
        self.rows.append(row)
        if not row['eligible']:return operations
        index=frame['own_view']['n']+sum(o['kind']=='NEW' for o in operations)
        op=dict(kind='NEW',key=f'DOWN_{index}',parent_id=2,side='DOWN',route='ACTIVE',price=ask,
                qty=row['quantity'],role='ACTIVE_CONFIRMED_REEXPOSURE_REPAIR')
        self.submissions.append(dict(t=int(frame['t']),**op))
        return [*operations,op]


def instrument(source,replace):
    marker='self.commitment_repair=_CommitmentRepairProbe(_OWN_SNAPSHOT)'
    source=replace(source,marker,marker+';self.coordination=_CoordinationProbe(_OWN_SNAPSHOT)')
    marker='   producer.demand.on_plan(f,ops)'
    source=replace(source,marker,'   ops=producer.coordination.apply(f,producer,ops,validate_size,_goal_crossing)\n'+marker)
    marker='  result.update(active_opportunity_mode='
    source=replace(source,marker,'  result.update(coordination_episode=producer.coordination.episode,coordination_submissions=producer.coordination.submissions)\n'+marker)
    source=replace(source,"active_matches_opportunity=(len(active)==len(producer.opportunity.submissions)<=1 and (not active or producer.opportunity.mode=='ONE_ACTIVE'))",
        "active_matches_opportunity=(len(active)==len(producer.opportunity.submissions)+len(producer.coordination.submissions)<=2 and len(producer.opportunity.submissions)<=1 and len(producer.coordination.submissions)<=1 and (not active or producer.opportunity.mode=='ONE_ACTIVE'))")
    return replace(source,'active_births=producer.active_births+producer.opportunity.submissions,',
                   'active_births=producer.active_births+producer.opportunity.submissions+producer.coordination.submissions,')


def self_test(crossing):
    global MODE
    saved=MODE
    try:
        previous=dict(inv=dict(UP=100.,DOWN=105.),cost=90.,payoff=dict(UP=10.,DOWN=15.))
        state=dict(inv=dict(UP=140.,DOWN=105.),cost=128.,payoff=dict(UP=12.,DOWN=-23.),
                   pending_qty=dict(UP=0.,DOWN=0.),pending_cash=dict(UP=0.,DOWN=0.),owners=[])
        ep=detect(previous,state,True,True,10);assert ep['anchor_floor']==10.
        assert detect(previous,state,False,True,10) is None and detect(previous,state,True,False,10) is None
        for mode in ('MINIMUM','RESTORE'):
            MODE=mode;r=decide(state,[],.06,100.,ep,True,crossing)
            assert r['eligible'] and r['quantity']*r['active_ask']>=1 and r['quantity']<=35
            assert r['available_cash'] is None and not r['cash_budget_enabled']
            assert not decide(state,[],.08,100.,ep,True,crossing)['eligible']
            assert not decide(state,[],.06,10.,ep,True,crossing)['eligible']
            pending=dict(state,pending_qty=dict(UP=0.,DOWN=35.),pending_cash=dict(UP=0.,DOWN=2.45),
                owners=[dict(key='old',side='DOWN',state='CANCEL_PENDING',qty=35.,limit=.07)])
            assert not decide(pending,[dict(kind='CANCEL',key='old')],.06,100.,ep,True,crossing)['eligible']
            crossing_state=dict(state,owners=[dict(key='up',side='UP',state='CANCEL_PENDING',qty=15.,limit=.94)])
            assert not decide(crossing_state,[dict(kind='CANCEL',key='up')],.06,100.,ep,True,crossing)['eligible']
            assert not decide(state,[dict(kind='NEW',key='up',side='UP',qty=15.,price=.94)],.06,100.,ep,True,crossing)['eligible']
        return dict(status='PASS',own_receipt_trigger=True,finite_anchor=True,variable_active_size=True,
                    current_depth=True,pending_reserved=True,no_cash_budget=True)
    finally:MODE=saved
