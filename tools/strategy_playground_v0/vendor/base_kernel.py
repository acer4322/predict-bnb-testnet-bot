"""Fast local receipt-stress world. NOT a fill-probability or market simulator.

One decision -> possible fills/cancel acknowledgement -> unresolved liabilities.
81 equally enumerated stresses are a curriculum, not market probabilities.
"""
import copy, itertools, math
import numpy as np

SIDES=('UP','DOWN')
ACTIONS=('KEEP','CANCEL_RISK_SIDE','PASSIVE_REPAIR15','ACTIVE_MIN','ACTIVE_QUARTER',
         'ACTIVE_HALF','ACTIVE_FULL','ADD_RISK15','CANCEL_AND_ACTIVE_HALF','OPEN_BOTH15')
FEATURES=('gross_lots','confirmed_gap_lots','floor_lots','best_lots','paid_lots',
          'risk_bid','repair_ask','spread','risk_pending_lots','repair_pending_lots',
          'risk_pending_cash_lots','repair_pending_cash_lots','cancel_cash_lots',
          'repair_depth_lots','residual_max_cost','active_pair_edge','owners_scaled')
SCENARIOS=list(itertools.product((0.,.5,1.),(0.,.5,1.),(0.,.5,1.),('EARLY_ACK','LATE_ACK','UNKNOWN')))
EPS=1e-8


def context(node):
    st=node['state']; inv=st['inv']
    diff=inv['UP']-inv['DOWN']
    if abs(diff)>EPS: major='UP' if diff>0 else 'DOWN'
    else: major='UP' if st['pending_cash']['UP']>=st['pending_cash']['DOWN'] else 'DOWN'
    minor='DOWN' if major=='UP' else 'UP'
    bid=node['public']['bid'];ask=node['public']['ask']
    bids={'UP':bid,'DOWN':1-ask};asks={'UP':ask,'DOWN':1-bid}
    lots=node['book']['unpaired'][major]
    basis=max((p for q,p in lots if q>EPS),default=0.)
    return major,minor,bids,asks,basis


def feature(node):
    s=node['state'];major,minor,bids,asks,basis=context(node)
    vals=[sum(s['inv'].values())/15,abs(s['inv']['UP']-s['inv']['DOWN'])/15,
          (min(s['inv'].values())-s['cost'])/15,(max(s['inv'].values())-s['cost'])/15,s['cost']/15,
          bids[major],asks[minor],node['public']['ask']-node['public']['bid'],
          s['pending_qty'][major]/15,s['pending_qty'][minor]/15,
          s['pending_cash'][major]/15,s['pending_cash'][minor]/15,
          sum(o['qty']*o['limit'] for o in s['owners'] if o['state']=='CANCEL_PENDING')/15,
          node['depth'][minor]/15,basis,1-basis-asks[minor],len(s['owners'])/20]
    return np.asarray(vals,dtype=float)


def proposals(node):
    s=node['state'];major,minor,bids,asks,basis=context(node);owners=s['owners']
    gap=max(0.,s['inv'][major]-s['inv'][minor]-s['pending_qty'][minor])
    cap=min(gap,node['depth'][minor]); amin=math.ceil((1./asks[minor]-1e-10)/.01)*.01
    def new(side,route,q,p): return {'side':side,'route':route,'qty':q,'price':round(p,10)}
    pp=math.floor((min(bids[minor]-.01,1-basis)+1e-10)/.01)*.01
    ap=min(.99,round(asks[minor]+.02,10))
    proposals=[([],False),([],True),([new(minor,'PASSIVE',15.,pp)],False)]
    for size in (amin,.25*cap,.5*cap,cap):
        q=math.floor((min(size,cap)+1e-10)/.01)*.01
        proposals.append(([new(minor,'ACTIVE',q,ap)],False))
    proposals.extend([([new(major,'PASSIVE',15.,math.floor((bids[major]-.01+1e-10)/.01)*.01)],False),
                      ([new(minor,'ACTIVE',math.floor((.5*cap+1e-10)/.01)*.01,ap)],True),
                      ([new(z,'PASSIVE',15.,math.floor((bids[z]-.01+1e-10)/.01)*.01) for z in SIDES],False)])
    out=[]
    for i,(orders,cancel) in enumerate(proposals):
        legal=True; reason='LEGAL'; pending=[(o['side'],o['limit']) for o in owners]
        if i==2 and (gap<15.-EPS or s['inv'][major]<=s['inv'][minor]+EPS):legal=False;reason='REPAIR_NET_BELOW_TICKET'
        if i==9 and (sum(s['inv'].values())>EPS or owners):legal=False;reason='OPEN_ONLY_EMPTY'
        if i in (1,8) and not any(o['side']==major and o['state']!='UNKNOWN' for o in owners):legal=False;reason='NO_CANCELABLE_RISK_OWNER'
        for o in orders:
            if o['qty']<=EPS or not .01<=o['price']<=.99 or o['qty']*o['price']<1.-EPS:legal=False;reason='MINIMUM_NOTIONAL_OR_PRICE'
            if o['route']=='ACTIVE' and (o['qty']>cap+EPS or any(x['route']=='ACTIVE' for x in owners)):legal=False;reason='ACTIVE_CAP_OR_PENDING'
            if o['route']=='PASSIVE' and o['price']>=asks[o['side']]-EPS:legal=False;reason='PASSIVE_MARKETABLE'
            if any(sd!=o['side'] and price+o['price']>=1.-EPS for sd,price in pending):legal=False;reason='OWN_CROSS_INCLUDING_CANCEL_PENDING'
            pending.append((o['side'],o['price']))
        out.append({'id':i,'name':ACTIONS[i],'orders':orders,'cancel_risk':cancel,'legal':legal,'reason':reason})
    return out


