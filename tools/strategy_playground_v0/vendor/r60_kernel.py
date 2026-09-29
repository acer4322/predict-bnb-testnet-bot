"""R60 matched four-event owner retention vs observable-state adaptive recourse."""
import copy
import numpy as np
import r59_kernel as prev

SIDES=prev.SIDES; ACTIONS=prev.ACTIONS; EPS=prev.EPS
proposals=prev.proposals; utility=prev.utility; measures=prev.measures
EVENTS=prev.EVENTS
# Both teachers see exactly the same four-event paths. These are hypothetical
# scenarios, not fill probabilities. The continuation controller never sees e.
PATHS=tuple(tuple(list(path)+[dict(path[0])]) for path in prev.TAILS)
COUNTERS={'steps':0,'teacher_nodes':0,'myopic_evaluations':0,'fast_outcomes':0}

def normalize(n):
    z=copy.deepcopy(n)
    z['observed_progress']={s:{'qty':0.,'cash':0.,'events':0} for s in SIDES}
    for o in z['state']['owners']:
        o['origin']='EXISTING'
        # Existing fills prior to the episode are unknown, never reconstructed.
    return z

def step(n,p,e):
    COUNTERS['steps']+=1
    z=prev.step(n,p,e)
    progress=copy.deepcopy(n['observed_progress'])
    for s in SIDES:
        rs=[r for r in z['event_receipts'] if r['side']==s]
        progress[s]['qty']+=sum(r['qty'] for r in rs)
        progress[s]['cash']+=sum(r['qty']*r['price'] for r in rs)
        progress[s]['events']+=int(bool(rs))
    z['observed_progress']=progress
    return z

def check_state(n):
    prev.check_state(n)
    assert all(v['qty']>=0 and v['cash']>=0 and v['events']>=0 for v in n['observed_progress'].values())
    return True

def features(n,progress=False):
    x=prev.features(n,True)
    if not progress:return x
    major,minor,_,asks,_=prev.old.base.context(n)
    v=[]
    for side in (major,minor):
        h=n['observed_progress'][side]
        owners=[o for o in n['state']['owners'] if o['side']==side]
        v.extend([h['qty']/15,h['cash']/15,h['cash']/max(EPS,h['qty']),h['events']/8,
                  sum(o.get('filled_total',0.) for o in owners)/15])
    extra=[]
    for p in proposals(n):
        q=sum(o['qty'] for o in p['orders'] if o['route']=='ACTIVE')
        cash=sum(o['qty']*o['price'] for o in p['orders'] if o['route']=='ACTIVE')
        extra.append(v+[q/15,cash/15,float(p['cancel_side']==minor)*v[5],
                         max(0.,asks[minor]-v[7]) if v[5]>0 else 0.])
    return np.c_[x,np.asarray(extra)]

def fast_value(n,p,e):
    """Exact one-event scalar accounting, checked against full FIFO state steps.

    No action is taken in this evaluator. FIFO remains in the actual transition;
    one-event terminal utility depends only on inventory/cash and retained owners.
    """
    COUNTERS['fast_outcomes']+=1
    s=n['state'];inv=dict(s['inv']);cash=s['cost'];pending={side:0. for side in SIDES}
    public=prev.event_book(n['public'],e);bids={'UP':public['bid'],'DOWN':1-public['ask']}
    asks={'UP':public['ask'],'DOWN':1-public['bid']};depth=dict(n['depth'])
    owners=[(o['side'],o['route'],o['qty'],o['limit'],o['cancel_requested'] or (p['cancel_side']==o['side'] and o['state']!='UNKNOWN')) for o in s['owners']]
    owners.extend((o['side'],o['route'],o['qty'],o['price'],False) for o in p['orders'])
    for side,route,q,limit,cancel in owners:
        if cancel and e['ack']=='EARLY':continue
        active=route=='ACTIVE';price=round(asks[side]+e['slip'],10) if active else limit
        eligible=price<=limit+EPS if active else bids[side]<=limit+EPS
        fill=q*e['fraction'] if eligible else 0.
        if active:fill=min(fill,depth[side]);depth[side]-=fill
        inv[side]+=fill;cash+=fill*price;left=q-fill
        if left>EPS and not ((cancel or active) and e['ack']!='UNKNOWN'):pending[side]+=left*limit
    pu=inv['UP']-cash;pd=inv['DOWN']-cash
    return min(pu,pd)+.25*max(pu,pd)+.25*min(pu-pending['DOWN'],pd-pending['UP'])-.05*cash

