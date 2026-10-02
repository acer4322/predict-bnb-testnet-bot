"""R61 isolated minimum-tail action expansion and paired-downside teachers."""
import copy,math
import numpy as np
import r60_kernel as prev
SIDES=prev.SIDES;EPS=prev.EPS
ACTIONS=prev.ACTIONS+('PASSIVE15_MINIMUM_OVER','ACTIVE_MINIMUM_OVER')
EVENTS=prev.EVENTS;PATHS=prev.PATHS
context=prev.prev.old.base.context
utility=prev.utility;measures=prev.measures;step=prev.step
normalize=prev.normalize;refresh=prev.prev.old.refresh
COUNTERS={'teachers':0,'paired_paths':0}

def original_proposals(n,expanded=True):
    ps=prev.proposals(n);s=n['state'];major,minor,bids,asks,basis=context(n)
    gap=s['inv'][major]-s['inv'][minor]
    pending=any(o['side']==minor for o in s['owners'])
    ap=min(.99,round(asks[minor]+.02,10))
    minimum=math.ceil((1/asks[minor]-1e-10)/.01)*.01
    candidates=[('PASSIVE',15.,ps[2]['orders'][0]['price']),('ACTIVE',minimum,ap)]
    for route,q,price in candidates:
        legal=True;reason='LEGAL';before=min(s['inv'].values())-s['cost']
        after=min(s['inv'][major],s['inv'][minor]+q)-s['cost']-q*price
        if not expanded:legal=False;reason='LEGACY_ACTION_SET'
        elif gap<=EPS or q<=gap+EPS:legal=False;reason='ONLY_POSITIVE_SUBMINIMUM_GAP'
        elif pending:legal=False;reason='REPAIR_SIDE_OWNER_UNRESOLVED'
        elif q*price<1-EPS or not .01<=price<=.99:legal=False;reason='MINIMUM_NOTIONAL_OR_PRICE'
        elif route=='PASSIVE' and price>=asks[minor]-EPS:legal=False;reason='PASSIVE_MARKETABLE'
        elif route=='ACTIVE' and (q>n['depth'][minor]+EPS or any(o['route']=='ACTIVE' for o in s['owners'])):legal=False;reason='ACTIVE_DEPTH_OR_UNRESOLVED'
        elif any(o['side']!=minor and o['limit']+price>=1-EPS for o in s['owners']):legal=False;reason='OWN_CROSS_INCLUDING_CANCEL_PENDING'
        elif after<before-EPS:legal=False;reason='FULL_FILL_FLOOR_WOULD_DECREASE'
        i=len(ps)
        ps.append({'id':i,'name':ACTIONS[i],'orders':[{'side':minor,'route':route,'qty':round(q,10),'price':price}],
                   'cancel_side':None,'legal':legal,'reason':reason,'overshoot_qty':max(0.,q-gap),'full_fill_floor_delta':after-before})
    return ps

def proposals(n,expanded=True):
    ps=original_proposals(n,expanded)
    mode=n.get('addition_mode','DYNAMIC')
    assert mode in ('DYNAMIC','FIXED_UP')
    if mode=='DYNAMIC':return ps
    _,_,bids,asks,_=context(n);side='UP'
    price=round(math.floor((bids[side]-.01+1e-10)/.01)*.01,10)
    reason='LEGAL'
    if not .01<=price<=.99 or 15*price<1-EPS:reason='MINIMUM_NOTIONAL_OR_PRICE'
    elif price>=asks[side]-EPS:reason='PASSIVE_MARKETABLE'
    elif any(o['side']!=side and o['limit']+price>=1-EPS for o in n['state']['owners']):reason='OWN_CROSS_INCLUDING_CANCEL_PENDING'
    ps[7]={**ps[7],'name':'ADD_FIXED_UP15','orders':[{'side':side,'route':'PASSIVE','qty':15.,'price':price}],
           'legal':reason=='LEGAL','reason':reason}
    return ps

def features(n):
    major,minor,bids,asks,basis=context(n);s=n['state'];before=measures(n)
    raw=prev.prev.old.state_features(n);gap=s['inv'][major]-s['inv'][minor]
    os=[o for o in s['owners'] if o['side']==minor];oq=sum(o['qty'] for o in os)
    op=sum(o['qty']*o['limit'] for o in os)/max(oq,EPS);tiny=sum(o['qty'] for o in os if o['qty']*o['limit']<1-EPS)
    rows=[]
    for a,p in enumerate(proposals(n)):
        q={side:sum(o['qty'] for o in p['orders'] if o['side']==side) for side in SIDES}
        cash=sum(o['qty']*o['price'] for o in p['orders']);full=[s['inv'][side]+q[side]-s['cost']-cash for side in SIDES]
        rows.append(np.r_[raw,np.eye(len(ACTIONS))[a],q[major]/15,q[minor]/15,cash/15,cash/max(sum(q.values()),EPS),
            float(any(o['route']=='ACTIVE' for o in p['orders'])),float(p['cancel_side']==major),float(p['cancel_side']==minor),
            (min(full)-before[0])/15,(max(full)-before[1])/15,(abs(s['inv'][major]+q[major]-s['inv'][minor]-q[minor])-gap)/15,
            oq/15,op,asks[minor]-op if os else 0.,tiny/15,max(0.,q[minor]-gap)/15,
            max(0.,1-gap*asks[minor]),float(a>=12),float(bool(os))])
    return np.asarray(rows)

def myopic(n,expanded=False):
    ps=proposals(n,expanded)
    vals=[sum(prev.fast_value(n,p,e) for e in EVENTS[:4])/4 if p['legal'] else -1e12 for p in ps]
    return int(np.argmax(vals))