def outcome_table(node):
    """Vectorized scenarios; no state mutations or predicted fill labels."""
    s=node['state'];major,minor,bids,asks,basis=context(node)
    props=proposals(node); n=len(SCENARIOS); states=[]
    old_fraction=np.asarray([[a,b] for a,b,_,_ in SCENARIOS]);new_fraction=np.asarray([x[2] for x in SCENARIOS])
    early_ack=np.asarray([x[3]=='EARLY_ACK' for x in SCENARIOS])
    unknown=np.asarray([x[3]=='UNKNOWN' for x in SCENARIOS])
    # Alternative public-book shocks are hypothetical stress, not a forecast.
    shock=np.asarray([.02*(a-b) for a,b,_,_ in SCENARIOS])
    future_bid=np.clip(node['public']['bid']+shock,.01,.98)
    future_ask=np.clip(node['public']['ask']+shock,future_bid+.01,.99)
    future_bids={'UP':future_bid,'DOWN':1-future_ask}
    active_extra=np.where(early_ack,.02,0.)
    for action in props:
        inv=np.tile([s['inv']['UP'],s['inv']['DOWN']],(n,1)).astype(float)
        cash=np.full(n,s['cost'],dtype=float);pending_cash=np.zeros((n,2));paid=np.zeros(n)
        for o in s['owners']:
            j=SIDES.index(o['side']); requested_cancel=action['cancel_risk'] and o['side']==major and o['state']!='UNKNOWN'
            cancel=o['state']=='CANCEL_PENDING' or requested_cancel
            eligible=np.ones(n,dtype=bool) if o['route']=='ACTIVE' else future_bids[o['side']]<=o['limit']+EPS
            frac=old_fraction[:,j]*eligible
            # Early ACK precludes a later fill; late ACK permits a racing fill.
            if cancel:frac=np.where(early_ack,0.,frac)
            filled=o['qty']*frac;inv[:,j]+=filled;cash+=filled*o['limit'];paid+=filled*o['limit']
            remaining=o['qty']-filled
            # Only a modeled confirmed cancellation releases the remainder.
            pending_cash[:,j]+=remaining*o['limit']*(unknown if cancel else 1.)
        for o in action['orders']:
            j=SIDES.index(o['side'])
            if o['route']=='ACTIVE':
                unit=np.minimum(o['price'],asks[o['side']]+active_extra)
                filled=o['qty']*new_fraction
                # Only a terminal IOC acknowledgement releases the remainder.
                pending_cash[:,j]+=(o['qty']-filled)*o['price']*unknown
            else:
                unit=np.full(n,o['price']);eligible=future_bids[o['side']]<=o['price']+EPS
                filled=o['qty']*new_fraction*eligible
                pending_cash[:,j]+=(o['qty']-filled)*o['price']
            inv[:,j]+=filled;cash+=filled*unit;paid+=filled*unit
        pay=inv-cash[:,None]
        floor=pay.min(axis=1);best=pay.max(axis=1)
        stressed=np.minimum(pay[:,0]-pending_cash[:,1],pay[:,1]-pending_cash[:,0])
        states.append(np.stack((floor,best,stressed,paid),axis=1))
    return np.asarray(states),np.asarray([p['legal'] for p in props]),props


def summaries(table):
    # Fixed common rubric; these weights are research curriculum assumptions.
    lo=np.quantile(table[:,:,0],.25,axis=1);best=np.quantile(table[:,:,1],.25,axis=1)
    pending=np.quantile(table[:,:,2],.25,axis=1)
    spent=table[:,:,3].mean(axis=1)
    utility=lo+.25*best+.25*pending-.05*spent
    return utility,np.stack((table[:,:,0].mean(axis=1),lo,best,pending,spent),axis=1)


