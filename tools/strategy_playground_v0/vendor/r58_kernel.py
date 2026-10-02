"""Research-only short receipt-event world; conditional stresses, not probabilities."""
import copy, math
import numpy as np
import base_kernel as base

SIDES=base.SIDES
EPS=1e-8
ACTIONS=base.ACTIONS+('CANCEL_REPAIR_SIDE','CANCEL_REPAIR_AND_ACTIVE_HALF')
EVENTS=(
    {'shift':-.02,'fraction':1.,'ack':'LATE','slip':0.},
    {'shift':.02,'fraction':1.,'ack':'LATE','slip':0.},
    {'shift':0.,'fraction':0.,'ack':'EARLY','slip':0.},
    {'shift':0.,'fraction':.5,'ack':'UNKNOWN','slip':.02},
)
COUNTERS={'transitions':0,'legal_action_evaluations':0}


def refresh(n):
    s=n['state']
    for side in SIDES:
        s['pending_qty'][side]=sum(o['qty'] for o in s['owners'] if o['side']==side)
        s['pending_cash'][side]=sum(o['qty']*o['limit'] for o in s['owners'] if o['side']==side)
    return n


def receipt(n,side,q,p):
    if q<EPS:return
    s=n['state'];b=n['book'];other='DOWN' if side=='UP' else 'UP'
    s['inv'][side]+=q;s['cost']+=q*p
    n['last_fill'][side]+=q
    b['total_cash']=s['cost'];b['total_qty']=dict(s['inv'])
    remaining=q;kept=[]
    for oq,op in b['unpaired'][other]:
        paired=min(oq,remaining);remaining-=paired;oq-=paired
        b['paired_quantity']+=paired;b['paired_cost']+=paired*(op+p)
        b['paired_surplus']+=paired*(1-op-p)
        if oq>EPS:kept.append([oq,op])
    b['unpaired'][other]=kept
    if remaining>EPS:b['unpaired'][side].append([remaining,p])


def normalize_source(n):
    z=copy.deepcopy(n);z['last_fill']={s:0. for s in SIDES};z['serial']=0
    for o in z['state']['owners']:
        o['age']=0;o['cancel_requested']=o['state']=='CANCEL_PENDING'
    return refresh(z)


def proposals(n):
    out=base.proposals(n);major,minor,*_=base.context(n)
    has=any(o['side']==minor and o['state']!='UNKNOWN' for o in n['state']['owners'])
    for p in out:p['cancel_side']=major if p['cancel_risk'] else None
    out.append({'id':10,'name':ACTIONS[10],'orders':[],'cancel_side':minor,
                'legal':has,'reason':'LEGAL' if has else 'NO_CANCELABLE_REPAIR_OWNER'})
    active=copy.deepcopy(out[5])
    active.update(id=11,name=ACTIONS[11],cancel_side=minor,legal=bool(active['legal'] and has))
    out.append(active)
    return out


def measures(n):
    s=n['state'];pay={side:s['inv'][side]-s['cost'] for side in SIDES}
    return np.asarray([min(pay.values()),max(pay.values()),
                       min(pay['UP']-s['pending_cash']['DOWN'],pay['DOWN']-s['pending_cash']['UP']),
                       s['cost'],abs(s['inv']['UP']-s['inv']['DOWN']),sum(s['pending_cash'].values()),
                       n['book']['paired_quantity']],dtype=float)


def utility(n):
    m=measures(n)
    return float(m[0]+.25*m[1]+.25*m[2]-.05*m[3])


def step(n,p,event):
    assert p['legal']
    COUNTERS['transitions']+=1
    z=copy.deepcopy(n);s=z['state'];z['serial']+=1;z['last_fill']={a:0. for a in SIDES}
    for o in s['owners']:
        o['age']+=1
        if p['cancel_side']==o['side'] and o['state']!='UNKNOWN':
            o['cancel_requested']=True;o['state']='CANCEL_PENDING'
    for k,o in enumerate(p['orders']):
        s['owners'].append({'key':f"micro_{z['serial']}_{k}",'side':o['side'],'route':o['route'],
                            'qty':o['qty'],'limit':o['price'],'state':'SUBMITTED','age':0,'cancel_requested':False})
    spread=max(.01,min(.25,n['public']['ask']-n['public']['bid']+event.get('widen',0.)))
    mid=(n['public']['ask']+n['public']['bid'])/2+event['shift']
    bid=max(.01,min(.99-spread,mid-spread/2));z['public']={'bid':bid,'ask':bid+spread}
    bids={'UP':bid,'DOWN':1-bid-spread};asks={'UP':bid+spread,'DOWN':1-bid}
    remaining_depth=dict(z['depth']);survivors=[]
    for o in s['owners']:
        cancel=o['cancel_requested'];side=o['side']
        if cancel and event['ack']=='EARLY':continue
        active=o['route']=='ACTIVE'
        # Active price must fit its submitted limit, including the conditional slip.
        unit=asks[side]+event['slip'] if active else o['limit']
        eligible=(unit<=o['limit']+EPS) if active else (bids[side]<=o['limit']+EPS)
        q=o['qty']*event['fraction'] if eligible else 0.
        if active:q=min(q,remaining_depth[side]);remaining_depth[side]-=q
        receipt(z,side,q,unit);o['qty']-=q
        if o['qty']<=EPS:continue
        if cancel or active:
            if event['ack']!='UNKNOWN':continue
            o['state']='UNKNOWN'
        survivors.append(o)
    s['owners']=survivors
    return refresh(z)


