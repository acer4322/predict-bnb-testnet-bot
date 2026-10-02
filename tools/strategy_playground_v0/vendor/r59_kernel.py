"""R59 grid-correct receipt world, existing-order continuation and confirmed attempt memory."""
import copy,math
from functools import lru_cache
import numpy as np
import r58_kernel as old

SIDES=old.SIDES;ACTIONS=old.ACTIONS;EPS=1e-8
EVENTS=old.EVENTS+(
    {'shift':-.01,'fraction':.5,'ack':'LATE','slip':.01,'widen':.02},
    {'shift':.01,'fraction':.5,'ack':'LATE','slip':.01,'widen':.02},
)
TAILS=(
    ({'shift':-.02,'fraction':.5,'ack':'LATE','slip':.02,'widen':.02},
     {'shift':.02,'fraction':.5,'ack':'LATE','slip':.02}),
    ({'shift':.02,'fraction':.5,'ack':'LATE','slip':.02,'widen':.02},
     {'shift':-.02,'fraction':.5,'ack':'LATE','slip':.02}),
    ({'shift':0.,'fraction':0.,'ack':'UNKNOWN','slip':0.},
     {'shift':0.,'fraction':0.,'ack':'EARLY','slip':0.}),
    ({'shift':0.,'fraction':.5,'ack':'LATE','slip':.01},)*2,
)
COUNTERS={'steps':0,'teacher_roots':0,'tail_value_calls':0}
proposals=old.proposals;measures=old.measures;utility=old.utility


def grid_book(bid,ask):
    lo=max(1,min(98,int(math.floor(bid*100+1e-7))))
    hi=max(lo+1,min(99,int(math.ceil(ask*100-1e-7))))
    return {'bid':lo/100,'ask':hi/100}


def event_book(public,event):
    spread=max(.01,min(.25,public['ask']-public['bid']+event.get('widen',0.)))
    mid=(public['ask']+public['bid'])/2+event['shift']
    return grid_book(mid-spread/2,mid+spread/2)


def normalize(n):
    z=copy.deepcopy(n);z['public']=grid_book(z['public']['bid'],z['public']['ask'])
    z['attempt_memory']={'failed_signatures':[],'confirmed_zero':0,'confirmed_partial':0,'confirmed_full':0}
    for o in z['state']['owners']:
        o['attempt_signature']=None;o['requested']=o['qty'];o['filled_total']=0.
    return z


def signature(n,order):
    s=n['state']
    return [order['side'],round(order['qty'],8),round(order['price'],8),
            *[round(n['public'][v],8) for v in ('bid','ask')],
            *[round(n['depth'][v],8) for v in SIDES],
            *[round(s['inv'][v],8) for v in SIDES],round(s['cost'],8),
            *[round(s['pending_qty'][v],8) for v in SIDES],
            *[round(s['pending_cash'][v],8) for v in SIDES]]


def retry_mask(n,props):
    """Diagnostic ablation only: omit the identical confirmed-zero attempt until state changes."""
    failed=n['attempt_memory']['failed_signatures']
    return np.asarray([p['legal'] and not any(o['route']=='ACTIVE' and signature(n,o) in failed for o in p['orders']) for p in props])


def step(n,p,e):
    assert p['legal'];COUNTERS['steps']+=1
    z=copy.deepcopy(n);s=z['state'];z['serial']+=1;z['last_fill']={a:0. for a in SIDES}
    z['event_receipts']=[]
    for o in s['owners']:
        o['age']+=1
        if p['cancel_side']==o['side'] and o['state']!='UNKNOWN':o['cancel_requested']=True;o['state']='CANCEL_PENDING'
    for j,o in enumerate(p['orders']):
        assert abs(o['price']*100-round(o['price']*100))<1e-6
        s['owners'].append({'key':f"micro_{z['serial']}_{j}",'side':o['side'],'route':o['route'],'qty':o['qty'],
             'limit':o['price'],'state':'SUBMITTED','age':0,'cancel_requested':False,'requested':o['qty'],'filled_total':0.,
             'attempt_signature':signature(n,o) if o['route']=='ACTIVE' else None})
    z['public']=event_book(n['public'],e);bid=z['public']['bid'];ask=z['public']['ask']
    bids={'UP':bid,'DOWN':1-ask};asks={'UP':ask,'DOWN':1-bid};depth=dict(n['depth']);survivors=[]
    for o in s['owners']:
        cancel=o['cancel_requested'];active=o['route']=='ACTIVE';side=o['side']
        if cancel and e['ack']=='EARLY':continue
        price=round(asks[side]+e['slip'],10) if active else o['limit']
        eligible=(price<=o['limit']+EPS) if active else bids[side]<=o['limit']+EPS
        qty=o['qty']*e['fraction'] if eligible else 0.
        if active:qty=min(qty,depth[side]);depth[side]-=qty
        old.receipt(z,side,qty,price);o['qty']-=qty;o['filled_total']+=qty
        if qty>EPS:z['event_receipts'].append({'key':o['key'],'side':side,'qty':qty,'price':price,'route':o['route']})
        terminal=o['qty']<=EPS or ((cancel or active) and e['ack']!='UNKNOWN')
        if terminal:
            if active and not cancel and o['attempt_signature'] is not None:
                mem=z['attempt_memory']
                if o['filled_total']<=EPS:
                    mem['confirmed_zero']+=1
                    if o['attempt_signature'] not in mem['failed_signatures']:mem['failed_signatures'].append(o['attempt_signature'])
                elif o['filled_total']<o['requested']-EPS:mem['confirmed_partial']+=1
                else:mem['confirmed_full']+=1
            continue
        if cancel or active:o['state']='UNKNOWN'
        survivors.append(o)
    s['owners']=survivors;return old.refresh(z)