def expanded_nodes(node):
    for scale,shift in itertools.product((.5,1.,1.5),(-.02,0.,.02)):
        z=copy.deepcopy(node);st=z['state']
        for s in SIDES:
            st['inv'][s]*=scale;st['pending_qty'][s]*=scale;st['pending_cash'][s]*=scale
            z['book']['unpaired'][s]=[[q*scale,p] for q,p in z['book']['unpaired'][s]]
        st['cost']*=scale
        for key in ('paired_quantity','paired_cost','paired_surplus','total_cash'):
            if key in z['book']:z['book'][key]*=scale
        if 'total_qty' in z['book']:
            z['book']['total_qty']={s:v*scale for s,v in z['book']['total_qty'].items()}
        for o in st['owners']:o['qty']*=scale
        spread=z['public']['ask']-z['public']['bid']
        z['public']['bid']=max(.01,min(.99-spread,z['public']['bid']+shift));z['public']['ask']=z['public']['bid']+spread
        z['variant']={'quantity_scale':scale,'public_price_shift':shift,'synthetic':scale!=1. or shift!=0.}
        yield z


def self_test():
    n={'state':{'inv':{'UP':30.,'DOWN':0.},'cost':12.,'owners':[],
                'pending_qty':{'UP':0.,'DOWN':0.},'pending_cash':{'UP':0.,'DOWN':0.}},
       'public':{'bid':.59,'ask':.61},'depth':{'UP':50.,'DOWN':50.},
       'book':{'unpaired':{'UP':[[30.,.4]],'DOWN':[]}}}
    before=copy.deepcopy(n);tab,mask,props=outcome_table(n);checks=0
    assert n==before and mask[0] and mask[6];checks+=2
    # Full repair at .41-.43: exact shares/cash payoff, no pending IOC credit.
    for k,(_,_,fraction,ack) in enumerate(SCENARIOS):
        q=props[6]['orders'][0]['qty']*fraction;paid=q*(.43 if ack=='EARLY_ACK' else .41)
        assert abs(tab[6,k,0]-(min(30.,q)-12.-paid))<1e-7
        expected=min(30.-12.-paid-((30.-q)*.43 if ack=='UNKNOWN' else 0.),q-12.-paid)
        assert abs(tab[6,k,2]-expected)<1e-7;checks+=2
    p=copy.deepcopy(n);p['state']['owners']=[{'key':'x','side':'UP','route':'PASSIVE','qty':15.,'limit':.6,'state':'CANCEL_PENDING'}]
    p['state']['pending_qty']['UP']=15.;p['state']['pending_cash']['UP']=9.
    assert not proposals(p)[8]['legal'];checks+=1
    # Cancellation requested now cannot make a same-plan opposite crossing legal.
    p['state']['owners'][0]['state']='SUBMITTED';assert not proposals(p)[8]['legal'];checks+=1
    pt=outcome_table(p)[0]
    assert abs(pt[1,SCENARIOS.index((0.,0.,0.,'UNKNOWN')),2]+21.)<EPS
    assert abs(pt[1,SCENARIOS.index((0.,0.,0.,'EARLY_ACK')),2]+12.)<EPS;checks+=2
    mirror=copy.deepcopy(n)
    for key in ('inv','pending_qty','pending_cash'):
        mirror['state'][key]={s:n['state'][key]['DOWN' if s=='UP' else 'UP'] for s in SIDES}
    mirror['book']['unpaired']={s:n['book']['unpaired']['DOWN' if s=='UP' else 'UP'] for s in SIDES}
    mirror['public']={'bid':1-n['public']['ask'],'ask':1-n['public']['bid']}
    mt=outcome_table(mirror)[0]
    for k,(a,b,c,d) in enumerate(SCENARIOS):
        assert np.allclose(tab[:,k],mt[:,SCENARIOS.index((b,a,c,d))]);checks+=1
    # Actual partial receipts may be less than the new-order notional minimum.
    assert any(0<x[3]<1. for x in outcome_table(n)[0][3]);checks+=1
    for z in expanded_nodes(n):
        for side in SIDES:
            assert abs(sum(o['qty']*o['limit'] for o in z['state']['owners'] if o['side']==side)-z['state']['pending_cash'][side])<EPS
        assert 0<z['public']['bid']<z['public']['ask']<1;checks+=1
    assert len(SCENARIOS)==81 and np.isfinite(tab).all();checks+=1
    return {'status':'PASS','checks':checks,'scenario_count':81,'new_order_minimum':1.,'passive_ticket':15.,
            'active_variable':True,'cancel_not_same_plan_release':True,'input_immutable':True,
            'execution_probabilities_calibrated':False,'scope':'synthetic local receipt/cancel/slippage stresses'}