def check_state(n):
    s=n['state'];b=n['book'];assert 0<n['public']['bid']<n['public']['ask']<1
    assert s['cost']>=-EPS
    residual=0.
    for side in SIDES:
        assert abs(s['inv'][side]-b['paired_quantity']-sum(q for q,p in b['unpaired'][side]))<1e-5
        assert abs(s['pending_qty'][side]-sum(o['qty'] for o in s['owners'] if o['side']==side))<1e-5
        assert abs(s['pending_cash'][side]-sum(o['qty']*o['limit'] for o in s['owners'] if o['side']==side))<1e-5
        residual+=sum(q*p for q,p in b['unpaired'][side])
    assert abs(b['paired_cost']+residual-s['cost'])<1e-5
    assert abs(b['paired_surplus']-residual-measures(n)[0])<1e-5
    assert all(o['qty']>0 for o in s['owners'])
    return True


def one_step(n):
    props=proposals(n);values=np.full(len(ACTIONS),-1e12);children={}
    for i,p in enumerate(props):
        if not p['legal']:continue
        COUNTERS['legal_action_evaluations']+=1
        children[i]=[step(n,p,e) for e in EVENTS]
        values[i]=np.mean([utility(c) for c in children[i]])
    return values,children,props


def teachers(n):
    one,children,props=one_step(n);two=np.full(len(ACTIONS),-1e12)
    for i,cs in children.items():two[i]=np.mean([one_step(c)[0].max() for c in cs])
    return one-one[0],two-two[0],np.asarray([p['legal'] for p in props])


def state_features(n):
    major,minor,*_=base.context(n);s=n['state'];extra=[]
    for side in (major,minor):
        owners=[o for o in s['owners'] if o['side']==side]
        qty=sum(o['qty'] for o in owners)
        extra.extend([sum(o['qty']*o['limit'] for o in owners)/max(qty,EPS),
                      min(10,max((o['age'] for o in owners),default=0))/10,
                      n['last_fill'][side]/15])
    extra.extend([sum(o['qty']*o['limit'] for o in s['owners'] if o['state']=='UNKNOWN')/15,
                  sum(o['qty'] for o in s['owners'] if o['cancel_requested'])/15])
    return np.r_[base.feature(n),extra]


def action_features(n,geometry=True):
    raw=state_features(n);major,minor,*_=base.context(n);s=n['state'];before=measures(n);rows=[]
    for i,p in enumerate(proposals(n)):
        x=np.r_[raw,np.eye(len(ACTIONS))[i]]
        if geometry:
            q={side:sum(o['qty'] for o in p['orders'] if o['side']==side) for side in SIDES}
            cash=sum(o['qty']*o['price'] for o in p['orders']);totalq=sum(q.values())
            full=[s['inv'][side]+q[side]-s['cost']-cash for side in SIDES]
            x=np.r_[x,q[major]/15,q[minor]/15,cash/15,cash/max(totalq,EPS),
                    float(any(o['route']=='ACTIVE' for o in p['orders'])),
                    float(p['cancel_side']==major),float(p['cancel_side']==minor),
                    (min(full)-before[0])/15,(max(full)-before[1])/15,
                    (abs(s['inv'][major]+q[major]-s['inv'][minor]-q[minor])-before[4])/15]
        rows.append(x)
    return np.asarray(rows)