def check_state(n):
    old.check_state(n)
    assert all(abs(n['public'][s]*100-round(n['public'][s]*100))<1e-6 for s in ('bid','ask'))
    for p in proposals(n):
        if p['legal']:
            for o in p['orders']:assert abs(o['price']*100-round(o['price']*100))<1e-6
    return True


def features(n,option=False):
    x=old.action_features(n,True)
    if not option:return x
    major,minor,bids,asks,basis=old.base.context(n);s=n['state'];gap=abs(s['inv']['UP']-s['inv']['DOWN'])
    owners=[o for o in s['owners'] if o['side']==minor]
    qty=sum(o['qty'] for o in owners);price=sum(o['qty']*o['limit'] for o in owners)/max(qty,EPS)
    tiny=sum(o['qty'] for o in owners if o['qty']*o['limit']<1-EPS)
    extra=[]
    for p in proposals(n):
        cancelled=qty if p['cancel_side']==minor else 0.
        extra.append([qty/15,price,asks[minor]-price if owners else 0.,tiny/15,
                      max(0.,1-gap*min(.99,asks[minor]+.02)),cancelled/15,
                      cancelled*max(0.,asks[minor]-price)/15,
                      float(p['cancel_side']==minor and 0<gap*asks[minor]<1)])
    return np.c_[x,np.asarray(extra)]


def tail_key(n):
    s=n['state']
    return (s['inv']['UP'],s['inv']['DOWN'],s['cost'],n['public']['bid'],n['public']['ask'],
            n['depth']['UP'],n['depth']['DOWN'],tuple((o['side'],o['route'],o['qty'],o['limit'],o['cancel_requested']) for o in s['owners']))


@lru_cache(maxsize=16000)
def tail_cached(key):
    """Exact KEEP-only endpoint accounting; no new orders, no fitted fill probabilities."""
    up,down,cost,bid,ask,du,dd,initial=key;values=[]
    for path in TAILS:
        inv={'UP':up,'DOWN':down};cash=cost;owners=list(initial);public={'bid':bid,'ask':ask}
        for e in path:
            public=event_book(public,e);bids={'UP':public['bid'],'DOWN':1-public['ask']};asks={'UP':public['ask'],'DOWN':1-public['bid']}
            kept=[];depth={'UP':du,'DOWN':dd}
            for side,route,remaining,price,cancel in owners:
                if cancel and e['ack']=='EARLY':continue
                active=route=='ACTIVE';unit=round(asks[side]+e['slip'],10) if active else price
                eligible=(unit<=price+EPS) if active else (bids[side]<=price+EPS)
                q=remaining*e['fraction'] if eligible else 0.
                if active:q=min(q,depth[side]);depth[side]-=q
                inv[side]+=q;cash+=q*unit;remaining-=q
                if remaining<=EPS or ((cancel or active) and e['ack']!='UNKNOWN'):continue
                kept.append((side,route,remaining,price,cancel))
            owners=kept
        pending={s:sum(q*p for side,route,q,p,cancel in owners if side==s) for s in SIDES}
        pu=inv['UP']-cash;pd=inv['DOWN']-cash
        values.append(min(pu,pd)+.25*max(pu,pd)+.25*min(pu-pending['DOWN'],pd-pending['UP'])-.05*cash)
    return float(np.mean(values))