def teacher(n):
    """Factorial: legacy/expanded actions x mean/lower-quarter paired advantage."""
    COUNTERS['teachers']+=1;ys=[];masks=[];tables=[]
    for expanded in (False,True):
        ps=proposals(n,expanded);v=np.full((len(ACTIONS),24),np.nan)
        for a,p in enumerate(ps):
            if not p['legal']:continue
            col=0
            for e in EVENTS:
                child=step(n,p,e)
                for path in PATHS:
                    z=child
                    for event in path:
                        # Public/current state only, no next-event input.
                        choice=myopic(z,expanded);z=step(z,proposals(z,expanded)[choice],event)
                    v[a,col]=utility(z);col+=1;COUNTERS['paired_paths']+=1
        mask=np.asarray([p['legal'] for p in ps]);adv=v-v[[0]]
        y=np.full((2,len(ACTIONS)),-1e12)
        y[0,mask]=adv[mask].mean(axis=1)
        y[1,mask]=np.sort(adv[mask],axis=1)[:,:6].mean(axis=1)
        ys.append(y);masks.append(mask);tables.append(adv)
    return np.asarray(ys),np.asarray(masks),np.asarray(tables)

def check_state(n):
    prev.check_state(n)
    for p in proposals(n):
        if not p['legal']:continue
        for o in p['orders']:
            assert o['qty']*o['price']>=1-EPS
            assert abs(o['price']*100-round(o['price']*100))<1e-6
            assert abs(o['qty']*100-round(o['qty']*100))<1e-6
        if p['id']>=12:assert p['full_fill_floor_delta']>=-EPS and p['overshoot_qty']>EPS
    return True

def boundary_node(anchor,side,gap):
    other='DOWN' if side=='UP' else 'UP';paired=30.;basis=.5;pc=.98
    inv={side:paired+gap,other:paired};cost=paired*pc+gap*basis
    n={'state':{'inv':inv,'cost':cost,'owners':[],'pending_qty':{s:0. for s in SIDES},'pending_cash':{s:0. for s in SIDES}},
       'public':dict(anchor['public']),'depth':{s:60. for s in SIDES},
       'book':{'unpaired':{side:[[gap,basis]],other:[]},'paired_quantity':paired,'paired_cost':paired*pc,
               'paired_surplus':paired*(1-pc),'total_cash':cost,'total_qty':dict(inv)},'last_fill':{s:0. for s in SIDES},'serial':0}
    return normalize(prev.prev.normalize(n))

def self_test():
    checks=0
    # R60 cancellation tail: fixed15 stays fixed; active is minimum variable qty.
    n=boundary_node({'public':{'bid':.89,'ask':.92}},'UP',7.5)
    ps=proposals(n);assert ps[12]['legal'] and ps[13]['legal'];assert ps[12]['orders'][0]['qty']==15
    assert abs(ps[13]['orders'][0]['qty']-9.1)<EPS;checks+=3
    assert all(not p['legal'] for p in proposals(n,False)[12:]);checks+=1
    for p in ps[12:]:
        for f in (0.,.25,.5,.75,1.):
            z=step(n,p,dict(shift=0.,fraction=f,ack='LATE',slip=0.));check_state(z)
            assert measures(z)[0]>=measures(n)[0]-EPS;checks+=1
    # UNKNOWN on repair side is never released; no stacking minimum repairs.
    u=step(n,ps[13],dict(shift=0.,fraction=0.,ack='UNKNOWN',slip=0.))
    assert u['state']['owners'] and all(not p['legal'] for p in proposals(u)[12:]);checks+=1
    u=step(u,proposals(u)[0],dict(shift=0.,fraction=0.,ack='EARLY',slip=0.))
    assert not u['state']['owners'] and proposals(u)[13]['legal'];checks+=1
    # Depth, own-cross and economic loss remain vetoes.
    z=copy.deepcopy(n);z['depth']['DOWN']=5.;assert not proposals(z)[13]['legal'];checks+=1
    z=boundary_node({'public':{'bid':.89,'ask':.92}},'UP',.5)
    assert all(not p['legal'] for p in proposals(z)[12:]);checks+=1
    z=copy.deepcopy(n);z['state']['owners']=[{'key':'cross','side':'UP','route':'PASSIVE','qty':15.,'limit':.95,'state':'CANCEL_PENDING','age':0,'cancel_requested':True,'requested':15.,'filled_total':0.,'attempt_signature':None}];refresh(z)
    assert all(not p['legal'] for p in proposals(z)[12:]);checks+=1
    # Partial fill below $1 is still valid; full overshoot flips inventory side.
    z=step(n,ps[13],dict(shift=0.,fraction=.25,ack='LATE',slip=0.));assert 0<z['state']['cost']-n['state']['cost']<1;checks+=1
    z=step(n,ps[13],dict(shift=0.,fraction=1.,ack='LATE',slip=0.));assert context(z)[0]=='DOWN';checks+=1
    for side in SIDES:
        for bid,ask in ((.09,.12),(.49,.52),(.89,.92)):
            for gap in (2.,7.5,14.):
                z=boundary_node({'public':{'bid':bid,'ask':ask}},side,gap);old=prev.proposals(z)
                assert proposals(z)[:12]==old;check_state(z);checks+=1
                for p in proposals(z):
                    if not p['legal']:continue
                    for e in EVENTS:
                        after=step(z,p,e);check_state(after)
                        assert abs(utility(after)-prev.fast_value(z,p,e))<1e-7;checks+=1
    return {'status':'PASS','checks':checks,'legacy12_actions_unchanged':True,'passive15_active_variable':True,
            'unresolved_reservations_preserved':True,'full_fill_floor_nondecreasing_for_new_actions':True,
            'partial_fills_below_minimum_allowed':True,'inventory_side_can_flip':True,'native_new':0,'live_changes':0}