def synthetic(anchor,k):
    """Reachable buy-only inventory and explicit pending, using one market's book anchor."""
    rng=np.random.default_rng(580000+int(anchor['index'])*31+k)
    major='UP' if rng.random()<.5 else 'DOWN';minor='DOWN' if major=='UP' else 'UP'
    gap=float(rng.choice([15,30,60,90]));paired=float(rng.choice([0,30,120]))
    pair_cost=float(rng.choice([.88,.98,1.04]));basis=float(rng.choice([.25,.5,.75,.9]))
    # Explicit empty opening cases, with no cost invented or borrowed from Target.
    if k==0:gap=0.;paired=0.
    inv={major:paired+gap,minor:paired};cost=paired*pair_cost+gap*basis
    spread=float(rng.choice([.01,.03,.09]));mid=(anchor['public']['bid']+anchor['public']['ask'])/2
    bid=max(.01,min(.99-spread,mid-spread/2))
    n={'state':{'inv':inv,'cost':cost,'owners':[],'pending_qty':{s:0. for s in SIDES},'pending_cash':{s:0. for s in SIDES}},
       'public':{'bid':bid,'ask':bid+spread},'depth':{s:float(rng.choice([15,30,60,120])) for s in SIDES},
       'book':{'unpaired':{major:[[gap,basis]] if gap else [],minor:[]},'paired_quantity':paired,
               'paired_cost':paired*pair_cost,'paired_surplus':paired*(1-pair_cost),'total_cash':cost,'total_qty':dict(inv)},
       'last_fill':{s:0. for s in SIDES},'serial':0}
    _,_,bids,_,_=base.context(n)
    if k and k%4:
        side=major if k%4==1 else minor
        price=math.floor((bids[side]-.01+1e-10)/.01)*.01
        if .01<=price<=.99 and price*15>=1:
            state='CANCEL_PENDING' if k%4==3 else 'SUBMITTED'
            n['state']['owners']=[{'key':'synthetic_owner','side':side,'route':'PASSIVE','qty':15.,'limit':price,
                                  'state':state,'cancel_requested':state=='CANCEL_PENDING','age':int(rng.integers(0,4))}]
    refresh(n);check_state(n);return n


def self_test():
    anchor={'index':64,'public':{'bid':.59,'ask':.61}};n=synthetic(anchor,2)
    checks=0
    for k in range(12):
        n=synthetic(anchor,k);before=copy.deepcopy(n)
        for p in proposals(n):
            if not p['legal']:continue
            for e in EVENTS:
                z=step(n,p,e);check_state(z);checks+=1
                assert z['state']['cost']>=n['state']['cost']-EPS
                for o in p['orders']:assert o['qty']*o['price']>=1-EPS
        assert n==before;checks+=1
    # Unknown cancellation retains reservation; only a later terminal ACK releases it.
    n=synthetic(anchor,1);side=n['state']['owners'][0]['side'];n['state']['owners'][0]['limit']=.1
    refresh(n);p=proposals(n)[1];assert p['legal']
    z=step(n,p,{'shift':0.,'fraction':0.,'ack':'UNKNOWN','slip':0.})
    assert z['state']['pending_qty'][side]==15 and z['state']['owners'][0]['state']=='UNKNOWN';checks+=1
    zz=step(z,proposals(z)[0],{'shift':0.,'fraction':0.,'ack':'EARLY','slip':0.})
    assert not zz['state']['owners'];checks+=1
    # Receipt size can be below one dollar; accounting still has to conserve it.
    z=copy.deepcopy(n);before=z['state']['cost'];receipt(z,side,.5,.2);check_state(z)
    assert abs(z['state']['cost']-before-.1)<EPS;checks+=1
    # No same-plan cancel release, including repair-side cancellation.
    n=synthetic(anchor,2);major,minor,*_=base.context(n)
    n['state']['owners']=[{'key':'cross','side':major,'route':'PASSIVE','qty':15.,'limit':.95,'state':'CANCEL_PENDING','cancel_requested':True,'age':1}]
    refresh(n);assert not proposals(n)[8]['legal'];checks+=1
    # Mirror all side labels and the stress direction, including FIFO lots and owners.
    n=synthetic(anchor,5);mirror=copy.deepcopy(n)
    for key in ('inv','pending_qty','pending_cash'):
        mirror['state'][key]={s:n['state'][key]['DOWN' if s=='UP' else 'UP'] for s in SIDES}
    for o in mirror['state']['owners']:o['side']='DOWN' if o['side']=='UP' else 'UP'
    for key in ('unpaired','total_qty'):mirror['book'][key]={s:n['book'][key]['DOWN' if s=='UP' else 'UP'] for s in SIDES}
    mirror['depth']={s:n['depth']['DOWN' if s=='UP' else 'UP'] for s in SIDES}
    mirror['public']={'bid':1-n['public']['ask'],'ask':1-n['public']['bid']}
    for p,mp in zip(proposals(n),proposals(mirror)):
        assert p['legal']==mp['legal']
        if not p['legal']:continue
        for e in EVENTS:
            me=dict(e,shift=-e['shift']);a=step(n,p,e);b=step(mirror,mp,me)
            assert np.allclose(measures(a),measures(b));checks+=1
    return {'status':'PASS','checks':checks,'state_accounting_and_pending_conserved':True,
            'cancel_request_not_release':True,'mirror_pass':True,'new_order_minimum':1,'passive_ticket':15,
            'event_probabilities_calibrated':False,'native_replays':0}