def terminal_value(n):
    COUNTERS['tail_value_calls']+=1;return tail_cached(tail_key(n))


def teacher(n):
    """Three matched two-decision teachers: base4, wider6, wider6 plus KEEP-only tail."""
    COUNTERS['teacher_roots']+=1;props=proposals(n);ys=np.full((3,len(ACTIONS)),-1e12)
    for a,p in enumerate(props):
        if not p['legal']:continue
        recourse=[]
        for e in EVENTS:
            child=step(n,p,e);values=[]
            for q in proposals(child):
                if not q['legal']:continue
                ends=[step(child,q,f) for f in EVENTS]
                raw=np.asarray([utility(z) for z in ends]);tail=np.asarray([terminal_value(z) for z in ends])
                values.append([raw[:4].mean(),raw.mean(),tail.mean()])
            recourse.append(np.max(values,axis=0))
        recourse=np.asarray(recourse)
        ys[:,a]=[recourse[:4,0].mean(),recourse[:,1].mean(),recourse[:,2].mean()]
    return ys-ys[:,[0]],np.asarray([p['legal'] for p in props])


def myopic(n):
    ps=proposals(n);scores=[np.mean([utility(step(n,p,e)) for e in EVENTS[:4]]) if p['legal'] else -1e12 for p in ps]
    return int(np.argmax(scores))


def self_test():
    count=0
    for i in range(12):
        n=normalize(old.synthetic({'index':64,'public':{'bid':.59,'ask':.61}},i));check_state(n)
        for p in proposals(n):
            if not p['legal']:continue
            for e in EVENTS:
                z=step(n,p,e);check_state(z)
                slow=[]
                for path in TAILS:
                    end=z
                    for t in path:end=step(end,proposals(end)[0],t)
                    slow.append(utility(end))
                assert abs(terminal_value(z)-np.mean(slow))<1e-8;count+=1
    # Confirmed zero is remembered, UNKNOWN and partial receipts are not zero.
    n=normalize(old.synthetic({'index':64,'public':{'bid':.59,'ask':.61}},4))
    n['state']['owners']=[];old.refresh(n);p=proposals(n)[6];assert p['legal']
    zero={'shift':0.,'fraction':0.,'ack':'EARLY','slip':0.}
    z=step(n,p,zero);assert z['attempt_memory']['confirmed_zero']==1
    assert not retry_mask(z,proposals(z))[6];count+=2
    moved=step(z,proposals(z)[0],dict(zero,shift=.01));assert retry_mask(moved,proposals(moved))[6];count+=1
    unknown=step(n,p,dict(zero,ack='UNKNOWN'));assert unknown['attempt_memory']['confirmed_zero']==0 and unknown['state']['owners'];count+=1
    partial=step(n,p,dict(zero,fraction=.5));assert partial['attempt_memory']['confirmed_zero']==0 and partial['attempt_memory']['confirmed_partial']==1;count+=1
    # UNKNOWN later receives canonical terminal zero; reservation is not released early.
    confirmed=step(unknown,proposals(unknown)[0],zero);assert confirmed['attempt_memory']['confirmed_zero']==1 and not confirmed['state']['owners'];count+=1
    # Minimum new order and small existing partial fills have different semantics.
    n=normalize(old.synthetic({'index':64,'public':{'bid':.59,'ask':.61}},1))
    side=old.base.context(n)[1];n['state']['owners']=[{'key':'small','side':side,'route':'PASSIVE','qty':.5,'limit':.9,'state':'SUBMITTED',
        'age':1,'cancel_requested':False,'attempt_signature':None,'requested':15.,'filled_total':14.5}];old.refresh(n)
    z=step(n,proposals(n)[0],dict(zero,fraction=.5));assert abs(z['last_fill'][side]-.25)<EPS;check_state(z);count+=1
    for bid,ask in ((.345,.375),(.01,.03),(.97,.99),(.51,.60)):
        x=grid_book(bid,ask);m=grid_book(1-ask,1-bid);assert abs(x['bid']-(1-m['ask']))<EPS and abs(x['ask']-(1-m['bid']))<EPS;count+=1
    tail_cached.cache_clear()
    return {'status':'PASS','checks':count,'grid_tick':.01,'quantity_step':.01,'new_order_minimum':1.,'passive_ticket':15.,
            'active_variable':True,'fast_tail_matches_explicit_keep_steps':True,'unknown_not_confirmed_zero':True,'native_new':0}