def myopic(n):
    COUNTERS['myopic_evaluations']+=1
    ps=proposals(n)
    scores=[sum(fast_value(n,p,e) for e in EVENTS[:4])/4 if p['legal'] else -1e12 for p in ps]
    return int(np.argmax(scores))

def continue_path(n,path,adaptive):
    z=n
    for event in path:
        # Only current state is passed to the chooser; the event is consumed later.
        a=myopic(z) if adaptive else 0
        z=step(z,proposals(z)[a],event)
    return z

def teacher(n):
    COUNTERS['teacher_nodes']+=1
    ps=proposals(n); y=np.full((2,len(ACTIONS)),-1e12)
    for a,p in enumerate(ps):
        if not p['legal']:continue
        scores=[[],[]]
        for e in EVENTS:
            child=step(n,p,e)
            for path in PATHS:
                for mode in (0,1):
                    scores[mode].append(utility(continue_path(child,path,bool(mode))))
        y[:,a]=np.mean(scores,axis=1)
    return y-y[:,[0]],np.asarray([p['legal'] for p in ps])

def self_test():
    checks=0
    for i in (1,2,4,6):
        n=normalize(prev.normalize(prev.old.synthetic({'index':64,'public':{'bid':.59,'ask':.61}},i)))
        original=copy.deepcopy(n)
        for p in proposals(n):
            if not p['legal']:continue
            for e in EVENTS:
                z=step(n,p,e);check_state(z)
                assert abs(fast_value(n,p,e)-utility(z))<1e-8
                for s in SIDES:
                    rs=[r for r in z['event_receipts'] if r['side']==s]
                    assert abs(z['observed_progress'][s]['qty']-sum(r['qty'] for r in rs))<EPS
                    assert abs(z['observed_progress'][s]['cash']-sum(r['qty']*r['price'] for r in rs))<EPS
                assert np.allclose(measures(z),prev.measures(prev.step(n,p,e)))
                checks+=1
        assert n==original;checks+=1
    # One pinned partial fill creates known episode progress; zero/UNKNOWN do not.
    n=normalize(prev.normalize(prev.old.synthetic({'index':64,'public':{'bid':.59,'ask':.61}},4)))
    n['state']['owners']=[];prev.old.refresh(n);p=proposals(n)[6];assert p['legal']
    z=step(n,p,dict(shift=0.,fraction=.5,ack='LATE',slip=0.))
    assert sum(v['qty'] for v in z['observed_progress'].values())>0;checks+=1
    z=step(n,p,dict(shift=0.,fraction=0.,ack='UNKNOWN',slip=0.))
    assert sum(v['qty'] for v in z['observed_progress'].values())==0 and z['state']['owners'];checks+=1
    assert features(z,True).shape==(12,features(z,False).shape[1]+14);checks+=1
    for q in proposals(z):
        if not q['legal']:continue
        for e in EVENTS:
            zz=step(z,q,e);assert abs(fast_value(z,q,e)-utility(zz))<1e-8;checks+=1
    return {'status':'PASS','checks':checks,'existing_R59_physics_unchanged':True,
            'progress_since_episode_anchor_only':True,'future_event_not_controller_input':True,'fast_myopic_matches_full_state_step':True,
            'native_new':0,'live_changes':0}
